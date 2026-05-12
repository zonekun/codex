"""QUICK コンセンサス取得テスト（松井証券リサーチネット経由）
9501 で印刷フォーマットページからデータ取得できるか検証する。
全段階のHTMLダンプを取得する調査モード。

Usage:
    PYTHONUTF8=1 python scripts/test_conse_quick.py
"""

from __future__ import annotations

import os
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================
# 定数（OrderForMABatch.py からコピー）
# ==========================================
STR_USER_NAME = "62406772"
STR_PASSWORD = "gundam01"
OTP_FILE_PATH = r"C:\Users\zonekun\Dropbox\アプリ\kabucom\matsui.txt"

TEST_TICKER = "9501"


# ==========================================
# OrderForMABatch.py からコピー（変更なし）
# ==========================================


def setup_driver():
    options = Options()
    user_data_dir_path = "C:\\Users\\zonekun\\AppData\\Local\\Microsoft\\Edge\\User Data"
    options.add_argument(f"--user-data-dir={user_data_dir_path}")
    options.add_argument('--profile-directory=Default')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--remote-debugging-port=9782')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    return webdriver.Edge(options=options)


def login_matsui(driver):
    driver.get('https://www.deal.matsui.co.jp/ITS/login/MemberLogin.jsp')
    wait = WebDriverWait(driver, 10)
    wait.until(EC.presence_of_element_located((By.ID, 'login-id'))).send_keys(STR_USER_NAME)
    driver.find_element(By.NAME, 'passwd').send_keys(STR_PASSWORD)
    if os.path.exists(OTP_FILE_PATH):
        os.remove(OTP_FILE_PATH)
    driver.find_element(By.CLASS_NAME, "login-form-submit").find_element(By.TAG_NAME, 'button').click()

    otp_code = None
    for i in range(120):
        if os.path.exists(OTP_FILE_PATH):
            with open(OTP_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) == 6 and content.isdigit():
                otp_code = content
                os.remove(OTP_FILE_PATH)
                break
        time.sleep(2)
    if not otp_code: sys.exit()

    wait.until(EC.presence_of_element_located((By.NAME, "authNo"))).send_keys(otp_code)
    wait.until(EC.element_to_be_clickable((By.XPATH, '//input[@alt="認証する"]'))).click()


# ==========================================
# ダンプユーティリティ
# ==========================================


def dump_html(driver, path: str, label: str) -> None:
    html = driver.page_source
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [DUMP] {label} → {path} ({len(html)} bytes)")


def dump_all_frames(driver, prefix: str) -> None:
    """現在のウィンドウの全フレームをダンプ。"""
    driver.switch_to.default_content()
    frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
    print(f"  フレーム数: {len(frames)}")
    frame_names = []
    for i, f in enumerate(frames):
        name = f.get_attribute("name") or f"frame{i}"
        src = (f.get_attribute("src") or "")[:100]
        print(f"    [{i}] name={name!r} src={src!r}")
        frame_names.append(name)

    for name in frame_names:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(name)
            dump_html(driver, rf"C:\tmp\{prefix}_{name}.html", f"{prefix}/{name}")
            sub_frames = driver.find_elements(By.TAG_NAME, "frame") + driver.find_elements(By.TAG_NAME, "iframe")
            if sub_frames:
                sub_names = []
                print(f"    {name} サブフレーム数: {len(sub_frames)}")
                for j, sf in enumerate(sub_frames):
                    sname = sf.get_attribute("name") or f"sub{j}"
                    print(f"      [{j}] name={sname!r}")
                    sub_names.append(sname)
                for sname in sub_names:
                    try:
                        driver.switch_to.default_content()
                        driver.switch_to.frame(name)
                        driver.switch_to.frame(sname)
                        dump_html(driver, rf"C:\tmp\{prefix}_{name}_{sname}.html", f"{prefix}/{name}/{sname}")
                    except Exception as e:
                        print(f"      [{sname}] error: {e}")
        except Exception as e:
            print(f"    [{name}] error: {e}")
    driver.switch_to.default_content()


# ==========================================
# メイン
# ==========================================


def main() -> None:
    driver = setup_driver()
    try:
        # ---- ログイン + OTP ----
        print("[1] ログイン")
        login_matsui(driver)
        time.sleep(5)
        print("[1] ログイン完了")

        # ---- リサーチネット遷移 ----
        print("[2] リサーチネット遷移")
        wait = WebDriverWait(driver, 10)

        driver.switch_to.default_content()
        driver.switch_to.frame("GM")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., '情報検索')]"))).click()

        driver.switch_to.default_content()
        driver.switch_to.frame("LM")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'リサーチネット')]"))).click()

        driver.switch_to.default_content()
        driver.switch_to.frame("CT")
        wait.until(EC.element_to_be_clickable((By.XPATH, "//img[@alt='起動する']"))).click()

        time.sleep(3)
        handles = driver.window_handles
        driver.switch_to.window(handles[-1])
        print("[2] リサーチネット起動完了")

        # ---- DUMP A: リサーチネットトップ ----
        print("\n=== DUMP A: リサーチネットトップ ===")
        print(f"  URL: {driver.current_url}")
        dump_html(driver, r"C:\tmp\rn_A_top.html", "A_top")
        dump_all_frames(driver, "rn_A")

        # ---- 検索実行 ----
        print(f"\n[3] 銘柄検索: {TEST_TICKER}")
        wait = WebDriverWait(driver, 10)
        search_box = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#text input[type='text']")))
        search_box.clear()
        search_box.send_keys(TEST_TICKER)
        driver.find_element(By.CSS_SELECTOR, "input[alt='検索']").click()
        time.sleep(3)

        # ---- DUMP B: 検索結果 ----
        print("\n=== DUMP B: 検索結果 ===")
        print(f"  URL: {driver.current_url}")
        dump_html(driver, r"C:\tmp\rn_B_search.html", "B_search_top")
        dump_all_frames(driver, "rn_B")

        # ---- 検索結果から銘柄クリック（qrn_frame_main内） ----
        print(f"\n[4] 銘柄リンクをクリック")
        driver.switch_to.default_content()
        driver.switch_to.frame("qrn_frame_main")
        try:
            link = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, f"//a[contains(@href, 'report_summary') and contains(@href, 'rcode={TEST_TICKER}')]")))
        except Exception:
            print(f"  銘柄 {TEST_TICKER} の検索結果なし → スキップ")
        else:
            link.click()
            time.sleep(3)

            # ---- 決算・財務クリック ----
            print(f"\n[5] 決算・財務")
            driver.switch_to.default_content()
            driver.switch_to.frame("qrn_frame_main")
            wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., '決算・財務')]"))).click()
            time.sleep(3)

            # ---- 印刷フォーマット表示クリック ----
            print(f"\n[6] 印刷フォーマット表示")
            driver.switch_to.default_content()
            driver.switch_to.frame("qrn_frame_main")
            try:
                btn = driver.find_element(By.XPATH, "//input[@value='印刷フォーマット表示']")
            except Exception:
                try:
                    btn = driver.find_element(By.XPATH, "//img[@alt='印刷フォーマット表示']/parent::a")
                except Exception:
                    btn = driver.find_element(By.XPATH, "//*[contains(., '印刷フォーマット表示')]")
            btn.click()
            time.sleep(3)

            # 別ウィンドウが開いた場合
            new_handles = driver.window_handles
            if len(new_handles) > len(handles):
                driver.switch_to.window(new_handles[-1])

            # ---- [CON]データ抽出 ----
            print("\n=== [CON]データ抽出 ===")
            html = driver.page_source
            con_pattern = (
                r'<td[^>]*>(\d{4}/\d{2})\([^)]*\)<[^>]*>[^<]*<[^>]*>\[CON\]</span></font></td>\s*'
                r'(?:<script><!--\s*)?'
                r'tableTdI\("([^"]*)"\);\s*'
                r'(?:-->.*?)?'
                r'tableTdI\("([^"]*)"\);\s*'
                r'(?:-->.*?)?'
                r'tableTdI\("([^"]*)"\);\s*'
                r'(?:-->.*?)?'
                r'tableTdI\("([^"]*)"\);\s*'
                r'(?:-->.*?)?'
                r'tableTdI\("([^"]*)"\);'
            )
            matches = list(re.finditer(con_pattern, html, re.DOTALL))
            for m in matches:
                revenue = m.group(2)
                if '.' not in revenue and revenue != '-':
                    print(f"  FY={m.group(1)} 売上高={revenue} 営業利益={m.group(3)} 経常利益={m.group(4)} 純利益={m.group(5)} EPS={m.group(6)}")
            if not any('.' not in m.group(2) and m.group(2) != '-' for m in matches):
                print(f"  銘柄 {TEST_TICKER} の[CON]データなし")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
