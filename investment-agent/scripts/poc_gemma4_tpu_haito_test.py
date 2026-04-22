"""Gemma 4 31B (TPU v6e-4 via vLLM) の配当変更検出精度テスト.

入力: data/master/gemma4_haito_test_texts.jsonl (100件、Gemini=True 正解)
出力: /tmp/gemma4_tpu_haito_results.json

TPU VM上で実行。vLLM OpenAI互換API (localhost:8000) に投げる。
batch=8 相当の並列度（asyncio semaphore 8）。
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

# TPU VM 上ではリポジトリ全体が無い場合があるため、import 失敗時はインライン実装を使う
try:
    # リポジトリ ルート想定（scripts/ の親）を path に足す
    _ROOT = Path(__file__).resolve().parent.parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from src.llm.truncation import truncate_for_model  # type: ignore
except Exception:  # noqa: BLE001
    # Gemma 4 31B 予算: 20000 文字
    def truncate_for_model(text: str, model: str, doc_category: str | None = None) -> str:
        if text is None:
            return ""
        if doc_category == "決算短信":
            return text
        return text[:20000]

VLLM_URL = "http://localhost:8000/v1/chat/completions"
MODEL = "google/gemma-4-31B-it"
CONCURRENCY = 8
INPUT_JSONL = Path(sys.argv[1] if len(sys.argv) > 1 else "gemma4_haito_test_texts.jsonl")
OUTPUT_JSON = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/gemma4_tpu_haito_results.json")


def build_prompt(doc_title: str, text: str) -> str:
    return f"""以下のTDnet適時開示文書を分析し、「配当変更（増減配）」に該当するか判定してください。

【判定基準】以下のいずれかに該当すれば True:
- 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
- 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
- 前期比・前回予想比で配当金額が変更されている記載
- 決算短信の配当欄で前期と当期（または予想）の配当金額に数値差がある

文書タイトル: {doc_title}
テキスト:
{truncate_for_model(text, "gemma-4-31b", doc_category=None)}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。他のテキストは一切出力しないこと。
{{ "haito_henkou": true, "reason": "判定理由（30字以内）" }}"""


async def call_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, row: dict) -> dict:
    async with sem:
        prompt = build_prompt(row["doc_title"], row["full_text"])
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                VLLM_URL,
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.0,
                },
                timeout=300,
            )
            elapsed = time.perf_counter() - t0
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            parsed = None
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", content, re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass
            pred = bool(parsed.get("haito_henkou")) if parsed else None
            reason = (parsed or {}).get("reason", "")
            return {
                "doc_id": row["doc_id"],
                "ticker": row["ticker"],
                "doc_title": row["doc_title"],
                "gemma26b_prior": row["gemma26b_prior"],
                "gemma4_pred": pred,
                "gemma4_reason": reason,
                "elapsed_sec": elapsed,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "raw": content[:300],
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            return {
                "doc_id": row["doc_id"],
                "ticker": row["ticker"],
                "doc_title": row["doc_title"],
                "gemma26b_prior": row["gemma26b_prior"],
                "gemma4_pred": None,
                "error": repr(e),
                "elapsed_sec": elapsed,
            }


async def main() -> None:
    rows = [json.loads(line) for line in INPUT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"[start] samples={len(rows)} concurrency={CONCURRENCY} model={MODEL}")

    sem = asyncio.Semaphore(CONCURRENCY)
    t_all0 = time.perf_counter()
    async with httpx.AsyncClient() as client:
        tasks = [call_one(client, sem, r) for r in rows]
        results: list[dict] = []
        for i, t in enumerate(asyncio.as_completed(tasks), 1):
            r = await t
            results.append(r)
            if i % 10 == 0 or i == len(rows):
                ok = sum(1 for x in results if x.get("gemma4_pred") is True)
                err = sum(1 for x in results if x.get("gemma4_pred") is None)
                print(f"[progress] {i}/{len(rows)} detected={ok} errors={err}")
    total_sec = time.perf_counter() - t_all0

    total = len(results)
    detected_true = sum(1 for r in results if r.get("gemma4_pred") is True)
    detected_false = sum(1 for r in results if r.get("gemma4_pred") is False)
    errors = sum(1 for r in results if r.get("gemma4_pred") is None)

    disagree_rows = [r for r in results if r.get("gemma26b_prior") is False]
    agree_rows = [r for r in results if r.get("gemma26b_prior") is True]
    recall_on_disagree = sum(1 for r in disagree_rows if r.get("gemma4_pred") is True) / max(len(disagree_rows), 1)
    recall_on_agree = sum(1 for r in agree_rows if r.get("gemma4_pred") is True) / max(len(agree_rows), 1)
    overall_recall = detected_true / total

    summary = {
        "total": total,
        "detected_true": detected_true,
        "detected_false": detected_false,
        "errors": errors,
        "overall_recall": overall_recall,
        "disagree_70_recall": recall_on_disagree,
        "disagree_70_count": len(disagree_rows),
        "agree_30_recall": recall_on_agree,
        "agree_30_count": len(agree_rows),
        "total_sec": total_sec,
        "avg_sec_per_doc": total_sec / total,
        "avg_prompt_tokens": sum((r.get("prompt_tokens") or 0) for r in results) / total,
        "avg_completion_tokens": sum((r.get("completion_tokens") or 0) for r in results) / total,
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    OUTPUT_JSON.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nsaved: {OUTPUT_JSON}")


if __name__ == "__main__":
    asyncio.run(main())
