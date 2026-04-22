# Colab `earnings_model_predict.ipynb` actual_return NaN バグ調査

**親**: [059_earnings_model_eda.md](../knowledges/tools/059_earnings_model_eda.md) 反省会ログ 2026-04-14 枝番 02
**作成日**: 2026-04-15
**対象ファイル**: `scripts/earnings_model/earnings_model_predict.ipynb` セル9（Step 3 答え合わせ）
**発覚**: 2026-04-14 発表分の反省会で 156/181（86%）が `actual_return=NaN`
**状態**: 解消済（2026-04-16 ガード節実装完了。Cell9 冒頭に 3-2a ブロック追加）

## 症状サマリ

| 区分 | 件数 | NaN | NaN率 | 予測側に価格あり |
|------|------|-----|-------|-----------------|
| ザラバ（is_intraday=True） | 26 | 1 | 3.8% | 0/1 |
| 引け後（is_intraday=False） | 155 | **155** | **100%** | **147/155** |

→ **引け後銘柄が全滅**。しかもそのうち147件は prediction 側に `adj_close` / `prev_close` が入っている（BQには当日価格データあり）→ 予測側データはOK、actual計算側の問題。

## 原因（確定）

**ノートブックの Step 3（答え合わせ）を翌営業日の価格データ投入前に実行した**。

- actual JSON の `created_at`: `2026-04-14T20:35:38+09:00`
- つまり2026-04-14（予測日）の夜に actual 計算を実行
- コードは 2026-04-15（翌営業日）の `STOCK_PRICE_JQUANTS` をクエリ:
  ```python
  q_actual = f"""
  SELECT a.TICKER, a.ADJ_CLOSE AS actual_close, p.ADJ_CLOSE AS prev_close
  FROM gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS a
  JOIN gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS p
    ON a.TICKER = p.TICKER AND p.DATE = '{PREDICT_DATE_HYPHEN}'
  WHERE a.DATE = '{ACTUAL_DATE_HYPHEN}'   -- 2026-04-15 がまだ未投入
    ...
  """
  ```
- `a.DATE='2026-04-15'` のレコードがまだBQに存在しなかった → JOIN結果ほぼ空 → 引け後全銘柄NaN

### 知見ファイル（059）の運用ルール

> 1. **当日引け後**: `PREDICT_DATE` を設定 → セル1〜6を実行（予測生成 + GCS保存）
> 2. **翌営業日引け後**: `ACTUAL_DATE` を設定 → セル8〜10を実行（答え合わせ + 精度集計）

→ 運用ルール上は翌営業日引け後に Step 3 を実行する設計。今回は予測日夜に Step 3 まで実行したため未来データ参照になった。**コードのバグというより運用ミス + コードが未然防止してくれない問題**。

## ザラバ1件NaNの個別原因

ザラバ26件中1件だけNaN。予測側にも価格なし。該当tickerは要特定（actual_close or prev_close が prediction JSON 段階で取れていない銘柄）。別途調査。

## 対応方針

### 即時対応（今日、運用リカバリ）
- 2026-04-15 の価格データが投入済みの状態で `earnings_model_predict.ipynb` Step 3 を**再実行** → GCS `actuals/` に新しい actual JSON が上書き保存される
- ローカル `download_review_data.py 20260414` で再DL（最新タイムスタンプのファイルが取得される）
- `review_report.py 20260414 --out-dir <path>` で再生成

### 恒久対応（コード修正）
`earnings_model_predict.ipynb` セル9 の冒頭に **guard 節** を追加:

```python
# Step 3 実行前の前提条件チェック
assert PREDICT_DATE != ACTUAL_DATE, (
    f"ACTUAL_DATE が PREDICT_DATE と同じ。翌営業日引け後に実行すべき。"
)

# ACTUAL_DATE 分の STOCK_PRICE_JQUANTS が投入済みか事前チェック
q_check = f"""
SELECT COUNT(*) AS cnt
FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS`
WHERE DATE = '{ACTUAL_DATE_HYPHEN}'
"""
n_rows = bq.query(q_check).to_dataframe().iloc[0]['cnt']
if n_rows < 3000:  # 全銘柄約4000なので3000未満ならまだ投入前
    raise RuntimeError(
        f"ACTUAL_DATE={ACTUAL_DATE_HYPHEN} の STOCK_PRICE_JQUANTS 投入不完全 ({n_rows}行)。"
        f"株価データ投入完了後に再実行してください。"
    )
```

これで翌営業日引け後データ投入前の誤実行を未然に検知できる。

### 付随対応
- **ザラバ1件NaN**: 予測側で `adj_close` / `prev_close` が取れない条件（上場初日・値付かず等）の特定と明示化
- **過去の NaN データ**: GCS `actuals/` の他日付ファイルも同様の問題がないか要確認（方向一致率が歪んでいる可能性）
- **知見ファイル（059）更新**: 「Step 3 実行タイミング必須条件」を運用ルール節に追記

## 関連

- 反省会スクリプト: `scripts/earnings_model/review_report.py`（NaNを「要確認」分類に分離する応急対応は実装済）
- memory: `project_colab_actual_return_nan.md`
