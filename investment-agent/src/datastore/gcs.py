"""Google Cloud Storage アクセス層.

大量の非構造化・半構造化データを格納。
例: EDINET適時開示、e-STAT統計データ、日証金データ等。
収集モジュールは別途実行される前提。エージェントは参照のみ。

注意: 生文書をそのままLLMに渡すとクレジット費消が大きいため、
ベクトルDB経由での検索も検討中。
"""
from __future__ import annotations

from pathlib import Path

from src.core.config import app_config
from src.core.logger import get_logger

log = get_logger(__name__)


class GCSClient:
    """GCS読み取りクライアント."""

    def __init__(self) -> None:
        cfg = app_config.get("datastore", {})
        self.bucket_name = cfg.get("gcs_bucket", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import storage
            self._client = storage.Client()
        return self._client

    def list_blobs(self, prefix: str) -> list[str]:
        """指定プレフィックス配下のオブジェクト一覧を返す."""
        client = self._get_client()
        bucket = client.bucket(self.bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)
        return [b.name for b in blobs]

    def download_as_text(self, blob_path: str) -> str:
        """テキストファイルをダウンロードして文字列で返す."""
        log.info("gcs_download", path=blob_path)
        client = self._get_client()
        bucket = client.bucket(self.bucket_name)
        blob = bucket.blob(blob_path)
        return blob.download_as_text(encoding="utf-8")

    def download_to_local(self, blob_path: str, local_path: str | Path) -> Path:
        """ファイルをローカルにダウンロードする."""
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        client = self._get_client()
        bucket = client.bucket(self.bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(str(local_path))
        log.info("gcs_downloaded_to_local", blob=blob_path, local=str(local_path))
        return local_path
