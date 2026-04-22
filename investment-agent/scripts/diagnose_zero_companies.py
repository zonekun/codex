"""
TDNET文書あり→抽出0件 の企業を診断:
アダプター確認 → BQテキスト取得 → regex試行 → 失敗原因分類

実行: PYTHONUTF8=1 uv run python scripts/diagnose_zero_companies.py [--batch 0]
"""
import json
import re
import sys
import argparse
from google.oauth2 import service_account
from google.cloud import storage, bigquery

KEY_FILE = r"G:\マイドライブ\claude\investment-agent\keys\gcp-service-account.json"
BUCKET = "stock_data_1930932"

creds = service_account.Credentials.from_service_account_file(KEY_FILE)
gcs = storage.Client(credentials=creds, project="gmailpj-357912")
bq = bigquery.Client(credentials=creds, project="gmailpj-357912")
bucket = gcs.bucket(BUCKET)

ZERO_TICKERS = [
    "212A", "2154", "2294", "2311", "245A", "2590", "2678",
    "3032", "3064", "3066", "3080", "3093", "3099", "3141", "3181", "3191", "3199", "3391", "3608",
    "386A", "4058", "4374", "4415", "4776",
    "6040", "6548", "6561",
    "7059", "7062", "7162", "7359", "7561", "7640", "7683", "7823", "7918",
    "8198", "8252", "8255", "8267", "8742", "8914",
    "9022", "9252", "9519", "9759", "9956",
]

parser = argparse.ArgumentParser()
parser.add_argument("--batch", type=int, default=-1, help="バッチ番号(0,1,2...), -1=全件")
args = parser.parse_args()

BATCH_SIZE = 10
if args.batch >= 0:
    start = args.batch * BATCH_SIZE
    tickers = ZERO_TICKERS[start:start + BATCH_SIZE]
else:
    tickers = ZERO_TICKERS

_FW2HW = str.maketrans(
    "（）！＂＃＄％＆＇＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～"
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "()!\"#$%&'*+,-./:;<=>?@[\\]^_`{|}~"
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz",
)

def normalize(text: str) -> str:
    text = text.replace("\u3000", " ")
    return text.translate(_FW2HW)

def get_adapter(ticker: str) -> dict | None:
    try:
        blob = bucket.blob(f"monthlydata/{ticker}/extract_adapter.json")
        return json.loads(blob.download_as_text())
    except Exception:
        return None

def get_latest_text(ticker: str) -> tuple | None:
    sql = """
    SELECT SUBMISSION_DATE, DOC_TITLE,
      STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = @ticker
      AND (MAIN_CATEGORY = '月次開示' OR EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '月次開示'))
      AND SUBMISSION_DATE >= '2025-01-01'
    GROUP BY TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT 1
    """
    cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    rows = list(bq.query(sql, job_config=cfg))
    if not rows:
        return None
    row = rows[0]
    return str(row["SUBMISSION_DATE"]), row["DOC_TITLE"], row["full_text"]

print(f"診断対象: {len(tickers)}社 (batch={args.batch})\n")
all_match = []
partial_match = []
all_fail = []
no_adapter = []
wrong_src = []
no_doc = []

for ticker in tickers:
    print(f"  [{ticker}]", end="", flush=True)
    adapter = get_adapter(ticker)
    if not adapter:
        no_adapter.append(ticker)
        print(" NO ADAPTER")
        continue

    src = adapter.get("source", "")
    fields = adapter.get("fields", [])

    if src != "tdnet":
        wrong_src.append((ticker, src))
        print(f" source={src}")
        continue

    doc = get_latest_text(ticker)
    if not doc:
        no_doc.append(ticker)
        print(" NO DOC")
        continue

    submission_date, doc_title, raw_text = doc
    text = normalize(raw_text)

    matched = []
    failed = []
    for f in fields:
        key = f.get("key") or f.get("name", "?")
        # regex (old) or row_label_regex (new)
        pattern = f.get("regex", f.get("row_label_regex", ""))
        # プレースホルダーを汎用パターンに変換してテスト
        p = pattern.replace("{month_num}", "\\d{1,2}").replace("{year}", "\\d{4}")
        try:
            m = re.search(p, text, re.IGNORECASE | re.DOTALL)
            if m:
                matched.append((key, m.group(0)[:50].replace("\n", " ")))
            else:
                failed.append(key)
        except re.error as e:
            failed.append(f"{key}[ERR:{e}]")

    if not failed:
        all_match.append(ticker)
        print(f" ALL_MATCH({len(matched)})")
    elif matched:
        partial_match.append(ticker)
        print(f" PARTIAL({len(matched)}/{len(fields)})")
    else:
        all_fail.append(ticker)
        print(f" ALL_FAIL")
    # 詳細表示
    for k, v in matched:
        print(f"    ✓ {k}: {v}")
    for k in failed:
        print(f"    ✗ {k}")

print(f"\n=== サマリー ===")
print(f"全マッチ(再実行で解決): {len(all_match)} → {all_match}")
print(f"一部マッチ: {len(partial_match)} → {partial_match}")
print(f"全不一致: {len(all_fail)} → {all_fail}")
print(f"アダプターなし: {len(no_adapter)} → {no_adapter}")
print(f"source≠tdnet: {len(wrong_src)} → {wrong_src}")
print(f"BQ文書なし: {len(no_doc)} → {no_doc}")
