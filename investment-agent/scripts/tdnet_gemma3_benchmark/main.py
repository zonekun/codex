"""TDNET カテゴリ分類ベンチマーク: gemma3:12b vs Gemini（BQ保存値）.

Task A: MAIN_CATEGORY（46カテゴリから1つ）分類
Task B: MAIN_CATEGORYが「決算短信」or「決算説明資料」の文書のみ SUB_CATEGORIES 抽出
BQ `TDNET_DOCUMENTS_ENHANCED` の MAIN_CATEGORY/SUB_CATEGORIES と比較して精度計算。

環境変数:
    TARGET_YEAR_MONTH : 対象年月 (YYYY-MM)  デフォルト: 2026-03
    GCS_OUTPUT_BUCKET : 結果CSV保存先バケット デフォルト: stock_data_1930932
    GCS_OUTPUT_PREFIX : 結果CSV保存先プレフィックス デフォルト: benchmarks/tdnet_gemma3
    MAX_DOCS          : 処理上限（0=無制限）  デフォルト: 300
    DOC_OFFSET        : 何件目からスタートするか デフォルト: 0
    SAVE_INTERVAL     : N件ごとに中間CSV保存  デフォルト: 50
    MODEL_NAME        : Ollamaモデル名        デフォルト: gemma3:12b

実行:
    PYTHONUTF8=1 python -m scripts.tdnet_gemma3_benchmark.main
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import structlog
from google.cloud import bigquery, storage
from google.oauth2 import service_account
from tenacity import retry, stop_after_attempt, wait_exponential

# プロジェクトルート（scripts/ の親の親）を import path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.llm.truncation import truncate_for_model  # noqa: E402

# ──────────────────────────────────────────
# ロギング設定
# ──────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()

JST = timezone(timedelta(hours=+9), "JST")

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────
PROJECT            = "gmailpj-357912"
DATASET            = "STOCK"
TABLE              = "TDNET_DOCUMENTS_ENHANCED"
TARGET_YEAR_MONTH  = os.environ.get("TARGET_YEAR_MONTH", "2026-03")
GCS_OUTPUT_BUCKET  = os.environ.get("GCS_OUTPUT_BUCKET", "stock_data_1930932")
GCS_OUTPUT_PREFIX  = os.environ.get("GCS_OUTPUT_PREFIX", "benchmarks/tdnet_gemma3")
MAX_DOCS           = int(os.environ.get("MAX_DOCS", "300"))
DOC_OFFSET         = int(os.environ.get("DOC_OFFSET", "0"))
SAVE_INTERVAL      = int(os.environ.get("SAVE_INTERVAL", "50"))
MODEL_NAME         = os.environ.get("MODEL_NAME", "gemma3:12b")
OLLAMA_HOST        = "http://localhost:11434"
OLLAMA_STARTUP_WAIT_SEC = 180
REQUEST_TIMEOUT_SEC     = 300

# Task B を実行するMAIN_CATEGORY（gemma3判定結果がこれらなら SUB_CATEGORIES 抽出を実施）
TASK_B_CATEGORIES = {"決算短信", "決算説明資料"}

# ──────────────────────────────────────────
# カテゴリリスト
# ──────────────────────────────────────────
VALID_CATEGORIES: list[str] = [
    "決算短信", "TOB・MBO", "業績修正", "買収防衛策", "上場廃止", "継続企業疑義(GC)",
    "決算説明資料", "自己株式取得", "役員異動（代表クラス）", "配当", "第三者割当・公募増資",
    "分配金", "合併・組織再編", "子会社化・買収", "主要株主異動", "新株予約権発行",
    "株式売出し", "配当変更（増減配）", "中期経営計画", "株式分割・併合", "特別損益計上",
    "業績予想", "事業計画（グロース）", "立会外分売", "監査人異動", "訴訟・法的",
    "インシデント（災害・事故）", "自己株式消却", "インシデント（セキュリティ）",
    "転換社債(CB)発行", "行政処分", "DES（債権株式化）", "リストラ・希望退職",
    "不祥事・社内調査", "その他（未分類）", "株主優待", "提携・協業", "月次開示",
    "子会社設立", "大型受注・契約", "資産売却（不動産）", "事業・子会社売却",
    "特別利益", "特別損失", "業績の重要な先行指標", "受注高/受注残高",
]
VALID_CATEGORIES_STR = ", ".join(VALID_CATEGORIES)
VALID_CATEGORIES_SET = set(VALID_CATEGORIES)

TITLE_PREFIX = "文書タイトル: "


# ──────────────────────────────────────────
# プロンプト
# ──────────────────────────────────────────
def build_main_category_prompt(doc_title: str, text: str) -> str:
    """Task A: MAIN_CATEGORY 分類プロンプト."""
    return (
        "以下のTDnet適時開示文書を分析し、最も適切な主カテゴリを1つ選択してください。\n\n"
        f"カテゴリ: {VALID_CATEGORIES_STR}\n\n"
        f"文書タイトル: {doc_title}\n"
        f"テキスト: {text[:5000]}\n\n"
        '以下のJSONフォーマットのみで出力してください。\n'
        '{"main_category": "カテゴリ名"}'
    )


def build_sub_category_prompt(doc_title: str, text: str) -> str:
    """Task B: SUB_CATEGORIES 抽出プロンプト（Gemini と同一）."""
    return (
        "以下のTDnet適時開示文書のテキストを分析し、投資判断に影響を与える"
        "【他カテゴリの重要情報】が内包されているか抽出してください。\n\n"
        "【重要：必ず以下のカテゴリ名からのみ選択すること。一言一句違わず出力してください】\n"
        f"{VALID_CATEGORIES_STR}\n\n"
        "※カテゴリ選択における特記事項※\n"
        "- 「業績の重要な先行指標」: SaaSの解約率やARPU、小売の新規出店数/退店数、"
        "販売数量・出荷台数、不動産の客室稼働率・オフィス入居率などが含まれる場合に選択してください。\n"
        "- 「受注高/受注残高」: 上記の先行指標の一部ですが、極めて重要な情報のため、"
        "記載がある場合は独立したカテゴリとして選択してください。\n\n"
        "以下のJSONフォーマットのみで出力してください。\n"
        '{"sub_categories": ["選択したカテゴリ1", "選択したカテゴリ2"]} '
        "// 該当がない場合は空リスト []\n\n"
        f"文書タイトル: {doc_title}\n"
        f"テキスト: {truncate_for_model(text, 'gemma-4-31b', doc_category=None)}"
    )


# ──────────────────────────────────────────
# Ollama
# ──────────────────────────────────────────
def start_ollama_server() -> subprocess.Popen:
    """Ollama サーバーをバックグラウンドで起動する."""
    logger.info("ollama_starting")
    return subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_ollama(timeout_sec: int = OLLAMA_STARTUP_WAIT_SEC) -> bool:
    """Ollama が起動するまで待機する."""
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.status_code == 200:
                logger.info("ollama_ready", elapsed_sec=round(time.time() - start, 1))
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    logger.error("ollama_startup_timeout")
    return False


def check_model_loaded() -> bool:
    """指定モデルが認識されているか確認する."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
        models = [m["name"] for m in r.json().get("models", [])]
        exists = any(MODEL_NAME in m for m in models)
        logger.info("model_check", model=MODEL_NAME, exists=exists, available=models)
        return exists
    except Exception as e:
        logger.error("model_check_failed", error=str(e))
        return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=30))
def call_ollama(prompt: str) -> str:
    """Ollama にプロンプトを送信してレスポンスを返す."""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 32768,
        },
        "format": "json",
    }
    r = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json=payload,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json().get("response", "")


# ──────────────────────────────────────────
# レスポンスパース
# ──────────────────────────────────────────
def parse_main_category(response: str) -> str:
    """Task A レスポンスから main_category を抽出する."""
    try:
        start = response.find("{")
        end   = response.rfind("}") + 1
        if start < 0 or end <= 0:
            return "その他（未分類）"
        obj = json.loads(response[start:end])
        cat = obj.get("main_category", "")
        if cat in VALID_CATEGORIES_SET:
            return cat
        # 完全一致しない場合は部分一致でフォールバック
        for valid in VALID_CATEGORIES:
            if valid in cat or cat in valid:
                return valid
        return "その他（未分類）"
    except Exception:
        return "その他（未分類）"


def parse_sub_categories(response: str) -> list[str]:
    """Task B レスポンスから sub_categories を抽出する."""
    try:
        start = response.find("{")
        end   = response.rfind("}") + 1
        if start < 0 or end <= 0:
            return []
        obj = json.loads(response[start:end])
        extracted = obj.get("sub_categories", [])
        return [c for c in extracted if c in VALID_CATEGORIES_SET]
    except Exception:
        return []


# ──────────────────────────────────────────
# BQ
# ──────────────────────────────────────────
def build_bq_client() -> bigquery.Client:
    """BQクライアントを構築する（Cloud Run / ローカル共通）."""
    key_file = os.environ.get("GCP_KEY_FILE", "/app/keys/gcp-service-account.json")
    if os.path.exists(key_file):
        creds = service_account.Credentials.from_service_account_file(
            key_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bigquery.Client(project=PROJECT, credentials=creds)
    return bigquery.Client(project=PROJECT)


def fetch_doc_ids(bq: bigquery.Client) -> list[dict[str, Any]]:
    """対象年月の全カテゴリ文書を取得する（MAIN_CATEGORYフィルタなし）.

    Returns:
        [{"doc_id": ..., "ticker": ..., "doc_title": ...,
          "bq_main_category": ..., "bq_sub_categories": [...]}]
    """
    year_month = TARGET_YEAR_MONTH
    date_from  = f"{year_month}-01"
    year, month = map(int, year_month.split("-"))
    if month == 12:
        date_to = f"{year + 1}-01-01"
    else:
        date_to = f"{year}-{month + 1:02d}-01"

    limit_val  = MAX_DOCS if MAX_DOCS > 0 else 99999
    offset_val = DOC_OFFSET

    sql = f"""
    SELECT
        doc_id,
        ticker,
        doc_title,
        bq_main_category,
        ANY_VALUE(bq_sub_categories) AS bq_sub_categories
    FROM (
        SELECT
            DOC_ID        AS doc_id,
            TICKER        AS ticker,
            DOC_TITLE     AS doc_title,
            MAIN_CATEGORY AS bq_main_category,
            SUB_CATEGORIES AS bq_sub_categories
        FROM `{PROJECT}.{DATASET}.{TABLE}`
        WHERE SUBMISSION_DATE >= '{date_from}'
          AND SUBMISSION_DATE <  '{date_to}'
    )
    GROUP BY doc_id, ticker, doc_title, bq_main_category
    ORDER BY bq_main_category, ticker
    LIMIT {limit_val} OFFSET {offset_val}
    """
    rows = list(bq.query(sql).result())
    logger.info("doc_ids_fetched", count=len(rows))
    return [
        {
            "doc_id":            r["doc_id"],
            "ticker":            r["ticker"],
            "doc_title":         r["doc_title"],
            "bq_main_category":  r["bq_main_category"],
            "bq_sub_categories": list(r["bq_sub_categories"] or []),
        }
        for r in rows
    ]


def fetch_doc_text(bq: bigquery.Client, doc_id: str, doc_title: str) -> str:
    """DOC_IDのchunk_textを結合して近似full_textを返す."""
    sql = f"""
    SELECT CHUNK_TEXT
    FROM `{PROJECT}.{DATASET}.{TABLE}`
    WHERE DOC_ID = '{doc_id}'
    """
    rows = list(bq.query(sql).result())
    prefix = f"{TITLE_PREFIX}{doc_title}\n"
    parts: list[str] = []
    for r in rows:
        chunk = r["CHUNK_TEXT"] or ""
        if chunk.startswith(prefix):
            chunk = chunk[len(prefix):]
        parts.append(chunk)
    return "\n".join(parts)


# ──────────────────────────────────────────
# 精度評価
# ──────────────────────────────────────────
def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard係数を返す."""
    if not a and not b:
        return 1.0
    union = a | b
    inter = a & b
    return len(inter) / len(union)


# ──────────────────────────────────────────
# GCS 保存
# ──────────────────────────────────────────
def save_csv_to_gcs(rows: list[dict], storage_client: storage.Client, suffix: str = "") -> str:
    """結果CSVをGCSに保存してGCSパスを返す."""
    ts       = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    fname    = f"tdnet_gemma3_benchmark_{TARGET_YEAR_MONTH}_offset{DOC_OFFSET}{suffix}_{ts}.csv"
    gcs_path = f"{GCS_OUTPUT_PREFIX}/{fname}"

    buf = io.StringIO()
    fieldnames = [
        "doc_id", "ticker", "doc_title",
        "bq_main_category", "gemma3_main_category", "main_cat_match",
        "bq_sub_categories", "gemma3_sub_categories",
        "sub_exact_match", "sub_jaccard",
        "task_a_elapsed_sec", "task_b_elapsed_sec",
        "text_len_used",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    bucket = storage_client.bucket(GCS_OUTPUT_BUCKET)
    blob   = bucket.blob(gcs_path)
    blob.upload_from_string(buf.getvalue(), content_type="text/csv; charset=utf-8")
    logger.info("csv_saved_to_gcs", gcs_path=f"gs://{GCS_OUTPUT_BUCKET}/{gcs_path}")
    return f"gs://{GCS_OUTPUT_BUCKET}/{gcs_path}"


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────
def main() -> None:
    """ベンチマークのメイン処理."""
    job_start = time.time()
    logger.info(
        "benchmark_start",
        target_year_month=TARGET_YEAR_MONTH,
        model=MODEL_NAME,
        max_docs=MAX_DOCS if MAX_DOCS > 0 else "unlimited",
        doc_offset=DOC_OFFSET,
    )

    # Ollama 起動
    _proc = start_ollama_server()
    if not wait_for_ollama():
        logger.error("ollama_not_ready")
        sys.exit(1)
    if not check_model_loaded():
        logger.error("model_not_loaded", model=MODEL_NAME)
        sys.exit(1)

    # BQ / GCS クライアント
    bq  = build_bq_client()
    gcs = storage.Client(project=PROJECT)

    # 対象文書取得（全カテゴリ）
    docs = fetch_doc_ids(bq)
    logger.info("processing_start", total_docs=len(docs))

    results: list[dict] = []
    main_cat_correct = 0
    task_b_count     = 0
    task_b_exact     = 0
    task_b_jaccard   = 0.0

    for i, doc in enumerate(docs, 1):
        doc_id          = doc["doc_id"]
        ticker          = doc["ticker"]
        doc_title       = doc["doc_title"]
        bq_main_cat     = doc["bq_main_category"]
        bq_sub_cats     = doc["bq_sub_categories"]

        # テキスト再構成
        full_text     = fetch_doc_text(bq, doc_id, doc_title)
        text_len_used = min(len(full_text), 30000)

        # ── Task A: MAIN_CATEGORY 分類 ──
        prompt_a  = build_main_category_prompt(doc_title, full_text)
        t_a_start = time.time()
        try:
            resp_a          = call_ollama(prompt_a)
            gemma3_main_cat = parse_main_category(resp_a)
            task_a_elapsed  = round(time.time() - t_a_start, 1)
        except Exception as e:
            logger.error("task_a_failed", doc_id=doc_id, error=str(e))
            gemma3_main_cat = "その他（未分類）"
            task_a_elapsed  = round(time.time() - t_a_start, 1)

        main_cat_match = gemma3_main_cat == bq_main_cat
        if main_cat_match:
            main_cat_correct += 1

        # ── Task B: SUB_CATEGORIES 抽出（gemma3がTask Bカテゴリと判定した場合のみ） ──
        gemma3_sub_cats  = []
        sub_exact_match  = None
        sub_jac          = None
        task_b_elapsed   = 0.0

        if gemma3_main_cat in TASK_B_CATEGORIES:
            task_b_count += 1
            prompt_b  = build_sub_category_prompt(doc_title, full_text)
            t_b_start = time.time()
            try:
                resp_b         = call_ollama(prompt_b)
                gemma3_sub_cats = parse_sub_categories(resp_b)
                task_b_elapsed  = round(time.time() - t_b_start, 1)
            except Exception as e:
                logger.error("task_b_failed", doc_id=doc_id, error=str(e))
                gemma3_sub_cats = []
                task_b_elapsed  = round(time.time() - t_b_start, 1)

            bq_sub_set   = set(bq_sub_cats)
            g3_sub_set   = set(gemma3_sub_cats)
            sub_exact_match = bq_sub_set == g3_sub_set
            sub_jac         = jaccard(bq_sub_set, g3_sub_set)
            if sub_exact_match:
                task_b_exact += 1
            task_b_jaccard += sub_jac

        results.append({
            "doc_id":               doc_id,
            "ticker":               ticker,
            "doc_title":            doc_title,
            "bq_main_category":     bq_main_cat,
            "gemma3_main_category": gemma3_main_cat,
            "main_cat_match":       main_cat_match,
            "bq_sub_categories":    "|".join(sorted(bq_sub_cats)),
            "gemma3_sub_categories": "|".join(sorted(gemma3_sub_cats)),
            "sub_exact_match":      sub_exact_match,
            "sub_jaccard":          round(sub_jac, 4) if sub_jac is not None else "",
            "task_a_elapsed_sec":   task_a_elapsed,
            "task_b_elapsed_sec":   task_b_elapsed,
            "text_len_used":        text_len_used,
        })

        if i % 10 == 0 or i <= 5:
            main_acc = main_cat_correct / i
            logger.info(
                "progress",
                done=i, total=len(docs),
                main_cat_accuracy=f"{main_acc:.1%}",
                task_b_docs=task_b_count,
                task_b_exact_rate=(
                    f"{task_b_exact/task_b_count:.1%}" if task_b_count else "N/A"
                ),
                last_a_sec=task_a_elapsed,
                last_b_sec=task_b_elapsed,
            )

        # N件ごとに中間CSV保存（タイムアウト対策）
        if SAVE_INTERVAL > 0 and i % SAVE_INTERVAL == 0:
            save_csv_to_gcs(results, gcs, suffix=f"_partial_{DOC_OFFSET + i}")

    # ── 集計サマリ ──
    total       = len(results)
    main_acc    = main_cat_correct / total if total else 0
    avg_a_sec   = sum(r["task_a_elapsed_sec"] for r in results) / total if total else 0
    avg_b_sec   = (
        sum(r["task_b_elapsed_sec"] for r in results if r["task_b_elapsed_sec"] > 0) / task_b_count
        if task_b_count else 0
    )
    task_b_exact_rate = task_b_exact / task_b_count if task_b_count else 0
    avg_sub_jac       = task_b_jaccard / task_b_count if task_b_count else 0
    job_elapsed       = round(time.time() - job_start, 1)

    logger.info(
        "benchmark_summary",
        total_docs              = total,
        task_a_accuracy         = f"{main_cat_correct}/{total} ({main_acc:.1%})",
        task_b_docs             = task_b_count,
        task_b_exact_match      = f"{task_b_exact}/{task_b_count} ({task_b_exact_rate:.1%})",
        task_b_avg_jaccard      = f"{avg_sub_jac:.3f}",
        avg_task_a_sec          = f"{avg_a_sec:.1f}",
        avg_task_b_sec          = f"{avg_b_sec:.1f}",
        total_job_sec           = job_elapsed,
        total_job_min           = round(job_elapsed / 60, 1),
    )

    # GPU費用試算（L4: $0.000916/sec）
    GPU_COST_PER_SEC = 0.000916
    gpu_cost     = round(job_elapsed * GPU_COST_PER_SEC, 2)
    cost_per_doc = round(gpu_cost / total, 4) if total else 0
    logger.info(
        "cost_estimate",
        gpu_type         = "NVIDIA L4",
        unit_price       = "$0.000916/sec",
        total_gpu_sec    = job_elapsed,
        total_cost_usd   = f"${gpu_cost}",
        cost_per_doc_usd = f"${cost_per_doc}",
    )

    # CSV保存
    gcs_path = save_csv_to_gcs(results, gcs)
    logger.info("done", gcs_output=gcs_path)

    # サマリを標準出力にも出す（ログ取得用）
    print("\n========== BENCHMARK SUMMARY ==========")
    print(f"対象年月          : {TARGET_YEAR_MONTH}")
    print(f"モデル            : {MODEL_NAME}")
    print(f"処理文書数        : {total}")
    print(f"[Task A] MAIN_CATEGORY精度: {main_cat_correct}/{total} ({main_acc:.1%})")
    print(f"[Task B] 対象文書数 (決算短信/説明資料): {task_b_count}")
    print(f"[Task B] SUB完全一致率: {task_b_exact}/{task_b_count} ({task_b_exact_rate:.1%})")
    print(f"[Task B] 平均Jaccard  : {avg_sub_jac:.3f}")
    print(f"平均秒/文書(TaskA): {avg_a_sec:.1f}秒")
    print(f"平均秒/文書(TaskB): {avg_b_sec:.1f}秒")
    print(f"総実行時間        : {job_elapsed:.0f}秒 ({job_elapsed/60:.1f}分)")
    print(f"GPU費用推定       : ${gpu_cost} (${cost_per_doc}/文書)")
    print(f"結果CSV           : {gcs_path}")
    print("========================================\n")


if __name__ == "__main__":
    main()
