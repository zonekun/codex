"""Phase D 3プロンプト版 比較レポート生成.

入力:
  - C:/tmp/gemma4_tpu_monthly_phaseD_baseline_results.jsonl
  - C:/tmp/gemma4_tpu_monthly_phaseD_v2_results.jsonl
  - C:/tmp/gemma4_tpu_monthly_phaseD_v3_results.jsonl

出力: C:/tmp/gemma4_tpu_monthly_phaseD_comparison.md

5カテゴリ差分（Gemma pred件数 vs Gemini gold件数）+ 全体 Jaccard +
HTTP 400件数 + スループットを 3ラン横並びで比較。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

RESULT_PATHS = {
    "baseline": Path("C:/tmp/gemma4_tpu_monthly_phaseD_baseline_results.jsonl"),
    "v2": Path("C:/tmp/gemma4_tpu_monthly_phaseD_v2_results.jsonl"),
    "v3": Path("C:/tmp/gemma4_tpu_monthly_phaseD_v3_results.jsonl"),
}
REPORT_PATH = Path("C:/tmp/gemma4_tpu_monthly_phaseD_comparison.md")

FOCUS_CATEGORIES = [
    "配当",
    "特別損失",
    "特別利益",
    "受注高/受注残高",
    "月次開示",
    "中期経営計画",
    "業績予想",
    "業績の重要な先行指標",
    "業績修正",
]


def load_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def jaccard(a: list[str], b: list[str]) -> float:
    sa = set(a or [])
    sb = set(b or [])
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("error") is None and r.get("is_monthly_pred") is not None]
    err = [r for r in rows if r.get("error") is not None or r.get("is_monthly_pred") is None]
    n_err = len(err)
    # HTTP 400 のカウント
    n_http400 = sum(
        1 for r in err
        if r.get("error") and str(r["error"]).startswith("HTTP 400")
    )
    jaccs = [jaccard(r.get("sub_cat_pred", []), r.get("gemini_sub_categories", [])) for r in ok]
    mean_jacc = sum(jaccs) / len(jaccs) if jaccs else 0.0

    pred_counter: Counter = Counter()
    gold_counter: Counter = Counter()
    for r in ok:
        for c in (r.get("sub_cat_pred") or []):
            pred_counter[c] += 1
        for c in (r.get("gemini_sub_categories") or []):
            gold_counter[c] += 1

    pred_monthly = sum(1 for r in ok if r.get("is_monthly_pred") is True)
    pred_not_monthly = sum(1 for r in ok if r.get("is_monthly_pred") is False)

    elapsed_times = [r.get("elapsed_sec", 0.0) for r in rows if r.get("elapsed_sec")]
    mean_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0.0

    return {
        "total": len(rows),
        "ok": len(ok),
        "err": n_err,
        "http400": n_http400,
        "mean_jaccard": mean_jacc,
        "pred_counter": pred_counter,
        "gold_counter": gold_counter,
        "pred_monthly": pred_monthly,
        "pred_not_monthly": pred_not_monthly,
        "mean_elapsed_sec": mean_elapsed,
    }


def main() -> None:
    summaries: dict[str, dict[str, Any]] = {}
    for name, path in RESULT_PATHS.items():
        rows = load_results(path)
        summaries[name] = summarize(rows)
        print(f"[{name}] loaded={len(rows)} from {path}", flush=True)

    # Gemini gold counter: 3ラン共通（同じ入力）。baseline の gold を代表値に。
    gold_counter: Counter = summaries.get("baseline", {}).get("gold_counter", Counter())
    if not gold_counter:
        for s in summaries.values():
            if s.get("gold_counter"):
                gold_counter = s["gold_counter"]
                break

    md = [
        "# Gemma 4 31B (TPU v6e-4) Phase D 3プロンプト比較レポート",
        "",
        "入力: TDnet 2024年 10日分 Phase 3対象 (2,359件)",
        "",
        "対象日: 2024-02-05, 02-07, 05-09, 05-16, 08-02, 08-07, 08-20, 11-06, 11-07, 11-12",
        "",
        "## 全体サマリ",
        "",
        "| 指標 | baseline | v2 | v3 |",
        "|---|---:|---:|---:|",
    ]
    for key in ("total", "ok", "err", "http400"):
        row = [key]
        for name in ("baseline", "v2", "v3"):
            row.append(str(summaries.get(name, {}).get(key, "-")))
        md.append("| " + " | ".join(row) + " |")

    # Jaccard と mean elapsed
    row = ["mean_jaccard"]
    for name in ("baseline", "v2", "v3"):
        val = summaries.get(name, {}).get("mean_jaccard", 0.0)
        row.append(f"{val:.3f}")
    md.append("| " + " | ".join(row) + " |")

    row = ["mean_elapsed_sec"]
    for name in ("baseline", "v2", "v3"):
        val = summaries.get(name, {}).get("mean_elapsed_sec", 0.0)
        row.append(f"{val:.2f}")
    md.append("| " + " | ".join(row) + " |")

    md += [
        "",
        "## is_monthly 予測分布",
        "",
        "| 予測 | baseline | v2 | v3 |",
        "|---|---:|---:|---:|",
    ]
    for key in ("pred_monthly", "pred_not_monthly"):
        row = [key.replace("pred_", "")]
        for name in ("baseline", "v2", "v3"):
            row.append(str(summaries.get(name, {}).get(key, 0)))
        md.append("| " + " | ".join(row) + " |")

    md += [
        "",
        "## 5カテゴリ+拡張 差分（Gemma pred件数 − Gemini gold件数）",
        "",
        "| カテゴリ | gold | baseline pred | v2 pred | v3 pred | baseline diff | v2 diff | v3 diff |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cat in FOCUS_CATEGORIES:
        gold_n = gold_counter.get(cat, 0)
        row = [cat, str(gold_n)]
        pred_vals: dict[str, int] = {}
        for name in ("baseline", "v2", "v3"):
            pc: Counter = summaries.get(name, {}).get("pred_counter", Counter())
            pred_vals[name] = pc.get(cat, 0)
        for name in ("baseline", "v2", "v3"):
            row.append(str(pred_vals[name]))
        for name in ("baseline", "v2", "v3"):
            diff = pred_vals[name] - gold_n
            row.append(f"{diff:+d}")
        md.append("| " + " | ".join(row) + " |")

    # degrade チェック（月次開示）
    md += [
        "",
        "## degrade チェック（月次開示）",
        "",
    ]
    baseline_monthly = summaries.get("baseline", {}).get("pred_counter", Counter()).get("月次開示", 0)
    v2_monthly = summaries.get("v2", {}).get("pred_counter", Counter()).get("月次開示", 0)
    v3_monthly = summaries.get("v3", {}).get("pred_counter", Counter()).get("月次開示", 0)
    gold_monthly = gold_counter.get("月次開示", 0)
    md += [
        f"- gold: {gold_monthly}",
        f"- baseline: {baseline_monthly} (diff: {baseline_monthly - gold_monthly:+d})",
        f"- v2: {v2_monthly} (diff: {v2_monthly - gold_monthly:+d})",
        f"- v3: {v3_monthly} (diff: {v3_monthly - gold_monthly:+d})",
    ]
    if abs(v3_monthly - gold_monthly) > abs(baseline_monthly - gold_monthly) + 5:
        md.append("- ⚠️ v3 で月次開示の検知が大きく悪化している可能性")
    else:
        md.append("- OK（v3 で月次開示の degrade なし）")

    md += [
        "",
        "## Baseline → v3 改善サマリ",
        "",
    ]
    bl = summaries.get("baseline", {})
    v3 = summaries.get("v3", {})
    md += [
        f"- mean Jaccard: {bl.get('mean_jaccard', 0.0):.3f} → {v3.get('mean_jaccard', 0.0):.3f} "
        f"(Δ {v3.get('mean_jaccard', 0.0) - bl.get('mean_jaccard', 0.0):+.3f})",
        f"- HTTP 400: {bl.get('http400', 0)} → {v3.get('http400', 0)}",
        f"- OK件数: {bl.get('ok', 0)} → {v3.get('ok', 0)}",
        f"- 平均推論時間: {bl.get('mean_elapsed_sec', 0.0):.2f}s → {v3.get('mean_elapsed_sec', 0.0):.2f}s",
    ]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"[report] saved: {REPORT_PATH}", flush=True)

    # Print summary JSON for orchestrator
    summary_json = {
        name: {
            "total": s["total"],
            "ok": s["ok"],
            "err": s["err"],
            "http400": s["http400"],
            "mean_jaccard": s["mean_jaccard"],
        }
        for name, s in summaries.items()
    }
    print("\n=== PHASE_D_SUMMARY_JSON ===", flush=True)
    print(json.dumps(summary_json, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
