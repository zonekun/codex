# Google 検索レートリミット（CAPTCHA）の検出と回避

**カテゴリ**: tools
**作成日**: 2026-03-17
**ステータス**: 有効
**関連ファイル**: `scripts/update_monthly_adapters.py`（curl_cffi + Chrome 偽装 + Playwright で月次 IR URL 探索 & adapter 生成。2026-04-11 時点 43 社連続 reCAPTCHA 0件の実績あり、Cloud Run & ローカル両対応）。旧: `find_monthly_urls_ddg.py`（DuckDuckGo、2026-04 廃止・物理削除）、`find_monthly_page_urls.py`（curl_cffi 旧版、Mode B フォールバックのみ）

## 概要

Google 検索を自動化すると、短時間に多数のリクエストを送った場合にレートリミット（CAPTCHAページ）が返される。検出せずに放置すると、本来見つかるはずの会社を「見つからない」と誤判定してしまう。

## レートリミットの検出方法

Google がレートリミットを発動すると、検索結果ページ内のリンクが **すべて `support.google.com` 等の Google ドメイン** に差し替えられる。通常の検索結果（外部サイトへのリンク）は1件も含まれない。

```python
_GOOGLE_CONTAMINATION_DOMAINS = {
    "support.google.com",
    "accounts.google.com",
    "policies.google.com",
    "google.com",
    "www.google.com",
}

def _is_rate_limited(links: list[str]) -> bool:
    """全リンクが Google のサポート/アカウントページなら CAPTCHA と判定."""
    if not links:
        return False
    return all(
        urlparse(lnk).netloc.lstrip("www.") in _GOOGLE_CONTAMINATION_DOMAINS
        for lnk in links
    )
```

## 対処方針

### 即時対処: スリープ＆リトライ（10秒待機）

```python
_RATE_LIMIT_SLEEP_SEC = 10.0

# 検索後にリンクを確認
if _is_rate_limited(links):
    logger.warning("Google レートリミット検出。%.0f秒待機してリトライ...", _RATE_LIMIT_SLEEP_SEC)
    time.sleep(_RATE_LIMIT_SLEEP_SEC)
    links = _google_search_links(query)  # 再試行
    if _is_rate_limited(links):
        logger.error("リトライ後もレートリミット継続 → RATE_LIMITED を返す")
        return "RATE_LIMITED"
```

### 呼び出し元での扱い: RATE_LIMITED はスキップ

レートリミットを検出したら `"RATE_LIMITED"` という文字列センチネルを返し、**呼び出し元でスキップ**する。`no_links_confirmed`（見つからないと確定）には絶対に変更しない。

```python
new_url = google_fallback_search(company_name, domain, ...)
if new_url == "RATE_LIMITED":
    logger.warning("Google レートリミット → no_links_confirmed に変更せずスキップ")
    stats["rate_limited_skip"] = stats.get("rate_limited_skip", 0) + 1
    continue  # 次の会社へ。今回の実行では判定を保留する
```

### 根本的な回避策

| 手段 | 効果 | 備考 |
|------|------|------|
| **検索間隔を空ける** | 高 | 各クエリの前後に `time.sleep(2~5)` を入れる |
| **クエリ数を減らす** | 高 | 1社あたりの検索ステージ数を最小化。不要な検索ステージを削除 |
| **`duckduckgo-search` を使う** | 高 | レートリミットが Google より緩い。pip: `duckduckgo-search` |
| **SerpAPI / ScaleSerp 等の有料API** | 高 | Google 公認の検索API。商用利用に向く |
| **ブラウザ経由の検索（Playwright）** | 中 | User-Agent + Cookie が人間に近くなる |
| **バッチサイズを小さくして時間を分散** | 中 | 1回の実行で処理する会社数を減らす |
| **IP ローテーション（プロキシ）** | 高 | 実装コストが高い |

### duckduckgo-search への切り替え例

```python
from duckduckgo_search import DDGS

def _ddg_search_links(query: str, max_results: int = 5) -> list[str]:
    with DDGS() as ddgs:
        results = ddgs.text(query, max_results=max_results)
        return [r["href"] for r in results if "href" in r]
```

`duckduckgo-search` は `pyproject.toml` に既に `duckduckgo-search>=6.0` が含まれているため追加インストール不要。

## 実際に遭遇したケース（2026-03-17）

- `update_monthly_adapters.py` で37社を順次 Google 検索（1社あたり最大5クエリ = 最大185リクエスト）
- 後半の会社で全リンクが `support.google.com` のみになる現象が発生
- 9887 松屋フーズHD はレートリミットによる誤検出で `no_links_confirmed` に分類されていた
- 実際には `"松屋フーズ 月次"` で Google 検索すると1件目に IR ページが出る

## 検索クエリの最適化（レートリミット削減にも寄与）

会社名のサフィックス（ホールディングス/HD等）を除いた短縮名で検索すると**ヒット率が上がり、検索ステージ数を減らせる**。

```python
_COMPANY_SUFFIX_RE = re.compile(
    r"[\s　]*(ホールディングス|ホールディング|ＨＤ|HD|グループ|グループホールディングス)$",
    re.IGNORECASE,
)

def _short_name(company_name: str) -> str:
    return _COMPANY_SUFFIX_RE.sub("", company_name).strip()
```

例: `"松屋フーズホールディングス"` → `"松屋フーズ"`
→ `"松屋フーズ 月次"` でそのまま IR ページが1件目に出る。
