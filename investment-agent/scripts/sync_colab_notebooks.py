# -*- coding: utf-8 -*-
"""Colab Notebooks 同期スクリプト.

PostToolUse hook で ipynb 編集時に自動実行される。
- Windows: ローカルフォルダ（C:\\gdrive\\Colab Notebooks）へコピー
- Linux: Google Drive API でアップロード（サービスアカウント認証）

対象ノートブックを追加する場合は COLAB_SYNC_MAP に追記する。
"""

import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ソース（プロジェクト相対） → Colab Notebooks 内のファイル名
COLAB_SYNC_MAP: dict[str, str] = {
    "scripts/earnings_model/earnings_model_eda.ipynb": "earnings_model_eda.ipynb",
    "scripts/earnings_model/earnings_model_predict.ipynb": "earnings_model_predict.ipynb",
    "scripts/factor_model/factor_model_residual_corr.ipynb": "factor_model_residual_corr.ipynb",
}

# Google Drive フォルダ ID（Colab Notebooks）
GDRIVE_FOLDER_ID = "1yR1gBOgLVO8VCkVNai1Nq_qN8-2ILtYR"


def sync_windows() -> None:
    """Windows: ローカル Google Drive フォルダへコピー."""
    dest_dir = Path(r"C:\gdrive\Colab Notebooks")
    if not dest_dir.exists():
        return

    for src_rel, dest_name in COLAB_SYNC_MAP.items():
        src = PROJECT_ROOT / src_rel
        if src.exists():
            shutil.copy2(src, dest_dir / dest_name)


def sync_linux() -> None:
    """Linux: Google Drive API でアップロード."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    sa_path = PROJECT_ROOT / "keys" / "gcp-service-account.json"
    if not sa_path.exists():
        return

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    service = build("drive", "v3", credentials=creds)

    # フォルダ内の既存ファイル一覧を取得（更新 or 新規作成の判定用）
    existing = {}
    results = service.files().list(
        q=f"'{GDRIVE_FOLDER_ID}' in parents and trashed=false",
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    for f in results.get("files", []):
        existing[f["name"]] = f["id"]

    for src_rel, dest_name in COLAB_SYNC_MAP.items():
        src = PROJECT_ROOT / src_rel
        if not src.exists():
            continue

        media = MediaFileUpload(str(src), mimetype="application/octet-stream")

        if dest_name in existing:
            # 既存ファイルを更新
            service.files().update(
                fileId=existing[dest_name],
                media_body=media,
                supportsAllDrives=True,
            ).execute()
        else:
            # 新規作成
            metadata = {
                "name": dest_name,
                "parents": [GDRIVE_FOLDER_ID],
            }
            service.files().create(
                body=metadata,
                media_body=media,
                supportsAllDrives=True,
            ).execute()


if __name__ == "__main__":
    if sys.platform == "win32":
        sync_windows()
    else:
        sync_linux()
