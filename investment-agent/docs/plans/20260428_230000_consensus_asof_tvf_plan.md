# CONSENSUS as-of TVF 実装プラン

**ステータス**: TVF作成済み（2026-04-28 JST）

## 目的

`batch_rerun_predict.py` の CONSENSUS 参照を `SOURCE='RAKU'` 固定から、指定日 as-of の IFIS 優先マージへ移行する。

現行の `STOCK.V_CONSENSUS_MERGED` は「現在最新」の VIEW であり、過去予測日の `DATAAT <= predict_date` を再現できない。BigQuery Table Function `STOCK.fn_consensus_merged_asof(as_of_date DATE)` を作成し、同じ IFIS 優先ロジックを指定日以前のデータだけで評価する。

## 前提確認

- 対象テーブル: `gmailpj-357912.STOCK.CONSENSUS`
- 粒度: `(TICKER, FY, QUARTER, TARGET)`
- `PROFIT`: 経常利益コンセンサス、百万円、累積値
- `TARGET`: `CURRENT` / `NEXT`
- `SOURCE`: `RAKU` / `IFIS`
- RAKU は `CURRENT` + `NEXT`、IFIS は `CURRENT` のみ
- `DATAAT`: 取得日。各 SOURCE の実行単位で全行同一日付

## 実装方針

### P0-1. BigQuery TVF 作成

`gmailpj-357912.STOCK.fn_consensus_merged_asof(as_of_date DATE)` を作成する。

返却カラムは、既存 `STOCK.V_CONSENSUS_MERGED` と同じ考え方に揃え、`(TICKER, FY, QUARTER, TARGET)` 粒度のロング形式にする。

| カラム | 内容 |
|---|---|
| `TICKER` | 銘柄コード |
| `FY` | 決算期、例 `202603` |
| `QUARTER` | `1Q` / `2Q` / `3Q` / `FY` |
| `PROFIT` | IFIS優先・RAKU補完後の経常利益コンセンサス、百万円 |
| `TARGET` | `CURRENT` / `NEXT` |
| `AS_OF_DATE` | TVF引数で渡した基準日 |

`SOURCE_USED` と `DATAAT` は返却しない。デバッグで必要な場合は別途検証SQLで確認する。

SQL方針:

1. `SOURCE='RAKU' AND DATAAT <= as_of_date` から `(TICKER, FY, QUARTER, TARGET)` ごとの最新行を抽出
2. `SOURCE='IFIS' AND DATAAT <= as_of_date` から同じ粒度で最新行を抽出
3. `(TICKER, FY, QUARTER, TARGET)` で FULL OUTER JOIN
4. `PROFIT` / `FY` / `QUARTER` / `TARGET` は IFIS 優先、IFIS 欠損時 RAKU
5. `SOURCE_USED` と `DATAAT` は返さず、`AS_OF_DATE` を付与して返す

`V_CONSENSUS_MERGED` の代表 `DATAAT = 全SOURCE中MAX` はキャッシュ鮮度用なので、TVFでは返さない。

### P0-2. `batch_rerun_predict.py` の参照先変更

現行:

- `STOCK.CONSENSUS` を直接参照
- `SOURCE='RAKU'`
- `DATAAT <= DATE_MAX_PREDICT`
- pandas 側で各 `predict_date` の `DATAAT <= ph` 最新を選ぶ

変更後:

- BQ側で `fn_consensus_merged_asof(DATE_MAX_PREDICT)` を呼ぶ
- per-group top 20 の考え方は原則維持しない

理由: TVFは指定日時点で各 `(TICKER, FY, QUARTER, TARGET)` の最新1行だけを返すため、`DATE_MAX_PREDICT` 1回呼び出しだけでは各 `predict_date` ごとの履歴候補が残らない。したがって以下どちらかを選ぶ。

推奨案:

- `DATE_PAIRS` の predict_date ごとに TVF を呼ぶ
- `predict_date` 列を付けて pandas 側に保持
- `compute_features()` では `shared["df_cons"]` を `predict_date` で絞って使う

代替案:

- TVFとは別に「履歴候補を返すTVF」を作る
- ただし複雑になり、今回の目的である `fn_consensus_merged_asof(date)` から外れる

### P0-3. pandas 側の結合変更

`fetch_shared_data()` で全 `DATE_PAIRS` の predict_date について TVF 結果を取得し、以下の列を保持する。

- `PREDICT_DATE`
- `TICKER`
- `FY`
- `QUARTER`
- `TARGET`
- `AS_OF_DATE`
- `CONSENSUS_PROFIT`

`compute_features(predict_date, shared)` では、現行の `DATAAT <= ph` + `drop_duplicates()` をやめ、`PREDICT_DATE == ph` の行を `(TICKER, QUARTER, TARGET)` 単位の辞書にする。

既存ロジックとの対応:

- `cons_map.get((tk, cur_per, "CURRENT"))` は `TARGET='CURRENT' AND QUARTER=cur_per` の `PROFIT`
- FY の `cons_map.get((tk, "FY", "NEXT"))` は `TARGET='NEXT' AND QUARTER='FY'` の `PROFIT`
- FY fallback の `cons_map.get((tk, "FY", "CURRENT"))` は `TARGET='CURRENT' AND QUARTER='FY'` の `PROFIT`

### P1-1. ドキュメント更新

更新対象:

- `data_catalog.md`
  - `STOCK.fn_consensus_merged_asof(date)` の仕様を `STOCK.CONSENSUS` セクションへ追記
- `docs/knowledges/tools/022_consensus_load.md`
  - TODOを実装済み仕様に更新
- `docs/knowledges/tools/059_earnings_model_eda.md`
  - batch再実行の CONSENSUS 取得方法を TVF方式へ更新

## 検証計画

### SQL単体確認

1. TVFが存在することを確認
2. `2026-04-13` など過去日で件数を確認
3. `TARGET='NEXT'` が RAKU 由来で残ることを確認
4. IFIS/RAKU 両方が存在する `CURRENT` で IFIS が優先されることを確認
5. 内部検証SQLで `DATAAT > as_of_date` の採用行がないことを確認

### 既存ロジックとの差分確認

RAKU固定時との差分は意図的に発生し得るため、差分を「異常」とは扱わない。確認する単位は以下。

- 行粒度が `(predict_date, TICKER, QUARTER, TARGET)` で一意
- TVF返却粒度が `(AS_OF_DATE, TICKER, FY, QUARTER, TARGET)` で一意
- `NEXT` のカバレッジが現行RAKU固定から減らない
- `CURRENT` が IFIS 優先になっている
- `consensus_deviation` の NaN率が極端に悪化しない

### スクリプト smoke

破壊的なGCS書き込みを避けるため、最初に小範囲実行モードを追加するか、一時的に `DATE_PAIRS` を1日に絞ったローカル検証を行う。

確認対象:

- `fetch_shared_data()` が完走する
- `compute_features()` の `cons_map` が空にならない
- prediction JSON の `consensus_deviation` / `f4_source` が生成される
- `SOURCE_USED` / `DATAAT` は本番TVF返却列に含めず、必要な場合は検証SQLだけで確認する

## ロールバック

- TVFは `DROP TABLE FUNCTION` または `CREATE OR REPLACE TABLE FUNCTION` で戻せる
- `batch_rerun_predict.py` は git revert
- GCSに生成された検証用 prediction/actual JSON は timestamp 付き追加保存のため既存ファイルを上書きしない

## 実装結果

- 作成SQL: `scripts/sql/create_fn_consensus_merged_asof.sql`
- 作成対象: `gmailpj-357912.STOCK.fn_consensus_merged_asof(as_of_date DATE)`
- 返却粒度: `(AS_OF_DATE, TICKER, FY, QUARTER, TARGET)`
- 返却列: `TICKER`, `FY`, `QUARTER`, `PROFIT`, `TARGET`, `AS_OF_DATE`
- 実行結果: `CREATE OR REPLACE TABLE FUNCTION` 成功
- smoke確認（`AS_OF_DATE = DATE '2026-04-13'`）:
  - row_count: 4,667
  - ticker_count: 1,385
  - duplicate_key_count: 0
  - TARGET別: CURRENT 3,294 / NEXT 1,373

## 未決事項

1. `batch_rerun_predict.py` に正式な `--dry-run` / `--date` オプションを追加してから検証するか、今回だけ一時的な小範囲実行で済ませるか
2. デバッグ用に SOURCE / DATAAT 付きの別TVFまたは検証SQLを残すか
   - 本番TVFの返却列には含めない
