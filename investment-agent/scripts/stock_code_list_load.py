"""JPX 東証銘柄リスト → BigQuery STOCK.STOCK_CODE_LIST ロード.

対応環境:
    - Google Colab（個人ユース）
    - Google Colab Enterprise
    - Cloud Run Job

実行方法:
    # Colab: セルで実行
    # Cloud Run: gcloud run jobs execute stock-code-list-load --region us-west1
"""

import io
import os
import sys
import traceback

import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from google.cloud import bigquery

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

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

# ==========================================
# 1. 基本設定
# ==========================================

PROJECT_ID     = "gmailpj-357912"
TABLE_ID       = f"{PROJECT_ID}.STOCK.STOCK_CODE_LIST"
TARGET_URL     = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
EXCHANGE_VALUE = "TSE"

JST = timezone(timedelta(hours=+9), "JST")

# ==========================================
# 2. 認証・クライアント（遅延初期化）
# ==========================================

def setup_environment() -> None:
    """実行環境に応じた GCP 認証を行う."""
    print(f"実行環境: {RUNTIME}")
    if RUNTIME == "colab_personal":
        from google.colab import auth
        auth.authenticate_user()
    # colab_enterprise / cloudrun は ADC が自動的に有効


_bq_client: bigquery.Client | None = None


def _get_bq_client() -> bigquery.Client:
    """BigQuery クライアントを取得する（遅延初期化・シングルトン）."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT_ID)
    return _bq_client


# ==========================================
# 3. データ取得・加工
# ==========================================

def get_latest_excel_url(page_url: str) -> str:
    """JPX のページから Excel ファイルのダウンロード URL を取得する."""
    res = requests.get(page_url, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    link = soup.find("a", href=lambda x: x and x.endswith(".xls"))
    if not link:
        raise RuntimeError("Excel ファイルのリンクが見つかりませんでした。")
    return "https://www.jpx.co.jp" + link["href"]


def process_dataframe(excel_url: str) -> pd.DataFrame:
    """Excel をダウンロードして加工し DataFrame を返す."""
    print(f"Excel ダウンロード中: {excel_url}")
    res = requests.get(excel_url, timeout=60)
    res.raise_for_status()
    df = pd.read_excel(io.BytesIO(res.content))

    mapping = {
        "コード":        "TICKER",
        "銘柄名":        "STOCK_NAME",
        "市場・商品区分": "MARKET_CATEGORY",
        "33業種コード":  "INDUSTRY_33_CODE",
        "33業種区分":   "INDUSTRY_33_CATEGORY",
        "17業種コード":  "INDUSTRY_17_CODE",
        "17業種区分":   "INDUSTRY_17_CATEGORY",
        "規模コード":    "SIZE_CODE",
        "規模区分":     "SIZE_CATEGORY",
    }
    df = df.rename(columns=mapping)

    # TICKER を 4 桁文字列に正規化（1301.0 → "1301"、130A → "130A"）
    df["TICKER"] = df["TICKER"].apply(lambda x: str(x).split(".")[0].strip())

    df["EXCHANGE"] = EXCHANGE_VALUE

    final_columns = [
        "TICKER", "EXCHANGE", "STOCK_NAME", "MARKET_CATEGORY",
        "INDUSTRY_33_CODE", "INDUSTRY_33_CATEGORY",
        "INDUSTRY_17_CODE", "INDUSTRY_17_CATEGORY",
        "SIZE_CODE", "SIZE_CATEGORY",
    ]
    df = df[final_columns].astype(str).replace(["nan", "None", "-"], None)
    print(f"加工完了: {len(df)} 件")
    return df


# ==========================================
# 4. BigQuery アップロード
# ==========================================

def upload_to_bigquery(df: pd.DataFrame) -> None:
    """既存の TSE レコードを削除して新規データをロードする."""
    client = _get_bq_client()

    print(f"{TABLE_ID} から {EXCHANGE_VALUE} の既存レコードを削除中...")
    client.query(
        f"DELETE FROM `{TABLE_ID}` WHERE EXCHANGE = '{EXCHANGE_VALUE}'"
    ).result()

    print(f"新規データをロード中 ({len(df)} 件)...")
    client.load_table_from_dataframe(df, TABLE_ID).result()
    print("BigQuery ロード完了。")


# ==========================================
# 5. メイン
# ==========================================

def main() -> None:
    """エントリポイント."""
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()

    try:
        setup_environment()

        excel_url = get_latest_excel_url(TARGET_URL)
        df        = process_dataframe(excel_url)
        upload_to_bigquery(df)

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        send_mail(
            "[STOCK_CODE_LIST] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
            attachment_text=log_text,
        )
        raise


if __name__ == "__main__":
    main()
