"""
TDnet 適時開示 PDF → テキスト抽出 → Gemini Batch Prediction → Batch Embedding → BigQuery ETL

Vertex AI Batch Prediction を利用して Gemini API 呼び出しと Embedding をバッチ処理。
オンライン予測比 約50%コスト削減 + RPM制限なし。

処理フロー（--job-mode で切替）:
  full（従来互換・非推奨）: Phase 1→2→3→4→5 一気通貫
  load                  : Phase 0/1/5_load のみ（AI_STATUS='pending' で insert）
  ai-prepare            : OCR + 正規表現月次補正 + state.json 保存（未実装）
  ai-finalize           : Gemma + Gemini + Embedding + BQ UPDATE（未実装）

Usage:
    PYTHONUTF8=1 python scripts/tdnet_load_parallel.py --job-mode=load --from 20260301 --to 20260331
"""

import argparse
import io
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import PyPDF2
from google import genai
from google.cloud import bigquery, storage
from google.cloud.bigquery import LoadJobConfig, SourceFormat, WriteDisposition

from langchain_text_splitters import RecursiveCharacterTextSplitter

# プロジェクトルートを import path に追加（src.llm.truncation 等を参照）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.llm.page_aware_text import PAGE_MARKER_PATTERN  # noqa: E402
from src.llm.truncation import truncate_for_model  # noqa: E402


def _log_rss(logger, tag: str) -> None:
    """現在プロセスの RSS メモリを /proc/self/status から読み、ログに記録.

    Cloud Run Jobs の OOM 挙動検証用。Linux のみ動作、他OSでは無視。
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    logger.log(f"[MEM] {tag}: RSS={kb / 1024:.0f} MB")
                    return
    except Exception:
        pass

logging.getLogger("PyPDF2").setLevel(logging.ERROR)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  ★ 実行設定                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

DATE_MODE   = "y"
DATE_SINGLE = "20260301"
DATE_FROM   = "20260301"
DATE_TO     = "20260331"

BATCH_POLL_INTERVAL = 60  # バッチジョブ完了ポーリング間隔（秒）
GEMINI_MODEL        = "gemini-3-flash-preview"  # 分析モデル（全カテゴリ統一）
GEMINI_LOCATION     = "global"                  # 3.x 系は global のみ

# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    """実行環境を判別する."""
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
LOCATION_GEMINI    = "us-central1"
BUCKET_NAME        = "stock_data_1930932"
GCS_PREFIX         = "tdnet"
GCS_BATCH_PREFIX   = "batch_prediction/tdnet"
TABLE_ID           = f"{PROJECT_ID}.STOCK.TDNET_DOCUMENTS_ENHANCED"
BQ_BATCH_SIZE      = 100
JST                = timezone(timedelta(hours=+9), "JST")

# ============================================================
# 認証・クライアント
# ============================================================

_creds = None


def _get_credentials():
    """認証情報を取得する."""
    global _creds
    if _creds is not None:
        return _creds
    if RUNTIME == "colab_personal":
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        _creds   = service_account.Credentials.from_service_account_info(key_info)
    elif RUNTIME == "local":
        from google.oauth2 import service_account
        from src.core.config import Settings
        settings = Settings()
        _creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        _creds = None
    return _creds


def _get_storage_client() -> storage.Client:
    """GCS クライアントを取得する."""
    creds = _get_credentials()
    if creds:
        return storage.Client(project=PROJECT_ID, credentials=creds)
    return storage.Client(project=PROJECT_ID)


def _get_bq_client() -> bigquery.Client:
    """BigQuery クライアントを取得する."""
    creds = _get_credentials()
    if creds:
        return bigquery.Client(project=PROJECT_ID, credentials=creds)
    return bigquery.Client(project=PROJECT_ID)


def _get_genai_client() -> genai.Client:
    """google-genai クライアントを取得する（Gemini 3.x 分析用: global）."""
    creds = _get_credentials()
    kwargs: dict = {
        "project": PROJECT_ID,
        "location": GEMINI_LOCATION,
        "vertexai": True,
    }
    if creds:
        kwargs["credentials"] = creds
    return genai.Client(**kwargs)


def _get_genai_client_embedding() -> genai.Client:
    """google-genai クライアントを取得する（Embedding 用: us-central1）."""
    creds = _get_credentials()
    kwargs: dict = {
        "project": PROJECT_ID,
        "location": LOCATION_GEMINI,  # us-central1
        "vertexai": True,
    }
    if creds:
        kwargs["credentials"] = creds
    return genai.Client(**kwargs)


def _load_processed_file_names(date_from: str, date_to: str) -> set[str]:
    """BQ から取込済みファイル名を取得する.

    P0-4: 失敗時 raise で jobs を中断（旧実装は empty set 返却で重複 INSERT 大量発生）。
    NR-2: tenacity で transient 失敗（quota, 権限瞬断, ネットワーク）をリトライ、
    最終的に失敗なら fail-fast。2 段構えで日次の安定性と冪等性を両立。
    """
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    from google.api_core.exceptions import GoogleAPIError, ServerError, TooManyRequests

    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"
    query = """
    SELECT DISTINCT FILE_NAME
    FROM `""" + TABLE_ID + """`
    WHERE SUBMISSION_DATE BETWEEN @d_from AND @d_to
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("d_from", "DATE", d_from_iso),
        bigquery.ScalarQueryParameter("d_to", "DATE", d_to_iso),
    ])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((GoogleAPIError, ServerError, TooManyRequests)),
        reraise=True,
    )
    def _query_with_retry():
        return _get_bq_client().query(query, job_config=job_config).result()

    rows = _query_with_retry()
    return {row.FILE_NAME for row in rows}


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
# カテゴリ判定に Gemini 分析が必要なカテゴリ集合
# ============================================================

_AMBIGUOUS_OVERWRITE: set[str] = {"その他（未分類）"}
_AMBIGUOUS_SUBCATEGORY: set[str] = {
    "業績予想",
    "大型受注・契約",
    "受注・契約",
    "業績の重要な先行指標",
}
_MONTHLY_SUB_CATEGORIES: set[str] = _AMBIGUOUS_SUBCATEGORY | {"受注高/受注残高"}
_NEEDS_SUB_CATEGORIES: set[str] = {"決算短信", "決算説明資料"}
_NEEDS_GEMINI_ANALYSIS: set[str] = (
    _AMBIGUOUS_OVERWRITE | _MONTHLY_SUB_CATEGORIES | _NEEDS_SUB_CATEGORIES
)

# ============================================================
# DocInfo データクラス
# ============================================================


@dataclass
class DocInfo:
    """1つの PDF ドキュメントの処理状態を保持する."""

    blob_name: str
    sub_date: str
    sec_code: str
    filer_name: str
    main_category: str
    doc_title: str
    doc_id: str
    disclosure_time: str = ""  # HH:MM（index CSV から取得）
    text: str = ""
    page_count: int = 0
    extract_method: str = "none"
    needs_vision: bool = False
    needs_analysis: bool = False
    analysis_model: str = GEMINI_MODEL
    # バッチ結果
    is_monthly: bool = False
    sub_categories: list[str] = field(default_factory=list)
    sub_categories_gemma: list[str] = field(default_factory=list)  # ai-finalize: Gemma 結果バックアップ
    # チャンク・埋め込み
    chunks: list[dict] = field(default_factory=list)
    # embeddings は (n_chunks, 768) の np.float32 配列、または空 list
    # Python の list[list[float]] は 1 float=24-32B でメモリ爆発するので numpy で保持
    embeddings: "np.ndarray | list" = field(default_factory=list)
    embedding_set: list[bool] = field(default_factory=list)  # 各チャンクの埋め込み取得成功フラグ


# ============================================================
# ユーティリティ関数（tdnet_load_parallel.py と同一）
# ============================================================

_MONTHLY_DOC_PATTERN = re.compile(
    # 既存（月次/月度 系）
    r"月次|月度売上|売上速報|売上高速報|売上推移速報|月度業績|受注速報"
    r"|月度連結|月度販売|月次売上|月次業績|月次報告|月次データ|月次速報"
    # 2026-04-19 追加（V4 検出の 41社/2279件 月次誤分類を吸収）
    r"|主要ＫＰＩ|主要KPI"                              # KPI 単独タイトル
    r"|速報数値"                                         # "速報数値" 単独
    r"|Monthly\s*Report|マンスリーレポート"             # 英字 Monthly / 月次マンスリー
    r"|月末運用資産|運用資産概況"                        # REIT 月次運用資産
    r"|前年比速報"                                       # 月度付き前年比速報
    r"|ポートフォリオ運営実績|ポートフォリオ稼働率"      # REIT 月次ポートフォリオ
    r"|DATA\s*FILE"                                     # DATA FILE YYYY年N月期速報
    r"|月度(?:IR|ＩＲ)レポート"                          # 月度IRレポート
    r"|ホテル運営状況"                                   # ホテル運営状況（N月度）
    r"|売上報告"                                         # YYYY年N月売上報告（FP無しを BQ で実測済）
    # 2026-04-19 追加（前年対比系、月のコンテキスト必須で FP 回避）
    r"|(?:\d+月|月度|月次).{0,30}前年対比"              # N月/月度/月次 近傍の前年対比
)

# Ticker 個別ハードコーディング例外（タイトルだけでは判定不能な銘柄固有表記）
# key=ticker, value=compiled regex
_TICKER_SPECIFIC_MONTHLY = {
    "3086": re.compile(r"連結売上収益報告"),  # J.フロントリテイリング月次連結売上（IFRS）
}


def _correct_category_by_title(main_category: str, doc_title: str, ticker: str = "") -> str:
    """DOC_TITLE のセマンティックパターンで '月次開示' に補正する.

    TDnet のカテゴリ割り当てで '業績予想' 等に誤分類される月次開示文書を
    タイトルから判定して修正する。セマンティック判定のみ（Gemini フォールバックは
    バッチ処理で実施する）。

    ticker 個別ハードコーディング例外（`_TICKER_SPECIFIC_MONTHLY`）も適用する。
    """
    if main_category == "月次開示":
        return main_category
    if _MONTHLY_DOC_PATTERN.search(doc_title):
        return "月次開示"
    # ticker 固有の月次パターン（3086 J.フロント等）
    specific = _TICKER_SPECIFIC_MONTHLY.get(ticker) if ticker else None
    if specific and specific.search(doc_title):
        return "月次開示"
    return main_category


def parse_tdnet_filename(blob_name: str) -> tuple[str, str, str, str, str, str]:
    """TDnet ファイル名をパースする.

    Raises:
        ValueError: ファイル名が想定フォーマット（6 parts 以上）に合致しない場合。
            呼び出し側で try/except して skip + log すること。旧実装は今日付+uuid4 を
            捏造して返していたが、誤日付 BQ 挿入と retry 時重複の原因になるため廃止。
    """
    filename = blob_name.split("/")[-1].replace(".pdf", "")
    parts    = filename.split("_")
    if len(parts) < 6:
        raise ValueError(
            f"parse_tdnet_filename: 想定 6 parts 以上だが {len(parts)} parts: {blob_name}"
        )
    raw_date = parts[0]
    if len(raw_date) < 8 or not raw_date[:8].isdigit():
        raise ValueError(
            f"parse_tdnet_filename: 先頭パートが YYYYMMDD 形式でない: {blob_name}"
        )
    sub_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    sec_code = parts[1][:4] if len(parts[1]) >= 4 else parts[1]
    doc_title     = parts[4]
    main_category = _correct_category_by_title(parts[3], doc_title, sec_code)
    return sub_date, sec_code, parts[2], main_category, doc_title, parts[5]


_MIN_TEXT_LEN = 50


def _content_length(text: str) -> int:
    """ページマーカーと空白を除いた実コンテンツの文字数を返す。"""
    stripped = PAGE_MARKER_PATTERN.sub("", text)
    return len(stripped.split())


# ページ内の連続空白（タブ・スペース）のみ圧縮する正規表現
# 改行(\n) は保持する必要があるため [ \t]+ のみ対象にする
_INLINE_WS_PATTERN = re.compile(r"[ \t]+")


def _normalize_page_text(page_text: str) -> str:
    """ページ内の連続する空白・タブを単一スペースに圧縮。改行は保持する。"""
    lines = []
    for line in page_text.split("\n"):
        collapsed = _INLINE_WS_PATTERN.sub(" ", line).strip()
        lines.append(collapsed)
    # 連続する空行は1つに圧縮（ただしページ内に限る）
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
    """PyPDF2でテキスト抽出（主手段）。(text, page_count) を返す。

    ページ境界を保持するため、各ページの先頭に `[PAGE N]` マーカーを入れて
    `[PAGE N]\\n{ページ本文}\\n\\n` の形式で連結する。
    改行は保持し、連続する空白・タブのみ圧縮する。
    """
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
    """pdfminer.six でテキスト抽出（特殊フォント・埋め込みフォント対応）.

    日本語 PDF 向けに LAParams を調整:
    - char_margin=1.0  : 文字間隔が広い和文フォントでも同一行と判定
    - word_margin=0.2  : 単語分割を抑制（日本語は空白区切りなし）
    - line_margin=0.3  : 行間が詰まった表や縦書き部分への対応

    `extract_text_to_fp` は既定で改ページ箇所に `\\x0c` (form feed) を出力する。
    これを利用してページ境界に `[PAGE N]` マーカーを挿入する。
    """
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        params = LAParams(char_margin=1.0, word_margin=0.2, line_margin=0.3)
        output = io.StringIO()
        extract_text_to_fp(io.BytesIO(pdf_bytes), output, laparams=params)
        raw = output.getvalue()
        # form feed（\x0c）でページを分割。末尾の空ページは除去
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


def create_chunks_for_tdnet(text: str, doc_title: str) -> list[dict]:
    """テキストをチャンク分割する.

    ページ境界（`\\n\\n[PAGE`）を最優先の分割境界とし、次いで段落・行・句読点で分割する。
    chunk_size=400, chunk_overlap=50 を維持。
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n\n[PAGE", "\n\n", "\n", "。", "、", " "],
    )
    return [
        {"chunk_text": f"文書タイトル: {doc_title}\n{chunk}"}
        for chunk in splitter.split_text(text)
    ]


# ============================================================
# ログ管理
# ============================================================

class BatchLogger:
    """GCS にフラッシュ可能なログ管理."""

    def __init__(self, bucket, log_blob_name: str) -> None:
        self._bucket = bucket
        self._log_blob_name = log_blob_name
        self._lines: list[str] = []

    def log(self, msg: str) -> None:
        """ログ追記+標準出力."""
        line = f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(line)
        self._lines.append(line)

    def flush_to_gcs(self) -> None:
        """GCS にログをアップロードする."""
        try:
            content = "\n".join(self._lines)
            self._bucket.blob(self._log_blob_name).upload_from_string(
                content.encode("utf-8"), content_type="text/plain; charset=utf-8"
            )
        except Exception as e:
            print(f"ログフラッシュエラー: {e}")


# ============================================================
# Index CSV → DOC_ID:時刻マッピング
# ============================================================

def _load_disclosure_time_map(
    bucket, date_from: str, date_to: str, logger: BatchLogger,
) -> dict[str, str]:
    """GCS 上の index CSV を読み込み、DOC_ID → HH:MM のマッピングを返す."""
    import csv as csv_mod

    time_map: dict[str, str] = {}

    # index CSV のファイル名パターン: index_{from}_{to}.csv
    # 日次実行の場合は index_{date}_{date}.csv、期間指定の場合はそのまま
    # 全候補を走査する
    prefix = f"{GCS_PREFIX}/index_"
    blobs = list(bucket.list_blobs(prefix=prefix))
    target_blobs = []

    d_from = int(date_from)
    d_to = int(date_to)

    for blob in blobs:
        name = blob.name.split("/")[-1]  # index_20260301_20260331.csv
        if not name.startswith("index_") or not name.endswith(".csv"):
            continue
        parts = name.replace("index_", "").replace(".csv", "").split("_")
        if len(parts) != 2:
            continue
        try:
            csv_from = int(parts[0])
            csv_to = int(parts[1])
        except ValueError:
            continue
        # 期間が重なる index CSV を対象とする
        if csv_to >= d_from and csv_from <= d_to:
            target_blobs.append(blob)

    for blob in target_blobs:
        try:
            content = blob.download_as_text(encoding="utf-8")
            reader = csv_mod.DictReader(io.StringIO(content))
            for row in reader:
                doc_id = row.get("id", "")
                pubdate = row.get("pubdate", "")
                if doc_id and pubdate and len(pubdate) >= 16:
                    # pubdate: "2026-02-24 15:30:00" → "15:30"
                    time_str = pubdate[11:16]
                    if time_str != "00:00":
                        time_map[doc_id] = time_str
        except Exception as e:
            logger.log(f"  index CSV 読み込みスキップ: {blob.name} - {e}")

    logger.log(f"開示時刻マッピング: {len(time_map)} 件ロード（index CSV {len(target_blobs)} ファイル）")
    return time_map


# ============================================================
# Phase 1: スキャン & テキスト抽出
# ============================================================

def phase1_scan_and_extract(
    bucket,
    date_from: str,
    date_to: str,
    ticker_from: str | None,
    ticker_to: str | None,
    processed_files: set[str],
    logger: BatchLogger,
    time_map: dict[str, str] | None = None,
) -> list[DocInfo]:
    """GCS blob を走査し、テキスト抽出を行い DocInfo リストを返す."""
    d_from = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to   = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))

    docs: list[DocInfo] = []
    skipped = 0

    logger.log("Phase 1: GCS blob スキャン & テキスト抽出 開始")
    # T-8 対応: ticker 範囲が両方指定され、かつ 4桁数字なら per-ticker prefix で list 発行
    #   数百万 blob のフラット走査を避け、対象 ticker 分のみ list。
    #   ticker 範囲無しの daily 実行は従来通り全走査（日付フィルタで絞られる）。
    if (ticker_from and ticker_to
            and ticker_from.isdigit() and ticker_to.isdigit()
            and len(ticker_from) == 4 and len(ticker_to) == 4):
        from itertools import chain
        start = int(ticker_from)
        end = int(ticker_to)
        logger.log(f"  [T-8] per-ticker prefix scan: {start:04d}〜{end:04d} ({end - start + 1} prefix)")
        iterators = [
            bucket.list_blobs(prefix=f"{GCS_PREFIX}/{t:04d}/")
            for t in range(start, end + 1)
        ]
        blob_iter = chain(*iterators)
    else:
        if not (ticker_from or ticker_to):
            logger.log(f"  [T-8][WARN] ticker 範囲無しでフラットバケット全走査中。backfill では per-ticker 推奨")
        blob_iter = bucket.list_blobs(prefix=f"{GCS_PREFIX}/")

    for blob in blob_iter:
        if not blob.name.lower().endswith(".pdf"):
            continue

        # P0-3: parse_tdnet_filename は raise する（旧実装は今日付+uuid4 捏造）
        try:
            sub_date, sec_code, filer_name, main_category, doc_title, doc_id = \
                parse_tdnet_filename(blob.name)
        except ValueError as e:
            logger.log(f"  ファイル名パース失敗 → スキップ: {blob.name} ({e})")
            skipped += 1
            continue

        try:
            d_blob = date.fromisoformat(sub_date)
        except ValueError:
            logger.log(f"  日付パース失敗 → スキップ: {blob.name}")
            skipped += 1
            continue

        if not (d_from <= d_blob <= d_to):
            continue
        if ticker_from and sec_code < ticker_from:
            continue
        if ticker_to and sec_code > ticker_to:
            continue
        if blob.name in processed_files:
            skipped += 1
            continue

        # テキスト抽出（PyPDF2 → pdfminer）
        try:
            pdf_bytes = blob.download_as_bytes()
        except Exception as e:
            logger.log(f"  GCS ダウンロード失敗 → スキップ: {blob.name} - {e}")
            skipped += 1
            continue

        text, page_count = _extract_text_pypdf2(pdf_bytes)
        extract_method = "pypdf2"

        if _content_length(text) < _MIN_TEXT_LEN:
            text_pm = _extract_text_pdfminer(pdf_bytes)
            if _content_length(text_pm) >= _MIN_TEXT_LEN:
                text = text_pm
                extract_method = "pdfminer"
                logger.log(f"  フォールバック抽出 (pdfminer) 成功: {blob.name}")

        needs_vision = _content_length(text) < _MIN_TEXT_LEN
        if needs_vision:
            extract_method = "none"

        # Gemini 分析が必要か判定
        needs_analysis = main_category in _NEEDS_GEMINI_ANALYSIS
        analysis_model = GEMINI_MODEL

        disclosure_time = (time_map or {}).get(doc_id, "")

        doc = DocInfo(
            blob_name=blob.name,
            sub_date=sub_date,
            sec_code=sec_code,
            filer_name=filer_name,
            main_category=main_category,
            doc_title=doc_title,
            doc_id=doc_id,
            disclosure_time=disclosure_time,
            text=text,
            page_count=page_count,
            extract_method=extract_method,
            needs_vision=needs_vision,
            needs_analysis=needs_analysis,
            analysis_model=analysis_model,
        )
        docs.append(doc)

    logger.log(f"Phase 1 完了: 対象 {len(docs)} 件, スキップ {skipped} 件")
    logger.log(f"  Vision OCR 必要: {sum(1 for d in docs if d.needs_vision)} 件")
    logger.log(f"  Gemini 分析必要: {sum(1 for d in docs if d.needs_analysis)} 件")
    return docs


# ============================================================
# バッチジョブ共通: JSONL アップロード & ポーリング & 結果取得
# ============================================================

def _upload_jsonl_to_gcs(
    bucket, gcs_path: str, lines: list[str],
) -> str:
    """JSONL を GCS にアップロードし、gs:// URI を返す."""
    content = "\n".join(lines)
    bucket.blob(gcs_path).upload_from_string(
        content.encode("utf-8"), content_type="application/jsonl"
    )
    return f"gs://{BUCKET_NAME}/{gcs_path}"


def _poll_batch_job(
    client: genai.Client, job_name: str, logger: BatchLogger, label: str,
) -> bool:
    """バッチジョブの完了をポーリングする. 成功なら True."""
    while True:
        job = client.batches.get(name=job_name)
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        logger.log(f"  [{label}] ジョブ状態: {state}")

        if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
            return True
        if state in ("JOB_STATE_FAILED", "FAILED", "JOB_STATE_CANCELLED", "CANCELLED",
                      "failed", "cancelled"):
            logger.log(f"  [{label}] ジョブ失敗: {state}")
            return False

        time.sleep(BATCH_POLL_INTERVAL)


def _download_batch_results(bucket, output_prefix: str) -> dict[str, dict]:
    """バッチ出力 JSONL を GCS からダウンロードし、key → response の辞書を返す."""
    results: dict[str, dict] = {}
    blobs = list(bucket.list_blobs(prefix=output_prefix))
    for blob in blobs:
        if not blob.name.endswith(".jsonl"):
            continue
        content = blob.download_as_string().decode("utf-8")
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                key = obj.get("key", "")
                results[key] = obj
            except json.JSONDecodeError:
                continue
    return results


# ============================================================
# Phase 2: Gemini Vision Batch（OCR）
# ============================================================

def phase2_vision_batch(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
) -> None:
    """テキスト抽出失敗 PDF を Gemini Vision でバッチ OCR する."""
    vision_docs = [d for d in docs if d.needs_vision]
    if not vision_docs:
        logger.log("Phase 2: Vision OCR 不要（スキップ）")
        return

    logger.log(f"Phase 2: Gemini Vision Batch OCR 開始 ({len(vision_docs)} 件)")

    # JSONL 作成（既存プロンプト文言を一言一句維持）
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    input_path  = f"{GCS_BATCH_PREFIX}/vision_{timestamp}_input.jsonl"
    output_path = f"{GCS_BATCH_PREFIX}/vision_{timestamp}_output/"

    lines: list[str] = []
    for i, doc in enumerate(vision_docs):
        gcs_uri = f"gs://{BUCKET_NAME}/{doc.blob_name}"
        doc_title_hint = doc.blob_name.split("/")[-1].replace(".pdf", "")
        request_obj = {
            "key": f"vision_{i}",
            "request": {
                "contents": [{
                    "role": "user",
                    "parts": [
                        {
                            "fileData": {
                                "fileUri": gcs_uri,
                                "mimeType": "application/pdf",
                            }
                        },
                        {
                            "text": (
                                f"以下のPDF文書（タイトル: {doc_title_hint}）から"
                                "すべてのテキストを抽出してください。"
                                "表・数値・箇条書きも含め、元の内容をできる限り"
                                "正確にテキストとして出力してください。"
                            ),
                        },
                    ],
                }],
            },
        }
        lines.append(json.dumps(request_obj, ensure_ascii=False))

    input_uri = _upload_jsonl_to_gcs(bucket, input_path, lines)
    logger.log(f"  JSONL アップロード完了: {input_uri}")

    # バッチジョブ投入
    job = client.batches.create(
        model=GEMINI_MODEL,
        src=input_uri,
        config=genai.types.CreateBatchJobConfig(
            dest=f"gs://{BUCKET_NAME}/{output_path}",
        ),
    )
    logger.log(f"  Vision バッチジョブ投入: {job.name}")

    # ポーリング
    success = _poll_batch_job(client, job.name, logger, "Vision")
    if not success:
        logger.log("  Vision バッチジョブ失敗 → Vision 対象は全件テキスト無しで続行")
        return

    # 結果取得
    results = _download_batch_results(bucket, output_path)
    logger.log(f"  Vision バッチ結果取得: {len(results)} 件")

    # M-7: 個別 parse 失敗・結果欠落・テキスト短すぎを件数カウント
    vision_ok = 0
    vision_parse_fail = 0
    vision_no_result = 0
    vision_too_short = 0
    for i, doc in enumerate(vision_docs):
        key = f"vision_{i}"
        if key in results:
            resp = results[key]
            try:
                text = resp["response"]["candidates"][0]["content"]["parts"][0]["text"]
                text = text.strip()
                # Vision結果にはマーカーが含まれないため len(text) で判定
                if len(text) >= _MIN_TEXT_LEN:
                    doc.text = text
                    doc.extract_method = "gemini_vision"
                    logger.log(f"  ★WARNING★ Gemini Vision フォールバック使用（画像PDF疑い）: {doc.blob_name}")
                    vision_ok += 1
                else:
                    logger.log(f"  警告 (Vision OCR テキスト短すぎ) → スキップ: {doc.blob_name}")
                    vision_too_short += 1
            except (KeyError, IndexError):
                logger.log(f"  Vision OCR 結果パース失敗: {doc.blob_name}")
                vision_parse_fail += 1
        else:
            logger.log(f"  Vision OCR 結果なし: {doc.blob_name}")
            vision_no_result += 1

    logger.log(
        f"Phase 2 完了: 成功 {vision_ok} / 結果パース失敗 {vision_parse_fail} / "
        f"結果なし {vision_no_result} / テキスト短すぎ {vision_too_short}"
    )


# ============================================================
# Phase 3: Gemini 分析 Batch（統合プロンプト: 月次判定 + サブカテゴリ）
# ============================================================

def _build_merged_prompt(doc_title: str, text: str, doc_category: str | None = None) -> str:
    """統合プロンプトを構築する.

    既存の2つのプロンプト文言は一言一句変更しない。
    タスクラベルと統合出力形式のみ追加。

    Args:
        doc_title: 文書タイトル。
        text: 抽出済みフルテキスト。
        doc_category: 文書カテゴリ（MAIN_CATEGORY）。決算短信等は切り詰め対象外。
    """
    truncated = truncate_for_model(text, "gemini-3-flash-batch", doc_category=doc_category)
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」（月次売上・月次業績・月次受注、月次顧客数等の、企業業績に影響ある定期的な月次報告）ですか？

【タスク2: サブカテゴリ抽出】
投資判断に影響を与える【他カテゴリの重要情報】が内包されているか抽出してください。

以下のカテゴリ名からのみ選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_STR}

※カテゴリ選択における特記事項:
- 「業績の重要な先行指標」: SaaSの解約率やARPU、小売の新規出店数/退店数、販売数量・出荷台数、不動産の客室稼働率・オフィス入居率など
- 「受注高/受注残高」: 上記の先行指標の一部だが、極めて重要な情報のため独立カテゴリとして選択

文書タイトル: {doc_title}
テキスト: {truncated}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


# Phase 3 Gemini Batch 並列分割設定（2026-04-20 追加）
# 5,000+ 件を 1 batch で投入すると Vertex AI Batch の compute が 3h+ 長期化するため
# 並列分割して投げる。コストは同じ、wall time が 1/N に短縮。
PHASE3_PARALLEL = 5              # 最大並列 batch 数
PHASE3_MIN_PER_CHUNK = 200       # 1 chunk あたり最低件数（細切れすぎ回避）


def _compute_phase3_chunks(n_total: int) -> list[tuple[int, int]]:
    """analysis_docs を分割するための (start, end) リストを返す.

    件数が少ない場合は 1 chunk、大量時は PHASE3_PARALLEL 個まで分割。
    """
    if n_total < PHASE3_MIN_PER_CHUNK * 2:
        return [(0, n_total)]
    n_chunks = min(PHASE3_PARALLEL, n_total // PHASE3_MIN_PER_CHUNK)
    size = n_total // n_chunks
    chunks: list[tuple[int, int]] = []
    for i in range(n_chunks):
        start = i * size
        end = (i + 1) * size if i < n_chunks - 1 else n_total
        chunks.append((start, end))
    return chunks


def _phase3_submit(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
    filter_fn=None,
) -> list[dict] | None:
    """Phase 3 バッチ投入のみ。ジョブ情報リスト（分割数分）を返す（ポーリングしない）.

    2026-04-20 以降は PHASE3_PARALLEL 個まで分割して並列投入。戻り値は list[dict]。
    後方互換: 呼び出し側で `isinstance(info, dict) or isinstance(info, list)` で分岐。

    Args:
        filter_fn: doc を受けて bool を返す predicate。None なら従来の
            `d.needs_analysis and d.text` が使われる。G-3 対応。
    """
    if filter_fn is None:
        filter_fn = lambda d: d.needs_analysis and bool(d.text)
    analysis_docs = [d for d in docs if filter_fn(d)]
    if not analysis_docs:
        logger.log("Phase 3: Gemini 分析不要（スキップ）")
        return None

    n_total = len(analysis_docs)
    chunks = _compute_phase3_chunks(n_total)
    n_chunks = len(chunks)

    logger.log(f"Phase 3: Gemini 分析 Batch 投入 ({GEMINI_MODEL}: {n_total} 件 / {n_chunks} 分割並列)")

    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    infos: list[dict] = []

    for idx, (start, end) in enumerate(chunks):
        part = f"p{idx + 1}of{n_chunks}"
        chunk_docs = analysis_docs[start:end]
        key_to_blob: dict[str, str] = {}

        input_path = f"{GCS_BATCH_PREFIX}/analysis_{timestamp}_{part}_input.jsonl"
        output_path = f"{GCS_BATCH_PREFIX}/analysis_{timestamp}_{part}_output/"

        lines: list[str] = []
        for i, doc in enumerate(chunk_docs):
            key = f"a_{idx}_{i}"  # chunk 間で key 衝突しないよう idx を含める
            key_to_blob[key] = doc.blob_name
            prompt = _build_merged_prompt(doc.doc_title, doc.text, doc_category=doc.main_category)
            request_obj = {
                "key": key,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generation_config": {
                        "response_mime_type": "application/json",
                        "temperature": 0.1,
                    },
                },
            }
            lines.append(json.dumps(request_obj, ensure_ascii=False))

        input_uri = _upload_jsonl_to_gcs(bucket, input_path, lines)
        logger.log(f"  [{part}] JSONL アップロード: {input_uri} ({len(lines)} 件)")

        job = client.batches.create(
            model=GEMINI_MODEL,
            src=input_uri,
            config=genai.types.CreateBatchJobConfig(
                dest=f"gs://{BUCKET_NAME}/{output_path}",
            ),
        )
        logger.log(f"  [{part}] batch job 投入: {job.name}")

        infos.append({
            "job": job.name,
            "output": output_path,
            "key_to_blob": key_to_blob,
            "timestamp": timestamp,
            "part": part,
        })

    return infos


def _phase3_poll_and_apply(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
    phase3_info,
) -> None:
    """Phase 3 バッチ結果をポーリングし、docs に適用する.

    phase3_info の型:
      - list[dict]: 新（並列分割）フォーマット（2026-04-20〜）
      - dict + 'job' key: 旧単一 batch
      - dict + 'flash_job' key: 超旧 legacy
    """
    import concurrent.futures

    # 後方互換: 旧フォーマット（flash_job/pro_job）→ legacy ハンドラ
    if isinstance(phase3_info, dict) and "flash_job" in phase3_info and "job" not in phase3_info:
        _phase3_poll_and_apply_legacy(docs, bucket, client, logger, phase3_info)
        return

    # 単一 dict を list に包む（単一 batch フォーマットの後方互換）
    if isinstance(phase3_info, dict):
        infos = [phase3_info]
    else:
        infos = phase3_info

    if not infos:
        logger.log("Phase 3 (poll): info 空でスキップ")
        return

    # blob_name → doc マッピング
    doc_by_blob: dict[str, DocInfo] = {d.blob_name: d for d in docs}

    # 並列ポーリング: 各 batch を別スレッドで poll、全完了を待つ
    def _poll_one(info: dict):
        job_name = info.get("job")
        part = info.get("part", "single")
        if not job_name:
            return (info, {})
        success = _poll_batch_job(client, job_name, logger, f"Analysis-{part}")
        if not success:
            logger.log(f"  [{part}] 分析バッチジョブ失敗")
            return (info, {})
        results = _download_batch_results(bucket, info["output"])
        logger.log(f"  [{part}] バッチ結果取得: {len(results)} 件")
        return (info, results)

    total_results = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(infos))) as pool:
        futures = [pool.submit(_poll_one, info) for info in infos]
        for fut in concurrent.futures.as_completed(futures):
            info, results = fut.result()
            key_to_blob = info["key_to_blob"]
            for key, resp_obj in results.items():
                blob_name = key_to_blob.get(key)
                if blob_name is None:
                    continue
                doc = doc_by_blob.get(blob_name)
                if doc is None:
                    continue
                try:
                    text_resp = resp_obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_resp)
                    if isinstance(parsed, list):
                        parsed = parsed[0] if parsed else {}
                    doc.is_monthly = parsed.get("is_monthly", False)
                    if doc.main_category in _NEEDS_SUB_CATEGORIES:
                        raw_subs = parsed.get("sub_categories", [])
                        doc.sub_categories = [c for c in raw_subs if c in VALID_CATEGORIES]
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    logger.log(f"  分析結果パース失敗 ({key}): {e}")
            total_results += len(results)

    logger.log(f"  全 batch 結果適用完了: {total_results} 件 (batch 数 {len(infos)})")

    # カテゴリ補正ロジック
    analysis_docs = [d for d in docs if d.needs_analysis and d.text]
    for doc in analysis_docs:
        if doc.main_category in _AMBIGUOUS_OVERWRITE and doc.is_monthly:
            logger.log(
                f"  ★PATTERN_MISS★ Gemini=月次/その他未分類を月次上書き "
                f"タイトル='{doc.doc_title}': {doc.blob_name}"
            )
            doc.main_category = "月次開示"

        # 月次性カテゴリで月次判定 → サブカテゴリに月次開示を追加
        if doc.main_category in _MONTHLY_SUB_CATEGORIES and doc.is_monthly:
            if "月次開示" not in doc.sub_categories:
                doc.sub_categories.append("月次開示")
                logger.log(f"  サブカテゴリ追加（{doc.main_category} → 月次開示）: {doc.blob_name}")

    logger.log("Phase 3 完了")


def _phase3_poll_and_apply_legacy(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
    phase3_info: dict,
) -> None:
    """後方互換: 旧フォーマット（flash_job/pro_job 2バッチ）の結果を適用する."""
    flash_job_name = phase3_info.get("flash_job")
    pro_job_name   = phase3_info.get("pro_job")
    flash_output   = phase3_info.get("flash_output", "")
    pro_output     = phase3_info.get("pro_output", "")
    key_to_blob    = phase3_info.get("key_to_blob", {})

    doc_by_blob: dict[str, DocInfo] = {d.blob_name: d for d in docs}

    for job_name, tag in [(flash_job_name, "flash"), (pro_job_name, "pro")]:
        if job_name is None:
            continue
        success = _poll_batch_job(client, job_name, logger, f"Analysis-{tag}")
        if not success:
            logger.log(f"  [{tag}] 分析バッチジョブ失敗")

    for tag, output_path in [("flash", flash_output), ("pro", pro_output)]:
        if not output_path:
            continue
        results = _download_batch_results(bucket, output_path)
        logger.log(f"  [{tag}] バッチ結果取得: {len(results)} 件")

        for key, resp_obj in results.items():
            blob_name = key_to_blob.get(key)
            if blob_name is None:
                continue
            doc = doc_by_blob.get(blob_name)
            if doc is None:
                continue
            try:
                text_resp = resp_obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text_resp)
                if isinstance(parsed, list):
                    parsed = parsed[0] if parsed else {}
                doc.is_monthly = parsed.get("is_monthly", False)
                if doc.main_category in _NEEDS_SUB_CATEGORIES:
                    raw_subs = parsed.get("sub_categories", [])
                    doc.sub_categories = [c for c in raw_subs if c in VALID_CATEGORIES]
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.log(f"  分析結果パース失敗 ({key}): {e}")

    # カテゴリ補正ロジック
    analysis_docs = [d for d in docs if d.needs_analysis and d.text]
    for doc in analysis_docs:
        if doc.main_category in _AMBIGUOUS_OVERWRITE and doc.is_monthly:
            doc.main_category = "月次開示"
        if doc.main_category in _MONTHLY_SUB_CATEGORIES and doc.is_monthly:
            if "月次開示" not in doc.sub_categories:
                doc.sub_categories.append("月次開示")

    logger.log("Phase 3 完了 (legacy format)")


def phase3_analysis_batch(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
    filter_fn=None,
) -> None:
    """Phase 3 一気通貫（full mode 用ラッパー）: 投入→ポーリング→適用.

    Args:
        filter_fn: doc を受けて bool を返す predicate。None なら従来の
            `d.needs_analysis and d.text` が使われる。G-3 対応。
    """
    info = _phase3_submit(docs, bucket, client, logger, filter_fn=filter_fn)
    if info is None:
        return
    _phase3_poll_and_apply(docs, bucket, client, logger, info)


# ============================================================
# State 保存/読込（submit/resume モード用）
# ============================================================

BACKFILL_STATE_PREFIX = f"{GCS_BATCH_PREFIX}/backfill_state"


def _serialize_docs(docs: list[DocInfo]) -> list[dict]:
    """DocInfo リストを JSON シリアライズ可能な dict リストに変換する."""
    return [
        {
            "blob_name": d.blob_name,
            "sub_date": d.sub_date,
            "sec_code": d.sec_code,
            "filer_name": d.filer_name,
            "main_category": d.main_category,
            "doc_title": d.doc_title,
            "doc_id": d.doc_id,
            "text": d.text,
            "page_count": d.page_count,
            "extract_method": d.extract_method,
            "needs_vision": d.needs_vision,
            "needs_analysis": d.needs_analysis,
            "analysis_model": d.analysis_model,
            "is_monthly": d.is_monthly,
            "sub_categories": d.sub_categories,
            "disclosure_time": getattr(d, "disclosure_time", None),
        }
        for d in docs
    ]


def _deserialize_docs(data: list[dict]) -> list[DocInfo]:
    """dict リストを DocInfo リストに復元する."""
    docs: list[DocInfo] = []
    for item in data:
        doc = DocInfo(
            blob_name=item["blob_name"],
            sub_date=item["sub_date"],
            sec_code=item["sec_code"],
            filer_name=item["filer_name"],
            main_category=item["main_category"],
            doc_title=item["doc_title"],
            doc_id=item["doc_id"],
            text=item.get("text", ""),
            page_count=item.get("page_count", 0),
            extract_method=item.get("extract_method", "none"),
            needs_vision=item.get("needs_vision", False),
            needs_analysis=item.get("needs_analysis", False),
            analysis_model=item.get("analysis_model", GEMINI_MODEL),
            is_monthly=item.get("is_monthly", False),
            sub_categories=item.get("sub_categories", []),
        )
        if hasattr(doc, "disclosure_time"):
            doc.disclosure_time = item.get("disclosure_time")
        docs.append(doc)
    return docs


def _backfill_state_id(
    date_from: str, date_to: str,
    ticker_from: str | None = None, ticker_to: str | None = None,
) -> str:
    """バックフィル state ファイル用の一意キーを生成する."""
    base = f"{date_from}_{date_to}"
    if ticker_from or ticker_to:
        base += f"_t{ticker_from or 'HEAD'}_{ticker_to or 'TAIL'}"
    return base


def _save_backfill_state(
    bucket, date_from: str, date_to: str,
    docs: list[DocInfo], phase3_info: dict,
    logger: BatchLogger,
    ticker_from: str | None = None, ticker_to: str | None = None,
) -> str:
    """submit モードの状態を GCS に保存する."""
    import gzip as _gzip

    state_id = _backfill_state_id(date_from, date_to, ticker_from, ticker_to)
    state_path = f"{BACKFILL_STATE_PREFIX}_{state_id}.json"
    docs_path  = f"{BACKFILL_STATE_PREFIX}_{state_id}_docs.json.gz"

    # docs を gzip 圧縮して保存
    docs_json = json.dumps(_serialize_docs(docs), ensure_ascii=False)
    compressed = _gzip.compress(docs_json.encode("utf-8"))
    bucket.blob(docs_path).upload_from_string(compressed, content_type="application/gzip")
    logger.log(f"  docs 保存完了: gs://{BUCKET_NAME}/{docs_path} ({len(compressed)//1024}KB)")

    # state メタデータ保存
    state = {
        "date_from": date_from,
        "date_to": date_to,
        "ticker_from": ticker_from,
        "ticker_to": ticker_to,
        "docs_path": docs_path,
        "phase3": phase3_info,
        "created_at": datetime.now(JST).isoformat(),
    }
    state_json = json.dumps(state, ensure_ascii=False, indent=2)
    bucket.blob(state_path).upload_from_string(
        state_json.encode("utf-8"), content_type="application/json"
    )
    logger.log(f"  state 保存完了: gs://{BUCKET_NAME}/{state_path}")
    return state_path


def _load_backfill_state(
    bucket, date_from: str, date_to: str, logger: BatchLogger,
    ticker_from: str | None = None, ticker_to: str | None = None,
) -> tuple[list[DocInfo], dict]:
    """resume モードの状態を GCS から読み込む."""
    import gzip as _gzip

    state_id = _backfill_state_id(date_from, date_to, ticker_from, ticker_to)
    state_path = f"{BACKFILL_STATE_PREFIX}_{state_id}.json"

    logger.log(f"  state 読込: gs://{BUCKET_NAME}/{state_path}")
    state_json = bucket.blob(state_path).download_as_text()
    state = json.loads(state_json)

    docs_path = state["docs_path"]
    logger.log(f"  docs 読込: gs://{BUCKET_NAME}/{docs_path}")
    compressed = bucket.blob(docs_path).download_as_bytes()
    docs_json = _gzip.decompress(compressed).decode("utf-8")
    docs = _deserialize_docs(json.loads(docs_json))

    logger.log(f"  復元完了: {len(docs)} 件")
    return docs, state["phase3"]


# ============================================================
# Phase 4: チャンク化 → Batch Embedding
# ============================================================

# Embedding 対象カテゴリ（ベクトル検索で使うもののみ）
_EMBED_CATEGORIES: set[str] = {"決算短信", "決算説明資料", "月次開示"}


def phase4_chunk_and_embed(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
    embed_client: genai.Client | None = None,
) -> None:
    """対象カテゴリのみチャンク化 + Embedding。それ以外はメタデータ1行のみ.

    Args:
        embed_client: Embedding 用 genai クライアント（us-central1）。
                      None の場合は client をそのまま使う（後方互換）。
    """
    _embed_client = embed_client or client
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 4: 対象なし（スキップ）")
        return

    embed_docs = [d for d in valid_docs if d.main_category in _EMBED_CATEGORIES]
    meta_docs  = [d for d in valid_docs if d.main_category not in _EMBED_CATEGORIES]

    logger.log(f"Phase 4: チャンク化 & Embedding 開始")
    logger.log(f"  Embedding 対象: {len(embed_docs)} 件, メタデータのみ: {len(meta_docs)} 件")

    # Embedding 対象のみチャンク化
    total_chunks = 0
    for doc in embed_docs:
        doc.chunks = create_chunks_for_tdnet(doc.text, doc.doc_title)
        total_chunks += len(doc.chunks)

    logger.log(f"  チャンク化完了: {total_chunks} チャンク")

    if total_chunks == 0:
        logger.log("  Embedding チャンクなし → スキップ")
        return

    # Embedding JSONL 作成
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    input_path  = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_input.jsonl"
    output_path = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_output/"

    # P0-2 (T-6): 同一 chunk_text を持つ複数 chunk（別 doc / 同 doc 別 index）が衝突しないよう
    # (doc, ci) の list で保持。Embedding API は content をキーに dedup するため、送信時点で
    # uniq 化しないと同じ content に対して重複課金になる → seen_content で送信側 dedup。
    from collections import defaultdict
    content_to_chunks: dict[str, list[tuple[DocInfo, int]]] = defaultdict(list)
    seen_content: set[str] = set()
    lines: list[str] = []

    for doc in embed_docs:
        for ci, chunk in enumerate(doc.chunks):
            chunk_text = chunk["chunk_text"]
            content_to_chunks[chunk_text].append((doc, ci))
            if chunk_text not in seen_content:
                seen_content.add(chunk_text)
                lines.append(json.dumps({"content": chunk_text}, ensure_ascii=False))

    input_uri = _upload_jsonl_to_gcs(bucket, input_path, lines)
    logger.log(f"  Embedding JSONL アップロード完了: {input_uri} ({len(lines)} 件)")

    job = _embed_client.batches.create(
        model="text-embedding-004",
        src=input_uri,
        config=genai.types.CreateBatchJobConfig(
            dest=f"gs://{BUCKET_NAME}/{output_path}",
        ),
    )
    logger.log(f"  Embedding バッチジョブ投入: {job.name}")

    success = _poll_batch_job(_embed_client, job.name, logger, "Embedding")
    if not success:
        logger.log("  Embedding バッチジョブ失敗 → 埋め込みなしで続行")
        return

    # 結果取得: numpy float32 で保持してメモリ節約（Python list の ~8倍圧縮）
    for doc in embed_docs:
        doc.embeddings = np.zeros((len(doc.chunks), 768), dtype=np.float32)
        doc.embedding_set = [False] * len(doc.chunks)

    embed_ok = 0
    output_blobs = list(bucket.list_blobs(prefix=output_path))
    for blob in output_blobs:
        if not blob.name.endswith(".jsonl"):
            continue
        with blob.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    chunk_text = obj["instance"]["content"]
                    embedding = obj["predictions"][0]["embeddings"]["values"]
                    # P0-2: 同 chunk_text を持つ全 (doc, ci) に embedding を反映
                    mappings = content_to_chunks.get(chunk_text, [])
                    emb_arr = np.asarray(embedding, dtype=np.float32)
                    for doc, ci in mappings:
                        doc.embeddings[ci, :] = emb_arr
                        doc.embedding_set[ci] = True
                        embed_ok += 1
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue

    logger.log(f"  Embedding 適用完了: {embed_ok}/{total_chunks} 件")
    logger.log("Phase 4 完了")


# ============================================================
# Phase 5: BQ Insert
# ============================================================

def phase5_bq_insert(
    docs: list[DocInfo], bucket, logger: BatchLogger,
) -> tuple[int, int]:
    """全ドキュメントを BQ にインサートする. (processed, skipped) を返す.

    P2-5 (→P0 昇格): 失敗は raise で伝播、戻り値に errors を混ぜない（004 A-2）。
    T-10 対応: insert_rows_json（streaming insert）は streaming buffer が
    90分間 DML をブロックする上、大量行時にレート上限にかかる。
    Load Job + GCS upload (load_table_from_uri) に統一。
    """
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 5: BQ Insert 対象なし（スキップ）")
        return 0, len(docs)

    logger.log(f"Phase 5: BQ Load Job 開始 ({len(valid_docs)} ドキュメント)")
    bq = _get_bq_client()

    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    )
    tmp_path = tmp.name
    row_count = 0
    try:
        with tmp:
            for doc in valid_docs:
                base_row = {
                    "DOC_ID":           doc.doc_id,
                    "TICKER":           doc.sec_code,
                    "FILER_NAME":       doc.filer_name,
                    "FILER_ID":         None,
                    "SUBMISSION_DATE":  doc.sub_date,
                    "DISCLOSURE_TIME":  doc.disclosure_time or None,
                    "MAIN_CATEGORY":    doc.main_category,
                    "DOC_TITLE":        doc.doc_title,
                    "SUB_CATEGORIES":   doc.sub_categories,
                    "PAGE_COUNT":       doc.page_count,
                    "TEXT_LENGTH":      len(doc.text),
                    "SECTION_CATEGORY": doc.doc_title,
                    "FILE_NAME":        doc.blob_name,
                }
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {**base_row, "CHUNK_TEXT": chunk_data["chunk_text"]}
                        if (doc.embedding_set and ci < len(doc.embedding_set)
                                and doc.embedding_set[ci]):
                            row["EMBEDDING"] = doc.embeddings[ci].tolist()
                        tmp.write(json.dumps(row, ensure_ascii=False))
                        tmp.write("\n")
                        row_count += 1
                else:
                    row = {**base_row, "CHUNK_TEXT": None}
                    tmp.write(json.dumps(row, ensure_ascii=False))
                    tmp.write("\n")
                    row_count += 1

        job_config = LoadJobConfig(
            source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=WriteDisposition.WRITE_APPEND,
        )
        now_ts_short = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        gcs_upload_path = f"{GCS_BATCH_PREFIX}/legacy_upload_{now_ts_short}.jsonl"
        _blob = bucket.blob(gcs_upload_path)
        _blob.upload_from_filename(tmp_path)
        gcs_uri = f"gs://{BUCKET_NAME}/{gcs_upload_path}"
        logger.log(f"  NDJSON を GCS へ upload: {gcs_uri} ({row_count} 行)")
        job = bq.load_table_from_uri(gcs_uri, TABLE_ID, job_config=job_config)
        job.result()
        try:
            _blob.delete()
        except Exception:
            pass
        processed = len(valid_docs)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    skipped = len(docs) - len(valid_docs)
    logger.log(f"Phase 5 成功: {processed} doc / {row_count} 行, スキップ {skipped}")
    return processed, skipped


# ============================================================
# Phase 4/5 load版（--job-mode=load 用、Embedding なし、AI_STATUS='pending'）
# ============================================================

def phase5_bq_insert_load(
    docs: list[DocInfo], bucket, logger: BatchLogger,
) -> tuple[int, int]:
    """load モード用: AI_STATUS='pending' でメタデータ1行のみ BQ Load Job で insert.

    P2-5 (→P0): 失敗は raise で伝播、戻り値は (processed, skipped)（004 A-2）。
    旧アーキに準拠し load 時はチャンク化しない（3カテゴリ判定は ai-finalize 時）。
    CHUNK_TEXT / MAIN / SUB / EMBEDDING は空。text は state.json で ai 処理側へ引き渡す。

    BQ Load Job（load_table_from_uri、GCS 経由）で streaming buffer を回避 + upload buffer
    メモリ消費を防ぐ。2026-04-20 バックフィル時の大量 row OOM 予防改修済。
    """
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 5 (load): BQ Insert 対象なし（スキップ）")
        return 0, len(docs)

    logger.log(f"Phase 5 (load): BQ Load Job 投入 ({len(valid_docs)} ドキュメント)")
    bq = _get_bq_client()

    # メモリ節約: tempfile に NDJSON を stream write → GCS upload → load_table_from_uri
    # load_table_from_json は rows list をメモリで持ち続ける + 内部シリアライズで 2倍膨張
    # load_table_from_file は HTTP upload buffer で +1-2 GB 消費の恐れ
    # GCS 経由なら BQ が直接読むので Python プロセスの memory 負荷ほぼゼロ
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    )
    tmp_path = tmp.name
    row_count = 0
    try:
        with tmp:
            for doc in valid_docs:
                row = {
                    "DOC_ID":           doc.doc_id,
                    "TICKER":           doc.sec_code,
                    "FILER_NAME":       doc.filer_name,
                    "FILER_ID":         None,
                    "SUBMISSION_DATE":  doc.sub_date,
                    "DISCLOSURE_TIME":  doc.disclosure_time or None,
                    "MAIN_CATEGORY":    None,
                    "DOC_TITLE":        doc.doc_title,
                    "PAGE_COUNT":       doc.page_count,
                    "TEXT_LENGTH":      len(doc.text),
                    "SECTION_CATEGORY": doc.doc_title,
                    "CHUNK_TEXT":       None,
                    "FILE_NAME":        doc.blob_name,
                    "AI_STATUS":        "pending",
                    "AI_PROCESSED_AT":  None,
                }
                tmp.write(json.dumps(row, ensure_ascii=False))
                tmp.write("\n")
                row_count += 1

        job_config = LoadJobConfig(
            source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=WriteDisposition.WRITE_APPEND,
        )
        now_ts_short = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        gcs_upload_path = f"{GCS_BATCH_PREFIX}/load_upload_{now_ts_short}.jsonl"
        _blob = bucket.blob(gcs_upload_path)
        _blob.upload_from_filename(tmp_path)
        gcs_uri = f"gs://{BUCKET_NAME}/{gcs_upload_path}"
        logger.log(f"  NDJSON を GCS へ upload: {gcs_uri}")
        job = bq.load_table_from_uri(gcs_uri, TABLE_ID, job_config=job_config)
        job.result()
        # GCS上の一時ファイル削除
        try:
            _blob.delete()
        except Exception:
            pass
        processed = len(valid_docs)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    skipped = len(docs) - len(valid_docs)
    logger.log(f"Phase 5 (load) 成功: {processed}, スキップ {skipped}")
    return processed, skipped


# ============================================================
# ai-prepare モード用関数
# ============================================================

AI_JOB_STATE_PREFIX = "ai_job"


def _load_pending_docs_from_bq(
    date_from: str, date_to: str,
    ticker_from: str | None, ticker_to: str | None,
    logger: BatchLogger,
) -> list[DocInfo]:
    """BQ から AI_STATUS='pending' の文書メタデータを取得し DocInfo リストを返す.

    チャンク単位で格納されているため DOC_ID で DISTINCT 集約する。
    """
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"
    # P0-5: SQL parametrize で injection 防止 + partition prune は DATE 型指定で維持
    params = [
        bigquery.ScalarQueryParameter("d_from", "DATE", d_from_iso),
        bigquery.ScalarQueryParameter("d_to", "DATE", d_to_iso),
        bigquery.ArrayQueryParameter("statuses", "STRING", ["pending", "pending_gemma"]),
    ]
    where = [
        "SUBMISSION_DATE BETWEEN @d_from AND @d_to",
        "AI_STATUS IN UNNEST(@statuses)",
    ]
    if ticker_from:
        where.append("TICKER >= @ticker_from")
        params.append(bigquery.ScalarQueryParameter("ticker_from", "STRING", ticker_from))
    if ticker_to:
        where.append("TICKER <= @ticker_to")
        params.append(bigquery.ScalarQueryParameter("ticker_to", "STRING", ticker_to))
    sql = f"""
    SELECT
      DOC_ID, TICKER, FILER_NAME, SUBMISSION_DATE, DOC_TITLE,
      FILE_NAME, DISCLOSURE_TIME, PAGE_COUNT
    FROM (
      SELECT
        DOC_ID, TICKER, FILER_NAME, SUBMISSION_DATE, DOC_TITLE,
        FILE_NAME, DISCLOSURE_TIME, PAGE_COUNT,
        ROW_NUMBER() OVER (PARTITION BY DOC_ID ORDER BY SUBMISSION_DATE) AS rn
      FROM `{TABLE_ID}`
      WHERE {' AND '.join(where)}
    )
    WHERE rn = 1
    ORDER BY SUBMISSION_DATE, TICKER
    """
    logger.log(f"BQ pending 文書メタデータ取得中 ({date_from}〜{date_to})...")
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(_get_bq_client().query(sql, job_config=job_config).result())
    docs: list[DocInfo] = []
    for r in rows:
        docs.append(DocInfo(
            blob_name=r.FILE_NAME or "",
            sub_date=r.SUBMISSION_DATE.strftime("%Y-%m-%d"),
            sec_code=r.TICKER or "",
            filer_name=r.FILER_NAME or "",
            main_category="",
            doc_title=r.DOC_TITLE or "",
            doc_id=r.DOC_ID,
            disclosure_time=r.DISCLOSURE_TIME or "",
            page_count=r.PAGE_COUNT or 0,
        ))
    logger.log(f"BQ pending 文書数: {len(docs)} 件")
    return docs


def phase1_extract_for_docs(
    bucket, docs: list[DocInfo], logger: BatchLogger,
) -> None:
    """指定された DocInfo リストの blob を GCS からダウンロード → テキスト再抽出 + MAIN 決定.

    load モード時には BQ に text を保存しないため、GCS PDF から直接テキスト抽出する。
    旧アーキ準拠で、ファイル名からカテゴリを抽出し `_correct_category_by_title` で
    月次補正した結果を `doc.main_category` に設定（state.json の pre_main_category に保存）。
    """
    logger.log(f"Phase 1 (ai-prepare): テキスト再抽出 + MAIN 決定 開始 ({len(docs)} ドキュメント)")
    for doc in docs:
        if not doc.blob_name:
            logger.log(f"  blob_name 無し → スキップ: {doc.doc_id}")
            continue

        # ファイル名から MAIN 抽出 + 月次補正（旧アーキ準拠）
        try:
            _, _, _, main_from_filename, _, _ = parse_tdnet_filename(doc.blob_name)
            doc.main_category = _correct_category_by_title(main_from_filename, doc.doc_title, doc.sec_code)
        except Exception as e:
            logger.log(f"  parse_tdnet_filename 失敗: {doc.blob_name} - {e}")
            doc.main_category = "その他（未分類）"

        # GCS PDF ダウンロード → テキスト抽出（PyPDF2 → pdfminer フォールバック）
        try:
            pdf_bytes = bucket.blob(doc.blob_name).download_as_bytes()
        except Exception as e:
            logger.log(f"  GCS ダウンロード失敗: {doc.blob_name} - {e}")
            continue

        text, page_count = _extract_text_pypdf2(pdf_bytes)
        doc.extract_method = "pypdf2"
        if _content_length(text) < _MIN_TEXT_LEN:
            text_pm = _extract_text_pdfminer(pdf_bytes)
            if _content_length(text_pm) >= _MIN_TEXT_LEN:
                text = text_pm
                doc.extract_method = "pdfminer"

        doc.text = text
        if page_count:
            doc.page_count = page_count
        doc.needs_vision = _content_length(text) < _MIN_TEXT_LEN
        if doc.needs_vision:
            doc.extract_method = "none"

    logger.log(f"Phase 1 (ai-prepare) 完了: "
               f"抽出成功 {sum(1 for d in docs if d.text)} / "
               f"Vision OCR 必要 {sum(1 for d in docs if d.needs_vision)}")


def _save_ai_prepare_state(
    bucket, run_id: str, docs: list[DocInfo], logger: BatchLogger,
) -> str:
    """ai-prepare 完了時点の state.json を GCS に保存.

    後段 Gemma TPU supervisor がこの state を読んで推論対象を決める。
    T-7 対応: docs が大量（19K+）でも state dict を in-memory で JSON 化せず、
    tempfile にプログレッシブ write → upload_from_filename で GB 級メモリ膨張を回避。
    """
    import tempfile
    path = f"{AI_JOB_STATE_PREFIX}/{run_id}/state.json"
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    )
    tmp_path = tmp.name
    try:
        with tmp:
            tmp.write('{')
            tmp.write(f'"run_id": {json.dumps(run_id)}, ')
            tmp.write(f'"created_at_jst": {json.dumps(datetime.now(JST).isoformat())}, ')
            tmp.write(f'"doc_count": {len(docs)}, ')
            tmp.write('"docs": [')
            for i, d in enumerate(docs):
                if i > 0:
                    tmp.write(',')
                doc_obj = {
                    "doc_id": d.doc_id,
                    "ticker": d.sec_code,
                    "submission_date": d.sub_date,
                    "file_name": d.blob_name,
                    "doc_title": d.doc_title,
                    "text": d.text,
                    "pre_main_category": d.main_category or None,
                    "extract_method": d.extract_method,
                }
                tmp.write(json.dumps(doc_obj, ensure_ascii=False))
            tmp.write(']}')
        bucket.blob(path).upload_from_filename(tmp_path, content_type="application/json")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    uri = f"gs://{bucket.name}/{path}"
    logger.log(f"state.json 保存完了: {uri} ({len(docs)} docs, streaming write)")
    return uri


def _update_ai_status(
    doc_ids: list[str], new_status: str, logger: BatchLogger,
) -> int:
    """指定された DOC_ID 群の AI_STATUS を更新する.

    P0-5: IN 句を parametrize（UNNEST(@doc_ids)）で injection 防止。
    M-4 (P1 D-4): 行数取得は `job.num_dml_affected_rows` の public API を使用
    （旧 `result._job_ref.job_id` は private API、`total_rows` は SELECT 用で UPDATE で 0 返却）。

    NR-3: doc_ids が数万件以上になる場合は BQ_BATCH_SIZE 相当で分割送信を検討
    （現状 UNNEST 上限は数百万件レベルだが、単一 DML の変更行数制限 10M に触れる可能性）。
    """
    if not doc_ids:
        return 0
    bq = _get_bq_client()
    sql = f"""
    UPDATE `{TABLE_ID}`
    SET AI_STATUS = @new_status
    WHERE DOC_ID IN UNNEST(@doc_ids)
      AND AI_STATUS IN ('pending', 'pending_gemma')
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
        bigquery.ArrayQueryParameter("doc_ids", "STRING", doc_ids),
    ])
    job = bq.query(sql, job_config=job_config)
    job.result()
    affected = job.num_dml_affected_rows or 0
    logger.log(f"BQ AI_STATUS UPDATE 完了: {new_status} に {affected} 行（対象 {len(doc_ids)} doc）")
    return affected


# ============================================================
# ai-finalize モード用関数
# ============================================================

def _load_ai_state(bucket, run_id: str) -> dict:
    """ai-prepare で保存された state.json を読み込む.

    T-7 対応: state.json は docs 配列を含み 100MB+ になりうるので
    blob.open("rb") でストリーム読み → json.load でファイルハンドル経由パース。
    """
    path = f"{AI_JOB_STATE_PREFIX}/{run_id}/state.json"
    with bucket.blob(path).open("rb") as f:
        return json.load(f)


def _load_gemma_results(bucket, run_id: str) -> dict[str, dict]:
    """Gemma TPU supervisor が書き出した gemma_CURRENT.jsonl を読む.

    gemma_CURRENT.jsonl は1件1行の JSONL 形式。
    各行は doc_id, main_category, sub_categories, is_monthly を含む想定。
    存在しない場合は空 dict を返す（F/G 未実装時のテスト用）。

    G-1 対応: blob.open("r") で行単位ストリーム読み（`download_as_text()` は
    数百MB 規模で一括ロードすると OOM 予備軍。449MB 実測あり）。
    """
    path = f"{AI_JOB_STATE_PREFIX}/{run_id}/gemma_CURRENT.jsonl"
    blob = bucket.blob(path)
    if not blob.exists():
        return {}
    results: dict[str, dict] = {}
    parse_errors = 0
    with blob.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                doc_id = rec.get("doc_id")
                if doc_id:
                    results[doc_id] = rec
            except json.JSONDecodeError:
                parse_errors += 1
                continue
    if parse_errors:
        # silent continue は禁則（004 A-3）
        print(f"[WARN] gemma_CURRENT.jsonl: {parse_errors} 行がパース失敗", file=sys.stderr)
    return results


def _docs_from_ai_state(state: dict) -> list[DocInfo]:
    """state.json から DocInfo リストを復元する."""
    docs: list[DocInfo] = []
    for d in state.get("docs", []):
        docs.append(DocInfo(
            blob_name=d.get("file_name", ""),
            sub_date=d.get("submission_date", ""),
            sec_code=d.get("ticker", ""),
            filer_name="",
            main_category=d.get("pre_main_category") or "",
            doc_title=d.get("doc_title", ""),
            doc_id=d.get("doc_id", ""),
            text=d.get("text", ""),
            extract_method=d.get("extract_method", "none"),
        ))
    return docs


def _apply_gemma_results(docs: list[DocInfo], gemma_results: dict[str, dict],
                         logger: BatchLogger) -> int:
    """Gemma 推論結果を DocInfo に反映する（旧アーキ MAIN/SUB 補正ルール準拠）.

    MAIN 決定:
      1. pre_main_category（state.json の doc.main_category、ファイル名由来 +
         _correct_category_by_title 済み）を基本とする
      2. pre_main in _AMBIGUOUS_OVERWRITE ('その他（未分類）') かつ is_monthly=True
         → 「月次開示」に上書き
      3. それ以外は pre_main を維持

    SUB 決定:
      - Gemma 出力 sub_categories を採用（VALID_CATEGORIES でフィルタ）
      - doc.main_category in _MONTHLY_SUB_CATEGORIES かつ is_monthly=True
        → sub に「月次開示」を追加（旧 _phase3_poll_and_apply と同じ）

    Returns:
        missing_count: Gemma 結果が無かった doc 数。TPU preempt 等による
        部分失敗を検知するシグナル（G-2 対応、呼び出し側で errors に加算する）。
    """
    applied = 0
    missing = 0
    missing_sample: list[str] = []  # 先頭数件の doc_id を log に出す
    for doc in docs:
        g = gemma_results.get(doc.doc_id) or {}
        is_monthly = bool(g.get("is_monthly", False))
        subs_raw = g.get("sub_categories", []) or []
        subs = [s for s in subs_raw if s in VALID_CATEGORIES]

        # MAIN 決定（旧 _AMBIGUOUS_OVERWRITE ルール）
        pre_main = doc.main_category or "その他（未分類）"
        if pre_main in _AMBIGUOUS_OVERWRITE and is_monthly:
            doc.main_category = "月次開示"
        else:
            doc.main_category = pre_main

        # SUB 決定（Gemma 結果 + 月次系カテゴリ補正）
        final_sub = set(subs)
        if doc.main_category in _MONTHLY_SUB_CATEGORIES and is_monthly:
            final_sub.add("月次開示")
        doc.sub_categories = sorted(final_sub)
        doc.sub_categories_gemma = sorted(final_sub)  # Gemini受注マージの際のバックアップ
        doc.is_monthly = is_monthly

        if g:
            applied += 1
        else:
            missing += 1
            if len(missing_sample) < 5:
                missing_sample.append(doc.doc_id)
    if missing:
        logger.log(
            f"[WARN] Gemma 結果適用: {applied} 件 / Gemma 結果なし: {missing} 件 "
            f"(sample: {missing_sample})"
        )
    else:
        logger.log(f"Gemma 結果適用: {applied} 件 / Gemma 結果なし: 0 件")
    return missing


def phase_gemini_tanshin_batch(
    docs: list[DocInfo], bucket, genai_client, logger: BatchLogger,
) -> None:
    """Gemini Flash Batch: MAIN_CATEGORY='決算短信' のみを対象に走らせ、受注判定用結果を得る.

    G-3 対応: filter_fn で決算短信のみを選択。needs_analysis 一時 mutation を廃止。
    Gemini 結果は doc.sub_categories に入るので、_merge_gemini_juchu で
    sub_categories_gemma とマージする。
    """
    targets = [d for d in docs if d.main_category == "決算短信" and d.text]
    if not targets:
        logger.log("Gemini Flash Batch: 決算短信の対象なし → スキップ")
        return

    logger.log(f"Gemini Flash Batch (決算短信のみ): {len(targets)} 件を投入")
    try:
        phase3_analysis_batch(
            docs, bucket, genai_client, logger,
            filter_fn=lambda d: d.main_category == "決算短信" and bool(d.text),
        )
    except Exception as e:
        logger.log(f"警告: Gemini Flash Batch 失敗 → Gemma 結果のみで続行: {e}")
        logger.log(traceback.format_exc())


def _merge_gemini_juchu(docs: list[DocInfo], logger: BatchLogger) -> None:
    """Gemini Flash Batch 結果の「受注高/受注残高」を Gemma SUB に差分適用する.

    前提: doc.sub_categories_gemma = Gemma 結果 / doc.sub_categories = Gemini 結果（決算短信のみ）
    決算短信以外: Gemma 結果をそのまま doc.sub_categories にセット（Gemini は動いてない）
    決算短信: Gemma 結果 + Gemini の受注判定（add/remove）
    """
    merged_cnt = 0
    for doc in docs:
        gemma_sub = set(doc.sub_categories_gemma or [])
        if doc.main_category == "決算短信":
            gemini_sub = set(doc.sub_categories or [])
            if "受注高/受注残高" in gemini_sub:
                gemma_sub.add("受注高/受注残高")
            else:
                gemma_sub.discard("受注高/受注残高")
            merged_cnt += 1
        doc.sub_categories = sorted(gemma_sub)
    logger.log(f"受注マージ完了: 決算短信 {merged_cnt} 件で Gemini 受注判定を反映")


def _delete_pending_gemma_rows(
    doc_ids: list[str], date_from: str, date_to: str, logger: BatchLogger,
) -> int:
    """pending_* 状態の既存行を DELETE する（ai-finalize の再 INSERT 前）.

    SUBMISSION_DATE partition prune を効かせてコストを抑える。
    completed 以外を全て対象（pending / pending_gemma / pending_finalize / NULL）。

    Args:
        doc_ids: 対象 DOC_ID リスト
        date_from: YYYYMMDD（partition prune 用）
        date_to: YYYYMMDD（partition prune 用）
    """
    if not doc_ids:
        return 0
    bq = _get_bq_client()
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"
    # P0-5: SQL parametrize で injection 防止。
    # #6: BETWEEN は DATE 型で渡せば partition prune が効く（string 不可）
    sql = f"""
    DELETE FROM `{TABLE_ID}`
    WHERE DOC_ID IN UNNEST(@doc_ids)
      AND SUBMISSION_DATE BETWEEN @d_from AND @d_to
      AND (AI_STATUS IS NULL OR AI_STATUS != 'completed')
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("doc_ids", "STRING", doc_ids),
        bigquery.ScalarQueryParameter("d_from", "DATE", d_from_iso),
        bigquery.ScalarQueryParameter("d_to", "DATE", d_to_iso),
    ])
    job = bq.query(sql, job_config=job_config)
    job.result()
    affected = job.num_dml_affected_rows or 0
    logger.log(f"pending_* 行 DELETE: {affected} 行 ({date_from}〜{date_to} partition prune)")
    return affected


def phase5_bq_insert_finalize(
    docs: list[DocInfo], bucket, logger: BatchLogger,
) -> tuple[int, int]:
    """ai-finalize 用 BQ Insert: AI_STATUS='completed' で Load Job 投入.

    P2-5 (→P0): 失敗は raise で伝播、戻り値は (processed, skipped)（004 A-2）。
    旧アーキ同様、3カテゴリ（_EMBED_CATEGORIES: 決算短信/決算説明資料/月次開示）のみ
    チャンク複数行+Embedding 付き。他カテゴリはメタデータ1行（CHUNK_TEXT=NULL, EMBEDDING なし）。

    Load Job (load_table_from_uri) を使用して streaming buffer を回避し、
    後続の DML（UPDATE/DELETE）を即時実行可能にする。
    """
    # 3カテゴリ外 doc の chunks を明示的に空化（保険）
    for doc in docs:
        if doc.main_category not in _EMBED_CATEGORIES:
            doc.chunks = []
            doc.embeddings = []
            doc.embedding_set = []

    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 5 (ai-finalize): BQ Insert 対象なし")
        return 0, len(docs)

    logger.log(f"Phase 5 (ai-finalize): BQ Load Job 投入 ({len(valid_docs)} ドキュメント)")
    bq = _get_bq_client()
    now_ts = datetime.now(JST).isoformat()

    # メモリ節約: rows を list で保持せず NDJSON 一時ファイルに stream append
    # その後 load_table_from_file でストリーム読込 → BQ へ Load Job
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False
    )
    tmp_path = tmp.name
    row_count = 0
    try:
        with tmp:
            for doc in valid_docs:
                base_row = {
                    "DOC_ID":           doc.doc_id,
                    "TICKER":           doc.sec_code,
                    "FILER_NAME":       doc.filer_name,
                    "FILER_ID":         None,
                    "SUBMISSION_DATE":  doc.sub_date,
                    "DISCLOSURE_TIME":  doc.disclosure_time or None,
                    "MAIN_CATEGORY":    doc.main_category or None,
                    "DOC_TITLE":        doc.doc_title,
                    "PAGE_COUNT":       doc.page_count,
                    "TEXT_LENGTH":      len(doc.text),
                    "SECTION_CATEGORY": doc.doc_title,
                    "FILE_NAME":        doc.blob_name,
                    "AI_STATUS":        "completed",
                    "AI_PROCESSED_AT":  now_ts,
                }
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        row = {**base_row, "CHUNK_TEXT": chunk_data["chunk_text"]}
                        if doc.sub_categories:
                            row["SUB_CATEGORIES"] = doc.sub_categories
                        # numpy 保持 → BQ 書込時に float list へ変換
                        if (doc.embedding_set and ci < len(doc.embedding_set)
                                and doc.embedding_set[ci]):
                            row["EMBEDDING"] = doc.embeddings[ci].tolist()
                        tmp.write(json.dumps(row, ensure_ascii=False))
                        tmp.write("\n")
                        row_count += 1
                else:
                    # Embedding 対象外: メタデータ1行のみ
                    row = {**base_row, "CHUNK_TEXT": None}
                    if doc.sub_categories:
                        row["SUB_CATEGORIES"] = doc.sub_categories
                    tmp.write(json.dumps(row, ensure_ascii=False))
                    tmp.write("\n")
                    row_count += 1

        job_config = LoadJobConfig(
            source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=WriteDisposition.WRITE_APPEND,
        )
        # メモリ節約: tempfile を GCS へアップロード → load_table_from_uri で読込
        # load_table_from_file は HTTP upload buffer で数GB膨張する OOM 事故を防ぐ（2026-04-20 19K doc で発覚）
        now_ts_short = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        gcs_upload_path = f"{GCS_BATCH_PREFIX}/ai_finalize_upload_{now_ts_short}.jsonl"
        _blob = bucket.blob(gcs_upload_path)
        _blob.upload_from_filename(tmp_path)
        _log_rss(logger, "after GCS upload")
        gcs_uri = f"gs://{BUCKET_NAME}/{gcs_upload_path}"
        logger.log(f"  NDJSON を GCS へ upload: {gcs_uri}")
        job = bq.load_table_from_uri(gcs_uri, TABLE_ID, job_config=job_config)
        job.result()
        # GCS上の一時ファイル削除
        try:
            _blob.delete()
        except Exception:
            pass
        processed = len(valid_docs)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    skipped = len(docs) - len(valid_docs)
    logger.log(f"Phase 5 (ai-finalize) 成功: {processed}, スキップ {skipped}, 総行数 {row_count}")
    return processed, skipped


def _cleanup_ai_state(bucket, run_id: str, logger: BatchLogger) -> None:
    """ai-finalize 完了時の GCS 状態ファイル後始末."""
    prefix = f"{AI_JOB_STATE_PREFIX}/{run_id}/"
    deleted = 0
    for blob in bucket.list_blobs(prefix=prefix):
        try:
            blob.delete()
            deleted += 1
        except Exception as e:
            logger.log(f"  GCS クリーンアップ失敗: {blob.name} - {e}")
    logger.log(f"GCS state クリーンアップ: {deleted} blob 削除")


def phase_ai_prepare(
    bucket, genai_client, docs: list[DocInfo], run_id: str, logger: BatchLogger,
) -> tuple[int, int]:
    """ai-prepare 本体: OCR + 正規表現月次補正 + state.json 保存.

    Returns: (成功 doc 数, エラー doc 数)
    """
    if not docs:
        logger.log("ai-prepare: 対象 doc なし")
        return 0, 0

    # 0. 同 run_id の GCS 残骸削除（対策 A: 再実行時の resume 誤動作を防ぐ）
    #    gemma_CURRENT.jsonl / _SUCCESS を削除。state.json は upload_from_string で上書きされるので対象外
    for name in ("gemma_CURRENT.jsonl", "_SUCCESS"):
        path = f"{AI_JOB_STATE_PREFIX}/{run_id}/{name}"
        try:
            blob = bucket.blob(path)
            if blob.exists():
                blob.delete()
                logger.log(f"[cleanup] 旧 {name} 削除: {path}")
        except Exception as e:
            logger.log(f"[cleanup] {path} 削除失敗（続行）: {e}")

    # 1. Vision OCR（Phase 1 再抽出で needs_vision になった分）
    try:
        phase2_vision_batch(docs, bucket, genai_client, logger)
        logger.flush_to_gcs()
    except Exception as e:
        logger.log(f"警告: Vision OCR 中にエラー（続行）: {e}")
        logger.log(traceback.format_exc())

    # 2. MAIN_CATEGORY は phase1_extract_for_docs で既に parse_tdnet_filename +
    #    _correct_category_by_title により設定済み（state.json の pre_main_category として保存）

    # 3. state.json を GCS に保存
    _save_ai_prepare_state(bucket, run_id, docs, logger)

    success = sum(1 for d in docs if d.text)
    errors = len(docs) - success
    return success, errors


# ============================================================
# ベクトルインデックス作成
# ============================================================

def _create_vector_index(logger: BatchLogger) -> None:
    """BQ ベクトルインデックスを作成/更新する."""
    logger.log("ベクトルインデックスの作成/更新を確認しています...")
    sql = f"""
    CREATE VECTOR INDEX IF NOT EXISTS tdnet_doc_vector_index
    ON `{TABLE_ID}`(EMBEDDING)
    OPTIONS(index_type = 'IVF', distance_type = 'COSINE', ivf_options = '{{"num_lists": 1000}}');
    """
    try:
        _get_bq_client().query(sql).result()
        logger.log("ベクトルインデックスの作成命令が完了しました。")
    except Exception as e:
        logger.log(f"警告 (インデックス作成): {e}")


# ============================================================
# メイン ETL
# ============================================================

def run_tdnet_batch_etl(
    date_from: str, date_to: str,
    ticker_from: str | None = None, ticker_to: str | None = None,
    run_mode: str = "full",
    job_mode: str = "full",
) -> None:
    """TDnet Batch ETL メイン処理.

    job_mode（新アーキ分離、優先される）:
      full         — 従来互換: Phase 1→2→3→4→5 一気通貫（非推奨、段階的に廃止予定）
      load         — Phase 0/1/4_chunk_only/5_load のみ（AI_STATUS='pending'）
      ai-prepare   — OCR + 正規表現月次補正 + state.json 保存（未実装）
      ai-finalize  — Gemma + Gemini + Embedding + BQ UPDATE（未実装）

    run_mode（job_mode='full' 時のみ有効、従来互換）:
      full   — Phase 1→2→3→4→5 一気通貫
      submit — Phase 1→2→3(投入のみ) → state保存 → exit（バックフィル用）
      resume — state読込 → Phase 3(結果適用)→4→5（バックフィル用）
    """
    storage_client = _get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)

    start_time_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    log_blob_name  = f"log/tdnet_load_to_bq_{start_time_str}_{job_mode}_log.txt"
    logger = BatchLogger(bucket, log_blob_name)

    logger.log(f"TDnet Batch ETL 開始: {date_from} ～ {date_to} [job_mode={job_mode}, run_mode={run_mode}]")
    if ticker_from or ticker_to:
        logger.log(f"TICKER範囲: {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    logger.log(f"実行環境: {RUNTIME}")

    docs: list[DocInfo] = []
    processed = skipped = errors = 0

    # ── 新アーキ分離モード ──
    if job_mode == "load":
        try:
            logger.log("[load モード] BQ 取込済みファイル一覧を取得中...")
            processed_files = _load_processed_file_names(date_from, date_to)
            logger.log(f"取込済みファイル数: {len(processed_files)} 件")

            time_map = _load_disclosure_time_map(bucket, date_from, date_to, logger)

            docs = phase1_scan_and_extract(
                bucket, date_from, date_to, ticker_from, ticker_to, processed_files, logger,
                time_map=time_map,
            )
            logger.flush_to_gcs()

            if not docs:
                logger.log("処理対象ドキュメントなし → 終了")
                return

            # 旧アーキ準拠: load 時はチャンク化しない（3カテゴリ判定不可のため）。
            # ai-finalize で 3カテゴリのみチャンク化 + Embedding する。
            processed, skipped = phase5_bq_insert_load(docs, bucket, logger)
            # Phase 5 は raise on failure なのでここに到達 = 成功、errors は 0 維持
        except Exception as e:
            logger.log(f"致命的なエラーで処理が中断: {e}")
            logger.log(traceback.format_exc())
            errors = 1
            raise  # main() で exit 1 させるため再送出（B-1）
        finally:
            logger.log("=== TDnet Batch ETL (load) 処理結果サマリー ===")
            logger.log(f"対象期間              : {date_from} ～ {date_to}")
            logger.log(f"総ドキュメント数       : {len(docs)} 件")
            logger.log(f"成功/スキップ/エラー  : {processed} / {skipped} / {errors}")
            logger.flush_to_gcs()
            print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")
        return

    if job_mode == "ai-prepare":
        # run_id: Workflows execution ID（環境変数 RUN_ID）or UUID4（衝突回避）
        run_id = os.environ.get("RUN_ID") or str(uuid.uuid4())
        logger.log(f"[ai-prepare] run_id={run_id}")
        try:
            genai_client = _get_genai_client()
            docs = _load_pending_docs_from_bq(
                date_from, date_to, ticker_from, ticker_to, logger,
            )
            logger.flush_to_gcs()
            if not docs:
                logger.log("pending ドキュメントなし → 終了")
                return

            phase1_extract_for_docs(bucket, docs, logger)
            logger.flush_to_gcs()

            # AI_STATUS を先に pending_gemma に更新（#6 順序修正）
            # DB update 失敗 → state 保存まで到達せず、retry で同じ pending 行を再処理（冪等）
            # state 保存 → DB update 失敗 の旧順序だと orphan state.json が残りやすい
            doc_ids_with_text = [d.doc_id for d in docs if d.text]
            _update_ai_status(doc_ids_with_text, "pending_gemma", logger)
            logger.flush_to_gcs()

            # DB update 成功後に state.json 生成 + GCS upload
            success, errors_cnt = phase_ai_prepare(
                bucket, genai_client, docs, run_id, logger,
            )
            logger.flush_to_gcs()

            processed = success
            errors = errors_cnt
        except Exception as e:
            logger.log(f"致命的なエラーで処理が中断: {e}")
            logger.log(traceback.format_exc())
            errors = 1
            raise  # main() で exit 1 させるため再送出（B-1）
        finally:
            logger.log("=== TDnet Batch ETL (ai-prepare) 処理結果サマリー ===")
            logger.log(f"対象期間              : {date_from} ～ {date_to}")
            logger.log(f"run_id                : {run_id}")
            logger.log(f"総ドキュメント数       : {len(docs)} 件")
            logger.log(f"成功/エラー           : {processed} / {errors}")
            logger.flush_to_gcs()
            print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")
        return

    if job_mode == "ai-finalize":
        run_id = os.environ.get("RUN_ID")
        if not run_id:
            logger.log("[ai-finalize] RUN_ID 環境変数が必須です")
            logger.flush_to_gcs()
            raise SystemExit("RUN_ID 環境変数が必須です")
        logger.log(f"[ai-finalize] run_id={run_id}")
        try:
            _log_rss(logger, "start")
            genai_client = _get_genai_client()
            embed_client = _get_genai_client_embedding()

            # 1. state.json + gemma_CURRENT.jsonl 読込
            state = _load_ai_state(bucket, run_id)
            docs = _docs_from_ai_state(state)
            logger.log(f"state.json から {len(docs)} 件復元")
            _log_rss(logger, "after state.json load")

            gemma_results = _load_gemma_results(bucket, run_id)
            logger.log(f"gemma_CURRENT.jsonl: {len(gemma_results)} 件")
            gemma_missing = _apply_gemma_results(docs, gemma_results, logger)
            # G-2: Gemma 部分失敗（TPU preempt 等）は専用変数で追跡
            # P0-1 (旧実装の regression 対策): Phase 5 戻り値で上書きされないよう errors とは別で保持
            gemma_missing_total = gemma_missing
            if gemma_missing_total > 0:
                logger.log(f"[G-2] Gemma missing={gemma_missing_total} 件（cleanup gate に反映）")
            _log_rss(logger, "after gemma apply")
            logger.flush_to_gcs()

            # 2. Gemini Flash Batch（決算短信のみ、受注判定）
            phase_gemini_tanshin_batch(docs, bucket, genai_client, logger)
            _log_rss(logger, "after gemini batch")
            logger.flush_to_gcs()

            # 3. 受注判定を Gemma SUB にマージ（決算短信以外は Gemma 結果そのまま）
            _merge_gemini_juchu(docs, logger)
            logger.flush_to_gcs()

            # 4. Embedding Batch（3カテゴリ限定、既存ロジック流用）
            phase4_chunk_and_embed(docs, bucket, genai_client, logger, embed_client)
            _log_rss(logger, "after embedding batch (peak)")
            logger.flush_to_gcs()

            # 5. partition prune のため docs の SUBMISSION_DATE min/max を計算
            #    text 有りの doc のみ insert/delete 対象（P0-7: text 無し doc は pending_* 保持）
            inserted_docs = [d for d in docs if d.text]
            text_missing_total = len(docs) - len(inserted_docs)
            inserted_doc_ids = [d.doc_id for d in inserted_docs]
            submission_dates = [d.sub_date for d in inserted_docs if d.sub_date]  # YYYY-MM-DD
            if submission_dates:
                min_d = min(submission_dates).replace("-", "")  # YYYYMMDD
                max_d = max(submission_dates).replace("-", "")
            else:
                min_d = date_from
                max_d = date_to
            if text_missing_total > 0:
                logger.log(
                    f"[P0-7] text 取得失敗 doc: {text_missing_total} 件 "
                    f"→ pending_* 行を保持（再実行で拾える）"
                )

            # 6. BQ INSERT（completed）— 先に実行してデータを保全
            #    失敗時は raise で phase5_errors=0 を保ったまま except に抜ける（B-4 順序入替）
            _log_rss(logger, "before BQ insert")
            phase5_errors = 0
            processed, skipped = phase5_bq_insert_finalize(docs, bucket, logger)
            _log_rss(logger, "after BQ insert")

            # 7. BQ DELETE（pending_* 既存行）— INSERT 成功 + text あり doc に限定
            #    P0-7: text 無し doc の pending_* 行は残して次回再実行で拾えるようにする
            _delete_pending_gemma_rows(inserted_doc_ids, min_d, max_d, logger)

            # 8. ベクトルインデックス更新（Embedding が入った場合のみ）
            # d.embeddings は np.ndarray なので any() に渡すと真偽評価エラー。embedding_set で判定。
            if processed > 0 and any(any(d.embedding_set) for d in docs if d.embedding_set):
                _create_vector_index(logger)

            # 9. GCS state ファイル cleanup — 全エラー（phase5 + gemma_missing + text_missing）
            #    が 0 の場合のみ実行（P0-1 regression の根本対策: errors を個別追跡して合算）
            total_errors = phase5_errors + gemma_missing_total + text_missing_total
            if processed > 0 and total_errors == 0:
                _cleanup_ai_state(bucket, run_id, logger)
            else:
                logger.log(
                    f"GCS state cleanup をスキップ "
                    f"(processed={processed}, phase5_errors={phase5_errors}, "
                    f"gemma_missing={gemma_missing_total}, text_missing={text_missing_total})"
                )
            # summary 用に errors 変数へ合算値を反映
            errors = total_errors
        except Exception as e:
            logger.log(f"致命的なエラーで処理が中断: {e}")
            logger.log(traceback.format_exc())
            errors = 1
            raise  # main() で exit 1 させるため再送出（B-1）
        finally:
            logger.log("=== TDnet Batch ETL (ai-finalize) 処理結果サマリー ===")
            logger.log(f"run_id                : {run_id}")
            logger.log(f"総ドキュメント数       : {len(docs)} 件")
            logger.log(f"成功/スキップ/エラー  : {processed} / {skipped} / {errors}")
            logger.flush_to_gcs()
            print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")
        return

    # ── 従来互換（job_mode='full'）: 以下は既存ロジック ──
    genai_client = _get_genai_client()
    embed_client = _get_genai_client_embedding()

    try:
        if run_mode == "resume":
            # ── resume モード: GCS から state を読み込み Phase 3 結果適用から再開 ──
            logger.log("resume モード: state を GCS から読み込み中...")
            docs, phase3_info = _load_backfill_state(
                bucket, date_from, date_to, logger,
                ticker_from=ticker_from, ticker_to=ticker_to,
            )
            logger.flush_to_gcs()

            _phase3_poll_and_apply(docs, bucket, genai_client, logger, phase3_info)
            logger.flush_to_gcs()

            phase4_chunk_and_embed(docs, bucket, genai_client, logger, embed_client)
            logger.flush_to_gcs()

            processed, skipped = phase5_bq_insert(docs, bucket, logger)
            if processed > 0:
                _create_vector_index(logger)

            # state / docs / ロックをクリーンアップ
            state_id = _backfill_state_id(date_from, date_to, ticker_from, ticker_to)
            for suffix in [".json", "_docs.json.gz", ".json.resume_triggered"]:
                path = f"{BACKFILL_STATE_PREFIX}_{state_id}{suffix}"
                try:
                    bucket.blob(path).delete()
                except Exception:
                    pass
            logger.log("state クリーンアップ完了")

        else:
            # ── full / submit モード: Phase 1 から開始 ──

            # ガード: バックフィル（31日超）で full モード禁止（Gemini再分析の二重課金防止）
            _from = datetime.strptime(date_from, "%Y%m%d")
            _to   = datetime.strptime(date_to,   "%Y%m%d")
            if run_mode == "full" and (_to - _from).days > 31:
                msg = (
                    f"fullモードはバックフィル（{(_to - _from).days}日間）では禁止です。"
                    " submit/resume を使用してください（Gemini Phase 3 二重課金防止）。"
                    " 強制実行するには環境変数 ALLOW_FULL_BACKFILL=1 を設定してください。"
                )
                if not os.environ.get("ALLOW_FULL_BACKFILL"):
                    logger.log(f"[BLOCKED] {msg}")
                    logger.flush_to_gcs()
                    raise SystemExit(msg)
                logger.log(f"[WARNING] {msg}（ALLOW_FULL_BACKFILL=1 で強制実行中）")

            logger.log("BQ 取込済みファイル一覧を取得中...")
            processed_files = _load_processed_file_names(date_from, date_to)
            logger.log(f"取込済みファイル数: {len(processed_files)} 件")

            time_map = _load_disclosure_time_map(bucket, date_from, date_to, logger)

            docs = phase1_scan_and_extract(
                bucket, date_from, date_to, ticker_from, ticker_to, processed_files, logger,
                time_map=time_map,
            )
            logger.flush_to_gcs()

            if not docs:
                logger.log("処理対象ドキュメントなし → 終了")
                return

            phase2_vision_batch(docs, bucket, genai_client, logger)
            logger.flush_to_gcs()

            if run_mode == "submit":
                # ── submit モード: バッチ投入 → state 保存 → exit ──
                phase3_info = _phase3_submit(docs, bucket, genai_client, logger)
                if phase3_info:
                    _save_backfill_state(
                        bucket, date_from, date_to, docs, phase3_info, logger,
                        ticker_from=ticker_from, ticker_to=ticker_to,
                    )
                logger.log("submit モード完了: バッチ投入済み。Cloud Functions で resume を待機。")
                logger.flush_to_gcs()
                return

            # ── full モード: 一気通貫 ──
            phase3_analysis_batch(docs, bucket, genai_client, logger)
            logger.flush_to_gcs()

            phase4_chunk_and_embed(docs, bucket, genai_client, logger, embed_client)
            logger.flush_to_gcs()

            processed, skipped = phase5_bq_insert(docs, bucket, logger)
            if processed > 0:
                _create_vector_index(logger)

    except Exception as e:
        logger.log(f"致命的なエラーで処理が中断: {e}")
        logger.log(traceback.format_exc())
        errors = 1
        raise  # main() で exit 1 させるため再送出（B-1）

    finally:
        logger.log("=== TDnet Batch ETL 処理結果サマリー ===")
        logger.log(f"対象期間              : {date_from} ～ {date_to}")
        logger.log(f"run_mode              : {run_mode}")
        logger.log(f"総ドキュメント数       : {len(docs)} 件")
        logger.log(f"成功/スキップ/エラー  : {processed} / {skipped} / {errors}")
        logger.flush_to_gcs()
        print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")


# ============================================================
# 日付解決 / 引数パース
# ============================================================

def _resolve_dates() -> tuple[str, str]:
    """DATE_MODE に基づいて日付範囲を返す.

    t: JST 今日（タイムゾーン非依存）
    y: JST 昨日（タイムゾーン非依存）— Cloud Run 日次バッチのデフォルト
    1: DATE_SINGLE 固定日
    r: DATE_FROM 〜 DATE_TO 範囲
    """
    today_jst = datetime.now(JST).date()
    if DATE_MODE == "t":
        d = today_jst.strftime("%Y%m%d")
        return d, d
    elif DATE_MODE == "y":
        yesterday = (today_jst - timedelta(days=1)).strftime("%Y%m%d")
        return yesterday, yesterday
    elif DATE_MODE == "1":
        return DATE_SINGLE, DATE_SINGLE
    elif DATE_MODE == "r":
        return DATE_FROM, DATE_TO
    else:
        raise ValueError(f"DATE_MODE が不正: {DATE_MODE!r}")


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする."""
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(
            mode=None, job_mode=None, date_from=None, date_to=None,
            ticker_from=None, ticker_to=None,
        )
    parser = argparse.ArgumentParser(description="TDnet 適時開示 PDF → BigQuery Batch ETL")
    parser.add_argument("--mode", default=None,
                        help="日付モード: t=今日, y=昨日, 1=DATE_SINGLE, r=DATE_FROM〜DATE_TO")
    parser.add_argument("--job-mode", dest="job_mode", default=None,
                        choices=["full", "load", "ai-prepare", "ai-finalize"],
                        help="ジョブモード: full=従来互換 / load=BQロードのみ / ai-prepare=未実装 / ai-finalize=未実装")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD（--mode r と併用 or 単独指定）")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    parser.add_argument("--ticker-from", dest="ticker_from", default=None)
    parser.add_argument("--ticker-to",   dest="ticker_to",   default=None)
    return parser.parse_args()


def main() -> None:
    """エントリーポイント."""
    global DATE_MODE, DATE_FROM, DATE_TO
    args = parse_args()

    # 環境変数フォールバック（gcloud --update-env-vars 対応）
    env_from = os.environ.get("DATE_FROM")
    env_to   = os.environ.get("DATE_TO")
    env_mode = os.environ.get("DATE_MODE")

    if args.date_from:
        date_from = args.date_from
        date_to   = args.date_to or args.date_from
    elif env_from:
        date_from = env_from
        date_to   = env_to or env_from
    elif args.mode or env_mode:
        DATE_MODE = args.mode or env_mode
        date_from, date_to = _resolve_dates()
    else:
        date_from, date_to = _resolve_dates()

    # run_mode: full(default) / submit / resume  — 従来互換（full 用）
    run_mode = os.environ.get("RUN_MODE", "full")

    # job_mode: full(default) / load / ai-prepare / ai-finalize  — 新アーキ分離
    job_mode = args.job_mode or os.environ.get("JOB_MODE") or "full"

    # ticker 環境変数フォールバック
    ticker_from = args.ticker_from or os.environ.get("TICKER_FROM") or None
    ticker_to   = args.ticker_to   or os.environ.get("TICKER_TO")   or None

    print("=== TDnet Batch ETL ===")
    print(f"実行環境    : {RUNTIME}")
    print(f"期間        : {date_from} ～ {date_to}")
    print(f"job_mode    : {job_mode}")
    print(f"run_mode    : {run_mode}")
    if ticker_from or ticker_to:
        print(f"ticker範囲  : {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    print(f"保存先      : {TABLE_ID}")
    print()

    try:
        run_tdnet_batch_etl(
            date_from, date_to,
            ticker_from=ticker_from, ticker_to=ticker_to,
            run_mode=run_mode,
            job_mode=job_mode,
        )
    except Exception as e:
        print(f"[FATAL] run_tdnet_batch_etl エラー終了: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
