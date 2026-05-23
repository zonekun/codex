# 007-M: TOB予測モデル 精度推移ログ

**カテゴリ**: analysis
**作成日**: 2026-04-25（007_tob_ml_prediction.md より分離: 2026-05-18）
**親知見 MD**: `docs/knowledges/analysis/007_tob_ml_prediction.md`
**スクリプト**: `scripts/tob_prediction/train_rf.py`

---

## 精度推移サマリ（最新が上）

| 日付 | 変数数 | ROC-AUC | PR-AUC | 変更点 |
|------|--------|---------|--------|--------|
| 2026-05-18 | 26 | **0.758** | **0.082** | ASSET_MGMT 619件昇格後の特徴量で再学習 |
| 2026-05-16 | 26 | 0.755 | 0.079 | オーナー色系4変数追加、median imputation |
| 2026-05-16 | 22 | 0.752 | 0.072 | imputation変更（旧モデル比較ベース） |
| 2026-04-25 | 22 | ～0.752 | ～0.072 | 初版実装（Walk-Forward、Optuna） |

---

## ラン詳細ログ

年別 Walk-Forward テーブル・SHAP 重要度の全ランは子 MD に自動追記される:

**`docs/knowledges/analysis/007_tob_run_log.md`**

> `train_rf.py` 実行完了時に自動追記。`--note "変更点"` で変更点を記録。

### 論文ベース精度（元論文 2018〜2024年評価、参考値）

| 指標 | 値 | 備考 |
|-----|------|------|
| ROC-AUC | 0.60〜0.75 | 年別に変動 |
| PR-AUC | 0.04〜0.09 | TOB発生件数が少なく低め |

---

## 特徴量一覧（現行 26変数、2026-05-18時点）

| カテゴリ | 変数名 |
|---------|--------|
| 財務系 (10) | equity_ratio, pbr, roe, payout_ratio, ln_market_cap, cash_rich_ratio, forecast_div_yield, forecast_profit_growth, cfo_to_mcap, operating_margin |
| 市場系 (4) | ret_60d, ret_240d, vol_240d, turnover_ratio |
| 株主構成系 (8) | top_shareholder_ratio, individual_ratio, foreign_ratio, financial_inst_ratio, other_corp_ratio, top10_concentration, has_activist, top_shareholder_is_public |
| オーナー色系 (4) | owner_count_in_top10, owner_ratio_in_top10, real_top_is_individual, has_famous_investor |

**データソース**: `STOCK.FIN_SUMMARY` (FY) + `STOCK.SHAREHOLDER_COMPOSITION` + `STOCK.STOCK_PRICE_JQUANTS` + `STOCK.STOCK_CODE_LIST` + `STOCK.DELISTED_STOCKS`（ラベル 289件）

**注意事項**:
- STOCK_CODE_LIST は現在上場銘柄のみ保持。廃止済みTOB対象銘柄の業種コードが欠損するリーケージあり（業種ダミーは除外して対応済み。根治は廃止済み銘柄の歴史的業種コード取得）
- キャッシュ: `C:\tmp\tob_prediction\*.csv`（BQ再クエリ回避）。`--refresh` で強制更新

---

## TOB発生有無別の予測確率とリターンの関係（論文再現、参考）

| サンプル区分 | 回帰直線の傾き | 解釈 |
|------------|-------------|-----|
| TOBが実際に発生した銘柄 | 左肩上がり（サプライズ方向） | 予測できなかった意外なTOBほど株価急騰。統計的に有意 |
| TOBが実際に発生しなかった銘柄 | **右肩上がり** | 予測確率が高い銘柄ほど株価上昇。TOB未発生でも市場がTOB可能性を先読み。統計的に有意 |
