# Windows 日本語パス × Python venv 問題

**カテゴリ**: tools
**作成日**: 2026-03-14
**ステータス**: 解決済み（2026-03-15 ジャンクション方式で対処）
**関連ファイル**: `scripts/update_conse_rakuten.py`, `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`

## 概要

本プロジェクトの venv は `G:\マイドライブ\claude\investment-agent\.venv` にある。
`マイドライブ` が日本語（非ASCII）であるため、Windows 上で Python コンパイル済み拡張（.pyd）の
DLL ロードがハングし、pandas / pyarrow / google-cloud-bigquery 等が使えない。

## 症状

| 操作 | 症状 |
|------|------|
| venv Python で `import pandas` | 無限ハング（timeout / Ctrl+C 不可） |
| venv Python で `import google.cloud.bigquery` | 無限ハング（BQ → _pandas_helpers → pandas と連鎖） |
| venv の requests で HTTPS 接続 | `SSLEOFError: UNEXPECTED_EOF_WHILE_READING`（certifi の cacert.pem も日本語パスにあるため読めない） |
| `sys.path.insert(0, venv/site-packages)` でシステムPython に venv を混入 | 同上（venv の certifi パスが引き込まれる） |
| `uv run python` | 同上（sys.path は正しく設定されるが DLL ロードのハングは変わらない） |

## なぜシステムPython は動くのか

システムPython のパッケージは `C:\Users\zonekun\AppData\Local\Programs\Python\Python312\Lib\site-packages\`
（ASCII パス）にインストールされている。DLL ロードも SSL 証明書読み込みも ASCII パスなので正常。

本プロジェクトで必要なパッケージ（selenium / PIL / google.generativeai / bs4 / google-cloud-bigquery）は
**全てシステムPython にもインストール済み**のため、venv を使わなくても動作する。

## 詳細調査結果（2026-03-14）

```
テスト環境
  システムPython: C:\Users\zonekun\AppData\Local\Programs\Python\Python312\python.exe
  venv Python:    G:\マイドライブ\claude\investment-agent\.venv\Scripts\python.exe
  urllib3:        両方とも 2.6.3（バージョン差は無関係）
  TLS接続:        ssl+socket で直接テストすると両方 TLSv1.3 で正常
```

| 実行方法 | pandas | BQ import | requests HTTPS |
|---------|--------|-----------|---------------|
| システムPython（クリーン） | ✅ | ✅ | ✅ |
| システムPython + `sys.path.insert(venv)` | ❌ ハング | ❌ ハング | ❌ SSLEOFError |
| `.venv\Scripts\python.exe` 直接 | ❌ ハング | ❌ ハング | ❌ SSLEOFError |
| `uv run --no-sync python` | ❌ ハング | ❌ ハング | `REQUESTS_CA_BUNDLE` で修正可 |
| `REQUESTS_CA_BUNDLE=C:/certifi/cacert.pem` + uv run | ❌ ハング | ❌ ハング | ✅（BQ import が先にハング） |

## 対処方針

### 解決策（2026-03-15 採用）：ジャンクション方式

`G:\マイドライブ` に対して ASCII パスのジャンクションを作成する:

```cmd
mklink /j "C:\gdrive" "G:\マイドライブ"
```

ジャンクション経由で venv Python を起動すると `sys.path` が `C:\gdrive\...`（ASCII）になり、
`.pyd` DLL ロードが正常動作する。venv の再作成・パッケージ再インストール不要。

**確認済み（2026-03-15）**:

| テスト | 結果 |
|--------|------|
| `C:\gdrive\...\python.exe` で `import pandas` | ✅ 正常 |
| `C:\gdrive\...\python.exe` で `from google.cloud import bigquery` | ✅ 正常 |

### プロジェクト全体の対応
- `$PROJECT_DIR = "C:\gdrive\claude\investment-agent"` に変更済み（PS1・CLAUDE.md）
- PowerShell メニューで venv Python を最優先に戻した
- スクリプト内の `sys.path.insert(0, venv_path)` は引き続き削除したまま（不要）

### 旧暫定運用（廃止）

~~スクリプトはシステムPython で実行する~~（ジャンクション方式で不要になった）

## 他のスクリプトへの影響

同じ venv を使う他のスクリプトでも pandas / pyarrow が必要な場合は同様にハングする。
ただし本プロジェクトのローカル実行スクリプトは全てシステムPython に依存関係があるため
現状は問題なし。

Cloud Run ジョブは venv を使わず（コンテナ内に直接インストール）、ASCII パスのため影響なし。

## 根拠・出典

- 2026-03-14 `update_conse_rakuten.py` の BQ ハング調査で判明
- 関連: `022_conse_rakuten.md`（トラブルシューティング）、`023_powershell_menu.md`（Python 検出方針）
