# Dropbox ファイル操作（アップロード / ダウンロード）

**カテゴリ**: tools
**作成日**: 2026-03-02
**ステータス**: 有効
**関連ファイル**: `scripts/stock_price_load.py`, `scripts/shina_margin_balance_load.py`, `scripts/is_holiday.py`, `scripts/edinet_delay.py`

## 概要

Dropbox SDK（`dropbox` パッケージ）を使ってファイルのアップロード・ダウンロードを行う。
認証情報は共通（アプリ固定）、パスはスクリプトごとに可変。Cloud Run からもAPI経由でアクセス可能。

## 認証情報（共通・固定）

```python
DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
```

- `APP_KEY` / `APP_SECRET`: Dropbox Developer Console で登録したアプリの資格情報
- `REFRESH_TOKEN`: OAuth2 リフレッシュトークン。SDK が自動的にアクセストークンへ交換する

## ダウンロード（Cloud Run / リモート環境向け）

ローカルの Dropbox 同期フォルダがない環境（Cloud Run 等）では API 経由でファイルをダウンロードする。

### 基本パターン（コピペ用）

```python
import io
import dropbox
from dropbox.exceptions import ApiError

DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'

dbx = dropbox.Dropbox(
    app_key=DBX_APP_KEY,
    app_secret=DBX_APP_SECRET,
    oauth2_refresh_token=DBX_REFRESH_TOKEN,
)

# ---- ダウンロード ----
dbx_path = "/stock/BB_債券履歴_new.xlsx"
_, response = dbx.files_download(dbx_path)
data = io.BytesIO(response.content)

# ---- 用途に応じて読み込み ----
# Excel: openpyxl.load_workbook(data) or pd.read_excel(data, sheet_name="VIX")
# CSV:   pd.read_csv(data)
```

### 実装済みスクリプト

| スクリプト | DL対象 | 用途 |
|-----------|--------|------|
| `scripts/edinet_delay.py` | `/stock/script/edinet_delay/edinet_delay.xlsx` | 既存xlsx読み込み→加工→再アップロード |

### 注意事項

- `files_download()` はファイル全体をメモリに読む。150MB超のファイルには不向き
- ファイルが存在しない場合は `ApiError`（`is_path()` → `is_not_found()`）で判定可能
- Cloud Run で使う場合、認証情報は Secret Manager に格納すること（ハードコード禁止）

---

## アップロード

### 基本パターン（コピペ用）

```python
import dropbox
from dropbox.files import WriteMode

DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'

# ---- アップロード先パス（スクリプトごとに変える） ----
DBX_FOLDER = '/stock/script/kdb'   # ← ここだけ変更する
upload_path = f"{DBX_FOLDER}/filename.csv"

# ---- バイト列の用意（例: Shift-JIS CSV） ----
csv_bytes = df.to_csv(index=False, lineterminator='\r\n').encode('shift_jis', errors='ignore')

# ---- アップロード実行 ----
dbx = dropbox.Dropbox(
    app_key=DBX_APP_KEY,
    app_secret=DBX_APP_SECRET,
    oauth2_refresh_token=DBX_REFRESH_TOKEN,
)
dbx.files_upload(csv_bytes, upload_path, mode=WriteMode('overwrite'))
print(f"Dropbox アップロード完了: {upload_path}")
```

## 注意事項

### `WriteMode('overwrite')` を必ず指定する

デフォルト（`add`）は同名ファイルが存在するとエラーになる。
定期バッチで同じファイル名を上書きする場合は `overwrite` が必須。

### アップロードサイズ上限

- `files_upload()` は **150MB 以下**のみ対応
- 150MB を超える場合は `files_upload_session_start()` / `files_upload_session_append_v2()` / `files_upload_session_finish()` を使うセッションアップロードが必要（未実装）

### REFRESH_TOKEN の有効期限

- Offline アクセスのリフレッシュトークンは**無期限**（アプリを revoke しない限り有効）
- トークンが無効化された場合は Dropbox Developer Console で再発行して定数を更新する

### パスの形式

- Dropbox のパスは `/` で始まるフルパス（例: `/stock/script/kdb/file.csv`）
- `DBX_FOLDER` にスラッシュ終端は不要（`f"{DBX_FOLDER}/file.csv"` で結合する）
- **Dropbox一時ファイル置き場**: `/stock/temp/`（ローカル: `C:\Users\zonekun\Dropbox\stock\temp`）。ユーザーがスマホ等から確認したいad-hoc CSVはここにアップロードする（ローカル専用の一時ファイルは従来通り `C:\tmp\`）

## 依存ライブラリ

```bash
uv add dropbox
```

`pyproject.toml` の `dependencies` に追加済みであれば `uv sync` のみで OK。

## 実装例（スクリプトへの組み込みテンプレート）

```python
from dropbox.files import WriteMode
import dropbox

# --- Dropbox設定 ---
DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
DBX_FOLDER        = '/your/target/folder'   # ← スクリプトごとに設定


def save_to_dropbox(data_bytes: bytes, filename: str) -> None:
    """Dropbox にファイルをアップロードする."""
    if not DBX_REFRESH_TOKEN:
        print("Dropbox REFRESH_TOKEN 未設定のためスキップします。")
        return

    upload_path = f"{DBX_FOLDER}/{filename}"
    print(f"Dropbox アップロード中: {upload_path}")
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )
    dbx.files_upload(data_bytes, upload_path, mode=WriteMode('overwrite'))
    print("Dropbox アップロード完了。")
```

---

## Dropbox 容量不足対応パターン（全ジョブ共通）

### 方針

- Dropboxアップロードが容量不足で失敗しても **BQ/GCS等の主処理は継続する**
- 処理完了後にメール件名「DROPBOX容量不足」で通知し、`sys.exit(1)` で **異常終了** として扱う

### 対応済みスクリプト

| スクリプト | グローバル変数 | メール件名 |
|-----------|-------------|---------|
| `stock_price_load.py` | `_DROPBOX_ERROR: str` | `[STOCK_PRICE] 【異常終了】DROPBOX容量不足` |
| `shina_margin_balance_load.py` | `_DROPBOX_ERRORS: list[str]` | `【異常終了】DROPBOX容量不足 品貸・残高` |
| `is_holiday.py` | `_DROPBOX_ERROR: str` | `[is_holiday] 【異常終了】DROPBOX容量不足` |
| `edinet_delay.py` | `_DROPBOX_ERROR: str` | `[EDINET_DELAY] 【異常終了】DROPBOX容量不足` |

### 実装パターン（コピペ用）

```python
# 1. モジュールレベルに追加
_DROPBOX_ERROR: str = ""

# 2. アップロード関数内
def save_to_dropbox(...) -> None:
    global _DROPBOX_ERROR
    ...
    try:
        dbx.files_upload(data_bytes, upload_path, mode=WriteMode('overwrite'))
        print("Dropbox アップロード完了。")
    except Exception as e:
        if "insufficient_space" in str(e):
            _DROPBOX_ERROR = f"Dropbox 容量不足のためアップロードをスキップ: {upload_path}"
            print(f"[警告] {_DROPBOX_ERROR}")
        else:
            raise

# 3. main() の最後（主処理完了後）
if _DROPBOX_ERROR:
    send_mail(
        "[JOB_NAME] 【異常終了】DROPBOX容量不足",
        f"Dropboxへのアップロードが容量不足で失敗しました。\n"
        f"主処理（BQ/GCS）は正常完了しています。\n\n"
        f"スキップされたファイル: {_DROPBOX_ERROR}",
        attachment_text=log_text,
    )
    sys.exit(1)
```

### 検出方法

```python
"insufficient_space" in str(e)
```

`dropbox.exceptions.ApiError` の `str()` 表現に `"insufficient_space"` が含まれることを利用。
型チェック（`e.error.is_path()` 等）より文字列マッチの方がシンプルで誤検出も少ない。

---

## 根拠・出典

`scripts/stock_price_load.py` の `save_to_dropbox()` 関数（実稼働済み）から抽出。
