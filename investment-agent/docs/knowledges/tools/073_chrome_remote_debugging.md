# Chrome リモートデバッグ（CDP）によるブラウザ操作追跡

**カテゴリ**: tools
**作成日**: 2026-04-12
**ステータス**: 有効

## 概要

ユーザーが手動で操作する Chrome ブラウザの画面内容を、Claude Code 側から **スクリーンショット不要・ウィンドウ前面化不要** で読み取る手法。

Chrome DevTools Protocol (CDP) 経由で URL・DOM・リンク一覧・テキストをリアルタイム取得する。月次開示の IR ページ構造調査など、ユーザーが「ここを見て」と操作する場面で使用。

## 起動手順

### 1. 既存 Chrome を全プロセス終了

`--remote-debugging-port` は Chrome の**最初のプロセス**でしか有効にならない。既存 Chrome が残っていると新規ウィンドウが既存プロセスに合流し、デバッグポートが開かない。

```bash
# Git Bash では /F を //F と書く（/ がパスと解釈されるため）
taskkill //F //IM chrome.exe
```

### 2. `--user-data-dir` 付きで Chrome を起動

デフォルトプロファイルでは `--remote-debugging-port` が拒否される。**専用のデータディレクトリを指定する必要がある**。

```bash
"C:/Program Files/Google/Chrome/Application/chrome.exe" \
  --remote-debugging-port=9222 \
  --user-data-dir="C:/tmp/chrome_debug_profile" &
```

> **注意**: `--user-data-dir` を指定すると通常のプロファイル（ブックマーク・ログイン情報等）は引き継がれない。デバッグ専用の新規プロファイルが作成される。

### 3. 起動確認

```bash
# 数秒待ってからタブ一覧を取得
sleep 4
python -c "
import requests
tabs = requests.get('http://localhost:9222/json').json()
for i, t in enumerate(tabs):
    print(f'{i}: {t[\"title\"]}  {t[\"url\"][:100]}')
"
```

成功すると `DevTools listening on ws://127.0.0.1:9222/devtools/browser/...` がコンソールに表示される。

### よくあるエラーと対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `ConnectionRefusedError: [WinError 10061]` | Chrome が起動していない or ポートが開いていない | 既存Chrome全終了 → `--user-data-dir` 付きで再起動 |
| `DevTools remote debugging requires a non-default data directory` | `--user-data-dir` 未指定 | `--user-data-dir="C:/tmp/chrome_debug_profile"` を追加 |
| ポートに接続できるがタブが見えない | 既存 Chrome プロセスに合流した | `taskkill //F //IM chrome.exe` で全終了してから再起動 |

### 4. Python から CDP 接続してページ内容を取得

```python
import requests
import json

# アクティブタブの情報を取得
tabs = requests.get("http://localhost:9222/json").json()
for tab in tabs:
    print(f"title={tab['title']}  url={tab['url']}")

# WebSocket で DOM 取得
import websockets, asyncio

async def get_page_content():
    ws_url = tabs[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
            "params": {"expression": "document.documentElement.outerHTML"}}))
        result = json.loads(await ws.recv())
        html = result["result"]["result"]["value"]
        return html

html = asyncio.run(get_page_content())
```

### 3. 簡易ヘルパー（推奨パターン）

```python
import requests
from bs4 import BeautifulSoup

def cdp_get_tabs():
    """CDP 接続済み Chrome のタブ一覧を取得する。"""
    return requests.get("http://localhost:9222/json").json()

def cdp_get_html(tab_index=0):
    """指定タブの HTML を CDP 経由で取得する。"""
    import websockets, asyncio, json
    tabs = cdp_get_tabs()
    ws_url = tabs[tab_index]["webSocketDebuggerUrl"]
    async def _get():
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"}}))
            r = json.loads(await ws.recv())
            return r["result"]["result"]["value"]
    return asyncio.run(_get())

def cdp_get_links(tab_index=0):
    """アクティブタブの全リンクを取得する。"""
    html = cdp_get_html(tab_index)
    soup = BeautifulSoup(html, "html.parser")
    return [(a.get_text(" ", strip=True)[:80], a["href"])
            for a in soup.find_all("a", href=True)]
```

## スクリーンショット方式との比較

| 項目 | CDP 方式 | スクリーンショット方式 |
|------|----------|---------------------|
| ウィンドウ前面化 | **不要** | 必要（他ウィンドウが被ると見えない） |
| テキスト取得 | **構造化 HTML/DOM** | 画像認識（不正確） |
| リンク URL | **直接取得可能** | 取得不可（テキストのみ） |
| PDF リンク抽出 | **正確** | 不可能 |
| セットアップ | Chrome 再起動が必要 | なし |
| ユーザー操作の邪魔 | **なし** | コンソール最小化が必要 |

## 注意事項

- `--remote-debugging-port=9222` は Chrome 起動時にのみ設定可能（起動中の Chrome に後付け不可）
- ユーザーに一度 Chrome を閉じてもらう必要がある
- ポート 9222 はローカル限定（外部からのアクセスはデフォルトでブロック）
- `websockets` パッケージが必要: `uv add websockets`

## 適用場面

- ユーザーが IR ページを手動ナビゲートして月次開示の構造を調査する場面
- SPA/JS レンダリングページでリンク構造を把握する場面
- WAF でスクレイパーがブロックされるサイトをユーザーのセッションで閲覧する場面
