"""テスト共通設定."""
from __future__ import annotations

import pytest


@pytest.fixture
def sample_idea():
    """テスト用の投資アイディア."""
    from src.idea.models import IdeaSource, IdeaStatus, InvestmentIdea

    return InvestmentIdea(
        id="test_001",
        title="逆日歩狙いの買方投資",
        description="逆日歩が発生している銘柄の買方ポジションを取ることで逆日歩収益を狙う",
        hypothesis="逆日歩発生銘柄の買方は、逆日歩分だけ超過リターンを得られる",
        source=IdeaSource.YOUTUBE,
        source_url="https://www.youtube.com/watch?v=example",
        status=IdeaStatus.NEW,
        tags=["逆日歩", "買方投資", "信用取引"],
    )
