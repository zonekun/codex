# 決算実績ロード（earnings-actual-load）

**カテゴリ**: tools
**作成日**: 2026-05-17
**ステータス**: 有効
**計画**: `docs/plans/tools-101_earnings_actual_load_20260522_175452.md`
**関連ファイル**:
- `scripts/earnings_actual_load.py`
- `docker/Dockerfile.earnings-actual-load`
- `cloudbuild/cloudbuild.earnings-actual-load.yaml`

## 概要

`STOCK.TDNET_DOCUMENTS_ENHANCED` から決算短信・業績予想を抽出し、`STOCK.fin_summary` と JOIN して補完した上で `STOCK.EARNINGS_DISCLOSURE_CALENDAR` に `RECORD_TYPE='A'`（実績）でロードする。

## BQ テーブル

`gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`

詳細スキーマは `docs/data_catalog/bq_earnings_calendar.md` を参照。

## データフロー

```
TDNET_DOCUMENTS_ENHANCED
  WHERE MAIN_CATEGORY IN ('決算短信', '業績予想')
  AND SUBMISSION_DATE BETWEEN @date_from AND @date_to
    │
    └── LEFT JOIN fin_summary
          ON TICKER = LOCAL_CODE AND SUBMISSION_DATE = DISCLOSED_DATE
          （同一銘柄×同一日複数行はDISCLOSURE_NUMBER DESCで最新1件）
                │
           変換・マッピング
                │
    DELETE A レコード（対象期間） → INSERT
                │
    EARNINGS_DISCLOSURE_CALENDAR (RECORD_TYPE='A')
```

## カラムマッピング

| カラム | 元データ | 変換ルール |
|--------|---------|-----------|
| `CATEGORY` | `MAIN_CATEGORY` | 決算短信→`R` / 業績予想→`F` |
| `QUARTER` | `TYPE_OF_CURRENT_PERIOD` (fin_summary) | FY/4Q/5Q→`本決算` / 2Q→`中間決算` / 1Q/3Q→そのまま / NULL→`不明` |
| `FISCAL_YEAR_END` | `CURRENT_FISCAL_YEAR_END_DATE` (fin_summary) | NULL許容 |
| `DISCLOSURE_TIME` | `DISCLOSED_TIME` (fin_summary) | NULL許容 |
| `REVISION_SEQ` | 計算値 | F(業績予想): 銘柄×FISCAL_YEAR_END×QUARTERで開示日昇順連番 / R(決算): 常に1 |
| `SOURCE` | 固定 | `"tdnet"` |
| `DISCLOSURE_NUMBER` | `DOC_ID` (TDNET_DOCUMENTS_ENHANCED) | TDnet開示番号 |

## 引数

| 引数 | 説明 |
|------|------|
| `--from YYYYMMDD` | 開始日 |
| `--to YYYYMMDD` | 終了日（省略時は `--from` と同日） |
| `--week` | 今日-7〜今日をロード（`--from`/`--to` より優先） |
| 引数なし | 今日1日分をロード |

## ロード方式

**冪等**: 対象期間の `RECORD_TYPE='A'` レコードを DELETE → 再 INSERT。

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

- fin_summary に対応レコードがない場合（開示が TDnet にあるが fin_summary 未取得）は `QUARTER='不明'`、`FISCAL_YEAR_END=NULL`、`DISCLOSURE_TIME=NULL` になる
- J-REIT/ETF 系銘柄（1672-1697 等）は fin_summary カバー外のため常に `QUARTER='不明'` → **仕様**
- 同一銘柄×同一日に連結/単体等の複数 fin_summary レコードがある場合、`DISCLOSURE_NUMBER DESC` 最新1件のみ使用
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
