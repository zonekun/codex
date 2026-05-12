# MD AI可読性レビュー提出: Codex伝言の記載先ファイル誤り

- 日時: 2026-05-01 提出
- パターン: 2 (誤読・ミス報告に対する改善レビュー)

---

## 事象

2026-05-01、Windows側 Claude Code が Codex 宛の伝言（受注残高パイプライン Phase 6 品質検証・修正タスク）を `docs/handoff.md` に記載した（コミット `9182f75`）。

しかし Codex 宛の伝言の正規記載先は `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` である（`docs/knowledges/tools/083_codex_collaboration.md` §引継ぎメモ で定義）。

`docs/handoff.md` は **端末間（Windows ↔ Linux VM）の Claude Code 同士**の引き継ぎボードであり、Codex との分業チャネルではない。

## 関連ファイル

- `docs/handoff.md` — 端末間引き継ぎボード（誤記載先）
- `docs/knowledges/tools/083_codex_collaboration.md` — Codex分業ワークフロー知見。§引継ぎメモに正規パスが記載
- `CLAUDE.md` — §Codex引継ぎチェック でセッション開始時の確認義務を定義
- `docs/knowledges/tools/086_terminal_handoff.md` — 端末間引き継ぎ手順

## 影響

- Linux VM 側 Claude Code がリモートで `docs/handoff.md` を git pull した際に、Codex宛エントリを自分宛と誤認するリスク（`to: Codex` と書いてあるので誤処理は避けられるが、混乱の元）
- Codex側は `codex-to-claude-handoff.md` しか読まないため、伝言が届かない
- 実際にLinux VM側で「両方残す形でマージ」という不要な作業が発生した

## レビュー依頼

パターン2として以下を分析してほしい:
1. なぜ Claude Code が `docs/handoff.md` に Codex 伝言を書いたのか（MDのどの記述・構成が誤読を許したか）
2. `docs/handoff.md` と `codex-to-claude-handoff.md` の責務分担が AI に明確に伝わっているか
3. 083知見ファイル、CLAUDE.md、handoff.md の記述に改善余地があるか
4. 再発防止策

---

# MD AI可読性レビュー: Codex伝言の記載先ファイル誤り（handoff.md への誤記載）

- 日時: 2026-05-01 15:13 JST
- 対象: `docs/handoff.md`, `docs/knowledges/tools/083_codex_collaboration.md`, `CLAUDE.md`
- パターン: 2 (誤読・ミス報告に対する改善レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/045_mr_codex_handoff_wrong_file.md`

---

## 【サマリー】

- レビュー対象の要約: Claude Code が Codex 宛の伝言を端末間引き継ぎボード（`docs/handoff.md`）に書いた。正規の Codex 伝言先は `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`（083知見ファイルで定義）。
- AI可読性評価: **C** — Codex 向けの「送り出し」導線が CLAUDE.md に不在。handoff.md が排他的に Claude Code 端末間専用であるという制約が明示されていない。
- 誤読リスク評価: **C** — handoff.md の汎用的な名前と構成、および CLAUDE.md における2つの引き継ぎチャネルの近接記述が、AI に「handoff.md は全引き継ぎの統一ボード」と誤認させるリスクが高い。
- 主要リスク:
  - CLAUDE.md L11-13 で Codex と端末間引き継ぎが近接配置され、L13 の Codex パスは「受け取り」専用だが「送り出し」パスの案内が無い
  - handoff.md のタイトル「端末間引き継ぎボード」と説明文に「Claude Code 同士」の排他制約が無く、Codex 宛エントリが書かれても構文的に違和感が無い
  - 083知見ファイルが Codex→Claude Code 方向の引き継ぎメモのみ記載し、Claude Code→Codex 方向の伝言手段を定義していない

---

## 【Markdown 品質評価】

### Accuracy / 正確性
- `docs/handoff.md:1-4` — タイトル「端末間引き継ぎボード」、説明文「別端末のClaude Codeに作業を引き継ぐためのファイル」。「Claude Code に」とあるが、これは受け手の制約であって送り手の制約ではない。「Claude Code 同士の端末間引き継ぎ専用」「Codex 宛は不可」という排他制約は明示されていない。
- `083_codex_collaboration.md:17-21` — 引継ぎメモのパスは正確だが、§引継ぎメモの説明「Codex が作業結果を記載する」「Claude Code はこのメモを読んで取り込む」は Codex→Claude 方向のみ。Claude→Codex 方向の伝言チャネルが未定義。
- `CLAUDE.md:13` — Codex 引継ぎチェックのパスは `codex-to-claude-handoff.md`。ファイル名自体が方向性（Codex→Claude）を示唆しているが、逆方向（Claude→Codex）の伝言先は CLAUDE.md のどこにも記載されていない。

### Completeness / 完全性
- **致命的な欠落**: Claude Code が Codex にタスクを委譲したい場合の手順・出力先が、CLAUDE.md にも 083 にも handoff.md にも定義されていない。唯一存在するのは Codex→Claude の受け取りフロー（083 §取り込みフロー）のみ。
- handoff.md は「from/to」フィールドを持つが、to に書ける値の制約（Windows / Linux VM のみ等）が定義されていない。

### Relevance / 関連性
- CLAUDE.md L11 と L13 が近接しており、L11 は「Codex 分業ワークフロー → 083」、L13 は「Codex 引継ぎチェック: codex-to-claude-handoff.md」。L143-146 は「端末間の作業移管」で handoff.md を案内。これら3箇所が別セクションに分散しているため、AI が「Codex 伝言」と「端末間引き継ぎ」を同一チャネルと混同する余地がある。

### Actionability / 実行可能性
- AI が「Codex にタスクを渡したい」と判断した時点で、次のアクションが一意に決まらない。083 を読んでも「Claude Code→Codex」の送り出し手順が無い。最も近い「引き継ぎを書く」行為の導線は handoff.md（CLAUDE.md L145）なので、そこに書くのは AI にとって合理的な推論。

---

## 【AI 誤読リスク】

- **「引き継ぎ」の汎用性**: handoff.md のタイトル「端末間引き継ぎボード」と、CLAUDE.md L145 の「docs/handoff.md を確認」が、AI にとって「作業を他者に引き継ぐための汎用ボード」として認識される。既存エントリに `from: Codex / to: Claude Code` のエントリ（2026-04-28 JST）が存在しており、Codex が handoff.md の参加者であるという先行事例がある。この先行事例が AI の誤認を強化する。
- **「端末間」の解釈**: 「端末」が「Windows/Linux VM」に限定されるのか、「Codex 環境」も含むのかが不明確。Codex は別の実行環境であり、広義には「別端末」と解釈できる。

---

## 【MD 構成リスク】

- CLAUDE.md の §プロジェクト概要（L11, L13）に Codex 関連が2行あり、§端末間の作業移管（L143-146）に handoff.md 関連が3行ある。この2つのセクションは物理的に130行離れており、AI が両方を関連付けて「Codex と端末間引き継ぎは別チャネル」と認識することを期待するのは困難。
- handoff.md 自体に「Codex 宛は禁止」「to フィールドの許容値は Windows / Linux VM のみ」という否定制約が無い。既存エントリの from/to を見ると `Windows`, `Linux VM`, `Claude Code`, `Codex` が混在しており、構造的に Codex が排除されていない。

---

## 【指示優先順位・文脈境界】

- 引き継ぎチャネルが2つ存在する:
  1. `docs/handoff.md` — Claude Code 端末間（CLAUDE.md L143-146, 086知見ファイル）
  2. `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` — Codex↔Claude Code（CLAUDE.md L13, 083知見ファイル）
- この2チャネルの使い分け基準が CLAUDE.md に明記されていない。L13 は「受け取り」のみ定義し、L145 は「端末間引き継ぎ」と汎用的に記述。AI が「Codex にタスクを送る」場合にどちらを使うかの判断基準が欠落。
- 正本帰属: handoff.md の利用ルールの正本は 086 知見ファイル。Codex 分業の正本は 083 知見ファイル。しかし両者の境界（「handoff.md に Codex を書くな」「Codex 宛は codex-to-claude-handoff.md に書け」）はどちらの知見ファイルにも明記されていない。

---

## 【パターン 2 のみ: 誤読・ミス原因分析】

### 事象
コミット 9182f75 で、Claude Code が Codex 宛の Phase 6 品質検証タスク伝言を `docs/handoff.md` に `to: Codex` として記載。正規の送り先は `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md`。Linux VM 側 Claude Code が git pull 時に Codex 宛エントリを確認し、不要なマージ作業が発生した。

### 読み手がどう解釈した可能性があるか
1. AI は「Codex にタスクを渡す」意図を持った
2. CLAUDE.md L13 を確認 → パスは `codex-to-claude-handoff.md` だが、これは「セッション開始時に未処理エントリがあれば取り込み作業を提案」と記載されており、**受け取り専用**の文脈。Claude Code が書き込む場所としては認識しにくい
3. 083知見ファイルを確認 → §引継ぎメモは「Codex が作業結果を記載する」「Claude Code はこのメモを読む」と記載 → やはり受け取り専用
4. 「他者にタスクを引き継ぐ」→ CLAUDE.md L145 `docs/handoff.md` → handoff.md は `from/to` 構造を持ち、`to: Codex` と書けば機能する → ここに書いた
5. handoff.md に既存の `from: Codex / to: Claude Code` エントリがあることで、「Codex も handoff.md の参加者」という先例が確認でき、誤認が強化された

### 直接原因
- **Claude Code→Codex 方向の伝言チャネルが未定義**: 083知見ファイルは Codex→Claude 方向のみ定義。逆方向の手順・出力先が存在しない。
- **handoff.md に排他制約が不在**: `to` フィールドの許容値が定義されておらず、`to: Codex` が構文的に有効。

### 根本原因
- 083 知見ファイルが Codex 分業を「Codex が作業→Claude Code が取り込み」の一方向フローとして設計されており、Claude Code が Codex にタスクを委譲する逆方向フローが想定されていなかった。プロジェクト運用が進み、Claude Code から Codex にタスクを送る場面が発生したが、導線が用意されていなかった。
- handoff.md と codex-to-claude-handoff.md の責務分担が暗黙的で、明文化されていない。

### 誤読を許した MD 上の原因

1. **`CLAUDE.md:13`** — 「セッション開始時、codex-to-claude-handoff.md に未処理エントリがあれば取り込み作業を提案すること」。この記述は Read-only な行動（読んで報告）のみ定義。Claude Code が Codex に書き込むための導線がこの行からは辿れない。
2. **`083_codex_collaboration.md:17-21`** — §引継ぎメモが Codex→Claude 方向専用。§取り込みフローも同方向。Claude Code→Codex の送り出し手順が無い。
3. **`docs/handoff.md:1-4`** — 「別端末のClaude Codeに作業を引き継ぐためのファイル」の「Claude Code に」が受け取り側の記述であり、「Claude Code 同士の専用チャネル」とは読み取れない。タイトル「端末間引き継ぎボード」も排他制約を含まない。
4. **`docs/handoff.md:42-44`** — 既存エントリ `from: Codex / to: Claude Code` が Codex の handoff.md 参加を実証しており、AI が「Codex も handoff.md のユーザー」と学習する根拠になっている。
5. **`CLAUDE.md:143-146`** — §端末間の作業移管が handoff.md を「他の端末への引き継ぎ先」として案内。「端末間」に Codex が含まれるかの限定が無い。

### 再発防止の方向性

1. **handoff.md の冒頭に排他制約を追記**: 「Claude Code 端末間（Windows/Linux VM）専用。Codex 宛の伝言は禁止。Codex には `C:\Users\zonekun\Documents\codex\...` を使う」
2. **083知見ファイルに Claude Code→Codex 方向のフローを追加**: §伝言フロー（Claude Code→Codex）を新設し、書き込み先パスを明記
3. **CLAUDE.md L13 の補足**: 受け取りだけでなく送り出しの導線も明記（083 へのポインタで十分）
4. **handoff.md の既存 Codex エントリの扱い**: 既存の `from: Codex` エントリは運用上の先例として混乱の元。今後は codex-to-claude-handoff.md に統一し、handoff.md からは Codex エントリを削除するか、移行注記を付ける

---

## 【重大な指摘】（即修正）

### #1 Claude Code→Codex 方向の伝言チャネルが未定義
- 箇所: `docs/knowledges/tools/083_codex_collaboration.md:17-35`
- 問題: §引継ぎメモと§取り込みフローが Codex→Claude Code の一方向のみ。逆方向（Claude Code が Codex にタスクを送る）の手順・出力先が存在しない。
- AI の誤読パターン: 「Codex にタスクを送りたい」→ 083 に導線なし → 汎用引き継ぎボード（handoff.md）にフォールバック
- トリガー: Claude Code が Codex にタスク委譲を行う場面（今回の Phase 6 委譲のように）
- 影響: 伝言が Codex に届かない + 端末間引き継ぎボードの汚染 + 他端末の Claude Code が Codex 宛エントリを自分宛と誤認するリスク
- 根拠: 083 §引継ぎメモ「Codex が作業結果を記載する」= Codex が writer、Claude が reader。逆方向が未定義
- 推奨対応: 083 に「§伝言（Claude Code→Codex）」セクションを追加し、`codex-to-claude-handoff.md` を双方向で使うか、別ファイル（`claude-to-codex-handoff.md`）を定義する。ファイル名が `codex-to-claude-` なので、逆方向は別ファイルが自然。ユーザー判断事項。
- MD 修正だけで足りるか: 足りる。083 に逆方向フローを追加すれば導線は成立する。

### #2 handoff.md に排他制約が不在
- 箇所: `docs/handoff.md:1-4`
- 問題: 「別端末のClaude Codeに作業を引き継ぐためのファイル」は受け手が Claude Code であることを示すが、送り手の制約や `to` フィールドの許容値が未定義。Codex 宛エントリが構文的に受け入れられる。
- AI の誤読パターン: 「from/to に自由にエンティティ名を書ける」→ `to: Codex` が有効と判断
- トリガー: Codex にタスクを送りたい場面
- 影響: handoff.md に Codex 宛エントリが混入し、(a) Codex に届かない (b) 他端末の Claude Code が混乱
- 根拠: 既存エントリに `from: Codex` が存在し、Codex の参加が先例化
- 推奨対応: handoff.md の冒頭ルールセクションに「to/from は Claude Code 端末（Windows / Linux VM）に限定。Codex 宛は `docs/knowledges/tools/083_codex_collaboration.md` 参照」を追記
- MD 修正だけで足りるか: 足りる

### #3 CLAUDE.md の Codex 引き継ぎチェックが受け取り専用で送り出し導線が無い
- 箇所: `CLAUDE.md:13`
- 問題: 「未処理エントリがあればユーザーに取り込み作業を提案すること」は受け取り行動のみ。Claude Code が Codex にタスクを送る場合の行動導線が CLAUDE.md に不在。
- AI の誤読パターン: Codex 宛の送り出し導線を探す → CLAUDE.md に無い → 最も近い導線 L145 handoff.md にフォールバック
- トリガー: ユーザーが「Codex に渡して」「Codex に委譲」等を指示
- 影響: 送り先が不定で handoff.md に誤記載される
- 根拠: L13 は Read 行動のみ定義。Write 行動（伝言を書く）の導線は L11 の 083 ポインタ経由だが、083 自体にも逆方向フローが無い（指摘#1）
- 推奨対応: CLAUDE.md L13 の直後に「Codex へのタスク委譲手順は `docs/knowledges/tools/083_codex_collaboration.md` §伝言フロー を参照」を追記（083 に逆方向フロー追加後）。CLAUDE.md にはポインタのみで詳細は 083 に委譲。
- MD 修正だけで足りるか: 足りる（083 の修正と組み合わせ）

---

## 【改善提案】（中優先度）

### #1 handoff.md の既存 Codex エントリの整理
- 箇所: `docs/handoff.md:42-68`
- 現状: 2026-04-28 エントリが `from: Codex / to: Claude Code` として存在。これは Codex が handoff.md に書いた先例であり、AI が「Codex も handoff.md の参加者」と学習する根拠になっている。
- 提案: このエントリを codex-to-claude-handoff.md に移動し、handoff.md からは削除（または移動済み注記で残す）。以降 Codex 関連エントリは codex-to-claude-handoff.md に統一。
- 期待効果: handoff.md に Codex 参加の先例が無くなり、AI が「handoff.md は Claude Code 端末間専用」と学習しやすくなる。

### #2 CLAUDE.md の §端末間の作業移管 に否定制約を追加
- 箇所: `CLAUDE.md:143-147`
- 現状: 「端末間」が Windows/Linux VM に限定される旨が不明確。
- 提案: 「端末間 = Windows ↔ Linux VM の Claude Code 同士。Codex は端末間引き継ぎの対象外（083 参照）」を補足。
- 期待効果: 「端末間」の解釈範囲が限定され、Codex が除外されることが明確になる。

### #3 086知見ファイルに排他制約を追加
- 箇所: `docs/knowledges/tools/086_terminal_handoff.md:7-11`
- 現状: §引き継ぎボード の説明に Codex 除外が不在。
- 提案: 「Codex との分業は別チャネル（083 参照）。handoff.md は Claude Code 端末間専用」を追記。
- 期待効果: 086 から直接 handoff.md を操作する AI が Codex エントリを書くことを防止。

---

## 【ソースコード・仕組み側への波及】

MD 修正で十分対応可能。ソースコード側のガードは不要。handoff.md は人間・AI が手動編集するファイルであり、バリデーションスクリプトを設けるほどの頻度・影響度ではない。

---

## 【推奨検証（Step 8）】

### 正本帰属チェック（8a）
- 推奨#1: 083知見ファイルに逆方向フロー追加 → 083 が Codex 分業の正本なので適切。
- 推奨#2: handoff.md の冒頭に排他制約追加 → handoff.md 自身のルールセクション内なので適切。086知見ファイルが詳細手順の正本だが、排他制約は handoff.md 自体に書くのが自然（自己文書化）。086 にも追記する提案（改善提案#3）と合わせて整合。
- 推奨#3: CLAUDE.md にポインタ追加 → CLAUDE.md はポインタのみ方針（L3）と整合。詳細は 083 に委譲。
- 全推奨が正本帰属ルールに準拠。memory への書き込み推奨は含まない。

### 上位ルール整合性（8b）
- CLAUDE.md L3「方針・制約・禁止事項のみ記載。手順詳細は知見ファイルに委譲し、本ファイルにはポインタのみ残す」→ 推奨#3 はポインタ追加のみなので整合。
- CLAUDE.md の memory 制約（§記録・保存の制約）→ 推奨に memory 書き込みは含まない。整合。
- 既存 feedback memory との衝突 → 関連する feedback memory エントリは確認されず、衝突なし。
- CLAUDE.md 自体の改訂（推奨#3、改善提案#2）→ CLAUDE.md が不十分であったことが事故原因であり、改訂は適切。

### 副作用シミュレーション（8c）

**(i) 単体副作用**:
- handoff.md に「Codex 宛は禁止」を追記した場合、AI は Codex 宛の伝言を handoff.md に書かなくなる。代替先（083 の逆方向フロー）が存在しない段階で排他制約だけ追加すると、AI が「どこにも書けない」状態になる。**083 の逆方向フロー追加（指摘#1）が先行必須**。
- 083 に逆方向フローを追加した場合、AI は Codex 宛の伝言を正しいパスに書くようになる。codex-to-claude-handoff.md が双方向で使われる場合、ファイル名の方向性（codex-to-claude）と実態（双方向）の不整合が新たな混乱の種になりうる。別ファイル（claude-to-codex-handoff.md）を設ける方がファイル名の自己文書化として明確。ユーザー判断事項。

**(ii) クロスルール競合**:
- CLAUDE.md L13（Codex 受け取り）と L145（端末間引き継ぎ）は別セクションに属し、推奨修正後も責務が分離される。競合なし。
- 086知見ファイルの排他制約追加（改善提案#3）と handoff.md 自体の排他制約（指摘#2）は同一ルールの二重記載になるが、086 は手順書、handoff.md は自己文書化なので冗長ではあるが矛盾はしない。

**(iii) 状態依存シナリオ**:
- handoff.md は状態を持たないファイル（エントリの追加・更新・削除のみ）。状態依存の問題は発生しない。

### 事後確認事項（8d）
- 083 に逆方向フロー追加後、次に Claude Code が Codex にタスクを送る場面で正しいパスに書き込むことを確認
- handoff.md に Codex エントリが新たに書き込まれないことを確認（次回の Codex 委譲時）
- 既存の handoff.md 内 Codex エントリ（2026-04-28, 2026-05-01）の移動・削除が完了したことを確認

---

## 【確認できなかった事項】

- `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` の実ファイル内容（プロジェクトリポジトリ外のため Read 不可。双方向利用の可否はファイル構造を確認する必要がある）
- ユーザーが Codex への伝言チャネルとして `codex-to-claude-handoff.md` の双方向利用を意図しているか、別ファイル新設を意図しているかの判断
