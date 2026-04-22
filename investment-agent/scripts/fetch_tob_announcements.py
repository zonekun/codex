"""EDINET 公開買付届出書 (docTypeCode=240) をスクレイピングし、BQ STOCK.DELISTED_STOCKS を拡張する。

入力: BQ STOCK.DELISTED_STOCKS の IS_TOB_MBO=TRUE レコード
出力: 以下7カラムを UPDATE
  - TOB_ANNOUNCEMENT_DATE: TOB公告日 (DATE)
  - TOB_PRICE: 買付価格（円/株, FLOAT64）
  - PRICE_BEFORE_ANNOUNCEMENT: 公告前営業日終値（円, FLOAT64） - 届出書内記載値または BQ STOCK_PRICE
  - PREMIUM_RATE: プレミアム率（FLOAT64, 例 0.3412）
  - TOB_TYPE: OTHER / SELF / MBO
  - TOB_ACQUIRER: 公開買付者名
  - TOB_DOC_ID: EDINET docID

アルゴリズム:
  1. 対象ticker×廃止日ごとに EDINET 検索ウィンドウ (delisting_date-365〜delisting_date+30) を設定
  2. その期間の docTypeCode=240 書類を全件取得
  3. ticker に紐づく書類をマッチ（secCode または subjectEdinetCode 経由）
     - Phase A: 意見表明報告書 (docTypeCode=290) の secCode==ticker から target の edinetCode を得る
     - Phase B: docTypeCode=240 の subjectEdinetCode==target_edinet でフィルタ
  4. マッチした XBRL をDL→TextBlock抽出→正規表現で価格・プレミアム・公告日・タイプ・買付者を抽出
  5. BQ へ UPDATE

Usage:
    # 対象1件テスト
    PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --ticker 7088

    # 全577件バックフィル
    PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --all

    # 未抽出のみ（TOB_PRICE IS NULL のレコード）
    PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --missing-only

    # dry-run（BQ書き込みなし）
    PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --ticker 7088 --dry-run
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests
import urllib3
from google.cloud import bigquery
from google.oauth2 import service_account

urllib3.disable_warnings()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")
BASE = "https://api.edinet-fsa.go.jp/api/v2"
KEY_FILE = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
DATASET = "STOCK"
TABLE = "DELISTED_STOCKS"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"
CACHE_DIR = r"C:\tmp\tob_edinet_cache"
DATE_CACHE_DIR = r"C:\tmp\tob_edinet_cache\dates"

TOB_F_DOC_TYPE = "240"  # 公開買付届出書
TOB_OPINION_DOC_TYPE = "290"  # 意見表明報告書

# 検索ウィンドウ（廃止日を起点に前後）
SEARCH_BEFORE_DAYS = 365
SEARCH_AFTER_DAYS = 30

# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────


@dataclass
class TOBExtract:
    ticker: str
    tob_doc_id: Optional[str] = None
    tob_announcement_date: Optional[date] = None
    tob_price: Optional[float] = None
    price_before_announcement: Optional[float] = None
    premium_rate: Optional[float] = None
    tob_type: Optional[str] = None
    tob_acquirer: Optional[str] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# EDINET API
# ──────────────────────────────────────────────


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    return s


def edinet_list_by_date(sess: requests.Session, d: date, use_cache: bool = True) -> list[dict]:
    """指定日の全書類一覧を取得。TOB関連のみ抽出してファイルキャッシュに保存。

    キャッシュ対象: docTypeCode in {240, 290, 250} + secCode不問の書類
    （secCode一致検索は ticker 単位で呼び出し側が行うため全書類を残す）
    """
    if use_cache:
        os.makedirs(DATE_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(DATE_CACHE_DIR, f"{d.isoformat()}.json")
        if os.path.isfile(cache_path):
            import json
            try:
                return json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                pass
    r = sess.get(
        f"{BASE}/documents.json",
        params={"date": d.isoformat(), "type": 2, "Subscription-Key": API_KEY},
        timeout=30,
    )
    if r.status_code != 200:
        return []
    results = r.json().get("results", []) or []
    if use_cache:
        # TOB関連 or secCode付き（ticker検索用）のみ保存でサイズ削減
        slim = [
            d for d in results
            if d.get("secCode") or d.get("docTypeCode") in ("240", "290", "250")
        ]
        import json
        try:
            json.dump(slim, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return slim
    return results


def edinet_download(sess: requests.Session, doc_id: str, cache_dir: str) -> Optional[str]:
    """指定 docID の XBRL ZIP をDLして展開。キャッシュ利用。戻り値は展開先ディレクトリ"""
    out_dir = os.path.join(cache_dir, doc_id)
    if os.path.isdir(out_dir):
        # cached
        return out_dir
    os.makedirs(out_dir, exist_ok=True)
    r = sess.get(
        f"{BASE}/documents/{doc_id}",
        params={"type": 1, "Subscription-Key": API_KEY},
        timeout=60,
    )
    if r.status_code != 200:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        z.extractall(out_dir)
    except Exception as e:
        print(f"    extract fail {doc_id}: {e}")
        return None
    return out_dir


# ──────────────────────────────────────────────
# XBRL パース
# ──────────────────────────────────────────────


def _read_xbrl_text(extract_dir: str) -> Optional[str]:
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(".xbrl"):
                try:
                    return open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
    return None


def _get_textblock(xbrl_text: str, tag_local: str, prefix: str = "jptoo-ton_cor") -> Optional[str]:
    """XBRL内のTextBlock相当タグの中身をHTMLタグ除去して返す。"""
    full = f"{prefix}:{tag_local}"
    m = re.search(rf"<{re.escape(full)}[^>]*>(.*?)</{re.escape(full)}>", xbrl_text, re.DOTALL)
    if not m:
        return None
    content = m.group(1)
    # HTMLエンティティ展開 + タグ除去
    content = (
        content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
    )
    content = re.sub(r"<[^>]+>", " ", content)
    content = re.sub(r"\s+", " ", content)
    return content.strip()


def _parse_tob_price(price_block: str) -> Optional[float]:
    """「普通株式１株につき、金1,710円」から1710を抽出。"""
    # パターン1: 普通株式...金X,XXX円
    m = re.search(r"普通株式[^新株]{0,50}?金?\s*([\d,]+)\s*円", price_block)
    if m:
        return float(m.group(1).replace(",", ""))
    # パターン2: １株につき、金X,XXX円 （普通株式の記載なしケース）
    m = re.search(r"[１1]\s*株\s*に\s*つき[^新株]{0,30}?金?\s*([\d,]+)\s*円", price_block)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _parse_price_before_and_premium(price_block: str) -> tuple[Optional[float], Optional[float]]:
    """公告前営業日終値 + プレミアム率を抽出。

    例: "前営業日である2025年11月７日の対象者株式の東京証券取引所プライム市場における終値1,275円に対して34.12％"
    """
    price_before = None
    premium = None
    # 前営業日の終値
    m = re.search(
        r"前営業日[^。]*?終値\s*([\d,]+)\s*円[^。]*?([\d.]+)\s*[％%]",
        price_block,
    )
    if m:
        try:
            price_before = float(m.group(1).replace(",", ""))
            premium = float(m.group(2)) / 100.0
        except ValueError:
            pass
    return price_before, premium


def _parse_announcement_date(period_block: str) -> Optional[date]:
    """「公告日 2025年11月11日」からdate object抽出。"""
    m = re.search(r"公告日\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", period_block)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _classify_tob_type(
    delisting_reason: str, filer_name: str, target_name: str
) -> str:
    """DELISTING_REASON 主体で OTHER / SELF / MBO を判定。

    - SELF: filer == target（自己株TOB、ただしDELISTED_STOCKSには通常入らない）
    - MBO: DELISTING_REASON に「ＭＢＯ」or「MBO」
    - OTHER: それ以外（他社・支配株主・親会社による買収）
    """
    # SELF判定: 買付者と対象会社が同じ
    if filer_name and target_name and filer_name.replace("株式会社", "").strip() == target_name.replace(
        "株式会社", ""
    ).strip():
        return "SELF"
    if delisting_reason and re.search(r"ＭＢＯ|MBO", delisting_reason):
        return "MBO"
    return "OTHER"


def parse_tob_filing(
    extract_dir: str, ticker: str, delisting_reason: str = ""
) -> TOBExtract:
    """展開済みXBRLディレクトリから情報抽出。"""
    extract = TOBExtract(ticker=ticker)
    xbrl_text = _read_xbrl_text(extract_dir)
    if not xbrl_text:
        extract.error = "xbrl not found"
        return extract

    price_block = _get_textblock(xbrl_text, "PriceOfPurchaseEtcTextBlock")
    period_block = _get_textblock(xbrl_text, "OriginalPeriodAtFilingTextBlock")
    target_block = _get_textblock(xbrl_text, "NameOfSubjectCompanyTextBlock")
    filer_block = _get_textblock(xbrl_text, "FullNameOrNameOfFilerOfNotificationCoverPage")

    if price_block:
        extract.tob_price = _parse_tob_price(price_block)
        pb, prem = _parse_price_before_and_premium(price_block)
        extract.price_before_announcement = pb
        extract.premium_rate = prem

    if period_block:
        extract.tob_announcement_date = _parse_announcement_date(period_block)

    if filer_block:
        extract.tob_acquirer = filer_block

    target_name = ""
    if target_block:
        m = re.search(r"対象者名[^】]*】\s*(.+?)$", target_block)
        target_name = m.group(1).strip() if m else target_block.strip()

    extract.tob_type = _classify_tob_type(
        delisting_reason, extract.tob_acquirer or "", target_name
    )

    if not extract.tob_price and not extract.tob_announcement_date:
        extract.error = "no price/date extracted"

    return extract


# ──────────────────────────────────────────────
# 検索: ticker → docID
# ──────────────────────────────────────────────


def find_tob_filing_for_ticker(
    sess: requests.Session,
    ticker: str,
    delisting_date: date,
    verbose: bool = True,
) -> Optional[dict]:
    """指定tickerのTOB公開買付届出書を検索し、最初の一致 doc メタデータを返す。

    ステップ:
      A. 検索ウィンドウ内の docTypeCode=240 を全列挙（subjectEdinetCodeで逆引き）
      B. もし B で見つからない場合、secCode==ticker の docTypeCode=290 から target edinet を取得し再探索
    """
    sec5 = ticker + "0"
    start = delisting_date - timedelta(days=SEARCH_BEFORE_DAYS)
    end = delisting_date + timedelta(days=SEARCH_AFTER_DAYS)

    # まず target の EDINET code を特定（secCode==sec5 の何らかの書類から）
    target_edinet = None
    d = start
    while d <= end and not target_edinet:
        docs = edinet_list_by_date(sess, d)
        for doc in docs:
            if doc.get("secCode") == sec5:
                target_edinet = doc.get("edinetCode")
                break
        time.sleep(0.05)
        d += timedelta(days=1)
    if verbose:
        print(f"  [{ticker}] target edinetCode={target_edinet}")
    if not target_edinet:
        return None

    # docTypeCode=240 で subjectEdinetCode==target_edinet を探す
    d = start
    while d <= end:
        docs = edinet_list_by_date(sess, d)
        for doc in docs:
            if (
                doc.get("docTypeCode") == TOB_F_DOC_TYPE
                and doc.get("subjectEdinetCode") == target_edinet
            ):
                if verbose:
                    print(
                        f"  [{ticker}] found docID={doc.get('docID')} "
                        f"date={d} filer={doc.get('filerName')}"
                    )
                return doc
        time.sleep(0.05)
        d += timedelta(days=1)
    return None


# ──────────────────────────────────────────────
# BQ IO
# ──────────────────────────────────────────────


def get_bq_client() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return bigquery.Client(project=PROJECT, credentials=creds)


def ensure_schema_extension(client: bigquery.Client) -> None:
    """DELISTED_STOCKS に必要カラムを ALTER TABLE で追加（存在チェック付き）。"""
    expected = [
        ("TOB_ANNOUNCEMENT_DATE", "DATE"),
        ("TOB_PRICE", "FLOAT64"),
        ("PRICE_BEFORE_ANNOUNCEMENT", "FLOAT64"),
        ("PREMIUM_RATE", "FLOAT64"),
        ("TOB_TYPE", "STRING"),
        ("TOB_ACQUIRER", "STRING"),
        ("TOB_DOC_ID", "STRING"),
    ]
    table_ref = client.get_table(TABLE_ID)
    existing = {f.name for f in table_ref.schema}
    to_add = [(n, t) for n, t in expected if n not in existing]
    if not to_add:
        print("schema already has all TOB columns.")
        return
    sql_parts = [f"ADD COLUMN IF NOT EXISTS {n} {t}" for n, t in to_add]
    sql = f"ALTER TABLE `{TABLE_ID}` " + ", ".join(sql_parts)
    print(f"running: {sql}")
    client.query(sql).result()
    print(f"added columns: {[n for n, _ in to_add]}")


def fetch_targets(
    client: bigquery.Client, tickers: Optional[list[str]], missing_only: bool, limit: Optional[int]
) -> pd.DataFrame:
    where = ["IS_TOB_MBO = TRUE"]
    if tickers:
        ticker_list = ",".join([f"'{t}'" for t in tickers])
        where.append(f"TICKER IN ({ticker_list})")
    if missing_only:
        where.append("TOB_PRICE IS NULL")
    where_sql = " AND ".join(where)
    limit_sql = f"LIMIT {limit}" if limit else ""
    sql = f"""
      SELECT TICKER, COMPANY_NAME, DELISTING_DATE, DELISTING_REASON
      FROM `{TABLE_ID}`
      WHERE {where_sql}
      ORDER BY DELISTING_DATE DESC
      {limit_sql}
    """
    return client.query(sql).to_dataframe()


def update_bq(client: bigquery.Client, extract: TOBExtract, delisting_date: date) -> None:
    sql = f"""
    UPDATE `{TABLE_ID}`
    SET TOB_ANNOUNCEMENT_DATE = @ann_date,
        TOB_PRICE = @price,
        PRICE_BEFORE_ANNOUNCEMENT = @price_before,
        PREMIUM_RATE = @premium,
        TOB_TYPE = @tob_type,
        TOB_ACQUIRER = @acquirer,
        TOB_DOC_ID = @doc_id
    WHERE TICKER = @ticker AND DELISTING_DATE = @delisting_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ticker", "STRING", extract.ticker),
            bigquery.ScalarQueryParameter("delisting_date", "DATE", delisting_date),
            bigquery.ScalarQueryParameter("ann_date", "DATE", extract.tob_announcement_date),
            bigquery.ScalarQueryParameter("price", "FLOAT64", extract.tob_price),
            bigquery.ScalarQueryParameter(
                "price_before", "FLOAT64", extract.price_before_announcement
            ),
            bigquery.ScalarQueryParameter("premium", "FLOAT64", extract.premium_rate),
            bigquery.ScalarQueryParameter("tob_type", "STRING", extract.tob_type),
            bigquery.ScalarQueryParameter("acquirer", "STRING", extract.tob_acquirer),
            bigquery.ScalarQueryParameter("doc_id", "STRING", extract.tob_doc_id),
        ]
    )
    client.query(sql, job_config=job_config).result()


# ──────────────────────────────────────────────
# main
# ──────────────────────────────────────────────


def process_one(
    sess: requests.Session,
    ticker: str,
    delisting_date: date,
    dry_run: bool,
    client: Optional[bigquery.Client],
    delisting_reason: str = "",
    verbose: bool = True,
) -> TOBExtract:
    doc_meta = find_tob_filing_for_ticker(sess, ticker, delisting_date, verbose=verbose)
    if not doc_meta:
        return TOBExtract(ticker=ticker, error="no TOB-F found")
    doc_id = doc_meta["docID"]
    os.makedirs(CACHE_DIR, exist_ok=True)
    extract_dir = edinet_download(sess, doc_id, CACHE_DIR)
    if not extract_dir:
        return TOBExtract(ticker=ticker, tob_doc_id=doc_id, error="download failed")
    extract = parse_tob_filing(extract_dir, ticker, delisting_reason)
    extract.tob_doc_id = doc_id
    if verbose:
        print(f"  [{ticker}] extract: {asdict(extract)}")
    if not dry_run and client:
        update_bq(client, extract, delisting_date)
        if verbose:
            print(f"  [{ticker}] BQ updated")
    return extract


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="特定tickerのみ実行")
    parser.add_argument("--all", action="store_true", help="全IS_TOB_MBO=TRUEを処理")
    parser.add_argument("--missing-only", action="store_true", help="TOB_PRICE IS NULL のみ")
    parser.add_argument("--limit", type=int, help="上限件数")
    parser.add_argument("--dry-run", action="store_true", help="BQ UPDATEせず、抽出結果表示のみ")
    parser.add_argument("--skip-schema", action="store_true", help="スキーマ拡張をスキップ")
    args = parser.parse_args()

    client = get_bq_client()
    if not args.skip_schema and not args.dry_run:
        ensure_schema_extension(client)

    tickers = [args.ticker] if args.ticker else None
    df = fetch_targets(
        client, tickers=tickers, missing_only=args.missing_only, limit=args.limit
    )
    print(f"targets: {len(df)}")

    sess = _session()
    results = []
    for i, row in df.iterrows():
        ticker = str(row["TICKER"])
        d = row["DELISTING_DATE"]
        if isinstance(d, pd.Timestamp):
            d = d.date()
        print(f"\n[{i+1}/{len(df)}] {ticker} {row['COMPANY_NAME']} delist={d}")
        try:
            ext = process_one(
                sess, ticker, d, args.dry_run, client,
                delisting_reason=str(row.get("DELISTING_REASON") or ""),
            )
        except Exception as e:
            print(f"  [{ticker}] ERR: {e}")
            ext = TOBExtract(ticker=ticker, error=str(e))
        results.append(
            {
                "ticker": ticker,
                "delisting_date": d,
                **asdict(ext),
            }
        )

    # サマリー
    ok = sum(1 for r in results if r.get("tob_price") is not None)
    print(f"\n=== DONE: price extracted {ok}/{len(results)} ===")
    # 失敗一覧
    fails = [r for r in results if r.get("error")]
    if fails:
        print(f"failures: {len(fails)}")
        for r in fails[:20]:
            print(f"  {r['ticker']} {r['error']}")


if __name__ == "__main__":
    main()
