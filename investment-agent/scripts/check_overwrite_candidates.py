"""NG銘柄のPDF比較: 過去月PDFと最新PDFで同じ月の値が異なるかを確認する。

overwrite_past_months の対象特定スクリプト。
"""
import csv
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pdfplumber
import structlog
from google.cloud import storage
from google.oauth2 import service_account

logger = structlog.get_logger()

PROJECT_ROOT = Path("C:/gdrive/claude/investment-agent")
KEYS_PATH = PROJECT_ROOT / "keys" / "gcp-service-account.json"
BUCKET_NAME = "stock_data_1930932"
ADAPTER_DIR = PROJECT_ROOT / "data" / "monthly_adapters"


def get_gcs_client() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEYS_PATH))
    return storage.Client(credentials=creds, project="gmailpj-357912")


def load_ng_data(csv_path: str) -> dict[str, list[dict]]:
    """NG行をticker別にグループ化して返す。"""
    result: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("match") == "NG":
                result[row["ticker"]].append(row)
    return result


def list_pdf_blobs(client: storage.Client, ticker: str) -> list:
    """GCSからTDNET PDFのblobリストを取得（ファイル名ソート）。"""
    bucket = client.bucket(BUCKET_NAME)
    prefix = f"tdnet/{ticker}/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    pdf_blobs = [b for b in blobs if b.name.endswith(".pdf")]
    pdf_blobs.sort(key=lambda b: b.name)
    return pdf_blobs


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出。"""
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n".join(text_parts)


def extract_tables_from_pdf(pdf_bytes: bytes) -> list[list[list[str]]]:
    """PDFから全テーブルを抽出。"""
    tables = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in page.extract_tables():
                if tbl and len(tbl) >= 2:
                    tables.append(tbl)
    return tables


def parse_year_month_from_title(text: str) -> tuple[int, int] | None:
    """PDFテキストから報告対象年月を推定。"""
    # 令和→西暦変換
    m = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月", text[:500])
    if m:
        return 2018 + int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text[:500])
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def find_numeric_values_in_tables(tables: list, target_month: int) -> dict[str, float]:
    """テーブルから特定月の数値を行ラベル→値で返す。"""
    import unicodedata
    values = {}
    for tbl in tables:
        # ヘッダー行から月列を特定
        month_col_idx = None
        for row_idx, row in enumerate(tbl):
            for col_idx, cell in enumerate(row):
                if cell is None:
                    continue
                cell_n = unicodedata.normalize("NFKC", str(cell)).strip()
                m = re.match(r"(\d{1,2})\s*月[度]?$", cell_n)
                if m and int(m.group(1)) == target_month:
                    month_col_idx = col_idx
                    break
                m = re.search(r"\d{4}年\s*(\d{1,2})月", cell_n)
                if m and int(m.group(1)) == target_month:
                    month_col_idx = col_idx
                    break
            if month_col_idx is not None:
                # この行以降のデータ行を処理
                for data_row in tbl[row_idx + 1:]:
                    if len(data_row) <= month_col_idx:
                        continue
                    label = data_row[0]
                    val_str = data_row[month_col_idx]
                    if label and val_str:
                        label_n = unicodedata.normalize("NFKC", str(label)).strip()
                        val_n = unicodedata.normalize("NFKC", str(val_str)).strip()
                        val_n = val_n.replace(",", "").replace("△", "-").replace("▲", "-")
                        try:
                            val = float(val_n)
                            values[label_n] = val
                        except ValueError:
                            pass
                break
    return values


def compare_pdfs_for_ticker(
    client: storage.Client,
    ticker: str,
    ng_rows: list[dict],
) -> list[dict]:
    """NG銘柄のPDFを比較。最新PDFと過去PDFで同月の値が異なるかを確認。"""
    results = []
    pdf_blobs = list_pdf_blobs(client, ticker)
    if len(pdf_blobs) < 2:
        logger.info(f"  {ticker}: PDFが2件未満、スキップ")
        return results

    # NG行からターゲット年月を取得
    ng_year_months = set()
    for row in ng_rows:
        ng_year_months.add(row["year_month"])

    # 最新PDFを取得
    latest_blob = pdf_blobs[-1]
    try:
        latest_bytes = latest_blob.download_as_bytes()
    except Exception as e:
        logger.error(f"  {ticker}: 最新PDF取得失敗: {e}")
        return results

    latest_text = extract_text_from_pdf(latest_bytes)
    latest_tables = extract_tables_from_pdf(latest_bytes)
    latest_ym = parse_year_month_from_title(latest_text)
    logger.info(f"  {ticker}: 最新PDF={latest_blob.name}, 年月={latest_ym}")

    # NG対象月ごとに過去PDFを探して比較
    for ym_str in sorted(ng_year_months):
        year, month = int(ym_str[:4]), int(ym_str[5:7])

        # 対象月のPDFを探す（ファイル名から年月を推定）
        target_blob = None
        for blob in pdf_blobs[:-1]:  # 最新以外
            blob_bytes = blob.download_as_bytes()
            blob_text = extract_text_from_pdf(blob_bytes)
            blob_ym = parse_year_month_from_title(blob_text)
            if blob_ym and blob_ym == (year, month):
                target_blob = blob
                target_bytes = blob_bytes
                target_tables = extract_tables_from_pdf(blob_bytes)
                break

        if target_blob is None:
            # ファイル名から推定
            for blob in pdf_blobs[:-1]:
                fname = blob.name.split("/")[-1]
                # YYYYMMDD形式を試す
                m = re.search(r"(\d{4})(\d{2})\d{2}", fname)
                if m:
                    f_year, f_month = int(m.group(1)), int(m.group(2))
                    # 提出月=month+1 or month が対象月のPDF
                    if (f_year == year and f_month == month) or \
                       (f_year == year and f_month == month + 1) or \
                       (f_year == year + 1 and month == 12 and f_month == 1):
                        try:
                            target_bytes = blob.download_as_bytes()
                            target_tables = extract_tables_from_pdf(target_bytes)
                            target_blob = blob
                            break
                        except Exception:
                            continue

        if target_blob is None:
            logger.info(f"  {ticker} {ym_str}: 対象月PDF見つからず")
            continue

        # 最新PDFから対象月のデータを抽出
        latest_vals = find_numeric_values_in_tables(latest_tables, month)
        # 対象月PDFから対象月のデータを抽出
        target_vals = find_numeric_values_in_tables(target_tables, month)

        if not latest_vals or not target_vals:
            logger.info(f"  {ticker} {ym_str}: テーブルからデータ抽出できず (latest={len(latest_vals)}, target={len(target_vals)})")
            continue

        # 共通のラベルで値を比較
        for label in set(latest_vals.keys()) & set(target_vals.keys()):
            lv = latest_vals[label]
            tv = target_vals[label]
            if abs(lv - tv) > 0.01:
                results.append({
                    "ticker": ticker,
                    "year_month": ym_str,
                    "label": label,
                    "latest_pdf": latest_blob.name.split("/")[-1],
                    "latest_value": lv,
                    "target_pdf": target_blob.name.split("/")[-1],
                    "target_value": tv,
                    "diff": round(abs(lv - tv), 2),
                })

    return results


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "/c/tmp/buffett_compare_20260410_094735.csv"
    ng_data = load_ng_data(csv_path)

    logger.info(f"NG銘柄数: {len(ng_data)}")

    client = get_gcs_client()

    # 全NG銘柄をチェック（overwrite_past_months設定有無を問わず）
    all_results: list[dict] = []
    already_set: list[str] = []
    candidates: list[str] = []

    for ticker in sorted(ng_data.keys()):
        adapter_path = ADAPTER_DIR / f"{ticker}.json"

        # adapter確認
        has_overwrite = False
        if adapter_path.exists():
            with open(adapter_path, encoding="utf-8") as f:
                adapter = json.load(f)
            has_overwrite = adapter.get("overwrite_past_months", False)

        logger.info(f"\n{'='*50}")
        logger.info(f"チェック中: {ticker} (overwrite_past_months={has_overwrite})")

        try:
            results = compare_pdfs_for_ticker(client, ticker, ng_data[ticker])
        except Exception as e:
            logger.error(f"  {ticker}: エラー: {e}")
            continue

        if results:
            logger.info(f"  >>> {ticker}: {len(results)}件の値差分を検出!")
            for r in results:
                logger.info(f"    {r['year_month']} {r['label']}: "
                          f"最新PDF={r['latest_value']} vs 過去PDF={r['target_value']} "
                          f"(diff={r['diff']})")
            all_results.extend(results)
            if not has_overwrite:
                candidates.append(ticker)
            else:
                already_set.append(ticker)
        else:
            logger.info(f"  {ticker}: 値差分なし（revision対象外）")

    # サマリー
    print("\n" + "=" * 60)
    print("=== PDF比較結果サマリー ===")
    print(f"チェック銘柄数: {len(ng_data)}")
    print(f"値差分検出: {len(set(r['ticker'] for r in all_results))}銘柄")
    print(f"\n--- overwrite_past_months 未設定で差分あり（新規候補）---")
    for t in candidates:
        print(f"  {t}")
        for r in all_results:
            if r["ticker"] == t:
                print(f"    {r['year_month']} {r['label']}: {r['target_value']} -> {r['latest_value']} (diff={r['diff']})")

    print(f"\n--- overwrite_past_months 設定済みだが差分あり ---")
    for t in already_set:
        print(f"  {t}")
        for r in all_results:
            if r["ticker"] == t:
                print(f"    {r['year_month']} {r['label']}: {r['target_value']} -> {r['latest_value']} (diff={r['diff']})")

    print(f"\n--- 全差分詳細 ---")
    for r in all_results:
        print(f"  {r['ticker']} {r['year_month']} {r['label']}: "
              f"{r['target_pdf']}={r['target_value']} -> {r['latest_pdf']}={r['latest_value']} (diff={r['diff']})")


if __name__ == "__main__":
    main()
