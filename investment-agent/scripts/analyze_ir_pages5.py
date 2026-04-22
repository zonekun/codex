import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

files = {
    'Shimamura sales': os.path.join(tmpdir, 'shimamura_sales.html'),
    'Nitori sales': os.path.join(tmpdir, 'nitori_sales.html'),
    'Takashimaya IR2 (shareholder redirect)': os.path.join(tmpdir, 'takashimaya_ir2.html'),
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
    for l in links[:15]:
        print(f'  {l}')

    # Iframes
    iframes = re.findall(r'<iframe[^>]*>', html, re.I)
    print(f'Iframes: {len(iframes)}')
    for i in iframes[:5]:
        print(f'  {i[:300]}')

    # JS keywords
    keywords = ['setParts', 'eIR', 'ir-cloud', 'ircloud', 'ajax', 'fetch(', 'XMLHttpRequest']
    found_kw = [kw for kw in keywords if kw.lower() in html.lower()]
    if found_kw:
        print(f'JS keywords: {found_kw}')

    # Look for tables
    tables = re.findall(r'<table[^>]*>', html, re.I)
    print(f'Tables: {len(tables)}')

    # Sample interesting content
    print('--- First 2000 chars of body ---')
    body_match = re.search(r'<body[^>]*>(.*)', html, re.I | re.S)
    if body_match:
        print(body_match.group(1)[:2000])
    else:
        print(html[:2000])
    print()
