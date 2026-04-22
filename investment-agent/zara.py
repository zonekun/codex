#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ザラ場ツール一発起動ラッパー.

サブメニューを表示し、番号選択で実行する。
"""

import sys

from scripts.zaraba_earnings import cmd_prepare, cmd_watch, cmd_catchup, resolve_date

MENU = """\
=== ザラ場ツール ===
1. 事前準備     — BQ から決算銘柄・事前情報をキャッシュ
2. ザラバ監視   — リアルタイム TDnet 監視 & スコアリング
3. キャッチアップ — 指定時刻までの開示を一括取得
q. 終了
"""


def _ask_date() -> str:
    """日付を対話入力で取得する（t/p/n/YYYYMMDD）."""
    today = resolve_date("t")
    raw = input(f"日付 [t=今日({today}), p=前取引日, n=次取引日, YYYYMMDD]: ").strip()
    if not raw:
        raw = "t"
    return resolve_date(raw)


def main() -> None:
    print(MENU)
    choice = input("番号を選択: ").strip()

    if choice == "1":
        target = _ask_date()
        force = input("キャッシュを無視して再取得? (y/N): ").strip().lower() == "y"
        cmd_prepare(target, force=force)
    elif choice == "2":
        target = _ask_date()
        cmd_watch(target)
    elif choice == "3":
        target = _ask_date()
        until = input("何時までの開示を取得? (HH:MM, 例: 15:30): ").strip()
        cmd_catchup(target, until_time=until)
    elif choice in ("q", "Q"):
        return
    else:
        print("無効な選択")


if __name__ == "__main__":
    main()
