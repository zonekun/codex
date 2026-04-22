"""EDINET 有価証券報告書から株主構成データを抽出し、BQ STOCK.SHAREHOLDER_COMPOSITION にロードする。

対象データ:
  - 大株主（トップ10）: 名前 + 持株比率
  - 所有者別状況: 外国人/個人/金融機関/その他法人/自己株式 比率
  - アクティビスト判定: トップ10に activists.csv のファンドが含まれるか

データソース優先順位:
  1. GCS `gs://stock_data_1930932/edinet/{ticker}/` にキャッシュされた XBRL
  2. EDINET API 直接取得（`docTypeCode=120` 有価証券報告書）

Usage:
  # 単一銘柄で動作確認（BQ書き込みなし）
  PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --ticker 7203 --year 2020 --dry-run

  # 全銘柄×全年（2013-2026）バックフィル
  PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --all

  # 欠損のみ補完
  PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --missing-only
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import urllib3
from bs4 import BeautifulSoup
from google.cloud import bigquery, storage
from google.oauth2 import service_account

# 既存資産を import
sys.path.insert(0, str(Path(__file__).parent))
from flag_activists_in_list import load_activists, match_activist, SHAREHOLDER_EXCLUSIONS

urllib3.disable_warnings()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")
EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
KEY_FILE = "keys/gcp-service-account.json"
PROJECT = "gmailpj-357912"
DATASET = "STOCK"
TABLE = "SHAREHOLDER_COMPOSITION"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"
GCS_BUCKET = "stock_data_1930932"
GCS_PREFIX = "edinet/{ticker}/"
LOCAL_CACHE_DIR = Path(r"C:\tmp\shareholder_xbrl")
DATE_CACHE_DIR = Path(r"C:\tmp\edinet_cache\dates")

JST = timezone(timedelta(hours=9))
ASR_DOC_TYPE = "120"  # 有価証券報告書

# 所有者別状況の構造化タグ（jpcrp_cor:）
CATEGORY_PCT_TAGS = {
    "foreign_individual": "PercentageOfShareholdingsForeignIndividuals",
    "foreign_corp": "PercentageOfShareholdingsForeignersOtherThanIndividuals",
    "individual": "PercentageOfShareholdingsIndividualsAndOthers",
    "financial_inst": "PercentageOfShareholdingsFinancialInstitutions",
    "financial_service": "PercentageOfShareholdingsFinancialServiceProviders",
    "other_corp": "PercentageOfShareholdingsOtherCorporations",
    "government": "PercentageOfShareholdingsNationalAndLocalGovernments",
}

# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────


@dataclass
class SharesComposition:
    ticker: str
    fiscal_year_end: Optional[date] = None
    doc_id: Optional[str] = None
    submit_date: Optional[date] = None
    top_shareholder_name: Optional[str] = None
    top_shareholder_ratio: Optional[float] = None
    top_shareholder_is_public: Optional[bool] = None
    top_shareholder_ticker: Optional[str] = None
    foreign_ratio: Optional[float] = None
    individual_ratio: Optional[float] = None
    financial_inst_ratio: Optional[float] = None
    other_corp_ratio: Optional[float] = None
    treasury_ratio: Optional[float] = None
    top10_concentration: Optional[float] = None
    top10_names_json: Optional[str] = None
    has_activist: Optional[bool] = None
    activist_names: Optional[str] = None
    activist_max_score: Optional[int] = None
    extracted_at: Optional[datetime] = None
    error: Optional[str] = None


# ──────────────────────────────────────────────
# XBRL 取得
# ──────────────────────────────────────────────


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    return s


def find_gcs_xbrl(storage_client: storage.Client, ticker: str, submit_year: int) -> Optional[str]:
    """GCS で 有報年 の XBRL blob_name を探す（提出年で一致）。"""
    prefix = GCS_PREFIX.format(ticker=ticker)
    for blob in storage_client.list_blobs(GCS_BUCKET, prefix=prefix):
        name = os.path.basename(blob.name)
        if "_有報年_" not in name or not name.endswith(".xbrl"):
            continue
        m = re.match(r"^\d+_有報年_(\d{8})_", name)
        if not m:
            continue
        if int(m.group(1)[:4]) == submit_year:
            return blob.name
    return None


def download_xbrl_from_gcs(storage_client: storage.Client, blob_name: str, local_dir: Path) -> Optional[Path]:
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / os.path.basename(blob_name)
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path
    blob = storage_client.bucket(GCS_BUCKET).blob(blob_name)
    blob.download_to_filename(str(local_path))
    return local_path


def edinet_list_by_date(sess: requests.Session, d: date, use_cache: bool = True) -> list[dict]:
    """日付ごとの全書類 json。TOBバックフィル時のキャッシュと互換。"""
    if use_cache:
        DATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = DATE_CACHE_DIR / f"{d.isoformat()}.json"
        if cache_path.exists():
            try:
                return json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                pass
    r = sess.get(
        f"{EDINET_BASE}/documents.json",
        params={"date": d.isoformat(), "type": 2, "Subscription-Key": API_KEY},
        timeout=30,
    )
    if r.status_code != 200:
        return []
    results = r.json().get("results", []) or []
    if use_cache:
        try:
            slim = [x for x in results if x.get("secCode") or x.get("docTypeCode") in ("120", "240", "290")]
            json.dump(slim, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
        return slim
    return results


def edinet_download_xbrl(sess: requests.Session, doc_id: str, out_dir: Path) -> Optional[Path]:
    """EDINET API から XBRL ZIP をDL→展開→.xbrl ファイルパスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = out_dir / doc_id
    if extract_dir.exists():
        for root, _, files in os.walk(extract_dir):
            for f in files:
                if f.endswith(".xbrl"):
                    return Path(root) / f
    r = sess.get(
        f"{EDINET_BASE}/documents/{doc_id}",
        params={"type": 1, "Subscription-Key": API_KEY},
        timeout=60,
    )
    if r.status_code != 200:
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        zf.extractall(str(extract_dir))
    except Exception:
        return None
    # PublicDoc を優先（AuditDocを避ける）
    candidates = []
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(".xbrl"):
                candidates.append(Path(root) / f)
    for p in candidates:
        if "PublicDoc" in str(p):
            return p
    return candidates[0] if candidates else None


# ──────────────────────────────────────────────
# XBRL パース
# ──────────────────────────────────────────────


def _get_tag_value(xbrl_text: str, tag_local: str, prefix: str = "jpcrp_cor") -> Optional[str]:
    """構造化数値タグの値を取得。優先度:
    1. OrdinaryShareMember（普通株式）
    2. FilingDateInstant
    3. 任意の最初の非ゼロ値
    4. 最後のマッチ
    """
    full = f"{prefix}:{tag_local}"
    matches = list(re.finditer(
        rf"<{re.escape(full)}([^>]*)>([^<]+)</{re.escape(full)}>", xbrl_text
    ))
    if not matches:
        return None
    # 優先1: 普通株式 context
    for m in matches:
        attrs = m.group(1)
        if "OrdinaryShareMember" in attrs or "OrdinarySharesMember" in attrs:
            return m.group(2).strip()
    # 優先2: FilingDateInstant
    for m in matches:
        if "FilingDateInstant" in m.group(1):
            return m.group(2).strip()
    # 優先3: 最初の非ゼロ値
    for m in matches:
        v = m.group(2).strip()
        try:
            if float(v) != 0.0:
                return v
        except ValueError:
            continue
    return matches[-1].group(2).strip()


def _get_textblock(xbrl_text: str, tag_local: str, prefix: str = "jpcrp_cor") -> Optional[str]:
    """TextBlock（HTML埋め込み）を取得、HTMLタグ付きで返す。"""
    full = f"{prefix}:{tag_local}"
    m = re.search(rf"<{re.escape(full)}[^>]*>(.*?)</{re.escape(full)}>", xbrl_text, re.DOTALL)
    if not m:
        return None
    content = m.group(1)
    # HTMLエンティティ展開
    content = (content.replace("&lt;", "<").replace("&gt;", ">")
                       .replace("&amp;", "&").replace("&nbsp;", " "))
    return content


def _parse_major_shareholders(textblock_html: str) -> list[dict]:
    """大株主テーブル（HTML）からtop10を抽出: [{name, ratio}, ...]"""
    if not textblock_html:
        return []
    soup = BeautifulSoup(textblock_html, "html.parser")
    shareholders = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        # ヘッダー列探索
        header_idx = -1
        name_col = -1
        ratio_col = -1
        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"])
            for j, c in enumerate(cells):
                t = c.get_text(strip=True)
                if name_col < 0 and ("氏名" in t or "名称" in t):
                    name_col = j
                if ratio_col < 0 and ("割合" in t or "比率" in t):
                    ratio_col = j
            if name_col >= 0 and ratio_col >= 0:
                header_idx = i
                break
        if header_idx < 0 or name_col < 0:
            continue
        for row in rows[header_idx + 1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) <= max(name_col, ratio_col):
                continue
            name = cells[name_col].get_text(strip=True)
            if not name or len(name) < 2:
                continue
            if name in ("計", "合計", "―", "－", "-"):
                break  # テーブル終端
            if name.replace(",", "").replace(".", "").isdigit():
                continue
            # 比率パース（常にpercentage表記として /100 する。有報の大株主テーブルは原則 percentage 表記）
            ratio = None
            if ratio_col >= 0 and ratio_col < len(cells):
                rtext = cells[ratio_col].get_text(strip=True)
                rm = re.search(r"(\d+(?:\.\d+)?)", rtext.replace(",", ""))
                if rm:
                    v = float(rm.group(1))
                    ratio = v / 100
            shareholders.append({"name": name, "ratio": ratio})
            if len(shareholders) >= 15:  # 通常10だが安全マージン
                break
        if shareholders:
            break
    return shareholders[:10]


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def parse_xbrl(xbrl_path: Path, ticker: str, activists: list) -> SharesComposition:
    """XBRL ファイルから株主構成データを抽出。"""
    extract = SharesComposition(ticker=ticker, extracted_at=datetime.now(JST))
    try:
        text = xbrl_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        extract.error = f"read fail: {e}"
        return extract

    # 会計年度末・提出日（FilingDateInstant context）
    m = re.search(r'<xbrli:context id="FilingDateInstant"[^>]*>.*?<xbrli:instant>([^<]+)</xbrli:instant>',
                  text, re.DOTALL)
    if m:
        try:
            extract.submit_date = datetime.fromisoformat(m.group(1)).date()
        except ValueError:
            pass
    # 事業年度末: jpdei_cor:CurrentFiscalYearEndDateDEI
    fye_val = _get_tag_value(text, "CurrentFiscalYearEndDateDEI", "jpdei_cor")
    if fye_val:
        try:
            extract.fiscal_year_end = datetime.fromisoformat(fye_val).date()
        except ValueError:
            pass

    # 構造化タグで所有者別カテゴリ比率
    foreign_ind = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["foreign_individual"]))
    foreign_corp = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["foreign_corp"]))
    individual = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["individual"]))
    fin_inst = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["financial_inst"]))
    fin_service = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["financial_service"]))
    other_corp = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["other_corp"]))

    if foreign_ind is not None or foreign_corp is not None:
        extract.foreign_ratio = (foreign_ind or 0) + (foreign_corp or 0)
    extract.individual_ratio = individual
    if fin_inst is not None or fin_service is not None:
        extract.financial_inst_ratio = (fin_inst or 0) + (fin_service or 0)
    extract.other_corp_ratio = other_corp

    # TREASURY_RATIO は構造化タグで取れないため、100% から引いて概算
    known_sum = sum(v for v in [extract.foreign_ratio, extract.individual_ratio,
                                 extract.financial_inst_ratio, extract.other_corp_ratio] if v is not None)
    gov = _to_float(_get_tag_value(text, CATEGORY_PCT_TAGS["government"])) or 0
    if known_sum > 0:
        extract.treasury_ratio = max(0.0, 1.0 - known_sum - gov)

    # 大株主
    major_block = _get_textblock(text, "MajorShareholdersTextBlock")
    if major_block:
        shareholders = _parse_major_shareholders(major_block)
        if shareholders:
            extract.top_shareholder_name = shareholders[0]["name"]
            extract.top_shareholder_ratio = shareholders[0].get("ratio")
            top10 = shareholders[:10]
            extract.top10_names_json = json.dumps(top10, ensure_ascii=False)
            if all(s.get("ratio") is not None for s in top10):
                extract.top10_concentration = sum(s["ratio"] for s in top10)

            # アクティビスト判定
            best_match = None
            matched_names: list[str] = []
            for s in top10:
                name = s["name"]
                if name in SHAREHOLDER_EXCLUSIONS:
                    continue
                m = match_activist(name, activists)
                if m:
                    matched_names.append(m["name"])
                    if best_match is None or m["score"] > best_match["score"]:
                        best_match = m
            if best_match:
                extract.has_activist = True
                extract.activist_names = ",".join(sorted(set(matched_names)))
                extract.activist_max_score = int(best_match["score"])
            else:
                extract.has_activist = False
                extract.activist_max_score = 0

    if not extract.top_shareholder_name and not extract.foreign_ratio:
        extract.error = "no shareholder data extracted"
    return extract


# ──────────────────────────────────────────────
# ticker + year → XBRL 取得パイプライン
# ──────────────────────────────────────────────


def get_xbrl_for_ticker_year(
    ticker: str, year: int,
    sess: requests.Session, storage_client: storage.Client,
) -> Optional[Path]:
    """ticker×提出年で有報XBRLを取得（GCS優先→EDINET API）。"""
    local_subdir = LOCAL_CACHE_DIR / f"{ticker}_{year}"
    if local_subdir.exists():
        for f in local_subdir.rglob("*.xbrl"):
            return f

    # GCS 試行
    try:
        blob_name = find_gcs_xbrl(storage_client, ticker, year)
        if blob_name:
            return download_xbrl_from_gcs(storage_client, blob_name, local_subdir)
    except Exception as e:
        print(f"  [{ticker}/{year}] GCS fail: {e}")

    # EDINET API 試行: 当年3月期決算の有報は翌年6月頃提出
    sec5 = ticker + "0"
    start = date(year, 4, 1)
    end = date(year, 12, 31)
    d = start
    while d <= end:
        docs = edinet_list_by_date(sess, d)
        for doc in docs:
            if doc.get("docTypeCode") == ASR_DOC_TYPE and doc.get("secCode") == sec5:
                time.sleep(0.05)
                return edinet_download_xbrl(sess, doc["docID"], local_subdir)
        d += timedelta(days=1)
    return None


def process_ticker_year(
    ticker: str, year: int,
    sess: requests.Session,
    storage_client: storage.Client,
    activists: list,
) -> SharesComposition:
    xbrl_path = get_xbrl_for_ticker_year(ticker, year, sess, storage_client)
    if not xbrl_path:
        return SharesComposition(ticker=ticker, error=f"no XBRL for year={year}",
                                  extracted_at=datetime.now(JST))
    ext = parse_xbrl(xbrl_path, ticker, activists)
    return ext


# ──────────────────────────────────────────────
# BQ
# ──────────────────────────────────────────────


def get_clients():
    creds = service_account.Credentials.from_service_account_file(
        KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return (
        bigquery.Client(project=PROJECT, credentials=creds),
        storage.Client(project=PROJECT, credentials=creds),
    )


def ensure_table(bq: bigquery.Client) -> None:
    schema = [
        bigquery.SchemaField("TICKER", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("FISCAL_YEAR_END", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("DOC_ID", "STRING"),
        bigquery.SchemaField("SUBMIT_DATE", "DATE"),
        bigquery.SchemaField("TOP_SHAREHOLDER_NAME", "STRING"),
        bigquery.SchemaField("TOP_SHAREHOLDER_RATIO", "FLOAT64"),
        bigquery.SchemaField("TOP_SHAREHOLDER_IS_PUBLIC", "BOOL"),
        bigquery.SchemaField("TOP_SHAREHOLDER_TICKER", "STRING"),
        bigquery.SchemaField("FOREIGN_RATIO", "FLOAT64"),
        bigquery.SchemaField("INDIVIDUAL_RATIO", "FLOAT64"),
        bigquery.SchemaField("FINANCIAL_INST_RATIO", "FLOAT64"),
        bigquery.SchemaField("OTHER_CORP_RATIO", "FLOAT64"),
        bigquery.SchemaField("TREASURY_RATIO", "FLOAT64"),
        bigquery.SchemaField("TOP10_CONCENTRATION", "FLOAT64"),
        bigquery.SchemaField("TOP10_NAMES_JSON", "STRING"),
        bigquery.SchemaField("HAS_ACTIVIST", "BOOL"),
        bigquery.SchemaField("ACTIVIST_NAMES", "STRING"),
        bigquery.SchemaField("ACTIVIST_MAX_SCORE", "INT64"),
        bigquery.SchemaField("EXTRACTED_AT", "TIMESTAMP", mode="REQUIRED"),
    ]
    try:
        bq.get_table(TABLE_ID)
        print(f"[info] table already exists: {TABLE_ID}")
    except Exception:
        table = bigquery.Table(TABLE_ID, schema=schema)
        bq.create_table(table)
        print(f"[info] created: {TABLE_ID}")


def _to_row(ext: SharesComposition) -> dict:
    return {
        "TICKER": ext.ticker,
        "FISCAL_YEAR_END": ext.fiscal_year_end.isoformat() if ext.fiscal_year_end else None,
        "DOC_ID": ext.doc_id,
        "SUBMIT_DATE": ext.submit_date.isoformat() if ext.submit_date else None,
        "TOP_SHAREHOLDER_NAME": ext.top_shareholder_name,
        "TOP_SHAREHOLDER_RATIO": ext.top_shareholder_ratio,
        "TOP_SHAREHOLDER_IS_PUBLIC": ext.top_shareholder_is_public,
        "TOP_SHAREHOLDER_TICKER": ext.top_shareholder_ticker,
        "FOREIGN_RATIO": ext.foreign_ratio,
        "INDIVIDUAL_RATIO": ext.individual_ratio,
        "FINANCIAL_INST_RATIO": ext.financial_inst_ratio,
        "OTHER_CORP_RATIO": ext.other_corp_ratio,
        "TREASURY_RATIO": ext.treasury_ratio,
        "TOP10_CONCENTRATION": ext.top10_concentration,
        "TOP10_NAMES_JSON": ext.top10_names_json,
        "HAS_ACTIVIST": ext.has_activist,
        "ACTIVIST_NAMES": ext.activist_names,
        "ACTIVIST_MAX_SCORE": ext.activist_max_score,
        "EXTRACTED_AT": ext.extracted_at.isoformat() if ext.extracted_at else None,
    }


def insert_rows(bq: bigquery.Client, extracts: list) -> None:
    """バッチ streaming INSERT（DELETE無し）。"""
    rows = [_to_row(e) for e in extracts if e.fiscal_year_end]
    if not rows:
        return
    errors = bq.insert_rows_json(TABLE_ID, rows)
    if errors:
        print(f"  batch insert err (first): {errors[0]}")


def upsert_record(bq: bigquery.Client, ext: SharesComposition) -> None:
    """単一レコード upsert（遅い。バッチINSERTを推奨）。"""
    if not ext.fiscal_year_end:
        print(f"  [{ext.ticker}] skip (no fiscal_year_end)")
        return
    # DELETE + INSERT upsert
    sql_del = f"""
    DELETE FROM `{TABLE_ID}`
    WHERE TICKER = @ticker AND FISCAL_YEAR_END = @fye
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("ticker", "STRING", ext.ticker),
        bigquery.ScalarQueryParameter("fye", "DATE", ext.fiscal_year_end),
    ])
    bq.query(sql_del, job_config=job_config).result()

    row = {
        "TICKER": ext.ticker,
        "FISCAL_YEAR_END": ext.fiscal_year_end.isoformat() if ext.fiscal_year_end else None,
        "DOC_ID": ext.doc_id,
        "SUBMIT_DATE": ext.submit_date.isoformat() if ext.submit_date else None,
        "TOP_SHAREHOLDER_NAME": ext.top_shareholder_name,
        "TOP_SHAREHOLDER_RATIO": ext.top_shareholder_ratio,
        "TOP_SHAREHOLDER_IS_PUBLIC": ext.top_shareholder_is_public,
        "TOP_SHAREHOLDER_TICKER": ext.top_shareholder_ticker,
        "FOREIGN_RATIO": ext.foreign_ratio,
        "INDIVIDUAL_RATIO": ext.individual_ratio,
        "FINANCIAL_INST_RATIO": ext.financial_inst_ratio,
        "OTHER_CORP_RATIO": ext.other_corp_ratio,
        "TREASURY_RATIO": ext.treasury_ratio,
        "TOP10_CONCENTRATION": ext.top10_concentration,
        "TOP10_NAMES_JSON": ext.top10_names_json,
        "HAS_ACTIVIST": ext.has_activist,
        "ACTIVIST_NAMES": ext.activist_names,
        "ACTIVIST_MAX_SCORE": ext.activist_max_score,
        "EXTRACTED_AT": ext.extracted_at.isoformat() if ext.extracted_at else None,
    }
    errors = bq.insert_rows_json(TABLE_ID, [row])
    if errors:
        print(f"  [{ext.ticker}] insert err: {errors}")


# ──────────────────────────────────────────────
# main
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="対象ticker（例: 7203）")
    parser.add_argument("--year", type=int, help="対象提出年（例: 2020）")
    parser.add_argument("--all", action="store_true", help="全銘柄×全年")
    parser.add_argument("--start-year", type=int, default=2013)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-ensure", action="store_true")
    args = parser.parse_args()

    bq, storage_client = get_clients()
    if not args.skip_ensure and not args.dry_run:
        ensure_table(bq)

    print("[info] loading activist master...")
    activists = load_activists()
    print(f"[info] activist count: {len(activists)}")

    sess = _session()

    if args.ticker and args.year:
        tickers_years = [(args.ticker, args.year)]
    elif args.all:
        df = bq.query(f"""
            SELECT DISTINCT TICKER FROM (
              SELECT TICKER FROM `{PROJECT}.STOCK.STOCK_CODE_LIST` WHERE EXCHANGE = 'TSE'
              UNION ALL
              SELECT TICKER FROM `{PROJECT}.STOCK.DELISTED_STOCKS`
            ) ORDER BY TICKER
        """).to_dataframe()
        tickers_years = [
            (t, y) for t in df["TICKER"].tolist() for y in range(args.start_year, args.end_year + 1)
        ]
    else:
        print("Usage: --ticker X --year Y or --all")
        return

    print(f"[info] targets: {len(tickers_years)}")
    for i, (ticker, year) in enumerate(tickers_years, 1):
        if i % 50 == 0:
            print(f"  [{i}/{len(tickers_years)}] {ticker}/{year}")
        try:
            ext = process_ticker_year(ticker, year, sess, storage_client, activists)
        except Exception as e:
            print(f"  [{ticker}/{year}] ERR: {e}")
            continue
        if args.dry_run:
            print(f"  [{ticker}/{year}] extract: {asdict(ext)}")
        else:
            if ext.error:
                continue
            upsert_record(bq, ext)


if __name__ == "__main__":
    main()
