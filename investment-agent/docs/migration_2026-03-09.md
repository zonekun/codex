# Dropbox → Google Drive 引っ越し記録（2026-03-09）

**目的**: 不具合発生時の調査用。ノウハウではない。

---

## 移行概要

| 項目 | 内容 |
|------|------|
| 実施日 | 2026-03-09 |
| 移行元 | `C:\Users\zonekun\Dropbox\claude\` |
| 移行先 | `G:\マイドライブ\claude\`（Google Drive）|
| 移行対象 | `investment-agent/` フォルダ一式（`.venv/` 除く、`data/` 含む） |
| 移行方法 | robocopy（PowerShell スクリプト `migrate_claude_to_gdrive.ps1`） |
| 旧フォルダ | **削除せず残存**（`C:\Users\zonekun\Dropbox\claude\investment-agent\` は現在も存在） |

---

## 移行後に変更したファイル

### 1. `.claude/settings.local.json`（フック設定）

hooks の `command` パスを書き換えた（3本）:

| Hook | 変更前 | 変更後 |
|------|--------|--------|
| UserPromptSubmit | `PYTHONUTF8=1 python /c/Users/zonekun/Dropbox/claude/investment-agent/scripts/claude_logger.py --event=prompt` | `/c/gdrive/claude/investment-agent/scripts/claude_logger.py` |
| Stop | 同上 `--event=stop` | 同左（パス変更） |
| PostToolUse | 同上 `--event=tool` | 同左（パス変更） |

> **旧フォルダの settings.local.json**: フックパスは旧パスのまま（書き換えていない）。
> 旧フォルダで claude を起動すると旧フックが動く。

### 2. `CLAUDE.md`

プロジェクトパス記述（3箇所）を更新:
- `C:\Users\zonekun\Dropbox\claude\investment-agent` → `G:\マイドライブ\claude\investment-agent`
- 起動コマンドの `cd` パスを更新

### 3. 親ディレクトリ `../.claude/settings.local.json`（`G:\マイドライブ\claude\.claude\`）

stock-skills 用 permissions パスを更新。

### 4. 知見ファイル内のパス例（参考記述のみ）

| ファイル | 変更内容 |
|---------|---------|
| `docs/knowledges/tools/017_claude_code_hooks_logger.md` | フック設定例の `cd` パスを更新 |
| `docs/knowledges/tools/005_cloudrun_job_deploy.md` | デプロイ手順の `cd` パスを更新 |
| `docs/knowledges/tools/027_jquants_mcp_server.md` | 同上 |
| `docs/knowledges/tools/028_fred_mcp_server.md` | 同上 |
| `docs/knowledges/tools/029_aws_mcp_servers.md` | 同上 |

---

## 変更していないもの（意図的に据え置き）

| ファイル / パス | 理由 |
|---------------|------|
| `scripts/menu_bond_update.py` L11: `EXCEL_PATH = r'C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx'` | Dropbox 上の Excel を読み込む処理。ファイル自体は Dropbox に残るため変更不要 |
| `scripts/convert_bond_history.py` L14: `SRC = Path(r"C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx")` | 同上 |
| `scripts/edinet_delay.py` の Dropbox API アップロード処理 | Dropbox へのアップロード先は変わらないため変更不要 |
| `data/` フォルダ以下のデータファイル | git 管理外のため robocopy でそのままコピー済み |
| `keys/` フォルダ | 同上 |
| `.env` | 同上 |

---

## 旧フォルダの現状（2026-03-09 時点）

- パス: `C:\Users\zonekun\Dropbox\claude\investment-agent\`
- git ステータス: 未コミット変更あり（新フォルダと同一内容だが settings.local.json は旧パスのまま）
- git ログ: 新フォルダと同一（同じリポジトリをコピー）
- **削除予定なし**（Dropbox 同期は継続しているが容量削減のため同期除外を検討）

---

## 移行時に発生したトラブル

### 1. PowerShell スクリプト実行ポリシーエラー
```
このシステムではスクリプトの実行が無効になっているため...
```
→ `powershell -ExecutionPolicy Bypass -File migrate_claude_to_gdrive.ps1` で回避。

### 2. PowerShell スクリプトの構文エラー
```
式またはステートメントのトークン '}' を使用できません。
```
→ スクリプトのエンコーディング問題。Claude が修正して再実行。

### 3. `Test-Path` アクセス拒否
```
Test-Path : アクセスが拒否されました。 G:\マイドライブ\claude
```
→ Google Drive の仮想フォルダに対して `Test-Path` が効かない場合がある。
スクリプト内の事前チェック処理をスキップして直接 robocopy を実行することで解決。

### 4. 最初の実行で `G:\マイドライブ\claude` を消去
移行に失敗した状態で `G:\マイドライブ\claude` 以下を一度 `rm -rf` で削除し、
PowerShell スクリプトを再実行して再コピー。

### 5. `uv sync` のハードリンク警告
```
warning: Failed to hardlink files; falling back to full copy.
```
→ キャッシュ（AppData）と `G:\マイドライブ`（Google Drive）が別ファイルシステムのため。
動作には問題なし。`UV_LINK_MODE=copy` を設定するか警告を無視してよい。

---

## 不具合調査のチェックリスト

移行後に不具合が出た場合の確認ポイント:

1. **フックが動いていない** → `.claude/settings.local.json` のフックパスが `/g/マイドライブ/...` になっているか確認
2. **スクリプトが旧パスを参照している** → `grep -r "Dropbox/claude/investment-agent" scripts/` で検索
3. **BB_債券履歴 Excel が読めない** → `C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx` は Dropbox に残存。Dropbox が同期されているか確認
4. **`uv run` が失敗する** → `G:\マイドライブ\claude\investment-agent\.venv\` が存在するか確認（robocopy 時に除外した場合は `uv sync` 要）
5. **キーファイルが見つからない** → `keys/gcp-service-account.json` は git 管理外のため robocopy でコピー済み。存在確認する
6. **MCP サーバーが動かない** → `.mcp.json` 内のパスが旧 Dropbox パスのままでないか確認

---

## 関連ファイル

- 移行スクリプト: `C:\Users\zonekun\Desktop\migrate_claude_to_gdrive.ps1`（デスクトップに残存）
- 旧フォルダ: `C:\Users\zonekun\Dropbox\claude\investment-agent\`
- 新フォルダ: `G:\マイドライブ\claude\investment-agent\`（現在の作業ディレクトリ）
