import re
import os

tmpdir = os.environ.get('TEMP', '/tmp')

# Check Takashimaya shareholder page for monthly links
path = os.path.join(tmpdir, 'takashimaya_ir3.html')
with open(path, encoding='utf-8', errors='replace') as f:
    html = f.read()

print("=== Takashimaya shareholder page: all anchors ===")
anchors = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)
for href, text in anchors:
    text_clean = re.sub(r'<[^>]+>', '', text).strip()
    if text_clean:
        print(f'  [{text_clean[:60]}] -> {href}')

print()

# Check ABC-Mart homepage for IR links
path = os.path.join(tmpdir, 'abcmart_com.html')
with open(path, encoding='utf-8', errors='replace') as f:
    html = f.read()

print("=== ABC-Mart homepage: IR-related links ===")
anchors = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)
for href, text in anchors:
    text_clean = re.sub(r'<[^>]+>', '', text).strip()
    if 'ir' in href.lower() or 'ir' in text_clean.lower() or '投資家' in text_clean or '株主' in text_clean:
        print(f'  [{text_clean[:60]}] -> {href}')
