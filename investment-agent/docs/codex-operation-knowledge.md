# Codex 向け運用知識メモ

作成日: 2026-04-22  
対象プロジェクト: `investment-agent`  
参照元: Claude Code 側 `CLAUDE.md`

## 1. 目的

本書は、Claude Code 側で蓄積されている運用知識のうち、Codex 側でも維持すべき実務ルールを整理したものである。  
本運用の正系は当面 Claude Code 側に残すが、Codex 側でも同等の前提で調査、改修、検証、handoff を行える状態を目指す。

## 2. 基本認識

- 本プロジェクトは単一アプリというより、投資関連の分析、収集、監視、運用スクリプト群として扱う
- コード修正だけでなく、実行環境、認証、MCP、長時間ジョブ監視、handoff が運用上の中核になる
- 運用知識は `CLAUDE.md` を原典とし、Codex 側はそこから必要事項を読み替えて使う

## 3. 起動時の考え方

- Claude Code 側にはセッション開始時メニューの定義がある
- Codex 側では同じ自動メニュー表示を必須とはしない
- ただし、ユーザーが定型運用を求めた場合は、`CLAUDE.md` にある起動メニュー相当の実行候補を優先して案内または実行する
- 起動候補の例:
  - `scripts/menu_bond_update.py`
  - `scripts/menu_signal_check.py`
  - `scripts/menu_phase_analyzer.ipynb`

## 4. 実行環境ルール

- OS 前提は Windows
- Python 実行は既存運用に合わせる
- Google Drive 上に venv を置かない
- `PYTHONUTF8=1` を前提にして Python スクリプトを実行する
- ファイルを開くコードでは `encoding="utf-8"` を明示する
- Windows の文字コード差異を前提に、UTF-8 前提を崩さない
- gcloud 系操作は既存運用のクォート崩れを避けるため、実行方法に注意する

## 5. パスとフォルダ構成

- フォルダ構成は `CLAUDE.md` の定義に従う
- Claude Code 側の参照パスは、日本語パスではなく `C:\gdrive\claude\investment-agent` を優先する
- Codex 側でも、原則として Claude Code 側と同じ構成を維持する
- パスの読み替えが必要な箇所は、Codex ローカル環境に合わせて置換する
- ただし、構成自体を崩す変更は Codex 側の判断だけで行わない

## 6. 監視の定義

- 「監視する」「見張る」と返答するだけでは不十分
- 実際に監視対象を追うプロセス、ジョブ、または定期確認手段を立ち上げることを前提にする
- 長時間ジョブの監視では、終局状態だけでなく進捗メトリクスの変化も見る
- stall 判定は想定所要時間ではなく、メトリクス不変時間で行う
- 完了見込み時刻を超えたら中間状態を必ず再確認する

## 7. 時刻の扱い

- 表示時刻は JST に統一する
- ログ、API、BigQuery、gcloud、MCP 由来の時刻も JST に変換して扱う
- 表示だけでなく判断にも JST を適用する

## 8. データと一時ファイルの扱い

- ローカルダウンロード先は既存運用に従い一時領域を使う
- クラウド同期フォルダに一時ファイルを置かない
- 長時間バッチでは逐次削除を徹底し、全件分を溜めない
- ディスク空き容量を監視する
- レジューム可能な処理は、既存データを見て処理済みをスキップする

## 9. クラッシュ後の再開

- Claude Code 側には `list_claude_sessions.py` やログ参照手順がある
- Codex 側では同一の復旧機構を前提にしない
- ただし、クラッシュ再開時に必要な情報は以下を優先して残す
  - 現在のブランチ
  - 直前に触ったファイル
  - 実行したコマンド
  - 未完了タスク
  - 次の確認ポイント

## 10. 主要確認対象

- 主要スクリプト
  - `download_monthly.py`
  - `tdnet_load_parallel.py`
  - `update_monthly_adapters.py`
  - `extract_monthly_data.py`
  - `monitor_backfill.py`
- 主要設定
  - `.env`
  - `.mcp.json`
  - `keys/`
  - `pyproject.toml`
  - `uv.lock`
- 主要ドキュメント
  - `CLAUDE.md`
  - `docs/commands.md`
  - `docs/codex-to-claude-handoff.md`

## 11. Codex 側で守るべき線引き

- 本運用判断は Claude Code 側に残す
- Codex 側は改修、調査、検証、文書化を担う
- 認証、MCP、GCP、ブラウザ操作を触る場合は、既存運用との差分を明示する
- 運用ルールを変更した場合は、コード変更と同じ重みで handoff に残す
- `CLAUDE.md` のルールを無視して独自運用へ寄せない
- `docs/knowledges/` 配下は Claude Code → Codex の一方通行同期対象として扱う
- Codex 側で機能改修しても、`docs/knowledges/` を直接更新しない
- Codex 側の再発防止、作業ルール、同期上の注意は本ファイルまたは `docs/codex-parallel-operation-policy.md` に記録する
- `docs/knowledges/` の更新が必要な場合は、Claude Code 側で反映する前提のメモとしてユーザーへ伝える

## 12. Codex 側の実務優先事項

- まず既存ルールを破らずに作業できること
- 主要コマンドの起動前提を再現できること
- 文字コード、パス、認証、MCP の差異で事故を起こさないこと
- Claude Code 側が差分を取り込みやすい形で作業を残すこと
