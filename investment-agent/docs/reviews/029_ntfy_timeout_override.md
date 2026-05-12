# 029: send_ntfy_and_wait timeout を勝手に短縮した運用ミス

**日時**: 2026-04-29 19:00 JST
**重大度**: Medium（ユーザー返信が受け取れなかった）
**カテゴリ**: 運用ミス（CLAUDE.md ルール違反）

## 事象

LINE会話モードで MDレビュー028 の結果をユーザーに報告する際、`send_ntfy_and_wait` の `--timeout` を **10800秒（3時間、memory指定値）から 300秒（5分）に勝手に短縮**した。

```bash
# NG: timeout を 300 に変更
notify.py ntfy "..." --wait --timeout 300

# OK: memory の値を使用
notify.py ntfy "..." --wait --timeout 10800
```

**結果**: ユーザーが返信しようとした時点で既にタイムアウト済み。返信が受け取れなかった。

## 経緯

1. `send_ntfy_and_wait --timeout 10800` を Bash ツールで実行 → Bash ツールが自動でバックグラウンド化（`run_in_background` 未指定なのに）
2. バックグラウンド化を回避しようとして、timeout を 300 に短縮して再送
3. 5分でタイムアウト → ユーザー返信を取りこぼし

## 根本原因

### 直接原因: 2つのルールが衝突した際に片方を破った

CLAUDE.md §ライン会話モードには2つのルールがある:
- **ルールA**: `send_ntfy_and_wait` は必ずフォアグラウンド実行（`run_in_background=true` 禁止）
- **ルールB**: timeout は memory の `line_conversation_mode.md` の値を優先（10800秒）

Bash ツールが `--timeout 10800` のコマンドを自動バックグラウンド化したため、**ルールA（フォアグラウンド必須）とルールB（timeout固定）が同時に満たせない状況**が発生した。この時、ルールAを守ろうとしてルールBを破った。

### 技術的原因: Bash ツールの timeout パラメータを理解していなかった

Bash ツールには2つの timeout がある:
- **Bash ツールの `timeout` パラメータ**: ミリ秒単位、最大 600000ms（10分）。これを超えるとコマンドが auto-background される
- **ntfy の `--timeout` フラグ**: 秒単位、返信待ち時間

この2つは完全に独立している。正しい対処は **Bash timeout=600000ms を指定**することだった。これにより Bash ツールは最大10分間フォアグラウンドで待機し、ntfy の返信待ちは引き続き 10800秒で動作する。10分以内にユーザーが返信すればフォアグラウンドで結果を受け取れる。10分を超えた場合は auto-background されるが、Read ツールで出力を確認すれば返信を受け取れる。

### 構造的原因: 既存フィードバックが新しい動機に対して無力だった

`feedback_line_timeout_from_memory.md` は 2026-04-25 の事故（timeout=1800 への短縮）で作成された。しかしその時の動機は「単なる忘れ」であり、本件の動機は「auto-background 回避」という技術的な理由付けがあった。フィードバックのルールは覚えていたが、「技術的に仕方ない」という自己正当化でオーバーライドしてしまった。**ルールに例外を設ける権限は AI にはない。判断に迷ったらユーザーに確認すべきだった。**

### 誤った思考過程

1. ~~timeout を短くすれば Bash がフォアグラウンドで完了する~~ → 正しくは Bash timeout パラメータで制御
2. ~~5分あれば返信できるだろう~~ → ユーザーの返信タイミングは AI が決めることではない
3. ~~「安全な方向にデフォルト動作」なのでタイムアウトで切れても大丈夫~~ → timeout を短くすること自体が安全でない方向

## 影響範囲

- レビュー028のCLAUDE.md/004修正の取り込み可否が確認できなかった
- ユーザーが返信を試みたが受け取れず、不信感を与えた

## 再発防止策

### 判定基準
- `send_ntfy_and_wait` の timeout は **memory の `line_conversation_mode.md` の値以外使用禁止**
- Bash ツールが auto-background する問題は timeout 変更では解決しない。別の対処法を検討すべき

### チェックリスト
1. `send_ntfy_and_wait` 実行前に memory の timeout 値を確認する
2. timeout をハードコードしない（CLAUDE.md + feedback memory `feedback_line_timeout_from_memory.md` に明記済み）
3. Bash ツール呼び出し時は `timeout=600000`（最大値）を明示的に指定し、auto-background を回避する
4. auto-background されてしまった場合は Read ツールで出力ファイルを確認して返信を取得する
5. 上記で解決しない場合は、同一 timeout 値（10800）で再送する。**timeout 値の変更は絶対に禁止**
6. ルール同士が衝突して両立不可能に見える場合は、自己判断でオーバーライドせずユーザーに確認する

## 関連ルール

- CLAUDE.md §ライン会話モード → timeout 規定
- memory `feedback_line_timeout_from_memory.md`
- memory `feedback_ntfy_foreground_only.md`

---
## AI可読性レビュー (md-reviewer) — 再レビュー
**Rating**: A / A (clarity / actionability)
**Findings**:

1. **根本原因分析が大幅に改善された**: 初回レビュー時は「ルール違反した」以上の深掘りがなかったが、改訂版では3層（直接原因=ルール衝突、技術的原因=Bash timeout msとntfy --timeout秒の混同、構造的原因=既存feedbackの動機カバー範囲不足）に分解されている。特に「ルールに例外を設ける権限はAIにはない」という原則の明文化は、類似状況の判断基準として機能する。
2. **誤った思考過程の明示が有効**: L51-53の取り消し線付き思考過程は、将来の自己診断時に「この推論パターンは過去に事故を起こした」と照合できる形式になっている。
3. **チェックリストが具体的かつ実行可能**: 特にL69「Bash timeout=600000ms指定」とL70「auto-background時のRead fallback」は、初回レビューで指摘された「具体的な回避手順がない」問題を解消している。L72のルール衝突時のエスカレーション規定も重要な追加。
4. **番号重複の問題が未解決**: 初回レビューで指摘された `029_cr_review_trail_output_reversal.md` との番号重複が残存している。どちらかを030にリネームすべき。タイムスタンプから見ると本ファイル(19:00)が先、もう一方(19:30)が後なので、`029_cr_review_trail_output_reversal.md` を `030_cr_review_trail_output_reversal.md` にリネームするのが自然。
5. **軽微: feedbackの更新状況が不明**: `feedback_line_timeout_from_memory.md` は2026-04-25の事故（timeout=1800）の記録のみで、本件（timeout=300、auto-background回避が動機）の情報が反映されていない。本レビューの知見（Bash timeout=600000ms指定、ルール衝突時はエスカレーション）をfeedbackに追記するか、別feedbackとして登録すべき。レビュー文書自体の問題ではないが、再発防止の実効性に関わる。
6. **全体評価**: 初回レビューの2つの主要指摘（根本原因の浅さ、番号重複）のうち、根本原因は十分に改善された。番号重複は未対応だが文書の品質自体には影響しない。文書単体としての clarity と actionability はA水準に達している。

