"""
抽出ログを解析してTDNETドキュメントあり→0件の企業を特定
"""
import re

with open("data/logs/extract_monthly_all.log", encoding="utf-8") as f:
    lines = f.readlines()

# 14:06以降のライン（最新の全件実行）を対象
recent = [l.strip() for l in lines if "2026-03-24 14:0" in l or "2026-03-24 14:1" in l]

failed = []
current_ticker = None
tdnet_count = 0
for l in recent:
    m = re.search(r"\[(\d+)/\d+\] (\S+) ", l)
    if m:
        current_ticker = m.group(2)
        tdnet_count = 0
    if "TDNET 文書" in l and "抽出中" in l:
        mc = re.search(r"TDNET 文書 (\d+) 件", l)
        tdnet_count = int(mc.group(1)) if mc else 0
    elif "抽出レコードなし" in l and tdnet_count > 0 and current_ticker:
        failed.append((current_ticker, tdnet_count))
        tdnet_count = 0  # reset

print(f"TDNETドキュメントあり → 抽出0件: {len(failed)}社")
for t, n in failed:
    print(f"  {t}: {n}文書")
