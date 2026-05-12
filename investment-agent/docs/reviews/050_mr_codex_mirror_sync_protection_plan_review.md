# MD AI可読性レビュー: Codex mirror sync 保護実装計画

- 日時: 2026-05-01 19:04 JST
- 対象: `C:\Users\zonekun\Documents\codex\investment-agent\docs\plans\20260501_185114_codex_mirror_sync_protection_plan.md`
- パターン: 3 (ad-hoc MD レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/050_mr_codex_mirror_sync_protection_plan_review.md`
- 関連: 提出 MD `docs/reviews/049_mr_codex_mirror_sync_protection_plan.md`、元レビュー `docs/reviews/048_mr_codex_mirror_sync_artifact_protection.md`

---

## 【サマリー】

- レビュー対象の要約: 048 レビュー（重大指摘5件）を受けた Codex 側の mirror sync 保護実装計画。`DEFAULT_EXCLUDES` 拡張、`CODEX_PROTECTED` ステータス追加、untracked 削除フェイルセーフ、命名規約、文書改善の6項目で構成
- AI可読性評価: A -- 計画構成は明確で、048 指摘への対応が項番対応で追跡可能。ただし一部の仕様詳細に曖昧さが残る
- 誤読リスク評価: B -- 実装者が計画を読んで作業する際に、判断が分かれる箇所が複数存在する
- 主要リスク:
  - `DEFAULT_EXCLUDES` と `_is_codex_artifact()` のパターン適用スコープが異なることが計画上で明示されていない
  - untracked 削除の「非ゼロ終了」仕様が CODEX_PROTECTED 検出時にも適用されるか曖昧
  - `001_sync_claude_md_mirror_gap.md` のリネーム後の採番衝突リスクが未検討

---

## 【Markdown 品質評価】

### Accuracy / 正確性

- 計画が参照する 048 の重大指摘5件と計画の対応関係は正確。5件すべてに対応する実装方針が記述されている
- `DEFAULT_EXCLUDES` の現行値（計画 L31-37 相当の記述）は実コード `sync_claude_md.py:31-37` と一致
- `plan_mirror()` の現行動作（dest-only を無差別 DELETE）の記述は実コード `sync_claude_md.py:220-221` と一致
- `apply_mirror_rows()` の現行動作（git tracking 状態を確認しない）は実コード `sync_claude_md.py:285-306` と一致

### Completeness / 完全性

- 048 の5件に対する対応は網羅されている
- テスト計画に検証項目7件が列挙されているが、エッジケースの網羅性に不足あり（後述）
- 実装後の確認手順5項目は適切
- ただし `summarize_rows()` の `ordered_statuses` への `CODEX_PROTECTED` 追加が実装方針本文に明記されていない（048 レビューの副作用シミュレーションで言及されているが、計画自体の §1 に記載漏れ）

### Relevance / 関連性

- 計画は対象を明確に絞っており、ノイズが少ない
- 各セクションが048の指摘番号と対応づけ可能な構成になっている

### Actionability / 実行可能性

- 実装者が「次に何をするか」は明確
- ただし、複数の判断ポイントで「どちらを選ぶか」の判断基準が実装者に委ねられている（§6 のリネーム vs 個別除外など）

---

## 【AI 誤読リスク】

1. **`DEFAULT_EXCLUDES` と `CODEX_ARTIFACT_PATTERNS` の適用スコープの違いが非明示**: `DEFAULT_EXCLUDES` は `collect_markdown()` 段階で収集自体をスキップするため、source 側・dest 側の両方に適用される。一方 `_is_codex_artifact()` は `plan_mirror()` の dest-only 分岐でのみ呼ばれる。計画 §1 は「第一防御」「第二防御」と書くが、適用スコープ（収集段階 vs 計画段階）の違いを明示していない。実装者が `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` を追加すると、Claude Code 側にも `*codex*` を含むファイルが存在する場合（例: `045_mr_codex_handoff_wrong_file.md`）、そのファイルの source 側収集もスキップされ、mirror の MODIFY 判定に入らなくなる。ただし現時点で Claude Code 側に `docs/reviews/*codex*` パターンに一致するファイルは `045_mr_codex_handoff_wrong_file.md` のみであり、このファイルは Codex 側にも同名で存在するため実害は限定的

2. **`CODEX_ARTIFACT_PATTERNS` の `*codex*` がファイル名のみに適用されるか、パス全体に適用されるかが計画から読み取れない**: 048 の修正文案では `filename = path.rsplit("/", 1)[-1]` とファイル名部分のみを対象にしているが、計画本文の §1 は「ファイル名に `codex` を含める」（§4）としか書いておらず、パス中のディレクトリ名に `codex` が含まれるケースを考慮していない

3. **「非ゼロ終了」の範囲が曖昧**: §2 で「untracked かつ `--force-delete-untracked` なしの場合は削除せず、非ゼロ終了にする」とあるが、§1 の `CODEX_PROTECTED` が存在する場合にも非ゼロ終了するかが未定義。CODEX_PROTECTED と untracked DELETE が同時に発生する場合の終了コードの優先順位も不明

---

## 【MD 構成リスク】

- 計画の §1-§6 の構成は論理的で、実装順序と対応している
- テスト計画が §1-§6 の直後に来ており、実装→テストの流れが自然
- ただし §6（既存規約外ファイル）の「推奨: リネーム第一候補」が §1-§5 の実装完了を前提としない独立作業であることが構成上明確でない。実装者がリネームを §1 の `DEFAULT_EXCLUDES` 追加前に行うと、リネーム先ファイル名が除外パターンに含まれるか事前確認が必要になるが、この順序依存性が記述されていない

---

## 【指示優先順位・文脈境界】

- 計画は Codex 側リポジトリのみを変更対象と明記しており（L9-11）、Claude Code 側への影響範囲が明確
- `AGENTS.md` への導線追加（§5）は「詳細ルールを重複させず参照のみ」と明記しており、正本帰属が `docs/claude-md-sync.md` と `docs/codex-operation-knowledge.md` に集約される方針は適切
- 計画と 048 レビュー修正文案の関係: 計画は 048 の修正文案を踏襲しているが、計画独自の判断（例: §6 のリネーム推奨）も含む。048 の修正文案がそのまま実装仕様になるか、計画が上書きするかの優先順位が非明示

---

## 【重大な指摘】（即修正）

### #1 `DEFAULT_EXCLUDES` の `docs/reviews/*codex*` 追加により Claude Code 側の同名ファイルが収集対象外になる

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:38-39`
- 問題: `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` を追加すると、`collect_markdown()` が source 側（Claude Code 側）と dest 側（Codex 側）の両方でこのパターンに一致するファイルを収集しない。Claude Code 側の `docs/reviews/045_mr_codex_handoff_wrong_file.md` は `*codex*` に一致するため source 側で収集されなくなる。結果として、Claude Code 側でこのファイルが更新されても mirror の MODIFY 判定に入らず、Codex 側に更新が反映されない
- AI の誤読パターン: 実装者が「`DEFAULT_EXCLUDES` は Codex 独自ファイルを収集しないためのもの」と理解し、source 側にも適用されることを見落とす
- トリガー: Claude Code 側で `*codex*` パターンに一致するレビューファイルが更新された場合
- 影響: Claude Code 側の更新が Codex 側に同期されない（サイレントな同期漏れ）
- 根拠: `collect_markdown()` は `source_root` と `dest_root` の両方に対して同一の `excludes` パラメータで呼び出される（`sync_claude_md.py:371-372`）。`DEFAULT_EXCLUDES` は「Codex 側だけで持つ文書」を意図しているが、コード上は双方に適用される
- 推奨対応: `DEFAULT_EXCLUDES` の `docs/reviews/*codex*` 追加は行わない。代わりに `_is_codex_artifact()` による `CODEX_PROTECTED` 分類のみで保護する（第二防御のみ）。または、`DEFAULT_EXCLUDES` を source/dest で分離する設計変更を検討する。最も簡単なのは、`CODEX_PROTECTED` のみでの保護に統一し、`DEFAULT_EXCLUDES` は現状のまま据え置くこと
- MD 修正だけで足りるか: 足りない。コード設計の判断を含む

### #2 `001_sync_claude_md_mirror_gap.md` のリネーム先 `001_codex_sync_claude_md_mirror_gap.md` が既存ファイルと採番衝突する

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:108`
- 問題: 計画はリネーム先を明示していないが、`codex` を含む名前にリネームする方針。しかし Codex 側の `docs/reviews/` には既に `001_monitor_backfill_duplicate_workflow.md` が存在する。もしリネーム先を `001_codex_sync_mirror_gap.md` 等にすると、同一採番 `001` のファイルが2つ存在する。md-reviewer の採番ルール（`docs/reviews/NNN` は一意）との不整合が発生する
- AI の誤読パターン: 実装者がリネーム先の採番を気にせず `001_codex_*` にリネームし、ファイル一覧で混乱する
- トリガー: §6 のリネーム実行時
- 影響: 採番の一意性が崩壊し、将来のレビューファイル参照で混乱が生じる
- 根拠: `ls` で確認した Codex 側 `docs/reviews/` に `001_monitor_backfill_duplicate_workflow.md` と `001_sync_claude_md_mirror_gap.md` が既に共存している。これはこの2ファイルが異なる系統（md-reviewer vs code-reviewer/Codex 独自）であるため現状は問題ないが、リネーム後にファイル名の接頭辞から系統を判別しにくくなる
- 推奨対応: リネームではなく `DEFAULT_EXCLUDES` への個別追加（`docs/reviews/001_sync_claude_md_mirror_gap.md`）を推奨する。ただし #1 の指摘を踏まえ、`DEFAULT_EXCLUDES` への追加は source 側にも適用される点に注意。このファイルは Codex 独自ファイルであるため Claude Code 側に存在せず、source 側での除外は影響なし。あるいは `_is_codex_artifact()` に個別パス判定を追加する方法もある
- MD 修正だけで足りるか: 足りる（計画の §6 を修正）

### #3 `summarize_rows()` への `CODEX_PROTECTED` 追加が計画本文に記載漏れ

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:43` 付近（§1 の予定する変更リスト）
- 問題: 計画 §1 の「予定する変更」に「mirror 出力の集計に `CODEX_PROTECTED` を含める」とあるが、具体的に `summarize_rows()` の `ordered_statuses` タプルに `CODEX_PROTECTED` を追加する必要があることが明記されていない。現行コード `sync_claude_md.py:386` で `summarize_rows(rows, ("ADD", "MODIFY", "DELETE"))` と呼んでおり、ここに `"CODEX_PROTECTED"` を追加しないと集計に表示されない
- AI の誤読パターン: 実装者が `plan_mirror()` と `apply_mirror_rows()` の修正だけで完了と判断し、`summarize_rows()` 呼び出し箇所の修正を忘れる
- トリガー: §1 の実装時
- 影響: `CODEX_PROTECTED` ファイルが dry-run 集計に表示されず、保護が機能していることの確認ができない
- 根拠: 048 レビューの副作用シミュレーション (i) で言及されているが、計画の「予定する変更」リストには行単位の変更箇所として記載されていない
- 推奨対応: §1 の予定する変更に「`main()` の mirror mode 分岐で `summarize_rows()` の `ordered_statuses` に `CODEX_PROTECTED` を追加する」を明記する
- MD 修正だけで足りるか: 足りる

### #4 テスト計画に「Claude Code 側に `*codex*` ファイルがある場合の mirror 動作」が欠落

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:118-131`
- 問題: テスト計画の検証項目7件は全て「Codex 側のファイル保護」に焦点を当てているが、#1 で指摘した「Claude Code 側にも `*codex*` パターンに一致するファイルがある場合」のテストが欠落。`DEFAULT_EXCLUDES` に `docs/reviews/*codex*` を追加する方針を採用した場合、Claude Code 側の `045_mr_codex_handoff_wrong_file.md` が収集されなくなることを検証するテストケースが必要
- AI の誤読パターン: テスト計画が「保護の正常系」のみをカバーし、「保護の副作用（過剰除外）」をカバーしない
- トリガー: #1 の `DEFAULT_EXCLUDES` 追加と組み合わせて発生
- 影響: テストが通っても副作用が検出されず、本番で同期漏れが発生する
- 根拠: テスト計画の検証項目に source 側ファイルの収集確認が含まれていない
- 推奨対応: #1 の推奨に従い `DEFAULT_EXCLUDES` 追加を見送る場合はこのテスト不要。`DEFAULT_EXCLUDES` 追加を維持する場合は「source 側に `*codex*` ファイルがある場合に mirror で MODIFY 判定されること」のテストを追加する
- MD 修正だけで足りるか: 足りる

### #5 untracked 判定の実装方法が未定義

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:58-60`
- 問題: §2 で「`apply_mirror_rows()` の DELETE 実行前に git tracking 状態を確認する」とあるが、git tracking 状態の確認方法が未定義。`subprocess` で `git ls-files --error-unmatch` を呼ぶのか、`gitpython` ライブラリを使うのか、`pathlib` ベースでの `.git/` ディレクトリ操作なのかが不明。実装方法により依存関係（新規パッケージ追加の有無）とエラーハンドリングが大きく異なる
- AI の誤読パターン: 実装者が最も簡単な `subprocess.run(["git", "ls-files", "--error-unmatch", path])` を選択するが、git コマンドが利用不可能な環境（CI 等）でのフォールバックを考慮しない
- トリガー: §2 の実装時
- 影響: 実装の品質と堅牢性に影響
- 根拠: 現行 `sync_claude_md.py` は git に一切依存しない純 Python スクリプトであり、git 依存の追加はアーキテクチャ上の判断を伴う
- 推奨対応: 計画に git tracking 確認の実装方法を明記する。推奨は `subprocess.run(["git", "-C", str(dest_root), "ls-files", "--error-unmatch", str(path)], capture_output=True)` の戻りコードによる判定。ただし「git コマンドが利用不可能な場合は tracked として扱う（fail open ではなく fail safe）」のフォールバック方針も明記すべき
- MD 修正だけで足りるか: 足りる

---

## 【改善提案】（中優先度）

### #1 `CODEX_PROTECTED` と untracked DELETE の終了コード設計を明記

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:60`
- 現状: §2 で「非ゼロ終了にする」とだけ記載。§1 の `CODEX_PROTECTED` 検出時の終了コードは未記載
- 提案: 終了コードの設計を明記する。例:
  - `0`: 正常終了（CODEX_PROTECTED ありでも apply 成功）
  - `1`: untracked DELETE が存在し `--force-delete-untracked` なしで一部スキップ
  - `2`: 致命的エラー（source/dest 不在、manifest 異常等、現行と同じ）
  - CODEX_PROTECTED は情報表示のみで終了コードに影響しない（保護は正常動作）
- 期待効果: CI/スクリプトからの呼び出し時に終了コードで状態を判別可能になる

### #2 `_is_codex_artifact()` に明示的な個別パスリストを追加する設計を検討

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:40-41`
- 現状: パターンマッチ（`*codex*`）のみで保護対象を判定
- 提案: パターンに加えて `CODEX_PROTECTED_PATHS` のような明示的パスリストも持たせる。`001_sync_claude_md_mirror_gap.md` のようにパターンに一致しないが保護が必要なファイルを個別登録できるようにする。これにより §6 のリネーム問題を回避できる
- 期待効果: パターン命名規約に従わない既存ファイルの保護が容易になる

### #3 `AGENTS.md` への導線追加は最小限で十分

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:93-100`
- 現状: §5 で「既存の Startup Instruction Discovery Rule と Local Documentation Compliance Rule を補強する」と記載
- 提案: `AGENTS.md` の L4（`docs/claude-md-sync.md` への参照）は既に存在する。これに「mirror mode 実行前に `docs/codex-operation-knowledge.md` §12 の保護ルールも確認すること」の1文を追加するだけで十分。Execution Change Rule（L57-64）は既に汎用的に破壊的操作の事前説明を義務化しており、mirror mode 固有の記述を追加する必要はない
- 期待効果: `AGENTS.md` の肥大化を避けつつ導線を確保

### #4 テスト計画に `--force-delete-untracked` 付きの実行テストを追加

- 箇所: `20260501_185114_codex_mirror_sync_protection_plan.md:118-131`
- 現状: 検証項目に `--force-delete-untracked` なしで削除されないことの確認はあるが、フラグありで削除されることの確認がない
- 提案: `--force-delete-untracked` 付きで untracked ファイルが削除されるポジティブテストを追加する
- 期待効果: フラグの正常系と異常系の両方をカバー

---

## 【ソースコード・仕組み側への波及】

### `collect_markdown()` の excludes が source/dest 共通である設計上の問題

- 対象: `scripts/sync_claude_md.py:371-372`
- 理由: `DEFAULT_EXCLUDES` は「Codex 側だけで持つ文書」を意図しているが、`collect_markdown()` は source と dest に同一の excludes を適用する。今回の `docs/reviews/*codex*` 追加により、Claude Code 側の `*codex*` ファイルも除外される副作用がある。この設計は今回の要件に対して構造的に不適合
- 推奨対応: 短期的には `DEFAULT_EXCLUDES` への `*codex*` 追加を見送り、`CODEX_PROTECTED` のみで保護する。中期的には `source_excludes` と `dest_excludes` を分離する設計変更を検討する
- 検証方法: `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` を追加した状態で `collect_markdown(source_root, ...)` の戻り値に `045_mr_codex_handoff_wrong_file.md` が含まれないことを確認する（含まれない = 副作用あり）

---

## 【修正文案】

### 計画 §1 への追記

```markdown
# before
予定する変更:

- `DEFAULT_EXCLUDES` に `docs/reviews/*codex*` と `docs/reviews/*_codex_*` を追加する。

# after
予定する変更:

- `CODEX_ARTIFACT_PATTERNS` を追加する。
- `_is_codex_artifact(path)` を追加する。
- mirror 計画時、dest-only かつ Codex 成果物に該当するファイルは `DELETE` ではなく `CODEX_PROTECTED` に分類する。
- mirror 出力の集計に `CODEX_PROTECTED` を含める（`summarize_rows()` の `ordered_statuses` に追加）。
- `--apply` 時に `CODEX_PROTECTED` は削除しない。

注意: `DEFAULT_EXCLUDES` への `docs/reviews/*codex*` 追加は行わない。
`DEFAULT_EXCLUDES` は `collect_markdown()` で source 側にも適用されるため、Claude Code 側の `*codex*` ファイル（例: `045_mr_codex_handoff_wrong_file.md`）が収集されなくなり、mirror の MODIFY 判定に入らなくなる副作用がある。
保護は `_is_codex_artifact()` による `CODEX_PROTECTED` 分類のみで実現する。
```

### 計画 §6 の修正

```markdown
# before
計画上の推奨:

- リネームを第一候補にする。
- ただし過去レビュー番号や参照があるため、実装前に `rg "001_sync_claude_md_mirror_gap"` で参照を確認する。
- 参照が多い場合は個別除外に切り替える。

# after
計画上の推奨:

- `_is_codex_artifact()` に個別パスリスト `CODEX_PROTECTED_PATHS` を追加し、`001_sync_claude_md_mirror_gap.md` を登録する。
- リネームは採番衝突リスク（`001_monitor_backfill_duplicate_workflow.md` が既存）があるため見送る。
- `DEFAULT_EXCLUDES` への個別追加も source 側への副作用があるため見送る。
```

---

## 【推奨検証（Step 8）】

### 正本帰属チェック（8a）

- 全ての推奨は Codex 側ファイル（`scripts/sync_claude_md.py`, `docs/claude-md-sync.md`, `docs/codex-operation-knowledge.md`, `AGENTS.md`）に対するものであり、Claude Code 側のファイルへの修正は含まない
- 計画 MD は Codex 側リポジトリに格納されるため正本帰属に問題なし
- 本レビュー自体は Claude Code 側の `docs/reviews/` に格納され、048 レビュー結果との連続性を維持

### 上位ルール整合性（8b）

- CLAUDE.md §破壊的操作: `CODEX_PROTECTED` ガードは破壊的操作の dry-run 先行原則と整合する
- AGENTS.md §Execution Change Rule: mirror mode の保護ガード追加は既存ワークフローの変更であり、実装時に変更内容を明示する義務がある。計画がこの義務を満たしている
- 推奨 #1（`DEFAULT_EXCLUDES` への追加見送り）は CLAUDE.md/AGENTS.md いずれとも矛盾しない

### 副作用シミュレーション（8c）

**(i) 単体副作用**:
- `DEFAULT_EXCLUDES` への追加を見送る推奨により、Codex 独自の `*codex*` ファイルが `collect_markdown()` で収集される。しかしこれらは `plan_mirror()` の dest-only 分岐で `CODEX_PROTECTED` に分類されるため、削除されない。dry-run 出力に CODEX_PROTECTED 行が表示されることで、保護が機能していることの確認が容易になる
- `CODEX_PROTECTED_PATHS` の個別パスリスト追加により、パターン命名規約に従わないファイルも保護できるが、リストのメンテナンスコストが発生する。ただし対象は少数（現時点で1件）のため実害は軽微

**(ii) クロスルール競合**:
- `DEFAULT_EXCLUDES` 据え置きにより、既存の除外パターン（`docs/codex-*.md` 等）との競合は発生しない
- `_is_codex_artifact()` と `DEFAULT_EXCLUDES` の責務が明確に分離される（`DEFAULT_EXCLUDES` = Codex 側専用文書の収集スキップ、`_is_codex_artifact()` = mirror 時の Codex 独自成果物の削除保護）

**(iii) 状態依存シナリオ**:
- mirror mode は状態を持たないため、状態依存の問題は発生しない

### 事後確認事項（8d）

1. 推奨実施後に mirror mode を dry-run 実行し、Claude Code 側の `045_mr_codex_handoff_wrong_file.md` が source 側で収集され、MODIFY/unchanged として正常に処理されることを確認
2. `001_sync_claude_md_mirror_gap.md` が `CODEX_PROTECTED` として表示されることを確認（`CODEX_PROTECTED_PATHS` による保護）
3. 新規 Codex 独自ファイル作成時に命名規約（`*codex*`）が遵守されているか確認
4. untracked な Codex 独自ファイルが `--apply` で削除されないことを確認

---

## 【確認できなかった事項】

- `20260428_codex_cleanup_workspace_review.md` が tracked か untracked かの確認（git status を実行していない）
- Codex 側の `tests/` ディレクトリの現状構成（テストフレームワークの有無、既存テストの存在）
- `docs/codex-parallel-operation-policy.md` に mirror sync 関連の既存記述があるかの確認
- `scripts/sync_markdown_mirror.py`（001 レビューで言及された別スクリプト）の現存状態と計画への影響

---

## 提出 MD §レビューしてほしい観点への回答

### 1. 048 の5指摘に対して計画が過不足なく対応しているか

**概ね過不足なし。** 5件全てに対応する実装方針が記載されている。ただし以下の不足がある:
- `summarize_rows()` への `CODEX_PROTECTED` 追加が計画本文に明記されていない（重大指摘 #3）
- untracked 判定の実装方法が未定義（重大指摘 #5）

### 2. `DEFAULT_EXCLUDES` と `CODEX_PROTECTED` の二重防御は妥当か

**二重防御の考え方は妥当だが、`DEFAULT_EXCLUDES` への `docs/reviews/*codex*` 追加は副作用がある。** `collect_markdown()` が source 側にも excludes を適用するため、Claude Code 側の `*codex*` ファイルが収集されなくなる（重大指摘 #1）。`CODEX_PROTECTED` のみでの保護に統一することを推奨する。

### 3. untracked 削除を fail closed する仕様は妥当か

**妥当。** untracked ファイルの削除は不可逆操作であり、CLAUDE.md §破壊的操作の原則に照らしても明示フラグを要求するのは正しい。ただし終了コードの設計と git tracking 確認の実装方法を計画に明記すべき（改善提案 #1、重大指摘 #5）。

### 4. `001_sync_claude_md_mirror_gap.md` はリネームと個別除外のどちらか

**どちらでもなく、`_is_codex_artifact()` に個別パスリストを追加する方法を推奨。** リネームは採番衝突リスクがあり（重大指摘 #2）、`DEFAULT_EXCLUDES` 個別追加は source 側への副作用がある（ただしこのファイルは Claude Code 側に存在しないため実害なし）。最も安全なのはコード側の `CODEX_PROTECTED_PATHS` リストへの追加。

### 5. `AGENTS.md` への導線追加は必要か

**最小限の導線のみ必要。** 既存の L4 に「mirror mode 実行前に §12 も確認」の1文を追加すれば十分。Execution Change Rule の補強は不要（改善提案 #3）。

### 6. テスト計画に不足がないか

**2点不足あり。**
- Claude Code 側の `*codex*` ファイルが収集される（または除外される）ことの確認（重大指摘 #4、ただし #1 の推奨に従えば不要）
- `--force-delete-untracked` 付きの正常系テスト（改善提案 #4）
