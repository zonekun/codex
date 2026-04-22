"""
バフェットコード KPI 月次データを全銘柄分スクレイプしてローカル CSV に保存する。
Selenium → nodriver (Google Chrome + Xvfb) 版。1GB RAM 対応。

出力:
  data/csv/bc_monthly_kpi.csv
    ticker, year_month, field, value

Usage:
  PYTHONUTF8=1 DISPLAY=:99 uv run python scripts/download_bc_kpi_v2.py
  PYTHONUTF8=1 DISPLAY=:99 uv run python scripts/download_bc_kpi_v2.py --tickers 3097 2294
  PYTHONUTF8=1 DISPLAY=:99 uv run python scripts/download_bc_kpi_v2.py --resume
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import random
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import nodriver as uc
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
GCS_CSV_KEY = "csv/bc_monthly_kpi.csv"
LOG_FILE    = Path("data/logs/bc_kpi_download.log")
JST         = timezone(timedelta(hours=9))

# Xvfb 仮想ディスプレイ
DISPLAY     = ":99"

# レートリミット（WAF回避のため10秒ベース）
WAIT_MEAN   = 10.0   # 秒（正規分布の平均）
WAIT_SIGMA  = 2.0
WAIT_MIN    = 8.0
WAIT_MAX    = 15.0
BATCH_SIZE  = 10     # N社ごとに長休憩
BATCH_WAIT  = (30, 60)  # 長休憩の範囲（秒）

# WAF バックオフ
WAF_BACKOFF = [60, 120, 240]

# User-Agent ローテーション
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

WAF_TITLES  = {"Human Verification", "Verification Required", "Just a moment..."}


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def upload_csv_to_gcs(gcs: storage.Client) -> None:
    """OUT_CSV を GCS にアップロードする。"""
    try:
        bucket = gcs.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_CSV_KEY)
        blob.upload_from_filename(str(OUT_CSV), content_type="text/csv")
        log(f"  GCS アップロード完了: gs://{GCS_BUCKET}/{GCS_CSV_KEY}")
    except Exception as e:
        log(f"  GCS アップロード失敗: {e}")


def write_ticker_log(ticker: str, records: list[dict], status: str) -> None:
    """銘柄ごとの取得結果をログファイルに追記する。"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_header = not LOG_FILE.exists()
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    if records:
        year_months = [r["year_month"] for r in records]
        min_ym = min(year_months)
        max_ym = max(year_months)
        rec_count = len(records)
    else:
        min_ym = max_ym = ""
        rec_count = 0
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if write_header:
            f.write("ticker,min_year_month,max_year_month,records,status,downloaded_at\n")
        f.write(f"{ticker},{min_ym},{max_ym},{rec_count},{status},{ts}\n")


def random_wait() -> float:
    """正規分布ベースのランダム待機秒数を返す。"""
    w = random.gauss(WAIT_MEAN, WAIT_SIGMA)
    return max(WAIT_MIN, min(WAIT_MAX, w))


# ──────────────────────────────────────────────
# Xvfb 管理
# ──────────────────────────────────────────────

def start_xvfb() -> Optional[subprocess.Popen]:
    """Xvfb を起動して Popen オブジェクトを返す。失敗時は None。"""
    try:
        proc = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        os.environ["DISPLAY"] = DISPLAY
        log(f"Xvfb 起動: {DISPLAY} (PID={proc.pid})")
        return proc
    except FileNotFoundError:
        log("Xvfb が見つかりません。sudo apt install xvfb を実行してください")
        return None


# ──────────────────────────────────────────────
# GCS: 対象銘柄一覧
# ──────────────────────────────────────────────

def get_gcs() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project=PROJECT, credentials=creds)


def list_tickers_with_records(gcs: storage.Client) -> list[str]:
    """structure.json が存在するティッカー一覧を返す（月次収集対象外を除く）。"""
    # data/monthly_adapters/{ticker}.json に inactive_reason があるものは除外
    inactive: set[str] = set()
    adapters_dir = Path("data/monthly_adapters")
    if adapters_dir.exists():
        for p in adapters_dir.glob("*.json"):
            try:
                import json as _json
                d = _json.loads(p.read_text(encoding="utf-8"))
                if d.get("inactive_reason"):
                    inactive.add(p.stem)
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
# nodriver: ブラウザ起動
# ──────────────────────────────────────────────

DEBUG_PORT = 9222
_chrome_proc: Optional[subprocess.Popen] = None


def start_chrome() -> subprocess.Popen:
    """
    Chrome を手動起動してデバッグポートが開くまで待機する。
    nodriver の接続タイムアウト(2.75秒)では 1GB RAM 環境で間に合わないため、
    起動を分離して確実に待機する。
    """
    global _chrome_proc
    w = random.randint(1024, 1920)
    h = random.randint(768, 1080)
    ua = random.choice(USER_AGENTS)

    cmd = [
        "/usr/bin/google-chrome",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--password-store=basic",
        "--disable-infobars",
        "--disable-breakpad",
        "--user-data-dir=/tmp/nodriver_chrome_profile",
        f"--window-size={w},{h}",
        f"--js-flags=--max-old-space-size=256",
        f"--user-agent={ua}",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
    ]
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY

    _chrome_proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
    )
    log(f"Chrome 起動 (PID={_chrome_proc.pid}) ポート {DEBUG_PORT} 待機中...")

    # デバッグポートが開くまで最大30秒待機
    import urllib.request
    for i in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1)
            log(f"Chrome 接続可 ({i+1}秒後)")
            return _chrome_proc
        except Exception:
            pass
    raise RuntimeError(f"Chrome がポート {DEBUG_PORT} を開かなかった（30秒タイムアウト）")


async def create_browser() -> uc.Browser:
    """既起動の Chrome に nodriver を接続する。"""
    browser = await uc.start(
        host="127.0.0.1",
        port=DEBUG_PORT,
    )
    return browser


# ──────────────────────────────────────────────
# nodriver: スクレイピング
# ──────────────────────────────────────────────

async def wait_for_waf(tab: uc.Tab, max_sec: int = 30) -> bool:
    """WAF チャレンジが解消されるまで待機。タイムアウト時 False。"""
    for _ in range(max_sec):
        title = await tab.evaluate("document.title")
        if title and title not in WAF_TITLES:
            return True
        await asyncio.sleep(1)
    return False


async def random_scroll(tab: uc.Tab) -> None:
    """自然なスクロールを模倣する。"""
    scroll_count = random.randint(2, 5)
    for _ in range(scroll_count):
        px = random.randint(200, 600)
        await tab.evaluate(f"window.scrollBy(0, {px})")
        await asyncio.sleep(random.uniform(0.3, 0.8))
    # 最上部に戻す
    await tab.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)


async def block_resources(tab: uc.Tab) -> None:
    """画像・フォント・CSS をブロックして帯域・メモリを節約。"""
    await tab.send(
        nodriver.cdp.network.set_blocked_ur_ls(
            urls=["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp",
                  "*.svg", "*.woff", "*.woff2", "*.ttf", "*.eot",
                  "*.css", "*.ico"]
        )
    )


async def scrape_buffett_kpi(tab: uc.Tab, ticker: str) -> list[dict]:
    """
    バフェットコード KPI ページから月次データを取得。

    Returns:
        [{"ticker": "3097", "year_month": "2026-02", "field": "全店 店舗数", "value": 192.0}, ...]
    """
    url = BUFFETT_URL.format(code=ticker)
    await tab.get(url)
    await asyncio.sleep(3)

    if not await wait_for_waf(tab, max_sec=20):
        log(f"  [{ticker}] WAF タイムアウト")
        return []

    await asyncio.sleep(1)
    await random_scroll(tab)

    records: list[dict] = []
    current_year: Optional[int] = None
    current_months: list[int] = []

    try:
        # テーブル HTML を取得して Python でパース
        html = await tab.evaluate(
            "document.querySelector('table') ? document.querySelector('table').outerHTML : ''"
        )
        if not html:
            log(f"  [{ticker}] テーブルなし")
            return records

        # 軽量 HTML パーサ（lxml or html.parser）
        from html.parser import HTMLParser

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows: list[list[tuple[str, str]]] = []  # [(tag, text), ...]
                self._current_row: list[tuple[str, str]] = []
                self._current_tag: Optional[str] = None
                self._current_text: list[str] = []
                self._current_class: str = ""
                self._in_cell = False

            def handle_starttag(self, tag, attrs):
                if tag == "tr":
                    self._current_row = []
                elif tag in ("th", "td"):
                    self._current_tag = tag
                    self._current_text = []
                    self._current_class = dict(attrs).get("class", "")
                    self._in_cell = True

            def handle_endtag(self, tag):
                if tag in ("th", "td") and self._in_cell:
                    text = "".join(self._current_text).strip()
                    self._current_row.append((self._current_tag, text, self._current_class))
                    self._in_cell = False
                    self._current_tag = None
                elif tag == "tr":
                    if self._current_row:
                        self.rows.append(self._current_row)
                    self._current_row = []

            def handle_data(self, data):
                if self._in_cell:
                    self._current_text.append(data)

        parser = TableParser()
        parser.feed(html)

        for row in parser.rows:
            ths = [(tag, text, cls) for tag, text, cls in row if tag == "th"]
            tds = [(tag, text, cls) for tag, text, cls in row if tag == "td"]

            # スペーサー行スキップ
            if tds and all("kpi__table-spacer" in cls for _, _, cls in tds):
                continue

            th_texts = [text for _, text, _ in ths]
            td_texts = [text for _, text, _ in tds]

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
# メイン（async）
# ──────────────────────────────────────────────

async def run(tickers: list[str], resume: bool, gcs: storage.Client) -> None:
    """スクレイピング本体。"""
    # CSV 出力準備
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = OUT_CSV.exists() and resume
    fh = open(OUT_CSV, "a" if file_exists else "w", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(fh, fieldnames=["ticker", "year_month", "field", "value"])
    if not file_exists:
        writer.writeheader()

    browser = await create_browser()
    tab = await browser.get("about:blank")
    log("ブラウザ起動完了")

    success = 0
    empty = 0
    error = 0
    waf_count = 0

    try:
        for idx, ticker in enumerate(tickers, 1):
            log(f"[{idx}/{len(tickers)}] {ticker}")
            records: list[dict] = []
            try:
                records = await scrape_buffett_kpi(tab, ticker)
                if records:
                    for r in records:
                        writer.writerow(r)
                    fh.flush()
                    months = len(set(r["year_month"] for r in records))
                    year_months = sorted(set(r["year_month"] for r in records))
                    log(f"  OK {len(records)} レコード ({months}ヶ月) {year_months[0]}〜{year_months[-1]}")
                    write_ticker_log(ticker, records, "ok")
                    success += 1
                else:
                    log(f"  データなし")
                    write_ticker_log(ticker, [], "empty")
                    empty += 1
            except Exception as e:
                log(f"  エラー: {e}")
                write_ticker_log(ticker, [], "error")
                error += 1
                # WAF 検知 → 指数バックオフ
                if "Verification" in str(e) or "cloudflare" in str(e).lower():
                    if waf_count < len(WAF_BACKOFF):
                        wait_sec = WAF_BACKOFF[waf_count]
                        log(f"  WAF 検知 ({waf_count + 1}回目) → {wait_sec}秒待機")
                        await asyncio.sleep(wait_sec)
                        waf_count += 1
                    else:
                        log("WAF 3回検知 → 中断。--resume で再開可能")
                        break

            # レートリミット（正規分布ベースのランダム待機）
            wait = random_wait()
            log(f"  待機 {wait:.1f}秒")
            await asyncio.sleep(wait)

            # N社ごとに長休憩 + GCS 保存
            if idx % BATCH_SIZE == 0:
                long_wait = random.uniform(*BATCH_WAIT)
                log(f"  {BATCH_SIZE}社完了 → GCS 保存 → 長休憩 {long_wait:.0f}秒")
                fh.flush()
                upload_csv_to_gcs(gcs)
                await asyncio.sleep(long_wait)

    finally:
        fh.close()
        try:
            browser.stop()
        except Exception:
            pass

    # 完了時にも GCS 保存
    upload_csv_to_gcs(gcs)
    log(f"\n=== 完了 ===")
    log(f"  成功: {success} / データなし: {empty} / エラー: {error}")
    log(f"  出力: {OUT_CSV}")
    log(f"  ログ: {LOG_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="バフェットコード KPI 月次データ全量ダウンロード（nodriver版）")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="対象ティッカー（省略時は GCS monthly_records.json 全銘柄）")
    parser.add_argument("--resume", action="store_true",
                        help="既存 CSV に含まれるティッカーをスキップして続行")
    args = parser.parse_args()

    log("=== バフェットコード KPI ダウンロード開始（nodriver版） ===")

    # Xvfb 起動（DISPLAY 未設定時）
    xvfb_proc = None
    if not os.environ.get("DISPLAY"):
        xvfb_proc = start_xvfb()
    else:
        os.environ["DISPLAY"] = os.environ["DISPLAY"]  # 既設定を引き継ぐ

    # Chrome 起動
    chrome_proc = start_chrome()

    # GCS クライアント（常に初期化）
    gcs = get_gcs()

    # 対象ティッカー決定
    if args.tickers:
        tickers = args.tickers
    else:
        tickers = list_tickers_with_records(gcs)
    log(f"対象: {len(tickers)} 社")

    # resume モード: ログファイル記録済みティッカーをスキップ
    if args.resume and LOG_FILE.exists():
        done_tickers: set[str] = set()
        with open(LOG_FILE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                done_tickers.add(row["ticker"])
        log(f"レジューム: {len(done_tickers)} 社スキップ（ログベース）")
        tickers = [t for t in tickers if t not in done_tickers]
        log(f"残り: {len(tickers)} 社")

    if not tickers:
        log("対象なし")
        return

    try:
        asyncio.run(run(tickers, args.resume, gcs))
    finally:
        if chrome_proc:
            chrome_proc.terminate()
            log("Chrome 停止")
        if xvfb_proc:
            xvfb_proc.terminate()
            log("Xvfb 停止")


if __name__ == "__main__":
    main()
