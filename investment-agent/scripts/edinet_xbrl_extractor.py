#!/usr/bin/env python3
"""EDINET XBRL から現金・有価証券を全銘柄分抽出するツール.

有価証券報告書の XBRL から現金および有価証券（持合い含む）を抽出し TSV に出力する。
清原スクリーニング等で四季報より高鮮度なデータが必要な場合に使用する。

実行環境:
    local          : PYTHONUTF8=1 python scripts/edinet_xbrl_extractor.py
    Colab          : ノートブック経由（notebooks/edinet_xbrl_extractor.ipynb）
    Cloud Run      : 環境変数で設定を渡す

出力:
    local     : ローカル TSV ファイル（OUTPUT_TSV_LOCAL）
    Colab/CR  : GCS（gs://{GCS_BUCKET}/{GCS_PREFIX}/edinet_financial_{YYYYMMDD}.tsv）
                colab_personal は files.download() でも自動DLされる
"""

# ╔══════════════════════════════════════════════╗
# ║  ★ 実行設定（Colab ではここを直接書き換える）    ║
# ╚══════════════════════════════════════════════╝
SEARCH_START_DATE = "2025-01-01"   # 検索開始日（YYYY-MM-DD）
DAYS_TO_SCAN      = 365            # 検索日数
TEST_MODE_LIMIT   = 0              # 0=全件 / N=N件で打ち切り（テスト用）

# local 環境の出力先
OUTPUT_TSV_LOCAL  = "edinet_financial_data.tsv"

# Colab / Cloud Run の GCS 出力先
GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "edinet_xbrl"
# ══════════════════════════════════════════════

import os
import sys
import requests
import datetime
import zipfile
import io
import csv
import time
import pandas as pd
from tqdm import tqdm
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ─────────────────────────────────────────────
# 実行環境の判別
# ─────────────────────────────────────────────
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
print(f"[環境] {RUNTIME}")

# 環境変数オーバーライド（Cloud Run 実行時に --set-env-vars で差し替え可能）
if os.environ.get("XBRL_START_DATE"):
    SEARCH_START_DATE = os.environ["XBRL_START_DATE"]
if os.environ.get("XBRL_DAYS"):
    DAYS_TO_SCAN = int(os.environ["XBRL_DAYS"])
if os.environ.get("XBRL_TEST_LIMIT"):
    TEST_MODE_LIMIT = int(os.environ["XBRL_TEST_LIMIT"])


# ─────────────────────────────────────────────
# EDINET API キー取得
# ─────────────────────────────────────────────
def get_edinet_api_key() -> str | None:
    """実行環境に応じて EDINET API キーを取得する."""
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        try:
            from google.colab import userdata
            return userdata.get("EDINET_API_KEY")
        except Exception:
            pass
    return os.environ.get("EDINET_API_KEY")


EDINET_API_KEY: str | None = get_edinet_api_key()


# ─────────────────────────────────────────────
# GCS クライアント取得（Colab / Cloud Run のみ）
# ─────────────────────────────────────────────
def get_gcs_client():
    """GCS クライアントを取得する（local では None を返す）."""
    if RUNTIME == "local":
        return None
    from google.cloud import storage
    if RUNTIME == "colab_personal":
        import json
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds = service_account.Credentials.from_service_account_info(key_info)
        return storage.Client(credentials=creds, project=key_info.get("project_id"))
    else:  # colab_enterprise / cloudrun: ADC
        return storage.Client()


# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────
BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"
HEADERS  = {"User-Agent": "EDINET-Extractor/3env-v1"}

CASH_TAGS = [
    "CashAndDeposits", "CashAndDepositsAssetsINS", "CashAssetsBNK",
    "CashAssetsINS", "DepositsAssetsINS", "DepositsCAFND",
]
SECURITY_TAGS = [
    "InvestmentSecurities", "InvestmentSecuritiesOfSubsidiariesAndAffiliates",
    "ShortTermInvestmentSecurities", "SecuritiesAssetsBNK", "SecuritiesAssetsINS",
    "OperationalInvestmentSecuritiesCA",
]


# ─────────────────────────────────────────────
# HTTP セッション
# ─────────────────────────────────────────────
def create_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504, 429],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


SESSION = create_session()


def request_with_retry(url: str, params: dict | None = None, stream: bool = False):
    """リトライ機能付きリクエスト."""
    p = dict(params or {})
    if EDINET_API_KEY:
        p["Subscription-Key"] = EDINET_API_KEY
    try:
        r = SESSION.get(url, params=p, headers=HEADERS, timeout=30, stream=stream)
        r.raise_for_status()
        return r
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None
        print(f"  [通信エラー] {e} URL: {url}")
        return None
    except Exception as e:
        print(f"  [予期せぬエラー] {e} URL: {url}")
        return None


# ─────────────────────────────────────────────
# JPX 銘柄リスト取得
# ─────────────────────────────────────────────
def get_valid_ticker_set() -> set[str] | None:
    """JPX から最新の上場銘柄リストを取得する."""
    base_page = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
    try:
        r = SESSION.get(base_page, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        target_link = soup.find("a", href=lambda h: h and "data_j.xls" in h)
        if not target_link:
            print("JPX Excel リンクが見つかりません")
            return None

        url = urljoin(base_page, target_link["href"])
        print(f"JPX リスト取得中: {url}")
        r = SESSION.get(url, headers=HEADERS)
        r.raise_for_status()

        df = pd.read_excel(io.BytesIO(r.content), usecols=["コード", "市場・商品区分"])
        df["code"] = df["コード"].astype(str).str.replace(r"\.0", "", regex=True)

        exclude = ["ETF・ETN", "PRO Market", "REIT・ベンチャーファンド・カントリーファンド・インフラファンド"]
        is_valid = ~df["市場・商品区分"].str.contains("|".join(exclude), na=False)

        tickers = set(df.loc[is_valid, "code"].tolist())
        print(f"対象銘柄数: {len(tickers)} 件")
        return tickers
    except Exception as e:
        print(f"銘柄リスト取得失敗: {e}")
        return None


# ─────────────────────────────────────────────
# XBRL パーサー
# ─────────────────────────────────────────────
def parse_xbrl(xbrl_content: bytes) -> tuple[float, float, bool]:
    """XBRL から現金・有価証券を抽出する（連結優先）.

    Returns:
        (cash, securities, has_data)
    """
    soup = BeautifulSoup(xbrl_content, "lxml-xml")
    if not soup.find():
        soup = BeautifulSoup(xbrl_content, "html.parser")

    data_con = {"cash": 0.0, "sec": 0.0, "found": False}
    data_non = {"cash": 0.0, "sec": 0.0, "found": False}
    seen_tags: set[tuple] = set()

    for tag in soup.find_all():
        tag_name = tag.name.split(":")[-1]
        ctx = tag.get("contextRef", "")

        if "CurrentYear" not in ctx or "Instant" not in ctx:
            continue
        if ("Member" in ctx or "Domain" in ctx) and "NonConsolidated" not in ctx:
            continue

        val_str = tag.text.strip()
        if not val_str:
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue

        key = (tag_name, ctx)
        if key in seen_tags:
            continue
        seen_tags.add(key)

        is_cash = tag_name in CASH_TAGS
        is_sec  = tag_name in SECURITY_TAGS
        if not (is_cash or is_sec):
            continue

        target = data_non if "NonConsolidated" in ctx else data_con
        if is_cash:
            target["cash"] += val
            target["found"] = True
        if is_sec:
            target["sec"] += val
            target["found"] = True

    if data_con["found"]:
        return data_con["cash"], data_con["sec"], True
    elif data_non["found"]:
        return data_non["cash"], data_non["sec"], True
    return 0.0, 0.0, False


# ─────────────────────────────────────────────
# 出力（環境別）
# ─────────────────────────────────────────────
def upload_to_gcs(local_path: str, gcs_client, date_str: str) -> str:
    """TSV を GCS にアップロードし GCS URI を返す."""
    fname = f"edinet_financial_{date_str}.tsv"
    blob_path = f"{GCS_PREFIX}/{fname}"
    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(local_path, content_type="text/tab-separated-values")
    uri = f"gs://{GCS_BUCKET}/{blob_path}"
    print(f"GCS アップロード完了: {uri}")
    return uri


# ─────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────
def main() -> None:
    if not EDINET_API_KEY:
        print("★注意: EDINET_API_KEY が設定されていません。")

    valid_tickers = get_valid_ticker_set()
    if not valid_tickers:
        return

    try:
        start_date = datetime.datetime.strptime(SEARCH_START_DATE, "%Y-%m-%d").date()
    except ValueError:
        print("日付形式エラー（YYYY-MM-DD）")
        return

    end_date   = start_date + datetime.timedelta(days=DAYS_TO_SCAN)
    dates_list = [start_date + datetime.timedelta(days=i)
                  for i in range((end_date - start_date).days)]
    today_str  = datetime.date.today().strftime("%Y%m%d")

    print(f"処理期間: {start_date} ～ {end_date - datetime.timedelta(days=1)}")
    if TEST_MODE_LIMIT:
        print(f"[テストモード] {TEST_MODE_LIMIT} 件で打ち切り")

    # ── ローカル一時ファイルに書き出し ──
    tmp_path = OUTPUT_TSV_LOCAL
    processed_count = 0
    error_count = 0

    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["証券コード", "会社名", "提出日", "現金(百万円)", "有価証券(百万円)"])

        for d in tqdm(dates_list, desc="Scanning"):
            date_str = d.strftime("%Y-%m-%d")
            r = request_with_retry(f"{BASE_URL}/documents.json",
                                   params={"date": date_str, "type": "2"})
            if not r:
                continue
            try:
                docs = r.json().get("results", [])
            except Exception:
                continue
            if not docs:
                continue

            for doc in docs:
                title = doc.get("docDescription") or ""
                if "有価証券報告書" not in title or "訂正" in title:
                    continue

                sec_code   = doc.get("secCode")
                short_code = str(sec_code)[:4] if sec_code else ""
                if short_code not in valid_tickers:
                    continue

                time.sleep(0.05)
                doc_id = doc.get("docID")
                dl = request_with_retry(f"{BASE_URL}/documents/{doc_id}",
                                        params={"type": "1"}, stream=True)
                if not dl:
                    error_count += 1
                    continue

                try:
                    with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
                        xbrl_file = next(
                            (n for n in z.namelist()
                             if "PublicDoc" in n and n.endswith(".xbrl")),
                            None,
                        )
                        if xbrl_file:
                            with z.open(xbrl_file) as xf:
                                cash, sec, has_data = parse_xbrl(xf.read())
                            if has_data:
                                filer  = doc.get("filerName") or "Unknown"
                                s_date = doc.get("submitDateTime", "")[:10]
                                writer.writerow([
                                    short_code, filer, s_date,
                                    int(round(cash / 1_000_000)),
                                    int(round(sec  / 1_000_000)),
                                ])
                                f.flush()
                                processed_count += 1
                except Exception:
                    error_count += 1
                    continue

                if TEST_MODE_LIMIT and processed_count >= TEST_MODE_LIMIT:
                    break
            if TEST_MODE_LIMIT and processed_count >= TEST_MODE_LIMIT:
                break

    print(f"\n完了: {processed_count} 件抽出成功  /  エラー: {error_count} 件")

    # ── 環境別の後処理 ──
    if RUNTIME == "local":
        print(f"保存先: {tmp_path}")

    else:  # Colab / Cloud Run → GCS へアップロード
        gcs_client = get_gcs_client()
        gcs_uri = upload_to_gcs(tmp_path, gcs_client, today_str)

        if RUNTIME == "colab_personal":
            try:
                from google.colab import files
                files.download(tmp_path)
            except Exception:
                pass

        print(f"GCS URI: {gcs_uri}")


if __name__ == "__main__":
    main()
