"""NG銘柄のPDF比較: 過去月PDFと最新PDFで同じ月の値が異なるかを確認する。

doc_title_pattern でフィルタして月次開示PDFのみ比較する。
"""
import csv
import io
import json
import re
import sys
import unicodedata
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
ADAPTER_DIR = PROJECT_ROOT / "meta" / "monthly"


def get_gcs_client() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(str(KEYS_PATH))
    return storage.Client(credentials=creds, project="gmailpj-357912")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).replace("\u3000", " ").replace("\n", " ").strip()
    return s


def load_ng_data(csv_path: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("match") == "NG":
                result[row["ticker"]].append(row)
    return result


def get_monthly_pdf_blobs(
    client: storage.Client,
    ticker: str,
    doc_title_pattern: str | None,
) -> list:
    """月次開示PDFのblob一覧を取得（doc_title_patternでフィルタ）。"""
    bucket = client.bucket(BUCKET_NAME)
    prefix = f"tdnet/{ticker}/"
    all_blobs = list(bucket.list_blobs(prefix=prefix))
    pdf_blobs = [b for b in all_blobs if b.name.endswith(".pdf")]

    if not doc_title_pattern:
        return sorted(pdf_blobs, key=lambda b: b.name)

    # ファイル名に含まれるタイトルでフィルタ
    pat = re.compile(doc_title_pattern, re.IGNORECASE)
    filtered = []
    for blob in pdf_blobs:
        fname = blob.name.split("/")[-1]
        fname_norm = _norm(fname)
        if pat.search(fname_norm):
            filtered.append(blob)

    # パターンマッチでヒットしない場合、「月次」を含むPDFを探す
    if not filtered:
        for blob in pdf_blobs:
            fname = blob.name.split("/")[-1]
            fname_norm = _norm(fname)
            if "月次" in fname_norm or "月度" in fname_norm:
                filtered.append(blob)

    return sorted(filtered, key=lambda b: b.name)


def extract_all_month_values_from_pdf(pdf_bytes: bytes) -> dict[int, dict[str, float]]:
    """PDFテーブルから全月列の値を{month: {label: value}} で返す。"""
    result: dict[int, dict[str, float]] = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables():
                    if not tbl or len(tbl) < 2:
                        continue
                    # ヘッダー行から月列を特定
                    month_cols: list[tuple[int, int]] = []
                    header_row_idx = -1
                    for row_idx, row in enumerate(tbl):
                        cells = [_norm(c or "") for c in row]
                        mc = []
                        for col_idx, cell in enumerate(cells):
                            m = re.match(r"(\d{1,2})\s*月[度]?$", cell)
                            if m:
                                mc.append((col_idx, int(m.group(1))))
                                continue
                            m = re.search(r"\d{4}年\s*(\d{1,2})月", cell)
                            if m:
                                mc.append((col_idx, int(m.group(1))))
                                continue
                            m = re.search(r"'?\d{2}/(\d{2})", cell)
                            if m:
                                mc.append((col_idx, int(m.group(1))))
                        if len(mc) >= 2:
                            month_cols = mc
                            header_row_idx = row_idx
                            break

                    if not month_cols:
                        continue

                    # データ行を処理
                    for data_row in tbl[header_row_idx + 1:]:
                        if not data_row or not data_row[0]:
                            continue
                        label = _norm(data_row[0])
                        if not label or label in ("", "None"):
                            continue
                        for col_idx, month_num in month_cols:
                            if col_idx >= len(data_row):
                                continue
                            val_str = data_row[col_idx]
                            if not val_str:
                                continue
                            val_n = _norm(val_str).replace(",", "").replace("△", "-").replace("▲", "-")
                            try:
                                val = float(val_n)
                                if month_num not in result:
                                    result[month_num] = {}
                                result[month_num][label] = val
                            except ValueError:
                                pass
                    # 1テーブル見つけたら終了（メインテーブルが通常最初にある）
                    if result:
                        return result
    except Exception as e:
        logger.error(f"PDF解析エラー: {e}")
    return result


def parse_report_month_from_filename(fname: str) -> tuple[int, int] | None:
    """ファイル名から報告月(year, month)を推定。"""
    fname_n = _norm(fname)
    # 「YYYY年M月度」パターン
    m = re.search(r"(\d{4})年(\d{1,2})月度", fname_n)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 「YYYY年M月期M月度」パターン
    m = re.search(r"(\d{4})年\d{1,2}月期\s*(\d{1,2})月度", fname_n)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 「令和N年M月」パターン
    m = re.search(r"令和(\d{1,2})年(\d{1,2})月", fname_n)
    if m:
        return 2018 + int(m.group(1)), int(m.group(2))
    return None


def compare_ticker(
    client: storage.Client,
    ticker: str,
    ng_rows: list[dict],
    adapter: dict | None,
) -> list[dict]:
    """1銘柄のPDF比較を実行。"""
    results = []

    doc_title_pattern = adapter.get("doc_title_pattern") if adapter else None
    pdf_blobs = get_monthly_pdf_blobs(client, ticker, doc_title_pattern)

    if len(pdf_blobs) < 2:
        logger.info(f"  {ticker}: 月次PDFが2件未満({len(pdf_blobs)}件)、スキップ")
        return results

    logger.info(f"  {ticker}: 月次PDF {len(pdf_blobs)}件")

    # NG対象年月
    ng_months = set()
    for row in ng_rows:
        ym = row["year_month"]
        ng_months.add((int(ym[:4]), int(ym[5:7])))

    # 最新PDFのデータを取得
    latest_blob = pdf_blobs[-1]
    latest_fname = latest_blob.name.split("/")[-1]
    logger.info(f"  最新PDF: {latest_fname}")

    try:
        latest_bytes = latest_blob.download_as_bytes()
        latest_all = extract_all_month_values_from_pdf(latest_bytes)
    except Exception as e:
        logger.error(f"  最新PDF取得/解析失敗: {e}")
        return results

    if not latest_all:
        logger.info(f"  最新PDFからテーブル抽出できず")
        return results

    latest_months = sorted(latest_all.keys())
    logger.info(f"  最新PDFに含まれる月: {latest_months}")

    # 各NG月について、その月のPDFを見つけて比較
    for year, month in sorted(ng_months):
        if month not in latest_all:
            logger.info(f"  {year}-{month:02d}: 最新PDFに{month}月のデータなし")
            continue

        # 対象月のPDFを探す
        target_blob = None
        for blob in pdf_blobs[:-1]:
            fname = blob.name.split("/")[-1]
            ym = parse_report_month_from_filename(fname)
            if ym and ym == (year, month):
                target_blob = blob
                break

        # ファイル名で見つからない場合、タイトルの年月推定を試す
        if target_blob is None:
            for blob in pdf_blobs[:-1]:
                fname = blob.name.split("/")[-1]
                fname_n = _norm(fname)
                # 提出日(先頭8桁)から推定: YYYYMMDD
                m = re.match(r"(\d{4})(\d{2})(\d{2})", fname_n)
                if m:
                    sub_year, sub_month, sub_day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    # 提出日ヒューリスティック: day<=15→前月が報告月
                    if sub_day <= 15:
                        rep_month = sub_month - 1 if sub_month > 1 else 12
                        rep_year = sub_year if sub_month > 1 else sub_year - 1
                    else:
                        rep_month = sub_month
                        rep_year = sub_year
                    if rep_year == year and rep_month == month:
                        target_blob = blob
                        break

        if target_blob is None:
            logger.info(f"  {year}-{month:02d}: 対象月PDF見つからず")
            continue

        target_fname = target_blob.name.split("/")[-1]
        logger.info(f"  {year}-{month:02d}: 比較 {target_fname}")

        try:
            target_bytes = target_blob.download_as_bytes()
            target_all = extract_all_month_values_from_pdf(target_bytes)
        except Exception as e:
            logger.error(f"  対象PDF取得/解析失敗: {e}")
            continue

        if month not in target_all:
            logger.info(f"  {year}-{month:02d}: 対象PDFに{month}月のデータなし")
            continue

        latest_vals = latest_all[month]
        target_vals = target_all[month]

        # 共通ラベルで比較
        common_labels = set(latest_vals.keys()) & set(target_vals.keys())
        if not common_labels:
            logger.info(f"  {year}-{month:02d}: 共通ラベルなし")
            continue

        has_diff = False
        for label in sorted(common_labels):
            lv = latest_vals[label]
            tv = target_vals[label]
            if abs(lv - tv) > 0.01:
                has_diff = True
                results.append({
                    "ticker": ticker,
                    "year_month": f"{year}-{month:02d}",
                    "label": label,
                    "latest_pdf": latest_fname,
                    "latest_value": lv,
                    "target_pdf": target_fname,
                    "target_value": tv,
                    "diff": round(abs(lv - tv), 2),
                })
                logger.info(f"    *** 差分: {label}: {tv} -> {lv} (diff={abs(lv - tv):.2f})")

        if not has_diff:
            logger.info(f"  {year}-{month:02d}: 値は一致（revision対象外）")

    return results


def main() -> None:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "/c/tmp/buffett_compare_20260410_094735.csv"
    ng_data = load_ng_data(csv_path)

    logger.info(f"NG銘柄数: {len(ng_data)}")

    client = get_gcs_client()

    all_results: list[dict] = []
    new_candidates: list[str] = []  # overwrite未設定で差分あり
    already_set_with_diff: list[str] = []

    for ticker in sorted(ng_data.keys()):
        adapter_path = ADAPTER_DIR / f"{ticker}_extract_adapter.json"

        adapter = None
        has_overwrite = False
        if adapter_path.exists():
            with open(adapter_path, encoding="utf-8") as f:
                adapter = json.load(f)
            has_overwrite = adapter.get("overwrite_past_months", False)
        else:
            logger.info(f"\n{ticker}: アダプターなし、スキップ")
            continue

        logger.info(f"\n{'='*60}")
        logger.info(f"チェック中: {ticker} (overwrite={has_overwrite})")

        try:
            results = compare_ticker(client, ticker, ng_data[ticker], adapter)
        except Exception as e:
            logger.error(f"  {ticker}: エラー: {e}")
            import traceback
            traceback.print_exc()
            continue

        if results:
            all_results.extend(results)
            if not has_overwrite:
                new_candidates.append(ticker)
            else:
                already_set_with_diff.append(ticker)

    # サマリー出力
    print("\n" + "=" * 70)
    print("=== PDF比較結果サマリー ===")
    diff_tickers = set(r["ticker"] for r in all_results)
    print(f"チェック銘柄数: {len(ng_data)}")
    print(f"値差分検出: {len(diff_tickers)}銘柄")

    print(f"\n--- 新規 overwrite_past_months 候補（未設定 + 差分あり）: {len(new_candidates)}銘柄 ---")
    for t in new_candidates:
        print(f"  {t}:")
        for r in all_results:
            if r["ticker"] == t:
                print(f"    {r['year_month']} {r['label']}: {r['target_value']} -> {r['latest_value']} (diff={r['diff']})")

    print(f"\n--- 設定済みだが依然NG（差分あり）: {len(already_set_with_diff)}銘柄 ---")
    for t in already_set_with_diff:
        print(f"  {t}:")
        for r in all_results:
            if r["ticker"] == t:
                print(f"    {r['year_month']} {r['label']}: {r['target_value']} -> {r['latest_value']} (diff={r['diff']})")


if __name__ == "__main__":
    main()
