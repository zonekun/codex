"""EDINET 大量保有変更報告書 遅延提出チェック → メール + Dropbox Excel 追記.

docTypeCode=350（変更報告書）のうち、義務発生日から提出日まで 60 日以上経過した
遅延提出を日次・範囲・指定日で検索し、結果をメール送信および Dropbox の Excel に
追記する。

対応環境:
    - Google Colab（個人ユース）
    - Google Colab Enterprise
    - Cloud Run Job
"""

import io
import os
import sys
import traceback
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

import dropbox
import openpyxl
import requests
from dropbox.exceptions import ApiError
from dropbox.files import WriteMode

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

_DROPBOX_ERROR: str = ""

# ==========================================
# 0. 実行環境判別
# ==========================================

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

JST = timezone(timedelta(hours=+9), "JST")

# ==========================================
# 1. 設定エリア
# ==========================================

# ------------------------------------------
# A. 検索モード
#    1: 特定の単一日付（SPECIFIC_DATE を使用）
#    2: 日付範囲（START_DATE〜END_DATE を使用）
#    3: システム日付・今日（デフォルト）
# ------------------------------------------
SEARCH_MODE = 3

# [MODE 1] 特定日付 (YYYY/MM/DD)
SPECIFIC_DATE = "2025/12/17"

# [MODE 2] 日付範囲 (YYYY/MM/DD)
START_DATE = "2025/01/01"
END_DATE   = "2025/12/17"

# ------------------------------------------
# B. 遅延日数の閾値（この日数以上を遅延とみなす）
# ------------------------------------------
DELAY_THRESHOLD_DAYS = 60

# --- EDINET API設定 ---
EDINET_API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")

# --- Dropbox設定 ---
DBX_APP_KEY       = 't8feblcw74hoeky'
DBX_APP_SECRET    = 'fcjgc37d034pw1n'
DBX_REFRESH_TOKEN = 'XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw'
DBX_FILE_PATH     = '/stock/AI分析優待/Edinet遅延.xlsx'


# ==========================================
# 2. 認証・セットアップ
# ==========================================

def setup_environment() -> None:
    """実行環境に応じた認証を行う."""
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()
    # colab_enterprise / cloudrun は ADC が自動的に有効


# ==========================================
# 3. EDINET API
# ==========================================

def search_edinet_documents(date_str: str) -> dict:
    """EDINET API で指定日の書類一覧を取得する."""
    formatted_date = date_str.replace("/", "-")
    params = {"date": formatted_date, "type": 2, "Subscription-Key": EDINET_API_KEY}
    response = requests.get(
        "https://api.edinet-fsa.go.jp/api/v2/documents.json",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def download_xbrl_document(doc_id: str) -> bytes:
    """EDINET API から XBRL ZIP をダウンロードする."""
    params = {"type": 1, "Subscription-Key": EDINET_API_KEY}
    response = requests.get(
        f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
        params=params,
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def extract_xbrl_data(zip_content: bytes) -> dict:
    """ZIP 内の XBRL から提出日・義務発生日・銘柄コード等を抽出する."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            for xbrl_file in [name for name in zf.namelist() if name.endswith('.xbrl')]:
                try:
                    with zf.open(xbrl_file) as f:
                        content = f.read()
                        root = ET.fromstring(content)
                        actual_ns = dict(
                            node for _, node in ET.iterparse(io.BytesIO(content), events=['start-ns'])
                        )
                        namespaces = {
                            'jplvh_cor': 'http://disclosure.edinet-fsa.go.jp/taxonomy/jplvh/2014-07-31/jplvh_cor',
                            'jpdei_cor': 'http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor',
                        }
                        for prefix, uri in actual_ns.items():
                            if 'jplvh' in uri and 'cor' in uri:
                                namespaces['jplvh_cor'] = uri
                            if 'jpdei' in uri and 'cor' in uri:
                                namespaces['jpdei_cor'] = uri

                        tags = {
                            'filing_date':    './/jplvh_cor:FilingDateCoverPage',
                            'obligation_date':'.//jplvh_cor:DateWhenFilingRequirementAroseCoverPage',
                            'security_code':  './/jplvh_cor:SecurityCodeOfIssuer',
                            'issuer_name':    './/jplvh_cor:NameOfIssuer',
                            'filer_name':     './/jpdei_cor:FilerNameInJapaneseDEI',
                        }
                        res = {k: root.find(v, namespaces) for k, v in tags.items()}
                        if res['filing_date'] is not None and res['obligation_date'] is not None:
                            code = res['security_code'].text if res['security_code'] is not None else "N/A"
                            return {
                                'security_code':    code[:4] if len(code) > 4 else code,
                                'issuer_name':      res['issuer_name'].text  if res['issuer_name']  is not None else "N/A",
                                'filer_name':       res['filer_name'].text   if res['filer_name']   is not None else "N/A",
                                'obligation_date':  res['obligation_date'].text,
                                'filing_date':      res['filing_date'].text,
                            }
                except Exception:
                    continue
    except Exception:
        pass
    return {}


def calculate_date_difference(date1_str: str, date2_str: str) -> int:
    """2つの YYYY-MM-DD 文字列の差分日数（絶対値）を返す."""
    try:
        d1 = datetime.strptime(date1_str, "%Y-%m-%d")
        d2 = datetime.strptime(date2_str, "%Y-%m-%d")
        return abs((d2 - d1).days)
    except Exception:
        return 0


# ==========================================
# 4. 保存
# ==========================================

def update_dropbox_excel(report_list: list[dict]) -> None:
    """Dropbox の Excel に遅延報告を追記する（書式・コメント維持）."""
    if not report_list:
        return

    print("Dropboxへ接続中...")
    dbx = dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )

    # 既存ファイルをダウンロード（なければ新規作成）
    try:
        print(f"既存ファイルをダウンロード中: {DBX_FILE_PATH}")
        _, response = dbx.files_download(DBX_FILE_PATH)
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        print("既存ファイルを読み込みました（書式維持）。")
    except ApiError as e:
        if e.error.is_path() and e.error.get_path().is_not_found():
            print("既存ファイルが見つかりません。新規作成します。")
            wb = openpyxl.Workbook()
            ws = wb.active
        else:
            raise

    # データ追記
    for r in report_list:
        ws.append([
            r['security_code'],
            r['issuer_name'],
            r['filer_name'],
            r['obligation_date'].replace("-", "/"),
            r['filing_date'].replace("-", "/"),
            f"{r['days_diff']}日",
        ])

    # アップロード
    global _DROPBOX_ERROR
    output = io.BytesIO()
    wb.save(output)
    print(f"Dropboxへアップロード中: {DBX_FILE_PATH}")
    try:
        dbx.files_upload(output.getvalue(), DBX_FILE_PATH, mode=WriteMode('overwrite'))
        print("Dropbox更新完了。")
    except Exception as e:
        if "insufficient_space" in str(e):
            _DROPBOX_ERROR = f"Dropbox 容量不足のためアップロードをスキップ: {DBX_FILE_PATH}"
            print(f"[警告] {_DROPBOX_ERROR}")
        else:
            raise


# ==========================================
# 5. 日付リスト生成
# ==========================================

def resolve_target_dates() -> list[str]:
    """SEARCH_MODE に応じた対象日付リスト（YYYY/MM/DD）を返す."""
    if SEARCH_MODE == 1:
        return [SPECIFIC_DATE]
    elif SEARCH_MODE == 2:
        start_d = datetime.strptime(START_DATE, "%Y/%m/%d")
        end_d   = datetime.strptime(END_DATE,   "%Y/%m/%d")
        dates   = []
        curr_d  = start_d
        while curr_d <= end_d:
            dates.append(curr_d.strftime("%Y/%m/%d"))
            curr_d += timedelta(days=1)
        return dates
    else:  # MODE 3: 今日
        return [datetime.now(JST).strftime("%Y/%m/%d")]


# ==========================================
# 6. メイン
# ==========================================

def main() -> None:
    """エントリポイント."""
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()

    try:
        setup_environment()

        target_dates = resolve_target_dates()
        date_label = (
            f"{START_DATE} - {END_DATE}" if SEARCH_MODE == 2
            else target_dates[0] if target_dates else "日付不明"
        )
        print(f"検索期間: {date_label}  閾値: {DELAY_THRESHOLD_DAYS}日以上")

        # 遅延報告の収集
        all_delayed_reports: list[dict] = []
        for date_str in target_dates:
            print(f"解析中: {date_str} ...")
            try:
                search_results = search_edinet_documents(date_str)
                if "results" not in search_results:
                    continue
                target_docs = [
                    doc for doc in search_results["results"]
                    if str(doc.get("docTypeCode")) == "350"
                    and "変更報告書" in (doc.get("docDescription") or "")
                ]
                for doc in target_docs:
                    try:
                        zip_content = download_xbrl_document(doc.get("docID"))
                        xbrl_data   = extract_xbrl_data(zip_content)
                        if not xbrl_data:
                            continue
                        days_diff = calculate_date_difference(
                            xbrl_data['obligation_date'], xbrl_data['filing_date']
                        )
                        if days_diff >= DELAY_THRESHOLD_DAYS:
                            xbrl_data['days_diff'] = days_diff
                            all_delayed_reports.append(xbrl_data)
                    except Exception:
                        continue
            except Exception as e:
                print(f"Error on {date_str}: {e}")
                continue

        # ---------------------------------------------------------
        # 処理結果メール（既存ロジック・[完了]とは別便）
        # ---------------------------------------------------------
        if all_delayed_reports:
            lines = [
                f"{r['security_code']}_____{r['issuer_name']}_____{r['filer_name']}"
                f"_____{r['obligation_date']}_____{r['filing_date']}_____{r['days_diff']}日"
                for r in all_delayed_reports
            ]
            print(f"完了: {len(all_delayed_reports)} 件の遅延報告が見つかりました。")
            send_mail(
                f"EDINET遅延報告 {date_label} ({len(all_delayed_reports)}件)",
                "\n".join(lines),
            )
            update_dropbox_excel(all_delayed_reports)
        else:
            print("完了: 対象なし。")
            send_mail(
                f"EDINET遅延報告 {date_label} (対象なし)",
                "対象となる遅延報告は見つかりませんでした。",
            )

        log_text = log_cap.stop()

        # Dropbox容量不足は後続処理完了後に異常終了として通知
        if _DROPBOX_ERROR:
            send_mail(
                "[EDINET_DELAY] 【異常終了】DROPBOX容量不足",
                f"Dropboxへのアップロードが容量不足で失敗しました。\n"
                f"メール通知は正常完了しています。\n\n"
                f"スキップされたファイル: {_DROPBOX_ERROR}",
                attachment_text=log_text,
            )
            sys.exit(1)

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[EDINET_DELAY] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
        )
        raise


if __name__ == "__main__":
    main()
