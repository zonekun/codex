# スキル: Twitter/X ツイート読み取り

## 概要
Twitter/X の URL を受け取り、ツイート本文・添付画像の内容をテキストとして出力する。chrome-devtools-mcp 経由で Chrome CDP を使用。

## 使い方
```
このツイートを読み取って: https://x.com/user/status/123456789
```

## 前提条件

### Chrome CDP 起動
Chrome が `--remote-debugging-port=9222` で起動済みであること。

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --remote-debugging-port=9222 \
  --user-data-dir="C:/Users/zonekun/AppData/Local/Google/Chrome/SeleniumProfile" &
```

- デフォルトの user-data-dir では CDP が拒否される。SeleniumProfile 等の非デフォルトディレクトリを指定する
- X にログイン済みのプロファイルを使えば認証付きコンテンツも取得可能
- chrome-headless-shell.exe が残留している場合は `Stop-Process -Name "chrome-headless-shell"` で先に片付ける

### .mcp.json 設定
```json
{
  "chrome-devtools": {
    "command": "npx",
    "args": ["-y", "chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:9222"]
  }
}
```

## 手順

### 1. 接続確認
```
mcp__chrome-devtools__list_pages → ページ一覧が返れば CDP 接続 OK
```
接続失敗時は Chrome CDP 起動手順を実行。

### 2. ページ遷移
```
mcp__chrome-devtools__navigate_page(url=<ツイートURL>, type="url", timeout=15000)
```

### 3. テキスト抽出
```javascript
mcp__chrome-devtools__evaluate_script:
() => {
  const article = document.querySelector('article[data-testid="tweet"]');
  if (!article) return { error: "Tweet not found" };
  const text = article.querySelector('[data-testid="tweetText"]');
  const user = article.querySelector('[data-testid="User-Name"]');
  const time = article.querySelector('time');
  const images = Array.from(article.querySelectorAll('img'))
    .map(img => ({ src: img.src, alt: img.alt }))
    .filter(img => !img.src.includes('profile_images') && !img.src.includes('emoji'));
  return {
    user: user ? user.innerText : null,
    text: text ? text.innerText : null,
    time: time ? time.getAttribute('datetime') : null,
    images: images
  };
}
```

### 4. 画像読み取り
添付画像がある場合、スクリーンショットで内容を読み取る:
```
mcp__chrome-devtools__evaluate_script: () => { window.scrollTo(0, 300); return "scrolled"; }
mcp__chrome-devtools__take_screenshot(filePath="C:/tmp/tweet_<id>.png")
Read(file_path="C:/tmp/tweet_<id>.png")
```
画像内容（グラフの軸・数値・テキスト等）をテキストに変換する。

### 5. 出力
以下の形式で表示する:

```
**@ユーザー名** (表示名) — YYYY-MM-DD HH:MM JST

> ツイート本文

**添付画像:**
- 画像の内容をテキストで記述（グラフなら軸・数値・傾向等）

**エンゲージメント:** RT N / いいね N / ブックマーク N / 表示 N

URL: https://x.com/...
```

- 時刻は UTC→JST 変換必須
- 画像がない場合は「添付画像」セクションを省略
- 複数画像がある場合は画像ごとにスクロール→スクリーンショット→読み取りを繰り返す

## 注意
- X にログインしていないプロファイルでは、ログイン促進モーダルでコンテンツが見えない場合がある
- 長いスレッドの場合は最初のツイートのみ取得する（リプライチェーンは対象外）
- スクリーンショットは `C:\tmp\` に一時保存し、読み取り後は削除してよい
