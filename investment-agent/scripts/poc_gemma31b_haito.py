"""Gemma 4 31B (llama.cpp on GCE VM) PoC: 配当変更(増減配) 検出テスト.

GCE VM (gemma31b-poc, us-central1-a) で起動している llama.cpp server (port 8080)
に SSH tunnel 経由で接続し、233件の配当変更サンプルを分類。

事前準備（別シェルで実行、PoC中は維持）:
    gcloud compute ssh gemma31b-poc --zone=us-central1-a --tunnel-through-iap \\
      -- -L 8080:localhost:8080 -N

Usage:
    PYTHONUTF8=1 python scripts/poc_gemma31b_haito.py --limit 3
    PYTHONUTF8=1 python scripts/poc_gemma31b_haito.py
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.truncation import truncate_for_model  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
logger = structlog.get_logger()

GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
CACHE_DIR = Path("C:/tmp/gemma4_poc/cache")
V5_CACHE = CACHE_DIR / "v5_202401.json"
OUT_CACHE = CACHE_DIR / "gemma4_31b_haito.json"

# llama.cpp server (SSH tunnel 経由)
LLAMA_URL = "http://localhost:8080/v1/chat/completions"

# L4 GPU で 20 tok/s 程度 + prompt processing なので 1件 40秒程度見込
REQUEST_INTERVAL_SEC = 0.5


def _get_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_bq_client():
    from google.cloud import bigquery
    return bigquery.Client(project=GCP_PROJECT, credentials=_get_credentials())


def load_haito_samples() -> list[dict]:
    with open(V5_CACHE, encoding="utf-8") as f:
        data = json.load(f)
    haito = [
        r for r in data["details"]
        if "配当変更（増減配）" in (r.get("gemini_subs") or [])
    ]
    logger.info("haito_samples_loaded", count=len(haito))
    return haito


def fetch_full_texts(bq_client, doc_ids: list[str]) -> dict[str, dict]:
    ids_str = ",".join(f"'{d}'" for d in doc_ids)
    sql = f"""
    SELECT DOC_ID, TICKER, DOC_TITLE,
        STRING_AGG(CHUNK_TEXT, '' ORDER BY CHUNK_TEXT) AS full_text
    FROM `{BQ_TABLE}`
    WHERE DOC_ID IN ({ids_str})
    GROUP BY DOC_ID, TICKER, DOC_TITLE
    """
    rows = list(bq_client.query(sql).result())
    return {
        row.DOC_ID: {
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "doc_title": row.DOC_TITLE,
            "full_text": row.full_text or "",
        }
        for row in rows
    }


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


def call_gemma31b(prompt: str, max_retries: int = 3) -> dict | None:
    """llama.cpp server を呼び出す."""
    import requests

    # Gemma 4 thinking モードでは content 前に reasoning が出る → 2000 tokens 確保
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 2000,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(LLAMA_URL, json=payload, timeout=600)
            if resp.status_code != 200:
                logger.warning("http_error", status=resp.status_code,
                               body=resp.text[:200], attempt=attempt)
                time.sleep(5 * (attempt + 1))
                continue

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            # Gemma 4 の thinking モードだと content が空で reasoning_content に入る
            if not content:
                content = (data["choices"][0]["message"]
                           .get("reasoning_content") or "").strip()

            try:
                return json.loads(content)
            except json.JSONDecodeError:
                import re
                m = re.search(r"\{[^}]+\}", content)
                if m:
                    return json.loads(m.group())
                logger.warning("json_parse_failed", content=content[:200])
        except Exception as e:
            logger.warning("call_error", error=str(e)[:200], attempt=attempt)
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
    return None


def run_poc(limit: int | None = None) -> None:
    haito_samples = load_haito_samples()
    if limit:
        haito_samples = haito_samples[:limit]
        logger.info("limited_to", count=len(haito_samples))

    bq_client = _get_bq_client()
    doc_ids = [s["doc_id"] for s in haito_samples]
    docs_map = fetch_full_texts(bq_client, doc_ids)
    logger.info("full_texts_fetched", count=len(docs_map))

    results = []
    detected = 0
    failed = 0
    total_time_sec = 0.0

    for i, sample in enumerate(haito_samples):
        doc_id = sample["doc_id"]
        if doc_id not in docs_map:
            logger.warning("doc_not_found", doc_id=doc_id)
            failed += 1
            continue

        doc = docs_map[doc_id]
        prompt = build_prompt(doc["doc_title"], doc["full_text"])

        call_start = time.time()
        result = call_gemma31b(prompt)
        call_sec = time.time() - call_start
        total_time_sec += call_sec

        if result is None:
            status = "FAILED"
            haito = None
            reason = None
            failed += 1
        else:
            status = "OK"
            haito = bool(result.get("haito_henkou", False))
            reason = (result.get("reason") or "")[:50]
            if haito:
                detected += 1

        results.append({
            "doc_id": doc_id,
            "ticker": sample["ticker"],
            "doc_title": sample["doc_title"],
            "gemini": True,
            "gemma26b": "配当変更（増減配）" in (sample.get("gemma_subs") or []),
            "gemma4_31b": haito,
            "gemma4_reason": reason,
            "call_sec": round(call_sec, 1),
            "status": status,
        })

        if (i + 1) % 5 == 0 or i == len(haito_samples) - 1:
            ok_count = sum(1 for r in results if r["status"] == "OK")
            avg_sec = total_time_sec / max(1, i + 1 - failed)
            logger.info(
                "progress",
                done=i + 1,
                total=len(haito_samples),
                detected=detected,
                failed=failed,
                detection_rate=(
                    f"{detected / max(1, ok_count):.1%}" if ok_count else "0%"
                ),
                avg_sec=f"{avg_sec:.1f}",
            )
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(OUT_CACHE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        if i < len(haito_samples) - 1:
            time.sleep(REQUEST_INTERVAL_SEC)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_CACHE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("results_saved", path=str(OUT_CACHE))

    total_ok = sum(1 for r in results if r["status"] == "OK")
    gemma26b_detected = sum(1 for r in results if r["gemma26b"])
    gemma31b_detected = sum(1 for r in results if r["gemma4_31b"] is True)

    print("\n" + "=" * 60)
    print("Gemma 4 31B Dense (L4 Q4_K_M) 配当変更 検出 PoC")
    print("=" * 60)
    print(f"対象サンプル数: {len(results)} 件（全てGemini検出済み=正解）")
    print(f"API呼び出し成功: {total_ok} 件 / 失敗: {failed} 件")
    print(f"平均推論時間: {total_time_sec / max(1, total_ok):.1f} 秒/件")
    print("-" * 60)
    print(f"Gemini 検出       : {len(results)} / {len(results)} (100.0%)  ← 正解")
    print(f"Gemma 26B MaaS    : {gemma26b_detected} / {len(results)} "
          f"({gemma26b_detected / len(results):.1%})")
    print(f"Gemma 4 31B Dense : {gemma31b_detected} / {len(results)} "
          f"({gemma31b_detected / len(results):.1%})")
    print("=" * 60)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_poc(limit=args.limit)
