"""`_text_quality_ok` の単体テスト (P0-1, 062 MD §3 準拠).

scripts/extract_monthly_data.py の `_text_quality_ok` に対して、
正常日本語 / 文字化け / 短すぎ / ASCII-only / 数字記号のみ / 境界条件
を検証する。

References:
    docs/knowledges/tools/062_pdf_processing_strategy.md §3 (L59-L61)
    docs/plans/20260421_093823_monthly_pdf_strategy_integration.md P0-1
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_extract_module():
    """scripts/extract_monthly_data.py を importlib で読み込む（パッケージ外のため）."""
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "extract_monthly_data.py"
    spec = importlib.util.spec_from_file_location("_emd_for_test", module_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_emd_for_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def emd():
    return _load_extract_module()


class TestTextQualityOk:
    """`_text_quality_ok(text, min_len=50, garbled_threshold=0.20)` の仕様確認."""

    def test_normal_japanese_passes(self, emd) -> None:
        """正常な月次 IR テキストは True."""
        text = (
            "2024年3月度 月次売上速報\n"
            "売上高 1,234 百万円 前年同月比 +5.3%\n"
            "既存店売上高 前年同月比 +2.1%\n"
            "客数 前年同月比 +1.2% 客単価 +0.9%\n"
        )
        assert emd._text_quality_ok(text) is True

    def test_garbled_fails(self, emd) -> None:
        """文字化け（制御文字 50% 超）は False."""
        # 長さ十分 (>50) かつ非正常文字を 50% 以上混入
        garbled = "\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f" * 2
        text = "売上高 1,234 百万円 " + garbled
        assert len(text) > 50
        assert emd._text_quality_ok(text) is False

    def test_too_short_fails(self, emd) -> None:
        """min_len 未満は質によらず False."""
        text = "売上高 1,234 百万円"  # 明らかに 50 字未満
        assert len(text) < 50
        assert emd._text_quality_ok(text) is False

    def test_ascii_only_passes(self, emd) -> None:
        """英字のみの業績速報も正常文字に含むため True."""
        text = (
            "Monthly Sales Report for March 2024. "
            "Net sales 1,234 million yen, up 5.3% year-on-year. "
            "Same-store sales up 2.1%."
        )
        assert len(text) >= 50
        assert emd._text_quality_ok(text) is True

    def test_digits_symbols_only_passes(self, emd) -> None:
        """数字記号のみ（正常文字クラス内）は長さ十分なら True."""
        text = "1,234,567 +5.3% -1.2% 100.0 (1,000) [2024/03/31] " * 2
        assert len(text) >= 50
        assert emd._text_quality_ok(text) is True

    def test_empty_string_fails(self, emd) -> None:
        """空文字は False."""
        assert emd._text_quality_ok("") is False

    def test_none_safe_fails(self, emd) -> None:
        """None 入力でも例外を投げず False を返す."""
        assert emd._text_quality_ok(None) is False  # type: ignore[arg-type]

    def test_threshold_boundary_just_ok(self, emd) -> None:
        """文字化け率が閾値未満なら True（境界条件）.

        101 文字中 19 文字が非正常（19/101 ≈ 18.8% < 20%）なら True。
        """
        normal = "売上" * 41  # 82 文字
        abnormal = "\x01" * 19  # 19 文字
        text = normal + abnormal
        assert len(text) == 101
        # ratio_abnormal = 19/101 ≈ 0.188 < 0.20 → True
        assert emd._text_quality_ok(text) is True

    def test_threshold_boundary_just_fail(self, emd) -> None:
        """文字化け率が閾値超過なら False（境界条件）.

        100 文字中 30 文字が非正常（30/100 = 30% > 20%）なら False。
        （20/100 ちょうどは浮動小数点精度で揺れるため、安全側の 30% を使う。）
        """
        normal = "売上" * 35  # 70 文字
        abnormal = "\x01" * 30  # 30 文字
        text = normal + abnormal
        assert len(text) == 100
        # ratio_abnormal = 30/100 = 0.30 → False
        assert emd._text_quality_ok(text) is False

    def test_custom_threshold(self, emd) -> None:
        """garbled_threshold を大きく取れば同じ文字列でも PASS."""
        normal = "売上" * 40  # 80 文字
        abnormal = "\x01" * 20  # 20 文字
        text = normal + abnormal
        assert emd._text_quality_ok(text, garbled_threshold=0.25) is True

    def test_custom_min_len(self, emd) -> None:
        """min_len を下げれば短い文字列でも質が OK なら True."""
        text = "売上高 1,234"  # 10 文字程度
        assert emd._text_quality_ok(text, min_len=5) is True
