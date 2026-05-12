"""決算反省会レポート生成: prediction + actual + 時価総額を結合してCSV/MDを出力.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/earnings_model/review_report.py 20260414 --out-dir C:/tmp

入力: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json
      （事前に download_review_data.py で取得しておく）
出力: <out-dir>/earnings_review_YYYYMMDD.csv, .md

分類:
    当たり: 予測 != NEUTRAL かつ direction_match=True
    中立  : 予測 == NEUTRAL
    ハズレ: 予測 != NEUTRAL かつ direction_match=False
    要確認: actual_return が NaN（集計バグ等で勝敗判定不能）

率カラムは全て「-9.7%」形式（負は半角マイナス、小数1桁四捨五入）。
時価総額は億円・小数切捨て。

「比較前終値／比較後終値」の意味:
    ザラバ: 発表日前日終値 / 発表日終値
    引け後: 発表日終値     / 翌営業日終値
（actual JSON の compare_before_close/compare_after_close を優先。
 旧スキーマで未保存の場合は予測JSONの prev_close/adj_close にフォールバック）
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from google.cloud import bigquery, storage
from google.oauth2 import service_account

LOCAL_DIR = Path("C:/tmp/earnings_review")
KEY_PATH = Path("C:/gdrive/claude/investment-agent/keys/gcp-service-account.json")
BQ_PROJECT = "gmailpj-357912"
MARKET_CAP_TABLE = "gmailpj-357912.STOCK.YF_STOCK_INFO"
EXCLUSIONS_BUCKET = "stock_data_1930932"
EXCLUSIONS_BLOB_PATH = "earnings_model/earnings_reaction_exclusions/exclusions.json"


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def fmt_pct(v: Any) -> str:
    """小数を「ー9.7%」形式（小数1桁、負はカタカナ長音）にする."""
    if v is None or _is_nan(v):
        return ""
    try:
        x = float(v) * 100
    except (TypeError, ValueError):
        return str(v)
    rounded = round(x, 1)
    if rounded == 0:
        return "0.0%"
    if rounded < 0:
        return f"-{abs(rounded):.1f}%"
    return f"{rounded:.1f}%"


def fmt_num(v: Any) -> str:
    """数値をそのまま（NaN/Noneは空）."""
    if v is None or _is_nan(v):
        return ""
    return str(v)


def fmt_bool(v: Any) -> str:
    """True→Y、False/None→空."""
    if v is True:
        return "Y"
    return ""


def fmt_market_cap_oku(market_cap_yen: Any) -> str:
    """円→億円・小数切捨て."""
    if market_cap_yen is None or _is_nan(market_cap_yen):
        return ""
    return str(int(float(market_cap_yen) // 100_000_000))


def load_json(predict_date: str, kind: str) -> dict[str, Any]:
    """prediction または actual JSON をローカルからロードする."""
    path = LOCAL_DIR / f"{kind}_{predict_date}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_market_divisions(tickers: list[str]) -> dict[str, str]:
    """BQ から各 ticker の市場区分（プライム/スタンダード/グロース等）を取得."""
    if not tickers:
        return {}
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = bigquery.Client(credentials=creds, project=BQ_PROJECT)
    query = """
        SELECT TICKER, MARKET_CATEGORY
        FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
        WHERE TICKER IN UNNEST(@tickers)
    """
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("tickers", "STRING", tickers)]
        ),
    )
    return {r["TICKER"]: (r["MARKET_CATEGORY"] or "") for r in job}


def fetch_market_caps(tickers: list[str], predict_date: str) -> dict[str, int]:
    """BQ から各 ticker の predict_date 直近の MARKET_CAP を取得する.

    Args:
        tickers: 対象 ticker リスト.
        predict_date: 基準日 YYYYMMDD（この日付以前の最新スナップショット）.

    Returns:
        {ticker: market_cap_yen}.
    """
    if not tickers:
        return {}
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = bigquery.Client(credentials=creds, project=BQ_PROJECT)
    pd_iso = f"{predict_date[:4]}-{predict_date[4:6]}-{predict_date[6:8]}"
    query = f"""
        SELECT TICKER, MARKET_CAP
        FROM `{MARKET_CAP_TABLE}`
        WHERE TICKER IN UNNEST(@tickers)
          AND LOADED_DATE <= DATE(@pd)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY LOADED_DATE DESC) = 1
    """
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("tickers", "STRING", tickers),
                bigquery.ScalarQueryParameter("pd", "STRING", pd_iso),
            ]
        ),
    )
    return {r["TICKER"]: r["MARKET_CAP"] for r in job}


def fetch_exclusions(predict_date: str) -> dict[str, str]:
    """GCS から exclusions.json をDLし、該当 predict_date のアクティブ除外を取得する.

    Args:
        predict_date: 対象日 YYYYMMDD.

    Returns:
        {ticker: reason} 形式。removed_at=null のレコードのみ採用。
    """
    creds = service_account.Credentials.from_service_account_file(str(KEY_PATH))
    client = storage.Client(credentials=creds, project="and-and-and")
    bucket = client.bucket(EXCLUSIONS_BUCKET)
    blob = bucket.blob(EXCLUSIONS_BLOB_PATH)
    if not blob.exists():
        return {}
    raw = blob.download_as_text(encoding="utf-8")
    if not raw.strip():
        return {}
    records = json.loads(raw)
    result: dict[str, str] = {}
    for r in records:
        if (
            r.get("predict_date") == predict_date
            and r.get("removed_at") is None
        ):
            result[r.get("ticker", "")] = r.get("reason", "")
    return result


def classify(pred: str, direction_match: bool, actual_return: Any) -> str:
    """結果分類."""
    if _is_nan(actual_return) or actual_return is None:
        return "要確認"
    if pred == "NEUTRAL":
        return "中立"
    return "当たり" if direction_match else "ハズレ"


_POS_PREFIX = (
    "進捗率高", "上方修正", "来期OP増益", "成長加速", "記念配当", "特別配当",
    "自社株買い", "増配", "PEG割安", "QoQ OP急伸",
)
_NEG_PREFIX = (
    "進捗率低", "下方修正", "来期OP減益", "売り圧力", "成長減速",
    "大幅減配", "減配", "PEG割高", "QoQ OP急落",
)


def split_reasons(reasons_str: str) -> tuple[str, str]:
    """スコア理由文字列を (ポジ理由, ネガ理由) に分割する.

    Args:
        reasons_str: compute_score から出力された ' / ' 区切り理由列.

    Returns:
        (positive reasons joined by ' / ', negative reasons joined by ' / ').
    """
    if not reasons_str or reasons_str == "シグナルなし":
        return "", ""
    pos: list[str] = []
    neg: list[str] = []
    for frag in [f.strip() for f in reasons_str.split(" / ") if f.strip()]:
        if frag.startswith(_POS_PREFIX):
            pos.append(frag)
        elif frag.startswith(_NEG_PREFIX):
            neg.append(frag)
        elif frag.startswith("YoY OP") or frag.startswith("純利コンセ乖離") or frag.startswith("コンセ乖離"):
            # 符号で判定
            if "-" in frag.split(" ", 1)[-1]:
                neg.append(frag)
            else:
                pos.append(frag)
        elif frag.startswith("来期予想未開示"):
            # 中立情報: ネガ側に入れて注意喚起（スコア0だが欠損で判断不能）
            neg.append(frag)
        else:
            # 未分類: ネガ側に寄せる（見逃しリスク回避）
            neg.append(frag)
    return " / ".join(pos), " / ".join(neg)


# 列定義: (CSVヘッダ, 値取得関数)
def build_row(
    a: dict[str, Any],
    p: dict[str, Any],
    market_cap_oku: str,
    market_division: str,
    exclusion_reason: str | None = None,
) -> dict[str, str]:
    """1銘柄分のレポート行を構築する."""
    result = classify(a.get("prediction"), a.get("direction_match", False), a.get("actual_return"))
    return {
        "結果": result,
        "ticker": a.get("ticker", ""),
        "銘柄名": a.get("name", ""),
        "時価総額(億)": market_cap_oku,
        "市場区分": market_division,
        "四半期": a.get("quarter", ""),
        "ザラバ": "Y" if a.get("is_intraday") else "",
        "発表時刻": p.get("disc_time", ""),
        "score": fmt_num(a.get("score")),
        "予測": a.get("prediction", ""),
        "実績カテゴリ": "" if _is_nan(a.get("actual_category")) else (a.get("actual_category") or ""),
        "騰落率": fmt_pct(a.get("actual_return")),
        "比較前終値": fmt_num(a.get("compare_before_close") if a.get("compare_before_close") is not None else p.get("prev_close")),
        "比較後終値": fmt_num(a.get("compare_after_close") if a.get("compare_after_close") is not None else p.get("adj_close")),
        "ポジ理由": split_reasons(p.get("reasons", ""))[0],
        "ネガ理由": split_reasons(p.get("reasons", ""))[1],
        "進捗率": fmt_pct(p.get("progress_op")),
        "YoY_OP": fmt_pct(p.get("yoy_op")),
        "コンセ乖離": fmt_pct(p.get("consensus_deviation")),
        "ガイダンス変化": fmt_pct(p.get("guidance_op_change")),
        "翌期OP変化": fmt_pct(p.get("next_year_op_change")),
        "baseline_YoY_OP": fmt_pct(p.get("baseline_yoy_op")),
        "QoQ_OP": fmt_pct(p.get("qoq_op")),
        "PER": fmt_num(p.get("per")),
        "配当変化": fmt_pct(p.get("div_change")),
        "特別配当": fmt_bool(p.get("has_special_dividend")),
        "自社株買い": fmt_bool(p.get("has_buyback")),
        "ガイダンス修正": fmt_bool(p.get("has_guidance_revision")),
        "売り要注意": fmt_bool(p.get("selloff_risk")),
        "除外": "Y" if exclusion_reason is not None else "",
        "除外理由": exclusion_reason if exclusion_reason is not None else "",
    }


SORT_ORDER = {"当たり": 0, "中立": 1, "ハズレ": 2, "要確認": 3}


def build_rows(predict_date: str) -> tuple[list[dict[str, str]], dict[str, int]]:
    """全銘柄分のレポート行を構築する."""
    pred = load_json(predict_date, "prediction")
    actual = load_json(predict_date, "actual")

    p_by_t = {p["ticker"]: p for p in pred["predictions"]}
    tickers = [a["ticker"] for a in actual["actuals"]]
    mc = fetch_market_caps(tickers, predict_date)
    missing_mkt = [t for t in tickers if not p_by_t.get(t, {}).get("market_division")]
    mkt_fallback = fetch_market_divisions(missing_mkt) if missing_mkt else {}
    exclusions = fetch_exclusions(predict_date)

    rows = []
    for a in actual["actuals"]:
        t = a["ticker"]
        p = p_by_t.get(t, {})
        mc_oku = fmt_market_cap_oku(mc.get(t))
        mkt = p.get("market_division") or mkt_fallback.get(t, "")
        excl_reason = exclusions.get(t)
        rows.append(build_row(a, p, mc_oku, mkt, excl_reason))

    def sort_key(r: dict[str, str]) -> tuple[int, float]:
        try:
            score_abs = -abs(float(r["score"])) if r["score"] else 0.0
        except ValueError:
            score_abs = 0.0
        return (SORT_ORDER[r["結果"]], score_abs)

    rows.sort(key=sort_key)

    counts = {k: 0 for k in SORT_ORDER}
    for r in rows:
        counts[r["結果"]] += 1
    return rows, counts


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    """CSV (UTF-8 BOM) を出力する."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(
    rows: list[dict[str, str]],
    counts: dict[str, int],
    predict_date: str,
    actual_meta: dict[str, Any],
    path: Path,
) -> None:
    """Markdown レポートを出力する."""
    headers = list(rows[0].keys())
    total = sum(counts.values())
    acc = actual_meta.get("direction_accuracy", 0)
    corr = actual_meta.get("score_return_correlation", 0)

    lines = [
        f"# 決算反省会レポート {predict_date}",
        "",
        f"- 件数: {total}（当たり {counts['当たり']} / 中立 {counts['中立']} / "
        f"ハズレ {counts['ハズレ']} / 要確認 {counts['要確認']}）",
        f"- 方向一致率（生値）: {acc:.1%}",
        f"- スコア×リターン相関: {corr:.3f}",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(str(r[h]).replace("|", "\\|") for h in headers) + " |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    """エントリポイント."""
    parser = argparse.ArgumentParser(description="決算反省会レポート生成（CSV+MD）")
    parser.add_argument("predict_date", help="予測日 YYYYMMDD")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="出力ディレクトリ（CSV/MDの保存先。都度指定）",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, counts = build_rows(args.predict_date)
    actual_meta = load_json(args.predict_date, "actual")

    csv_path = out_dir / f"earnings_review_{args.predict_date}.csv"
    md_path = out_dir / f"earnings_review_{args.predict_date}.md"
    write_csv(rows, csv_path)
    write_md(rows, counts, args.predict_date, actual_meta, md_path)

    print(f"done: {csv_path}, {md_path}")


if __name__ == "__main__":
    main()
