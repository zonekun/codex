# CR-182: レビューフック統合 + レビュー記録追記方式改定

## レビュー対象ファイルパス

- `scripts/review_agent_gate.py` — UserPromptSubmitフック（リマインダメッセージ変更）
- `skills/code-reviewer.md` — 004-1追記手段セクション（Read+Edit → Bash printf >>）
- `skills/md-reviewer.md` — 同上
- `docs/knowledges/tools/097_review_submission_guide.md` — §2返却方式（インライン編集 → 末尾追記）、§3苦情申し立て
- `.claude/settings.local.json` — review_submit_hook PostToolUseフック削除

## レビューパターン

パターン 1（コードレビュー）

## 事象・背景

1. レビューフックが2つ（review_agent_gate.py + review_submit_hook.py）あったが、#2は#1存在下でデッドコードだったため統合
2. #1のリマインダが「Agent起動せよ」のみで、レビューMD作成ステップが飛ばされる事故が発生 → 「①MD作成→②Agent起動」に修正
3. レビュー記録（004-1追記、返却記入）で毎回Read+Editしてトークンを浪費 → Bash printf >> による追記専用に改定

## 補足情報

- review_submit_hook.py は削除済み（git rm）
- Windowsフレンドリーか否かも含めて包括的にレビューしてほしい（Bash printf >> がWindows Git Bash環境で問題なく動作するか）
- 実行環境: Windows 10 + Git Bash、パスはフォワードスラッシュ

---

# コードレビュー: レビューフック統合 + 追記方式改定

- 日時: 2026-05-15 22:10 JST
- 対象: commits 3280d8f, f3cc6a3（計5ファイル）
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: レビューフックを2本から1本に統合し、リマインダに「MD作成→Agent起動」のアトミック手順を明記。併せて004-1蓄積ログ・097返却セクションの記入方式をRead+EditからBash `printf >>` 末尾追記に統一した
- 品質評価: **B** — 設計意図は的確で実装も簡潔だが、004-1ファイル本体の追記手段説明が旧方式のまま更新されておらず、3箇所の記述が矛盾している
- 主要リスク:
  1. 004-1自体の「追記手段」説明（L16）が旧Read+Edit方式のまま — スキルMDと矛盾
  2. 004-1の「追記位置ルール」（L36-40）が日付降順を規定するが、`printf >>` は末尾追記で昇順になる — 順序規約の矛盾
  3. `printf` のシングルクォート内に日本語を含む場合のGit Bash環境でのエンコーディングリスク

## 【重大な指摘】（即修正）

### #1 004-1「追記手段」説明が旧方式のまま（3箇所矛盾）

- 箇所: `docs/knowledges/tools/004-1_code_review_findings_log.md:16`
- 事象: 004-1 L16に「`Read offset=113 limit=20` で蓄積エントリ先頭を部分読み → `Edit` で最新日付見出し直下に 1 行挿入」と記載されている。一方、`skills/code-reviewer.md:408-411` と `skills/md-reviewer.md:393-398` は `printf >>` 末尾追記に変更済み。004-1本体の指示とスキルMDの指示が矛盾しており、レビュワーが004-1を先に読んだ場合に旧方式で実行する
- トリガー: 次回のcode-reviewer/md-reviewer起動時
- 影響: レビュワーが004-1のL16に従って旧方式（部分Read→Edit）を実行する可能性がある。トークン節約の意図が損なわれる
- 根拠: 004-1 L16の文面と、code-reviewer.md L408-414の文面を比較すると明らかに不整合
- 推奨対応: **[検証済み]** 004-1 L16を `printf >>` 方式に書き換える。以下のような文面に:「**追記手段**: Bash `printf >>` で末尾追記。Read/Edit 不要（トークン効率化）。苦情受付（既存行削除）時のみ Read + Edit 許可。」

### #2 004-1「追記位置ルール」と `printf >>` 末尾追記の順序矛盾

- 箇所: `docs/knowledges/tools/004-1_code_review_findings_log.md:36-40`
- 事象: L36-38に「日付**降順**で `### YYYY-MM-DD` 見出しを配置」「同日のエントリは同一見出し配下に追記（既存見出しがあれば再作成しない）」と規定されている。しかし `printf >>` はファイル末尾に追記するため、新しい日付が最下部に来る（**昇順**）。さらに「同日見出しがあれば再作成しない」に対し、code-reviewer.md L412は「同日エントリが既にあるか不明でも、日付見出しごと追記して構わない（重複見出しは許容）」と明記しており、ルールが正反対になっている
- トリガー: 次回のレビュー実行時
- 影響: 004-1の蓄積エントリの日付順序が混在する。現在のデータ（L118-149）は降順だが、今後の `printf >>` 追記分は末尾に昇順で積まれる。月次メンテでの整形が必要になる。また「重複見出しは許容」は004-1側の「再作成しない」と矛盾
- 根拠: 004-1 L36-40 vs code-reviewer.md L412の文面比較
- 推奨対応: **[方向性]** 二つの方向がある: (A) 004-1の追記位置ルールを「末尾追記・昇順・重複見出し許容」に変更してスキルMDと一致させる (B) スキルMDの `printf >>` を維持しつつ、004-1の位置ルールを「月次メンテで降順に整形」と追記する。どちらを選ぶかは提出元に委ねる

## 【改善提案】（可読性・保守性）

### #1 review_agent_gate.py: except 節が広すぎる

- 箇所: `scripts/review_agent_gate.py:25-26`
- 現状: `except Exception: return` で全例外を握り潰している。json.JSONDecodeError のみ想定される箇所で ValueError や TypeError 等の予期しないバグも隠れる
- 提案: `except (json.JSONDecodeError, KeyError):` に限定する。ただしhookスクリプトという性質上、クラッシュ時にClaude Codeの動作を阻害しないことが最優先であり、現状でも実害は低い。改善の優先度は低い

### #2 097ガイド §3-1 の printf 例にハードコード日付

- 箇所: `docs/knowledges/tools/097_review_submission_guide.md:97`
- 現状: `printf '\n---\n\n## 返却 2026-05-15\n\n...'` と日付が `2026-05-15` にハードコードされている
- 提案: §2-2（L74）では `YYYY-MM-DD` プレースホルダが使われているが、§3-1ではリテラル日付。テンプレートとしての一貫性のため `YYYY-MM-DD` に統一するか、「実行日に置換」の注記を付ける

### #3 printf の Windows Git Bash エンコーディング

- 箇所: `skills/code-reviewer.md:410`, `skills/md-reviewer.md:395`, `docs/knowledges/tools/097_review_submission_guide.md:74`
- 現状: `printf` のシングルクォート文字列内に直接日本語テキスト（タグ名・説明文）が入る場合、Git Bash の locale 設定によっては UTF-8 ではなく別エンコーディングで書き込まれる可能性がある
- 提案: CLAUDE.md §6 で `PYTHONUTF8=1` が必須とされているが、Bashの `printf` に対するエンコーディング保証はない。実際にはGit Bash on Windowsはデフォルトで UTF-8 を使用するため、実害が出る可能性は低い。しかし万全を期すなら、テンプレートのコマンド例に `LC_ALL=C.UTF-8 printf ...` を前置するか、「Git BashのデフォルトUTF-8を前提」と明記することを検討

### #4 review_submit_hook.py の削除妥当性

- 箇所: `.claude/settings.local.json` (旧 L172-180)
- 現状: review_submit_hook.py の PostToolUse フックが削除された。このフックは `Write|Edit` で `docs/reviews/` に書き込まれたときにリマインダを出す仕組みだった
- 提案: 削除の判断は妥当。review_agent_gate.py が UserPromptSubmit で「レビュー」キーワード検知時に「①MD作成→②Agent起動」をリマインドするため、PostToolUse での二重チェックは不要。むしろ PostToolUse は Write/Edit のたびに発火するため、レビューMDと関係ないファイル編集でも判定処理が走るオーバーヘッドがあった。統合は適切

## 【確認できなかった事項】

- Git Bash の `printf >>` で `>>` が既存ファイルの末尾に LF（`\n`）のみで追記するか、CRLF が混入しないかは実行環境依存。Git Bash は通常 LF を使用するが、`.gitattributes` の設定や `core.autocrlf` の状態によっては CRLF に変換される可能性がある。実機テストなしには確定不可
- `printf` の `\n` エスケープが Git Bash で正しく改行に展開されることは一般的に保証されるが、パス中の日本語を含むディレクトリ（`G:\マイドライブ\...`）からの相対パス指定時の挙動は実行しないと確認不可。ただし CLAUDE.md §6 でジャンクションパス（`C:\gdrive\claude\investment-agent`）を使用する規約があるため、実運用では問題ない可能性が高い

---

## 返却 2026-05-15

- #1: [採用] 004-1 L15/L36-40を修正済み
- #2: [採用] 004-1の追記位置ルールをprintf >>末尾追記に合わせて変更済み
- 改善提案#1: [見送り: hookスクリプトのexceptは広くて問題ない。クラッシュ回避が最優先]
- 改善提案#2: [採用] YYYY-MM-DDに統一済み
- 改善提案#3: [見送り: Git BashデフォルトUTF-8で実害低。実問題が出たら対応]
- 改善提案#4: 確認のみ（削除妥当の判断に同意）
