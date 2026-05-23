# 作業計画: TOB ML 推論時の TOB 発表済み銘柄 leak 修正

**作成日時**: 2026-05-21 17:45 (JST)
**ステータス**: 未着手
**分類**: (b) 継続改修型（バグ修正）
**親知見 MD**: `docs/knowledges/analysis/015_tob_insider_screener.md`
**関連プラン**: `docs/plans/analysis-017_tob_price_pattern_ml_20260520_225804.md`（Phase 3-C 完了）

## 問題

`scripts/tob_prediction/predict_tob_ml.py` で 2026-05-21 推論を実行したところ、Top100 のうち **19銘柄が既に TOB 発表済**だった（2025-06〜2026-04 発表）。

### 検証根拠

BQ `STOCK.DELISTED_STOCKS_TOB_ENHANCE` で確認:

| TICKER | 銘柄 | TOB発表日 | ML順位 | proba |
|--------|------|----------|-------|-------|
| 6197 | ソラスト | 2026-03-24 | 1 | 0.074 |
| 1726 | ビーアールHD | 2026-02-04 | 3 | 0.034 |
| 6201 | 豊田自動織機 | 2025-06-03 | 69 | 0.0034 |
| 4384 | ラクスル | 2025-12-11 | 73 | 0.0034 |
| ほか 15 銘柄 | … | 2025-08〜2026-04 | … | … |

### 原因（構造的バグ）

- **学習側 OK**: `build_ml_dataset.py` L233 で `DATE >= IR_FIRST_RELEASE_DATE` を除外（leak 防止済み）
- **推論側 NG**: `predict_tob_ml.py` の `build_features_for_date()` が:
  - `fetch_ohlcv_full` で **廃止銘柄を含めて取得**（TSE フィルタなし）
  - latest snapshot に **TOB 発表後の異常値動き**（出来高急騰・上放れ）が混入
  - ML が「TOB 直前パターン」と誤判定し、過去 TOB 銘柄を上位に並べる

OOS lift=1.73 の評価自体は学習時 leak 防止が機能しているため正当。**実運用推論のみで問題化**。

## 修正方針

`predict_tob_ml.py` の `build_features_for_date()` に **TOB 発表済み銘柄を除外**するフィルタを追加。

### 採用案: TOB 発表済み除外（最小修正）

```python
def fetch_announced_tob_tickers(target_date: str) -> set[str]:
    """target_date 以前に TOB 発表済の TICKER を返す."""
    client = bigquery.Client(...)
    sql = f"""
      SELECT DISTINCT TICKER
      FROM `{BQ_PROJECT}.STOCK.DELISTED_STOCKS_TOB_ENHANCE`
      WHERE IR_FIRST_RELEASE_DATE IS NOT NULL
        AND IR_FIRST_RELEASE_DATE <= DATE(@target_date)
    """
    ...
    return set(df["TICKER"].astype(str))

def build_features_for_date(target_date, force_reload=False):
    ...
    feats = (
        feats[feats["DATE"] <= target_date]
        .sort_values("DATE")
        .groupby("TICKER", as_index=False)
        .last()
    )
    # === 追加: TOB 発表済み除外 ===
    announced = fetch_announced_tob_tickers(target_date)
    n_before = len(feats)
    feats = feats[~feats["TICKER"].astype(str).isin(announced)]
    log.info("filtered_announced_tob", removed=n_before - len(feats))
    return feats
```

### 補助案（必要なら追加）

- **STOCK_CODE_LIST の現存銘柄フィルタ**: マスタに存在しない廃止銘柄をさらに除外（二重防御）
- **ETF/JDR/出資証券除外**: `MARKET_CATEGORY` が `ETF・ETN` や `出資証券` を除外（TOB対象外なので推論結果から除く）

## 作業ステップ

1. [ ] `predict_tob_ml.py` に `fetch_announced_tob_tickers()` を実装
2. [ ] `build_features_for_date()` で除外適用 + 除外件数ログ
3. [ ] 2026-05-21 で再推論 → 既出 TOB 19銘柄が消えていることを確認
4. [ ] クロス参照 (`screen_tob_insider.py` Top1000 と join) を再計算
5. [ ] 015 知見 MD に「推論時 leak 防御」セクション追加
6. [ ] コミット (`fix: predict_tob_ml.py で TOB 発表済み銘柄を推論から除外`)

## 検証指標

- 修正前: Top100 中 TOB 既発 = **19銘柄**
- 修正後の期待値: Top100 中 TOB 既発 = **0銘柄**
- 既出 ML Top4 (6197 ソラスト / 1726 ビーアールHD など) が **消える**ことを目視確認

## 必要データ

| データ | ストレージ | パス/テーブル |
|--------|-----------|--------------|
| TOB IR 日 | (a) BQ | `STOCK.DELISTED_STOCKS_TOB_ENHANCE.IR_FIRST_RELEASE_DATE` |
| 推論対象 OHLCV | (c) parquet | `C:/tmp/tob_insider_screener/ohlcv_full_*.parquet` |
| 既存推論結果 | (c) CSV | `data/output/tob_ml_screen_20260521.csv`（修正前ベースライン） |

## リスク

| リスク | 対策 |
|--------|------|
| BQ クエリ追加で推論時間増 | 1秒以下の単純 SELECT なので無視可 |
| TOB 発表後・成立前の銘柄（取引中）の扱い | 仕様として除外する（TOB成立確定前でも IR 発表後は本来の予測対象外） |

## 撤退基準

- 修正後も TOB 既発銘柄が残る → `fetch_ohlcv_full` 側で廃止銘柄を取らない実装に切替
- STOCK_CODE_LIST に存在しない TICKER もまだ混じる → マスタ JOIN フィルタを追加（補助案）

## 想定所要時間

30〜45 分（実装 15min、テスト推論 5min、検証 5min、MD 更新 + コミット 10min）
