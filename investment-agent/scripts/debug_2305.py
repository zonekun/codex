"""2305 スタジオアリス の抽出デバッグ"""
import re

_FW2HW = str.maketrans(
    "（）！＂＃＄％＆＇＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～"
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "()!\"#$%&'*+,-./:;<=>?@[\\]^_`{|}~"
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz",
)

raw = "各月売上高・前年比等 （単位：百万円） 月次売上高 (A) 前年同月売上高 (B) 前年同月比 (A)/(B) ３月度 ２，６６７ ２，７６１ ９６．６％ ４月度 ２，２０７ ２，２７８ ９６．９％ ５月度 ２，４２３ ２，３７７ １０１．９％ ６月度 ２，０１８ ２，２６２ ８９．２％ ７月度 １，６９６ １，８８１ ９０．２％ ８月度 ２，２２１ ２，３８７ ９３．１％ ９月度 ２，３６０ ２，６３９ ８９．４％ １０月度 ３，２８４ ３，５４４ ９２．６ ％ １１月度 ４，５６２ ４，９１３ ９２．９％ １２月度 ３，１０７ ３，２４６ ９５．７％ １月度 １，６７９ １，８８６ ８９．０％ ２月度 １，６９４ 通期計 ３１，８７６"

text = raw.translate(_FW2HW)
print("変換後テキスト:")
print(text)
print()

report_month = 1
pattern = r"各月売上高[\s\S]*?" + str(report_month) + r"月度\s+([\d,]+)\s+[\d,]+\s+[\d.]+%"
print("パターン:", pattern)

m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
print("マッチ結果:", m)
if m:
    print("キャプチャ:", m.group(1))

# 簡易マッチ確認
print()
print("=== 個別確認 ===")
print("各月売上高 in text:", "各月売上高" in text)
print("1月度 in text:", "1月度" in text)
idx = text.find("1月度")
if idx >= 0:
    print("1月度の前後:", repr(text[max(0,idx-5):idx+20]))
