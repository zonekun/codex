# Claude Code 取り込み伝言メモ テンプレート

Codex 側の変更を Claude Code 側へ取り込んでもらうための伝言メモ。実際の伝言は `docs/codex-to-claude-handoff.md` に残す。Claude Code 側の `docs/handoff.md` は Windows -> Linux 引き継ぎ用なので、この用途では使わない。

原則としてこの粒度に留める。詳細な調査ログや長い経緯は別ファイルに分離し、伝言メモには取り込みに必要な情報と事象要約だけを書く。

## 必須の書き方

Codex と Claude Code は別の作業ツリーを持つため、相対パスだけを書いてはいけない。Claude Code が「どの実体ファイルを修正したのか」を誤読しないよう、以下を必ず明記する。

- `where_changed`: Codex 側 Git 作業ツリー、Claude Code 側ファイル直編集、Colab Notebooks 直編集など、修正場所の種別
- `repository`: Git 変更ならローカルリポジトリ絶対パス
- `git_branch`: Git 変更ならブランチ名
- `changed`: 修正した実体ファイルの絶対パス。必要なら括弧で「Codex Git作業ツリー」などを補足
- `not_changed_directly`: 混同されやすい反対側のファイルを直編集していない場合、その絶対パス

特に `scripts/...` のような相対パスだけを `changed` に書くのは禁止。相対パスを書く場合でも、直前に `repository` と `git_branch` を置き、`changed` には絶対パスまたは「`repository` からの相対パス」と明記する。

```markdown
# Claude Code 伝言メモ

- リポジトリ: `<git remote url>`
- where_changed: `<Codex 側 Git 作業ツリー / Claude Code 側ファイル直編集 / Colab Notebooks 直編集 / その他>`
- repository: `<ローカルリポジトリ絶対パス。Git変更でない場合は n/a>`
- ブランチ: `<branch>`
- コミット: `<short sha> <commit subject>`
- 取り込み対象: `origin/<branch>`
- changed:
  - `<修正した実体ファイルの絶対パス>`
- not_changed_directly:
  - `<直編集していないが混同されやすい反対側ファイルの絶対パス。なければ n/a>`

<起こった事象 1 行目>
<原因または変更理由 1 行>
<今回の対応または期待挙動 1 行>
```

## 例

```markdown
# Claude Code 伝言メモ

- リポジトリ: `https://github.com/zonekun/codex.git`
- where_changed: `Codex 側 Git 作業ツリー`
- repository: `C:\Users\zonekun\Documents\codex\investment-agent`
- ブランチ: `codex/integration`
- コミット: `c7e9a95 Treat empty stock price fetch as failure`
- 取り込み対象: `origin/codex/integration`
- changed:
  - `C:\Users\zonekun\Documents\codex\investment-agent\scripts\stock_price_load.py`（Codex Git作業ツリー）
- not_changed_directly:
  - `G:\マイドライブ\claude\investment-agent\scripts\stock_price_load.py`

`stock-price-load-2p7nd` は Cloud Run Job として成功扱いだったが、yfinance から 2026-04-20 の株価データを取得できず、GCS / BigQuery / Dropbox への保存が全スキップされていた。
原因は `scripts/stock_price_load.py` が `target_data.empty` を正常終了として扱っていたこと。
今回の改修で空取得時は `RuntimeError` を raise し、既存の `[STOCK_PRICE] エラー` メール送信と Cloud Run Job 失敗扱いに乗せるようにした。
```
