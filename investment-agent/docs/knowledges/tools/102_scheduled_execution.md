# 102: 時間指定ローカル実行 — スケジュール手段選択

## 注意: `/schedule` はローカル実行に使えない

`/schedule` スキルは内部で RemoteTrigger（クラウド隔離環境）を使う。ローカルで時間指定したい用途には合わない。

## 手段比較

| 手段 | 100%トークン到達後に発火するか | ローカルリソース使用 | 用途 |
|------|-------------------------------|---------------------|------|
| CronCreate | **しない**（REPL停止で消滅） | ○ | 通常の時間指定実行 |
| RemoteTrigger（/schedule） | する | **✗**（クラウド隔離） | ローカル不要なエージェント限定 |
| Windows タスクスケジューラ | **する**（OS レベル） | ○ | 100%到達見込み時の第二選択 |

## Windows タスクスケジューラ 原則

**すべきこと**
- 通常は `-WindowStyle Hidden` で PowerShell を起動する（Visible 指定は Not Responding セッションの GDI ブロックを受けるリスクがある）
- **コンソールを表示したい場合**: `cmd.exe /c start "title" powershell.exe -ExecutionPolicy Bypass -File <path>` を `/TR` に指定する。`powershell.exe -WindowStyle Normal` を直接指定しても Task Scheduler はデスクトップと関連付けないため窓が出ない。`cmd /c start` 経由のみ有効。なお `start` は非同期起動のためタスクの `Last Result` は PS1 の終了コードを反映しない
- ランチャーは PS1 スクリプトファイルに分離し `/F` に文字列を直書きしない
- **wt.exe（Windows Terminal）で可視化したい場合**: `/TR` に `wt.exe` を指定する場合は **フルパス必須**（`C:\Users\zonekun\AppData\Local\Microsoft\WindowsApps\wt.exe`）。相対パスは schtasks から見つからず exit=0x80070002 になる。サブ窓（tail等）を追加起動するときは引数を1文字列で渡す: `Start-Process $wt -ArgumentList "new-tab powershell.exe -NoExit -ExecutionPolicy Bypass -File <path>"`。配列渡し `@("powershell.exe","-File",...)` は wt.exe に正しく伝わらない（MR-220）
- **PS1 ファイルは BOM 付き UTF-8 で保存する**。PowerShell 5.1 は BOM なし UTF-8 を Shift-JIS として読むため、日本語コメントを含む PS1 が parse エラーになる。Write ツールで生成した場合は `Out-File -Encoding utf8 -FilePath <path>` で上書きすること（MR-215）
- 登録後は `schtasks /query` で `Status: Ready` と `Next Run Time` を確認する
- `/sc ONCE` 以外のタスクは `schtasks /run` でテスト起動し `Last Result: 0` を確認する（`/sc ONCE` タスクは本番実行になるため不可）
- ログオンセッションが存在する前提で設計する（`Logon Mode: Interactive only` により、ログオフ状態では発火しない）

**すべからずこと**
- `-WindowStyle Normal` / `-NoExit` を指定しない（GDI 依存・Not Responding ブロックのリスク）
- ランチャー PS1 に claude セッション特有の処理を埋め込まない（再利用性を損なう）
- セッションが 100% 到達後に回収が必要なデータをセッション内メモリにのみ保持しない（終了前にファイル書き出し）

## CronCreate との使い分け判断

- **CronCreate を使う**: タスク実行中にトークン 100% 到達しないことが確実、かつ処理が軽量
- **タスクスケジューラを使う**: 長時間実行でトークン 100% 到達が見込まれる、またはセッションがフリーズしても確実に発火させたい

## 注意事項

- schtasks の `/SD` 日付フォーマットはシステムロケールの「短い日付形式」に依存する（日本語 Windows 標準は `yyyy/MM/dd`）
- タスクスケジューラはセッション外プロセスとして起動するため、claude のコンテキストは引き継がれない。プロンプトは自己完結した内容にすること
- 一回限りタスクは `/sc ONCE` で登録し、実行後は `schtasks /delete` で削除する
