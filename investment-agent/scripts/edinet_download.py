import argparse
import requests
import os
import traceback
import zipfile
import io
import re
import time
import sys
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=+9), "JST")

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture


def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "cloudrun"


RUNTIME: str = detect_runtime()

# ==========================================
# 1. 動作設定セクション
# ==========================================

# --- [銘柄指定方法] ---
# True: JPXリスト全て (本番運用) / False: 指定したテスト用銘柄のみ (テストモード)
# Cloud Run では本番モードをデフォルトとする（Colab では False のまま）
PRODUCTION_MODE = (RUNTIME == "cloudrun")

# テストモード時に使用する証券コード (リスト形式で複数指定可能)
TEST_TICKERS = ["7203"]

# --- [日付指定方法] ---
# 1: 単一日付 (今日)
# 2: 単一日付 (以下の SPECIFIED_DATE で指定)
# 3: 範囲指定 (RANGE_START_DATE ～ 今日)
DATE_SELECT_MODE = 1

# [MODE 2] 用の特定日付 (yyyymmdd形式)
SPECIFIED_DATE = "20260218"

# [MODE 3] 用の範囲開始日 (yyyymmdd形式)
RANGE_START_DATE = "20240101"

# --- [フィルタ・加工設定] ---
# True: 指定した種別のみ取得 (通常運用) / False: 全ての書類種別を取得 (デバッグ用)
FILTER_BY_DOC_TYPE = True  

# True: 解凍してHTML結合・不要ファイル削除を行う / False: ZIPのまま保存
EXTRACT_ZIP = True  

# --- [外部サービス設定] ---
# EDINET API Key（Cloud Run では環境変数 EDINET_API_KEY が優先される）
API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")
# GCS保存先バケット名とプレフィックス
GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "edinet"
GCS_BASE_URL = f"gs://{GCS_BUCKET}/{GCS_PREFIX}"  # 後方互換用（参照のみ）
# BQ差分チェック用テーブル（edinet_load.py の出力先と同一）
BQ_PROJECT = "gmailpj-357912"
BQ_TABLE = f"{BQ_PROJECT}.STOCK.ir_documents_enhanced"

# --- [環境変数によるオーバーライド（Cloud Run 用）] ---
# EDINET_DATE_MODE : 1/2/3 → DATE_SELECT_MODE を上書き
# EDINET_DATE      : YYYYMMDD → SPECIFIED_DATE を上書き（MODE=2 時に使用）
# EDINET_RANGE_START: YYYYMMDD → RANGE_START_DATE を上書き（MODE=3 時に使用）
# EDINET_PRODUCTION: true/false → PRODUCTION_MODE を上書き
if os.environ.get("EDINET_DATE_MODE"):
    DATE_SELECT_MODE = int(os.environ["EDINET_DATE_MODE"])
if os.environ.get("EDINET_DATE"):
    SPECIFIED_DATE = os.environ["EDINET_DATE"]
if os.environ.get("EDINET_RANGE_START"):
    RANGE_START_DATE = os.environ["EDINET_RANGE_START"]
if os.environ.get("EDINET_PRODUCTION"):
    PRODUCTION_MODE = os.environ["EDINET_PRODUCTION"].lower() == "true"

# --- [3文字略称定義 (DOC_GROUP_MAP)] ---
DOC_GROUP_MAP = {
    # --- 有報年 (年次報告書) ---
    ("010", "030000"): "有報年", ("010", "030001"): "有報年",
    ("010", "032000"): "有報年", ("010", "032001"): "有報年",
    ("010", "040000"): "有報年", ("010", "040001"): "有報年",

    # --- 有報四 (四半期・半期報告書) ---
    ("010", "043000"): "有報四", ("010", "043001"): "有報四",
    ("010", "043A00"): "有報四", ("010", "043A01"): "有報四",
    ("010", "050000"): "有報四", ("010", "050001"): "有報四",
    ("010", "052000"): "有報四", ("010", "052001"): "有報四",

    # --- 届出書 (有価証券届出書・通知書) ---
    ("010", "010000"): "届出書", ("010", "010001"): "届出書",
    ("010", "020000"): "届出書", ("010", "020001"): "届出書",
    ("010", "022000"): "届出書", ("010", "022001"): "届出書",
    ("010", "023000"): "届出書", ("010", "023001"): "届出書",
    ("010", "024000"): "届出書", ("010", "024001"): "届出書",
    ("010", "025000"): "届出書", ("010", "025001"): "届出書",
    ("010", "026000"): "届出書", ("010", "026001"): "届出書",
    ("010", "027000"): "届出書", ("010", "027001"): "届出書",

    # --- 大量保 (大量保有報告書) ※現在はコメントアウトにより除外 ---
    # ("060", "010000"): "大量保", ("060", "010002"): "大量保",
    # ("060", "020002"): "大量保", ("060", "030000"): "大量保",
    # ("060", "030002"): "大量保", ("060", "090001"): "大量保",

    # --- ＴＯＢ (公開買付) ---
    ("040", "040001"): "ＴＯＢ", ("040", "050006"): "ＴＯＢ", ("040", "060001"): "ＴＯＢ",
    ("040", "060007"): "ＴＯＢ", ("040", "080001"): "ＴＯＢ", ("040", "080008"): "ＴＯＢ",
    ("050", "020000"): "ＴＯＢ", ("050", "020001"): "ＴＯＢ", ("050", "030006"): "ＴＯＢ",
    ("050", "040001"): "ＴＯＢ", ("050", "040007"): "ＴＯＢ",

    # --- 自社株 (自己株券買付状況) ---
    ("030", "253000"): "自社株", ("030", "253001"): "自社株",
}

# ==========================================
# 2. 補助関数・ロジック
# ==========================================

def setup_environment() -> bool:
    """実行環境に応じた GCP 認証を行う.

    - colab_personal : auth.authenticate_user() でインタラクティブ認証
    - colab_enterprise: ADC (Application Default Credentials) を自動使用
    - cloudrun       : Attached Service Account の ADC を自動使用
    """
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()
    # enterprise / cloudrun は ADC が自動的に有効なため追加処理不要
    return True

def find_latest_jpx_url():
    base_domain = "https://www.jpx.co.jp"
    list_page_url = f"{base_domain}/markets/statistics-equities/misc/01.html"
    try:
        res = requests.get(list_page_url)
        soup = BeautifulSoup(res.text, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'data_j\.xls[x]?'))
        if links:
            href = links[0].get('href')
            return href if href.startswith('http') else base_domain + href
    except: pass
    return "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

def get_target_tickers():
    if not PRODUCTION_MODE:
        print(f"銘柄モード: テストモード {TEST_TICKERS}")
        return TEST_TICKERS
    print("銘柄モード: 本番運用 (JPX全銘柄)")
    url = find_latest_jpx_url()
    try:
        response = requests.get(url)
        df_jpx = pd.read_excel(io.BytesIO(response.content))
        df_jpx['code_str'] = df_jpx['コード'].astype(str).str.replace(r'\.0$', '', regex=True)
        exclude = ['ETF・ETN', 'PRO Market', 'プライム（外国株式）', 'REIT・ベンチャーファンド・カントリーファンド・インフラファンド', 'スタンダード（外国株式）', 'グロース（外国株式）']
        is_exclude = df_jpx['市場・商品区分'].isin(exclude) & (df_jpx['code_str'] != '1306')
        tickers = df_jpx.loc[~is_exclude, 'code_str'].unique().tolist()
        if '1306' not in tickers: tickers.append('1306')
        return tickers
    except Exception as e:
        print(f"JPXリスト取得エラー: {e}")
        return []

def sanitize(name):
    return re.sub(r'[\\/:*?"<>| ]', '_', str(name))


def _edinet_get(url: str, params: dict, max_retry: int = 5) -> requests.Response:
    """EDINET API GET リクエスト（指数バックオフ付きリトライ）.

    接続エラー・タイムアウト・5xx 系エラーをリトライする。
    待機時間: 10s → 20s → 40s → 80s → 160s
    """
    for attempt in range(max_retry):
        try:
            resp = requests.get(url, params=params, timeout=60)
            if resp.status_code < 500:
                return resp
            # 5xx: サーバーエラーはリトライ
            raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
        except Exception as e:
            if attempt == max_retry - 1:
                raise
            wait = (2 ** attempt) * 10  # 10, 20, 40, 80, 160 秒
            print(f"  [RETRY {attempt + 1}/{max_retry}] {type(e).__name__}: {e} → {wait}s 待機")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _resolve_dates() -> tuple[list[datetime], str]:
    """DATE_SELECT_MODE に従って処理対象日付リストを返す."""
    today = datetime.now(JST)
    if DATE_SELECT_MODE == 1:
        return [today]
    elif DATE_SELECT_MODE == 2:
        return [datetime.strptime(SPECIFIED_DATE, "%Y%m%d")]
    elif DATE_SELECT_MODE == 3:
        dates, curr = [], datetime.strptime(RANGE_START_DATE, "%Y%m%d")
        while curr <= today:
            dates.append(curr)
            curr += timedelta(days=1)
        return dates
    return [today]


def parse_args() -> argparse.Namespace:
    """Cloud Run 用の日付引数をパースする.

    Colab 環境では sys.argv に Jupyter カーネルの引数が混入するため argparse をスキップ。
    """
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None, force=False)

    parser = argparse.ArgumentParser(
        description="EDINET 開示書類ダウンロード → GCS",
        epilog=(
            "例:\n"
            "  (引数なし)              今日分を実行\n"
            "  --from 20250204         指定日1日のみ\n"
            "  --from 20250101 --to 20250204  期間指定\n"
        ),
    )
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD（省略時は今日）")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    parser.add_argument("--force", action="store_true", default=False,
                        help="既存GCSファイルを上書きする")
    return parser.parse_args()

# モジュールレベルの GCS クライアント（遅延初期化・シングルトン）
_gcs_client_lazy = None

def _get_gcs_client():
    """GCS クライアントを取得する（遅延初期化・シングルトン）."""
    global _gcs_client_lazy
    if _gcs_client_lazy is None:
        from google.cloud import storage
        _gcs_client_lazy = storage.Client()
    return _gcs_client_lazy

_bq_client_lazy = None

def _get_bq_client():
    """BQ クライアントを取得する（遅延初期化・シングルトン）.

    google-cloud-bigquery が未インストールの場合は None を返す。
    """
    global _bq_client_lazy
    if _bq_client_lazy is None:
        try:
            from google.cloud import bigquery
            _bq_client_lazy = bigquery.Client(project=BQ_PROJECT)
        except Exception:
            return None
    return _bq_client_lazy

def _report_gcs_bq_diff() -> str:
    """GCS に保存済みだが BQ 未登録のファイル数をレポートする.

    ストリーミング処理でメモリを節約する（全件セット展開を避ける）。

    Returns:
        差分サマリー文字列。BQ 接続不可時はスキップメッセージを返す。
    """
    bq = _get_bq_client()
    if bq is None:
        return "[BQ差分チェックスキップ] google-cloud-bigquery 未インストールまたは接続エラー"
    try:
        # BQ登録済みファイル数のみカウント（全件展開しない）
        bq_count_row = next(iter(bq.query(
            f"SELECT COUNT(DISTINCT file_name) AS cnt FROM `{BQ_TABLE}`"
        ).result()))
        bq_count = bq_count_row.cnt
    except Exception as e:
        return f"[BQ差分チェックスキップ] BQクエリエラー: {e}"

    # GCS ブロブをストリーミングでカウント（セットに全件展開しない）
    bucket = _get_gcs_client().bucket(GCS_BUCKET)
    gcs_count = sum(
        1 for b in bucket.list_blobs(prefix=GCS_PREFIX)
        if b.name.lower().endswith(('.htm', '.html')) and "大量保有" not in b.name
    )
    summary = (
        f"GCS/BQ 差分レポート:\n"
        f"  GCS HTML総数 : {gcs_count} 件\n"
        f"  BQ登録済み   : {bq_count} 件\n"
        f"  BQ未登録(推定): {max(0, gcs_count - bq_count)} 件"
    )
    print(f"\n--- {summary} ---")
    return summary


# ---- 再開（レジューム）ログ ----

def _resume_blob_path(date_from: str, date_to: str) -> str:
    """GCS 上の再開ログ blob パスを返す."""
    return f"{GCS_PREFIX}/_resume_{date_from}_{date_to}.txt"


def _load_done_dates(blob_path: str) -> set[str]:
    """GCS から完了済み日付セット (YYYY-MM-DD) を読み込む."""
    try:
        blob = _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path)
        if not blob.exists():
            return set()
        return {
            line[5:].strip()
            for line in blob.download_as_text().splitlines()
            if line.startswith("DONE:")
        }
    except Exception:
        return set()


def _mark_date_done(blob_path: str, date_str: str) -> None:
    """GCS の再開ログに完了日付を追記する."""
    try:
        blob = _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path)
        existing = blob.download_as_text() if blob.exists() else ""
        blob.upload_from_string(existing + f"DONE:{date_str}\n")
    except Exception as e:
        print(f"  [WARN] 再開ログ書き込みエラー: {e}")


def _delete_resume_log(blob_path: str) -> None:
    """正常完了後に再開ログを GCS から削除する."""
    try:
        blob = _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path)
        if blob.exists():
            blob.delete()
            print(f"[再開ログ] 削除完了: gs://{GCS_BUCKET}/{blob_path}")
    except Exception as e:
        print(f"  [WARN] 再開ログ削除エラー: {e}")


def _upload_dir_to_gcs(local_dir: str, ticker: str) -> None:
    """ローカルディレクトリ内のファイルを GCS へアップロードする（既存スキップ）.

    Args:
        local_dir: アップロード元のローカルディレクトリパス
        ticker   : 証券コード4桁（GCS パス `edinet/{ticker}/` に使用）
    """
    bucket = _get_gcs_client().bucket(GCS_BUCKET)
    for fname in os.listdir(local_dir):
        fpath = os.path.join(local_dir, fname)
        if not os.path.isfile(fpath):
            continue
        blob_name = f"{GCS_PREFIX}/{ticker}/{fname}"
        blob = bucket.blob(blob_name)
        if blob.exists():
            continue  # 既存ファイルはスキップ（gsutil -n 相当）
        blob.upload_from_filename(fpath)
        print(f"  → GCS アップロード: {blob_name}")

def _process_one_date(d: datetime, search_map: dict, resume_blob: str, force: bool = False) -> int:
    """指定日の EDINET データを処理して GCS に保存し、ダウンロード件数を返す.

    成功時は DONE ログを記録して件数を返す。失敗時は例外を送出する（DONE は記録しない）。
    force=True の場合、既存GCSファイルを上書きする。
    """
    import shutil
    d_str       = d.strftime("%Y-%m-%d")
    daily_count = 0
    used_tickers: set = set()

    res_json = _edinet_get(
        "https://api.edinet-fsa.go.jp/api/v2/documents.json",
        params={"date": d_str, "type": 2, "Subscription-Key": API_KEY},
    ).json()

    if "results" in res_json:
        for doc in res_json["results"]:
            ticker = search_map.get(doc.get("secCode"))
            if ticker:
                abbr = DOC_GROUP_MAP.get((doc.get("ordinanceCode"), doc.get("formCode")))
                if FILTER_BY_DOC_TYPE:
                    if not abbr:
                        continue
                else:
                    abbr = abbr if abbr else "全書類"

                temp_dir = f"temp_{ticker}"
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)

                prefix = (
                    f"{ticker}_{abbr}_{d_str.replace('-','')}_{sanitize(doc.get('docDescription'))}"
                    f"_{doc.get('docID')}"
                )
                if RUNTIME != "local" and EXTRACT_ZIP:
                    gcs_key = f"{GCS_PREFIX}/{ticker}/{prefix}_MERGED_REPORT.html"
                    if not force and _get_gcs_client().bucket(GCS_BUCKET).blob(gcs_key).exists():
                        continue
                if process_document(doc.get("docID"), prefix, temp_dir):
                    daily_count += 1
                    used_tickers.add(ticker)
                time.sleep(1.0)  # EDINET API レートリミット対策（ドキュメント間）

        if daily_count > 0:
            for t_code in used_tickers:
                _upload_dir_to_gcs(f"temp_{t_code}", t_code)
                shutil.rmtree(f"temp_{t_code}")
            print(f"【成功】{d_str}: {daily_count} 件をGCSへ保存完了")

    time.sleep(2.0)  # EDINET API レートリミット対策（日付間）
    _mark_date_done(resume_blob, d_str)
    return daily_count


def process_document(doc_id, file_prefix, ticker_dir):
    url = f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
    res = _edinet_get(url, params={"type": 1, "Subscription-Key": API_KEY})
    if res.status_code != 200: return False
    
    try:
        if not EXTRACT_ZIP:
            with open(os.path.join(ticker_dir, f"{file_prefix}.zip"), "wb") as f:
                f.write(res.content)
            return True

        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            html_list = []
            for info in z.infolist():
                if info.is_dir() or "AuditDoc/" in info.filename: continue
                ext = os.path.splitext(info.filename)[1].lower()
                if ext in ['.xsd', '.xml', '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg']: continue
                
                if "PublicDoc/" in info.filename and ext in ['.htm', '.html']:
                    with z.open(info.filename) as f:
                        html_list.append((info.filename, f.read().decode('utf-8', errors='ignore')))
                else:
                    new_name = f"{file_prefix}_{info.filename.replace('/', '_')}"
                    with z.open(info.filename) as src, open(os.path.join(ticker_dir, new_name), "wb") as dst:
                        dst.write(src.read())
            
            if html_list:
                html_list.sort(key=lambda x: x[0])
                merged = "\n".join([c for _, c in html_list])
                with open(os.path.join(ticker_dir, f"{file_prefix}_MERGED_REPORT.html"), "w", encoding="utf-8") as f:
                    f.write(merged)
        return True
    except: return False

# ==========================================
# 3. メイン処理
# ==========================================

def main():
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()
    date_label = "不明"

    try:
        if not setup_environment():
            log_cap.stop()
            return

        valid_tickers = get_target_tickers()
        if not valid_tickers:
            log_cap.stop()
            return

        search_map = {f"{t}0": t for t in valid_tickers}

        # 日付解決: --from/--to 引数 > 環境変数 > 設定ブロック
        args = parse_args()
        if args.date_from:
            d_from = datetime.strptime(args.date_from, "%Y%m%d")
            d_to   = datetime.strptime(args.date_to or args.date_from, "%Y%m%d")
            target_dates, curr = [], d_from
            while curr <= d_to:
                target_dates.append(curr); curr += timedelta(days=1)
        else:
            target_dates = _resolve_dates()

        # --- 再開ログ確認 ---
        resume_blob = _resume_blob_path(
            target_dates[0].strftime("%Y%m%d"),
            target_dates[-1].strftime("%Y%m%d"),
        )
        done_dates  = set() if args.force else _load_done_dates(resume_blob)
        resume_mode = bool(done_dates)
        if resume_mode:
            orig_count   = len(target_dates)
            target_dates = [d for d in target_dates if d.strftime("%Y-%m-%d") not in done_dates]
            skipped      = orig_count - len(target_dates)
            print(f"\n{'='*50}")
            print(f"[再開モード] 再開ログを検出しました")
            print(f"  完了済み : {skipped} 日")
            if target_dates:
                print(f"  再開日付 : {target_dates[0].strftime('%Y-%m-%d')}")
                print(f"  残り日数 : {len(target_dates)} 日")
            else:
                print(f"  全日付が完了済みです。再開ログを削除して終了します。")
                print(f"{'='*50}\n")
                _delete_resume_log(resume_blob)
                log_cap.stop()
                return
            print(f"{'='*50}\n")

        date_label = (
            f"{target_dates[0].strftime('%Y-%m-%d')} ～ {target_dates[-1].strftime('%Y-%m-%d')}"
            if len(target_dates) > 1
            else target_dates[0].strftime('%Y-%m-%d')
        )

        print(f"--- 巡回開始 (全 {len(target_dates)} 日間) ---")
        total_dl: int = 0
        failed_dates: list[datetime] = []

        for d in target_dates:
            d_str = d.strftime("%Y-%m-%d")
            try:
                total_dl += _process_one_date(d, search_map, resume_blob, force=args.force)
            except Exception as e:
                print(f"【エラー】{d_str}: {e}")
                # 例外発生時は DONE を記録しない → エンド・リトライ対象へ
                failed_dates.append(d)

        # --- エンド・リトライ（全日付処理後に1回のみ）---
        if failed_dates:
            print(f"\n--- エラー日付のリトライ ({len(failed_dates)} 日) ---")
            retry_failed: list[datetime] = []
            for d in failed_dates:
                d_str = d.strftime("%Y-%m-%d")
                try:
                    total_dl += _process_one_date(d, search_map, resume_blob, force=args.force)
                    print(f"[リトライ成功] {d_str}")
                except Exception as e:
                    print(f"[リトライ失敗] {d_str}: {e}")
                    retry_failed.append(d)
            if retry_failed:
                print(f"リトライ後も失敗: {[d.strftime('%Y-%m-%d') for d in retry_failed]}")
            failed_dates = retry_failed

        # 全日付処理完了 → 再開ログを削除
        _delete_resume_log(resume_blob)

        elapsed = datetime.now(JST) - start_time
        print(f"\n--- 完了 (合計: {total_dl} 件) ---")

        # GCS/BQ 差分レポート
        diff_summary = _report_gcs_bq_diff()

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        tb_str   = traceback.format_exc()
        print(f"[FATAL] {e}\n{tb_str}", file=sys.stderr)
        # 異常終了メール（ログをテキスト添付）
        send_mail(
            f"[EDINET] エラー {date_label}",
            f"EDINET ダウンロードでエラーが発生しました。\n\n"
            f"対象期間  : {date_label}\n"
            f"エラー    : {e}\n\n"
            f"トレースバック:\n{tb_str}",
            attachment_text=log_text or None,
        )
        raise

if __name__ == "__main__":
    main()