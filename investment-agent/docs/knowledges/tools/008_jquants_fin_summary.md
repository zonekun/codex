# J-Quants /fins/summary 全データ取得 → BigQuery ロードスクリプト

**カテゴリ**: tools
**作成日**: 2026-02-28
**ステータス**: 有効
**関連ファイル**: `scripts/jquants_get_fin_summary.py`
**動作確認環境**: Colab personal（稼働確認済み）/ Cloud Run（稼働確認済み：2026-02-28）/ Colab enterprise（未確認）

## 概要

J-Quants API V2 の `/fins/summary` エンドポイントから、指定期間の財務サマリーを
日付ループ + ページネーションで全件取得し、BigQuery テーブルに直接 APPEND ロードするスクリプト。

**対応環境**: Colab personal / Colab enterprise / Cloud Run Job（ローカルは対象外）

---

## 実行方法

### Colab personal / enterprise

**前提（Colab Secrets）**:
| Secret | colab_personal | colab_enterprise |
|--------|---------------|-----------------|
| `JQUANTS_API_KEY` | 必須 | 必須 |
| `GCP_SA_KEY` | 必須（JSON文字列） | 不要（ADC使用） |

スクリプト冒頭の設定ブロックで期間を指定して実行する:

```python
START_DATE = datetime(2017, 1, 1)
END_DATE   = datetime(2017, 12, 31)
# END_DATE = datetime.today()
```

```bash
!python scripts/jquants_get_fin_summary.py
```

### Cloud Run Job

**前提（環境変数）**:
- `JQUANTS_API_KEY` を Cloud Run Job の環境変数に設定
- BQ 認証: ADC（サービスアカウントをジョブに付与）

```bash
# デフォルト日付（設定ブロックの値）で実行
gcloud run jobs execute jquants-fin-summary --region us-west1

# 日付指定（= 区切りを使用すること。, 区切りは不可）
gcloud run jobs execute jquants-fin-summary --region us-west1 \
  --args="--from=20170101,--to=20171231"
```

---

## 設定パラメータ（冒頭ブロック）

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `DATE_MODE` | `"t"` | `"t"`=今日 / `"1"`=DATE_SINGLE の1日 / `"r"`=DATE_FROM〜DATE_TO |
| `DATE_SINGLE` | `"20260228"` | MODE=`"1"` のときの日付 (YYYYMMDD) |
| `DATE_FROM` | `"20170101"` | MODE=`"r"` のときの開始日 (YYYYMMDD) |
| `DATE_TO` | `"20171231"` | MODE=`"r"` のときの終了日 (YYYYMMDD) |
| `SLEEP_SEC` | `0.5` | レートリミット対策の待機秒数 |
| `PROJECT_ID` | `"gmailpj-357912"` | GCP プロジェクト |
| `DATASET_ID` | `"STOCK"` | BigQuery データセット |
| `TABLE_ID` | `"fin_summary"` | BigQuery テーブル名 |

**優先順位**: `--from`/`--to` 引数（Cloud Run）> `DATE_MODE` 設定ブロック（Colab）

---

## 環境別の動作差異

| 項目 | colab_personal | colab_enterprise | cloudrun |
|------|---------------|-----------------|---------|
| 環境判別 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 未設定 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 設定済み | `CLOUD_RUN_JOB` 環境変数あり |
| APIキー取得 | Colab Secrets `JQUANTS_API_KEY` | Colab Secrets `JQUANTS_API_KEY` | 環境変数 `JQUANTS_API_KEY` |
| BQ認証 | Colab Secrets `GCP_SA_KEY` → service_account | ADC | ADC |
| 日付設定 | 設定ブロック直接編集 | 設定ブロック直接編集 | `--from`/`--to` 引数（省略時は設定ブロック） |
| argparse | スキップ | スキップ | 使用 |

---

## 処理フロー

```
detect_runtime() → RUNTIME 決定
    ↓
parse_args()（Cloud Run: --from/--to / Colab: 設定ブロック）
    ↓
get_api_key()（環境別）
    ↓
日付ループ（date_from ～ date_to を1日ずつ）
    ↓ 各日付
GET /fins/summary?date=YYYYMMDD
    ↓ ページネーション（pagination_key が無くなるまでループ）
    ↓ 429 レートリミット → 30秒待機してリトライ
    ↓ 0件の日はスキップ（営業日外）
全件を list に蓄積 → DataFrame 化
    ↓
preprocess()（BQ ロード前クレンジング）
    1. カラム名リネーム: API省略名 → BQ UPPER_SNAKE_CASE
    2. 空文字列 → None（API は欠損値を "" で返す）
    3. LOCAL_CODE: 5桁 → 4桁
    4. DATE 型カラム: 文字列 → datetime.date
    5. DISCLOSED_TIME: "HH:MM" → datetime.time
    6. BOOLEAN 型カラム: "true"/"false" → bool
    7. object 型の数値カラム → pd.to_numeric（float64 化）
    8. float64 → Int64（整数列のみ）
    ↓
get_bq_client()（環境別）→ load_table_from_dataframe() WRITE_APPEND
```

---

## BigQuery ロード前処理（preprocess 関数）

| 処理 | 対象列 | 内容 |
|------|--------|------|
| カラム名リネーム | 全列 | API 省略名（DiscDate, Code等）→ BQ UPPER_SNAKE_CASE（DISCLOSED_DATE, LOCAL_CODE等） |
| 空文字列統一 | 全列 | `""` → `None`（API は欠損値を空文字列で返すため。pyarrow は `""` を int/float に変換できない） |
| コード4桁化 | `LOCAL_CODE` | `"75500"` → `"7550"`（末尾除去） |
| DATE 型変換 | 7つの日付列 | 文字列 `"YYYY-MM-DD"` → `datetime.date`（pyarrow 必須） |
| TIME 型変換 | `DISCLOSED_TIME` | `"HH:MM"` → `datetime.time`（pyarrow 必須） |
| BOOL 型変換 | 6つのフラグ列 | `"true"/"false"` 文字列 → Python `bool`（pyarrow 必須） |
| 数値型変換 | 非STRING/DATE/TIME/BOOL の object 列 | `pd.to_numeric(errors='coerce')` で float64 化 |
| 整数型変換 | 全 float64 列 | 全値が整数の列を `Int64`（nullable）に変換 |

---

## 出力先（BigQuery）

| 項目 | 値 |
|------|----|
| テーブル | `gmailpj-357912.STOCK.fin_summary` |
| 書き込みモード | `WRITE_APPEND` |

---

## Dockerfile 構成（必須コピー）

`docker/Dockerfile.jquants-fin-summary` では **`jquants_common.py` のコピーが必須**。

```dockerfile
COPY scripts/jquants_get_fin_summary.py scripts/jquants_get_fin_summary.py
COPY scripts/jquants_common.py scripts/jquants_common.py   # ← 必須！忘れると ModuleNotFoundError
COPY scripts/notify.py scripts/notify.py
```

`jquants_get_fin_summary.py` は `from jquants_common import ...` で共通モジュールをインポートしている。
`jquants_common.py` を COPY し忘れると Cloud Run でのみ `ModuleNotFoundError: No module named 'jquants_common'` が発生する（ローカル実行では同ディレクトリにあるため発生しない）。

---

## 注意事項

- **jquants-api-client は使用しない**（V2 未対応のため `requests` で直接呼び出し）
- **`load_table_from_dataframe()` には `pyarrow` が必要**（Dockerfile で明示インストール）
- **`display()` は使用しない**（Cloud Run 非対応のため `print()` に統一）
- **レートリミット**: 429 応答時は 30 秒待機してリトライ
- **0件の日はログ非表示**: 土日・祝日はスキップ
- **ページネーション**: `pagination_key` を辿って全件取得
- **空文字列に注意**: J-Quants API は欠損値を JSON `null` ではなく空文字列 `""` で返す。
  `preprocess()` 冒頭で `df.replace("", None)` を実行しないと pyarrow の型変換エラーになる
- **BOOLEAN カラム**: API は `"true"`/`"false"` 文字列で返す。`bool` に変換しないと pyarrow エラー
- **Cloud Run --args 書式**: `--args="--from=20260227,--to=20260227"`（`=` 区切り必須。`,--` で連結）

---

## ビュー設計: v_fin_summary_actual_for_q_on_q

**BQビューID**: `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
**作成スクリプト**: `scripts/create_fin_summary_view.py`

### 目的

`fin_summary` の財務数値は当期累計値（1Q=Q1, 2Q=Q1+Q2, 3Q=Q1+Q2+Q3）で格納されている。
前期同四半期比（Q on Q）分析には単独Q値が必要なため、ビューで変換して提供する。

### 4ステップ CTE 構成

```
Step 1: filter  → TYPE_OF_CURRENT_PERIOD IN ('1Q','2Q','3Q','FY') のみ
Step 2: dedupe  → 同一 LOCAL_CODE × CURRENT_FISCAL_YEAR_START_DATE × TYPE_OF_CURRENT_PERIOD で
                  DISCLOSED_DATE DESC, DISCLOSED_TIME DESC の最新開示のみ残す（修正開示対応）
Step 3: with_prev → LAG() OVER (PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE
                                 ORDER BY CURRENT_PERIOD_END_DATE) で前期累積値を取得
Step 4: SELECT  → value - COALESCE(prev, 0) で単独Q値を算出
                  FY → QUARTER='4Q' に変換（TYPE_OF_CURRENT_PERIOD='FY' は別途保持）
```

### 設計判断の根拠

| 設計項目 | 採用した方針 | 理由 |
|---------|------------|------|
| PARTITION キー | `LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE` | 会計年度単位でグループ化。期末日ベースではなく開始日ベースの方が会計年度変更に強い |
| ORDER BY | `CURRENT_PERIOD_END_DATE` | 1Q < 2Q < 3Q < FY の時系列順が保証される |
| Q1のprev | NULL → `COALESCE(prev, 0)` で 0 扱い | Q1の累積値 = Q1単独値（prev=0から引く） |
| 半期報告企業 | Q1/Q3 が存在しない → prev=NULL | `COALESCE(prev, 0)` により 2Q単独 = 2Q累積（正しい）|
| FY行の扱い | ビューに含める（4Q として変換） | 削除ではなくQUARTER='4Q'に変換。FY = Q1+Q2+Q3+Q4 累積から Q3 累積を引いて Q4 単独を算出 |
| 予想値 | 除外 | 実績値のみのビュー（v_fin_summary_**actual**_for_q_on_q） |
| 最新開示のみ | ROW_NUMBER() + _rn = 1 | 修正開示で同一期の旧データが複数あっても最新1件のみ残す |

### 半期報告企業について

東証上場企業には半期報告（Q2と通期のみ開示）企業は存在しない（日本の制度上、四半期報告が義務）。
設計上は対応済み（Q1がない → LAG=NULL → COALESCE=0 → 2Q単独=2Q累積）だが実際には発動しない。

### ビュー再作成方法

```bash
# ビュー作成（初回 or SQL変更時）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py

# SQL 確認のみ（BQ 書き込みなし）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py --dry-run
```

内部では `delete_table` → `create_table` を使用（CREATE OR REPLACE VIEW 相当）。
`fin_summary` テーブルのスキーマが変わった場合は再作成不要（ビューなので自動反映）。
ビュー定義の SQL を変更した場合のみ再実行が必要。

### 動作確認（2026-03-07）

トヨタ自動車 (7203) 2023年度で正常動作確認：

```
QUARTER  CURRENT_FISCAL_YEAR_START_DATE  NET_SALES           OPERATING_PROFIT
1Q       2023-04-01                      10,546,831,000,000  1,120,900,000,000
2Q       2023-04-01                      11,434,786,000,000  1,438,394,000,000
3Q       2023-04-01                      12,041,103,000,000  1,680,944,000,000
4Q       2023-04-01                      11,072,605,000,000  1,112,696,000,000
```

（各行が単独四半期値。4Q = FY累積 - 3Q累積 で算出）
