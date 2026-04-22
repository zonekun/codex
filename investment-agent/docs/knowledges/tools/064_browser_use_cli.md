# browser-use CLI 2.0

**カテゴリ**: tools
**作成日**: 2026-04-04
**ステータス**: 有効
**関連ファイル**: なし

## 概要

ヘッドレスブラウザ操作CLI。JSレンダリングページや画像内テキスト確認など、WebFetch では取得できないコンテンツを取得する。

## 使い方

```bash
# ページを開く
PYTHONUTF8=1 uv run browser-use open <URL>

# テキスト構造を取得
PYTHONUTF8=1 uv run browser-use state

# スクリーンショット保存（保存先は /c/tmp/）
PYTHONUTF8=1 uv run browser-use screenshot /c/tmp/screenshot.png

# スクロール
PYTHONUTF8=1 uv run browser-use scroll down
```

## 注意事項

- スクリーンショットは `/c/tmp/` に保存（Google Drive 禁止）
- WebFetch で不十分だった場合のフォールバックとして使う（最初から使うのではない）
- note.com 等の JS レンダリングページで有効性を確認済み
