"""GCS の monthly/record/*/monthly_records.json を全件取得して CSV に保存する。

出力: data/csv/monthly_records_all.csv
形式: ロング形式 (ticker, company_name, year_month, year, month,
                  source, doc_title, submission_date, field_name, field_value)
"""
import csv
import json
import os
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

KEY_FILE = Path("keys/gcp-service-account.json")
GCS_BUCKET = "stock_data_1930932"
GCS_RECORD = "monthly/record"
OUT_CSV = Path("data/csv/monthly_records_all.csv")


def get_gcs_client():
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE))
    return storage.Client(project="gmailpj-357912", credentials=creds)


def main():
    client = get_gcs_client()
    bucket = client.bucket(GCS_BUCKET)

    # monthly/record/*/monthly_records.json を列挙
    blobs = list(client.list_blobs(bucket, prefix=f"{GCS_RECORD}/"))
    targets = [b for b in blobs if b.name.endswith("/monthly_records.json")]
    print(f"monthly_records.json: {len(targets)}社")

    rows = []
    for i, blob in enumerate(targets, 1):
        ticker = blob.name.split("/")[2]
        try:
            text = blob.download_as_text(encoding="utf-8")
            data = json.loads(text)
        except Exception as e:
            print(f"  [{i}] {ticker} エラー: {e}")
            continue

        company_name = data.get("company_name", "")
        for rec in data.get("records", []):
            base = {
                "ticker": ticker,
                "company_name": company_name,
                "year_month": rec.get("year_month", ""),
                "year": rec.get("year", ""),
                "month": rec.get("month", ""),
                "source": rec.get("source", ""),
                "doc_title": rec.get("doc_title", ""),
                "submission_date": rec.get("submission_date", ""),
            }
            fields = rec.get("fields", {})
            if fields:
                for field_name, field_value in fields.items():
                    rows.append({**base, "field_name": field_name, "field_value": field_value})
            else:
                rows.append({**base, "field_name": "", "field_value": ""})

        print(f"  [{i}/{len(targets)}] {ticker} {company_name}: {data.get('record_count', 0)}件")

    if not rows:
        print("データなし")
        return

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ticker", "company_name", "year_month", "year", "month",
                  "source", "doc_title", "submission_date", "field_name", "field_value"]
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n=== 完了 ===")
    print(f"行数: {len(rows):,} 行")
    print(f"保存: {OUT_CSV}")


if __name__ == "__main__":
    main()
