# 048_mr_codex_mirror_sync_artifact_protection

作成日時: 2026-05-01 JST

## 依頼種別

md-reviewer への報告・助言依頼。

Claude Code 側の md-reviewer 用レビュー依頼ですが、結論は Codex 向けの運用改善アドバイスとしてほしいです。

## 依頼内容

Codex が Claude Code 側の Markdown 正本同期を行うときに、Codex 独自生成物をどう保護すべきかをレビューしてください。

特に以下を評価してください。

- `docs/claude-md-sync.md` の mirror mode 説明・注意書き
- `scripts/sync_claude_md.py` の mirror mode 除外ルール
- `docs/codex-operation-knowledge.md` または `AGENTS.md` に追加すべき Codex 側運用ルール
- mirror 実行前の確認粒度
- `docs/reviews/` 配下の Codex 生成レビュー依頼・検証結果の命名規約
- untracked な Codex 成果物が mirror delete で復旧困難になることを防ぐガード

## 事故事象

ユーザーから「クロードコード側から md ファイルを取り込む」指示があり、Codex はまず `docs/claude-md-sync.md` と `scripts/sync_claude_md.py` を確認しました。

その後、通常の safe sync を実行しました。

- dry-run: `SAFE_IMPORT 112`, `CONFLICT 26`, `CODEX_ONLY 5`, `CLAUDE_DELETED 2`
- apply: safe import のみ反映

続いてユーザーから「クロードコードが正。すべて上書き、削除も反映」と指示がありました。

Codex はこれを「Claude Code 側を正本とする mirror mode の適用」と解釈し、以下を実行しました。

```powershell
python scripts/sync_claude_md.py --mode mirror --apply
```

結果として、mirror mode の削除対象に Codex 独自生成物が含まれました。

## 影響を受けた Codex 成果物

削除または削除対象になった主なファイルは以下です。

- `docs/reviews/045_codex_phase6_order_backlog_adapter_validation.md`
- `docs/reviews/041_codex_unit_mixed_fix_review_request.md`
- `docs/reviews/001_sync_claude_md_mirror_gap.md`
- `docs/reviews/20260428_codex_cleanup_workspace_review.md`
- `docs/claude-code-handoff-template.md`

このうち tracked ファイルは `git restore` で復旧し、untracked の Codex レビュー成果物は手動再作成しました。

## Codex 側の解釈

今回の本質は、Claude Code 側の正本同期指示を受けたときに、Codex が「Claude Code 管理下の共有 Markdown」と「Codex 独自成果物」を明確に分離しないまま mirror delete を許容したことです。

Claude Code 側の成果物や指示を問題視する意図はありません。Codex 側の同期操作・確認粒度・成果物保護ルールを見直すための報告です。

## 確認したいこと

md-reviewer には、Codex 向けに以下の観点で助言をお願いします。

1. mirror mode 実行時に `docs/reviews/*codex*` や `docs/reviews/*_codex_*` を除外すべきか。
2. `docs/reviews/NNN_codex_*.md` のような命名規約を Codex 成果物保護の前提にしてよいか。
3. `scripts/sync_claude_md.py --mode mirror` は、削除対象に untracked または Codex 命名のファイルが含まれる場合に fail closed すべきか。
4. 「Claude Code が正、すべて上書き、削除も反映」という指示があっても、Codex 独自成果物の削除は別確認にすべきか。
5. `docs/codex-operation-knowledge.md` に、mirror 同期前の必須確認項目として何を書くべきか。

## 期待する出力

- Codex に追加すべき運用ルール案
- `docs/claude-md-sync.md` への追記案
- `scripts/sync_claude_md.py` のガード改善案
- 今後の mirror 実行時の確認テンプレート案

---

# MD AI可読性レビュー: Codex mirror sync 時の独自成果物保護

- 日時: 2026-05-01 18:36 JST
- 対象: `docs/claude-md-sync.md`, `scripts/sync_claude_md.py`, `AGENTS.md`, `docs/codex-operation-knowledge.md`
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/048_mr_codex_mirror_sync_artifact_protection.md`

---

## 【サマリー】

- レビュー対象の要約: Codex が Claude Code 正本の mirror sync を実行した際、Codex 独自生成物（レビュー MD 等）が削除対象に含まれた事故の原因分析と、Codex 側の運用改善提案
- AI可読性評価: C -- `docs/claude-md-sync.md` の mirror mode 説明が「Codex 独自成果物の扱い」について無言であり、AI が mirror = 全削除と解釈する余地がある
- 誤読リスク評価: B -- safe mode の分類体系は明確だが、mirror mode への切り替え判断基準と保護対象の定義が欠落
- 主要リスク:
  - `DEFAULT_EXCLUDES` が `docs/reviews/*codex*` を含まず、mirror mode で Codex 独自レビューが削除対象になる
  - `plan_mirror()` に dest-only ファイルの出自判定がなく、全 dest-only を無差別に DELETE 分類する
  - 「すべて上書き、削除も反映」指示を受けた AI が mirror mode 適用時に独自成果物の別確認を行う義務が未定義

---

## 【Markdown 品質評価】

### Accuracy / 正確性

- `docs/claude-md-sync.md` の mirror mode に関する記述は正確だが、記述範囲が「共有 MD の同期」に限定されており、Codex 独自成果物の存在を前提としていない
- `DEFAULT_EXCLUDES` の除外パターン（`docs/codex-*.md`, `docs/plans/*codex*.md`）は `docs/reviews/` 配下の Codex 固有ファイルをカバーしない。これは正確であるが不十分
- `docs/codex-operation-knowledge.md` 11 の「`docs/knowledges/` 配下は Claude Code から Codex への一方通行同期対象」は正確だが、`docs/reviews/` の双方向性（両側で独自ファイルが生成される）への言及がない

### Completeness / 完全性

- mirror mode の説明に以下が欠落:
  - Codex 独自成果物（レビュー MD、検証結果、handoff テンプレート等）の保護方針
  - mirror 実行前の確認チェックリスト
  - DELETE 対象に untracked/Codex 命名ファイルが含まれる場合の処理方針
  - 「全上書き」指示でも保護すべきファイル種別の定義
- `AGENTS.md` 4 の Execution Change Rule は適用可能な原則だが、mirror mode 特有のリスク（独自成果物消失）への導線がない

### Relevance / 関連性

- `docs/claude-md-sync.md` の safe mode 説明は充実しており、mirror mode との対比が明確。ノイズは少ない
- `docs/codex-operation-knowledge.md` は全般的な運用知識だが、sync 固有の注意事項がない

### Actionability / 実行可能性

- AI が「Claude Code が正、すべて上書き」指示を受けた場合に、mirror mode を選択することは自然な判断
- しかし mirror mode 実行時に「Codex 独自成果物を別途保護する」判断基準が文書上にないため、AI は指示通りに全削除を実行する
- dry-run 出力で DELETE 行を目視確認する習慣は暗黙的に期待されているが、DELETE 対象が「保護すべきか否か」の判定基準が未定義

---

## 【AI 誤読リスク】

1. **「mirror mode = Claude Code 側を正本として完全一致させる」の解釈幅**: `claude-md-sync.md:72` の `--mode mirror` コマンド例が注意書きなしで提示されており、AI は mirror = 例外なく Claude Code 側に揃える、と読む。Codex 独自ファイルを保護する判断が介入する余地がない
2. **`DEFAULT_EXCLUDES` の暗黙的信頼**: AI はスクリプトの除外パターンを「設計者が保護すべきファイルを既に定義済み」と解釈する。実際には `docs/reviews/*codex*` が漏れている
3. **「削除も反映」指示の解釈**: ユーザーが「すべて上書き、削除も反映」と指示した場合、AI は「ユーザーが削除を承認済み」と判断し、dry-run 結果の DELETE 行を精査せずに `--apply` を実行する合理的根拠を持つ

---

## 【MD 構成リスク】

- `docs/claude-md-sync.md` は safe mode 中心の構成で、mirror mode は最下部の 4 行コマンド例のみ。mirror mode の注意事項・制約・保護対象が構造的に不在
- `docs/codex-operation-knowledge.md` 11 は `docs/knowledges/` の一方通行性を定義するが、`docs/reviews/` の双方向性に触れない。AI が「`docs/` 配下は全て Claude Code 正本」と一般化するリスクがある

---

## 【指示優先順位・文脈境界】

- **ユーザー指示 vs 成果物保護**: ユーザーから「すべて上書き」指示があった場合、Codex の AI がその指示を「Codex 独自成果物も含めた全削除の承認」と解釈するか「共有 MD に限定した上書きの承認」と解釈するかが曖昧。`AGENTS.md` の Execution Change Rule（5）は「後続操作に影響する場合は明示せよ」と規定するが、mirror mode の DELETE がこの条件に該当するかの判断が AI に委ねられている
- **正本帰属の空白**: `docs/reviews/` 配下のファイルは Claude Code 側とCodex 側で独立に生成される。どちらが正本かの定義がなく、mirror mode が一律に Claude Code 側を正本として扱うことで Codex 独自ファイルが消える

---

## 【パターン 2 のみ: 誤読・ミス原因分析】

### 事象

ユーザーが「クロードコード側から md ファイルを取り込む」と指示。Codex は safe sync を実行後、ユーザーから「クロードコードが正。すべて上書き、削除も反映」と追加指示を受けた。Codex はこれを mirror mode 適用と解釈し、`python scripts/sync_claude_md.py --mode mirror --apply` を実行。結果、Claude Code 側に存在しない Codex 独自レビュー MD（`041_codex_*`, `045_codex_*`, `001_sync_claude_md_mirror_gap.md`, `20260428_codex_cleanup_workspace_review.md`）が DELETE 対象として削除された。

### 読み手がどう解釈した可能性があるか

1. Codex AI は `claude-md-sync.md` を読み、mirror mode が「Claude Code 側をファイルレベルで完全ミラーする」モードであると正しく理解した
2. ユーザーの「すべて上書き、削除も反映」は mirror mode の意味的に完全一致するため、指示に忠実に実行した
3. `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` が含まれないことを確認する動機がなかった（除外パターンは設計者が定義済みと信頼）
4. dry-run で DELETE 行を確認したとしても、ユーザーが「削除も反映」と明示的に承認しているため、DELETE の精査をスキップする判断は合理的

### 直接原因

1. **`scripts/sync_claude_md.py` の `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` が不在** (`sync_claude_md.py:31-37`): Codex 独自レビュー MD が `collect_markdown()` で収集され、Claude Code 側に対応ファイルがないため `plan_mirror()` で DELETE に分類される
2. **`plan_mirror()` に dest-only ファイルの出自判定がない** (`sync_claude_md.py:209-224`): dest に存在し source に存在しないファイルは無差別に DELETE になる。ファイル名パターンや git status（tracked/untracked）による保護判定がない
3. **`claude-md-sync.md` に mirror mode での保護対象の定義がない**: safe mode の `CODEX_ONLY` 分類（Codex 側だけ変更→触らない）に相当する概念が mirror mode に存在しない

### 根本原因

1. **`docs/reviews/` の双方向生成が設計時に想定されていない**: `claude-md-sync.md` の設計は「Claude Code 側が正本で Codex 側が受け取り」を前提にしている。しかし `docs/reviews/` は両側で独立に生成されるディレクトリであり、この非対称性が除外パターンと mirror mode 設計に反映されていない
2. **mirror mode の設計が「共有ファイルの強制同期」と「Codex 独自ファイルの削除」を区別しない**: mirror mode は safe mode の CONFLICT 解消手段として設計されたが、副次効果として Codex 独自ファイルの削除が発生する構造になっている
3. **Codex 独自成果物の命名規約と保護規約が未定義**: `docs/codex-*.md` は除外パターンに含まれるが、`docs/reviews/` 配下の Codex 固有ファイルには命名規約がなく、保護対象として識別できない

### 誤読を許した MD 上の原因

- `docs/claude-md-sync.md:72-79`: mirror mode コマンド例が注意書きなしで提示。safe mode の「Codex 側の MD 変更を上書きしない」原則（L83）が mirror mode にも適用されるかが不明
- `docs/claude-md-sync.md:31-37`: `DEFAULT_EXCLUDES` のコメントが「Codex 移植・並走運用のために Codex 側だけで持つ文書は既定除外する」と説明するが、ここに列挙されたパターンが網羅的かどうかが不明。AI は「ここに書かれていないパターンは保護対象外」と読む
- `docs/codex-operation-knowledge.md:105-108`: `docs/knowledges/` の一方通行性は明記するが、`docs/reviews/` が双方向であることが未記載。AI が `docs/` 配下全体に一方通行原則を一般化するリスク
- `AGENTS.md:60-63`: Execution Change Rule は mirror mode の破壊的操作に適用可能だが、sync スクリプト実行が「既存 method の使用」に見えるため発火しない

### 再発防止の方向性

1. **コード側ガード**: `plan_mirror()` または `apply_mirror_rows()` で、dest-only ファイルのうち Codex 命名パターン（`*codex*`, `*_codex_*`）に一致するものを DELETE から除外し、別ステータス（`CODEX_PROTECTED`）で表示する
2. **除外パターン拡張**: `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` と `docs/reviews/*_codex_*` を追加
3. **MD 改善**: `claude-md-sync.md` に mirror mode の注意事項セクションを追加し、保護対象と確認義務を明記
4. **運用ルール追加**: `docs/codex-operation-knowledge.md` に mirror sync 前の必須確認チェックリストを追加
5. **命名規約の確立**: Codex 独自成果物に `_codex_` または `codex_` プレフィックスを命名規約として義務化し、保護の前提にする

---

## 【重大な指摘】（即修正）

### #1 `plan_mirror()` が dest-only ファイルを無差別に DELETE 分類する

- 箇所: `scripts/sync_claude_md.py:220-221`
- 問題: `plan_mirror()` は dest に存在し source に存在しないファイルを全て `Row("DELETE", path, "remove destination-only file")` に分類する。Codex 独自成果物か共有ファイルの Codex 側残骸かを区別しない
- AI の誤読パターン: AI はスクリプトの分類結果を信頼し、DELETE 対象を精査しない。ユーザーが「削除も反映」と指示している場合は特に精査動機がない
- トリガー: ユーザーが mirror mode 実行を指示し、Codex 側に独自ファイルが存在する場合
- 影響: Codex 独自レビュー MD、検証結果 MD、handoff テンプレート等が不可逆的に削除される（untracked の場合は `git restore` 不可）
- 根拠: 実際に `041_codex_unit_mixed_fix_review_request.md`, `045_codex_phase6_order_backlog_adapter_validation.md` 等が削除された
- 推奨対応: `plan_mirror()` に Codex 命名パターン検出を追加し、該当ファイルを `CODEX_PROTECTED` ステータスで出力。`--apply` 時に CODEX_PROTECTED は削除しない。または `--force-delete-codex` のような明示フラグを要求する
- MD 修正だけで足りるか: 足りない。コード側のガード改善が必須

### #2 `DEFAULT_EXCLUDES` が `docs/reviews/*codex*` を含まない

- 箇所: `scripts/sync_claude_md.py:31-37`
- 問題: 除外パターンに `docs/codex-*.md` と `docs/plans/*codex*.md` はあるが、`docs/reviews/*codex*` と `docs/reviews/*_codex_*` がない。Codex が `docs/reviews/` に独自ファイルを生成する運用が始まった後にパターンが更新されていない
- AI の誤読パターン: AI は `DEFAULT_EXCLUDES` を「設計者が保護すべきファイルを網羅的に定義済み」と信頼する
- トリガー: mirror mode 実行時、Codex 側 `docs/reviews/` に独自ファイルが存在する場合
- 影響: Codex 独自レビュー MD が `collect_markdown()` で収集され、mirror mode で DELETE 対象になる
- 根拠: Codex 側 `docs/reviews/` に `001_sync_claude_md_mirror_gap.md`, `041_codex_*`, `045_codex_*`, `20260428_codex_cleanup_workspace_review.md` が存在するが、いずれも除外パターンに該当しない
- 推奨対応: `DEFAULT_EXCLUDES` に `"docs/reviews/*codex*"`, `"docs/reviews/*_codex_*"` を追加。さらに、将来の Codex 独自ファイル生成に備え、全ディレクトリ横断の `"*codex*"` 除外を検討（ただし過剰除外のリスクあり）
- MD 修正だけで足りるか: 足りない。コード修正が必要

### #3 `claude-md-sync.md` に mirror mode の保護対象・注意事項が不在

- 箇所: `docs/claude-md-sync.md:72-79`（Codex 側ファイル）
- 問題: mirror mode のコマンド例は示されているが、「mirror mode で何が削除されるか」「保護すべき Codex 独自成果物は何か」「mirror 実行前に何を確認すべきか」の記述がない。safe mode の「Codex 側の MD 変更を上書きしない」原則（L83）が mirror mode にも適用されるかが不明
- AI の誤読パターン: AI は mirror mode = safe mode の制約を解除した強制同期、と解釈する。保護対象の概念が存在しないため、dest-only ファイルの全削除を正常動作と判断する
- トリガー: ユーザーから mirror mode 実行指示、または「全上書き」指示
- 影響: AI が保護確認なしに mirror mode を実行し、Codex 独自成果物を削除する
- 根拠: 今回の事故
- 推奨対応: mirror mode セクションに以下を追記: (a) 保護対象の定義 (b) mirror 実行前チェックリスト (c) DELETE 対象に Codex 命名ファイルが含まれる場合の処理
- MD 修正だけで足りるか: 足りる（コード側ガードと併用で効果最大化）

### #4 Codex 独自成果物の命名規約が未定義

- 箇所: `docs/codex-operation-knowledge.md` 全体、`AGENTS.md` 全体
- 問題: Codex が `docs/reviews/` に独自ファイルを生成する際の命名規約が未定義。現状のファイル名は `NNN_codex_*.md`（`041_codex_*`, `045_codex_*`）と `NNN_sync_claude_md_*.md`（`001_sync_claude_md_mirror_gap.md`）と `YYYYMMDD_codex_*.md`（`20260428_codex_cleanup_workspace_review.md`）が混在。保護パターンとして `*codex*` で網羅できるが、`001_sync_claude_md_mirror_gap.md` のように `codex` を含まないファイルは保護されない
- AI の誤読パターン: 命名規約がないため、AI が新規ファイルを作成する際に `codex` を含まない名前を選ぶ可能性がある
- トリガー: Codex が `docs/reviews/` に新規ファイルを作成する場合
- 影響: 命名規約外のファイルが mirror mode で保護されない
- 根拠: `001_sync_claude_md_mirror_gap.md` は `codex` を含まないため、`*codex*` 除外パターンでは保護されない
- 推奨対応: Codex 独自成果物の命名規約を `NNN_codex_<slug>.md` に統一し、`docs/codex-operation-knowledge.md` と `AGENTS.md` に明記する。既存の規約外ファイルはリネームする
- MD 修正だけで足りるか: 足りる（命名規約の文書化で対応可）

### #5 mirror mode で untracked ファイル削除時のフェイルセーフがない

- 箇所: `scripts/sync_claude_md.py:285-306`
- 問題: `apply_mirror_rows()` は DELETE 対象ファイルの git tracking 状態を確認しない。tracked ファイルは `git restore` で復旧可能だが、untracked ファイルは復旧不可能。untracked ファイルの削除は事実上不可逆操作であり、`--dry-run` 先行原則（CLAUDE.md §破壊的操作）に照らしても追加のガードが必要
- AI の誤読パターン: AI は dry-run 出力を確認するが、DELETE 対象が tracked/untracked かを区別する判断基準がない
- トリガー: mirror mode で untracked な Codex 独自ファイルが DELETE 対象になる場合
- 影響: 不可逆的なデータ損失
- 根拠: 提出 MD の「untracked の Codex レビュー成果物は手動再作成しました」の記述
- 推奨対応: `apply_mirror_rows()` で DELETE 実行前に `git ls-files --error-unmatch <path>` 相当のチェックを行い、untracked ファイルの場合は警告表示 + `--force-delete-untracked` フラグを要求する
- MD 修正だけで足りるか: 足りない。コード側ガードが必要

---

## 【改善提案】（中優先度）

### #1 mirror dry-run 出力に DELETE 対象の出自情報を追加

- 箇所: `scripts/sync_claude_md.py:209-224`
- 現状: `plan_mirror()` の DELETE 行は `"remove destination-only file"` としか表示しない
- 提案: DELETE 行の action に「tracked/untracked」「Codex 命名パターン一致/不一致」の情報を付加する。例: `"DELETE (untracked, matches codex pattern) docs/reviews/041_codex_*.md"`
- 期待効果: dry-run 結果の目視確認時に保護すべきファイルの判別が容易になる

### #2 `docs/codex-operation-knowledge.md` に sync セクション追加

- 箇所: `docs/codex-operation-knowledge.md` 全体（既存 11 セクションの後）
- 現状: sync 運用に関する注意事項がない。`AGENTS.md:4` が `claude-md-sync.md` への参照を持つが、Codex 独自成果物保護の観点がない
- 提案: 新セクション「12. Claude Code MD 同期時の注意」を追加し、以下を記載:
  - mirror mode 実行前の必須確認チェックリスト
  - Codex 独自成果物の命名規約
  - 「全上書き」指示でも Codex 独自成果物は別確認する義務
  - untracked ファイルの mirror 削除は不可逆であることの明示
- 期待効果: Codex AI が mirror mode 実行前に保護確認を行う導線が確立される

### #3 mirror mode の safe モードからのエスカレーション判断基準を明記

- 箇所: `docs/claude-md-sync.md:82-86`（Codex 側ファイル）
- 現状: 原則セクションに「Codex 側の MD 変更を上書きしない」とあるが、mirror mode への切り替え判断基準がない。ユーザーが「全上書き」と指示した場合に safe mode の `--apply --delete` で対応するか mirror mode に切り替えるかの判断が AI に委ねられている
- 提案: 原則セクションに「mirror mode は safe mode の CONFLICT を手動解決した上でも不整合が残る場合の最終手段として使う。独自成果物が存在する場合は事前にバックアップを取るか、除外パターンに追加してから実行する」を追記
- 期待効果: mirror mode が「最終手段」として位置づけられ、安易な切り替えが抑制される

---

## 【ソースコード・仕組み側への波及】

### sync_claude_md.py のガード改善

- 対象: `scripts/sync_claude_md.py`
- 理由: MD の注意書きだけでは AI が mirror mode 実行時に独自成果物を保護する保証がない。コード側のフェイルセーフが必要
- 推奨対応:
  1. `DEFAULT_EXCLUDES` に `"docs/reviews/*codex*"`, `"docs/reviews/*_codex_*"` を追加
  2. `plan_mirror()` に Codex 命名パターン検出を追加し、dest-only かつパターン一致のファイルを `CODEX_PROTECTED` ステータスにする
  3. `apply_mirror_rows()` で `CODEX_PROTECTED` は削除しない。削除にはユーザー確認を含む `--include-codex-files` のような明示フラグを要求する
  4. DELETE 対象が untracked の場合に警告を表示する
- 検証方法:
  1. Codex 側に `docs/reviews/999_codex_test.md` を作成（untracked）
  2. `python scripts/sync_claude_md.py --mode mirror` を実行し、dry-run で `CODEX_PROTECTED` ステータスを確認
  3. `--apply` 実行後に当該ファイルが残存することを確認

---

## 【修正文案】

### 1. `docs/claude-md-sync.md` への追記案

```markdown
# before (L79 の後、原則セクションの前に新セクション追加)
（原則セクションの直前に挿入）

# after
## Mirror mode の注意事項

mirror mode は Claude Code 側を完全正本としてファイルレベルの一致を強制する。
以下の特性を理解した上で使うこと。

- Codex 側にしか存在しないファイルは全て DELETE 対象になる
- `DEFAULT_EXCLUDES` に含まれないパターンの Codex 独自ファイルも削除される
- untracked ファイルの削除は `git restore` で復旧できない

### 保護対象

以下のパターンに一致するファイルは、mirror mode でも削除しない。

- `docs/reviews/*codex*` — Codex 独自レビュー・検証結果
- `docs/reviews/*_codex_*` — Codex 独自レビュー（別命名パターン）
- `docs/codex-*.md` — Codex 運用文書（既存の除外パターン）
- `docs/plans/*codex*.md` — Codex 作業計画（既存の除外パターン）

### mirror 実行前チェックリスト

1. `--mode mirror` を dry-run で実行し、DELETE 対象を確認する
2. DELETE 対象に Codex 独自成果物（`*codex*`, `*_codex_*`）が含まれていないか確認する
3. DELETE 対象に untracked ファイルが含まれていないか `git status` で確認する
4. Codex 独自成果物が DELETE 対象に含まれる場合は、バックアップを取るか除外パターンに追加してから再実行する
5. 上記確認後に `--apply` を実行する

### 「全上書き」指示への対応

ユーザーから「すべて上書き」「Claude Code が正」等の指示があっても、以下は別確認にする。

- Codex 命名パターン（`*codex*`）に一致するファイルの削除
- untracked ファイルの削除
```

### 2. `docs/codex-operation-knowledge.md` への追記案

```markdown
# before (末尾に新セクション追加)

# after
## 12. Claude Code MD 同期時の独自成果物保護

mirror mode（`--mode mirror`）は Claude Code 側を正本として全ファイルを強制同期する。
Codex 独自成果物が削除対象に含まれるため、以下を必ず遵守する。

### 命名規約

Codex が `docs/reviews/` に独自ファイルを作成する場合は、ファイル名に `codex` を含める。

- 推奨: `NNN_codex_<slug>.md`
- 許容: `NNN_<slug>_codex_<detail>.md`
- 禁止: `codex` を含まない名前（保護パターンから漏れる）

### mirror 実行前の必須確認

1. dry-run で DELETE 対象を確認する
2. DELETE 対象に Codex 命名パターンのファイルが含まれていないか確認する
3. untracked ファイルが DELETE 対象に含まれていないか確認する
4. 不安な場合は `git stash --include-untracked` で退避してから実行する

### 「全上書き」指示への対応

ユーザーが「Claude Code が正、全上書き」と指示しても、Codex 独自成果物の削除は別途ユーザーに確認する。
理由: ユーザーの意図は「共有 MD の不整合解消」であり、「Codex 独自成果物の破棄」ではない可能性が高い。
```

### 3. `scripts/sync_claude_md.py` のガード改善案

```python
# before (DEFAULT_EXCLUDES)
DEFAULT_EXCLUDES = (
    "docs/claude-code-intake-checklist.md",
    "docs/claude-md-sync.md",
    "docs/codex-*.md",
    "docs/git-bootstrap-notes.md",
    "docs/plans/*codex*.md",
)

# after
DEFAULT_EXCLUDES = (
    "docs/claude-code-intake-checklist.md",
    "docs/claude-md-sync.md",
    "docs/codex-*.md",
    "docs/git-bootstrap-notes.md",
    "docs/plans/*codex*.md",
    "docs/reviews/*codex*",
    "docs/reviews/*_codex_*",
)
```

```python
# before (plan_mirror)
def plan_mirror(
    source: dict[str, FileInfo],
    dest: dict[str, FileInfo],
) -> list[Row]:
    rows: list[Row] = []
    paths = sorted(set(source) | set(dest))
    for path in paths:
        src = source.get(path)
        dst = dest.get(path)
        if src and not dst:
            rows.append(Row("ADD", path, "new from Claude"))
        elif dst and not src:
            rows.append(Row("DELETE", path, "remove destination-only file"))
        elif src and dst and src.sha256 != dst.sha256:
            rows.append(Row("MODIFY", path, "overwrite from Claude"))
    return rows

# after
CODEX_ARTIFACT_PATTERNS = ("*codex*", "*_codex_*")

def _is_codex_artifact(path: str) -> bool:
    """Check if the file path matches Codex artifact naming patterns."""
    filename = path.rsplit("/", 1)[-1] if "/" in path else path
    return any(fnmatch.fnmatchcase(filename, pat) for pat in CODEX_ARTIFACT_PATTERNS)

def plan_mirror(
    source: dict[str, FileInfo],
    dest: dict[str, FileInfo],
) -> list[Row]:
    rows: list[Row] = []
    paths = sorted(set(source) | set(dest))
    for path in paths:
        src = source.get(path)
        dst = dest.get(path)
        if src and not dst:
            rows.append(Row("ADD", path, "new from Claude"))
        elif dst and not src:
            if _is_codex_artifact(path):
                rows.append(Row("CODEX_PROTECTED", path, "Codex artifact, skipping delete"))
            else:
                rows.append(Row("DELETE", path, "remove destination-only file"))
        elif src and dst and src.sha256 != dst.sha256:
            rows.append(Row("MODIFY", path, "overwrite from Claude"))
    return rows
```

---

## 【推奨検証（Step 8）】

### 正本帰属チェック（8a）

- `claude-md-sync.md` への追記: このファイルは Codex 側の正本（Codex 側だけで管理、Claude Code 側には存在しない）。Codex 側の sync 運用に関する正本として適切
- `codex-operation-knowledge.md` への追記: Codex 運用知識の正本として適切。CLAUDE.md の memory 制約には抵触しない（Codex 側のファイルであるため）
- `sync_claude_md.py` の修正: Codex 側のスクリプトの修正であり正本帰属に問題なし
- 全ての推奨は Codex 側ファイルの修正であり、Claude Code 側のファイルへの修正は含まない。正本帰属に問題なし

### 上位ルール整合性（8b）

- CLAUDE.md §破壊的操作: 推奨する `CODEX_PROTECTED` ガードは CLAUDE.md の「破壊的操作は dry-run 先行」原則と整合する。mirror mode の DELETE が破壊的操作に該当するのに dry-run のみで保護ガードがない現状よりも改善される
- AGENTS.md §Execution Change Rule: 推奨する mirror 実行前チェックリストは「後続操作に影響する場合は明示せよ」の具体化であり整合する
- `docs/codex-parallel-operation-policy.md` §8 注意事項: 「重要変更は Codex 側だけで完結させない」原則と、mirror mode による独自成果物削除の別確認義務は整合する
- CLAUDE.md 側の修正は推奨に含まれないため、Claude Code 側ルールとの衝突リスクはない

### 副作用シミュレーション（8c）

**(i) 単体副作用**:
- `CODEX_PROTECTED` ステータスの追加により、mirror mode の出力ステータスが 4 種（ADD/MODIFY/DELETE/CODEX_PROTECTED）に増える。`summarize_rows()` の `ordered_statuses` に `CODEX_PROTECTED` を追加する必要がある（修正文案では省略しているが実装時に必要）
- `_is_codex_artifact()` の `*codex*` パターンは、`docs/reviews/` 以外のディレクトリにも適用される。ただし `DEFAULT_EXCLUDES` で `docs/codex-*.md` は既に除外済みのため二重保護になるだけで実害はない
- 過剰保護リスク: ファイル名に偶然 `codex` を含む Claude Code 側のファイル（例: `docs/reviews/NNN_mr_codex_handoff_wrong_file.md` = Claude Code 側の既存ファイル 045）が mirror mode で MODIFY されなくなる可能性。ただしこのファイルは source と dest の両方に存在するため、`plan_mirror()` の dest-only 分岐には到達しない。影響なし

**(ii) クロスルール競合**:
- `claude-md-sync.md` の既存原則「Codex 側の MD 変更を上書きしない」（L83）と、mirror mode の追記案「Codex 命名パターンのファイルは削除しない」は同方向のルールであり競合しない
- `AGENTS.md` の Execution Change Rule と mirror 実行前チェックリストは補完関係にあり競合しない
- `codex-operation-knowledge.md` §11 の一方通行原則（`docs/knowledges/` のみ）と §12 の双方向保護（`docs/reviews/`）は対象ディレクトリが異なるため競合しない

**(iii) 状態依存シナリオ**:
- mirror mode は状態を持たない（毎回ファイルレベルの差分を取る）ため、状態依存の問題は発生しない
- ただし、Codex 独自ファイルが `codex` を含まない名前で作成された場合、`CODEX_PROTECTED` による保護が効かない。この場合は `DEFAULT_EXCLUDES` への個別追加が必要になるが、命名規約の導入により将来的には解消される

### 事後確認事項（8d）

1. 推奨実施後に mirror mode を dry-run 実行し、既存の Codex 独自ファイル（`041_codex_*`, `045_codex_*`, `001_sync_claude_md_mirror_gap.md`）が `CODEX_PROTECTED` として表示されることを確認
2. `001_sync_claude_md_mirror_gap.md` は `codex` を含まないため、`CODEX_PROTECTED` にならない可能性がある。命名規約に従い `001_codex_sync_mirror_gap.md` にリネームするか、`DEFAULT_EXCLUDES` に個別追加が必要
3. 新規 Codex 独自ファイル作成時に命名規約が遵守されているか、次回 mirror 実行時に確認
4. `summarize_rows()` 呼び出し箇所に `CODEX_PROTECTED` ステータスが追加されているか確認（実装の完全性）

---

## 【確認できなかった事項】

- Codex 側 `001_sync_claude_md_mirror_gap.md` の内容（`codex` を含まないファイル名の意図・経緯が不明）
- `20260428_codex_cleanup_workspace_review.md` が tracked か untracked かの確認（git status を実行していない）
- Codex 側で今後 `docs/reviews/` 以外のディレクトリに独自ファイルを生成する計画があるか（命名規約の適用範囲に影響）
- `docs/claude-code-handoff-template.md`（影響を受けたファイルの 1 つ）の復旧状況

---

## 確認したいこと 5 点への回答

### 1. mirror mode で `docs/reviews/*codex*` を除外すべきか

**はい、除外すべき。** `DEFAULT_EXCLUDES` に `"docs/reviews/*codex*"` と `"docs/reviews/*_codex_*"` を追加し、さらにコード側で `plan_mirror()` に `CODEX_PROTECTED` ガードを入れる二重防御を推奨する。除外パターンは `collect_markdown()` 段階でファイルを収集対象外にするため、mirror の比較対象にすら入らない。一方 `CODEX_PROTECTED` は除外パターンをすり抜けたファイルの最終防御として機能する。

### 2. `NNN_codex_*.md` 命名規約を保護前提にしてよいか

**よい。ただし規約の文書化と既存ファイルのリネームが前提。** 現状 `001_sync_claude_md_mirror_gap.md` のように `codex` を含まないファイルが存在するため、命名規約の導入と同時に既存ファイルのリネームが必要。命名規約は `docs/codex-operation-knowledge.md` と `AGENTS.md` の両方に記載する。

### 3. sync スクリプトで untracked/Codex 命名ファイル削除時に fail closed すべきか

**はい、fail closed すべき。** 具体的には:
- Codex 命名パターン一致: `CODEX_PROTECTED` ステータスにして `--apply` 時にスキップ（fail closed）
- untracked ファイル: 警告を表示し、`--force-delete-untracked` のような明示フラグがない限り削除しない
- 上記のいずれかに該当するファイルが存在する場合、`--apply` の戻り値を非ゼロにして注意喚起する

### 4. 「全上書き」指示でも Codex 独自成果物は別確認にすべきか

**はい、別確認にすべき。** ユーザーの「全上書き」指示の意図は通常「共有 MD の不整合解消」であり、「Codex 独自成果物の破棄」ではない。Codex AI は mirror mode 実行前に DELETE 対象を確認し、Codex 独自成果物が含まれる場合はユーザーに個別確認を取る義務を `docs/codex-operation-knowledge.md` に明記する。

### 5. mirror 同期前の必須確認項目

以下のチェックリストを `docs/codex-operation-knowledge.md` §12 に記載する:

1. `python scripts/sync_claude_md.py --mode mirror`（dry-run）を実行し DELETE 対象を一覧する
2. DELETE 対象に Codex 命名パターン（`*codex*`, `*_codex_*`）のファイルが含まれていないか確認する
3. DELETE 対象に untracked ファイルが含まれていないか `git status docs/reviews/` で確認する
4. Codex 独自成果物が DELETE 対象に含まれる場合、ユーザーに個別確認を取る
5. 不安な場合は `git stash --include-untracked` で全ファイルを退避してから実行する
6. `--apply` 実行後に `git diff --stat` で変更内容を確認する
7. Codex 独自成果物が残存していることを `ls docs/reviews/*codex*` で確認する

