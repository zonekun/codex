# MD AI可読性レビュー: 083 Codex分業ワークフロー — Codex側 Read/Write 多用事故

- 日時: 2026-05-16 20:00 JST
- 対象: `docs/knowledges/tools/083_codex_collaboration.md`
- パターン: 4（運用事故 — ルール欠陥の事故報告）
- レビュアー: Claude (md-reviewer)
- 出力先: `docs/reviews/187_mr_codex_handoff_write_violation.md`
- モード: 通常モード（Completeness / Actionability に B 判定あり）

---

## 【サマリー】

| 項目 | 評価 |
|------|------|
| AI可読性評価 | B |
| 誤読リスク評価 | B |
| 重大指摘 | 2件 |

主要リスク:
1. 083 の append-only ルールが Claude Code 専用語彙（`printf >>`）で記述され、Codex の行動を拘束する文言になっていない
2. 「原則不要」が抜け穴として機能 — Codex は「不要≠禁止」と解釈しうる
3. Codex 側ドキュメント（`docs/codex-to-claude-handoff.md`）にも "no Read/Edit required" とあるが "prohibited" ではない

---

## 【Markdown 品質評価】

| 軸 | 評価 | 根拠 |
|----|------|------|
| Accuracy | A | ブランチ構成・パス・コマンドは正確。現行運用と整合 |
| Completeness | B | Claude Code 側のルールは十分だが、Codex 側への拘束力が欠如。Writeへの言及ゼロ |
| Relevance | A | 不要な情報はなく、全セクションが運用に直結 |
| Actionability | B | Claude Code には実行可能だが、Codex にとって「何が禁止か」が明示されていない |

---

## 【パターン 4: 原因分析】

### 事象

Codex が `docs/codex-to-claude-handoff.md`（双方向伝言板）に TASK エントリを追記する際、`printf >>` ではなく Read → Write でファイル全体を書き換えた。append-only の設計意図（過去エントリの不変性保証）に反する操作。

### AIの思考回路（Codex側の推定判断チェーン）

1. タスク受領 → 伝言板にエントリ追記が必要と判断
2. `AGENTS.md` → `docs/codex-to-claude-handoff.md` を参照先として認識
3. `docs/codex-to-claude-handoff.md` L3: "All writes use `printf >>` (no Read/Edit required)" を読む
4. **分岐点**: "required" = 「必要ない」≠「禁止」と解釈。自分にとって自然な手段（Read → Write）を選択
5. Codex は OpenAI エージェントであり、`printf >>` はネイティブ手段ではない。ファイル操作のデフォルトが Read/Write であるため、禁止されていない限りそちらを使う
6. 083 (Claude Code 側知見MD) は Codex の参照パスに存在しない（`docs/knowledges/` は Claude Code → Codex 同期だが Codex が自発的に読むルーティングテーブルに 083 は含まれていない）

### 直接原因

1. **083 L25**: 「Read/Edit は原則不要」— 禁止ではなく「不要」。Codex にとって「不要だが許容」と読める
2. **Codex 側 `docs/codex-to-claude-handoff.md` L3**: "no Read/Edit required" — 同様に「不要」であって「禁止」ではない
3. **Write への言及ゼロ**: 083 は Read/Edit に触れるが Write は未言及。Codex の Write 操作はどのルールにも抵触しない状態

### 根本原因

1. **対象読者の暗黙限定**: 083 は Claude Code の知見ファイル（`docs/knowledges/tools/`）であり、暗黙的に Claude Code 向けに書かれている。Codex が読むことを想定した文言設計になっていない
2. **Codex のツール特性未考慮**: Claude Code は `printf >>` が自然だが、Codex（OpenAI エージェント）はファイル全体の Read/Write がデフォルト操作。この差異が 083 の執筆時に考慮されていない
3. **禁止と不要の区別欠如**: 「原則不要」は Claude Code に対しても曖昧だが、Claude Code は CLAUDE.md の append-only 文化を理解しているため暗黙的に禁止と解釈できた。Codex にはその文化的文脈がない
4. **Codex 側ドキュメントへの転記不足**: `docs/codex-to-claude-handoff.md`（Codex が直接読むファイル）にも "required" でなく "prohibited" にすべきだった

---

## 【AI 誤読リスク】

### リスク1: 「原則不要」の曖昧性（083 L25）

- **箇所**: `docs/knowledges/tools/083_codex_collaboration.md` L25
- **問題**: 「原則不要」は日本語として「普通は要らない（が例外あり）」。禁止の意図なら「禁止」と書くべき
- **誤読パターン**: Codex が「必要と判断すれば使ってよい」と解釈
- **トリガー**: 伝言板への追記タスク発生時
- **影響**: append-only 設計の形骸化、過去エントリ改変リスク

### リスク2: Write 操作への言及不在（083 全体）

- **箇所**: `docs/knowledges/tools/083_codex_collaboration.md` §追記方式 / §伝言板の運用ルール
- **問題**: Read/Edit は触れているが Write は未言及。Write による全体書き換えは Read+Edit より破壊的だが規制対象外
- **誤読パターン**: 「Read/Edit は原則不要と書いてある。Write は言及されていないから問題ない」
- **トリガー**: Codex がファイル内容を確認してから書き直すパターン
- **影響**: ファイル全体が書き換わり、過去エントリが意図せず改変される可能性

---

## 【MD 構成リスク】

### 構成問題1: 対象読者の非明示

083 は `docs/knowledges/tools/` に配置されており、Claude Code 向け知見ファイルの位置づけ。しかし §双方向伝言板 のルールは Codex にも適用される必要がある。対象読者が明示されておらず、「このルールは誰に対する制約か」が不明確。

### 構成問題2: Codex 側との二重管理

同じ情報が 083（Claude Code 側）と `docs/codex-to-claude-handoff.md`（Codex 側）に分散。正本がどちらか不明。ルール変更時に片方だけ更新される構造的リスク。

---

## 【指示優先順位・文脈境界】

### 文脈境界の問題

- **083 の位置**: Claude Code の知見MD（`docs/knowledges/tools/`）。Codex の `AGENTS.md` や `operation-knowledge.md` からの参照パスに含まれていない
- **Codex の参照チェーン**: `AGENTS.md` → `CLAUDE.md` → `docs/codex-to-claude-handoff.md`。083 への到達パスが存在しない
- **結果**: 083 に書かれたルールは Codex に到達しない。Codex が読む `docs/codex-to-claude-handoff.md` のみが拘束力を持つ

### 正本帰属

伝言板運用ルールの正本は 083 と `docs/codex-to-claude-handoff.md` のどちらか不明:
- 083 = Claude Code 側の包括的ドキュメント（ブランチ構成・取り込みフロー含む）
- `docs/codex-to-claude-handoff.md` = 伝言板ファイル自体のヘッダー（Format Rules）

**推奨**: 伝言板操作ルールの正本は `docs/codex-to-claude-handoff.md`（両者が読むファイル）に一元化し、083 はポインタのみとする。

---

## 【重大な指摘】

### 指摘1: 「原則不要」→「禁止」への文言強化

- **箇所**: `docs/knowledges/tools/083_codex_collaboration.md` L25
- **問題**: 「Read/Edit は原則不要」は禁止の意図を伝達できていない
- **誤読パターン**: 「不要≠禁止」→ 自分に自然な手段を選択
- **トリガー**: Codex / Claude Code いずれかが伝言板に追記する場面
- **影響**: append-only 設計の形骸化。過去エントリの意図せぬ改変
- **根拠**: 実際に Codex が Read/Write を選択した事実（本事故）
- **推奨対応**: 083 L25 を「Read/Edit/Write 禁止。`printf >>` 末尾追記のみ許可（Readはアクティブタスク参照時のみ例外許可）」に変更。同時に `docs/codex-to-claude-handoff.md` L3 を "All writes use `printf >>`. **Read/Edit/Write of the entire file is prohibited.** (Exception: Read is allowed only to check active task details.)" に変更
- **MD修正で足りるか**: Yes（文言強化で十分）

### 指摘2: Codex 向け制約の明示セクション追加

- **箇所**: `docs/knowledges/tools/083_codex_collaboration.md` — セクション不在
- **問題**: 083 全体が Claude Code 向けに書かれており、Codex の行動制約が明示されていない
- **誤読パターン**: Codex が 083 を読んでも「自分への指示」と認識しない
- **トリガー**: Codex が 083 を参照する場面（現状は参照パスがないため発火しにくいが、将来的にリスク）
- **影響**: Codex 固有の制約（Write 禁止、printf >> の代替手段提示等）が伝わらない
- **根拠**: Codex の `AGENTS.md` / `operation-knowledge.md` から 083 への参照パスが存在しない事実
- **推奨対応**: (A) `docs/codex-to-claude-handoff.md` の Format Rules セクションに禁止事項を明記（Codex が確実に読むファイル）。(B) 083 にも「Codex 側の制約」サブセクションを追加し、Codex のデフォルト手段（Read/Write）が禁止である旨を記載。(C) Codex の `operation-knowledge.md` §Common Principles に伝言板操作制約へのポインタを追加
- **MD修正で足りるか**: Yes（ドキュメント追記で対応可能）

---

## 【改善提案】

### 提案1: Codex 向けの printf >> 代替手段の明示

- **箇所**: `docs/codex-to-claude-handoff.md` Format Rules
- **現状**: `printf >>` と書いてあるが、Codex（OpenAI エージェント）にとって `printf >>` は必ずしもネイティブ操作ではない
- **提案**: Codex 向けにファイル末尾追記の具体的コマンド例を併記（PowerShell: `Add-Content` / bash: `printf >>` / Python: `open(..., 'a')`）
- **期待効果**: Codex が「printf >> の代わりに何を使えばよいか」で迷わない

### 提案2: 083 §伝言板の運用ルール L81 の拡充

- **箇所**: `docs/knowledges/tools/083_codex_collaboration.md` L81
- **現状**: 「Read が必要になるのはアクティブタスクの詳細を参照する時だけ」— Read の例外のみ言及
- **提案**: 「Write / Edit によるファイル全体の書き換えは常時禁止。ファイル内容の確認が必要な場合でも、追記操作自体は printf >> で行うこと」を追記
- **期待効果**: Read の例外許可と Write/Edit の絶対禁止の区別が明確になる

---

## 【ソースコード・仕組み側への波及】

本件は MD 修正で十分対応可能。ソースコード側のガード（hook / validator）は現時点では不要。

理由:
- 伝言板は git 管理されており、Write による全体書き換えがあっても `git diff` で検知可能
- Claude Code 側の取り込みフローで必ず内容確認が入る
- Codex の行動をプログラム的に制約する手段は現状存在しない（OpenAI のシステムプロンプトでの制御が限界）

---

## 【修正文案】

### Before (083 L25)

```markdown
全操作を `printf >>` 末尾追記で完結させる。Read/Edit は原則不要。
```

### After (083 L25)

```markdown
全操作を `printf >>` 末尾追記で完結させる。**Read/Edit/Write によるファイル全体操作は禁止**（Read はアクティブタスク詳細の参照時のみ例外許可）。この制約は Claude Code・Codex 双方に適用される。
```

### Before (docs/codex-to-claude-handoff.md L3)

```markdown
Append-only message board. All writes use `printf >>` (no Read/Edit required).
```

### After (docs/codex-to-claude-handoff.md L3)

```markdown
Append-only message board. All writes use `printf >>` or equivalent append command.
**Read/Edit/Write of the entire file is PROHIBITED.** (Exception: Read is allowed only to check active task details.)
Codex: use `Add-Content` (PowerShell) or `open(..., 'a')` (Python) — never rewrite the whole file.
```

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 伝言板操作ルールの正本: `docs/codex-to-claude-handoff.md`（両者が読むファイル）に一元化を推奨
- 083 は Claude Code 側の包括文書として位置づけ、伝言板操作の詳細は handoff ファイルへのポインタとする
- memory への複製: なし（問題なし）

### 8b. 上位ルール整合性チェック

- CLAUDE.md §4.3「新規規約の導入は文書化とアトミック」→ 083 + handoff ファイルの同時修正が必要（片方だけ更新は違反）
- CLAUDE.md §1 編集ポリシー → 本件は CLAUDE.md 改訂不要（知見 MD レベルで完結）

**記載先判定**:
- Q1: 知見MD（083）への追記で対応できるか？ → **Yes**。083 の文言修正 + Codex 側 handoff ファイルの修正で完結
- Q2: CLAUDE.md 改訂は必要か？ → **No**
- Q3: N/A

### 8c. 副作用シミュレーション

**(i) 単体副作用**: 「禁止」に変更しても、Read の例外許可が明記されているため、アクティブタスク確認フローは阻害されない。問題なし

**(ii) クロスルール競合**: 083 §伝言板の運用ルール L79「追記後は都度コミット＋プッシュ必須」との整合 → printf >> + commit + push の流れは変わらない。競合なし

**(iii) 状態依存シナリオ**: Codex が `printf >>` 相当の操作を知らない場合、代替手段が示されていないと操作不能になるリスク → 修正文案で `Add-Content` / `open(..., 'a')` を例示済み。対応済み

**(iv) 再発防止策の実効性**: 文言強化は意志依存型か？ → 部分的にYes（Codex が読むかは保証されない）。ただし `docs/codex-to-claude-handoff.md` は Codex の `AGENTS.md` から直接参照されるファイルであり、Codex の起動ルーティンで必ず読まれるため実効性は高い。追加の構造的強制（hook等）は現時点では過剰

### 8d. 事後確認事項

- 修正実施後、次回 Codex がタスク追記する際に Read/Write ではなく append 操作を使用しているか確認
- `docs/codex-to-claude-handoff.md` の git log で Write による全体書き換え（差分が大きい）が再発していないか監視

---

## 【確認できなかった事項】

1. Codex が実際に `docs/codex-to-claude-handoff.md` の L3 ルールを読んだ上で Read/Write を選択したのか、それとも L3 を読まずにデフォルト動作したのかは不明（Codex 側のログ未確認）
2. Codex の `operation-knowledge.md` から `docs/codex-to-claude-handoff.md` への参照は存在するが、Codex が起動時に Format Rules セクションまで確実に読んでいるかは未検証
3. 過去に同様の Write 操作が行われた回数（今回が初回か再発かの判定）は git log 未確認
