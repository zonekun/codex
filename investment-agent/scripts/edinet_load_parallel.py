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
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup
from google import genai
from google.cloud import bigquery, storage
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


def _load_processed_file_names(date_from: str, date_to: str) -> set[str]:
    """BQ から取込済みファイル名を取得する."""
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
    embeddings: list[list[float]] = field(default_factory=list)


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
    """submit モードの状態を GCS に保存する."""
    import gzip as _gzip

    state_id = f"{date_from}_{date_to}"
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
    import gzip as _gzip

    state_id = f"{date_from}_{date_to}"
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
) -> list[DocInfo]:
    """GCS blob を走査し、テキスト抽出を行い DocInfo リストを返す."""
    d_from = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to   = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))

    docs: list[DocInfo] = []
    skipped = 0

    logger.log("Phase 1: GCS blob スキャン & テキスト抽出 開始")
    blob_iter = bucket.list_blobs(prefix=f"{GCS_PREFIX}/")

    for blob in blob_iter:
        # HTML ファイルのみ対象
        if not blob.name.lower().endswith((".htm", ".html")):
            continue

        # ファイル名の大量保有除外
        if "大量保有" in blob.name:
            skipped += 1
            continue

        # Ticker フィルタ（edinet/{code}/... の code 部分で判定）
        parts = blob.name.split("/")
        code = parts[1] if len(parts) >= 2 else ""
        if ticker_from and code < ticker_from:
            continue
        if ticker_to and code > ticker_to:
            continue

        # 日付フィルタ（ファイル名から日付抽出）
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
                logger.log(f"  日付パース失敗 → スキップ: {blob.name}")
                skipped += 1
                continue

        # BQ 登録済みスキップ
        if blob.name in processed_files:
            skipped += 1
            continue

        # HTML ダウンロード & テキスト抽出
        try:
            html_content = blob.download_as_text()
        except Exception as e:
            logger.log(f"  GCS ダウンロード失敗 → スキップ: {blob.name} - {e}")
            skipped += 1
            continue

        # 本文に大量保有報告書が含まれる場合は除外
        if "大量保有報告書" in html_content[:500]:
            skipped += 1
            continue

        try:
            sec_code, filer_name, filer_id, sub_date, doc_type, text = \
                _extract_metadata_and_text(html_content, blob.name)
        except Exception as e:
            logger.log(f"  メタデータ抽出失敗 → スキップ: {blob.name} - {e}")
            skipped += 1
            continue

        if not text or len(text) < 50:
            skipped += 1
            continue

        doc = DocInfo(
            blob_name=blob.name,
            sub_date=sub_date,
            sec_code=sec_code,
            filer_name=filer_name,
            filer_id=filer_id,
            doc_type=doc_type,
            doc_id=str(uuid.uuid4()),
            text=text,
        )
        docs.append(doc)

    logger.log(f"Phase 1 完了: 対象 {len(docs)} 件, スキップ {skipped} 件")
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

    lines: list[str] = []
    for doc in valid_docs:
        for chunk in doc.chunks:
            lines.append(json.dumps({"content": chunk["chunk_text"]}, ensure_ascii=False))

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

    # content_to_chunk マッピングを docs の chunks から再構築
    content_to_chunk: dict[str, tuple[DocInfo, int]] = {}
    embed_chunk_count = 0
    for doc in docs:
        if doc.chunks:
            doc.embeddings = [None] * len(doc.chunks)  # type: ignore[list-item]
            for ci, chunk in enumerate(doc.chunks):
                content_to_chunk[chunk["chunk_text"]] = (doc, ci)
                embed_chunk_count += 1

    # 結果取得（ストリーミング）
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
                    mapping = content_to_chunk.get(chunk_text)
                    if mapping:
                        doc, ci = mapping
                        doc.embeddings[ci] = embedding
                        embed_ok += 1
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue

    logger.log(f"  Embedding 適用完了: {embed_ok}/{embed_chunk_count} 件")
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

    content_to_chunk: dict[str, tuple[DocInfo, int]] = {}
    lines: list[str] = []

    for doc in valid_docs:
        for ci, chunk in enumerate(doc.chunks):
            chunk_text = chunk["chunk_text"]
            content_to_chunk[chunk_text] = (doc, ci)
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

    # 結果取得（ストリーミング）
    for doc in valid_docs:
        if doc.chunks:
            doc.embeddings = [None] * len(doc.chunks)  # type: ignore[list-item]

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
                    mapping = content_to_chunk.get(chunk_text)
                    if mapping:
                        doc, ci = mapping
                        doc.embeddings[ci] = embedding
                        embed_ok += 1
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue

    logger.log(f"  Embedding 適用完了: {embed_ok}/{embed_chunk_count} 件")
    logger.log("Phase 2 完了")


# ============================================================
# Phase 3: BQ Insert
# ============================================================

def phase3_bq_insert(
    docs: list[DocInfo], logger: BatchLogger,
) -> tuple[int, int, int]:
    """全ドキュメントを BQ にインサートする. (processed, skipped, errors) を返す."""
    valid_docs = [d for d in docs if d.text]
    if not valid_docs:
        logger.log("Phase 3: BQ Insert 対象なし（スキップ）")
        return 0, len(docs), 0

    logger.log(f"Phase 3: BQ Insert 開始 ({len(valid_docs)} ドキュメント)")

    bq = _get_bq_client()
    processed = 0
    errors = 0

    rows_buffer: list[dict] = []

    for doc in valid_docs:
        has_error = False

        if doc.chunks:
            # チャンク + Embedding あり
            embeddings = doc.embeddings if doc.embeddings else [None] * len(doc.chunks)
            for ci, chunk_data in enumerate(doc.chunks):
                embedding = embeddings[ci] if ci < len(embeddings) else None
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
                rows_buffer.append(row)
        else:
            # メタデータ1行のみ
            rows_buffer.append({
                "DOC_ID":            doc.doc_id,
                "SECURITY_CODE":     doc.sec_code,
                "FILER_NAME":        doc.filer_name,
                "FILER_ID":          doc.filer_id,
                "SUBMISSION_DATE":   doc.sub_date,
                "DOC_TYPE":          doc.doc_type,
                "SECTION_CATEGORY":  None,
                "CHUNK_TEXT":        None,
                "FILE_NAME":         doc.blob_name,
            })

        # バッチ送信
        if len(rows_buffer) >= BQ_BATCH_SIZE:
            errs = bq.insert_rows_json(TABLE_ID, rows_buffer)
            if errs:
                logger.log(f"  BQ Insert エラー: {errs}")
                has_error = True
            rows_buffer = []

        if has_error:
            errors += 1
        else:
            processed += 1

    # 残りをフラッシュ
    if rows_buffer:
        errs = bq.insert_rows_json(TABLE_ID, rows_buffer)
        if errs:
            logger.log(f"  BQ Insert エラー (最終バッチ): {errs}")
            errors += 1

    skipped = len(docs) - len(valid_docs)
    logger.log(f"Phase 3 完了: 成功 {processed}, スキップ {skipped}, エラー {errors}")
    return processed, skipped, errors


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
) -> None:
    """EDINET Batch ETL メイン処理.

    run_mode:
      full   — Phase 1→2→3 一気通貫（日次ロード用）
      submit — Phase 1→2(Embedding投入のみ) → state保存 → exit（バックフィル用）
      resume — state読込 → Phase 2(Embedding結果適用)→3(BQ Insert)（バックフィル用）
    """
    storage_client = _get_storage_client()
    bucket = storage_client.bucket(BUCKET_NAME)
    genai_client = _get_genai_client()

    start_time_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    log_blob_name  = f"log/edinet_load_to_bq_{start_time_str}_{run_mode}_log.txt"
    logger = BatchLogger(bucket, log_blob_name)

    logger.log(f"EDINET Batch ETL 開始: {date_from} ～ {date_to} [mode={run_mode}]")
    if ticker_from or ticker_to:
        logger.log(f"TICKER範囲: {ticker_from or '先頭'} ～ {ticker_to or '末尾'}")
    logger.log(f"実行環境: {RUNTIME}")

    docs: list[DocInfo] = []
    processed = skipped = errors = 0

    try:
        if run_mode == "resume":
            # ── resume モード: GCS から state を読み込み Embedding 結果適用から再開 ──
            logger.log("resume モード: state を GCS から読み込み中...")
            docs, embedding_info = _load_backfill_state(bucket, date_from, date_to, logger)
            logger.flush_to_gcs()

            phase2_poll_and_apply(docs, bucket, genai_client, logger, embedding_info)
            logger.flush_to_gcs()

            processed, skipped, errors = phase3_bq_insert(docs, logger)
            if processed > 0:
                _create_vector_index(logger)

            # state / docs / ロックをクリーンアップ
            state_id = f"{date_from}_{date_to}"
            for suffix in [".json", "_docs.json.gz", ".json.resume_triggered"]:
                path = f"{BACKFILL_STATE_PREFIX}_{state_id}{suffix}"
                try:
                    bucket.blob(path).delete()
                except Exception:
                    pass
            logger.log("state クリーンアップ完了")

        else:
            # ── full / submit モード: Phase 1 から開始 ──
            logger.log("BQ 取込済みファイル一覧を取得中...")
            processed_files = _load_processed_file_names(date_from, date_to)
            logger.log(f"取込済みファイル数: {len(processed_files)} 件")

            docs = phase1_scan_and_extract(
                bucket, date_from, date_to, ticker_from, ticker_to, processed_files, logger,
            )
            logger.flush_to_gcs()

            if not docs:
                logger.log("処理対象ドキュメントなし → 終了")
                return

            if run_mode == "submit":
                # ── submit モード: Embedding バッチ投入 → state 保存 → exit ──
                embedding_info = phase2_chunk_and_submit(docs, bucket, genai_client, logger)
                if embedding_info:
                    _save_backfill_state(bucket, date_from, date_to, docs, embedding_info, logger)
                logger.log("submit モード完了: バッチ投入済み。Cloud Functions で resume を待機。")
                logger.flush_to_gcs()
                return

            # ── full モード: 一気通貫 ──
            phase2_chunk_and_embed(docs, bucket, genai_client, logger)
            logger.flush_to_gcs()

            processed, skipped, errors = phase3_bq_insert(docs, logger)
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
        logger.log(f"総ドキュメント数       : {len(docs)} 件")
        logger.log(f"正常処理              : {processed} 件")
        logger.log(f"スキップ              : {skipped} 件")
        logger.log(f"エラー                : {errors} 件")
        logger.flush_to_gcs()
        print(f"ログファイル出力完了: gs://{BUCKET_NAME}/{log_blob_name}")


# ============================================================
# 日付解決 / 引数パース
# ============================================================

def _resolve_dates() -> tuple[str, str]:
    """DATE_MODE に基づいて日付範囲を返す."""
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
    global DATE_MODE, DATE_FROM, DATE_TO
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
    elif args.mode or env_mode:
        DATE_MODE = args.mode or env_mode
        date_from, date_to = _resolve_dates()
    else:
        date_from, date_to = _resolve_dates()

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

    run_edinet_batch_etl(
        date_from, date_to,
        ticker_from=ticker_from, ticker_to=ticker_to,
        run_mode=run_mode,
    )


if __name__ == "__main__":
    main()
