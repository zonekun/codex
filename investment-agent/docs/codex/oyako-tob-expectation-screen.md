# 親子上場 TOB 期待決算前上昇スクリーニング仕様

作成日: 2026-05-22
対象スクリプト: `scripts/analyze_oyako_tob_expectation.py`
主出力: `data/output/oyako_tob_expectation_classification.csv`
入力: `C:\Users\zonekun\Dropbox\stock\temp\oyako.txt`

## 目的

親子上場関連銘柄について、決算前に TOB 期待で買われ、決算発表後に期待が剥落する、または好決算で維持されるパターンが過去に出ている銘柄を分類する。

このスクリーニングは、まず「過去にこの癖がある銘柄の分類表」を作るためのもの。次回決算前に買われそうな銘柄の判断は、この分類表に予定日データを組み合わせて行う。

## 保護ルール

この仕様MDは Codex 専用文書として `docs/codex/` に置く。`docs/codex/**` は Markdown 同期と Claude Code 側ソース同期の保護対象。

対象スクリプト `scripts/analyze_oyako_tob_expectation.py` は Codex 専用 helper として扱う。Claude Code 側からのソース同期で削除・上書きされないよう、`docs/claude-md-sync.md` の `$codexProtected` に具体パスを入れる。

## データソース

- 対象 ticker: `oyako.txt` の1列目4桁コード。ファイルはタブ区切り、CP932/Shift-JIS。
- 実績決算日: `gmailpj-357912.STOCK.FIN_SUMMARY`
- 決算実績・決算予定日: `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
- 株価: `gmailpj-357912.STOCK.STOCK_PRICE`
- ベンチマーク: `gmailpj-357912.STOCK.INDEX_PRICE` の `INDEX_CODE = 'N225'`
- 銘柄名・業種: `gmailpj-357912.STOCK.STOCK_CODE_LIST`
- 時価総額: `gmailpj-357912.STOCK.YF_STOCK_INFO`
- 除外: `gmailpj-357912.STOCK.DELISTED_STOCKS` に存在する ticker

## BQアクセス方針

BQアクセスは多数回走らせない。

初回または `--refresh-cache` 指定時のみ、BQでイベント特徴量を一括取得し、`data/cache/oyako_tob_expectation_events.csv` にキャッシュする。以後のスコア調整・分類表再生成はこのキャッシュを使う。

`EARNINGS_DISCLOSURE_CALENDAR` と `YF_STOCK_INFO` 由来の補助列は、キャッシュとは別に実行時点のBQから取得する。

## 対象イベント

`FIN_SUMMARY` から以下を対象にする。

- `LOCAL_CODE` が対象 ticker
- `TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'`
- 1Q / 2Q / 3Q / FY すべて対象
- 同一 ticker / 同一日 / 同一四半期は1イベントへ dedup
- `DELISTED_STOCKS` に存在する ticker は除外

## 特徴量

各決算イベントについて、決算前後の市場対比リターンを作る。

- `abn_pre5`
- `abn_pre10`
- `abn_pre20`
- `abn_pre40`
- `abn_post1`
- `abn_post3`
- `abn_post5`
- `volume_ratio`

`abn_*` は銘柄リターンから日経平均リターンを引いた市場対比リターン。

## 上昇イベント判定

以下のどれかを満たすと `strong_runup_event` とする。

- `abn_pre5 >= 0.03`
- `abn_pre10 >= 0.04`
- `abn_pre20 >= 0.06`
- `abn_pre40 >= 0.08`

決算前上昇後、`abn_post5 <= -0.03` なら `fade_after_event` とする。

## 分類列

### `pattern_score`

決算前に買われる癖の強さを表す総合点。大きいほど過去の決算前に買われやすい。

主な構成要素:

- `runup_event_rate`
- `best_pre_abn_median`
- `volume_ratio_median`
- `fade_after_runup_rate`

目安:

- 40以上: かなり見る価値あり
- 35以上: 見る価値あり
- 30以上: 広めに拾うなら対象
- 30未満: 基本は後回し

今回の分布では、40点以上は約27銘柄、35点以上は約48銘柄、30点以上は約97銘柄だった。

### `pattern_class`

- `strong_pre_runup`: 決算前に買われる癖が強い
- `moderate_pre_runup`: 癖はあるが strong より弱い
- `no_clear_pattern`: 再現性が弱い

### `pattern_rank`

ソート用の数値ランク。

- `1`: `strong_pre_runup`
- `2`: `moderate_pre_runup`
- `9`: `no_clear_pattern`

### `timing_class`

エントリタイミングの型。

- `early_40d`: 決算約40営業日前から買われやすい
- `standard_20d`: 決算約20営業日前から買われやすい
- `late_10d`: 決算約10営業日前から買われやすい
- `late_5d`: 決算約5営業日前から買われやすい

### `best_pre_window_days`

5/10/20/40営業日前のうち、過去中央値で最も強かった期間。

### `best_pre_abn_median`

最も効いた決算前期間での市場対比リターン中央値。

目安:

- 0.035以上: かなり強い、strong級
- 0.020以上: 見る価値あり、moderate級
- 0.010未満: 上昇幅としては弱い

今回の分布:

- 中央値: 0.0088
- 75%点: 0.0164
- 90%点: 0.0260
- 95%点: 0.0362
- 最大: 0.0726

分類別中央値:

- `strong_pre_runup`: 0.0420
- `moderate_pre_runup`: 0.0248
- `no_clear_pattern`: 0.0069

### `runup_event_rate`

過去決算のうち、決算前上昇イベントになった比率。高いほど再現性が高い。

### `event_count`

判定に使った過去決算イベント数。少ない銘柄はスコアが高くても信頼度を下げて見る。

### `fade_class` / `fade_after_runup_rate`

決算前に上がった後、決算後に剥落しやすいかを見る補助列。

`fade_after_earnings` は、決算後に期待剥落しやすい分類。好決算で買われるケースもあるため、スクリーニングの必須条件にはしない。

## 次回予定日とエントリ日

`next_expected_earnings_date` は正式予定日ではない。出力ヘッダーでは `エントリ基準日算出の仮の決算予定日` と表示する。現在は `EARNINGS_DISCLOSURE_CALENDAR` の過去実績値ベースの仮置き。

ロジック:

1. `EARNINGS_DISCLOSURE_CALENDAR` の `CATEGORY = 'R'`、`RECORD_TYPE = 'A'` から銘柄ごとの最新実績決算を確認する。
2. 最新が 1Q なら次は中間決算、中間決算なら3Q、3Qなら本決算、本決算なら1Qとする。
3. 過去の同じ四半期の発表日だけを年送りする。
4. `as_of` 以降で最も近い日を採用する。
5. 土日に投影された場合は前営業日に寄せる。

このロジックにより、2020年の遅延決算日など別四半期の特殊日が次回予定日に混ざることを避ける。

`entry_date` は `next_expected_earnings_date` から `best_pre_window_days` 営業日前にした日。日本の祝日は未反映で、平日ベースの概算。

`next_earnings_date` は `EARNINGS_DISCLOSURE_CALENDAR` の `CATEGORY = 'R'`、`RECORD_TYPE = 'S'` から、`as_of` 以降で最も近い予定日を採用する。予定表に存在しない銘柄は空欄にする。

`market_cap_oku_yen` は `YF_STOCK_INFO.MARKET_CAP` の最新 `LOADED_DATE` を1億円で割った値。

## 実行

通常再生成:

```powershell
$env:PYTHONUTF8='1'
uv run python scripts/analyze_oyako_tob_expectation.py --as-of 2026-05-22
```

BQキャッシュを更新する場合:

```powershell
$env:PYTHONUTF8='1'
uv run python scripts/analyze_oyako_tob_expectation.py --refresh-cache --as-of 2026-05-22
```

Dropboxへ配布する場合:

```powershell
Copy-Item -LiteralPath 'C:\Users\zonekun\Documents\codex\investment-agent\data\output\oyako_tob_expectation_classification.csv' `
  -Destination 'C:\Users\zonekun\Dropbox\stock\temp\oyako_tob_expectation_classification.csv' -Force
```

## 運用上の見方

まず以下を見る。

1. `pattern_class`
2. `pattern_score`
3. `timing_class`
4. `entry_date`
5. `エントリ基準日算出の仮の決算予定日`
6. `runup_event_rate`
7. `best_pre_abn_median`
8. `event_count`
9. `fade_after_runup_rate`

初期運用では、以下を優先確認対象にする。

- `pattern_class = strong_pre_runup`
- または `pattern_score >= 35` かつ `pattern_class = moderate_pre_runup`

## 注意

`エントリ基準日算出の仮の決算予定日` は exit 判断には使わない。exit には正規の決算予定日 `next_earnings_date` を使う。
