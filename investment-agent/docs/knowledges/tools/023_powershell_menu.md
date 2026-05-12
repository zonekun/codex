# ローカル実行メニュー（PowerShell）

**カテゴリ**: tools
**作成日**: 2026-03-04
**ステータス**: 有効
**関連ファイル**:
- `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.bat`
- `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`

## プロジェクトパス（2026-03-14 Dropbox→GDrive移行後）

```powershell
$PROJECT_DIR  = "C:\gdrive\claude\investment-agent"
$SCRIPTS_DIR  = "$PROJECT_DIR\scripts"
$VENV_PYTHON  = "$PROJECT_DIR\.venv\Scripts\python.exe"
```

> **移行履歴**:
> - 2026-03-14: `C:\Users\zonekun\Dropbox\claude\investment-agent` → `G:\マイドライブ\claude\investment-agent`
> - 2026-03-15: `mklink /j "C:\gdrive" "G:\マイドライブ"` でジャンクション作成。`C:\gdrive\...`（ASCII）経由で起動することで venv Python の DLL ロード問題を解決。`$PROJECT_DIR` を `C:\gdrive\...` に更新。

## 概要

investment-agent のローカル実行スクリプトを番号メニューから起動するツール。
bat ファイルをダブルクリックするだけで PowerShell メニューが起動する。

## 起動方法

`claude-investment-agent.bat` をダブルクリック（またはコマンドプロンプトから実行）。

## メニュー項目

キーは 1-9, A, B, C… の順で自動採番（PS1 の `Get-MenuKey` / `Resolve-MenuIndex` で変換）。項目の追加・削除で後続キーがずれるため、**番号をドキュメントにハードコードしない**。正本は PS1 の `$MENU` 配列。

| 内容 | スクリプト | 引数 | 備考 |
|------|-----------|------|------|
| BB_債券履歴_new 取り込み更新 | `menu_bond_update.py` | — | |
| テールリスクシグナル 直近10日チェック | `menu_signal_check.py` | — | |
| IFISコンセンサス取得 → BQ（通常/自動再開） | `update_conse_ifis.py` | — | |
| IFISコンセンサス取得 → BQ（強制新規） | `update_conse_ifis.py` | `--fresh` | |
| QUICKコンセンサス取得 → BQ（通常/自動再開） | `update_conse_quick.py` | — | 松井証券リサーチネット経由 |
| QUICKコンセンサス取得 → BQ（強制新規） | `update_conse_quick.py` | `--fresh` | |
| 地方証券取引所銘柄 BQ更新 | `update_regional_codes.py` | — | |
| ザラ場ツール（決算リアルタイム監視） | `zaraba_earnings.py` | サブコマンド選択式 | `IsZaraba` 専用ハンドラ |
| 最新決算表示（XBRL + BQ 四半期推移） | `xbrl_lookup.py` | 銘柄コード入力 | `IsXbrlLookup` 専用ハンドラ |
| 決算未発表会社一覧 | `menu_earnings_undisclosed.py` | — | BQ予定 vs TDNet実績突合。実行時刻まで |
| 株式市場4局面判定 | `menu_phase_analyzer.ipynb` | — | JupyterLab 起動 |
| 決算反応予測（predict + 答え合わせ） | `earnings_model\predict.py` | モード選択式 | `IsPredict` 専用ハンドラ |
| 株主優待情報取得（全銘柄走査 → JSONL） | `update_yutai.py` | — | 松井証券リサーチネット経由。再開対応 |

### 専用ハンドラ一覧

PS1メニューには、対話型サブメニューが必要なスクリプト用の **専用ハンドラ**（`IsXxx` フラグ）が存在する。

#### IsZaraba — ザラ場ツール

サブコマンドと対象日を対話入力する。対象日は `YYYYMMDD` 直接入力 + ショートカット `t/p/n` に対応（Python 側 `resolve_date` で変換）。

| PS1番号 | CLI サブコマンド | 内容 |
|---------|-----------------|------|
| 1 | `prepare` | 事前準備（BQキャッシュ取得） |
| 2 | `watch` | ザラバ監視（リアルタイムスコアリング） |
| 3 | `catchup` | キャッチアップ（中断後の再開用） |
| 4 | `review` | ローカル結果表示（results.csv 時系列） |
| 5 | `upload` | 結果保存（results.csv → GCS） |
| 6 | `gcs-review` | GCS 結果表示（watch レイアウト） |
| Z | `backup` | キャッシュ保管（全キャッシュを zip スナップショット） |

#### IsPredict — 決算反応予測

モード選択と日付を対話入力する。

| PS1モード | CLI サブコマンド | 内容 |
|----------|-----------------|------|
| default → t | `today --actual-date <今日>` | 当日の予測 + 答え合わせ |
| default → range | `backfill --from <答え合わせ日> --to <答え合わせ日>` | 答え合わせ日の範囲で期間指定バッチ |
| rbld | `backfill` | 全期間リビルド |
| （PS1非公開） | `predict` | 予測のみ実行。CLI直接実行用 |
| （PS1非公開） | `accuracy` | 精度集計。CLI直接実行用 |

### 専用ハンドラ（IsXxx）同期義務

専用ハンドラ付きスクリプトのサブコマンドを追加・改修・削除した場合、**対応する全箇所を同一作業で更新**すること。1箇所だけ更新して他を忘れる事故が繰り返し発生している。

#### 対象一覧

| IsXxx | スクリプト（argparse 正本） | ラッパー | PS1 ハンドラ |
|-------|--------------------------|---------|-------------|
| `IsZaraba` | `scripts/zaraba_earnings.py` | `zara.py` | PS1 `if ($item.IsZaraba)` ブロック |
| `IsPredict` | `scripts/earnings_model/predict.py` | なし | PS1 `elseif ($item.IsPredict)` ブロック |

> 新規 `IsXxx` ハンドラを追加した場合は本テーブルと上記の専用ハンドラ一覧にも行を追加すること。

**更新対象（4箇所）**:
1. スクリプト本体（CLI argparse）
2. ラッパー（`zara.py` 等、存在する場合）
3. PS1 サブメニュー + switch ハンドラ
4. **追加する側の機能MD**（例: `066_zaraba_tool.md`）にも同じ同期義務を記載

**発火タイミング**: 上記スクリプトの argparse 定義を変更したとき（サブコマンドの追加・削除・引数変更）
**検証**: 各 IsXxx について、PS1 の分岐ケース数 = ラッパーの分岐数（ラッパーがある場合） = argparse の add_parser 数 が一致することを目視確認。一部サブコマンドを PS1 で意図的に非公開にする場合は、上記一覧に「PS1非公開」と明記する

## 構成の設計ポイント

### bat → ps1 の2層構成

```bat
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0claude-investment-agent.ps1"
```

- **ExecutionPolicy エラー回避**: ps1 を直接実行すると `UnauthorizedAccess` エラーが出る環境がある。bat 側で `-ExecutionPolicy Bypass` を渡すことで回避
- **`%~dp0`**: bat ファイルのディレクトリパスに展開される（末尾 `\` 付き）。ps1 と bat を同じフォルダに置けば相対的に参照できる

### ps1 ファイルは BOM付きUTF-8 で保存

PowerShell 5.1（Windows標準）は BOM なし UTF-8 を Shift-JIS と誤認し日本語が文字化けする。
**必ず BOM付きUTF-8（UTF-8 with BOM）で保存すること。**

```powershell
# PowerShell で BOM付き保存
$content = Get-Content ".\script.ps1" -Encoding UTF8 -Raw
[System.IO.File]::WriteAllText(
    ".\script.ps1",
    $content,
    [System.Text.UTF8Encoding]::new($true)  # $true = BOM付き
)
```

ps1 先頭にも以下を追加してコンソール出力を UTF-8 に固定する:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null
```

### Test-Path vs [System.IO.File]::Exists()

Dropbox の「オンラインのみ」ファイル（ローカルに実体なし）に対して `Test-Path` が `$false` を返すことがある。
`[System.IO.File]::Exists()` は .NET の直接呼び出しのためより信頼性が高い。

```powershell
# ❌ Dropbox オフラインファイルで誤判定することがある
if (-not (Test-Path $scriptPath)) { ... }

# ✅ より確実
if (-not ([System.IO.File]::Exists($scriptPath))) { ... }
```

### Python 検出（where.exe のみ）

`where.exe python` で PATH から動的検索するだけ。端末ごとにインストール先が異なるため、ハードコードパスや venv チェックは使わない。

> **変更履歴**:
> - 2026-03-15: `$env:LOCALAPPDATA` ハードコードパス → `where.exe python` 動的検索に変更（端末間パス差異対応）
> - 2026-03-15: venv チェック削除（システムPython で selenium/BQ 等すべて動作するため不要）

```powershell
function Find-Python {
    # where.exe で PATH から動的検索（WindowsApps ストアスタブは除外）
    $found = (where.exe python 2>$null) | ForEach-Object { $_.Trim() } |
             Where-Object { $_ -notmatch "WindowsApps" -and [System.IO.File]::Exists($_) } |
             Select-Object -First 1
    if ($found) { return $found }
    # py ランチャー経由
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $ver = & py --version 2>&1
        if ($LASTEXITCODE -eq 0) { return "py_launcher" }
    }
    return "python"
}
```

> **WindowsApps 除外の理由**: `C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe` は Microsoft Store のスタブ（実体なし）。where.exe の結果に含まれることがあるため除外する。

## 引数渡しの注意点（`@()` splatting バグ）

引数ありメニュー項目を実行する際の正しい実装:

```powershell
# ❌ NG: @($item.Args) は System.Object[] → System.String に変換され、
#        @argList splatting で文字単位に展開される
$argList = if ($item.Args -and $item.Args.Count -gt 0) { @($item.Args) } else { @() }
& $PYTHON $scriptPath @argList
# → Python が受け取るのは ['-', '-', 'f', 'r', 'e', 's', 'h']（文字単位！）

# ✅ OK: $item.Args を直接渡す（System.Object[] のまま PowerShell が展開）
if ($item.Args -and $item.Args.Count -gt 0) { & $PYTHON $scriptPath $item.Args }
else { & $PYTHON $scriptPath }
```

**原因**: PowerShell の `@(single_element_array)` は単一要素を `System.String` にアンボックスする。
文字列を `@argList` でsplat すると、PowerShell が `IEnumerable<char>` として1文字ずつ展開する。

**Args の定義も配列で**:
```powershell
Args = @("--fresh")        # ✅ 配列で定義
Args = "--fresh"           # ❌ 文字列で定義（Split で分割しても同じ問題が起きる）
```

---

## メニュー項目の追加方法

### 通常項目（引数固定・対話なし）

ps1 の `$MENU` 配列に追記するだけ:

```powershell
@{
    Label  = "表示名"
    Script = "スクリプトファイル名.py"
    Args   = ""          # 引数がある場合はスペース区切りで記入
    Note   = "補足説明"
}
```

### 専用ハンドラ付き項目（対話型サブメニューが必要な場合）

1. `$MENU` 配列に `IsXxx = $true` フラグ付きでエントリ追加
2. メインループの `if/elseif` に対話型分岐を追加（既存の `IsZaraba` / `IsPredict` ブロックを参考に）
3. 上記「専用ハンドラ（IsXxx）同期義務」セクションの対象一覧テーブルにエントリ追加
4. 本MDの専用ハンドラ一覧にサブコマンド表を追加

### 共通

追記後は BOM付きUTF-8 で再保存する（上記の保存コマンドを使うこと）。

## 根拠・出典

- 2026-03-04 セッションで実装
