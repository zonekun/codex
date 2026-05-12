"""
バフェットコード KPI 月次データを全銘柄分スクレイプしてローカル CSV に保存する。

出力:
  data/csv/bc_monthly_kpi.csv
    ticker, year_month, field, value

Usage:
  PYTHONUTF8=1 uv run python scripts/download_bc_kpi.py
  PYTHONUTF8=1 uv run python scripts/download_bc_kpi.py --tickers 3097 2294
  PYTHONUTF8=1 uv run python scripts/download_bc_kpi.py --resume
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
GCS_BUCKET  = "stock_data_1930932"
GCS_META    = "monthly/meta"
KEY_FILE    = "keys/gcp-service-account.json"
PROJECT     = "gmailpj-357912"
BUFFETT_URL = "https://www.buffett-code.com/company/{code}/kpi"
OUT_CSV     = Path("data/csv/bc_monthly_kpi.csv")
CHROME_EXE  = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
JST         = timezone(timedelta(hours=9))

# レートリミット: 1銘柄あたりの待機秒数（ランダム幅）
WAIT_MIN = 10
WAIT_MAX = 18
BATCH_SIZE = 10     # N社ごとに長休憩
BATCH_WAIT_MIN = 30
BATCH_WAIT_MAX = 60


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def remove_ticker_from_targets(path: str, ticker: str) -> bool:
    """targets CSV から ticker 行を削除。成功時 True。"""
    try:
        p = Path(path)
        if not p.exists():
            return False
        with open(p, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return False
        header, data = rows[0], rows[1:]
        new_data = [r for r in data if not (r and r[0].strip() == ticker)]
        if len(new_data) == len(data):
            return False
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(new_data)
        os.replace(tmp, p)
        return True
    except Exception as e:
        log(f"  消込失敗: {e}")
        return False


# ──────────────────────────────────────────────
# GCS: 対象銘柄一覧
# ──────────────────────────────────────────────

def get_gcs() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project=PROJECT, credentials=creds)


def list_tickers_with_records(gcs: storage.Client) -> list[str]:
    """structure.json が存在するティッカー一覧を返す（inactive除外）。"""
    import json as _json

    # inactive_reason があるアダプターを除外
    inactive: set[str] = set()
    adapters_dir = Path("meta/monthly")
    if adapters_dir.exists():
        for p in adapters_dir.glob("*_extract_adapter.json"):
            try:
                d = _json.loads(p.read_text(encoding="utf-8"))
                if d.get("inactive_reason"):
                    inactive.add(p.name.removesuffix("_extract_adapter.json"))
            except Exception:
                pass

    tickers = []
    for blob in gcs.list_blobs(GCS_BUCKET, prefix=f"{GCS_META}/"):
        if blob.name.endswith("/structure.json"):
            ticker = blob.name.split("/")[2]
            if ticker not in inactive:
                tickers.append(ticker)
    return sorted(tickers)


# ──────────────────────────────────────────────
# Selenium
# ──────────────────────────────────────────────

def create_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.binary_location = CHROME_EXE
    opts.add_argument("--window-size=1200,800")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--js-flags=--max-old-space-size=512")
    opts.add_argument("--memory-pressure-off")
    opts.add_argument("--disable-extensions")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def wait_waf(driver, max_sec: int = 30) -> bool:
    for _ in range(max_sec):
        title = driver.title
        if title and title not in ("Human Verification", "Verification Required"):
            return True
        time.sleep(1)
    return False


def scrape_buffett_kpi(driver, ticker: str) -> list[dict]:
    """
    バフェットコード KPI ページから月次データを取得。

    Returns:
        [{"ticker": "3097", "year_month": "2026-02", "field": "全店 店舗数", "value": 192.0}, ...]
    """
    from selenium.webdriver.common.by import By

    url = BUFFETT_URL.format(code=ticker)
    driver.get(url)
    time.sleep(4)
    if not wait_waf(driver, max_sec=20):
        log(f"  [{ticker}] WAF タイムアウト")
        return []
    time.sleep(2)

    records: list[dict] = []
    current_year: Optional[int] = None
    current_months: list[int] = []

    try:
        tables = driver.find_elements(By.TAG_NAME, "table")
        if not tables:
            log(f"  [{ticker}] テーブルなし")
            return records

        table = tables[0]
        rows = table.find_elements(By.TAG_NAME, "tr")

        for row in rows:
            ths = row.find_elements(By.TAG_NAME, "th")
            tds = row.find_elements(By.TAG_NAME, "td")

            # スペーサー行スキップ
            if tds and all("kpi__table-spacer" in (td.get_attribute("class") or "")
                           for td in tds):
                continue

            th_texts = [th.text.strip() for th in ths]
            td_texts = [td.text.strip() for td in tds]

            # ヘッダー行: th のみ（YYYY年）
            if ths and not tds:
                year_m = re.match(r"(\d{4})年", th_texts[0]) if th_texts else None
                if year_m:
                    current_year = int(year_m.group(1))
                    current_months = []
                    for t in th_texts[1:]:
                        mm = re.match(r"(\d+)月", t)
                        if mm:
                            current_months.append(int(mm.group(1)))
                continue

            # データ行: th (メトリクス名) + td (値 × 12)
            if ths and tds and current_year is not None:
                metric_name = th_texts[0] if th_texts else ""
                if not metric_name:
                    continue

                for i, val_str in enumerate(td_texts):
                    if i >= len(current_months):
                        break
                    if not val_str or val_str in ("-", "－", "—", "N/A", "na"):
                        continue
                    month = current_months[i]
                    ym = f"{current_year}-{month:02d}"
                    try:
                        val = float(val_str.replace(",", "").replace("％", ""))
                        records.append({
                            "ticker": ticker,
                            "year_month": ym,
                            "field": metric_name,
                            "value": val,
                        })
                    except ValueError:
                        pass

    except Exception as e:
        log(f"  [{ticker}] パースエラー: {e}")

    return records


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="バフェットコード KPI 月次データ全量ダウンロード")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="対象ティッカー（省略時は GCS monthly_records.json 全銘柄）")
    parser.add_argument("--targets-csv", default=None,
                        help="再取得対象 CSV（先頭列=ticker）。成功時に当該行を削除")
    parser.add_argument("--resume", action="store_true",
                        help="既存 CSV に含まれるティッカーをスキップして続行")
    args = parser.parse_args()

    log("=== バフェットコード KPI ダウンロード開始 ===")

    # 対象ティッカー決定
    if args.tickers:
        tickers = args.tickers
    elif args.targets_csv:
        tickers = []
        with open(args.targets_csv, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # ヘッダースキップ
            for row in reader:
                if row and row[0].strip():
                    tickers.append(row[0].strip())
        log(f"--targets-csv {args.targets_csv} から {len(tickers)} 社読込")
    else:
        gcs = get_gcs()
        tickers = list_tickers_with_records(gcs)
    log(f"対象: {len(tickers)} 社")

    # resume モード: 既存 CSV のティッカーをスキップ
    done_tickers: set[str] = set()
    if args.resume and OUT_CSV.exists():
        with open(OUT_CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                done_tickers.add(row["ticker"])
        log(f"レジューム: {len(done_tickers)} 社スキップ")
        tickers = [t for t in tickers if t not in done_tickers]
        log(f"残り: {len(tickers)} 社")

    if not tickers:
        log("対象なし")
        return

    # CSV 出力準備
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = OUT_CSV.exists() and args.resume
    fh = open(OUT_CSV, "a" if file_exists else "w", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(fh, fieldnames=["ticker", "year_month", "field", "value"])
    if not file_exists:
        writer.writeheader()

    # Chrome 起動
    driver = create_driver()
    log("Chrome 起動完了")

    success = 0
    empty = 0
    error = 0
    waf_count = 0

    try:
        for idx, ticker in enumerate(tickers, 1):
            log(f"[{idx}/{len(tickers)}] {ticker}")
            try:
                records = scrape_buffett_kpi(driver, ticker)
                if records:
                    for r in records:
                        writer.writerow(r)
                    fh.flush()
                    months = len(set(r["year_month"] for r in records))
                    log(f"  ✅ {len(records)} レコード ({months}ヶ月)")
                    success += 1
                    if args.targets_csv:
                        if remove_ticker_from_targets(args.targets_csv, ticker):
                            log(f"  消込: {args.targets_csv}")
                else:
                    log(f"  ⚠️ データなし")
                    empty += 1
            except Exception as e:
                log(f"  ❌ エラー: {e}")
                error += 1
                # WAF 検知 → 長めに待機
                if "Verification" in str(e) or "cloudflare" in str(e).lower():
                    waf_count += 1
                    log(f"  🛑 WAF 検知 ({waf_count}回目) → 60秒待機")
                    time.sleep(60)
                    if waf_count >= 3:
                        log("WAF 3回検知 → 中断。--resume で再開可能")
                        break

            # レートリミット
            wait = random.uniform(WAIT_MIN, WAIT_MAX)
            time.sleep(wait)

            # N社ごとに長休憩 + GCS保存
            if idx % BATCH_SIZE == 0:
                fh.flush()
                long_wait = random.uniform(BATCH_WAIT_MIN, BATCH_WAIT_MAX)
                log(f"  {BATCH_SIZE}社完了 → 長休憩 {long_wait:.0f}秒")
                # GCS にCSV中間保存
                try:
                    gcs = get_gcs()
                    bucket = gcs.bucket(GCS_BUCKET)
                    blob = bucket.blob("csv/bc_monthly_kpi.csv")
                    blob.upload_from_filename(str(OUT_CSV), content_type="text/csv")
                    log(f"  GCS中間保存完了")
                except Exception as e:
                    log(f"  GCS中間保存失敗: {e}")
                time.sleep(long_wait)

    finally:
        fh.flush()
        try:
            gcs = get_gcs()
            bucket = gcs.bucket(GCS_BUCKET)
            blob = bucket.blob("csv/bc_monthly_kpi.csv")
            blob.upload_from_filename(str(OUT_CSV), content_type="text/csv")
            log("GCS最終保存完了")
        except Exception as e:
            log(f"GCS最終保存失敗: {e}")
        fh.close()
        driver.quit()

    log(f"\n=== 完了 ===")
    log(f"  成功: {success} / データなし: {empty} / エラー: {error}")
    log(f"  出力: {OUT_CSV}")


if __name__ == "__main__":
    main()
