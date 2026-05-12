# Google Cloud Storage
> 親: [`data_catalog.md`](../../data_catalog.md)

### (b) Google Cloud Storage

| GCSパス | 説明 | 更新頻度 | 備考 |
|---------|------|---------|------|
| `gs://stock_data_1930932/stock_price/new/` | yfinanceで取得した日次CSV（BQロード前） | 日次 | ロード後 history/ に移動 |
| `gs://stock_data_1930932/stock_price/history/` | BQロード済みの日次CSV（アーカイブ） | 日次 | |
| `gs://stock_data_1930932/edinet/{証券コード}/` | EDINET 有価証券報告書（HTML） | 日次 | 詳細は下記参照 |
| `gs://stock_data_1930932/edinet_delay/backfill.csv` | 大量保有変更報告書の遅延提出一覧（2021/01〜2025/01バックフィル） | 一回限り | `scripts/edinet_delay_backfill.py`（Cloud Run Job: `edinet-delay-backfill`）。カラム: security_code, issuer_name, filer_name, obligation_date, filing_date, delay_days。日次運用分はDropbox `Edinet遅延.xlsx` に蓄積（Cloud Run Job: `edinet-delay`） |
| `gs://stock_data_1930932/edinet_xbrl/edinet_financial_{YYYYMMDD}.tsv` | XBRL抽出：全銘柄の現金・有価証券（百万円） | 手動実行時 | `scripts/edinet_xbrl_extractor.py`（Cloud Run Job: `edinet-xbrl-extractor`）で生成。清原スクリーニング用。**四季報より鮮度が高い** |
| `gs://stock_data_1930932/tdnet/{証券コード4桁}/` | TDnet 適時開示 PDF（irbank CDN 経由） | 日次 | 詳細は下記参照 |
| `gs://stock_data_1930932/monthly/meta/{ticker}/` | 月次開示メタ定義（structure / url_adapter / extract_adapter） | 手動更新 | ローカルGit管理先は `meta/monthly/{ticker}_structure.json`, `meta/monthly/{ticker}_url_adapter.json`, `meta/monthly/{ticker}_extract_adapter.json` |
| `gs://stock_data_1930932/monthly/record/{ticker}/monthly_records.json` | 月次開示の抽出済み数値レコード | 手動・バッチ更新 | BC過去データ補完も同ファイルへ `source=bc_historical` として追記 |
| `gs://stock_data_1930932/quarterly/meta/{ticker}/` | 四半期先行指標メタ定義（structure / extract_adapter） | 手動更新 | ローカルGit管理先は `meta/quarterly/{ticker}_structure.json`, `meta/quarterly/{ticker}_extract_adapter.json` |
| `gs://stock_data_1930932/quarterly/record/{ticker}/records.json` | 四半期先行指標の抽出済み数値レコード（受注高・受注残等） | 抽出時自動 | `scripts/extract_order_backlog.py` が抽出→即upsert。最新期のみ蓄積。スキーマ定義: `docs/knowledges/tools/089_quarterly_disclosure_master.md` §レコード保管設計 |
| `gs://stock_data_1930932/estat/` | e-STAT統計データ（景気動向指数, 鉱工業生産指数等） | 月次 | e-STAT API で取得（予定） |
| `gs://stock_data_1930932/nisshokin/` | 日証金 逆日歩・品貸料・貸借データ | 日次 | ※現在はBQ直接ロード（z_shina.py）。GCSアーカイブは将来検討 |
| `gs://stock_data_1930932/monthlydata/` | TDnet月次開示データ（企業×月ごとJSON） | 日次 | `scripts/monthly_data_load.py` / Cloud Run Job `monthly-data-load` |
| `gs://stock_data_1930932/config/monthly_disclosure_master.csv` | 月次開示収集マスタ（取得方法・取得元URL） | 手動更新 | カラム: TICKER, COMPANY_NAME, DISCLOSURE_TYPE(a/b/c), IR_URL, NOTES, UPDATED_AT。a=TDnetテキストPDF, b=TDnet画像PDF, c=独自IR開示。`scripts/verify_monthly_irbank.py` の調査結果から作成 |
| `gs://stock_data_1930932/earnings_model/zaraba_beta_20d/beta_20d.csv` | 全銘柄20日β（TOPIX対比） | 日次 | Cloud Run Job `beta-calc`（月〜金 18:30 JST）。カラム: TICKER, beta_20d, calc_date。決算反応モデル因子9で使用 |
| `gs://stock_data_1930932/earnings_model/earnings_reaction_predictions/` | 決算反応モデル予測結果JSON | 随時 | `prediction_{YYYYMMDD}_{HHMMSS}.json`（JST）。生成: `scripts/earnings_model/predict.py predict`。カラム: PRED_COLUMNS（`earnings_model_core.py` 定義、`eps_consensus_deviation`/`f4_source` 含む） |
| `gs://stock_data_1930932/earnings_model/earnings_reaction_actuals/` | 決算反応モデル答え合わせJSON | 随時 | `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`（JST、2026-04-15〜新規約。旧: `actual_{PREDICT_DATE}_{HHMMSS}.json`）。生成: `scripts/earnings_model/predict.py answer` |
| `gs://stock_data_1930932/earnings_model/earnings_reaction_accuracy/` | 決算反応モデル累積精度サマリー | 随時 | `accuracy_summary.json`。生成: `scripts/earnings_model/predict.py accuracy` |
| `gs://stock_data_1930932/earnings_model/earnings_reaction_exclusions/` | 決算反応モデル学習データ除外フラグ | 随時 | `exclusions.json`（JSON array, `{ticker, predict_date, reason, added_at, removed_at}`。`scripts/earnings_model/exclusion_manager.py` で管理。詳細は `docs/knowledges/tools/076_earnings_exclusion_mechanism.md`） |
| `gs://stock_data_1930932/earnings_model/zaraba_scoring_results/` | ザラ場スコアリング結果CSV | 随時 | `results_YYYYMMDD.csv`。生成: `scripts/zaraba_earnings.py upload`。watch/catchup のスコアリング結果をGCS永続化。`cmd_gcs_review` で表示 |

**`gs://stock_data_1930932/edinet/{証券コード}/` 詳細:**

- **格納対象**: 有価証券報告書のみ（半期報告書・四半期報告書含む）
- **ファイル形式**: HTML と XBRL の2種類が存在する
- **パス構造**: `gs://stock_data_1930932/edinet/{証券コード}/{ファイル名}`
  - `{証券コード}`: 東証4桁銘柄コード（可変）
- **ファイル名フォーマット**:
  - HTML: `{証券コード}_{書類略称}_{提出日}_{書類種別}_{EDINET文書ID}_MERGED_REPORT.html`
  - XBRL: `{証券コード}_{書類略称}_{提出日}_{書類種別}_{EDINET文書ID}_XBRL_PublicDoc_{XBRLファイル名}.xbrl`

  例:
  ```
  gs://stock_data_1930932/edinet/7203/7203_有報四_20241113_半期報告書－第121期(2024_04_01－2025_03_31)_S100UP32_MERGED_REPORT.html
  gs://stock_data_1930932/edinet/7203/7203_有報四_20241113_半期報告書－第121期(2024_04_01－2025_03_31)_S100UP32_XBRL_PublicDoc_{XBRLファイル名}.xbrl
  ```

- **収集モジュール**: `scripts/edinet_download.py`（Cloud Run Job: `edinet-download`）
- **スケジュール**: 月〜金 23:50 JST（`edinet-download-daily`）※日次で当日分を自動取得
- **GCS取得済み日付範囲**: `2024-01-04` 〜 `2025-12-26`（43,959ファイル、482日分）※2021〜2023年は取得中（2026-03-03時点）、2026年分は未取得
- **BQ（ir_documents_enhanced）**: 0件（2026-03-03時点 TRUNCATE済み。GCSファイルから再ロード予定）
- **Embedding フィルタ**: 四半期・半期報告書はメタデータのみ（CHUNK_TEXT=NULL, EMBEDDING=NULL）。有価証券報告書は15セクション除外（財務諸表・注記・監査報告書等）、コーポレートガバナンス=半分チャンク、事業等のリスク=1/4チャンク
- **ETL スクリプト**: `scripts/edinet_load_parallel.py`（Batch Prediction アーキテクチャ、ストリーミング結果処理）

**`gs://stock_data_1930932/tdnet/{証券コード4桁}/` 詳細:**

- **格納対象**: TDnet 適時開示 PDF（カテゴリ S/A/B のもの。C=不要は除外）
- **データソース**: yanoshin 非公式 API（メタデータ） + irbank CDN（PDF本体）
- **パス構造**: `gs://stock_data_1930932/tdnet/{証券コード4桁}/{ファイル名}.pdf`
- **ファイル名フォーマット**: `{日付}_{証券コード4桁}_{会社名}_{カテゴリ}_{タイトル}_{doc_id}.pdf`
- **収集モジュール**: `scripts/tdnet_download.py`（Cloud Run Job: `tdnet-download`）
- **スケジュール**: 月〜金 23:50 JST（`tdnet-download-daily`）※日次で当日分を自動取得
- **過去データ補完**: `scripts/irbank_tdnet_download.py`（Cloud Run Job: `irbank-tdnet-download`）で過去 PDF を遡及取得
- **GCS取得済み**: 2025年全期間は取得中（2026-03-04時点）

**⚠️ GCS検索時の注意（BQ未登録の場合）:**

ファイル名先頭の `{日付}` は **書類作成日ではなく TDnet インデックス掲載日**（ジョブがダウンロードした日）。
書類作成日は `doc_id`（ファイル末尾の14桁）の先頭8桁で判別できる（例: `140120260313581851` → 作成日 `20260313`）。
MBO・上場廃止前後や年度末は開示後数日遅れてインデックスに掲載されるケースがある。

BQ（`STOCK.TDNET_DOCUMENTS_ENHANCED`）が未登録の場合は GCS MCP で検索する:
```
# 銘柄の全開示を一覧
gcs_list(prefix="tdnet/4384/")

# 特定日付ファイルを探す場合はプレフィックスで絞る
gcs_list(prefix="tdnet/4384/20260317")

# 見つからない場合は ±3営業日 の日付範囲で再検索する
# （書類作成日 ≠ インデックス掲載日のため）
```

**`gs://stock_data_1930932/monthlydata/` 詳細:**

- **格納対象**: TDnet 月次開示カテゴリ（`MAIN_CATEGORY='月次開示'`）の文書チャンクテキスト
- **データソース**: BQ `STOCK.TDNET_DOCUMENTS_ENHANCED`
- **パス構造**:
  - `monthlydata/_progress.json` — 進捗管理（loaded_keys リストで取込済み管理）
  - `monthlydata/{ticker}/{yyyy-mm}.json` — 企業×月ごとの月次データ
- **JSON スキーマ**: ticker, name, yyyymm, submission_date, doc_title, chunk_texts, monthly_items, loaded_at
- **収集モジュール**: `scripts/monthly_data_load.py`（Cloud Run Job: `monthly-data-load`）
- **スケジュール**: 毎日 JST 7:00 を予定（未設定）
- **リラン方法**: `_progress.json` の `loaded_keys` から対象 `ticker/yyyy-mm` を削除して再実行
- **知見**: `docs/knowledges/tools/030_monthly_data_load.md`

