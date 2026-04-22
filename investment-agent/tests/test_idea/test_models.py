"""アイディアモデルのテスト."""
from __future__ import annotations

from src.idea.models import IdeaSource, IdeaStatus, InvestmentIdea


def test_create_idea(sample_idea: InvestmentIdea) -> None:
    """アイディアを作成できること."""
    assert sample_idea.id == "test_001"
    assert sample_idea.status == IdeaStatus.NEW
    assert sample_idea.source == IdeaSource.YOUTUBE


def test_idea_status_transition() -> None:
    """ステータス遷移が正しく定義されていること."""
    statuses = list(IdeaStatus)
    assert IdeaStatus.NEW in statuses
    assert IdeaStatus.TRADER in statuses
    assert IdeaStatus.RETIRED in statuses
