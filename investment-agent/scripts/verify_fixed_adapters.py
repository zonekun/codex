"""
修正アダプター検証スクリプト
- adapters_fixed.json の regex を実際の BQ full_text に対してテスト
- OK/NG を報告し、NGの場合はテキストの該当箇所を表示する
- 追加: BC突合結果CSVからスケール差・フィールドズレのサンプル抽出

Usage:
    PYTHONUTF8=1 python scripts/verify_fixed_adapters.py
    PYTHONUTF8=1 python scripts/verify_fixed_adapters.py --scale-diff   # スケール差10件
    PYTHONUTF8=1 python scripts/verify_fixed_adapters.py --field-offset  # フィールドズレ10件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
from pathlib import Path
from google.cloud import bigquery
from google.oauth2 import service_account

# SSL 回避（社内環境等）
urllib3.disable_warnings()
class _NoVerify(_HA):
    def send(self, req, **kw): kw["verify"] = False; return super().send(req, **kw)
_orig = _req.Session.__init__
def _p(self, *a, **kw): _orig(self, *a, **kw); self.mount("https://", _NoVerify()); self.verify = False
_req.Session.__init__ = _p

KEY_FILE = "keys/gcp-service-account.json"
PROJECT  = "gmailpj-357912"

# ──────────────────────────────────────────────
# BQ
# ──────────────────────────────────────────────

def get_bq() -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return bigquery.Client(project=PROJECT, credentials=creds)


def fetch_full_text(bq: bigquery.Client, ticker: str) -> str:
    """指定ティッカーの最新月次文書 full_text を取得（CHUNK_TEXT を結合）"""
    sql = f"""
    SELECT
        STRING_AGG(CHUNK_TEXT, ' ') AS full_text,
        SUBMISSION_DATE
    FROM (
        SELECT d.CHUNK_TEXT, d.SUBMISSION_DATE
        FROM `{PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED` d
        WHERE d.TICKER = @ticker
          AND d.MAIN_CATEGORY = '月次開示'
        ORDER BY d.SUBMISSION_DATE DESC
        LIMIT 200
    )
    GROUP BY SUBMISSION_DATE
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 1
    """
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    rows = list(bq.query(sql, job_config=job_cfg).result())
    if not rows:
        return ""
    return rows[0].full_text or ""


# ──────────────────────────────────────────────
# regex テスト
# ──────────────────────────────────────────────

def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*\.?\d*", text)


def test_regex(pattern: str, full_text: str, use_last_number: bool = False,
               value_type: str = "number") -> tuple[str | None, str]:
    """
    Returns (extracted_value, status)
    status: "OK", "CAPTURE_OK", "NO_MATCH", "NO_NUMBER"
    """
    try:
        flags = re.DOTALL if ".*?" in pattern or "\\n" not in pattern else 0
        m = re.search(pattern, full_text, flags)
    except re.error as e:
        return None, f"REGEX_ERROR: {e}"

    if not m:
        return None, "NO_MATCH"

    # キャプチャグループがあればそれを使う
    if m.lastindex and m.lastindex >= 1:
        raw = m.group(1).strip()
        nums = _extract_numbers(raw)
        if not nums:
            return raw, "CAPTURE_OK (no digits)"
        if use_last_number:
            return nums[-1], "CAPTURE_OK"
        return nums[0], "CAPTURE_OK"

    # キャプチャグループなし → マッチ後テキストから数字を抽出
    rest = full_text[m.end():]
    nums = _extract_numbers(rest[:80])
    if not nums:
        return None, "NO_NUMBER"
    val = nums[-1] if use_last_number else nums[0]
    return val, "OK"


def show_context(pattern: str, full_text: str, chars: int = 200) -> str:
    """パターン前後のテキストを返す（デバッグ用）"""
    try:
        m = re.search(re.escape(pattern[:30]), full_text, re.DOTALL)
    except Exception:
        m = None
    if m:
        start = max(0, m.start() - 50)
        end = min(len(full_text), m.end() + chars)
        return repr(full_text[start:end])

    # パターンの最初のリテラル部分を探す
    lit = re.split(r"[\\(\[.*?+]", pattern)[0]
    if lit:
        idx = full_text.find(lit)
        if idx >= 0:
            return repr(full_text[max(0, idx-30):idx+chars])
    return "(context not found)"


# ──────────────────────────────────────────────
# メイン検証
# ──────────────────────────────────────────────

def verify_adapters(bq: bigquery.Client, fixed_path: Path) -> None:
    adapters = json.loads(fixed_path.read_text(encoding="utf-8"))
    print(f"\n{'='*60}")
    print(f"修正アダプター検証: {len(adapters)} ティッカー")
    print(f"{'='*60}\n")

    for ticker, adapter in adapters.items():
        print(f"── {ticker} ({adapter.get('doc_title_pattern','')}) ──")
        full_text = fetch_full_text(bq, ticker)
        if not full_text:
            print(f"  ⚠ BQ文書なし\n")
            continue
        print(f"  full_text長: {len(full_text):,} chars")

        for field in adapter.get("fields", []):
            key = field["key"]
            pattern = field["row_label_regex"]
            use_last = field.get("use_last_number", False)
            vtype = field.get("value_type", "number")

            val, status = test_regex(pattern, full_text, use_last, vtype)

            if status in ("OK", "CAPTURE_OK"):
                print(f"  ✅ {key}: {val} ({status})")
            else:
                print(f"  ❌ {key}: {status}")
                print(f"     pattern: {pattern[:80]}")
                # 最初のリテラル部分を検索して前後を表示
                lit_match = re.match(r"([^\[\\.*()+?{}|^$]+)", pattern)
                if lit_match:
                    lit = lit_match.group(1)
                    idx = full_text.find(lit)
                    if idx >= 0:
                        snippet = full_text[max(0,idx-20):idx+150]
                        print(f"     先頭リテラル '{lit}' 周辺: {repr(snippet[:150])}")
                    else:
                        print(f"     先頭リテラル '{lit}' がfull_text中に見つからない")
        print()


# ──────────────────────────────────────────────
# スケール差・フィールドズレ分析
# ──────────────────────────────────────────────

def analyze_scale_diff(csv_path: Path, n: int = 10) -> None:
    """diff が 50〜999 の NG 行を抽出（単位スケール差の可能性が高い）"""
    import csv
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("match") != "NG":
                continue
            try:
                diff = float(row.get("diff", "999999"))
            except ValueError:
                continue
            if 50 <= diff <= 10000:
                rows.append(row)
    rows.sort(key=lambda r: float(r.get("diff","0")), reverse=True)

    print(f"\n{'='*60}")
    print(f"スケール差NG サンプル (diff 50〜10000): {len(rows)} 件 → 先頭{n}件")
    print(f"{'='*60}")
    for r in rows[:n]:
        print(f"  {r['ticker']} | {r['year_month']} | {r['our_field']}")
        print(f"    our={r['our_value']}  bc_field={r['bc_field']}  bc={r['bc_value']}  diff={r['diff']}")
    print()


def analyze_field_offset(csv_path: Path, n: int = 10) -> None:
    """diff が 0.5〜5 の NG 行を抽出（フィールドマッチング微小ズレの可能性）"""
    import csv
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("match") != "NG":
                continue
            try:
                diff = float(row.get("diff", "999999"))
            except ValueError:
                continue
            if 0.5 < diff <= 5.0:
                rows.append(row)
    rows.sort(key=lambda r: float(r.get("diff","0")))

    print(f"\n{'='*60}")
    print(f"フィールドズレNG サンプル (diff 0.5〜5.0): {len(rows)} 件 → 先頭{n}件")
    print(f"{'='*60}")
    for r in rows[:n]:
        print(f"  {r['ticker']} | {r['year_month']} | {r['our_field']}")
        print(f"    our={r['our_value']}  bc_field={r['bc_field']}  bc={r['bc_value']}  diff={r['diff']}")
    print()


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-diff",   action="store_true", help="スケール差10件を表示")
    parser.add_argument("--field-offset", action="store_true", help="フィールドズレ10件を表示")
    parser.add_argument("--csv", default=None, help="突合結果CSV (デフォルト: 最新の C:\\tmp\\buffett_compare_*.csv)")
    args = parser.parse_args()

    # CSV パスを解決
    csv_path: Path | None = None
    if args.csv:
        csv_path = Path(args.csv)
    else:
        candidates = sorted(Path(r"C:\tmp").glob("buffett_compare_*.csv"))
        if candidates:
            csv_path = candidates[-1]

    bq = get_bq()

    if args.scale_diff or args.field_offset:
        if not csv_path or not csv_path.exists():
            print(f"ERROR: 突合CSVが見つかりません。--csv で指定してください。")
            sys.exit(1)
        print(f"CSV: {csv_path}")
        if args.scale_diff:
            analyze_scale_diff(csv_path)
        if args.field_offset:
            analyze_field_offset(csv_path)
    else:
        fixed_path = Path(r"C:\tmp\adapters_fixed.json")
        if not fixed_path.exists():
            print(f"ERROR: {fixed_path} が見つかりません")
            sys.exit(1)
        verify_adapters(bq, fixed_path)

    print("完了")


if __name__ == "__main__":
    main()
