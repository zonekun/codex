"""株主優待 中間JSONL → 4月優待CSV変換

中間JSONLから4月権利確定分のみ抽出し、CSVに出力する。
4月+10月の銘柄は4月分のテキストのみ切り出す。
4月以外のみの銘柄（例: 7月末）は除外する。

Usage:
    PYTHONUTF8=1 python scripts/test_yutai_extract.py
"""
from __future__ import annotations

import json
import os
import re
import sys

import structlog

log = structlog.get_logger()

INPUT_JSONL = r"C:\tmp\yutai_raw.jsonl"
OUTPUT_CSV = r"C:\Users\zonekun\Dropbox\stock\temp\yutai\yutai_april.csv"


def has_april(kenri: str) -> bool:
    """権利確定に4月が含まれるか判定する。"""
    return "4月" in kenri


def extract_april_text(kenri: str, text: str) -> str:
    """4月+他月の銘柄から4月分テキストを切り出す。

    4月のみの場合はそのまま返す。
    4月+10月等の場合、テキスト内の月別セクション境界で分割を試みる。
    明確な境界がなければ全文を返す（共通優待のため）。
    """
    if "10月" not in kenri:
        return text

    # パターン1: ～変更前～ / ～変更後～ で分かれている（2751等）
    m = re.search(r"(～変更後～.*?)$", text, re.DOTALL)
    if m:
        after_section = m.group(1)
        if "10月" in after_section and "4月" not in after_section:
            before = text[:m.start()].strip()
            return before

    # パターン2: 基準日テーブルに4月/10月が別行（3031等）
    # → 両方同じ内容なのでそのまま返す

    # パターン3: 「4月末基準日の株主優待品」セクション（3361等）
    april_section = re.search(
        r"(～\d{4}年4月末基準日の株主優待品～[\s\S]*?)(?=～\d{4}年\d{1,2}月|$)",
        text,
    )
    if april_section:
        base = text
        specific = april_section.group(1).strip()
        general_end = april_section.start()
        general = text[:general_end].strip()
        return general + "\n" + specific

    return text


def clean_text(text: str) -> str:
    """末尾の免責文言を除去する。"""
    markers = [
        "●株主優待情報を投資の判断材料とされる場合",
        "●株主優待は企業が独自に実施",
    ]
    for marker in markers:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].strip()
    # 写真キャプション除去
    text = re.sub(r"［写真\d+］.*?(?:\n|$)", "", text)
    return text.strip()


def main() -> None:
    """中間JSONLを読み込み、4月優待CSVを出力する。"""
    if not os.path.exists(INPUT_JSONL):
        log.error("input_not_found", path=INPUT_JSONL)
        sys.exit(1)

    records: list[dict[str, str]] = []
    skipped: list[str] = []

    with open(INPUT_JSONL, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            ticker = r["ticker"]
            kenri = r["kenri_kakutei"]

            if not has_april(kenri):
                log.info("skip_no_april", ticker=ticker, kenri=kenri)
                skipped.append(ticker)
                continue

            text = r["yutai_text"]
            text = extract_april_text(kenri, text)
            text = clean_text(text)

            records.append({"ticker": ticker, "text": text})
            log.info("extracted", ticker=ticker, kenri=kenri, text_len=len(text))

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8") as f:
        f.write("ｺｰﾄﾞ,優待内容\n")
        for rec in records:
            escaped = rec["text"].replace("\n", " ").replace('"', '""')
            f.write(f'{rec["ticker"]},"{escaped}"\n')

    log.info("done", output=OUTPUT_CSV, extracted=len(records),
             skipped=len(skipped), skipped_tickers=skipped)


if __name__ == "__main__":
    main()
