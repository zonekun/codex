import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

files = {
    'Shimamura (IR top)': os.path.join(tmpdir, 'shimamura3.html'),
    'Nitori (library)': os.path.join(tmpdir, 'nitori2.html'),
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
    for l in links[:15]:
        print(f'  {l}')

    # Iframes
    iframes = re.findall(r'<iframe[^>]*>', html, re.I)
    print(f'Iframes: {len(iframes)}')
    for i in iframes[:5]:
        print(f'  {i[:300]}')

    # JS keywords
    keywords = ['setParts', 'eIR', 'ir-cloud', 'ircloud', 'ajax', 'fetch(', 'monthly']
    found_kw = [kw for kw in keywords if kw.lower() in html.lower()]
    if found_kw:
        print(f'Keywords in page: {found_kw}')

    # Look for links to monthly pages
    monthly_links = re.findall(r'href="([^"]*monthly[^"]*)"', html, re.I)
    monthly_links += re.findall(r"href='([^']*monthly[^']*)'", html, re.I)
    if monthly_links:
        print(f'Monthly-related links:')
        for l in monthly_links[:10]:
            print(f'  {l}')

    print('--- First 3000 chars ---')
    print(html[:3000])
    print()
