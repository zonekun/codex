"""120件のPDFテキストから判定シグナルを抽出する。

判定は Claude (エージェント) 自身が最終判断する。このスクリプトは
判定ルールのヒューリスティックを適用して候補シグナルを提示するだけ。

出力: C:/tmp/v2_pdf_signals.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

IN_PATH = Path("C:/tmp/v2_pdf_texts.json")
OUT_PATH = Path("C:/tmp/v2_pdf_signals.json")


def signals_for_dividend(text: str) -> dict:
    """配当True候補シグナル。"""
    sig = {}
    # 決算短信内の「配当予想からの修正の有無 ： 有」（半角・全角・スペース混在対応）
    # "配当予想からの修正の有無" + "有" をキーフレーズ
    m = re.search(r"配当予想からの修正の有無\s*[:：]\s*有", text)
    sig["配当予想修正フラグ_有"] = bool(m)
    m2 = re.search(r"配当予想からの修正の有無\s*[:：]\s*無", text)
    sig["配当予想修正フラグ_無"] = bool(m2)

    # 独立告知っぽい語
    sig["キーワード_配当予想の修正"] = "配当予想の修正" in text
    sig["キーワード_配当予想修正に関するお知らせ"] = bool(
        re.search(r"配当予想(の)?(修正|変更).{0,20}(お知らせ|について)", text)
    )
    sig["キーワード_増配"] = bool(re.search(r"(増配|期末配当予想の増額|配当金(を|の)増額)", text))
    sig["キーワード_減配"] = bool(re.search(r"(減配|期末配当予想の減額|配当金(を|の)減額)", text))
    sig["キーワード_復配"] = "復配" in text
    sig["キーワード_記念配当"] = "記念配当" in text
    sig["キーワード_特別配当"] = "特別配当" in text
    sig["キーワード_無配転落"] = bool(re.search(r"無配(転落|とする)", text))

    # 配当政策・配当方針の変更
    sig["キーワード_配当方針変更"] = bool(re.search(r"配当方針(の)?(変更|見直し)", text))

    # 「配当」の直接的独立告知
    sig["キーワード_剰余金の配当"] = bool(re.search(r"剰余金の配当(に関するお知らせ|について|の決定)", text))

    return sig


def signals_for_special_loss(text: str) -> dict:
    sig = {}
    sig["特別損失の計上"] = bool(re.search(r"特別損失(を計上|の計上|の発生)", text))
    sig["特別損失に関するお知らせ"] = bool(re.search(r"特別損失に関するお知らせ", text))
    sig["減損損失の計上"] = bool(re.search(r"減損損失(を計上|の計上|の発生|を認識)", text))
    sig["事業構造改革費用"] = "事業構造改革費用" in text
    sig["事業構造改善費用"] = "事業構造改善費用" in text
    sig["訴訟和解金"] = bool(re.search(r"(訴訟和解金|和解金|損害賠償)", text))
    sig["災害損失"] = bool(re.search(r"災害(損失|による損失)", text))
    sig["業績予想の修正_特損理由"] = bool(
        re.search(r"(業績予想の修正|通期業績予想の修正).{0,200}(特別損失|減損)", text, re.DOTALL)
    )
    sig["特別損失_見出し独立"] = bool(re.search(r"^[^\n]{0,20}特別損失(に関するお知らせ|の計上について)", text, re.MULTILINE))
    return sig


def signals_for_special_gain(text: str) -> dict:
    sig = {}
    sig["特別利益の計上"] = bool(re.search(r"特別利益(を計上|の計上|の発生)", text))
    sig["特別利益に関するお知らせ"] = bool(re.search(r"特別利益に関するお知らせ", text))
    sig["固定資産売却益"] = bool(re.search(r"固定資産売却益(を計上|の計上|の発生)", text))
    sig["投資有価証券売却益"] = bool(re.search(r"投資有価証券売却益(を計上|の計上|の発生)", text))
    sig["事業譲渡益"] = bool(re.search(r"事業譲渡益(を計上|の計上|の発生)", text))
    sig["関係会社株式売却益"] = bool(re.search(r"関係会社株式売却益", text))
    sig["業績予想の修正_特利理由"] = bool(
        re.search(r"(業績予想の修正|通期業績予想の修正).{0,200}(特別利益|売却益)", text, re.DOTALL)
    )
    return sig


def extract_snippet(text: str, keyword_re: str, chars: int = 200) -> list[str]:
    """正規表現周辺の前後150文字を抜粋（最大3件）。"""
    snippets = []
    for m in re.finditer(keyword_re, text):
        s = max(0, m.start() - 100)
        e = min(len(text), m.end() + chars)
        snippets.append(text[s:e].replace("\n", " ")[:chars])
        if len(snippets) >= 3:
            break
    return snippets


def main() -> None:
    with open(IN_PATH, encoding="utf-8") as f:
        samples = json.load(f)

    results = []
    for d in samples:
        text = d["text"]
        cat = d["category"]
        base = {k: d[k] for k in ("category", "subset", "doc_id", "ticker", "short_name", "doc_type", "title", "file_name", "text_len", "page_count", "error")}
        sig: dict = {}

        if cat == "配当":
            sig = signals_for_dividend(text)
            base["snippet_配当予想修正"] = extract_snippet(text, r"配当予想からの修正の有無\s*[:：]\s*[有無]", 150)
            base["snippet_配当修正告知"] = extract_snippet(text, r"配当予想(の)?(修正|変更|増額|減額)", 200)
            base["snippet_特別記念"] = extract_snippet(text, r"(記念配当|特別配当|復配)", 150)
        elif cat == "特別損失":
            sig = signals_for_special_loss(text)
            base["snippet_特損"] = extract_snippet(text, r"特別損失(を計上|の計上|の発生|に関するお知らせ)", 250)
            base["snippet_減損"] = extract_snippet(text, r"減損損失(を計上|の計上|の発生|を認識)", 200)
            base["snippet_業績修正"] = extract_snippet(text, r"業績予想の修正", 300)
        elif cat == "特別利益":
            sig = signals_for_special_gain(text)
            base["snippet_特利"] = extract_snippet(text, r"特別利益(を計上|の計上|の発生|に関するお知らせ)", 250)
            base["snippet_売却益"] = extract_snippet(text, r"(固定資産売却益|投資有価証券売却益|事業譲渡益|関係会社株式売却益)", 200)
            base["snippet_業績修正"] = extract_snippet(text, r"業績予想の修正", 300)

        base["signals"] = sig
        base["signal_true_count"] = sum(1 for v in sig.values() if v)
        results.append(base)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(results)} signals to {OUT_PATH}")


if __name__ == "__main__":
    main()
