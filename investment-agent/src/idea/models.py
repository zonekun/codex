"""投資アイディア データモデル."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IdeaStatus(str, Enum):
    """アイディアのステータス."""

    NEW = "NEW"
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    ANALYZED_PASS = "ANALYZED_PASS"
    ANALYZED_FAIL = "ANALYZED_FAIL"
    BACKTESTING = "BACKTESTING"
    BACKTEST_PASS = "BACKTEST_PASS"
    BACKTEST_FAIL = "BACKTEST_FAIL"
    CANDIDATE = "CANDIDATE"
    TRADER = "TRADER"
    RETIRED = "RETIRED"


class IdeaSource(str, Enum):
    """アイディアのソース種別."""

    TWITTER = "twitter"
    YOUTUBE = "youtube"
    YOUTUBE_LIVE = "youtube_live"
    ARXIV = "arxiv"
    MANUAL = "manual"
    BOOK = "book"


class InvestmentIdea(BaseModel):
    """投資アイディア."""

    id: str = Field(description="一意のアイディアID")
    title: str = Field(description="アイディアのタイトル")
    description: str = Field(description="アイディアの詳細説明")
    hypothesis: str = Field(description="検証すべき仮説")
    source: IdeaSource = Field(description="ソース種別")
    source_url: str = Field(default="", description="ソースURL")
    source_timestamp: str = Field(default="", description="ソースのタイムスタンプ")
    status: IdeaStatus = Field(default=IdeaStatus.NEW)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    # 分析結果
    p_value: float | None = Field(default=None, description="統計検定のp値")
    effect_size: float | None = Field(default=None, description="効果量")

    # バックテスト結果
    sharpe_ratio: float | None = None
    win_rate: float | None = None
    max_drawdown: float | None = None
    total_return: float | None = None
