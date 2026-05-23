# 103: C:\tmp フォルダ管理台帳

> **更新: エントリ追加は `printf >>` で末尾追記（Read/Edit/Write 不要）。削除は Edit で対象行を削除。**

## 概要

`C:\tmp\` に置くファイル・ディレクトリはすべてここで管理する。
**作りっぱなし放置禁止**。不要になった時点でここから削除し、実体も削除すること。

> ⚠️ **C:\tmp は一時置き場。期限列に日付を書いたエントリは `cleanup_disk.py --tmp` で自動削除される。**
> 長期保管が必要なものは `data/cache/`・`data/output/` 等プロジェクトフォルダに置くこと。

- Google Drive（`C:\gdrive\` / `G:\`）への保存禁止（容量逼迫・他端末同期の問題）
- 検証用 DL・一時キャッシュは `C:\tmp\` を使う

## 運用ルール

1. **新規追加時**: 実体を置く前に `printf >>` でエントリ末尾追記 → コミット
2. **削除時**: 実体削除後、対象行を Edit で削除 → コミット
3. **セッション開始時**: 本MDでスコープ外ファイルが増えていないか確認
4. **サイズ目安**: 合計 500MB を超えたら整理を検討する

## 詳細メモ

**tob_insider_screener/**  
JQuantsBQClient（`jquants_bq_client.py`）自動生成の parquet キャッシュ。`ohlcv_<from>_<to>.parquet` / `topix_<from>_<to>.parquet`。BQ 再取得コスト節約のため保持。

**claude-trading-skills/**  
`git clone https://github.com/tradermonty/claude-trading-skills C:/tmp/claude-trading-skills`  
`screen_vcp_jp.py` が `VCP_SKILL_DIR` 環境変数で参照。Step i-b（内製化）完了後は不要。

---

## エントリ一覧

| パス | 用途 | 参照プロジェクト | 期限(YYYY-MM-DD)/手動削除条件 |
|------|------|----------------|------------------------------|
| `C:\tmp\claude-trading-skills\` | VCP スクリーナー外部リポジトリ（VCP_SKILL_DIR） | 015 インサイダー検知 | screen_vcp_jp.py 内製化後（手動） |
| `C:\tmp\tob_insider_screener\` | BQ クエリ結果 parquet キャッシュ（300MB） | 015 インサイダー検知 | 015 Step i 完了時（手動） |
