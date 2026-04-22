import httpx, re
from pathlib import Path
from collections import Counter

url = "https://webapi.yanoshin.jp/webapi/tdnet/list/20260217-20260224.json?limit=9999"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
resp = httpx.get(url, timeout=60, headers=headers, follow_redirects=True)
items = [item["Tdnet"] for item in resp.json().get("items", []) if "Tdnet" in item]

def get_filename(doc_url):
    if not doc_url: return None
    real = doc_url.split("rd.php?", 1)[1] if "rd.php?" in doc_url else doc_url
    return real.rstrip("/").split("/")[-1]

filename_map = {get_filename(i.get("document_url")): i for i in items if get_filename(i.get("document_url"))}

def is_reit(item: dict) -> bool:
    code = item.get("company_code", "")
    name = item.get("company_name", "")
    return code.startswith("R") or bool(re.search(r'リート|投資法人|REIT', name))

def classify(title: str, item: dict) -> tuple:
    t = title

    if (item.get("url_report_type_summary")
            or item.get("url_report_type_fs_consolidated")
            or item.get("url_report_type_fs_non_consolidated")):
        return ("決算短信", "S")

    # S
    if re.search(r'決算短信', t): return ("決算短信", "S")
    if re.search(r'上場廃止', t): return ("上場廃止", "S")
    if re.search(r'事業.*継続性|継続企業|ゴーイング', t): return ("継続企業疑義(GC)", "S")
    if re.search(r'公開買付|TOB|MBO', t): return ("TOB・MBO", "S")
    if re.search(r'大規模買付.*対応|買収防衛|ポイズンピル', t): return ("買収防衛策", "S")
    if re.search(r'業績.*修正|修正.*業績|業績予想.*修正|業績.*下方|業績.*上方', t): return ("業績修正", "S")
    if re.search(r'特別損失.*計上.*業績|特別利益.*計上.*業績|減損.*業績', t): return ("業績修正", "S")

    # A
    if re.search(r'決算補足|決算説明|決算ハイライト|IR説明|投資家.*説明|決算.*説明会', t): return ("決算説明資料", "A")
    if re.search(r'業績予想|利益予想|売上予想|業績.*予想.*開示|業績.*予想.*変更|前年比速報', t): return ("業績予想", "A")
    if re.search(r'第三者割当|公募増資|新株式.*発行', t): return ("第三者割当・公募増資", "A")
    if re.search(r'新株予約権.*発行|ワラント|MSワラント|ライツ', t): return ("新株予約権発行", "A")
    if re.search(r'転換社債|CB.*発行|ユーロ円建.*転換社債', t): return ("転換社債(CB)発行", "A")
    if re.search(r'株式分割|株式併合', t): return ("株式分割・併合", "A")
    if re.search(r'自己株式.*取得|自社株.*取得|自己株式立会外|ToSTNeT|ＴｏＳＴＮｅＴ', t): return ("自己株式取得", "A")
    if re.search(r'自己株式.*消却|自社株.*消却', t): return ("自己株式消却", "A")
    if re.search(r'配当.*増額|増配|特別配当|記念配当|配当.*復活|配当.*廃止|配当.*減額|減配|配当.*変更|配当支払.*変更', t): return ("配当変更", "A")
    if re.search(r'剰余金の配当|剰余金配当.*確定|配当に関するお知らせ|配当.*確定|資本剰余金.*配当|純資産減少割合.*確定', t): return ("配当", "A")
    if re.search(r'分配金', t): return ("分配金", "A")
    if re.search(r'合併|株式交換|株式移転|吸収分割|新設分割|会社分割', t): return ("合併・組織再編", "A")
    if re.search(r'特定子会社.*異動', t): return ("子会社化・買収", "A")
    if re.search(r'買収|完全子会社化|子会社.*株式.*取得|株式.*取得.*子会社', t): return ("子会社化・買収", "A")
    if re.search(r'特別転進|早期退職|希望退職|人員削減|リストラ', t): return ("リストラ・希望退職", "A")
    if re.search(r'特別損失|特別利益|減損損失', t): return ("特別損益計上", "A")
    if re.search(r'火災|爆発|事故.*発生|災害', t): return ("インシデント（災害・事故）", "A")
    if re.search(r'サイバー|不正アクセス|情報漏洩|システム.*障害', t): return ("インシデント（セキュリティ）", "A")
    if re.search(r'不正|横領|粉飾|調査委員会|第三者委員会', t): return ("不祥事・社内調査", "A")
    if re.search(r'行政処分|営業停止|課徴金|業務改善命令', t): return ("行政処分", "A")
    if re.search(r'立会外分売', t): return ("立会外分売", "A")
    if re.search(r'売出し|発行価格.*決定|売出価格.*決定', t): return ("株式売出し", "A")
    if re.search(r'デット.*エクイティ|債権.*株式化', t): return ("DES（債権株式化）", "A")
    if re.search(r'中期経営計画|経営計画|経営方針', t): return ("中期経営計画", "A")
    if re.search(r'事業計画.*成長可能性', t): return ("事業計画（グロース）", "A")
    if re.search(r'仮差押|差押|訴訟|判決|和解|調停', t): return ("訴訟・法的", "A")
    if re.search(r'主要株主.*異動|大株主.*変更|筆頭株主', t): return ("主要株主異動", "A")
    if re.search(r'公認会計士.*異動|監査人.*異動|監査法人.*変更', t): return ("監査人異動", "A")
    if re.search(r'最高経営責任者|CEO.*異動|代表取締役.*異動|代表取締役.*就任|代表取締役.*退任|社長.*交代|社長.*就任|代表執行役.*異動|代表執行役.*就任', t): return ("役員異動（代表クラス）", "A")

    # B
    if re.search(r'役員|取締役|監査役|人事異動|経営体制|執行体制|経営執行', t): return ("役員・人事", "B")
    if re.search(r'月次|月次.*売上|月次.*概況|月次.*KPI', t): return ("月次開示", "B")
    if re.search(r'業務提携|資本業務提携|資本提携|合弁.*設立', t): return ("提携・協業", "B")
    if re.search(r'受注|契約締結|基本合意|覚書締結', t): return ("受注・契約", "B")
    if re.search(r'子会社.*設立|孫会社|関係会社.*設立|海外.*設立', t): return ("子会社設立", "B")
    if re.search(r'子会社.*持分.*譲渡|子会社.*売却|事業.*譲渡|撤退', t): return ("事業・子会社売却", "B")
    if re.search(r'販売用不動産.*購入|不動産.*取得|固定資産.*取得|物件.*取得|土地.*取得', t): return ("資産取得（不動産）", "B")
    if re.search(r'不動産.*譲渡|固定資産.*譲渡|物件.*売却|不動産.*売却|信託受益権.*譲渡', t): return ("資産売却（不動産）", "B")
    if re.search(r'株主総会|定時総会|臨時総会', t): return ("株主総会", "B")
    if re.search(r'株主優待|優待制度', t): return ("株主優待", "B")
    if re.search(r'暗号資産|ビットコイン|イーサリアム', t): return ("暗号資産", "B")
    if re.search(r'格付', t): return ("格付", "B")
    if re.search(r'開示事項.*経過|開示事項.*変更|開示事項.*追加|開示.*延期', t): return ("開示事項の経過・変更", "B")
    if re.search(r'訂正', t): return ("訂正", "B")
    if re.search(r'連結子会社.*配当金受領|子会社.*配当金受領', t): return ("子会社からの配当受領", "B")
    if re.search(r'社債.*発行|普通社債|無担保社債|シンジケートローン|借入.*締結|資金.*借入|当座貸越|コミットメントライン', t): return ("資金調達（社債・借入）", "B")

    # C
    if re.search(r'日々の開示事項', t): return ("ETF/ETN日々開示", "C")
    if re.search(r'ETFの収益分配|ETFの分配金', t): return ("ETF分配金", "C")
    if re.search(r'ETF|ＥＴＦ', t): return ("ETF関連", "C")
    if is_reit(item) and re.search(r'資金.*借入|投資法人債|借換|期限前弁済|利率決定|金利決定', t): return ("J-REIT（資金調達）", "C")
    if re.search(r'株式給付信託|J-ESOP|BBT.*追加拠出|持株会.*譲渡制限|譲渡制限付株式.*報酬|自己株式.*処分.*従業員|従業員.*自己株式.*処分|譲渡制限付株式.*自己株式.*処分', t): return ("株式報酬制度（ESOP等）", "C")
    if re.search(r'ストックオプション.*内容確定|ストック.*オプション.*確定|新株予約権.*確定|ストック.*オプションに関するお知らせ', t): return ("SO行使価額確定", "C")
    if re.search(r'本店.*移転|本社.*移転', t): return ("本店移転", "C")
    if re.search(r'定款.*変更|定款の一部変更', t): return ("定款変更", "C")
    if re.search(r'監査等委員会.*移行|指名委員会.*移行|ガバナンス.*改定|内部統制.*改定', t): return ("ガバナンス変更", "C")
    if re.search(r'資本金.*減少|資本準備金.*減少|資本剰余金.*振替|別途積立金.*取崩', t): return ("資本構成変更（税務整理）", "C")
    if re.search(r'支配株主', t): return ("支配株主関連", "C")
    if re.search(r'有価証券報告|四半期報告', t): return ("有価証券報告書", "C")

    return ("その他（未分類）", "B")


# --- API全件集計 ---
pri_order = {"S": 0, "A": 1, "B": 2, "C": 3}
api_cats = Counter()
for item in items:
    cat, pri = classify(item["title"], item)
    api_cats[(cat, pri)] += 1

print("=== 最終版分類ロジック ― API全件（1,245件）===\n")
print(f"{'P':<3} {'カテゴリ':<32} {'件数':>5}")
print("-" * 45)
for (cat, pri), cnt in sorted(api_cats.items(), key=lambda x: (pri_order[x[0][1]], -x[1])):
    print(f"  {pri}  {cat:<32} {cnt:>4}件")

# --- DL済みで残存「その他（未分類）」 ---
save_dir = Path("C:/Users/zonekun/Dropbox/stock/script/tdnet")
downloaded = list(save_dir.rglob("*.pdf"))
still_other = []
for pdf in downloaded:
    item = filename_map.get(pdf.name)
    if item:
        cat, pri = classify(item["title"], item)
        if cat == "その他（未分類）":
            still_other.append((item["company_name"], item["title"]))

print(f"\n=== DL済みで残存「その他（未分類）」: {len(still_other)}件 ===")
for company, title in still_other:
    print(f"  [{company}] {title}")

# --- 優先度別サマリ ---
totals = {p: sum(v for (_, pp), v in api_cats.items() if pp == p) for p in "SABC"}
print(f"\n=== 優先度別サマリ ===")
for p, label in [("S","最重要"), ("A","重要  "), ("B","参考  "), ("C","不要  ")]:
    n = totals[p]
    print(f"  {p}（{label}）: {n:>4}件 ({n/len(items)*100:.1f}%)")
