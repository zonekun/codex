"""受注チューニング用PDFテキストから「受注」関連文脈のみ抽出してJudgment補助テキスト生成.

_pdf_texts.json から各サンプルについて
- タイトル
- 「受注高」「受注残高」「受注」「Backlog」等のキーワード近傍(±200文字)
を抜き出し、Claude自己判定用の薄いテキストを作る。

Usage:
    PYTHONUTF8=1 python scripts/gemma4_juchu_tuning_slice.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

IN_PATH = Path("C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_juchu_20260416/_pdf_texts.json")
OUT_PATH = Path("C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_juchu_20260416/_pdf_snippets.md")

# 受注関連キーワード
JUCHU_KWS = [
    "受注高", "受注残高", "受注残", "Order Intake", "Backlog",
    "受注状況", "受注額", "受注金額", "受注実績", "受注見通し",
]

WINDOW = 180  # キーワード前後の文字数


def find_snippets(text: str, kws: list[str], window: int) -> list[tuple[str, str]]:
    """text中からkwsのいずれかを含む位置近傍のwindow文字切り出し."""
    results: list[tuple[str, str]] = []
    seen_ranges: list[tuple[int, int]] = []
    for kw in kws:
        for m in re.finditer(re.escape(kw), text):
            s = max(0, m.start() - window)
            e = min(len(text), m.end() + window)
            # 重複する範囲はスキップ(既に切り出した範囲の80%以上を含む場合)
            overlap = False
            for (ps, pe) in seen_ranges:
                if s >= ps and e <= pe:
                    overlap = True
                    break
            if overlap:
                continue
            seen_ranges.append((s, e))
            snip = text[s:e].replace("\n", " ").strip()
            results.append((kw, snip))
    return results


def main() -> None:
    payload = json.loads(IN_PATH.read_text(encoding="utf-8"))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("# 受注カテゴリ PDFスニペット(Claude自己判定用)\n\n")
        for kind in ("detected", "missed", "over_detected"):
            f.write(f"\n---\n\n# === {kind} ===\n\n")
            items = payload.get(kind, [])
            for i, it in enumerate(items, 1):
                doc_id = it["doc_id"]
                ticker = it["ticker"]
                title = it["doc_title"]
                gm = it["gemma_main"]
                gs = ",".join(it["gemma_subs"] or [])
                em = it["gemini_main"]
                es = ",".join(it["gemini_subs"] or [])
                text = it.get("pdf_text") or ""
                snippets = find_snippets(text, JUCHU_KWS, WINDOW)
                f.write(f"## [{kind}#{i}] {ticker} {title[:60]}\n\n")
                f.write(f"- doc_id: `{doc_id}`\n")
                f.write(f"- Gemma main={gm}  subs=[{gs}]\n")
                f.write(f"- Gemini main={em}  subs=[{es}]\n")
                f.write(f"- PDFテキスト長: {len(text)}文字\n")
                f.write(f"- 受注系キーワードヒット: {len(snippets)}件\n\n")
                if not snippets:
                    # キーワードなし → 冒頭1500文字を出す
                    head = text[:1500].replace("\n", " ")
                    f.write(f"**[KW_NONE] 冒頭:** {head}\n\n")
                else:
                    for j, (kw, snip) in enumerate(snippets[:8], 1):
                        f.write(f"**[kw={kw} #{j}]** ...{snip}...\n\n")
    print(f"[WRITE] {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
