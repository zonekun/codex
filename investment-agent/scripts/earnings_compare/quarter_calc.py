"""前Q算出ロジック.

最新決算日から「前の四半期」の開示検索期間を算出する。
日本企業の決算開示スケジュール:
  Q4(本決算) → Q1 → Q2 → Q3 の順に約3ヶ月間隔で開示。
  Q1の場合、前Qは前会計年度Q4（約3ヶ月前に開示）。

使い方:
    from scripts.earnings_compare.quarter_calc import get_quarter_date_ranges
    latest_range, prev_range = get_quarter_date_ranges(date(2025, 11, 14))
"""

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class QuarterDateRange:
    """四半期の検索期間."""

    date_from: date
    date_to: date

    def __str__(self) -> str:
        return f"{self.date_from} ~ {self.date_to}"


def get_quarter_date_ranges(
    latest_date: date,
    latest_window_days: int = 30,
    prev_offset_days: int = 90,
    prev_window_days: int = 45,
) -> tuple[QuarterDateRange, QuarterDateRange]:
    """最新決算日から最新Q・前Qの開示検索期間を算出する.

    Args:
        latest_date: 最新決算の開示日（環境変数 LATEST_DATE から取得）。
        latest_window_days: 最新Q の検索ウィンドウ（±日数）。
        prev_offset_days: 前Q の中心を latest_date から何日前に置くか。
            Q1 → 前会計年度Q4 は約90日前（3ヶ月）。
        prev_window_days: 前Q の検索ウィンドウ（±日数）。

    Returns:
        (latest_range, prev_range): 各四半期の検索期間。
    """
    latest_range = QuarterDateRange(
        date_from=latest_date - timedelta(days=latest_window_days),
        date_to=latest_date + timedelta(days=latest_window_days),
    )

    prev_center = latest_date - timedelta(days=prev_offset_days)
    prev_range = QuarterDateRange(
        date_from=prev_center - timedelta(days=prev_window_days),
        date_to=prev_center + timedelta(days=prev_window_days),
    )

    return latest_range, prev_range
