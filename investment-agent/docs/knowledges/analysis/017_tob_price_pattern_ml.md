# 017 TOB価格パターン ML予測モデル（LightGBM）

**カテゴリ**: analysis
**作成日**: 2026-05-22
**ステータス**: △ 部分採用（インサイダー検知モデルと並走運用中）
**プラン**: `docs/plans/analysis-017_tob_price_pattern_ml_20260520_225804.md`（完了 2026-05-21 / コミット 818c671e）
**関連知見**:
- [`015_tob_insider_screener.md`](015_tob_insider_screener.md) — ハンドクラフト静止×発火スコア（並走対象）
- [`007_tob_ml_prediction.md`](../analysis/007_tob_ml_prediction.md) — ファンダメンタルズ系 TOB ML（別モデル）
- [`016_vcp_insider_daily.md`](016_vcp_insider_daily.md) — VCP 日次スクリーナー（補完ツール）

---

## 概要

価格・出来高パターン（~40変数）で TOB ターゲット株を事前予測する LightGBM モデル。
ハンドクラフトの初動スコア（015, lift=1.12）を ML で置き換え、識別力を向上させることが目的。

**位置づけ**: ハンドクラフト（015）の上位互換ではなく **並走・合成シグナル**。
完全置換ライン lift@5% > 2.0 には未達（1.73）のため部分採用。

---

## 結果サマリ ★必須

| 指標 | 目標 | 実績 | 判定 |
|------|------|------|------|
| OOS lift@5% | > 2.0 | **1.73** | △ 部分採用 |
| OOS lift@1% | — | 2.99 | — |
| PR-AUC | > 0.05 | 0.00376 | △（クラス比 1:378 で圧縮） |
| vs. ハンドクラフト | +0.6 | +0.61 (+54%) | ◯ |

**学習期間**: 2022-01-01〜2024-12-31 / **検証期間**: 2025-01-01〜2026-05-14
（COVID 期 2020-2021 除外がベスト）

---

## スクリプト

| スクリプト | 役割 |
|-----------|------|
| `scripts/tob_prediction/build_ml_dataset.py` | ラベル+特徴量（~40変数）parquet 生成 |
| `scripts/tob_prediction/train_lgbm.py` | LightGBM 学習 + Optuna + SHAP |
| `scripts/tob_prediction/predict_tob_ml.py` | 当日全銘柄推論 → `tob_ml_screen_<date>.csv` |

**ベストモデル**: `data/models/tob_ml_lgbm_best.pkl`
**SHAP画像**: `data/output/tob_ml_shap_20260521_153931.png`

---

## 特徴量（~40変数）

- **既存スコア構成要素** (15): bb_width, bb_width_rank, vol_level, vol_ratio_20d, dormant_days, atr_pct 等
- **発火スコア要素** (7): vol_score, bb_score, donchian_score, candle_score, car_score, ignition, ignition_v3
- **総合スコア** (3): momentum, momentum_v2, momentum_v3
- **追加軽量** (7): 業種（target encoding）, 市場区分（one-hot）, 出来高代金, 時価総額 proxy, リターン(5/10/20d), ボラ(10/20d), TOPIX相関(rolling 60d)

---

## ラベル設計

- **positive**: 各 TOB の `IR_FIRST_RELEASE_DATE - [1, 30] 営業日` の各日（199件 × 30日）
- **negative**: 同 evaluation date の他銘柄
- **leak 防止**: TOB 公表後の同銘柄サンプルは negative にも含めない
- **leak 検証スクリプト**: `scripts/tob_prediction/_leak_check.py`

---

## 既知の問題・残課題

| 問題 | 対応プラン | 状態 |
|------|-----------|------|
| `predict_tob_ml.py` 推論時に TOB 既発 19 銘柄が混入（リーク） | `docs/plans/analysis-015_predict_leak_fix_20260521_174525.md` (コミット d2df7d66) | **未着手** |

---

## 採用判定基準（再掲）

| lift@5% | 方針 |
|---------|------|
| 1.00〜1.29 | 撤退（ハンドクラフトのみ本番） |
| **1.30〜1.99** | **部分採用 ← 現在地（1.73）** |
| 2.00+ | 完全採用（ハンドクラフト置き換え） |

---

## 使用データ

| データ | テーブル |
|--------|---------|
| 全銘柄 OHLCV（廃止銘柄含む） | `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` |
| TOPIX 日次終値 | `gmailpj-357912.STOCK.INDEX_PRICE` (INDEX_CODE='0000') |
| TOB IR 日 | `gmailpj-357912.STOCK.DELISTED_STOCKS_TOB_ENHANCE.IR_FIRST_RELEASE_DATE` |
| 銘柄マスタ（業種・市場区分） | `gmailpj-357912.STOCK.STOCK_CODE_LIST` |
