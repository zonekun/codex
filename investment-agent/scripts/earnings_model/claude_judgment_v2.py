"""Claude自身の判定結果を反映し、比較レポートを生成する。

このスクリプトは Claude (エージェント) が PDF テキストを読んで行った判定を
ハードコードしている。各行の判定理由は judgment_helper.txt を参照して付与した。

出力:
    C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/v2_analysis_20260416/_claude_judgment.md
    C:/tmp/claude_judgment_v2.csv
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

IN_PATH = Path("C:/tmp/v2_pdf_texts_full.json")
OUT_MD = Path("C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/v2_analysis_20260416/_claude_judgment.md")
OUT_CSV = Path("C:/tmp/claude_judgment_v2.csv")

# (doc_id, category, subset) -> (Claude判定 True/False, 判定理由)
# subset: detected (Gemma=T, Gemini=T) or missed (Gemma=F, Gemini=T)
#
# Claude=True の場合:
#   detected → 両モデル正解
#   missed → Gemini正解 (Gemmaの漏れ)
# Claude=False の場合:
#   detected → 両モデル過剰検知
#   missed → Gemma正解 (Geminiの過剰検知)
JUDGMENTS: dict[tuple[str, str, str], tuple[bool, str]] = {
    # ===== 配当 / detected =====
    ("140120231227509532", "配当", "detected"): (True, "配当予想修正=有+記念配当。「業績予想及び配当予想の修正に関するお知らせ」同時告知"),
    ("140120231226508864", "配当", "detected"): (True, "配当予想修正=有。「通期連結業績予想の修正及び配当予想の修正に関するお知らせ」"),
    ("140120240105511267", "配当", "detected"): (True, "配当予想修正=有。「2024年２月期配当予想の修正（無配）及び株主優待制度の廃止に関するお知らせ」"),
    ("140120240106511765", "配当", "detected"): (False, "決算短信の配当の状況欄に予想32円→33円記載のみ。独立告知なし"),
    ("140120231219505612", "配当", "detected"): (True, "配当予想修正=有。「通期個別業績予想の修正、剰余金の配当（中間配当）および期末配当予想の修正に関するお知らせ」"),
    ("140120240109512418", "配当", "detected"): (True, "決算説明資料で年間6円増配・特別配当11円・配当性向明示（株主還元セクション独立）"),
    ("140120240105511574", "配当", "detected"): (True, "決算補足説明資料で増配の記述あり「着実に増配を重ねてきました」、2024年配当予想計58円開示"),
    ("140120240110512692", "配当", "detected"): (True, "配当予想修正=有。期末配当18円（前期12円から増配）"),
    ("140120240104511123", "配当", "detected"): (True, "配当予想修正=有。「業績予想の修正並びに期末配当予想の修正に関するお知らせ」"),
    ("140120240111513621", "配当", "detected"): (False, "決算補足説明資料、配当の告知的文言なし"),
    ("140120240110512927", "配当", "detected"): (True, "配当予想修正=有。「2024年２月期配当予想に関するお知らせ」"),
    ("140120240109512320", "配当", "detected"): (True, "配当予想修正=有。「業績予想及び配当予想の修正に関するお知らせ」"),
    ("140120240111513635", "配当", "detected"): (True, "配当予想修正=有。「期末配当予想の修正（増配）に関するお知らせ」"),
    ("140120240111513433", "配当", "detected"): (True, "配当予想修正=有。「通期連結業績予想及び配当予想の修正に関するお知らせ」"),
    ("140120240111513351", "配当", "detected"): (True, "本文で「期末配当金を１株につき４円増額」明示（増配告知）"),
    ("140120240111512393", "配当", "detected"): (True, "配当予想修正=有。「通期連結業績予想および配当予想の修正に関するお知らせ」"),
    ("140120240109512172", "配当", "detected"): (False, "配当の状況欄で2023年11月期実績の記念配当15円内訳記載のみ。独立告知タイトルなし"),
    ("140120231215504124", "配当", "detected"): (True, "決算説明資料で「年間配当額を120円（配当性向43%）に増配」ハイライト（独立告知）"),
    ("140120240105511527", "配当", "detected"): (False, "決算説明資料、配当に関する告知的文言なし"),
    ("140120231213502882", "配当", "detected"): (True, "配当予想修正=有。「通期業績予想および配当予想の修正に関するお知らせ」"),

    # ===== 配当 / missed =====
    ("140120231112586834", "配当", "missed"): (False, "配当予想修正=無、定例決算短信の配当の状況欄のみ"),
    ("140120231216504424", "配当", "missed"): (False, "配当予想修正=無、定例決算短信のみ"),
    ("140120231129596802", "配当", "missed"): (False, "配当予想修正=無、定例決算短信のみ"),
    ("140120231204598641", "配当", "missed"): (False, "配当予想修正=無。記念配当は既発表の実績通り、新規告知なし"),
    ("140120231213502432", "配当", "missed"): (False, "配当予想修正=無、定例決算短信のみ"),
    ("140120231213502767", "配当", "missed"): (False, "配当予想修正=無、定例決算短信のみ"),
    ("140120231120592095", "配当", "missed"): (True, "配当予想修正=有（中間7→10円増額）と本文明示（短信ながら修正あり）"),
    ("140120231218504494", "配当", "missed"): (False, "配当の状況欄のみ、修正無"),
    ("140120231201598214", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231214503270", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231127595203", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231218504625", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231215504287", "配当", "missed"): (False, "米国基準決算短信。中間配当増配は既に実施済みの過去形表現、新規告知なし"),
    ("140120231214503548", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231207500368", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231211501522", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231218504970", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231201598009", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231215504120", "配当", "missed"): (False, "配当予想修正=無"),
    ("140120231208500667", "配当", "missed"): (False, "配当予想修正=無"),

    # ===== 特別損失 / detected =====
    ("140120231220506464", "特別損失", "detected"): (False, "短信PLの減損損失14百万円計上のみ。独立告知なし"),
    ("140120240110512659", "特別損失", "detected"): (True, "「関東第一工場における出火に関するお知らせ（第二報）および業績予想の修正に関するお知らせ」特損1400百万円"),
    ("140120240110512724", "特別損失", "detected"): (True, "決算説明資料で出火関連1,400百万円特損計上見込み記載（イートアンドHDと同時開示）"),
    ("140120240110512616", "特別損失", "detected"): (False, "短信PLの減損損失23百万円のみ。独立告知なし"),
    ("140120240109512449", "特別損失", "detected"): (True, "共同物流事業の減損34億47百万円特損計上、親会社純損失13億円という決算短信レベルの大型独立告知"),
    ("140120240110513120", "特別損失", "detected"): (True, "百貨店譲渡関連損失132,241百万円・子会社譲渡関連損失・事業構造改革費用など特損1,775億円の大型独立告知"),
    ("140120240111513288", "特別損失", "detected"): (False, "短信PLに特損記述なし。サンデーの定例決算短信"),
    ("140120240109512277", "特別損失", "detected"): (True, "「繰延税金負債の取崩し及び業績予想の修正に関するお知らせ」同時公表、減損5億18百万円等"),
    ("140120240105511555", "特別損失", "detected"): (True, "店舗閉鎖損失・新型コロナ関連等の特別損失計上の詳細独立記述（前期比較で整理）"),
    ("140120240109512445", "特別損失", "detected"): (True, "店舗閉鎖+減損3億52百万円、通期予想・配当予想取り下げとの同時告知"),
    ("140120240102510691", "特別損失", "detected"): (False, "資産売却関連の言及はPL表のみ、独立告知タイトルなし"),
    ("140120240111513555", "特別損失", "detected"): (False, "2023年10月公表の業績予想修正の再掲のみ、新規告知なし"),
    ("140120240111513558", "特別損失", "detected"): (True, "決算補足資料で「信託SO対応に伴い、特別損失を計上、これに伴い2024/2期の業績予想を修正」独立告知"),
    ("140120240109512462", "特別損失", "detected"): (True, "「棚卸資産評価損（特別損失）の計上ならびに通期業績予想の修正に関するお知らせ」同時告知"),
    ("140120240111513433", "特別損失", "detected"): (True, "「メタノビ」減損処理+特別損失12百万円計上、「通期連結業績予想及び配当予想の修正に関するお知らせ」"),
    ("140120240111513430", "特別損失", "detected"): (True, "決算説明資料で減損損失12百万円計上を通期修正と合わせて独立告知"),
    ("140120240111513610", "特別損失", "detected"): (False, "短信本文に特別損失の独立告知なし（PL数値のみ）"),
    ("140120231228510507", "特別損失", "detected"): (True, "「特別損失の計上及び2024年２月期通期連結業績予想の修正に関するお知らせ」同時告知、減損192百万円"),
    ("140120240105511456", "特別損失", "detected"): (False, "短信本文に特損独立告知なし（通常の1Q決算短信）"),
    ("140120231226509390", "特別損失", "detected"): (True, "在外子会社資金流出事案による特損計上を独立記述（送金詐欺損失33億円等特損合計108億円）"),

    # ===== 特別損失 / missed =====
    ("140120231112586834", "特別損失", "missed"): (False, "PL表の減損146百万円・特損176百万円のみ。通常決算"),
    ("140120231213502432", "特別損失", "missed"): (False, "PL表の特損数値のみ"),
    ("140120231218505059", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231221506727", "特別損失", "missed"): (False, "決算短信、特損独立告知なし"),
    ("140120231220506301", "特別損失", "missed"): (False, "1Q短信、特損独立告知なし"),
    ("140120231219505666", "特別損失", "missed"): (True, "減損損失88百万円・店舗設備減損の計上を独立記述、通期予想修正絡み"),
    ("140120231208500900", "特別損失", "missed"): (False, "1Q短信、特損独立告知なし"),
    ("140120231219505621", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231214503270", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231127595203", "特別損失", "missed"): (False, "PL表の特損3百万円のみ"),
    ("140120231215504124", "特別損失", "missed"): (False, "決算説明資料、特損独立告知なし"),
    ("140120231221506600", "特別損失", "missed"): (False, "決算短信、減損14百万円の記述はあるが特損独立告知タイトルなし"),
    ("140120231214503548", "特別損失", "missed"): (False, "PL表の減損591百万円のみ、独立告知なし"),
    ("140120231221506877", "特別損失", "missed"): (False, "業績予想修正お知らせはあるが、特損は定例数値で独立告知ではない"),
    ("140120231207500368", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231211501522", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231219505493", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231218504970", "特別損失", "missed"): (False, "特損独立告知なし"),
    ("140120231201598009", "特別損失", "missed"): (False, "事業構造改善費用26百万円はPL上の数値のみ、独立告知タイトルなし"),
    ("140120231205599249", "特別損失", "missed"): (False, "PL表の特損数値のみ、独立告知なし"),

    # ===== 特別利益 / detected =====
    ("140120240109512463", "特別利益", "detected"): (True, "資産除去債務消滅益21百万円の特別利益計上を独立記述（セグメント説明）"),
    ("140120240110513120", "特別利益", "detected"): (True, "固定資産売却益・投資有価証券売却益など特利112億円超の大型計上、事業譲渡絡みで独立記述"),
    ("140120240111513723", "特別利益", "detected"): (False, "短信PLのみ、独立告知なし"),
    ("140120240112514232", "特別利益", "detected"): (False, "短信PLのみ、独立告知なし"),
    ("140120240102510691", "特別利益", "detected"): (False, "短信PLの資産売却益数値のみ、独立告知なし"),
    ("140120240110512870", "特別利益", "detected"): (False, "短信PLのみ、独立告知なし"),
    ("140120240109512320", "特別利益", "detected"): (False, "業績予想修正の特利の同時告知はなし、短信PL数値のみ"),
    ("140120240111513865", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120231225508814", "特別利益", "detected"): (False, "短信PLの特利数値のみ、独立告知なし"),
    ("140120240111513805", "特別利益", "detected"): (True, "「連結子会社の異動（株式譲渡）および特別利益の計上に関するお知らせ」関係会社株式売却益1,700百万円"),
    ("140120240111513813", "特別利益", "detected"): (True, "決算説明資料で事業売却関連の特別利益計上（関係会社株式売却益1,700百万円）を独立告知セクションで記述"),
    ("140120240112514483", "特別利益", "detected"): (True, "決算説明資料で「投資有価証券売却、事業譲渡による特別利益を計上」と独立告知"),
    ("140120240112514475", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120240111513610", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120240111513649", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120240115514995", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120240112514792", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120240115515006", "特別利益", "detected"): (False, "決算説明資料、特利の独立告知なし"),
    ("140120240112514022", "特別利益", "detected"): (False, "短信PLのみ"),
    ("140120231219505493", "特別利益", "detected"): (False, "短信PLのみ、子会社化絡みの特利数値のみ"),

    # ===== 特別利益 / missed =====
    ("140120231112586834", "特別利益", "missed"): (False, "PL表の特利19百万円のみ"),
    ("140120231219505552", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231218505059", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231221506727", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231219505666", "特別利益", "missed"): (False, "特利674百万円計上あるがPL上の数値、独立告知なし"),
    ("140120231221507140", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231222507416", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231221506992", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231219505621", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231214503270", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231127595203", "特別利益", "missed"): (False, "短信PL固定資産売却益7百万円のみ"),
    ("140120231215504124", "特別利益", "missed"): (False, "決算説明資料、特利独立告知なし"),
    ("140120231221506600", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231221506877", "特別利益", "missed"): (False, "短信PL特利739百万円のみ、独立告知なし"),
    ("140120231207500368", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231218504970", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231201598009", "特別利益", "missed"): (False, "短信PL情報セキュリティ対策引当金戻入額95百万円のみ"),
    ("140120231222507710", "特別利益", "missed"): (False, "短信PLのみ"),
    ("140120231220506142", "特別利益", "missed"): (False, "文字化けPDFでテキスト抽出困難だが、定例決算短信（京成、連結）で独立告知性は低い"),
    ("140120231205599249", "特別利益", "missed"): (False, "有価証券売却益6,481百万円はPL表のみ、独立告知なし"),
}


def main() -> None:
    with open(IN_PATH, encoding="utf-8") as f:
        samples = json.load(f)

    rows = []
    summary: dict[tuple[str, str], dict[str, int]] = {}

    for d in samples:
        key = (d["doc_id"], d["category"], d["subset"])
        claude_tf, reason = JUDGMENTS.get(key, (None, "NOT JUDGED"))
        # Gemma, Gemini 判定は subset から逆算
        if d["subset"] == "detected":
            gemma = True
            gemini = True
        else:
            gemma = False
            gemini = True
        rows.append({
            "category": d["category"],
            "subset": d["subset"],
            "doc_id": d["doc_id"],
            "ticker": d["ticker"],
            "short_name": d["short_name"],
            "title": d["title"],
            "gemini": gemini,
            "gemma": gemma,
            "claude": claude_tf,
            "reason": reason,
            "pdf_path": d["pdf_path"],
        })

        # 集計
        cs = (d["category"], d["subset"])
        if cs not in summary:
            summary[cs] = {"claude_true": 0, "claude_false": 0, "total": 0}
        summary[cs]["total"] += 1
        if claude_tf is True:
            summary[cs]["claude_true"] += 1
        elif claude_tf is False:
            summary[cs]["claude_false"] += 1

    # CSV保存
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote CSV: {OUT_CSV}")

    # MD生成
    lines = []
    lines.append("# Claude判定結果 (v2 120 PDFs)\n")
    lines.append(f"- 判定日時: 2026-04-16 JST")
    lines.append(f"- 判定主体: Claude Code (Opus 4.6 1M) エージェント")
    lines.append(f"- 入力: `{IN_PATH}`")
    lines.append(f"- 判定ルール: 「開示イベントとしての告知」True / 「PL数値や配当欄数値の記載のみ」False\n")

    lines.append("## カテゴリ別集計\n")
    lines.append("| カテゴリ | セット | Gemma | Gemini | Claude=T | Claude=F | 勝者 | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for (cat, sub), s in summary.items():
        if sub == "detected":
            gm, gn = "T", "T"
            # 両モデル正解率 = claude_true / total
            winner = "両者正解" if s["claude_true"] >= s["claude_false"] else "両者過剰検知"
            note = f"Claude=T率 {s['claude_true']}/{s['total']} → 両者正解率"
        else:
            gm, gn = "F", "T"
            # Claude=T → Gemini正解, Claude=F → Gemma正解
            winner = "Gemini勝ち" if s["claude_true"] > s["claude_false"] else "Gemma勝ち"
            note = f"Gemini正解 {s['claude_true']}, Gemma正解 {s['claude_false']}"
        lines.append(f"| {cat} | {sub} | {gm} | {gn} | {s['claude_true']} | {s['claude_false']} | {winner} | {note} |")
    lines.append("")

    # 総合集計: Gemini vs Gemma 勝数
    gemini_win = 0
    gemma_win = 0
    both_correct = 0
    both_wrong = 0
    for r in rows:
        if r["claude"] is None:
            continue
        if r["subset"] == "detected":
            if r["claude"]:
                both_correct += 1
            else:
                both_wrong += 1
        else:  # missed: gemma=F, gemini=T
            if r["claude"]:
                gemini_win += 1
            else:
                gemma_win += 1

    lines.append("## Gemini vs Gemma 直接比較 (missed セットのみ)\n")
    lines.append(f"- **Gemini 正解 (Gemmaが漏らした)**: {gemini_win} 件")
    lines.append(f"- **Gemma 正解 (Geminiが過剰検知)**: {gemma_win} 件")
    lines.append(f"- Gemini勝率: {gemini_win / (gemini_win + gemma_win) * 100:.1f}%\n")

    lines.append("## detected セット共通正解率 (両モデル一致時の妥当性)\n")
    lines.append(f"- **両者正解**: {both_correct} 件")
    lines.append(f"- **両者過剰検知（Claude=F）**: {both_wrong} 件")
    lines.append(f"- 共通正解率: {both_correct / (both_correct + both_wrong) * 100:.1f}%\n")

    for (cat, sub) in [("配当","detected"),("配当","missed"),("特別損失","detected"),("特別損失","missed"),("特別利益","detected"),("特別利益","missed")]:
        lines.append(f"\n## {cat} / {sub}\n")
        lines.append("| # | doc_id | ticker | short_name | title | Gemini | Gemma | Claude | 勝者 | 理由 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        i = 1
        for r in rows:
            if r["category"] != cat or r["subset"] != sub:
                continue
            # 勝者計算
            if r["claude"] is None:
                winner = "?"
            elif sub == "detected":
                winner = "両者正解" if r["claude"] else "両者過剰"
            else:
                winner = "Gemini" if r["claude"] else "Gemma"
            c = "T" if r["claude"] else "F" if r["claude"] is False else "?"
            title_short = r["title"][:30].replace("|", "/")
            reason_short = r["reason"][:70].replace("|", "/")
            lines.append(f"| {i} | {r['doc_id']} | {r['ticker']} | {r['short_name']} | {title_short} | T | {'T' if r['gemma'] else 'F'} | {c} | {winner} | {reason_short} |")
            i += 1
    lines.append("")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote MD: {OUT_MD}")

    # コンソールサマリー
    print("\n=== Summary ===")
    print(f"Gemini vs Gemma (missed): Gemini正解 {gemini_win} vs Gemma正解 {gemma_win}")
    print(f"両モデル共通 (detected): 両者正解 {both_correct} vs 両者過剰検知 {both_wrong}")

    # LINE通知用サマリーを返す
    summary_text = (
        f"配当 detected: Gemma T + Gemini T 中、Claude正解 {summary[('配当','detected')]['claude_true']} / 過剰 {summary[('配当','detected')]['claude_false']}\n"
        f"配当 missed: Gemini正解 {summary[('配当','missed')]['claude_true']} / Gemma正解 {summary[('配当','missed')]['claude_false']}\n"
        f"特損 detected: 両者正解 {summary[('特別損失','detected')]['claude_true']} / 両者過剰 {summary[('特別損失','detected')]['claude_false']}\n"
        f"特損 missed: Gemini正解 {summary[('特別損失','missed')]['claude_true']} / Gemma正解 {summary[('特別損失','missed')]['claude_false']}\n"
        f"特利 detected: 両者正解 {summary[('特別利益','detected')]['claude_true']} / 両者過剰 {summary[('特別利益','detected')]['claude_false']}\n"
        f"特利 missed: Gemini正解 {summary[('特別利益','missed')]['claude_true']} / Gemma正解 {summary[('特別利益','missed')]['claude_false']}\n"
        f"\n結論: missed合計 Gemini {gemini_win} vs Gemma {gemma_win} → "
        f"{'Gemini勝利' if gemini_win > gemma_win else 'Gemma勝利' if gemma_win > gemini_win else '同点'}\n"
    )
    print("\n=== LINE Summary ===")
    print(summary_text)
    return summary_text


if __name__ == "__main__":
    main()
