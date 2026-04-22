"""XBRL → J-Quants fin_summary 変換パイプライン（20社テスト用、レガシー）.

本番用は `convert_to_fin_summary.py`。こちらはテスト専用。
勘定科目マッピング（TAG_CANDIDATES, ADAPTERS, CURRENT_DURATION_CONTEXTS,
EXCLUDE_CONTEXT_PATTERNS）は `xbrl_mapping.py` から import し、
単一ソースで管理する。
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import structlog
from lxml import etree
from google.cloud import storage
from google.oauth2 import service_account

logger = structlog.get_logger()

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
GCS_BUCKET = "stock_data_1930932"

# 勘定科目マッピング: xbrl_mapping から単一ソースで取得
sys.path.insert(0, str(SCRIPT_DIR))
from xbrl_mapping import (  # type: ignore  # noqa: E402
    TAG_CANDIDATES,
    ADAPTERS,
    CURRENT_DURATION_CONTEXTS,
    EXCLUDE_CONTEXT_PATTERNS,
)


def get_gcs_client() -> storage.Client:
    """GCS クライアントを取得する."""
    creds = service_account.Credentials.from_service_account_file(
        str(PROJECT_DIR / "keys" / "gcp-service-account.json")
    )
    return storage.Client(credentials=creds, project="gmailpj-357912")


def load_adapters() -> dict[str, dict]:
    """企業別アダプタ定義を返す（xbrl_mapping.ADAPTERSを流用）."""
    return ADAPTERS


def find_xbrl_files(
    client: storage.Client, ticker: str
) -> list[dict[str, str]]:
    """指定銘柄の有報/四半期報告書XBRLパスを返す."""
    bucket = client.bucket(GCS_BUCKET)
    prefix = f"edinet/{ticker}/{ticker}_有報"
    blobs = list(bucket.list_blobs(prefix=prefix))
    xbrl_blobs = [b for b in blobs if b.name.endswith(".xbrl")]

    results = []
    for blob in xbrl_blobs:
        name = blob.name
        if "有報年" in name or "有価証券報告書" in name:
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

        if doc_type not in ("2Q", "3Q", "FY"):
            continue

        match = re.search(r"_(\d{4}-\d{2}-\d{2})_01_", name)
        period_end = match.group(1) if match else ""

        results.append({
            "blob_name": name,
            "doc_type": doc_type,
            "period_end": period_end,
        })

    results.sort(key=lambda x: x["period_end"])
    return results


def is_valid_context(context_id: str) -> bool:
    """当期・連結のDurationコンテキストかどうか判定する."""
    # 除外パターンチェック
    for pattern in EXCLUDE_CONTEXT_PATTERNS:
        if pattern in context_id:
            return False
    # 当期Durationパターンチェック
    return any(ctx in context_id for ctx in CURRENT_DURATION_CONTEXTS)


def parse_xbrl_elements(xbrl_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
    """XBRLバイトをパースし、全要素をローカル名でグループ化して返す.

    Returns:
        {"NetSales": [{"value": "...", "context": "...", "decimals": "..."}, ...], ...}
    """
    elements: dict[str, list[dict[str, Any]]] = {}

    try:
        root = etree.fromstring(xbrl_bytes)
    except etree.XMLSyntaxError:
        logger.error("xml_parse_error")
        return {}

    for elem in root.iter():
        # ローカル名を取得
        local_name = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else None
        if not local_name:
            continue

        context_ref = elem.get("contextRef")
        if not context_ref:
            continue

        text = elem.text
        if not text or not text.strip():
            continue

        decimals = elem.get("decimals", "")

        if local_name not in elements:
            elements[local_name] = []
        elements[local_name].append({
            "value": text.strip(),
            "context": context_ref,
            "decimals": decimals,
            "ns": etree.QName(elem.tag).namespace or "",
        })

    return elements


def extract_pl(
    elements: dict[str, list[dict[str, Any]]],
    adapter: dict | None = None,
) -> dict[str, dict[str, Any] | None]:
    """パース済み要素からPL5項目を抽出する."""
    result: dict[str, dict[str, Any] | None] = {}

    for jq_col, default_tags in TAG_CANDIDATES.items():
        # アダプタ定義があれば優先
        if adapter and jq_col in adapter.get("tag_map", {}):
            tags = adapter["tag_map"][jq_col] + default_tags
        else:
            tags = default_tags

        found = None
        for tag_name in tags:
            if tag_name not in elements:
                continue

            entries = elements[tag_name]
            # 当期・連結のDurationのみフィルタ
            valid_entries = [
                e for e in entries
                if is_valid_context(e["context"])
            ]

            if not valid_entries:
                continue

            # 最初の有効エントリを採用
            entry = valid_entries[0]
            try:
                raw_val = entry["value"]
                dec = entry["decimals"]
                if jq_col == "EARNINGS_PER_SHARE":
                    found = {
                        "value": float(raw_val),
                        "tag": tag_name,
                        "context": entry["context"],
                    }
                else:
                    found = {
                        "value": int(float(raw_val)),
                        "tag": tag_name,
                        "context": entry["context"],
                    }
            except (ValueError, TypeError):
                continue
            break  # 最初にヒットしたタグで確定

        result[jq_col] = found

    return result


def load_jquants_reference() -> dict[str, list[dict[str, str]]]:
    """ローカルCSVからJ-Quantsの参照値をロードする."""
    csv_path = PROJECT_DIR / "data" / "csv" / "fin_summary_sample_20stocks.csv"
    data: dict[str, list[dict[str, str]]] = {}
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["LOCAL_CODE"]
            if ticker not in data:
                data[ticker] = []
            data[ticker].append(row)
    return data


def compare_values(
    xbrl_data: dict[str, Any] | None,
    jq_val_str: str,
    is_eps: bool = False,
) -> str:
    """XBRL値とJ-Quants値を比較して結果文字列を返す."""
    if xbrl_data is None:
        if not jq_val_str or jq_val_str == "":
            return "BOTH_NULL"  # 両方NULL → 正常
        return "MISS"
    if not jq_val_str or jq_val_str == "":
        return "JQ_NULL"

    xbrl_val = xbrl_data["value"]
    try:
        jq_val = float(jq_val_str)
    except (ValueError, TypeError):
        return "JQ_ERR"

    if is_eps:
        if abs(jq_val) > 0:
            diff = abs(xbrl_val - jq_val) / abs(jq_val) * 100
            return "OK" if diff < 2 else f"DIFF_{diff:.1f}%"
        return "OK" if abs(xbrl_val) < 1 else "DIFF"
    else:
        if jq_val == xbrl_val:
            return "OK"
        if abs(jq_val) > 0:
            diff = abs(xbrl_val - jq_val) / abs(jq_val) * 100
            return "OK" if diff < 0.01 else f"DIFF_{diff:.1f}%"
        return "DIFF"


def generate_adapter_suggestion(
    ticker: str, elements: dict, pl_result: dict, jq_row: dict
) -> dict | None:
    """不一致項目からアダプタ候補を自動生成する."""
    suggestions: dict[str, list[str]] = {}

    for jq_col in ["NET_SALES", "OPERATING_PROFIT", "PROFIT"]:
        jq_val_str = jq_row.get(jq_col, "")
        if not jq_val_str:
            continue
        try:
            jq_val = int(float(jq_val_str))
        except (ValueError, TypeError):
            continue

        xbrl_data = pl_result.get(jq_col)
        if xbrl_data and xbrl_data["value"] == jq_val:
            continue  # 一致

        # 全要素からJ-Quants値と一致するタグを逆引き
        for tag_name, entries in elements.items():
            for entry in entries:
                if not is_valid_context(entry["context"]):
                    continue
                try:
                    val = int(float(entry["value"]))
                except (ValueError, TypeError):
                    continue
                if val == jq_val:
                    if jq_col not in suggestions:
                        suggestions[jq_col] = []
                    if tag_name not in suggestions[jq_col]:
                        suggestions[jq_col].append(tag_name)

    return {"tag_map": suggestions} if suggestions else None


def main() -> None:
    """20社×(2Q/3Q/FY)のXBRL→PL抽出テストを実行する."""
    client = get_gcs_client()
    adapters = load_adapters()
    jq_ref = load_jquants_reference()

    tickers = sorted(jq_ref.keys())

    total = 0
    ok_count = 0
    miss_count = 0
    diff_count = 0
    jq_null_count = 0
    both_null_count = 0
    miss_details: list[dict] = []
    diff_details: list[dict] = []
    adapter_suggestions: dict[str, dict] = {}

    for ticker in tickers:
        adapter = adapters.get(ticker)
        xbrl_files = find_xbrl_files(client, ticker)
        if not xbrl_files:
            logger.warning("no_xbrl_files", ticker=ticker)
            continue

        jq_rows = jq_ref[ticker]

        for xbrl_info in xbrl_files:
            blob_name = xbrl_info["blob_name"]
            period_end = xbrl_info["period_end"]
            doc_type = xbrl_info["doc_type"]

            # J-Quants対応行を探す
            matched_jq = None
            for row in jq_rows:
                if row.get("CURRENT_PERIOD_END_DATE") == period_end:
                    matched_jq = row
                    break
            if not matched_jq:
                continue

            # XBRLパース
            try:
                bucket = client.bucket(GCS_BUCKET)
                xbrl_bytes = bucket.blob(blob_name).download_as_bytes()
                elements = parse_xbrl_elements(xbrl_bytes)
            except Exception as e:
                logger.error("parse_error", ticker=ticker, error=str(e))
                continue

            # PL抽出
            pl_result = extract_pl(elements, adapter)

            # 突合
            results_str = []
            for jq_col in ["NET_SALES", "OPERATING_PROFIT", "ORDINARY_PROFIT", "PROFIT", "EARNINGS_PER_SHARE"]:
                status = compare_values(
                    pl_result[jq_col],
                    matched_jq.get(jq_col, ""),
                    is_eps=(jq_col == "EARNINGS_PER_SHARE"),
                )
                results_str.append(f"{jq_col}={status}")
                total += 1
                if status == "OK":
                    ok_count += 1
                elif status.startswith("DIFF"):
                    diff_count += 1
                    xv = pl_result[jq_col]["value"] if pl_result[jq_col] else "N/A"
                    jv = matched_jq.get(jq_col, "")
                    diff_details.append({
                        "ticker": ticker, "doc_type": doc_type,
                        "period_end": period_end, "field": jq_col,
                        "xbrl_val": str(xv), "jq_val": jv,
                        "diff_pct": status.replace("DIFF_", ""),
                    })
                elif status == "MISS":
                    miss_count += 1
                    jv = matched_jq.get(jq_col, "")
                    miss_details.append({
                        "ticker": ticker, "doc_type": doc_type,
                        "period_end": period_end, "field": jq_col,
                        "jq_val": jv,
                    })
                elif status == "JQ_NULL":
                    jq_null_count += 1
                elif status == "BOTH_NULL":
                    both_null_count += 1

            # アダプタ候補生成
            suggestion = generate_adapter_suggestion(
                ticker, elements, pl_result, matched_jq
            )
            if suggestion and suggestion["tag_map"]:
                if ticker not in adapter_suggestions:
                    adapter_suggestions[ticker] = {"tag_map": {}}
                for col, tags in suggestion["tag_map"].items():
                    if col not in adapter_suggestions[ticker]["tag_map"]:
                        adapter_suggestions[ticker]["tag_map"][col] = []
                    for t in tags:
                        if t not in adapter_suggestions[ticker]["tag_map"][col]:
                            adapter_suggestions[ticker]["tag_map"][col].append(t)

            print(f"{ticker} {doc_type} {period_end}: {' | '.join(results_str)}")

    # サマリー
    excluded = jq_null_count + both_null_count
    effective = total - excluded
    print(f"\n{'='*60}")
    print(f"TOTAL: {total} checks ({jq_null_count} JQ_NULL, {both_null_count} BOTH_NULL excluded)")
    print(f"EFFECTIVE: {effective} checks")
    if effective > 0:
        print(f"  OK:   {ok_count} ({ok_count/effective*100:.1f}%)")
        print(f"  DIFF: {diff_count} ({diff_count/effective*100:.1f}%)")
        print(f"  MISS: {miss_count} ({miss_count/effective*100:.1f}%)")

    # MISS詳細テーブル出力
    if miss_details:
        print(f"\n--- MISS Details ({len(miss_details)} items) ---")
        print(f"{'TICKER':<8} {'TYPE':<4} {'PERIOD_END':<12} {'FIELD':<22} {'JQ_VALUE':>18}")
        print("-" * 70)
        for md in miss_details:
            print(f"{md['ticker']:<8} {md['doc_type']:<4} {md['period_end']:<12} {md['field']:<22} {md['jq_val']:>18}")

    # DIFF詳細テーブル出力
    if diff_details:
        print(f"\n--- DIFF Details ({len(diff_details)} items) ---")
        print(f"{'TICKER':<8} {'TYPE':<4} {'PERIOD_END':<12} {'FIELD':<22} {'XBRL':>18} {'JQ':>18} {'DIFF%':>8}")
        print("-" * 90)
        for dd in diff_details:
            print(f"{dd['ticker']:<8} {dd['doc_type']:<4} {dd['period_end']:<12} {dd['field']:<22} {dd['xbrl_val']:>18} {dd['jq_val']:>18} {dd['diff_pct']:>8}")

    # アダプタ候補保存
    if adapter_suggestions:
        out_path = SCRIPT_DIR / "adapter_suggestions.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(adapter_suggestions, f, indent=2, ensure_ascii=False)
        print(f"\nAdapter suggestions: {out_path}")
        print(f"  {len(adapter_suggestions)} companies need adapters")


if __name__ == "__main__":
    main()
