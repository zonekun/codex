# Claude Code 取り込み伝言メモ テンプレート

Codex 側の変更を Claude Code 側へ取り込んでもらうための伝言メモ。`docs/handoff.md` は Claude Code 内の Windows -> Linux 引き継ぎ用なので、この用途では使わない。

原則としてこの粒度に留める。詳細な調査ログや長い経緯は別ファイルに分離し、伝言メモには取り込みに必要な情報と事象要約だけを書く。

```markdown
# Claude Code 伝言メモ

- リポジトリ: `<git remote url>`
- ブランチ: `<branch>`
- コミット: `<short sha> <commit subject>`
- 取り込み対象: `origin/<branch>`

<起こった事象 1 行目>
<原因または変更理由 1 行>
<今回の対応または期待挙動 1 行>
```

## 例

```markdown
# Claude Code 伝言メモ

- リポジトリ: `https://github.com/zonekun/codex.git`
- ブランチ: `codex/integration`
- コミット: `c7e9a95 Treat empty stock price fetch as failure`
- 取り込み対象: `origin/codex/integration`

`stock-price-load-2p7nd` は Cloud Run Job として成功扱いだったが、yfinance から 2026-04-20 の株価データを取得できず、GCS / BigQuery / Dropbox への保存が全スキップされていた。
原因は `scripts/stock_price_load.py` が `target_data.empty` を正常終了として扱っていたこと。
今回の改修で空取得時は `RuntimeError` を raise し、既存の `[STOCK_PRICE] エラー` メール送信と Cloud Run Job 失敗扱いに乗せるようにした。
```
