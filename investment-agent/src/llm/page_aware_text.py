"""ページ境界マーカー `[PAGE N]` を含むテキストを扱うユーティリティ.

`scripts/tdnet_load_parallel.py` および `scripts/tdnet_load_recovery.py` で
抽出される新フォーマットのテキスト（`[PAGE 1]\\n...\\n\\n[PAGE 2]\\n...`）を
解析するためのヘルパー関数群を提供する。

既存の BQ `STOCK.TDNET_DOCUMENTS_ENHANCED.CHUNK_TEXT` には旧フォーマット
（ページマーカー無しの単一スペース連結）と新フォーマットが混在する可能性が
あるため、`has_page_markers()` で事前判定してから `split_by_pages()` を
呼ぶこと。
"""

from __future__ import annotations

import re

__all__ = [
    "PAGE_MARKER_PATTERN",
    "has_page_markers",
    "split_by_pages",
    "page_count",
    "digit_ratio",
    "digit_ratio_per_page",
    "reconstruct_from_chunks",
    "is_legacy_format",
]


# `[PAGE 1]`, `[PAGE 23]` のようなマーカー。
# 行頭要件は付けない（チャンク境界で行頭にならないケースに対応するため）。
PAGE_MARKER_PATTERN = re.compile(r"\[PAGE (\d+)\]")


def has_page_markers(text: str) -> bool:
    """テキストにページ境界マーカー `[PAGE N]` が含まれるか判定する.

    Args:
        text: 判定対象のテキスト。

    Returns:
        少なくとも 1 つの `[PAGE N]` マーカーがあれば True。
    """
    if not text:
        return False
    return bool(PAGE_MARKER_PATTERN.search(text))


def split_by_pages(text: str) -> list[tuple[int, str]]:
    """ページ境界マーカーでテキストを分割する.

    マーカーが無い場合は全文を `(1, text)` として返す（後方互換）。

    Args:
        text: 抽出済みテキスト。

    Returns:
        `[(page_num, page_text), ...]` のリスト。`page_text` はマーカー行を
        含まない本文のみ。連続するマーカーで間が空の場合でも空ページは含める。
    """
    if not text:
        return []
    if not has_page_markers(text):
        return [(1, text.strip())]

    # 出現順にマーカー位置を取得し、各区間を本文として切り出す。
    matches = list(PAGE_MARKER_PATTERN.finditer(text))
    result: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        result.append((page_num, body))
    return result


def page_count(text: str) -> int:
    """テキスト内のユニークなページ番号数を返す.

    マーカー無しの場合は 1、空文字の場合は 0 を返す。
    """
    if not text:
        return 0
    pages = split_by_pages(text)
    if not pages:
        return 0
    return len({p for p, _ in pages})


def digit_ratio(text: str) -> float:
    """テキスト中の数字（0-9）比率を返す（0.0 - 1.0）.

    全角数字は含めない（必要なら呼び出し側で `unicodedata.normalize("NFKC", ...)` 済を渡す）。
    空文字の場合は 0.0 を返す。
    """
    if not text:
        return 0.0
    total = len(text)
    digits = sum(1 for ch in text if ch.isascii() and ch.isdigit())
    return digits / total if total else 0.0


def digit_ratio_per_page(text: str) -> list[tuple[int, float]]:
    """ページごとの数字比率を返す.

    Args:
        text: ページ境界マーカー付きの抽出テキスト。

    Returns:
        `[(page_num, ratio), ...]` のリスト。マーカー無しの場合は
        `[(1, ratio_of_whole_text)]` を返す。
    """
    return [(page_num, digit_ratio(body)) for page_num, body in split_by_pages(text)]


def reconstruct_from_chunks(chunks: list[str]) -> str:
    """CHUNK_TEXT のリストを連結してフルテキスト復元.

    新フォーマット（`[PAGE N]` マーカー有）はそのまま連結（マーカーで
    境界が保たれるため）。旧フォーマット（マーカー無し）は従来どおり
    空白連結する。

    Args:
        chunks: `CHUNK_TEXT` の文字列リスト（チャンク順に並んでいること）。

    Returns:
        連結された全文テキスト。`chunks` が空なら空文字列を返す。
    """
    if not chunks:
        return ""
    # 新旧フォーマット判定は先頭チャンクの中身で行う。
    # 新フォーマットは `[PAGE N]` マーカーがチャンク中に含まれるため
    # そのまま連結すればページ境界が保たれる。
    # 旧フォーマットはマーカーが無いので空白連結（従来挙動）に合わせる。
    has_markers = any(has_page_markers(c) for c in chunks if c)
    if has_markers:
        return "".join(chunks)
    return " ".join(c for c in chunks if c)


def is_legacy_format(text: str) -> bool:
    """旧フォーマット（ページマーカー無し）なら True.

    Args:
        text: 判定対象のテキスト。

    Returns:
        `[PAGE N]` マーカーが含まれない場合 True。空文字も True。
    """
    return not has_page_markers(text)
