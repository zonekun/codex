"""Qwen2.5 7B モデルを GCS にアップロードする初回セットアップスクリプト.

このスクリプトは一度だけ実行する。
Linux 環境（GCP VM または Cloud Shell）で実行することを推奨。

前提:
    - Ollama がインストール済み（https://ollama.ai/download）
    - gcloud CLI が認証済み
    - GCS バケット stock_data_1930932 への書き込み権限

実行方法:
    PYTHONUTF8=1 python scripts/setup_qwen_gcs.py

アップロード先:
    gs://stock_data_1930932/test/ollama-models/
    → Cloud Run Job が /root/.ollama に FUSE マウント

Ollama ディレクトリ構造（マウント後）:
    /root/.ollama/
    └── models/
        ├── manifests/registry.ollama.ai/library/qwen2.5/7b
        └── blobs/sha256-xxxxxxx...
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import requests
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────
MODEL_NAME = "qwen2.5:7b"
GCS_DEST = "gs://stock_data_1930932/test/ollama-models"
OLLAMA_HOST = "http://localhost:11434"
STARTUP_WAIT_SEC = 30


def get_default_ollama_models_dir() -> Path:
    """OS に応じた Ollama モデルディレクトリを返す.

    Returns:
        Ollama のデフォルトモデルディレクトリ。
    """
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("USERPROFILE", "C:\\Users\\User")) / ".ollama"
    elif system == "Darwin":
        return Path.home() / ".ollama"
    else:  # Linux
        return Path("/root/.ollama") if os.getuid() == 0 else Path.home() / ".ollama"


def check_ollama_installed() -> bool:
    """Ollama がインストール済みか確認する."""
    try:
        subprocess.run(["ollama", "--version"],
                       capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def start_ollama_server() -> subprocess.Popen:
    """Ollama サーバーをサブプロセスで起動する."""
    logger.info("ollama_starting")
    return subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_ollama(timeout_sec: int = STARTUP_WAIT_SEC) -> bool:
    """Ollama サーバーの起動を待機する."""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def pull_model(model: str = MODEL_NAME) -> None:
    """Ollama で指定モデルをダウンロードする.

    Args:
        model: モデル名（例: qwen2.5:7b）。
    """
    logger.info("model_pull_start", model=model,
                message="約4.7GB のダウンロードが開始されます。数分〜数十分かかります")
    subprocess.run(["ollama", "pull", model], check=True)
    logger.info("model_pull_done", model=model)


def upload_to_gcs(models_dir: Path, gcs_dest: str) -> None:
    """モデルファイルを GCS にアップロードする.

    Args:
        models_dir: Ollama のルートディレクトリ（~/.ollama）。
        gcs_dest: GCS のアップロード先（gs://...）。
    """
    src = str(models_dir) + os.sep
    logger.info("gcs_upload_start", src=src, dest=gcs_dest,
                message="モデルファイルを GCS にアップロードします")

    # gcloud storage rsync を使用（gsutil は Windows で権限エラーになる）
    cmd = [
        "gcloud", "storage", "rsync", "-r",
        src,
        gcs_dest + "/",
    ]
    subprocess.run(cmd, check=True)
    logger.info("gcs_upload_done", dest=gcs_dest)


def main() -> None:
    """セットアップのメイン処理."""
    logger.info("setup_start", model=MODEL_NAME, gcs_dest=GCS_DEST)

    # Ollama インストール確認
    if not check_ollama_installed():
        logger.error("ollama_not_installed",
                     message="Ollama をインストールしてください: https://ollama.ai/download")
        sys.exit(1)

    # モデルディレクトリ確認
    ollama_dir = get_default_ollama_models_dir()
    logger.info("ollama_dir", path=str(ollama_dir))

    # Ollama サーバー起動
    proc = start_ollama_server()
    try:
        if not wait_for_ollama():
            logger.error("ollama_startup_failed")
            sys.exit(1)
        logger.info("ollama_ready")

        # モデルダウンロード
        pull_model(MODEL_NAME)

    finally:
        proc.terminate()
        time.sleep(2)

    # GCS アップロード
    upload_to_gcs(ollama_dir, GCS_DEST)

    logger.info("setup_complete",
                message=f"モデルを {GCS_DEST} にアップロードしました。"
                        "Cloud Run Job 作成時に GCS FUSE マウントを設定してください。")


if __name__ == "__main__":
    main()
