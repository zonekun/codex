"""Phase 4: SHAREHOLDER_COMPOSITION の TOP_SHAREHOLDER_IS_PUBLIC / TOP_SHAREHOLDER_TICKER を更新。

アルゴリズム:
  1. BQ STOCK_CODE_LIST (EXCHANGE=TSE) を読み込み → 正規化社名→ticker マップ作成
  2. BQ SHAREHOLDER_COMPOSITION から TOP_SHAREHOLDER_IS_PUBLIC IS NULL の unique 株主名を取得
  3. 正規化マッチで 80-90% カバー → BQ UPDATE
  4. 残 unmatched を Gemini (gemini-3-flash-preview, 個人APIキー) でバッチ判定
     - プロンプト: 株主名 + 全東証銘柄一覧（CSV） → JSON {is_listed, ticker, confidence}
  5. Gemini結果も BQ UPDATE

Usage:
  PYTHONUTF8=1 python scripts/apply_shareholder_listing_flag.py --dry-run
  PYTHONUTF8=1 python scripts/apply_shareholder_listing_flag.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
from google import genai
from google.cloud import bigquery
from google.oauth2 import service_account

sys.path.insert(0, str(Path(__file__).parent))
from flag_activists_in_list import normalize  # 正規化関数を流用

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

KEY_FILE = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
TABLE_ID = f"{PROJECT}.STOCK.SHAREHOLDER_COMPOSITION"
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_BATCH_SIZE = 50  # 1プロンプトで判定する株主数


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def build_normalized_map(bq: bigquery.Client) -> dict[str, str]:
    """正規化後社名 → ticker のマップ。TSE 4桁ticker のみ。"""
    sql = f"""
    SELECT DISTINCT TICKER, STOCK_NAME
    FROM `{PROJECT}.STOCK.STOCK_CODE_LIST`
    WHERE EXCHANGE = 'TSE' AND STOCK_NAME IS NOT NULL
    """
    df = bq.query(sql).to_dataframe()
    return {normalize(name): ticker for name, ticker in zip(df["STOCK_NAME"], df["TICKER"])}


def get_unique_top_shareholders(bq: bigquery.Client) -> list[str]:
    """判定未実施の unique TOP_SHAREHOLDER_NAME を返す。"""
    sql = f"""
    SELECT DISTINCT TOP_SHAREHOLDER_NAME
    FROM `{TABLE_ID}`
    WHERE TOP_SHAREHOLDER_NAME IS NOT NULL
      AND TOP_SHAREHOLDER_IS_PUBLIC IS NULL
    ORDER BY TOP_SHAREHOLDER_NAME
    """
    df = bq.query(sql).to_dataframe()
    return df["TOP_SHAREHOLDER_NAME"].tolist()


def normalize_match(
    names: list[str], name_to_ticker: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """正規化厳密マッチ。(name→ticker), 未マッチ一覧を返す。

    部分マッチは過剰検出リスクあり（例: "Apaman Network" が 441A に誤ヒット）のため不採用。
    厳密一致以外はGeminiフォールバックに任せる。
    """
    matched: dict[str, str] = {}
    unmatched: list[str] = []
    for name in names:
        norm = normalize(name)
        if norm and norm in name_to_ticker:
            matched[name] = name_to_ticker[norm]
        else:
            unmatched.append(name)
    return matched, unmatched


GEMINI_PROMPT_TEMPLATE = """あなたは日本の株式市場の専門家です。以下の株主名のリストから、それぞれが東京証券取引所の上場企業に該当するかを判定してください。

## 上場企業リスト（一部、参考）
{listed_sample}

## 判定ルール
1. 株主名が明らかに日本の上場企業（株式会社・ホールディングス等）の場合: `is_listed=true`、該当 ticker を返す
2. 個人名（例: 孫 正義、滝崎武光）: `is_listed=false`
3. 信託銀行の「信託口」名義、カストディバンク常任代理人名: `is_listed=false`（実質的な株主は裏の機関投資家であり上場企業でない）
4. 外国法人（JP MORGAN, SSBTC CLIENT, CITIBANK 等）: `is_listed=false`
5. 政府・公的機関: `is_listed=false`

## 対象株主名リスト
{shareholders_json}

## 出力
各株主名について、`is_listed`, `ticker` (4桁, 不明なら null), `confidence` (0-100) を含む JSON 配列で返してください。
"""


def gemini_batch_resolve(
    client: genai.Client, shareholders: list[str], listed_sample: str
) -> list[dict]:
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        listed_sample=listed_sample[:10000],
        shareholders_json=json.dumps(shareholders, ensure_ascii=False),
    )
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING"},
                "is_listed": {"type": "BOOLEAN"},
                "ticker": {"type": "STRING", "nullable": True},
                "confidence": {"type": "INTEGER"},
            },
            "required": ["name", "is_listed", "ticker", "confidence"],
        },
    }
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[prompt],
        config={"response_schema": schema, "response_mime_type": "application/json"},
    )
    return json.loads(resp.text)


def update_bq(bq: bigquery.Client, name_to_result: dict[str, tuple[bool, Optional[str]]]) -> None:
    """辞書で一括UPDATE（parameterized query、一時テーブル JOIN 方式）。"""
    if not name_to_result:
        return
    # 一時テーブルに結果をロード → UPDATE ... FROM で JOIN
    import uuid
    tmp_table = f"{PROJECT}.STOCK._tmp_shareholder_listing_{uuid.uuid4().hex[:8]}"
    rows = [
        {
            "NAME": name,
            "IS_PUBLIC": is_public,
            "TICKER": ticker,
        }
        for name, (is_public, ticker) in name_to_result.items()
    ]
    schema = [
        bigquery.SchemaField("NAME", "STRING"),
        bigquery.SchemaField("IS_PUBLIC", "BOOL"),
        bigquery.SchemaField("TICKER", "STRING"),
    ]
    table = bigquery.Table(tmp_table, schema=schema)
    table = bq.create_table(table)
    errors = bq.insert_rows_json(tmp_table, rows)
    if errors:
        print(f"  tmp insert err: {errors[0]}")
        bq.delete_table(tmp_table, not_found_ok=True)
        return
    # streaming buffer の影響回避のため 少し待つ + LOAD job で代替可能だが今回は streaming でOK
    sql = f"""
    UPDATE `{TABLE_ID}` t
    SET
      TOP_SHAREHOLDER_IS_PUBLIC = s.IS_PUBLIC,
      TOP_SHAREHOLDER_TICKER = s.TICKER
    FROM `{tmp_table}` s
    WHERE t.TOP_SHAREHOLDER_NAME = s.NAME
    """
    try:
        bq.query(sql).result()
    finally:
        bq.delete_table(tmp_table, not_found_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-gemini", action="store_true", help="Gemini判定をスキップ、正規化マッチのみ")
    args = parser.parse_args()

    bq = get_bq_client()
    print("[info] loading STOCK_CODE_LIST...")
    name_to_ticker = build_normalized_map(bq)
    print(f"[info] listed companies: {len(name_to_ticker)}")

    print("[info] fetching unique unresolved shareholders...")
    shareholders = get_unique_top_shareholders(bq)
    print(f"[info] to resolve: {len(shareholders)}")

    # Step 1: 正規化マッチ
    matched, unmatched = normalize_match(shareholders, name_to_ticker)
    print(f"[info] normalized match: {len(matched)} / {len(shareholders)} = {len(matched)*100/max(1,len(shareholders)):.1f}%")
    print(f"[info] unmatched: {len(unmatched)}")

    # Step 2: Gemini batch
    gemini_results: dict[str, tuple[bool, Optional[str]]] = {}
    if unmatched and not args.skip_gemini:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("[warn] GEMINI_API_KEY not set, skipping Gemini batch")
        else:
            client = genai.Client(api_key=api_key)
            listed_sample = "\n".join(
                f"{t}: {name}" for name, t in list(name_to_ticker.items())[:500]
            )
            for i in range(0, len(unmatched), GEMINI_BATCH_SIZE):
                batch = unmatched[i:i + GEMINI_BATCH_SIZE]
                print(f"  [{i+1}-{i+len(batch)}/{len(unmatched)}] Gemini...")
                try:
                    results = gemini_batch_resolve(client, batch, listed_sample)
                    for r in results:
                        gemini_results[r["name"]] = (r.get("is_listed", False), r.get("ticker"))
                except Exception as e:
                    print(f"  batch fail: {e}")
                time.sleep(1.0)

    # Step 3: 統合 & UPDATE
    all_results: dict[str, tuple[bool, Optional[str]]] = {}
    for name, ticker in matched.items():
        all_results[name] = (True, ticker)
    all_results.update(gemini_results)
    # 残 unmatched (Gemini も失敗) は is_listed=False 扱い
    for name in unmatched:
        if name not in all_results:
            all_results[name] = (False, None)

    print(f"\n=== 集計 ===")
    n_listed = sum(1 for is_p, _ in all_results.values() if is_p)
    print(f"is_listed=True: {n_listed}/{len(all_results)}")

    if args.dry_run:
        print("dry-run, skipping BQ UPDATE")
        for name, (is_p, t) in list(all_results.items())[:20]:
            print(f"  {name} → public={is_p}, ticker={t}")
        return

    # バッチUPDATE (一度に全件は大きすぎるので500件ずつ)
    items = list(all_results.items())
    for i in range(0, len(items), 500):
        batch = dict(items[i:i + 500])
        print(f"  UPDATE batch {i+1}-{i+len(batch)}")
        update_bq(bq, batch)


if __name__ == "__main__":
    main()
