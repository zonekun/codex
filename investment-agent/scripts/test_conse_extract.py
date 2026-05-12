"""Test extract_consensus with simulated Selenium HTML."""
import sys
sys.path.insert(0, "scripts")
from update_conse_rakuten import extract_consensus

test_html = (
    '<html><body>'
    '<table class="tbl-data-01" border="1" cellspacing="0">'
    '<thead><tr><th></th><th>1Q</th><th>2Q</th><th>3Q</th><th>通期</th></tr></thead>'
    '<tbody>'
    '<tr><th class="sub">202603(A)<br/>進捗率</th>'
    '<td>1,252,152<br/><span>24.9%</span></td>'
    '<td>2,478,127<br/><span>49.4%</span></td>'
    '<td>4,188,484<br/><span>83.4%</span></td>'
    '<td>--<br/>--%</td></tr>'
    '<tr><th class="sub">会社予想</th><td>--</td><td>--</td><td>--</td><td>5,020,000</td></tr>'
    '<tr class="cons"><th class="sub">コンセンサス</th>'
    '<td>1,145,225</td><td>2,463,250</td><td>3,723,017</td><td>5,221,420</td></tr>'
    '</tbody></table>'
    '<table class="tbl-data-09" border="1" cellspacing="0">'
    '<thead><tr><th>決算期</th><th>発表日</th><th colspan="2">売上高</th>'
    '<th colspan="2">営業利益</th><th colspan="2">経常利益</th><th colspan="2">当期純利益</th></tr></thead>'
    '<tbody>'
    '<tr class="cons"><th class="cons">202603(A)<br/>コンセンサス</th>'
    '<td class="date">2026/04/24</td><td class="cell-02">50,796,270</td><td></td>'
    '<td class="cell-02">4,022,673</td><td></td>'
    '<td class="cell-02">5,221,420</td><td></td>'
    '<td class="cell-02">3,720,790</td><td></td></tr>'
    '<tr class="cons"><th class="cons">202703(A)<br/>コンセンサス</th>'
    '<td class="date">2026/04/24</td><td class="cell-02">52,833,510</td><td></td>'
    '<td class="cell-02">4,532,827</td><td></td>'
    '<td class="cell-02">5,580,910</td><td></td>'
    '<td class="cell-02">3,959,180</td><td></td></tr>'
    '</tbody></table>'
    '</body></html>'
)

records, fy_period = extract_consensus(test_html)
print(f"FY period: {fy_period}")
print(f"Records ({len(records)}):")
for r in records:
    print(f"  {r}")

# Assertions
assert fy_period == "202603", f"Expected 202603, got {fy_period}"
assert len(records) == 6, f"Expected 6 records, got {len(records)}"
assert records[0] == {"QUARTER": "1Q", "PROFIT": 1145225, "TARGET": "CURRENT"}
assert records[1] == {"QUARTER": "2Q", "PROFIT": 2463250, "TARGET": "CURRENT"}
assert records[2] == {"QUARTER": "3Q", "PROFIT": 3723017, "TARGET": "CURRENT"}
assert records[3] == {"QUARTER": "FY", "PROFIT": 5221420, "TARGET": "CURRENT"}
assert records[4] == {"QUARTER": "FY", "PROFIT": 5221420, "TARGET": "CURRENT"} or records[4]["TARGET"] == "NEXT"
print("\nAll assertions passed!")
