"""XBRL勘定科目マッピング本体 + 全銘柄一括検証スクリプト.

EDINET 有報XBRL（jppfs_cor）用の勘定科目マッピング定義と抽出ロジックの単一ソース。
他モジュール（convert_to_fin_summary.py / extract_pipeline.py）は本ファイルから
TAG_CANDIDATES / ADAPTERS / extract_pl() 等を import する。

主な定義:
    - TAG_CANDIDATES: PL5項目 × 優先度順XBRLタグリスト
    - ADAPTERS: 企業固有タグ（1375/6758/7203）
    - OPERATING_REVENUE_ADD_TAGS: 小売業の営業収入合算タグ
    - CURRENT_DURATION_CONTEXTS / EXCLUDE_CONTEXT_PATTERNS: context判定
    - parse_xbrl / extract_pl / find_all_xbrl_files / is_valid_context: 抽出ロジック

本スクリプトを直接実行すると全銘柄×全期間の検証モード（Excel/J-Quants突合）が走る。
TDnet決算短信iXBRL用マッピングは `scripts/zaraba_tdnet_poller.py` の TDNET_TAG_MAP
（別系統）で管理。TAG_CANDIDATES更新時は同期必須。

Usage (Local):
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/xbrl_to_jquants/xbrl_mapping.py
    # 再開: PYTHONUTF8=1 ... xbrl_mapping.py --resume
    # NG銘柄のアダプタ候補逆引き: PYTHONUTF8=1 ... xbrl_mapping.py --reverse
"""

import csv
import io
import json as json_module
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from lxml import etree

# ============================================================
# 環境判定・認証
# ============================================================
try:
    from google.colab import auth
    RUNTIME = "colab"
except ImportError:
    RUNTIME = "local"

if RUNTIME == "colab":
    auth.authenticate_user()
    from google.cloud import bigquery, storage
    bq = bigquery.Client(project="gmailpj-357912")
    gcs = storage.Client(project="gmailpj-357912")
else:
    from google.cloud import bigquery, storage
    from google.oauth2 import service_account
    _key = "C:/gdrive/claude/investment-agent/keys/gcp-service-account.json"
    _creds = service_account.Credentials.from_service_account_file(_key)
    bq = bigquery.Client(credentials=_creds, project="gmailpj-357912")
    gcs = storage.Client(credentials=_creds, project="gmailpj-357912")

GCS_BUCKET = "stock_data_1930932"
bucket = gcs.bucket(GCS_BUCKET)

# ============================================================
# 設定
# ============================================================
# M = 全銘柄  /  T = テスト（20社のみ）
MODE = "M"

TEST_TICKERS = [
    "2802", "2914", "3382", "4063", "4502", "4568", "5401", "6098",
    "6367", "6501", "6758", "6861", "6902", "7203", "7267", "7741",
    "8001", "8035", "9433", "9984",
]

# ============================================================
# タグ候補（SummaryOfBusinessResults優先）
# ============================================================
TAG_CANDIDATES: dict[str, list[str]] = {
    "NET_SALES": [
        # --- Summary系（最優先） ---
        "NetSalesSummaryOfBusinessResults",
        "RevenueIFRSSummaryOfBusinessResults",
        "RevenuesUSGAAPSummaryOfBusinessResults",
        "OperatingRevenue1SummaryOfBusinessResults",
        "GrossOperatingRevenueSummaryOfBusinessResults",
        "NetSalesOfCompletedConstructionContractsSummaryOfBusinessResults",
        "OperatingRevenue2SummaryOfBusinessResults",
        "RevenueSummaryOfBusinessResults",
        "BusinessRevenueSummaryOfBusinessResults",
        # --- 業種別（NetSalesより優先） ---
        "OrdinaryIncomeBNK",              # 銀行業: 経常収益
        "OperatingIncomeINS",             # 保険業: 経常収益
        "NetSalesOfCompletedConstructionContractsCNS",  # 建設業
        # --- 汎用 ---
        "NetSales",
        "RevenueIFRS",
        "NetSalesIFRS",
        "Revenue2IFRS",
        "IncomeIFRSKeyFinancialData",
        "RevenueKeyFinancialData",
        "OperatingRevenue1",
        "RevenuesFromExternalCustomers",
        "BusinessRevenues",
        "OperatingRevenue2",
        "Revenue",
        "RevenueFromContractsWithCustomers",
    ],
    "OPERATING_PROFIT": [
        "OperatingIncomeLossSummaryOfBusinessResults",
        "OperatingProfitLossIFRSSummaryOfBusinessResults",
        "OperatingIncomeLossUSGAAPSummaryOfBusinessResults",
        "OperatingIncomeIFRSSummaryOfBusinessResults",
        "OperatingIncomeLossIFRSSummaryOfBusinessResults",
        "BusinessProfitLossIFRSSummaryOfBusinessResults",
        "BusinessProfitLossIFRS",
        "BusinessProfitIFRS",
        "CoreOperatingIncomeIFRSKeyFinancialData",
        "OperatingProfitIFRSKeyFinancialData",
        "OperatingIncome",
        "OperatingIncomeLoss",
        "OperatingIncomeIFRS",
        "OperatingIncomeLossIFRS",
        "OperatingIncomeLossUSGAAP",
        "OperatingProfitLossIFRS",
    ],
    "ORDINARY_PROFIT": [
        "OrdinaryIncomeLossSummaryOfBusinessResults",
        "OrdinaryIncome",
        "OrdinaryIncomeSummaryOfBusinessResults",
    ],
    "PROFIT": [
        "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
        "NetIncomeLossAttributableToOwnersOfParentUSGAAPSummaryOfBusinessResults",
        "NetIncomeLossSummaryOfBusinessResults",
        "ProfitLossIFRSSummaryOfBusinessResults",
        "ProfitLossAttributableToOwnersOfParent",
        "ProfitLossAttributableToOwnersOfParentIFRS",
        "ProfitLoss",
        "ProfitLossIFRS",
    ],
    "EARNINGS_PER_SHARE": [
        "BasicEarningsLossPerShareSummaryOfBusinessResults",
        "BasicEarningsLossPerShareIFRSSummaryOfBusinessResults",
        "BasicEarningsLossPerShareUSGAAPSummaryOfBusinessResults",
        "BasicEarningsLossPerShareIFRS",
        "BasicAndDilutedEarningsLossPerShareIFRS",
        "BasicEarningsLossPerShare",
    ],
}

# 企業別アダプタ（タグ候補の先頭に挿入）
ADAPTERS: dict[str, dict[str, list[str]]] = {
    "1375": {
        "NET_SALES": [
            "IncomeIFRSKeyFinancialData",
            "Revenue2IFRS",
        ],
    },
    "6758": {
        "NET_SALES": [
            "SalesAndFinancialServicesRevenueIFRSKeyFinancialData",
            "SalesAndFinancialServicesRevenueIFRS",
        ],
    },
    "7203": {
        "NET_SALES": [
            "OperatingRevenuesIFRSKeyFinancialData",
            "TotalNetRevenuesIFRS",
            "SalesRevenuesIFRS",
        ],
    },
}

CHECKPOINT_INTERVAL = 100  # 何社ごとにチェックポイント保存するか

CURRENT_DURATION_CONTEXTS = {
    "CurrentYearDuration",
    "CurrentYTDDuration",
    "InterimDuration",
    "CurrentQuarterDuration",
}

EXCLUDE_CONTEXT_PATTERNS = ["NonConsolidated", "Prior", "Member"]

PL_FIELDS = ["NET_SALES", "OPERATING_PROFIT", "ORDINARY_PROFIT", "PROFIT", "EARNINGS_PER_SHARE"]


# ============================================================
# ヘルパー関数
# ============================================================
def is_valid_context(ctx: str) -> bool:
    """当期・連結のDurationコンテキストか判定."""
    for p in EXCLUDE_CONTEXT_PATTERNS:
        if p in ctx:
            return False
    return any(c in ctx for c in CURRENT_DURATION_CONTEXTS)


def parse_xbrl(xbrl_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
    """XBRLバイトをパースし要素をローカル名でグループ化."""
    try:
        root = etree.fromstring(xbrl_bytes)
    except etree.XMLSyntaxError:
        return {}

    elements: dict[str, list[dict[str, Any]]] = {}
    for elem in root.iter():
        local = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else None
        if not local:
            continue
        ctx = elem.get("contextRef")
        if not ctx:
            continue
        text = elem.text
        if not text or not text.strip():
            continue
        if local not in elements:
            elements[local] = []
        elements[local].append({
            "value": text.strip(),
            "context": ctx,
            "decimals": elem.get("decimals", ""),
        })
    return elements


# 営業収入タグ候補（売上高に加算して営業収益を算出）
OPERATING_REVENUE_ADD_TAGS = [
    "OperatingRevenue2SummaryOfBusinessResults",
    "OperatingRevenue2",
]


def _find_first_tag(
    elements: dict[str, list[dict]], tags: list[str], is_eps: bool = False,
) -> dict[str, Any] | None:
    """タグ候補リストから最初にマッチするタグの値を返す."""
    for tag in tags:
        if tag not in elements:
            continue
        valid = [e for e in elements[tag] if is_valid_context(e["context"])]
        if not valid:
            continue
        entry = valid[0]
        try:
            raw = entry["value"]
            if is_eps:
                return {"value": float(raw), "tag": tag, "context": entry["context"]}
            return {"value": int(float(raw)), "tag": tag, "context": entry["context"]}
        except (ValueError, TypeError):
            continue
    return None


def extract_pl(
    elements: dict[str, list[dict]], ticker: str
) -> dict[str, dict[str, Any] | None]:
    """PL5項目を抽出."""
    result: dict[str, dict[str, Any] | None] = {}
    adapter = ADAPTERS.get(ticker, {})

    for field, default_tags in TAG_CANDIDATES.items():
        tags = adapter.get(field, []) + default_tags
        is_eps = field == "EARNINGS_PER_SHARE"
        found = _find_first_tag(elements, tags, is_eps)

        # NET_SALES: 営業収入（OperatingRevenue2）を加算して営業収益を算出
        # ベースタグが NetSales* の場合のみ（OperatingRevenue*/GrossOperating* は既に合算済み）
        if field == "NET_SALES" and found is not None and found["tag"].startswith("NetSales"):
            add_tags = adapter.get("_NET_SALES_ADD", OPERATING_REVENUE_ADD_TAGS)
            add_val = _find_first_tag(elements, add_tags)
            if add_val is not None and add_val["value"] != 0:
                found = {
                    "value": found["value"] + add_val["value"],
                    "tag": f'{found["tag"]}+{add_val["tag"]}',
                    "context": found["context"],
                }

        result[field] = found
    return result


def find_all_xbrl_files(ticker: str) -> list[dict[str, str]]:
    """指定銘柄の有報/四半期報告書XBRLパスを返す."""
    results = []
    for doc_prefix, doc_type_default in [
        (f"{ticker}_有報年", "FY"),
        (f"{ticker}_有報四", None),  # 四半期/半期は名前から判定
    ]:
        prefix = f"edinet/{ticker}/{doc_prefix}"
        blobs = list(bucket.list_blobs(prefix=prefix))
        for b in blobs:
            if not b.name.endswith(".xbrl"):
                continue
            name = b.name
            # 書類種別判定
            if doc_type_default == "FY":
                doc_type = "FY"
            elif "半期報告書" in name:
                doc_type = "2Q"
            elif "第3四半期" in name:
                doc_type = "3Q"
            elif "第2四半期" in name:
                doc_type = "2Q"
            elif "第1四半期" in name:
                doc_type = "1Q"
            else:
                continue
            # 期末日抽出
            m = re.search(r"_(\d{4}-\d{2}-\d{2})_01_", name)
            if not m:
                continue
            results.append({
                "blob_name": name,
                "doc_type": doc_type,
                "period_end": m.group(1),
            })
    results.sort(key=lambda x: x["period_end"])
    return results


def get_jq_data(tickers: list[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """BQからfin_summaryを全期間取得."""
    where = "WHERE 1=1"
    if tickers:
        ticker_list = ",".join(f"'{t}'" for t in tickers)
        where += f" AND LOCAL_CODE IN ({ticker_list})"
    sql = f"""
    SELECT *
    FROM `gmailpj-357912.STOCK.fin_summary`
    {where}
    ORDER BY LOCAL_CODE, DISCLOSED_DATE DESC
    """
    print("BQからfin_summaryを取得中...")
    df = bq.query(sql).to_dataframe()
    print(f"  {len(df)}行取得")

    # 銘柄×期末日ごとに最新開示のみ残す（修正開示対応）
    df = df.sort_values(["LOCAL_CODE", "CURRENT_PERIOD_END_DATE", "DISCLOSED_DATE"], ascending=[True, True, False])
    df = df.drop_duplicates(subset=["LOCAL_CODE", "CURRENT_PERIOD_END_DATE"], keep="first")

    data: dict[str, list[dict[str, str]]] = {}
    for _, row in df.iterrows():
        t = row["LOCAL_CODE"]
        if t not in data:
            data[t] = []
        data[t].append({col: str(row[col]) if row[col] is not None else "" for col in df.columns})
    return data


def save_checkpoint(
    path: str, idx: int, ng_rows: list[dict], summary: dict,
    processed: int, skipped: int, matched_files: int,
) -> None:
    """中間結果をJSONに保存."""
    data = {
        "last_index": idx,
        "ng_rows": ng_rows,
        "summary": summary,
        "processed": processed,
        "skipped": skipped,
        "matched_files": matched_files,
    }
    with open(path, "w", encoding="utf-8") as f:
        json_module.dump(data, f, ensure_ascii=False)
    print(f"  [checkpoint] {path} saved (idx={idx}, processed={processed})")


def load_checkpoint(path: str) -> dict | None:
    """チェックポイントを読み込む. 無ければNone."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json_module.load(f)


def compare(xbrl_val: Any, jq_str: str, is_eps: bool = False) -> str:
    """比較結果を返す."""
    jq_empty = not jq_str or jq_str in ("", "<NA>", "nan", "None", "NaN")
    if xbrl_val is None:
        return "BOTH_NULL" if jq_empty else "MISS"
    if jq_empty:
        return "JQ_NULL"
    try:
        jq_val = float(jq_str)
    except (ValueError, TypeError):
        return "JQ_ERR"
    # 差異率計算
    if abs(jq_val) > 0:
        diff_pct = abs(float(xbrl_val) - jq_val) / abs(jq_val) * 100
    elif xbrl_val == 0:
        return "OK"
    else:
        return "NG"
    # 許容範囲: 1%未満 → OK（端数処理・表示単位差）
    return "OK" if diff_pct < 1.0 else "NG"


# ============================================================
# メイン処理
# ============================================================
def main() -> None:
    """一括検証実行."""
    resume_mode = "--resume" in sys.argv
    filter_tickers = TEST_TICKERS if MODE == "T" else None
    jq_data = get_jq_data(filter_tickers)
    tickers = TEST_TICKERS if MODE == "T" else sorted(jq_data.keys())
    print(f"対象銘柄: {len(tickers)}社 (MODE={MODE})")

    today = datetime.now().strftime("%Y%m%d")
    if RUNTIME == "local":
        out_dir = "C:/tmp/xbrl_validate"
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = "."
    ng_path = f"{out_dir}/mapping_ng_{today}.csv"
    summary_path = f"{out_dir}/mapping_summary_{today}.csv"
    checkpoint_path = f"{out_dir}/checkpoint_{today}.json"

    # チェックポイントから復元
    start_idx = 0
    ng_rows: list[dict] = []
    summary = {"total": 0, "ok": 0, "ng": 0, "miss": 0, "both_null": 0, "jq_null": 0}
    processed = 0
    skipped_ticker = 0
    matched_files = 0

    if resume_mode:
        cp = load_checkpoint(checkpoint_path)
        if cp:
            start_idx = cp["last_index"] + 1
            ng_rows = cp["ng_rows"]
            summary = cp["summary"]
            processed = cp["processed"]
            skipped_ticker = cp["skipped"]
            matched_files = cp["matched_files"]
            print(f"[resume] checkpoint読み込み: idx={start_idx}から再開 (処理済み{processed}社)")
        else:
            print("[resume] checkpointなし。最初から実行します。")

    for i, ticker in enumerate(tickers):
        if i < start_idx:
            continue
        if i % CHECKPOINT_INTERVAL == 0:
            print(f"  進捗: {i}/{len(tickers)} ({processed}社処理, {matched_files}ファイル突合)")
            if i > start_idx:
                save_checkpoint(checkpoint_path, i - 1, ng_rows, summary, processed, skipped_ticker, matched_files)

        # GCSから全XBRL取得
        xbrl_files = find_all_xbrl_files(ticker)
        if not xbrl_files:
            skipped_ticker += 1
            continue

        jq_rows = jq_data.get(ticker, [])
        if not jq_rows:
            skipped_ticker += 1
            continue

        processed += 1

        for xbrl_info in xbrl_files:
            blob_name = xbrl_info["blob_name"]
            period_end = xbrl_info["period_end"]
            doc_type = xbrl_info["doc_type"]

            # J-Quants対応データ
            matched_jq = None
            for row in jq_rows:
                if row.get("CURRENT_PERIOD_END_DATE") == period_end:
                    matched_jq = row
                    break
            if not matched_jq:
                continue

            # XBRLパース
            try:
                xbrl_bytes = bucket.blob(blob_name).download_as_bytes()
                elements = parse_xbrl(xbrl_bytes)
            except Exception as e:
                ng_rows.append({
                    "ticker": ticker, "period_end": period_end, "doc_type": doc_type,
                    "field": "PARSE_ERROR", "xbrl_value": "", "xbrl_tag": "",
                    "jq_value": "", "status": "ERROR", "note": str(e)[:100],
                })
                continue

            matched_files += 1
            pl = extract_pl(elements, ticker)

            for field in PL_FIELDS:
                jq_str = matched_jq.get(field, "")
                xbrl_data = pl.get(field)
                xbrl_val = xbrl_data["value"] if xbrl_data else None
                xbrl_tag = xbrl_data["tag"] if xbrl_data else ""

                status = compare(xbrl_val, jq_str, is_eps=(field == "EARNINGS_PER_SHARE"))
                summary["total"] += 1
                if status == "OK":
                    summary["ok"] += 1
                elif status == "NG":
                    summary["ng"] += 1
                    ng_rows.append({
                        "ticker": ticker,
                        "period_end": period_end,
                        "doc_type": doc_type,
                        "field": field,
                        "xbrl_value": str(xbrl_val) if xbrl_val is not None else "",
                        "xbrl_tag": xbrl_tag,
                        "jq_value": jq_str,
                        "status": "NG",
                        "note": f"diff={abs(float(xbrl_val or 0) - float(jq_str or 0)):,.0f}" if jq_str else "",
                    })
                elif status == "MISS":
                    summary["miss"] += 1
                    ng_rows.append({
                        "ticker": ticker,
                        "period_end": period_end,
                        "doc_type": doc_type,
                        "field": field,
                        "xbrl_value": "",
                        "xbrl_tag": "",
                        "jq_value": jq_str,
                        "status": "MISS",
                        "note": "XBRLにタグなし",
                    })
                elif status == "BOTH_NULL":
                    summary["both_null"] += 1
                elif status == "JQ_NULL":
                    summary["jq_null"] += 1

    # 最終チェックポイント保存
    save_checkpoint(checkpoint_path, len(tickers) - 1, ng_rows, summary, processed, skipped_ticker, matched_files)

    # NG CSV出力
    fieldnames = [
        "ticker", "period_end", "doc_type", "field", "xbrl_value", "xbrl_tag",
        "jq_value", "status", "note",
    ]
    if ng_rows:
        with open(ng_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(ng_rows)

    # サマリー
    eff = summary["total"] - summary["both_null"] - summary["jq_null"]
    print(f"\n{'='*60}")
    print(f"処理: {processed}社 / スキップ: {skipped_ticker}社 / 突合ファイル: {matched_files}件")
    print(f"TOTAL: {summary['total']} checks")
    print(f"  BOTH_NULL: {summary['both_null']}  JQ_NULL: {summary['jq_null']}")
    print(f"EFFECTIVE: {eff}")
    if eff > 0:
        print(f"  OK:   {summary['ok']} ({summary['ok']/eff*100:.1f}%)")
        print(f"  NG:   {summary['ng']} ({summary['ng']/eff*100:.1f}%)")
        print(f"  MISS: {summary['miss']} ({summary['miss']/eff*100:.1f}%)")
    print(f"\nNG log: {ng_path} ({len(ng_rows)}行)")

    # サマリーCSV
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerow(["processed", processed])
        writer.writerow(["skipped", skipped_ticker])
        writer.writerow(["total_checks", summary["total"]])
        writer.writerow(["effective", eff])
        writer.writerow(["ok", summary["ok"]])
        writer.writerow(["ng", summary["ng"]])
        writer.writerow(["miss", summary["miss"]])
        writer.writerow(["both_null", summary["both_null"]])
        writer.writerow(["jq_null", summary["jq_null"]])
        if eff > 0:
            writer.writerow(["ok_rate", f"{summary['ok']/eff*100:.1f}%"])
    print(f"Summary: {summary_path}")

    # 正常完了: チェックポイント削除
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
        print(f"[cleanup] {checkpoint_path} 削除")


def reverse_lookup() -> None:
    """NG CSVからアダプタ候補を逆引きする（--reverse モード）.

    mainの突合結果CSVを入力とし、NG/MISS銘柄のXBRLを1回だけDLして
    J-Quants値と一致するタグを探す。
    """
    from collections import defaultdict
    from glob import glob as globfn

    # 最新のNG CSVを探す（ローカルはC:/tmp/xbrl_validate/を優先）
    if RUNTIME == "local":
        ng_files = sorted(globfn("C:/tmp/xbrl_validate/mapping_ng_*.csv"), reverse=True)
        if not ng_files:
            ng_files = sorted(globfn("mapping_ng_*.csv"), reverse=True)
    else:
        ng_files = sorted(globfn("mapping_ng_*.csv"), reverse=True)
    if not ng_files:
        print("ERROR: mapping_ng_*.csv が見つかりません。先に main を実行してください。")
        return
    ng_path = ng_files[0]
    print(f"入力: {ng_path}")

    with open(ng_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        ng_rows = list(reader)

    # NG/MISSのみ、J-Quants値がある行
    targets = [r for r in ng_rows if r["status"] in ("NG", "MISS") and r.get("jq_value")]
    ticker_ng_map: dict[str, list[dict]] = defaultdict(list)
    for r in targets:
        ticker_ng_map[r["ticker"]].append(r)

    total = len(ticker_ng_map)
    print(f"逆引き対象: {total}銘柄 ({len(targets)}行)")

    adapter_suggestions: dict[str, dict[str, list[str]]] = {}
    for idx, (t, rows) in enumerate(ticker_ng_map.items()):
        if idx % 50 == 0:
            print(f"  逆引き進捗: {idx}/{total}")

        all_files = find_all_xbrl_files(t)
        fy_files = [f for f in all_files if f["doc_type"] == "FY"]
        blob_name = fy_files[-1]["blob_name"] if fy_files else None
        if not blob_name:
            continue
        try:
            xbrl_bytes = bucket.blob(blob_name).download_as_bytes()
            elements = parse_xbrl(xbrl_bytes)
        except Exception:
            continue

        for ng in rows:
            field = ng["field"]
            jq_str = ng["jq_value"]
            try:
                jq_val = int(float(jq_str)) if field != "EARNINGS_PER_SHARE" else float(jq_str)
            except (ValueError, TypeError):
                continue

            candidates = []
            for tag_name, entries in elements.items():
                for entry in entries:
                    if not is_valid_context(entry["context"]):
                        continue
                    try:
                        val = int(float(entry["value"])) if field != "EARNINGS_PER_SHARE" else float(entry["value"])
                    except (ValueError, TypeError):
                        continue
                    if field != "EARNINGS_PER_SHARE":
                        if val == jq_val:
                            candidates.append(tag_name)
                    else:
                        if abs(val - jq_val) < 0.01:
                            candidates.append(tag_name)
            ng["adapter_candidate"] = "; ".join(candidates[:5]) if candidates else ""

            if candidates and field in PL_FIELDS:
                if t not in adapter_suggestions:
                    adapter_suggestions[t] = {}
                if field not in adapter_suggestions[t]:
                    adapter_suggestions[t][field] = []
                for c in candidates[:3]:
                    if c not in adapter_suggestions[t][field]:
                        adapter_suggestions[t][field].append(c)

    # アダプタ候補JSON出力
    today = datetime.now().strftime("%Y%m%d")
    if RUNTIME == "local":
        out_dir = "C:/tmp/xbrl_validate"
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = "."
    if adapter_suggestions:
        adapter_path = f"{out_dir}/adapter_suggestions_{today}.json"
        with open(adapter_path, "w", encoding="utf-8") as f:
            json_module.dump(adapter_suggestions, f, indent=2, ensure_ascii=False)
        print(f"Adapter suggestions: {adapter_path} ({len(adapter_suggestions)}社)")

    # NG CSV上書き（adapter_candidate列追加）
    out_path = ng_path.replace(".csv", "_with_adapters.csv")
    fieldnames = [
        "ticker", "period_end", "doc_type", "field", "xbrl_value", "xbrl_tag",
        "jq_value", "status", "note", "adapter_candidate",
    ]
    for r in ng_rows:
        if "adapter_candidate" not in r:
            r["adapter_candidate"] = ""
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ng_rows)
    print(f"出力: {out_path} ({len(ng_rows)}行)")


if __name__ == "__main__":
    if "--reverse" in sys.argv:
        reverse_lookup()
    else:
        main()
