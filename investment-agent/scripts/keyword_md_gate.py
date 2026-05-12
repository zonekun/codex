#!/usr/bin/env python3
"""索引ファースト強制リマインダ フック.

UserPromptSubmit hook として動作。
全プロンプトに対して1行リマインダを出力し、
CLAUDE.md §高頻度参照テーブルの照合を構造的に強制する。

起点: MR-066（意志依存型ルール違反）
"""

import json
import sys

REMINDER = "[索引ファースト] ファイル探索・データ確認・回答検討する際は最初にCLAUDE.md §高頻度参照テーブルを照合せよ"


def main() -> None:
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    print(REMINDER, end="")
    sys.exit(0)


if __name__ == "__main__":
    main()
