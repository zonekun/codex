import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

files = {
    'Takashimaya 月次営業情報': os.path.join(tmpdir, 'takashimaya_eigyou.html'),
    'ABC-Mart IR (.co.jp)': os.path.join(tmpdir, 'abcmart_co_ir.html'),
}

for name, path in files.items():
    print(f'=== {name} ===')
    if not os.path.exists(path):
        print(f'  FILE NOT FOUND: {path}')
        print()
        continue
    with open(path, encoding='utf-8', errors='replace') as f:
        html = f.read()
    print(f'Page size: {len(html)} chars')
    t = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
    if t:
        print(f'Title: {t.group(1).strip()[:120]}')

    # Find download links
    links = re.findall(r'href="([^"]*\.(?:pdf|xlsx|csv|xls)[^"]*)"', html, re.I)
    links += re.findall(r"href='([^']*\.(?:pdf|xlsx|csv|xls)[^']*)'", html, re.I)
    print(f'PDF/XLSX links: {len(links)}')
    for l in links[:20]:
        print(f'  {l}')

    # Iframes
    iframes = re.findall(r'<iframe[^>]*>', html, re.I)
    print(f'Iframes: {len(iframes)}')
    for i in iframes[:5]:
        print(f'  {i[:300]}')

    # JS keywords
    keywords = ['setParts', 'eIR', 'ir-cloud', 'ircloud', 'ajax', 'fetch(', 'XMLHttpRequest', 'monthly']
    found_kw = [kw for kw in keywords if kw.lower() in html.lower()]
    if found_kw:
        print(f'JS keywords: {found_kw}')

    # Tables
    tables = re.findall(r'<table[^>]*>', html, re.I)
    print(f'Tables: {len(tables)}')

    # Look for monthly data links
    anchors = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)
    print('Relevant links (monthly/sales/pdf):')
    for href, text in anchors:
        text_clean = re.sub(r'<[^>]+>', '', text).strip()
        if any(kw in href.lower() or kw in text_clean for kw in ['.pdf', 'monthly', '月次', '営業', 'eigyou', 'sales']):
            if text_clean:
                print(f'  [{text_clean[:60]}] -> {href}')

    print()
