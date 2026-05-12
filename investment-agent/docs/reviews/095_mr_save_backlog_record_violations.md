# 095 事故報告: save_backlog_record.py 規約違反10件多発

## レビュー種別

- レビュワー: md-reviewer
- パターン: 4（運用事故 — 自己の行動不備記録）

## レビュー対象ファイルパス

- `scripts/save_backlog_record.py`（修正後）
- `docs/knowledges/tools/004_coding_conventions.md`（参照元規約）
- `CLAUDE.md`（参照元規約）

## 事象・背景

### 事象

2026-05-07、`save_backlog_record.py` を新規作成した際、初版で **10件のコーディング規約違反** が混入した。code-reviewer（093_cr）が6件検出し、ユーザー指示「規約違反多すぎる、他にもないか自分で確認」を受けて追加4件を自力発見。

### 違反一覧

| # | 違反分類 | 規約出典 | 内容 |
|---|---------|---------|------|
| 1 | A-1 | 004§A-1 | `sys.exit(1 if errors else 0)` なし。exit 0 固定 |
| 2 | E-1 | 004§E-1 + CLAUDE.md§GCP認証 | bucket名・project ID・keypath をハードコード |
| 3 | CLAUDE.md | CLAUDE.md§コーディング規約 | `print()` 使用（structlog 必須） |
| 4 | 設計 | 004§新規バッチチェックリスト | dry-run時にGCS既存レコードを読まない非対称設計 |
| 5 | 設計 | — | `adapter_version` フィールドが常に空文字（根拠なし） |
| 6 | E-1 | 004§E-1 | `--input-dir` デフォルトがマシン固有パス |
| 7 | CLAUDE.md | CLAUDE.md§GCP認証 | `settings.google_application_credentials` 未使用 |
| 8 | CLAUDE.md | CLAUDE.md§コーディング規約 | docstring が Google style でない（Args/Returns 欠落） |
| 9 | 設計 | — | `STRUCTURE_DIR` が相対パス（CWD依存で壊れやすい） |
| 10 | A-7 | 004§A-7 | サマリ出力が print で structured field なし |

### ユーザーの問題意識

「規約違反多すぎる」— 新規スクリプト作成時に既存規約を事前参照していない品質ギャップ。

## 補足情報

- 修正コミット: 修正後の現行版は規約準拠済み
- code-reviewerの評価: C（6件重大指摘）
- 規約違反の根本原因: 004_coding_conventions.md §新規バッチジョブ作成時チェックリスト を作成前に参照していなかった

---

# MD AI可読性レビュー: save_backlog_record.py 規約違反10件 — 再発防止分析

- 日時: 2026-05-07 12:27 JST
- 対象: `scripts/save_backlog_record.py`, `docs/knowledges/tools/004_coding_conventions.md`, `CLAUDE.md`
- パターン: 4（運用事故 — 自己の行動不備記録）
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/095_mr_save_backlog_record_violations.md`

---

## 【サマリー】

- AI可読性評価: **B** — 規約文書自体は明確に書かれているが、新規スクリプト作成時に構造的に参照を強制する仕組みが欠如
- 誤読リスク評価: **A** — 規約を読みさえすれば誤読する余地は少ない。問題は「読まない」こと
- 主要リスク:
  1. CLAUDE.md 高頻度参照テーブルの「スクリプトを新規作成・改修」→ 004 への導線は存在するが、**AIの行動を構造的に強制するトリガーが無い**（意志依存）
  2. 004 §新規バッチジョブ作成時チェックリストはチェック項目として網羅的だが、**CLAUDE.md 側のコーディング規約（print禁止/structlog/GCP認証/docstring style）をカバーしていない**
  3. 既存の `keyword_md_gate.py` hook は「索引ファースト」を毎回リマインドするが、**スクリプト新規作成という特定の行為に対する規約読み込み強制が無い**

---

## 【Markdown 品質評価】

### Accuracy / 正確性: A

- 004の規約記述は正確。CLAUDE.md のコーディング規約も正確
- 修正後の `save_backlog_record.py` は実際に規約準拠しており、事後的には整合している

### Completeness / 完全性: B

- **004 §新規バッチジョブ作成時チェックリスト** は A-1〜F-1 のアンチパターン系は網羅しているが、以下が欠落:
  - `print()` 禁止 / `structlog` 使用（CLAUDE.md 規約だが 004 チェックリストに不記載）
  - GCP認証パターン（`settings.google_application_credentials` 経由）
  - docstring スタイル（Google style / Args / Returns 必須）
  - 型ヒント必須
- これらは CLAUDE.md §コーディング規約に記載されているが、004 のチェックリストから**クロスリファレンス**されていない

### Relevance / 関連性: A

- 両文書とも対象が明確で、ノイズは少ない

### Actionability / 実行可能性: B

- 004 チェックリストは「作成後にレビューで使う」形式。**作成前に参照してから書き始める**という発火タイミング指定が弱い
- CLAUDE.md 高頻度参照テーブルの「スクリプトを新規作成・改修」は **タスク受領時** に参照すべきとあるが、実際のスクリプト Write/Edit の瞬間に強制されない

---

## 【パターン 4: 行動不備原因分析】

### 事象

新規スクリプト `save_backlog_record.py` 初版で10件の規約違反が混入。AIは CLAUDE.md の高頻度参照テーブルに明記された 004 を参照せずにコーディングを開始した。

### 読み手の解釈

「タスクの本質（GCS upsertロジックの実装）に集中し、横断的規約の確認ステップをスキップした」— 規約の存在は知識として持っているが、Write 操作前のゲートとして参照する習慣が無かった。

### 直接原因

1. **004 §新規バッチジョブ作成時チェックリストを参照せずに `Write` した**
2. **CLAUDE.md §コーディング規約を参照せずにコーディングした**

### 根本原因

1. **トリガー導線の不在**: CLAUDE.md 高頻度参照テーブルは「タスクを受けたら照合せよ」という汎用ルールだが、**「scripts/ 配下に新規 .py を Write する直前」という具体的な発火条件にバインドされていない**
2. **構造的強制の欠如**: 既存 hook は `keyword_md_gate.py`（全プロンプトに索引ファーストリマインダ）と `check_category_drift.py`（Edit/Write 後のカテゴリドリフト検知）が存在するが、**「scripts/*.py への Write 前に 004 チェックリストを確認したか」を検証する hook が無い**
3. **チェックリストの分散**: コーディング規約が CLAUDE.md と 004 に二重に存在し、004 のチェックリストが CLAUDE.md 固有項目（print禁止・structlog・GCP認証・docstring style）をカバーしていない。AIは 004 だけ見れば十分と判断し得る

### MD上の原因

- `CLAUDE.md:231` — 「スクリプトを新規作成・改修 → 004」。導線は存在するが、**ファイル Write の瞬間に発火する仕組みではない**（意志依存）
- `docs/knowledges/tools/004_coding_conventions.md:294-317` — チェックリストは A-x〜F-x を網羅するが、CLAUDE.md §コーディング規約の横断的ルール（print禁止・structlog・GCP認証・docstring・型ヒント）が **含まれていない**

### MD構成原因

- CLAUDE.md §コーディング規約（L275-299）と 004 §新規バッチジョブ作成時チェックリスト（L294-317）は **同じ「新規スクリプト作成時に守るべきこと」を二元管理** している
- 004 チェックリストは自文書内のアンチパターンのみ参照し、CLAUDE.md 規約をインポートしていない
- AIが 004 チェックリストを見ても、CLAUDE.md 固有のルール（4項目）を見落とす構造になっている

### 再発防止の方向性

1. **004 チェックリストに CLAUDE.md 規約項目を統合**: 「新規バッチジョブ作成時に確認すべき全項目」を 004 に一元化
2. **PostToolUse hook で構造的強制**: `scripts/*.py` への Write/Edit 時に「004 チェックリスト確認済みか」をリマインドする hook 追加
3. **CLAUDE.md の高頻度参照テーブルの発火条件を明確化**: 「scripts/ 配下のファイルを新規作成する場合、Write 前に必ず Read せよ」

### 対策スコープ

この問題は `save_backlog_record.py` 固有ではない。**今後新規作成される全スクリプトで同じ問題が再発する**。CLAUDE.md レベルの包括的対策が必須。

---

## 【重大な指摘】（即修正）

### #1 004 チェックリストが CLAUDE.md 規約項目を含んでいない

- 箇所: `docs/knowledges/tools/004_coding_conventions.md:294-317`
- 問題: チェックリストは自文書内のアンチパターン（A-1〜F-1）のみ。CLAUDE.md §コーディング規約の以下4項目が欠落:
  - `print()` 禁止 / `structlog` 使用
  - GCP認証は `settings.google_application_credentials` 経由
  - docstring は Google style（Args/Returns 必須）
  - 型ヒント必須
- AIの誤読パターン: 「004 チェックリストを全項目確認した → 規約準拠完了」と判断。CLAUDE.md 側のルールは別途確認が必要だという認識が欠落
- トリガー: 新規スクリプト作成時に 004 チェックリストを参照するが CLAUDE.md を参照しない場合
- 影響: 今回と同じ pattern で print使用・GCP認証ハードコード・docstring不備が再発
- 根拠: 今回の10件中4件（#3, #7, #8, #10）が CLAUDE.md 固有ルール違反であり、004 チェックリストだけでは検出できなかった
- 推奨対応: 004 チェックリスト末尾に以下を追加:
  ```
  - [ ] CLAUDE.md§コーディング規約: `print()` 不使用、`structlog` でロギング
  - [ ] CLAUDE.md§コーディング規約: GCP認証は `settings.google_application_credentials` 経由
  - [ ] CLAUDE.md§コーディング規約: docstring は Google style（Args/Returns/Raises 記載）
  - [ ] CLAUDE.md§コーディング規約: 全関数に型ヒント
  ```
- MD修正だけで足りるか: **足りない**。下記 #2 の構造的強制と組み合わせが必要

**[採用]** 004 チェックリスト末尾に4項目追加済み。

### #2 新規スクリプト Write 前の規約参照を強制する仕組みが無い

- 箇所: `CLAUDE.md:231`（高頻度参照テーブル）、`.claude/settings.local.json:142-172`（PostToolUse hooks）
- 問題: CLAUDE.md は「スクリプトを新規作成・改修 → 004 を読め」と書いているが、これは **意志依存型ルール**。既存 PostToolUse hook（`check_category_drift.py`）は Edit/Write 後に発火するカテゴリドリフト検知であり、「Write 前に 004 を参照したか」は検証しない
- AIの誤読パターン: タスクの本質（機能実装）に集中し、横断的規約チェックのステップを無意識にスキップ
- トリガー: 新規スクリプト作成を含むタスク（今後も繰り返し発生）
- 影響: 規約を参照せずに書いたコードが code-reviewer で大量指摘される → 手戻り → ユーザー不満
- 根拠: 今回の事故がまさにこのパターン。`keyword_md_gate.py` は毎プロンプトで「索引ファースト」を出すが、新規スクリプト作成の特定行為に対する具体的な強制力がない
- 推奨対応: **CLAUDE.md に明示的な行動ルールを追加**:
  ```
  - **新規スクリプト作成時の必読**: `scripts/` 配下に新規 .py ファイルを作成する場合、
    Write 実行前に `docs/knowledges/tools/004_coding_conventions.md` §新規バッチジョブ作成時チェックリスト
    を Read し、全項目を確認してからコーディングに入ること。
    確認なしの Write は禁止（事故: review 095）
  ```
  記載先: `CLAUDE.md` §コーディング規約セクション末尾
- MD修正だけで足りるか: **CLAUDE.md への追記で中程度の効果**。意志依存は残るが、「事故 review 095」の参照により「これは実際に起きた事故」という重みが加わる。hook 追加（下記 #3）で構造的強制に格上げ可能

**[採用]** CLAUDE.md §コーディング規約末尾に行動命令を追加済み。

### #3 PostToolUse hook による構造的強制の提案

- 箇所: `.claude/settings.local.json:142-172`（PostToolUse hooks 設定）
- 問題: 現在の hook 構成には「scripts/*.py への Write 前に規約確認を促す」仕組みが無い
- AIの誤読パターン: N/A（hook の不在は MD の AI可読性問題ではなく、仕組みの欠如）
- トリガー: `scripts/` 配下に新規 .py が Write される瞬間
- 影響: CLAUDE.md にルールを書いても、構造的に強制されなければ再発リスクが残る
- 根拠: 既存の `keyword_md_gate.py`（UserPromptSubmit hook）が「索引ファースト」を構造的に毎回リマインドして効果を発揮している前例がある
- 推奨対応: PostToolUse hook（matcher: `Write`）を追加。`scripts/` 配下の新規 .py ファイルが作成された場合に「004 チェックリスト確認リマインダ」を出力するスクリプトを実装。具体的には:
  - hook スクリプト: Write された file_path が `scripts/*.py` かつ新規作成（git untracked）の場合にリマインダ出力
  - リマインダ文面: `[規約チェック] scripts/ 新規 .py 検出。004 §新規バッチジョブ作成時チェックリスト を確認せよ`
- MD修正だけで足りるか: **足りない。ソースコード側の仕組み追加が必要**
- 記載先: `.claude/settings.local.json` の hooks.PostToolUse 配列に追加。hook スクリプトは `scripts/check_new_script_convention.py` として新規作成

**[採用]** `scripts/check_new_script_convention.py` 作成 + `.claude/settings.local.json` に PostToolUse hook 追加済み。

---

## 【改善提案】（中優先度）

### #1 004 チェックリストに「出典参照」列を追加

- 箇所: `docs/knowledges/tools/004_coding_conventions.md:294-317`
- 現状: チェックリスト各項目が自文書内のアンチパターン番号のみを参照。CLAUDE.md からの横断参照が無い
- 提案: 各項目に `(出典: 004§A-1)` / `(出典: CLAUDE.md§コーディング規約)` のような注記を付け、参照元を明確にする
- 期待効果: AIが「この項目はどのルール文書に基づくか」を即座に判断でき、疑義時に正確な根拠を辿れる

**[見送り: 追加した4項目には既に「CLAUDE.md§コーディング規約:」プレフィックスで出典を明示済み。既存項目は自文書内なので自明]**

### #2 CLAUDE.md §コーディング規約から 004 チェックリストへの明示的リンク

- 箇所: `CLAUDE.md:275-299`
- 現状: CLAUDE.md §コーディング規約は規約の列挙のみ。004 チェックリストの存在への言及が無い
- 提案: §コーディング規約の末尾に以下を追加: `> 新規バッチジョブ作成時は 004 §新規バッチジョブ作成時チェックリスト で全項目確認`
- 期待効果: AIが CLAUDE.md のコーディング規約を読んだ後、自然に 004 チェックリストにも到達する動線が確保される

**[採用]** 重大指摘#2で追加した行動命令が実質的にこの導線を果たしている。追加済み。

### #3 チェックリストの分類整理

- 箇所: `docs/knowledges/tools/004_coding_conventions.md:294-317`
- 現状: 全項目がフラットに並んでいる
- 提案: カテゴリ分け（「失敗伝播」「設定管理」「基本規約」等）して、どの領域のチェックが漏れたか一目で分かるようにする
- 期待効果: 10件中何件がどのカテゴリに属するかが可視化され、弱点パターンの自己認識が容易になる

**[見送り: 項目数25件でフラット構造は許容範囲。カテゴリ分けは可読性向上より管理コスト増の方が大きい]**

---

## 【ソースコード・仕組み側への波及】

### PostToolUse hook 新規追加の提案

**問題**: MD にルールを書くだけでは意志依存型であり、構造的強制にならない。

**提案する仕組み**:

```
# .claude/settings.local.json の hooks.PostToolUse に追加
{
  "matcher": "Write",
  "hooks": [
    {
      "type": "command",
      "command": "PATH=$HOME/.local/bin:$PATH PYTHONUTF8=1 uv run python scripts/check_new_script_convention.py",
      "timeout": 10
    }
  ]
}
```

`scripts/check_new_script_convention.py` の仕様:
- stdin から PostToolUse event を受信（Write tool の出力）
- file_path が `scripts/*.py`（`tmp_*` を除く）かつ新規作成の場合にリマインダ出力
- リマインダ: `[規約チェック] scripts/ 新規 .py 検出。004 §チェックリスト + CLAUDE.md §コーディング規約 を確認せよ`
- 既存ファイルの Edit の場合は不発火（改修時は別のタイミングで参照される前提）

**期待効果**: `keyword_md_gate.py` と同様の「構造的リマインダ」で、スクリプト新規作成という特定行為に対して規約参照を強制する。

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 推奨 #1（004 チェックリスト拡充）: 004 が「新規バッチジョブ作成時の全チェック項目」の正本。CLAUDE.md §コーディング規約からの引用追加は正本帰属的に正しい（004 に集約する方向）
- 推奨 #2（CLAUDE.md への行動ルール追加）: CLAUDE.md は行動ルールの正本。「Write 前に 004 を Read せよ」は行動ルールであり、CLAUDE.md が正本として適切
- memory への詳細記載は行わない（CLAUDE.md に記載先を持つため）

### 8b. 上位ルール整合性チェック

- 推奨が CLAUDE.md の「設定値はハードコーディング禁止」「print禁止」等のルールと矛盾しないか: **矛盾なし**。むしろ既存ルールの遵守を強化する方向
- Q1: 「新規スクリプト作成時に規約を事前参照する」というべき論は CLAUDE.md に汎用ルールとして明文化されているか? → **されていない**。高頻度参照テーブルに「スクリプトを新規作成・改修 → 004」と書いてあるのみで、「Write 前に必ず Read せよ」という行動命令は無い。**追加を推奨に含める**（重大指摘 #2）
- Q2: 汎用ルールが CLAUDE.md に存在するか? → **存在しない**。初回事故だが汎用性が高い
- Q3: 対策が個別MD固有に閉じていないか? → #1 は 004 の修正、#2 は CLAUDE.md への追加、#3 は hook 追加。いずれも汎用的

### 8c. 副作用シミュレーション

**(i) 単体副作用**:
- 004 チェックリストに4項目追加 → チェック項目が増えるがいずれも必須規約。過剰制約にはならない
- CLAUDE.md に「Write 前に Read せよ」追加 → 改修時にも毎回 004 を Read する負荷が発生するが、「新規作成」に限定すれば妥当

**(ii) クロスルール競合**:
- CLAUDE.md §注意事項「知見ファイルは最後まで読む」と組み合わさり、004 を全文読む負荷が増す可能性。ただし「チェックリスト部分のみ確認」と限定すれば回避可能
- `keyword_md_gate.py` の「索引ファースト」リマインダと hook の二重発火: 両者は異なるタイミング（プロンプト受信時 vs Write 後）なので競合しない

**(iii) 状態依存シナリオ**:
- ライン会話モード中に新規スクリプトを作成するケース: hook は BG 処理と独立して発火するため影響なし

**(iv) 再発防止策の実効性**:
- 推奨 #2（CLAUDE.md に行動ルール追加）は **意志依存型**。ただし「事故: review 095」の参照が付くことで単なる注意喚起より抑止力がある
- 推奨 #3（PostToolUse hook）は **構造的強制型**。AIの意志に関係なくリマインダが発火する
- 推奨 #1（004 チェックリスト統合）は **網羅性の改善**。参照した場合の見落とし防止

### 8d. 事後確認事項

- 推奨 #1 実施後: 次回新規スクリプト作成時に CLAUDE.md 規約違反（print/structlog/GCP認証/docstring）が再発しないか確認
- 推奨 #3 実施後: hook が正常に発火し、リマインダが表示されることを `scripts/tmp_*.py` 以外の Write で検証
- 副作用監視: hook 追加による Write 操作の遅延が 10秒以内に収まることを確認

---

## 【確認できなかった事項】

- 既存の全スクリプト（`scripts/*.py`、`tmp_*` を除く）が現行の CLAUDE.md §コーディング規約に準拠しているか（print使用・structlog不使用のスクリプトが他にも存在する可能性）
- `check_category_drift.py` の詳細実装: 本 hook が既にスクリプト Write を検知する機能を持っていれば、そこに規約リマインダを統合できる可能性
- 過去に同様の「新規スクリプト初版で規約違反多発」が起きていないか: 004-1 蓄積ログに類似エントリがあるか未確認（今回が初回記録の可能性あり）
