# MD AI可読性レビュー提出: /clear後LINE会話モードのファーストトリガー欠落

## 事象

1. LINE会話モードが active: true の状態でユーザーが `/clear` を実行
2. 新セッション開始時、memory の `line_conversation_mode.md` には `active: true` が残っている
3. しかし Claude は最初の `send_ntfy_and_wait` を自発的に送信しない
4. ユーザーはスマホ側に返信先のメッセージID（3桁ID）がないため、LINE経由で Claude に指示を送れない
5. 結果、「双方向会話できない」状態に陥る

## ユーザーからの指摘

> こういうファーストトリガー無いと返信出来ない　事象をMDレビューワに報告

## 関連ファイル

- `CLAUDE.md` §ライン会話モード — 「コンテキスト縮小・リセット後の復旧義務」(L109付近)
- `docs/knowledges/tools/068_line_ntfy_push.md` §ライン会話モード (L129-182)
- `C:\Users\zonekun\.claude\projects\G---------claude\memory\line_conversation_mode.md` — 状態管理

## 分析

CLAUDE.md L109 の復旧義務:
> コンテキスト圧縮・ScheduleWakeup起動・CronCreateトリガー等の直後、**memory の `line_conversation_mode.md` を必ずチェック**。`active: true` ならモード継続。

この記述は「チェックしてモード継続」とあるが、**「モード継続」の具体的アクション（＝ファーストトリガー送信）が明記されていない**。
- 「モード継続」＝以降のレスポンスを send_ntfy_and_wait で送る、と解釈されるが
- /clear 直後は「以降のレスポンス」がまだ無い → ユーザーが画面で何か打たない限り LINE ループが再開しない
- ユーザーが画面を見ていない（外出中等）場合、完全にデッドロック

## 期待される動作

`/clear` 後のセッション開始時に memory を確認し `active: true` なら、ユーザーの最初のメッセージを待つ前に（またはユーザーの最初のメッセージ処理時に）、以下のような初回トリガーを自発的に送信すべき:

```
send_ntfy_and_wait("LINE会話モード継続中です。何か指示があれば返信してください。", timeout=10800)
```

## レビュー依頼

パターン2（誤読・ミス報告に対する改善レビュー）として、CLAUDE.md §ライン会話モード の「復旧義務」記述の AI 可読性をレビューし、ファーストトリガー送信を義務化する修正案を提示してください。

---

## AI可読性レビュー追記: 2026-05-01 19:36 JST -- md-reviewer

-> `docs/reviews/052_mr_line_mode_first_trigger_after_clear.md`
