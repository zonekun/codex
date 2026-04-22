# Google 検索によるページ URL 取得ノウハウ

**カテゴリ**: tools
**作成日**: 2026-03-15
**更新日**: 2026-04-04
**ステータス**: 有効

---

## 概要

Google 検索を bot 検知されずにローカル実行する汎用手法。
IR ページ URL 取得に限らず、Google 検索が必要なあらゆる場面で使用する。

## 方法1: `curl_cffi`（軽量・高速）

Chrome TLS フィンガープリント偽装で bot 検知を回避する。

```python
from curl_cffi import requests as cffi_requests

resp = cffi_requests.get(
    search_url,
    headers=search_headers,
    impersonate="chrome124",  # Chrome 124 のフィンガープリントを偽装
    timeout=15,
)
```

### 検索結果のパース

Google の検索結果 HTML からリンクを抽出する。`/url?q=` リダイレクト形式が残存する場合はデコードする:

```python
def _extract_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = qs.get("q", [""])[0]
        if href.startswith("http"):
            results.append(href)
    return results
```

### 制限

- Google がヘッドレスブラウザを検知するケースがある → 方法2 にフォールバック

## 方法2: ローカル Chrome 直接検索（IP BAN 回避）

`curl_cffi` でも突破できない場合、ローカルの Chrome を Selenium で操作して実ブラウザとして検索する。

### 実装パターン

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time, urllib.parse

opts = Options()
opts.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
opts.add_argument("--disable-blink-features=AutomationControlled")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=opts)
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
})

# 検索
query = "検索クエリ"
driver.get(f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=ja")
time.sleep(4)

# JavaScript で検索結果を取得（CSS セレクタは変わる可能性あり）
results = driver.execute_script('''
    var items = [];
    document.querySelectorAll('a[jsname]').forEach(function(a) {
        var href = a.href;
        var h3 = a.querySelector('h3');
        if (h3 && href && !href.includes('google.com')) {
            items.push({title: h3.innerText, url: href});
        }
    });
    return items;
''')

driver.quit()
```

### レートリミット

- 1検索あたり **4〜6秒** の待機（`time.sleep`）
- 連続50件以上は CAPTCHA リスクあり → 30件程度で一旦停止
- CAPTCHA が出たら手動で解除してから続行

## `site:` 演算子の活用

方法1・方法2 共通。

```
1. site:ドメイン 指定で検索 → ヒットした1件目を採用
2. ヒットなし → ドメイン指定なしで再検索 → 情報サイトを除外した1件目を採用
```

ポイント:
- `site:` 演算子はGoogle側でフィルタするため、post-filterより精度が高い
- **ドメイン指定なしでも大体正しい答えが得られる**（ユーザー知見）
- ドメイン指定なしフォールバックは社名変更・ドメイン変更ケースを自然にカバーする

## reCAPTCHA 突破実績（2026-04-11 検証）

### curl_cffi による Google reCAPTCHA バイパスの仕組み

Google の reCAPTCHA は以下の複数シグナルで bot 判定を行う:

1. **TLS フィンガープリント**: TCP接続のTLSハンドシェイク時にクライアント固有のシグネチャを確認。Python `requests`/`httpx` は OpenSSL ベースで Chrome と異なるフィンガープリントを持ち、ここで弾かれる
2. **User-Agent**: HTTP ヘッダの UA 文字列
3. **IP 評価**: 同一IPからの短期間大量リクエスト
4. **JavaScript 実行環境**: `navigator.webdriver` フラグ、Canvas フィンガープリント等（ブラウザ実行時のみ）

`curl_cffi` の `impersonate="chrome124"` は **シグナル1（TLSフィンガープリント）を Chrome 124 と完全一致させる**。これは libcurl が Chrome と同じ TLS 拡張・暗号スイート順序・ALPN 設定を再現するためで、Google のサーバー側から見ると「Chrome からのリクエスト」と区別できない。

### 突破条件と限界

| 条件 | 結果 |
|------|------|
| curl_cffi + chrome124 + 適切なUA + 低頻度 | ✅ reCAPTCHA回避成功（43社連続検索で突破実績） |
| Playwright headless + stealth | ❌ reCAPTCHA発生（IP フラグ済み状態で不安定） |
| Playwright headful (real Chrome) | ❌ reCAPTCHA発生（IPが既にフラグ済みだとブラウザ種別問わず発生） |
| browser-use chromium | ❌ reCAPTCHA発生 |
| browser-use --browser real | ❌ reCAPTCHA発生 |

**重要**: IP が Google に「bot 疑い」としてフラグされると、ブラウザ種別問わず reCAPTCHA が出る。curl_cffi は IP フラグされにくい（TLS が Chrome そのものなので低リスク検知）。

### 汎用テンプレート（プロジェクト共通）

Google 検索が必要なスクリプトで共通使用するパターン:

```python
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, parse_qs

_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Referer": "https://www.google.com/",
}


def google_search(query: str, num: int = 10) -> list[dict]:
    """Google 検索し [{title, url}, ...] を返す。reCAPTCHA 回避済み。"""
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&num={num}"
    resp = cffi_requests.get(
        search_url,
        headers=_SEARCH_HEADERS,
        impersonate="chrome124",
        timeout=15,
    )
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            href = qs.get("q", [""])[0]
        if href.startswith("http") and "google.com" not in href:
            title = a.get_text(" ", strip=True)[:200]
            results.append({"title": title, "url": href})
    return results
```

### 適用済みスクリプト

| スクリプト | 利用箇所 | 備考 |
|-----------|---------|------|
| `update_monthly_adapters.py` | `google_fallback_search()` | 月次 IR ページ URL 探索。43社連続実行で reCAPTCHA 0件（2026-04-11） |
| `find_monthly_page_urls.py` | 旧版の Google 検索フォールバック | curl_cffi 使用 |

---

## 根拠・出典

`find_monthly_page_urls.py` の Mode B 実装・改善過程で確立。
- DuckDuckGo: bot 制限が厳しく実用不可
- requests + Google: CAPTCHA で弾かれる
- curl_cffi + Chrome 偽装: 2026-03-15 時点で有効
- ローカル Chrome Selenium: 2026-04-04 時点で有効
- **curl_cffi reCAPTCHA バイパス**: 2026-04-11 に 43社連続検索で突破確認。Playwright（headless/headful共）・browser-use は同一IP で全滅
