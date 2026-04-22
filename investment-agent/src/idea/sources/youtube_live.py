"""YouTubeライブ配信チャット取得ソース.

ライブ配信中の視聴者コメントからリアルタイムの投資アイディアを抽出する。
市場が動いている時間帯のライブチャットはSNSと同等の情報価値がある。
"""
from __future__ import annotations

from src.core.logger import get_logger
from src.idea.models import InvestmentIdea
from src.idea.sources.base import BaseSource

log = get_logger(__name__)


class YouTubeLiveSource(BaseSource):
    """YouTubeライブ配信チャットからアイディアを抽出する."""

    async def fetch_new_content(self) -> list[dict]:
        """ライブチャットリプレイを取得する.

        YouTube Data API または yt-dlp を使用。
        字幕（配信者の発言）とチャット（視聴者コメント）の両方を取得。
        """
        log.info("youtube_live_fetch_start")
        # TODO: 実装
        # 1. youtube-transcript-api で自動字幕取得（タイムスタンプ付き）
        # 2. yt-dlp でライブチャットリプレイ取得
        # 3. テキストファイルとして data/lake/youtube/ に保存
        return []

    async def extract_ideas(self, content: list[dict]) -> list[InvestmentIdea]:
        """チャット内容からLLMで投資アイディアを抽出する.

        抽出時に根拠箇所のタイムスタンプ・コメント位置を記録する。
        """
        log.info("youtube_live_extract_start", num_items=len(content))
        # TODO: 実装
        # 1. コンテンツをLLM（Claude）に渡す
        # 2. 投資アイディアが含まれていれば構造化して返す
        # 3. 根拠箇所（例: "32:27 - 逆日歩狙いの買方投資"）を記録
        return []
