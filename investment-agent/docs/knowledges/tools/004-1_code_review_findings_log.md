# コードレビュー不備 蓄積ログ

**カテゴリ**: tools
**作成日**: 2026-04-21
**ステータス**: 有効（追記中）
**適用範囲**: code-reviewer スキル（`skills/code-reviewer.md`）が検出した不備の時系列蓄積

## 目的

code-reviewer がレビューで検出した「プラン不備」「コード不備」を**時系列で蓄積**し、**傾向が見えた段階で** `004_coding_conventions.md` のルール追加 or `skills/code-reviewer.md` のチェック項目追加に繋げる。

**運用原則**: 一件ごとの都度対策は禁止。傾向分析の結果として対策を打つ（過剰反応・ルールインフレ防止）。

## 記録フォーマット

各エントリは以下の 1 行:

```
- [YYYY-MM-DD] <tag> | <対象プラン/コード> | <症状の 1 行要約>
```

- `<tag>`: 下記タグカタログから選ぶ（該当がなければカタログに新規追加）
- `<対象プラン/コード>`: `docs/plans/<ファイル名>` or `scripts/<ファイル名>:L<行番号>`
- `<症状の 1 行要約>`: 80 文字以内、固有名詞を含める

## タグカタログ

### format 系（プラン MD 書式違反）
- `format:commit-hash-missing` — 基準 commit hash が冒頭に無い
- `format:line-number-drift` — 行番号が実コードと乖離（commit 指定無しの結果）
- `format:caller-ref-vague` — 呼び出し側波及が曖昧（該当行リストになっていない）
- `format:antipattern-map-missing` — アンチパターン対応表が末尾に無い
- `format:rollback-missing` — ロールバック手順が欠落
- `format:verification-thin` — 検証戦略が薄い（smoke/dev/prod/回収手順が揃っていない）
- `format:before-after-missing` — 修正方針が after のみで before 対比なし

### content 系（プラン内容の不備）
- `content:unverified-assumption` — 「既に〜がある」系の前提が実コードと食い違い
- `content:priority-inflation` — 読みづらさ・デッドコードだけで P0 に置いている
- `content:missing-downstream` — 呼び出し側への波及の記述漏れ
- `content:data-loss-path` — データロスト経路（silent drop / orphan row）の見落とし
- `content:silent-exception` — 例外握り潰しによる検知不能の見落とし
- `content:orphan-resource` — ジョブ/バッチの orphan（中断時のリソース放置）の見落とし
- `content:similar-bug-uncovered` — 同種バグが他箇所にあるのに横展開していない
- `content:regression-risk-missed` — 修正による regression リスクの記述漏れ

### bug 系（コード側の実バグ、プラン未指摘）
- `bug:sql-injection` — f-string SQL 組立・パラメタライズ未実施
- `bug:race-condition` — 並行性バグ
- `bug:resource-leak` — ファイル/接続/ディスクのリーク
- `bug:error-swallowing` — 広義 except で例外を無言で捨てる
- `bug:type-mismatch` — 型の齟齬（None/空/NaN/union 型の扱いミス）
- `bug:partition-prune-loss` — BQ partition prune が効かない SQL

（該当タグ無ければカタログに追加してから使う）

## 蓄積エントリ（新しい順）

### 2026-04-21

- [2026-04-21] content:unverified-assumption | docs/plans/20260421_063341_tdnet_load_code_review.md P0-3 | 「既に except ValueError がある」誤認、実コードで parse_tdnet_filename 呼び出しは裸
- [2026-04-21] content:data-loss-path | docs/plans/20260421_063341_tdnet_load_code_review.md | ai-finalize で text 無し doc の pending_* 行 DELETE によるデータロスト経路未指摘
- [2026-04-21] content:orphan-resource | docs/plans/20260421_063341_tdnet_load_code_review.md | phase_gemini_tanshin_batch の orphan Gemini batch + 例外握り潰し未指摘
- [2026-04-21] content:similar-bug-uncovered | docs/plans/20260421_063341_tdnet_load_code_review.md | P1-3 orphan cancel が phase_gemini_tanshin_batch 経由にも必要な件の横展開漏れ
- [2026-04-21] format:line-number-drift | docs/plans/20260421_063341_tdnet_load_code_review.md | plan と実コードで最大 +27 行のドリフト（基準 commit 未指定が原因）
- [2026-04-21] content:priority-inflation | docs/plans/20260421_063341_tdnet_load_code_review.md P0-6 | 読みづらさ（errors=1 デッドコード）のみで P0
- [2026-04-21] bug:partition-prune-loss | docs/plans/20260421_063341_tdnet_load_code_review.md P0-5 | parametrize 化後に DATE 型指定が漏れると partition prune が無効化するリスクが検証に含まれていない
- [2026-04-21] bug:error-swallowing | scripts/tdnet_load_parallel.py:1322-1323 | Embedding parse 失敗が silent continue（プラン P1-9 でリストにも無し）
- [2026-04-21] bug:error-swallowing | scripts/tdnet_load_parallel.py:779-780 | Vision OCR 個別 doc パース失敗が silent（プラン P1-9 リスト漏れ）
- [2026-04-21] bug:resource-leak | scripts/tdnet_load_parallel.py:680 | `_download_batch_results` が blob 全量メモリロード（004 C-2 違反、プラン未指摘）

## 傾向分析

### トリガー条件

- **単一タグが 3 件以上** 蓄積した時点で分析着手
- **月次で全タグの頻度集計**（毎月 1 日、前月分を集計）
- 重大（bug:sql-injection / bug:race-condition 等）は 1 件でも即分析

### 分析手順

1. 該当タグのエントリを一覧化し、**共通原因**を抽出
2. 共通原因が「プラン作成時の見落とし」なら → `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット にルール追加（定義正本）
3. 共通原因が「レビュー観点の不足」なら → `skills/code-reviewer.md` のチェックリストに項目追加
4. 共通原因が「コード設計の繰り返しミス」なら → `004` のアンチパターン集（A-x / B-x など）に新項目追加
5. 分析結果と対策を**本ファイル末尾「対策履歴」に追記**（どのエントリを根拠にどのルール追加をしたか紐付け）

### 分析時の禁止事項

- **1 件だけのエントリを根拠にルールを追加しない**（過剰反応）
- **タグ分類を細かくしすぎない**（1 件 1 タグ状態は傾向が見えない）
- **対策でルールを増やす時は既存ルールの整理も同時に**（ルールインフレ回避）

## 対策履歴

（傾向分析の結果として実施した 004 / スキル md 改訂をここに記録する）

- （未実施。初期エントリ蓄積中）
