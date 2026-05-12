"""BB_債券履歴_new.xlsx → data/csv/bond_history.csv 更新スクリプト.

PSメニュー「BB_債券履歴_new 取り込み更新」
詳細: docs/knowledges/tools/023_powershell_menu.md
"""
import os

import pandas as pd

EXCEL_PATH = r'C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx'
CSV_PATH   = os.path.join(os.path.dirname(__file__), '..', 'data', 'csv', 'bond_history.csv')

COL_MAP = {
    0:  'DATE',
    1:  'BEI',
    2:  'US10Y',
    3:  'US5Y',
    4:  'US2Y',
    5:  'SP500_DIV_YIELD',
    6:  'AAA_YIELD',
    7:  'AAA_SPREAD',
    8:  'USDJPY',
    9:  'HYG',
    10: 'USDX',
    11: 'SOX',
    12: 'BADI',
    13: 'CRB',
    14: 'SKEW',
    15: 'DOW',
    17: 'NASDAQ',
    19: 'VIX',
    23: 'SP500',
    26: 'JP10Y',
    29: 'WTI',
    30: 'FEAR_GREED',
}

def run():
    df = pd.read_excel(EXCEL_PATH, sheet_name='LIST', header=0, skiprows=5)
    out = df.iloc[:, list(COL_MAP.keys())].copy()
    out.columns = list(COL_MAP.values())
    out['DATE'] = pd.to_datetime(out['DATE']).dt.date
    for col in out.columns:
        if col != 'DATE':
            out[col] = pd.to_numeric(out[col], errors='coerce')
    out.to_csv(CSV_PATH, index=False, encoding='utf-8')
    print(f'✅ 更新完了: {len(out)} 行 / 期間: {out["DATE"].iloc[0]} 〜 {out["DATE"].iloc[-1]}')

if __name__ == '__main__':
    run()
