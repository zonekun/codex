# -*- coding: utf-8 -*-
"""TDnet カテゴリ定義の乖離検知スクリプト.

Claude Code PostToolUse hook から呼ばれ、以下3箇所のカテゴリ定義を比較する:
  1. 006 MD（統合正本）のカテゴリテーブル
  2. tdnet_download.py の classify() が返すカテゴリ名
  3. tdnet_load_parallel.py の VALID_CATEGORIES リスト

差分があれば stderr に警告を出力する。
常に exit 0（ワークフローをブロックしない）。

hook から呼ばれる場合は stdin に JSON が渡される。tool_input.file_path を
チェックし、対象ファイル以外への変更では即座にスキップする。

Usage:
    PYTHONUTF8=1 uv run python scripts/check_category_drift.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================
# 対象ファイルのパス定義
# ============================================================

MD_006_PATH = PROJECT_ROOT / "docs" / "knowledges" / "tools" / "006_tdnet_category_classification.md"
DOWNLOAD_PATH = PROJECT_ROOT / "scripts" / "tdnet_download.py"
LOAD_PARALLEL_PATH = PROJECT_ROOT / "scripts" / "tdnet_load_parallel.py"

# hook 起動判定: これらのファイル名を含む変更のみチェックを実行する
_TARGET_FILENAMES: set[str] = {
    "tdnet_download.py",
    "tdnet_load_parallel.py",
    "tdnet_load_recovery.py",
    "gemma_tpu_worker.py",
    "006_tdnet_category_classification.md",
}


def _should_run_from_stdin() -> bool:
    """stdin の hook JSON から編集対象ファイルを読み取り、チェック対象か判定する.

    stdin が空（直接実行）の場合は常に True を返す。
    """
    if sys.stdin.isatty():
        return True

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return True
        data = json.loads(raw)
        file_path = data.get("tool_input", {}).get("file_path", "")
        if not file_path:
            return True
        basename = Path(file_path).name
        return basename in _TARGET_FILENAMES
    except (json.JSONDecodeError, AttributeError, TypeError):
        return True


def parse_md_categories(path: Path) -> set[str]:
    """006 MD のカテゴリテーブルからカテゴリ名を抽出する.

    テーブル行のパターン: | # | P | カテゴリ | ... |
    ヘッダ行・セパレータ行はスキップする。

    Returns:
        カテゴリ名の集合
    """
    if not path.exists():
        return set()

    text = path.read_text(encoding="utf-8")
    categories: set[str] = set()

    # カテゴリ一覧テーブルの行を抽出
    # パターン: | 数字or- | S/A/B/C | カテゴリ名 | ... |
    pattern = re.compile(
        r"^\|\s*(?:\d+b?|-)\s*\|\s*[SABC]\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        cat = m.group(1).strip()
        if cat and cat != "カテゴリ":
            categories.add(cat)

    return categories


def parse_classify_return_values(path: Path) -> set[str]:
    """tdnet_download.py の classify() 関数から return されるカテゴリ名を抽出する.

    return ("カテゴリ名", "優先度") のパターンをすべて収集する。

    Returns:
        カテゴリ名の集合
    """
    if not path.exists():
        return set()

    text = path.read_text(encoding="utf-8")
    categories: set[str] = set()

    # classify 関数内の return ("...", "...") を抽出
    pattern = re.compile(r'return\s*\(\s*"([^"]+)"\s*,\s*"[SABC]"\s*\)')
    for m in pattern.finditer(text):
        categories.add(m.group(1))

    return categories


def parse_valid_categories(path: Path) -> set[str]:
    """tdnet_load_parallel.py の VALID_CATEGORIES リストから値を抽出する.

    AST パースでリテラルを安全に取得する。フォールバックとして正規表現も使う。

    Returns:
        カテゴリ名の集合
    """
    if not path.exists():
        return set()

    text = path.read_text(encoding="utf-8")

    # VALID_CATEGORIES: list[str] = [...] のブロックを抽出
    pattern = re.compile(
        r"^VALID_CATEGORIES\s*(?::\s*list\[str\]\s*)?=\s*\[",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return set()

    # "= [" の最後の "[" の位置を見つける（list[str] の "[" ではなく代入値の "[" ）
    # match.end() は "[" の直後を指すので -1 する
    bracket_start = match.end() - 1
    depth = 0
    end = bracket_start
    for i in range(bracket_start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    list_text = text[bracket_start:end]

    # ast.literal_eval で安全にパース
    try:
        values = ast.literal_eval(list_text)
        if isinstance(values, list):
            return set(values)
    except (ValueError, SyntaxError):
        pass

    # フォールバック: 正規表現で文字列リテラルを抽出
    categories: set[str] = set()
    for m in re.finditer(r'"([^"]+)"', list_text):
        categories.add(m.group(1))
    return categories


def main() -> None:
    """3箇所のカテゴリ定義を比較し、差分があれば stderr に警告を出力する."""
    md_cats = parse_md_categories(MD_006_PATH)
    classify_cats = parse_classify_return_values(DOWNLOAD_PATH)
    valid_cats = parse_valid_categories(LOAD_PARALLEL_PATH)

    # いずれかが空の場合はパースエラーの可能性があるのでスキップ
    if not md_cats:
        print("check_category_drift: 006 MD のパースに失敗（0件）。スキップ", file=sys.stderr)
        return
    if not classify_cats:
        print("check_category_drift: classify() のパースに失敗（0件）。スキップ", file=sys.stderr)
        return
    if not valid_cats:
        print("check_category_drift: VALID_CATEGORIES のパースに失敗（0件）。スキップ", file=sys.stderr)
        return

    warnings: list[str] = []

    # --- 006 MD vs classify() ---
    md_only_vs_classify = md_cats - classify_cats
    classify_only_vs_md = classify_cats - md_cats
    if md_only_vs_classify:
        warnings.append(f"  006 MD にあって classify() にない: {sorted(md_only_vs_classify)}")
    if classify_only_vs_md:
        warnings.append(f"  classify() にあって 006 MD にない: {sorted(classify_only_vs_md)}")

    # --- 006 MD vs VALID_CATEGORIES ---
    # VALID_CATEGORIES は AI 判定用サブセットなので、006 MD の DL 専用カテゴリ
    # （C 優先度など）は含まれないのが正常。逆方向のみチェック。
    valid_only_vs_md = valid_cats - md_cats
    if valid_only_vs_md:
        warnings.append(f"  VALID_CATEGORIES にあって 006 MD にない: {sorted(valid_only_vs_md)}")

    # --- classify() vs VALID_CATEGORIES ---
    # VALID_CATEGORIES は classify() のサブセット（DL専用カテゴリは含まない）。
    # VALID_CATEGORIES に存在するが classify() にないものは異常。
    valid_only_vs_classify = valid_cats - classify_cats
    if valid_only_vs_classify:
        warnings.append(
            f"  VALID_CATEGORIES にあって classify() にない: {sorted(valid_only_vs_classify)}"
        )

    if warnings:
        print(
            "\n".join(
                [
                    "",
                    "=== TDnet カテゴリ同期チェック: 差分検出 ===",
                    *warnings,
                    f"  006 MD: {len(md_cats)}件, classify(): {len(classify_cats)}件,"
                    f" VALID_CATEGORIES: {len(valid_cats)}件",
                    "  -> 006 MD の同期ルールに従い全箇所を更新してください",
                    "",
                ]
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    try:
        if not _should_run_from_stdin():
            sys.exit(0)
        main()
    except Exception as e:
        # hook はワークフローをブロックしない
        print(f"check_category_drift: 予期しないエラー: {e}", file=sys.stderr)
    sys.exit(0)
