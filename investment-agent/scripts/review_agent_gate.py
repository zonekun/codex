"""「レビュー」入力検知 → Agent起動リマインダ.

UserPromptSubmit hook。ユーザー入力に「レビュー」を含む場合のみ
エージェント起動を促すリマインダを出力する。

起点: MR-178（インラインレビュー事故）
"""
from __future__ import annotations

import json
import sys

REMINDER = (
    "[レビュー検出] ①docs/reviews/ にレビューMDを作成（097ガイド §1-1〜§1-2）"
    " → ②Agent ツールで /code-reviewer or /md-reviewer 起動（§1-3）。"
    " ①②はアトミック（中断禁止）。インラインレビュー禁止（§8）"
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
