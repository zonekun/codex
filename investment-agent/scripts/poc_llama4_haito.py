"""Llama 4 Maverick PoC: 配当変更(増減配) 検出テスト.

Vertex AI Model Garden MaaS 経由で Llama 4 Maverick (400B/17B MoE) を呼び出し、
既存の233件の配当変更サンプル（Geminiが検出した決算短信）での検出率を測定する。

比較対象:
- Gemini (正解): 233/233 (100%)
- Gemma 26B MaaS: 155/233 (66.5%)
- Llama 4 Maverick: ??/233 ← 今回テスト

Usage:
    PYTHONUTF8=1 python scripts/poc_llama4_haito.py
    PYTHONUTF8=1 python scripts/poc_llama4_haito.py --limit 20  # 動作確認
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

JST = ZoneInfo("Asia/Tokyo")
logger = structlog.get_logger()

GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
CACHE_DIR = Path("C:/tmp/gemma4_poc/cache")
V5_CACHE = CACHE_DIR / "v5_202401.json"
OUT_CACHE = CACHE_DIR / "llama4_maverick_haito.json"

# Vertex AI MaaS 経由（OpenAI互換 Chat Completions API）
# us-east5 が Llama 4 MaaS の提供リージョン
LLAMA_MODEL = "meta/llama-4-maverick-17b-128e-instruct-maas"
LOCATION = "us-east5"


def _get_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_bq_client():
    from google.cloud import bigquery
    return bigquery.Client(project=GCP_PROJECT, credentials=_get_credentials())


def _get_access_token() -> str:
    """SA 認証情報からアクセストークンを取得."""
    import google.auth.transport.requests
    creds = _get_credentials()
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def load_haito_samples() -> list[dict]:
    """v5 キャッシュから配当変更(増減配) サンプルを抽出."""
    with open(V5_CACHE, encoding="utf-8") as f:
        data = json.load(f)
    details = data["details"]
    haito = [
        r for r in details
        if "配当変更（増減配）" in (r.get("gemini_subs") or [])
    ]
    logger.info("haito_samples_loaded", count=len(haito))
    return haito


def fetch_full_texts(bq_client, doc_ids: list[str]) -> dict[str, dict]:
    """BQ から full_text を取得."""
    ids_str = ",".join(f"'{d}'" for d in doc_ids)
    sql = f"""
    SELECT
        DOC_ID, TICKER, FILER_NAME, DOC_TITLE,
        STRING_AGG(CHUNK_TEXT, '' ORDER BY CHUNK_TEXT) AS full_text
    FROM `{BQ_TABLE}`
    WHERE DOC_ID IN ({ids_str})
    GROUP BY DOC_ID, TICKER, FILER_NAME, DOC_TITLE
    """
    rows = list(bq_client.query(sql).result())
    return {
        row.DOC_ID: {
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "filer_name": row.FILER_NAME,
            "doc_title": row.DOC_TITLE,
            "full_text": row.full_text or "",
        }
        for row in rows
    }


def build_prompt(doc_title: str, text: str) -> str:
    """配当変更(増減配) 判定専用プロンプト."""
    return f"""以下のTDnet適時開示文書を分析し、「配当変更（増減配）」に該当するか判定してください。

【判定基準】以下のいずれかに該当すれば True:
- 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
- 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
- 前期比・前回予想比で配当金額が変更されている記載
- 決算短信の配当欄で前期と当期（または予想）の配当金額に数値差がある

文書タイトル: {doc_title}
テキスト:
{text[:8000]}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。
{{ "haito_henkou": true, "reason": "判定理由（30字以内）" }}"""


def call_llama4(access_token: str, prompt: str, max_retries: int = 5) -> dict | None:
    """Llama 4 Maverick MaaS を呼び出す（リトライ間隔延長版）."""
    import requests

    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/"
        f"{GCP_PROJECT}/locations/{LOCATION}/endpoints/openapi/chat/completions"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 200,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code != 200:
                logger.warning(
                    "llama_http_error",
                    status=resp.status_code,
                    body=resp.text[:200],
                    attempt=attempt,
                )
                # 429: 長めに待機（クォータ回復）/ 500,503: 指数バックオフ
                if resp.status_code == 429:
                    time.sleep(30 + 10 * attempt)
                    continue
                if resp.status_code in (500, 503):
                    time.sleep(5 + 5 * attempt)
                    continue
                return None

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # JSONパース
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                import re
                m = re.search(r"\{[^}]+\}", content)
                if m:
                    return json.loads(m.group())
                logger.warning("json_parse_failed", content=content[:200])
        except Exception as e:
            logger.warning("llama_call_error", error=str(e)[:200], attempt=attempt)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def run_poc(limit: int | None = None) -> None:
    """PoCメイン."""
    # 1. 配当変更サンプル取得
    haito_samples = load_haito_samples()
    if limit:
        haito_samples = haito_samples[:limit]
        logger.info("limited_to", count=len(haito_samples))

    # 2. BQから full_text 取得
    bq_client = _get_bq_client()
    doc_ids = [s["doc_id"] for s in haito_samples]
    docs_map = fetch_full_texts(bq_client, doc_ids)
    logger.info("full_texts_fetched", count=len(docs_map))

    # 3. Llama 4 Maverick で判定
    access_token = _get_access_token()
    token_issued_at = time.time()
    results = []
    detected = 0
    failed = 0

    for i, sample in enumerate(haito_samples):
        doc_id = sample["doc_id"]
        if doc_id not in docs_map:
            logger.warning("doc_not_found", doc_id=doc_id)
            failed += 1
            continue

        # トークン更新（50分経過したら）
        if time.time() - token_issued_at > 50 * 60:
            access_token = _get_access_token()
            token_issued_at = time.time()

        doc = docs_map[doc_id]
        prompt = build_prompt(doc["doc_title"], doc["full_text"])
        result = call_llama4(access_token, prompt)

        if result is None:
            status = "FAILED"
            haito = None
            reason = None
            failed += 1
        else:
            status = "OK"
            haito = bool(result.get("haito_henkou", False))
            reason = result.get("reason", "")[:50]
            if haito:
                detected += 1

        results.append({
            "doc_id": doc_id,
            "ticker": sample["ticker"],
            "doc_title": sample["doc_title"],
            "gemini": True,  # 全サンプルGemini検出済み
            "gemma26b": "配当変更（増減配）" in (sample.get("gemma_subs") or []),
            "llama4_maverick": haito,
            "llama4_reason": reason,
            "status": status,
        })

        if (i + 1) % 10 == 0 or i == len(haito_samples) - 1:
            logger.info(
                "progress",
                done=i + 1,
                total=len(haito_samples),
                detected=detected,
                failed=failed,
                detection_rate=f"{detected / max(1, i + 1 - failed):.1%}",
            )

        time.sleep(3)  # レートリミット対策（Llama 4 MaaS は低QPM）

    # 4. 結果保存
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_CACHE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("results_saved", path=str(OUT_CACHE))

    # 5. サマリー
    total_ok = sum(1 for r in results if r["status"] == "OK")
    gemma_detected = sum(1 for r in results if r["gemma26b"])
    llama_detected = sum(1 for r in results if r["llama4_maverick"] is True)

    print("\n" + "=" * 60)
    print("Llama 4 Maverick 配当変更(増減配) 検出 PoC 結果")
    print("=" * 60)
    print(f"対象サンプル数: {len(results)} 件（全てGemini検出済み=正解）")
    print(f"API呼び出し成功: {total_ok} 件")
    print(f"API呼び出し失敗: {failed} 件")
    print("-" * 60)
    print(f"Gemini 検出       : {len(results)} / {len(results)} (100.0%)  ← 正解")
    print(f"Gemma 26B 検出    : {gemma_detected} / {len(results)} "
          f"({gemma_detected / len(results):.1%})")
    print(f"Llama 4 Maverick : {llama_detected} / {len(results)} "
          f"({llama_detected / len(results):.1%})")
    print("=" * 60)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="件数制限（動作確認用）")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_poc(limit=args.limit)
