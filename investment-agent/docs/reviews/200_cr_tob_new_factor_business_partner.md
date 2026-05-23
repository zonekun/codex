# コードレビュー: TOB ML新因子 has_business_partner_investor 追加計画

- 日時: 2026-05-17 18:30 JST
- 対象: `docs/plans/analysis-007_tob_new_factor_business_partner_20260517_175057.md`
- コード: `scripts/tob_prediction/compute_owner_features.py`、`scripts/tob_prediction/train_rf.py`
- パターン: 4（新規開発・設計計画のレビュー）
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `SHAREHOLDER_COMPOSITION_EXTEND` の `LISTED_CORP` TYPE を活用し、TOP10株主に上場事業法人が存在するかを示す2カラム（`HAS_BUSINESS_PARTNER_INVESTOR` / `BUSINESS_PARTNER_RATIO_IN_TOP10`）を `SHAREHOLDER_COMPOSITION` に追加し、RF モデルへ 26→27変数として組み込む。
- 品質評価: **B** — 計画の段階的検証・撤退基準が充実している一方、Plan B 依存の明示が不十分な箇所と、コード実装上の論理不整合が複数存在する。
- 主要リスク:
  1. Plan B 完了前に Step C を誤って実行すると全件 `HAS_BUSINESS_PARTNER_INVESTOR=FALSE` でサイレント汚染される（フェールセーフなし）
  2. `compute_owner_features.py` の `ticker_filter` 二重適用による sample モード時の UPDATE 空振りリスク
  3. `_load_shareholders` のキャッシュが Plan C 実行後に古い列定義のまま残留する可能性

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

- LISTED_CORP 型を EXTEND テーブル経由で JOIN する方式は、既存の `entries_typed` CTE のパターンと完全に整合しており、適切。
- `LOGICAL_OR` / `SUM(IF(...))` の BQ 集計パターンも既存 `owner_agg` と同一慣用形で問題なし。
- 初回は BINARY のみ（`has_business_partner_investor`）で実装し、CONTINUOUS（`business_partner_ratio_in_top10`）を Walk-Forward 後判断する段階設計は合理的。

### 既存システムとの統合

- [x] `compute_owner_features.py`: `NEW_COLUMNS`・`owner_agg` CTE・`SET` 句・derived SELECT・`verify_results` の4箇所を変更する必要があり、プランは全て列挙している。
- [x] `train_rf.py`: `BINARY_FEATURES`・`_load_shareholders`・`build_feature_matrix` の3箇所を変更する必要があり、プランは全て列挙している。
- [ ] **`screen_tob.py` の対応**: Step F-3 でスクリーナーを実行するが、`screen_tob.py` が BQ から `HAS_BUSINESS_PARTNER_INVESTOR` を直接 SELECT しているか、または `train_rf.py` の CSV 経由か不明。前者なら追加カラムのフォールバック処理が必要。（確認できなかった事項参照）
- [ ] **`bq_shareholder_composition.md`**: Step G-1 でカタログ追記が計画されており、GCP リソース変更後 MD 整合チェックの義務として必須。計画に含まれているため問題なし。

### リスク・コスト

- BQ DML UPDATE（全件約 37,657 行）: 既存 `full` モードと同規模。追加 JOIN は EXTEND テーブルの小テーブルへの JOIN であり、スキャン量増加は軽微と推定。
- Optuna 30 trials × 4年 Walk-Forward: 計算コスト変化なし（変数数 26→27 の 1 増は RF では無視できる）。
- 撤退基準（F-4: AUC 低下 > 0.005 → 除去）が定義されている。BINARY_FEATURES からの除去だけで済み、BQ カラムは残置。ロールバックコストは低い。

### 抜け漏れ

- [ ] **Plan B 未完了時の誤実行に対するフェールセーフが実装に存在しない**（重大: 詳細は【重大な指摘】#1 参照）
- [ ] **`verify_results` の sample 銘柄リストが Step C-6 で更新されていない**: プランの Step E-3 確認項目は「ジャストシステム(4686)・日本アクア(1429)・ユニクロ(9983)・久光(4530)」の4銘柄だが、`main()` の sample モードは `["9983", "4530", "4917", "7203"]`（ユニクロ・久光・マンダム・トヨタ）の4銘柄。**ジャストシステム(4686)・日本アクア(1429) が sample 実行に含まれていない**ため、smoke test の主要確認項目（HAS_BUSINESS_PARTNER_INVESTOR=TRUE の検証）がカバーされない。
- [ ] **新カラム追加後の既存ヌル行の扱い**: `full` モード実行前に ALTER TABLE を経た行は全て NULL のまま。UPDATE が全件を必ずカバーするなら問題ないが、`TOP10_NAMES_JSON IS NULL` の行は entries が 0 件となり、`owner_agg` に存在しないため `derived` との JOIN で欠落 → UPDATE 非対象となる。この挙動は既存カラム（HAS_FAMOUS_INVESTOR 等）でも同様のはずだが、プランで言及なし。

### 段階的検証計画

- smoke（sample 4銘柄）→ dev（full 実行 + Walk-Forward）→ 本番適用判断基準の3段が明示されており適切。
- 回収手順（BINARY_FEATURES から除去 → `--refresh`）も定義済み。

### 完了条件の検証可能性

- ROC-AUC >= 0.755、PR-AUC >= 0.079 は数値基準で検証可能。
- SHAP top20 入りは `train_rf.py:L525` の importance ログで確認可能。具体的。

### データカタログ整合

- 使用テーブル `SHAREHOLDER_COMPOSITION` / `SHAREHOLDER_COMPOSITION_EXTEND` は既存テーブル。Step G-1 でカタログ追記が計画されており、整合している。

---

## 【重大な指摘】（即修正）

### #1 Plan B 未完了時のサイレント汚染: フェールセーフなし

- 箇所: `compute_owner_features.py:L176-L185`（`owner_agg` CTE、計画 C-2 の追加箇所）および `compute_owner_features.py:L234-L238`（`dry-run` 分岐）
- 事象: `EXTEND_TABLE` に `LISTED_CORP` TYPE が 0 件の状態で `--mode sample` や `--mode full` を実行すると、`LOGICAL_OR(entry_type = 'LISTED_CORP')` が全件 FALSE、`SUM(...)` が全件 0.0 となり、BQ は正常終了して `HAS_BUSINESS_PARTNER_INVESTOR=FALSE` で全行を上書きする。エラーも警告も出ない。
- トリガー: Plan B 完了前に誰かが（または手順を飛ばして）`--mode sample/full` を実行したとき。
- 影響: 37,657 行が `HAS_BUSINESS_PARTNER_INVESTOR=FALSE` で汚染され、Walk-Forward 評価で「因子が効かない」という誤った結論が導かれる。再実行で上書きできるが、Walk-Forward 結果を既に保存していた場合は誤結論が残る。
- 根拠: プランに「Plan B 完了ゲート確認（Step B）後に Step C に進む」と明記されているが、スクリプト実装上にこのゲートを強制するコードは存在しない。`_eda_listed_corp` 関数（C-5 で追加予定）は `dry-run` モードでのみ呼ばれ、`sample/full` では呼ばれない。
- 推奨対応 [方向性]: `compute_and_update()` の冒頭（ALTER TABLE の後、UPDATE SQL 実行前）に LISTED_CORP カウントを確認するガードを追加する。LISTED_CORP 件数が 50 件未満なら `RuntimeError` または `sys.exit(1)` で停止する。確度: [方向性]（実装詳細は提出元に委ねる）

### #2 sample モードの smoke test 銘柄がプラン確認項目と不一致

- 箇所: `compute_owner_features.py:L248-L249`（`main()` の sample_tickers）
- 事象: 現行コードの sample_tickers は `["9983", "4530", "4917", "7203"]`。プラン Step E-3 の確認項目は「ジャストシステム(4686)・日本アクア(1429) が HAS_BUSINESS_PARTNER_INVESTOR=TRUE」であることを要求しているが、これら2銘柄が sample_tickers に含まれていない。
- トリガー: Step C 実装後に `--mode sample` を実行する都度。
- 影響: 計画の smoke test 基準（LISTED_CORP TYPE が正しく機能するかの確認）をパスできない。TRUE であるべき銘柄を検証せずに `--mode full` に進んでしまうリスクがある。
- 根拠: プランの「検証戦略 §1: smoke test — ジャストシステム(4686)が HAS_BUSINESS_PARTNER_INVESTOR=TRUE であること」と実装 `L248-249` の乖離。
- 推奨対応 [検証済み]: プラン C-5 と同時に、`main()` の sample_tickers を `["4686", "1429", "9983", "4530"]` に変更する（ジャストシステム・日本アクア・ユニクロ・久光）。トヨタ(7203)は LISTED_CORP を持つが代替確認として久光(4530)で FALSE 確認が可能。

### #3 `_load_shareholders` キャッシュが古い列定義で残留

- 箇所: `train_rf.py:L90-L99`（`_cached` 関数）、`train_rf.py:L133-L143`（`_load_shareholders`）
- 事象: `HAS_BUSINESS_PARTNER_INVESTOR` / `BUSINESS_PARTNER_RATIO_IN_TOP10` を SELECT に追加した後も、`--refresh` フラグなしで実行すると、`CACHE_DIR/shareholders.csv`（旧スキーマ）をキャッシュヒットとして読み込む。`build_feature_matrix` 内で `df["HAS_BUSINESS_PARTNER_INVESTOR"]` を参照すると `KeyError` で即クラッシュする。
- トリガー: Step D 実装後に `--refresh` を付け忘れて `train_rf.py` を実行したとき。プラン E-5 に `Remove-Item` によるキャッシュ削除手順があるが、削除を忘れる・別端末で試す等の運用ミスで発生する。
- 影響: `KeyError` によるクラッシュ（ただし実害ゼロ、`--refresh` を付ければ解消）。
- 根拠: `_cached` 関数 `L90-L99` はキャッシュ存在のみを確認し、スキーマ整合性を検証しない（これは既存の構造的制約）。
- 推奨対応 [方向性]: `build_feature_matrix` の shareholder features ブロック（L316-L328）で新カラムを参照する直前に `if "HAS_BUSINESS_PARTNER_INVESTOR" not in df.columns: raise KeyError(...)` を入れる、またはプランの E-5 手順を確実に踏ませる案内コメントをスクリプト冒頭に追加する。キャッシュ削除手順はプランに既に含まれているため、運用ルールとして E-5 の明示で十分という判断も可。

---

## 【改善提案】（可読性・保守性）

### #1 C-5 の `_eda_listed_corp` が sample/full モードで呼ばれない

- 箇所: `compute_owner_features.py:L234-L238`（`dry-run` 分岐）および `L241-L257`（sample/full 共通パス）
- 現状: `_eda_listed_corp` は `dry-run` 専用として設計されている（C-5）。しかし sample/full モード実行時も LISTED_CORP 件数を確認したい（フェールセーフとして）。
- 提案: sample/full の `add_columns_if_missing` 直後に `_eda_listed_corp` を呼び出し、LISTED_CORP が 0 件なら警告ログを出す（重大指摘 #1 の実装として兼用）。

### #2 プランの行番号が現コードと乖離している箇所

- 箇所: プラン `Step C-2`「L179-L185」、`Step C-3`「L134-L140」、`Step C-6`「L216-L224」
- 現状: 実コードを確認すると、`owner_agg` は L176-L185（C-2 の L179 は実際は L176）、SET 句は L132-L139（C-3 の L134 は L132）、`verify_results` の SELECT は L215-L217（C-6 の L216 は L215）にある。プランの行番号が 2-4 行ずれている。
- 提案: `format:line-number-drift` に該当。実装時に行番号を再確認すること。プランには基準 commit hash が記載されていないため、行番号の乖離が起きやすい構造的問題でもある（後述の フォーマット評価参照）。

### #3 `build_feature_matrix` で `business_partner_ratio_in_top10` を常に計算する

- 箇所: `train_rf.py:L323-L328`（プラン D-3 の追加箇所）
- 現状: プランは「BINARY のみで初回実装、比率カラムは計算のみしておいて Step F で判断」としている。計算した `business_partner_ratio_in_top10` は `keep_cols`（L337）に含まれなければ `all_years.append(df[keep_cols])` で切り捨てられる。
- 提案: Step F で CONTINUOUS_FEATURES に追加する場合に `keep_cols` も変更が必要であることをプランが明示していない。Step F の「CONTINUOUS_FEATURES に `business_partner_ratio_in_top10` を追記」と同時に `keep_cols` の変更（実装 `build_feature_matrix` 内の L337 相当）も必要である旨を D-3 の注記に追加すること。

### #4 プランフォーマット評価: 基準 commit hash なし

- 箇所: プラン冒頭
- 現状: `code-reviewer` スキルの パターン2/4 フォーマットチェックに「冒頭に対象ファイルの基準 commit hash があるか」の項目がある。本プランは計画 MD のため厳密な適用はパターン2と異なるが、行番号を明記した改修計画（C-1〜C-6, D-1〜D-3）では commit hash なしで行番号を指定しており、実コードとの乖離リスクが高い。
- 提案: 実装着手時に `git log --oneline -1 scripts/tob_prediction/compute_owner_features.py` で基準 hash を確認し、プランの「実装記録」欄に記入してから変更を開始すること。

---

## 【修正例】

### #1 に対する修正案（Plan B ガード）

```python
# compute_owner_features.py: compute_and_update() 冒頭（ALTER TABLE 後、UPDATE 前）に追加
def _check_plan_b_complete(client: bigquery.Client) -> None:
    """Plan B 完了確認: LISTED_CORP が 50 件以上存在することを検証."""
    sql = f"""
    SELECT COUNT(*) AS n
    FROM `{EXTEND_TABLE}`
    WHERE TYPE = 'LISTED_CORP'
    """
    df = client.query(sql).to_dataframe()
    n = int(df["n"].iloc[0])
    if n < 50:
        raise RuntimeError(
            f"Plan B 未完了: LISTED_CORP={n}件 (基準: 50件以上)。"
            "Plan B を完了してから再実行してください。"
        )
    logger.info("plan_b_gate_passed", listed_corp_count=n)
```

### #2 に対する修正案（sample_tickers 変更）

```python
# compute_owner_features.py:L248 相当
# before
sample_tickers = ["9983", "4530", "4917", "7203"]
print(f"\n--- sample: {sample_tickers} の4銘柄で確認 ---")

# after
sample_tickers = ["4686", "1429", "9983", "4530"]  # ジャストシステム・日本アクア・ユニクロ・久光
print(f"\n--- sample: {sample_tickers} の4銘柄で確認（4686・1429がTRUEなら Plan B/C 正常） ---")
```

---

## 【確認できなかった事項】

- `screen_tob.py` の実装内容: Step F-3 でスクリーナーを実行するが、スクリプトが BQ から新カラムを直接 SELECT するか、または `predictions_*.csv` のみを使うかが不明。BQ を直接参照する場合は新カラムへの対応が必要かもしれない。
- `SHAREHOLDER_COMPOSITION` の `TOP10_NAMES_JSON IS NULL` 行の割合: UPDATE 非対象となる行が多い場合、NULL のまま残留するが意図的なのか確認できない。
- `BUSINESS_PARTNER_RATIO_IN_TOP10` が 0.0 の場合と NULL の場合の区別: EXTEND JOIN が未ヒットで `LISTED_CORP` が 1 件もない企業は `IFNULL(..., 0.0)` で 0.0 になる。これは「データなし（NULL）」と「LISTED_CORP 株主が存在するが保有比率 0」を区別できない。RF での feature imputation への影響は軽微と推定するが、厳密には確認が必要。
