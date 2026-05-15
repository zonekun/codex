"""
EDINET GCS HTML → テキスト抽出 → Gemini Batch Prediction → Batch Embedding → BigQuery ETL

Vertex AI Batch Prediction を利用して Embedding をバッチ処理。
オンライン予測比 約50%コスト削減 + RPM制限なし。

処理フロー:
  Phase 1: GCS blob 走査 → HTML テキスト抽出 → メタデータ抽出
  Phase 2: Gemini Vision Batch（テキスト抽出失敗HTML の OCR）— EDINET では通常不要
  Phase 3: チャンク化 → Batch Embedding
  Phase 4: BQ Insert

Usage:
    PYTHONUTF8=1 python scripts/edinet_load_parallel.py
    PYTHONUTF8=1 python scripts/edinet_load_parallel.py --from 20240101 --to 20241231
    PYTHONUTF8=1 python scripts/edinet_load_parallel.py --from 20230101 --to 20231231 --ticker-from 1301 --ticker-to 3727
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
from bs4 import BeautifulSoup
from google import genai
from google.cloud import bigquery, storage
from google.cloud.bigquery import LoadJobConfig, SourceFormat, WriteDisposition
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.getLogger("urllib3").setLevel(logging.WARNING)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  ★ 実行設定                                                        ║
# ╚══════════════════════════════════════════════════════════════════╝

DATE_MODE   = "y"
DATE_SINGLE = "20260301"
DATE_FROM   = "20260301"
DATE_TO     = "20260331"

BATCH_POLL_INTERVAL = 60  # バッチジョブ完了ポーリング間隔（秒）

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
GCS_PREFIX         = "edinet"
GCS_BATCH_PREFIX   = "batch_prediction/edinet"
TABLE_ID           = f"{PROJECT_ID}.STOCK.IR_DOCUMENTS_ENHANCED"
BQ_BATCH_SIZE      = 100
BACKFILL_MEM_BATCH = 50   # backfill モードのメモリバッチサイズ（docs 単位）
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
    """google-genai クライアントを取得する."""
    creds = _get_credentials()
    kwargs: dict = {
        "project": PROJECT_ID,
        "location": LOCATION_GEMINI,
        "vertexai": True,
    }
    if creds:
        kwargs["credentials"] = creds
    return genai.Client(**kwargs)


def _load_processed_file_names(
    date_from: str, date_to: str,
    ticker_from: str | None = None, ticker_to: str | None = None,
) -> set[str]:
    """BQ から取込済みファイル名を取得する."""
    d_from_iso = f"{date_from[:4]}-{date_from[4:6]}-{date_from[6:8]}"
    d_to_iso   = f"{date_to[:4]}-{date_to[4:6]}-{date_to[6:8]}"

    where_clauses = ["SUBMISSION_DATE BETWEEN @d_from AND @d_to"]
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("d_from", "DATE", d_from_iso),
        bigquery.ScalarQueryParameter("d_to", "DATE", d_to_iso),
    ]
    if ticker_from:
        where_clauses.append("SECURITY_CODE >= @ticker_from")
        params.append(bigquery.ScalarQueryParameter("ticker_from", "STRING", ticker_from))
    if ticker_to:
        where_clauses.append("SECURITY_CODE <= @ticker_to")
        params.append(bigquery.ScalarQueryParameter("ticker_to", "STRING", ticker_to))

    query = f"""
    SELECT DISTINCT FILE_NAME
    FROM `{TABLE_ID}`
    WHERE {' AND '.join(where_clauses)}
    """
    try:
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = _get_bq_client().query(query, job_config=job_config).result()
        return {row.FILE_NAME for row in rows}
    except Exception as e:
        print(f"警告: BQ 取込済みファイル一覧の取得に失敗: {e} → 重複チェックなしで続行")
        return set()


# ============================================================
# DocInfo データクラス
# ============================================================

@dataclass
class DocInfo:
    """1つの HTML ドキュメントの処理状態を保持する."""

    blob_name: str
    sub_date: str
    sec_code: str
    filer_name: str
    filer_id: str
    doc_type: str
    doc_id: str
    text: str = ""
    # チャンク・埋め込み
    chunks: list[dict] = field(default_factory=list)
    embeddings: np.ndarray | None = field(default=None)
    embedding_set: list[bool] = field(default_factory=list)


# ============================================================
# State シリアライズ/デシリアライズ（submit/resume モード用）
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
            "filer_id": d.filer_id,
            "doc_type": d.doc_type,
            "doc_id": d.doc_id,
            "text": d.text,
            "chunks": d.chunks,
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
            filer_id=item["filer_id"],
            doc_type=item["doc_type"],
            doc_id=item["doc_id"],
            text=item.get("text", ""),
            chunks=item.get("chunks", []),
        )
        docs.append(doc)
    return docs


def _save_backfill_state(
    bucket, date_from: str, date_to: str,
    docs: list[DocInfo], embedding_info: dict,
    logger: "BatchLogger",
) -> str:
    """submit モードの状態を GCS に NDJSON stream write で保存する."""
    state_id = f"{date_from}_{date_to}"
    state_path = f"{BACKFILL_STATE_PREFIX}_{state_id}.json"
    docs_path  = f"{BACKFILL_STATE_PREFIX}_{state_id}_docs.jsonl"

    # docs を NDJSON stream write（一括JSON+gzip → OOM 回避）
    with bucket.blob(docs_path).open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(_serialize_docs([d])[0], ensure_ascii=False) + "\n")
    logger.log(f"  docs 保存完了: gs://{BUCKET_NAME}/{docs_path} ({len(docs)} 件)")

    # state メタデータ保存
    state = {
        "date_from": date_from,
        "date_to": date_to,
        "docs_path": docs_path,
        "embedding": embedding_info,
        "created_at": datetime.now(JST).isoformat(),
    }
    state_json = json.dumps(state, ensure_ascii=False, indent=2)
    bucket.blob(state_path).upload_from_string(
        state_json.encode("utf-8"), content_type="application/json"
    )
    logger.log(f"  state 保存完了: gs://{BUCKET_NAME}/{state_path}")
    return state_path


def _load_backfill_state(
    bucket, date_from: str, date_to: str, logger: "BatchLogger",
) -> tuple[list[DocInfo], dict]:
    """resume モードの状態を GCS から読み込む."""
    state_id = f"{date_from}_{date_to}"
    state_path = f"{BACKFILL_STATE_PREFIX}_{state_id}.json"

    logger.log(f"  state 読込: gs://{BUCKET_NAME}/{state_path}")
    state_json = bucket.blob(state_path).download_as_text()
    state = json.loads(state_json)

    docs_path = state["docs_path"]
    logger.log(f"  docs 読込: gs://{BUCKET_NAME}/{docs_path}")

    # NDJSON stream read（後方互換: .json.gz なら旧形式で読む）
    if docs_path.endswith(".json.gz"):
        import gzip as _gzip
        compressed = bucket.blob(docs_path).download_as_bytes()
        docs_json = _gzip.decompress(compressed).decode("utf-8")
        docs = _deserialize_docs(json.loads(docs_json))
    else:
        docs: list[DocInfo] = []
        with bucket.blob(docs_path).open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    docs.append(_deserialize_docs([json.loads(line)])[0])

    logger.log(f"  復元完了: {len(docs)} 件")
    return docs, state.get("embedding", {})


# ============================================================
# ユーティリティ関数
# ============================================================

def _format_to_bq_date(jp_date_str: str | None) -> str:
    """和暦/西暦の日付文字列を ISO 形式に変換する."""
    if not jp_date_str:
        return datetime.now(JST).date().isoformat()
    trans_map = str.maketrans("０１２３４５６７８９", "0123456789")
    n_str = jp_date_str.translate(trans_map)
    reiwa_match = re.search(r"令和(\d+|元)年(\d{1,2})月(\d{1,2})日", n_str)
    if reiwa_match:
        y_str, m, d = reiwa_match.groups()
        year = 2018 + (1 if y_str == "元" else int(y_str))
        return f"{year}-{int(m):02d}-{int(d):02d}"
    heisei_match = re.search(r"平成(\d+|元)年(\d{1,2})月(\d{1,2})日", n_str)
    if heisei_match:
        y_str, m, d = heisei_match.groups()
        year = 1988 + (1 if y_str == "元" else int(y_str))
        return f"{year}-{int(m):02d}-{int(d):02d}"
    ad_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", n_str)
    if ad_match:
        y, m, d = ad_match.groups()
        return f"{y}-{int(m):02d}-{int(d):02d}"
    return datetime.now(JST).date().isoformat()


def _extract_date_from_blob_name(blob_name: str) -> str | None:
    """blob名から YYYYMMDD 日付を抽出する."""
    fname = blob_name.split("/")[-1]
    for part in fname.split("_"):
        if re.match(r"^\d{8}$", part):
            return part
    return None


def _extract_metadata_and_text(
    html_content: str, blob_name: str,
) -> tuple[str, str, str, str, str, str]:
    """HTML から メタデータとテキストを抽出する.

    Returns:
        (security_code, filer_name, filer_id, submission_date, doc_type, text)
    """
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    security_code = "UNKNOWN"
    for part in blob_name.split("/"):
        if part.isdigit() and len(part) == 4:
            security_code = part
            break

    date_match = re.search(r"【提出日】\s*([^\n]{5,20})", text)
    raw_date = date_match.group(1).strip() if date_match else None
    submission_date = _format_to_bq_date(raw_date)

    name_match = re.search(
        r"【(?:会社名|ファンド名|発行者名|提出者|届出者|氏名又は名称|公開買付者)】\s*([^\n]+)", text
    )
    filer_name = name_match.group(1).strip() if name_match else "UNKNOWN"

    edinet_exact_match = re.search(r"【[EＥ][DＤ][IＩ][NＮ][EＥ][TＴ]コード】\s*(E\d{5})", text)
    edinet_fallback_match = re.search(r"(E\d{5})", text)
    if edinet_exact_match:
        filer_id = edinet_exact_match.group(1)
    elif edinet_fallback_match:
        filer_id = edinet_fallback_match.group(1)
    else:
        filer_id = "UNKNOWN"

    if "公開買付" in blob_name:
        doc_type = "公開買付関連"
    elif "四半期" in blob_name or "半期" in blob_name:
        doc_type = "四半期・半期報告書"
    else:
        doc_type = "有価証券報告書"

    return security_code, filer_name, filer_id, submission_date, doc_type, text


def _create_chunks_with_headers(text: str) -> list[dict]:
    """テキストをチャンク分割する（セクションヘッダ付き）."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400, chunk_overlap=50,
        separators=["\n\n", "\n", "。", "、", " "],
    )
    raw_chunks = splitter.split_text(text)
    enhanced_chunks: list[dict] = []
    current_section = "一般"
    for chunk in raw_chunks:
        section_match = re.search(r"【([^】]+)】", chunk)
        if section_match:
            current_section = section_match.group(1).strip()
        enhanced_text = f"章名: {current_section}\n{chunk}"
        enhanced_chunks.append({
            "section_category": current_section,
            "chunk_text": enhanced_text,
        })
    return enhanced_chunks


# ============================================================
# Embedding 対象フィルタ
# ============================================================

# 四半期・半期報告書は Embedding 不要（メタデータのみ）
_EMBED_DOC_TYPES: set[str] = {"有価証券報告書", "公開買付関連"}

# 有価証券報告書のうち Embedding 除外するセクション
_SKIP_SECTIONS: set[str] = {
    "注記事項", "事務連絡者氏名", "役員の状況", "新株予約権等の状況",
    "連結財務諸表注記", "縦覧に供する場所", "企業情報", "電話番号",
    "監査の状況", "沿革", "連結キャッシュ・フロー計算書",
    "株主資本等変動計算書", "財務諸表等", "連結財務諸表等",
    "連結株主資本等変動計算書",
    "サステナビリティに関する考え方及び取組",
}

# チャンク数を削減するセクション（キーワード部分一致 → 削減率）
_REDUCE_SECTIONS: dict[str, float] = {
    "コーポレート・ガバナンス": 0.5,   # 半減
    "事業等のリスク": 0.25,            # 1/4
}


def _filter_chunks_for_embed(
    chunks: list[dict], doc_type: str,
) -> list[dict]:
    """Embedding 対象のチャンクのみ返す."""
    if doc_type not in _EMBED_DOC_TYPES:
        return []

    result: list[dict] = []
    # セクション別にグループ化
    section_chunks: dict[str, list[dict]] = {}
    for chunk in chunks:
        sec = chunk["section_category"]
        section_chunks.setdefault(sec, []).append(chunk)

    for sec, sec_chunks in section_chunks.items():
        # 除外セクション
        if sec in _SKIP_SECTIONS:
            continue

        # チャンク数削減セクション
        reduce_ratio = None
        for keyword, ratio in _REDUCE_SECTIONS.items():
            if keyword in sec:
                reduce_ratio = ratio
                break

        if reduce_ratio is not None:
            limit = max(1, int(len(sec_chunks) * reduce_ratio))
            result.extend(sec_chunks[:limit])
        else:
            result.extend(sec_chunks)

    return result


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
# バッチジョブ共通: JSONL アップロード & ポーリング
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
    max_wait_sec: int = 7200,
) -> bool:
    """バッチジョブの完了をポーリングする. 成功なら True."""
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
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

    logger.log(f"  [{label}] タイムアウト ({max_wait_sec}s)")
    return False


# ============================================================
# Phase 1: スキャン & テキスト抽出
# ============================================================

def _phase1_iter(
    bucket,
    date_from: str,
    date_to: str,
    ticker_from: str | None,
    ticker_to: str | None,
    processed_files: set[str],
    logger: BatchLogger,
) -> "Iterator[DocInfo]":
    """GCS blob を走査し、テキスト抽出済み DocInfo を1件ずつ yield する."""
    from collections.abc import Iterator  # noqa: F811
    d_from = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to   = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))

    logger.log("Phase 1: GCS blob スキャン & テキスト抽出 開始")
    scan_prefix = f"{GCS_PREFIX}/"
    list_kwargs: dict = {"prefix": scan_prefix}
    if ticker_from:
        list_kwargs["start_offset"] = f"{GCS_PREFIX}/{ticker_from}/"
        logger.log(f"  ticker範囲: {ticker_from} ～ {ticker_to or '末尾'}")
    if ticker_to:
        list_kwargs["end_offset"] = f"{GCS_PREFIX}/{ticker_to}0/"
    blob_iter = bucket.list_blobs(**list_kwargs)

    for blob in blob_iter:
        if not blob.name.lower().endswith((".htm", ".html")):
            continue

        if "大量保有" in blob.name:
            continue

        parts = blob.name.split("/")
        code = parts[1] if len(parts) >= 2 else ""
        if ticker_from and code < ticker_from:
            continue
        if ticker_to and code > ticker_to:
            continue

        blob_date_str = _extract_date_from_blob_name(blob.name)
        if blob_date_str:
            try:
                d_blob = date(
                    int(blob_date_str[:4]),
                    int(blob_date_str[4:6]),
                    int(blob_date_str[6:8]),
                )
                if not (d_from <= d_blob <= d_to):
                    continue
            except ValueError:
                continue
        else:
            continue

        if blob.name in processed_files:
            continue

        try:
            html_content = blob.download_as_text()
        except Exception as e:
            logger.log(f"  GCS ダウンロード失敗 → スキップ: {blob.name} - {e}")
            continue

        if "大量保有報告書" in html_content[:500]:
            continue

        try:
            sec_code, filer_name, filer_id, sub_date, doc_type, text = \
                _extract_metadata_and_text(html_content, blob.name)
        except Exception as e:
            logger.log(f"  メタデータ抽出失敗 → スキップ: {blob.name} - {e}")
            continue

        if not text or len(text) < 50:
            continue

        yield DocInfo(
            blob_name=blob.name,
            sub_date=sub_date,
            sec_code=sec_code,
            filer_name=filer_name,
            filer_id=filer_id,
            doc_type=doc_type,
            doc_id=str(uuid.uuid4()),
            text=text,
        )


def phase1_scan_and_extract(
    bucket,
    date_from: str,
    date_to: str,
    ticker_from: str | None,
    ticker_to: str | None,
    processed_files: set[str],
    logger: BatchLogger,
) -> list[DocInfo]:
    """GCS blob を走査し、テキスト抽出を行い DocInfo リストを返す（full/submit 用）."""
    docs = list(_phase1_iter(
        bucket, date_from, date_to, ticker_from, ticker_to, processed_files, logger,
    ))
    logger.log(f"Phase 1 完了: 対象 {len(docs)} 件")
    return docs


# ============================================================
# Phase 2: チャンク化 → Batch Embedding
# ============================================================

def _phase2_chunk(docs: list[DocInfo], logger: BatchLogger) -> int:
    """全 docs をチャンク化する。Embedding 対象チャンク数を返す."""
    valid_docs = [d for d in docs if d.text]
    embed_chunk_count = 0
    meta_only_count = 0
    for doc in valid_docs:
        all_chunks = _create_chunks_with_headers(doc.text)
        embed_chunks = _filter_chunks_for_embed(all_chunks, doc.doc_type)
        if embed_chunks:
            doc.chunks = embed_chunks
            embed_chunk_count += len(embed_chunks)
        else:
            doc.chunks = []
            meta_only_count += 1
        doc.text = ""  # メモリ解放（HTML全文はチャンク化後は不要）

    logger.log(f"  Embedding 対象: {len(valid_docs) - meta_only_count} 件 ({embed_chunk_count} チャンク)")
    logger.log(f"  メタデータのみ: {meta_only_count} 件")
    return embed_chunk_count


def phase2_chunk_and_submit(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
) -> dict | None:
    """チャンク化 → Embedding JSONL → バッチ投入。ポーリングせずにジョブ情報を返す."""
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 2 (submit): 対象なし（スキップ）")
        return None

    logger.log(f"Phase 2 (submit): チャンク化 & Embedding 投入開始 ({len(valid_docs)} 件)")

    embed_chunk_count = _phase2_chunk(docs, logger)
    if embed_chunk_count == 0:
        logger.log("  Embedding チャンクなし → スキップ")
        return None

    # Embedding JSONL 作成
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    input_path  = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_input.jsonl"
    output_path = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_output/"

    seen_content: set[str] = set()
    lines: list[str] = []
    for doc in valid_docs:
        for chunk in doc.chunks:
            ct = chunk["chunk_text"]
            if ct not in seen_content:
                seen_content.add(ct)
                lines.append(json.dumps({"content": ct}, ensure_ascii=False))

    input_uri = _upload_jsonl_to_gcs(bucket, input_path, lines)
    logger.log(f"  Embedding JSONL アップロード完了: {input_uri} ({len(lines)} 件)")

    job = client.batches.create(
        model="text-embedding-004",
        src=input_uri,
        config=genai.types.CreateBatchJobConfig(
            dest=f"gs://{BUCKET_NAME}/{output_path}",
        ),
    )
    logger.log(f"  Embedding バッチジョブ投入: {job.name}")
    logger.log("Phase 2 (submit) 完了: ポーリングなしで終了")

    return {
        "job": job.name,
        "input": input_path,
        "output": output_path,
    }


def phase2_poll_and_apply(
    docs: list[DocInfo], bucket, client: genai.Client,
    logger: BatchLogger, embedding_info: dict,
) -> None:
    """Embedding バッチジョブの完了を待ち、結果を docs に適用する."""
    job_name = embedding_info.get("job")
    output_path = embedding_info.get("output", "")

    if not job_name:
        logger.log("Phase 2 (resume): Embedding ジョブ情報なし → スキップ")
        return

    logger.log(f"Phase 2 (resume): Embedding 結果取得開始 (job={job_name})")

    success = _poll_batch_job(client, job_name, logger, "Embedding")
    if not success:
        logger.log("  Embedding バッチジョブ失敗 → 埋め込みなしで続行")
        return

    # content_to_chunks マッピングを docs の chunks から再構築（1:N 対応）
    content_to_chunks: dict[str, list[tuple[DocInfo, int]]] = defaultdict(list)
    embed_chunk_count = 0
    for doc in docs:
        if doc.chunks:
            doc.embeddings = np.zeros((len(doc.chunks), 768), dtype=np.float32)
            doc.embedding_set = [False] * len(doc.chunks)
            for ci, chunk in enumerate(doc.chunks):
                content_to_chunks[chunk["chunk_text"]].append((doc, ci))
                embed_chunk_count += 1

    # 結果取得（ストリーミング）
    embed_ok = 0
    embed_errors = 0
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
                    mappings = content_to_chunks.get(chunk_text, [])
                    emb_arr = np.asarray(embedding, dtype=np.float32)
                    for doc, ci in mappings:
                        doc.embeddings[ci, :] = emb_arr
                        doc.embedding_set[ci] = True
                        embed_ok += 1
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    logger.log(f"  Embedding parse失敗: {e}")
                    embed_errors += 1

    logger.log(f"  Embedding 適用完了: {embed_ok}/{embed_chunk_count} 件 (parse失敗: {embed_errors})")
    logger.log("Phase 2 (resume) 完了")


def phase2_chunk_and_embed(
    docs: list[DocInfo], bucket, client: genai.Client, logger: BatchLogger,
) -> None:
    """対象カテゴリ・セクションのみチャンク化 + Embedding（full モード用一気通貫）."""
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 2: 対象なし（スキップ）")
        return

    logger.log(f"Phase 2: チャンク化 & Embedding 開始 ({len(valid_docs)} 件)")

    embed_chunk_count = _phase2_chunk(docs, logger)
    if embed_chunk_count == 0:
        logger.log("  Embedding チャンクなし → スキップ")
        return

    # Embedding JSONL 作成
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    input_path  = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_input.jsonl"
    output_path = f"{GCS_BATCH_PREFIX}/embed_{timestamp}_output/"

    content_to_chunks: dict[str, list[tuple[DocInfo, int]]] = defaultdict(list)
    seen_content: set[str] = set()
    lines: list[str] = []

    for doc in valid_docs:
        for ci, chunk in enumerate(doc.chunks):
            chunk_text = chunk["chunk_text"]
            content_to_chunks[chunk_text].append((doc, ci))
            if chunk_text not in seen_content:
                seen_content.add(chunk_text)
                lines.append(json.dumps({"content": chunk_text}, ensure_ascii=False))

    input_uri = _upload_jsonl_to_gcs(bucket, input_path, lines)
    logger.log(f"  Embedding JSONL アップロード完了: {input_uri} ({len(lines)} 件)")

    job = client.batches.create(
        model="text-embedding-004",
        src=input_uri,
        config=genai.types.CreateBatchJobConfig(
            dest=f"gs://{BUCKET_NAME}/{output_path}",
        ),
    )
    logger.log(f"  Embedding バッチジョブ投入: {job.name}")

    success = _poll_batch_job(client, job.name, logger, "Embedding")
    if not success:
        logger.log("  Embedding バッチジョブ失敗 → 埋め込みなしで続行")
        return

    # 結果取得（ストリーミング）: numpy float32 で保持してメモリ節約
    for doc in valid_docs:
        if doc.chunks:
            doc.embeddings = np.zeros((len(doc.chunks), 768), dtype=np.float32)
            doc.embedding_set = [False] * len(doc.chunks)

    embed_ok = 0
    embed_errors = 0
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
                    mappings = content_to_chunks.get(chunk_text, [])
                    emb_arr = np.asarray(embedding, dtype=np.float32)
                    for doc, ci in mappings:
                        doc.embeddings[ci, :] = emb_arr
                        doc.embedding_set[ci] = True
                        embed_ok += 1
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    logger.log(f"  Embedding parse失敗: {e}")
                    embed_errors += 1

    logger.log(f"  Embedding 適用完了: {embed_ok}/{embed_chunk_count} 件 (parse失敗: {embed_errors})")
    logger.log("Phase 2 完了")


# ============================================================
# Phase 3: BQ Insert
# ============================================================

def phase3_bq_insert(
    docs: list[DocInfo], bucket, logger: BatchLogger,
) -> tuple[int, int, int]:
    """全ドキュメントを GCS 経由 BQ Load Job でインサートする.

    streaming insert → Load Job 移行（004 C-5）。
    冪等性: Load Job 前に対象 FILE_NAME の既存行を DELETE してから APPEND。
    Returns: (processed, skipped, errors)
    """
    import tempfile
    from pathlib import Path

    valid_docs = [d for d in docs if d.text or d.chunks]
    if not valid_docs:
        logger.log("Phase 3: BQ Insert 対象なし（スキップ）")
        return 0, len(docs), 0

    logger.log(f"Phase 3: BQ Load Job 開始 ({len(valid_docs)} ドキュメント)")

    bq = _get_bq_client()
    row_count = 0

    # NDJSON を tempfile に stream write → GCS upload → load_table_from_uri
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".jsonl", delete=False,
    )
    tmp_path = tmp.name
    try:
        with tmp:
            for doc in valid_docs:
                if doc.chunks:
                    for ci, chunk_data in enumerate(doc.chunks):
                        embedding = None
                        if doc.embeddings is not None and ci < doc.embeddings.shape[0]:
                            if doc.embedding_set and doc.embedding_set[ci]:
                                embedding = doc.embeddings[ci].tolist()
                        row: dict = {
                            "DOC_ID":            doc.doc_id,
                            "SECURITY_CODE":     doc.sec_code,
                            "FILER_NAME":        doc.filer_name,
                            "FILER_ID":          doc.filer_id,
                            "SUBMISSION_DATE":   doc.sub_date,
                            "DOC_TYPE":          doc.doc_type,
                            "SECTION_CATEGORY":  chunk_data["section_category"],
                            "CHUNK_TEXT":        chunk_data["chunk_text"],
                            "FILE_NAME":         doc.blob_name,
                        }
                        if embedding is not None:
                            row["EMBEDDING"] = embedding
                        tmp.write(json.dumps(row, ensure_ascii=False) + "\n")
                        row_count += 1
                else:
                    tmp.write(json.dumps({
                        "DOC_ID":            doc.doc_id,
                        "SECURITY_CODE":     doc.sec_code,
                        "FILER_NAME":        doc.filer_name,
                        "FILER_ID":          doc.filer_id,
                        "SUBMISSION_DATE":   doc.sub_date,
                        "DOC_TYPE":          doc.doc_type,
                        "SECTION_CATEGORY":  None,
                        "CHUNK_TEXT":        None,
                        "FILE_NAME":         doc.blob_name,
                    }, ensure_ascii=False) + "\n")
                    row_count += 1

        # 冪等性: 対象 FILE_NAME の既存行を DELETE（再実行時の重複防止）
        file_names = list({d.blob_name for d in valid_docs})
        _delete_existing_rows(bq, file_names, logger)

        # GCS upload → Load Job（本テーブルに直接 APPEND）
        now_ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        gcs_upload_path = f"{GCS_BATCH_PREFIX}/load_upload_{now_ts}.jsonl"
        _blob = bucket.blob(gcs_upload_path)
        _blob.upload_from_filename(tmp_path)
        gcs_uri = f"gs://{BUCKET_NAME}/{gcs_upload_path}"
        logger.log(f"  NDJSON を GCS へ upload: {gcs_uri} ({row_count} 行)")

        job_config = LoadJobConfig(
            source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=WriteDisposition.WRITE_APPEND,
        )
        load_job = bq.load_table_from_uri(gcs_uri, TABLE_ID, job_config=job_config)
        load_job.result()

        # GCS 一時ファイル削除
        try:
            _blob.delete()
        except Exception:
            pass

        processed = len(valid_docs)
        errors = 0
        if load_job.errors:
            logger.log(f"  Load Job エラー: {load_job.errors}")
            errors = len(load_job.errors)

    except Exception as e:
        logger.log(f"  BQ Load Job 失敗: {e}")
        logger.log(traceback.format_exc())
        processed = 0
        errors = len(valid_docs)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    skipped = len(docs) - len(valid_docs)
    logger.log(f"Phase 3 完了: 成功 {processed}, スキップ {skipped}, エラー {errors}")
    return processed, skipped, errors


def _delete_existing_rows(
    bq: bigquery.Client, file_names: list[str], logger: BatchLogger,
) -> None:
    """Load Job 冪等性のため、対象 FILE_NAME の既存行を DELETE する."""
    if not file_names:
        return
    params = [bigquery.ArrayQueryParameter("fnames", "STRING", file_names)]
    sql = f"DELETE FROM `{TABLE_ID}` WHERE FILE_NAME IN UNNEST(@fnames)"
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    result = bq.query(sql, job_config=job_config).result()
    deleted = result.num_dml_affected_rows or 0
    if deleted > 0:
        logger.log(f"  冪等性DELETE: 既存 {deleted} 行を削除（再挿入予定）")


# ============================================================
# ベクトルインデックス作成
# ============================================================

def _create_vector_index(logger: BatchLogger) -> None:
    """BQ ベクトルインデックスを作成/更新する."""
    logger.log("ベクトルインデックスの作成/更新を確認しています...")
    sql = f"""
    CREATE VECTOR INDEX IF NOT EXISTS edinet_doc_vector_index
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

def run_edinet_batch_etl(
    date_from: str, date_to: str,
    ticker_from: str | None = None, ticker_to: str | None = None,
    run_mode: str = "full",
) -> int:
    """EDINET Batch ETL メイン処理.

    run_mode:
      full      — Phase 1→2→3 一気通貫（日次ロード用、Embedding あり）
      backfill  — Phase 1→チャンク化→3（Embedding スキップ、後日追加可）
      submit    — Phase 1→2(Embedding投入のみ) → state保存 → exit（旧バックフィル用）
      resume    — state読込 → Phase 2(Embedding結果適用)→3(BQ Insert)（旧バックフィル用）

    Returns: エラー件数（0 = 正常終了）
    """
    storage_client = _get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)
    genai_client = None  # 遅延初期化（backfill モードでは不要）

    start_time_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    log_blob_name  = f"log/edinet_load_to_bq_{start_time_str}_{run_mode}_log.txt"
    logger = BatchLogger(bucket, log_blob_name)

    logger.log(f"EDINET Batch ETL 開始: {date_from} ～ {date_to} [mode={run_mode}]")
    if ticker_from or ticker_to:
        logger.log(f"TICKER範囲: {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    logger.log(f"実行環境: {RUNTIME}")

    docs: list[DocInfo] = []
    processed = skipped = errors = total_docs = 0

    try:
        if run_mode == "resume":
            # ── resume モード: GCS から state を読み込み Embedding 結果適用から再開 ──
            genai_client = _get_genai_client()
            logger.log("resume モード: state を GCS から読み込み中...")
            docs, embedding_info = _load_backfill_state(bucket, date_from, date_to, logger)
            logger.flush_to_gcs()

            phase2_poll_and_apply(docs, bucket, genai_client, logger, embedding_info)
            logger.flush_to_gcs()

            processed, skipped, errors = phase3_bq_insert(docs, bucket, logger)
            if processed > 0:
                _create_vector_index(logger)

            # state / docs / ロックをクリーンアップ
            state_id = f"{date_from}_{date_to}"
            for suffix in [".json", "_docs.json.gz", "_docs.jsonl", ".json.resume_triggered"]:
                path = f"{BACKFILL_STATE_PREFIX}_{state_id}{suffix}"
                try:
                    bucket.blob(path).delete()
                except Exception:
                    pass
            logger.log("state クリーンアップ完了")

        else:
            # ── full / submit / backfill: Phase 1 から開始 ──
            logger.log("BQ 取込済みファイル一覧を取得中...")
            processed_files = _load_processed_file_names(
                date_from, date_to, ticker_from, ticker_to,
            )
            logger.log(f"取込済みファイル数: {len(processed_files)} 件")

            if run_mode == "backfill":
                # ── backfill モード: ストリーミングバッチ処理（メモリ一定） ──
                logger.log(f"backfill モード: Embedding スキップ, バッチサイズ={BACKFILL_MEM_BATCH}")
                batch: list[DocInfo] = []
                batch_num = 0
                total_docs = 0
                for doc in _phase1_iter(
                    bucket, date_from, date_to, ticker_from, ticker_to,
                    processed_files, logger,
                ):
                    batch.append(doc)
                    if len(batch) >= BACKFILL_MEM_BATCH:
                        batch_num += 1
                        total_docs += len(batch)
                        logger.log(f"  バッチ {batch_num}: {len(batch)} 件処理中 (累計 {total_docs})")
                        _phase2_chunk(batch, logger)
                        p, s, e = phase3_bq_insert(batch, bucket, logger)
                        processed += p; errors += e
                        batch.clear()
                        logger.flush_to_gcs()
                if batch:
                    batch_num += 1
                    total_docs += len(batch)
                    logger.log(f"  バッチ {batch_num} (最終): {len(batch)} 件処理中 (累計 {total_docs})")
                    _phase2_chunk(batch, logger)
                    p, s, e = phase3_bq_insert(batch, bucket, logger)
                    processed += p; errors += e
                if total_docs == 0:
                    logger.log("処理対象ドキュメントなし → 終了")
                    return 0
                docs = []

            else:
                docs = phase1_scan_and_extract(
                    bucket, date_from, date_to, ticker_from, ticker_to,
                    processed_files, logger,
                )
                logger.flush_to_gcs()

                if not docs:
                    logger.log("処理対象ドキュメントなし → 終了")
                    return 0

                if run_mode == "submit":
                    genai_client = _get_genai_client()
                    embedding_info = phase2_chunk_and_submit(
                        docs, bucket, genai_client, logger,
                    )
                    if embedding_info:
                        _save_backfill_state(
                            bucket, date_from, date_to, docs, embedding_info, logger,
                        )
                    logger.log("submit モード完了: バッチ投入済み。Cloud Functions で resume を待機。")
                    logger.flush_to_gcs()
                    return 0

                else:
                    # ── full モード: 一気通貫 ──
                    genai_client = _get_genai_client()
                    phase2_chunk_and_embed(docs, bucket, genai_client, logger)
                    logger.flush_to_gcs()
                    processed, skipped, errors = phase3_bq_insert(docs, bucket, logger)

            if processed > 0:
                _create_vector_index(logger)

    except Exception as e:
        logger.log(f"致命的なエラーで処理が中断: {e}")
        logger.log(traceback.format_exc())
        errors = 1

    finally:
        logger.log("=== EDINET Batch ETL 処理結果サマリー ===")
        logger.log(f"対象期間              : {date_from} ～ {date_to}")
        logger.log(f"run_mode              : {run_mode}")
        doc_count = total_docs if run_mode == "backfill" else len(docs)
        logger.log(f"総ドキュメント数       : {doc_count} 件")
        logger.log(f"正常処理              : {processed} 件")
        logger.log(f"スキップ              : {skipped} 件")
        logger.log(f"エラー                : {errors} 件")
        logger.flush_to_gcs()
        print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")

    return errors


# ============================================================
# 日付解決 / 引数パース
# ============================================================

def _resolve_dates(mode: str = DATE_MODE) -> tuple[str, str]:
    """日付モードに基づいて日付範囲を返す."""
    today_jst = datetime.now(JST).date()
    if mode == "t":
        d = today_jst.strftime("%Y%m%d")
        return d, d
    elif mode == "y":
        yesterday = (today_jst - timedelta(days=1)).strftime("%Y%m%d")
        return yesterday, yesterday
    elif mode == "1":
        return DATE_SINGLE, DATE_SINGLE
    elif mode == "r":
        return DATE_FROM, DATE_TO
    else:
        raise ValueError(f"DATE_MODE が不正: {mode!r}")


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする."""
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(
            mode=None, date_from=None, date_to=None,
            ticker_from=None, ticker_to=None,
        )
    parser = argparse.ArgumentParser(description="EDINET GCS HTML → BigQuery Batch ETL")
    parser.add_argument("--mode", default=None,
                        help="日付モード: t=今日, y=昨日, 1=DATE_SINGLE, r=DATE_FROM〜DATE_TO")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD（--mode r と併用 or 単独指定）")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD（省略時は --from と同日）")
    parser.add_argument("--ticker-from", dest="ticker_from", default=None)
    parser.add_argument("--ticker-to",   dest="ticker_to",   default=None)
    return parser.parse_args()


def main() -> None:
    """エントリーポイント."""
    args = parse_args()

    # 環境変数フォールバック（gcloud --update-env-vars 対応）
    # resume モードでは環境変数を優先（前回 submit の --args が残るため）
    env_from = os.environ.get("DATE_FROM")
    env_to   = os.environ.get("DATE_TO")
    env_mode = os.environ.get("DATE_MODE")

    if env_from:
        date_from = env_from
        date_to   = env_to or env_from
    elif args.date_from:
        date_from = args.date_from
        date_to   = args.date_to or args.date_from
    else:
        mode = args.mode or env_mode or DATE_MODE
        date_from, date_to = _resolve_dates(mode)

    # run_mode: full(default) / submit / resume
    run_mode = os.environ.get("RUN_MODE", "full")

    # ticker 環境変数フォールバック
    ticker_from = args.ticker_from or os.environ.get("TICKER_FROM") or None
    ticker_to   = args.ticker_to   or os.environ.get("TICKER_TO")   or None

    print("=== EDINET Batch ETL ===")
    print(f"実行環境    : {RUNTIME}")
    print(f"期間        : {date_from} ～ {date_to}")
    print(f"run_mode    : {run_mode}")
    if ticker_from or ticker_to:
        print(f"ticker範囲  : {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    print(f"保存先      : {TABLE_ID}")
    print()

    errors = run_edinet_batch_etl(
        date_from, date_to,
        ticker_from=ticker_from, ticker_to=ticker_to,
        run_mode=run_mode,
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
