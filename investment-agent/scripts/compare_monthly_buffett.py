"""
月次データ抽出結果（monthly_records.json）をバフェットコードの数値と突合する。

- GCS から monthly_records.json を読み込む（extract-monthly-data の出力）
- バフェットコード /company/{code}/kpi をローカル Chrome で取得
- 最新 year_month の値を突合し、一致/不一致を判定
- 結果を C:\tmp\buffett_compare_YYYYMMDD.csv に出力

Usage:
    PYTHONUTF8=1 python scripts/compare_monthly_buffett.py
    PYTHONUTF8=1 python scripts/compare_monthly_buffett.py --limit 20
    PYTHONUTF8=1 python scripts/compare_monthly_buffett.py --tickers 3097 3046 2670
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from google.cloud import storage
from google.oauth2 import service_account

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
GCS_BUCKET  = "stock_data_1930932"
GCS_META    = "monthly/meta"
GCS_RECORD  = "monthly/record"
KEY_FILE    = "keys/gcp-service-account.json"
PROJECT     = "gmailpj-357912"
BUFFETT_URL = "https://www.buffett-code.com/company/{code}/kpi"
OUT_DIR     = Path(r"C:\tmp")
CHROME_EXE  = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
JST         = timezone(timedelta(hours=9))

# 数値比較の許容誤差（絶対値）
TOLERANCE   = 0.5


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ──────────────────────────────────────────────
# GCS
# ──────────────────────────────────────────────

def get_gcs() -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project=PROJECT, credentials=creds)


def list_tickers_with_records(gcs: storage.Client) -> list[str]:
    """monthly_records.json が存在するティッカー一覧を返す。"""
    blobs = gcs.list_blobs(GCS_BUCKET, prefix=f"{GCS_RECORD}/", delimiter="/")
    # prefixes を列挙してから monthly_records.json の有無を確認
    tickers = []
    for blob in gcs.list_blobs(GCS_BUCKET, prefix=f"{GCS_RECORD}/"):
        if blob.name.endswith("/monthly_records.json"):
            ticker = blob.name.split("/")[2]
            tickers.append(ticker)
    return sorted(tickers)


def load_adapter_fields(gcs: storage.Client, ticker: str) -> dict[str, dict]:
    """
    extract_adapter.json から unit_scale / yoy_offset / bc_floor を取得して
    { field_key: {"unit_scale": float, "yoy_offset": float, "bc_floor": bool} } 形式で返す。
    アダプターがない場合や対象フィールドに設定がない場合は空 dict を返す。

    bc_floor: true の場合、比較時に math.floor(adj_val) を使用する。
    バフェットコードが小数点以下を切り捨て表示するフィールドに設定する。
    """
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/extract_adapter.json")
        adapter = json.loads(blob.download_as_text())
    except Exception:
        return {}

    result = {}
    for f in adapter.get("fields", []):
        key = f.get("key", "")
        if not key:
            continue
        conf: dict = {}
        if "bc_key" in f:
            conf["bc_key"] = str(f["bc_key"])
        if "unit_scale" in f:
            conf["unit_scale"] = float(f["unit_scale"])
        if "yoy_offset" in f:
            conf["yoy_offset"] = float(f["yoy_offset"])
        if "bc_floor" in f:
            conf["bc_floor"] = bool(f["bc_floor"])
        if f.get("bc_ignore"):
            conf["bc_ignore"] = True
        if conf:
            result[key] = conf
    return result


def load_structure_bc_names(gcs: storage.Client, ticker: str) -> set[str]:
    """
    structure.json の monthly_items[*].name / metrics[*].name（BC 正名）を集合で返す。
    旧版 (metrics) / 新版 (monthly_items) 両対応。
    """
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/structure.json")
        structure = json.loads(blob.download_as_text())
    except Exception:
        return set()
    names: set[str] = set()
    for key in ("monthly_items", "metrics"):
        for it in structure.get(key, []):
            if isinstance(it, dict) and it.get("name"):
                names.add(str(it["name"]))
    return names


def check_adapter_definitions(
    gcs: storage.Client, ticker: str,
) -> dict:
    """
    adapter.json の fields と structure.json.monthly_items の整合性を事前チェック。

    Returns:
        {
            "ticker": str,
            "total": int,          # 全 field 数
            "bc_ignore": int,      # bc_ignore=true の field 数
            "with_bc_key": int,    # bc_key 明示 field 数
            "issues": list[dict],  # 定義不備リスト
        }
    """
    try:
        adapter_blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/extract_adapter.json")
        adapter = json.loads(adapter_blob.download_as_text())
    except Exception:
        return {"ticker": ticker, "total": 0, "bc_ignore": 0, "with_bc_key": 0, "issues": []}

    bc_names = load_structure_bc_names(gcs, ticker)
    fields = adapter.get("fields", [])
    total = len(fields)
    bc_ignore = sum(1 for f in fields if f.get("bc_ignore"))
    with_bc_key = sum(1 for f in fields if "bc_key" in f)

    issues: list[dict] = []
    for f in fields:
        if f.get("bc_ignore"):
            continue
        key = str(f.get("key", ""))
        bc_key = f.get("bc_key")
        target = str(bc_key) if bc_key else key
        if not bc_names:
            issues.append({"key": key, "bc_key": bc_key, "issue": "structure_missing"})
            continue
        if target not in bc_names:
            issues.append({
                "key": key,
                "bc_key": bc_key,
                "issue": "bc_key_not_in_structure" if bc_key else "key_not_in_structure_and_no_bc_key",
            })
    return {
        "ticker": ticker,
        "total": total,
        "bc_ignore": bc_ignore,
        "with_bc_key": with_bc_key,
        "issues": issues,
    }


def print_precheck_banner(gcs: storage.Client, tickers: list[str]) -> dict:
    """
    compare 実行前に全対象銘柄の adapter / structure 定義整合性をチェックして
    banner 表示。結果サマリを返す（呼び出し元で CSV 出力可能）。
    """
    log("=== 🔍 bc_key / structure.json 定義整合性 pre-check ===")
    total_fields = 0
    total_bc_ignore = 0
    total_with_bc_key = 0
    problem_tickers: list[dict] = []
    for t in tickers:
        r = check_adapter_definitions(gcs, t)
        total_fields += r["total"]
        total_bc_ignore += r["bc_ignore"]
        total_with_bc_key += r["with_bc_key"]
        if r["issues"]:
            problem_tickers.append(r)

    total_comparable = total_fields - total_bc_ignore
    total_implicit = total_comparable - total_with_bc_key
    log(f"  対象銘柄            : {len(tickers)}")
    log(f"  全 fields           : {total_fields}")
    log(f"    うち bc_ignore     : {total_bc_ignore}")
    log(f"    うち BC 突合対象   : {total_comparable}")
    log(f"      bc_key 明示      : {total_with_bc_key}")
    log(f"      bc_key 省略      : {total_implicit}（= key を暗黙の bc_key として照合）")
    total_issues = sum(len(p["issues"]) for p in problem_tickers)
    if total_issues:
        log(f"  ⚠️  定義不備       : {len(problem_tickers)} 銘柄 / {total_issues} field")
        log(f"      （structure.monthly_items[*].name に対応先が見つからない field）")
        shown = 0
        for p in problem_tickers:
            if shown >= 20:
                break
            for iss in p["issues"][:3]:
                log(f"      {p['ticker']}: key='{iss['key'][:40]}' bc_key={iss['bc_key']!r} reason={iss['issue']}")
                shown += 1
                if shown >= 20:
                    break
        if total_issues > 20:
            log(f"      ... 他 {total_issues - 20} 件（CSV: {OUT_DIR / 'bc_key_precheck.csv'} 参照）")
        # CSV 出力
        precheck_csv = OUT_DIR / "bc_key_precheck.csv"
        try:
            with open(precheck_csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["ticker", "key", "bc_key", "issue"])
                for p in problem_tickers:
                    for iss in p["issues"]:
                        w.writerow([p["ticker"], iss["key"], iss.get("bc_key"), iss["issue"]])
            log(f"  不備詳細 CSV       : {precheck_csv}")
        except Exception as e:
            log(f"  不備詳細 CSV 書き込み失敗: {e}")
    else:
        log(f"  ✅ 定義不備なし")
    log("=" * 60)
    return {
        "total_fields": total_fields,
        "total_bc_ignore": total_bc_ignore,
        "total_with_bc_key": total_with_bc_key,
        "total_issues": total_issues,
        "problem_tickers": problem_tickers,
    }


def read_monthly_records(gcs: storage.Client, ticker: str) -> Optional[dict]:
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_RECORD}/{ticker}/monthly_records.json")
        return json.loads(blob.download_as_text())
    except Exception as e:
        log(f"  [{ticker}] GCS読み込みエラー: {e}")
        return None


# ──────────────────────────────────────────────
# Selenium
# ──────────────────────────────────────────────

def create_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    import os
    from pathlib import Path

    # ChromeDriverManager を使わず、キャッシュ済みの chromedriver を直接指定
    _CHROMEDRIVER = os.environ.get(
        "CHROMEDRIVER_PATH",
        str(Path.home() / ".wdm/drivers/chromedriver/win64/146.0.7680.165/chromedriver.exe"),
    )

    opts = Options()
    opts.binary_location = CHROME_EXE
    opts.page_load_strategy = "none"
    opts.add_argument("--window-size=1200,800")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--js-flags=--max-old-space-size=512")
    opts.add_argument("--memory-pressure-off")
    opts.add_argument("--disable-extensions")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(_CHROMEDRIVER)
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
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


def scrape_buffett_kpi(driver, ticker: str) -> dict[str, dict]:
    """
    バフェットコード KPI ページから月次データを取得。

    ページ構造（HTML テーブル）:
      <thead><tr>
        <th>2026年</th><th>1月</th>...<th>12月</th>  ← 年ごとに thead が存在
      </tr></thead>
      <tbody><tr>
        <th>メトリクス名</th><td>値</td>...<td>値</td>  ← データ行
      </tr></tbody>

    Returns:
        { "2026-02": {"全店 店舗数": 192, ...}, ... }
    """
    from selenium.webdriver.common.by import By

    url = BUFFETT_URL.format(code=ticker)
    driver.get(url)
    time.sleep(8)
    wait_waf(driver, max_sec=30)
    time.sleep(2)

    result: dict[str, dict] = {}

    try:
        tables = driver.find_elements(By.TAG_NAME, "table")
        if not tables:
            log(f"  [{ticker}] テーブルなし")
            return result

        table = tables[0]
        rows = table.find_elements(By.TAG_NAME, "tr")

        current_year: Optional[int] = None
        current_months: list[int] = []  # 各列に対応する月番号

        for row in rows:
            ths = row.find_elements(By.TAG_NAME, "th")
            tds = row.find_elements(By.TAG_NAME, "td")

            # スペーサー行をスキップ
            if tds and all("kpi__table-spacer" in (td.get_attribute("class") or "")
                           for td in tds):
                continue

            th_texts = [th.text.strip() for th in ths]
            td_texts = [td.text.strip() for td in tds]

            # ヘッダー行: th のみ（最初の th が "YYYY年"）
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
                        if ym not in result:
                            result[ym] = {}
                        result[ym][metric_name] = val
                    except ValueError:
                        pass

        if result:
            log(f"  [{ticker}] バフェットコード {len(result)}ヶ月分取得")
        else:
            log(f"  [{ticker}] バフェットコード データなし（テーブルは存在するが値なし）")

    except Exception as e:
        log(f"  [{ticker}] テーブルパースエラー: {e}")

    return result


# ──────────────────────────────────────────────
# 比較ロジック
# ──────────────────────────────────────────────

def compare_records(
    gcs_records: list[dict],
    buffett_data: dict[str, dict],
    adapter_fields: Optional[dict[str, dict]] = None,
) -> list[dict]:
    """
    GCS の records と buffett-code の月次データを突合する（2026-04-18 改修版）。

    マッチング戦略（決定論的 / drift 許容）:
      1. adapter.fields[*].bc_key 明示 → その名前で bc_ym を直接引き当て
      2. bc_key 省略 → our_key を暗黙の bc_key として直接引き当て（移行期の後方互換）
      3. いずれも不在 → BC_NODATA
      ※ 正規化一致・部分一致・値近似 tiebreaker・YoY+100 自動検出はすべて廃止。
         structure.json の定義に従わない drift は pre-check banner で洗い出して
         adapter 側で bc_key を明示する運用に倒す。

    アダプター調整（adapter_fields で指定）:
      unit_scale  : our_val * unit_scale を比較値とする（デフォルト 1.0）
                    例: 百万円で取ったが BC は円 → unit_scale: 1000000
      yoy_offset  : our_val + yoy_offset を比較値とする（デフォルト 0）
                    例: 変化率で取ったが BC は 100+変化率 → yoy_offset: 100
      （※ unit_scale/yoy_offset 誤設定による NG は「adapter 設定誤りの検出」として
        意図通りの挙動。自動補正はしない）

    BC 表示精度の自動吸収:
      BC は小数点 N 桁表示、our は N+1 桁以上取りうる。bc_val の表示桁数を検出して
      our_val を round/floor/ceil の 3 候補に変換し、いずれかと完全一致すれば diff=0。
      これにより旧 bc_floor フラグは不要化（floor 候補が自動的に含まれる）。

    Returns:
        比較結果のリスト
    """
    if adapter_fields is None:
        adapter_fields = {}

    rows = []
    for rec in gcs_records:
        ym = rec["year_month"]
        our_fields = rec.get("fields", {})
        bc_ym = buffett_data.get(ym, {})

        for our_key, our_val in our_fields.items():
            if not isinstance(our_val, (int, float)):
                continue

            fconf = adapter_fields.get(our_key, {})
            # bc_ignore=true の field は突合・カウント・CSV出力すべてから除外
            if fconf.get("bc_ignore"):
                continue
            unit_scale       = fconf.get("unit_scale", 1.0)
            field_yoy_offset = fconf.get("yoy_offset", 0)
            bc_floor         = fconf.get("bc_floor", False)

            # アダプター指定の unit_scale / yoy_offset を適用した比較値
            adj_val = our_val * unit_scale + field_yoy_offset
            adj_label = ""
            if unit_scale != 1.0:
                adj_label += f"×{unit_scale}"
            if field_yoy_offset != 0:
                adj_label += f"+{field_yoy_offset}"
            if bc_floor:
                # 後方互換: 旧 bc_floor フラグ（新ロジックの floor 候補に含まれるので実質冗長）
                adj_label += "(floor)"
                adj_val = math.floor(adj_val)

            # 1) bc_key 明示 / 省略時は key を bc_key とみなす
            target_bc_key = fconf.get("bc_key", our_key)

            if not bc_ym:
                rows.append({
                    "year_month": ym, "our_field": our_key, "our_value": our_val,
                    "yoy_offset": adj_label,
                    "bc_field": "", "bc_value": "", "diff": "", "match": "BC_NODATA",
                    "match_score": 0,
                })
                continue

            # 2) 直接引き当て（完全一致のみ）
            if target_bc_key not in bc_ym:
                rows.append({
                    "year_month": ym, "our_field": our_key, "our_value": our_val,
                    "yoy_offset": adj_label,
                    "bc_field": "", "bc_value": "", "diff": "", "match": "BC_NODATA",
                    "match_score": 0,
                })
                continue

            bc_val = bc_ym[target_bc_key]
            if not isinstance(bc_val, (int, float)):
                rows.append({
                    "year_month": ym, "our_field": our_key, "our_value": our_val,
                    "yoy_offset": adj_label,
                    "bc_field": target_bc_key, "bc_value": bc_val, "diff": "",
                    "match": "BC_NON_NUMERIC", "match_score": 100,
                })
                continue

            # 3) BC 表示精度の自動検出 + round/floor/ceil 3 候補マッチ
            bc_str = f"{bc_val:.10g}"
            decimals = len(bc_str.split(".")[1]) if "." in bc_str else 0
            unit = 10 ** decimals
            candidates = {
                round(adj_val * unit) / unit,
                math.floor(adj_val * unit) / unit,
                math.ceil(adj_val * unit) / unit,
            }
            if any(abs(bc_val - c) < 1e-9 for c in candidates):
                diff = 0.0
            else:
                diff = min(abs(bc_val - c) for c in candidates)

            match = "OK" if diff <= TOLERANCE else "NG"
            rows.append({
                "year_month": ym,
                "our_field": our_key,
                "our_value": our_val,
                "yoy_offset": adj_label,
                "bc_field": target_bc_key,
                "bc_value": bc_val,
                "diff": round(diff, 3),
                "match": match,
                "match_score": 100,
            })

    return rows


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offline", action="store_true", default=True,
                        help="data/csv/bc_monthly_kpi.csv をBCデータソースに使う（デフォルト）")
    parser.add_argument("--selenium", action="store_true",
                        help="Selenium でスクレイピング（明示指定時のみ）")
    parser.add_argument("--local-records", type=str, default=None,
                        help="ローカルの monthly_records JSON ディレクトリ（例: C:\\tmp\\extract_monthly_data）")
    args = parser.parse_args()
    if args.selenium:
        args.offline = False

    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_csv = OUT_DIR / f"buffett_compare_{ts}.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=== 月次データ バフェットコード突合 ===")
    gcs = get_gcs()

    # 対象ティッカー決定
    if args.tickers:
        tickers = args.tickers
    else:
        log("GCS から monthly_records.json 保有ティッカーを列挙中...")
        tickers = list_tickers_with_records(gcs)
        log(f"  → {len(tickers)}社")
        if args.limit:
            tickers = tickers[:args.limit]

    # --- オフラインモード: CSV からBCデータを一括読み込み ---
    bc_csv_cache: dict[str, dict[str, dict]] = {}  # {ticker: {year_month: {field: value}}}
    if args.offline:
        bc_csv_path = Path("data/csv/bc_monthly_kpi.csv")
        if not bc_csv_path.exists():
            log(f"エラー: {bc_csv_path} が存在しません。先に download_bc_kpi.py を実行してください。")
            return
        import pandas as pd
        bc_df = pd.read_csv(bc_csv_path, encoding="utf-8-sig")
        for _, row in bc_df.iterrows():
            t = str(row["ticker"])
            ym = str(row["year_month"])
            field = str(row["field"])
            val = row["value"]
            if t not in bc_csv_cache:
                bc_csv_cache[t] = {}
            if ym not in bc_csv_cache[t]:
                bc_csv_cache[t][ym] = {}
            try:
                bc_csv_cache[t][ym][field] = float(val)
            except (ValueError, TypeError):
                pass
        log(f"オフラインモード: bc_monthly_kpi.csv から {len(bc_csv_cache)} 社分読み込み")

    # pre-check: 全対象銘柄の adapter / structure 定義整合性を最初に洗い出す
    print_precheck_banner(gcs, tickers)

    driver = None if args.offline else create_driver()
    all_rows: list[dict] = []
    summary = {"ok": 0, "ng": 0, "bc_nodata": 0, "bc_non_numeric": 0, "gcs_empty": 0, "error": 0}

    def _is_window_error(e: Exception) -> bool:
        msg = str(e).lower()
        return any(k in msg for k in ("no such window", "target window already closed",
                                      "disconnected", "session deleted", "invalid session",
                                      "max retries exceeded", "failed to establish",
                                      "connectionrefusederror", "timeoutexception"))

    try:
        for i, ticker in enumerate(tickers, 1):
            log(f"[{i}/{len(tickers)}] {ticker}")

            # 論理削除チェック: adapter._excluded=true の銘柄は突合から除外
            try:
                _ad_blob = gcs.bucket(GCS_BUCKET).blob(f"{GCS_META}/{ticker}/extract_adapter.json")
                _ad = json.loads(_ad_blob.download_as_text())
                if _ad.get("_excluded"):
                    log(f"  [{ticker}] 管理対象外 → スキップ: {_ad.get('_excluded_reason', '')}")
                    summary.setdefault("excluded", 0)
                    summary["excluded"] += 1
                    continue
            except Exception:
                pass

            # データ読み込み（ローカル優先 → GCS フォールバック）
            gcs_data = None
            if args.local_records:
                local_path = Path(args.local_records) / f"monthly_records_{ticker}.json"
                if local_path.exists():
                    import json as _json
                    gcs_data = _json.loads(local_path.read_text(encoding="utf-8"))
            if not gcs_data:
                gcs_data = read_monthly_records(gcs, ticker)
            if not gcs_data or not gcs_data.get("records"):
                log(f"  [{ticker}] GCSデータなし → スキップ")
                summary["gcs_empty"] += 1
                continue

            # 直近3ヶ月のみ対象
            records = sorted(gcs_data["records"], key=lambda r: r["year_month"])[-3:]

            # バフェットコード取得
            if args.offline:
                buffett_data = bc_csv_cache.get(ticker, {})
                if buffett_data:
                    n_months = sum(len(v) for v in buffett_data.values())
                    log(f"  [{ticker}] CSV キャッシュ {n_months} フィールド")
                else:
                    log(f"  [{ticker}] CSV キャッシュにデータなし")
            elif True:  # Selenium モード
              try:
                buffett_data = scrape_buffett_kpi(driver, ticker)
              except Exception as e:
                if _is_window_error(e):
                    log(f"  [{ticker}] Selenium window クラッシュ検知 → driver 再作成してリトライ")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    time.sleep(5)
                    driver = create_driver()
                    try:
                        buffett_data = scrape_buffett_kpi(driver, ticker)
                    except Exception as e2:
                        log(f"  [{ticker}] リトライ失敗: {e2}")
                        summary["error"] += 1
                        continue
                else:
                    log(f"  [{ticker}] Selenium エラー: {e}")
                    summary["error"] += 1
                    continue

            # アダプター読み込み（unit_scale / yoy_offset 取得）
            adapter_fields = load_adapter_fields(gcs, ticker)

            # 突合
            rows = compare_records(records, buffett_data, adapter_fields)
            for row in rows:
                row["ticker"] = ticker
                all_rows.append(row)
                if row["match"] == "OK":
                    summary["ok"] += 1
                elif row["match"] == "NG":
                    summary["ng"] += 1
                elif row["match"] == "BC_NON_NUMERIC":
                    summary["bc_non_numeric"] += 1
                else:
                    summary["bc_nodata"] += 1

            # 結果サマリーを都度表示
            if rows:
                for row in rows:
                    status = "✅" if row["match"] == "OK" else ("❌" if row["match"] == "NG" else "⚠️")
                    adj_note = f" [{row['yoy_offset']}]" if row.get("yoy_offset") else ""
                    log(f"  {status} {row['year_month']} {row['our_field']}="
                        f"{row['our_value']}{adj_note} vs BC:{row['bc_field']}={row['bc_value']} "
                        f"diff={row['diff']}")

            if not args.offline:
                time.sleep(2)  # レートリミット配慮

    finally:
        if driver:
            driver.quit()

    # CSV 保存
    if all_rows:
        fieldnames = ["ticker", "year_month", "our_field", "our_value",
                      "yoy_offset", "bc_field", "bc_value", "diff", "match", "match_score"]
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        log(f"\n結果保存: {out_csv}")

    # サマリー表示
    log("\n=== 突合サマリー ===")
    total_fields = summary["ok"] + summary["ng"] + summary["bc_nodata"] + summary.get("bc_non_numeric", 0)
    log(f"  対象ティッカー: {len(tickers)}社")
    log(f"  総フィールド数: {total_fields}")
    log(f"  ✅ 一致 (diff≤{TOLERANCE}): {summary['ok']}")
    log(f"  ❌ 不一致:                  {summary['ng']}")
    log(f"  ⚠️  BC未取得:               {summary['bc_nodata']}")
    if summary.get("bc_non_numeric"):
        log(f"  ⚠️  BC値が非数値:           {summary['bc_non_numeric']}")
    log(f"  GCSデータなし:              {summary['gcs_empty']}")
    log(f"  エラー:                     {summary['error']}")
    if total_fields > 0:
        match_rate = summary["ok"] / total_fields * 100
        log(f"  一致率: {match_rate:.1f}%")


if __name__ == "__main__":
    main()
