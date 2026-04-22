# クロスプラットフォームPython スクリプト設計パターン

**カテゴリ**: tools
**作成日**: 2026-02-28
**ステータス**: 有効
**関連ファイル**: `scripts/tdnet_download.py`, `src/collector/runtime.py`

## 概要

ローカルWindows / Google Colab / Cloud Run Job の3環境で動くPythonスクリプトを作る際の
設計パターンと落とし穴。`tdnet_download.py` の実装・バグ修正で得た知見。

---

## 1. 実行環境の自動判別

```python
def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "local" | "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    import os
    # Cloud Run Jobs:     CLOUD_RUN_JOB（K_JOB は存在しない）
    # Cloud Run Services: K_SERVICE
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"

RUNTIME: str = detect_runtime()
```

> **注意**: Cloud Run Jobs の環境変数は `CLOUD_RUN_JOB`。`K_JOB` は**存在しない**。

---

## 2. argparse の落とし穴（Colab）

### 問題

Colab では `sys.argv` にJupyterカーネルの引数が混入する:

```
['/usr/local/lib/.../ipykernel_launcher.py', '-f', '/root/.../kernel-abc123.json']
```

`parser.parse_args()` がこれを拾い、unrecognized arguments エラーでクラッシュする。

### 解決策: Colab時はargparseをスキップ

```python
def parse_args() -> argparse.Namespace:
    # Colab環境ではsys.argvにJupyterカーネルの引数が混入するためargparseは使わない
    # → ファイル冒頭の設定ブロック（DATE_MODE等）を直接編集すること
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None, save_dir=SAVE_DIR)

    parser = argparse.ArgumentParser(...)
    parser.add_argument("--from", dest="date_from", ...)
    return parser.parse_args()
```

**Colab向けの設定は「ファイル冒頭の設定ブロックを直接書き換える」運用**にする。
Cloud Run（`cloudrun`）は `sys.argv` がクリーンなのでargparseはそのまま使える。

---

## 3. GCS認証（環境別）

```python
def _get_gcs_client():
    from google.cloud import storage
    if RUNTIME == "colab_personal":
        import json
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds = service_account.Credentials.from_service_account_info(key_info)
        return storage.Client(credentials=creds)
    else:  # colab_enterprise / cloudrun: ADC を使用
        return storage.Client()
    # local は GCS を使わず、ローカルファイルシステムに保存する
```

| 環境 | 認証方法 |
|------|---------|
| `local` | GCSを使わない。ローカルパスに保存 |
| `colab_personal` | Colab Secrets（`GCP_SA_KEY`）からサービスアカウントキーを取得 |
| `colab_enterprise` | ADC（Application Default Credentials）。追加設定不要 |
| `cloudrun` | ADC（Cloud Runはサービスアカウントを自動付与） |

---

## 4. 保存先の振り分けパターン

```python
if RUNTIME == "local":
    save_path = save_dir / code4 / filename
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(content)
else:  # Colab / Cloud Run → GCS
    blob_path = f"{GCS_PREFIX}/{code4}/{filename}"
    bucket.blob(blob_path).upload_from_string(content, content_type="application/pdf")
```

---

## 5. 設定ブロックの設計

ファイル冒頭に「ここを書き換える」ブロックを明示し、argparse（CLIオプション）との
優先順位を明記する。

```python
# ╔═══════════════════════════════════════════╗
# ║  ★ 実行設定（ここを直接書き換えて使う）      ║
# ╚═══════════════════════════════════════════╝
DATE_MODE = "t"          # "t"=今日 / "1"=特定の1日 / "r"=期間
DATE_SINGLE = "20260224"
DATE_FROM   = "20260217"
DATE_TO     = "20260224"
SAVE_DIR    = Path("C:/Users/zonekun/Dropbox/stock/script/tdnet")  # local のみ使用
GCS_BUCKET  = "stock_data_1930932"   # Colab / Cloud Run のみ使用
GCS_PREFIX  = "tdnet"
```

**優先順位ルール（CLIで上書き可能な場合）**:
`--from`/`--to` 引数 > `DATE_MODE` 設定ブロック

---

## 6. その他のクロスプラットフォーム注意点

| 項目 | NG | OK |
|------|----|----|
| ファイルエンコーディング | `open(f)` | `open(f, encoding="utf-8")` |
| 標準出力の文字化け（Windows） | そのまま実行 | `PYTHONUTF8=1` を付けて実行 |
| URLパスのstem取得 | `Path(url).stem`（Windowsでは`/`を区切りと解釈しない） | `PurePosixPath(url).stem` |
| ローカルパス | ハードコードしたWindowsパス | `Path` + 環境変数 or 引数で切り替え |

### URLからファイルstemを取得する場合

```python
from pathlib import PurePosixPath
stem = PurePosixPath(url.rstrip("/").split("/")[-1]).stem
# または
stem = url.rstrip("/").split("/")[-1].rsplit(".", 1)[0]
```

---

## 7. テンプレート構成（新規スクリプト作成時）

```python
# ╔═══════════════════════════╗
# ║  ★ 実行設定（書き換えてOK） ║
# ╚═══════════════════════════╝
DATE_MODE = "t"
SAVE_DIR  = Path("C:/...")   # local のみ使用
GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "xxx"

RUNTIME = detect_runtime()

def parse_args():
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(...)   # 設定ブロックに委ねる
    parser = argparse.ArgumentParser(...)
    return parser.parse_args()

def main():
    args = parse_args()
    ...
    if RUNTIME == "local":
        # ローカルファイル保存
    else:  # Colab / Cloud Run → GCS
        # GCSアップロード

if __name__ == "__main__":
    main()
```
