# レビュースキル共通: レビュー結果の一元管理と識別性の改善

**作成日時**: 2026-04-29 18:00 JST（18:30 更新）
**対象ファイル**: `skills/code-reviewer.md`（334行）、`skills/md-reviewer.md`（633行）、commit 557e15f 時点
**目的**: (A) Pattern 2 のレビュー結果が `docs/reviews/` に残らない問題を修正 (B) ファイル名からレビュー種別（コード/MD）を識別可能にする

---

## 問題1: Pattern 2 のレビューが docs/reviews/ に残らない

### 現状

| スキル | パターン | 出力先 | `docs/reviews/` に残るか |
|---|---|---|---|
| code-reviewer | 1（新規コード） | `docs/reviews/NNN_<slug>.md` | **○** |
| code-reviewer | 2（改修プラン） | `docs/plans/*.md` 末尾に追記のみ | **✕** ← 問題 |
| code-reviewer | 3（ad-hoc） | `docs/reviews/NNN_<slug>.md` | **○** |
| md-reviewer | 1（作成MD） | `docs/reviews/NNN_<slug>.md` | **○** |
| md-reviewer | 2（誤読ミス） | `docs/reviews/` 原則作成 / ユーザー指示で既存MDに追記 | **△**（追記指示時のみ残らない） |
| md-reviewer | 3（ad-hoc） | `docs/reviews/NNN_<slug>.md` | **○** |

### 影響

- `docs/reviews/` を一覧しても code-reviewer Pattern 2 のレビューが見えない（現在 **8件** が `docs/plans/` にのみ存在）
- レビュー履歴の一元管理ができず、過去レビューの横断検索・傾向分析が不完全
- プランMDがアーカイブ/削除されるとレビュー結果も消失する

---

## 問題2: docs/reviews/ のファイル名からレビュー種別が識別不可能

### 現状

`docs/reviews/` の命名は `NNN_<slug>.md` のみ。ファイル名だけでは:
- code-reviewer の出力か md-reviewer の出力か不明
- ファイルを開いて `レビュアー:` 行を読まないと判別不可能

---

## 修正方針

### 方針A: レビュー本体を docs/reviews/ に、プランMDにポインタ

code-reviewer Pattern 2 の出力先を**逆転**する:
- **レビュー本体**: `docs/reviews/` に新規作成（正本）
- **プランMD**: ポインタ（サマリー＋reviewsへの参照）を末尾に追記

md-reviewer Pattern 2 で既存MDへ追記する場合も同様に、**本体は `docs/reviews/`、追記先MDにはポインタのみ**とする。

#### 採用理由

- `docs/reviews/` が全レビューの正本所在地として一元化される
- プランMDにはポインタがあるので、改修実装者もレビュー内容を辿れる
- プランMDのアーカイブ/削除でレビュー結果が消失しない

#### プランMDに追記するポインタのフォーマット

```markdown
---

## レビュー追記: YYYY-MM-DD HH:MM JST — code-reviewer

→ `docs/reviews/NNN_cr_<slug>.md`
```

### 方針B: ファイル名にレビュー種別プレフィックスを追加

`docs/reviews/` の命名規則を変更:

| 種別 | 命名 | 例 |
|---|---|---|
| code-reviewer | `NNN_cr_<slug>.md` | `029_cr_tdnet_load_parallel.md` |
| md-reviewer | `NNN_mr_<slug>.md` | `030_mr_zaraba_md_update.md` |

- `cr` = code review、`mr` = markdown review
- 連番 `NNN` のルールは変更なし（001から空き最若番、3桁ゼロ埋め）
- 既存ファイルの遡及リネームは不要

---

## 具体的な修正内容

### 修正1: code-reviewer.md — Pattern 2 の出力ルール変更

**箇所**: `skills/code-reviewer.md:91-104`

**before**:

```markdown
**出力**:
- **新規 MD を作らない**。`docs/reviews/` にも書かない
- 提示された**プラン MD に追記する**（`docs/plans/YYYYMMDD_HHMMSS_<slug>.md` の末尾にセクション追加）
- 追記セクションは次の形式で始める:

  ```markdown
  ---

  ## レビュー追記: YYYY-MM-DD HH:MM JST — code-reviewer

  （以下、下記「出力スタイル」テンプレートに従った内容）
  ```

- 同一プランに対して複数回レビューが走る場合は、既存の「レビュー追記」セクションの下にさらに日時付きで追加（**過去の追記は消さない・上書きしない**）
```

**after**:

```markdown
**出力**（2段階）:

**① docs/reviews/ にレビュー本体を作成**:
- `docs/reviews/NNN_cr_<slug>.md` を新規作成（採番ルール・出力テンプレートはパターン1と同一）
- **レビュー本体の正本はこちら**

**② プランMDにポインタを追記**:
- 提示された**プラン MD の末尾**にレビューMDへのポインタを追記する
- 追記セクションは次の形式:

  ```markdown
  ---

  ## レビュー追記: YYYY-MM-DD HH:MM JST — code-reviewer

  → `docs/reviews/NNN_cr_<slug>.md`
  ```

- 同一プランに対して複数回レビューが走る場合は、既存の追記の下にさらに日時付きで追加（**過去の追記は消さない・上書きしない**）
```

### 修正2: code-reviewer.md — 全パターンの命名規則に `cr_` プレフィックス追加

**箇所**: `skills/code-reviewer.md:79-83`（Pattern 1 の出力先）、`skills/code-reviewer.md:148-152`（Pattern 3 の出力先）

**before**:
```markdown
- 保存先: `docs/reviews/NNN_<slug>.md`
  - `NNN` は `docs/reviews/` 内の既存ファイルから見て**空いている最若番を 001 から連番**で採番（3 桁ゼロ埋め）。新規作成時に必ず既存一覧を確認して衝突を避ける
  - `slug` はレビュー対象を表す短い英小文字スラッグ（例: `earnings_batch_rerun`）
  - 例: `docs/reviews/001_earnings_batch_rerun.md`, `docs/reviews/002_tdnet_load_parallel.md`
```

**after**:
```markdown
- 保存先: `docs/reviews/NNN_cr_<slug>.md`
  - `NNN` は `docs/reviews/` 内の既存ファイルから見て**空いている最若番を 001 から連番**で採番（3 桁ゼロ埋め）。新規作成時に必ず既存一覧を確認して衝突を避ける
  - `cr_` はコードレビューを示すプレフィックス（md-reviewer は `mr_`）
  - `slug` はレビュー対象を表す短い英小文字スラッグ（例: `earnings_batch_rerun`）
  - 例: `docs/reviews/029_cr_earnings_batch_rerun.md`, `docs/reviews/030_cr_tdnet_load_parallel.md`
```

### 修正3: code-reviewer.md — 「呼び出し時にエージェントが最初にやること」更新

**箇所**: `skills/code-reviewer.md:293-294`

**before**:
```markdown
   - パターン 1: `docs/reviews/` を確認し、空いている最若番で `docs/reviews/NNN_<slug>.md` を決定（001 から連番）
   - パターン 2: 提示された `docs/plans/*.md` の末尾に追記（新規 MD は作らない）
```

**after**:
```markdown
   - パターン 1: `docs/reviews/` を確認し、空いている最若番で `docs/reviews/NNN_cr_<slug>.md` を決定（001 から連番）
   - パターン 2: `docs/reviews/NNN_cr_<slug>.md` にレビュー本体を作成 ＋ 提示された `docs/plans/*.md` の末尾にポインタ追記
```

### 修正4: md-reviewer.md — 全パターンの命名規則に `mr_` プレフィックス追加

**箇所**: `skills/md-reviewer.md:96-101`（Pattern 1）、`skills/md-reviewer.md:113`（Pattern 2）、`skills/md-reviewer.md:143`（Pattern 3）

**before**:
```markdown
- 保存先: `docs/reviews/NNN_<slug>.md`
```

**after**:
```markdown
- 保存先: `docs/reviews/NNN_mr_<slug>.md`
```

### 修正5: md-reviewer.md — Pattern 2 の追記指示時もレビュー本体を reviews に残す

**箇所**: `skills/md-reviewer.md:112-124`

**before**:
```markdown
**出力**:
- 原則として**新規レビュー MD を作成**: `docs/reviews/NNN_<slug>.md`
- ただし、ユーザーが特定の既存 MD への追記を明示した場合は、新規 MD を作らず、その MD の末尾に追記する:
```

**after**:
```markdown
**出力**（2段階）:

**① docs/reviews/ にレビュー本体を作成**（常に実行）:
- `docs/reviews/NNN_mr_<slug>.md` を新規作成（**レビュー本体の正本**）

**② 追記先MDにポインタを追記**（ユーザーが既存MDへの追記を明示した場合のみ）:
- 指定されたMDの末尾にレビューMDへのポインタを追記する:
```

### 修正6: md-reviewer.md — 「呼び出し時にエージェントが最初にやること」更新

**箇所**: `skills/md-reviewer.md:550-553`

**before**:
```markdown
   - パターン 1 / 3: `docs/reviews/` を確認し、空いている最若番で `docs/reviews/NNN_<slug>.md` を決定（001 から連番）
   - パターン 2（既存 MD 追記指示あり）: 指定された MD の末尾に追記（新規 MD は作らない）
   - パターン 2（追記指示なし）: `docs/reviews/NNN_<slug>.md` に新規作成
```

**after**:
```markdown
   - パターン 1 / 3: `docs/reviews/` を確認し、空いている最若番で `docs/reviews/NNN_mr_<slug>.md` を決定（001 から連番）
   - パターン 2: `docs/reviews/NNN_mr_<slug>.md` にレビュー本体を作成（常に）。ユーザーが既存MDへの追記を明示した場合は、追加でその MD の末尾にポインタを追記
```

---

## レビュー指摘の取り込み（2026-04-29 20:30 JST）

`docs/reviews/029_cr_review_trail_output_reversal.md` の指摘を以下の通り取り込み済み:

### 重大な指摘

| # | 内容 | 対応 |
|---|------|------|
| #1 | code-reviewer.md 起動方式セクション L19,25 のファイル名パターンに `cr_` が未付与 | code-reviewer.md 修正時に起動方式セクションも `NNN_cr_<slug>.md` に統一済み |
| #2 | md-reviewer.md 起動方式セクション L19 のファイル名パターンに `mr_` が未付与 | 修正4 のスコープに L19（修正4-1）を追加し `NNN_mr_<slug>.md` に統一済み |
| #3 | md-reviewer Pattern 2 のポインタテンプレートが未定義 | 修正5 に md-reviewer 用ポインタテンプレート（`## AI可読性レビュー追記: YYYY-MM-DD HH:MM JST — md-reviewer` + `→ docs/reviews/NNN_mr_<slug>.md`）を追加済み |

### 改善提案

| # | 内容 | 対応 |
|---|------|------|
| #1 | code-reviewer.md L291 パターン判定がパターン3を含まない | code-reviewer.md 修正3 で「パターン 1 / 2 / 3 のいずれか」に修正済み |

### ポインタフォーマットの最小化

修正1（code-reviewer）・修正5（md-reviewer）のプランMDポインタから品質評価・主要リスクを削除し、リンクのみのフォーマットに統一済み。

---

## 修正対象外（スコープ外）

- 既存の `docs/reviews/` ファイルの遡及リネーム（`cr_` / `mr_` 付与） → 不要。新規作成分から適用
- 既存の8件（code-reviewer Pattern 2 で plans にのみ追記済み）の遡及ポインタMD作成 → 必要なら別途タスク化
- 004-1 findings log のフォーマット変更 → 不要

---

## 検証

1. code-reviewer Pattern 2 を1回実行し、`docs/reviews/NNN_cr_<slug>.md` に本体が作成され、プランMDにポインタが追記されることを確認
2. md-reviewer Pattern 1 を1回実行し、`docs/reviews/NNN_mr_<slug>.md` にファイル名が正しく `mr_` 付きで作成されることを確認
3. `docs/reviews/` をファイル名一覧した時に `cr_` / `mr_` で種別が識別できることを確認
4. 連番が既存ファイルと衝突しないことを確認

---

## 関連ドキュメント

- 対象スキルMD: `skills/code-reviewer.md`、`skills/md-reviewer.md`
- 不備蓄積ログ: `docs/knowledges/tools/004-1_code_review_findings_log.md`

---

## レビュー追記: 2026-04-29 19:30 JST -- code-reviewer

レビュー本体: `docs/reviews/029_cr_review_trail_output_reversal.md`

### サマリー
- 品質評価: A -- 問題定義が明確で修正方針が簡潔・合理的。6箇所の修正内容は一貫
- 主要リスク:
  - 起動方式セクション（code-reviewer L19,25 / md-reviewer L19）のファイル名パターンが修正対象から漏れ、`cr_`/`mr_` なしの旧命名が残存
  - md-reviewer Pattern 2 のポインタテンプレートがプランに未定義
  - code-reviewer L291 のパターン判定がパターン3を含まない（既存問題）

> 詳細は上記レビューMDを参照
