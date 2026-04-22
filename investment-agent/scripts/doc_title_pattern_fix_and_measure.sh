#!/bin/bash
# NFKC 修復 → 影響銘柄のみ再 extract → compare → 効果測定 を一括実行
set -e
cd /c/gdrive/claude/investment-agent

PY="C:/venvs/investment-agent/Scripts/python.exe"
LINT_CSV="data/logs/doc_title_pattern_lint_20260418_231544.csv"
TS=$(date +%Y%m%d_%H%M%S)
LOG_DIR="data/logs"
SUMMARY_MD="$LOG_DIR/doc_title_pattern_fix_report_${TS}.md"

echo "=== Step 1: NFKC 修復（--dry-run でリスト取得） ===" | tee -a "$SUMMARY_MD"
PYTHONUTF8=1 "$PY" scripts/fix_doc_title_patterns_bg.py \
  --lint-csv "$LINT_CSV" --dry-run 2>&1 | tee -a "$SUMMARY_MD" > "$LOG_DIR/fix_dryrun_${TS}.log"
# 修正対象 ticker を抽出
FIXED_TICKERS=$(grep -oP '\[\K\d{4}[A-Z]?(?=\])' "$LOG_DIR/fix_dryrun_${TS}.log" | sort -u | tr '\n' ' ')
echo "修復対象 tickers: $FIXED_TICKERS" | tee -a "$SUMMARY_MD"

echo "" | tee -a "$SUMMARY_MD"
echo "=== Step 2: 実適用 ===" | tee -a "$SUMMARY_MD"
PYTHONUTF8=1 "$PY" scripts/fix_doc_title_patterns_bg.py \
  --lint-csv "$LINT_CSV" 2>&1 | tee -a "$SUMMARY_MD" > "$LOG_DIR/fix_apply_${TS}.log"
tail -10 "$LOG_DIR/fix_apply_${TS}.log" | tee -a "$SUMMARY_MD"

echo "" | tee -a "$SUMMARY_MD"
echo "=== Step 3: 影響銘柄の再 extract ===" | tee -a "$SUMMARY_MD"
if [ -n "$FIXED_TICKERS" ]; then
  PYTHONUTF8=1 "$PY" scripts/extract_monthly_data.py \
    --tickers $FIXED_TICKERS --since 2024 2>&1 \
    | tee -a "$LOG_DIR/extract_after_fix_${TS}.log" | tail -30 \
    | tee -a "$SUMMARY_MD"
else
  echo "修復対象なし → skip" | tee -a "$SUMMARY_MD"
fi

echo "" | tee -a "$SUMMARY_MD"
echo "=== Step 4: compare 再実行 ===" | tee -a "$SUMMARY_MD"
PYTHONUTF8=1 "$PY" scripts/compare_monthly_buffett.py --offline \
  --tickers $FIXED_TICKERS 2>&1 \
  | tee -a "$LOG_DIR/compare_after_fix_${TS}.log" | tail -30 \
  | tee -a "$SUMMARY_MD"

echo "" | tee -a "$SUMMARY_MD"
echo "=== Step 5: 効果測定（before/after diff） ===" | tee -a "$SUMMARY_MD"
PREV_CSV="C:/tmp/buffett_compare_20260418_173658.csv"
LATEST_CSV=$(ls -t C:/tmp/buffett_compare_*.csv | head -1)
echo "PREV: $PREV_CSV" | tee -a "$SUMMARY_MD"
echo "AFTER: $LATEST_CSV" | tee -a "$SUMMARY_MD"

PYTHONUTF8=1 "$PY" -c "
import csv
from collections import defaultdict

def load_counts(path, tickers_filter=None):
    ok = ng = nd = 0
    per_t = defaultdict(lambda: {'ok':0, 'ng':0, 'nd':0})
    with open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if tickers_filter and r['ticker'] not in tickers_filter: continue
            m = r['match']
            if m == 'OK': ok += 1; per_t[r['ticker']]['ok'] += 1
            elif m == 'NG': ng += 1; per_t[r['ticker']]['ng'] += 1
            else: nd += 1; per_t[r['ticker']]['nd'] += 1
    return (ok, ng, nd, per_t)

tickers = set('$FIXED_TICKERS'.split())
b_ok, b_ng, b_nd, b_per = load_counts('$PREV_CSV', tickers)
a_ok, a_ng, a_nd, a_per = load_counts('$LATEST_CSV', tickers)
print(f'修正対象 {len(tickers)} 銘柄の集計（before → after）')
print(f'  OK:       {b_ok:4d} → {a_ok:4d}  (Δ {a_ok - b_ok:+d})')
print(f'  NG:       {b_ng:4d} → {a_ng:4d}  (Δ {a_ng - b_ng:+d})')
print(f'  BC_NODATA:{b_nd:4d} → {a_nd:4d}  (Δ {a_nd - b_nd:+d})')
# ticker 別改善/劣化
improved = []
degraded = []
for t in sorted(tickers):
    b = b_per.get(t, {'ok':0,'ng':0,'nd':0})
    a = a_per.get(t, {'ok':0,'ng':0,'nd':0})
    d = a['ok'] - b['ok']
    if d > 0: improved.append((t, b['ok'], a['ok'], d))
    elif d < 0: degraded.append((t, b['ok'], a['ok'], d))
print()
print(f'改善 {len(improved)} 銘柄、劣化 {len(degraded)} 銘柄')
for t, b, a, d in sorted(improved, key=lambda x: -x[3])[:15]:
    print(f'  + {t}: OK {b} → {a} ({d:+d})')
for t, b, a, d in sorted(degraded, key=lambda x: x[3])[:10]:
    print(f'  - {t}: OK {b} → {a} ({d:+d})')
" 2>&1 | tee -a "$SUMMARY_MD"

echo "" | tee -a "$SUMMARY_MD"
echo "サマリ: $SUMMARY_MD"
