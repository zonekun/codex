# 決算発表予定スクレイピング → BQ ロード（earnings-schedule-load）

**カテゴリ**: tools
**作成日**: 2026-04-04
**ステータス**: 有効
**関連ファイル**:
- `scripts/earnings_schedule_load.py`
- `docker/Dockerfile.earnings-schedule-load`
- `cloudbuild/cloudbuild.earnings-schedule-load.yaml`

## 概要

ghostrader.net から決算発表予定（日付・時刻・銘柄・四半期種別）をスクレイピングし、BQ `STOCK.EARNINGS_DISCLOSURE_CALENDAR` に `RECORD_TYPE='S'` でロードする。同時に Dropbox に CSV を出力する。

## BQ テーブル

`gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`

詳細スキーマは `data_catalog.md` を参照。

## データソース（ghostrader.net）

| URL | 内容 |
|:----|:-----|
| `https://www.ghostrader.net/` | 当日の決算発表予定 |
| `https://www.ghostrader.net/Schedule_1_Tomorrow.htm` | 翌営業日 p1 |
| `https://www.ghostrader.net/Schedule_1_Tomorrow2.htm` | 翌営業日 p2 |
| `https://www.ghostrader.net/Schedule_2_After.htm` | 翌々営業日以降 |

### サイト仕様

- サーバーサイド HTML（Selenium 不要、requests + BeautifulSoup で取得可能）
- データテーブル: `table[bgcolor="#FFFBF7"]`
- セル構造: `cells[0]`=時刻, `cells[1]`=銘柄コード, `cells[2]`=企業名(aタグ), `cells[6]`=四半期(imgのalt)
- After ページは `cells[0]` が `MM/DD HH:MM` 形式（日付+時刻混在）で、決算月カラムが空になる場合あり
- 土日祝・決算なし日はテーブルが存在しない（正常動作）

### QUARTER マッピング

| ghostrader (img alt) | 本テーブル |
|:---------------------|:----------|
| 本決算 | 本決算 |
| 第１四半期 | 1Q |
| 第２四半期 | 中間決算 |
| 第３四半期 | 3Q |

## ロード方式

**冪等**: 実行日以降の `RECORD_TYPE='S'` レコードを全 DELETE → 再 INSERT。

## Dropbox 出力

- **ローカル実行**: `C:\Users\zonekun\Dropbox\stock\bunseki\発表日次速報_Ghos.csv` に直接書き込み
- **Cloud Run 実行**: Dropbox API 経由でアップロード（`/stock/bunseki/発表日次速報_Ghos.csv`）

CSV フォーマット（カンマ区切り、ヘッダなし）:
```
YYYYMMDD,HH:MM,QUARTER,TICKER
20260406,13:00,本決算,8217
20260406,14:30,1Q,8923
```

## Cloud Run Job

- **ジョブ名**: `earnings-schedule-load`
- **イメージ**: `us-west1-docker.pkg.dev/gmailpj-357912/tools/earnings-schedule-load:latest`
- **リージョン**: us-west1
- **メモリ**: 512Mi / CPU: 1 / タイムアウト: 600s

## スケジュール

| スケジューラー | cron (JST) | Cloud Run Job args | 対象日 |
|:--|:--|:--|:--|
| `earnings-schedule-load-daily` | `0 5 * * 0-5` | `--skip-days=14-17` | 日〜金 05:00（14〜17日はスクリプト側でスキップ） |

- 14〜17日はスキップ（決算集中期の谷間、ghostrader.net のデータ不安定）
- 土曜除外（ghostrader.net にデータなし）
- 日曜は翌日以降の最新データ取得のため実行する

### 設計意図（なぜ1本スケジューラ + `--skip-days` か）

以前は `earnings-schedule-load-early` (`0 5 1-13 * 0-5`) と `earnings-schedule-load-late` (`0 5 18-31 * 0-5`) の2本で「1-13日 **AND** 平日」「18-31日 **AND** 平日」を表現する意図だった。しかし **unix-cron は day-of-month と day-of-week の両指定を OR 結合する**ため、実際には両方のスケジューラが平日は毎日発火し、05:00 に2重トリガーとなっていた（2026-04-17 検出、067 参照）。

現行は cron を `0 5 * * 0-5` に統一し、月内のスキップ日はコード側の `--skip-days` 引数で制御する。Scheduler は1本、cron は単純な「平日発火」のみに保つ。

## ビルド・デプロイ

```bash
# ビルド
gcloud builds submit \
  --config cloudbuild/cloudbuild.earnings-schedule-load.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .

# ジョブ更新
gcloud run jobs update earnings-schedule-load \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tools/earnings-schedule-load:latest \
  --region us-west1

# 手動実行
gcloud run jobs execute earnings-schedule-load --region us-west1
```

## 既知の制約

- After ページ（翌々営業日以降）の一部銘柄は決算月カラムが空 → `FISCAL_YEAR_END` が NULL になる
- ghostrader.net は翌々営業日までしか公開しない → 毎日の蓄積が必須
- 実績データ（`RECORD_TYPE='A'`）のロードは別途 TDNET_DOCUMENTS_ENHANCED からの ETL で対応（未実装）
