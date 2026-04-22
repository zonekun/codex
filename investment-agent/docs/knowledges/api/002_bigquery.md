# BigQuery 使用上の注意事項

**カテゴリ**: api
**作成日**: 2026-02-23
**ステータス**: 有効
**適用範囲**: 分析スクリプト・バックテストスクリプト共通

---

## BigQuery オフロード原則

**BigQuery で実行できる処理は Python に持ち込まない。**
BQから取得するのは「統計処理の入力として必要な最小限のデータ」だけにする。

### Python に残してよい処理（BQで代替不可）

- 統計検定: ADF検定・グレンジャー因果性・Mann-Whitney U 等（statsmodels / scipy）
- 時系列モデル: AR白色化・CCF・VAR・共和分検定（statsmodels）
- 機械学習モデル（sklearn等）
- 可視化（matplotlib / plotly）

### BigQuery にオフロードすべき処理の実装例

#### 週次終値への変換（`DATE_TRUNC` + `LAST_VALUE`）

```sql
SELECT
  DATE_TRUNC(YEARDATE, WEEK(MONDAY)) AS week_start,
  TICKER,
  LAST_VALUE(CLOSE) OVER (
    PARTITION BY TICKER, DATE_TRUNC(YEARDATE, WEEK(MONDAY))
    ORDER BY YEARDATE
    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
  ) AS weekly_close
FROM `gmailpj-357912.STOCK.STOCK_PRICE`
WHERE TICKER IN UNNEST(@tickers)
  AND YEARDATE BETWEEN @date_from AND @date_to
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TICKER, DATE_TRUNC(YEARDATE, WEEK(MONDAY))
  ORDER BY YEARDATE DESC
) = 1
ORDER BY TICKER, week_start
```

#### 週次リターンの計算（`LAG` ウィンドウ関数）

```sql
WITH weekly AS (
  -- 上記の週次終値クエリ
)
SELECT
  week_start,
  TICKER,
  weekly_close,
  (weekly_close - LAG(weekly_close) OVER (PARTITION BY TICKER ORDER BY week_start))
    / LAG(weekly_close) OVER (PARTITION BY TICKER ORDER BY week_start) AS weekly_ret
FROM weekly
```

#### 移動平均・前年同期比（ウィンドウ関数）

```sql
SELECT
  YEARDATE,
  TICKER,
  CLOSE,
  AVG(CLOSE) OVER (
    PARTITION BY TICKER ORDER BY YEARDATE
    ROWS BETWEEN 51 PRECEDING AND CURRENT ROW   -- 52週移動平均
  ) AS ma52w,
  CLOSE / LAG(CLOSE, 52) OVER (PARTITION BY TICKER ORDER BY YEARDATE) - 1
    AS yoy_ret   -- 前年同期比
FROM `gmailpj-357912.STOCK.STOCK_PRICE`
WHERE TICKER IN UNNEST(@tickers)
```

---

## 認証

```python
from google.oauth2 import service_account
from google.cloud import bigquery

# ✅ 正しい（settings経由でキーファイルパスを取得）
from src.core.config import get_settings
settings = get_settings()
creds = service_account.Credentials.from_service_account_file(
    settings.google_application_credentials
)
client = bigquery.Client(project=settings.gcp_project_id, credentials=creds)

# ❌ 誤り（os.environ["GOOGLE_APPLICATION_CREDENTIALS"] への注入は不要）
# os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "..."
```

- キーファイル: `keys/gcp-service-account.json`
- サービスアカウント: `bq-loader@gmailpj-357912.iam.gserviceaccount.com`
- `.env` の `GOOGLE_APPLICATION_CREDENTIALS` に設定済み（`src/core/config.py` の `Settings` が読む）

---

## テーブル参照の書き方

```sql
-- ✅ バッククォートで囲む（ハイフンがあるためプロジェクト名には必須）
SELECT * FROM `gmailpj-357912.STOCK.STOCK_PRICE`

-- ❌ 誤り
SELECT * FROM gmailpj-357912.STOCK.STOCK_PRICE  -- ハイフンで構文エラー
```

- テーブル名・カラム名は**すべて大文字**（`STOCK_PRICE`, `YEARDATE`, `TICKER` 等）
- SQL内のバッククォートはシングルクォート文字列内に書く（エスケープ不要）

---

## 複数ティッカーを IN 句で渡す（UNNEST パラメータ化クエリ）

SQL文字列に直接ティッカーを埋め込むのは**インジェクションリスクあり・非推奨**。
`ArrayQueryParameter` + `UNNEST` を使うこと。

```python
from google.cloud import bigquery

SQL = """
SELECT YEARDATE, TICKER, CLOSE
FROM `gmailpj-357912.STOCK.STOCK_PRICE`
WHERE TICKER IN UNNEST(@tickers)
  AND YEARDATE BETWEEN @date_from AND @date_to
ORDER BY TICKER, YEARDATE
"""

tickers   = ["7550", "3197", "9861"]   # 4桁コード
date_from = "2017-01-01"
date_to   = "2026-02-21"

cfg = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ArrayQueryParameter("tickers",   "STRING", tickers),
        bigquery.ScalarQueryParameter("date_from", "DATE",   date_from),
        bigquery.ScalarQueryParameter("date_to",   "DATE",   date_to),
    ]
)
df = client.query(SQL, job_config=cfg).to_dataframe()
```

- `UNNEST(@tickers)` で配列をIN句に展開
- 日付は `ScalarQueryParameter("name", "DATE", "YYYY-MM-DD")` で渡す
- `to_dataframe()` で pandas DataFrame に変換（`google-cloud-bigquery[pandas]` 必要）

---

## TICKER の形式

| 用途 | 形式 | 例 |
|------|------|-----|
| `STOCK.STOCK_PRICE` クエリ | **4桁**文字列 | `"7550"` |
| J-Quants API | **5桁**（4桁 + "0"） | `"75500"` |
| yfinance | **4桁 + ".T"** | `"7550.T"` |

```python
# BigQuery → J-Quants 変換
jq_code = bq_ticker + "0"          # "7550" → "75500"

# BigQuery → yfinance 変換
yf_code = bq_ticker + ".T"         # "7550" → "7550.T"
```

---

## 週次リターンへの変換（STOCK_PRICE からの典型処理）

```python
import pandas as pd

# BQから取得したDataFrame（YEARDATE: date, TICKER: str, CLOSE: int）
df["YEARDATE"] = pd.to_datetime(df["YEARDATE"])
df = df.sort_values(["TICKER", "YEARDATE"])

# 週次に集約（各週の最終営業日の終値）
df_weekly = (
    df.set_index("YEARDATE")
      .groupby("TICKER")["CLOSE"]
      .resample("W-FRI")             # 金曜締め（日本市場は金曜が週末）
      .last()
      .reset_index()
)

# 週次リターン
df_weekly["ret"] = df_weekly.groupby("TICKER")["CLOSE"].pct_change()
```

- `resample("W-FRI")`: 金曜日を週末とした週次リサンプル
- `.last()`: 週内の最終営業日の終値を取得
- `pct_change()`: 前週比リターン（欠損週はNaN）

---

## load_table_from_dataframe の型変換（pyarrow 三大地雷）

`load_table_from_dataframe()` は BQ テーブルのスキーマを読んで pyarrow 経由で型変換する。
pandas の dtype が BQ スキーマと不一致だとエラーになる。

### 必須の前処理パターン

```python
import datetime
import pandas as pd

def preprocess_for_bq(df: pd.DataFrame) -> pd.DataFrame:
    # ① 欠損値の空文字列 → None（API が "" で欠損を返す場合に必須）
    #    pyarrow は "" を int/float/bool に変換できないため必ずやること
    df = df.replace("", None)

    # ② DATE 型列: 文字列 → datetime.date
    #    pyarrow は文字列を BQ DATE に変換できない
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # ③ TIME 型列: 文字列 → datetime.time
    #    pyarrow は文字列を BQ TIME に変換できない
    if "MY_TIME_COL" in df.columns:
        def _to_time(x):
            if not isinstance(x, str) or not x:
                return None
            if len(x) == 5:          # "HH:MM" → "HH:MM:00"
                x += ":00"
            try:
                return datetime.time.fromisoformat(x)
            except ValueError:
                return None
        df["MY_TIME_COL"] = df["MY_TIME_COL"].apply(_to_time)

    # ④ BOOLEAN 型列: "true"/"false" 文字列 → bool
    for col in BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].map(
                lambda x: True if x == "true" else (False if x == "false" else None)
            )

    # ⑤ object dtype の数値列 → float64（① で None になった後も object のまま残る列対策）
    #    STRING / DATE / TIME / BOOL 列以外に apply する
    non_numeric = STRING_COLS | DATE_COLS | BOOL_COLS | {"MY_TIME_COL"}
    for col in df.select_dtypes(include="object").columns:
        if col not in non_numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ⑥ float64 → Int64（BQ INTEGER 型列の nullable 整数変換）
    for col in df.select_dtypes(include="float64").columns:
        non_null = df[col].dropna()
        if len(non_null) > 0 and (non_null == non_null.round()).all():
            df[col] = df[col].astype("Int64")

    return df
```

### BQ スキーマの事前確認

列数が多い場合は先にスキーマを確認してから変換方針を決める:

```python
table = client.get_table("gmailpj-357912.DATASET.TABLE")
for f in table.schema:
    print(f"{f.name}\t{f.field_type}")
# → DATE / TIME / BOOLEAN / INTEGER / FLOAT / STRING を分類して変換処理を設計する
```

### よくある pyarrow エラーと対処

| エラーメッセージ | 原因 | 対処 |
|----------------|------|------|
| `Could not convert '' with type str: tried to convert to int64` | 空文字列 `""` が整数列に混入 | `df.replace("", None)` + `pd.to_numeric` |
| `Error converting Pandas column "COL" with datatype "object" to DATE` | 文字列を DATE 型に変換不可 | `pd.to_datetime().dt.date` |
| `Error converting ... to TIME` | 文字列を TIME 型に変換不可 | `datetime.time.fromisoformat()` |
| `Error converting ... to BOOLEAN` | `"true"/"false"` 文字列を bool に変換不可 | `map()` で Python `bool` に変換 |

---

## よくあるエラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `Syntax error: Expected end of input but got "-"` | プロジェクト名のハイフンをバッククォートで囲んでいない | `` `gmailpj-357912.STOCK.TABLE` `` |
| `TypeError: can only concatenate str (not "int") to str` | TICKERを整数として扱っている | `str(ticker)` で明示変換、またはBQクエリ側で `CAST(TICKER AS STRING)` |
| `KeyError: 'YEARDATE'` | カラム名の大文字・小文字ミス | `df.columns` で確認。BQは大文字で返す |
| `403 Access Denied` | サービスアカウントの権限不足 | `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` を確認 |
| `403 request failed: the user does not have 'bigquery.readsessions.create' permission` | `google-cloud-bigquery-storage` がインストールされていると `to_dataframe()` が BQ Storage API を自動使用しようとする | `to_dataframe(create_bqstorage_client=False)` を指定して無効化 |
| `TypeError: data type 'dbdate' not understood` | BQ DATE 列を含む parquet を `pandas.read_parquet()` で読むと DATE 型が未認識 | `pyarrow.parquet.read_table()` で読む。またはBQクエリ側で `CAST(YEARDATE AS STRING)` して文字列で取得 |
| `UPDATE or DELETE ... would affect rows in the streaming buffer` | streaming insert 直後（数分〜数時間）は DELETE 不可 | 下記「streaming buffer の DELETE 回避」参照 |

---

## streaming buffer の DELETE 回避

streaming insert（`insert_rows_json` 等）の直後はバッファに行が残るため `DELETE` / `UPDATE` が失敗する。

### 事前予防: Load Job を使う（2026-04-18 追記）

**新規実装では `load_table_from_json`（BQ Load Job）を第一選択にする**。streaming buffer を介さないため DML が即時可能で、かつ Load Job 自体は**無料**。

```python
from google.cloud.bigquery import LoadJobConfig, SourceFormat, WriteDisposition

rows = [{"DOC_ID": "...", "TICKER": "...", ...}, ...]  # list of dict

job_config = LoadJobConfig(
    source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
    write_disposition=WriteDisposition.WRITE_APPEND,
)
job = bq.load_table_from_json(rows, TABLE_ID, job_config=job_config)
job.result()  # 完了待ち（同期）
```

### insert 方式の使い分け

| 観点 | `insert_rows_json` (streaming) | `load_table_from_json` (Load Job) |
|------|-------------------------------|------------------------------------|
| レイテンシ | 低（数秒） | 中（数秒〜数十秒、非同期 job.result() 待ち） |
| streaming buffer | 入る（30-90分 DML 不可） | **入らない**（即 DML 可） |
| 料金 | 有料 $0.01 / 200 MB | **無料** |
| 1日のクオータ | 無制限（行数のみ） | テーブル毎 1,500 jobs / day |
| schema 自動検出 | ❌（row 内容のみ） | ✅（auto detect） |
| 小量頻繁 insert（低レイテンシ必須） | ✅ 向き | △ job 起動オーバーヘッド |
| ETL / バッチ投入 | △ streaming buffer 問題 | ✅ **推奨** |
| insert 後すぐ DELETE/UPDATE する設計 | ❌ 絶対に避ける | ✅ **推奨** |

**判断基準**: データ投入後に「UPDATE / DELETE / MERGE が即時必要」なら **Load Job 一択**。低レイテンシ必須（ダッシュボードにリアルタイム反映等）かつ DML 不要な場合のみ streaming。

### Load Job API の選択（2026-04-20 追記、大量 row は GCS upload 経由必須）

Load Job には 3 つの API がある。**row 数が多い（目安 10K 超）場合は必ず `load_table_from_uri` を使う**。

| API | データ source | Python プロセス memory | 大量 row 時のリスク |
|-----|--------------|----------------------:|--------------------|
| `load_table_from_json(list)` | Python list[dict] | list + JSON 文字列で 2 倍保持 | 20K row で 100MB+、OOM リスク |
| `load_table_from_file(fp)` | ローカルファイル | HTTP upload buffer で GB 単位膨張 | **OOM 事故実績あり**（19K doc で 8Gi 超）|
| **`load_table_from_uri(gcs_uri)`** | **GCS 上 JSONL** | **ほぼゼロ**（BQ が直接読む） | **なし** |

#### 実装パターン（推奨）

```python
import tempfile, json
from google.cloud import bigquery
from google.cloud.bigquery import LoadJobConfig, SourceFormat, WriteDisposition

# 1. tempfile に NDJSON stream write（メモリに list を保持しない）
tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".jsonl", delete=False)
tmp_path = tmp.name
with tmp:
    for row in generate_rows():
        tmp.write(json.dumps(row, ensure_ascii=False))
        tmp.write("\n")

# 2. GCS へ upload（streaming、Python buffer 極小）
blob = bucket.blob(f"batch_prediction/xxx/upload_{ts}.jsonl")
blob.upload_from_filename(tmp_path)
gcs_uri = f"gs://{bucket.name}/{blob.name}"

# 3. BQ Load Job（BQ が GCS から直接読む、Python プロセス経由しない）
job_config = LoadJobConfig(
    source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
    write_disposition=WriteDisposition.WRITE_APPEND,
)
job = bq.load_table_from_uri(gcs_uri, TABLE_ID, job_config=job_config)
job.result()

# 4. GCS 一時ファイル削除（Load Job 完遂後、数秒で不要）
try: blob.delete()
except: pass

# 5. ローカル tempfile も削除
Path(tmp_path).unlink(missing_ok=True)
```

**コスト**: GCS ストレージは秒単位削除で実質ゼロ（~$0.0005/run）。Load Job 自体は無料。

**事故事例**: 2026-04-20 TDnet ai-finalize で `load_table_from_file` により 19,156 doc の insert が 8Gi / 16Gi OOM で失敗。`load_table_from_uri` に切り替えて解決。詳細 `docs/knowledges/tools/013-2_monitor_backfill.md` インシデント #6。

### 事後対処: テーブル再作成

既に streaming insert してしまった行を消す場合:

### 回避パターン: テーブル再作成

```python
# 1. 削除したい行を除いた新テーブルを作成
client.query("""
    CREATE OR REPLACE TABLE `project.DATASET.TABLE_NEW`
    AS SELECT * FROM `project.DATASET.TABLE`
    WHERE <削除したくない条件>
""").result()

# 2. 元テーブルを削除
client.delete_table("project.DATASET.TABLE")

# 3. 新テーブルをリネーム（ALTER TABLE RENAME）
client.query("""
    ALTER TABLE `project.DATASET.TABLE_NEW`
    RENAME TO TABLE
""").result()
```

### 注意

- `CREATE OR REPLACE TABLE ... AS SELECT *` で作ったテーブルはパーティション設定が引き継がれない
- 元テーブルがパーティション済みの場合は `copy_table` で WRITE_TRUNCATE するとエラーになる → 上記の DROP + RENAME パターンを使う
- `ALTER TABLE RENAME TO` は BQ でサポート済み（2023年〜）

---

## 大量 UPDATE の最適化（2026-04-20 追記）

**事象**: `scripts/load_shareholder_from_index.py` で37,700レコードの upsert を行った際、per-record `DELETE + INSERT` パターンで **0.5 row/sec、完了ETA 21時間**となりクラッシュ。

### per-record DELETE + INSERT（遅い、アンチパターン）

```python
# ❌ 1件毎に DML クエリ発行 → クエリ料金 + レイテンシ でスケールしない
for ext in extracts:
    bq.query("DELETE FROM ... WHERE key = @k", ...).result()
    bq.query("INSERT INTO ... VALUES (...)", ...).result()
```

単一クエリ ~500ms × 37k = 18時間以上。さらに同時クエリ上限 (100 同時 queries / project) に引っかかる。

### 最適化1: `insert_rows_json` バッチ（DELETE不要な新規投入時）

```python
# ✅ 200件バッチで streaming insert → 5 row/sec → 2時間
pending = []
for ext in extracts:
    pending.append(to_row(ext))
    if len(pending) >= 200:
        bq.insert_rows_json(TABLE_ID, pending)  # batch
        pending = []
```

**前提**: INSERT のみで良い（重複排除は事後 SELECT DISTINCT や resume で）。新規テーブルの初期ロード向き。

### 最適化2: 一時テーブル JOIN UPDATE（安全な大量 UPDATE）

SQL インジェクション回避 + 大量値を UPDATE する場合の定番:

```python
import uuid

tmp_table = f"{PROJECT}.STOCK._tmp_update_{uuid.uuid4().hex[:8]}"
# 1. 一時テーブル作成 + insert_rows_json で値ロード
rows = [{"KEY": k, "NEW_VAL": v} for k, v in updates.items()]
table = bq.create_table(bigquery.Table(tmp_table, schema=schema))
bq.insert_rows_json(tmp_table, rows)

# 2. JOIN UPDATE（一回のクエリで全件更新）
bq.query(f"""
    UPDATE `{TABLE_ID}` t
    SET t.TARGET_COL = s.NEW_VAL
    FROM `{tmp_table}` s
    WHERE t.KEY = s.KEY
""").result()

# 3. 一時テーブル削除
bq.delete_table(tmp_table, not_found_ok=True)
```

**採用例**: `scripts/apply_shareholder_listing_flag.py` で 5,700 株主名に対する bulk UPDATE。 f-string による SQL 組立 (`CASE WHEN`) を回避 + streaming buffer 未経由で即時 UPDATE 可能。

### 判断基準

| ケース | 推奨 |
|---|---|
| 新規テーブル大量 INSERT (DELETEなし) | `insert_rows_json` 200件バッチ or Load Job (前述) |
| 既存テーブル少量 UPDATE (<100件) | per-row `UPDATE ... WHERE key=...` で可 |
| 既存テーブル中〜大量 UPDATE (1k-100k件) | **一時テーブル JOIN UPDATE** |
| 既存テーブル全件書き換え | `CREATE OR REPLACE TABLE ... AS SELECT` |

---

## GCS `list_blobs` の per-prefix キャッシュ（2026-04-20 追記）

**事象**: `scripts/load_shareholder_from_index.py` で ticker × 14年 = 約 66,000件の GCS blob lookup を行った際、`storage_client.list_blobs(bucket, prefix='edinet/{ticker}/')` を **(ticker, year) 毎**に呼び出し → 1コールあたり 200-500ms で全体が非現実的遅さ。

### アンチパターン

```python
# ❌ ticker × year の組み合わせ毎に list_blobs → 66,000 コール
for ticker, year in records:
    blobs = list(storage_client.list_blobs(bucket, prefix=f"edinet/{ticker}/"))
    # find year-matching blob...
```

### 推奨パターン: ticker 単位キャッシュ

```python
import threading

_gcs_list_cache: dict[str, list[str]] = {}
_cache_lock = threading.Lock()

def _get_cached_blobs(storage_client, ticker: str) -> list[str]:
    with _cache_lock:
        if ticker in _gcs_list_cache:
            return _gcs_list_cache[ticker]
    # キャッシュミス時のみ GCS 呼び出し
    blobs = [b.name for b in storage_client.list_blobs(BUCKET, prefix=f"edinet/{ticker}/")]
    with _cache_lock:
        _gcs_list_cache[ticker] = blobs
    return blobs
```

**効果**: 66,000 コール → **5,129 コール** (unique ticker 数) に削減。処理時間が数分短縮、コストも約 1/12。

**注意点**:
- スレッド並列で同 prefix の重複 list を避けるため `threading.Lock` 必須（同時呼び出しの race はほぼ無害だが、料金の二重発生回避）
- 巨大 prefix（万単位 blob）では blob 名だけ保持し Blob オブジェクトは破棄（メモリ節約）
- 長時間ジョブでは TTL を設けないと stale になる — 年次バッチなら問題なし、日次バッチでは 1h TTL 推奨

---

## よくあるエラーと対処

| 状況 | エラー | 正解 |
|------|-------|------|
| テーブル名を推測してクエリ | `Not found: Table xxx was not found` | **必ず先に `client.list_tables()` でテーブル一覧を確認してからクエリを書く** |
| カラム名を推測してクエリ | `Unrecognized name: XXX` | **BQクエリを書く前に必ず `data_catalog.md` のスキーマ定義を読む**。特に `STOCK_CODE_LIST` は `Code`/`CompanyName` でなく `TICKER`/`STOCK_NAME` |
| `DATE` をカラム名に使う | `Unrecognized name: DATE` | BQ予約語。実際のカラム名をスキーマで確認する（例: `SUBMISSION_DATE`） |
| ストリーミング挿入直後に DELETE/UPDATE | `400 UPDATE or DELETE ... would affect rows in the streaming buffer` | **1〜2時間待つか、CTAS（CREATE TABLE AS SELECT で対象外レコードを別テーブルにコピー → swap → DROP）で対処** |

### Windows ローカルでの SSL エラー回避

Python BQ クライアントで `SSLEOFError: EOF occurred in violation of protocol` が発生する場合、`requests.Session.__init__` をパッチして全接続で `verify=False` にする。`ssl._create_default_https_context` や `PYTHONHTTPSVERIFY=0` では**効かない**。

```python
# ローカルスクリプトの先頭（bigquery.Client(...) より前）に追加
import urllib3, requests as _req
from requests.adapters import HTTPAdapter as _HA
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw): kw["verify"] = False; return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw): _orig(self, *a, **kw); self.mount("https://", _NoVerify()); self.verify = False
_req.Session.__init__ = _p
```

> gcloud CLI は影響なし（正常動作）。
