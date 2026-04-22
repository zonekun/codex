"""Gemma 4 31B TPU 月次PoC レポート生成.

入力: /c/tmp/gemma4_tpu_monthly_results.jsonl
出力: /c/tmp/gemma4_tpu_monthly_report.md

比較対象:
 - Gemini (BQ既存結果, jsonl に gemini_main_category / gemini_sub_categories 埋め込み済み)
 - Gemma 26B MaaS (C:/tmp/gemma4_poc/gemma4_poc_20260412_191204_gemma-4-26b-a4b-it-maas.json)

メトリクス:
 - MAIN一致率（main_cat_pred vs gemini_main_category）※MAINはGemini流用なので常に一致
 - SUB Jaccard 平均（sub_cat_pred vs gemini_sub_categories）
 - is_monthly 精度（判定トリガーは「月次開示」正解）
 - エラー件数、スループット、トークン統計
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RESULTS_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "C:/tmp/gemma4_tpu_monthly_results.jsonl")
REPORT_PATH = Path(sys.argv[2] if len(sys.argv) > 2 else "C:/tmp/gemma4_tpu_monthly_report.md")
GEMMA26B_PATH = Path("C:/tmp/gemma4_poc/gemma4_poc_20260412_191204_gemma-4-26b-a4b-it-maas.json")


def load_results(path: Path) -> list[dict[str, Any]]:
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


def main() -> None:
    rows = load_results(RESULTS_PATH)
    print(f"loaded {len(rows)} rows", flush=True)
    total = len(rows)

    ok = [r for r in rows if r.get("error") is None and r.get("is_monthly_pred") is not None]
    err = [r for r in rows if r.get("error") is not None or r.get("is_monthly_pred") is None]
    n_ok = len(ok)
    n_err = len(err)

    # SUB Jaccard vs Gemini
    jaccs = [jaccard(r.get("sub_cat_pred", []), r.get("gemini_sub_categories", [])) for r in ok]
    mean_jacc = sum(jaccs) / len(jaccs) if jaccs else 0.0

    # is_monthly precision/recall vs Gemini gold
    # Gold: "月次開示" was main category → is_monthly=True expected. But input is Phase 3 set which doesn't include main_cat=月次開示.
    # So ground truth is: row["main_category"] != monthly. Just report distribution.
    pred_monthly = sum(1 for r in ok if r.get("is_monthly_pred") is True)
    pred_not_monthly = sum(1 for r in ok if r.get("is_monthly_pred") is False)

    # SUB category distribution diff
    from collections import Counter
    sub_pred = Counter()
    sub_gold = Counter()
    for r in ok:
        for c in (r.get("sub_cat_pred") or []):
            sub_pred[c] += 1
        for c in (r.get("gemini_sub_categories") or []):
            sub_gold[c] += 1

    # Throughput
    elapsed_times = [r.get("elapsed_sec", 0.0) for r in rows if r.get("elapsed_sec")]
    mean_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0.0
    p_toks = [r.get("prompt_tokens") or 0 for r in rows]
    c_toks = [r.get("completion_tokens") or 0 for r in rows]
    avg_p = sum(p_toks) / max(len(p_toks), 1)
    avg_c = sum(c_toks) / max(len(c_toks), 1)

    # Compare to Gemma 26B MaaS if available
    gemma26b_cmp: dict[str, Any] = {"available": False}
    if GEMMA26B_PATH.exists():
        data26 = json.loads(GEMMA26B_PATH.read_text(encoding="utf-8"))
        by_doc: dict[str, dict] = {}
        # Format A: {"details": [...]}
        if isinstance(data26, dict) and "details" in data26:
            for item in data26["details"]:
                did = item.get("doc_id")
                if did:
                    by_doc[str(did)] = item
        # Format B: {"results": [...]}
        elif isinstance(data26, dict) and "results" in data26:
            for item in data26["results"]:
                did = item.get("doc_id")
                if did:
                    by_doc[str(did)] = item
        # Format C: list
        elif isinstance(data26, list):
            for item in data26:
                did = item.get("doc_id")
                if did:
                    by_doc[str(did)] = item
        # Find overlap (match by doc_id as string)
        overlap_docs = [r for r in ok if str(r.get("doc_id", "")) in by_doc]
        if overlap_docs:
            j_gemma31 = []
            j_gemma26 = []
            for r in overlap_docs:
                item26 = by_doc[str(r["doc_id"])]
                # Gemma 26B format: gemma_subs (list of str)
                sub26 = item26.get("gemma_subs") or item26.get("gemma_sub_categories") or item26.get("sub_categories") or []
                if not isinstance(sub26, list):
                    sub26 = []
                gold = r.get("gemini_sub_categories", [])
                j_gemma31.append(jaccard(r.get("sub_cat_pred", []), gold))
                j_gemma26.append(jaccard(sub26, gold))
            gemma26b_cmp = {
                "available": True,
                "overlap_n": len(overlap_docs),
                "gemma31b_mean_jaccard": sum(j_gemma31) / len(j_gemma31),
                "gemma26b_mean_jaccard": sum(j_gemma26) / len(j_gemma26),
            }

    # Render markdown
    md = [
        "# Gemma 4 31B (TPU v6e-4) 月次 PoC レポート",
        "",
        f"- 入力: `{RESULTS_PATH}`",
        f"- 対象: TDnet 2024年1月 Phase 3対象 {total}件",
        "",
        "## 実行結果サマリ",
        "",
        f"- 総件数: {total}",
        f"- OK: {n_ok} ({n_ok/max(total,1)*100:.1f}%)",
        f"- エラー: {n_err}",
        f"- 1件あたり平均推論時間: {mean_elapsed:.2f}秒",
        f"- 平均 prompt_tokens: {avg_p:.0f}",
        f"- 平均 completion_tokens: {avg_c:.0f}",
        "",
        "## is_monthly 予測分布",
        "",
        f"- True: {pred_monthly} ({pred_monthly/max(n_ok,1)*100:.1f}%)",
        f"- False: {pred_not_monthly} ({pred_not_monthly/max(n_ok,1)*100:.1f}%)",
        "",
        "## SUB カテゴリ精度（Gemini 基準 Jaccard 平均）",
        "",
        f"- mean Jaccard: **{mean_jacc:.3f}** (n={len(jaccs)})",
        "",
        "## SUB カテゴリ出現頻度（上位10）",
        "",
        "| カテゴリ | Gemma31B pred | Gemini gold |",
        "|---|---:|---:|",
    ]
    top_cats = sorted(set(list(sub_pred.keys()) + list(sub_gold.keys())),
                      key=lambda c: -(sub_pred[c] + sub_gold[c]))[:10]
    for c in top_cats:
        md.append(f"| {c} | {sub_pred[c]} | {sub_gold[c]} |")

    md += [
        "",
        "## Gemma 26B (MaaS) との比較",
        "",
    ]
    if gemma26b_cmp["available"]:
        md += [
            f"- 比較可能件数: {gemma26b_cmp['overlap_n']}",
            f"- Gemma 4 31B mean Jaccard: **{gemma26b_cmp['gemma31b_mean_jaccard']:.3f}**",
            f"- Gemma 4 26B mean Jaccard: {gemma26b_cmp['gemma26b_mean_jaccard']:.3f}",
        ]
    else:
        md.append("- 比較データ無し (Gemma 26B 結果が見つからない or 重複0)")

    md += [
        "",
        "## エラー内訳（上位）",
        "",
    ]
    from collections import Counter as C2
    err_kinds = C2()
    for r in err:
        e = r.get("error") or "no_prediction"
        key = e.split(":")[0][:50]
        err_kinds[key] += 1
    for k, v in err_kinds.most_common(10):
        md.append(f"- {k}: {v}")

    REPORT_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"report saved: {REPORT_PATH}", flush=True)

    # also print summary
    print(json.dumps({
        "total": total, "ok": n_ok, "err": n_err,
        "mean_jaccard": mean_jacc,
        "pred_monthly": pred_monthly,
        "mean_elapsed": mean_elapsed,
        "gemma26b_cmp": gemma26b_cmp,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
