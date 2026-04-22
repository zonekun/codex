"""
楽天証券 IFIS コンセンサス（経常利益）取得 → BigQuery STOCK.CONSENSUS ロード
実行タイミング: 明示的な指示があったときのみ（ローカル専用・Selenium使用）

【再開機能】
    実行中断時は data/logs/conse_resume.json に状態が保存される。
    次回起動時に自動的に続きから再開（DATAATは最初の実行日で固定）。
    強制的に新規実行するには --fresh フラグを指定する。

【前提】
    - Chrome + ChromeDriver がインストール済みであること
    - SeleniumProfile3 プロファイルで楽天証券にログイン済みであること
    - 画像認証が出た場合は C:\\Users\\zonekun\\Dropbox\\アプリ\\kabucom\\raku.txt に
      クリックするキーワードを1行ずつ書いて保存すること（自動で読み込まれる）
"""

import sys
import os
import csv

import time
import json
import re
import io
import argparse
from datetime import date, datetime
from bs4 import BeautifulSoup

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from PIL import Image

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from google.oauth2 import service_account
from google.cloud import bigquery

# ==========================================
# パス・定数設定
# ==========================================
_BASE = os.path.join(os.path.dirname(__file__), '..')
KEY_FILE   = os.path.join(_BASE, 'keys', 'gcp-service-account.json')
STATE_FILE = os.path.join(_BASE, 'data', 'logs', 'conse_resume.json')
BQ_TABLE   = 'gmailpj-357912.STOCK.CONSENSUS'
_DEBUG_ROOT = r"C:\tmp"

# CSV保存先パス
CSV_PATH   = r"C:\Users\zonekun\Dropbox\stock\py\conse_raku.csv"

# ==========================================
# アカウント情報・APIキー
# ==========================================
GEMINI_API_KEY = "AIzaSyA7pAjWL3oPFAsDtnaVwaX9m7lsf74e1Tk"
GEMINI_MODEL   = "gemini-3-flash-preview"

strUserName = "SQBS9695"
strPassword = "gundam01"
strPin      = "a178"

_gemini_client = genai.Client(api_key=GEMINI_API_KEY)


class EmojiChoice(BaseModel):
    """画像認証で Gemini が選ぶ emoji の id（0〜9）。"""
    id: int = Field(ge=0, le=9)

# ==========================================
# BigQuery クライアント
# ==========================================

def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project='gmailpj-357912', credentials=creds)


def insert_to_bq(client: bigquery.Client, rows: list) -> bool:
    """BQ にストリーミングインサート。"""
    errors = client.insert_rows_json(BQ_TABLE, rows)
    if errors:
        print(f"  ⚠️ BQ書き込みエラー: {errors}")
        return False
    return True

# ==========================================
# CSV保存用関数 (データのみ・1行形式)
# ==========================================

def save_to_csv(row_dict: dict) -> None:
    """データをCSVファイルに1銘柄1行で追記保存する (項目名なし・Shift-JIS)。"""
    # 書き出し順序の定義
    keys = ["TICKER", "1Q_CURRENT", "2Q_CURRENT", "3Q_CURRENT", "FY_CURRENT", "FY_NEXT"]
    # 辞書からリストに変換
    row_data = [row_dict.get(k, "") for k in keys]

    try:
        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        
        # 'a' (append) モードで開き、項目名は書き込まない
        with open(CSV_PATH, mode='a', newline='', encoding='cp932', errors='replace') as f:
            writer = csv.writer(f)
            writer.writerow(row_data)
    except Exception as e:
        print(f"  ⚠️ CSV保存失敗: {e}")

# ==========================================
# 再開用状態管理
# ==========================================

def load_state() -> dict | None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_state(dataat: str, last_ticker: str) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({"dataat": dataat, "last_ticker": last_ticker}, f)


def clear_state() -> None:
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

# ==========================================
# 銘柄リスト取得
# ==========================================

def get_japanese_stock_tickers(client: bigquery.Client) -> list:
    """BigQuery STOCK_CODE_LIST からプライム・スタンダード・グロース（内国株式）を取得。
    BQ 取得失敗時は JPX Excel にフォールバック。
    """
    # ---- BQ 方式（メイン） ----
    print("📊 BigQuery から銘柄リストを取得中...")
    try:
        query = """
            SELECT TICKER
            FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
            WHERE EXCHANGE = 'TSE'
              AND MARKET_CATEGORY IN (
                'プライム（内国株式）',
                'スタンダード（内国株式）',
                'グロース（内国株式）'
              )
            ORDER BY TICKER
        """
        rows = list(client.query(query).result())
        tickers = [row["TICKER"] for row in rows]
        print(f"✅ {len(tickers)} 銘柄取得完了（BQ）")
        return tickers
    except Exception as e:
        print(f"⚠️ BQ 取得失敗: {e} → JPX Excel にフォールバック")

    # ---- JPX Excel 方式（フォールバック） ----
    import io
    import requests
    import pandas as pd

    print("📊 JPX 上場銘柄一覧 Excel を取得中...")
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), dtype=str)
        target_categories = {
            "プライム（内国株式）",
            "スタンダード（内国株式）",
            "グロース（内国株式）",
        }
        df = df[df["市場・商品区分"].isin(target_categories)]
        tickers = sorted(df["コード"].str.zfill(4).tolist())
        print(f"✅ {len(tickers)} 銘柄取得完了（JPX Excel）")
        return tickers
    except Exception as e2:
        print(f"❌ 銘柄リスト取得失敗（Excel）: {e2}")
        return []

# ==========================================
# ログイン・認証関連
# ==========================================

def read_alt_texts_from_file():
    file_path = r"C:\Users\zonekun\Dropbox\アプリ\kabucom\raku.txt"
    max_retries    = 120
    retry_interval = 2

    print(f"📁 指示ファイル({os.path.basename(file_path)})の作成を待機しています...")

    for i in range(max_retries):
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = [line.strip() for line in f if line.strip()]
                if not content:
                    print(f"⚠️ ファイルはありましたが内容が空です。再試行します... ({i + 1}/{max_retries})")
                    time.sleep(retry_interval)
                    continue
                print(f"✅ ファイルを読み込みました: {content}")
                try:
                    os.remove(file_path)
                    print("🗑️ 読み込み済みファイルを削除しました。")
                except OSError as e_remove:
                    print(f"⚠️ ファイル削除失敗: {e_remove}")
                return content
            except Exception as e:
                print(f"⚠️ 読み込み中にエラーが発生しました: {e} - 再試行します...")
        if i < max_retries - 1:
            time.sleep(retry_interval)
        else:
            print("❌ タイムアウト: 指定時間内にファイルが見つかりませんでした。")
            return []

    return []


def _capture_emojis(driver, debug_dir=None, tag=""):
    """emoji_0〜emoji_9 の img 要素を都度 DOM から引いてキャプチャし、(images, buttons) を返す。
    シャッフル対策で毎回 find_element する。debug_dir 指定時は {tag}_emoji_{i}.png で保存。"""
    images = []
    buttons = []
    for i in range(10):
        element_id = f"emoji_{i}"
        button = driver.find_element(By.ID, element_id)
        buttons.append(button)
        img_element = button.find_element(By.TAG_NAME, "img")
        png_data = img_element.screenshot_as_png
        image = Image.open(io.BytesIO(png_data))
        images.append(image)
        if debug_dir is not None:
            image.save(os.path.join(debug_dir, f"{tag}_emoji_{i}.png"))
    return images, buttons


def perform_image_authentication(driver):
    target_alt_texts = read_alt_texts_from_file()
    if not target_alt_texts:
        print("テキストファイルが空か、読み込めませんでした。処理を終了します。")
        return

    print(f"✅ クリック対象のキーワード: {target_alt_texts}")

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=EmojiChoice,
    )

    debug_dir = os.path.join(_DEBUG_ROOT, f"rakuten_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(debug_dir, exist_ok=True)
    print(f"🗂️ デバッグPNG保存先: {debug_dir}")

    try:
        wait = WebDriverWait(driver, 20)
        wait.until(expected_conditions.presence_of_element_located((By.ID, "emoji_0")))

        clicked_count = 0

        for kw_idx, alt_text in enumerate(target_alt_texts, start=1):
            print(f"\n🔍 [kw{kw_idx}] キーワード '{alt_text}' に最も似ている画像を探しています...")

            # 毎キーワードで DOM から再取得（クリック後のシャッフル対応）
            try:
                emoji_images, emoji_buttons = _capture_emojis(
                    driver,
                    debug_dir=debug_dir,
                    tag=f"kw{kw_idx}_pre",
                )
            except NoSuchElementException as e:
                print(f"❌ 画像要素が見つかりませんでした: {e}")
                return

            prompt_text = "\n".join([
                "以下の10枚の画像は、順番に id 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 です。",
                f"この中から、単語「{alt_text}」を表しているイラストとして最も適切な（類似度が高い）画像の id を1つだけ選んでください。",
                "回答は 0 から 9 までの数字のみを返してください。それ以外の文字は不要です。",
            ])

            contents = [prompt_text, *emoji_images]

            try:
                response = _gemini_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=config,
                )
                result_text = (response.text or "").strip()
                print(f"  raw response: {result_text}")

                with open(os.path.join(debug_dir, f"kw{kw_idx}_gemini.txt"), "w", encoding="utf-8") as f:
                    f.write(f"keyword: {alt_text}\nraw: {result_text}\n")

                choice = response.parsed
                if isinstance(choice, EmojiChoice):
                    best_index = choice.id
                else:
                    data = json.loads(result_text)
                    best_index = int(data["id"])

                if 0 <= best_index <= 9:
                    print(f"🤖 Geminiの判定: ID {best_index} が '{alt_text}' です。")
                    target_button = emoji_buttons[best_index]
                    driver.execute_script("arguments[0].click();", target_button)
                    print(f"🎯 ボタン(ID: emoji_{best_index})をクリックしました。")
                    clicked_count += 1
                    time.sleep(2.5)  # シャッフル / 再描画待ち

                    try:
                        _capture_emojis(driver, debug_dir=debug_dir, tag=f"kw{kw_idx}_post")
                    except Exception as cap_err:
                        print(f"  ⚠️ post-click キャプチャ失敗: {cap_err}")
                else:
                    print(f"❌ Geminiが範囲外のIDを返しました: {best_index}")

            except Exception as api_error:
                print(f"❌ Gemini API エラー: {api_error}")
                continue

        if clicked_count == len(target_alt_texts):
            print("\n✔️ 全ての画像の選択が完了しました。")
            try:
                print("🔍 「認証する」ボタンを探しています...")
                auth_button = driver.find_element(By.XPATH, "//input[@value='認証する']")
                print("🎯 「認証する」ボタンをクリックします。")
                auth_button.click()
                time.sleep(5)
            except NoSuchElementException:
                print("❌ 「認証する」ボタンが見つかりませんでした。")
        else:
            print(f"⚠️ 一部の画像のクリックに失敗しました（成功: {clicked_count}/{len(target_alt_texts)}）")

    except Exception as e:
        print(f"Seleniumの処理中にエラーが発生しました: {e}")


def login(driver):
    wait = WebDriverWait(driver, 30)
    driver.get('https://www.rakuten-sec.co.jp/ITS/V_ACT_Login.html')
    time.sleep(2)

    j_username = driver.find_element(By.NAME, "loginid")
    j_username.clear()
    j_username.send_keys(strUserName)

    j_password = driver.find_element(By.NAME, "passwd")
    j_password.clear()
    j_password.send_keys(strPassword)

    time.sleep(2)

    wait.until(expected_conditions.element_to_be_clickable((By.ID, "login-btn"))).click()
    time.sleep(10)
    perform_image_authentication(driver)

# ==========================================
# データ抽出
# ==========================================

def clean_number(text: str) -> str:
    cleaned = re.sub(r"[,\s\u3000\xa0]", "", text.strip())
    return cleaned if re.match(r'^-?\d+$', cleaned) else ""


def extract_consensus(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    # 四半期テーブル
    progress_table = None
    for table in soup.find_all("table", class_="tbl-data-01"):
        thead = table.find("thead")
        if thead and "1Q" in thead.get_text():
            progress_table = table
            break
    if progress_table:
        cons_row = progress_table.find("tr", class_="cons")
        if cons_row:
            tds, qs = cons_row.find_all("td"), ["1Q", "2Q", "3Q", "FY"]
            for i, quarter in enumerate(qs):
                if i < len(tds):
                    val = clean_number(tds[i].get_text())
                    if val: results.append({"QUARTER": quarter, "PROFIT": int(val), "TARGET": "CURRENT"})
    # 通期テーブル
    fin_table = soup.find("table", class_="tbl-data-09")
    if fin_table:
        cons_rows, current_found = fin_table.find_all("tr", class_="cons"), False
        for row in cons_rows:
            tds = row.find_all("td")
            val = clean_number(tds[5].get_text()) if len(tds) > 5 else ""
            if not val: continue
            target = "CURRENT" if not current_found else "NEXT"
            current_found = True
            if target == "NEXT" or not any(r["QUARTER"] == "FY" and r["TARGET"] == "CURRENT" for r in results):
                results.append({"QUARTER": "FY", "PROFIT": int(val), "TARGET": target})
    return results

# ==========================================
# メイン処理
# ==========================================

def get_iframe_html(driver, code: str) -> str:
    wait = WebDriverWait(driver, 20)
    driver.switch_to.default_content()
    try:
        elem = wait.until(expected_conditions.element_to_be_clickable((By.ID, "search-stock-01")))
        elem.clear(); elem.send_keys(code)
        driver.find_element(By.ID, "searchStockFormSearchBtn").click()
        tab = wait.until(expected_conditions.element_to_be_clickable((By.XPATH, "//nobr[text()='業績']/parent::a")))
        driver.execute_script("arguments[0].click();", tab)
        iframe = wait.until(expected_conditions.presence_of_element_located((By.NAME, "stockFrame")))
        driver.switch_to.frame(iframe); html = driver.page_source
        driver.switch_to.default_content()
        return html
    except Exception: return ""


def process_codes(driver, codes: list, dataat: str, bq_client: bigquery.Client, resume_from: str | None):
    skip = resume_from is not None
    for code in codes:
        code = code.strip()
        if not code: continue
        if skip:
            if code == resume_from: skip = False
            continue

        print(f"🔍 処理中: {code}")
        html = get_iframe_html(driver, code)
        if not html:
            save_state(dataat, code); continue

        records = extract_consensus(html)
        if records:
            # BQ 保存
            bq_rows = [{"DATAAT": dataat, "TICKER": code, "FY": "000000", **rec} for rec in records]
            if insert_to_bq(bq_client, bq_rows):
                # CSV 保存 (項目名なし)
                csv_row = {k: "" for k in ["TICKER", "1Q_CURRENT", "2Q_CURRENT", "3Q_CURRENT", "FY_CURRENT", "FY_NEXT"]}
                csv_row["TICKER"] = code
                for rec in records:
                    key = f"{rec['QUARTER']}_{rec['TARGET']}"
                    if key in csv_row: csv_row[key] = rec['PROFIT']
                save_to_csv(csv_row)
                print(f"  ✅ CSV追記完了: {code}")

        save_state(dataat, code)
        time.sleep(1)


def run(fresh: bool = False):
    state = None if fresh else load_state()
    dataat = state["dataat"] if state else date.today().strftime('%Y-%m-%d')
    resume_from = state["last_ticker"] if state else None

    bq_client = get_bq_client()
    codes = get_japanese_stock_tickers(bq_client)
    if not codes: return

    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={os.path.expanduser('~')}\\AppData\\Local\\Google\\Chrome\\SeleniumProfile3")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(options=options)
    try:
        login(driver)
        process_codes(driver, codes, dataat, bq_client, resume_from)
        clear_state()
    finally:
        driver.quit()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fresh', action='store_true')
    args = parser.parse_args()
    run(fresh=args.fresh)