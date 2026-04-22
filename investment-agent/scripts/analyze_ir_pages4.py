import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

# Check shimamura IR top for monthly links
path = os.path.join(tmpdir, 'shimamura3.html')
with open(path, encoding='utf-8', errors='replace') as f:
    html = f.read()

print("=== Shimamura: looking for monthly/uriage links ===")
# Find all anchor tags
anchors = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)
for href, text in anchors:
    text_clean = re.sub(r'<[^>]+>', '', text).strip()
    if any(kw in href.lower() or kw in text_clean.lower() for kw in ['monthly', 'uriage', '月次', '売上', 'sales', 'data']):
        print(f'  [{text_clean[:50]}] -> {href}')

print()

# Check nitori library for monthly links
path = os.path.join(tmpdir, 'nitori2.html')
with open(path, encoding='utf-8', errors='replace') as f:
    html = f.read()

print("=== Nitori library: looking for monthly/uriage links ===")
anchors = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)
for href, text in anchors:
    text_clean = re.sub(r'<[^>]+>', '', text).strip()
    if any(kw in href.lower() or kw in text_clean.lower() for kw in ['monthly', 'uriage', '月次', '売上', 'sales']):
        print(f'  [{text_clean[:50]}] -> {href}')

print()
print("=== Nitori full anchor list (all IR-related) ===")
for href, text in anchors:
    text_clean = re.sub(r'<[^>]+>', '', text).strip()
    if text_clean and '/ir/' in href:
        print(f'  [{text_clean[:50]}] -> {href}')
