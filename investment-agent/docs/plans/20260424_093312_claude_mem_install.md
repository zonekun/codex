# claude-mem 導入プラン（Windows・investment-agent プロジェクト）

**作成日時**: 2026-04-24 09:33 JST
**ステータス**: Step 1〜4 完了・Step 5（再起動）待ち + クラッシュリカバリフック運用OFF済
**対象**: Claude Code 用永続メモリプラグイン `thedotmack/claude-mem` の導入
**参考**: `C:\Users\zonekun\Downloads\claude-mem-windows-recommendation.md`（Windows 注意点・事前調査済み）

---

## 1. 目的

過去セッションのコンテキストを自動圧縮して次セッションに inject することで、クラッシュ再開や長期プロジェクト継続の品質を上げる。

## 2. 導入対象ツール

- リポジトリ: https://github.com/thedotmack/claude-mem
- 方式: Claude Code plugin marketplace 経由
- 主要構成物:
  - SQLite DB: `claude-mem.db`（主データ、バックアップ最優先）
  - vector-db: ベクトル検索用（最悪再生成可）
  - logs: 動作ログ
  - settings: `~/.claude-mem/settings.json`（**env var で移動不可**／仕様上の制約）
  - Plugin cache: `~/.claude/plugins/cache/thedotmack/claude-mem/`
  - Web UI: http://localhost:37777（リアルタイム閲覧）

## 3. 前提・制約

### 環境
- Node.js v24.15.0 / npm 11.12.1 / npx あり（確認済）
- Bun / uv は未確認（install 時 auto-install される）
- Git Bash / PowerShell 併用環境（Windows 10）

### 仕様上の既知制約（公式ドキュメント確認済）
- `CLAUDE_MEM_DATA_DIR` は **data dir（SQLite/logs/vector-db/worker port）のみを移動可**
- `settings.json` は **`~/.claude-mem/settings.json` 固定**（env var でも動かせない）
- `CLAUDE_MEM_DATA_DIR` は settings.json 内の key または env var で指定可
- **公式のアンインストールコマンドは無い**（手動で dir 削除 + plugin uninstall）

### Windows 運用上の配慮（Windows 注意点ドキュメント由来）
- data dir のデフォルト `C:\Users\zonekun\.claude-mem` は容量監視しづらい → `C:\tmp\claude-mem` に固定
- 複数環境／複数 WSL／複数コンテナから **同じ claude-mem dir を共有しない**
- vector-db に容量アラート（肥大化想定）

## 4. 既存メモリシステムとの関係（最重要）

本プロジェクトは既に自前の auto-memory を運用中:

- 場所: `C:\Users\zonekun\.claude\projects\G---------claude\memory\`
- 構成: `MEMORY.md`（index）+ 個別 md（feedback / project / reference 等、50 件超）
- 管理: CLAUDE.md のルールに従い Claude が手動で追加・更新
- 特性: 行動ルール・ユーザー像・プロジェクト進捗の **恒久的キュレーション**

claude-mem との差分:

| 観点 | 既存 auto-memory | claude-mem |
|---|---|---|
| 粒度 | 手動キュレーション（ルール単位） | セッション自動圧縮（履歴単位） |
| 保存先 | `.claude/projects/.../memory/` | `C:\tmp\claude-mem/*.db` |
| 検索 | index を文字列 match | skill 経由で自然言語クエリ |
| ライフサイクル | 恒久（書き換え・削除は明示） | 自動蓄積（retention 要確認） |

**方針**: **併存運用（コンフリクトさせない）**
- 既存 auto-memory は CLAUDE.md が定義する行動ルール層として維持
- claude-mem は「過去セッションで何をしたか」の履歴層として追加
- CLAUDE.md への書き込み禁止事項（調査結果・再発防止策）は変えない → claude-mem が副次的に履歴を持っても、権威的な知見ファイルは `docs/knowledges/` のまま

**確認ポイント**（導入後）:
1. claude-mem の auto-inject が CLAUDE.md の「起動時メニュー」「クラッシュ再開モード」と競合しないか
2. auto-inject された内容が context を過剰消費していないか
3. 圧縮後の履歴が既存 memory と矛盾した場合の扱い（claude-mem 側を削除・既存側を正とする）

## 5. 導入手順

### Step 1: data dir 準備（PowerShell）

```powershell
$dir = "C:\tmp\claude-mem"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
```

### Step 2: 環境変数の永続設定（PowerShell・User スコープ）

```powershell
[Environment]::SetEnvironmentVariable("CLAUDE_MEM_DATA_DIR", "C:\tmp\claude-mem", "User")
```

- 既存 PowerShell/Git Bash セッションには反映されない → 新規シェルで確認
- Git Bash で確認: `echo "$CLAUDE_MEM_DATA_DIR"`

### Step 3: インストール（plugin marketplace 方式を採用）

npx 一発方式と plugin 方式があるが、**plugin 方式**を選択。理由:
- アンインストール時に `/plugin uninstall` が使える
- Claude Code の plugin 管理系に統一される

Claude Code 内で実行:

```
/plugin marketplace add thedotmack/claude-mem
/plugin install claude-mem
```

### Step 4: settings.json に data dir を明示（env var の二重化）

`~/.claude-mem/settings.json` を開いて以下を追加（ファイル自体は初回起動時に auto-create されるため、Step 5 の後）:

```json
{
  "CLAUDE_MEM_DATA_DIR": "C:\\tmp\\claude-mem"
}
```

- env var が効かない経路（子プロセス起動時の env 継承失敗等）の保険
- Windows のバックスラッシュは JSON で `\\` エスケープ

### Step 5: Claude Code 再起動

- 新規ターミナル → `claude` 起動
- 起動時に claude-mem の自動 inject が走るかどうか確認

### Step 6: 初期動作確認

- Web UI: http://localhost:37777 が開けるか
- `C:\tmp\claude-mem\claude-mem.db` が生成されたか
- `~/.claude-mem/settings.json` が意図通りか
- Plugin cache: `~/.claude/plugins/cache/thedotmack/claude-mem/` の存在

## 6. 検証手順

### Phase 1: 単一セッション内
1. 簡単な調査タスクを 1 本実行（例: 特定 knowledge md の中身確認）
2. セッション終了時に claude-mem.db のサイズ変化を確認
3. Web UI で履歴が記録されているか確認

### Phase 2: セッション跨ぎ
1. 新規セッション起動
2. 前セッションの内容が context に inject されるか観察
3. inject 分の token 消費量が許容範囲か判断

### Phase 3: 既存機能との共存確認
1. CLAUDE.md の起動時メニュー表示が崩れないか
2. `scripts/list_claude_sessions.py` ベースのクラッシュ再開モードと競合しないか
3. `C:\tmp\claude_logs\<session_id>\` の既存ログ保管と併存できるか

## 7. 運用ルール（恒久）

### 容量監視（PowerShell で随時）

```powershell
Get-ChildItem C:\tmp\claude-mem -Force |
  Select-Object FullName,
    @{Name="SizeMB";Expression={ "{0:N1}" -f (($_ | Get-ChildItem -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB) }}
```

### 監視対象 4 箇所

| パス | 用途 | 肥大化リスク |
|---|---|---|
| `C:\tmp\claude-mem\claude-mem.db` | メインDB | 中 |
| `C:\tmp\claude-mem\vector-db\` | ベクトル検索 | **高**（アラート対象） |
| `C:\tmp\claude-mem\logs\` | ログ | 中 |
| `~/.claude/plugins/cache/thedotmack/claude-mem/` | plugin cache | 低 |

### バックアップ
- 最優先: `C:\tmp\claude-mem\claude-mem.db`
- vector-db は再生成可能前提
- 方式は未定（`sync_push.sh` に組み込むかは要判断／基本 `C:\tmp` は同期対象外なのでローカル保管のみでも可）

### 共有禁止
- 複数端末で同じ data dir を共有しない（Google Drive／GCS にも置かない）
- WSL／コンテナ間共有禁止

## 8. ロールバック手順

1. Claude Code 内: `/plugin uninstall claude-mem`
2. `/plugin marketplace remove thedotmack/claude-mem`
3. `~/.claude-mem/` 削除
4. `C:\tmp\claude-mem\` 削除
5. `~/.claude/plugins/cache/thedotmack/claude-mem/` 削除
6. 環境変数削除:
   ```powershell
   [Environment]::SetEnvironmentVariable("CLAUDE_MEM_DATA_DIR", $null, "User")
   ```
7. Claude Code 再起動

## 9. 未解決事項・要判断

| 項目 | 内容 | 判断タイミング |
|---|---|---|
| vector-db retention | 古い embedding を自動削除する設定があるか未確認 | 初回動作後 settings.json で確認 |
| inject 量の制御 | auto-inject される token 数の上限設定があるか | 初回動作で実測してから |
| 既存 MEMORY.md との整合 | 両システムの内容が矛盾した場合のルール | 3 セッション運用後に判断 |
| sync_push.sh 組込み | claude-mem.db を端末間同期するか | 併用開始後 1 週間で判断（原則ローカル限定推奨） |
| CLAUDE.md への追記 | 運用ルールをプロジェクト knowledge に昇格するか | 1 か月運用後 |

## 10. 導入判断の観点（承認前チェック）

- [ ] 既存 auto-memory を壊す副作用が無いこと（settings.json のパスは別）
- [ ] `C:\tmp` に 1GB 以上の空きがあること（DB + vector-db 想定）
- [ ] ロールバック手順が明確であること（本プラン §8）
- [ ] 複数端末運用で data dir 共有の事故が起きない導線になっていること（`C:\tmp` は GCS 同期対象外 ✓）
