"""
BC KPI ダウンロード進捗監視 + 異常時LINE通知。

監視対象: data/logs/bc_kpi_download_run.log
通知条件:
  - WAF タイムアウト / WAF 検知
  - 連続 5社 で empty or error
  - ログ更新が5分以上停止
  - スクリプト完了 / 中断
通知方法: scripts/notify.py の send_ntfy

Usage:
  PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/monitor_bc_download.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import send_ntfy

LOG_PATH       = Path("data/logs/bc_kpi_download_run2.log")
TARGETS_CSV    = Path("data/bc_redownload_targets.csv")
MONITOR_LOG    = Path("data/logs/bc_monitor.log")
JST            = timezone(timedelta(hours=9))

POLL_SEC       = 30           # ログ確認間隔
STALL_SEC      = 5 * 60       # ログ更新停止判定
CONSEC_FAIL_N  = 5            # 連続失敗の閾値
COOLDOWN_SEC   = 10 * 60      # 同一アラートのクールダウン

WAF_PATTERNS = ["WAF タイムアウト", "WAF 検知", "Verification", "cloudflare"]
COMPLETION_PATTERNS = ["=== 完了 ===", "WAF 3回検知 → 中断"]


def mlog(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(MONITOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(message: str, title: str = "BC監視", priority: str = "high", tags: str = "warning") -> None:
    try:
        send_ntfy(message, title=title, priority=priority, tags=tags)
        mlog(f"LINE送信: {message}")
    except Exception as e:
        mlog(f"LINE送信失敗: {e}")


def main() -> None:
    mlog("=== BC ダウンロード監視開始 ===")
    notify("BC ダウンロード監視 開始（261社）", title="BC監視", tags="eyes", priority="default")

    last_size = 0
    last_change_ts = time.time()
    consec_fail = 0
    cooldowns: dict[str, float] = {}
    completed = False

    def can_alert(key: str) -> bool:
        now = time.time()
        last = cooldowns.get(key, 0)
        if now - last >= COOLDOWN_SEC:
            cooldowns[key] = now
            return True
        return False

    # 初回: 既存ログをスキップ
    if LOG_PATH.exists():
        last_size = LOG_PATH.stat().st_size

    while not completed:
        try:
            time.sleep(POLL_SEC)

            # ファイル存在確認
            if not LOG_PATH.exists():
                mlog("ログファイル未作成。待機")
                continue

            cur_size = LOG_PATH.stat().st_size

            # 新規行を読む
            new_lines: list[str] = []
            if cur_size > last_size:
                with open(LOG_PATH, encoding="utf-8") as f:
                    f.seek(last_size)
                    new_lines = f.read().splitlines()
                last_size = cur_size
                last_change_ts = time.time()

            # 残社数
            remaining = 0
            if TARGETS_CSV.exists():
                with open(TARGETS_CSV, encoding="utf-8") as f:
                    remaining = max(0, sum(1 for _ in f) - 1)

            # WAF / 完了 / 中断 検出
            for line in new_lines:
                # WAF
                for pat in WAF_PATTERNS:
                    if pat in line:
                        if can_alert(f"waf:{pat}"):
                            notify(f"⚠ WAFブロック疑い: {line[-200:]}\n残{remaining}社", title="BC-WAF", priority="urgent", tags="rotating_light")
                        break

                # 完了 / 中断
                for pat in COMPLETION_PATTERNS:
                    if pat in line:
                        notify(f"BC ダウンロード終了: {line[-200:]}\n残{remaining}社", title="BC完了", priority="high", tags="checkered_flag")
                        completed = True
                        break

                if completed:
                    break

                # データなし / エラー連続検出
                if "⚠️ データなし" in line or "❌ エラー" in line:
                    consec_fail += 1
                    if consec_fail >= CONSEC_FAIL_N and can_alert("consec_fail"):
                        notify(f"⚠ 連続失敗 {consec_fail}社\n直近: {line[-200:]}\n残{remaining}社", title="BC連続失敗", priority="high", tags="warning")
                elif "✅" in line:
                    if consec_fail >= CONSEC_FAIL_N:
                        notify(f"✓ 連続失敗から回復（{consec_fail}社後）", title="BC回復", priority="default", tags="white_check_mark")
                    consec_fail = 0

            # 進捗停止検出
            if not completed and (time.time() - last_change_ts) > STALL_SEC:
                if can_alert("stall"):
                    notify(f"⚠ ログ更新停止 {int((time.time()-last_change_ts)/60)}分\n残{remaining}社", title="BC停止", priority="urgent", tags="warning")

        except KeyboardInterrupt:
            mlog("監視中断")
            break
        except Exception as e:
            mlog(f"監視エラー: {e}")
            time.sleep(POLL_SEC)

    mlog("=== 監視終了 ===")


if __name__ == "__main__":
    main()
