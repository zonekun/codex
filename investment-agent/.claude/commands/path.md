ユーザーが指定したキーワードで知見・プラン・レビューファイルのフルパスを表示せよ。以下の手順のみ:

1. `docs/knowledges/INDEX.md` を Grep（キーワード = $ARGUMENTS）
2. ヒットあり → パス部分を抽出し `G:\マイドライブ\claude\investment-agent\` を先頭に付けて表示
3. ヒットなし → `docs/plans/`、`docs/reviews/`、`docs/reports/` を Glob（パターン = `**/*$ARGUMENTS*`）で検索
4. Glob ヒットあり → フルパスを1行ずつ表示
5. すべてヒットなし → 「該当なし」と1行出力

余計な説明・確認・Readは禁止。パスだけ出す。
