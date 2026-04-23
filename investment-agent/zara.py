#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ザラ場ツール一発起動ラッパー.

サブメニューを表示し、番号選択で実行する。
"""

from scripts.zaraba_earnings import (
    PREPARE_DATA_CONSENSUS,
    PREPARE_DATA_FULL,
    PREPARE_TARGET_ALL,
    PREPARE_TARGET_SCHEDULED,
    cmd_catchup,
    cmd_prepare,
    cmd_watch,
    resolve_date,
)

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


def _ask_prepare_target() -> str:
    """prepare の対象範囲を取得する."""
    print("対象 [1=決算予定銘柄(既定) / 2=全銘柄]")
    raw = input("対象を選択: ").strip()
    if raw in ("", "1"):
        return PREPARE_TARGET_SCHEDULED
    if raw == "2":
        return PREPARE_TARGET_ALL
    print("無効な選択のため、決算予定銘柄を使用します")
    return PREPARE_TARGET_SCHEDULED


def _ask_prepare_data() -> str:
    """prepare のデータ種別を取得する."""
    print("データ種別 [1=全データ(既定) / 2=コンセのみ]")
    raw = input("データ種別を選択: ").strip()
    if raw in ("", "1"):
        return PREPARE_DATA_FULL
    if raw == "2":
        return PREPARE_DATA_CONSENSUS
    print("無効な選択のため、全データを使用します")
    return PREPARE_DATA_FULL


def main() -> None:
    print(MENU)
    choice = input("番号を選択: ").strip()

    if choice == "1":
        target = _ask_date()
        prepare_target = _ask_prepare_target()
        prepare_data = _ask_prepare_data()
        force = input("キャッシュを無視して再取得? (y/N): ").strip().lower() == "y"
        cmd_prepare(target, force=force, target=prepare_target, data=prepare_data)
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
