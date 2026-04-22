import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

files = {
    'Nitori': os.path.join(tmpdir, 'nitori.html'),
    'Takashimaya': os.path.join(tmpdir, 'takashimaya.html'),
    'Shimamura': os.path.join(tmpdir, 'shimamura.html'),
    'ABCMart': os.path.join(tmpdir, 'abcmart.html'),
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
        print(f'Title: {t.group(1).strip()[:100]}')

    # Find download links
    links = re.findall(r'href="([^"]*\.(?:pdf|xlsx|csv|xls)[^"]*)"', html, re.I)
    links += re.findall(r"href='([^']*\.(?:pdf|xlsx|csv|xls)[^']*)'", html, re.I)
    print(f'PDF/XLSX links: {len(links)}')
    for l in links[:10]:
        print(f'  {l}')

    # Iframes
    iframes = re.findall(r'<iframe[^>]*>', html, re.I)
    print(f'Iframes: {len(iframes)}')
    for i in iframes[:3]:
        print(f'  {i[:200]}')

    # JS keywords
    keywords = ['setParts', 'eIR', 'ir-cloud', 'ircloud', 'ajax', 'fetch(']
    found_kw = [kw for kw in keywords if kw.lower() in html.lower()]
    if found_kw:
        print(f'JS keywords: {found_kw}')

    print('--- First 2500 chars ---')
    print(html[:2500])
    print()
