# 株主構成データ整備プラン

**起票日**: 2026-04-20 15:56 JST
**目的**: TOB予測モデル（`docs/knowledges/analysis/007_tob_ml_prediction.md`）の説明変数である株主構成系データを全上場銘柄・年次でBQに整備する
**ステータス**: 完了（2026-05-18: SHAREHOLDER_COMPOSITION テーブル構築・EXTEND分類・compute_owner_features全完了。手順を 007_tob_ml_prediction.md §データパイプライン A-2 に統合済み）
**依存**: DELISTED_STOCKS 拡張完了（IS_PAPER_TOB_LABEL=288件）

---

## 確定事項

| 項目 | 決定 |
|---|---|
| Q1 スナップショット頻度 | **年次**（有報提出時期ベース、通常3月期=6-7月提出） |
| Q2 過去データ範囲 | **2013年〜現在** |
| Q3 筆頭株主上場判定 | 正規化 + Gemini ハイブリッド。モデル=**`gemini-3-flash-preview`**（個人APIキー） |
| Q4 並列化 | **日付レベルキャッシュ流用**（`C:\tmp\edinet_cache\dates\`） |
| 追加因子 | **アクティビスト有無**（`activists.csv` + `activist_aliases.csv` のマッチング流用） |

## データソース戦略

既存 `ir_documents_enhanced` BQ = 0件（空）なので使えない。GCS生XBRL + EDINET API直接の2層:

```
XBRL取得 = GCSキャッシュ優先 → 不在なら EDINET API からDL
         ↓ C:\tmp\shareholder_xbrl\{ticker}\{doc_id}.xbrl に統一保存
         ↓ パース: MajorShareholdersTextBlock + 所有者別状況
         ↓ アクティビスト判定（flag_activists_in_list.pyのマッチ関数を import）
         ↓ BQ STOCK.SHAREHOLDER_COMPOSITION にロード
```

## BQ スキーマ（新規テーブル）

`gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION`

| カラム | 型 | モード | 説明 |
|---|---|---|---|
| TICKER | STRING | REQUIRED | 4桁コード ★PK |
| FISCAL_YEAR_END | DATE | REQUIRED | 有報の対象事業年度末日 ★PK |
| DOC_ID | STRING | NULLABLE | 有報EDINET docID |
| SUBMIT_DATE | DATE | NULLABLE | 有報提出日 |
| TOP_SHAREHOLDER_NAME | STRING | NULLABLE | 筆頭株主名 |
| TOP_SHAREHOLDER_RATIO | FLOAT64 | NULLABLE | 筆頭株主持株比率 (0.0〜1.0) |
| TOP_SHAREHOLDER_IS_PUBLIC | BOOL | NULLABLE | 国内上場企業か |
| TOP_SHAREHOLDER_TICKER | STRING | NULLABLE | マッチ時の ticker |
| FOREIGN_RATIO | FLOAT64 | NULLABLE | 外国人持株比率 |
| INDIVIDUAL_RATIO | FLOAT64 | NULLABLE | 個人持株比率（論文KOJIN） |
| FINANCIAL_INST_RATIO | FLOAT64 | NULLABLE | 金融機関持株比率 |
| OTHER_CORP_RATIO | FLOAT64 | NULLABLE | その他法人持株比率 |
| TREASURY_RATIO | FLOAT64 | NULLABLE | 自己株式比率 |
| TOP10_CONCENTRATION | FLOAT64 | NULLABLE | 上位10株主合計比率 |
| TOP10_NAMES_JSON | STRING | NULLABLE | 上位10株主のJSON配列 `[{name, ratio}]` |
| HAS_ACTIVIST | BOOL | NULLABLE | TOP10にアクティビストが含まれるか |
| ACTIVIST_NAMES | STRING | NULLABLE | マッチしたアクティビスト名のカンマ区切り |
| ACTIVIST_MAX_SCORE | INT64 | NULLABLE | マッチ最高スコア (0-100) |
| EXTRACTED_AT | TIMESTAMP | REQUIRED | 抽出日時（JST） |

**主キー**: `(TICKER, FISCAL_YEAR_END)` — NOT ENFORCED

## 実装フェーズ

### Phase 0: GCSカバレッジ調査 (30分)
- [x] `gs://stock_data_1930932/edinet/{ticker}/` をリストアップ
- [x] 全上場銘柄 × 2013〜2026 の有報年(docTypeCode=120) の在庫表作成
- [x] 不足件数を集計 → 追加DL計画確定

### Phase 1: XBRL パーサ実装 (半日)
- [x] `scripts/fetch_shareholder_composition.py` 新規作成
- [x] `fetch_tob_shareholders.py` から以下を流用:
  - `MajorShareholdersTextBlock` 抽出関数
  - EDINET 日付スキャン関数
  - 日付キャッシュ (`C:\tmp\edinet_cache\dates\`)
- [x] 追加実装:
  - `ShareholdingByShareholderCategoryTableTextBlock` から FOREIGN/INDIVIDUAL/FINANCIAL_INST/OTHER_CORP/TREASURY 比率抽出
  - HTMLテーブル行パース（正規表現）
  - アクティビスト判定 (`flag_activists_in_list` の関数 import)

### Phase 2: 動作検証 (既存GCSデータで確認、1-2時間)
- [x] 7203 トヨタ(2015-2025)・9984 ソフトバンクG・6861 キーエンス等 代表5銘柄で抽出精度確認
- [x] 期待値: TOP_WEIGHT、FOREIGN_RATIO 等が妥当な範囲（0〜1）
- [x] 所有者別状況テーブルのパース安定性確認

### Phase 3: 過去分バックフィル (3-5時間、並列化前提)
- [x] 2013-2026 の全有報年 docID 収集（日付スキャン）
- [x] GCS不在分のXBRL を EDINET API からDL（`C:\tmp\shareholder_xbrl\` にローカルキャッシュ）
- [x] 全件パース → SHAREHOLDER_COMPOSITION にロード

### Phase 4: 筆頭株主上場判定 (1-2時間)
- [x] 正規化マッチ (`STOCK_CODE_LIST` と突合) で 80-90% カバー想定
- [x] 残unmatch を Gemini (`gemini-3-flash-preview`) でバッチ処理
  - プロンプト: 株主名 + 上場銘柄候補リスト（ファジー抽出したTop10） → ticker or null + confidence
  - response_schema で `{is_listed, ticker, confidence, reason}` 固定
- [x] TOP_SHAREHOLDER_IS_PUBLIC / TOP_SHAREHOLDER_TICKER を UPDATE

### Phase 5: ドキュメント更新 (1時間)
- [x] `data_catalog.md` に SHAREHOLDER_COMPOSITION セクション追加
- [x] `docs/knowledges/tools/` 配下に新規MD (例: 079_shareholder_composition.md)
- [x] `CLAUDE.md` 索引に追加
- [x] `docs/knowledges/analysis/007_tob_ml_prediction.md` の特徴量リストを更新

## 想定規模

| 項目 | 見積 |
|---|---|
| 対象銘柄 | ≈ 4,500（上場+廃止） |
| 対象年 | 14年（2013〜2026） |
| 総レコード | ≈ 63,000（銘柄ごとの上場年数で減少） |
| XBRL ZIP DL量 | 15-30GB（ローカルキャッシュ） |
| EDINET API 呼び出し | 日付スキャン ≈ 3,500日 + 個別DL 数万件 |
| 処理時間 | 初回 3-5時間、再実行 ~30分 |
| Gemini API コスト | 1-2万件 × `gemini-3-flash-preview` で数百円〜千円程度 |

## 関連ファイル

- 流用元: `scripts/fetch_tob_shareholders.py`（XBRL大株主抽出）
- 流用元: `scripts/flag_activists_in_list.py`（アクティビスト判定）
- 流用元: `scripts/fetch_tob_announcements.py`（日付キャッシュ + EDINET API管理）
- マスタ: `data/master/activists.csv` / `data/master/activist_aliases.csv`
- マスタ: `STOCK.STOCK_CODE_LIST`（上場判定）
- 出力先: 新規 `scripts/fetch_shareholder_composition.py`
- BQ: 新規 `STOCK.SHAREHOLDER_COMPOSITION`
- ローカル: `C:\tmp\shareholder_xbrl\`

## リスク・留意点

- **EDINET v2 API の2013-2014年データ不安定**（006 MDに記載） → 該当期間は取得漏れ発生可能、再試行ロジック必要
- **有報 XBRL 形式の世代間差異**: 2013年頃は XBRL 1.0系、後年は 2.0系の可能性あり。パース失敗時はHTML fallback or スキップ
- **株主名の表記揺れ**: 全角/半角・法人格有無・信託口番号の差異。`flag_activists_in_list.py` の正規化関数を再利用
- **並列化時の GCS 書き込み競合**: なし（ローカルキャッシュに書き込むのみ）
- **BQ への ingestion 速度**: WRITE_APPEND で 63K件。1分以内で完了想定

## 出口条件

- [x] `STOCK.SHAREHOLDER_COMPOSITION` に 2013〜2026 の全上場銘柄 × 年次でデータ充填（実績: 37,657行 / 4,411銘柄 / 2015-2026）
- [x] IS_PAPER_TOB_LABEL=TRUE の 289銘柄に「TOB前年のスナップショット」が揃う
- [x] 知見 MD 一式更新（002 BQ / 006 EDINET / 058 JPX / 081 Shareholder / 007 Analysis / CLAUDE.md）

---

## 完了後 TODO（次フェーズ候補）

### 1. Random Forest モデル実装 (論文再現コア)
- 入力: `DELISTED_STOCKS.IS_PAPER_TOB_LABEL=TRUE` (289件) を正解ラベル、`SHAREHOLDER_COMPOSITION` を説明変数源
- 特徴量 30 種を構築:
  - 株主構成: TOP_SHAREHOLDER_RATIO / IS_PUBLIC / INDIVIDUAL_RATIO / FOREIGN_RATIO / HAS_ACTIVIST 他
  - 財務: `STOCK.fin_summary` から PBR / log(時価総額) / ROE / 配当性向 等
  - 市場: `STOCK.STOCK_PRICE_JQUANTS` から 60日リターン / 240日リターン / 240日ボラティリティ / 出来高回転率
  - 業種: `STOCK_CODE_LIST.INDUSTRY_17_CATEGORY`
- 前処理 6 ステップ（欠損除去→標準化→WF分割→RandomUnderSampling 1:20→Tomek Links→SMOTENC 1:10）
- 実装: `scripts/tob_prediction/train_rf.py` 新規（`imbalanced-learn` 依存要）
- 評価: ROC-AUC / PR-AUC を年別に出力、論文値 (0.60-0.75) と比較

### 2. SHAP 分析
- `shap` ライブラリで特徴量寄与度を可視化
- 論文の最重要特徴（TOP_WEIGHT, SHAREHOLDER_PUBLIC, KOJIN）を国内データで検証
- 追加因子 `HAS_ACTIVIST` の寄与度を評価（日本市場固有性）

### 3. バックテスト
- 戦略: 毎年5月末リバランス、予測確率上位 5% / 15% / 25% を等ウェイトロング
- ベンチマーク: TOPIX (`STOCK.INDEX_PRICE`)
- 評価期間: 2018〜2024（論文合わせ）
- メトリクス: 年率リターン / Sharpe / MaxDD / α
- 実装: 既存 `backtest_daily_pnl_model` の枠組みを流用

### 4. 残課題（必要に応じて）
- **136件の救済**: 「株式の併合 / 株式等売渡請求」で TOB-F が subjectEdinetCode 紐付け失敗のケース → 対象会社名での EDINET docDescription 文字列マッチ or TDnet `MAIN_CATEGORY='TOB・MBO'` からの docID 取得ルート
- **PREMIUM_RATE NULL 2件** (9613 NTTデータ等): 届出書のプレミアム記述パターン再分析、正規表現改善
- **85件の「完全子会社化（方式不明）」**: TDnet 文書確認で TOB/株式交換/株式移転を個別判定
- **TDnet BQ 欠損期間** (2017年前半、2025/04-12): データロード復旧

### 5. 運用・更新
- 新規廃止銘柄: `scrape_jpx_delisted.py` を月次で実行 → 新規 TOB 案件は `fetch_tob_announcements.py --missing-only` で自動拡張
- 新規決算期: `fetch_shareholder_composition.py` を6月〜7月提出期に年次実行し SHAREHOLDER_COMPOSITION を追加
- Cloud Run Job 化（日次 or 月次トリガ）は運用フェーズで検討
- [x] SHAP分析で TOP_WEIGHT / HAS_ACTIVIST / FOREIGN_RATIO / INDIVIDUAL_RATIO が抽出可能
- [x] data_catalog.md / 知見MD / CLAUDE.md 更新完了

---

## 次のアクション

**Phase 0 から着手**: GCS カバレッジ調査スクリプト `scripts/_check_edinet_gcs_coverage.py` を作成し、全銘柄×全年の在庫表を出力する。
