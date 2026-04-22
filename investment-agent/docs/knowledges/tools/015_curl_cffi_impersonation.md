# curl_cffi によるクライアント偽装（TLS フィンガープリント + UA 固定）

**カテゴリ**: tools
**作成日**: 2026-03-01
**ステータス**: 有効
**関連ファイル**: `scripts/tdnet_download.py`, `src/collector/regional_exchange.py`

## 概要

`curl_cffi` ライブラリを使い、Python の HTTP クライアントを Chrome と同じ TLS フィンガープリント + User-Agent で偽装する。
`httpx` や `requests` では弾かれるサイト（TDnet 等）でのスクレイピングに有効。

---

## なぜ必要か

一部の証券・金融系サイトは Bot 検出として TLS フィンガープリントを確認する。
Python の `requests` / `httpx` は OpenSSL ベースの TLS フィンガープリントを持ち、Chrome とは異なるため弾かれることがある。
`curl_cffi` は libcurl ベースで Chrome の TLS フィンガープリントをそのまま再現できる。

---

## インストール

```bash
uv add curl-cffi
# または
pip install curl-cffi
```

`Dockerfile` では:
```dockerfile
RUN pip install --no-cache-dir curl-cffi>=0.6
```

> **`pyproject.toml`**: `"curl-cffi>=0.6"` を依存に追加。`httpx` は削除してよい（同等機能を curl_cffi がカバー）。

---

## 実装パターン

### requests 互換 API（推奨）

```python
from curl_cffi import requests as curl_requests

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": _UA}

resp = curl_requests.get(
    url,
    timeout=30,
    headers=HEADERS,
    impersonate="chrome124",   # ← TLS フィンガープリントを Chrome 124 に偽装
)
resp.raise_for_status()
content = resp.content
```

### Session を使う場合（複数リクエストで効率化）

```python
from curl_cffi import requests as curl_requests

session = curl_requests.Session(impersonate="chrome124")
session.headers.update({"User-Agent": _UA})

resp = session.get(url, timeout=30)
```

---

## 既存コードからの移行

| 移行前 (`httpx`) | 移行後 (`curl_cffi`) |
|-----------------|---------------------|
| `import httpx` | `from curl_cffi import requests as curl_requests` |
| `httpx.get(url, follow_redirects=True, ...)` | `curl_requests.get(url, impersonate="chrome124", ...)` |
| `except httpx.HTTPError` | `except Exception` |

| 移行前 (`requests`) | 移行後 (`curl_cffi`) |
|--------------------|---------------------|
| `import requests` | `from curl_cffi import requests` |
| `requests.get(url, headers=...)` | `requests.get(url, headers=..., impersonate="chrome124")` |

> `curl_cffi.requests` は `requests` と同じ API（`get`, `post`, `Session` 等）を持つため、
> `import requests` → `from curl_cffi import requests` の1行変更で基本的に移行完了。

---

## UA 固定値（Windows Chrome）

```python
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
```

`impersonate="chrome124"` と一致するバージョン（Chrome 124）を使うこと。

---

## `impersonate` 指定値の選択肢

| 値 | 対応ブラウザ |
|----|------------|
| `"chrome124"` | Chrome 124（推奨・実績あり） |
| `"chrome120"` | Chrome 120 |
| `"chrome110"` | Chrome 110 |
| `"safari17_0"` | Safari 17.0 |
| `"firefox121"` | Firefox 121 |

最新の利用可能な値は `curl_cffi` のドキュメント参照。通常は `"chrome124"` で十分。

---

## UA ローテーションパターン（ban 対策強化版）

固定 UA だと連続アクセスで bot 判定されやすい場合、リクエストごとにランダム切替する:

```python
import random
from curl_cffi import requests as curl_requests

_UA_POOL: list[tuple[str, str]] = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/124.0.0.0 Safari/537.36", "chrome124"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/120.0.0.0 Safari/537.36", "chrome120"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/116.0.0.0 Safari/537.36", "chrome116"),
    ("Mozilla/5.0 (Macintosh; ...) ... Chrome/124.0.0.0 Safari/537.36", "chrome124"),
    ("Mozilla/5.0 (Macintosh; ...) Safari/605.1.15", "safari17_0"),
    ("Mozilla/5.0 (Windows NT 10.0; ...) Firefox/125.0", "chrome124"),  # Firefox UA は impersonate なし
]

def _next_request_kwargs() -> dict:
    ua, impersonate = random.choice(_UA_POOL)
    return {"headers": {"User-Agent": ua}, "impersonate": impersonate}

resp = curl_requests.get(url, timeout=30, **_next_request_kwargs())
```

> **注意**: Firefox の `impersonate` 値は curl_cffi に存在しないため、Firefox UA でも `"chrome124"` を設定する。UA と impersonate プロファイルは完全一致しなくてもよい（どちらか一方だけでも効果あり）。

---

## IP ローテーションについて

### Cloud Run の場合（IP ローテーション不要）

Cloud Run は **実行ごとに異なるコンテナインスタンス（異なる外部 IP）が割り当てられる**。
1日1回程度のバッチジョブであれば、プロキシなしで自然に IP が変わる。

### 無料プロキシは推奨しない

- 信頼性が低い（頻繁にダウン）
- HTTPS（TLS）非対応が多い → `curl_cffi` の impersonate と組み合わせ不可
- レートが低く、かえってジョブが遅くなる

### 有料プロキシ（オプション）

必要な場合は環境変数 `IRBANK_PROXIES` でカンマ区切りリストを渡してラウンドロビン:

```python
import itertools, os

def _load_proxies() -> list[dict]:
    raw = os.environ.get("IRBANK_PROXIES", "")
    return [{"http": p, "https": p} for p in raw.split(",") if p.strip()]

_proxy_cycle = itertools.cycle(_load_proxies()) or None

def _next_request_kwargs() -> dict:
    ua, impersonate = random.choice(_UA_POOL)
    kwargs = {"headers": {"User-Agent": ua}, "impersonate": impersonate}
    if _proxy_cycle:
        kwargs["proxies"] = next(_proxy_cycle)
    return kwargs
```

---

## 適用済みスクリプト

| スクリプト | 偽装箇所 | 備考 |
|-----------|---------|------|
| `scripts/tdnet_download.py` | `fetch_one_page()`, `_fetch_bytes()` | TDnet 本体スクレイピング |
| `src/collector/regional_exchange.py` | `scrape_fse()`, `scrape_sse()` | FSE（福岡）・SSE（札幌）銘柄取得。NSE（名古屋）は Playwright 使用のため対象外 |
| `scripts/download_monthly.py` | `handle_scrape_links()`, `handle_html_table()`, ファイルDL | 2026-04-12改修: curl_cffi プライマリ + requests フォールバック。requests 単体では多数のIRサイトでTLS拒否・タイムアウト |
| `scripts/update_monthly_adapters.py` | `google_fallback_search()` 内の `_google_search()` | Google検索。curl_cffi chrome124偽装でreCAPTCHA回避 |

---

## 注意事項

- Playwright を使う箇所（NSE スクレイピング等）は curl_cffi は不要・非対象
- `impersonate` を指定しない場合は `requests` と同等の動作（偽装なし）
- `follow_redirects` パラメータは curl_cffi では不要（デフォルトでリダイレクト追従）
