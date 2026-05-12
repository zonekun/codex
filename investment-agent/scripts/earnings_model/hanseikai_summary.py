"""決算反省会ワンコマンドサマリー: DL + レポート生成 + 構造化ターミナル出力.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/hanseikai_summary.py 20260428

DL → CSV/MD生成 → コンパクトなターミナルサマリーを stdout に出力。
Claude が直接読める ~2000-3000 token の構造化テキストを生成する。
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Any

from download_review_data import (
    LOCAL_DIR,
    download_for_date,
    get_gcs_client,
)
from review_report import (
    build_rows,
    load_json,
    write_csv,
    write_md,
)


def _is_nan(v: Any) -> bool:
    """NaN 判定."""
    return isinstance(v, float) and math.isnan(v)


def _safe_float(v: Any) -> float | None:
    """float変換（NaN/None → None）."""
    if v is None or _is_nan(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None



def _fmt_row(row: dict[str, str]) -> str:
    """1銘柄分のMarkdownテーブル行を生成する."""
    ticker = row.get("ticker", "")
    name = row.get("銘柄名", "")
    q = row.get("四半期", "")
    score_raw = row.get("score", "")
    try:
        score_num = int(score_raw)
        score = f"+{score_num}" if score_num > 0 else str(score_num)
    except (ValueError, TypeError):
        score = score_raw
    pred = row.get("予測", "")
    ret = row.get("騰落率", "")
    pos = row.get("ポジ理由", "")
    neg = row.get("ネガ理由", "")
    ret_val = ret.replace("%", "")
    try:
        ret_num = float(ret_val)
        ret_fmt = f"+{ret_num:.1f}%" if ret_num >= 0 else f"{ret_num:.1f}%"
    except ValueError:
        ret_fmt = ret
    return f"| {ticker} | {name} | {q} | {score} | {pred} → {ret_fmt} | {pos} | {neg} |"


def _section_table(section_name: str, section_rows: list[dict[str, str]]) -> list[str]:
    """セクション見出し + Markdownテーブルを生成する."""
    lines: list[str] = []
    lines.append(f"### {section_name}")
    lines.append("")
    if not section_rows:
        lines.append("(なし)")
        lines.append("")
        return lines
    lines.append("| Ticker | 銘柄 | Q | score | 予測→実績 | ポジ | ネガ |")
    lines.append("|--------|------|---|-------|----------|------|------|")
    for row in section_rows:
        lines.append(_fmt_row(row))
    lines.append("")
    return lines


def generate_summary(
    predict_date: str,
    rows: list[dict[str, str]],
    counts: dict[str, int],
    actual_meta: dict[str, Any],
) -> str:
    """構造化ターミナルサマリーを生成する.

    Args:
        predict_date: 予測日 YYYYMMDD.
        rows: build_rows() の出力（レポート行リスト）.
        counts: build_rows() の出力（結果分類カウント）.
        actual_meta: actual JSON のメタデータ.

    Returns:
        Claude が読むための構造化テキスト（Markdown テーブル）.
    """
    total = sum(counts.values())
    acc = actual_meta.get("direction_accuracy", 0)
    corr = actual_meta.get("score_return_correlation", 0)

    lines: list[str] = []
    lines.append(f"## 決算反省会サマリー {predict_date}")
    lines.append("")
    lines.append(
        f"件数: {total}（当たり {counts['当たり']} / 中立 {counts['中立']} / "
        f"ハズレ {counts['ハズレ']} / 要確認 {counts['要確認']}）"
    )
    lines.append(f"方向一致率: {acc:.1%} / 相関: {corr:.3f}")
    lines.append("")

    # 3区分に分類
    hit_rows = [r for r in rows if r.get("結果") == "当たり"]
    neutral_rows = [r for r in rows if r.get("結果") == "中立"]
    miss_rows = [r for r in rows if r.get("結果") == "ハズレ"]

    lines.extend(_section_table("当たり", hit_rows))
    lines.extend(_section_table("中立", neutral_rows))
    lines.extend(_section_table("ハズレ", miss_rows))

    # カテゴリ別平均リターン
    lines.append("### カテゴリ別")
    lines.append("")
    for category in ("UP", "NEUTRAL", "DOWN"):
        cat_rows = [r for r in rows if r.get("予測") == category]
        if not cat_rows:
            lines.append(f"{category}: n=0")
            continue
        rets: list[float] = []
        dir_match_count = 0
        for r in cat_rows:
            ret_str = r.get("騰落率", "")
            if ret_str.endswith("%"):
                try:
                    rets.append(float(ret_str[:-1]))
                except ValueError:
                    pass
            if r.get("結果") == "当たり":
                dir_match_count += 1
        n = len(cat_rows)
        if rets:
            mean_ret = sum(rets) / len(rets)
            dir_acc = dir_match_count / n * 100 if n > 0 else 0
            lines.append(f"{category}: n={n} mean={mean_ret:+.1f}% dir_acc={dir_acc:.0f}%")
        else:
            lines.append(f"{category}: n={n} (リターンデータなし)")
    lines.append("")

    csv_path = LOCAL_DIR / f"earnings_review_{predict_date}.csv"
    md_path = LOCAL_DIR / f"earnings_review_{predict_date}.md"
    lines.append(f"CSV/MD出力: {csv_path}, {md_path}")

    return "\n".join(lines)


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(
        description="決算反省会ワンコマンドサマリー（DL + レポート + ターミナル出力）"
    )
    parser.add_argument("predict_date", help="予測日 YYYYMMDD")
    args = parser.parse_args()

    predict_date: str = args.predict_date
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    client = get_gcs_client()
    download_for_date(client, predict_date)

    rows, counts = build_rows(predict_date)
    actual_meta = load_json(predict_date, "actual")

    csv_path = LOCAL_DIR / f"earnings_review_{predict_date}.csv"
    md_path = LOCAL_DIR / f"earnings_review_{predict_date}.md"
    write_csv(rows, csv_path)
    write_md(rows, counts, predict_date, actual_meta, md_path)

    summary = generate_summary(predict_date, rows, counts, actual_meta)
    summary_path = LOCAL_DIR / f"summary_{predict_date}.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(f"done: {summary_path}")


if __name__ == "__main__":
    main()
