# EDINET 開示書類 ダウンロード → GCS 保存スクリプト

**カテゴリ**: tools
**作成日**: 2026-02-28
**ステータス**: 有効
**関連ファイル**: `scripts/edinet_download.py`
**パイプライン後続**: `docs/knowledges/tools/012_edinet_load.md`（本スクリプトが GCS に保存した HTML を BQ へ ETL する）
**動作確認環境**: Colab personal / Colab enterprise（稼働確認済み）/ Cloud Run（稼働確認済み 2026-02-28）

## 概要

EDINET API V2 を使用し、上場企業の開示書類（有報・届出書・TOB・自社株買い等）を
自動収集・ZIP解凍・HTML結合し、GCS `gs://stock_data_1930932/edinet/` へ保存するパイプライン。

**対応環境**: Colab personal / Colab enterprise（ローカルは未対象）

---

## 実行方法

Colab のセルで実行:

```bash
!python scripts/edinet_download.py
```

スクリプト冒頭の設定ブロックを書き換えてから実行する。

---

## 設定パラメータ（冒頭ブロック）

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `PRODUCTION_MODE` | `(RUNTIME == "cloudrun")` | Cloud Run では自動的に `True`（JPX全銘柄）。Colab/ローカルでは `False`（TEST_TICKERSのみ）。環境変数 `EDINET_PRODUCTION=true/false` でオーバーライド可 |
| `TEST_TICKERS` | `["7203"]` | テストモード時の対象コード |
| `DATE_SELECT_MODE` | `1` | `1`=今日 / `2`=SPECIFIED_DATE / `3`=RANGE_START_DATE〜今日。**`--from`/`--to` 引数指定時は無視される** |
| `SPECIFIED_DATE` | `"20260218"` | MODE=2 の日付 (YYYYMMDD) |
| `RANGE_START_DATE` | `"20240101"` | MODE=3 の開始日 (YYYYMMDD)。終了は常に今日 |
| `FILTER_BY_DOC_TYPE` | `True` | `True`=指定種別のみ（通常運用） / `False`=全書類（デバッグ） |
| `EXTRACT_ZIP` | `True` | `True`=解凍・結合加工 / `False`=ZIPのまま保存 |
| `API_KEY` | `"0607..."` | EDINET API Key |
| `GCS_BASE_URL` | `"gs://stock_data_1930932/edinet"` | GCS保存先ベースパス |

> **注意**: `RANGE_END_DATE` は廃止。MODE=3 の終了日は常に「今日」。

---

## 環境別の動作差異

| 項目 | colab_personal | colab_enterprise | cloudrun |
|------|----------------|-----------------|---------|
| 環境判別 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 未設定 | `google.colab` import 可 & `GOOGLE_CLOUD_PROJECT` 設定あり | `CLOUD_RUN_JOB` or `K_SERVICE` 設定あり、またはそれ以外 |
| GCS認証 | `auth.authenticate_user()` でインタラクティブ認証 | ADC（追加処理不要） | Attached Service Account の ADC（追加処理不要） |
| GCS アップロード | `google.cloud.storage` クライアント | 同左 | 同左 |
| EDINET API Key | 設定ブロックの `API_KEY` | 同左 | 環境変数 `EDINET_API_KEY` を優先（なければ設定ブロック値） |

> **注意**:
> - `detect_runtime()` はモジュール import 時に即時実行され `RUNTIME` 定数に格納される
> - Cloud Run では `google-cloud-storage` パッケージが必要（Dockerfile に追加すること）
> - GCS アップロードは `_upload_dir_to_gcs()` が担当（`gsutil` コマンド依存を廃止）
> - 既存ファイルのスキップ: `blob.exists()` チェックで `gsutil cp -n` 相当を実現

---

## 処理フロー

```
setup_environment() → 環境判別・GCS認証
    ↓
get_target_tickers()
    ├─ PRODUCTION_MODE=True: JPX公式サイトから銘柄一覧Excelを取得
    │   ETF/REIT/外国株等を除外（1306 は除外対象外）
    └─ PRODUCTION_MODE=False: TEST_TICKERS を使用
    ↓
日付ループ（DATE_SELECT_MODE に従う）
    ↓ 各日付
_edinet_get()（リトライ付き）で EDINET API documents.json（書類一覧）を取得
    ↓ secCode → 5桁コード（4桁+"0"）で銘柄を特定
フィルタ（FILTER_BY_DOC_TYPE）
    ├─ True:  DOC_GROUP_MAP に定義された書類種別のみ抽出
    └─ False: 全書類を対象（略称="全書類"）
    ↓ 対象書類ごとに
【GCS事前チェック】（Cloud Run & EXTRACT_ZIP=True のみ）
    → {prefix}_MERGED_REPORT.html が GCS に既存なら ZIP DL をスキップ（再実行高速化）
process_document() → _edinet_get()（リトライ付き）で EDINET API documents/{docID}?type=1 でZIPダウンロード
    ├─ EXTRACT_ZIP=False: ZIPのまま temp_{ticker}/ へ保存
    └─ EXTRACT_ZIP=True: ZIP展開・クレンジング・HTML結合
        ・AuditDoc/ フォルダを除外
        ・.xsd/.xml/画像ファイルを除外
        ・PublicDoc/ 内の複数HTMLをファイル名順に結合 → MERGED_REPORT.html
    ↓ 1日分完了後
_upload_dir_to_gcs() → google.cloud.storage クライアント（シングルトン）でアップロード
temp_{ticker}/ を削除（ディスク節約）
    ↓
time.sleep(0.1) でレートリミット対策
```

---

## 書類種別フィルタ（DOC_GROUP_MAP）

府令コード × 様式コードの組み合わせで書類を分類:

| 略称 | 対象書類 | 府令コード | 様式コード |
|------|---------|-----------|-----------|
| `有報年` | 有価証券報告書（年次）・確認書 | 010 | 030000/030001/032000/032001/040000/040001 |
| `有報四` | 四半期・半期報告書・確認書 | 010 | 043000/043001/043A00/043A01/050000/050001/052000/052001 |
| `届出書` | 有価証券届出書・通知書（各種） | 010 | 010000/010001/020000/020001/022000/022001/023000/023001/024000/024001/025000/025001/026000/026001/027000/027001 |
| `大量保` | 大量保有報告書（**コメントアウト中・現在除外**） | 060 | 010000/010002/020002/030000/030002/090001 |
| `ＴＯＢ` | 公開買付関連書類 | 040/050 | 040001/050006/060001/060007/080001/080008/020000/020001/030006/040001/040007 |
| `自社株` | 自己株券買付状況報告書 | 030 | 253000/253001 |

> - `届出書` は 022〜027 系の様式コードが追加された（旧版は 010/020 系のみ）
> - `ＴＯＢ` は大幅拡充（旧版は 2 エントリのみ）
> - `大量保` はコメントアウトにより **現在は取得されない**。復活させる場合はコメントを外す
> - `FILTER_BY_DOC_TYPE=True`（デフォルト）時は上記（大量保除く）のみ取得

---

## GCS ファイル命名規則

```
gs://stock_data_1930932/edinet/{証券コード4桁}/{ファイル名}
```

ファイル名フォーマット:
```
{証券コード}_{3文字略称}_{YYYYMMDD}_{書類名（特殊文字→_）}_{docID}_MERGED_REPORT.html
```

例:
```
gs://stock_data_1930932/edinet/7203/7203_有報四_20241113_半期報告書－第121期_S100UP32_MERGED_REPORT.html
```

---

## 銘柄特定ロジック

- EDINET API は `secCode` を **5桁**（4桁証券コード + "0"）で返す
- JPX リストの4桁コードに "0" を付加した `search_map` で突合する
- 例: `"72030"` → `"7203"`

---

## 出力先（GCS）

| 項目 | 値 |
|------|-----|
| バケット | `gs://stock_data_1930932/edinet/` |
| パス構造 | `edinet/{証券コード4桁}/{ファイル名}` |
| アップロード | `_upload_dir_to_gcs()`（`blob.exists()` で既存ファイルはスキップ） |
| 一時ファイル | `temp_{ticker}/`（アップロード後削除） |

**確認済みデータ範囲:**
| 確認日 | 期間 | 総ファイル数 |
|--------|------|------------|
| 2026-02-28 | 2025-01-06 〜 2025-12-26 | 22,958件 |

---

## 注意事項

- **google-cloud-storage 依存**: GCS アップロードに `google.cloud.storage` クライアントを使用（gsutil 廃止済み）
- **xlrd 必須**: JPX銘柄一覧が `.xls`（旧Excel形式）のため `docker/Dockerfile.edinet` に `xlrd>=2.0.1` が必要。openpyxl のみでは `Import xlrd failed` エラーになる
- **Cloud Run 対応済み**: `google-cloud-storage` パッケージと Attached Service Account があれば動作
- **EDINET_API_KEY 環境変数**: Cloud Run では `EDINET_API_KEY` 環境変数で API Key を渡す（設定ブロックのハードコード値を上書き）
- **レートリミット（sleep 設定）**: ドキュメント間 `time.sleep(1.0)`、日付間 `time.sleep(2.0)` が必要。旧値（0.1s/日のみ）ではドキュメント間の間隔がゼロで EDINET API に IP バンされる。**`time.sleep` を削除・短縮しないこと**
- **EDINET API 接続エラー対策 (`_edinet_get()`)**: 集中アクセス後にAPIが接続拒否することがある。`_edinet_get()` ヘルパー経由で指数バックオフリトライ（10→20→40→80→160s）を行う。`requests.get()` を直接呼ばないこと
- **GCS事前チェック**: ZIP DL前に `MERGED_REPORT.html` の GCS 存在確認を行い、既存なら API 呼び出し自体をスキップ（再実行時の EDINET API 負荷を大幅削減）
- **GCS クライアントはシングルトン**: `_gcs_client_lazy` で遅延初期化・使い回し。ループ内で毎回 `storage.Client()` を生成しないこと
- **重複スキップ**: `blob.exists()` チェックにより既存ファイルは上書きしない（`gsutil cp -n` 相当）
- **HTML結合順序**: `PublicDoc/` 内のHTMLはファイル名の辞書順（章立て順）でソートして結合
- **修正時の厳守事項**:
  - HTML結合順序（ファイル名辞書順）を変えないこと
  - ファイル命名規則（`{コード}_{略称}_{日付}_{書類名}_{ID}`）を変えないこと
  - `time.sleep` を削除しないこと
  - GCS アップロードは1日単位バッチ処理を維持すること

---

## EDINET API IP バン機構（2026-03-01 判明）

### 症状
`SSLError: UNEXPECTED_EOF_WHILE_READING` が全リクエストで連続発生。HTTP 429 は返さない。

### 正体
EDINET サーバーが TCP/SSL レベルで接続を強制切断する。TLS ハンドシェイク途中で EOF を返すため `requests` は SSLError として受け取る。**IP 単位でのブロック**であり、ローカル PC からは 200 OK でも Cloud Run の IP だけ全リクエストが弾かれる状態になる。

### 発動条件
ドキュメント間の sleep なしで 366 日分（数万リクエスト）を一括実行すると発動。前回の成功ジョブが大量取得して IP を消耗し、次ジョブ開始時点ですでにバン状態だったケースも確認。

### 回復方法
ジョブをキャンセルして IP を休ませる。`time.sleep(1.0)` / `time.sleep(2.0)` を入れた修正版で**月別分割実行**（例: 47日 / 91日 / 92日 / 92日）することで再発を防ぐ。

### プロキシローテーションについて
技術的には `requests.get(proxies={"https": ...})` で実装可能だが採用しない。理由:（1）信頼できる住宅用 IP プロキシは有料（$5〜15/GB）、（2）金融庁の政府系 API でレート制限を意図的に回避するのは利用規約リスクあり、（3）sleep 調整 + 月別分割で問題なく解決できる。

---

## 再開機能（レジューム）設計（2026-03-01 追加）

### 概要
長時間ジョブの途中クラッシュ・IP バン発生時に、完了済み日付をスキップして再開できる。

### 再開ログ
- **保存先**: `gs://stock_data_1930932/edinet/_resume_{YYYYMMDD}_{YYYYMMDD}.txt`
- **フォーマット**: 1行1エントリ `DONE:YYYY-MM-DD`
- **追記タイミング**: 1日分の処理が完了し GCS アップロードまで終わった直後（`time.sleep(2.0)` の後）
- **追記しない**: 例外発生時（SSLError 等）→ 次回実行で自動再試行

### 起動時の動作
1. 対象期間に対応する再開ログを GCS から読み込む
2. 完了済み日付を `target_dates` から除外
3. スキップがあれば `[再開モード]` バナーを表示して再開日付・残り日数を明示
4. 全日付完了済みなら再開ログを削除して終了

### 正常終了時のログ削除
全日付ループ完了後に `_delete_resume_log()` でログを削除。これにより通常実行（次回の別期間実行等）に干渉しない。日付範囲がファイル名に含まれるため、異なる期間のログとは衝突しない。

---

## エンド・リトライロジック（2026-03-01 追加）

### 目的

EDINET API の一時的な SSL エラー（IP バン・瞬断）は、全日付を一周した後に再試行すると解消することが多い。
ループ中にエラーが発生するたびに即時リトライすると、バン状態の IP でリクエストを連発してしまい逆効果になる。

### 方式

「一通り全日付を処理し、失敗した日付をリストに溜めておき、最後に1回だけまとめてリトライする」方式。

```python
total_dl: int = 0
failed_dates: list[datetime] = []

# --- メインループ ---
for d in target_dates:
    d_str = d.strftime("%Y-%m-%d")
    try:
        total_dl += _process_one_date(d, search_map, resume_blob)
    except Exception as e:
        print(f"【エラー】{d_str}: {e}")
        failed_dates.append(d)

# --- エンド・リトライ（全日付処理後に1回のみ）---
if failed_dates:
    print(f"\n--- エラー日付のリトライ ({len(failed_dates)} 日) ---")
    retry_failed: list[datetime] = []
    for d in failed_dates:
        d_str = d.strftime("%Y-%m-%d")
        try:
            total_dl += _process_one_date(d, search_map, resume_blob)
            print(f"[リトライ成功] {d_str}")
        except Exception as e:
            print(f"[リトライ失敗] {d_str}: {e}")
            retry_failed.append(d)
    if retry_failed:
        print(f"リトライ後も失敗: {[d.strftime('%Y-%m-%d') for d in retry_failed]}")
    failed_dates = retry_failed

_delete_resume_log(resume_blob)
```

### `_process_one_date()` ヘルパー

メインループとエンドリトライで同じ処理を再利用するため、1日分の処理を関数化する。

```python
def _process_one_date(
    d: datetime,
    search_map: dict[str, str],
    resume_blob: storage.Blob,
) -> int:
    """1日分のEDINETダウンロード処理。成功なら取得件数を返す。例外はそのまま上位へ伝播。"""
    # ... 日付ループ内の処理をそのまま移植 ...
    return dl_count
```

### 判断基準

| 状況 | 対処 |
|------|------|
| エラーが連続している（バン中） | リトライしても失敗。次回実行でレジューム機能が引き継ぐ |
| エラーが散発的（瞬断・タイムアウト） | エンドリトライで解消することが多い |
| リトライ後も失敗した日付 | ログに `リトライ後も失敗: [...]` と出力。次回実行でレジューム機能が自動スキップ |

### Cloud Run での OOM 対策との組み合わせ

大量ダウンロード時は月別分割 + エンドリトライ + レジューム機能を組み合わせて使う:
1. 月別に `--from` / `--to` を分けて実行（メモリ蓄積を防ぐ）
2. 各月の実行内でエンドリトライが一時的な API エラーをカバー
3. OOM や完全バンが発生しても、次回実行でレジューム機能が続きから再開

### `_report_gcs_bq_diff()` OOM 問題（2026-03-04 修正済み）

**症状**: Cloud Run（Gen2）で Signal 7（OOM Killed）が発生。ログに `The configured memory limit was reached` が表示される。

**原因**: `_report_gcs_bq_diff()` 関数が以下の2箇所でメモリを大量消費していた:
- GCS の全ブロブ名を Python `set` に全件展開
- BQ の全 `file_name` を Python `set` に全件展開

GCS に数万ファイルが蓄積した段階でメモリ（4Gi）を超過する。

**修正内容**（2026-03-04 適用済み）:
```python
# ❌ 旧: 全件をメモリに展開
gcs_names = {b.name for b in bucket.list_blobs(prefix=GCS_PREFIX) if ...}
bq_names  = {r.file_name for r in bq.query("SELECT file_name FROM ...").result()}

# ✅ 新: カウントのみ（メモリ O(1)）
bq_count = next(iter(bq.query(
    f"SELECT COUNT(DISTINCT file_name) AS cnt FROM `{BQ_TABLE}`"
).result())).cnt
gcs_count = sum(
    1 for b in bucket.list_blobs(prefix=GCS_PREFIX)
    if b.name.lower().endswith(('.htm', '.html')) and "大量保有" not in b.name
)
```

差分の「何が未登録か」は分からなくなるが、「件数の差」だけで実用上は十分。

---

## バックフィル計画

**計画**: `docs/plans/tools-012_edinet_backfill_20260513_221200.md`（2017-2023年 + 2026年欠損のGCSダウンロード＋BQロード）
