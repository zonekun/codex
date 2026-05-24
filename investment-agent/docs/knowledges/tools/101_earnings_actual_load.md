# 決算実績ロード（earnings-actual-load）

**カテゴリ**: tools
**作成日**: 2026-05-17
**ステータス**: 有効
**計画**: `docs/plans/tools-101_earnings_actual_load_20260522_175452.md`
**関連ファイル**:
- `scripts/earnings_actual_load.py`
- `scripts/earnings_actual_backfill.py`
- `docker/Dockerfile.earnings-actual-load`
- `cloudbuild/cloudbuild.earnings-actual-load.yaml`

## 概要

`STOCK.fin_summary` から決算短信・業績予想修正を抽出し、`STOCK.EARNINGS_DISCLOSURE_CALENDAR` に `RECORD_TYPE='A'`（実績）でロードする。

## BQ テーブル

`gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`

詳細スキーマは `docs/data_catalog/bq_earnings_calendar.md` を参照。

## データフロー

```
fin_summary
  WHERE DISCLOSED_DATE BETWEEN 2017-01-01 AND @date_to
    AND (
      TYPE_OF_DOCUMENT LIKE '%FinancialStatements%'
      OR TYPE_OF_DOCUMENT IN ('EarnForecastRevision', 'REITEarnForecastRevision')
    )
    │
    ├── valid_tickers で対象銘柄を制限
    │     STOCK_CODE_LIST: TSE 内国株式（プライム/スタンダード/グロース）
    │     DELISTED_STOCKS: 旧東証主要市場（バックフィル用）
    │
    ├── R: 同一銘柄×開示日×FY で最も進んだ期を残し、同一銘柄×FY×QUARTERでは最新開示1件
    └── F: 同一銘柄×FY×QUARTER で全履歴ベースの DISCLOSURE_NUMBER ASC 連番
                │
           DISCLOSED_DATE BETWEEN @date_from AND @date_to に切り出し
                │
           変換・マッピング
                │
    ステージテーブル → 対象期間A + 今回出力した論理キーをBQトランザクションで置換
                │
    EARNINGS_DISCLOSURE_CALENDAR (RECORD_TYPE='A')
```

## カラムマッピング

| カラム | 元データ | 変換ルール |
|--------|---------|-----------|
| `CATEGORY` | `TYPE_OF_DOCUMENT` | `%FinancialStatements%`→`R` / `EarnForecastRevision`, `REITEarnForecastRevision`→`F` |
| `QUARTER` | `TYPE_OF_CURRENT_PERIOD` (fin_summary) | FY/4Q/5Q→`本決算` / 2Q→`中間決算` / 1Q/3Q→そのまま / NULL→`不明` |
| `FISCAL_YEAR_END` | `CURRENT_FISCAL_YEAR_END_DATE` (fin_summary) | NULL許容 |
| `DISCLOSURE_TIME` | `DISCLOSED_TIME` (fin_summary) | NULL許容 |
| `REVISION_SEQ` | 計算値 | F(業績予想): 銘柄×FISCAL_YEAR_END×QUARTERで `DISCLOSURE_NUMBER ASC` 連番 / R(決算): 常に1 |
| `SOURCE` | 固定 | `"jquants"` |

## 引数

| 引数 | 説明 |
|------|------|
| `--from YYYYMMDD` | 開始日 |
| `--to YYYYMMDD` | 終了日（省略時は `--from` と同日） |
| `--week` | 今日-7〜今日をロード（`--from`/`--to` より優先） |
| 引数なし | 今日1日分をロード |

## ロード方式

**冪等**: 変換後データをステージテーブルへロードし、BQトランザクションで次を置換する。

- 対象期間内の `RECORD_TYPE='A'`
- 今回出力した論理キー（`TICKER`, `FISCAL_YEAR_END`, `QUARTER`, `CATEGORY`, `RECORD_TYPE`, `REVISION_SEQ`）に一致する既存A行

これにより、週次ロードでもFの `REVISION_SEQ` は全履歴ベースで維持され、Rの後続開示が過去の同一FY/QUARTER行を置き換える。

## Phase 4-7 バックフィル

専用プログラム: `scripts/earnings_actual_backfill.py`

- 既定は dry-run。BQ更新には `--execute` が必須
- 対象期間は `2017-01-01`〜`2026-05-22` に制限
- Rはバックフィル全期間で銘柄×FISCAL_YEAR_END×QUARTERごとに最新開示1件を採用する
- Fの `REVISION_SEQ` はチャンク内ではなくバックフィル全期間で採番してから対象チャンクを切り出す
- 実行時は `EARNINGS_DISCLOSURE_CALENDAR_BAK_YYYYMMDD_HHMMSS` を作成
- チャンクごとにステージングテーブルへロード後、BQトランザクションで対象A行を置換
- 検証: スキーマ、S予定行数不変、投入件数、論理PK重複、2162/2026-05-11サンプル
- 実行ログは `docs/plans/tools-101_earnings_actual_load_20260522_175452.md` に追記

## Cloud Run Job

- **ジョブ名**: `earnings-actual-load`
- **イメージ**: `us-west1-docker.pkg.dev/gmailpj-357912/tools/earnings-actual-load:latest`
- **リージョン**: us-west1

## スケジュール

| スケジューラー | cron (JST) | args | 対象期間 |
|:--|:--|:--|:--|
| `earnings-actual-load-weekly` | `0 5 * * 0` | `--week` | 今日-7〜今日（日曜 05:00） |

## ビルド・デプロイ

```bash
# ビルド
gcloud builds submit \
  --config cloudbuild/cloudbuild.earnings-actual-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# ジョブ更新
gcloud run jobs update earnings-actual-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/earnings-actual-load:latest \
  --region us-west1

# 手動実行（過去1週間）
gcloud run jobs execute earnings-actual-load \
  --region us-west1 \
  --args="--week"

# 手動実行（日付指定）
gcloud run jobs execute earnings-actual-load \
  --region us-west1 \
  --args="--from,20260501,--to,20260517"
```

## 既知の制約

- fin_summary 起点のため、fin_summary 未取得のTDnet開示は実績Aに入らない
- J-REIT/ETF 系銘柄（1672-1697 等）は `STOCK_CODE_LIST.MARKET_CATEGORY` フィルタで通常ロード対象外
- 同一銘柄×同一開示日×同一FYに複数の決算短信がある場合、`TYPE_OF_CURRENT_PERIOD` が最も進んだ期を優先し、同一期の訂正・差替えは `DISCLOSURE_NUMBER DESC` 最新1件のみ使用
- `DISCLOSURE_NUMBER` / `TYPE_OF_DOCUMENT` / `DOC_TITLE` は Phase 4 で物理DROP済み。実績A/予定Sロードとも出力しない
- `--week` は常に今日起点で7日前〜今日。土日・休日も含む（TDnet は土日開示あり）

## バグ修正履歴

### 2026-05-22: 訂正書類除外フィルタ追加 + fin_summary 欠落対処

**Bug 1: 訂正書類・お知らせの混入**
- 症状: 2162/05-11 で訂正書類が正書類と同一 QUARTER='3Q' として4件ロードされていた
- 原因: EXTRACT_SQL の `tdnet_docs` CTE に DOC_TITLE フィルタがなく、TDnet の訂正書類・お知らせがそのままロードされていた
- 修正: `EXTRACT_SQL` に以下の4条件を追加（`scripts/earnings_actual_load.py` L141-L145）:
  ```sql
  AND DOC_TITLE NOT LIKE '%訂正%'
  AND DOC_TITLE NOT LIKE '%差替え%'
  AND DOC_TITLE NOT LIKE '%のお知らせ'
  AND DOC_TITLE NOT LIKE '%に関するお知らせ'
  ```
- 対象外 240,181件 → 除外 4,670件（約2%）

**Bug 2: QUARTER='不明' 2,568件**
- 症状: fin_summary ジョブが 05-12〜05-16 の5日間サイレント0件取得 → earnings-actual-load（05-17 05:00 JST）実行時に JOIN 失敗
- 根本原因: 旧 Cloud Run Job spec に `--shift-day` 引数なし = shift_day=0 → 02:00 JST 時点では J-Quants API に当日データが未反映でサイレント0件
- 対処: `--from=20260330 --to=20260515` と `--from=20260516 --to=20260522` で再実行 → **2,568 → 247件**（ETF/REIT カバー外のみ）
- 再発防止: Phase 3 で fin_summary 最新日チェックを追加予定

### 2026-05-23: Phase 4 fin_summary 起点への転換（Codex ローカル実装）

- 変更: `EXTRACT_SQL` を `TDNET_DOCUMENTS_ENHANCED` 起点から `fin_summary` 起点へ全面変更
- 訂正書類除外: `DOC_TITLE LIKE` 依存を廃止し、Rは `DISCLOSURE_NUMBER DESC` 最新1件を採用
- 銘柄絞り込み: `STOCK_CODE_LIST.MARKET_CATEGORY` の内国株式3市場 + `DELISTED_STOCKS` 旧東証主要市場
- SOURCE: 実績Aは `jquants`
- 互換: `DISCLOSURE_NUMBER` / `TYPE_OF_DOCUMENT` / `DOC_TITLE` は BQ DDL DROP 済み。実績A/予定Sロードとも出力対象外
- smoke: 2162/2026-05-11 は `3Q×1件` になることをBQで確認
- 未実施: 2017年以降バックフィル

### 2026-05-24: Phase 4-7 バックフィル完了

- `scripts/earnings_actual_backfill.py --mode all --execute` をローカル実行
- run_id: `20260524_124252`
- backup: `EARNINGS_DISCLOSURE_CALENDAR_BAK_20260524_124252`
- 投入結果: 2017-01-01〜2026-05-22 のA行 `165,555` 件
- 検証: S予定行 `4,925` 不変、論理PK重複0、2162/2026-05-11 は `3Q` のR 1件
- 実行中に見つかった対策:
  - バックアップ復元はpartitioned tableを再作成せず、既存テーブルへDELETE/INSERTで復元
  - dry-runでも変換後キー重複を検証
  - Rは同日同FYで最も進んだ期を残し、同一出力QUARTER内では最新開示1件に正規化
  - FはFY/4Q/5Qなど変換後QUARTER単位でREVISION_SEQを採番

### 2026-05-24: 通常ロードの差分更新を全履歴キー基準へ修正

- 背景: 4-7後のレビューで、週次 `--week` が対象期間内だけでFの `REVISION_SEQ` を採番すると、過去F行と論理キーが衝突しうる点を検出
- 修正:
  - `EXTRACT_SQL` は `2017-01-01`〜`@date_to` の履歴でR/Fを正規化・採番し、最後に対象期間へ切り出す
  - 通常ロードもステージテーブルを使い、対象期間A行に加えて今回出力した論理キーの既存A行を削除してからINSERT
  - 投入後、今回の論理キーに全体重複がないことを検証
  - `cloudbuild.earnings-actual-load.yaml` は明示 `docker push` + `gcloud run jobs update` 形式へ更新
