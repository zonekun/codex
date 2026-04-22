"""
ローカル Chrome (Selenium) で buffett-code.com から月次情報を取得し GCS に保存。

URL パターン:
  企業一覧: https://www.buffett-code.com/monthly_reports
  KPI詳細:  https://www.buffett-code.com/company/{code}/kpi

実行:
  PYTHONUTF8=1 python scripts/buffett_monthly_local.py
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_URL   = "https://www.buffett-code.com"
LIST_URL   = f"{BASE_URL}/monthly_reports"
KPI_URL    = f"{BASE_URL}/company/{{code}}/kpi"
GCS_BUCKET = "stock_data_1930932"
GCS_MONTHLY = "monthly"
GCS_META = "monthly/meta"
JST        = timezone(timedelta(hours=9))
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_DIR  = Path(__file__).parent.parent / "data" / "csv"
KEY_FILE   = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_gcs_client():
    from google.cloud import storage
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project="gmailpj-357912", credentials=creds)


def upload_json(gcs, gcs_path: str, data: object) -> None:
    bucket = gcs.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    log(f"  → GCS: gs://{GCS_BUCKET}/{gcs_path}")


# ──────────────────────────────────────────────
# ドライバー起動
# ──────────────────────────────────────────────

def create_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.binary_location = CHROME_EXE
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


# ──────────────────────────────────────────────
# WAF 通過待機
# ──────────────────────────────────────────────

def wait_waf(driver, max_sec: int = 30) -> bool:
    """AWS WAF JS チャレンジが通過して実コンテンツが表示されるまで待機。"""
    for i in range(max_sec):
        title = driver.title
        if title and title not in ("Human Verification", "Verification Required"):
            log(f"  WAF 通過: title={title!r} ({i}s)")
            return True
        time.sleep(1)
    log(f"  WAF 未通過 (title={driver.title!r})")
    return False


# ──────────────────────────────────────────────
# ページ構造調査（デバッグ用）
# ──────────────────────────────────────────────

def inspect_page(driver, label: str) -> None:
    """ページ構造（iframe・__NEXT_DATA__・主要要素）をログ出力。"""
    log(f"--- [{label}] 構造調査 ---")
    log(f"  title: {driver.title!r}")
    log(f"  url:   {driver.current_url}")

    # iframe 一覧
    from selenium.webdriver.common.by import By
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    log(f"  iframe数: {len(iframes)}")
    for idx, fr in enumerate(iframes):
        log(f"    [{idx}] src={fr.get_attribute('src')!r} "
            f"id={fr.get_attribute('id')!r} name={fr.get_attribute('name')!r}")

    # __NEXT_DATA__
    try:
        nd = driver.execute_script(
            "const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null;"
        )
        if nd:
            data = json.loads(nd)
            pp = data.get("props", {}).get("pageProps", {})
            log(f"  __NEXT_DATA__ pageProps keys: {list(pp.keys())}")
        else:
            log("  __NEXT_DATA__: なし")
    except Exception as e:
        log(f"  __NEXT_DATA__ エラー: {e}")

    # 主要 CSS クラスのテキスト（先頭200文字）
    try:
        body_text = driver.execute_script("return document.body.innerText;")
        log(f"  body_text (先頭200): {body_text[:200]!r}")
    except Exception:
        pass

    # ページソースをファイルに保存
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    fname = re.sub(r"[^\w]", "_", label)
    path = DEBUG_DIR / f"buffett_{fname}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    log(f"  ページソース保存: {path}")


# ──────────────────────────────────────────────
# iframe 切り替えヘルパー
# ──────────────────────────────────────────────

def switch_to_content_frame(driver) -> bool:
    """コンテンツ iframe があれば切り替える。なければ default_content のまま。"""
    from selenium.webdriver.common.by import By
    driver.switch_to.default_content()
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    if not iframes:
        return False
    # src がない or メインコンテンツっぽい iframe を選択
    for fr in iframes:
        src = fr.get_attribute("src") or ""
        if not src or "ad" not in src.lower():
            try:
                driver.switch_to.frame(fr)
                log(f"  iframe に切り替え: src={src!r}")
                return True
            except Exception:
                pass
    return False


# ──────────────────────────────────────────────
# 企業一覧取得
# ──────────────────────────────────────────────

def scrape_company_list(driver) -> list[dict]:
    from selenium.webdriver.common.by import By

    log(f"企業一覧ページ: {LIST_URL}")
    driver.get(LIST_URL)
    time.sleep(4)
    wait_waf(driver, max_sec=30)
    time.sleep(3)

    inspect_page(driver, "monthly_reports")

    # default_content と各 iframe 両方を試す
    for attempt in ["default", "iframe"]:
        if attempt == "iframe":
            if not switch_to_content_frame(driver):
                continue

        companies = []

        # __NEXT_DATA__ から取得
        try:
            nd = driver.execute_script(
                "const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null;"
            )
            if nd:
                data = json.loads(nd)
                pp = data.get("props", {}).get("pageProps", {})
                for key, val in pp.items():
                    if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                        sample = val[0]
                        # company-like データを探す
                        code_key = next((k for k in sample if "code" in k.lower()
                                         or "ticker" in k.lower()), None)
                        name_key = next((k for k in sample if "name" in k.lower()), None)
                        if code_key and name_key:
                            for item in val:
                                code = str(item.get(code_key, ""))
                                name = str(item.get(name_key, ""))
                                if re.match(r"^\d{4}$", code):
                                    companies.append({"code": code, "name": name})
                            if companies:
                                log(f"  __NEXT_DATA__[{key!r}] から {len(companies)} 社 ({attempt})")
                                return companies
        except Exception as e:
            log(f"  __NEXT_DATA__ エラー: {e}")

        # リンクから取得
        try:
            links = driver.find_elements(By.CSS_SELECTOR, "a")
            for link in links:
                href = link.get_attribute("href") or ""
                # /company/{code}/ または /monthly_reports/{code} パターン
                m = re.search(r"/company/(\w{4})/|/monthly_reports/(\w{4})", href)
                if m:
                    code = m.group(1) or m.group(2)
                    text = link.text.strip()
                    if not any(c["code"] == code for c in companies):
                        companies.append({"code": code, "name": text})
            if companies:
                log(f"  リンクから {len(companies)} 社 ({attempt})")
                return companies
        except Exception as e:
            log(f"  リンク解析エラー: {e}")

        driver.switch_to.default_content()

    return []


# ──────────────────────────────────────────────
# 各企業の KPI 構造取得
# ──────────────────────────────────────────────

def scrape_kpi_structure(driver, code: str) -> dict:
    """/company/{code}/kpi から月次項目と直近データを取得。"""
    from selenium.webdriver.common.by import By

    url = KPI_URL.format(code=code)
    driver.get(url)
    time.sleep(4)
    wait_waf(driver, max_sec=20)
    time.sleep(2)

    result = {"items": [], "sample_data": []}

    # default_content と iframe 両方試す
    for attempt in ["default", "iframe"]:
        if attempt == "iframe":
            if not switch_to_content_frame(driver):
                continue

        # __NEXT_DATA__ から取得
        try:
            nd = driver.execute_script(
                "const el=document.getElementById('__NEXT_DATA__'); return el?el.textContent:null;"
            )
            if nd:
                data = json.loads(nd)
                pp = data.get("props", {}).get("pageProps", {})

                # KPI 項目定義を探す
                for key, val in pp.items():
                    if isinstance(val, list) and len(val) > 0:
                        if isinstance(val[0], dict):
                            sample = val[0]
                            keys = list(sample.keys())
                            # 月次データっぽいキー: date/ym + 数値カラム
                            if any(k in ("date", "ym", "yearMonth", "period") for k in keys):
                                non_date_keys = [k for k in keys
                                                 if k not in ("date","ym","yearMonth","period","id","code")]
                                result["items"] = [{"name": k, "unit": "", "key": k} for k in non_date_keys]
                                result["sample_data"] = val[:3]
                                log(f"  __NEXT_DATA__[{key!r}] から {len(result['items'])} 項目 ({attempt})")
                                return result

                # kpi/indicator 系のキーを直接探す
                for key in ["kpiItems", "indicators", "metrics", "kpis", "monthlyKpi"]:
                    if isinstance(pp.get(key), list):
                        for item in pp[key]:
                            if isinstance(item, dict):
                                name = item.get("name", item.get("label", item.get("title", "")))
                                unit = item.get("unit", "")
                                k    = item.get("key", item.get("id", ""))
                                if name:
                                    result["items"].append({"name": name, "unit": unit, "key": k})
                        if result["items"]:
                            log(f"  __NEXT_DATA__[{key!r}] から {len(result['items'])} 項目 ({attempt})")
                            return result
        except Exception as e:
            log(f"  KPI __NEXT_DATA__ エラー: {e}")

        # テーブルヘッダから取得
        try:
            headers = driver.find_elements(By.CSS_SELECTOR,
                "th, [role='columnheader'], thead td")
            for h in headers:
                text = h.text.strip()
                if text and text not in ("年月", "日付", "期間", "前年同月比", ""):
                    result["items"].append({"name": text, "unit": "", "key": ""})
            if result["items"]:
                log(f"  テーブルヘッダから {len(result['items'])} 項目 ({attempt})")
                return result
        except Exception:
            pass

        driver.switch_to.default_content()

    # 調査用: 最初の企業はページソースを保存
    if not result["items"]:
        inspect_page(driver, f"kpi_{code}")

    return result


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main() -> None:
    log("=== buffett-code 月次情報スクレイピング（ローカル Chrome）===")
    gcs    = get_gcs_client()
    driver = create_driver()

    try:
        # ① 企業一覧取得
        companies = scrape_company_list(driver)

        if not companies:
            log("企業リストが空です。保存されたHTMLを確認してください:")
            log(f"  {DEBUG_DIR}/buffett_monthly_reports.html")
            input("Enter で終了...")
            return

        log(f"企業数: {len(companies)} 社")

        # ② 各企業の KPI 構造取得
        for i, company in enumerate(companies):
            code = company.get("code", "")
            name = company.get("name", "")
            if not code:
                continue

            log(f"[{i+1}/{len(companies)}] {code} {name}")
            kpi = scrape_kpi_structure(driver, code)
            company["monthly_items"] = kpi["items"]
            log(f"  → {len(kpi['items'])} 項目")

            upload_json(gcs, f"{GCS_META}/{code}/structure.json", {
                "code":          code,
                "name":          name,
                "scraped_at":    datetime.now(JST).isoformat(),
                "source":        "buffett_code_selenium",
                "monthly_items": kpi["items"],
                "sample_data":   kpi.get("sample_data", []),
            })
            time.sleep(0.8)

        # ③ 企業一覧を保存
        upload_json(gcs, f"{GCS_MONTHLY}/company_list.json", {
            "scraped_at": datetime.now(JST).isoformat(),
            "source":     "buffett_code_selenium",
            "total":      len(companies),
            "companies": [
                {"code": c["code"], "name": c["name"],
                 "items_count": len(c.get("monthly_items", []))}
                for c in companies
            ],
        })

        log(f"=== 完了: {len(companies)} 社 ===")

    finally:
        input("\n[Enter] でブラウザを閉じます...")
        driver.quit()


if __name__ == "__main__":
    main()
