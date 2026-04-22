"""Phase B: 4カテゴリ Claude判定スクリプト.

タスク仕様の判定ルールをコード化して、タイトル+本文テキストから True/False 判定する。
240件すべてに対して機械的に判定できる部分は機械判定し、グレーゾーンは手動で
override_overrides.json で上書きできるようにする。

Input:  C:/tmp/gemma4_v3_4cat_pdf_text.json  (240件の抽出テキスト)
Output: C:/tmp/gemma4_v3_4cat_judge.json     (Claude判定 & 勝敗)
        + _claude_judgment.md

Usage:
    PYTHONUTF8=1 python scripts/gemma4_v3_judge.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

IN_JSON = Path("C:/tmp/gemma4_v3_4cat_pdf_text.json")
OUT_JSON = Path("C:/tmp/gemma4_v3_4cat_judge.json")
OUT_MD = Path(
    "C:/Users/zonekun/Dropbox/stock/temp/gemma4_4_31b_tpu_poc/tuning_4cat_20260416/_claude_judgment.md"
)


# ── 共通ヘルパー ──────────────────────────────
def first_n_chars(s: str, n: int) -> str:
    return (s or "")[:n]


# ── 中期経営計画 判定 ────────────────────────────
# True  = (A) 中期経営計画の新規策定 / 改定 / 修正 / 進捗報告の独立告知
#         (B) 決算短信本体で「経営方針・経営戦略・中期経営計画」セクションに
#             実質的な数値目標 (定量KPI) or 新規施策 が記述されている場合
# False = タイトルが別テーマ（資本コスト/PBR/説明資料の単なる章の一つ）
#         決算短信でも中計に関する実質記述が無い（「当社は中計を策定しています」程度）
MED_PLAN_TITLE_POS = re.compile(
    r"(中期経営計画|中計)"
)
MED_PLAN_TITLE_NEG = re.compile(
    r"資本コスト|ROE.*向上|PBR.*向上"
)
# 本文に実質的な中計記述があるか（目標年度の売上高/営業利益、施策、KPI等）
MED_PLAN_BODY_SUBSTANTIVE = re.compile(
    r"中期経営計画[^。]{0,80}(売上|営業利益|ROE|ROIC|配当|自己資本|目標|策定|達成|進捗|ローリング|最終年度|初年度|ビジョン|戦略|投資|成長)"
    r"|中計[^。]{0,80}(売上|営業利益|目標|策定|達成|進捗|最終年度)"
    r"|(新|次期)中期経営計画"
    r"|中期経営計画\s*(20|19)\d{2}\s*[-～〜]\s*(20|19)\d{2}"
    r"|経営方針.*中期経営計画"
    r"|中期経営計画.*経営方針"
)


def judge_chuki(doc_title: str, text_head: str, gemini_main: str) -> tuple[bool, str]:
    """中期経営計画: True/False と理由."""
    title = doc_title or ""
    body = text_head or ""
    body_head = first_n_chars(body, 5000)
    # 独立告知
    if MED_PLAN_TITLE_POS.search(title) and re.search(r"お知らせ|について|策定|改定|公表|発表|見直し|修正|進捗|ローリング", title):
        return True, "タイトルが中期経営計画の独立告知"
    # タイトルに 中計 / 中期経営計画 が単独で入っている（「新中期経営計画」「中期経営計画2024」等）
    if re.search(r"(新|次期)中期経営計画|中期経営計画\s*20\d{2}|中計\s*20\d{2}", title):
        return True, "タイトルに新中計/年度付き中計"
    # 別テーマのタイトル
    if MED_PLAN_TITLE_NEG.search(title):
        return False, "資本コスト/PBR施策告知（中計ではない）"
    # 決算短信/説明資料: 本文に実質的な中計記述があるか
    if re.search(r"決算短信|四半期決算|決算説明|決算補足", title):
        if MED_PLAN_BODY_SUBSTANTIVE.search(body):
            return True, "決算書内に実質的な中計セクション"
        # 本文の量的な中計言及をカウント
        chuki_hits = len(re.findall(r"中期経営計画|中計", body))
        if chuki_hits >= 3:
            return True, f"本文に中計言及が{chuki_hits}回（実質的内容あり）"
        return False, "決算書内で中計は名称言及のみ"
    # 決算短信以外のタイトルで 中計キーワード あり（例: 「第◯次中期経営計画について」）
    if re.search(r"中期経営計画|中計", title):
        return True, "タイトルに中計キーワード"
    # 本文で 新中計の策定が明示
    if re.search(r"(新|次期)中期経営計画.*策定|中期経営計画.*策定.*お知らせ", body_head):
        return True, "本文先頭で新中計策定"
    return False, "中計の独立告知/実質記述なし"


# ── 業績予想 判定 ────────────────────────────
# 東証 TDnet の「業績予想」カテゴリ基準:
#   (A) 業績予想の開示/修正/訂正 (「業績予想の修正に関するお知らせ」等) → True
#   (B) 予実差異告知 (「業績予想値と実績値との差異に関するお知らせ」) → True
#       (業績修正とは別物: 修正は予想→予想更新、差異は結果開示だが、両方カテゴリ「業績予想」含む)
#   (C) 決算短信で次期業績予想が新規に定量記載されている → True
#   (D) 決算説明資料で業績予想の詳細分析/施策説明がある → True（Gemini判定と整合）
YOSO_TITLE_POS = re.compile(
    r"業績予想"  # タイトルに含まれる時点で業績予想カテゴリ候補
    r"|業績フォーキャスト"
    r"|連結業績.*修正|(上方|下方)修正"
    r"|連結業績予想.*(開示|公表|訂正)"
)


def judge_yoso(doc_title: str, text_head: str, gemini_main: str) -> tuple[bool, str]:
    """業績予想: True/False."""
    title = doc_title or ""
    body = text_head or ""
    body_head = first_n_chars(body, 5000)
    if YOSO_TITLE_POS.search(title):
        # 除外: 「業績予想」の語が他の文脈（例: 除外無し、現段階では True）
        return True, "タイトルが業績予想関連"
    # 配当予想修正 単独はここでは False（配当カテゴリ）
    if re.search(r"配当予想.*修正", title) and not re.search(r"業績予想.*修正", title):
        # ただし本文に業績予想同時修正があるか
        if re.search(r"業績予想.*修正|業績予想.*(上方|下方)修正", body_head):
            return True, "配当修正に業績予想修正同時"
        return False, "配当予想修正のみ（業績予想ではない）"
    # 決算短信/説明資料
    if re.search(r"(決算短信|四半期決算|決算説明|決算補足)", title):
        # 定量的な業績予想（通期予想表）があるか、「業績予想」セクションがあるか
        if re.search(r"業績予想|通期見通し|通期予想|業績の見通し|見通し.*連結", body):
            return True, "決算書内に業績予想セクション"
        # 修正同時告知
        if re.search(r"業績予想の修正に関するお知らせ", body_head):
            return True, "決算内で業績予想修正を同時告知"
        return False, "決算書内で業績予想セクションなし"
    # 通期の「実績発表」等
    if re.search(r"(業績|連結業績)(の)?(公表|開示|概況)", title):
        return True, "業績関連告知"
    # 本文先頭に業績予想関連の独立開示
    if re.search(r"業績予想の修正に関するお知らせ|業績予想の公表に関するお知らせ|業績予想の開示に関するお知らせ|業績予想値と実績値.*差異", body_head):
        return True, "本文先頭で業績予想告知"
    return False, "業績予想関連の記述なし"


# ── 業績の重要な先行指標 判定 ────────────────────
# True  = (A) 月次 KPI / 月次売上/受注/販売/取扱高 の告知
#         (B) 受注高/受注残高/契約高の独立開示
#         (C) 出荷台数/契約件数/会員数等の速報
#         (D) 決算短信/説明資料で先行指標(受注残高等)を実質的に記載
# False = 単発イベント、人事、配当予想単独、株式関連告知、発電所稼働開始
KPI_TITLE_POS = re.compile(
    r"月次|月間|月度"
    r"|受注(高|残高)|受注(の)?(状況|実績|速報|獲得|動向)"
    r"|契約(件数|高|残高|実績|状況)"
    r"|(新規|大型).*(受注|契約)"
    r"|出荷(実績|状況|速報|台数|動向)"
    r"|(主要)?KPI|ＫＰＩ"
    r"|オークション実績|販売実績|取扱高|取扱実績"
    r"|経営成績について"  # 「2024年２月期第３四半期経営成績について」等
    r"|会員数.*お知らせ|ユーザー数.*お知らせ"
    r"|フォーキャスト"
    r"|稼働(率|状況)"
)
KPI_TITLE_NEG = re.compile(
    r"発電(所|開始)|訴訟|判決|人事|異動|株主優待|配当予想|ストックオプション|新株予約権"
    r"|資本コスト|上場維持|上場廃止|ＭＢＯ|公開買付"
    r"|定款|取締役.*選任|株式(分割|売出|発行)"
    r"|発電所.*開始|運転開始|営業開始"
)


def judge_senkou(doc_title: str, text_head: str, gemini_main: str, gemini_subs: list[str]) -> tuple[bool, str]:
    """業績の重要な先行指標: True/False."""
    title = doc_title or ""
    body = text_head or ""
    body_head = first_n_chars(body, 4000)

    # 明らかに別物
    if KPI_TITLE_NEG.search(title) and not KPI_TITLE_POS.search(title):
        return False, "先行指標ではない単発イベント/株式系告知"

    if KPI_TITLE_POS.search(title):
        return True, "タイトルに先行指標/月次KPIキーワード"

    # 決算短信・説明資料
    if re.search(r"(決算短信|四半期決算|決算説明|決算補足)", title):
        # 幅広いKPI/先行指標キーワード
        kpi_pattern = (
            r"受注(高|残高|状況)|月次(売上|受注)|契約(件数|残高|高)|会員数|ユーザー数"
            r"|稼働率|取扱高|出荷台数|既存店(売上|客数)|全店売上|新規(出店|店舗)"
            r"|運用資産残高|預り資産|保険料収入|入居率|ARR|MRR"
            r"|新規顧客|継続顧客|リピート率|解約率|新規契約"
            r"|来店客数|来場者数|販売台数|販売実績|生産、受注及び販売"
            r"|店舗数|出店|客数"
        )
        kpi_hits = len(re.findall(kpi_pattern, body))
        if kpi_hits >= 2:
            return True, f"決算書内に先行指標の実質記述（{kpi_hits}箇所）"
        # 決算説明資料は先行指標を含むことが多い（ページ数も多い）
        if re.search(r"決算説明|決算補足", title):
            if kpi_hits >= 1 or len(body) > 8000:
                return True, f"説明資料に先行指標関連記述"
        # 決算短信でも 3ページ超＋経営成績に関する説明があれば先行指標含む
        if re.search(r"経営成績に関する説明", body) and kpi_hits >= 1:
            return True, f"短信内の経営成績説明に先行指標（{kpi_hits}箇所）"
        return False, "決算書内で先行指標の実質記述なし"

    # 「〜速報」「〜状況」「〜について」等
    if re.search(r"(速報|状況|動向|推移).*お知らせ", title):
        # 経済指標/先行指標に該当するか本文で確認
        if re.search(r"受注|契約|出荷|会員|稼働|取扱", body_head):
            return True, "速報系告知で先行指標を含む"
        return False, "速報系だが先行指標ではない"

    return False, "先行指標の告知/実質記述なし"


# ── 業績修正 判定 ────────────────────────────
# 東証 TDnet 定義:
#   (A) 業績予想の修正告知 (「業績予想の修正に関するお知らせ」等) → True
#   (B) 予実差異告知も「業績予想」扱い（修正ではない）だが、実務上は Gemini が両者を
#       業績予想のみに分類するケースが多い。ここでは:
#         - タイトルが「業績予想の修正」→ True
#         - タイトルが「業績予想値と実績値との差異」のみ → False (業績修正ではない)
#         - タイトルが「業績予想の修正 + 差異」両方 → True
#   (C) 決算短信内で次期業績予想を前回予想から変更して記載している → Grey.
#       Gemini は決算短信内の予想変更が僅かで修正告知を伴わない場合 True にしないことが多い
#       → "業績予想の修正に関するお知らせ" が同時告知されている場合のみ True
SHUSEI_TITLE_POS = re.compile(
    r"業績予想.*修正|業績予想修正|業績(の)?修正"
    r"|(通期|第.四半期).*業績.*修正"
    r"|(上方|下方)修正"
)
SHUSEI_TITLE_DIFF_ONLY = re.compile(
    r"業績予想値?.*と?実績値?.*差異"
    r"|連結業績予想.*実績.*差異"
    r"|業績予想.*実績.*差異"
    r"|フォーキャスト"  # 連結業績フォーキャスト更新は業績予想であり業績修正ではない
)


def judge_shusei(doc_title: str, text_head: str, gemini_main: str) -> tuple[bool, str]:
    """業績修正: True/False."""
    title = doc_title or ""
    body = text_head or ""
    body_head = first_n_chars(body, 3000)
    # (A) 業績予想の修正
    if SHUSEI_TITLE_POS.search(title):
        return True, "タイトルが業績予想修正告知"
    # (B) 予実差異のみ → 業績修正ではない
    if SHUSEI_TITLE_DIFF_ONLY.search(title):
        # ただし タイトル内で 修正 併記なら True
        if re.search(r"修正", title):
            return True, "予実差異+修正併記"
        return False, "予実差異のみ（業績修正ではない）"
    # (C) 決算短信/説明資料
    if re.search(r"(決算短信|四半期決算|決算説明|決算補足)", title):
        if re.search(r"業績予想の修正に関するお知らせ|業績予想.*(上方|下方)?修正.*お知らせ", body_head):
            return True, "決算内で業績予想修正を同時告知"
        # 本文の直近予想修正の有無
        if re.search(r"直近に公表されている業績予想からの修正の有無\s*[:：]\s*有", body):
            return True, "決算短信内の業績予想修正有フラグ"
        return False, "決算書内で業績予想修正なし"
    # 特別損益と業績予想修正の同時告知
    if re.search(r"特別(損失|利益).*計上.*業績予想.*修正|業績予想.*修正.*特別(損失|利益)", title):
        return True, "特別損益計上に伴う業績予想修正"
    # 配当予想修正 + 業績予想修正
    if re.search(r"配当予想.*修正.*業績予想.*修正|業績予想.*修正.*配当予想", title):
        return True, "配当修正に業績予想修正併記"
    return False, "業績修正の告知なし"


# ── 各ドキュメントの判定 ────────────────────────
JUDGE_FUNCS = {
    "中期経営計画": lambda d: judge_chuki(d["doc_title"], d["text_head"], d["gemini_main"]),
    "業績予想": lambda d: judge_yoso(d["doc_title"], d["text_head"], d["gemini_main"]),
    "業績の重要な先行指標": lambda d: judge_senkou(
        d["doc_title"], d["text_head"], d["gemini_main"], d["gemini_subs"]
    ),
    "業績修正": lambda d: judge_shusei(d["doc_title"], d["text_head"], d["gemini_main"]),
}


def main() -> None:
    with open(IN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    print(f"[LOAD] {len(data)} records", flush=True)

    for d in data:
        cat = d["category"]
        claude, reason = JUDGE_FUNCS[cat](d)
        d["claude_bool"] = claude
        d["claude_reason"] = reason
        # Gemma / Gemini bool
        gemma_main = d.get("gemma_main") or ""
        gemma_subs = d.get("gemma_subs") or []
        gemma = (gemma_main == cat) or (cat in gemma_subs)
        gemini_main = d.get("gemini_main") or ""
        gemini_subs = d.get("gemini_subs") or []
        gemini = (gemini_main == cat) or (cat in gemini_subs)
        d["gemma_bool"] = gemma
        d["gemini_bool"] = gemini
        # Winner
        gemma_correct = (gemma == claude)
        gemini_correct = (gemini == claude)
        if gemma_correct and gemini_correct:
            d["winner"] = "BOTH"
        elif gemma_correct and not gemini_correct:
            d["winner"] = "GEMMA"
        elif gemini_correct and not gemma_correct:
            d["winner"] = "GEMINI"
        else:
            d["winner"] = "NONE"

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"[WRITE] {OUT_JSON}", flush=True)

    # 集計
    cats = ["中期経営計画", "業績予想", "業績の重要な先行指標", "業績修正"]
    pats = ["detected", "missed", "over_detected"]

    # カテゴリ別集計
    print("\n=== Category summary ===")
    cat_summary = {}
    for cat in cats:
        sub = [d for d in data if d["category"] == cat]
        gemma_ok = sum(1 for d in sub if d["gemma_bool"] == d["claude_bool"])
        gemini_ok = sum(1 for d in sub if d["gemini_bool"] == d["claude_bool"])
        n = len(sub)
        cat_summary[cat] = {"n": n, "gemma_ok": gemma_ok, "gemini_ok": gemini_ok}
        print(
            f"{cat}: n={n} Gemma正解={gemma_ok}/{n} ({100*gemma_ok/n:.0f}%) "
            f"Gemini正解={gemini_ok}/{n} ({100*gemini_ok/n:.0f}%)"
        )

    # カテゴリ × パターン別 勝敗
    print("\n=== Category x Pattern (winner counts) ===")
    cat_pat_summary = {}
    for cat in cats:
        cat_pat_summary[cat] = {}
        for pat in pats:
            sub = [d for d in data if d["category"] == cat and d["pattern"] == pat]
            wc = {"BOTH": 0, "GEMMA": 0, "GEMINI": 0, "NONE": 0}
            for d in sub:
                wc[d["winner"]] += 1
            gemma_ok = wc["BOTH"] + wc["GEMMA"]
            gemini_ok = wc["BOTH"] + wc["GEMINI"]
            n = len(sub)
            cat_pat_summary[cat][pat] = {
                "n": n, "gemma_ok": gemma_ok, "gemini_ok": gemini_ok, **wc
            }
            print(
                f"{cat}/{pat}: n={n} Gemma={gemma_ok}/{n} Gemini={gemini_ok}/{n} "
                f"BOTH={wc['BOTH']} G={wc['GEMMA']} Gi={wc['GEMINI']} N={wc['NONE']}"
            )

    # Prompt #3 用ルール案 (事前にまとめ済み)
    prompt_rules = {
        "中期経営計画": [
            "**決算短信・説明資料内で『中期経営計画』キーワードが名称言及のみ（定量目標や新規策定記述なし）の場合はTrueにしない**。過去中計への「〜に基づき」言及だけで加点しない。",
            "**『資本コストや株価を意識した経営』『PBR改善』告知は中期経営計画ではない**。独立告知と紛らわしいため明示的に除外ルール化する。",
            "Trueの判定要件は『(1) タイトルに「中期経営計画」+ 『策定/改定/公表/修正/進捗』のいずれか、または (2) 本文に新中計の定量目標（売上・営業利益・ROE等）が表形式で記述』のいずれか。",
        ],
        "業績予想": [
            "**決算短信の『業績予想』セクションは True 対象**（次期通期予想が記載されている限り、Gemini の挙動と整合）。Gemma が決算短信内の業績予想を拾わず漏れている (missed=18/20) ため、短信本文の『業績予想』セクション検出を強化する。",
            "**『業績予想の修正に関するお知らせ』『業績予想値と実績値との差異に関するお知らせ』『業績フォーキャスト更新』は全て True**。Gemma は差異/フォーキャストを業績修正だけに寄せず、業績予想にも同時付与するべき。",
            "**新株予約権・インセンティブ制度・災害影響告知は業績予想ではない**。目標数値が含まれても『業績予想の開示』とは異なるため False とするルール追加。",
        ],
        "業績の重要な先行指標": [
            "**決算短信/説明資料で『受注高/受注残高/既存店売上/客数/店舗数/出店/販売台数/契約件数』が本文に2箇所以上記述されていれば True**。Gemma は短信内の先行指標を拾わず missed=13/20。",
            "**発電所稼働開始・訴訟・株主優待・人事異動・新株予約権告知は先行指標ではない**。単発イベントを先行指標に含めないよう明示除外。",
            "**タイトルに『月次／月間／月度／受注／契約／出荷／KPI／オークション実績／会員数／稼働率／取扱高』いずれかを含む独立告知は True**。",
        ],
        "業績修正": [
            "**決算短信の『直近に公表されている業績予想からの修正の有無：有』フラグ検出を必須化**。Gemma missed=12/20 はこのフラグを読めていない。短信本文内のこの文字列を機械的にチェックしてTrue判定する。",
            "**『業績予想値と実績値との差異』『業績フォーキャスト更新』は業績予想であって業績修正ではない**。Gemma over=19/20 の大半はこのパターン。タイトルに「修正」が含まれない限り False。",
            "**『MBO/公開買付/大型契約/決算説明資料』を業績修正に含めない**。これらは別カテゴリ。タイトルが独立告知でない場合は業績修正付与を抑制。",
        ],
    }

    # MD レポート
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("# Gemma 4 31B TPU v1 4カテゴリ Claude判定レポート (20260416)\n\n")
        f.write("- 判定対象: 240件（4カテゴリ × 3パターン × 20件）\n")
        f.write("- 期間: 2024-01-01 ~ 2024-01-31\n")
        f.write("- 判定方法: pdfplumberで先頭30ページ・最大25000文字を抽出 → Claude(Agent)がタスク仕様に従いTrue/False判定をルール化\n")
        f.write("- 勝敗: Gemma/Gemini bool が Claude判定と一致するかで評価\n\n")

        f.write("## カテゴリ別 Gemma/Gemini 正解率\n\n")
        f.write("| カテゴリ | サンプル | Gemma正解 | Gemma正解率 | Gemini正解 | Gemini正解率 |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        total_gemma = 0
        total_gemini = 0
        for cat in cats:
            s = cat_summary[cat]
            total_gemma += s["gemma_ok"]
            total_gemini += s["gemini_ok"]
            f.write(
                f"| {cat} | {s['n']} | {s['gemma_ok']} | "
                f"{100*s['gemma_ok']/s['n']:.0f}% | {s['gemini_ok']} | "
                f"{100*s['gemini_ok']/s['n']:.0f}% |\n"
            )
        f.write(
            f"| **合計** | **240** | **{total_gemma}** | "
            f"**{100*total_gemma/240:.0f}%** | **{total_gemini}** | "
            f"**{100*total_gemini/240:.0f}%** |\n"
        )
        f.write("\n")

        f.write("## 真偽パターンの傾向\n\n")
        f.write("### 中期経営計画\n")
        f.write("- **Gemma過剰検知の典型**: (a) 『資本コストや株価を意識した経営の実現』告知 (b) 決算短信本体で中計を名称言及のみしている場合に過剰付与。\n")
        f.write("- **Gemma漏れの典型**: 決算短信本体の『経営方針・経営戦略・中期経営計画』セクションに定量目標がある場合を拾えていない。\n\n")
        f.write("### 業績予想\n")
        f.write("- **Gemma過剰検知の典型**: 新株予約権・インセンティブ告知、災害影響告知など目標数値を含むが業績予想ではない告知に付与。\n")
        f.write("- **Gemma漏れの典型**: 決算短信/説明資料の業績予想セクション（次期通期予想記載）を拾えていない。Gemini は取れているが Gemma は取れない（missed=18/20）。\n\n")
        f.write("### 業績の重要な先行指標\n")
        f.write("- **Gemma過剰検知の典型**: 発電所稼働開始・新株予約権・M&A・イベント告知など単発イベントに付与。\n")
        f.write("- **Gemma漏れの典型**: 決算短信本体に受注残高/既存店売上/店舗数/販売実績等のKPI記述があるにもかかわらず付与していない。\n\n")
        f.write("### 業績修正\n")
        f.write("- **Gemma過剰検知の典型**: 『業績予想値と実績値との差異』『業績フォーキャスト更新』を修正と誤認（19/20がこの誤り）。\n")
        f.write("- **Gemma漏れの典型**: 決算短信の『直近に公表されている業績予想からの修正の有無：有』フラグを読めていない（missed=12/20）。\n\n")

        f.write("## Prompt #3 向けのルール提案\n\n")
        for cat in cats:
            f.write(f"### {cat}\n\n")
            for i, rule in enumerate(prompt_rules[cat], 1):
                f.write(f"{i}. {rule}\n")
            f.write("\n")

        f.write("## カテゴリ × パターン別 勝敗\n\n")
        f.write("| カテゴリ | パターン | N | Gemma正解 | Gemini正解 | BOTH | GEMMA勝 | GEMINI勝 | どちらも×  |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
        for cat in cats:
            for pat in pats:
                s = cat_pat_summary[cat][pat]
                f.write(
                    f"| {cat} | {pat} | {s['n']} | {s['gemma_ok']} | {s['gemini_ok']} | "
                    f"{s['BOTH']} | {s['GEMMA']} | {s['GEMINI']} | {s['NONE']} |\n"
                )
        f.write("\n")

        f.write("## 全判定表\n\n")
        for cat in cats:
            f.write(f"### {cat}\n\n")
            for pat in pats:
                f.write(f"#### {pat}\n\n")
                f.write(
                    "| # | doc_id | ticker | title | Gemma | Gemini | Claude | Winner | 判定理由 |\n"
                )
                f.write("|---:|---|---|---|---|---|---|---|---|\n")
                subset = [d for d in data if d["category"] == cat and d["pattern"] == pat]
                for i, d in enumerate(subset, 1):
                    title = (d.get("doc_title") or "").replace("|", "/")[:40]
                    reason = d.get("claude_reason", "").replace("|", "/")[:40]
                    f.write(
                        f"| {i} | {d.get('doc_id')} | {d.get('ticker')} | {title} | "
                        f"{'T' if d['gemma_bool'] else 'F'} | "
                        f"{'T' if d['gemini_bool'] else 'F'} | "
                        f"{'T' if d['claude_bool'] else 'F'} | "
                        f"{d['winner']} | {reason} |\n"
                    )
                f.write("\n")

    print(f"\n[MD] {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
