"""
activist_edinet_scan.py
=======================
EDINETの大量保有報告書一覧からアクティビストファンドの保有を検出するスクリプト。

処理概要:
  1. activists.csv を読み込み、マッチング対象のファンド名リストを構築
  2. EDINET API documents.json を日付ループで取得（府令コード060 = 大量保有報告書）
  3. filerName を正規化 + ファジーマッチング でアクティビスト判定
  4. 結果を C:\\tmp\\activist_edinet_results.csv に保存

使用方法:
  PYTHONUTF8=1 python scripts/activist_edinet_scan.py
  PYTHONUTF8=1 python scripts/activist_edinet_scan.py --start 20240101 --end 20260329
  PYTHONUTF8=1 python scripts/activist_edinet_scan.py --start 20240101  # 今日まで
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jaconv
import pandas as pd
import requests
import structlog
from rapidfuzz import fuzz, process

# ─── 設定 ──────────────────────────────────────────
API_KEY = os.environ.get("EDINET_API_KEY", "0607081d467e4c7b8229ac4f41ec3408")
EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
ACTIVISTS_CSV = Path(__file__).parent.parent / "data/master/activists.csv"
OUTPUT_PATH = Path(__file__).parent.parent / "data/logs/activist_edinet_scan.csv"
FUZZY_THRESHOLD = 85          # ファジーマッチスコアの下限（0-100）
SLEEP_SEC = 0.3               # API呼び出し間隔（秒）
JST = timezone(timedelta(hours=9), "JST")

# 府令コード060 が大量保有報告書
TAIRYOUHO_ORDINANCE = "060"

log = structlog.get_logger()


# ─── テキスト正規化 ──────────────────────────────────
def normalize(text: str, keep_spaces: bool = False) -> str:
    """全角/半角・カナ統一・記号除去の正規化.

    Args:
        keep_spaces: True の場合スペースを保持（単語境界マッチ用）
    """
    if not text:
        return ""
    # 半角→全角カタカナ
    t = jaconv.h2z(text, kana=True, ascii=False, digit=False)
    # ひらがな→カタカナ
    t = jaconv.hira2kata(t)
    # 全角英数→半角
    t = jaconv.z2h(t, kana=False, ascii=True, digit=True)
    # よくある区切り文字を統一（スペース区切り文字はスペースに変換）
    t = t.replace("・", " ").replace("･", " ").replace("　", " ")
    t = t.replace("（株）", "").replace("(株)", "").replace("株式会社", "")
    t = t.replace("合同会社", "").replace("有限会社", "").replace("LLC", "").replace("Ltd", "")
    t = t.replace(".", " ").replace(",", " ").replace("、", " ").replace("。", "")
    if not keep_spaces:
        t = t.replace(" ", "")
    return t.upper().strip()


# ─── アクティビストリスト読み込み ────────────────────
def load_activists() -> list[dict]:
    """activists.csv を読み込んで正規化名も付加して返す."""
    df = pd.read_csv(ACTIVISTS_CSV, encoding="utf-8")
    activists = []
    for _, row in df.iterrows():
        activists.append({
            "region": row["REGION"],
            "name": row["NAME"],
            "norm": normalize(row["NAME"]),
            "category": row["CATEGORY"],
        })
    log.info("アクティビストリスト読み込み完了", count=len(activists))
    return activists


# ─── EDINET API ──────────────────────────────────────
def edinet_get(url: str, params: dict, max_retry: int = 5) -> requests.Response:
    """EDINET API GETリクエスト（指数バックオフ付きリトライ）."""
    for attempt in range(max_retry):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code < 500:
                return resp
            raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}")
        except Exception as e:
            if attempt == max_retry - 1:
                raise
            wait = (2 ** attempt) * 5
            log.warning("EDINET APIリトライ", attempt=attempt + 1, error=str(e), wait_sec=wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_documents_for_date(date_str: str) -> list[dict]:
    """指定日の大量保有報告書一覧を取得する.

    Args:
        date_str: YYYY-MM-DD形式

    Returns:
        書類情報の辞書リスト
    """
    url = f"{EDINET_BASE}/documents.json"
    params = {
        "date": date_str,
        "type": 2,          # type=2: 提出書類一覧（メタデータのみ）
        "Subscription-Key": API_KEY,
    }
    resp = edinet_get(url, params)
    if resp.status_code != 200:
        log.warning("documents.json 取得失敗", date=date_str, status=resp.status_code)
        return []

    data = resp.json()
    results = data.get("results", [])

    # 府令コード060（大量保有報告書）のみ抽出
    docs = [
        r for r in results
        if r.get("ordinanceCode") == TAIRYOUHO_ORDINANCE
    ]
    return docs


# ─── マッチング ──────────────────────────────────────
def match_activist(
    filer_name: str,
    activists: list[dict],
) -> Optional[dict]:
    """filerName に対してアクティビストマッチングを行う.

    Returns:
        マッチした場合: {"name": ..., "region": ..., "score": ..., "method": ...}
        マッチなし: None
    """
    norm_filer = normalize(filer_name)
    # スペース保持版（単語境界マッチ用）
    norm_filer_words = normalize(filer_name, keep_spaces=True).split()

    # 完全一致（正規化後）
    for act in activists:
        if act["norm"] == norm_filer:
            return {"name": act["name"], "region": act["region"], "score": 100, "method": "exact"}

    # 部分一致（正規化後）
    for act in activists:
        act_norm = act["norm"]
        if not act_norm:
            continue
        if len(act_norm) >= 4:
            # 4文字以上: 通常の部分文字列マッチ
            if act_norm in norm_filer:
                return {"name": act["name"], "region": act["region"], "score": 95, "method": "partial"}
        else:
            # 3文字以下: 単語境界マッチ（"LIM" が先頭単語の場合のみ）
            if norm_filer_words and norm_filer_words[0] == act_norm:
                return {"name": act["name"], "region": act["region"], "score": 93, "method": "word_start"}
        # 逆方向（filer名がact名に含まれる）: 4文字以上のみ
        if norm_filer and len(norm_filer) >= 4 and norm_filer in act_norm:
            return {"name": act["name"], "region": act["region"], "score": 90, "method": "partial_rev"}

    # ファジーマッチ（partial_ratioは共通部分文字列で誤マッチするためratio使用）
    norm_names = [act["norm"] for act in activists]
    if norm_filer and len(norm_filer) >= 4:   # 極短文字列はスキップ
        best = process.extractOne(norm_filer, norm_names, scorer=fuzz.ratio)
        if best and best[1] >= FUZZY_THRESHOLD:
            idx = norm_names.index(best[0])
            return {
                "name": activists[idx]["name"],
                "region": activists[idx]["region"],
                "score": best[1],
                "method": "fuzzy",
            }

    return None


# ─── 日付レンジ生成 ──────────────────────────────────
def date_range(start: str, end: str) -> list[str]:
    """YYYYMMDD形式 → YYYY-MM-DD形式の日付リストを返す（土日含む、APIが弾く）."""
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    dates = []
    cur = s
    while cur <= e:
        # 土曜(5)・日曜(6)はEDINET提出なし → スキップ
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ─── メイン ──────────────────────────────────────────
def main() -> None:
    """メイン処理."""
    parser = argparse.ArgumentParser(description="EDINET大量保有報告書アクティビストスキャン")
    parser.add_argument("--start", default="20230101", help="開始日 YYYYMMDD (デフォルト: 20230101)")
    parser.add_argument("--end", default=datetime.now(JST).strftime("%Y%m%d"), help="終了日 YYYYMMDD (デフォルト: 今日)")
    args = parser.parse_args()

    log.info("スキャン開始", start=args.start, end=args.end)

    activists = load_activists()
    dates = date_range(args.start, args.end)
    log.info("対象営業日数", count=len(dates))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    total_docs = 0
    matched_docs = 0

    for i, date_str in enumerate(dates):
        docs = fetch_documents_for_date(date_str)
        total_docs += len(docs)

        for doc in docs:
            filer_name = doc.get("filerName", "") or ""
            sec_code = doc.get("secCode", "") or ""
            company_name = doc.get("edinetCode", "") or ""  # 発行者EDINETコード
            submitter_name = doc.get("submitterName", "") or ""
            doc_description = doc.get("docDescription", "") or ""
            doc_id = doc.get("docID", "") or ""

            # filerName と submitterName 両方でマッチング
            match = match_activist(filer_name, activists) or match_activist(submitter_name, activists)

            if match:
                matched_docs += 1

            rows.append({
                "date": date_str,
                "is_activist": bool(match),
                "activist_name": match["name"] if match else "",
                "activist_region": match["region"] if match else "",
                "match_score": match["score"] if match else "",
                "match_method": match["method"] if match else "",
                "filer_name": filer_name,
                "submitter_name": submitter_name,
                "sec_code": sec_code[:4] if sec_code else "",
                "doc_description": doc_description,
                "doc_id": doc_id,
            })

        # 進捗表示（50日ごと）
        if (i + 1) % 50 == 0:
            log.info("進捗", processed=i + 1, total=len(dates), total_docs=total_docs, matched=matched_docs)

        time.sleep(SLEEP_SEC)

    # CSV保存
    df_out = pd.DataFrame(rows)
    df_out.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    log.info(
        "スキャン完了",
        total_dates=len(dates),
        total_docs=total_docs,
        matched=matched_docs,
        output=str(OUTPUT_PATH),
    )

    total_all = len(df_out)
    print(f"\n全{total_all}件保存（うちアクティビスト: {matched_docs}件）→ {OUTPUT_PATH}")
    if matched_docs > 0:
        print("\n=== アクティビスト別件数 ===")
        summary = (
            df_out[df_out["is_activist"]]
            .groupby(["activist_name", "activist_region"])["date"]
            .count()
            .rename("件数")
        )
        print(summary.to_string())


if __name__ == "__main__":
    main()
