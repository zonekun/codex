# コードレビュー: orders_launcher_0600.ps1 (タスクスケジューラ自動起動スクリプト)

- 日時: 2026-05-20 09:30 JST
- 対象: `C:\tmp\orders_launcher_0600.ps1` + schtasks 登録コマンド（提出 MD: `docs/reviews/214_cr_orders_launcher_0600.md`）
- パターン: 1 (新規コード品質確認)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Windows タスクスケジューラで 06:00 JST に `claude.exe` を非対話的に起動し、orders-commander を自動実行するワンショットランチャー PS1 スクリプト
- 品質評価: **C** — 最重要要件「100% トークン切れでも発火するか」に対して設計上の弱点が 2 件あり、意図通りに動作しない可能性が高い。schtasks 引数にも潜在的な問題がある
- 主要リスク:
  1. `Logon: Interactive only` + 同一セッション制約により、Claude セッションが Not Responding 状態だとタスクが発火しないか "無音で失敗" する可能性
  2. schtasks の `/SD` 日付フォーマットがロケール依存で、環境によっては `YYYY/MM/DD` が正しくパースされないリスク
  3. END ログの `$processed` 計算がマイナスになり得る（parallel=5 で index.csv が他プロセスにより書き換わる競合）

---

## 【重大な指摘】

### #1 Interactive only 制約: セッションフリーズ中の発火保証なし

- 箇所: `docs/reviews/214_cr_orders_launcher_0600.md` 登録タスク `Logon: Interactive only`
- 事象: Windows タスクスケジューラで `Logon Type: Interactive only` のタスクは、**ログオン済みのインタラクティブセッションが存在する場合にのみ**起動される。これは通常満たされているが、問題は「同一デスクトップセッションにアタッチされるか」ではなく「起動できるか」の点。タスク自体は起動される。しかし——
- トリガー: Claude セッション（ターミナルプロセス）が「Not Responding」状態（CPU 100%・メッセージキュー詰まり）のとき、**同一ユーザー名の別プロセス起動は OS レベルでは可能**。ただし、以下の間接的障害が発生し得る:
  1. `powershell.exe -WindowStyle Normal` は新しいウィンドウ（コンソールウィンドウ）を開こうとする。GDI/Win32 ウィンドウ生成は Desktop Window Manager 経由。Not Responding プロセスがデスクトップメッセージキューをブロックしている場合（稀だが発生する）、新ウィンドウ作成自体がハングすることがある
  2. `-WindowStyle Normal` を指定すると GUI ウィンドウ生成を経由するため Hidden 起動より安全性が低い
- 影響: 新 PowerShell プロセスは起動できるが、ウィンドウ生成段階でブロックされ、スクリプト本体が実行されないまま吊り下がる
- 推奨対応: **[方向性]** `-WindowStyle Normal` を `-WindowStyle Hidden` に変更する。非対話バッチ実行であれば Hidden の方が適切で、デスクトップメッセージキュー依存を排除できる。また schtasks 登録時に `/RL HIGHEST` を追加して管理者権限で起動することで、セッション制約の影響を最小化できる

### #2 schtasks `/SD` 日付フォーマットがロケール依存

- 箇所: `docs/reviews/214_cr_orders_launcher_0600.md` 登録コマンド `/SD 2026/05/20`
- 事象: schtasks.exe の `/SD` に渡す日付フォーマットは**システムロケールの「短い日付形式」に依存**する。日本語 Windows では既定値は `yyyy/MM/dd` のため `2026/05/20` は通常動作する。しかしコントロールパネルで日付形式をカスタマイズしている環境（例: `yyyy-MM-dd`）では「無効な日付フォーマット」エラーになり、タスクが登録されない（またはサイレントに登録失敗する）。
- トリガー: コントロールパネル → 地域 → 短い日付 形式が `yyyy/MM/dd` 以外に設定されている場合
- 影響: タスク登録自体が失敗している場合、06:00 に何も発火しない（`schtasks /query` で `Ready` と表示されていれば登録済み確認済みとのことなので、この環境では現時点で問題はない。ただし将来の再登録時にリスク）
- 根拠: 提出 MD に「登録済みタスク: Status = Ready」とあるため現環境では登録成功済み。ただしフォーマット依存性の知識として記録
- 推奨対応: **[方向性]** 再登録・再利用を考慮するなら、ロケール非依存の `MM/dd/YYYY` 形式（schtasks の英語 OS デフォルト形式）または `Get-Date -Format "MM/dd/yyyy"` で動的生成する方式が安全

### #3 `-p $prompt` のシングルクォートヒアドキュメントと変数展開の混在

- 箇所: `C:\tmp\orders_launcher_0600.ps1:19-29`
- 事象: `$prompt` は `@'...'@`（シングルクォートヒアドキュメント＝リテラル文字列）で定義されているため、`{ISO8601_UTC}` や `{このセッションで処理したticker数}` の `{}` がそのまま Claude へ渡るプレースホルダとなっている。これは**意図的**（Claude に判断させる）と思われるが、`data/output/token_usage_log.tsv` のパス記述もリテラルになっている点に注意。
- トリガー: Claude が `{ISO8601_UTC}` を実際の UTC 日時に置き換えずそのまま書き込むと、TSV ログが壊れる。Claude モデルによっては `{}` 形式のプレースホルダを「記述例」として認識し、そのまま書き込む場合がある
- 影響: token_usage_log.tsv の BATCH 行に `{ISO8601_UTC}` というリテラル文字列が書き込まれ、集計時にパース失敗
- 推奨対応: **[方向性]** プロンプト内でフォーマットを明確化する。例: 「ISO 8601 UTC 形式（例: 2026-05-20T06:10:00Z）」と例示を添えることで Claude の出力精度が上がる

### #4 END ログ `$processed` がマイナスになり得る

- 箇所: `C:\tmp\orders_launcher_0600.ps1:33`
- 事象: `$processed = $pendingBefore - $pendingAfter`。parallel=5 で複数タスクが同時に orders_index.csv を書き換えると、Claude セッション終了時点の `$pendingAfter` が `$pendingBefore` より**大きくなることがある**（他のプロセスが pending を追加した場合）または逆に、claude がクラッシュした場合は index.csv 未更新で `$processed=0`
- トリガー: ① 他プロセスが index.csv に pending 行を追加した場合、② claude が中途クラッシュで index.csv を更新せずに終了した場合
- 影響: END ログに負の値または 0 が記録され、トークン費消比較実験の計測値が不正確になる
- 推奨対応: **[方向性]** `$processed` が負の場合は `0` にクランプするガードを追加。また、claude の終了コードを `$exitCode = $LASTEXITCODE` で取得して END ログに追記すれば異常終了を区別できる

---

## 【改善提案】

### #1 startTime が UTC 表記なのに変数名が `$startTime`

- 箇所: `C:\tmp\orders_launcher_0600.ps1:12-13`
- 現状: `$startTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")` で UTC を取得しているが、変数名は `$startTime`（タイムゾーン不明）
- 提案: `$startTimeUtc` に改名するか、コメントで UTC 明示する。token_usage_log.tsv の TSV ヘッダー行が無いため、後から読む際に UTC であることが不明

### #2 `$indexCsv` 存在チェック欠如

- 箇所: `C:\tmp\orders_launcher_0600.ps1:11`
- 現状: `Get-Content $indexCsv` を直接呼び出し。ファイルが存在しない場合 PowerShell は `$null` を返し `Measure-Object` が 0 を返す（エラーにならない）。START ログに `pending=0` と書かれて誤認する
- 提案: 先頭に `if (-not (Test-Path $indexCsv)) { Write-Error "index CSV not found: $indexCsv"; exit 1 }` を追加

### #3 token_usage_log.tsv の出力ディレクトリ自動生成がない

- 箇所: `C:\tmp\orders_launcher_0600.ps1:6` および `13行目`
- 現状: `$logFile = "$workDir\data\output\token_usage_log.tsv"`。`data\output\` ディレクトリが存在しない場合、`Add-Content` はエラーをスローせず（`$ErrorActionPreference = "Continue"` のため）サイレントに失敗する
- 提案: `$logDir = Split-Path $logFile; if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }` を START ログの前に追加

### #4 claude.exe の終了コードを記録していない

- 箇所: `C:\tmp\orders_launcher_0600.ps1:29`
- 現状: `& "claude.exe" ... $prompt` を実行後、`$LASTEXITCODE` を取得していない。claude が exit 1 で終了しても END ログに正常終了として記録される
- 提案: `$claudeExitCode = $LASTEXITCODE` を 29 行目の直後に追加し、END ログの備考列に含める

### #5 `Select-String ",pending,"` の CSV フォーマット依存

- 箇所: `C:\tmp\orders_launcher_0600.ps1:11`
- 現状: `,pending,` という固定文字列検索で pending 行をカウント。CSV の列順・クォーティング・空白の有無によっては誤カウントになる（例: `,pending` が値の一部として含まれる場合、または `, pending,` と空白が入る場合）
- 提案: `Import-Csv $indexCsv | Where-Object { $_.status -eq "pending" } | Measure-Object` の方がロバスト。ただし CSV のヘッダー名確認が前提

---

## 【確認できなかった事項】

1. **Interactive only でのフリーズ中プロセス起動の実挙動**: Windows の Desktop Window Manager がどの程度のセッション Not Responding でメッセージキューをブロックするかは環境依存。`-WindowStyle Hidden` への変更で回避できる可能性は高いが、実際の claude.exe Not Responding 状態でのテストは実行できていない
2. **`claude.exe` の `-p` フラグの挙動**: `-p $prompt` で渡した複数行テキストが正しく単一引数として渡されるか（PowerShell の `&` 演算子 + 文字列引数の扱い）は、実行環境によって改行の扱いが異なることがある。ヒアドキュメントを `-p` に渡す実績パターンが codebase に見当たらなかったため不確認
3. **orders_index.csv の実際のカラム名・フォーマット**: `Select-String ",pending,"` の正確性評価に必要だが、このレビュー内では確認できていない
4. **`$ErrorActionPreference = "Continue"` の意図**: 意図的に設定されているが、Add-Content のサイレント失敗を許容する設計かどうか確認できていない。ログ欠落を許容するなら Continue は適切だが、ログの完全性が実験の前提なら Stop が望ましい

---

## 返却 2026-05-20

### 重大指摘
- #1: [採用] -WindowStyle Hidden に変更。タスク再登録済み
- #2: [見送り: 現環境で Ready 確認済み。再登録時の注意事項として把握]
- #3: [採用] プロンプトに具体例（2026-05-20T06:10:00Z）を追加
- #4: [採用] Math::Max(0, ...) でクランプ + $LASTEXITCODE を END ログに追記

### 改善提案
- #1: [採用] $startTimeUtc / $endTimeUtc に改名
- #2: [採用] Test-Path チェック追加
- #3: [採用] Split-Path + New-Item でディレクトリ自動生成
- #4: [採用] $claudeExitCode 取得・記録
- #5: [採用] Import-Csv | Where-Object { $_.status -eq "pending" } に変更（header: status 確認済み）
