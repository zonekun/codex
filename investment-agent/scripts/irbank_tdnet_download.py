"""
irbank.net 経由での TDnet 適時開示 PDF 一括ダウンロード（過去データ用）

使用ケース:
  TDnet 公式サイトには PDF 保持期間（30〜90日）があるため、過去の PDF は 404 になる。
  irbank.net は TDnet の PDF を自社 CDN (f.irbank.net) に保持しているため、
  過去分（少なくとも 2014年〜）も取得可能。

データソース:
  - メタデータ: yanoshin 非公式 API
  - PDF:        https://f.irbank.net/pdf/YYYYMMDD/{doc_id}.pdf

Usage:
    PYTHONUTF8=1 python scripts/irbank_tdnet_download.py --from 20250101 --to 20250131
    PYTHONUTF8=1 python scripts/irbank_tdnet_download.py --from 20250101 --to 20250131 --save-dir /path/to/dir

File destination:
    [local]  <save_dir>/<証券コード4桁>/<filename>.pdf
    [GCS]    gs://stock_data_1930932/tdnet/<証券コード4桁>/<filename>.pdf  ※既存 tdnet/ と同じ場所

Rate limit:
    yanoshin API: 2秒間隔
    PDF DL:       3秒間隔
    （ban 対策のため変更しないこと）

Ban 対策:
    UA ローテーション: リクエストごとに UA と curl_cffi impersonate プロファイルをランダム選択
    IP ローテーション: 環境変数 IRBANK_PROXIES にカンマ区切りでプロキシURLを指定するとラウンドロビン
                      例: IRBANK_PROXIES=http://user:pass@proxy1:8080,http://user:pass@proxy2:8080
                      未設定の場合はダイレクト接続
"""

import argparse
import csv
import io
import itertools
import os
import random
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests as curl_requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

JST = timezone(timedelta(hours=+9), "JST")

# ============================================================
# 設定
# ============================================================
SAVE_DIR   = Path("C:/Users/zonekun/Dropbox/stock/script/tdnet")  # local 専用
GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "tdnet"   # 既存の tdnet/ と同じ場所に保存（日付でファイル名衝突は起きない）

YANOSHIN_BASE_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/"
IRBANK_PDF_BASE   = "https://f.irbank.net/pdf/"

PAGE_INTERVAL     = 2.0   # yanoshin API 呼び出し間隔（秒）: ban 対策
DOWNLOAD_INTERVAL = 3.0   # PDF ダウンロード間隔（秒）: ban 対策
REQUEST_TIMEOUT   = 45    # HTTP タイムアウト（秒）

# ============================================================
# UA ローテーション（curl_cffi impersonate と対応させる）
# ============================================================
# (User-Agent文字列, curl_cffi impersonateプロファイル) のペアリスト
_UA_POOL: list[tuple[str, str]] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        "chrome124",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",
        "chrome120",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/116.0.0.0 Safari/537.36",
        "chrome116",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
        "chrome124",
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15",
        "safari17_0",
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
        "chrome124",  # curl_cffi に Firefox プロファイルがないため Chrome で代替
    ),
]

# ============================================================
# プロキシローテーション
# 環境変数 IRBANK_PROXIES にカンマ区切りで指定:
#   例: http://user:pass@proxy1:8080,http://user:pass@proxy2:8080
# 未設定またはプロキシなしの場合はダイレクト接続
# ============================================================
def _load_proxies() -> list[dict]:
    """環境変数からプロキシリストを読み込む."""
    raw = os.environ.get("IRBANK_PROXIES", "").strip()
    if not raw:
        return []
    result = []
    for entry in raw.split(","):
        entry = entry.strip()
        if entry:
            result.append({"https": entry, "http": entry})
    return result

_PROXY_LIST: list[dict] = _load_proxies()
_proxy_cycle = itertools.cycle(_PROXY_LIST) if _PROXY_LIST else None


def _next_request_kwargs() -> dict:
    """リクエストごとに UA・impersonate・プロキシをローテーションして返す."""
    ua, impersonate = random.choice(_UA_POOL)
    kwargs: dict = {
        "headers":     {"User-Agent": ua},
        "impersonate": impersonate,
    }
    if _proxy_cycle is not None:
        kwargs["proxies"] = next(_proxy_cycle)
    return kwargs


# ============================================================
# 実行環境の自動判別
# ============================================================
def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "local" | "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME: str = detect_runtime()


# ============================================================
# カテゴリ分類（tdnet_download.py と同じロジック）
# ============================================================

_SKIP_B_CATEGORIES: set[str] = {
    "役員・人事",
    "資金調達（社債・借入）",
    "株主総会",
    "資産取得（不動産）",
    "暗号資産",
    "訂正",
    "開示事項の経過・変更",
    "子会社からの配当受領",
    "格付",
}


def _is_reit(item: dict) -> bool:
    name = item.get("company_name", "")
    return bool(re.search(r'リート|投資法人|REIT', name))


def classify(title: str, item: dict) -> tuple[str, str]:
    """タイトルとアイテム情報からカテゴリと優先度を返す.

    Returns:
        (category, priority)  priority: S=最重要 / A=重要 / B=参考 / C=不要
    """
    t = title

    # ===== S: 最重要 =====
    if re.search(r'決算短信', t):                                                     return ("決算短信", "S")
    if re.search(r'上場廃止', t):                                                     return ("上場廃止", "S")
    if re.search(r'事業.*継続性|継続企業|ゴーイング', t):                                return ("継続企業疑義(GC)", "S")
    if re.search(r'公開買付|TOB|MBO', t):                                             return ("TOB・MBO", "S")
    if re.search(r'大規模買付.*対応|買収防衛|ポイズンピル', t):                           return ("買収防衛策", "S")
    if re.search(r'業績.*修正|修正.*業績|業績予想.*修正|業績.*下方|業績.*上方', t):       return ("業績修正", "S")
    if re.search(r'特別損失.*計上.*業績|特別利益.*計上.*業績|減損.*業績', t):            return ("業績修正", "S")

    # ===== A: 重要 =====
    if re.search(r'決算補足|決算説明|決算ハイライト|IR説明|投資家.*説明|決算.*説明会', t): return ("決算説明資料", "A")
    if re.search(r'業績予想|利益予想|売上予想|業績.*予想.*開示|業績.*予想.*変更|前年比速報', t): return ("業績予想", "A")
    if re.search(r'第三者割当|公募増資|新株式.*発行', t):                               return ("第三者割当・公募増資", "A")
    if re.search(r'新株予約権.*発行|ワラント|MSワラント|ライツ', t):                     return ("新株予約権発行", "A")
    if re.search(r'転換社債|CB.*発行|ユーロ円建.*転換社債', t):                         return ("転換社債(CB)発行", "A")
    if re.search(r'株式分割|株式併合', t):                                             return ("株式分割・併合", "A")
    if re.search(r'自己株式.*取得|自社株.*取得|自己株式立会外|ToSTNeT|ＴｏＳＴＮｅＴ', t): return ("自己株式取得", "A")
    if re.search(r'自己株式.*消却|自社株.*消却', t):                                   return ("自己株式消却", "A")
    if re.search(r'配当.*増額|増配|特別配当|記念配当|配当.*復活|配当.*廃止|配当.*減額|減配|配当.*変更|配当支払.*変更', t): return ("配当変更（増減配）", "A")
    if re.search(r'剰余金の配当|剰余金配当.*確定|配当に関するお知らせ|配当.*確定|資本剰余金.*配当|純資産減少割合.*確定', t): return ("配当", "A")
    if re.search(r'分配金', t):                                                       return ("分配金", "A")
    if re.search(r'合併|株式交換|株式移転|吸収分割|新設分割|会社分割', t):               return ("合併・組織再編", "A")
    if re.search(r'特定子会社.*異動', t):                                              return ("子会社化・買収", "A")
    if re.search(r'買収|完全子会社化|子会社.*株式.*取得|株式.*取得.*子会社', t):         return ("子会社化・買収", "A")
    if re.search(r'特別転進|早期退職|希望退職|人員削減|リストラ', t):                    return ("リストラ・希望退職", "A")
    if re.search(r'特別利益', t):                                                      return ("特別利益", "A")
    if re.search(r'特別損失|減損損失', t):                                              return ("特別損失", "A")
    if re.search(r'火災|爆発|事故.*発生|災害', t):                                    return ("インシデント（災害・事故）", "A")
    if re.search(r'サイバー|不正アクセス|情報漏洩|システム.*障害', t):                   return ("インシデント（セキュリティ）", "A")
    if re.search(r'不正|横領|粉飾|調査委員会|第三者委員会', t):                         return ("不祥事・社内調査", "A")
    if re.search(r'行政処分|営業停止|課徴金|業務改善命令', t):                          return ("行政処分", "A")
    if re.search(r'立会外分売', t):                                                   return ("立会外分売", "A")
    if re.search(r'売出し|発行価格.*決定|売出価格.*決定', t):                           return ("株式売出し", "A")
    if re.search(r'デット.*エクイティ|債権.*株式化', t):                                return ("DES（債権株式化）", "A")
    if re.search(r'中期経営計画|経営計画|経営方針', t):                                 return ("中期経営計画", "A")
    if re.search(r'事業計画.*成長可能性', t):                                          return ("事業計画（グロース）", "A")
    if re.search(r'仮差押|差押|訴訟|判決|和解|調停', t):                               return ("訴訟・法的", "A")
    if re.search(r'主要株主.*異動|大株主.*変更|筆頭株主', t):                           return ("主要株主異動", "A")
    if re.search(r'公認会計士.*異動|監査人.*異動|監査法人.*変更', t):                    return ("監査人異動", "A")
    if re.search(r'最高経営責任者|CEO.*異動|代表取締役.*異動|代表取締役.*就任|代表取締役.*退任'
                 r'|社長.*交代|社長.*就任|代表執行役.*異動|代表執行役.*就任', t):        return ("役員異動（代表クラス）", "A")

    # ===== B: 参考 =====
    if re.search(r'役員|取締役|監査役|人事異動|経営体制|執行体制|経営執行', t):          return ("役員・人事", "B")
    if re.search(r'月次|月次.*売上|月次.*概況|月次.*KPI', t):                          return ("月次開示", "B")
    if re.search(r'稼働率|入居率|出荷台数|販売台数|販売数量|解約率|チャーン|ARPU|ARR|MRR', t):
                                                                                      return ("業績の重要な先行指標", "B")
    if re.search(r'業務提携|資本業務提携|資本提携|合弁.*設立', t):                       return ("提携・協業", "B")
    if re.search(r'受注高|受注残高|受注残', t):                                         return ("受注高/受注残高", "B")
    if re.search(r'受注|契約締結|基本合意|覚書締結', t):                                return ("大型受注・契約", "B")
    if re.search(r'子会社.*設立|孫会社|関係会社.*設立|海外.*設立', t):                  return ("子会社設立", "B")
    if re.search(r'子会社.*持分.*譲渡|子会社.*売却|事業.*譲渡|撤退', t):               return ("事業・子会社売却", "B")
    if re.search(r'販売用不動産.*購入|不動産.*取得|固定資産.*取得|物件.*取得|土地.*取得', t): return ("資産取得（不動産）", "B")
    if re.search(r'不動産.*譲渡|固定資産.*譲渡|物件.*売却|不動産.*売却|信託受益権.*譲渡', t): return ("資産売却（不動産）", "B")
    if re.search(r'株主総会|定時総会|臨時総会', t):                                    return ("株主総会", "B")
    if re.search(r'株主優待|優待制度', t):                                             return ("株主優待", "B")
    if re.search(r'暗号資産|ビットコイン|イーサリアム', t):                              return ("暗号資産", "B")
    if re.search(r'格付', t):                                                         return ("格付", "B")
    if re.search(r'開示事項.*経過|開示事項.*変更|開示事項.*追加|開示.*延期', t):         return ("開示事項の経過・変更", "B")
    if re.search(r'訂正', t):                                                         return ("訂正", "B")
    if re.search(r'連結子会社.*配当金受領|子会社.*配当金受領', t):                       return ("子会社からの配当受領", "B")
    if re.search(r'社債.*発行|普通社債|無担保社債|シンジケートローン|借入.*締結'
                 r'|資金.*借入|当座貸越|コミットメントライン', t):                       return ("資金調達（社債・借入）", "B")

    # ===== C: 不要 =====
    if re.search(r'日々の開示事項', t):                                               return ("ETF/ETN日々開示", "C")
    if re.search(r'ETFの収益分配|ETFの分配金', t):                                    return ("ETF分配金", "C")
    if re.search(r'ETF|ＥＴＦ', t):                                                  return ("ETF関連", "C")
    if _is_reit(item) and re.search(r'資金.*借入|投資法人債|借換|期限前弁済|利率決定|金利決定', t):
                                                                                      return ("J-REIT（資金調達）", "C")
    if re.search(r'株式給付信託|J-ESOP|BBT.*追加拠出|持株会.*譲渡制限|譲渡制限付株式.*報酬'
                 r'|自己株式.*処分.*従業員|従業員.*自己株式.*処分|譲渡制限付株式.*自己株式.*処分', t):
                                                                                      return ("株式報酬制度（ESOP等）", "C")
    if re.search(r'ストックオプション.*内容確定|ストック.*オプション.*確定|新株予約権.*確定'
                 r'|ストック.*オプションに関するお知らせ', t):                           return ("SO行使価額確定", "C")
    if re.search(r'本店.*移転|本社.*移転', t):                                        return ("本店移転", "C")
    if re.search(r'定款.*変更|定款の一部変更', t):                                     return ("定款変更", "C")
    if re.search(r'監査等委員会.*移行|指名委員会.*移行|ガバナンス.*改定|内部統制.*改定', t): return ("ガバナンス変更", "C")
    if re.search(r'資本金.*減少|資本準備金.*減少|資本剰余金.*振替|別途積立金.*取崩', t):  return ("資本構成変更（税務整理）", "C")
    if re.search(r'支配株主', t):                                                     return ("支配株主関連", "C")
    if re.search(r'有価証券報告|四半期報告', t):                                       return ("有価証券報告書", "C")

    return ("その他（未分類）", "B")


def is_needed(category: str, priority: str) -> bool:
    """この開示をダウンロードすべきか判定する."""
    if priority == "C":
        return False
    if category in _SKIP_B_CATEGORIES:
        return False
    return True


# ============================================================
# ファイル名生成（tdnet_download.py と同じ形式）
# ============================================================

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\r\n\t]')
_LEGAL_ENTITY  = re.compile(
    r'株式会社|（株）|㈱|有限会社|（有）|合同会社|合資会社'
    r'|一般社団法人|公益社団法人|一般財団法人|公益財団法人'
)


def _sanitize(s: str, max_len: int = 50) -> str:
    s = _INVALID_CHARS.sub('', s).strip('. ')
    return s[:max_len]


def _clean_company_name(name: str) -> str:
    return _LEGAL_ENTITY.sub('', name).strip()


def make_filename(tdnet: dict, category: str) -> str:
    """開示レコードから保存ファイル名を生成する.

    Format:
        {日付}_{証券コード4桁}_{会社名}_{カテゴリ}_{ファイル概要}_{doc_id}.pdf
    """
    pubdate  = tdnet.get("pubdate", "")
    date_str = pubdate[:10].replace("-", "")
    code4    = (tdnet.get("company_code") or "")[:4]
    name     = _sanitize(_clean_company_name(tdnet.get("company_name") or ""), max_len=20)
    cat      = _sanitize(category, max_len=20)
    title    = _sanitize(tdnet.get("title") or "", max_len=40)
    doc_id   = tdnet.get("id", "")
    parts    = [p for p in [date_str, code4, name, cat, title, doc_id] if p]
    return "_".join(parts) + ".pdf"


# ============================================================
# yanoshin API でメタデータ取得
# ============================================================

def fetch_disclosures_yanoshin(date_from: str, date_to: str) -> list[dict]:
    """yanoshin APIから1日ずつ開示一覧を取得する.

    Args:
        date_from: 開始日 (YYYYMMDD)
        date_to:   終了日 (YYYYMMDD)

    Returns:
        開示レコードのリスト
    """
    d_from  = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to    = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))
    current = d_from
    all_items: list[dict] = []

    while current <= d_to:
        date_str = current.strftime("%Y%m%d")
        url = f"{YANOSHIN_BASE_URL}{date_str}-{date_str}.json?limit=9999"
        try:
            resp = curl_requests.get(
                url, timeout=60, **_next_request_kwargs()
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [WARN] yanoshin API 取得失敗 ({date_str}): {e}")
            current += timedelta(days=1)
            time.sleep(PAGE_INTERVAL)
            continue

        day_items = []
        for item in data.get("items", []):
            t = item.get("Tdnet", {})
            if not t:
                continue
            # rd.php? リダイレクトを解除して実際の PDF URL を取得
            doc = t.get("document_url", "")
            if "rd.php?" in doc:
                t["document_url"] = doc.split("rd.php?", 1)[1]
            # company_code を 4 桁に正規化
            code = (t.get("company_code") or "")
            t["company_code"] = code[:4] if len(code) > 4 else code
            # doc_id: URL のファイル名（拡張子なし）
            fn = (t.get("document_url") or "").rstrip("/").split("/")[-1]
            t["id"] = fn.replace(".pdf", "")
            day_items.append(t)

        if day_items:
            print(f"  [{date_str}] {len(day_items)} 件取得 (yanoshin)")
            all_items.extend(day_items)
        else:
            print(f"  [{date_str}] 0 件（休日/祝日）")

        current += timedelta(days=1)
        time.sleep(PAGE_INTERVAL)

    print(f"[yanoshin] 取得件数合計: {len(all_items)}")
    return all_items


# ============================================================
# irbank CDN から PDF 取得
# ============================================================

def _build_irbank_url(pubdate: str, doc_id: str) -> str:
    """irbank.net の PDF URL を構築する.

    Args:
        pubdate: 開示日時 (YYYY-MM-DD HH:MM:SS 形式)
        doc_id:  ドキュメントID (例: 140120250730523265)

    Returns:
        https://f.irbank.net/pdf/YYYYMMDD/{doc_id}.pdf
    """
    date_str = pubdate[:10].replace("-", "")  # "2025-07-31 16:30:00" → "20250731"
    return f"{IRBANK_PDF_BASE}{date_str}/{doc_id}.pdf"


def fetch_pdf_from_irbank(pubdate: str, doc_id: str) -> tuple[bytes | None, str]:
    """irbank CDN から PDF を取得する.

    pubdate の YYYYMMDD、doc_id 内の日付、±1日 の順で試みる。
    TDnet の開示日（pubdate）と irbank のフォルダ日が合致することが大半だが、
    タイムゾーン・登録日の差異があるため複数候補を試す。

    Returns:
        (bytes | None, used_url): PDF バイト列と使用した URL
    """
    primary_date = pubdate[:10].replace("-", "")
    primary_d    = date(int(primary_date[:4]), int(primary_date[4:6]), int(primary_date[6:8]))

    # 候補: pubdate 当日, +1日, -1日, doc_id 内の日付
    candidates = [
        primary_date,
        (primary_d + timedelta(days=1)).strftime("%Y%m%d"),
        (primary_d - timedelta(days=1)).strftime("%Y%m%d"),
    ]
    # doc_id 内の日付（"1401YYYYMMDDNNNNNN" の 4〜12 文字目）
    if len(doc_id) >= 12:
        docid_date = doc_id[4:12]
        if docid_date not in candidates:
            candidates.append(docid_date)

    for folder_date in candidates:
        url = f"{IRBANK_PDF_BASE}{folder_date}/{doc_id}.pdf"
        try:
            resp = curl_requests.get(
                url, timeout=REQUEST_TIMEOUT, **_next_request_kwargs()
            )
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content, url
            if resp.status_code != 404:
                print(f"  [WARN] irbank 予期しないステータス {resp.status_code}: {url}")
        except Exception as e:
            print(f"  [WARN] irbank 取得例外 ({url}): {e}")

    return None, ""


# ============================================================
# GCS 関連（Cloud Run / Colab 専用）
# ============================================================

_gcs_client = None


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is not None:
        return _gcs_client
    from google.cloud import storage
    if RUNTIME == "colab_personal":
        import json
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds = service_account.Credentials.from_service_account_info(key_info)
        _gcs_client = storage.Client(credentials=creds)
    elif RUNTIME == "local":
        from google.oauth2 import service_account
        key_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json"
        )
        creds = service_account.Credentials.from_service_account_file(key_path)
        _gcs_client = storage.Client(credentials=creds, project="gmailpj-357912")
    else:  # colab_enterprise / cloudrun: ADC
        _gcs_client = storage.Client()
    return _gcs_client


def _gcs_blob_exists(blob_path: str) -> bool:
    return _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).exists()


def _upload_to_gcs(content: bytes, blob_path: str) -> bool:
    try:
        _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).upload_from_string(
            content, content_type="application/pdf"
        )
        return True
    except Exception as e:
        print(f"  [ERROR] GCS アップロード失敗: {blob_path} → {e}")
        return False


def _upload_csv_to_gcs(csv_text: str, blob_path: str) -> None:
    _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).upload_from_string(
        csv_text.encode("utf-8-sig"), content_type="text/csv; charset=utf-8"
    )


# ============================================================
# 再開（レジューム）ログ
# ============================================================

def _resume_log_ref(save_dir: Path, date_from: str, date_to: str) -> "Path | str":
    name = f"_resume_irbank_{date_from}_{date_to}.txt"
    if RUNTIME == "local":
        return save_dir / name
    return f"{GCS_PREFIX}/{name}"


def _load_done_dates(log_ref: "Path | str") -> set[str]:
    try:
        if isinstance(log_ref, Path):
            if not log_ref.exists():
                return set()
            lines = log_ref.read_text(encoding="utf-8").splitlines()
        else:
            blob = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            if not blob.exists():
                return set()
            lines = blob.download_as_text().splitlines()
        return {line[5:].strip() for line in lines if line.startswith("DONE:")}
    except Exception:
        return set()


def _mark_date_done(log_ref: "Path | str", date_str: str) -> None:
    try:
        if isinstance(log_ref, Path):
            log_ref.parent.mkdir(parents=True, exist_ok=True)
            with open(log_ref, "a", encoding="utf-8") as f:
                f.write(f"DONE:{date_str}\n")
        else:
            blob     = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            existing = blob.download_as_text() if blob.exists() else ""
            blob.upload_from_string(existing + f"DONE:{date_str}\n")
    except Exception as e:
        print(f"  [WARN] 再開ログ書き込みエラー: {e}")


def _delete_resume_log(log_ref: "Path | str") -> None:
    try:
        if isinstance(log_ref, Path):
            if log_ref.exists():
                log_ref.unlink()
                print(f"[再開ログ] 削除完了: {log_ref}")
        else:
            blob = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            if blob.exists():
                blob.delete()
                print(f"[再開ログ] 削除完了: gs://{GCS_BUCKET}/{log_ref}")
    except Exception as e:
        print(f"  [WARN] 再開ログ削除エラー: {e}")


# ============================================================
# 引数解析
# ============================================================

def parse_args() -> argparse.Namespace:
    # 全環境共通: 環境変数 IRBANK_DATE_FROM / IRBANK_DATE_TO で日付指定可能
    env_from = os.environ.get("IRBANK_DATE_FROM")
    env_to   = os.environ.get("IRBANK_DATE_TO")

    if RUNTIME in ("colab_personal", "colab_enterprise"):
        # Colab: 環境変数必須（CLI 引数は使えないため）
        if not env_from:
            raise ValueError(
                "Colab 環境では環境変数 IRBANK_DATE_FROM を設定してください\n"
                "例: os.environ['IRBANK_DATE_FROM'] = '20250101'"
            )
        return argparse.Namespace(
            date_from=env_from,
            date_to=env_to or env_from,
            save_dir=SAVE_DIR,
        )

    parser = argparse.ArgumentParser(
        description="irbank.net 経由 TDnet 適時開示 PDF 一括ダウンロード（過去データ用）",
    )
    parser.add_argument("--from", dest="date_from", required=False, default=None,
                        help="開始日 YYYYMMDD（環境変数 IRBANK_DATE_FROM でも指定可）")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD (省略時は --from と同じ日)")
    parser.add_argument("--save-dir", type=Path, default=SAVE_DIR,
                        help="保存先ディレクトリ (local のみ, default: %(default)s)")
    args = parser.parse_args()

    # 環境変数で上書き（Cloud Run の --args 指定がない場合の fallback）
    if env_from and not args.date_from:
        args.date_from = env_from
    if env_to and not args.date_to:
        args.date_to = env_to

    if not args.date_from:
        parser.error("開始日を指定してください: --from YYYYMMDD または環境変数 IRBANK_DATE_FROM")

    return args


# ============================================================
# メイン
# ============================================================

def main() -> None:
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()
    date_label = "不明"

    try:
        args      = parse_args()
        date_from = args.date_from
        date_to   = args.date_to or args.date_from
        save_dir: Path = args.save_dir

        date_label = f"{date_from} ～ {date_to}" if date_from != date_to else date_from

        print("=== irbank.net 経由 TDnet 適時開示ダウンロード ===")
        print(f"実行環境 : {RUNTIME}")
        print(f"期間     : {date_from} ～ {date_to}")
        if RUNTIME == "local":
            print(f"保存先   : {save_dir}")
        else:
            print(f"保存先   : gs://{GCS_BUCKET}/{GCS_PREFIX}/")
        print(f"UA pool  : {len(_UA_POOL)} パターン（リクエストごとにランダム選択）")
        if _PROXY_LIST:
            print(f"Proxy    : {len(_PROXY_LIST)} 件（ラウンドロビン）")
        else:
            print("Proxy    : なし（ダイレクト接続）")
        print()

        # 1. yanoshin API でメタデータ取得
        print("[Step 1] yanoshin API からメタデータ取得中...")
        disclosures = fetch_disclosures_yanoshin(date_from, date_to)
        if not disclosures:
            print("対象データがありませんでした。")
            log_cap.stop()
            return

        # 2. ダウンロード実行
        success     = 0
        skip_dup    = 0
        skip_cat    = 0
        skip_resume = 0
        errors      = 0
        not_on_irbank = 0  # irbank に存在しない（404）件数
        index_rows: list[dict] = []

        # 再開ログ
        resume_ref  = _resume_log_ref(save_dir, date_from, date_to)
        done_dates  = _load_done_dates(resume_ref)
        resume_mode = bool(done_dates)
        if resume_mode:
            print(f"\n{'='*50}")
            print(f"[再開モード] 再開ログを検出しました（完了済み {len(done_dates)} 日）")
            print(f"{'='*50}\n")

        # 日付ごとにグループ化
        disclosures_by_date: dict[str, list[dict]] = {}
        for item in disclosures:
            d = (item.get("pubdate") or "")[:10].replace("-", "")
            disclosures_by_date.setdefault(d, []).append(item)

        total    = len(disclosures)
        item_num = 0

        print(f"\n[Step 2] PDF ダウンロード中（全 {total} 件）...")

        for date_str in sorted(disclosures_by_date.keys()):
            day_items = disclosures_by_date[date_str]

            if date_str in done_dates:
                skip_resume += sum(
                    1 for it in day_items
                    if is_needed(*classify(it.get("title", ""), it))
                )
                item_num += len(day_items)
                print(f"[再開] {date_str}: スキップ（完了済み）")
                continue

            for tdnet in day_items:
                item_num += 1
                title   = tdnet.get("title") or ""
                doc_id  = tdnet.get("id") or ""
                pubdate = tdnet.get("pubdate") or ""

                if not doc_id:
                    continue

                # カテゴリ判定
                category, priority = classify(title, tdnet)

                if not is_needed(category, priority):
                    skip_cat += 1
                    print(f"[{item_num}/{total}] スキップ ({priority}/{category}): {title[:50]}")
                    continue

                code4    = (tdnet.get("company_code") or "")[:4]
                filename = make_filename(tdnet, category)

                # インデックス記録（DL 試行前に記録）
                index_rows.append({
                    "id":           doc_id,
                    "pubdate":      pubdate,
                    "company_code": code4,
                    "company_name": tdnet.get("company_name", ""),
                    "category":     category,
                    "priority":     priority,
                    "title":        title,
                    "filename":     filename,
                    "irbank_url":   _build_irbank_url(pubdate, doc_id),
                })

                if RUNTIME == "local":
                    save_path = save_dir / code4 / filename
                    if save_path.exists():
                        print(f"[{item_num}/{total}] スキップ (既存): {filename}")
                        skip_dup += 1
                        continue
                    print(f"[{item_num}/{total}] DL: {filename}")
                    content, used_url = fetch_pdf_from_irbank(pubdate, doc_id)
                    if content:
                        save_path.parent.mkdir(parents=True, exist_ok=True)
                        save_path.write_bytes(content)
                        success += 1
                    else:
                        print(f"  [SKIP] irbank に PDF なし: {doc_id}")
                        not_on_irbank += 1

                else:  # Colab / Cloud Run → GCS
                    blob_path = f"{GCS_PREFIX}/{code4}/{filename}"
                    if _gcs_blob_exists(blob_path):
                        print(f"[{item_num}/{total}] スキップ (既存): {filename}")
                        skip_dup += 1
                        continue
                    print(f"[{item_num}/{total}] DL→GCS: {filename}")
                    content, used_url = fetch_pdf_from_irbank(pubdate, doc_id)
                    if content:
                        if _upload_to_gcs(content, blob_path):
                            success += 1
                        else:
                            errors += 1
                    else:
                        print(f"  [SKIP] irbank に PDF なし: {doc_id}")
                        not_on_irbank += 1

                time.sleep(DOWNLOAD_INTERVAL)

            # 1日分完了
            _mark_date_done(resume_ref, date_str)
            time.sleep(2.0)  # 日付間スリープ

        _delete_resume_log(resume_ref)

        # 3. インデックス CSV を保存
        fieldnames = ["id", "pubdate", "company_code", "company_name",
                      "category", "priority", "title", "filename", "irbank_url"]
        index_name = f"index_irbank_{date_from}_{date_to}.csv"

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(index_rows)
        csv_text = buf.getvalue()

        if RUNTIME == "local":
            save_dir.mkdir(parents=True, exist_ok=True)
            index_path = save_dir / index_name
            with open(index_path, "w", newline="", encoding="utf-8-sig") as f:
                f.write(csv_text)
            index_label = str(index_path)
        else:
            blob_path = f"{GCS_PREFIX}/{index_name}"
            _upload_csv_to_gcs(csv_text, blob_path)
            index_label = f"gs://{GCS_BUCKET}/{blob_path}"

        elapsed = datetime.now(JST) - start_time
        print()
        print("=== 完了 ===")
        print(f"  DL成功               : {success}")
        print(f"  スキップ（既存）       : {skip_dup}")
        print(f"  スキップ（カテゴリ）    : {skip_cat}")
        if skip_resume:
            print(f"  スキップ（再開）      : {skip_resume}")
        print(f"  irbank に PDF なし    : {not_on_irbank}")
        print(f"  GCS アップロードエラー : {errors}")
        print(f"  インデックス           : {index_label}")
        print(f"  実行時間              : {elapsed}")

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        tb_str   = traceback.format_exc()
        print(f"[FATAL] {e}\n{tb_str}", file=sys.stderr)
        send_mail(
            f"[IRBANK-TDNET] エラー {date_label}",
            f"irbank.net 経由 TDnet ダウンロードでエラーが発生しました。\n\n"
            f"対象期間 : {date_label}\n"
            f"エラー   : {e}\n\n"
            f"--- スタックトレース ---\n{tb_str}",
            attachment_text=log_text,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
