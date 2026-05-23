# 215 事故報告: Windows タスクスケジューラ起動失敗（エンコーディング）

## メタデータ

- **レビューパターン**: 2（事故報告）
- **提出日**: 2026-05-20
- **提出者**: メインエージェント

## レビュー対象

- `C:\tmp\orders_launcher_0600.ps1`（問題発生スクリプト）
- `docs/knowledges/tools/102_scheduled_execution.md`（新規作成した知見MD）

## 事象

2026-05-20 06:00 JST に Windows タスクスケジューラで `orders_launcher_0600.ps1` を起動する予定だったが、起動しなかった。

ユーザーが 07:26 に「たすくすけじゅーら動かない」と報告。調査した結果、スクリプトの文字コードが原因と判明した。

## 根本原因

Claude の `Write` ツールで書き出した `.ps1` ファイルが **UTF-8 BOM なし** であった。  
PowerShell 5.1 は BOM なし UTF-8 をデフォルトで Shift-JIS として読み込む。そのため、スクリプト中のヒアドキュメント終端 `'@` の前に日本語コメントの末尾バイト列が混入し、`'@` が行頭に来なくなって parse エラーとなった。  
PowerShell はエラーを無視して終了し、claude.exe は一度も起動されなかった。

## 経緯

1. 00:26 — `Write` ツールで `orders_launcher_0600.ps1` 作成（BOM なし UTF-8）
2. 00:27 — `schtasks /create` で 06:00 に登録、`Ready` 確認
3. 06:00 — タスク発火するが PS1 が parse エラーで即終了。debug.log にも何も書かれず
4. 07:26 — ユーザーが起動失敗を報告
5. 07:30 — `cat -A` でエンコーディング崩れを発見
6. 07:32 — `Out-File -Encoding utf8`（BOM 付き）で書き直し、テスト実行で `SCRIPT_START` を確認

## 影響

- orders-commander の 06:00 実行が 1 回分丸ごとスキップ
- 手動介入・デバッグで約 1 時間のロス

## 再発防止として期待すること

- Windows / PowerShell 5.1 環境で `.ps1` ファイルを `Write` ツールで作成するときのエンコーディング規約
- 知見MD `004_coding_conventions.md` または他の適切な場所への記載要否の判断
- 今回 `102_scheduled_execution.md` には記載しなかったが、追記が必要か

---

## 返却 2026-05-20

- #1: [採用] 102_scheduled_execution.md に PS1 BOM 付き UTF-8 要件 + テスト起動確認ステップを追記
- #2: [採用] 004_coding_conventions.md §シェルからのPython実行 に PS1 エンコーディング規約を追記
