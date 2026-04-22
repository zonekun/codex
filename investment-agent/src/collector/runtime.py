"""実行環境自動判別・認証モジュール.

全データ収集モジュールが共通で使用する。
以下の3環境を自動判別し、適切な認証情報を返す:
  1. Google Colab（個人ユース）: google.colab importable & GOOGLE_CLOUD_PROJECT 未設定
  2. Google Cloud Enterprise Colab: google.colab importable & GOOGLE_CLOUD_PROJECT 設定済み
  3. ローカルPC: google.colab import不可
"""
from __future__ import annotations

import os
from typing import Literal

RuntimeType = Literal["colab_personal", "colab_enterprise", "local"]

# ローカル環境のデフォルトキーパス
_LOCAL_DEFAULT_KEY_PATH = r"keys/gcp-service-account.json"
_LOCAL_DEFAULT_DOWNLOAD_DIR = r"C:\Users\zonekun\Downloads"

# GCP設定
PROJECT_ID = "gmailpj-357912"
DATASET_ID = "STOCK"
BUCKET_NAME = "stock_data_1930932"


def detect_runtime() -> RuntimeType:
    """実行環境を自動判別する.

    Returns:
        "colab_personal" | "colab_enterprise" | "local"
    """
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


def get_credentials():
    """実行環境に応じた GCP 認証情報を取得する.

    Returns:
        google.auth.credentials.Credentials
    """
    runtime = detect_runtime()

    if runtime == "colab_personal":
        from google.colab import userdata
        import json
        from google.oauth2 import service_account
        key_json_str = userdata.get("GCP_SA_KEY")
        if not key_json_str:
            raise ValueError("Colab Secrets に 'GCP_SA_KEY' が未設定です")
        key_info = json.loads(key_json_str)
        return service_account.Credentials.from_service_account_info(key_info)

    elif runtime == "colab_enterprise":
        import google.auth
        credentials, project = google.auth.default()
        return credentials

    else:  # local
        from google.oauth2 import service_account
        key_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            _LOCAL_DEFAULT_KEY_PATH,
        )
        if not os.path.exists(key_path):
            raise FileNotFoundError(
                f"サービスアカウントキーが見つかりません: {key_path}\n"
                "環境変数 GOOGLE_APPLICATION_CREDENTIALS を設定するか、"
                f"プロジェクト直下の {_LOCAL_DEFAULT_KEY_PATH} にキーファイルを配置してください"
            )
        return service_account.Credentials.from_service_account_file(key_path)


def get_bigquery_client():
    """認証済みの BigQuery クライアントを返す."""
    from google.cloud import bigquery
    credentials = get_credentials()
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)


def get_storage_client():
    """認証済みの GCS クライアントを返す."""
    from google.cloud import storage
    credentials = get_credentials()
    return storage.Client(credentials=credentials, project=PROJECT_ID)


def get_download_dir() -> str:
    """ダウンロード先ディレクトリを返す（ローカル環境のみ使用）."""
    runtime = detect_runtime()
    if runtime == "local":
        return _LOCAL_DEFAULT_DOWNLOAD_DIR
    # Colab環境ではメモリ上で処理するため、一時ディレクトリを返す
    import tempfile
    return tempfile.mkdtemp()


def log_runtime_info() -> None:
    """実行環境情報をログ出力する."""
    runtime = detect_runtime()
    labels = {
        "colab_personal": "Google Colab (個人)",
        "colab_enterprise": "Google Cloud Enterprise Colab",
        "local": "ローカルPC",
    }
    print(f"実行環境: {labels[runtime]}")
    print(f"GCP Project: {PROJECT_ID}")
    print(f"BQ Dataset: {DATASET_ID}")
    print(f"GCS Bucket: {BUCKET_NAME}")
