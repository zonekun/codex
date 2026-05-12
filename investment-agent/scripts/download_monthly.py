"""月次開示ファイルをダウンロードするスクリプト。

url_adapter.json（2026-04-20 #A 物理分離で adapter.json からリネーム）を使用して以下のタイプを処理:
  - eir_api         : eIR (eir-parts.net) JSONP API 経由
  - scrape_links    : HTMLページからリンクをスクレイピング
  - html_table      : IRページの HTML をそのまま保存（テーブル変換は build_monthly_extractor.py が担う）
  - disco_quarterly : ディスコ（6146）四半期速報専用

保存先: data/monthlyir/{ticker}_{会社名}/

Cloud Run 並列タスク:
  CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT 環境変数でアダプター一覧を分割。
  各タスクが異なる IP で処理することでレートリミットを回避できる。

使い方:
    PYTHONUTF8=1 python scripts/download_monthly.py
    PYTHONUTF8=1 python scripts/download_monthly.py --tickers 2294 2433
    PYTHONUTF8=1 python scripts/download_monthly.py --dry-run
    PYTHONUTF8=1 python scripts/download_monthly.py --type eir_api
    PYTHONUTF8=1 python scripts/download_monthly.py --type scrape_links
    PYTHONUTF8=1 python scripts/download_monthly.py --exclude-edinet
"""
import argparse
import datetime
import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from google.cloud import storage
from google.oauth2 import service_account

# ==========================================
# 設定
# ==========================================
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
GCS_DOCS = "monthly/docs"
GCS_LOG = "monthly/log"
KEY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "keys", "gcp-service-account.json",
)
RATE_SEC = 1.0
FILE_SLEEP = 0.3

# Cloud Run タスク分割
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None or os.environ.get("CLOUD_RUN_TASK_INDEX") is not None
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", 0))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", 1))

# 保存先: ローカル実行時は C:\tmp\monthlyir（Google Drive 圧迫防止）
# 環境変数 MONTHLY_OUT_DIR で上書き可能
OUT_DIR = (
    Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "monthlyir"
    if IS_CLOUD_RUN
    else Path(os.environ.get("MONTHLY_OUT_DIR", r"C:\tmp\monthlyir"))
)

EIR_BASE = "https://ssl4.eir-parts.net"

DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv|html?)(\?.*)?$", re.IGNORECASE)

# 拡張子なし PDF URL パス (2026-04-24 D1: 3548 Baroque 対応)
# 例: /jp/notice/download/428/7314 → PDF として扱う
# リンクテキストに月次キーワード (NON_MONTHLY除外 + EIR_MONTHLY 一致) が含まれる前提で
# handle_scrape_links 内でのみ PDF 扱いする
DOWNLOAD_PATH_EXT = re.compile(
    r"/download/\d+/\d+/?$"                       # 3548 Baroque 形式
    r"|/media-download/\d+/[0-9a-f]+/PDF/?$"      # 8160 木曽路 形式 (2026-04-24)
    r"|/ir_disp\.php\?ir_no=\d+",                 # 7412 アトム ir_disp.php 直接PDF返却
    re.IGNORECASE,
)


def _is_downloadable(url: str) -> bool:
    """URL が PDF/Excel/CSV/HTML などダウンロード対象か判定する。

    ① .pdf 等の拡張子付き URL → True
    ② /download/<digits>/<digits> 形式 (拡張子なし) → True (2026-04-24: 3548 Baroque 対応)
    ③ /media-download/<digits>/<hex>/PDF/ 形式 (拡張子なし) → True (2026-04-24: 8160 木曽路 対応)
    """
    return bool(DOWNLOAD_EXT.search(url) or DOWNLOAD_PATH_EXT.search(url))


def _guess_ext(url: str, default: str = "pdf") -> str:
    """URL から拡張子を推定する。拡張子なし URL は default ('pdf') を返す。"""
    ext_m = DOWNLOAD_EXT.search(url)
    if ext_m:
        return ext_m.group(1).lower()
    # 拡張子なしパスは PDF 扱い
    if DOWNLOAD_PATH_EXT.search(url):
        return "pdf"
    return default

# 明らかに月次でない文書を除外するキーワード（URL・ファイル名に含まれる場合スキップ）
NON_MONTHLY_RE = re.compile(
    r"midmgtplan|mid.?term|中期経営|integrated.?report|統合報告|annual.?report|有価証券報告"
    r"|株主総会|shareholders.?meeting|一般事業主行動計画|マルチステークホルダー"
    r"|FAX.?order|chainstore.?FAX|食品安全方針|半期報告書|四半期報告書"
    r"|決議通知|prospectus|目論見書",
    re.IGNORECASE,
)

# 月次データと判定するキーワード
EIR_MONTHLY_RE = re.compile(
    r"月次|月度|月分|monthly|マンスリー|売上速報|売上高|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|稼働実績|出荷量|KPI|Net Sales"
    r"|稼働率|入居率|来店|客数|セールス|オペレーション|生産実績|契約件数|新規契約|解約|ユーザー数|会員数|AUM|預かり",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ==========================================
# SSL パッチ（Windows ローカル GCS 用）
# ==========================================

def _apply_ssl_patch() -> None:
    import urllib3
    from requests.adapters import HTTPAdapter
    urllib3.disable_warnings()

    class _NoVerify(HTTPAdapter):
        def send(self, req, **kw):
            kw["verify"] = False
            return super().send(req, **kw)

    _orig = requests.Session.__init__

    def _patched(self, *a, **kw):
        _orig(self, *a, **kw)
        self.mount("https://", _NoVerify())
        self.verify = False

    requests.Session.__init__ = _patched


_apply_ssl_patch()


# ==========================================
# GCS ヘルパー
# ==========================================

def _get_gcs_client():
    if IS_CLOUD_RUN and not os.path.exists(KEY_FILE):
        # Cloud Run: ADC (Application Default Credentials) を使用
        return storage.Client(project="gmailpj-357912")
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project="gmailpj-357912", credentials=creds)


def _load_active_adapters(
    bucket,
    type_filter: str | None = None,
    exclude_edinet: bool = False,
) -> list[dict]:
    """GCS から status=active のアダプターを全件取得する。"""
    blobs = list(bucket.list_blobs(prefix=f"{GCS_META}/"))
    # #A 物理分離 (2026-04-20): adapter.json → url_adapter.json に移行済
    adapter_blobs = [b for b in blobs if b.name.endswith("/url_adapter.json")]
    adapters = []
    skipped_edinet = 0
    for blob in adapter_blobs:
        try:
            adapter = json.loads(blob.download_as_text())
        except Exception as e:
            logger.warning("adapter読み込み失敗: %s - %s", blob.name, e)
            continue
        ticker = adapter.get("ticker", "(unknown)")
        if adapter.get("status") != "active":
            logger.debug("非activeスキップ: ticker=%s status=%s", ticker, adapter.get("status"))
            continue
        if type_filter and adapter.get("type") != type_filter:
            continue
        # EDINET 除外（source="edinet" または adapter_note に "EDINET" を含む）
        if exclude_edinet:
            src = adapter.get("source", "").lower()
            note = adapter.get("adapter_note", "").lower()
            if "edinet" in src or "edinet" in note:
                skipped_edinet += 1
                logger.debug("EDINET除外: %s %s", adapter.get("ticker"), adapter.get("company_name"))
                continue
        adapters.append(adapter)
    logger.info(
        "アダプター読み込み: %d社 (type_filter=%s, EDINET除外=%d社)",
        len(adapters), type_filter or "全", skipped_edinet,
    )
    return adapters


def _save_run_log(bucket, results: list[dict], dry_run: bool) -> None:
    """ダウンロード結果サマリーを GCS に保存する。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    task_suffix = f"_task{TASK_INDEX}" if TASK_COUNT > 1 else ""
    dry_suffix = "_dryrun" if dry_run else ""
    gcs_path = f"{GCS_LOG}/{ts}{task_suffix}{dry_suffix}.json"
    log_data = {
        "run_at": datetime.datetime.now().isoformat(),
        "dry_run": dry_run,
        "task_index": TASK_INDEX,
        "task_count": TASK_COUNT,
        "is_cloud_run": IS_CLOUD_RUN,
        "total_companies": len(results),
        "total_downloaded": sum(r.get("downloaded", 0) for r in results),
        "total_skipped": sum(r.get("skipped", 0) for r in results),
        "total_failed": sum(r.get("failed", 0) for r in results),
        "results": results,
    }
    try:
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        logger.info("実行ログをGCSに保存: gs://%s/%s", GCS_BUCKET, gcs_path)
    except Exception as e:
        logger.warning("GCSログ保存失敗: %s", e)


# ==========================================
# 共通ユーティリティ
# ==========================================

def _safe_filename(text: str, maxlen: int = 50) -> str:
    """ファイル名に使えない文字を除去して短縮する。"""
    text = re.sub(r'[\\/:*?"<>|　\s]+', "_", text.strip())
    return text[:maxlen]


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]


_YYYYMM_MIN_YEAR = 2015
_YYYYMM_MAX_YEAR = datetime.datetime.now().year + 1


def _validate_yyyymm(year: int, month: int) -> bool:
    return _YYYYMM_MIN_YEAR <= year <= _YYYYMM_MAX_YEAR and 1 <= month <= 12


def _extract_yyyymm_regex(text: str, url: str = "") -> str:
    """regex で YYYYMM を抽出。年月範囲 validation で 2029-04 等の不正値を弾く。
    取れない場合は '000000'。
    """
    # 「YYYY年M月」または「YYYY年MM月」
    for m in re.finditer(r'(20\d{2})年\s*(\d{1,2})月', text):
        y, mo = int(m.group(1)), int(m.group(2))
        if _validate_yyyymm(y, mo):
            return f"{y}{mo:02d}"
    # URL/text 中の 6桁 YYYYMM
    for src in (url, text):
        for m in re.finditer(r'(20\d{2})(\d{2})', src):
            y, mo = int(m.group(1)), int(m.group(2))
            if _validate_yyyymm(y, mo):
                return f"{y}{mo:02d}"
    # YYYY/MM or YYYY-MM
    for m in re.finditer(r'(20\d{2})[/\-](\d{2})', text + " " + url):
        y, mo = int(m.group(1)), int(m.group(2))
        if _validate_yyyymm(y, mo):
            return f"{y}{mo:02d}"
    return "000000"


# Gemini fallback (regex 失敗時のみ呼ぶ)
_GEMINI_MODEL_NAME = "gemini-3.1-flash-lite-preview"
_GEMINI_TIMEOUT_SEC = 90.0
_GEMINI_PROMPT_BASE = """あなたは日本株月次開示PDFのメタ情報から「対象データ月（月度）」を特定するアシスタント。
タイトルとURLから、PDFがカバーするデータの年月（対象月度）を YYYY-MM 形式で返す。

【会計期表記の変換ルール（重要）】
タイトルに頻出する「YYYY年X月期 Y月度」形式は、YYYY=会計期末年、X=会計期末月、Y=月度。
→ Y ≤ X: 対象月度 = (YYYY)-Y
→ Y > X: 対象月度 = (YYYY-1)-Y

例:
 - 「2027年2月期3月度」→ FY末=2026-02 → Y(3)>X(2) → 2026-03
 - 「2026年7月期12月度」→ FY末=2026-07 → Y(12)>X(7) → 2025-12
 - 「2026年3月期2月度」→ FY末=2026-03 → Y(2)≤X(3) → 2026-02
 - 「2026年1月期5月度」→ FY末=2026-01 → Y(5)>X(1) → 2025-05

【その他】
- 和暦は西暦に変換: 「平成30年1月度」→ 2018-01、「令和8年3月度」→ 2026-03
- 「YYYY年M月」単独（FY期表記なし）はそのまま YYYY-MM として採用
- URL内の長い数字列（DOC_ID `140120260409500512` やハッシュ）から抽出しない
- 提出日や発行日からの推測はしない（対象月度のみ）
- 判定不能なら "UNKNOWN" を返す

【出力】JSON のみ:
{"year_month": "YYYY-MM"}  または  {"year_month": "UNKNOWN"}

【入力】
タイトル: {title}
URL: {url}
"""


def _call_gemini_yyyymm(text: str, url: str) -> str | None:
    """Gemini で title+URL から年月を判定。"""
    prompt = _GEMINI_PROMPT_BASE.replace("{title}", text or "(なし)").replace("{url}", url or "(なし)")
    try:
        from google import genai
        from google.genai import types
        if IS_CLOUD_RUN:
            client = genai.Client(vertexai=True, project="gmailpj-357912", location="global")
        else:
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                return None
            client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=_GEMINI_MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0,
            ),
        )
        txt = resp.text
        data = json.loads(txt)
        ym = str(data.get("year_month", ""))
        if ym == "UNKNOWN":
            return None
        m = re.match(r'^(\d{4})-(\d{2})$', ym)
        if not m:
            return None
        y, mo = int(m.group(1)), int(m.group(2))
        if not _validate_yyyymm(y, mo):
            return None
        return f"{y}{mo:02d}"
    except Exception as e:
        logger.debug("Gemini yyyymm 失敗: %s", e)
        return None


# Gemini 呼び出しは同一 (text, url) で重複しがち → in-memory cache
import functools


@functools.lru_cache(maxsize=2048)
def _extract_yyyymm_gemini_cached(text: str, url: str) -> str:
    """Gemini fallback。失敗時 '000000'."""
    result = _call_gemini_yyyymm(text, url)
    return result if result else "000000"


def _extract_yyyymm(text: str, url: str = "") -> str:
    """テキスト/URL から YYYYMM 抽出。
    優先順:
      1. regex + 年月範囲 validation
      2. Gemini 3.1 Flash Lite preview fallback (lru_cache)
      3. '000000'
    """
    result = _extract_yyyymm_regex(text, url)
    if result != "000000":
        return result
    return _extract_yyyymm_gemini_cached(text or "", url or "")


def _classify_error(e: Exception | None = None, status_code: int | None = None) -> str:
    """例外またはHTTPステータスコードからエラー種別を判定する。"""
    if status_code is not None:
        if 400 <= status_code < 500:
            return "http_4xx"
        if 500 <= status_code < 600:
            return "http_5xx"
    if e is None:
        return "unknown"
    err_name = type(e).__name__.lower()
    err_msg = str(e).lower()
    if "timeout" in err_name or "timeout" in err_msg:
        return "timeout"
    if "connection" in err_name:
        return "connection"
    if "dns" in err_name or "name resolution" in err_msg or "nodename nor servname" in err_msg:
        return "dns"
    if "ssl" in err_name:
        return "ssl"
    if "importerror" in err_name or "modulenotfounderror" in err_name:
        return "import_error"
    return "unknown"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _already_local(company_dir: Path, url_hash_str: str) -> bool:
    """同一 URL ハッシュのファイルが既にローカルに存在するか確認。"""
    return any(url_hash_str in f.name for f in company_dir.iterdir() if f.is_file())


def _get_ticker_gcs_hashes(bucket, ticker: str) -> set[str]:
    """GCS上の既存ファイルのURLハッシュセット（8文字）を返す。スキップ判定用。"""
    prefix = f"{GCS_DOCS}/{ticker}/"
    try:
        hashes = set()
        for blob in bucket.list_blobs(prefix=prefix):
            name = blob.name.rsplit("/", 1)[-1]
            # filename: YYYYMM_ticker_title_HASH.ext → hash は最後の '_' 以降かつ '.' 以前
            base = name.rsplit(".", 1)[0]
            h = base.rsplit("_", 1)[-1]
            if len(h) == 8:
                hashes.add(h)
        return hashes
    except Exception as e:
        logger.warning("GCS hash list 失敗 ticker=%s: %s", ticker, e)
        return set()


def _ext_content_type(ext: str) -> str:
    return {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "csv": "text/csv",
        "html": "text/html",
    }.get(ext.lower(), "application/octet-stream")


def _upload_to_gcs(bucket, ticker: str, filename: str, data: bytes, ext: str) -> None:
    """ファイルをGCSの monthly/docs/{ticker}/{filename} にアップロード。"""
    gcs_path = f"{GCS_DOCS}/{ticker}/{filename}"
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(data, content_type=_ext_content_type(ext))
    logger.debug("GCS保存: gs://%s/%s", GCS_BUCKET, gcs_path)


# ==========================================
# eIR API ハンドラー
# ==========================================

def _fetch_eir_page(eir_code: str, page: int, category: str = "announcement") -> list[dict]:
    """eIR の {category}_{page}.js を取得してアイテムリストを返す。"""
    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.error("curl_cffi が未インストール。`uv add curl-cffi` を実行してください。")
        return []

    for path_seg in ["eir", "EIR"]:
        url = f"{EIR_BASE}/V4Public/{path_seg}/{eir_code}/ja/{category}/{category}_{page}.js"
        try:
            resp = cf_requests.get(url, impersonate="chrome120", timeout=15)
            if resp.status_code != 200:
                continue
            # curl_cffi の resp.text はエンコーディングが化ける場合があるため
            # content.decode('utf-8') を使う
            try:
                # utf-8-sig: BOM (EF BB BF) を自動除去
                text = resp.content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = resp.content.decode("shift-jis", errors="replace")
            m = re.search(
                r"eolparts_\w+\s*\(\s*(\{.*\})\s*\)",
                text,
                re.DOTALL,
            )
            if not m:
                continue
            data = json.loads(m.group(1))
            items = data.get("item", [])
            if not items:
                for g in data.get("group", []):
                    items += g.get("item", [])
            return items
        except Exception as e:
            logger.debug("eIR fetch failed %s: %s", url, e)
    return []


def _fetch_eir_category(eir_code: str, category: str) -> list[dict]:
    """指定カテゴリの全アイテムを取得する。

    eIR のページ番号体系は企業ごとに異なる:
      - page=0 で全件返すタイプ
      - page=1 から昇順ページネーション
      - page=N（高い番号）から始まるタイプ（例: 8153 は page=24 のみ有効）
    全パターンに対応するため: page=0 → 昇順 → 降順（30→0）の順で探索する。
    """
    # まず page=0 を試す（全件まとめて返すタイプ）
    items = _fetch_eir_page(eir_code, 0, category)
    if items:
        logger.debug("eIR %s %s: page=0 で %d 件", eir_code, category, len(items))
        return items

    # page=1 から昇順ページネーション
    all_items: list[dict] = []
    for pg in range(1, 50):
        page_items = _fetch_eir_page(eir_code, pg, category)
        if not page_items:
            break
        all_items.extend(page_items)
        logger.debug("eIR %s %s: page=%d で %d 件", eir_code, category, pg, len(page_items))
        if len(page_items) < 50:
            break
    if all_items:
        return all_items

    # 昇順でも見つからない場合: 高い番号から降順で探索
    # （一部企業は最新ページ番号が高く、page=0/1 が 404 になるパターン）
    for pg in range(30, 0, -1):
        page_items = _fetch_eir_page(eir_code, pg, category)
        if page_items:
            logger.info("eIR %s %s: page=%d で %d 件（降順探索でヒット）", eir_code, category, pg, len(page_items))
            all_items.extend(page_items)
            # ヒットしたページの前後も確認
            for pg2 in range(pg - 1, max(pg - 5, 0), -1):
                more = _fetch_eir_page(eir_code, pg2, category)
                if more:
                    all_items.extend(more)
                else:
                    break
            return all_items
    return all_items


def _fetch_eir_announcement(
    eir_code: str,
    eir_category: str | None = None,
    eir_page: int | None = None,
) -> list[dict]:
    """全アナウンスメントを取得する。

    eir_category/eir_page が指定されている場合はそのページを直接使用し、
    前後のページも確認して全件を取得する。
    未指定の場合は announcement → new_release の順で自動探索。
    """
    if eir_category and eir_page is not None:
        # 指定ページから逆順に全ページを取得（最新から過去へ）
        all_items: list[dict] = []
        for pg in range(eir_page, 0, -1):
            page_items = _fetch_eir_page(eir_code, pg, eir_category)
            if not page_items:
                continue  # このページは空（404）
            all_items.extend(page_items)
            logger.debug("eIR %s %s page=%d: %d 件", eir_code, eir_category, pg, len(page_items))
        # 指定ページより新しいページも確認
        for pg in range(eir_page + 1, eir_page + 20):
            page_items = _fetch_eir_page(eir_code, pg, eir_category)
            if not page_items:
                break
            all_items.extend(page_items)
        return all_items

    # eir_category だけ指定（eir_page なし）: 指定カテゴリを全ページ取得してから自動探索にもフォールスルー
    if eir_category:
        items = _fetch_eir_category(eir_code, eir_category)
        if items:
            logger.debug("eIR %s: eir_category=%s で %d 件取得", eir_code, eir_category, len(items))
            return items
        logger.debug("eIR %s: eir_category=%s で 0 件 → 自動探索に移行", eir_code, eir_category)

    # 自動探索: announcement → new_release → ir_material → ir_material2
    for cat in ["announcement", "new_release", "ir_material", "ir_material2"]:
        if cat == eir_category:
            continue  # 既に試したカテゴリはスキップ
        items = _fetch_eir_category(eir_code, cat)
        if items:
            logger.debug("eIR %s: %s で %d 件取得", eir_code, cat, len(items))
            return items
    return []


def handle_eir_api(
    adapter: dict,
    company_dir: Path,
    dry_run: bool,
    since_year: int | None = None,
    file_log: list | None = None,
    bucket=None,
    existing_hashes: set | None = None,
) -> dict:
    """eIR API から月次データをダウンロードする。統計 dict を返す。

    since_year: この年以降のデータのみダウンロード（例: 2022）
    file_log: 各ファイルの結果を追記するリスト（呼び出し元で共有）
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.error("curl_cffi が未インストール。`uv add curl-cffi` を実行してください。")
        return stats

    ticker = adapter["ticker"]
    company = adapter.get("company_name", ticker)
    eir_code = adapter.get("eir_code") or adapter.get("eir_company_code", "")

    if not eir_code:
        logger.warning("[%s] eir_code が未設定", ticker)
        return stats

    eir_category = adapter.get("eir_category")  # "announcement" or "new_release"
    eir_page = adapter.get("eir_page")           # specific page number if known

    logger.info("[%s] %s eIR code=%s 取得開始", ticker, company, eir_code)
    items = _fetch_eir_announcement(eir_code, eir_category, eir_page)
    if not items:
        logger.warning("[%s] eIR アナウンスメント取得失敗", ticker)
        return stats

    # 月次アイテムだけフィルタ
    monthly_items = [it for it in items if EIR_MONTHLY_RE.search(it.get("title", ""))]

    # 年フィルタ
    if since_year:
        filtered = []
        for it in monthly_items:
            date_str = re.sub(r"[^0-9]", "", it.get("date", ""))[:8]
            if date_str and int(date_str[:4]) >= since_year:
                filtered.append(it)
        logger.info("[%s] 年フィルタ（%d以降）: %d → %d 件", ticker, since_year, len(monthly_items), len(filtered))
        monthly_items = filtered
    logger.info("[%s] 全 %d 件 → 月次 %d 件", ticker, len(items), len(monthly_items))

    if not monthly_items:
        logger.info("[%s] 月次アイテムなし（全件表示）", ticker)
        for it in items[:5]:
            logger.debug("  %s | %s", it.get("date", ""), it.get("title", ""))
        return stats

    if not (IS_CLOUD_RUN and bucket):
        _ensure_dir(company_dir)

    for item in monthly_items:
        item_date = item.get("date", "") or ""
        title = _safe_filename(item.get("title", "notitle"), 40)
        link = item.get("link", "") or item.get("url", "")
        text_html = item.get("text", "")
        yyyymm = _extract_yyyymm(item_date + " " + title, link)

        if link and DOWNLOAD_EXT.search(link):
            # PDF/Excel をダウンロード
            ext_m = DOWNLOAD_EXT.search(link)
            ext = ext_m.group(1).lower() if ext_m else "pdf"
            h = _url_hash(link)
            filename = f"{yyyymm}_{ticker}_{title}_{h}.{ext}"
            out_path = company_dir / filename

            _skip = (existing_hashes is not None and h in existing_hashes) or out_path.exists()
            if _skip:
                logger.debug("スキップ（既存）: %s", filename)
                stats["skipped"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": link, "status": "skipped", "bytes": 0, "yyyymm": yyyymm})
                continue

            if dry_run:
                logger.info("[dry-run] %s  url=%s", filename, link)
                stats["downloaded"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": link, "status": "dry-run", "bytes": 0, "yyyymm": yyyymm})
                continue

            try:
                resp = cf_requests.get(link, impersonate="chrome120", timeout=30)
                if resp.status_code == 200:
                    size = len(resp.content)
                    if IS_CLOUD_RUN and bucket:
                        _upload_to_gcs(bucket, ticker, filename, resp.content, ext)
                        if existing_hashes is not None:
                            existing_hashes.add(h)
                    else:
                        out_path.write_bytes(resp.content)
                    logger.info("保存: %s  %d bytes  url=%s", filename, size, link)
                    stats["downloaded"] += 1
                    if file_log is not None:
                        file_log.append({"ticker": ticker, "file": filename, "url": link, "status": "ok", "bytes": size, "yyyymm": yyyymm})
                else:
                    logger.warning("ダウンロード失敗 HTTP %d  url=%s", resp.status_code, link)
                    stats["failed"] += 1
                    if file_log is not None:
                        file_log.append({"ticker": ticker, "file": filename, "url": link, "status": f"http_{resp.status_code}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(None, resp.status_code), "response_size": len(resp.content)})
            except Exception as e:
                logger.warning("ダウンロード例外 %s: %s", link, e)
                stats["failed"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": link, "status": f"error:{e}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(e)})
            time.sleep(FILE_SLEEP)

        elif text_html:
            # HTML テーブルを CSV として保存
            h = _url_hash(text_html[:200])
            filename = f"{yyyymm}_{ticker}_{title}_{h}.csv"
            out_path = company_dir / filename

            _skip_csv = (existing_hashes is not None and h in existing_hashes) or out_path.exists()
            if _skip_csv:
                logger.debug("スキップ（既存）: %s", filename)
                stats["skipped"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": "", "status": "skipped", "bytes": 0, "yyyymm": yyyymm})
                continue

            if dry_run:
                logger.info("[dry-run] %s (HTML table→CSV)", filename)
                stats["downloaded"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": "", "status": "dry-run", "bytes": 0, "yyyymm": yyyymm})
                continue

            try:
                import pandas as pd
                tables = pd.read_html(io.StringIO(text_html))
                if tables:
                    if IS_CLOUD_RUN and bucket:
                        buf = io.StringIO()
                        tables[0].to_csv(buf, index=False, encoding="utf-8-sig")
                        csv_bytes = buf.getvalue().encode("utf-8-sig")
                        _upload_to_gcs(bucket, ticker, filename, csv_bytes, "csv")
                        if existing_hashes is not None:
                            existing_hashes.add(h)
                        size = len(csv_bytes)
                    else:
                        tables[0].to_csv(out_path, index=False, encoding="utf-8-sig")
                        size = out_path.stat().st_size
                    logger.info("保存(CSV): %s", filename)
                    stats["downloaded"] += 1
                    if file_log is not None:
                        file_log.append({"ticker": ticker, "file": filename, "url": "", "status": "ok", "bytes": size, "yyyymm": yyyymm})
            except Exception as e:
                logger.warning("HTML table 変換失敗 %s: %s", title, e)
                stats["failed"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": "", "status": f"error:{e}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(e)})
        else:
            logger.debug("[%s] リンクなし・テキストなし: %s", ticker, item.get("title", ""))

    return stats


# ==========================================
# scrape_links ハンドラー
# ==========================================

def _fetch_html_playwright(url: str, ticker: str, timeout_nav: int = 60000, timeout_idle: int = 30000) -> str | None:
    """Playwright（Chromium + stealth）で URL を取得して HTML を返す。失敗時は None。

    timeout_nav: goto のタイムアウト ms
    timeout_idle: networkidle のタイムアウト ms
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth as _Stealth
    except ImportError as e:
        logger.warning("[%s] Playwright import 失敗: %s", ticker, e)
        return None

    # Cloud Run では channel 指定なし（Chromium バンドル）
    # ローカルでは Google Chrome が使える場合はそちらを優先
    # --disable-http2: ERR_HTTP2_PROTOCOL_ERROR 対策（HTTP/1.1 にフォールバック）
    # ローカル: WAF回避のためヘッドあり + タイムアウト延長
    _headless = bool(os.environ.get("CLOUD_RUN_JOB"))
    launch_opts: dict = {"headless": _headless, "args": ["--disable-http2"]}
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", **launch_opts)
                logger.debug("[%s] Playwright: Google Chrome を使用", ticker)
            except Exception:
                browser = pw.chromium.launch(**launch_opts)
                logger.debug("[%s] Playwright: Chromium（バンドル）を使用", ticker)
            try:
                pw_page = browser.new_page()
                _Stealth().apply_stealth_sync(pw_page)
                pw_page.goto(url, timeout=timeout_nav)
                try:
                    pw_page.wait_for_load_state("networkidle", timeout=timeout_idle)
                except Exception:
                    pass  # networkidle タイムアウトは無視してコンテンツ取得を試みる
                # JS レンダリングの遅延に備えて追加待機（SPA・遅延ロード対策）
                pw_page.wait_for_timeout(2000)
                html_content = pw_page.content()
            finally:
                browser.close()
        return html_content
    except Exception as e:
        logger.warning("[%s] Playwright 取得例外 %s: %s", ticker, url, e)
        return None


def handle_scrape_links(
    adapter: dict,
    company_dir: Path,
    session: requests.Session,
    dry_run: bool,
    file_log: list | None = None,
    bucket=None,
    existing_hashes: set | None = None,
) -> dict:
    """HTMLページからリンクをスクレイピングしてダウンロード。統計 dict を返す。"""
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    ticker = adapter["ticker"]
    company = adapter.get("company_name", ticker)
    page_url = adapter.get("ir_page_url", "")

    if not page_url:
        logger.warning("[%s] ir_page_url が未設定", ticker)
        return 0

    # URL に # が含まれる場合は # まで
    fetch_url = page_url.split("#")[0] if "#" in page_url else page_url

    playwright_required = adapter.get("playwright_required", False)
    if playwright_required:
        html_content = _fetch_html_playwright(fetch_url, ticker)
        if html_content is None:
            # Playwright 失敗時は requests にフォールバック（HTTP2エラー対策）
            logger.info("[%s] Playwright 失敗 → requests にフォールバック", ticker)
            try:
                resp = session.get(fetch_url, headers=HEADERS, timeout=30)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, "html.parser")
                else:
                    logger.warning("[%s] フォールバック取得失敗 HTTP %d: %s", ticker, resp.status_code, fetch_url)
                    return stats
            except Exception as e:
                logger.warning("[%s] フォールバック取得例外: %s", ticker, e)
                return stats
        else:
            soup = BeautifulSoup(html_content, "html.parser")
    else:
        # curl_cffi をプライマリに使用（TLS フィンガープリント偽装で bot 検知回避）
        # requests は curl_cffi 失敗時のフォールバック
        soup = None
        try:
            from curl_cffi import requests as cf_requests
            _t0 = time.perf_counter()
            cf_resp = cf_requests.get(fetch_url, impersonate="chrome124", headers=HEADERS, timeout=20)
            _elapsed = time.perf_counter() - _t0
            if cf_resp.status_code == 200:
                logger.info("[%s] HTML取得: method=curl_cffi status=%d size=%d elapsed=%.1fs", ticker, cf_resp.status_code, len(cf_resp.content), _elapsed)
                soup = BeautifulSoup(cf_resp.content, "html.parser")
            else:
                logger.info("[%s] HTML取得: method=curl_cffi status=%d size=%d elapsed=%.1fs", ticker, cf_resp.status_code, len(cf_resp.content), _elapsed)
                logger.warning("[%s] curl_cffi ページ取得失敗 HTTP %d: %s", ticker, cf_resp.status_code, fetch_url)
        except Exception as e:
            logger.debug("[%s] curl_cffi ページ取得例外: %s → requests フォールバック", ticker, e)
        if soup is None:
            try:
                _t0 = time.perf_counter()
                resp = session.get(fetch_url, headers=HEADERS, timeout=15)
                _elapsed = time.perf_counter() - _t0
                if resp.status_code == 200:
                    logger.info("[%s] HTML取得: method=requests status=%d size=%d elapsed=%.1fs", ticker, resp.status_code, len(resp.content), _elapsed)
                    soup = BeautifulSoup(resp.content, "html.parser")
                else:
                    logger.info("[%s] HTML取得: method=requests status=%d size=%d elapsed=%.1fs", ticker, resp.status_code, len(resp.content), _elapsed)
                    logger.warning("[%s] ページ取得失敗 HTTP %d: %s", ticker, resp.status_code, fetch_url)
                    return stats
            except Exception as e:
                logger.warning("[%s] ページ取得例外: %s", ticker, e)
                return stats

    css_selector = adapter.get("css_selector")
    link_text_pat = adapter.get("link_text_pattern")
    link_href_pat = adapter.get("link_href_pattern", r"\.(pdf|xlsx|xls|csv)")
    follow_links = adapter.get("follow_links", False)
    follow_links_save_html = adapter.get("follow_links_save_html", False)
    if follow_links_save_html and not follow_links:
        logger.warning("[%s] follow_links_save_html=true だが follow_links=false のため無効", ticker)
        follow_links_save_html = False
    html_saved_count = 0

    text_re = re.compile(link_text_pat, re.IGNORECASE) if link_text_pat else None
    href_re = re.compile(link_href_pat, re.IGNORECASE) if link_href_pat else None

    if css_selector:
        candidates = soup.select(css_selector)
    else:
        candidates = soup.find_all("a", href=True)

    links: list[tuple[str, str]] = []

    if follow_links:
        # 2段階取得: listing page → text_pattern に一致するサブページ → そこからファイルを収集
        sub_urls: list[str] = []
        for a in candidates:
            href = re.sub(r"[\t\n\r]", "", a.get("href", "").strip())  # タブ・改行除去
            text = a.get_text(strip=True)
            abs_url = urljoin(fetch_url, href)
            if not href or href.startswith("#"):
                continue
            if _is_downloadable(abs_url):
                continue  # ファイル直リンクはスキップ（サブページではない）
            if text_re and (text_re.search(text) or text_re.search(href)):
                sub_urls.append(abs_url)

        seen_sub: set[str] = set()
        for sub_url in sub_urls:
            if sub_url in seen_sub:
                continue
            seen_sub.add(sub_url)
            try:
                if playwright_required:
                    sub_html = _fetch_html_playwright(sub_url, ticker)
                    if sub_html is None:
                        continue
                    sub_soup = BeautifulSoup(sub_html, "html.parser")
                else:
                    sub_soup = None
                    try:
                        from curl_cffi import requests as cf_requests
                        sub_cf = cf_requests.get(sub_url, impersonate="chrome124", headers=HEADERS, timeout=20)
                        if sub_cf.status_code == 200:
                            sub_soup = BeautifulSoup(sub_cf.content, "html.parser")
                    except Exception:
                        pass
                    if sub_soup is None:
                        sub_resp = session.get(sub_url, headers=HEADERS, timeout=15)
                        if sub_resp.status_code != 200:
                            logger.debug("[%s] サブページ取得失敗 %d: %s", ticker, sub_resp.status_code, sub_url)
                            continue
                        sub_soup = BeautifulSoup(sub_resp.content, "html.parser")
                if follow_links_save_html:
                    sub_title_el = sub_soup.find("title")
                    sub_title_text = sub_title_el.get_text(strip=True) if sub_title_el else "subpage"
                    sub_title_safe = _safe_filename(sub_title_text, 40)
                    sub_yyyymm = _extract_yyyymm(sub_title_text, sub_url)
                    sub_h = _url_hash(sub_url)
                    sub_filename = f"{sub_yyyymm}_{ticker}_{sub_title_safe}_{sub_h}.html"
                    sub_content = str(sub_soup).encode("utf-8")
                    if (existing_hashes is not None and sub_h in existing_hashes) or (company_dir.is_dir() and _already_local(company_dir, sub_h)):
                        logger.debug("スキップ（既存HTML）: %s", sub_url)
                    elif dry_run:
                        logger.info("[dry-run] HTML保存: %s  url=%s", sub_filename, sub_url)
                        html_saved_count += 1
                        if file_log is not None:
                            file_log.append({"ticker": ticker, "file": sub_filename, "url": sub_url, "status": "dry-run", "bytes": 0, "yyyymm": sub_yyyymm})
                    else:
                        size = len(sub_content)
                        if IS_CLOUD_RUN and bucket:
                            _upload_to_gcs(bucket, ticker, sub_filename, sub_content, "html")
                            if existing_hashes is not None:
                                existing_hashes.add(sub_h)
                        else:
                            _ensure_dir(company_dir)
                            (company_dir / sub_filename).write_bytes(sub_content)
                        logger.info("HTML保存: %s  %d bytes  url=%s", sub_filename, size, sub_url)
                        html_saved_count += 1
                        if file_log is not None:
                            file_log.append({"ticker": ticker, "file": sub_filename, "url": sub_url, "status": "ok", "bytes": size, "yyyymm": sub_yyyymm})
                for a in sub_soup.find_all("a", href=True):
                    href = re.sub(r"[\t\n\r]", "", a.get("href", "").strip())
                    text = a.get_text(strip=True)
                    abs_url2 = urljoin(sub_url, href)
                    if href_re and not href_re.search(abs_url2) and not href_re.search(href):
                        continue
                    if not _is_downloadable(abs_url2):
                        continue
                    links.append((text, abs_url2))
            except Exception as e:
                logger.warning("[%s] サブページ取得例外 %s: %s", ticker, sub_url, e)
            time.sleep(0.5)

        msg = f"[{ticker}] {company} follow_links: サブページ {len(seen_sub)} 件 → ファイル {len(links)} 件"
        if follow_links_save_html:
            msg += f" + HTML保存 {html_saved_count} 件"
        logger.info(msg)
    else:
        for a in candidates:
            href = re.sub(r"[\t\n\r]", "", a.get("href", "").strip())  # 前後+埋め込みの制御文字を除去
            text = a.get_text(strip=True)
            abs_url = urljoin(fetch_url, href)

            # CMSコピー由来の不正URL（/COPY- や /COPY- を含む）をスキップ
            if re.search(r"/COPY[-_]", abs_url):
                continue
            if href_re and not href_re.search(abs_url) and not href_re.search(href):
                continue
            if text_re and not text_re.search(text) and not text_re.search(href):
                continue
            if not _is_downloadable(abs_url):
                continue
            links.append((text, abs_url))

    # 月次キーワードでさらに絞り込み（あれば優先）
    # 明らかに月次でない文書を除外（URL・テキスト両方チェック）
    filtered_links = [
        (t, u) for t, u in links
        if not (NON_MONTHLY_RE.search(u) or NON_MONTHLY_RE.search(t))
    ]
    if len(filtered_links) < len(links):
        skipped = [(t, u) for t, u in links if NON_MONTHLY_RE.search(u) or NON_MONTHLY_RE.search(t)]
        for t, u in skipped:
            logger.info("[%s] 非月次文書スキップ: %s", ticker, u.split("/")[-1][:60])
    links = filtered_links

    monthly_links = [
        (t, u) for t, u in links
        if EIR_MONTHLY_RE.search(t) or EIR_MONTHLY_RE.search(u)
    ]
    target_links = monthly_links if monthly_links else links

    # 重複除去
    seen_urls: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for t, u in target_links:
        if u not in seen_urls:
            seen_urls.add(u)
            deduped.append((t, u))
    target_links = deduped

    if not target_links:
        # 0件の原因を切り分けてログに出す（鵜呑み防止）
        if not links and soup is not None:
            all_a = soup.find_all("a", href=True)
            logger.warning("[%s] ダウンロード対象リンクなし（ページ上のリンク総数=%d, href_re=%s, text_re=%s）",
                           ticker, len(all_a),
                           link_href_pat or "(なし)", link_text_pat or "(なし)")
        else:
            logger.info("[%s] ダウンロード対象リンクなし（候補%d件が月次フィルタで除外）", ticker, len(links))
        if html_saved_count:
            stats["html_saved"] = html_saved_count
        return stats

    logger.info("[%s] %s リンク %d 件（月次絞り込み後 %d 件）", ticker, company, len(links), len(target_links))
    if not (IS_CLOUD_RUN and bucket):
        _ensure_dir(company_dir)

    for text, url in target_links:
        ext = _guess_ext(url)
        h = _url_hash(url)

        if (existing_hashes is not None and h in existing_hashes) or (company_dir.is_dir() and _already_local(company_dir, h)):
            logger.debug("スキップ（既存）: %s", url)
            stats["skipped"] += 1
            if file_log is not None:
                file_log.append({"ticker": ticker, "file": f"*_{h}.{ext}", "url": url, "status": "skipped", "bytes": 0, "yyyymm": ""})
            continue

        # title 選定: リンクテキストがジェネリック（PDF版はこちらから等）の場合、
        # URLパスのファイル名を使うと 月次キーワード (monthly 等) が残って後段フィルタに有利
        url_stem = Path(urlparse(url).path).stem
        _generic_text_re = re.compile(r"^(PDF版|こちら|詳細|ダウンロード|資料|リンク)", re.IGNORECASE)
        if text and not _generic_text_re.search(text):
            title_src = text
        else:
            title_src = url_stem or text or "notitle"
        title = _safe_filename(title_src, 40)
        yyyymm = _extract_yyyymm(text or "", url)
        filename = f"{yyyymm}_{ticker}_{title}_{h}.{ext}"
        out_path = company_dir / filename

        if dry_run:
            logger.info("[dry-run] %s  url=%s", filename, url)
            stats["downloaded"] += 1
            if file_log is not None:
                file_log.append({"ticker": ticker, "file": filename, "url": url, "status": "dry-run", "bytes": 0, "yyyymm": yyyymm})
            continue

        try:
            # curl_cffi をプライマリに使用（TLS フィンガープリント偽装）
            resp = None
            try:
                from curl_cffi import requests as cf_requests
                resp = cf_requests.get(url, impersonate="chrome124", headers=HEADERS, timeout=30)
            except Exception:
                pass
            if resp is None or resp.status_code != 200:
                fb_resp = session.get(url, headers=HEADERS, timeout=30)
                if fb_resp.status_code == 200:
                    resp = fb_resp
            if resp is not None and resp.status_code == 200:
                size = len(resp.content)
                if IS_CLOUD_RUN and bucket:
                    _upload_to_gcs(bucket, ticker, filename, resp.content, ext)
                    if existing_hashes is not None:
                        existing_hashes.add(h)
                else:
                    out_path.write_bytes(resp.content)
                logger.info("保存: %s  %d bytes  url=%s", filename, size, url)
                stats["downloaded"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": url, "status": "ok", "bytes": size, "yyyymm": yyyymm})
            else:
                logger.warning("ダウンロード失敗 HTTP %d  url=%s", resp.status_code, url)
                stats["failed"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": url, "status": f"http_{resp.status_code}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(None, resp.status_code), "response_size": len(resp.content)})
        except Exception as e:
            logger.warning("ダウンロード例外 %s: %s", url, e)
            stats["failed"] += 1
            if file_log is not None:
                file_log.append({"ticker": ticker, "file": filename, "url": url, "status": f"error:{e}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(e)})
        time.sleep(FILE_SLEEP)

    if html_saved_count:
        stats["html_saved"] = html_saved_count
    return stats


# ==========================================
# disco_quarterly ハンドラー（6146 ディスコ専用）
# ==========================================

DISCO_NEWS_LIST = "https://www.disco.co.jp/jp/ir/news/topics/index.html"
DISCO_VIEWER_RE = re.compile(
    r"viewer\.html\?file=(/jp/ir/library/doc/news/[^\"\s&]+\.pdf)", re.I
)
DISCO_SPEED_RE = re.compile(
    r'href="(/jp/ir/news/topics/index\.html\?id=(\d+))"[^>]*>\s*([^<]*速報[^<]*)</a',
    re.I,
)
DISCO_BASE = "https://www.disco.co.jp"


def _follow_iframe(html_text: str, base_url: str, ticker: str, session: requests.Session) -> tuple[str, str] | None:
    """HTML 内の iframe を検出し、そのコンテンツを取得する。

    Returns:
        (iframe_html_text, iframe_url) or None if no relevant iframe found.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    iframes = soup.find_all("iframe")
    if not iframes:
        return None

    for iframe in iframes:
        src = iframe.get("src", "")
        if not src:
            continue
        # 広告・SNS系の iframe はスキップ
        if any(x in src.lower() for x in ["twitter", "facebook", "youtube", "google", "line-it", "ads"]):
            continue
        iframe_url = urljoin(base_url, src)
        logger.info("[%s] iframe 検出: %s → %s", ticker, src, iframe_url)
        try:
            from curl_cffi import requests as cf_requests
            resp = cf_requests.get(iframe_url, impersonate="chrome124", headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                if hasattr(resp, 'apparent_encoding') and resp.apparent_encoding and resp.apparent_encoding.upper() not in ("UTF-8", "UTF8"):
                    resp.encoding = resp.apparent_encoding
                return resp.text, iframe_url
        except Exception:
            pass
        try:
            resp = session.get(iframe_url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                if hasattr(resp, 'apparent_encoding') and resp.apparent_encoding and resp.apparent_encoding.upper() not in ("UTF-8", "UTF8"):
                    resp.encoding = resp.apparent_encoding
                return resp.text, iframe_url
        except Exception:
            pass
    return None


def handle_html_table(
    adapter: dict,
    company_dir: Path,
    session: requests.Session,
    dry_run: bool,
    file_log: list | None = None,
    bucket=None,
) -> dict:
    """月次データが掲載されているページの HTML をそのまま保存。テーブル変換は build_monthly_extractor.py が担う。

    拡張機能:
      - playwright_required=true: Playwright で JS レンダリング後の HTML を取得
      - iframe 自動フォロー: ページ内に iframe がある場合、iframe のコンテンツを取得して保存
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    ticker = adapter["ticker"]
    company = adapter.get("company_name", ticker)
    ir_page_url = adapter.get("ir_page_url", "")

    if not ir_page_url:
        logger.warning("[%s] html_table: ir_page_url 未設定", ticker)
        return stats

    logger.info("[%s] %s HTML 取得: %s", ticker, company, ir_page_url)

    playwright_required = adapter.get("playwright_required", False)
    html_text: str | None = None
    actual_url = ir_page_url

    if playwright_required:
        # Playwright で JS レンダリング後の HTML を取得
        html_content = _fetch_html_playwright(ir_page_url, ticker)
        if html_content:
            html_text = html_content
            logger.info("[%s] Playwright で HTML 取得成功 (%d bytes)", ticker, len(html_text))
        else:
            logger.warning("[%s] Playwright 失敗 → curl_cffi/requests にフォールバック", ticker)

    if html_text is None:
        # curl_cffi をプライマリに使用（TLS フィンガープリント偽装で bot 検知回避）
        resp = None
        try:
            from curl_cffi import requests as cf_requests
            resp = cf_requests.get(ir_page_url, impersonate="chrome124", headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                logger.debug("[%s] curl_cffi HTML取得失敗 HTTP %d → requests フォールバック", ticker, resp.status_code)
                resp = None
        except Exception as e:
            logger.debug("[%s] curl_cffi HTML取得例外: %s → requests フォールバック", ticker, e)
        if resp is None:
            try:
                resp = session.get(ir_page_url, headers=HEADERS, timeout=20)
                if resp.status_code != 200:
                    logger.warning("[%s] HTTP %d: %s", ticker, resp.status_code, ir_page_url)
                    stats["failed"] += 1
                    return stats
            except Exception as e:
                logger.warning("[%s] ページ取得例外: %s", ticker, e)
                stats["failed"] += 1
                return stats
        # Shift-JIS 等の日本語エンコーディングを自動検出して適用
        # curl_cffi は apparent_encoding を持たないため、HTML meta から charset をスニフする
        if hasattr(resp, 'apparent_encoding') and resp.apparent_encoding and resp.apparent_encoding.upper() not in ("UTF-8", "UTF8"):
            resp.encoding = resp.apparent_encoding
            html_text = resp.text
        else:
            # HTML meta charset 検出 (curl_cffi 対応)
            _raw = resp.content if hasattr(resp, 'content') else bytes(resp.text, 'utf-8')
            _charset = None
            # meta charset="shift_jis" / http-equiv Content-Type; charset=...
            _meta_m = re.search(
                rb'<meta[^>]+charset\s*=\s*["\']?([^"\'>\s]+)',
                _raw[:4096], re.IGNORECASE,
            )
            if _meta_m:
                _charset = _meta_m.group(1).decode('ascii', errors='ignore').lower().strip()
            if _charset and _charset.replace('-', '').replace('_', '') not in ('utf8', 'utf8sig'):
                try:
                    html_text = _raw.decode(_charset, errors='replace')
                    logger.debug("[%s] charset=%s で再デコード", ticker, _charset)
                except (UnicodeDecodeError, LookupError):
                    html_text = resp.text
            else:
                html_text = resp.text

    # iframe 自動フォロー: ページ内に <table> がなく iframe がある場合、iframe コンテンツを取得
    if not playwright_required and html_text:
        soup_check = BeautifulSoup(html_text, "html.parser")
        has_data_table = bool(soup_check.find("table"))
        has_iframe = bool(soup_check.find("iframe"))
        if has_iframe and not has_data_table:
            logger.info("[%s] テーブルなし＋iframe検出 → iframe フォロー", ticker)
            iframe_result = _follow_iframe(html_text, ir_page_url, ticker, session)
            if iframe_result:
                html_text, actual_url = iframe_result
                logger.info("[%s] iframe コンテンツ取得成功 (%d bytes): %s", ticker, len(html_text), actual_url)
        elif has_iframe:
            # テーブルはあるが、データが少ない場合（ナビテーブル等）も iframe をフォロー
            tables = soup_check.find_all("table")
            total_rows = sum(len(t.find_all("tr")) for t in tables)
            if total_rows <= 3:
                logger.info("[%s] テーブル行数少(%d行)＋iframe検出 → iframe フォロー", ticker, total_rows)
                iframe_result = _follow_iframe(html_text, ir_page_url, ticker, session)
                if iframe_result:
                    html_text, actual_url = iframe_result

    yyyymm = _extract_yyyymm("", actual_url)
    filename_html = f"{yyyymm}_{ticker}_monthly_table.html"
    out_path = company_dir / filename_html

    if dry_run:
        logger.info("[dry-run] html_table %s  url=%s", filename_html, actual_url)
        stats["downloaded"] += 1
        if file_log is not None:
            file_log.append({"ticker": ticker, "file": filename_html, "url": actual_url, "status": "dry-run", "bytes": 0, "yyyymm": yyyymm})
        return stats

    html_bytes = html_text.encode("utf-8")
    size = len(html_bytes)
    if IS_CLOUD_RUN and bucket:
        _upload_to_gcs(bucket, ticker, filename_html, html_bytes, "html")
    else:
        _ensure_dir(company_dir)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_text)
        size = out_path.stat().st_size

    logger.info("[%s] HTML 保存: %s (%d bytes)  url=%s", ticker, filename_html, size, actual_url)
    stats["downloaded"] += 1
    if file_log is not None:
        file_log.append({"ticker": ticker, "file": filename_html, "url": actual_url, "status": "ok", "bytes": size, "yyyymm": yyyymm})
    return stats


# ==========================================
# jsonp_api ハンドラー (2026-04-24 D3: 6412 平和 対応)
# ==========================================

_JSONP_CALLBACK_RE = re.compile(
    r"^[^(]*?\(\s*(\{.*\}|\[.*\])\s*\)\s*;?\s*$", re.DOTALL
)


def _parse_jsonp(text: str, callback_hint: str | None = None) -> dict | list | None:
    """JSONP 応答から内側の JSON を抽出してパースする。

    Args:
        text: JSONP 文字列 (例: `irvasset({"date":[...], ...})`)
        callback_hint: callback 名のヒント。未指定なら汎用 regex で抽出。

    Returns:
        パース済み dict/list。失敗時 None。
    """
    if not text:
        return None
    text = text.strip()
    # BOM 除去
    if text.startswith("\ufeff"):
        text = text[1:]

    def _try_parse(s: str) -> dict | list | None:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        # unquoted keys: {date:[...]} → {"date":[...]}
        fixed = re.sub(r'(?<=[{,])(\w+):', r'"\1":', s)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None

    # 1. callback_hint が指定されている場合、`<hint>(...)` を優先して剥がす
    if callback_hint:
        pattern = re.compile(
            rf"^\s*{re.escape(callback_hint)}\s*\((.*)\)\s*;?\s*$", re.DOTALL
        )
        m = pattern.match(text)
        if m:
            result = _try_parse(m.group(1))
            if result is not None:
                return result

    # 2. 汎用 regex フォールバック (`<anyname>({...})` or `<anyname>([...])`)
    m = _JSONP_CALLBACK_RE.match(text)
    if m:
        result = _try_parse(m.group(1))
        if result is not None:
            return result

    # 3. そのまま JSON としてパース試行
    return _try_parse(text)


def handle_jsonp_api(
    adapter: dict,
    company_dir: Path,
    dry_run: bool,
    file_log: list | None = None,
    bucket=None,
    existing_hashes: set | None = None,
) -> dict:
    """JSONP API から月次 PDF リスト を取得してダウンロードする。

    adapter の期待スキーマ:
      - type: "jsonp_api"
      - jsonp_api_url: JSONP エンドポイント URL (required)
      - jsonp_callback: callback 名 (任意、フォールバック regex あり)
      - link_href_pattern: PDF URL の絞り込み正規表現 (任意)

    JSON 構造は以下の2パターンを自動判定:
      A. parallel arrays: {"date":[...], "title":[...], "url":[...], ...}
         (例: azcms.ir-service.net/_irvassetctgry.aspx)
      B. list of items: [{"date":..., "title":..., "url":...}, ...]
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.error("curl_cffi が未インストール")
        return stats

    ticker = adapter["ticker"]
    company = adapter.get("company_name", ticker)
    api_url = adapter.get("jsonp_api_url", "")
    callback = adapter.get("jsonp_callback")
    href_pat = adapter.get("link_href_pattern")
    href_re = re.compile(href_pat, re.IGNORECASE) if href_pat else None

    if not api_url:
        logger.warning("[%s] jsonp_api_url が未設定", ticker)
        return stats

    logger.info("[%s] %s JSONP API 取得: %s (callback=%s)", ticker, company, api_url, callback or "(auto)")
    try:
        resp = cf_requests.get(api_url, impersonate="chrome124", headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning("[%s] JSONP API HTTP %d", ticker, resp.status_code)
            stats["failed"] += 1
            return stats
        try:
            jsonp_text = resp.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            jsonp_text = resp.content.decode("shift-jis", errors="replace")
    except Exception as e:
        logger.warning("[%s] JSONP API 取得例外: %s", ticker, e)
        stats["failed"] += 1
        return stats

    data = _parse_jsonp(jsonp_text, callback_hint=callback)
    if data is None:
        logger.warning("[%s] JSONP パース失敗 (先頭 200 文字): %s", ticker, jsonp_text[:200])
        stats["failed"] += 1
        return stats

    # parallel arrays → list of items に正規化
    items: list[dict] = []
    if isinstance(data, dict):
        urls = data.get("url") or data.get("urls") or []
        titles = data.get("title") or data.get("titles") or []
        dates = data.get("date") or data.get("dates") or []
        if isinstance(urls, list):
            for i, u in enumerate(urls):
                items.append({
                    "url": u,
                    "title": titles[i] if i < len(titles) else "",
                    "date": dates[i] if i < len(dates) else "",
                })
        else:
            # dict だが parallel arrays 構造でない → item キー試行
            items = data.get("item", []) or data.get("items", [])
    elif isinstance(data, list):
        items = data
    else:
        logger.warning("[%s] JSONP 構造が未対応: type=%s", ticker, type(data).__name__)
        return stats

    logger.info("[%s] JSONP アイテム: %d 件", ticker, len(items))

    # 月次フィルタ
    text_pat = adapter.get("link_text_pattern")
    text_re = re.compile(text_pat, re.IGNORECASE) if text_pat else None
    filtered: list[dict] = []
    for it in items:
        url = str(it.get("url") or it.get("link") or "")
        title = str(it.get("title") or "")
        if not url:
            continue
        if href_re and not href_re.search(url):
            continue
        if NON_MONTHLY_RE.search(url) or NON_MONTHLY_RE.search(title):
            continue
        if text_re and not (text_re.search(title) or text_re.search(url)):
            continue
        if not (EIR_MONTHLY_RE.search(url) or EIR_MONTHLY_RE.search(title)):
            continue
        filtered.append({"url": url, "title": title, "date": str(it.get("date") or "")})

    logger.info("[%s] 月次フィルタ後: %d 件", ticker, len(filtered))

    if not filtered:
        return stats

    if not (IS_CLOUD_RUN and bucket):
        _ensure_dir(company_dir)

    for it in filtered:
        url = it["url"]
        title_src = it.get("title") or Path(urlparse(url).path).stem or "notitle"
        title = _safe_filename(title_src, 40)
        ext = _guess_ext(url)
        h = _url_hash(url)
        yyyymm = _extract_yyyymm(
            (it.get("date") or "") + " " + (it.get("title") or ""),
            url,
        )
        filename = f"{yyyymm}_{ticker}_{title}_{h}.{ext}"
        out_path = company_dir / filename

        _skip = (existing_hashes is not None and h in existing_hashes) or (
            company_dir.is_dir() and _already_local(company_dir, h)
        )
        if _skip:
            logger.debug("スキップ（既存）: %s", filename)
            stats["skipped"] += 1
            if file_log is not None:
                file_log.append({
                    "ticker": ticker, "file": filename, "url": url,
                    "status": "skipped", "bytes": 0, "yyyymm": yyyymm,
                })
            continue

        if dry_run:
            logger.info("[dry-run] %s  url=%s", filename, url)
            stats["downloaded"] += 1
            if file_log is not None:
                file_log.append({
                    "ticker": ticker, "file": filename, "url": url,
                    "status": "dry-run", "bytes": 0, "yyyymm": yyyymm,
                })
            continue

        try:
            dl = cf_requests.get(url, impersonate="chrome124", headers=HEADERS, timeout=30)
            if dl.status_code == 200:
                size = len(dl.content)
                if IS_CLOUD_RUN and bucket:
                    _upload_to_gcs(bucket, ticker, filename, dl.content, ext)
                    if existing_hashes is not None:
                        existing_hashes.add(h)
                else:
                    out_path.write_bytes(dl.content)
                logger.info("保存: %s  %d bytes  url=%s", filename, size, url)
                stats["downloaded"] += 1
                if file_log is not None:
                    file_log.append({
                        "ticker": ticker, "file": filename, "url": url,
                        "status": "ok", "bytes": size, "yyyymm": yyyymm,
                    })
            else:
                logger.warning("DL 失敗 HTTP %d  url=%s", dl.status_code, url)
                stats["failed"] += 1
                if file_log is not None:
                    file_log.append({
                        "ticker": ticker, "file": filename, "url": url,
                        "status": f"http_{dl.status_code}", "bytes": 0, "yyyymm": yyyymm,
                        "error_type": _classify_error(None, dl.status_code), "response_size": len(dl.content),
                    })
        except Exception as e:
            logger.warning("DL 例外 %s: %s", url, e)
            stats["failed"] += 1
            if file_log is not None:
                file_log.append({
                    "ticker": ticker, "file": filename, "url": url,
                    "status": f"error:{e}", "bytes": 0, "yyyymm": yyyymm,
                    "error_type": _classify_error(e),
                })
        time.sleep(FILE_SLEEP)

    return stats


def handle_disco_quarterly(
    adapter: dict,
    company_dir: Path,
    dry_run: bool,
    file_log: list | None = None,
    bucket=None,
    existing_hashes: set | None = None,
) -> dict:
    """ディスコ（6146）の四半期売上速報 PDF をダウンロード。統計 dict を返す。"""
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.error("curl_cffi が未インストール")
        return stats

    ticker = adapter["ticker"]
    company = adapter.get("company_name", "ディスコ")

    logger.info("[%s] %s ニュースリスト取得", ticker, company)
    try:
        resp = cf_requests.get(DISCO_NEWS_LIST, impersonate="chrome120", timeout=15)
        if resp.status_code != 200:
            logger.warning("[%s] ニュースリスト取得失敗 HTTP %d", ticker, resp.status_code)
            stats["failed"] += 1
            return stats
    except Exception as e:
        logger.warning("[%s] ニュースリスト取得例外: %s", ticker, e)
        stats["failed"] += 1
        return stats

    speed_links = DISCO_SPEED_RE.findall(resp.text)
    logger.info("[%s] 速報リンク %d 件検出", ticker, len(speed_links))

    if not speed_links:
        return stats

    if not (IS_CLOUD_RUN and bucket):
        _ensure_dir(company_dir)

    for path_part, news_id, title_text in speed_links:
        news_url = DISCO_BASE + path_part
        title = _safe_filename(title_text.strip(), 40)

        try:
            page_resp = cf_requests.get(news_url, impersonate="chrome120", timeout=15)
            if page_resp.status_code != 200:
                continue
        except Exception as e:
            logger.warning("[%s] ニュースページ取得失敗 %s: %s", ticker, news_url, e)
            continue

        pdf_matches = DISCO_VIEWER_RE.findall(page_resp.text)
        if not pdf_matches:
            logger.debug("[%s] PDF リンクなし: %s", ticker, news_url)
            continue

        for pdf_path in pdf_matches:
            pdf_url = DISCO_BASE + pdf_path
            h = _url_hash(pdf_url)

            if (existing_hashes is not None and h in existing_hashes) or (company_dir.is_dir() and _already_local(company_dir, h)):
                logger.debug("スキップ（既存）: %s", pdf_url)
                stats["skipped"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": f"*_{h}.pdf", "url": pdf_url, "status": "skipped", "bytes": 0, "yyyymm": ""})
                continue

            yyyymm = _extract_yyyymm(title_text, pdf_url)
            filename = f"{yyyymm}_{ticker}_{title}_{h}.pdf"
            out_path = company_dir / filename

            if dry_run:
                logger.info("[dry-run] %s  url=%s", filename, pdf_url)
                stats["downloaded"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": pdf_url, "status": "dry-run", "bytes": 0, "yyyymm": yyyymm})
                continue

            try:
                pdf_resp = cf_requests.get(pdf_url, impersonate="chrome120", timeout=30)
                if pdf_resp.status_code == 200:
                    size = len(pdf_resp.content)
                    if IS_CLOUD_RUN and bucket:
                        _upload_to_gcs(bucket, ticker, filename, pdf_resp.content, "pdf")
                        if existing_hashes is not None:
                            existing_hashes.add(h)
                    else:
                        out_path.write_bytes(pdf_resp.content)
                    logger.info("保存: %s  %d bytes  url=%s", filename, size, pdf_url)
                    stats["downloaded"] += 1
                    if file_log is not None:
                        file_log.append({"ticker": ticker, "file": filename, "url": pdf_url, "status": "ok", "bytes": size, "yyyymm": yyyymm})
                else:
                    logger.warning("PDF ダウンロード失敗 HTTP %d  url=%s", pdf_resp.status_code, pdf_url)
                    stats["failed"] += 1
                    if file_log is not None:
                        file_log.append({"ticker": ticker, "file": filename, "url": pdf_url, "status": f"http_{pdf_resp.status_code}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(None, pdf_resp.status_code), "response_size": len(pdf_resp.content)})
            except Exception as e:
                logger.warning("PDF ダウンロード例外 %s: %s", pdf_url, e)
                stats["failed"] += 1
                if file_log is not None:
                    file_log.append({"ticker": ticker, "file": filename, "url": pdf_url, "status": f"error:{e}", "bytes": 0, "yyyymm": yyyymm, "error_type": _classify_error(e)})
            time.sleep(FILE_SLEEP)

        time.sleep(RATE_SEC)

    return stats


# ==========================================
# メイン
# ==========================================

def main() -> None:
    parser = argparse.ArgumentParser(description="月次開示ダウンロード v2")
    parser.add_argument("--dry-run", action="store_true", help="ダウンロードせず一覧表示のみ")
    parser.add_argument("--tickers", nargs="*", help="対象ティッカーを限定")
    parser.add_argument(
        "--type",
        choices=["eir_api", "scrape_links", "disco_quarterly", "html_table", "jsonp_api"],
        help="処理タイプを限定",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        metavar="YEAR",
        help="この年以降のデータのみダウンロード（例: --since 2022）",
    )
    parser.add_argument(
        "--exclude-edinet",
        action="store_true",
        help="EDINET経由の企業を除外する",
    )
    args = parser.parse_args()

    logger.info(
        "起動: dry_run=%s, task=%d/%d, is_cloud_run=%s",
        args.dry_run, TASK_INDEX + 1, TASK_COUNT, IS_CLOUD_RUN,
    )

    gcs = _get_gcs_client()
    bucket = gcs.bucket(GCS_BUCKET)

    adapters = _load_active_adapters(
        bucket,
        type_filter=args.type,
        exclude_edinet=args.exclude_edinet,
    )

    if args.tickers:
        adapters = [a for a in adapters if a["ticker"] in args.tickers]
        logger.info("ティッカー絞り込み: %s", [a["ticker"] for a in adapters])

    # Cloud Run タスク分割
    if TASK_COUNT > 1:
        adapters = adapters[TASK_INDEX::TASK_COUNT]
        logger.info(
            "タスク分割: TASK_INDEX=%d / TASK_COUNT=%d → %d社を担当",
            TASK_INDEX, TASK_COUNT, len(adapters),
        )

    logger.info("処理対象: %d社", len(adapters))

    # scrape_links 用 session
    session = requests.Session()

    results: list[dict] = []
    all_file_log: list[dict] = []

    for i, adapter in enumerate(adapters, 1):
        ticker = adapter["ticker"]
        company = adapter.get("company_name", ticker)
        a_type = adapter.get("type", "unknown")

        logger.info("[%d/%d] %s %s (type=%s)", i, len(adapters), ticker, company, a_type)

        # ローカル保存先（ティッカーのみ。会社名は取得ミスのリスクがあるため除外）
        company_dir = OUT_DIR / ticker

        file_log: list[dict] = []
        stats: dict = {"downloaded": 0, "skipped": 0, "failed": 0}
        error_msg = None

        # Cloud Run: GCS上の既存ファイルハッシュを取得してスキップ判定に使う
        existing_hashes = _get_ticker_gcs_hashes(bucket, ticker) if IS_CLOUD_RUN else None

        _company_t0 = time.perf_counter()
        try:
            if a_type == "eir_api":
                stats = handle_eir_api(adapter, company_dir, dry_run=args.dry_run, since_year=args.since, file_log=file_log, bucket=bucket, existing_hashes=existing_hashes)
            elif a_type == "scrape_links":
                stats = handle_scrape_links(adapter, company_dir, session, dry_run=args.dry_run, file_log=file_log, bucket=bucket, existing_hashes=existing_hashes)
                time.sleep(RATE_SEC)
            elif a_type == "disco_quarterly":
                stats = handle_disco_quarterly(adapter, company_dir, dry_run=args.dry_run, file_log=file_log, bucket=bucket, existing_hashes=existing_hashes)
            elif a_type == "html_table":
                stats = handle_html_table(adapter, company_dir, session, dry_run=args.dry_run, file_log=file_log, bucket=bucket)
                time.sleep(RATE_SEC)
            elif a_type == "jsonp_api":
                stats = handle_jsonp_api(adapter, company_dir, dry_run=args.dry_run, file_log=file_log, bucket=bucket, existing_hashes=existing_hashes)
                time.sleep(RATE_SEC)
            else:
                logger.debug("[%s] 未対応タイプ: %s", ticker, a_type)
                continue
        except Exception as e:
            logger.error("[%s] 処理例外: %s", ticker, e, exc_info=True)
            error_msg = str(e)
            stats["failed"] += 1

        _company_elapsed = time.perf_counter() - _company_t0
        logger.info(
            "[%s] 完了: downloaded=%d skipped=%d failed=%d elapsed=%.1fs",
            ticker, stats["downloaded"], stats["skipped"], stats["failed"], _company_elapsed,
        )
        if _company_elapsed > 60:
            logger.warning("[%s] 処理遅延: %.1fs (閾値 60s)", ticker, _company_elapsed)

        all_file_log.extend(file_log)
        results.append({
            "ticker": ticker,
            "company": company,
            "type": a_type,
            "downloaded": stats["downloaded"],
            "skipped": stats["skipped"],
            "failed": stats["failed"],
            "error": error_msg,
            "files": file_log,
        })

    # サマリー
    total_dl = sum(r["downloaded"] for r in results)
    total_skip = sum(r["skipped"] for r in results)
    total_fail = sum(r["failed"] for r in results)

    logger.info("=== 実行完了 ===")
    logger.info("総ダウンロード: %d件  スキップ: %d件  失敗: %d件", total_dl, total_skip, total_fail)

    zero_dl = [r for r in results if r["downloaded"] == 0 and r["failed"] == 0 and r["skipped"] == 0]
    if zero_dl:
        logger.info("取得なし（リンク未検出）: %d社", len(zero_dl))
        for r in zero_dl:
            logger.info("  %s %s (%s)", r["ticker"], r["company"], r["type"])

    failed_companies = [r for r in results if r["failed"] > 0 or r["error"]]
    if failed_companies:
        logger.info("失敗あり: %d社", len(failed_companies))
        for r in failed_companies:
            logger.warning("  %s %s (%s) failed=%d error=%s", r["ticker"], r["company"], r["type"], r["failed"], r["error"] or "")

    # GCS にログ保存
    _save_run_log(bucket, results, args.dry_run)


if __name__ == "__main__":
    main()
