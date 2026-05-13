---
name: サブエージェント詰まり復旧
description: Agentツールがタイムアウト/中断した場合のJONLログ調査・中間成果物サルベージ手順
type: tools
作成日: 2026-05-12
ステータス: 有効
---

## 概要

Agentツールで起動したサブエージェントが長時間応答なし・タイムアウト・ユーザー中断した場合、中間成果物がファイルに書き出されていない可能性がある。JONLログから分析内容をサルベージする手順。

## サブエージェントログの場所

```
C:\Users\zonekun\.claude\projects\<project-hash>\<session-id>\subagents\agent-<agent-id>.jsonl
C:\Users\zonekun\.claude\projects\<project-hash>\<session-id>\subagents\agent-<agent-id>.meta.json
```

## 調査手順

### 1. 該当セッションの特定

```bash
# 最近更新されたセッションディレクトリを確認
ls -lt C:/Users/zonekun/.claude/projects/G---------claude-investment-agent/ | head -10
```

### 2. サブエージェントファイルの探索

```bash
# 直近2時間以内に更新されたサブエージェントメタファイル
find "C:\Users\zonekun\.claude\projects\G---------claude-investment-agent" -path "*/subagents/*" -name "*.meta.json" -mmin -120
```

### 3. メタ情報で対象エージェント特定

meta.json に `agentType` と `description` が記録されている。Read で確認。

### 4. ログからツール呼び出し履歴を確認

```bash
# どのツールを何回呼んだか
grep -o '"name":"[^"]*"' <agent-jsonl-path> | sort | uniq -c | sort -rn
```

### 5. Write呼び出しの有無確認

Write が0件 → ファイル書き出し前に中断。分析テキストはassistantメッセージ内にある可能性。

### 6. レビュー本文等のサルベージ

```powershell
# JSONL内でレビュー関連キーワードの位置を特定
$raw = [System.IO.File]::ReadAllText("<agent-jsonl-path>")
$patterns = @("コードレビュー:", "重大な指摘", "改修プラン評価", "サマリー")
foreach ($p in $patterns) {
    $idx = $raw.IndexOf($p)
    if ($idx -gt 0) { Write-Output "Pattern '$p' at position $idx" }
}
```

テキストが見つかった場合は前後のJSON行を抽出して内容を確認。

## 注意事項

- Globのパスエスケープに注意（バックスラッシュ混在で不一致になる場合がある）。`find` コマンドのほうが確実
- エージェントがWriteを呼ぶ前に中断された場合、レビュー本文はアシスタントの思考内にのみ存在し、抽出不可能なケースもある
- 同一コンテキストで同じファイルを読んでいるなら、エージェント再起動よりメインで直接書いた方がトークン節約になる

## 2026-05-12 事例（code-reviewer 49分ハング）

- code-reviewer が約50分稼働、Read×18 / Grep×4 / Glob×2 / Write×0
- `docs/reviews/` にはファイル未作成。メインエージェントが直接レビューMDを書き出して解決

### 根本原因（2026-05-12 再調査で確定）

**前回推定（誤）**: APIレスポンス生成が48分無応答
**確定原因**: Grepツール（ripgrep）がGoogle Drive File Stream全体検索で48分ハング

- L56: Grep(`v_fin_summary_actual_for_q_on_q`, path=`G:\マイドライブ\...\investment-agent`全体)
- L57: Read(`predict.py`, 単一ファイル) — 並列ツール呼び出し
- L58 (1秒後): Read結果返却
- L59 (48分後): ユーザーがGrep拒否→中断。Grepのtool_resultは返らなかった
- 同エージェント内の単一ファイルGrep 3件は全て0.6-0.7秒で正常完了

### 原因の連鎖

1. ripgrepがGoogle Drive File Stream（仮想FS）上でディレクトリ全走査を実行
2. 大量のstat/open呼び出しがネットワークI/Oでブロック
3. Grepツールのタイムアウトが発火せず
4. Agent/Taskツールにタイムアウト機構なし（GitHub #49150 — closed as not planned）
5. オーケストレータが無期限待機

### 再発防止

- **Grep/Globは対象ファイルを特定してから呼ぶ**。プロジェクトルート全体検索は禁止。単一ファイル指定なら0.6秒、全体検索は数十分ハングのリスク
- 検索対象が不明な場合は、先にGlob等でファイルパスを特定してからGrep
- ハング検知時は早めにユーザー中断し、メインエージェントで代替実行
