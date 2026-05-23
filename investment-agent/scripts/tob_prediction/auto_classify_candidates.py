"""創業家資産管理会社候補 自動判定スクリプト.

family_holding_candidates.csv の判定列を自動付与する。

判定ルール:
  前処理: 発行体が廃止済み銘柄 (DELISTED_STOCKS) → 除外
  R_EXCL: BQ EXTEND で現在 LISTED_CORP の名前 → 除外済み（スキップ）
  R0: 有限/合同会社 → ASSET_MGMT（全件）
  R1: edinet大量保有報告 filer_name 照合 → ASSET_MGMT
  R2: 会社名キーワードパターン → ASSET_MGMT
  R3: 区分=株式会社5%+ かつ REAL_TOP_NAME（信託口スキップ後実質筆頭）と一致 → ASSET_MGMT（第一位みなしオーナー）
  残り: 要確認

Usage:
    PYTHONUTF8=1 uv run python scripts/tob_prediction/auto_classify_candidates.py --dry-run
    PYTHONUTF8=1 uv run python scripts/tob_prediction/auto_classify_candidates.py
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd
import structlog
from google.cloud import bigquery, storage
from google.oauth2 import service_account

from src.core.config import settings

logger = structlog.get_logger()

PROJECT = "gmailpj-357912"
EXTEND_TABLE = f"{PROJECT}.STOCK.SHAREHOLDER_COMPOSITION_EXTEND"
SC_TABLE = f"{PROJECT}.STOCK.SHAREHOLDER_COMPOSITION"
DELISTED_TABLE = f"{PROJECT}.STOCK.DELISTED_STOCKS"
GCS_BUCKET = "stock_data_1930932"
GCS_BACKFILL_PATHS = [
    "edinet_delay/backfill.csv",
    "edinet_delay/backfill_20210101.csv",
]
INPUT_CSV = Path(r"C:\tmp\tob_prediction\family_holding_candidates.csv")
OUTPUT_CSV = Path(r"C:\tmp\tob_prediction\family_holding_candidates_classified.csv")
DROPBOX_DIR = Path(r"C:\Users\zonekun\Dropbox\stock\temp\tob_prediction")

# R2 判定キーワード
_HOLDING_KEYWORDS = [
    "ホールディングス", "ホールディング",
    "holdings", "Holdings", "HOLDINGS",
    "資産管理", "財産管理", "財務管理", "投資管理",
    "インベストメント", "investment", "Investment",
    "キャピタル", "capital", "Capital",
    "アセット", "asset", "Asset",
    "事務所",
]

_LEGAL_PREFIXES = [
    "株式会社", "㈱", "（株）", "(株)", "(株）", "（株)",
    "(株）", "（株)", "合同会社", "有限会社", "合資会社",
]
_LEGAL_SUFFIXES = ["株式会社", "㈱", "（株）", "(株)", "合同会社", "有限会社", "合資会社"]

_ALL_SPACES_RE = re.compile(r"[\s　\xa0]+")
# 注釈サフィックス: （注）, （注1）, （注）３, (注)２ など
_ANNOTATION_RE = re.compile(r"[（(]注[^（(）)]*[）)][^\s（(）)]*$")
# 旧社名注記: （現ソニーグループ株式会社） など
_CURRENT_NAME_RE = re.compile(r"[（(]現[^（(）)]*[）)]\s*$")


def _normalize_fullwidth(name: str) -> str:
    result = []
    for ch in name:
        cp = ord(ch)
        if 0xFF21 <= cp <= 0xFF3A:      # Ａ-Ｚ → A-Z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF41 <= cp <= 0xFF5A:    # ａ-ｚ → a-z
            result.append(chr(cp - 0xFEE0))
        elif 0xFF10 <= cp <= 0xFF19:    # ０-９ → 0-9
            result.append(chr(cp - 0xFEE0))
        elif cp == 0xFF06:              # ＆ → & (三井Ｅ＆Ｓ等の銘柄名対応)
            result.append('&')
        else:
            result.append(ch)
    return "".join(result)


def _normalize(name: str) -> str:
    """全角→ASCII + 全スペース除去 + 法人格プレフィックス/サフィックス除去 + 注釈除去 + upper."""
    name = _normalize_fullwidth(name)
    name = _ALL_SPACES_RE.sub("", name)
    # 法人格 PREFIX 除去
    for prefix in _LEGAL_PREFIXES:
        norm_prefix = _ALL_SPACES_RE.sub("", prefix)
        if name.upper().startswith(norm_prefix.upper()):
            name = name[len(norm_prefix):]
            break
    # 注釈サフィックス除去: （注）, （注1）, （注）３, (注)２ など
    name = _ANNOTATION_RE.sub("", name)
    # 旧社名注記除去: （現ソニーグループ株式会社） など
    name = _CURRENT_NAME_RE.sub("", name)
    # 法人格 SUFFIX 除去（プレフィックスで取れなかった場合）
    for suffix in _LEGAL_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.upper()


def _strip_prefix(name: str) -> str:
    """法人格プレフィックスのみ除去（大小維持）."""
    for prefix in _LEGAL_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def build_clients() -> tuple[bigquery.Client, storage.Client]:
    creds = service_account.Credentials.from_service_account_file(
        settings.google_application_credentials
    )
    return (
        bigquery.Client(project=PROJECT, credentials=creds),
        storage.Client(project=PROJECT, credentials=creds),
    )


def load_listed_corp_names(bq: bigquery.Client) -> set[str]:
    """BQ EXTEND で現在 LISTED_CORP の名前セットを返す."""
    df = bq.query(f"""
        SELECT NAME FROM `{EXTEND_TABLE}` WHERE TYPE = 'LISTED_CORP'
    """).to_dataframe()
    names = set(df["NAME"].tolist())
    logger.info("listed_corp_loaded", count=len(names))
    return names


def load_broader_listed_normalized() -> set[str]:
    """data/master/listed_company_names.csv + corporate_vehicles.csv から正規化済み名セットを返す.

    ①上場銘柄名(EXTEND 誤分類補完) + ②Claude 知識ベースの非上場グループ子会社
    stock_name に _normalize() を直接適用し ＆→& 等の全角記号変換差異を吸収する。
    """
    listed_path = Path("data/master/listed_company_names.csv")
    vehicles_path = Path("data/master/corporate_vehicles.csv")

    df_listed = pd.read_csv(listed_path, encoding="utf-8")
    names = set(df_listed["stock_name"].dropna().apply(_normalize).tolist())

    df_vehicles = pd.read_csv(vehicles_path, encoding="utf-8")
    names |= set(df_vehicles["name"].dropna().apply(_normalize).tolist())

    logger.info("broader_listed_loaded", count=len(names))
    return names


def load_delisted_tickers(bq: bigquery.Client) -> set[str]:
    """BQ DELISTED_STOCKS から廃止済み TICKER セットを返す."""
    df = bq.query(f"""
        SELECT DISTINCT TICKER FROM `{DELISTED_TABLE}`
    """).to_dataframe()
    tickers = set(df["TICKER"].tolist())
    logger.info("delisted_tickers_loaded", count=len(tickers))
    return tickers


def load_real_top_holders(bq: bigquery.Client) -> dict[str, str]:
    """SHAREHOLDER_COMPOSITION から最新年度の REAL_TOP_NAME を {ticker: name} で返す.

    信託口スキップ後の実質筆頭株主名（R3 第一位株主判定に使用）。
    """
    df = bq.query(f"""
        SELECT TICKER, REAL_TOP_NAME
        FROM (
            SELECT TICKER, REAL_TOP_NAME,
                   ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY FISCAL_YEAR_END DESC) AS rn
            FROM `{SC_TABLE}`
            WHERE REAL_TOP_NAME IS NOT NULL
        )
        WHERE rn = 1
    """).to_dataframe()
    result = dict(zip(df["TICKER"], df["REAL_TOP_NAME"]))
    logger.info("real_top_holders_loaded", count=len(result))
    return result


def load_backfill_filers(gcs: storage.Client) -> dict[tuple[str, str], list[str]]:
    """GCS backfill CSV を読み込み {(security_code, normalized_filer)} → [raw_filer] のマップを返す."""
    bucket = gcs.bucket(GCS_BUCKET)
    mapping: dict[tuple[str, str], list[str]] = {}
    for path in GCS_BACKFILL_PATHS:
        data = bucket.blob(path).download_as_text(encoding="utf-8")
        for row in csv.DictReader(io.StringIO(data)):
            code = row["security_code"].strip().lstrip("0") or row["security_code"].strip()
            filer = row["filer_name"].strip()
            key = (code, _normalize(filer))
            mapping.setdefault(key, []).append(filer)
    total = sum(len(v) for v in mapping.values())
    logger.info("backfill_loaded", unique_keys=len(mapping), total_rows=total)
    return mapping


def _apply_r2(name: str) -> bool:
    """R2: 法人格除去後の名前にキーワードが含まれるか."""
    stripped = _strip_prefix(name)
    stripped_norm = _normalize_fullwidth(stripped)
    for kw in _HOLDING_KEYWORDS:
        kw_norm = _normalize_fullwidth(kw)
        if kw_norm.lower() in stripped_norm.lower():
            return True
    return False


def classify(
    df: pd.DataFrame,
    listed_names: set[str],
    broader_listed_normalized: set[str],
    filer_map: dict[tuple[str, str], list[str]],
    real_top_map: dict[str, str],
) -> pd.DataFrame:
    """判定・判定根拠列を付与して返す."""
    df = df.copy()
    judgements = []
    bases = []

    for _, row in df.iterrows():
        name: str = str(row["株主名"])
        kubun: str = str(row["区分"])
        ticker: str = str(row["発行体TICKER"]).strip()

        # R_EXCL: EXTEND LISTED_CORP + data/master/listed_company_names.csv 正規化照合
        # 後者で EXTEND 誤分類（PRIVATE_CORP に残った上場企業、例: 三井E&S）を補完
        if name in listed_names or _normalize(name) in broader_listed_normalized:
            judgements.append("除外済(LISTED_CORP)")
            bases.append("R_EXCL")
            continue

        # R0: 有限/合同会社は全件 ASSET_MGMT
        if kubun == "有限/合同":
            judgements.append("ASSET_MGMT")
            bases.append("R0-有限合同")
            continue

        # R1: backfill filer_name照合
        norm_name = _normalize(name)
        # security_code は先頭ゼロ除去して比較
        ticker_norm = ticker.lstrip("0") or ticker
        if (ticker_norm, norm_name) in filer_map:
            judgements.append("ASSET_MGMT")
            bases.append("R1-大量保有報告")
            continue

        # R2: キーワードパターン
        if _apply_r2(name):
            judgements.append("ASSET_MGMT")
            bases.append("R2-キーワード")
            continue

        # R3: 第一位みなしオーナー（区分=株式会社5%+ かつ REAL_TOP_NAME と名前一致）
        # REAL_TOP_NAME = 信託口スキップ後の実質筆頭株主（Plan C で BQ に追加）
        if kubun == "株式会社5%+" and ticker in real_top_map:
            real_top = real_top_map[ticker]
            if _normalize(name) == _normalize(real_top):
                judgements.append("ASSET_MGMT")
                bases.append("R3-第一位株主")
                continue

        # 残り
        judgements.append("要確認")
        bases.append("未判定")

    df["判定"] = judgements
    df["判定根拠"] = bases
    return df


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bq, gcs = build_clients()

    logger.info("loading_listed_corp_names")
    listed_names = load_listed_corp_names(bq)

    logger.info("loading_broader_listed_names")
    broader_listed_normalized = load_broader_listed_normalized()

    logger.info("loading_delisted_tickers")
    delisted_tickers = load_delisted_tickers(bq)

    logger.info("loading_real_top_holders")
    real_top_map = load_real_top_holders(bq)

    logger.info("loading_backfill")
    filer_map = load_backfill_filers(gcs)

    logger.info("reading_candidates", path=str(INPUT_CSV))
    df = pd.read_csv(INPUT_CSV, encoding="utf-8-sig")
    logger.info("candidates_loaded", rows=len(df))

    # 廃止済み発行体を除外（TOB予測対象外）
    before = len(df)
    df = df[~df["発行体TICKER"].isin(delisted_tickers)].copy()
    excluded_delisted = before - len(df)
    logger.info("delisted_issuers_excluded", excluded=excluded_delisted, remaining=len(df))

    df = classify(df, listed_names, broader_listed_normalized, filer_map, real_top_map)

    # 集計
    counts = df["判定"].value_counts()
    print("\n=== 判定結果 ===")
    for k, v in counts.items():
        print(f"  {k}: {v}件")
    print(f"  合計: {len(df)}件")
    print(f"  除外済(発行体廃止済み): {excluded_delisted}件（集計外）")

    # サンプル表示
    for label in ["ASSET_MGMT", "ASSET_MGMT候補", "要確認", "除外済(LISTED_CORP)"]:
        sub = df[df["判定"] == label]
        if sub.empty:
            continue
        print(f"\n--- {label} サンプル5件 ---")
        print(sub[["発行体TICKER", "発行体名", "株主名", "最大保有比率", "判定根拠"]].head(5).to_string(index=False))

    # R3第一位株主のみ別出力（レビュー用）
    r3_sub = df[df["判定根拠"] == "R3-第一位株主"]
    if not r3_sub.empty:
        print(f"\n--- R3-第一位株主 全{len(r3_sub)}件 ---")
        print(r3_sub[["発行体TICKER", "発行体名", "株主名", "最大保有比率"]].to_string(index=False))

    if args.dry_run:
        print("\n--dry-run: CSV出力をスキップ")
        return

    out_cols = ["発行体TICKER", "発行体名", "株主名", "最大保有比率", "区分", "判定", "判定根拠"]
    df[out_cols].to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    logger.info("output_saved", path=str(OUTPUT_CSV), rows=len(df))
    print(f"\n出力: {OUTPUT_CSV}")

    import shutil
    dropbox_dest = DROPBOX_DIR / OUTPUT_CSV.name
    shutil.copy2(OUTPUT_CSV, dropbox_dest)
    logger.info("dropbox_copied", path=str(dropbox_dest))


if __name__ == "__main__":
    main()
