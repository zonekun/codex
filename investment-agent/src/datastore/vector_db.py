"""ベクトルDB（将来拡張用）.

GCS上の文書（EDINET適時開示等）をそのままLLMに渡すと
クレジット費消が大きいため、ベクトルDBで関連箇所のみを検索して渡す。

候補: Chroma, Qdrant, Pinecone等
"""
from __future__ import annotations

from src.core.logger import get_logger

log = get_logger(__name__)


class VectorDBClient:
    """ベクトルDB検索クライアント（将来実装）."""

    def __init__(self) -> None:
        log.info("vector_db_init", status="not_implemented")

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """クエリに関連する文書チャンクを検索する."""
        # TODO: ベクトルDB選定後に実装
        raise NotImplementedError("ベクトルDBは未実装です。GCS文書を直接参照してください。")
