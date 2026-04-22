"""
TDnet 適時開示 PDF → ベクトル化 → BigQuery ETL（リカバリ専用版）

tdnet_load_parallel.py の Quota 超過エラー分を再処理するリカバリ専用スクリプト。
並列処理は行わず（MAX_WORKERS=1）、embedding バッチサイズを大きくして
API コール数を抑制しながら未取込ファイルを確実に処理する。

主な差異（tdnet_load_parallel.py との比較）:
  - MAX_WORKERS   : 5 → 1（シングルスレッド）
  - batch_size    : 5 → 20（API コール数 1/4）
  - sleep         : 0.1s → 0.5s（120 req/min。Quota 1500 の 8%）
  - 用途          : 429 Quota Exceeded エラー分の再処理のみ

Usage:
    PYTHONUTF8=1 python scripts/tdnet_load_recovery.py --from 20251101 --to 20251130
    PYTHONUTF8=1 python scripts/tdnet_load_recovery.py --from 20250101 --to 20251231
"""

import argparse
import io
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import PyPDF2
from google.cloud import bigquery, storage
from langchain_text_splitters import RecursiveCharacterTextSplitter
import vertexai
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part

# プロジェクトルートを import path に追加（src.llm.truncation 等を参照）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.llm.truncation import truncate_for_model  # noqa: E402

logging.getLogger("PyPDF2").setLevel(logging.ERROR)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  ★ 実行設定（リカバリ専用: Quota 超過対策）                           ║
# ╚══════════════════════════════════════════════════════════════════╝

DATE_MODE   = "r"
DATE_SINGLE = "20250101"
DATE_FROM   = "20250101"
DATE_TO     = "20251231"

MAX_WORKERS = 1   # シングルスレッド（Quota 消費ゼロ競合）

# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"

RUNTIME: str = detect_runtime()

# ============================================================
# 基本設定
# ============================================================

PROJECT_ID         = "gmailpj-357912"
LOCATION_EMBEDDING = "us-central1"
LOCATION_GEMINI    = "us-west1"
BUCKET_NAME        = "stock_data_1930932"
GCS_PREFIX         = "tdnet"
TABLE_ID           = f"{PROJECT_ID}.STOCK.TDNET_DOCUMENTS_ENHANCED"
BQ_BATCH_SIZE      = 100
JST                = timezone(timedelta(hours=+9), "JST")

# ============================================================
# スレッドロック
# ============================================================

_log_lock      = threading.Lock()
_counter_lock  = threading.Lock()
_vertexai_lock = threading.Lock()
_client_lock   = threading.RLock()

# ============================================================
# 認証・クライアント（遅延初期化・スレッドセーフ）
# ============================================================

_creds          = None
_storage_client = None
_bq_client      = None


def _get_credentials():
    global _creds
    with _client_lock:
        if _creds is not None:
            return _creds
        if RUNTIME == "colab_personal":
            from google.colab import userdata
            from google.oauth2 import service_account
            key_info = json.loads(userdata.get("GCP_SA_KEY"))
            _creds   = service_account.Credentials.from_service_account_info(key_info)
        else:
            _creds = None
    return _creds


def _get_storage_client():
    global _storage_client
    with _client_lock:
        if _storage_client is None:
            creds = _get_credentials()
            _storage_client = (
                storage.Client(project=PROJECT_ID, credentials=creds) if creds
                else storage.Client(project=PROJECT_ID)
            )
    return _storage_client


def _get_bq_client():
    global _bq_client
    with _client_lock:
        if _bq_client is None:
            creds = _get_credentials()
            _bq_client = (
                bigquery.Client(project=PROJECT_ID, credentials=creds) if creds
                else bigquery.Client(project=PROJECT_ID)
            )
    return _bq_client


def _load_processed_file_names(date_from: str, date_to: str) -> set[str]:
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"
    query = f"""
    SELECT DISTINCT FILE_NAME
    FROM `{TABLE_ID}`
    WHERE SUBMISSION_DATE BETWEEN '{d_from_iso}' AND '{d_to_iso}'
    """
    try:
        rows = _get_bq_client().query(query).result()
        return {row.FILE_NAME for row in rows}
    except Exception as e:
        print(f"警告: BQ 取込済みファイル一覧の取得に失敗: {e} → 重複チェックなしで続行")
        return set()


def _init_vertexai(location: str) -> None:
    with _vertexai_lock:
        creds  = _get_credentials()
        kwargs = {"credentials": creds} if creds else {}
        vertexai.init(project=PROJECT_ID, location=location, **kwargs)


# ============================================================
# 正規カテゴリリスト
# ============================================================

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

# ============================================================
# ユーティリティ関数（tdnet_load_parallel.py と同一）
# ============================================================

_MONTHLY_DOC_PATTERN = re.compile(
    r"月次|月度売上|売上速報|売上高速報|売上推移速報|月度業績|受注速報"
    r"|月度連結|月度販売|月次売上|月次業績|月次報告|月次データ|月次速報"
)


def _correct_category_by_title(main_category: str, doc_title: str) -> str:
    if main_category != "月次開示" and _MONTHLY_DOC_PATTERN.search(doc_title):
        return "月次開示"
    return main_category


def _check_monthly_by_gemini(doc_title: str) -> bool:
    _init_vertexai(LOCATION_GEMINI)
    model  = GenerativeModel("gemini-2.5-flash")
    prompt = (
        "以下のTDnet適時開示のタイトルは「月次開示」（月次売上・月次業績・月次データ等の"
        "定期的な月次報告）ですか？\n"
        f"タイトル: {doc_title}\n"
        "「はい」または「いいえ」のみ回答してください。"
    )
    try:
        response = model.generate_content(
            prompt, generation_config=GenerationConfig(temperature=0.0)
        )
        return "はい" in (response.text or "")
    except Exception:
        return False


def parse_tdnet_filename(blob_name: str) -> tuple[str, str, str, str, str, str]:
    filename = blob_name.split("/")[-1].replace(".pdf", "")
    parts    = filename.split("_")
    if len(parts) >= 6:
        raw_date = parts[0]
        sub_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        sec_code = parts[1][:4] if len(parts[1]) >= 4 else parts[1]
        doc_title     = parts[4]
        main_category = _correct_category_by_title(parts[3], doc_title)
        return sub_date, sec_code, parts[2], main_category, doc_title, parts[5]
    sec_code = "UNKNOWN"
    for part in blob_name.split("/"):
        if part.isdigit() and len(part) == 4:
            sec_code = part
            break
    return (
        datetime.now(JST).date().isoformat(),
        sec_code, "UNKNOWN", "その他（未分類）", "タイトル不明", str(uuid.uuid4()),
    )


_MIN_TEXT_LEN = 50


# ページ内の連続空白（タブ・スペース）のみ圧縮する正規表現
# 改行(\n) は保持する必要があるため [ \t]+ のみ対象にする
_INLINE_WS_PATTERN = re.compile(r"[ \t]+")


def _normalize_page_text(page_text: str) -> str:
    """ページ内の連続する空白・タブを単一スペースに圧縮。改行は保持する。"""
    lines = []
    for line in page_text.split("\n"):
        collapsed = _INLINE_WS_PATTERN.sub(" ", line).strip()
        lines.append(collapsed)
    result_lines: list[str] = []
    prev_empty = False
    for line in lines:
        if line == "":
            if not prev_empty:
                result_lines.append("")
            prev_empty = True
        else:
            result_lines.append(line)
            prev_empty = False
    return "\n".join(result_lines).strip()


def _extract_text_pypdf2(pdf_bytes: bytes) -> tuple[str, int]:
    """PyPDF2でテキスト抽出（主手段）。ページ境界 `[PAGE N]` を保持する。"""
    try:
        reader     = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        buf: list[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            normalized = _normalize_page_text(extracted)
            buf.append(f"[PAGE {idx}]\n{normalized}")
        return "\n\n".join(buf).strip(), page_count
    except Exception:
        return "", 0


def _extract_text_pdfminer(pdf_bytes: bytes) -> str:
    """pdfminer.six でテキスト抽出（form feed `\\x0c` をページ境界として利用）。"""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        params = LAParams(char_margin=1.0, word_margin=0.2, line_margin=0.3)
        output = io.StringIO()
        extract_text_to_fp(io.BytesIO(pdf_bytes), output, laparams=params)
        raw = output.getvalue()
        raw_pages = raw.split("\x0c")
        if raw_pages and raw_pages[-1].strip() == "":
            raw_pages = raw_pages[:-1]
        buf: list[str] = []
        for idx, page_text in enumerate(raw_pages, start=1):
            normalized = _normalize_page_text(page_text)
            buf.append(f"[PAGE {idx}]\n{normalized}")
        return "\n\n".join(buf).strip()
    except Exception:
        return ""


def _extract_text_gemini_vision(pdf_bytes: bytes, doc_title_hint: str) -> str:
    try:
        _init_vertexai(LOCATION_GEMINI)
        model    = GenerativeModel("gemini-2.5-flash")
        pdf_part = Part.from_data(pdf_bytes, mime_type="application/pdf")
        prompt   = (
            f"以下のPDF文書（タイトル: {doc_title_hint}）からすべてのテキストを抽出してください。"
            "表・数値・箇条書きも含め、元の内容をできる限り正確にテキストとして出力してください。"
        )
        response = model.generate_content([pdf_part, prompt])
        return response.text.strip() if response.text else ""
    except Exception:
        return ""


def extract_pdf_info(blob) -> tuple[str, int, str]:
    try:
        pdf_bytes = blob.download_as_bytes()
        text, page_count = _extract_text_pypdf2(pdf_bytes)
        if len(text) >= _MIN_TEXT_LEN:
            return text, page_count, "pypdf2"
        text_pm = _extract_text_pdfminer(pdf_bytes)
        if len(text_pm) >= _MIN_TEXT_LEN:
            return text_pm, page_count, "pdfminer"
        doc_title_hint = blob.name.split("/")[-1].replace(".pdf", "")
        text_gv = _extract_text_gemini_vision(pdf_bytes, doc_title_hint)
        if len(text_gv) >= _MIN_TEXT_LEN:
            return text_gv, page_count, "gemini_vision"
        return "", page_count, "none"
    except Exception:
        return "", 0, "none"


def get_sub_categories(doc_title: str, text: str, main_category: str) -> list[str]:
    if not text or len(text) < 100:
        return []
    truncated = truncate_for_model(text, "gemini-3-flash-batch", doc_category=main_category)
    prompt = f"""
    以下のTDnet適時開示文書のテキストを分析し、投資判断に影響を与える【他カテゴリの重要情報】が内包されているか抽出してください。

    【重要：必ず以下のカテゴリ名からのみ選択すること。一言一句違わず出力してください】
    {VALID_CATEGORIES_STR}

    ※カテゴリ選択における特記事項※
    - 「業績の重要な先行指標」: SaaSの解約率やARPU、小売の新規出店数/退店数、販売数量・出荷台数、不動産の客室稼働率・オフィス入居率などが含まれる場合に選択してください。
    - 「受注高/受注残高」: 上記の先行指標の一部ですが、極めて重要な情報のため、記載がある場合は独立したカテゴリとして選択してください。

    以下のJSONフォーマットのみで出力してください。
    {{ "sub_categories": ["選択したカテゴリ1", "選択したカテゴリ2"] }} // 該当がない場合は空リスト []

    文書タイトル: {doc_title}
    テキスト: {truncated}
    """
    _init_vertexai(LOCATION_GEMINI)
    gemini_config = GenerationConfig(response_mime_type="application/json", temperature=0.1)
    model_name    = "gemini-2.5-pro" if main_category == "決算短信" else "gemini-2.5-flash"
    target_model  = GenerativeModel(model_name)
    try:
        response  = target_model.generate_content(prompt, generation_config=gemini_config)
        res_json  = json.loads(response.text)
        extracted = res_json.get("sub_categories", [])
        return [cat for cat in extracted if cat in VALID_CATEGORIES]
    except Exception:
        return []


def create_chunks_for_tdnet(text: str, doc_title: str) -> list[dict]:
    """ページ境界 `\\n\\n[PAGE` を最優先の分割境界に追加したチャンカー。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n[PAGE", "\n\n", "\n", "。", "、", " "],
    )
    return [
        {"chunk_text": f"文書タイトル: {doc_title}\n{chunk}"}
        for chunk in splitter.split_text(text)
    ]


def get_embeddings_in_batches(texts: list[str], batch_size: int = 20) -> list:
    """埋め込みベクトルを取得する（リカバリ版: batch_size=20, sleep=0.5s）。

    Quota 消費: batch_size=20, sleep=0.5s → 2 calls/sec = 120 req/min
    textembedding-gecko Quota 1500 req/min の 8% のみ使用。
    """
    _init_vertexai(LOCATION_EMBEDDING)
    model      = TextEmbeddingModel.from_pretrained("text-embedding-004")
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch   = texts[i:i + batch_size]
        inputs  = [TextEmbeddingInput(t, "RETRIEVAL_DOCUMENT") for t in batch]
        results = model.get_embeddings(inputs)
        embeddings.extend([r.values for r in results])
        time.sleep(0.5)  # 120 req/min に制限（Quota 1500 の 8%）
    return embeddings


# ============================================================
# 1ブロブ処理関数
# ============================================================

def _process_one_blob(
    blob,
    sub_date: str,
    sec_code: str,
    filer_name: str,
    main_category: str,
    doc_title: str,
    doc_id: str,
    add_log,
) -> tuple[int, int, int]:
    processed = skipped = errors = 0
    rows_to_insert: list[dict] = []

    try:
        full_text, page_count, extract_method = extract_pdf_info(blob)
        text_length = len(full_text)
        if text_length == 0:
            add_log(f"  警告 (テキスト抽出ゼロ・全手段失敗) → スキップ: {blob.name}")
            return 0, 1, 0
        if extract_method == "gemini_vision":
            add_log(f"  ★WARNING★ Gemini Vision フォールバック使用（画像PDF疑い）: {blob.name}")
        elif extract_method == "pdfminer":
            add_log(f"  フォールバック抽出 (pdfminer) 成功: {blob.name}")

        _AMBIGUOUS_OVERWRITE   = {"その他（未分類）"}
        _AMBIGUOUS_SUBCATEGORY = {
            "業績予想", "大型受注・契約", "受注・契約", "業績の重要な先行指標",
        }
        if main_category in _AMBIGUOUS_OVERWRITE:
            if _check_monthly_by_gemini(doc_title):
                add_log(
                    f"  ★PATTERN_MISS★ Gemini=月次/その他未分類を月次上書き "
                    f"タイトル='{doc_title}': {blob.name}"
                )
                main_category = "月次開示"

        sub_categories: list[str] = []
        if main_category in ("決算短信", "決算説明資料"):
            model_label = "gemini-2.5-pro" if main_category == "決算短信" else "gemini-2.5-flash"
            add_log(f"  内包シグナルを解析中... ({model_label} 使用) [{blob.name}]")
            sub_categories = get_sub_categories(doc_title, full_text, main_category)
            if sub_categories:
                add_log(f"  → 検出: {sub_categories} [{blob.name}]")

        _MONTHLY_SUB_CATEGORIES = _AMBIGUOUS_SUBCATEGORY | {"受注高/受注残高"}
        if main_category in _MONTHLY_SUB_CATEGORIES:
            if _check_monthly_by_gemini(doc_title) and "月次開示" not in sub_categories:
                sub_categories.append("月次開示")
                add_log(f"  サブカテゴリ追加（{main_category} → 月次開示）: {blob.name}")

        chunks      = create_chunks_for_tdnet(full_text, doc_title)
        chunk_texts = [c["chunk_text"] for c in chunks]
        embeddings  = get_embeddings_in_batches(chunk_texts)

        for chunk_data, embedding in zip(chunks, embeddings):
            rows_to_insert.append({
                "DOC_ID":           doc_id,
                "TICKER":           sec_code,
                "FILER_NAME":       filer_name,
                "FILER_ID":         None,
                "SUBMISSION_DATE":  sub_date,
                "MAIN_CATEGORY":    main_category,
                "DOC_TITLE":        doc_title,
                "SUB_CATEGORIES":   sub_categories,
                "PAGE_COUNT":       page_count,
                "TEXT_LENGTH":      text_length,
                "SECTION_CATEGORY": doc_title,
                "CHUNK_INDEX":      chunk_data.get("chunk_index", 0),
                "CHUNK_TEXT":       chunk_data["chunk_text"],
                "EMBEDDING":        embedding,
                "FILE_NAME":        blob.name,
                "EXTRACT_METHOD":   extract_method,
                "LOADED_AT":        datetime.now(JST).isoformat(),
            })

        for i in range(0, len(rows_to_insert), BQ_BATCH_SIZE):
            batch = rows_to_insert[i:i + BQ_BATCH_SIZE]
            errs  = _get_bq_client().insert_rows_json(TABLE_ID, batch)
            if errs:
                add_log(f"  BQ インサートエラー: {errs}")
                errors += 1
            else:
                add_log(f"  BQ インサート: {len(batch)} 件 [{blob.name}]")

        add_log(f"  抽出完了: {len(chunks)} チャンク [{blob.name}]")
        add_log(f"ロード成功: {blob.name}")
        processed = 1

    except Exception as e:
        add_log(f"エラー: {blob.name} - {e}")
        add_log(traceback.format_exc())
        errors = 1

    return processed, skipped, errors


# ============================================================
# メイン ETL 処理
# ============================================================

def run_tdnet_recovery(date_from: str, date_to: str,
                       ticker_from: str | None = None, ticker_to: str | None = None) -> None:
    bucket = _get_storage_client().bucket(BUCKET_NAME)

    start_time_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    log_blob_name  = f"log/tdnet_recovery_{start_time_str}_log.txt"
    log_lines: list[str] = []

    _flush_stop = threading.Event()

    def _upload_log_to_gcs() -> None:
        with _log_lock:
            content = "\n".join(log_lines)
        try:
            bucket.blob(log_blob_name).upload_from_string(
                content.encode("utf-8"), content_type="text/plain; charset=utf-8"
            )
        except Exception as e:
            print(f"ログ定期フラッシュエラー: {e}")

    def _periodic_flush() -> None:
        while not _flush_stop.wait(10):
            _upload_log_to_gcs()

    _flush_thread = threading.Thread(target=_periodic_flush, daemon=True, name="log-flusher")
    _flush_thread.start()

    d_from = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to   = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))

    def add_log(msg: str) -> None:
        line = f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with _log_lock:
            print(line)
            log_lines.append(line)

    def create_vector_index() -> None:
        add_log("ベクトルインデックスの作成/更新を確認しています...")
        sql = f"""
        CREATE VECTOR INDEX IF NOT EXISTS tdnet_doc_vector_index
        ON `{TABLE_ID}`(EMBEDDING)
        OPTIONS(index_type = 'IVF', distance_type = 'COSINE', ivf_options = '{{"num_lists": 1000}}');
        """
        try:
            _get_bq_client().query(sql).result()
            add_log("ベクトルインデックスの作成命令が完了しました。")
        except Exception as e:
            add_log(f"警告 (インデックス作成): {e}")

    add_log(f"TDnet ETL リカバリ開始（MAX_WORKERS={MAX_WORKERS}）: {date_from} ～ {date_to}")
    if ticker_from or ticker_to:
        add_log(f"TICKER範囲: {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    add_log(f"実行環境: {RUNTIME}")
    add_log("embedding設定: batch_size=20, sleep=0.5s → 約120 req/min")

    add_log("BQ 取込済みファイル一覧を取得中...")
    processed_files = _load_processed_file_names(date_from, date_to)
    add_log(f"取込済みファイル数: {len(processed_files)} 件（これらはスキップ）")

    _init_vertexai(LOCATION_GEMINI)
    _init_vertexai(LOCATION_EMBEDDING)

    processed_count = 0
    skipped_count   = 0
    error_count     = 0

    try:
        blob_iter = bucket.list_blobs(prefix=f"{GCS_PREFIX}/")
        add_log("GCS blob イテレータ作成完了")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}

            for blob in blob_iter:
                if not blob.name.lower().endswith(".pdf"):
                    continue

                sub_date, sec_code, filer_name, main_category, doc_title, doc_id = \
                    parse_tdnet_filename(blob.name)

                try:
                    d_blob = date.fromisoformat(sub_date)
                except ValueError:
                    add_log(f"  日付パース失敗 → スキップ: {blob.name}")
                    skipped_count += 1
                    continue

                if not (d_from <= d_blob <= d_to):
                    continue
                if ticker_from and sec_code < ticker_from:
                    continue
                if ticker_to and sec_code > ticker_to:
                    continue
                if blob.name in processed_files:
                    skipped_count += 1
                    continue

                add_log(f"処理開始: {blob.name} (カテゴリ: {main_category}, TICKER: {sec_code})")
                future = executor.submit(
                    _process_one_blob,
                    blob, sub_date, sec_code, filer_name,
                    main_category, doc_title, doc_id, add_log,
                )
                futures[future] = blob.name

            for future in as_completed(futures):
                blob_name = futures[future]
                try:
                    p, s, e = future.result()
                    with _counter_lock:
                        processed_count += p
                        skipped_count   += s
                        error_count     += e
                except Exception as exc:
                    add_log(f"ワーカー例外: {blob_name} - {exc}")
                    with _counter_lock:
                        error_count += 1

        if processed_count > 0:
            create_vector_index()

    except Exception as e:
        add_log(f"致命的なエラーで処理が中断: {e}")
        add_log(traceback.format_exc())

    finally:
        add_log("=== TDnet ETL リカバリ 処理結果サマリー ===")
        add_log(f"対象期間              : {date_from} ～ {date_to}")
        add_log(f"並列数                : {MAX_WORKERS}")
        add_log(f"正常処理ドキュメント数  : {processed_count} 件")
        add_log(f"スキップ（取込済）      : {skipped_count} 件")
        add_log(f"エラー発生数           : {error_count} 件")

        _flush_stop.set()
        _flush_thread.join(timeout=30)
        _upload_log_to_gcs()
        print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")


# ============================================================
# 引数パース
# ============================================================

def parse_args() -> argparse.Namespace:
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None, ticker_from=None, ticker_to=None)
    parser = argparse.ArgumentParser(description="TDnet 適時開示 PDF → BigQuery ETL（リカバリ版）")
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to",   dest="date_to",   default=None)
    parser.add_argument("--ticker-from", dest="ticker_from", default=None)
    parser.add_argument("--ticker-to",   dest="ticker_to",   default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.date_from:
        date_from = args.date_from
        date_to   = args.date_to or args.date_from
    else:
        date_from = DATE_FROM
        date_to   = DATE_TO

    print("=== TDnet ETL（リカバリ版）===")
    print(f"実行環境    : {RUNTIME}")
    print(f"期間        : {date_from} ～ {date_to}")
    print(f"並列数      : {MAX_WORKERS}（シングルスレッド）")
    print(f"embedding   : batch_size=20, sleep=0.5s（120 req/min）")
    if args.ticker_from or args.ticker_to:
        print(f"TICKER範囲  : {args.ticker_from or '先頭'} ～ {args.ticker_to or '末尾'}")
    print(f"保存先      : {TABLE_ID}")
    print()

    run_tdnet_recovery(date_from, date_to, ticker_from=args.ticker_from, ticker_to=args.ticker_to)


if __name__ == "__main__":
    main()
