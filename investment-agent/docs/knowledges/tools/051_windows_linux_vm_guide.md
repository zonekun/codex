# Windows ↔ Linux VM 開発環境ガイド

**カテゴリ**: tools
**作成日**: 2026-03-21
**更新日**: 2026-04-28
**ステータス**: 有効
**関連ファイル**: `scripts/sync_push.sh`, `scripts/sync_pull.sh`, `.claude/settings.local.json`

---

## VMインベントリ

| インスタンス名 | ゾーン | マシンタイプ | スペック | 用途 | プロビジョニング |
|--------------|--------|------------|---------|------|---------------|
| `claude-dev-vm` | `us-west1-a` | `e2-micro` | 2 vCPU / 1GB | Claude Code 開発端末 | STANDARD |
| `claude-high-vm` | `us-west1-a` | `e2-small` | 2 vCPU / 2GB | ザラ場ツール + XBRL開発 | STANDARD |

共通: プロジェクト `gmailpj-357912` / OS Ubuntu 24.04 LTS / ディスク 20GB

### SSH接続

**`--tunnel-through-iap` は全SSH接続で必須。** VMに外部IPが無いため、IAP Tunnel経由でないと到達できない。

```bash
# claude-dev-vm（対話）
gcloud compute ssh zonekun@claude-dev-vm --zone=us-west1-a --tunnel-through-iap

# claude-high-vm（対話）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap

# claude-high-vm（非対話 — Windowsから1コマンド実行）
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap --command="cd ~/project/claude/investment-agent && git pull origin master"
```

**禁止:**
- `ssh user@host` での直接接続（外部IP無し・IAP未経由のため接続不可）
- `gcloud compute ssh` で `--tunnel-through-iap` を省略（接続失敗する）

### 新規VMプロビジョニング手順

```bash
# 1. VM作成
gcloud compute instances create <VM名> \
  --zone=us-west1-a \
  --machine-type=e2-small \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-standard \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --provisioning-model=STANDARD

# 2. SSH接続してセットアップ
gcloud compute ssh <VM名> --zone=us-west1-a --tunnel-through-iap

# 3. VM内で実行
sudo apt-get update && sudo apt-get install -y git
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
claude --version  # 確認
```

---

## 同期の基本方針

| ファイル種別 | 同期方法 | 方向 |
|------------|---------|------|
| コード（git管理） | git push/pull | 双方向 |
| `.env`, `keys/`, `data/logs/` | GCS経由（sync_secrets_*.sh） | 双方向 |
| `.claude/settings.local.json` | **git管理外・端末固有** | 同期しない |

> **注意**: VM には Google Drive は入っていない。Windows の `C:\gdrive\...` パスは Google Drive のローカルジャンクションだが、VM には自動同期されない。同期手段は上記の **git + GCS のみ**。

---

## Windows → Linux VM

### 送り出し側（Windows で実行）

「linuxへの同期」「linuxに反映して」等の指示は、以下の2ステップで完了する。

**Step 1: git push（コード）**

```bash
# Windows Git Bash
cd /g/マイドライブ/claude
git push origin master
```

**Step 2: secrets を GCS へプッシュ**

```bash
# Windows Git Bash
bash scripts/sync_push.sh
```

対象: `.env`, `keys/gcp-service-account.json`

**送り出し側の操作はここまでで完了。** 以降の受け取り操作はユーザーから明示的に指示された場合にのみ実行する。

---

### 受け取り側（Linux VM で実行）

以下はVM側で変更を受け取る操作。送り出し側の「linuxへの同期」指示の範囲には含まれない。

**Windowsから非対話で実行する場合（`--tunnel-through-iap` 必須）:**

```bash
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap \
  --command="cd ~/project/claude/investment-agent && git pull origin master && bash scripts/sync_pull.sh"
```

**VMにSSHログインしてから実行する場合（`--tunnel-through-iap` 必須）:**

```bash
# 先にSSHログイン
gcloud compute ssh claude-high-vm --zone=us-west1-a --tunnel-through-iap

# VM内で実行
cd ~/project/claude/investment-agent
git pull origin master
bash scripts/sync_pull.sh
```

---

## Linux VM → Windows

### コード変更を Windows に反映

```bash
# Linux VM でコミット・プッシュ
cd ~/project/claude
git add -u
git add <新規ファイル>
git commit -m "..."
git push origin master

# Windows Git Bash でプル
git pull origin master
```

### secrets を GCS へプッシュ（VM → Windows）

```bash
# Linux VM
bash ~/project/claude/investment-agent/scripts/sync_secrets_push.sh
```

Windows側は `sync_secrets_pull.sh` の Windows版（または手動 gsutil cp）で受け取る。

---

## Linux VM セットアップ（新規VM構築時）

### 1. Claude Code（ネイティブ）

```bash
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

Node.js不要。ネイティブバイナリが自前ランタイムを内蔵。自動更新あり。

### 2. 基本ツール

```bash
# uv インストール
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### 3. リポジトリ取得 & secrets 展開

```bash
claude  # ブラウザ認証
gh auth login  # GitHub CLI 認証
gh repo clone zonekun/claude ~/project/claude
cd ~/project/claude/investment-agent
bash scripts/sync_pull.sh
```

### 4. 依存関係インストール

```bash
uv sync
uv run playwright install chromium --with-deps  # playwright 使用スクリプト用
```

### 5. MCP設定追加（後述）

`.claude/settings.local.json` に `mcpServers` を追記する。

---

## Linux VM のパス一覧

| ツール | パス |
|--------|------|
| python3 | `/usr/bin/python3` |
| uv | `~/.local/bin/uv` |
| uvx | `~/.local/bin/uvx` |
| gcloud | `/snap/bin/gcloud` |

### hook / MCP で PATH が通らない問題

Linux VM の hook や MCP はログインシェルを経由しないため `~/.bashrc` が読まれず uv/gcloud が見つからない。

**対策:** コマンド先頭に `PATH=$HOME/.local/bin:/snap/bin:$PATH` を前置する。

例（`settings.local.json` の hooks 設定）:
```json
"command": "PATH=$HOME/.local/bin:/snap/bin:$PATH PYTHONUTF8=1 uv run python scripts/claude_logger.py --event=tool"
```

---

## MCP サーバー設定（Linux VM 固有）

`.mcp.json` は Windows パス固定（`C:\\venvs\\...`）のため Linux VM では動作しない。
**Linux VM 用の設定は `.claude/settings.local.json`（git管理外）の `mcpServers` に追記する。**

`.mcp.json` の設定は Linux VM 起動時に接続失敗するが Claude Code 本体には影響しない（エラーログのみ）。

### 設定内容（2026-03-22 追加済み）

```json
"mcpServers": {
  "gcp": {
    "command": "/home/zonekun/.local/bin/uv",
    "args": ["run", "python", "/home/zonekun/project/claude/investment-agent/scripts/gcp_mcp_server.py"],
    "env": {
      "PYTHONUTF8": "1",
      "GOOGLE_APPLICATION_CREDENTIALS": "/home/zonekun/project/claude/investment-agent/keys/gcp-service-account.json",
      "PATH": "/home/zonekun/.local/bin:/snap/bin:/usr/local/bin:/usr/bin:/bin"
    }
  },
  "jquants-doc": {
    "command": "/bin/bash",
    "args": ["-c", "TOKEN=$(/snap/bin/gcloud auth print-identity-token 2>/dev/null); exec /home/zonekun/.local/bin/uvx mcp-remote https://jquants-mcp-480182964684.us-west1.run.app/sse --header \"Authorization: Bearer ${TOKEN}\""]
  },
  "fred": {
    "command": "/bin/bash",
    "args": ["-c", "TOKEN=$(/snap/bin/gcloud auth print-identity-token 2>/dev/null); exec /home/zonekun/.local/bin/uvx mcp-remote https://fred-mcp-480182964684.us-west1.run.app/sse --header \"Authorization: Bearer ${TOKEN}\""]
  }
}
```

### 確認方法

Claude Code セッション起動後に `/mcp` コマンドで接続状態を確認。

### VM 再構築時の注意

`settings.local.json` は git 管理外のため VM 再構築時は上記設定を手動で追記が必要。

---

## トラブルシューティング

### Settings Error: Invalid or malformed JSON

**症状:** Claude Code 起動時に `.claude/settings.local.json` が Invalid or malformed JSON エラー

**原因（2026-04-09発生）:** `settings.local.json` がgit管理下に入っていたため、Windows↔VM間のgit pull時にマージ影響で先頭の `{` が欠落した。

**修正:**
```bash
# 先頭に { が欠落していないか確認
head -1 .claude/settings.local.json

# 欠落していた場合
sed -i '1s/^/{\n/' .claude/settings.local.json

# JSON妥当性検証
python3 -c "import json; json.load(open('.claude/settings.local.json')); print('OK')"
```

**再発防止（対応済み）:**
- `.gitignore` に `.claude/settings.local.json` を追加
- `git rm --cached` でgit追跡から除外
- 端末固有ファイルは絶対にgit管理しない

---

## GCS バケットパス（secrets 同期先）

`gs://stock_data_1930932/config/investment-agent/`
