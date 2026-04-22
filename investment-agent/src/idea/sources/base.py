"""ソース基底クラス."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.idea.models import InvestmentIdea


class BaseSource(ABC):
    """外部ソースの基底クラス."""

    @abstractmethod
    async def fetch_new_content(self) -> list[dict]:
        """新しいコンテンツを取得する."""
        ...

    @abstractmethod
    async def extract_ideas(self, content: list[dict]) -> list[InvestmentIdea]:
        """コンテンツから投資アイディアを抽出する."""
        ...
