# TOB予測法: 機械学習による他社株TOB予測ポートフォリオ戦略

**カテゴリ**: analysis
**作成日**: 2026-03-24
**ステータス**: 有効
**投資アイデアステータス**: BACKTEST_PASS（2022-2025 Top5%累積+70.6%、年平均+14.4%、TOBリフト3-6x）
**根拠論文**: `C:\Users\zonekun\Dropbox\book\機械学習による他社株TOBの予測可能性.pdf`
**論文著者**: 久保正裕・梶並俊彦・鈴木智也（茨城大学／大和アセットマネジメント）
**発表媒体**: 人工知能学会 金融情報学研究会 SIG-FIN-036-41

---

## 元の投資アイデア

**仮説**: TOB（公開買い付け）の対象となる銘柄は事前に機械学習で予測可能であり、
予測確率上位銘柄でロングポートフォリオを構築するとTOPIXを上回るリターンが得られる。

**投資メカニズム（2つの経路）**:
1. **直接効果**: 実際にTOBが発生した場合、買収プレミアムによる株価急騰（異常収益）を取得できる
2. **間接効果**: TOBが実際に発生しなくても、予測確率が高い銘柄（＝割安・株主構成上の問題を抱える銘柄）は市場がTOB可能性を織り込んで株価が上昇しやすい傾向がある

**ポイント**: TOBの「当たり外れ」に関わらず、予測確率自体が株価上昇の先行指標として機能する。

---

## ★ 使用データ

| 項目 | 内容 |
|------|------|
| 対象市場 | 東京証券取引所上場全銘柄（約3,000銘柄/年） |
| データ期間 | 2011年1月〜2025年5月（モデル評価: 2018〜2024年） |
| 財務データ | 日経NEEDS-FinancialQUEST（財務情報・株主構成） |
| アナリスト予想 | 東洋経済データサービス（企業予想データ） |
| 基準日 | 毎年5月末（3月決算の公表・市場反映の時間差を考慮） |
| ラベル | TOB公告価格が公告時株価より5%以上高い場合のみ「他社株TOB」と判定（自己株TOBを除外） |

**TOB発生件数（年別）**:
- 2011〜2017年: 31〜60件/年
- 2018〜2022年: 42〜57件/年
- 2023年: 73件、2024年: 85件（近年急増傾向）

**説明変数（計26変数、自前実装）**:

| カテゴリ | 変数 |
|---------|------|
| 財務系 (10) | equity_ratio, pbr, roe, payout_ratio, ln_market_cap, cash_rich_ratio, forecast_div_yield, forecast_profit_growth, cfo_to_mcap, operating_margin |
| 市場系 (4) | ret_60d, ret_240d, vol_240d, turnover_ratio |
| 株主構成系 (8) | top_shareholder_ratio, individual_ratio, foreign_ratio, financial_inst_ratio, other_corp_ratio, top10_concentration, has_activist, top_shareholder_is_public |
| オーナー色系 (4) | owner_count_in_top10, owner_ratio_in_top10, real_top_is_individual, has_famous_investor |

---

## ★ 使用アルゴリズム

**主手法: Random Forest（ランダムフォレスト）**

**選択理由**: 特徴量の重要度解釈が容易（SHAP）、高次元・多重共線性に強い、過学習が比較的起きにくい。

**不均衡データ対処（6ステップ前処理）**:

| Step | 処理内容 |
|------|---------|
| 1 | 欠損値を含む銘柄を削除 |
| 2 | 連続変数を月次クロスセクション方向に標準化 |
| 3 | 予測対象年=テスト、過去5年=訓練に分割（ウォークフォワード） |
| 4 | RandomUnderSampling（多数派を削減、比率を1:20に） |
| 5 | Tomek Links（クラス境界付近の多数派ノイズ除去） |
| 6 | SMOTENC（少数派を合成生成、最終比率1:10に） |

**ハイパーパラメータ最適化**: Optuna（PR-AUCを最大化指標として最適化、年ごとに再チューニング）

---

## データパイプライン

### A. 初期構築（一度だけ実行）

#### A-1. TOBラベル作成

**BQテーブル**: `STOCK.DELISTED_STOCKS`（既存テーブルに列追加）

**スクリプト**: `scripts/fetch_tob_announcements.py`（docTypeCode=240 を subjectEdinetCode で逆引き）

**追加カラム**: `TOB_ANNOUNCEMENT_DATE` / `TOB_PRICE` / `PRICE_BEFORE_ANNOUNCEMENT` / `PREMIUM_RATE` / `TOB_TYPE` / `TOB_ACQUIRER` / `TOB_DOC_ID`

**IS_PAPER_TOB_LABEL の定義**（BQ BOOL列）:
```sql
TOB_PRICE IS NOT NULL
  AND PREMIUM_RATE >= 0.05
  AND TOB_TYPE IN ('OTHER', 'MBO')
```

**バックフィル実績**: 577件の IS_TOB_MBO=TRUE 中 **289件が IS_PAPER_TOB_LABEL=TRUE**（完了: 2026-04-20）

**設計判断**:
- `TOB_TYPE = 'OTHER'`（他社株TOB）と `'MBO'`（経営陣買収）を両方含める。`'SELF'`（自己株TOB）は除外
- `TOB_DOC_ID IS NOT NULL` は条件から外した（EDINET v2 の 2019年以前不安定期で有効TOBを取り逃すため）
- `TOB_TYPE` 判定は XBRL 本文キーワードではなく `DELISTING_REASON` 文字列マッチで行う（誤マッチ対策）
- 詳細: `docs/knowledges/api/006_edinet_api.md`（「TOB公告情報抽出」セクション）

**IS_TOB_MBO と IS_PAPER_TOB_LABEL の役割分担**:

| フラグ | 定義 | 粒度 |
|---|---|---|
| `IS_TOB_MBO` | Gemini が TDnet 180日分テキストから判定した広義のTOB性取引（スクイーズアウト・実質買収全般を含む） | 広義 |
| `IS_PAPER_TOB_LABEL` | 論文定義の「他社株TOB」。EDINET公開買付届出書あり ∧ プレミアム≥5% ∧ TOB_TYPE∈{OTHER,MBO} | 狭義 |

**IS_TOB_MBO=TRUE かつ IS_PAPER_TOB_LABEL=FALSE の 289件**は EDINET で公開買付届出書が見つからなかったケース（スクイーズアウト段階の廃止136件・方式不明85件・組織再編29件など）。「IS_TOB_MBO=FALSE に戻す」一括訂正は行わない（Gemini判定は広義のTOB性判定として保持）。

**ラベル利用例**:
```sql
SELECT TICKER, DATE_TRUNC(TOB_ANNOUNCEMENT_DATE, YEAR) AS tob_year
FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
WHERE IS_PAPER_TOB_LABEL = TRUE
```

#### A-2. 株主構成テーブル構築

**BQテーブル**: `STOCK.SHAREHOLDER_COMPOSITION`（37,657行 / 4,411銘柄 / 2015-2026年）

**データソース**: EDINET 有価証券報告書 XBRL から抽出（大株主上位10名テーブル）

**スクリプト**: `scripts/fetch_shareholder_composition.py`

**詳細手順**: `docs/knowledges/tools/081_shareholder_composition.md` / 計画: `docs/plans/tools-081_shareholder_composition_20260420_155621.md`（完了）

**論文SHAP特徴量のBQカラムへのマッピング**:
| 論文変数 | BQ カラム |
|---|---|
| TOP_WEIGHT | `TOP_SHAREHOLDER_RATIO` |
| SHAREHOLDER_PUBLIC | `TOP_SHAREHOLDER_IS_PUBLIC` |
| KOJIN | `INDIVIDUAL_RATIO` |
| 外国人保有比率 | `FOREIGN_RATIO` |

**追加因子（論文にない日本市場固有）**: `HAS_ACTIVIST`（アクティビスト保有）、`ACTIVIST_NAMES`、`ACTIVIST_MAX_SCORE`

#### A-3. 株主名 TYPE 分類（EXTEND テーブル）

**BQテーブル**: `STOCK.SHAREHOLDER_COMPOSITION_EXTEND`（56,160名）

**TYPE 種別と件数（2026-05-18時点）**:

| TYPE | 件数 | 説明 |
|------|------|------|
| INDIVIDUAL | 19,251 | 個人名 |
| INSTITUTION | 12,602 | 国内機関投資家 |
| FOREIGN_CUSTODIAN | 10,239 | 外国カストディ |
| PRIVATE_CORP | 7,776 | 非上場法人（資産管理会社等） |
| TRUST_BANK | 2,128 | 信託銀行 |
| LISTED_CORP | 1,978 | 上場事業法人（STOCK_CODE_LISTから照合） |
| ASSET_MGMT | 1,793 | オーナー色資産管理会社（irbank.netスクレイピング判定） |
| UNCLASSIFIED | 393 | Sonnet判定 |

**スクリプト（実行順）**:
1. `export_listed_company_names.py` — STOCK_CODE_LIST + DELISTED_STOCKS → `data/master/listed_company_names.csv`
2. `classify_shareholder_names.py --listed-names-csv data/master/listed_company_names.csv` — ルールベース分類（LISTED_CORP付き）
3. `load_shareholder_name_types.py` — EXTEND テーブルに BQ INSERT（WRITE_APPEND）

**注意事項**:
- アクティビスト（`data/master/activist_aliases.csv` 315件）は ASSET_MGMT に昇格しない（誤包含防止）
- 上場事業法人は PRIVATE_CORP から LISTED_CORP に昇格（照合は正規化済みマッチ）

#### A-4. オーナー色因子算出

**BQテーブル**: `STOCK.SHAREHOLDER_COMPOSITION`（派生カラム追加: OWNER_COUNT / OWNER_RATIO 等）

**スクリプト**: `compute_owner_features.py --mode full`（37,607行更新）

**オーナー候補（ASSET_MGMT昇格）の判定方法**:
- `family_holding_candidates_classified.csv` で「要確認」銘柄を抽出
- `owner_judge_batch.py --offset N`（irbank.net スクレイピング）でYES/NO判定
- `owner-judge-commander` スキルで少量をClaudeが直接判定
- 結果を `classify_shareholder_names.py` にフィードバック → `load_shareholder_name_types.py` でBQへ

### B. 年次更新（新年度データ追加時）

1. `fetch_shareholder_composition.py` 実行（新年度レコードをBQ INSERT）
2. 新出株主名を検出:
   ```sql
   SELECT DISTINCT JSON_VALUE(e, '$.name') AS name
   FROM STOCK.SHAREHOLDER_COMPOSITION, UNNEST(JSON_QUERY_ARRAY(TOP10_NAMES_JSON)) AS e
   WHERE FISCAL_YEAR_END >= DATE_SUB(CURRENT_DATE(), INTERVAL 1 YEAR)
   EXCEPT DISTINCT
   SELECT NAME FROM STOCK.SHAREHOLDER_COMPOSITION_EXTEND
   ```
3. 新出名を `classify_shareholder_names.py` でルール分類 → UNCLASSIFIED残はSonnet判定
4. `load_shareholder_name_types.py` で EXTEND テーブルに INSERT（WRITE_APPEND）
5. `compute_owner_features.py` で新年度行の派生カラム再計算

### C. スクリプト一覧

| スクリプト | 入力 | 出力 BQ | 目的 |
|-----------|------|---------|------|
| `fetch_tob_announcements.py` | EDINET API | `STOCK.DELISTED_STOCKS`（列追加） | TOBラベル作成 |
| `fetch_shareholder_composition.py` | EDINET XBRL | `STOCK.SHAREHOLDER_COMPOSITION` | 株主構成テーブル構築 |
| `export_listed_company_names.py` | `STOCK.STOCK_CODE_LIST` + `STOCK.DELISTED_STOCKS` | `data/master/listed_company_names.csv` | LISTED_CORP照合用 |
| `classify_shareholder_names.py` | SHAREHOLDER_COMPOSITION + listed_company_names.csv | ローカルCSV | 株主名TYPE分類 |
| `load_shareholder_name_types.py` | ローカルCSV | `STOCK.SHAREHOLDER_COMPOSITION_EXTEND` | BQロード |
| `owner_judge_batch.py` | family_holding_candidates_classified.csv | ローカルCSV | ASSET_MGMT昇格候補判定 |
| `compute_owner_features.py` | SHAREHOLDER_COMPOSITION + EXTEND | SHAREHOLDER_COMPOSITION（派生列） | オーナー色因子算出 |
| `train_rf.py` | FIN_SUMMARY + SHAREHOLDER_COMPOSITION + STOCK_PRICE_JQUANTS | ローカルCSV（predictions） | RFモデル学習・予測 |
| `screen_tob.py` | predictions CSV + BQキャッシュ | stdout | 予測確率上位銘柄スクリーニング |

**キャッシュ**: `C:\tmp\tob_prediction\*.csv`（BQ再クエリ回避）。`--refresh` で強制更新

---

## スクリーニングツール

**スクリプト**: `scripts/tob_prediction/screen_tob.py`

予測確率上位銘柄を根拠付きで一覧表示するCLIツール。

**主要オプション**:
| オプション | 説明 | デフォルト |
|-----------|------|----------|
| `--top-n N` | 上位N社表示 | 30 |
| `--top-pct N` | 上位N%表示 | - |
| `--year YYYY` | 予測年指定 | 最新の predictions_YYYY.csv |
| `--multi-year` | 直近2年の平均確率で順位付け | false |
| `--min-cap` / `--max-cap` | 時価総額フィルタ（億円） | - |
| `--market` | 市場区分 (prime/standard/growth) | - |
| `--has-activist` | アクティビスト保有のみ | false |
| `--min-top-ratio` | 筆頭株主比率N%以上 | - |
| `--format` | 出力形式 (table/csv) | table |

**根拠表示**: 特徴量の値ベースで最大3つの根拠を生成（筆頭株主比率→親子上場→PBR→時価総額→アクティビスト→個人比率→配当性向の優先順）。

**データソース**: predictions CSV (train_rf.py出力) + STOCK_CODE_LIST (銘柄名) + YF_STOCK_INFO (時価総額) + DELISTED_STOCKS (除外) + train_rf.py特徴量マトリクス (根拠)。全BQデータはCSVキャッシュ (`C:\tmp\tob_prediction\`)。

---

## モデル精度（最新）

**最新スコア（2026-05-18）**: ROC-AUC **0.758** / PR-AUC **0.082**（ASSET_MGMT 619件昇格後、26変数）

詳細・精度推移・SHAP重要度: `docs/knowledges/analysis/007_tob_model_metrics.md`

**STOCK_CODE_LIST リーケージ**: 現在上場銘柄のみ保持のため廃止済みTOB銘柄の業種コードが欠損。業種ダミーを除外することで軽減済み。根治は廃止済み銘柄の歴史的業種コード取得が必要。

---

## バックテスト結果

**ステータス**: BACKTEST_PASS — Top5%累積+70.6%、年平均+14.4%（4年連続2桁プラス）、TOBヒットリフト3.3-6.1x

詳細・年度別リターン・TOBヒット分析・TOPIX比較: `docs/knowledges/analysis/007_tob_backtest_results.md`

---

## 既知の限界・残課題

**MBO/オーナー型TOB未捕捉（構造的限界）**:
- ASSET_MGMT 619件昇格後もオーナー型（久光4530/マンダム4917）はTop5%未達（2026-05-18確認）
- 根本原因: 創業家保有が希薄化（5〜10%未満）し機関投資家が上位を占有する構造。株主名以外の情報（役員・定款等）なしでは検出困難
- 現モデルは親子上場型TOBに特化

**TOBラベル救済候補**（IS_TOB_MBO=TRUE かつ IS_PAPER_TOB_LABEL=FALSE の 289件）:
- 136件（スクイーズアウト段階の廃止）: TOB-F を対象会社名マッチで列挙 or TDNET_DOCUMENTS_ENHANCED から docID 検索
- 85件（方式不明）・9件（その他）: TDnet 文書確認で TOB 有無を個別判別
- 11件（合併）: TOB先行の有無を個別確認（TASAKI, ダンロップスポーツ等）

**candidate生成スクリプト**: `generate_family_holding_candidates.py`（アクティビスト・上場事業法人除外ロジック実装済み）

**将来改善候補**（優先順）:
1. モデル再学習 — 特徴量追加や期間延長による精度向上検証
2. 歴史的業種コード取得 — STOCK_CODE_LIST リーケージの根治
3. オーナー型 TOB 検出強化 — 役員・定款情報の活用

---

## 関連する既存スクリプト・知見

- EDINET パイプライン: `docs/knowledges/tools/009_edinet_download.md`
- EDINET TOB公告情報抽出: `docs/knowledges/api/006_edinet_api.md`
- 株主構成データ抽出: `docs/knowledges/tools/081_shareholder_composition.md`
- アクティビスト判定: `docs/knowledges/tools/063_activist_detection.md`
- JPX 上場廃止銘柄スクレイピング: `docs/knowledges/tools/058_scrape_jpx_delisted.md`
- J-Quants /fins/summary: `docs/knowledges/tools/008_jquants_fin_summary.md`
- バックテスト設計: `docs/knowledges/tools/039_backtest_daily_pnl_model.md`, `docs/knowledges/tools/045_backtest_evaluation_metrics.md`
- 精度推移ログ: `docs/knowledges/analysis/007_tob_model_metrics.md`
- バックテスト結果: `docs/knowledges/analysis/007_tob_backtest_results.md`
