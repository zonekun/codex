"""SKEW × VIX × Fear&Greed テールリスクシグナル 直近10日チェック.

PSメニュー「テールリスクシグナル 直近10日チェック」
詳細: docs/knowledges/analysis/003_skew_vix_fg_tail_risk.md, docs/knowledges/tools/023_powershell_menu.md
"""
import os

import pandas as pd

CSV_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'csv', 'bond_history.csv')

def run(days: int = 10):
    df = pd.read_csv(CSV_PATH, parse_dates=['DATE'])
    df = df.sort_values('DATE').reset_index(drop=True)
    for c in ['SP500', 'VIX', 'SKEW', 'FEAR_GREED']:
        df[c] = df[c].ffill()

    SKEW_TH = df['SKEW'].quantile(0.90)
    VIX_LOW = df['VIX'].quantile(0.25)
    FG_HIGH = 70

    print(f'=== テールリスクシグナル（直近{days}日） ===')
    print(f'閾値: SKEW≥{SKEW_TH:.1f} / VIX≤{VIX_LOW:.1f} / F&G≥{FG_HIGH}')
    print(f'{"日付":^12} | {"SKEW":^8} | {"VIX":^6} | {"F&G":^6} | シグナル')
    print('-' * 55)
    for _, row in df.tail(days).iterrows():
        sk, vx, fg = row['SKEW'], row['VIX'], row['FEAR_GREED']
        s1 = '⚠' if sk >= SKEW_TH else '  '
        s2 = '⚠' if vx <= VIX_LOW else '  '
        s3 = '⚠' if fg >= FG_HIGH  else '  '
        fired = (sk >= SKEW_TH) and (vx <= VIX_LOW) and (fg >= FG_HIGH)
        sig = '🔴発火' if fired else '🟢'
        print(f'{str(row["DATE"].date()):^12} | {sk:>5.1f}{s1}  | {vx:>4.1f}{s2} | {fg:>4.0f}{s3} | {sig}')

    # 現在の状態サマリー
    latest = df.iloc[-1]
    sk, vx, fg = latest['SKEW'], latest['VIX'], latest['FEAR_GREED']
    fired = (sk >= SKEW_TH) and (vx <= VIX_LOW) and (fg >= FG_HIGH)
    print()
    print(f'最新({latest["DATE"].date()}): ', end='')
    if fired:
        print('🔴 シグナル発火中 — 大暴落リスク2.2倍（ポジション縮小・ヘッジ検討）')
    else:
        n = sum([sk >= SKEW_TH, vx <= VIX_LOW, fg >= FG_HIGH])
        print(f'🟢 非発火（{n}/3条件充足）')

if __name__ == '__main__':
    run()
