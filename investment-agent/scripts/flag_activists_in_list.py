"""
flag_activists_in_list.py
=========================
四季報大株主リスト（list.txt）の各行にアクティビストフラグを付与して CSV 保存。

使用方法:
  PYTHONUTF8=1 python scripts/flag_activists_in_list.py
  PYTHONUTF8=1 python scripts/flag_activists_in_list.py --input path/to/input.txt
  PYTHONUTF8=1 python scripts/flag_activists_in_list.py --input in.txt --output out.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import jaconv
import pandas as pd
from rapidfuzz import fuzz, process

DEFAULT_INPUT  = Path(r"C:\Users\zonekun\Dropbox\shikihokabu.txt")
ACTIVISTS_CSV  = Path(__file__).parent.parent / "data/master/activists.csv"
ALIASES_CSV    = Path(__file__).parent.parent / "data/master/activist_aliases.csv"
DEFAULT_OUTPUT = Path(__file__).parent.parent / "data/csv/shareholders_activist_flag.csv"
FUZZY_THRESHOLD = 85

# 一般的用語のため partial_rev マッチから除外するブロックリスト（正規化後の表記）
PARTIAL_REV_BLOCKLIST = {
    "INVESTMENTS",
    "INVESTMENT",
    "MANAGEMENT",
    "HOLDINGS",
}

# この株主名は判別不可のため非アクティビストとして固定除外
SHAREHOLDER_EXCLUSIONS = {
    "2投資事業有限責任組合",
    "1号投資事業有限責任組合",
}


def normalize(text: str, keep_spaces: bool = False) -> str:
    if not text:
        return ""
    t = jaconv.h2z(text, kana=True, ascii=False, digit=False)
    t = jaconv.hira2kata(t)
    t = jaconv.z2h(t, kana=False, ascii=True, digit=True)
    t = t.replace("・", " ").replace("･", " ").replace("　", " ")
    t = t.replace("（株）", "").replace("(株)", "").replace("株式会社", "")
    t = t.replace("合同会社", "").replace("有限会社", "").replace("LLC", "").replace("Ltd", "")
    t = t.replace(".", " ").replace(",", " ").replace("、", " ").replace("。", "")
    if not keep_spaces:
        t = t.replace(" ", "")
    return t.upper().strip()


def load_activists() -> list[dict]:
    """activists.csv + activist_aliases.csv を統合して返す."""
    df = pd.read_csv(ACTIVISTS_CSV, encoding="utf-8")
    entries = [
        {"name": r["NAME"], "region": r["REGION"], "norm": normalize(r["NAME"])}
        for _, r in df.iterrows()
    ]
    # エイリアス追加（関連法人名も同一アクティビストとして扱う）
    if ALIASES_CSV.exists():
        region_map = {r["NAME"]: r["REGION"] for _, r in df.iterrows()}
        df_alias = pd.read_csv(ALIASES_CSV, encoding="utf-8")
        for _, r in df_alias.iterrows():
            entries.append({
                "name": r["ACTIVIST_NAME"],
                "region": region_map.get(r["ACTIVIST_NAME"], ""),
                "norm": normalize(r["ALIAS"]),
                "alias": r["ALIAS"],
            })
    return entries


def match_activist(shareholder: str, activists: list[dict]) -> dict | None:
    norm = normalize(shareholder)
    words = normalize(shareholder, keep_spaces=True).split()

    for act in activists:
        if act["norm"] == norm:
            return {**act, "score": 100, "method": "exact"}

    for act in activists:
        n = act["norm"]
        if not n:
            continue
        if len(n) >= 4 and n in norm:
            return {**act, "score": 95, "method": "partial"}
        if len(n) < 4 and words and words[0] == n:
            return {**act, "score": 93, "method": "word_start"}
        # 逆方向: 8文字以上 かつ ブロックリスト語を含まないもののみ
        if norm and len(norm) >= 8 and norm in n and not any(b in norm for b in PARTIAL_REV_BLOCKLIST):
            return {**act, "score": 90, "method": "partial_rev"}

    return None


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする."""
    parser = argparse.ArgumentParser(
        description="四季報大株主リストにアクティビストフラグを付与",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"入力テキストファイル (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"出力CSVファイル (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_txt: Path = args.input
    output_csv: Path = args.output

    activists = load_activists()

    lines = input_txt.read_text(encoding="utf-8").splitlines()
    print(f"読み込み: {len(lines)}行 ({input_txt})")

    rows = []
    for line in lines:
        name = line.strip()
        if not name:
            continue
        match = None if name in SHAREHOLDER_EXCLUSIONS else match_activist(name, activists)
        rows.append({
            "shareholder_name": name,
            "is_activist": bool(match),
            "activist_name": match["name"] if match else "",
            "activist_region": match["region"] if match else "",
            "match_method": match["method"] if match else "",
            "match_score": match["score"] if match else "",
        })

    df = pd.DataFrame(rows)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False, encoding="utf-8-sig")

    activist_count = df["is_activist"].sum()
    print(f"アクティビスト: {activist_count}件 / {len(df)}行")
    print(df[df["is_activist"]][["shareholder_name", "activist_name", "match_method"]].to_string())
    print(f"\n保存: {output_csv}")


if __name__ == "__main__":
    main()
