# FY Conservative Guidance Screener

**作成日時**: 2026-05-14 13:14 JST
**ステータス**: 完了
**基準 commit**: `2a21bb8`
**対象ファイル**: `scripts/fy_conservative_guidance_screener.py`
**対象読者**: Codex 実装担当 / Claude Code 取り込み担当
**目的**: FY 決算で弱気ガイダンスを出して売られた後、期中上方修正または最終着地で前年並み以上へ戻るパターンを繰り返す日本株を一覧化する。

---

## 前提サマリ

- データ正本は BigQuery `gmailpj-357912.STOCK`。
- 主テーブル:
  - `FIN_SUMMARY`: FY 決算実績、初期ガイダンス、業績予想修正、最終着地の数値判定。
  - `TDNET_DOCUMENTS_ENHANCED`: `MAIN_CATEGORY` / `SUB_CATEGORIES` / `DOC_TITLE` / `DISCLOSURE_TIME` によるイベント補強と証拠本文。
  - `STOCK_PRICE`: FY 決算発表後のイベントリターン。
  - `STOCK_CODE_LIST`: 普通株 universe と ETF/REIT/PRO/外国株などの除外。
- 初版は BQ SQL 中心で年度イベント表を作り、Python は抽出実行、スコア集計、CSV/HTML 出力だけを担う。
- 完成後は Claude Code へ取り込ませる前提のため、成果物パス・実行コマンド・検証結果を明示する。

---

## 目標アウトプット

1. `data/output/fy_conservative_guidance/candidate_years.csv`
   - `TICKER`, `FISCAL_YEAR_END`, 初期予想、最終実績、上方修正有無、決算後リターン、判定理由。
2. `data/output/fy_conservative_guidance/company_scores.csv`
   - `TICKER`, `STOCK_NAME`, `hit_count`, `hit_rate`, 平均下落率、平均初期弱気幅、平均着地超過率。
3. `data/output/fy_conservative_guidance/report.html`
   - Claude Code / ブラウザ確認用の一覧。年度別 evidence を折りたたみ可能にする。
4. `docs/codex-to-claude-handoff.md`
   - 完成時に取り込み依頼として実行コマンド、出力パス、検証結果を記録する。

---

## 判定定義

### Universe

`STOCK_CODE_LIST` から普通株に限定する。

- 採用: `MARKET_CATEGORY` がプライム/スタンダード/グロースの内国株式。
- 除外: ETF/ETN、REIT/インフラ/ベンチャーファンド、PRO Market、外国株、出資証券、その他ファンド類。

### FY 決算イベント

`FIN_SUMMARY.TYPE_OF_DOCUMENT LIKE 'FYFinancialStatements_%'` かつ `TYPE_OF_CURRENT_PERIOD = 'FY'` を基礎にする。

TDnet 側は同日近傍で次を補助キーにする。

- `MAIN_CATEGORY = '決算短信'`
- `SUB_CATEGORIES` に通期/FY/連結/個別などの該当情報があれば利用
- `DOC_TITLE` に `決算短信`、`通期`、`FY` 相当の正規表現

### 初期弱気ガイダンス

FY 決算発表時点の翌期予想を、直前 FY 実績と比較する。

- 主指標: 営業利益。欠損時は経常利益、純利益の順にフォールバック。
- `initial_growth = initial_forecast_metric / previous_actual_metric - 1`
- 初期弱気:
  - `initial_growth <= 0`
  - または `initial_growth <= 0.05` かつ同社の過去成長率より明確に低い
  - 赤字予想や大幅減益予想は強い弱気として別フラグ

注意: `FORECAST_*` と `NEXT_YEAR_FORECAST_*` のどちらが翌期予想を持つかは、`CURRENT_FISCAL_YEAR_END_DATE` / `NEXT_FISCAL_YEAR_END_DATE` と実データで吸収する。列名だけで決め打ちしない。

### 暴落判定

`STOCK_PRICE` で FY 決算発表日の直前終値と、翌営業日/3営業日後終値を比較する。

- 初版閾値:
  - `ret_1d <= -0.07`
  - または `ret_3d <= -0.10`
- 改良版:
  - `INDEX_PRICE` で市場リターンを引いた abnormal return を使う。
  - `DISCLOSURE_TIME` が場中/引け後かで基準日を補正する。

### 弱気がウソだった判定

主判定は「最終 FY 実績 vs 初期 FY ガイダンス」と「最終 FY 実績 vs 前期 FY 実績」。

- `actual_vs_initial = final_actual_metric / initial_forecast_metric - 1`
- `actual_growth = final_actual_metric / previous_actual_metric - 1`
- `conservative_guidance_hit`:
  - `actual_vs_initial >= 0.10`
  - かつ `actual_growth >= -0.05`
- 強い hit:
  - `actual_vs_initial >= 0.20`
  - かつ `actual_growth >= 0`

### 上方修正

`FIN_SUMMARY.TYPE_OF_DOCUMENT = 'EarnForecastRevision'` と TDnet `MAIN_CATEGORY IN ('業績修正', '業績予想')` を使う。

- 同じ会計年度内の修正を時系列で追う。
- 初期予想比 +10%以上、または減益予想から前年並み以上へ改善したものを `positive_revision = true`。
- 上方修正は補助証拠。最終着地が悪ければ hit にはしない。

---

## 実装ステップ

### P0-1. BQ 年度イベント PoC

**対象**: `scripts/fy_conservative_guidance_screener.py`

**方針**:

- `--date-from` / `--date-to` / `--limit-tickers` を持つ CLI を作る。
- BQ 側で以下の CTE を組む。
  - `universe`
  - `fy_actuals`
  - `fy_initial_guidance`
  - `forecast_revisions`
  - `tdnet_events`
  - `price_reactions`
  - `year_hits`
- 初回は 2024-2026 の2-3年で `candidate_years.csv` を出す。

**検証**:

- `--date-from 2024-01-01 --date-to 2026-05-14 --limit-tickers 200`
- 出力候補 10-30件を手で確認。
- `FIN_SUMMARY` の翌期予想列解釈が妥当か、候補ごとに原数値を出す。

### P0-2. 企業スコアリング

**対象**: 同上

**方針**:

- `year_hits` を `TICKER` 単位に集計する。
- `hit_count >= 2` または `hit_rate >= 0.30` を初期候補にする。
- スコアは説明可能な線形加点にする。
  - hit回数
  - 平均 `actual_vs_initial`
  - 平均決算後下落率
  - 上方修正あり年度数
  - 直近年度の再現性

**検証**:

- スコア上位20件を `report.html` で確認。
- 特殊イベント、TOB、REIT、ETF が混入していないことを確認。

### P1-1. TDnet evidence 表示

**対象**: `report.html`

**方針**:

- 各年度ごとに FY決算短信、業績修正、最終FY決算の `DOC_TITLE` と日付を表示。
- `SUB_CATEGORIES` も表示し、カテゴリ判定の根拠を見える化する。
- `CHUNK_TEXT` は長文を出さず、関連タイトルと数値表を主証拠にする。

**検証**:

- 文字化けなし。
- 1銘柄あたり年度 evidence が追える。

### P1-2. Claude Code 取り込み用ハンドオフ

**対象**: `docs/codex-to-claude-handoff.md`

**方針**:

- 完成時に以下を記録する。
  - 実装 commit
  - 実行コマンド
  - 出力CSV/HTMLパス
  - 主要件数
  - 既知の限界
  - Claude Code 側で取り込むべきファイル

---

## 閾値の初期値

| 項目 | 初期値 | 理由 |
---|---:|---|
| 初期弱気成長率 | `<= 0%` / `<= 5%` | 日本企業の保守予想を広めに拾う |
| 決算後1営業日下落 | `<= -7%` | 明確な失望売りを拾う |
| 決算後3営業日下落 | `<= -10%` | 翌日だけでなく遅延反応を拾う |
| 最終実績 vs 初期予想 | `>= +10%` | 弱気が外れたと言える最低ライン |
| 最終実績 vs 前期実績 | `>= -5%` | 「前年比変わらず」を許容 |
| 繰り返し判定 | `hit_count >= 2` | 1回だけの偶然を除外 |

---

## リスクと対策

- **予想列の解釈ミス**: `FORECAST_*` / `NEXT_YEAR_FORECAST_*` を列名だけで決めない。期間日付とサンプル出力で検証する。
- **カテゴリ漏れ**: `MAIN_CATEGORY` だけでなく `SUB_CATEGORIES` と `DOC_TITLE` 正規表現を併用する。
- **市場全体下落との混同**: 初版は単純リターン、改良版で `INDEX_PRICE` 補正。
- **特殊イベント混入**: TOB・MBO、REIT、ETF、上場廃止、構造改革損益などは除外/低信頼にする。
- **上方修正だけで誤判定**: hit の主判定は最終FY実績との比較。上方修正は confidence 加点に限定する。

---

## 検証戦略

1. **smoke test**: 200銘柄・2024-2026でCSVが出ること。
2. **sample review**: 候補10-30件について FY決算短信、業績修正、最終FY実績の数値を目視確認。
3. **full run**: 普通株全体・過去5-8年で `company_scores.csv` を作成。
4. **quality gate**:
   - ETF/REIT/PRO/外国株が0件。
   - `candidate_years.csv` に FY決算、初期予想、最終実績、価格反応の4要素が揃う。
   - 上位20件のうち明らかな誤判定が20%未満。

---

## 関連ドキュメント

- `docs/knowledges/api/002_bigquery.md`
- `docs/knowledges/tools/013_tdnet_load.md`
- `data_catalog.md`
- `docs/codex-to-claude-handoff.md`

---

## 実装後チェック

- [x] `scripts/fy_conservative_guidance_screener.py` を追加した。
- [x] `candidate_years.csv` と `company_scores.csv` を生成した。
- [x] `report.html` を生成した。
- [x] smoke test と sample review の件数を記録した。
- [x] Claude Code 取り込み用に `docs/codex-to-claude-handoff.md` を更新した。

## 実装記録

**実装日時**: 2026-05-14 14:06 JST
**実装ファイル**: `scripts/fy_conservative_guidance_screener.py`

**PoC実行**:

```powershell
$env:PYTHONUTF8='1'
<python> scripts\fy_conservative_guidance_screener.py `
  --date-from 2024-01-01 `
  --date-to 2026-05-14 `
  --limit-tickers 200 `
  --output-dir data/output/fy_conservative_guidance_poc
```

- `candidate_years`: 448
- `pattern_hits`: 13
- `company_scores`: 13
- ETF/REIT/PRO/外国株/出資証券の混入: 0

**全量実行**:

```powershell
$env:PYTHONUTF8='1'
<python> scripts\fy_conservative_guidance_screener.py `
  --date-from 2018-01-01 `
  --date-to 2026-05-14 `
  --output-dir data/output/fy_conservative_guidance
```

- `candidate_years`: 25,615
- `pattern_hits`: 584
- `company_scores`: 502
- `invalid_baseline`: 5,779（前期実績・初期予想・最終実績が欠損または0以下のため判定対象外）
- ETF/REIT/PRO/外国株/出資証券の混入: 0

**全量出力**:

- `C:\Users\zonekun\Documents\codex\investment-agent\data\output\fy_conservative_guidance\candidate_years.csv`
- `C:\Users\zonekun\Documents\codex\investment-agent\data\output\fy_conservative_guidance\company_scores.csv`
- `C:\Users\zonekun\Documents\codex\investment-agent\data\output\fy_conservative_guidance\report.html`
