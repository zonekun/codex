"""「レビュー」入力検知 → Agent起動リマインダ.

UserPromptSubmit hook。ユーザー入力に「レビュー」を含む場合のみ
エージェント起動を促すリマインダを出力する。

起点: MR-178（インラインレビュー事故）
"""
from __future__ import annotations

import json
import sys

REMINDER = (
    "[レビュー検出] /code-reviewer または /md-reviewer を"
    " Agent ツールで起動せよ。インラインレビュー禁止（§8）"
)


def main() -> None:
    """ユーザー入力に「レビュー」が含まれる場合リマインダを出力する。"""
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt", "")
    except Exception:
        return
    if "レビュー" in prompt:
        print(REMINDER, end="")
    sys.exit(0)


if __name__ == "__main__":
    main()
