"""
オーナー色バッチ判定スクリプト (irbank.net スクレイピング版)

用途: family_holding_candidates_classified.csv の「要確認」銘柄を
      irbank.net の大株主情報から YES/NO 判定し CSV に追記する。
実行: PYTHONUTF8=1 python scripts/tob_prediction/owner_judge_batch.py [--offset N]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
INPUT_CSV = Path("C:/tmp/tob_prediction/family_holding_candidates_classified.csv")
OUTPUT_CSV = Path("C:/tmp/tob_prediction/owner_judge_results_batch.csv")
NOTIFY_SCRIPT = Path("scripts/notify.py")
PYTHON = sys.executable

# ---------------------------------------------------------------------------
# 判定ルール定数
# ---------------------------------------------------------------------------

# これらが筆頭株主なら → NO（機関投資家・外資・大手上場）
NO_KEYWORDS: list[str] = [
    "日本マスタートラスト", "日本カストディ", "資産管理サービス信託",
    "STATE STREET", "BLACKROCK", "VANGUARD", "NORTHERN TRUST",
    "JP MORGAN", "GOLDMAN SACHS", "MORGAN STANLEY", "CITIBANK",
    "DBS BANK", "BNY MELLON", "UBS", "CREDIT SUISSE",
    "野村信託", "大和証券", "三菱UFJ信託", "みずほ信託", "三井住友信託",
    "富士フイルム", "SBIインキュベーション", "SBIホールディングス",
    "RIZAPグループ", "メルコホールディングス", "Kakao", "Cykan",
    "A.P.F.Group", "A．P．F",
]

# 外資パターン（英字が多い + LTD/LLC/CORP/INC等）
FOREIGN_PAT = re.compile(r"(?:LTD|LLC|CORP|INC|PTE|PLC|AG|SA|BV|NV|GmbH)[\.\s]?$", re.IGNORECASE)
FOREIGN_ALLCAPS = re.compile(r"^[A-Z0-9\s\.&,\-]{6,}$")  # 全部英大文字

# 個人名パターン（漢字2〜4文字のみ、会社系ワードなし）
PERSONAL_PAT = re.compile(r"^[一-鿿]{2,5}$")
COMPANY_WORDS = re.compile(r"会社|株式|ホールディングス|グループ|興産|商事|産業|不動産|投資|コーポレーション|ファンド|信託|銀行|証券|保険")

# 創業家系持株会社パターン（→ YES候補）
OWNER_HOLDING_PAT = re.compile(r"興産|商事|プロパティ|エステート|リアルティ|アセット|キャピタル|マネジメント")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def get_shareholders(ticker: str) -> list[str]:
    """irbank.net から上位5株主名を取得する。失敗時は空リスト。"""
    url = f"https://irbank.net/{ticker}/holder"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        names: list[str] = []
        for table in tables:
            for row in table.find_all("tr")[1:6]:  # ヘッダー除く上位5行
                cells = row.find_all(["td", "th"])
                if cells:
                    name = cells[0].get_text(strip=True)
                    if name and name != "大株主":
                        names.append(name)
            if names:
                break
        return names[:5]
    except Exception:
        return []


def is_foreign(name: str) -> bool:
    """外資系っぽい名前かどうか。"""
    return bool(FOREIGN_PAT.search(name) or FOREIGN_ALLCAPS.match(name))


def is_institution(name: str) -> bool:
    """機関投資家・大手上場企業かどうか。"""
    for kw in NO_KEYWORDS:
        if kw.lower() in name.lower():
            return True
    return False


def is_personal(name: str) -> bool:
    """個人名っぽいかどうか。"""
    return bool(PERSONAL_PAT.match(name) and not COMPANY_WORDS.search(name))


def judge(ticker: str, shareholders: list[str]) -> tuple[str, str]:
    """YES/NO と根拠を返す。"""
    if not shareholders:
        return "NO", "株主情報取得失敗。デフォルトNO"

    top3 = shareholders[:3]
    top1 = shareholders[0]

    # 個人名が上位3位以内
    for name in top3:
        if is_personal(name):
            return "YES", f"個人名「{name}」が上位大株主。オーナー支配と判断"

    # 筆頭が機関投資家・外資・大手上場
    if is_institution(top1) or is_foreign(top1):
        return "NO", f"「{top1}」が筆頭株主。機関・外資・大手上場支配のためNO"

    # 上位3の過半が機関・外資
    no_count = sum(1 for n in top3 if is_institution(n) or is_foreign(n))
    if no_count >= 2:
        return "NO", f"上位株主の多数が機関・外資。筆頭:「{top1}」"

    # 創業家系持株会社パターン
    for name in top3:
        if OWNER_HOLDING_PAT.search(name) and not is_institution(name):
            return "YES", f"「{name}」が上位株主。創業家系持株会社の可能性あり"

    # 判定不能 → デフォルトNO
    top_str = "・".join(top3[:2])
    return "NO", f"上位株主「{top_str}」。創業家支配の直接根拠なし。デフォルトNO"


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    """バッチ処理メイン。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=int, default=5, help="スキップ件数 (default=5)")
    parser.add_argument("--limit", type=int, default=0, help="処理件数上限 0=全件 (default=0)")
    args = parser.parse_args()

    # 要確認リスト構築
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    required = {}
    for r in rows:
        if r.get("判定", "").strip() == "要確認":
            t = r.get("発行体TICKER", "").strip()
            n = r.get("発行体名", "").strip()
            if t and t not in required:
                required[t] = n

    targets = list(required.items())[args.offset:]
    if args.limit > 0:
        targets = targets[: args.limit]

    total = len(targets)
    print(f"[owner_judge_batch] 処理対象: {total}件 (offset={args.offset})", flush=True)

    # 出力CSV（初回のみヘッダー）
    write_header = not OUTPUT_CSV.exists()
    out_f = open(OUTPUT_CSV, "a", encoding="utf-8", newline="")
    writer = csv.writer(out_f)
    if write_header:
        writer.writerow(["発行体TICKER", "発行体名", "YES_NO", "根拠"])

    yes_cnt = no_cnt = err_cnt = 0

    for i, (ticker, name) in enumerate(targets, 1):
        try:
            shareholders = get_shareholders(ticker)
            verdict, reason = judge(ticker, shareholders)
            writer.writerow([ticker, name, verdict, reason])
            out_f.flush()
            if verdict == "YES":
                yes_cnt += 1
            else:
                no_cnt += 1
            print(f"[{i}/{total}] {ticker} {name[:10]} → {verdict}", flush=True)
        except Exception as e:
            writer.writerow([ticker, name, "NO", f"例外: {e}。デフォルトNO"])
            out_f.flush()
            err_cnt += 1
            print(f"[{i}/{total}] {ticker} ERROR: {e}", flush=True)

        time.sleep(0.6)  # レートリミット対策

    out_f.close()

    summary = (
        f"【owner_judge_batch 完了】\n"
        f"処理: {total}件 YES:{yes_cnt} NO:{no_cnt} ERR:{err_cnt}\n"
        f"出力: {OUTPUT_CSV}"
    )
    print(summary, flush=True)

    # LINE通知
    try:
        import subprocess
        subprocess.run(
            [
                PYTHON, str(NOTIFY_SCRIPT), "ntfy", summary,
                "--sender", "ATP", "--task", "owner_judge_batch",
            ],
            check=False,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
