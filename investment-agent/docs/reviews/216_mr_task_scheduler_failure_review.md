# MD AI可読性レビュー: Windows タスクスケジューラ起動失敗（エンコーディング事故）

- **日時**: 2026-05-20 (JST)
- **対象**: `docs/reviews/215_mr_task_scheduler_failure.md`（事故報告MD）、`C:\tmp\orders_launcher_0600.ps1`（問題発生スクリプト）、`docs/knowledges/tools/102_scheduled_execution.md`
- **パターン**: 2（誤読・ミス報告に対する改善レビュー）
- **レビュアー**: Claude (md-reviewer)
- **出力先**: `docs/reviews/216_mr_task_scheduler_failure_review.md`

---

## 【サマリー】

- **AI可読性評価**: A（提出MD 215 は事象・原因・修正経緯を過不足なく記述。AI誤読リスクは低い）
- **誤読リスク評価**: B（102_scheduled_execution.md は PS1 ファイルのエンコーディング要件を一切記載していない。同様のタスクスケジューラ利用時に同じ事故が再発しうる導線の欠陥がある）
- **重大指摘**: 2件
  1. `102_scheduled_execution.md` に PS1 エンコーディング要件の記載がなく、次回同じ手順で PS1 を Write すれば同一事故が再発する
  2. `004_coding_conventions.md` の `ファイルopen` ルールが Python ファイルに限定した記述で、PowerShell スクリプト（`.ps1`）の Write ツール出力エンコーディングが明示されていない

---

## 【Markdown 品質評価】

| 軸 | 評価 | 根拠 |
|----|------|------|
| Accuracy | A | 事象・根本原因・経緯・影響が実際のファイル内容（BOM なし UTF-8 → Shift-JIS 誤読 → parse エラー）と整合している。修正後スクリプトの内容（`Out-File -Encoding utf8` による BOM 付き書き直し、`Add-Content -Encoding utf8` でログ書き込み）を確認済み |
| Completeness | B | 事故報告として必要な事象・原因・修正・影響は揃っているが、「再発防止として期待すること」が質問形式で終わっており、AIへの具体的な指示・完了条件が未定義。これはレビュアー（本レビュー）への委任として意図的なので許容できるが、**102_scheduled_execution.md 側に追記が必要かという問いへの答えが本レビューの主要アウトプット** |
| Relevance | A | 不要な背景情報なし。事故に直接関係する情報のみ記述 |
| Actionability | B | 「知見MDへの記載要否の判断」を求めているが、どちらのMDに何を追記すべきかを本レビューが判定する必要がある |

---

## 【パターン 2: 原因分析】

### 1. 事象

`orders_launcher_0600.ps1` を Write ツールで作成 → BOM なし UTF-8 で書き出し → PowerShell 5.1 が Shift-JIS として読み込み → ヒアドキュメント終端 `'@` の前に日本語コメント末尾バイト列が混入 → `'@` が行頭に来ず parse エラー → 06:00 発火するが即終了 → `debug.log` に何も出ず → 07:26 ユーザーが未実行を報告。

### 1.5. AIの思考回路

AIは `Write` ツールでファイルを書き出す際、CLAUDE.md §6 の `ファイルopen` ルール（`encoding=` 必ず明示）を参照した可能性がある。しかしこのルールは Python の `open()` に特化した文脈で書かれており、Write ツール自体の出力エンコーディングについては言及がない。

102_scheduled_execution.md の「ランチャーは PS1 スクリプトファイルに分離し」という指示には、PS1 ファイルの作成方法・エンコーディング要件が未記載だった。

AIは「PS1 ファイルを作るには Write ツールを使えばよい」という暗黙の判断をしたが、Write ツールが BOM なし UTF-8 を出力することと、PowerShell 5.1 が BOM なし UTF-8 を Shift-JIS として読む問題を結びつけるルールが存在しなかった。

### 2. 直接原因

- `102_scheduled_execution.md` に「PS1 ファイルを Write ツールで作成する際は BOM 付き UTF-8 が必要」という記載がない（欠落）
- `CLAUDE.md §6 ファイルopen` ルールが Python `open()` のみを対象とし、Write ツール / PowerShell スクリプト出力には適用範囲が明示されていない（曖昧さ）

### 3. 根本原因

Write ツールの出力エンコーディングが「BOM なし UTF-8」であるという特性が、どのルールファイルにも明記されていない。PowerShell 5.1 環境特有の「BOM なし UTF-8 → Shift-JIS 誤読」問題は既知の Windows 運用上の制約だが、プロジェクト内の規約に取り込まれていなかった。

タスクスケジューラ利用時の知見（102）は「登録後の確認方法」「起動後の検証」に集中しており、「PS1 ファイル自体の品質保証」の観点が欠落していた。

### 4-1. MD上の原因

- `docs/knowledges/tools/102_scheduled_execution.md` 全体（38行）: 「ランチャーは PS1 スクリプトファイルに分離し」と指示しているが、ファイル作成方法・エンコーディングへの言及なし
- `CLAUDE.md §6` L118: `ファイルopen: encoding= 必ず明示。ソースコード/JSON/YAML/MD → utf-8` — PS1 と Write ツール出力が明示されていない
- `docs/knowledges/tools/004_coding_conventions.md §シェルからのPython実行`（L356-363）: PowerShell に関するルールが存在するが、PS1 ファイルのエンコーディングには言及なし

### 4-2. MD構成原因

「PS1 ファイルを作成する」という操作は以下の複数 MD にまたがるが、エンコーディング要件はどこにも記載されていない:
- CLAUDE.md §6 → 「ファイルopen」（Python `open()` のみ）
- 102_scheduled_execution.md → 「PS1 に分離する」（作成方法未定義）
- 004_coding_conventions.md §シェルからのPython実行 → PowerShell 言及あり、エンコーディング言及なし

どのルールも「PS1 ファイルを Write ツールで作るときの制約」に到達する導線を持っていない。

### 5. 再発防止

**102_scheduled_execution.md への追記: 必要（優先度高）**

根拠: タスクスケジューラを使う最初のステップは「PS1 スクリプトの作成」であり、このファイルを参照するAIが最初に直面する問題がエンコーディング。知見MDの責務として「PS1 作成時の要件」を記載するのが最も自然な到達導線。

**004_coding_conventions.md への追記: 必要（優先度中）**

根拠: 「Windows ローカルに `.ps1` を Write ツールで書き出す場合は BOM 付き UTF-8 を使う」はプロジェクト横断ルール。タスクスケジューラ以外の用途（CloudBuild 後のローカルスクリプト等）でも同様の問題が起きうる。004 §シェルからのPython実行 の直後または §その他 に追加するのが適切。

**CLAUDE.md への追記: 不要**

CLAUDE.md §6 の `ファイルopen` ルールは Python `open()` のエンコーディング規約として機能しており、そこに PS1 固有の例外を追加すると肥大化する。002 の詳細は 004 に委譲するポインタ構造が既にある（`→ その他の規約: 004_coding_conventions.md`）ので、004 側に追記するのみで十分。

### 6. 新規リスク

- 102 への追記で「Write ツールを使う」という手順を明記すると、今後 Write ツールのデフォルト動作が変わった際に記述が陳腐化するリスクがある。「Write ツールで作成した PS1 は BOM なし UTF-8 になる」ではなく「PS1 ファイルは BOM 付き UTF-8 で保存すること。PowerShell 5.1 は BOM なし UTF-8 を Shift-JIS として読む」という事実を主語にすると陳腐化しにくい

### 7. 対策スコープ

Q1: 知見MD（004等）への追記で対応できるか？ → **Yes**（102 + 004 の両方への追記で対応可能）
Q2: CLAUDE.md 既存原則の改訂が必要か？ → **No**（004 へのポインタが既にある）
Q3: CLAUDE.md 編集ポリシーを満たすか？ → 問いに至らず

---

## 【MD 構成リスク】

102_scheduled_execution.md の「ランチャーは PS1 スクリプトファイルに分離し」という記述は、**作成手順の完全性を保証していない**。AIは「分離する = Write ツールで書き出す = 完了」と判断しうるが、エンコーディング検証ステップが存在しない。

---

## 【指示優先順位・文脈境界】

CLAUDE.md §6 の `ファイルopen` ルールが Python `open()` の規約として機能している。Write ツール（Claude Code の内部ツール）の出力エンコーディングは別概念だが、AI はこの区別を明示されない限り混同する可能性がある。

Write ツールは常に BOM なし UTF-8 を出力するという仕様がどの規約ファイルにも記載されていない。この仕様を知らなければ、エンコーディング問題は予測できない。

---

## 【重大な指摘】

### 指摘 1: 102_scheduled_execution.md にPS1エンコーディング要件が欠落

| 項目 | 内容 |
|------|------|
| **箇所** | `docs/knowledges/tools/102_scheduled_execution.md` 全体（特に「Windows タスクスケジューラ 原則 → すべきこと」節） |
| **問題** | PS1 ファイル作成時の BOM 付き UTF-8 要件が記載されていない |
| **誤読パターン** | AIが次回タスクスケジューラ設定時に102を参照しても、PS1を Write ツールで生成するだけでエンコーディングを考慮しない |
| **トリガー条件** | 「タスクスケジューラで PS1 を実行する」という設定作業が発生した時 |
| **影響** | PS1 が BOM なし UTF-8 で作成され、PowerShell 5.1 が Shift-JIS として読んで parse エラー → タスクが無音で失敗 |
| **根拠** | 今回の事故経緯（215_mr_task_scheduler_failure.md §経緯）と `orders_launcher_0600.ps1` の内容確認（修正後は `Out-File -Encoding utf8` BOM付きで正常動作）から [検証済み] |
| **推奨対応** | 102_scheduled_execution.md の「Windows タスクスケジューラ 原則 → すべきこと」に追記: `PS1 ファイルは BOM 付き UTF-8 で保存する（PowerShell 5.1 は BOM なし UTF-8 を Shift-JIS として読む。Write ツールで生成した場合は Out-File -Encoding utf8 で上書きすること）` |
| **MD修正で足りるか** | Yes（構造的なガードとしては `schtasks /run` 前に PS1 テスト実行するチェックステップを追加することで、parse エラーを事前検知できる。これを追記するとさらに防御的） |

### 指摘 2: 004_coding_conventions.md の「ファイルopen」ルールが Write ツール出力エンコーディングをカバーしていない

| 項目 | 内容 |
|------|------|
| **箇所** | `CLAUDE.md §6` L118（委譲先: `004_coding_conventions.md §その他`） |
| **問題** | 「ファイルopen: encoding= 必ず明示」は Python `open()` の規約。Write ツールで `.ps1` を書き出す際のエンコーディング要件が未定義 |
| **誤読パターン** | AIが「ファイルopen の encoding= ルールに従っている → 規約を守っている」と判断し、Write ツール出力の BOM 問題を見落とす |
| **トリガー条件** | Windows 環境で PowerShell スクリプトを Write ツールで生成する任意のタスク |
| **影響** | PS1 に限らず、PowerShell が読む設定ファイル等で同様のエンコーディング問題が発生しうる |
| **根拠** | CLAUDE.md §6 L118 の記述が Python open() の文脈（直前行に PYTHONUTF8 の記述、対象が `ソースコード/JSON/YAML/MD`）で閉じており、PS1 が明示されていない [検証済み] |
| **推奨対応** | `004_coding_conventions.md §シェルからのPython実行` またはその直後の `§その他` に追記: `PS1 ファイルを Write ツールで作成した場合、BOM なし UTF-8 で書き出される。PowerShell 5.1 環境では BOM 付き UTF-8 が必要なため、作成後に Out-File -Encoding utf8 で上書きすること` |
| **MD修正で足りるか** | Yes |

---

## 【改善提案】

### 提案 1: 102_scheduled_execution.md に「PS1事前テスト実行」チェックを追加

| 項目 | 内容 |
|------|------|
| **箇所** | 102_scheduled_execution.md「すべきこと」節 |
| **現状** | 登録後の確認は `schtasks /query` で `Status: Ready` を確認のみ |
| **提案** | `schtasks /create` 後、`schtasks /run` + `schtasks /query` で最終 `Result` を確認するステップを追加。parse エラーは `Result: 0x1` 等で検知可能 |
| **期待効果** | エンコーディング問題に限らず、PS1 の任意の parse エラーや実行前提条件の欠落を登録直後に検知できる |

### 提案 2: 215_mr_task_scheduler_failure.md に `cat` コマンドでの確認方法を追記

| 項目 | 内容 |
|------|------|
| **箇所** | 215_mr_task_scheduler_failure.md §経緯 |
| **現状** | `cat -A` でエンコーディング崩れを発見（Step 5）とあるが、これは事後確認 |
| **提案** | 「PS1 作成直後に `Format-Hex` または `Get-Content -Encoding Byte` で先頭3バイトが `EF BB BF`（BOM）であることを確認する」手順を推奨として102に追記 |
| **期待効果** | タスク発火前にエンコーディングを検証できる |

---

## 【修正文案】

### 102_scheduled_execution.md「すべきこと」節への追記

**before:**
```
**すべきこと**
- `-WindowStyle Hidden` で PowerShell を起動する（...）
- ランチャーは PS1 スクリプトファイルに分離し `/F` に文字列を直書きしない
- 登録後は `schtasks /query` で `Status: Ready` と `Next Run Time` を確認する
- ログオンセッションが存在する前提で設計する（...）
```

**after:**
```
**すべきこと**
- `-WindowStyle Hidden` で PowerShell を起動する（...）
- ランチャーは PS1 スクリプトファイルに分離し `/F` に文字列を直書きしない
- **PS1 ファイルは BOM 付き UTF-8 で保存する**。PowerShell 5.1 は BOM なし UTF-8 を Shift-JIS として読むため、日本語コメントを含む PS1 が parse エラーになる。Write ツールで生成した場合は必ず `Out-File -Encoding utf8 -FilePath <path>` で上書きすること（`utf8` は PowerShell 5.1 では BOM 付き UTF-8）
- 登録後は `schtasks /run /TN "<task-name>"` でテスト起動し、`schtasks /query /TN "<task-name>" /FO LIST` で `Last Result: 0` を確認する
- 登録後は `schtasks /query` で `Status: Ready` と `Next Run Time` を確認する
- ログオンセッションが存在する前提で設計する（...）
```

### 004_coding_conventions.md §シェルからのPython実行 への追記

**before（末尾）:**
```
- **3行超は `$env:TEMP\xxx.py` に書き出してから実行**
```

**after（末尾に追加）:**
```
- **3行超は `$env:TEMP\xxx.py` に書き出してから実行**
- **PS1 ファイルのエンコーディング**: Write ツールは BOM なし UTF-8 で書き出す。PowerShell 5.1 は BOM なし UTF-8 を Shift-JIS として読むため、日本語を含む PS1 は parse エラーになる。Write ツールで PS1 を作成した後は `Out-File -Encoding utf8 -FilePath <path>` で上書きすること（MR-215）
```

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- PS1 エンコーディング要件の正本は `102_scheduled_execution.md`（タスクスケジューラ固有の操作手順）と `004_coding_conventions.md`（プロジェクト横断の PS1 操作規約）の両方に記載するのが適切
- memory への記載は不要（プロジェクト共通ルールは docs/ に書く）

### 8b. 上位ルール整合性チェック

- CLAUDE.md §1「追加してよいもの: 原則・禁止事項（1-3行）、知見MDへのポインタ（1行）」に照らし、CLAUDE.md 自体への追記は不要と判断した（004 へのポインタが既にある）
- 004_coding_conventions.md への追記は「原則・禁止事項を1行で。コード例の列挙は文書肥大化につき禁止」（`feedback_doc_no_examples.md`）に従い、コード例は最小限にとどめること
- 102_scheduled_execution.md の修正文案では「`Out-File -Encoding utf8`」の記述が1行コマンド例として機能しており、列挙ではないため許容範囲

### 8c. 副作用シミュレーション

**(i) 単体副作用**: 102 への「BOM 付き UTF-8」追記は、ASCII のみの PS1 や Shift-JIS で作成した既存 PS1 を「Write ツールで上書きする」場合にも適用される。ASCII 専用ファイルは BOM なし UTF-8 でも動作するため、ルールの適用範囲を「日本語コメントを含む場合」と限定しても良いが、「常に BOM 付き UTF-8 にする」という単純化の方がAIへの指示として明確であるため、過剰防御として許容する

**(ii) クロスルール競合**: CLAUDE.md §6 L118 の `ソースコード → utf-8` という記述と、今回の追記（PS1 は BOM 付き UTF-8）が競合するように見えるが、`ソースコード` は Python のソースコードを指しており、PS1 は独立カテゴリとして追記することで矛盾しない

**(iii) 状態依存**: テスト実行（`schtasks /run`）の追記は、一回限りタスク（`/sc ONCE`）で実行してしまうと本番実行になるリスクがある。修正文案ではこの点を考慮してテスト起動の表現を「登録直後の確認」として位置づけているが、`/sc ONCE` のタスクにはテスト起動しないよう注意書きを加えることが望ましい

**(iv) 再発防止策の実効性**: 「Write ツールで作成後に Out-File で上書き」は意志依存だが、これをフックや CI で強制する仕組みは現状のプロジェクト構成では過剰。現時点ではルール文書化 + チェックステップ追加（`schtasks /run` 確認）の組み合わせで再発防止策として十分と判断

### 8d. 事後確認事項の定義

- 004 と 102 への追記後、`orders_launcher_0600.ps1` の現行ファイル（`C:\tmp`）が実際に BOM 付き UTF-8 で保存されているか確認済み（`Out-File -Encoding utf8` で修正済みとのこと）
- 他の PS1 ファイルが BOM なし UTF-8 で存在しないか横展開確認: 不明（本レビューのスコープ外）

---

## 【確認できなかった事項】

- `C:\tmp\orders_launcher_0600.ps1` の修正後ファイルの BOM の有無を直接バイト検証できていない（PowerShell `Format-Hex` 等での確認は行っていない）。事故報告MD記載の「07:32 — `Out-File -Encoding utf8` で書き直し、テスト実行で `SCRIPT_START` を確認」を根拠として信頼している
- プロジェクト内に他の PS1 ファイルが存在するか、BOM なし UTF-8 で作成された PS1 が他にないかの横展開確認は未実施
- `schtasks /sc ONCE` のタスクに対してテスト実行（`schtasks /run`）すると本番動作してしまうリスクの評価（修正文案に注意書きを加えることを推奨するが、本レビューでは修正文案を提示する形にとどめた）
