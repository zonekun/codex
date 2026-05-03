# Claude Code -> Codex 移行見積もりレポート

作成日: 2026-04-22
対象プロジェクト: `G:\マイドライブ\claude\investment-agent`
保存先ワークスペース: `C:\Users\zonekun\Documents\codex\investment-agent`

## 目的

Claude Code 上で運用している `investment-agent` を Codex プロジェクトへ移行するにあたり、次を概算する。

- どれぐらい時間がかかるか
- どの Codex プランが必要か
- 移行時の具体的なチェックリスト
- Plus で足りるか、Pro にすべきかの利用量目安

## 確認した事実

以下は実際に確認できた範囲の情報。

### トップレベル構成

確認できた主なディレクトリとファイル:

- `.claude`
- `config`
- `dashboard`
- `data`
- `docs`
- `functions`
- `keys`
- `reference_code`
- `scripts`
- `skills`
- `src`
- `tests`
- `.venv`
- `notebooks`
- `docker`
- `cloudbuild`
- `workflows`
- `.env`
- `.mcp.json`
- `CLAUDE.md`
- `pyproject.toml`
- `uv.lock`

### ファイル規模

- 全体ファイル数: `7,311`
- `src` 配下ファイル数: `12`
- `tests` 配下ファイル数: `51`

補足:

- 全体 7,311 ファイルには `.venv` やキャッシュ、生成物もかなり含まれる
- したがって、移行工数の本体はアプリ本体コード量そのものよりも、運用ルール、設定、MCP、スクリプト運用の置換にある

### 主な拡張子分布

上位 20 件の概況:

- `.py`: `2,119`
- `.xml`: `2,023`
- `.json`: `1,982`
- `.md`: `215`
- `.js`: `102`
- `.csv`: `62`
- `.yaml`: `58`
- そのほか `.gz`, `.pyi`, `.log`, `.sh`, フォント類など

示唆:

- 実コードに加え、データ・中間成果物・設定ファイルが多い
- 「Codex に移すべきもの」と「移さなくてよいもの」の切り分けが工数に影響する

### Python / 依存関係

`pyproject.toml` から確認できた主要点:

- Python: `>=3.12`
- パッケージマネージャ・ロック: `uv.lock`
- 主な依存:
  - `pydantic`, `pandas`, `numpy`, `scipy`, `statsmodels`, `scikit-learn`
  - `fastapi`, `streamlit`, `plotly`
  - `anthropic`
  - `google-cloud-*`, `google-genai`, `google-generativeai`
  - `playwright`, `selenium`, `undetected-chromedriver`, `webdriver-manager`
  - `yfinance`, `yt-dlp`, `youtube-transcript-api`, `jquants-api-client`
  - `mcp`

示唆:

- 依存は重め
- 単純なテキスト編集プロジェクトではなく、ブラウザ操作、GCP、データ処理、LLM 連携を含む
- Codex への移行では、コード移植よりも実行環境と認証周りの整備がボトルネックになりやすい

### Claude 専用資産

確認できた `.claude` 配下:

- `.claude/settings.local.json`
- `.claude/scheduled_tasks.lock`

示唆:

- Claude 固有のローカル設定とスケジュール関連状態が存在する
- これらはそのまま Codex にコピーして終わりではなく、意図を読み替えて再構成する必要がある

### MCP 設定

`.mcp.json` から確認できた MCP:

- `jquants-doc`
- `fred`
- `gcp`

内容上の特徴:

- `fred` は `gcloud auth print-identity-token` を利用している
- `gcp` は `C:\venvs\investment-agent\Scripts\python.exe` を利用している
- `gcp` は `C:\gdrive\claude\investment-agent\scripts\gcp_mcp_server.py` を参照している

示唆:

- パスの固定化が強い
- 認証前提もあるため、Codex 移行では MCP 定義の再作成が必要

### scripts の特徴

`scripts/` 配下には多数の運用スクリプトが存在した。傾向として次が見られる。

- ETL / 収集:
  - `download_monthly.py`
  - `tdnet_download.py`
  - `edinet_load_parallel.py`
  - `monthly_data_load.py`
- 解析 / 加工:
  - `extract_monthly_data.py`
  - `update_monthly_adapters.py`
  - `fetch_shareholder_composition.py`
- 監視 / 回復:
  - `monitor_backfill.py`
  - `recover_backfill_batches.py`
  - `check_jobs.py`
- 一時調査 / 修復:
  - `investigate_*`
  - `fix_*`
  - `redesign_*`
- Claude 補助:
  - `claude_logger.py`
  - `list_claude_sessions.py`

示唆:

- このプロジェクトは「単一アプリ」より「運用スクリプト群」の性格が強い
- Codex 移行では IDE 的な使い方だけでなく、日常運用の導線を再整理する必要がある

## 移行工数の概算

## 結論

- 最短移行: `6〜10時間`
- 安全移行: `10〜16時間`
- 運用再設計込み: `16〜32時間`

営業日換算の目安:

- まず動かすだけ: `0.75〜2営業日`
- 定期実行や運用再設計まで含む: `2〜4営業日`

## 工数の内訳

### 1. Codex 初期設定・ログイン・ワークスペース接続

- 目安: `0.5〜1時間`

作業:

- Codex アプリまたは CLI へログイン
- 対象ワークスペースを開く
- Git / ローカルパス / 基本的な実行確認

### 2. Claude 専用資産の棚卸しと置換設計

- 目安: `2〜4時間`

作業:

- `.claude/settings.local.json` の意味を読む
- `CLAUDE.md` から実運用に必要なルールを抽出
- Claude 固有機能を Codex でどう置換するか整理

### 3. コマンド運用・スクリプト・MCP の調整

- 目安: `2〜5時間`

作業:

- `.mcp.json` の再設定
- パス固定部分の修正
- Python 実行パス、環境変数、認証の再接続
- 主要スクリプトの起動導線作成

### 4. テスト実行と動作確認

- 目安: `1.5〜4時間`

作業:

- `pytest` 実行
- smoke テスト
- 主要スクリプトの起動確認
- 既存不具合と移行不具合の切り分け

### 5. 詰まり対応の予備

- 目安: `1〜2時間`

典型的な詰まり:

- GCP 認証が通らない
- Google Drive 依存パスがずれる
- Playwright / Chrome 系が動かない
- secret の配置差異
- Windows 固有の UTF-8 問題

## 移行チェックリスト

以下を順番に実施すると、抜け漏れを減らせる。

### 1. ワークスペース複製

- `G:\マイドライブ\claude\investment-agent` を Codex 用作業ディレクトリへコピーまたは Git で同期する
- `.venv`、キャッシュ、生成物は原則持ち込まない
- コード、設定、必要ドキュメント、必要最小限のデータ定義だけを対象にする

### 2. Claude 専用資産の棚卸し

- `.claude/` の全ファイルを確認する
- `settings.local.json` の用途を分類する
- `scheduled_tasks.lock` を「移行対象」ではなく「旧状態ファイル」として扱う
- `CLAUDE.md` から次を抽出する
  - 起動手順
  - よく使うコマンド
  - 禁止事項
  - 通知方法
  - スケジュール運用
  - handoff 手順

### 3. Codex 向け運用ドキュメント化

- `CLAUDE.md` をそのまま流用せず、Codex 向けの運用メモを新規作成する
- 最低限まとめる項目:
  - 起動手順
  - よく使うコマンド
  - MCP
  - secrets
  - テスト
  - 自動化
  - 運用上の注意

### 4. MCP 移植

- `.mcp.json` の各 server を Codex で利用可能か確認する
- `gcloud auth print-identity-token` 前提の server は認証確認を行う
- 固定パスを Codex 用ディレクトリに置換する
- `gcp_mcp_server.py` の実行環境を確認する

### 5. Python 実行環境の再作成

- Python `3.12` を確認
- `uv.lock` / `pyproject.toml` から環境再構築
- `playwright`, `selenium`, `undetected-chromedriver`, `pyautogui` の動作確認
- `PYTHONUTF8=1` を前提にする箇所を確認

### 6. Secrets と外部認証

- `.env` の移行
- GCP サービスアカウントや `keys/` 配下の整理
- J-Quants、Anthropic、Google 系 API の再接続
- 必要なら通知系認証も移す

### 7. 主要入口コマンドの動作確認

最初に見る候補:

- `download_monthly.py`
- `tdnet_load_parallel.py`
- `update_monthly_adapters.py`
- `extract_monthly_data.py`
- `monitor_backfill.py`

理由:

- 主要 ETL、加工、監視が揃っており、運用の中核を代表しやすい

### 8. テスト確認

- `pytest` を実行
- `tests` の全量より先に smoke 的な通りを確認
- 不通時は「移行影響」か「既存不安定」かを切り分ける

### 9. スケジュール / 自動化の再設計

- Claude の scheduled task 的な運用をどう代替するか決める
- 候補:
  - Codex Automations
  - OS タスクスケジューラ
  - GCP 側ジョブ
  - GitHub Actions

### 10. 受け入れ条件

- 主要 5 コマンドが動く
- 主要 MCP が接続できる
- テストが主要系で通る
- Claude 固有知識なしで運用を再開できる
- 定期実行の代替手段が確定している

## 必要な Codex プラン

## 結論

- 最小必要プラン: `ChatGPT Plus`
- 推奨開始プラン: `ChatGPT Plus`
- 日常運用で重くなったら: `Pro`
- チーム運用や請求分離が必要なら: `Business`

## 2026-04-22 時点で確認した公式情報

OpenAI 公式ヘルプ / 公式ページで確認した内容:

- Codex は `Plus / Pro / Business / Enterprise(Edu)` に含まれる
- Plus は `20 USD / 月`
- Pro は `100 USD` と `200 USD` の tiers がある
- Business は標準 seat が `25 USD / 月` または `20 USD / 月 (年払い相当)` の案内
- Pro tiers の説明では、Plus より高い Codex 利用枠がある

注記:

- 価格や利用上限は将来変わる可能性がある
- 地域や通貨によって見え方が変わる場合がある

## この案件に対するプラン判断

### Plus が向いているケース

- まず移行作業を一度やり切りたい
- 使い方は 1 人
- 並列エージェントを常用しない
- まずお試ししたい

### Pro が向いているケース

- 平日ほぼ毎日 Codex を使う
- 長いコンテキストのタスクが多い
- 調査、修正、レビューを一気通貫で頻繁に回す
- 並列タスクを多用する

### Business が向いているケース

- チーム利用したい
- 管理機能や請求分離が必要
- workspace 単位で使いたい

## Plus で足りるか / Pro にすべきか

## 利用量の見積もりレンジ

このプロジェクト向けの現実的な使い方を 4 パターンに分ける。

### 1. 軽運用

- 週 2〜3 回
- 1 回 30〜60 分
- 単発修正、単発調査中心
- 月 `8〜15` セッション程度

判定:

- `Plus で十分`

### 2. 標準運用

- 平日ほぼ毎日
- 1 日 1〜2 時間
- コード修正、レビュー、テスト、MCP 調整を継続
- 月 `20〜40` セッション程度

判定:

- `Plus でも開始可能`
- ただし上限や快適性の面では `Pro 100` がかなり有利

### 3. 重運用

- 毎日 2〜4 時間
- 大きめのファイルや長い文脈を扱う
- 調査系・修復系スクリプトを頻繁に触る
- 月 `40〜80+` セッション

判定:

- `Pro 100 推奨`

### 4. 常時運用

- 複数プロジェクト並行
- 並列エージェント常用
- 監視、バックフィル、レビューなどを継続的に回す
- 実質的に「上限を気にせず使いたい」

判定:

- `Pro 200` または `Business`

## この investment-agent に当てはめた判断

このプロジェクトは次の特徴を持つ。

- `scripts` が非常に多い
- 調査・修復・監視系が多い
- MCP を使う
- GCP / 認証 / ブラウザ操作が絡む
- 運用知識の比重が高い

このため、移行初月は `Plus` で始めてもよいが、移行後に Codex を日常の開発ハブとして使う場合は `Pro 100` のほうが快適になる可能性が高い。

### 実務的な推奨

- まず 1 か月は `Plus` で開始
- 次のどれかに当てはまれば `Pro 100` に上げる
  - 週 4 日以上使う
  - 1 日に長いタスクを複数投げる
  - 並列タスクを多用する
  - 利用上限や待ちを意識するようになる

`Pro 200` は、1 人でも常時複数案件を回す段階になってからで十分。

## 費用感の概算

- 試験導入 1 か月: `Plus 20 USD`
- 本格運用 1 か月: `Pro 100 USD`
- ヘビーユース 1 か月: `Pro 200 USD`

チーム運用:

- `Business 25 USD / 月 / 席`
- または `20 USD / 月 / 席` の年払い相当

## 最終結論

### 時間

- まず動かすだけなら `6〜16時間`
- 安全側では `1〜2営業日`
- 運用再設計込みなら `2〜4営業日`

### プラン

- 最小必要: `Plus`
- 実務上の推奨開始: `Plus`
- 日常的に使い倒すなら: `Pro 100`
- チーム化や常時重運用なら: `Pro 200` または `Business`

### ひとことで言うと

この案件は「コード移植」より「運用知識の移植」が本体。したがって、最初は Plus で始められるが、移行後に本格活用するなら Pro 側へ上がる可能性が高い。

## 参考にした公式情報

- [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-codex-in-chatgpt)
- [What is ChatGPT Plus?](https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus-)
- [About ChatGPT Pro tiers](https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers)
- [What is ChatGPT Business?](https://help.openai.com/en/articles/8792828)
