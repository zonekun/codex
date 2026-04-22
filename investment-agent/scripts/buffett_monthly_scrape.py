"""
TDnet BQ（TDNET_DOCUMENTS_ENHANCED）の月次開示カテゴリから
月次情報開示企業一覧と項目構造を取得し GCS に保存する。

GCS 保存先:
  gs://stock_data_1930932/monthly/company_list.json
  gs://stock_data_1930932/monthly/meta/{ticker}/structure.json

月次データ本文は GCS の TDnet PDF から取得する。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from google.cloud import bigquery, storage

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
RUNTIME    = "cloudrun" if os.environ.get("CLOUD_RUN_JOB") else "local"
GCS_BUCKET = "stock_data_1930932"
GCS_MONTHLY = "monthly"
GCS_META = "monthly/meta"
BQ_PROJECT = "gmailpj-357912"
BQ_TABLE   = f"{BQ_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
JST        = timezone(timedelta(hours=9))

KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")


def get_bq_client():
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return bigquery.Client(project=BQ_PROJECT, credentials=creds)
    return bigquery.Client(project=BQ_PROJECT)


def get_gcs_client():
    if RUNTIME == "local":
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(KEY_FILE)
        return storage.Client(project=BQ_PROJECT, credentials=creds)
    return storage.Client()


def read_json(gcs: storage.Client, gcs_path: str) -> Optional[dict]:
    """GCS から JSON を読み込む。存在しなければ None。"""
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(gcs_path)
        return json.loads(blob.download_as_text())
    except Exception:
        return None


def upload_json(gcs: storage.Client, gcs_path: str, data: object) -> None:
    bucket = gcs.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    print(f"  → GCS: gs://{GCS_BUCKET}/{gcs_path}", flush=True)


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ──────────────────────────────────────────────
# 月次開示カテゴリの企業一覧を BQ から取得
# ──────────────────────────────────────────────

def fetch_companies(bq: bigquery.Client) -> list[dict]:
    """MAIN_CATEGORY = '月次開示' の企業を BQ から取得。"""
    q = f"""
    SELECT
      TICKER,
      FILER_NAME,
      COUNT(DISTINCT FILE_NAME)  AS doc_count,
      MAX(SUBMISSION_DATE)        AS latest_date,
      ARRAY_AGG(DOC_TITLE ORDER BY SUBMISSION_DATE DESC LIMIT 1)[OFFSET(0)] AS latest_title,
      ARRAY_AGG(FILE_NAME  ORDER BY SUBMISSION_DATE DESC LIMIT 3)            AS recent_files
    FROM `{BQ_TABLE}`
    WHERE MAIN_CATEGORY = '月次開示'
    GROUP BY TICKER, FILER_NAME
    ORDER BY latest_date DESC
    """
    rows = list(bq.query(q).result())
    companies = []
    for row in rows:
        companies.append({
            "ticker":       row.TICKER,
            "name":         row.FILER_NAME,
            "doc_count":    row.doc_count,
            "latest_date":  str(row.latest_date),
            "latest_title": row.latest_title,
            "recent_files": list(row.recent_files),
        })
    log(f"月次開示企業: {len(companies)} 社")
    return companies


# ──────────────────────────────────────────────
# 各企業の月次データ構造を BQ チャンクテキストから抽出
# ──────────────────────────────────────────────

def fetch_structure(bq: bigquery.Client, ticker: str) -> list[dict]:
    """指定企業の最新月次開示文書のチャンクテキストから項目構造を抽出。"""
    q = f"""
    SELECT CHUNK_TEXT, DOC_TITLE, SUBMISSION_DATE
    FROM `{BQ_TABLE}`
    WHERE MAIN_CATEGORY = '月次開示'
      AND TICKER = @ticker
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 30
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    rows = list(bq.query(q, job_config=job_config).result())
    if not rows:
        return []

    # 全チャンクテキストを結合して項目を抽出
    all_text = "\n".join(r.CHUNK_TEXT or "" for r in rows)
    return parse_monthly_items(all_text)


def parse_monthly_items(text: str) -> list[dict]:
    """テキストから月次項目（名前・単位）を抽出するパーサー。"""
    items = []
    seen = set()

    # パターン1: 「項目名（単位）」 形式  例: 全店 売上（百万円）
    for m in re.finditer(r"([^\n（\(\s]{2,30})\s*[（\(]([^）\)]{1,20})[）\)]", text):
        name = m.group(1).strip()
        unit = m.group(2).strip()
        # 数値単位らしいものだけ採用
        unit_keywords = ["円", "百万", "千円", "件", "人", "台", "%", "％", "棟", "店", "kg", "t", "枚"]
        if any(kw in unit for kw in unit_keywords) and name not in seen:
            seen.add(name)
            items.append({"name": name, "unit": unit})

    # パターン2: テーブルヘッダ行を推定（数字や日付の前に出てくる行）
    # 行ごとに処理
    lines = text.split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        # 月次の「項目」行っぽいもの: 2〜20文字の日本語テキスト、数字なし
        if (2 <= len(line) <= 25
                and re.search(r"[一-龥ぁ-んァ-ン]", line)
                and not re.search(r"\d{4}", line)
                and not any(skip in line for skip in ["株式会社", "コード", "年月", "年度", "前年", "前月"])
                and line not in seen):
            # 次の行に数値があれば項目と判断
            next_lines = lines[i+1:i+3] if i+1 < len(lines) else []
            if any(re.search(r"\d{3,}", nl) for nl in next_lines):
                seen.add(line)
                items.append({"name": line, "unit": ""})

    return items[:30]  # 上限30項目


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main() -> None:
    log("=== 月次情報構造 収集開始（TDnet BQ ベース）===")
    bq  = get_bq_client()
    gcs = get_gcs_client()

    # 1. 企業一覧取得
    companies = fetch_companies(bq)
    if not companies:
        log("月次開示企業が見つかりませんでした。")
        raise SystemExit(1)

    # 2. 各企業の構造を抽出して GCS に保存
    success = 0
    for i, company in enumerate(companies):
        ticker = company["ticker"]
        name   = company["name"]
        log(f"[{i+1}/{len(companies)}] {ticker} {name}")

        items = fetch_structure(bq, ticker)
        log(f"  → {len(items)} 項目検出")

        structure_data = {
            "ticker":        ticker,
            "name":          name,
            "scraped_at":    datetime.now(JST).isoformat(),
            "source":        "tdnet_bq",
            "doc_count":     company["doc_count"],
            "latest_date":   company["latest_date"],
            "latest_title":  company["latest_title"],
            "monthly_items": items,
        }
        # 既存 structure.json の手動追加メトリクス（manual_add フラグ付き）を保持
        existing_structure = read_json(gcs, f"{GCS_META}/{ticker}/structure.json")
        if existing_structure:
            # manual_override=true なら全体スキップ
            if existing_structure.get("manual_override"):
                log(f"  → structure.json manual_override=true → スキップ")
                success += 1
                continue
            # 手動追加メトリクス（metrics / monthly_items 内の manual_add=true）を引き継ぐ
            for key in ("metrics", "monthly_items"):
                old_items = existing_structure.get(key, [])
                manual_items = [m for m in old_items if m.get("manual_add")]
                if manual_items:
                    new_names = {m.get("name", "") for m in structure_data.get(key, items)}
                    for mi in manual_items:
                        if mi.get("name", "") not in new_names:
                            if key == "monthly_items":
                                items.append(mi)
                            else:
                                structure_data.setdefault(key, []).append(mi)
        upload_json(gcs, f"{GCS_META}/{ticker}/structure.json", structure_data)
        success += 1

    # 3. 企業一覧を保存（手動追加企業を保持）
    existing_list = read_json(gcs, f"{GCS_MONTHLY}/company_list.json")
    manual_companies = []
    if existing_list:
        manual_companies = [c for c in existing_list.get("companies", []) if c.get("manual_add")]
    company_list_data = {
        "scraped_at": datetime.now(JST).isoformat(),
        "source":     "tdnet_bq",
        "total":      len(companies),
        "companies": [
            {
                "ticker":      c["ticker"],
                "name":        c["name"],
                "doc_count":   c["doc_count"],
                "latest_date": c["latest_date"],
                "items_count": len(fetch_structure(bq, c["ticker"])),
            }
            for c in companies
        ],
    }
    # 手動追加企業（manual_add=true）をマージ
    if manual_companies:
        existing_tickers = {c["ticker"] for c in company_list_data["companies"]}
        for mc in manual_companies:
            if mc.get("ticker") not in existing_tickers:
                company_list_data["companies"].append(mc)
        company_list_data["total"] = len(company_list_data["companies"])
    upload_json(gcs, f"{GCS_MONTHLY}/company_list.json", company_list_data)

    log(f"=== 完了: {success}/{len(companies)} 社 ===")
    log(f"  企業一覧: gs://{GCS_BUCKET}/{GCS_MONTHLY}/company_list.json")
    log(f"  構造ファイル: gs://{GCS_BUCKET}/{GCS_META}/{{ticker}}/structure.json")


if __name__ == "__main__":
    main()
