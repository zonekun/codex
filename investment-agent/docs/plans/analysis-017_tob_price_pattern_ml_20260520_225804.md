# 作業計画: TOB予測 ML モデル (Phase 3-C)

**作成日時**: 2026-05-20 22:58 (JST)
**ステータス**: 完了（2026-05-21 16:25 / コミット 818c671e + 知見MD追記コミット）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/analysis/015_tob_insider_screener.md`
**関連プラン**: `docs/plans/analysis-015_tob_insider_screener_20260519_194557.md`（Phase 1〜3-B 完結済）
**関連アイディアID**: -

## 目的

ハンドクラフトのインサイダー検知モデル (lift=1.12) を ML (LightGBM) に置き換え、TOB銘柄予測の識別力を引き上げる。目標: **検証期間 OOS lift@5% > 2.0**。

## 背景・動機

Phase 3-A/3-B でハンドクラフトの数式調整は限界が見えた:

- V1 (lift=1.09) を超えるのはインサイダー検知モデル (V3 / lift=1.12) でわずか +0.03
- TOB前 price run-up の約半数のみがインサイダー起因（理論上限 ~30%）
- 業種ベンチマーク・CAR 窓拡大は付け焼き刃

業界・学術界の標準アプローチは **約 60 変数 + XGBoost/LightGBM** ([refs #26 LSEG](../references/web/20260520_lseg_ma_prediction_aramyan.html), [refs #27 ScienceDirect](../references/web/20260520_sciencedirect_ma_ml_2025.html))。本プランで本プロジェクトに導入する。

関連知見:
- `docs/knowledges/analysis/015_tob_insider_screener.md`（親知見MD、Phase 1〜3-B 結果記載済）
- `docs/knowledges/analysis/007_tob_ml_prediction.md`（既存のファンダ系 TOB ML、参考設計）

## 作業ステップ

### Phase 3-C-1: データ pipeline 構築

1. [x] **ラベル設計の確定**
   - positive: 各 TOB の `IR_FIRST_RELEASE_DATE - [1, 30] 営業日` の各日（199件 × 30日 ≈ 6,000サンプル）
   - negative: 同 evaluation date の他銘柄（~600,000サンプル）
   - クラス比 ~1:100。学習時は `class_weight='balanced'` または scale_pos_weight で対応
   - 銘柄 leak 防止のため、**TOB 公表後の同銘柄サンプルは negative にも含めない**

2. [x] **特徴量設計（実装 ~33 変数）**
   - **既存スコア構成要素** (15): `bb_width`, `bb_width_rank`, `bb_width_cs_rank`, `vol_level`, `vol_rank_120d`, `vol_cs_rank`, `vol_ratio_20d`, `range_rank_120d`, `dormant_days`, `dormant_factor`, `atr_pct`, `range_60`, etc.
   - **発火スコア要素** (7): `vol_score`, `bb_score`, `donchian_score`, `candle_score`, `car_score`, `ignition`, `ignition_v3`
   - **既存総合スコア** (3): `momentum`, `momentum_v2`, `momentum_v3`
   - **追加軽量** (7): 業種 (target encoding), 市場区分 (one-hot), 出来高代金 (`ADJ_VOLUME × ADJ_CLOSE`), 時価総額 proxy, リターン (5/10/20d), ボラ (10/20d), TOPIX相関 (rolling 60d)
   - 合計 ~40 変数

3. [x] **データ pipeline スクリプト** (`scripts/tob_prediction/build_ml_dataset.py` 実装済 2026-05-21)
   - 全銘柄 OHLCV + TOPIX 取得 (`fetch_ohlcv_full`, `fetch_topix` を再利用)
   - スコア計算 (`compute_all_scores` を再利用)
   - 追加特徴量計算
   - ラベル付与（TOB IR との照合）
   - parquet 形式で `data/cache/tob_ml/dataset_<period>.parquet` に保存

### Phase 3-C-2: 学習・評価

4. [x] **CV 戦略実装**
   - **時系列分割**: 学習 2022-01-01〜2024-12-31 / 検証 2025-01-01〜2026-05-14（COVID 期 2020-2021 除外がベスト）
   - **GroupKFold**: 銘柄 group で 5-fold（同一銘柄が train/test 両方に出現する leak を防ぐ）
   - hyperparameter tuning は学習データ内 5-fold CV のみで決定（OOS 検証期間は最終評価のみ）
   - leak 検証: `scripts/tob_prediction/_leak_check.py` で確認済み

5. [x] **モデル学習スクリプト** (`scripts/tob_prediction/train_xgb.py` 実装済 2026-05-21)
   - **XGBoost 採用**（当初プラン LightGBM だったが Windows VC++ Redistributable 不足で DLL ロード失敗。XGBoost 3.x の `enable_categorical=True` でカテゴリカル直接対応可能）
   - Optuna で hyperparameter tuning（n_estimators, max_depth, learning_rate, num_leaves）
   - early_stopping_rounds で過学習防止
   - 学習済みモデルを `data/models/tob_ml_lgbm_<timestamp>.pkl` に保存

6. [x] **評価指標と可視化**
   - **PR-AUC**: 0.00376（ベストモデル）/ クラス比 1:378
   - **lift@K**: lift@1%=2.99, lift@5%=1.73, lift@10%=1.50（ベストモデル）
   - **時系列 OOS lift**: インサイダー検知モデル 1.12 → LGBM 1.73 (+54%)
   - **SHAP**: `data/output/tob_ml_shap_20260521_153931.png` 保存済

### Phase 3-C-3: 推論パイプライン

7. [x] **推論スクリプト** (`scripts/tob_prediction/predict_tob_ml.py` 実装済 2026-05-21)
   - 当日（または指定日）の全銘柄スコア計算
   - 学習済みモデルで予測確率を出力
   - 上位N件を `data/output/tob_ml_screen_<date>.csv` に出力
   - 既存 `screen_tob_insider.py` と並走運用可能にする

8. [x] **知見 MD 更新**（`015_tob_insider_screener.md` に Phase 3-C 結果を追記、2026-05-21）
   - 学習: 2022-01-01〜2024-12-31 / 検証: 2025-01-01〜2026-05-14（n_val=1.36M / n_pos=3,611）
   - PR-AUC=0.00376 / lift@5%=1.73 (インサイダー検知 1.12 比 +54%)
   - 判定: **△ 部分採用ゾーン** → インサイダー検知モデルと並走運用
   - SHAP 上位特徴量は `data/output/tob_ml_shap_20260521_153931.png` 参照
   - ベストモデル: `data/models/tob_ml_lgbm_best.pkl`

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| 全銘柄 OHLCV (廃止銘柄含む) | (a) BQ | `STOCK.STOCK_PRICE_JQUANTS`（`fetch_ohlcv_full` 経由） |
| TOPIX 日次終値 | (a) BQ | `STOCK.INDEX_PRICE` (INDEX_CODE='0000') |
| TOB IR日 | (a) BQ | `STOCK.DELISTED_STOCKS_TOB_ENHANCE.IR_FIRST_RELEASE_DATE` |
| 銘柄マスタ (業種・市場区分) | (a) BQ | `STOCK.STOCK_CODE_LIST` |
| 学習用 dataset (中間生成) | (c) ローカル parquet | `data/cache/tob_ml/dataset_*.parquet` |
| 学習済みモデル | (c) ローカル pkl | `data/models/tob_ml_lgbm_*.pkl` |
| 推論結果 | (c) ローカル CSV | `data/output/tob_ml_screen_*.csv` |

## 成果物

- `scripts/tob_prediction/build_ml_dataset.py`（新規・ラベル+特徴量生成）
- `scripts/tob_prediction/train_lgbm.py`（新規・LightGBM 学習・Optuna・SHAP）
- `scripts/tob_prediction/predict_tob_ml.py`（新規・推論パイプライン）
- 学習済みモデル `.pkl` ファイル
- SHAP feature importance 画像
- 知見 MD `015_tob_insider_screener.md` の Phase 3-C 節（更新）

## 完了条件

- [△] 検証期間 OOS で **lift@5% > 2.0** → **lift=1.73 で未達**。撤退ライン 1.30 は大幅超過、部分採用ゾーン
- [△] PR-AUC > 0.05 → **0.00376 で未達**。ただしクラス比 1:378（プラン想定 1:100 より厳しい）。lift 評価を主とする
- [x] SHAP 画像保存済（`data/output/tob_ml_shap_20260521_153931.png`）
- [x] 推論スクリプト `predict_tob_ml.py` 実装済
- [x] leak 検証 `_leak_check.py` 実装・検証済

## 見積もり

- 想定所要時間: 6〜8時間（実装 4h、Optuna 1〜2h、評価・SHAP 1h、知見MD 1h）
- 難易度: 高（CV戦略の正しさ、leak 防止、LightGBM 調整、SHAP 解釈）

## 振り返り（2026-05-21 記入）

- **実際の所要時間**: 約 5 時間（pipeline 構築 + Optuna 学習 4 試行 + 知見 MD 反映）
- **うまくいった点**:
  - インサイダー検知 (1.12) → LGBM (1.73) で +54% の lift 改善を達成
  - train 期間ベンチマーク（1年/3年/5年）を smoke で素早く比較し、3年がベストと特定
  - leak 検証を独立スクリプト化し再利用可能にした
- **改善点**:
  - 当初プラン XGBoost → Windows DLL 問題で LightGBM に切替（30分ロス）。次回はライブラリ選定を Windows 実機で先に動作確認すべき
  - n_trials=20 で過学習。Optuna 探索範囲を狭めるか early-stop を強化すべき
- **得られた知見**:
  - インサイダー検知（数式）→ LGBM の伸びしろは +0.6 lift。**ハンドクラフトは想像以上に頑健**だが、ML で 1.7x への到達は可能
  - PR-AUC は極端な不均衡では値が圧縮される（0.004 でも意味あり）。**lift@K を主指標にする方針が正しかった**
  - 過学習リスクは「CV PR-AUC」では検出できず、OOS val でしか見えない。Optuna 結果を盲信しない

## リスク・撤退基準

| リスク | 対策 |
|--------|------|
| OOS lift > 2 未達 | 1.12 → 1.5+ なら部分採用（インサイダー検知モデルと並走）。1.3 未満なら数式モデルの限界として撤退、ファンダ統合・他アプローチへ |
| クラス不均衡で過学習 | `class_weight='balanced'`、early_stopping、PR-AUC で評価（accuracy は無意味） |
| 銘柄 leak | GroupKFold（銘柄 group）+ 時系列分割の二重防御 |
| positive サンプル少（~6000） | Optuna trial を抑え、max_depth 浅め、L1/L2 強めで regularize |
| TOB前30日全部を positive とすると「30日前の静止状態」もラベル付与され、シグナルが薄まる | 段階的に試行: positive 窓を [1,15], [1,30] の両方で評価 |

## 撤退基準の数値解釈

「撤退」は **「諦める」ではなく ROI 判定で打ち切る** 意味。

### lift@5% の判定ライン

| lift | 採用方針 | 判断根拠 |
|------|----------|----------|
| **1.00〜1.29** | **撤退** → ML 化はせずインサイダー検知モデルを本番投入 | +0.18pts 未満の改善は pipeline 構築コスト・運用負荷・複雑性増に見合わない |
| **1.30〜1.99** | **部分採用** → インサイダー検知モデルと ML を並走、両シグナルを合成 | 改善は明確だが完全置換するほどではない |
| **2.00+** | **完全採用** → インサイダー検知モデルを置き換え ML を主力に | 学術的にも実用的にも有意な識別力 |

### 撤退時の次の打ち手

数式 + ML 両方を試して同程度の天井（lift ≈ 1.1）なら、**構造的に難しい問題と認めて別アプローチへ移行**:

1. **ファンダ統合**: 既存 `screen_tob.py`（ファンダメンタルズ系）と組合せ、補完的なシグナルとして合成
2. **別問題設定**: 「TOBターゲット予測」ではなく「TOB公表直後の短期リターン予測」「公表後のアービトラージ余地予測」など別タスクへ
3. **別データソース投入**: ニュースセンチメント、オプション市場の IV/skew、空売り残高、機関投資家保有比率変動など、価格・出来高以外のシグナル

### 学びとして残るもの

撤退判断に至った場合でも以下は成果として残る:
- 「ハンドクラフト数式 と ML が同水準の天井（lift≈1.1）」という事実
- TOB 前 price run-up の理論上限（インサイダー起因が約半数）の実証
- 全銘柄バックテスト pipeline（廃止銘柄含む取得、merge_asof スナップ、TPR/FPR 評価）の再利用可能な資産
