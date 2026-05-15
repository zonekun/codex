#!/usr/bin/env python3
"""月次データ抽出スクリプト（Phase 2）.

【概要】
  extract_adapter.json を読み込み、正規表現でメトリクス値を抽出して
  monthly_records.json として保存する。

【前提】
  build_monthly_extractor.py（Phase 1）で extract_adapter.json が生成済みであること。

【ファイル構成（GCS: gs://stock_data_1930932/monthly/）】
  meta/{ticker}/extract_adapter.json  → 文書→メトリクスのマッピングルール（Phase 1 が生成）
  record/{ticker}/monthly_records.json  → 抽出結果（year_month × metrics）（本スクリプトが生成）

【使い方】
  PYTHONUTF8=1 uv run python scripts/extract_monthly_data.py --all --since 2020       # 本運用（全社）
  PYTHONUTF8=1 uv run python scripts/extract_monthly_data.py --tickers 3097 --since 2024
  PYTHONUTF8=1 uv run python scripts/extract_monthly_data.py --sample 30 --since 2020  # デフォルト（テスト用）
  PYTHONUTF8=1 uv run python scripts/extract_monthly_data.py --no-gcs
"""

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _safe_re_search(pattern: str, text: str, flags: int = 0, timeout: float = 5.0):
    """re.search with timeout to prevent catastrophic backtracking.

    Runs the regex in a thread and returns None if it exceeds `timeout` seconds.
    """
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(re.search, pattern, text, flags).result(timeout=timeout)
    except (concurrent.futures.TimeoutError, Exception):
        return None


_TIMEOUT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini-to")


def _call_with_timeout(fn, timeout: float = 90.0):
    """関数呼び出しに timeout を付ける（Gemini API ハング対策）.

    Vertex AI / google-genai の generate_content はデフォルトで timeout がなく、
    API 側の問題で無限待機するケースがある（例: 2026-04-17 extract-monthly-data-mxc4s
    で 3663 の Gemini 処理が10分超ハング）。本ヘルパでラップして None を返す。

    重要: `with ThreadPoolExecutor() as ex:` は context manager exit 時に
    `shutdown(wait=True)` を呼び、スレッド内で hung 中の API 呼び出し完了を
    待機してしまう（timeout が効かない）。これを避けるため module-level の
    executor を使い、hung スレッドは leak させて本ワーカーを先に進める。
    """
    try:
        fut = _TIMEOUT_EXECUTOR.submit(fn)
    except Exception:
        return None
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()  # キューにあればキャンセル、稼働中ならスレッドはleak（プロセス終了で消える）
        return None
    except Exception:
        return None

import pandas as pd
from bs4 import BeautifulSoup
from google.cloud import bigquery, storage
from google.oauth2 import service_account

# ====================================================
# SSL パッチ（Windows ローカル GCS/BQ 認証用）
# ====================================================
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA

urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig_init = _req.Session.__init__

def _patched_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False

_req.Session.__init__ = _patched_init

# ====================================================
# 設定
# ====================================================
JST = timezone(timedelta(hours=9))
SCRIPT_DIR = Path(__file__).resolve().parent
# C:\tmp\ などプロジェクト外から実行する場合のフォールバック
_project_root_override = Path(r"C:\gdrive\claude\investment-agent")
PROJECT_ROOT = _project_root_override if _project_root_override.exists() else SCRIPT_DIR.parent

GCP_PROJECT = "gmailpj-357912"
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
GCS_RECORD = "monthly/record"
GCS_DOCS = "monthly/docs"
GCS_LOG = "monthly/log"
BQ_DATASET = "STOCK"
VERTEXAI_REGION = "global"
GEMINI_MODEL = "gemini-3-flash-preview"

GCS_BATCH_MONTHLY = "batch_prediction/monthly_extract"
BATCH_POLL_INTERVAL_MONTHLY = 30

KEY_FILE = PROJECT_ROOT / "keys" / "gcp-service-account.json"
INDEX_CSV = PROJECT_ROOT / "data" / "monthly_adapter_index.csv"
MONTHLYIR_DIR = PROJECT_ROOT / "data" / "monthlyir"
TMP_DIR = Path(r"C:\tmp\extract_monthly_data")
LOG_DIR = Path(r"C:\tmp\extract_monthly_data\logs")

LOG_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ====================================================
# ロギング
# ====================================================

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("extract_monthly_data")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    logger.addHandler(sh)

    log_file = LOG_DIR / f"extract_monthly_data_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.log"
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(log_file, encoding="utf-8", maxBytes=50 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    return logger


# ====================================================
# GCP クライアント
# ====================================================

IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None or os.environ.get("CLOUD_RUN_TASK_INDEX") is not None


def get_credentials():
    if IS_CLOUD_RUN and not KEY_FILE.exists():
        import google.auth
        creds, _ = google.auth.default()
        return creds
    return service_account.Credentials.from_service_account_file(str(KEY_FILE))


def get_gcs() -> storage.Client:
    return storage.Client(project=GCP_PROJECT, credentials=get_credentials())


def get_bq() -> bigquery.Client:
    return bigquery.Client(project=GCP_PROJECT, credentials=get_credentials())


# ====================================================
# GCS ヘルパー
# ====================================================

def gcs_read_json(gcs: storage.Client, path: str) -> Optional[dict]:
    try:
        blob = gcs.bucket(GCS_BUCKET).blob(path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text(encoding="utf-8"))
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] gcs_read_json 失敗: %s | path=%s", e, path,
        )
        return None


def gcs_write_json(gcs: storage.Client, path: str, data: dict) -> None:
    blob = gcs.bucket(GCS_BUCKET).blob(path)
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


# ====================================================
# Gemini クライアント（gemini 抽出タイプ用）
# ====================================================

def get_gemini():
    """Vertex AI Gemini クライアントを初期化して返す."""
    from google import genai
    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=VERTEXAI_REGION, credentials=get_credentials())
    return client


def extract_from_text_gemini(
    full_text: str,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    gemini_model,
    pdf_bytes: Optional[bytes] = None,
) -> Optional[dict]:
    """Gemini を使って文書からメトリクス値を抽出する（extraction_method: gemini 用）.

    pdf_bytes が渡された場合は PDF を直接 Gemini に送信（表構造を保持）。
    pdf_bytes が None の場合は従来通り full_text を使用。

    Args:
        full_text: STRING_AGG で結合した文書テキスト（pdf_bytes がない場合のフォールバック）
        adapter: extract_adapter.json の内容（extraction_method == "gemini" のもの）
        doc_title: 文書タイトル
        submission_date: 提出日（YYYY-MM-DD）
        gemini_model: 初期化済み Gemini モデル
        pdf_bytes: GCS から取得した生 PDF バイナリ（省略時はテキストモード）

    Returns:
        抽出結果 dict（year_month, fields 等）または None
    """
    from google.genai import types

    fields = adapter.get("fields", [])
    if not fields:
        return None

    # _parse_year_month はヒントのみ。Gemini が最終判断する
    ym_hint = _parse_year_month(adapter, doc_title, submission_date)
    ym_hint_str = f"{ym_hint[0]:04d}-{ym_hint[1]:02d}" if ym_hint else "不明"

    field_lines = [
        f'- {f["key"]}: {f.get("description", f["key"])} (type: {f.get("value_type", "number")})'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)

    # 対象月を明示（Gemini が複数月テーブルから正しい列を選ぶために必須）
    target_month_str = f"{ym_hint[1]}月" if ym_hint else "当該月"

    prompt = (
        f"以下の月次開示文書から、指定フィールドの **{target_month_str}** の値を抽出してください。\n\n"
        f"文書タイトル: {doc_title}\n"
        f"提出日: {submission_date}\n"
        f"対象年月: {ym_hint_str}\n\n"
        f"⚠️【絶対禁止事項】(全フィールド共通・最優先・違反厳禁)\n"
        f"1. **集計列を絶対に取らない**: 1Q/2Q/3Q/4Q/第1四半期/第2四半期/第3四半期/第4四半期/"
        f"上期/下期/累計/通期/年度合計/YTD/合計/年計/期計 等の集計列の値は絶対に返さない。"
        f"必ず**月別の単月列**（4月,5月,…,3月 のような12個並んだ月別列）から取得すること\n"
        f"2. **対象月厳守**: 月別列が横並びになっている場合、**{target_month_str} の列のみ**から取る。"
        f"隣の月や末尾の累計列を絶対に取らない。{target_month_str}列が見つからなければ null を返す\n"
        f"3. **当年/前年の取り違え禁止**: 「当年/前年」「今期/前期」「2026年5月期/2025年5月期」等の"
        f"年度比較表が並ぶ場合、対象年月 {ym_hint_str} に該当する**当年（最新期）の行/列**から取る。"
        f"前年の値を当年として返さない\n"
        f"4. **全店/既存店の取り違え禁止**: 「全店」「既存店」「全社」「グループ合計」「単体」等が並ぶ場合、"
        f"フィールドの description で指定された行のみ選ぶ\n"
        f"5. **サブカテゴリ/業態の取り違え禁止**: 「直営/FC/合計」「国内/海外」「戸建/集合/分譲」"
        f"「電気/ガス/水道」「商品/サービス」等のサブカテゴリが並ぶ場合、descriptionで指定されたものだけを選ぶ。"
        f"カテゴリ間で値をコピーしない\n"
        f"6. **パーセント値の形式**: 「前年同月比」「成長率」等は **100前後の水準値**"
        f"（100=据え置き、110=10%増、95=5%減）で返す。「+5」「-3」のような差分形式に変換しない\n"
        f"7. **年度数字を値として返さない**: 2015〜2035 の年の数字（例: 「2026年5月期」の2026）を"
        f"フィールド値として返さない。年度表記であり、求める数値ではない\n\n"
        f"【抽出フィールド】\n{field_desc}\n\n"
        f"【自己検証】返答前に各値について「対象月 {target_month_str} の単月列・description指定の行/カテゴリ」から"
        f"取った値か再確認。不安なら null を返す。\n"
        f"- year_month: {ym_hint_str} を返すこと\n"
        f"- 値が見つからない場合は null を返すこと"
    )

    # PDF モード: 生 PDF を直接 Gemini に送信（表構造を保持）
    if pdf_bytes:
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        contents = [pdf_part, prompt]
    else:
        # テキストモード（フォールバック）
        import re as _re
        cleaned_text = _re.sub(r"文書タイトル:\s*\S[^\n]*", " ", full_text)
        contents = [prompt + f"\n\n【文書テキスト】\n{cleaned_text[:20000]}"]

    response_schema = {
        "type": "object",
        "properties": {
            "year_month": {"type": "string"},
            **{
                f["key"]: {
                    "type": "number" if f.get("value_type") in ("integer", "float", "percentage") else "string",
                    "nullable": True,
                }
                for f in fields
            },
        },
        "required": ["year_month"],
    }

    try:
        resp = _call_with_timeout(
            lambda: gemini_model.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=0,
                ),
            ),
            timeout=90.0,
        )
        if resp is None:
            import logging as _log
            _log.getLogger("extract_monthly_data").warning(
                "[%s] extract_from_text_gemini タイムアウト(90s): doc=%s | prompt_len=%d",
                adapter.get("ticker", "?"), doc_title, len(prompt),
            )
            return None
        data = json.loads(resp.text)
    except Exception as e:
        resp_text_preview = ""
        try:
            resp_text_preview = resp.text[:500] if resp is not None else ""
        except Exception:
            pass
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[%s] extract_from_text_gemini 失敗: %s | doc=%s | prompt_len=%d | resp_len=%d | resp_preview=%s",
            adapter.get("ticker", "?"), e, doc_title, len(prompt),
            len(resp_text_preview), resp_text_preview,
        )
        return None

    # Gemini が返した year_month を解析。失敗時は _parse_year_month ヒントで補完
    try:
        ym_str = data.get("year_month", "")
        parts = str(ym_str).split("-")
        report_year = int(parts[0])
        report_month = int(parts[1])
        if not (2010 <= report_year <= 2035 and 1 <= report_month <= 12):
            raise ValueError(f"year_month out of range: {ym_str}")
        year_month = f"{report_year:04d}-{report_month:02d}"
    except (ValueError, IndexError, AttributeError):
        if ym_hint:
            report_year, report_month = ym_hint
            year_month = f"{report_year:04d}-{report_month:02d}"
        else:
            return None

    fields_data: dict = {}
    for f in fields:
        key = f["key"]
        val = data.get(key)
        if val is None:
            continue
        val_type = f.get("value_type", "float")
        try:
            num = float(val)
            # 年バグ後処理: 年の数字（2015-2035）が値として入った場合は除外
            if 2015 <= num <= 2035:
                continue
            if val_type == "integer":
                fields_data[key] = int(num)
            else:
                fields_data[key] = num
        except (ValueError, TypeError):
            pass

    if not fields_data:
        return None

    return {
        "year": report_year,
        "month": report_month,
        "year_month": year_month,
        "source": "tdnet",
        "extraction_method": "gemini",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


# ====================================================
# Gemini Batch Prediction ヘルパー（月次抽出用）
# ====================================================


def _build_extract_prompt(
    adapter: dict,
    doc_title: str,
    submission_date: str,
    full_text: str = "",
    is_multi_month: bool = False,
) -> str:
    """Gemini 抽出用プロンプトを構築する（同期/バッチ共通）."""
    fields = adapter.get("fields", [])
    ym_hint = _parse_year_month(adapter, doc_title, submission_date)
    ym_hint_str = f"{ym_hint[0]:04d}-{ym_hint[1]:02d}" if ym_hint else "不明"

    if is_multi_month:
        field_lines = [
            f'- {f["key"]}: type={f.get("value_type", "number")}'
            for f in fields
        ]
        field_desc = "\n".join(field_lines)
        custom_prompt = adapter.get("gemini_custom_prompt", "")
        custom_block = f"\n【補足】\n{custom_prompt}\n" if custom_prompt else ""
        return (
            "この月次開示PDFから、掲載されている全会計年度・全月の月次データを抽出してください。\n\n"
            f"文書: {doc_title}\n提出日: {submission_date}\n\n"
            f"【抽出フィールド】\n{field_desc}\n"
            f"{custom_block}\n"
            "【出力形式】JSON配列のみ。説明不要。空欄・未発表の月はスキップ。\n"
            '[\n  {"year_month": "YYYY-MM", "fields": {"フィールド名": 数値, ...}},\n  ...\n]\n'
            "数値は %記号除去、カンマ除去。▲/△ は負の数に変換。"
        )

    field_lines = [
        f'- {f["key"]}: {f.get("description", f["key"])} (type: {f.get("value_type", "number")})'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)
    target_month_str = f"{ym_hint[1]}月" if ym_hint else "当該月"

    return (
        f"以下の月次開示文書から、指定フィールドの **{target_month_str}** の値を抽出してください。\n\n"
        f"文書タイトル: {doc_title}\n"
        f"提出日: {submission_date}\n"
        f"対象年月: {ym_hint_str}\n\n"
        f"⚠️【絶対禁止事項】(全フィールド共通・最優先・違反厳禁)\n"
        f"1. **集計列を絶対に取らない**: 1Q/2Q/3Q/4Q/第1四半期/第2四半期/第3四半期/第4四半期/"
        f"上期/下期/累計/通期/年度合計/YTD/合計/年計/期計 等の集計列の値は絶対に返さない。"
        f"必ず**月別の単月列**（4月,5月,…,3月 のような12個並んだ月別列）から取得すること\n"
        f"2. **対象月厳守**: 月別列が横並びになっている場合、**{target_month_str} の列のみ**から取る。"
        f"隣の月や末尾の累計列を絶対に取らない。{target_month_str}列が見つからなければ null を返す\n"
        f"3. **当年/前年の取り違え禁止**: 「当年/前年」「今期/前期」「2026年5月期/2025年5月期」等の"
        f"年度比較表が並ぶ場合、対象年月 {ym_hint_str} に該当する**当年（最新期）の行/列**から取る。"
        f"前年の値を当年として返さない\n"
        f"4. **全店/既存店の取り違え禁止**: 「全店」「既存店」「全社」「グループ合計」「単体」等が並ぶ場合、"
        f"フィールドの description で指定された行のみ選ぶ\n"
        f"5. **サブカテゴリ/業態の取り違え禁止**: 「直営/FC/合計」「国内/海外」「戸建/集合/分譲」"
        f"「電気/ガス/水道」「商品/サービス」等のサブカテゴリが並ぶ場合、descriptionで指定されたものだけを選ぶ。"
        f"カテゴリ間で値をコピーしない\n"
        f"6. **パーセント値の形式**: 「前年同月比」「成長率」等は **100前後の水準値**"
        f"（100=据え置き、110=10%増、95=5%減）で返す。「+5」「-3」のような差分形式に変換しない\n"
        f"7. **年度数字を値として返さない**: 2015〜2035 の年の数字（例: 「2026年5月期」の2026）を"
        f"フィールド値として返さない。年度表記であり、求める数値ではない\n\n"
        f"【抽出フィールド】\n{field_desc}\n\n"
        f"【自己検証】返答前に各値について「対象月 {target_month_str} の単月列・description指定の行/カテゴリ」から"
        f"取った値か再確認。不安なら null を返す。\n\n"
        f"【出力形式】JSON のみ。説明不要。\n"
        f'{{"year_month": "{ym_hint_str}", "フィールド名": 数値またはnull, ...}}\n'
        f"- year_month: {ym_hint_str} を返すこと\n"
        f"- 値が見つからない場合は null を返すこと"
    )


def _build_extract_response_schema(adapter: dict) -> dict:
    """adapter の fields 定義から response_schema を構築する."""
    fields = adapter.get("fields", [])
    return {
        "type": "object",
        "properties": {
            "year_month": {"type": "string"},
            **{
                f["key"]: {
                    "type": "number" if f.get("value_type") in ("integer", "float", "percentage") else "string",
                    "nullable": True,
                }
                for f in fields
            },
        },
        "required": ["year_month"],
    }


def _build_batch_request_obj(
    key: str,
    prompt: str,
    pdf_gcs_uri: Optional[str] = None,
    full_text: Optional[str] = None,
    is_multi_month: bool = False,
) -> dict:
    """バッチJSONL の1行分のリクエストオブジェクトを構築する.

    response_schema は Batch JSONL では使用しない（TDnet load と同じパターン）。
    プロンプト側で JSON 形式を指示し、response_mime_type のみ設定する。
    """
    parts: list[dict] = []

    if pdf_gcs_uri:
        parts.append({
            "fileData": {"fileUri": pdf_gcs_uri, "mimeType": "application/pdf"},
        })
        parts.append({"text": prompt})
    elif full_text:
        cleaned_text = re.sub(r"文書タイトル:\s*\S[^\n]*", " ", full_text)
        parts.append({"text": prompt + f"\n\n【文書テキスト】\n{cleaned_text[:20000]}"})
    else:
        parts.append({"text": prompt})

    gen_config: dict = {
        "temperature": 0,
        "response_mime_type": "application/json",
    }

    return {
        "key": key,
        "request": {
            "contents": [{"role": "user", "parts": parts}],
            "generation_config": gen_config,
        },
    }


def _upload_monthly_batch_jsonl(
    bucket, lines: list[str], label: str = "extract",
) -> tuple[str, str]:
    """月次抽出バッチ用 JSONL を GCS にアップロードし、(input_uri, output_prefix) を返す."""
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    input_path = f"{GCS_BATCH_MONTHLY}/{label}_{timestamp}_input.jsonl"
    output_path = f"{GCS_BATCH_MONTHLY}/{label}_{timestamp}_output/"

    content = "\n".join(lines)
    bucket.blob(input_path).upload_from_string(
        content.encode("utf-8"), content_type="application/jsonl",
    )
    return f"gs://{GCS_BUCKET}/{input_path}", output_path


def _poll_monthly_batch(
    client, job_name: str, logger: logging.Logger, label: str = "extract",
    max_wait_sec: int = 7200,
) -> bool:
    """バッチジョブをポーリングする. 成功なら True."""
    t0 = time.monotonic()
    while True:
        job = client.batches.get(name=job_name)
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        logger.info(f"  [batch:{label}] ジョブ状態: {state}")

        if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED", "completed"):
            return True
        if state in ("JOB_STATE_FAILED", "FAILED", "JOB_STATE_CANCELLED", "CANCELLED",
                     "failed", "cancelled"):
            logger.warning(f"  [batch:{label}] ジョブ失敗: {state}")
            return False

        elapsed = time.monotonic() - t0
        if elapsed > max_wait_sec:
            logger.warning(f"  [batch:{label}] ポーリングタイムアウト ({max_wait_sec}s超過)")
            return False

        time.sleep(BATCH_POLL_INTERVAL_MONTHLY)


def _download_monthly_batch_results(bucket, output_prefix: str) -> dict[str, dict]:
    """バッチ出力 JSONL を GCS からダウンロードし、key → response obj の辞書を返す."""
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


def _parse_batch_result_single(
    resp_obj: dict, adapter: dict, doc_title: str, submission_date: str,
    source_label: str = "tdnet",
) -> Optional[dict]:
    """バッチ結果1件を record dict にパースする（単月抽出用）."""
    try:
        text_resp = resp_obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text_resp)
        if isinstance(data, list):
            data = data[0] if data else {}
    except (KeyError, IndexError, json.JSONDecodeError):
        return None

    fields = adapter.get("fields", [])
    ym_hint = _parse_year_month(adapter, doc_title, submission_date)

    try:
        ym_str = data.get("year_month", "")
        parts = str(ym_str).split("-")
        report_year = int(parts[0])
        report_month = int(parts[1])
        if not (2010 <= report_year <= 2035 and 1 <= report_month <= 12):
            raise ValueError(f"year_month out of range: {ym_str}")
        year_month = f"{report_year:04d}-{report_month:02d}"
    except (ValueError, IndexError, AttributeError):
        if ym_hint:
            report_year, report_month = ym_hint
            year_month = f"{report_year:04d}-{report_month:02d}"
        else:
            return None

    fields_data: dict = {}
    for f in fields:
        key = f["key"]
        val = data.get(key)
        if val is None:
            continue
        val_type = f.get("value_type", "float")
        try:
            num = float(val)
            if 2015 <= num <= 2035:
                continue
            if val_type == "integer":
                fields_data[key] = int(num)
            else:
                fields_data[key] = num
        except (ValueError, TypeError):
            pass

    if not fields_data:
        return None

    return {
        "year": report_year,
        "month": report_month,
        "year_month": year_month,
        "source": source_label,
        "extraction_method": "gemini",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


def _parse_batch_result_multi_month(
    resp_obj: dict, adapter: dict, doc_title: str, submission_date: str,
    since: int = 2020,
    source_label: str = "pdf_gemini_all",
) -> list[dict]:
    """バッチ結果1件を record dict リストにパースする（複数月抽出用）."""
    try:
        text_resp = resp_obj["response"]["candidates"][0]["content"]["parts"][0]["text"]
        # JSON 配列を抽出
        json_m = re.search(r"\[.*\]", text_resp, re.DOTALL)
        if not json_m:
            return []
        items = json.loads(json_m.group())
    except (KeyError, IndexError, json.JSONDecodeError):
        return []

    fields = adapter.get("fields", [])
    records: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ym = item.get("year_month", "")
        fm = re.match(r"(\d{4})-(\d{1,2})", str(ym))
        if not fm:
            continue
        y_i = int(fm.group(1))
        m_i = int(fm.group(2))
        if y_i < since:
            continue
        flds = item.get("fields", {})
        if not isinstance(flds, dict) or not flds:
            continue
        flds_clean: dict = {}
        for k, v in flds.items():
            if isinstance(v, (int, float)):
                flds_clean[k] = v
        if not flds_clean:
            continue
        records.append({
            "year": y_i,
            "month": m_i,
            "year_month": f"{y_i:04d}-{m_i:02d}",
            "source": source_label,
            "extraction_method": "gemini",
            "doc_title": doc_title,
            "submission_date": str(submission_date),
            "fields": flds_clean,
        })
    return records


def _submit_and_poll_extract_batch(
    batch_lines: list[str],
    gcs: storage.Client,
    logger: logging.Logger,
) -> dict[str, dict]:
    """バッチJSONL をサブミット → ポーリング → 結果辞書を返す."""
    from google import genai

    bucket = gcs.bucket(GCS_BUCKET)
    input_uri, output_prefix = _upload_monthly_batch_jsonl(bucket, batch_lines)
    logger.info(f"  [batch] JSONL アップロード完了: {input_uri} ({len(batch_lines)} 件)")

    client = get_gemini()
    job = client.batches.create(
        model=GEMINI_MODEL,
        src=input_uri,
        config=genai.types.CreateBatchJobConfig(
            dest=f"gs://{GCS_BUCKET}/{output_prefix}",
        ),
    )
    logger.info(f"  [batch] ジョブ投入: {job.name}")

    success = _poll_monthly_batch(client, job.name, logger)
    if not success:
        logger.warning("  [batch] バッチジョブ失敗")
        return {}

    results = _download_monthly_batch_results(bucket, output_prefix)
    logger.info(f"  [batch] 結果取得: {len(results)} 件")
    return results


# ====================================================
# BQ: TDNET 月次文書テキスト取得
# ====================================================

def get_tdnet_docs(ticker: str, bq: bigquery.Client, since: int = 2020, limit: int = 200) -> list[dict]:
    """TDNET_DOCUMENTS_ENHANCED から月次開示の全文テキストを取得.

    フィルタ: MAIN_CATEGORY = '月次開示' のみ使用（2026-04-18 改修）。
    SUB_CATEGORIES はノイズが多いため併用しない。取りこぼし発見時に再検討。
    """
    sql = f"""
    SELECT
      TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME,
      STRING_AGG(CHUNK_TEXT, ' ') AS full_text
    FROM `{GCP_PROJECT}.{BQ_DATASET}.TDNET_DOCUMENTS_ENHANCED`
    WHERE TICKER = @ticker
      AND MAIN_CATEGORY = '月次開示'
      AND SUBMISSION_DATE >= '{since}-01-01'
    GROUP BY TICKER, SUBMISSION_DATE, DOC_TITLE, FILE_NAME
    ORDER BY SUBMISSION_DATE DESC
    LIMIT {limit}
    """
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)]
    )
    try:
        df = bq.query(sql, job_config=job_cfg).to_dataframe()
        return df.to_dict("records")
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error(f"get_tdnet_docs BQエラー: {e}")
        return []


# ====================================================
# データ抽出: TDNET テキスト
# ====================================================

# 元号→西暦変換テーブル（基底年: 元号1年 = base + 1 を base で表す）
_JAPANESE_ERA_BASE: dict[str, int] = {
    "令和": 2018,  # 令和1年 = 2019
    "平成": 1988,  # 平成1年 = 1989
    "昭和": 1925,  # 昭和1年 = 1926
    "大正": 1911,  # 大正1年 = 1912
}


def _era_to_western_year(text: str) -> Optional[int]:
    """テキストから元号年を抽出して西暦に変換。見つからなければ None。"""
    for era, base in _JAPANESE_ERA_BASE.items():
        m = re.search(rf"{era}(\d{{1,2}})年", text)
        if m:
            return base + int(m.group(1))
    return None



# ==============================================================
# 会計年度履歴 lookup（時点ベース fy_end_month 取得）
# ==============================================================
_FY_HISTORY_CACHE: Optional[dict[str, list[dict]]] = None


def _load_fy_history() -> dict[str, list[dict]]:
    """data/master/ticker_fiscal_year_history.csv を読み込む (singleton)."""
    global _FY_HISTORY_CACHE
    if _FY_HISTORY_CACHE is not None:
        return _FY_HISTORY_CACHE
    import csv as _csv
    p = Path("data/master/ticker_fiscal_year_history.csv")
    result: dict[str, list[dict]] = {}
    if not p.exists():
        _FY_HISTORY_CACHE = result
        return result
    with open(p, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            t = row.get("ticker", "").strip()
            try:
                fss = row.get("fy_start_date", "").strip()
                fse = row.get("fy_end_date", "").strip()
                if not fss or not fse:
                    continue
                from datetime import date as _date
                start = _date.fromisoformat(fss[:10])
                end = _date.fromisoformat(fse[:10])
            except Exception:
                continue
            result.setdefault(t, []).append({"start": start, "end": end})
    # 各 ticker のエントリを start でソート
    for t in result:
        result[t].sort(key=lambda r: r["start"])
    _FY_HISTORY_CACHE = result
    return result


def _lookup_fy_end_month(ticker: str, submission_date: str) -> Optional[int]:
    """ticker + submission_date → その時点での会計年度終了月を返す."""
    history = _load_fy_history()
    entries = history.get(ticker, [])
    if not entries:
        return None
    try:
        from datetime import date as _date
        sub = _date.fromisoformat(str(submission_date)[:10])
    except Exception:
        return None
    # 範囲内マッチ
    for e in entries:
        if e["start"] <= sub <= e["end"]:
            return e["end"].month
    # 過去のうち最新
    past = [e for e in entries if e["end"] < sub]
    if past:
        return max(past, key=lambda x: x["end"])["end"].month
    # 未来のうち最古（submission が履歴前）
    future = [e for e in entries if e["start"] > sub]
    if future:
        return min(future, key=lambda x: x["start"])["end"].month
    return None


def _parse_year_month(adapter: dict, doc_title: str, submission_date: str) -> Optional[tuple[int, int]]:
    """アダプターのregexでタイトルから年月を抽出。失敗したら提出日から推定。

    解決順序:
      0. adapter.year_month_from_submission_minus_1=True → submission_date - 1 月を返す
      1. adapter の year_from_title_regex（アダプター指定）
      2. 元号→西暦変換（令和/平成/昭和）
      3. 直接西暦年「YYYY年」（決算期形式でない場合のみ）
      4. 提出日ヒューリスティック（day≤15 → 前月）
    """
    # Step 0: submission_date - 1 month ショートカット
    # (8218 コメリ等、filename YYYYMM プレフィックスが提出年月で、報告月はその前月の銘柄用)
    if adapter.get("year_month_from_submission_minus_1") and submission_date:
        try:
            dt = pd.to_datetime(submission_date)
            if not pd.isna(dt):
                y, m = int(dt.year), int(dt.month)
                if m == 1:
                    return y - 1, 12
                return y, m - 1
        except Exception:
            pass
    year_re = adapter.get("year_from_title_regex") or r"(?P<year>\d{4})年"

    # 月抽出: まず「X月度」（実報告月）を優先、なければアダプターの regex を使用
    month_m = re.search(r"(?P<month>\d{1,2})月度", doc_title)
    if not month_m:
        month_re = adapter.get("month_from_title_regex") or r"(?P<month>\d{1,2})月[度期]"
        # Gemini が (?<name>...) を生成することがある → Python 用 (?P<name>..>) に正規化
        month_re = re.sub(r'\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>', r'(?P<\1>', month_re)
        try:
            month_m = re.search(month_re, doc_title)
        except re.error:
            month_m = re.search(r"(?P<month>\d{1,2})月[度期]", doc_title)

    year_re = re.sub(r'\(\?<([a-zA-Z_][a-zA-Z0-9_]*)>', r'(?P<\1>', year_re)
    try:
        year_m = re.search(year_re, doc_title)
    except re.error:
        year_m = re.search(r"(?P<year>\d{4})年", doc_title)

    if year_m and month_m:
        try:
            yd = year_m.groupdict()
            md = month_m.groupdict()
            year = int(yd.get("year", year_m.group(1)))
            month = int(md.get("month", month_m.group(1)))
            # アダプターに year_from_title_regex が未指定の場合、デフォルト regex
            # （\d{4}年）が「YYYY年M月期N月度」の決算期タイトルにヒットすると
            # 会計年度の西暦をそのまま返してしまう。その場合は提出日ヒューリスティックへ委ねる。
            _is_default_re = not adapter.get("year_from_title_regex")
            if _is_default_re and re.search(r"\d{4}年\d{1,2}月期", doc_title):
                pass  # fall through → 提出日ヒューリスティック
            else:
                # 会計年度補正: adapter.use_fy_history_correction=True かつ
                # BQ マスタに ticker 情報があれば、submission_date 時点の
                # fy_end_month で year を補正する。
                # doc_title 中「YYYY年M月期」の YYYY は fy_end_year (決算期終了年)
                # target_month > fy_end_month なら実 year = fy_end_year - 1
                if adapter.get("use_fy_history_correction"):
                    _ticker = str(adapter.get("ticker", "")).strip()
                    _fy_end_month = _lookup_fy_end_month(_ticker, submission_date)
                    if _fy_end_month and month > _fy_end_month:
                        return year - 1, month
                return year, month
        except (IndexError, ValueError):
            pass

    # --- アダプター regex が失敗した場合のフォールバック ---

    # 2. 元号→西暦変換（令和/平成/昭和 対応）
    if month_m:
        era_year = _era_to_western_year(doc_title)
        if era_year is not None:
            try:
                md = month_m.groupdict()
                month = int(md.get("month", month_m.group(1)))
                return era_year, month
            except (IndexError, ValueError):
                pass

    # 3. 直接西暦年「YYYY年」（YYYY年M月期 形式の決算期タイトルは除外。
    #    決算期形式は提出日ヒューリスティックに委ねる方が正確）
    if month_m and not re.search(r"\d{4}年\d{1,2}月期", doc_title):
        wy = re.search(r"(?P<year>\d{4})年", doc_title)
        if wy:
            try:
                md = month_m.groupdict()
                year = int(wy.group("year"))
                month = int(md.get("month", month_m.group(1)))
                return year, month
            except (IndexError, ValueError):
                pass

    # 4. 提出日ヒューリスティック（月次文書は翌月初旬〜中旬提出が多い）
    try:
        dt = pd.to_datetime(submission_date)
        if pd.isna(dt):
            return None
        day = int(dt.day)
        if day <= 15:
            report_month = int(dt.month) - 1 if int(dt.month) > 1 else 12
            report_year = int(dt.year) if int(dt.month) > 1 else int(dt.year) - 1
        else:
            report_month = int(dt.month)
            report_year = int(dt.year)
        return report_year, report_month
    except Exception:
        return None


# PDF 抽出テキストの品質判定用正規表現（P0-1: 062 MD §3 準拠、2026-04-21 追加）
# 正常とみなす文字クラス: ひらがな/カタカナ/漢字/英数字/一般的な記号/全角記号・全角英数
_NORMAL_TEXT_RE = re.compile(
    r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF"  # CJK
    r"A-Za-z0-9"                                   # 英数字
    r"\s\.,\(\)\[\]\{\}\-\+\*/%:;!\?\|~=<>_\"'#&"  # 半角記号
    r"\u3000-\u303F\uFF00-\uFFEF]"                 # 全角記号・全角英数
)


def _text_quality_ok(
    text: str,
    min_len: int = 50,
    garbled_threshold: float = 0.20,
) -> bool:
    """抽出テキストの質を判定する (P0-1, 062 MD §3 準拠)。

    量 (min_len 以上) と質 (文字化け率 < threshold) の両方を満たすとき True。
    文字化け率 = 1 - (正常文字数 / 全文字数)。正常文字 = CJK/英数字/一般的記号。

    Args:
        text: 抽出結果テキスト。
        min_len: 最小文字数（この値未満は即 False）。
        garbled_threshold: 文字化け率の許容上限。これを超えると False。

    Returns:
        量・質の両方を満たすなら True。

    References:
        docs/knowledges/tools/062_pdf_processing_strategy.md §3 (L59-L61)
    """
    s = (text or "").strip()
    if len(s) < min_len:
        return False
    normal = len(_NORMAL_TEXT_RE.findall(s))
    ratio_abnormal = 1 - (normal / len(s))
    return ratio_abnormal < garbled_threshold


def _extract_pdf_ocr(pdf_bytes: bytes, logger: Optional[logging.Logger] = None) -> str:
    """画像PDF を OCR してテキストを返す。

    pdf2image → Pillow → pytesseract（jpn+eng）。
    画像PDFのみ対応（フォント埋め込みなしのスキャン PDF）。
    """
    import io

    # まず pdfplumber でテキスト有無を確認（テキストPDFならOCR不要）
    # P0-1: 量 (len>50) に加えて質 (_text_quality_ok) も判定
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text_check = " ".join((p.extract_text() or "") for p in pdf.pages[:2])
            if _text_quality_ok(text_check):
                return text_check  # テキストPDF (質OK) → OCR不要
            if logger and len(text_check.strip()) > 50:
                logger.debug(
                    "  [text_quality_fail] pdfplumber text_check で文字化け検出 → OCR へ escalate"
                )
    except Exception:
        pass

    lines: list[str] = []
    try:
        import fitz  # PyMuPDF — pdf2image より軽量
        import pytesseract
        from PIL import Image

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_num, page in enumerate(doc):
                # 150dpi でレンダリング
                mat = fitz.Matrix(150 / 72, 150 / 72)
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                ocr_text = pytesseract.image_to_string(img, lang="jpn+eng")
                if ocr_text.strip():
                    lines.append(ocr_text)
                if logger:
                    logger.debug(f"    OCR page {page_num}: {len(ocr_text)} chars")
    except ImportError as e:
        if logger:
            logger.warning(f"  [ocr] 依存パッケージ不足: {e}")
    except Exception as e:
        if logger:
            logger.warning(f"  [ocr] OCR 失敗: {e}")

    return "\n".join(lines)


def _extract_pdf_gemini_personal(
    pdf_bytes: bytes,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    client,
    model_name: str,
    logger: Optional[logging.Logger] = None,
) -> Optional[dict]:
    """個人APIキーの google-genai を使って PDF からメトリクス値を抽出する。

    Vertex AI ではなくローカル実行用。gemini-3-flash-preview を使用。
    """
    import base64

    fields = adapter.get("fields", [])
    if not fields:
        return None

    ym = _parse_year_month(adapter, doc_title, submission_date)
    if not ym:
        return None
    year_val, month_val = ym
    ym_str = f"{year_val:04d}-{month_val:02d}"

    field_lines = [
        f'- {f["key"]}: type={f.get("value_type", "number")}'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)

    prompt = (
        f"以下の月次開示PDFから、指定フィールドの {month_val}月 の値を抽出してください。\n\n"
        f"文書: {doc_title}\n提出日: {submission_date}\n対象年月: {ym_str}\n\n"
        f"【抽出フィールド】\n{field_desc}\n\n"
        f"【出力形式】JSON のみ。説明不要。\n"
        f'{{"year_month": "{ym_str}", "fields": {{"フィールド名": 数値, ...}}}}\n'
        f"数値は %記号除去、カンマ除去。▲/△ は負の数に変換。"
    )

    try:
        from google.genai import types
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        response = _call_with_timeout(
            lambda: client.models.generate_content(
                model=model_name,
                contents=[pdf_part, prompt],
            ),
            timeout=90.0,
        )
        if response is None:
            if logger:
                logger.warning(f"  [gemini] {doc_title}: timeout(90s) でスキップ")
            return None
        resp_text = response.text.strip()
        # JSON 部分を抽出
        json_m = re.search(r"\{.*\}", resp_text, re.DOTALL)
        if json_m:
            result = json.loads(json_m.group())
            if "fields" in result and result["fields"]:
                result.setdefault("year", year_val)
                result.setdefault("month", month_val)
                result.setdefault("year_month", ym_str)
                result["source"] = "pdf_gemini"
                result["doc_title"] = doc_title
                result["submission_date"] = str(submission_date)
                if logger:
                    logger.debug(f"  [gemini] {doc_title}: {len(result['fields'])} fields extracted")
                return result
    except Exception as e:
        if logger:
            logger.debug(f"  [gemini] 抽出エラー: {e}")

    return None


def _extract_pdf_gemini_all_months(
    pdf_bytes: bytes,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    client,
    model_name: str,
    since: int = 2020,
    logger: Optional[logging.Logger] = None,
) -> list[dict]:
    """PDF全体から複数月分のレコードを一括抽出する（Gemini PDF直接）。

    会計年度跨ぎや、1つのPDFに複数会計年度の表が含まれる場合向け。
    adapter の ``extraction_method: gemini`` + ``overwrite_past_months: true`` で使用。

    adapter.gemini_custom_prompt があれば差し込む（会計年度境界の指示など）。
    """
    import base64

    fields = adapter.get("fields", [])
    if not fields:
        return []

    field_lines = [
        f'- {f["key"]}: type={f.get("value_type", "number")}'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)

    custom_prompt = adapter.get("gemini_custom_prompt", "")
    custom_block = f"\n【補足】\n{custom_prompt}\n" if custom_prompt else ""

    prompt = (
        "この月次開示PDFから、掲載されている全会計年度・全月の月次データを抽出してください。\n\n"
        f"文書: {doc_title}\n提出日: {submission_date}\n\n"
        f"【抽出フィールド】\n{field_desc}\n"
        f"{custom_block}\n"
        "【出力形式】JSON配列のみ。説明不要。空欄・未発表の月はスキップ。\n"
        '[\n  {"year_month": "YYYY-MM", "fields": {"フィールド名": 数値, ...}},\n  ...\n]\n'
        "数値は %記号除去、カンマ除去。▲/△ は負の数に変換。"
    )

    try:
        from google.genai import types
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        response = _call_with_timeout(
            lambda: client.models.generate_content(
                model=model_name,
                contents=[pdf_part, prompt],
            ),
            timeout=120.0,
        )
        if response is None:
            if logger:
                logger.warning(f"  [gemini_all] {doc_title}: timeout(120s) でスキップ")
            return []
        resp_text = response.text.strip()
        # JSON 配列を抽出
        json_m = re.search(r"\[.*\]", resp_text, re.DOTALL)
        if not json_m:
            if logger:
                logger.debug(f"  [gemini_all] JSON配列が見つからない: {resp_text[:200]}")
            return []
        try:
            items = json.loads(json_m.group())
        except json.JSONDecodeError as e:
            if logger:
                logger.debug(f"  [gemini_all] JSONパース失敗: {e}")
            return []

        records = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ym = item.get("year_month", "")
            fm = re.match(r"(\d{4})-(\d{1,2})", str(ym))
            if not fm:
                continue
            y_i = int(fm.group(1))
            m_i = int(fm.group(2))
            if y_i < since:
                continue
            flds = item.get("fields", {})
            if not isinstance(flds, dict) or not flds:
                continue
            # 数値以外は除外
            flds_clean: dict = {}
            for k, v in flds.items():
                if isinstance(v, (int, float)):
                    flds_clean[k] = v
            if not flds_clean:
                continue
            records.append({
                "year": y_i,
                "month": m_i,
                "year_month": f"{y_i:04d}-{m_i:02d}",
                "source": "pdf_gemini_all",
                "doc_title": doc_title,
                "submission_date": str(submission_date),
                "fields": flds_clean,
            })
        if logger:
            logger.debug(f"  [gemini_all] {doc_title}: {len(records)} months extracted")
        return records
    except Exception as e:
        if logger:
            logger.debug(f"  [gemini_all] 抽出エラー: {e}")
        return []


def _extract_html_gemini_personal(
    html_text: str,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    client,
    model_name: str,
    logger: Optional[logging.Logger] = None,
) -> Optional[dict]:
    """個人APIキーの google-genai を使って HTML テキストからメトリクス値を抽出する。

    source=html_table + extraction_method=gemini 用。
    """
    fields = adapter.get("fields", [])
    if not fields:
        return None

    ym = _parse_year_month(adapter, doc_title, submission_date)
    if not ym:
        return None
    year_val, month_val = ym
    ym_str = f"{year_val:04d}-{month_val:02d}"

    field_lines = []
    for f in fields:
        desc = f.get("description", "")
        line = f'- {f["key"]}: type={f.get("value_type", "number")}'
        if desc:
            line += f" ({desc})"
        field_lines.append(line)
    field_desc = "\n".join(field_lines)

    custom_prompt = adapter.get("custom_prompt") or adapter.get("gemini_custom_prompt", "")
    custom_section = f"\n\n【補足情報】\n{custom_prompt}" if custom_prompt else ""

    prompt = (
        f"以下の月次開示HTMLから、指定フィールドの {month_val}月 の値を抽出してください。\n\n"
        f"文書: {doc_title}\n提出日: {submission_date}\n対象年月: {ym_str}\n\n"
        f"【抽出フィールド】\n{field_desc}\n\n"
        f"【出力形式】JSON のみ。説明不要。\n"
        f'{{"year_month": "{ym_str}", "fields": {{"フィールド名": 数値, ...}}}}\n'
        f"数値は %記号除去、カンマ除去。▲/△ は負の数に変換。"
        f"{custom_section}"
        f"\n\n【HTMLテキスト】\n{html_text[:20000]}"
    )

    try:
        response = _call_with_timeout(
            lambda: client.models.generate_content(
                model=model_name,
                contents=[prompt],
            ),
            timeout=180.0,
        )
        if response is None:
            if logger:
                logger.warning(f"  [gemini] {doc_title}: HTML timeout(180s) でスキップ")
            return None
        resp_text = response.text.strip()
        # JSON 部分を抽出
        json_m = re.search(r"\{.*\}", resp_text, re.DOTALL)
        if json_m:
            result = json.loads(json_m.group())
            if "fields" in result and result["fields"]:
                result.setdefault("year", year_val)
                result.setdefault("month", month_val)
                result.setdefault("year_month", ym_str)
                result["source"] = "html_gemini"
                result["doc_title"] = doc_title
                result["submission_date"] = str(submission_date)
                if logger:
                    logger.debug(f"  [gemini] {doc_title}: {len(result['fields'])} fields extracted")
                return result
    except Exception as e:
        if logger:
            logger.debug(f"  [gemini] HTML抽出エラー: {e}")

    return None


def _extract_html_gemini_all_months(
    html_text: str,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    client,
    model_name: str,
    since: int = 2020,
    logger: Optional[logging.Logger] = None,
) -> list[dict]:
    """HTML全体から複数月分のレコードを一括抽出する（Gemini HTML直接）。

    累積型HTMLページ（1ページに複数月のデータが含まれる場合）向け。
    adapter の ``extraction_method: gemini`` + ``gemini_multi_month: true`` で使用。
    """
    fields = adapter.get("fields", [])
    if not fields:
        return []

    field_lines = [
        f'- {f["key"]}: type={f.get("value_type", "number")}'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)

    custom_prompt = adapter.get("custom_prompt") or adapter.get("gemini_custom_prompt", "")
    custom_block = f"\n【補足】\n{custom_prompt}\n" if custom_prompt else ""

    prompt = (
        "この月次開示HTMLから、掲載されている全会計年度・全月の月次データを抽出してください。\n\n"
        f"文書: {doc_title}\n提出日: {submission_date}\n\n"
        f"【抽出フィールド】\n{field_desc}\n"
        f"{custom_block}\n"
        "【出力形式】JSON配列のみ。説明不要。空欄・未発表の月はスキップ。\n"
        '[\n  {"year_month": "YYYY-MM", "fields": {"フィールド名": 数値, ...}},\n  ...\n]\n'
        "数値は %記号除去、カンマ除去。▲/△ は負の数に変換。"
        f"\n\n【HTMLテキスト】\n{html_text[:40000]}"
    )

    try:
        response = _call_with_timeout(
            lambda: client.models.generate_content(
                model=model_name,
                contents=[prompt],
            ),
            timeout=180.0,
        )
        if response is None:
            if logger:
                logger.warning(f"  [gemini_all_html] {doc_title}: timeout(180s) でスキップ")
            return []
        resp_text = response.text.strip()
        json_m = re.search(r"\[.*\]", resp_text, re.DOTALL)
        if not json_m:
            if logger:
                logger.debug(f"  [gemini_all_html] JSON配列が見つからない: {resp_text[:200]}")
            return []
        try:
            items = json.loads(json_m.group())
        except json.JSONDecodeError as e:
            if logger:
                logger.debug(f"  [gemini_all_html] JSONパース失敗: {e}")
            return []

        records = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ym = item.get("year_month", "")
            fm = re.match(r"(\d{4})-(\d{1,2})", str(ym))
            if not fm:
                continue
            y_i = int(fm.group(1))
            m_i = int(fm.group(2))
            if y_i < since:
                continue
            flds = item.get("fields", {})
            if not isinstance(flds, dict) or not flds:
                continue
            flds_clean: dict = {}
            for k, v in flds.items():
                if isinstance(v, (int, float)):
                    flds_clean[k] = v
            if not flds_clean:
                continue
            records.append({
                "year": y_i,
                "month": m_i,
                "year_month": f"{y_i:04d}-{m_i:02d}",
                "source": "html_gemini_all",
                "doc_title": doc_title,
                "submission_date": str(submission_date),
                "fields": flds_clean,
            })
        if logger:
            logger.debug(f"  [gemini_all_html] {doc_title}: {len(records)} months extracted")
        return records
    except Exception as e:
        if logger:
            logger.debug(f"  [gemini_all_html] 抽出エラー: {e}")
        return []


def _build_excel_gemini_prompt(
    adapter: dict,
    doc_title: str,
    submission_date: str,
    csv_text: str,
) -> str:
    """Excel→CSV テキストから月次メトリクスを抽出する Gemini プロンプトを構築する."""
    fields = adapter.get("fields", [])
    field_lines = [
        f'- {f["key"]}: {f.get("description", f["key"])} (type: {f.get("value_type", "number")})'
        for f in fields
    ]
    field_desc = "\n".join(field_lines)
    custom_prompt = adapter.get("custom_prompt") or adapter.get("gemini_custom_prompt", "")
    custom_block = f"\n【補足】\n{custom_prompt}\n" if custom_prompt else ""

    return (
        "以下のExcel/CSVから変換したテーブルデータから、掲載されている全月の月次データを抽出してください。\n\n"
        f"文書: {doc_title}\n提出日: {submission_date}\n\n"
        "⚠️【絶対禁止事項】\n"
        "1. **集計列を絶対に取らない**: 1Q/2Q/3Q/4Q/上期/下期/累計/通期/年度合計/YTD/合計 等の"
        "集計行・集計列の値は返さない。必ず月別の単月データから取得すること\n"
        "2. **当年/前年の取り違え禁止**: 年度比較が並ぶ場合、最新期（当年）の値を取る\n"
        "3. **全店/既存店の取り違え禁止**: フィールドの description で指定された区分のみ選ぶ\n"
        "4. **サブカテゴリの取り違え禁止**: 「直営/FC/合計」「国内/海外」等が並ぶ場合、"
        "description で指定されたものだけを選ぶ\n"
        "5. **パーセント値の形式**: 前年同月比は100前後の水準値（100=据え置き、110=10%増）で返す\n"
        "6. **年度数字を値として返さない**: 2015〜2035 の年の数字はフィールド値ではない\n\n"
        f"【抽出フィールド】\n{field_desc}\n"
        f"{custom_block}\n"
        "【出力形式】JSON配列のみ。説明不要。空欄・未発表の月はスキップ。\n"
        '[\n  {"year_month": "YYYY-MM", "fields": {"フィールド名": 数値, ...}},\n  ...\n]\n'
        "数値は %記号除去、カンマ除去。▲/△ は負の数に変換。\n\n"
        f"【CSVデータ】\n{csv_text[:20000]}"
    )


def _extract_xlsx_gemini_personal(
    xlsx_path: Path,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    client,
    model_name: str,
    logger: Optional[logging.Logger] = None,
) -> list[dict]:
    """pandas で XLSX/CSV を テキスト化し、Gemini で月次メトリクスを抽出する.

    extraction_method=excel_gemini 用。複数月レコードのリストを返す。
    XLSX と CSV の両方に対応（拡張子で自動判別）。
    """
    try:
        if xlsx_path.suffix.lower() == ".csv":
            enc = adapter.get("encoding", "utf-8-sig")
            df = pd.read_csv(xlsx_path, header=None, encoding=enc)
        else:
            sheet_name = adapter.get("sheet_name", 0)
            df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)
    except Exception as e:
        if logger:
            logger.warning(f"  [excel_gemini] {xlsx_path.name} 読み込み失敗: {e}")
        return []

    csv_text = df.to_csv(index=False, header=False)
    if not csv_text.strip():
        if logger:
            logger.debug(f"  [excel_gemini] {xlsx_path.name} CSVテキスト空")
        return []

    prompt = _build_excel_gemini_prompt(adapter, doc_title, submission_date, csv_text)

    try:
        response = _call_with_timeout(
            lambda: client.models.generate_content(
                model=model_name,
                contents=[prompt],
            ),
            timeout=180.0,
        )
        if response is None:
            if logger:
                logger.warning(f"  [excel_gemini] {doc_title}: timeout(180s)")
            return []
        resp_text = response.text.strip()

        json_arr_m = re.search(r"\[.*\]", resp_text, re.DOTALL)
        if json_arr_m:
            items = json.loads(json_arr_m.group())
            records: list[dict] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                flds = item.get("fields", {})
                if not isinstance(flds, dict) or not flds:
                    continue
                ym = item.get("year_month", "")
                fm = re.match(r"(\d{4})-(\d{1,2})", str(ym))
                if not fm:
                    continue
                records.append({
                    "year": int(fm.group(1)),
                    "month": int(fm.group(2)),
                    "year_month": f"{int(fm.group(1)):04d}-{int(fm.group(2)):02d}",
                    "source": "excel_gemini",
                    "extraction_method": "excel_gemini",
                    "doc_title": doc_title,
                    "submission_date": str(submission_date),
                    "fields": {k: v for k, v in flds.items() if isinstance(v, (int, float))},
                })
            if logger and records:
                logger.debug(f"  [excel_gemini] {doc_title}: {len(records)} months extracted")
            return records

        json_obj_m = re.search(r"\{.*\}", resp_text, re.DOTALL)
        if json_obj_m:
            result = json.loads(json_obj_m.group())
            flds = result.get("fields", {})
            if isinstance(flds, dict) and flds:
                ym = result.get("year_month", "")
                fm = re.match(r"(\d{4})-(\d{1,2})", str(ym))
                if fm:
                    return [{
                        "year": int(fm.group(1)),
                        "month": int(fm.group(2)),
                        "year_month": f"{int(fm.group(1)):04d}-{int(fm.group(2)):02d}",
                        "source": "excel_gemini",
                        "extraction_method": "excel_gemini",
                        "doc_title": doc_title,
                        "submission_date": str(submission_date),
                        "fields": {k: v for k, v in flds.items() if isinstance(v, (int, float))},
                    }]
    except Exception as e:
        if logger:
            logger.debug(f"  [excel_gemini] 抽出エラー: {e}")

    return []


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出。精度優先で3段フォールバック。

    1. pdfplumber extract_tables() → ラベル付きフラットテキスト（罫線表に最高精度）
    2. PyMuPDF get_text() → CJK間スペースなし（テキスト系PDFに信頼性高）
    3. pdfplumber extract_text() → 最終フォールバック
    """
    import io

    def _table_to_lines(tables: list) -> list[str]:
        """pdfplumber テーブルをラベル継承付きフラットテキスト行に変換。"""
        lines: list[str] = []
        section = ""
        for tbl in tables:
            for row in tbl:
                if not row or not any(c for c in row if c):
                    continue
                cells = [(c or "").replace("\n", " ").replace("\t", " ").strip() for c in row]
                col0 = cells[0] if cells else ""
                col1 = cells[1] if len(cells) > 1 else ""
                if col0:
                    section = col0  # 新セクション
                # ラベル（セクション + サブタイプ）+ 数値列をスペース区切りで結合
                label = f"{section} {col1}".strip() if col1 else section
                values = " ".join(c for c in cells[2:] if c)
                if values:
                    lines.append(f"{label} {values}")
        return lines

    # ① pdfplumber テーブル抽出
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_lines: list[str] = []
            has_any_table = False
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    has_any_table = True
                    all_lines.extend(_table_to_lines(tables))
                else:
                    # テーブルなしページは extract_text() で補完
                    t = page.extract_text() or ""
                    if t.strip():
                        all_lines.append(t)
            if has_any_table and all_lines:
                result = "\n".join(all_lines)
                # P0-1: 質 OK なら返す、NG なら ② にフォールスルー
                if _text_quality_ok(result):
                    return result
    except Exception:
        pass

    # ② PyMuPDF（CJK間スペースなし・高精度テキスト抽出）
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
            # P0-1: 質 OK なら返す、NG なら ③ にフォールスルー
            if _text_quality_ok(text):
                return text
    except Exception:
        pass

    # ③ pdfplumber extract_text()（最終フォールバック）
    # P1-2: 日本語 PDF 向け LAParams。TDnet tdnet_load_parallel で実績あり。
    # char_margin=1.0: 文字間ギャップ許容 (既定 2.0 は CJK で単語分割しがち)
    # word_margin=0.2: 単語境界判定 (既定 0.1 より緩め)
    # line_margin=0.3: 行結合判定 (既定 0.5 より厳しめ、折返し行を誤結合させない)
    _LAPARAMS_JA = {"char_margin": 1.0, "word_margin": 0.2, "line_margin": 0.3}
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes), laparams=_LAPARAMS_JA) as pdf:
            result = "\n".join(p.extract_text() or "" for p in pdf.pages)
            # P0-1: 質 NG なら空文字を返して phase_extract で判別可能にする
            if _text_quality_ok(result):
                return result
            return ""
    except Exception:
        return ""


def _extract_pdf_by_column(
    pdf_bytes: bytes,
    adapter: dict,
    target_month: int,
    target_year: int,
    doc_title: str,
    submission_date: str,
) -> Optional[dict]:
    """PDF テーブルの列ヘッダーから対象月を特定し、その列の値だけを抽出する。

    複数月が横に並ぶ表（12月/1月/2月/...）で正しい月の値を取得するための
    列指定抽出。テーブル構造がない PDF や月列が特定できない場合は None を返す。

    ラベル列の自動検出・親行継承・全トークンマッチに対応:
    - ヘッダー行で最初に「N月」が出現する列を特定 → それより左がラベル列
    - ラベル列のセルが空欄なら直上の行から継承（セル結合対応）
    - adapter key を全トークンに分解し、ラベル列の結合テキストに全て含まれるかで判定
    """
    import io
    import unicodedata as _ud

    def _norm(s: str) -> str:
        """全角→半角、CJK間スペース除去、改行→スペース"""
        s = _ud.normalize("NFKC", s).replace("\u3000", " ").replace("\n", " ").strip()
        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
        s = re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)

    def _key_tokens(key: str) -> list[str]:
        """adapter key からマッチ用トークンを抽出。

        "トライアル 既存店 売上（前年同月比）" → ["トライアル", "既存店", "売上"]
        "全店 店舗数" → ["全店", "店舗数"]
        括弧以降・単位表記を除去。1文字トークンは除外。
        """
        # 括弧以降を除去してからスペース分割
        stripped = re.sub(r"[（(].*$", "", key).strip()
        tokens = []
        for part in stripped.split():
            t = _norm(part)
            if len(t) >= 2:
                tokens.append(t)
        return tokens

    try:
        import pdfplumber
    except ImportError:
        return None

    fields_data: dict[str, float | int] = {}

    # adapter fields のトークンを事前計算
    field_tokens: list[tuple[dict, list[str]]] = []
    for field in adapter.get("fields", []):
        key = field.get("key") or field.get("name", "")
        if not key:
            continue
        tokens = _key_tokens(key)
        if tokens:
            field_tokens.append((field, tokens))

    if not field_tokens:
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                page_text = page.extract_text() or ""

                # ページテキストからセクションラベルを抽出（複数記法対応）
                # 【】■《》<>◇●①②③ 数字. ローマ数字. ≪≫
                section_labels: list[str] = []
                _section_patterns = [
                    r"[【\[]((?:(?![】\]]).)+)[】\]]",       # 【...】[...]
                    r"[《≪]((?:(?![》≫]).)+)[》≫]",         # 《...》≪...≫
                    r"[『]((?:(?![』]).)+)[』]",             # 『...』
                    r"<((?:(?!>).)+)>",                     # <...>
                    r"■\s*(.+?)(?:\n|$)",                   # ■...
                    r"◇\s*(.+?)(?:\n|$)",                   # ◇...
                    r"●\s*(.+?)(?:\n|$)",                   # ●...
                    r"[①-⑳]\s*(.+?)(?:\n|$)",              # ①...
                    r"(?:^|\n)\s*(\d+)\.\s*(.+?)(?:\n|$)",  # 数字. ...
                ]
                page_text_norm = _norm(page_text)
                for pat in _section_patterns:
                    for sm in re.finditer(pat, page_text_norm):
                        label = sm.group(sm.lastindex) if sm.lastindex else sm.group(1)
                        if label and len(label) >= 2:
                            section_labels.append(_norm(label))

                # テーブル直上テキストからセクション推定（【】がない場合の補完）
                table_above_text: list[str] = []
                try:
                    found_tables = page.find_tables()
                    words = page.extract_words()
                    for ft in found_tables:
                        bbox = ft.bbox
                        above = [w for w in words if w["bottom"] < bbox[1] and w["bottom"] > bbox[1] - 40]
                        txt = _norm(" ".join(w["text"] for w in sorted(above, key=lambda w: (w["top"], w["x0"]))))
                        table_above_text.append(txt)
                except Exception:
                    pass

                _skip_tables = set(adapter.get("skip_tables", []))
                for tbl_idx, tbl in enumerate(tables):
                    if not tbl or len(tbl) < 2:
                        continue
                    if tbl_idx in _skip_tables:
                        continue

                    # ヘッダー行を探す（「N月」パターンを含む行）
                    header_row_idx = None
                    target_col = None
                    first_data_col = None  # 最初の月列 = ラベル列の終端+1
                    def _extract_month_num(cell: str) -> int | None:
                        """セル文字列から月番号を抽出。N月/N月度/YYYY年N月度/N 月/'YY/MM 等に対応。"""
                        m = re.search(r"(\d{1,2})\s*月[度]?$", cell)
                        if m:
                            return int(m.group(1))
                        m = re.search(r"\d{4}年\s*(\d{1,2})月", cell)
                        if m:
                            return int(m.group(1))
                        # 'YY/MM 形式（例: '25/07）
                        m = re.search(r"'?\d{2}/(\d{2})", cell.strip())
                        if m:
                            return int(m.group(1))
                        return None

                    for ri, row in enumerate(tbl):
                        cells = [_norm(c or "") for c in row]
                        month_cols = [(i, c) for i, c in enumerate(cells)
                                      if _extract_month_num(c) is not None]
                        if len(month_cols) >= 2:
                            header_row_idx = ri
                            first_data_col = month_cols[0][0]
                            # 対象月の列を探す
                            for ci, cv in month_cols:
                                if _extract_month_num(cv) == target_month:
                                    target_col = ci
                                    break
                            break

                    if header_row_idx is None or target_col is None or first_data_col is None:
                        continue

                    # ラベル列数（月列の左側が全てラベル列）
                    n_label_cols = first_data_col

                    # テーブルセクション（【】→ テーブル直上テキスト の順で推定）
                    tbl_section = ""
                    if tbl_idx < len(section_labels) and section_labels[tbl_idx]:
                        tbl_section = section_labels[tbl_idx]
                    elif tbl_idx < len(table_above_text) and table_above_text[tbl_idx]:
                        tbl_section = table_above_text[tbl_idx]

                    # 親行のラベルを継承するための配列（セル結合対応）
                    inherited: list[str] = [""] * n_label_cols

                    # データ行を処理
                    for row in tbl[header_row_idx + 1:]:
                        if not row:
                            continue
                        cells = [_norm(c or "") for c in row]

                        # ラベル列を更新（空欄なら直上から継承）
                        for ci in range(n_label_cols):
                            cell_val = cells[ci] if ci < len(cells) else ""
                            if cell_val:
                                inherited[ci] = cell_val
                            # else: inherited[ci] は前の行のまま

                        # 対象月列の値を取得（セル結合による列ずれを補正）
                        # ヘッダーの月列位置とデータ行の値位置がずれる場合がある
                        # target_col を中心に前後を探索して最初の非空数値セルを取得
                        val_str = ""
                        for offset in [0, -1, 1, -2, 2]:
                            ci = target_col + offset
                            if 0 <= ci < len(cells):
                                candidate = cells[ci].replace(",", "").strip().rstrip("%")
                                if candidate and re.match(r"[\d.]+", candidate):
                                    val_str = candidate
                                    break
                        if not val_str:
                            continue

                        # ラベルコンテキスト構築: セクション + 継承ラベル全結合
                        label_parts = [tbl_section] + inherited
                        label_context = " ".join(p for p in label_parts if p)
                        label_context_norm = _norm(label_context)

                        # adapter field マッチ: 全トークンが label_context に含まれるか
                        for field, tokens in field_tokens:
                            key = field.get("key") or field.get("name", "")
                            if key in fields_data:
                                continue

                            # row_label_regex がある場合はそちらを優先
                            row_pattern = field.get("row_label_regex", "")
                            matched = False
                            if row_pattern:
                                try:
                                    if (re.search(row_pattern, label_context, re.IGNORECASE)
                                        or re.search(_norm(row_pattern), label_context_norm, re.IGNORECASE)):
                                        matched = True
                                except re.error:
                                    pass

                            # トークンマッチ: key の全トークンが label_context に存在
                            if not matched:
                                if all(t in label_context_norm for t in tokens):
                                    matched = True

                            # プレフィックススキップ: 先頭トークンがラベルに無い場合、
                            # セクション（テーブルのヘッダーやページ【】）にプレフィックスが
                            # 含まれるならば残りトークンでマッチ
                            if not matched and len(tokens) >= 2:
                                prefix = tokens[0]
                                rest = tokens[1:]
                                rest_in_label = all(t in label_context_norm for t in rest)
                                if rest_in_label:
                                    # プレフィックスがこのテーブルのセクションに含まれるか
                                    # （ページ全体ではなくテーブル固有のセクションで判定）
                                    prefix_in_section = prefix in _norm(tbl_section)
                                    if prefix_in_section:
                                        matched = True

                            if matched:
                                val_type = field.get("value_type", "float")
                                # 行種別チェック: 金額行/前年比行の区別
                                # ラベルに「前年比」「増減率」「対前期比」等があれば前年比行
                                _is_ratio_row = bool(re.search(
                                    r"前年|増減率|対前期|同月比|同期比",
                                    label_context_norm,
                                ))
                                # adapter key の括弧内ヒントから期待する行種別を推定
                                _paren = re.search(r"[（(](.+?)[）)]", key)
                                _paren_hint = _norm(_paren.group(1)) if _paren else ""
                                _wants_ratio = bool(re.search(r"前年|同月比|同期比", _paren_hint))
                                _wants_amount = bool(re.search(r"百万円|円|万円|億円", _paren_hint))
                                # value_type からも推定
                                if val_type == "percentage":
                                    _wants_ratio = True
                                # 行種別が期待と不一致ならスキップ
                                if _wants_ratio and not _is_ratio_row and _is_ratio_row is not None:
                                    continue  # 前年比を求めているのに金額行 → スキップ
                                if _wants_amount and _is_ratio_row:
                                    continue  # 金額を求めているのに前年比行 → スキップ
                                try:
                                    if val_type == "integer":
                                        fields_data[key] = int(float(val_str))
                                    else:
                                        fields_data[key] = float(val_str)
                                except (ValueError, TypeError):
                                    pass
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] _extract_pdf_by_column 失敗: %s | doc=%s | pdf_size=%d | target=%04d-%02d",
            e, doc_title, len(pdf_bytes), target_year, target_month,
        )
        return None

    if not fields_data:
        return None

    return {
        "year": target_year,
        "month": target_month,
        "year_month": f"{target_year:04d}-{target_month:02d}",
        "source": "pdf_column",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


def _extract_pdf_single_month(
    pdf_bytes: bytes,
    adapter: dict,
    target_month: int,
    target_year: int,
    doc_title: str,
    submission_date: str,
) -> Optional[dict]:
    """月列がない単月テーブルから値を抽出する。

    テーブル構造例:
      R0: ['', '2026年2月', '前年同月比']     or  ['', '単月', '累計']
      R1: ['取扱高(百万円)', '2,944', '123.4']
      R2: ['売上高(百万円)', '389', '117.4']
    col0=ラベル、col1以降=値。月はヘッダーやタイトルから取得済み。
    """
    import io
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKC", s).replace("\u3000", " ").replace("\n", " ").strip()
        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
        s = re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)

    def _key_tokens(key: str) -> list[str]:
        stripped = re.sub(r"[（(].*$", "", key).strip()
        return [_norm(p) for p in stripped.split() if len(_norm(p)) >= 2]

    try:
        import pdfplumber
    except ImportError:
        return None

    fields_data: dict[str, float | int] = {}
    _skip_tables = set(adapter.get("skip_tables", []))

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                page_text = page.extract_text() or ""
                page_text_norm = _norm(page_text)

                for tbl_idx, tbl in enumerate(tables):
                    if not tbl or len(tbl) < 2:
                        continue
                    if tbl_idx in _skip_tables:
                        continue

                    # 月列が2つ以上あるテーブルはスキップ（_extract_pdf_by_column 向け）
                    has_multi_month = False
                    for row in tbl[:4]:
                        cells = [_norm(c or "") for c in row]
                        mc = [c for c in cells if re.search(r"(\d{1,2})月[度]?$", c)
                              or re.search(r"\d{4}年\s*(\d{1,2})月", c)]
                        if len(mc) >= 2:
                            has_multi_month = True
                            break
                    if has_multi_month:
                        continue

                    # 当月データ列を推定（col1 = 当期値が多い）
                    # ヘッダーに「当年度」「当期」「今期」等があれば優先
                    data_col = 1  # デフォルト
                    for row in tbl[:3]:
                        cells = [_norm(c or "") for c in row]
                        for ci, c in enumerate(cells):
                            if ci >= 1 and re.search(r"当[年期]|今期|当月|単月", c):
                                data_col = ci
                                break
                            # 「YYYY年N月」形式のヘッダーがあればその列
                            m = re.search(r"\d{4}年\s*\d{1,2}月", c)
                            if m and ci >= 1:
                                data_col = ci
                                break

                    # --- 前年同月比 列の検出 ---
                    # ヘッダー行に「前年同月比」「前年比」等があればその列を優先
                    ratio_col = None
                    for row in tbl[:3]:
                        cells = [_norm(c or "") for c in row]
                        for ci, c in enumerate(cells):
                            if ci >= 1 and re.search(r"前年同月比|前年比|YoY|対前年", c):
                                ratio_col = ci
                                break
                        if ratio_col is not None:
                            break

                    # 各行をスキャンして adapter field にマッチ
                    for field in adapter.get("fields", []):
                        key = field.get("key") or field.get("name", "")
                        if not key or key in fields_data:
                            continue
                        tokens = _key_tokens(key)
                        rlr = field.get("row_label_regex", "")
                        val_type = field.get("value_type", "float")

                        # 前年比フィールドなら ratio_col を優先
                        paren = re.search(r"[（(](.+?)[）)]", key)
                        paren_hint = _norm(paren.group(1)) if paren else ""
                        is_ratio = any(w in paren_hint for w in ["前年", "伸び", "比"])
                        use_col = ratio_col if is_ratio and ratio_col is not None else data_col

                        for row in tbl[1:]:  # ヘッダー行スキップ
                            if not row:
                                continue
                            cells = [_norm(c or "") for c in row]

                            # 全列からラベルを探す（col0だけでなく）
                            row_label_parts: list[str] = []
                            for ci in range(min(3, len(cells))):
                                if cells[ci]:
                                    row_label_parts.append(cells[ci])
                            row_label = " ".join(row_label_parts)
                            if not row_label:
                                continue

                            label_norm = _norm(row_label)

                            # row_label_regex マッチ（優先）
                            matched = False
                            if rlr:
                                try:
                                    if re.search(rlr, label_norm, re.IGNORECASE):
                                        matched = True
                                except re.error:
                                    pass

                            # トークンマッチ（フォールバック）
                            if not matched and tokens:
                                if all(t in label_norm for t in tokens):
                                    matched = True
                                elif len(tokens) >= 2 and all(t in label_norm for t in tokens[1:]) and tokens[0] in page_text_norm:
                                    matched = True

                            if not matched:
                                continue

                            # 値取得（use_col を優先、なければ隣接列を探索）
                            for try_col in [use_col, use_col - 1, use_col + 1, data_col]:
                                if try_col < 0 or try_col >= len(cells):
                                    continue
                                raw = cells[try_col].replace(",", "").strip()
                                is_negative = bool(re.search(r"[▲△]", raw))
                                raw_clean = re.sub(r"[▲△\s％%]", "", raw).strip()
                                nums = re.findall(r"[\d,]+\.?\d*", raw_clean)
                                if nums:
                                    val_str = nums[-1].replace(",", "")
                                    try:
                                        v = float(val_str)
                                        if val_type == "integer":
                                            v = int(v)
                                        fields_data[key] = -v if is_negative else v
                                    except (ValueError, TypeError):
                                        pass
                                    break
                            break  # 最初のマッチで終了
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] _extract_pdf_single_month 失敗: %s | doc=%s | pdf_size=%d | target=%04d-%02d",
            e, doc_title, len(pdf_bytes), target_year, target_month,
        )
        return None

    if not fields_data:
        return None

    return {
        "year": target_year,
        "month": target_month,
        "year_month": f"{target_year:04d}-{target_month:02d}",
        "source": "pdf_single_month",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


def _extract_pdf_by_row(
    pdf_bytes: bytes,
    adapter: dict,
    target_month: int,
    target_year: int,
    doc_title: str,
    submission_date: str,
) -> Optional[dict]:
    """月が行方向にあるテーブルから対象月の行を特定し値を抽出する。

    adapter に month_direction: "row" がある場合に使用。
    テーブル構造:
      R0: ['', 'グループA', ..., 'グループB', ...]   ← セクションヘッダー
      R1: ['', '売上高', '', '客数', ...]              ← メトリクスヘッダー
      R2: ['', '金額', '伸び率', '人数', '伸び率', ...] ← サブヘッダー
      R3: ['2025年4月', '9,105', '116.0', ...]         ← データ行（月=行ラベル）

    セクション区別（9005 輸送人員/運賃収入 等）:
      テーブル直上のテキストからセクション名を取得し、key トークンとマッチさせる。
    """
    import io
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKC", s).replace("\u3000", " ").replace("\n", " ").strip()
        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
        s = re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)

    try:
        import pdfplumber
    except ImportError:
        return None

    fields_data: dict[str, float | int] = {}

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue

                # --- テーブル直上テキストをセクション識別用に取得 ---
                # crop() でテーブル間のテキストを正確に取得
                table_section_texts: list[str] = []
                try:
                    found_tables = page.find_tables()
                    pb = page.bbox  # (x0, top, x1, bottom) — マイナスマージン対応
                    for ft_idx, ft in enumerate(found_tables):
                        bbox = ft.bbox
                        top_y = found_tables[ft_idx - 1].bbox[3] if ft_idx > 0 else pb[1]
                        bottom_y = bbox[1]
                        if bottom_y > top_y + 2:
                            cropped = page.crop((pb[0], top_y, pb[2], bottom_y))
                            txt = _norm(cropped.extract_text() or "")
                        else:
                            txt = ""
                        table_section_texts.append(txt)
                except Exception:
                    pass
                while len(table_section_texts) < len(tables):
                    table_section_texts.append("")

                for tbl_idx, tbl in enumerate(tables):
                    if not tbl or len(tbl) < 3:
                        continue
                    ncols = len(tbl[0])
                    section_text = table_section_texts[tbl_idx]

                    # 対象月の行を探す（col0 に「N月」「YYYY年N月」）
                    target_row_idx = None
                    month_pat = re.compile(
                        rf"(?:^|\D){target_month}月|^{target_month}月"
                    )
                    for ri, row in enumerate(tbl):
                        cell0 = _norm(row[0] or "") if row else ""
                        if month_pat.search(cell0):
                            # 四半期計や累計行は除外
                            if re.search(r"四半期|累計|合計|上期|下期", cell0):
                                continue
                            target_row_idx = ri
                            # 同じ月が複数ある場合は最後（最新期）を採用
                    if target_row_idx is None:
                        continue

                    target_cells = [_norm(c or "") for c in tbl[target_row_idx]]

                    # ヘッダー行を構築（target_row_idx より前の非データ行）
                    header_rows: list[list[str]] = []
                    for ri in range(target_row_idx):
                        cells = [_norm(c or "") for c in tbl[ri]]
                        non_empty = [c for c in cells[1:] if c]
                        # 数値ばかりの行はスキップ（△/▲記号+スペース含む）
                        if non_empty and all(
                            re.match(r"^[▲△\-+]?\s*[\d,]+\.?\d*\s*[%％]?$", c) for c in non_empty
                        ):
                            continue
                        if cells[0] and re.search(r"\d{1,2}月", cells[0]):
                            continue
                        header_rows.append(cells)

                    if not header_rows:
                        continue

                    # 各列のコンテキストを構築（セクションテキスト + ヘッダー行縦結合）
                    col_contexts: list[str] = [""] * ncols
                    # 最深ヘッダー（直接セルの値）: 前年比/YoY 列の判別用
                    col_deepest: list[str] = [""] * ncols
                    for ci in range(1, ncols):
                        parts = []
                        if section_text:
                            parts.append(section_text)
                        deepest_val = ""
                        for hr in header_rows:
                            val = hr[ci] if ci < len(hr) else ""
                            if not val:
                                for lci in range(ci - 1, 0, -1):
                                    lval = hr[lci] if lci < len(hr) else ""
                                    if lval:
                                        val = lval
                                        break
                            if val and val not in parts:
                                parts.append(val)
                            # 最深は直接セルのみ（継承前）
                            direct = hr[ci] if ci < len(hr) else ""
                            if direct:
                                deepest_val = _norm(direct)
                        col_contexts[ci] = " ".join(parts)
                        col_deepest[ci] = deepest_val

                    # adapter field マッチ
                    for field in adapter.get("fields", []):
                        key = field.get("key") or field.get("name", "")
                        if not key or key in fields_data:
                            continue

                        rlr = field.get("row_label_regex", "")
                        key_clean = re.sub(r"[（(].*$", "", key).strip()
                        tokens = [_norm(p) for p in key_clean.split() if len(_norm(p)) >= 2]
                        paren = re.search(r"[（(](.+?)[）)]", key)
                        paren_hint = _norm(paren.group(1)) if paren else ""
                        is_ratio = any(w in paren_hint for w in ["前年", "伸び", "比"])

                        for ci in range(1, ncols):
                            ctx = _norm(col_contexts[ci])
                            if not ctx:
                                continue

                            # --- マッチ判定 ---
                            if rlr:
                                # row_label_regex で列ヘッダーマッチ（優先）
                                try:
                                    if not re.search(rlr, ctx, re.IGNORECASE):
                                        continue
                                except re.error:
                                    continue
                                # key tokens でセクション区別（複数テーブルがある場合のみ必須）
                                if tokens and len(tables) > 1 and not all(t in ctx for t in tokens):
                                    continue
                            else:
                                if not all(t in ctx for t in tokens):
                                    continue

                            # --- 前年比列の絞り込み ---
                            # 最終ヘッダー行（最も具体的なサブヘッダー）の直接セルで判定
                            # セクションテキストに前年比マーカーがある場合はスキップ（全列が前年比の場合）
                            _ratio_kw = r"前年比|伸び率|YoY|Change|増減|対前年|前年同月比"
                            _section_has_ratio = bool(re.search(_ratio_kw, section_text, re.IGNORECASE))
                            if is_ratio and header_rows and not _section_has_ratio:
                                last_hr = header_rows[-1]
                                direct_cell = _norm(last_hr[ci]) if ci < len(last_hr) and last_hr[ci] else ""
                                if not re.search(_ratio_kw, direct_cell, re.IGNORECASE):
                                    continue

                            # --- 値取得（隣接列フォールバック付き）---
                            extracted = False
                            for try_ci in [ci, ci - 1, ci + 1]:
                                if try_ci < 1 or try_ci >= len(target_cells):
                                    continue
                                raw = target_cells[try_ci].replace(",", "").strip()
                                is_neg = bool(re.search(r"[▲△]", raw))
                                raw = re.sub(r"[▲△\s％%]", "", raw)
                                if raw and re.match(r"[\d.]+", raw):
                                    val_type = field.get("value_type", "float")
                                    try:
                                        v = float(raw)
                                        if val_type == "integer":
                                            v = int(v)
                                        fields_data[key] = -v if is_neg else v
                                        extracted = True
                                    except (ValueError, TypeError):
                                        pass
                                    break
                            if extracted:
                                break
                    # テーブル跨ぎ: break しない → 次テーブルで残りフィールドを拾う
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] _extract_pdf_by_row 失敗: %s | doc=%s | pdf_size=%d | target=%04d-%02d",
            e, doc_title, len(pdf_bytes), target_year, target_month,
        )
        return None

    if not fields_data:
        return None

    return {
        "year": target_year,
        "month": target_month,
        "year_month": f"{target_year:04d}-{target_month:02d}",
        "source": "pdf_row",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


def _extract_pdf_all_months(
    pdf_bytes: bytes,
    adapter: dict,
    doc_title: str,
    submission_date: str,
    since: int = 2020,
) -> list[dict]:
    """PDF テーブルのヘッダーにある全月分を抽出して返す。

    overwrite_past_months: true のアダプター用。各月列について
    _extract_pdf_by_column と同じロジックで値を取得し、
    年はタイトルや提出日から推定する。
    """
    import io

    # まず _parse_year_month でタイトルから基準年月を取得
    ym = _parse_year_month(adapter, doc_title, submission_date)
    if not ym:
        return []
    base_year, base_month = ym

    try:
        import pdfplumber
    except ImportError:
        return []

    # テーブルから全月ヘッダーを収集
    month_cols: list[tuple[int, int]] = []  # [(col_index, month_number), ...]
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables():
                    if not tbl or len(tbl) < 2:
                        continue
                    import unicodedata as _ud
                    def _n(s: str) -> str:
                        s = _ud.normalize("NFKC", s).replace("\u3000", " ").replace("\n", " ").strip()
                        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
                        s = re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
                        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
                    def _row_month(c: str) -> int | None:
                        m = re.match(r"(\d{1,2})\s*月[度]?$", c)
                        if m:
                            return int(m.group(1))
                        m = re.search(r"\d{4}年\s*(\d{1,2})月", c)
                        if m:
                            return int(m.group(1))
                        m = re.search(r"'?\d{2}/(\d{2})", c.strip())
                        if m:
                            return int(m.group(1))
                        return None
                    for row in tbl:
                        cells = [_n(c or "") for c in row]
                        mc = [(i, _row_month(c)) for i, c in enumerate(cells) if _row_month(c) is not None]
                        if len(mc) >= 2:
                            month_cols = mc
                            break
                    if month_cols:
                        break
                if month_cols:
                    break
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] _extract_pdf_all_months 失敗: %s | doc=%s | pdf_size=%d",
            e, doc_title, len(pdf_bytes),
        )
        return []

    if not month_cols:
        return []

    # 各月の年を推定（基準年月から逆算）
    # テーブルの月列が 12,1,2 のように年をまたぐ場合、base_month より大きい月は前年
    results = []
    for _, month_num in month_cols:
        if month_num > base_month:
            year = base_year - 1
        else:
            year = base_year
        if year < since:
            continue
        rec = _extract_pdf_by_column(pdf_bytes, adapter, month_num, year, doc_title, submission_date)
        if rec:
            results.append(rec)

    return results


def extract_from_tdnet_text(
    full_text: str,
    adapter: dict,
    doc_title: str,
    submission_date: str,
) -> Optional[dict]:
    """TDNET テキストとアダプターを使ってメトリクス値を抽出"""
    ym = _parse_year_month(adapter, doc_title, submission_date)
    if not ym:
        return None
    report_year, report_month = ym

    # テキストを行に分割（改行 or 複数スペース区切り）
    # 正規表現の壊滅的バックトラックを防ぐため先頭20000文字に制限
    text = full_text[:20000].replace("\u3000", " ")  # 全角スペース正規化
    # 全角ASCII → 半角正規化（（百万円） → (百万円)、２，６６７ → 2,667 等）
    _FW2HW = str.maketrans(
        "（）！＂＃＄％＆＇＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～"
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "()!\"#$%&'*+,-./:;<=>?@[\\]^_`{|}~"
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz",
    )
    text = text.translate(_FW2HW)
    # OCR で CJK 文字間にスペースが入るケースを正規化（「全 社 売 上」→「全社売上」）
    # 元テキスト（lines）と de-spaced テキスト（lines_dense）の両方を保持
    def _dense(t: str) -> str:
        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
        t = re.sub(rf"({cjk}) ({cjk})", r"\1\2", t)
        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", t)  # 2パス
    lines = text.split("\n")
    if len(lines) <= 3:
        lines = re.split(r"  {2,}", text)
    lines_dense = [_dense(l) for l in lines]

    def _store_val(fields_data: dict, key: str, val_str: str, val_type: str) -> None:
        """文字列値を型変換して fields_data に格納するヘルパー"""
        v = val_str.replace(",", "").strip()
        if val_type == "percentage":
            fields_data[key] = float(v)
        elif val_type == "integer":
            fields_data[key] = int(v)
        else:
            fields_data[key] = float(v)

    fields_data: dict[str, float | int] = {}
    _matched_fields: list[str] = []
    _failed_fields: list[str] = []
    for field in adapter.get("fields", []):
        val_type = field.get("value_type", "float")
        key = field.get("key") or field.get("name", "")
        if not key:
            continue

        # ---------------------------------------------------------------
        # 旧フォーマット: "regex" + "group" キーを持つフィールド
        # full_text に対して DOTALL で直接マッチし group(N) を抽出
        # ---------------------------------------------------------------
        if "regex" in field and "row_label_regex" not in field:
            pattern = field.get("regex", "")
            grp = field.get("group", 1)
            use_last = field.get("use_last_number", False)
            if not pattern:
                continue
            # {month_num} / {year} プレースホルダーを実際の値に置換
            pattern = pattern.replace("{month_num}", str(report_month)).replace("{year}", str(report_year))
            try:
                re.compile(pattern, re.IGNORECASE | re.DOTALL)
            except re.error:
                continue
            try:
                m = _safe_re_search(pattern, text, re.IGNORECASE | re.DOTALL)
                if m:
                    if use_last:
                        # グループ or マッチ後テキストから全数値を取得して最後の値を返す
                        if grp > 0 and m.lastindex and m.lastindex >= grp:
                            segment = m.group(grp)
                        else:
                            segment = text[m.end():m.end() + 500]
                        nums = [v.replace(",", "") for v in re.findall(r"\d{1,3}(?:,\d{3})+", segment)]
                        if not nums:
                            nums = re.findall(r"\d{3,}", segment)
                        if nums:
                            _store_val(fields_data, key, nums[-1], val_type)
                    else:
                        val_str = m.group(grp).replace(",", "").strip().rstrip("%")
                        _store_val(fields_data, key, val_str, val_type)
            except Exception:
                pass
            continue

        # ---------------------------------------------------------------
        # 標準フォーマット: row_label_regex
        # ---------------------------------------------------------------
        # sections サポート（2735 のような上/下半期で列構造が変わる銘柄用）:
        #   field に "sections" があれば、report_month に応じて最初にマッチする section の
        #   row_label_regex / column_map / match_occurrence を field-level 設定に override する。
        sections = field.get("sections", [])
        section_override: dict = {}
        for sec in sections:
            if report_month in sec.get("months", []) or str(report_month) in [str(m) for m in sec.get("months", [])]:
                section_override = sec
                break

        row_pattern = section_override.get("row_label_regex") or field.get("row_label_regex", "")
        if not row_pattern:
            continue
        # {month_num} / {year} / {fy_month_idx} / {col_idx} プレースホルダーを実際の値に置換
        # fy_month_idx: 会計年度始まり月から数えた月インデックス (1-12)
        #   例: fiscal_year_start_month=4, report_month=3 → ((3-4)%12)+1 = 12
        # col_idx: section.column_map または adapter.column_map[report_month] で明示指定された物理列 index。
        #   7918 や 2735 のように表内に「上期/下期/累計」等が挟まる場合に利用。未指定時は fy_idx にフォールバック。
        fy_start = adapter.get("fiscal_year_start_month", 1)
        fy_idx = ((report_month - fy_start) % 12) + 1
        col_map = section_override.get("column_map") or adapter.get("column_map") or {}
        col_idx = col_map.get(str(report_month), col_map.get(report_month, fy_idx))
        row_pattern = (
            row_pattern.replace("{month_num}", str(report_month))
                       .replace("{year}", str(report_year))
                       .replace("{fy_month_idx}", str(fy_idx))
                       .replace("{col_idx}", str(col_idx))
        )
        # match_occurrence: 複数マッチのうち N 番目を採用（1-indexed）
        match_occurrence = section_override.get("match_occurrence") or field.get("match_occurrence", 1)

        # ^ アンカーを除去したパターンでフォールバック検索できるよう準備
        row_pattern_no_anchor = re.sub(r"^\^", "", row_pattern).rstrip("$")

        # 無効な regex（可変幅 lookbehind 等）は事前にスキップ
        try:
            re.compile(row_pattern, re.IGNORECASE)
        except re.error:
            continue

        # row_label_regex 自体にキャプチャグループが含まれる場合の検出
        has_capture_group = bool(re.search(r"(?<!\\)\((?!\?)", row_pattern))

        # パターンの CJK 部分も de-space（OCR テキストに対応）
        row_pattern_dense = _dense(row_pattern_no_anchor)

        grp_raw = field.get("group", 1)
        if isinstance(grp_raw, str):
            grp = int(
                grp_raw.replace("{col_idx}", str(col_idx))
                       .replace("{fy_month_idx}", str(fy_idx))
                       .replace("{month_num}", str(report_month))
            )
        else:
            grp = grp_raw
        use_last = field.get("use_last_number", False)

        def _extract_from_match(m, src_line: str) -> bool:
            """マッチオブジェクトから値を取り出して fields_data に格納。成功時 True。"""
            if has_capture_group and m.groups():
                # group パラメータ対応: 指定グループ番号が範囲外ならグループ1にフォールバック
                actual_grp = grp if (m.lastindex and m.lastindex >= grp) else 1
                segment = (m.group(actual_grp) or "")
                if use_last:
                    # use_last_number: キャプチャ内の全数値から最後の値を取得
                    nums = [v.replace(",", "") for v in re.findall(r"\d{1,3}(?:,\d{3})+", segment)]
                    if not nums:
                        nums = re.findall(r"[\d.]+", segment)
                    if nums:
                        try:
                            _store_val(fields_data, key, nums[-1], val_type)
                            return True
                        except (ValueError, TypeError):
                            pass
                else:
                    val_str = segment.replace(",", "").strip().rstrip("%")
                    if val_str and re.match(r"[\d.△▲+-]+", val_str):
                        val_str = re.sub(r"[△▲]", "", val_str)
                        try:
                            _store_val(fields_data, key, val_str, val_type)
                            return True
                        except (ValueError, TypeError):
                            pass

            rest = src_line[m.end():]
            if val_type == "percentage":
                vals = re.findall(r"(\d+\.?\d*)\s*%", rest)
                if not vals:
                    vals = re.findall(r"(\d{2,3}\.\d)", rest)
                if not vals:
                    vals = re.findall(r"(\d+\.?\d*)\s*%", src_line)
            elif val_type == "integer":
                comma_vals = [v.replace(",", "") for v in re.findall(r"\d{1,3}(?:,\d{3})+", rest)]
                vals = comma_vals if comma_vals else re.findall(r"(\d{3,})", rest)
            else:
                vals = re.findall(r"(\d+\.?\d*)", rest)

            if vals:
                v = vals[-1] if use_last else vals[0]
                if val_type == "percentage":
                    fields_data[key] = float(v)
                elif val_type == "integer":
                    fields_data[key] = int(v)
                else:
                    fields_data[key] = float(v)
                return True
            return False

        try:
            matched = False
            # match_occurrence >= 2: 全テキスト DOTALL で finditer → N 番目のマッチを採用
            # （2735 のように同じラベルが複数回現れる表で、上/下半期を区別するために使う）
            try:
                _mo = int(match_occurrence)
            except (TypeError, ValueError):
                _mo = 1
            if _mo >= 2:
                all_matches = list(re.finditer(row_pattern, text, re.IGNORECASE | re.DOTALL))
                if len(all_matches) >= _mo:
                    m = all_matches[_mo - 1]
                    if _extract_from_match(m, m.group(0)):
                        matched = True

            for li, (line, line_d) in enumerate(zip(lines, lines_dense)):
                if matched:
                    break
                # 優先順: 元テキスト×元パターン → 元テキスト×アンカー除去 → dense×dense
                m = re.search(row_pattern, line, re.IGNORECASE)
                if not m and row_pattern_no_anchor != row_pattern:
                    m = re.search(row_pattern_no_anchor, line, re.IGNORECASE)
                if not m:
                    m = re.search(row_pattern_dense, line_d, re.IGNORECASE)
                    if m:
                        line = line_d
                if m:
                    if _extract_from_match(m, line):
                        matched = True
                        break
                    # ラベル行に値がない場合: 次の1-3行を結合して再試行
                    # （非TDnet PDFではラベルと値が別行になるケースが多い）
                    for look_ahead in range(1, 4):
                        if li + look_ahead < len(lines):
                            combined = line + " " + " ".join(lines[li+1:li+1+look_ahead])
                            m2 = re.search(row_pattern, combined, re.IGNORECASE)
                            if not m2:
                                m2 = re.search(row_pattern_no_anchor, combined, re.IGNORECASE)
                            if m2 and _extract_from_match(m2, combined):
                                matched = True
                                break
                    if matched:
                        break

            # per-line でマッチしなかった場合: DOTALL で全テキスト検索
            # .*? または [\s\S] を含むパターンは複数行にまたがる可能性がある
            _needs_dotall = ".*?" in row_pattern_no_anchor or r"[\s\S]" in row_pattern_no_anchor
            if not matched and _needs_dotall:
                m = _safe_re_search(row_pattern_no_anchor, text, re.IGNORECASE | re.DOTALL)
                if not m:
                    m = _safe_re_search(row_pattern_dense, _dense(text), re.IGNORECASE | re.DOTALL)
                if m:
                    _extract_from_match(m, m.group(0))
        except Exception as e:
            import logging as _log
            _log.getLogger("extract_monthly_data").warning(
                "[%s] extract_from_tdnet_text フィールド失敗: %s | field=%s | pattern_len=%d | text_len=%d",
                adapter.get("ticker", "?"), e, key,
                len(row_pattern) if row_pattern else 0, len(text),
            )
        if key in fields_data:
            _matched_fields.append(key)
        elif key:
            _failed_fields.append(key)

    _ticker = adapter.get("ticker", "?")
    _total = len(_matched_fields) + len(_failed_fields)
    _log = logging.getLogger("extract_monthly_data")
    if _total > 0:
        _log.info(
            "[%s] field抽出: matched=%d/%d fields=[%s] | doc=%s",
            _ticker, len(_matched_fields), _total,
            ",".join(_matched_fields) if _matched_fields else "(none)", doc_title[:60],
        )
    if _failed_fields:
        _log.warning(
            "[%s] field未マッチ: [%s] | text_len=%d",
            _ticker, ",".join(_failed_fields), len(text),
        )

    if not fields_data:
        return None

    return {
        "year": report_year,
        "month": report_month,
        "year_month": f"{report_year:04d}-{report_month:02d}",
        "source": "tdnet",
        "doc_title": doc_title,
        "submission_date": str(submission_date),
        "fields": fields_data,
    }


# ====================================================
# データ抽出: XLSX/CSV
# ====================================================

def extract_from_xlsx(
    file_path: Path,
    adapter: dict,
    year_month_hint: Optional[str] = None,
) -> list[dict]:
    """XLSX/CSV ファイルとアダプターを使ってメトリクス値を抽出（複数月対応）"""
    try:
        sheet = adapter.get("sheet_name", 0)
        if file_path.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, sheet_name=sheet, header=None)
        else:
            csv_enc = adapter.get("encoding", "utf-8-sig")
            df = pd.read_csv(file_path, header=None, encoding=csv_enc)
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] extract_from_xlsx 読み込み失敗: %s | path=%s", e, file_path,
        )
        return []

    label_col = adapter.get("label_col_index", 0)
    label_col_count = adapter.get("label_col_count", 1)  # 複数ラベル列を結合
    data_start = adapter.get("data_start_row", 1)
    records = []

    # ラベル列から各フィールドの行を特定（複数列結合対応）
    if label_col_count > 1:
        # 複数列を結合してラベル文字列を作成（セクションヘッダーを継承）
        label_parts = []
        section_label = ""
        for ri in range(data_start, len(df)):
            parts = []
            for ci in range(label_col, label_col + label_col_count):
                if ci < len(df.columns):
                    val = str(df.iloc[ri, ci]) if pd.notna(df.iloc[ri, ci]) else ""
                    parts.append(val.strip())
            col0 = parts[0] if parts else ""
            if col0 and col0 != "nan":
                section_label = col0
            elif section_label:
                parts[0] = section_label
            label_parts.append(" ".join(p for p in parts if p and p != "nan"))
        label_col_series = pd.Series(label_parts, index=range(len(label_parts)))
    else:
        label_col_series = df.iloc[data_start:, label_col].astype(str).fillna("")

    for field in adapter.get("fields", []):
        row_pattern = field.get("row_label_regex", "")
        val_type = field.get("value_type", "float")
        key = field.get("key", "")
        if not row_pattern or not key:
            continue

        for idx, label in enumerate(label_col_series):
            if re.search(row_pattern, label, re.IGNORECASE):
                row_idx = data_start + idx
                # その行の数値を全列取得（ラベル列の次から）
                data_col_start = label_col + label_col_count
                row_vals = df.iloc[row_idx, data_col_start:]
                for col_offset, v in enumerate(row_vals):
                    try:
                        if isinstance(v, str):
                            v = v.replace("%", "").replace(",", "").strip()
                        num = float(v)
                        if val_type == "integer":
                            num = int(num)
                        # year_month はファイル名や別の行から推定（TODO: 高度化）
                        ym = year_month_hint or "unknown"
                        # 既存レコードに追加 or 新規作成
                        while len(records) <= col_offset:
                            records.append({"year_month": ym, "source": "download", "fields": {}})
                        records[col_offset]["fields"][key] = num
                    except (ValueError, TypeError):
                        pass
                break

    return [r for r in records if r["fields"]]


# ====================================================
# データ抽出: HTML テーブル
# ====================================================

def extract_from_html(
    file_path: Path,
    adapter: dict,
    year_month_hint: Optional[str] = None,
) -> list[dict]:
    """HTML ファイルのテーブルとアダプターを使ってメトリクス値を抽出（複数月対応）.

    テーブル方向を自動検出:
      A) 月がカラム方向（2664 パターン）: R0=['', '4月', '5月', ...], R1=['全店', '3.1', ...]
      B) 月が行方向（8698 パターン）: R0=['', 'col_header1', ...], R1=['2026年3月', val, ...]
      C) フォールバック: 従来の row_label_regex + 順次カラム展開
    """
    import unicodedata as _ud

    def _norm(s: str) -> str:
        s = _ud.normalize("NFKC", s).replace("\u3000", " ").strip()
        cjk = r"[\u3000-\u9fff\u2e80-\u2fdf\uff00-\uffef]"
        s = re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)
        return re.sub(rf"({cjk}) ({cjk})", r"\1\2", s)

    def _parse_val(s: str) -> Optional[float]:
        """△/▲→負数, カンマ/％除去して数値化。"""
        s = _norm(str(s)).replace(",", "").replace("％", "").replace("%", "").strip()
        is_neg = bool(re.search(r"[▲△]", s))
        s = re.sub(r"[▲△\s]", "", s)
        if not s or not re.match(r"[\d.]+", s):
            return None
        try:
            v = float(s)
            return -v if is_neg else v
        except ValueError:
            return None

    try:
        with open(file_path, encoding="utf-8") as f:
            html = f.read()
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        import logging as _log
        _log.getLogger("extract_monthly_data").warning(
            "[?] extract_from_html 読み込み失敗: %s | path=%s", e, file_path,
        )
        return []

    # table_selector があればそのテーブル、なければ月次キーワードを含む最初のテーブル
    table_selector = adapter.get("table_selector")
    all_tables: list = []
    if table_selector:
        t = soup.select_one(table_selector)
        if t:
            all_tables = [t]
    if not all_tables:
        monthly_re = re.compile(
            r"月次|月度|monthly|売上速報|売上高|月別|受注速報|受注実績|販売台数"
            r"|輸送実績|旅客数|約定代金|売買代金|客数|客単価"
            r"|全店|既存店|店舗数|前年比|前年同月比",
            re.IGNORECASE,
        )
        for tbl in soup.find_all("table"):
            if monthly_re.search(tbl.get_text()):
                all_tables.append(tbl)

    # フォールバック: 月カラム（N月×3+）を持つテーブルを探す
    if not all_tables:
        for tbl in soup.find_all("table"):
            rows = tbl.find_all("tr")
            if len(rows) < 2:
                continue
            for tr in rows[:3]:
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                mc = sum(1 for c in cells if re.search(r"^\d{1,2}月$", _norm(c)))
                if mc >= 3:
                    all_tables.append(tbl)
                    break

    if not all_tables:
        return []

    fields = adapter.get("fields", [])
    all_records: list[dict] = []

    # table_index: 特定テーブルのみ処理（巨大R1等で誤検出する場合）
    target_table_index = adapter.get("table_index")
    for ti, table in enumerate(all_tables):
        if target_table_index is not None and ti != target_table_index:
            continue
        rows_data: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows_data.append(cells)
        if not rows_data or len(rows_data) < 2:
            continue

        # 各テーブル直前の見出し（h2-h5, caption）から年度を抽出してテーブル固有のヒントにする
        per_table_fy_year: Optional[int] = None
        try:
            # 直前の見出し/caption等を探索（最大5要素前まで）
            check = table
            for _ in range(8):
                check = check.find_previous(["h1", "h2", "h3", "h4", "h5", "h6", "caption", "p", "div"])
                if check is None:
                    break
                htxt = check.get_text(strip=True)
                m_nendo = re.search(r"(\d{4})\s*年度", htxt)
                if m_nendo:
                    per_table_fy_year = int(m_nendo.group(1)) + 1  # 2025年度 → 決算期 2026年
                    break
                m_fy = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月期", htxt)
                if m_fy:
                    per_table_fy_year = int(m_fy.group(1))
                    break
                m_year = re.search(r"(\d{4})\s*年", htxt)
                if m_year and len(htxt) < 30:
                    # "2025年" のみの短い見出しを拾う（長文は誤検出防止）
                    per_table_fy_year = int(m_year.group(1)) + 1
                    break
        except Exception:
            pass

        # --- 方向検出 ---
        # B) col0 に "YYYY年N月" パターンが3行以上 → 月が行方向
        month_row_count = sum(
            1 for row in rows_data[1:]
            if row and re.search(r"\d{4}年\s*\d{1,2}月", _norm(row[0]))
        )

        # B') col0 に "N月" (年なし) パターンが3行以上 → 月が行方向（年はページ文脈から推定）
        bare_month_row_count = sum(
            1 for row in rows_data[1:]
            if row and re.match(r"^\d{1,2}月$", _norm(row[0]))
        )

        # A) ヘッダー行に "N月" が3列以上 → 月がカラム方向
        month_col_count = 0
        header_month_ri = -1
        for ri, row in enumerate(rows_data[:3]):
            mc = sum(1 for c in row if re.search(r"^\d{1,2}月$", _norm(c)))
            if mc >= 3:
                month_col_count = mc
                header_month_ri = ri
                break

        if month_row_count >= 3 or (bare_month_row_count >= 3 and month_col_count < 3):
            # ========== B) 月が行方向（8698 パターン） ==========
            # pandas.read_html で rowspan/colspan を展開し、多層ヘッダをフラット化
            # （9900 等の「全店/既存店×売上高/客数/客単価」のようなネスト構造向け）
            flat_headers: list[str] = []
            flat_rows_data: list[list[str]] = []
            try:
                import io as _io
                _dfs = pd.read_html(_io.StringIO(str(table)))
                if _dfs:
                    _df = _dfs[0]
                    # 多層カラムをスペース連結してフラット化
                    if isinstance(_df.columns, pd.MultiIndex):
                        flat_headers = []
                        for col in _df.columns:
                            parts = [str(p).strip() for p in col if str(p).strip() and not str(p).startswith("Unnamed")]
                            flat_headers.append(" ".join(parts))
                    else:
                        flat_headers = [str(c).strip() for c in _df.columns]
                    # データ行に先頭カラムを含めて rows_data と同じ形式に
                    flat_rows_data = [flat_headers] + _df.astype(str).values.tolist()
            except Exception:
                pass

            # pandas 版を優先、失敗時は従来の find_all('tr') ベース rows_data
            if flat_rows_data and len(flat_rows_data) >= 2:
                header_row = flat_headers
                header_ri = 0
                # rows_data を pandas ベースに置き換え
                rows_data = flat_rows_data
            else:
                header_row = []
                header_ri = 0
                for ri, row in enumerate(rows_data[:5]):
                    non_empty = [c for c in row[1:] if _norm(c) and len(_norm(c)) > 2]
                    if len(non_empty) >= 2:
                        header_row = row
                        header_ri = ri
                        break
            if not header_row:
                continue

            headers_norm = [_norm(c) for c in header_row]

            # フィールド→カラム番号マッピング
            field_col_map: list[tuple[dict, int]] = []
            for field in fields:
                rlr = field.get("row_label_regex", "")
                if not rlr:
                    continue
                for ci, hdr in enumerate(headers_norm):
                    if ci == 0 or not hdr:
                        continue
                    try:
                        if re.search(rlr, hdr, re.IGNORECASE):
                            field_col_map.append((field, ci))
                            break
                    except re.error:
                        pass

            # B') 年なし月行の場合、ページ文脈から決算期を推定して年を補完
            fy_end_year: Optional[int] = None
            fy_end_month: int = 3  # デフォルト3月決算
            if bare_month_row_count >= 3 and month_row_count < 3:
                # 優先度: (1) テーブル固有見出し > (2) ページ全体の YYYY年M月期 >
                #          (3) YYYY年度（最大）> (4) year_month_hint
                if per_table_fy_year:
                    fy_end_year = per_table_fy_year
                else:
                    page_text = soup.get_text()
                    fy_m = re.search(r"(\d{4})年\s*(\d{1,2})月期", page_text)
                    if fy_m:
                        fy_end_year = int(fy_m.group(1))
                        fy_end_month = int(fy_m.group(2))
                    elif re.search(r"(\d{4})\s*年度", page_text):
                        years_nendo = [int(y) for y in re.findall(r"(\d{4})\s*年度", page_text)]
                        if years_nendo:
                            fy_end_year = max(years_nendo) + 1
                    elif year_month_hint and len(year_month_hint) >= 4:
                        try:
                            fy_end_year = int(year_month_hint[:4])
                        except ValueError:
                            pass
                # adapter から明示的な fy_end_month を受け取る
                _adapter_fy_em = adapter.get("fy_end_month")
                if _adapter_fy_em:
                    try:
                        fy_end_month = int(_adapter_fy_em)
                    except (ValueError, TypeError):
                        pass

            # 各データ行から値を抽出
            for row in rows_data[header_ri + 1:]:
                if not row:
                    continue
                cell0 = _norm(row[0])
                ym_m = re.search(r"(\d{4})年\s*(\d{1,2})月", cell0)
                if ym_m:
                    year_v = int(ym_m.group(1))
                    month_v = int(ym_m.group(2))
                else:
                    # B') "N月" のみ（年なし）→ 決算期から年を推定
                    bare_m = re.match(r"^(\d{1,2})月$", cell0)
                    if not bare_m:
                        continue
                    month_v = int(bare_m.group(1))
                    if fy_end_year is None:
                        continue
                    # 会計年度の開始月 = 決算期末月+1
                    # 例: 3月決算 → 4月開始、開始月以降=前年、1-3月=決算期年
                    fy_start_month = (fy_end_month % 12) + 1
                    if fy_start_month <= fy_end_month:
                        # 例: 12月決算(start=1,end=12) → 全月が決算期年
                        year_v = fy_end_year
                    elif month_v >= fy_start_month:
                        # 例: 3月決算, 4月～12月 → 決算期年-1
                        year_v = fy_end_year - 1
                    else:
                        # 例: 3月決算, 1月～3月 → 決算期年
                        year_v = fy_end_year
                year_month = f"{year_v:04d}-{month_v:02d}"

                rec_fields: dict[str, float | int] = {}
                for field, ci in field_col_map:
                    if ci < len(row):
                        val = _parse_val(row[ci])
                        if val is not None:
                            if field.get("value_type") == "integer":
                                val = int(val)
                            rec_fields[field["key"]] = val

                if rec_fields:
                    all_records.append({
                        "year_month": year_month,
                        "source": "html_table",
                        "fields": rec_fields,
                    })

        elif month_col_count >= 3 and header_month_ri >= 0:
            # ========== A) 月がカラム方向（2664 パターン） ==========
            # pandas.read_html で rowspan/colspan を展開
            # マルチヘッダ + マルチラベル列がある場合、ラベル列を結合して1列のラベルにする
            _use_pandas = False
            try:
                import io as _io
                _dfs = pd.read_html(_io.StringIO(str(table)))
                if _dfs:
                    _df = _dfs[0]
                    # マルチヘッダを月のみフラット化
                    if isinstance(_df.columns, pd.MultiIndex):
                        new_cols = []
                        for col in _df.columns:
                            # 月 (例: "4月") を最下層から拾う、なければフラット結合
                            month_hit = None
                            for part in col:
                                sp = str(part).strip()
                                if re.match(r"^\d{1,2}月$", sp):
                                    month_hit = sp
                                    break
                            if month_hit:
                                new_cols.append(month_hit)
                            else:
                                parts = [str(p).strip() for p in col if str(p).strip() and not str(p).startswith("Unnamed")]
                                new_cols.append(" ".join(parts) if parts else "")
                        _df.columns = new_cols
                    # ラベル列（値でない先頭数列）を検出して結合
                    _first_month_col = None
                    for ci, c in enumerate(_df.columns):
                        if re.match(r"^\d{1,2}月$", str(c)):
                            _first_month_col = ci
                            break
                    if _first_month_col and _first_month_col >= 1:
                        # ラベル列を結合
                        label_series = _df.iloc[:, :_first_month_col].astype(str).agg(" ".join, axis=1).str.strip()
                        # rows_data を pandas ベースに置き換え
                        new_rows: list[list[str]] = []
                        new_rows.append([""] + list(_df.columns[_first_month_col:]))
                        for ri in range(len(_df)):
                            row_vals = _df.iloc[ri, _first_month_col:].astype(str).tolist()
                            new_rows.append([label_series.iloc[ri]] + row_vals)
                        rows_data = new_rows
                        # ヘッダ行を新しい rows_data[0] に更新
                        header_month_ri = 0
                        _use_pandas = True
            except Exception:
                pass

            header_row = rows_data[header_month_ri]

            # 月→カラムインデックスのマッピング
            month_col_map: dict[int, int] = {}
            for ci, cell in enumerate(header_row):
                m = re.search(r"^(\d{1,2})月$", _norm(cell))
                if m:
                    month_col_map[int(m.group(1))] = ci

            # 年度推定 優先度:
            # (1) テーブル固有見出し > (2) テーブル自身のヘッダ行の "YYYY年" >
            # (3) year_month_hint > (4) ページ全体テキスト
            base_year = None
            if per_table_fy_year:
                # per_table_fy_year は決算期末年 (e.g., 2025年度 → 2026)
                # 会計年度開始は決算期末月+1 の前年 → 2026年3月期 → 4月始まりで base_year=2025
                base_year = per_table_fy_year - 1
            if not base_year:
                # テーブル自身の先頭行で "YYYY年" を発見（例: "| 2025年 | 2026年 | 年間"）
                # 4月始まり会計年度なので最古年（最も左の年）を base_year とする
                for ri, row in enumerate(rows_data[:3]):
                    years_in_header = []
                    for cell in row:
                        m = re.search(r"(\d{4})年(?!\s*\d+月|\s*度)", _norm(cell))
                        if m:
                            years_in_header.append(int(m.group(1)))
                    if years_in_header:
                        base_year = min(years_in_header)
                        break
            if not base_year and year_month_hint and len(year_month_hint) >= 4:
                try:
                    base_year = int(year_month_hint[:4])
                except ValueError:
                    pass
            if not base_year:
                # ページ全体テキストから年度を推定
                page_text = soup.get_text()
                ym = re.search(r"(\d{4})\s*年度", page_text)
                if ym:
                    base_year = int(ym.group(1))
                else:
                    ym = re.search(r"(\d{4})年\s*\d{1,2}月期", page_text)
                    if ym:
                        base_year = int(ym.group(1)) - 1  # 決算期の前年度開始

            data_rows = rows_data[header_month_ri + 1:]

            for month_num, col_idx in sorted(month_col_map.items()):
                # year推定（4月始まり会計年度）
                if base_year:
                    year_v = base_year if month_num >= 4 else base_year + 1
                else:
                    year_v = 2025
                year_month = f"{year_v:04d}-{month_num:02d}"

                rec_fields: dict[str, float | int] = {}
                for field in fields:
                    rlr = field.get("row_label_regex", "")
                    key = field.get("key", "")
                    val_type = field.get("value_type", "float")
                    if not key:
                        continue

                    for row in data_rows:
                        if not row:
                            continue
                        label = _norm(row[0]) if row else ""
                        matched = False
                        if rlr:
                            try:
                                if re.search(rlr, label, re.IGNORECASE):
                                    matched = True
                            except re.error:
                                pass
                        if not matched:
                            continue

                        if col_idx < len(row):
                            val = _parse_val(row[col_idx])
                            if val is not None:
                                if val_type == "integer":
                                    val = int(val)
                                rec_fields[key] = val
                        break

                if rec_fields:
                    all_records.append({
                        "year_month": year_month,
                        "source": "html_table",
                        "fields": rec_fields,
                    })

        else:
            # ========== C) フォールバック: 従来ロジック ==========
            max_cols = max(len(r) for r in rows_data)
            df = pd.DataFrame([r + [""] * (max_cols - len(r)) for r in rows_data])
            label_col = adapter.get("label_col_index", 0)
            data_start = adapter.get("data_start_row", 1)

            label_col_series = df.iloc[data_start:, label_col].astype(str).fillna("")

            for field in fields:
                row_pattern = field.get("row_label_regex", "")
                val_type = field.get("value_type", "float")
                key = field.get("key", "")
                if not row_pattern or not key:
                    continue

                for idx, label in enumerate(label_col_series):
                    if re.search(row_pattern, label, re.IGNORECASE):
                        row_idx = data_start + idx
                        row_vals = df.iloc[row_idx, label_col + 1:]
                        for col_offset, v in enumerate(row_vals):
                            val = _parse_val(v)
                            if val is not None:
                                if val_type == "integer":
                                    val = int(val)
                                ym = year_month_hint or "unknown"
                                while len(all_records) <= col_offset:
                                    all_records.append({"year_month": ym, "source": "html_table", "fields": {}})
                                all_records[col_offset]["fields"][key] = val
                        break

    return [r for r in all_records if r.get("fields")]


# ====================================================
# 対象銘柄選定
# ====================================================

def load_active_companies(
    tickers: Optional[list[str]] = None,
    sample: int = 30,
    bq: Optional[bigquery.Client] = None,
    all_mode: bool = False,
) -> list[dict]:
    """対象銘柄を選定する。

    - --tickers 指定時: そのティッカーを直接使用（CSV/BQに存在しなくてもOK）
    - --all 指定時: BQ TDNET月次全銘柄 + CSV active 全銘柄（件数制限なし）
    - 自動選定時: monthly_adapter_index.csv の active 銘柄 + BQ の TDNET月次あり銘柄を合算
    """
    # ティッカー指定時は CSV に依存しない
    if tickers:
        # 会社名は CSV から補完（なければティッカーをそのまま使用）
        name_map: dict[str, str] = {}
        if INDEX_CSV.exists():
            try:
                df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
                for _, row in df.iterrows():
                    name_map[str(row["ticker"])] = str(row.get("company_name", ""))
            except Exception:
                pass
        return [{"ticker": t, "company_name": name_map.get(t, t), "type": "unknown"} for t in tickers]

    # CSV の会社名マップを作成（会社名補完用）
    name_map: dict[str, dict] = {}
    if INDEX_CSV.exists():
        try:
            df = pd.read_csv(INDEX_CSV, dtype=str, encoding="utf-8-sig")
            active = df[df["category"] == "active"]
            for _, row in active.iterrows():
                t = str(row["ticker"])
                name_map[t] = {"ticker": t, "company_name": str(row.get("company_name", t)), "type": str(row.get("type", ""))}
        except Exception:
            pass

    companies: dict[str, dict] = {}

    # BQ の TDNET月次あり銘柄を優先選定
    if bq:
        try:
            limit_clause = "" if all_mode else f"LIMIT {sample * 3}"
            sql = f"""
            SELECT TICKER, COUNT(*) as cnt
            FROM `{GCP_PROJECT}.{BQ_DATASET}.TDNET_DOCUMENTS_ENHANCED`
            WHERE MAIN_CATEGORY = '月次開示'
               OR '月次開示' IN UNNEST(SUB_CATEGORIES)
            GROUP BY TICKER
            ORDER BY cnt DESC
            {limit_clause}
            """
            tdnet_df = bq.query(sql).to_dataframe()
            for _, row in tdnet_df.iterrows():
                t = str(row["TICKER"])
                info = name_map.get(t, {"ticker": t, "company_name": t, "type": "tdnet_only"})
                companies[t] = info
        except Exception:
            pass

    # 残りを CSV active 銘柄（ローカルファイルあり）で補完
    for t, info in name_map.items():
        if not all_mode and len(companies) >= sample:
            break
        if t not in companies:
            # ローカルダウンロードファイルが存在する場合のみ追加
            patterns = list(MONTHLYIR_DIR.glob(f"{t}_*/"))
            if patterns:
                companies[t] = info
            elif bq:
                # GCS monthly/docs/{ticker}/ にファイルがあるか（Cloud Run 用）
                try:
                    gcs_c = get_gcs()
                    gcs_blobs = list(gcs_c.bucket(GCS_BUCKET).list_blobs(
                        prefix=f"{GCS_DOCS}/{t}/", max_results=1))
                    if gcs_blobs:
                        companies[t] = info
                except Exception:
                    pass

    result = list(companies.values()) if all_mode else list(companies.values())[:sample]
    return result


# ====================================================
# P2-1: 構造化エラーサマリー JSON
# ====================================================

def _save_extract_error_log(
    gcs: "storage.Client",
    results: dict,
    error_entries: list[dict],
) -> None:
    """抽出エラーサマリーを GCS に保存する。"""
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    gcs_path = f"{GCS_LOG}/extract_{ts}.json"
    log_data = {
        "run_at": datetime.now(JST).isoformat(),
        "total_tickers": sum(len(v) for v in results.values()),
        "success": len(results.get("success", [])),
        "skip": len(results.get("skip", [])),
        "failed": len(results.get("fail", [])),
        "errors": error_entries,
    }
    try:
        bucket = gcs.bucket(GCS_BUCKET)
        blob = bucket.blob(gcs_path)
        blob.upload_from_string(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        logging.getLogger("extract_monthly_data").info(
            "エラーサマリーをGCSに保存: gs://%s/%s", GCS_BUCKET, gcs_path,
        )
    except Exception as e:
        logging.getLogger("extract_monthly_data").warning(
            "GCSエラーサマリー保存失敗: %s", e,
        )


# ====================================================
# Phase 2: データ抽出
# ====================================================

def phase_extract(
    companies: list[dict],
    bq: bigquery.Client,
    gcs: storage.Client,
    since: int,
    no_gcs: bool,
    logger: logging.Logger,
    batch_mode: bool = False,
) -> dict:
    results = {"success": [], "skip": [], "fail": []}

    _error_entries: list[dict] = []

    # Batch collection (batch_mode=True のとき使用)
    _batch_lines: list[str] = []
    _batch_meta: dict[str, dict] = {}
    _deferred_companies: dict[str, dict] = {}

    for i, company in enumerate(companies, 1):
        ticker = company["ticker"]
        name = company.get("company_name", ticker)
        logger.info(f"[{i}/{len(companies)}] {ticker} {name}")
        _company_t0 = time.perf_counter()

        # アダプター取得（GCS または ローカル TMP）
        adapter_gcs_path = f"{GCS_META}/{ticker}/extract_adapter.json"
        adapter = gcs_read_json(gcs, adapter_gcs_path)
        if not adapter and no_gcs:
            local = TMP_DIR / f"extract_adapter_{ticker}.json"
            if local.exists():
                adapter = json.loads(local.read_text(encoding="utf-8"))
        if not adapter:
            logger.info(f"  → extract_adapter.json なし → スキップ（先に build_monthly_extractor.py を実行）")
            results["skip"].append(ticker)
            continue

        # 論理削除フラグ: _excluded=true の銘柄は管理外として処理しない
        if adapter.get("_excluded") or adapter.get("excluded"):
            reason = adapter.get("_excluded_reason") or adapter.get("excluded_reason", "(reason 未記載)")
            logger.info(f"  → _excluded=true → 管理対象外スキップ: {reason}")
            results.setdefault("excluded", []).append(ticker)
            continue

        # adapter 必須フィールド検証: structure.json の metrics と突合
        _adapter_fields = adapter.get("fields", [])
        _adapter_field_keys = {
            f.get("bc_key") or f.get("key") for f in _adapter_fields if isinstance(f, dict)
        }
        _structure_path = f"{GCS_META}/{ticker}/structure.json"
        _structure = gcs_read_json(gcs, _structure_path)
        if not _structure and no_gcs:
            _local_struct = PROJECT_ROOT / "meta" / "monthly" / f"{ticker}_structure.json"
            if _local_struct.exists():
                _structure = json.loads(_local_struct.read_text(encoding="utf-8"))
        _structure_metric_names = {
            m.get("name") for m in (_structure or {}).get("metrics", [])
            if isinstance(m, dict) and m.get("name") and m.get("source", "bc") != "original"
        }
        if not _adapter_fields:
            _missing_detail = "adapter.fields が空リスト（抽出項目未定義）"
            if _structure_metric_names:
                _missing_detail += f"。structure.json に {len(_structure_metric_names)} メトリクス定義あり"
            logger.warning("[%s] adapter fields=[] → %s", ticker, _missing_detail)
            results["skip"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "adapter_no_fields",
                "error_detail": _missing_detail, "elapsed": 0.0,
            })
            continue
        elif _structure_metric_names:
            _missing_metrics = _structure_metric_names - _adapter_field_keys
            if _missing_metrics:
                logger.warning(
                    "[%s] adapter fields 不足（%d/%d）: %s",
                    ticker, len(_missing_metrics), len(_structure_metric_names),
                    sorted(_missing_metrics),
                )
                _error_entries.append({
                    "ticker": ticker, "error_type": "adapter_fields_incomplete",
                    "error_detail": f"不足{len(_missing_metrics)}件: {sorted(_missing_metrics)}",
                    "elapsed": 0.0,
                })

        records: list[dict] = []
        extraction_method = adapter.get("extraction_method", "regex")

        # --- TDNET ソース ---
        # source 厳格化対応: pdf → non-tdnet(pdf), html_table → non-tdnet(html_table)
        # 後方互換のため旧表記も accept
        _src = adapter.get("source", "")
        _src_norm = {"pdf": "non-tdnet(pdf)", "html_table": "non-tdnet(html_table)"}.get(_src, _src)
        if _src_norm == "tdnet":
            docs = get_tdnet_docs(ticker, bq, since=since, limit=200)
            logger.info(f"  TDNET 文書 {len(docs)} 件 → 抽出中 (method={extraction_method})")

            # GCS PDF を列指定抽出に使う準備（doc_title → PDF blob のマッピング）
            _pdf_blob_map: dict[str, object] = {}
            try:
                bucket = gcs.bucket(GCS_BUCKET)
                _MONTHLY_KW_re = re.compile(
                    r"月次|月度|店舗売上|monthly|売上速報|業績速報|月別売上|月末運用|前年対比率|主要KPI",
                    re.IGNORECASE,
                )
                for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/"):
                    if b.name.endswith(".pdf") and _MONTHLY_KW_re.search(Path(b.name).name):
                        stem = Path(b.name).stem
                        _pdf_blob_map[stem] = b
            except Exception as e:
                logger.warning("[%s] GCS PDF blob 一覧取得失敗: %s", ticker, e)

            if extraction_method == "gemini" and not batch_mode:
                # Gemini 抽出パス（同期モード）: 初回のみ初期化
                if "_gemini_model" not in phase_extract.__dict__:
                    logger.info("  Gemini クライアント初期化中...")
                    phase_extract._gemini_model = get_gemini()
                gemini_model = phase_extract._gemini_model

            # adapter.doc_title_pattern を inclusion filter として使用.
            # TDnet source では BQ MAIN_CATEGORY='月次開示' が既に一次フィルタしているため
            # adapter 側 doc_title_pattern は原則不要 (2026-04-19 改修).
            # 過剰に厳しいパターンで records 激減する事例が多発 → TDnet では skip.
            # 非 TDnet (non-tdnet(pdf)/html_table) では引き続き inclusion filter として使用.
            _doc_title_filter = adapter.get("doc_title_pattern")
            _doc_title_filter_re = None
            # TDnet source では doc_title_pattern による filter を行わない
            # 注: この branch は `_src_norm == "tdnet"` なので ここでは必ず TDnet
            if _doc_title_filter:
                logger.info(f"  TDnet source → doc_title_pattern filter skip ('{_doc_title_filter[:40]}')")

            for doc in docs:
                doc_title = str(doc.get("DOC_TITLE", ""))
                sub_date = str(doc.get("SUBMISSION_DATE", ""))
                full_text = doc.get("full_text") or ""

                # doc_title_pattern inclusion filter
                if _doc_title_filter_re and not _doc_title_filter_re.search(doc_title):
                    continue

                # まず年月を判定
                ym = _parse_year_month(adapter, doc_title, sub_date)
                if not ym:
                    continue
                year_val, month_val = ym
                if year_val < since:
                    continue

                rec = None
                _cached_pdf: Optional[bytes] = None
                matched_blob = None

                # PDF blob を特定（submission_date 一致を優先）
                if _pdf_blob_map:
                    matched_blob = _pdf_blob_map.get(doc_title)
                    if not matched_blob:
                        _sub_prefix = sub_date.replace("-", "")
                        for stem, b in _pdf_blob_map.items():
                            if stem.startswith(_sub_prefix) and (doc_title in stem or stem in doc_title):
                                matched_blob = b
                                break
                    if not matched_blob:
                        for stem, b in _pdf_blob_map.items():
                            if doc_title in stem or stem in doc_title:
                                matched_blob = b
                                break

                # --- extraction_method=gemini: Gemini PDF直接を最優先 ---
                if extraction_method == "gemini":
                    if batch_mode:
                        # バッチモード: リクエストを収集（Gemini呼び出しは後で一括）
                        pdf_gcs_uri = f"gs://{GCS_BUCKET}/{matched_blob.name}" if matched_blob else None
                        _key = f"{ticker}__tdnet__{len(_batch_lines)}"
                        _prompt = _build_extract_prompt(adapter, doc_title, sub_date)
                        _req = _build_batch_request_obj(
                            _key, _prompt, pdf_gcs_uri=pdf_gcs_uri,
                            full_text=full_text if not pdf_gcs_uri else None,
                        )
                        _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                        _batch_meta[_key] = {
                            "ticker": ticker, "adapter": adapter,
                            "doc_title": doc_title, "submission_date": sub_date,
                            "source_label": "tdnet", "multi_month": False,
                        }
                    else:
                        if matched_blob:
                            try:
                                _cached_pdf = matched_blob.download_as_bytes()
                            except Exception as e:
                                logger.warning(
                                    "[%s] PDF download 失敗 (gemini): %s | blob=%s",
                                    ticker, e, matched_blob.name if matched_blob else "?",
                                )
                        rec = extract_from_text_gemini(
                            full_text, adapter, doc_title, sub_date, gemini_model,
                            pdf_bytes=_cached_pdf,
                        )
                else:
                    # --- extraction_method=regex: PDF列指定抽出を優先 ---
                    if not matched_blob:
                        logger.warning(
                            "[%s] regex adapter だが PDF blob なし（url_adapter.json 未作成?）。スキップ | doc=%s",
                            ticker, doc_title[:60],
                        )
                        continue
                    if matched_blob:
                        try:
                            _cached_pdf = matched_blob.download_as_bytes()
                            if adapter.get("month_direction") == "row":
                                rec = _extract_pdf_by_row(
                                    _cached_pdf, adapter, month_val, year_val, doc_title, sub_date,
                                )
                            else:
                                rec = _extract_pdf_by_column(
                                    _cached_pdf, adapter, month_val, year_val, doc_title, sub_date,
                                )
                        except Exception as e:
                            logger.warning(
                                "[%s] PDF column/row 抽出失敗: %s | doc=%s | blob=%s",
                                ticker, e, doc_title,
                                matched_blob.name if matched_blob else "?",
                            )
                        # 単月テーブルフォールバック
                        if not rec and _cached_pdf:
                            try:
                                rec = _extract_pdf_single_month(
                                    _cached_pdf, adapter, month_val, year_val, doc_title, sub_date,
                                )
                            except Exception as e:
                                logger.warning(
                                    "[%s] _extract_pdf_single_month フォールバック失敗: %s | doc=%s",
                                    ticker, e, doc_title,
                                )

                    # フォールバック + 補完マージ: BQ テキスト or PDF テキストから row_label_regex 抽出
                    # _extract_pdf_by_column が部分的 rec を返すケース (8914 稼働率 field 等) でも、
                    # row_label_regex 定義済 field を補完する。
                    _has_row_regex = any(
                        f.get("row_label_regex") for f in adapter.get("fields", [])
                    )
                    if not rec:
                        _fallback_text = full_text
                        if _has_row_regex and _cached_pdf:
                            try:
                                _fb_pdf_text = _extract_pdf_text(_cached_pdf)
                                if _fb_pdf_text.strip():
                                    _fallback_text = _fb_pdf_text
                            except Exception as e:
                                logger.warning(
                                    "[%s] _extract_pdf_text 失敗 (rec=None fallback): %s",
                                    ticker, e,
                                )
                        rec = extract_from_tdnet_text(
                            _fallback_text, adapter, doc_title, sub_date,
                        )
                    elif _has_row_regex:
                        _pdf_text_for_regex = ""
                        try:
                            _pdf_text_for_regex = _extract_pdf_text(_cached_pdf) if _cached_pdf else ""
                        except Exception as e:
                            logger.warning(
                                "[%s] _extract_pdf_text 失敗 (regex補完): %s | doc=%s",
                                ticker, e, doc_title,
                            )
                            _pdf_text_for_regex = ""
                        _text_for_regex = (
                            _pdf_text_for_regex if _pdf_text_for_regex.strip() else full_text
                        )
                        text_rec = extract_from_tdnet_text(
                            _text_for_regex, adapter, doc_title, sub_date,
                        )
                        if text_rec:
                            rec_fields = rec.setdefault("fields", {})
                            for k, v in text_rec.get("fields", {}).items():
                                if k not in rec_fields:
                                    rec_fields[k] = v
                if rec:
                    records.append(rec)

            # overwrite_past_months: 最新PDFから全月分を抽出し既存レコードを上書き
            if adapter.get("overwrite_past_months") and _pdf_blob_map:
                # 最新の PDF blob を取得（ファイル名でソート→最新）
                latest_stem = sorted(_pdf_blob_map.keys())[-1] if _pdf_blob_map else None
                if latest_stem:
                    latest_blob = _pdf_blob_map[latest_stem]
                    try:
                        pdf_bytes = latest_blob.download_as_bytes()
                        # blob stem の先頭8桁 (YYYYMMDD) から submission_date を推定
                        _owpm_sub_date = ""
                        _stem_date_m = re.match(r"(\d{4})(\d{2})(\d{2})", latest_stem)
                        if _stem_date_m:
                            _owpm_sub_date = f"{_stem_date_m.group(1)}-{_stem_date_m.group(2)}-{_stem_date_m.group(3)}"
                        all_month_recs = _extract_pdf_all_months(
                            pdf_bytes, adapter, latest_stem,
                            submission_date=_owpm_sub_date,
                            since=since,
                        )
                        if all_month_recs:
                            # year_month をキーにして既存レコードを上書き
                            rec_map = {r["year_month"]: r for r in records}
                            for new_rec in all_month_recs:
                                new_rec["source"] = "pdf_column_overwrite"
                                rec_map[new_rec["year_month"]] = new_rec
                            records = list(rec_map.values())
                            logger.info(f"  overwrite_past_months: {len(all_month_recs)} 月分で上書き")
                    except Exception as e:
                        logger.warning(
                            "[%s] overwrite_past_months 失敗: %s | blob=%s",
                            ticker, e, latest_stem,
                        )

        # --- ダウンロードソース ---
        elif adapter.get("source") == "download":
            ext = adapter.get("format", "xlsx")
            is_pdf = ext.lower() == "pdf"
            _is_excel_gemini = extraction_method == "excel_gemini"

            _gemini_xlsx_client = None
            _gemini_xlsx_model = None
            if _is_excel_gemini and not batch_mode:
                if IS_CLOUD_RUN:
                    _gemini_xlsx_client = get_gemini()
                    _gemini_xlsx_model = GEMINI_MODEL
                    logger.info(f"  Gemini Vertex AI 初期化 (model={_gemini_xlsx_model})")
                else:
                    try:
                        from google import genai  # noqa: F811
                        from dotenv import load_dotenv
                        load_dotenv(PROJECT_ROOT / ".env")
                        _gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
                        if _gemini_api_key:
                            _gemini_xlsx_client = genai.Client(api_key=_gemini_api_key)
                            _gemini_xlsx_model = GEMINI_MODEL
                            logger.info(f"  Gemini 個人APIキー初期化 (model={_gemini_xlsx_model})")
                        else:
                            logger.warning("  GEMINI_API_KEY 未設定 → excel_gemini スキップ")
                    except ImportError:
                        logger.warning("  google-genai 未インストール → excel_gemini スキップ")

            def _process_download_pdf(pdf_bytes: bytes, fname_stem: str) -> None:
                """monthlyir PDF を TDNET と同じロジックで抽出する。"""
                sub_date = (
                    f"{fname_stem[:4]}-{fname_stem[4:6]}-{fname_stem[6:8]}"
                    if len(fname_stem) >= 8 and fname_stem[:8].isdigit()
                    else ""
                )
                ym = _parse_year_month(adapter, fname_stem, sub_date)
                if not ym:
                    return
                year, month = ym
                if year < since:
                    return
                # 列方向 or 行方向
                if adapter.get("month_direction") == "row":
                    rec = _extract_pdf_by_row(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                else:
                    rec = _extract_pdf_by_column(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                # 単月テーブルフォールバック
                if not rec:
                    rec = _extract_pdf_single_month(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                if not rec:
                    # フォールバック: 全テキスト化→regex 抽出
                    text = _extract_pdf_text(pdf_bytes)
                    if text.strip():
                        rec = extract_from_tdnet_text(text, adapter, fname_stem, sub_date)
                        if rec:
                            rec["source"] = "monthlyir_pdf"
                if rec:
                    if "source" not in rec:
                        rec["source"] = "monthlyir_pdf"
                    records.append(rec)

            patterns = list(MONTHLYIR_DIR.glob(f"{ticker}_*/"))
            if patterns:
                company_dir = patterns[0]
                files = sorted(company_dir.glob(f"*.{ext}"))
                logger.info(f"  ダウンロードファイル {len(files)} 件 → 抽出中 (method={extraction_method})")
                for fpath in files:
                    if is_pdf:
                        _process_download_pdf(fpath.read_bytes(), fpath.stem)
                    elif _is_excel_gemini:
                        _fname = fpath.stem
                        _sub = (
                            f"{_fname[:4]}-{_fname[4:6]}-{_fname[6:8]}"
                            if len(_fname) >= 8 and _fname[:8].isdigit()
                            else ""
                        )
                        if batch_mode:
                            try:
                                if fpath.suffix.lower() == ".csv":
                                    _df = pd.read_csv(fpath, header=None, encoding=adapter.get("encoding", "utf-8-sig"))
                                else:
                                    _df = pd.read_excel(fpath, sheet_name=adapter.get("sheet_name", 0), header=None)
                                _csv = _df.to_csv(index=False, header=False)
                                if _csv.strip():
                                    _key = f"{ticker}__xlsx__{len(_batch_lines)}"
                                    _prompt = _build_excel_gemini_prompt(adapter, _fname, _sub, _csv)
                                    _req = _build_batch_request_obj(_key, _prompt)
                                    _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                                    _batch_meta[_key] = {
                                        "ticker": ticker, "adapter": adapter,
                                        "doc_title": _fname, "submission_date": _sub,
                                        "source_label": "excel_gemini", "multi_month": True, "since": since,
                                    }
                            except Exception as e:
                                logger.debug(f"  [excel_gemini/batch] {fpath.name} 失敗: {e}")
                        elif _gemini_xlsx_client:
                            recs = _extract_xlsx_gemini_personal(
                                fpath, adapter, _fname, _sub,
                                _gemini_xlsx_client, _gemini_xlsx_model, logger,
                            )
                            records.extend(recs)
                    else:
                        recs = extract_from_xlsx(fpath, adapter)
                        records.extend(recs)
            elif gcs:
                # GCS monthly/docs/{ticker}/ からファイル取得（Cloud Run 用）
                import tempfile
                bucket = gcs.bucket(GCS_BUCKET)
                gcs_blobs = [b for b in bucket.list_blobs(prefix=f"{GCS_DOCS}/{ticker}/")
                             if b.name.endswith(f".{ext}")]
                if gcs_blobs:
                    logger.info(f"  GCS {GCS_DOCS}/{ticker}/ から {len(gcs_blobs)} 件 → 抽出中 (method={extraction_method})")
                    for blob in gcs_blobs:
                        if is_pdf:
                            try:
                                pdf_bytes = blob.download_as_bytes()
                                _process_download_pdf(pdf_bytes, Path(blob.name).stem)
                            except Exception as e:
                                logger.debug(f"  [download/pdf] {blob.name} 失敗: {e}")
                        elif _is_excel_gemini and (batch_mode or _gemini_xlsx_client):
                            try:
                                with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                                    tmp_path = tmp.name
                                blob.download_to_filename(tmp_path)
                                _fname = Path(blob.name).stem
                                _sub = (
                                    f"{_fname[:4]}-{_fname[4:6]}-{_fname[6:8]}"
                                    if len(_fname) >= 8 and _fname[:8].isdigit()
                                    else ""
                                )
                                if batch_mode:
                                    if Path(tmp_path).suffix.lower() == ".csv":
                                        _df = pd.read_csv(tmp_path, header=None, encoding=adapter.get("encoding", "utf-8-sig"))
                                    else:
                                        _df = pd.read_excel(tmp_path, sheet_name=adapter.get("sheet_name", 0), header=None)
                                    _csv = _df.to_csv(index=False, header=False)
                                    if _csv.strip():
                                        _key = f"{ticker}__xlsx__{len(_batch_lines)}"
                                        _prompt = _build_excel_gemini_prompt(adapter, _fname, _sub, _csv)
                                        _req = _build_batch_request_obj(_key, _prompt)
                                        _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                                        _batch_meta[_key] = {
                                            "ticker": ticker, "adapter": adapter,
                                            "doc_title": _fname, "submission_date": _sub,
                                            "source_label": "excel_gemini", "multi_month": True, "since": since,
                                        }
                                elif _gemini_xlsx_client:
                                    recs = _extract_xlsx_gemini_personal(
                                        Path(tmp_path), adapter, _fname, _sub,
                                        _gemini_xlsx_client, _gemini_xlsx_model, logger,
                                    )
                                    records.extend(recs)
                            except Exception as e:
                                logger.debug(f"  [excel_gemini] {blob.name} 失敗: {e}")
                            finally:
                                try:
                                    os.unlink(tmp_path)
                                except (PermissionError, OSError):
                                    pass
                        else:
                            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                                tmp_path = tmp.name
                            blob.download_to_filename(tmp_path)
                            recs = extract_from_xlsx(Path(tmp_path), adapter)
                            records.extend(recs)
                            try:
                                os.unlink(tmp_path)
                            except PermissionError:
                                pass  # Windows: file may still be locked
                else:
                    logger.info(f"  → ダウンロードファイルなし（download_monthly.py を先に実行してください）")
            else:
                logger.info(f"  → ダウンロードファイルなし（download_monthly.py を先に実行してください）")

        # --- HTML テーブルソース ---
        elif _src_norm == "non-tdnet(html_table)":
            # Gemini 初期化（extraction_method=gemini かつ同期モードの場合のみ）
            _gemini_html_client = None
            _gemini_html_model = None
            if extraction_method == "gemini" and not batch_mode:
                if IS_CLOUD_RUN:
                    _gemini_html_client = get_gemini()
                    _gemini_html_model = GEMINI_MODEL
                    logger.info(f"  Gemini Vertex AI 初期化 (model={_gemini_html_model})")
                else:
                    try:
                        from google import genai
                        from dotenv import load_dotenv
                        load_dotenv(PROJECT_ROOT / ".env")
                        _gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
                        if _gemini_api_key:
                            _gemini_html_client = genai.Client(api_key=_gemini_api_key)
                            _gemini_html_model = GEMINI_MODEL
                            logger.info(f"  Gemini 個人APIキー初期化 (model={_gemini_html_model})")
                        else:
                            logger.warning("  GEMINI_API_KEY 未設定 → Gemini 抽出スキップ、regex にフォールバック")
                    except ImportError:
                        logger.warning("  google-genai 未インストール → Gemini 抽出スキップ、regex にフォールバック")

            patterns = list(MONTHLYIR_DIR.glob(f"{ticker}_*/"))
            if patterns:
                company_dir = patterns[0]
                files = sorted(company_dir.glob("*_monthly_table.html"))
                logger.info(f"  HTML テーブルファイル {len(files)} 件 → 抽出中 (method={extraction_method})")
                for fpath in files:
                    if extraction_method == "gemini" and (batch_mode or _gemini_html_client):
                        try:
                            html_text = fpath.read_text(encoding="utf-8")
                            fname_stem = fpath.stem
                            sub_date = ""
                            if len(fname_stem) >= 6 and fname_stem[:6].isdigit():
                                ym_prefix = fname_stem[:6]
                                if ym_prefix != "000000":
                                    sub_date = f"{ym_prefix[:4]}-{ym_prefix[4:6]}-20"
                            if not sub_date:
                                from datetime import datetime as _dt
                                try:
                                    from zoneinfo import ZoneInfo as _ZI
                                    sub_date = _dt.now(_ZI("Asia/Tokyo")).strftime("%Y-%m-%d")
                                except Exception:
                                    sub_date = _dt.now().strftime("%Y-%m-%d")
                            _is_multi_html = bool(adapter.get("gemini_multi_month"))
                            if batch_mode:
                                _key = f"{ticker}__html__{len(_batch_lines)}"
                                _prompt = _build_extract_prompt(
                                    adapter, fname_stem, sub_date,
                                    full_text=html_text, is_multi_month=_is_multi_html,
                                )
                                _req = _build_batch_request_obj(
                                    _key, _prompt, full_text=html_text,
                                )
                                _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                                _batch_meta[_key] = {
                                    "ticker": ticker, "adapter": adapter,
                                    "doc_title": fname_stem, "submission_date": sub_date,
                                    "source_label": "html_gemini", "multi_month": _is_multi_html,
                                }
                            else:
                                if _is_multi_html:
                                    multi = _extract_html_gemini_all_months(
                                        html_text, adapter, fname_stem, sub_date,
                                        _gemini_html_client, _gemini_html_model,
                                        since=since, logger=logger,
                                    )
                                    if multi:
                                        records.extend(multi)
                                    else:
                                        rec = _extract_html_gemini_personal(
                                            html_text, adapter, fname_stem, sub_date,
                                            _gemini_html_client, _gemini_html_model, logger,
                                        )
                                        if rec:
                                            records.append(rec)
                                else:
                                    rec = _extract_html_gemini_personal(
                                        html_text, adapter, fname_stem, sub_date,
                                        _gemini_html_client, _gemini_html_model, logger,
                                    )
                                    if rec:
                                        records.append(rec)
                        except Exception as e:
                            logger.debug(f"  [gemini] {fpath.name} 失敗: {e}")
                    else:
                        recs = extract_from_html(fpath, adapter)
                        records.extend(recs)
            elif gcs:
                # GCS monthly/docs/{ticker}/ からファイル取得（Cloud Run 用）
                import tempfile
                bucket = gcs.bucket(GCS_BUCKET)
                _all_html_blobs = [b for b in bucket.list_blobs(prefix=f"{GCS_DOCS}/{ticker}/")
                                   if b.name.endswith(".html")]
                # ローカルモード同様 monthly_table を優先。なければ月次キーワードでフィルタ
                _MONTHLY_HTML_RE = re.compile(
                    r"monthly_table|月次|月度|monthly|売上速報|受注速報|月末|月別",
                    re.IGNORECASE,
                )
                gcs_blobs = [b for b in _all_html_blobs
                             if _MONTHLY_HTML_RE.search(Path(b.name).stem)]
                if not gcs_blobs:
                    gcs_blobs = _all_html_blobs  # フォールバック: 全HTMLを対象
                if gcs_blobs:
                    logger.info(f"  GCS {GCS_DOCS}/{ticker}/ から HTML {len(gcs_blobs)} 件 → 抽出中 (method={extraction_method})")
                    for blob in gcs_blobs:
                        if extraction_method == "gemini" and (batch_mode or _gemini_html_client):
                            try:
                                html_bytes = blob.download_as_bytes()
                                html_text = html_bytes.decode("utf-8", errors="replace")
                                fname_stem = Path(blob.name).stem
                                sub_date = ""
                                if len(fname_stem) >= 6 and fname_stem[:6].isdigit():
                                    ym_prefix = fname_stem[:6]
                                    if ym_prefix != "000000":
                                        sub_date = f"{ym_prefix[:4]}-{ym_prefix[4:6]}-20"
                                if not sub_date:
                                    from datetime import datetime as _dt
                                    try:
                                        from zoneinfo import ZoneInfo as _ZI
                                        sub_date = _dt.now(_ZI("Asia/Tokyo")).strftime("%Y-%m-%d")
                                    except Exception:
                                        sub_date = _dt.now().strftime("%Y-%m-%d")
                                _is_multi_html = bool(adapter.get("gemini_multi_month"))
                                if batch_mode:
                                    _key = f"{ticker}__html__{len(_batch_lines)}"
                                    _prompt = _build_extract_prompt(
                                        adapter, fname_stem, sub_date,
                                        full_text=html_text, is_multi_month=_is_multi_html,
                                    )
                                    _req = _build_batch_request_obj(
                                        _key, _prompt, full_text=html_text,
                                    )
                                    _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                                    _batch_meta[_key] = {
                                        "ticker": ticker, "adapter": adapter,
                                        "doc_title": fname_stem, "submission_date": sub_date,
                                        "source_label": "html_gemini", "multi_month": _is_multi_html,
                                    }
                                else:
                                    if _is_multi_html:
                                        multi = _extract_html_gemini_all_months(
                                            html_text, adapter, fname_stem, sub_date,
                                            _gemini_html_client, _gemini_html_model,
                                            since=since, logger=logger,
                                        )
                                        if multi:
                                            records.extend(multi)
                                        else:
                                            rec = _extract_html_gemini_personal(
                                                html_text, adapter, fname_stem, sub_date,
                                                _gemini_html_client, _gemini_html_model, logger,
                                            )
                                            if rec:
                                                records.append(rec)
                                    else:
                                        rec = _extract_html_gemini_personal(
                                            html_text, adapter, fname_stem, sub_date,
                                            _gemini_html_client, _gemini_html_model, logger,
                                        )
                                        if rec:
                                            records.append(rec)
                            except Exception as e:
                                logger.debug(f"  [gemini] {blob.name} 失敗: {e}")
                        else:
                            tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
                            tmp_path = tmp.name
                            tmp.close()
                            try:
                                blob.download_to_filename(tmp_path)
                                recs = extract_from_html(Path(tmp_path), adapter)
                                records.extend(recs)
                            finally:
                                try:
                                    os.unlink(tmp_path)
                                except OSError:
                                    pass
                else:
                    logger.info(f"  → HTML ファイルなし（download_monthly.py を先に実行してください）")
            else:
                logger.info(f"  → HTML ファイルなし（download_monthly.py を先に実行してください）")

        # --- PDF ソース（GCS から PDF を取得してテキスト抽出）---
        elif _src_norm == "non-tdnet(pdf)":
            import io
            try:
                import pdfplumber
            except ImportError:
                logger.warning(f"  [pdf] pdfplumber 未インストール → スキップ")
                results["skip"].append(ticker)
                continue

            # 月次関連PDFのキーワードフィルター（download_monthly.py の EIR_MONTHLY_RE と同期）
            _MONTHLY_KW = re.compile(
                r"月次|月度|monthly|マンスリー|売上速報|売上高|月別|受注速報|受注実績|販売台数"
                r"|輸送実績|旅客数|搭乗実績|稼働実績|出荷量|KPI|Net Sales"
                r"|稼働率|入居率|来店|客数|セールス|オペレーション|生産実績|契約件数"
                r"|新規契約|解約|ユーザー数|会員数|AUM|預かり|店舗売上|業績速報|月別売上"
                r"|業績動向|Store Performance|受注|処方|調剤",
                re.IGNORECASE,
            )

            # Gemini 初期化（extraction_method=gemini かつ同期モードの場合のみ）
            _gemini_model_pdf = None
            _gemini_client = None
            if extraction_method == "gemini" and not batch_mode:
                if IS_CLOUD_RUN:
                    _gemini_client = get_gemini()
                    _gemini_model_pdf = GEMINI_MODEL
                    logger.info(f"  Gemini Vertex AI 初期化 (model={_gemini_model_pdf})")
                else:
                    try:
                        from google import genai
                        from dotenv import load_dotenv
                        load_dotenv(PROJECT_ROOT / ".env")
                        _gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
                        if _gemini_api_key:
                            _gemini_client = genai.Client(api_key=_gemini_api_key)
                            _gemini_model_pdf = GEMINI_MODEL
                            logger.info(f"  Gemini 個人APIキー初期化 (model={_gemini_model_pdf})")
                        else:
                            logger.warning("  GEMINI_API_KEY 未設定 → Gemini 抽出スキップ")
                    except ImportError:
                        logger.warning("  google-genai 未インストール → Gemini 抽出スキップ")

            try:
                bucket = gcs.bucket(GCS_BUCKET)
                # 1) monthly/docs/{ticker}/ を優先（IR HPダウンロード分）
                # 2) フォールバック: tdnet/{ticker}/（TDnet 適時開示分）
                all_blobs = []
                docs_blobs = [b for b in bucket.list_blobs(prefix=f"{GCS_DOCS}/{ticker}/") if b.name.endswith(".pdf")]
                if docs_blobs:
                    all_blobs = docs_blobs
                    logger.debug(f"  GCS {GCS_DOCS}/{ticker}/ から {len(docs_blobs)} PDF")
                else:
                    all_blobs = [b for b in bucket.list_blobs(prefix=f"tdnet/{ticker}/") if b.name.endswith(".pdf")]
                # 月次関連のみ絞り込み（adapter に monthly_kw_override があれば優先）
                _kw_override = adapter.get("monthly_kw_override")
                _kw_re = re.compile(_kw_override, re.IGNORECASE) if _kw_override else _MONTHLY_KW
                pdf_blobs = [b for b in all_blobs if _kw_re.search(Path(b.name).name)]
                # doc_title_pattern: ファイル名 OR PDF内テキストでマッチ（厳密フィルターではなく優先順位付け）
                # source="pdf" ではファイル名と文書タイトルが異なる場合が多いため、除外はしない
                _dtp = adapter.get("doc_title_pattern")
                if _dtp and len(pdf_blobs) > 20:
                    # 大量のPDFがある場合のみフィルタリング（少数なら全件処理）
                    try:
                        _dtp_re = re.compile(_dtp, re.IGNORECASE)
                        _filtered = [b for b in pdf_blobs if _dtp_re.search(Path(b.name).name)]
                        if _filtered:
                            pdf_blobs = _filtered
                    except re.error:
                        pass
                logger.info(f"  GCS PDF 全{len(all_blobs)}件 → 月次関連{len(pdf_blobs)}件 → 抽出中 (method={extraction_method})")
                for blob in pdf_blobs:
                    fname_stem = Path(blob.name).stem
                    if len(fname_stem) >= 8 and fname_stem[:8].isdigit():
                        sub_date = f"{fname_stem[:4]}-{fname_stem[4:6]}-{fname_stem[6:8]}"
                    elif len(fname_stem) >= 6 and fname_stem[:6].isdigit():
                        # YYYYMM_ 形式: day=20 で提出日ヒューリスティックが報告月を正しく返す
                        sub_date = f"{fname_stem[:4]}-{fname_stem[4:6]}-20"
                    else:
                        sub_date = ""

                    try:
                        pdf_bytes = blob.download_as_bytes()
                    except Exception as e:
                        logger.debug(f"  [pdf] {fname_stem} ダウンロード失敗: {e}")
                        continue

                    # --- 年月推定 ---
                    ym = _parse_year_month(adapter, fname_stem, sub_date)
                    if not ym and fname_stem.startswith("000000"):
                        # テーブル以外のテキスト（タイトル・日付）も含めるため page.extract_text() を使用
                        try:
                            import pdfplumber as _plumb
                            with _plumb.open(io.BytesIO(pdf_bytes)) as _tpdf:
                                _pdf_text_head = " ".join(
                                    (p.extract_text() or "") for p in _tpdf.pages[:2]
                                )[:800]
                        except Exception:
                            _pdf_text_head = _extract_pdf_text(pdf_bytes)[:500]
                        _month_re = adapter.get("month_from_title_regex") or r"(\d{1,2})月"
                        _year_re = adapter.get("year_from_title_regex") or r"(\d{4})年"
                        _month_m = re.search(_month_re, fname_stem)
                        _year_m = re.search(_year_re, _pdf_text_head)
                        if _month_m and _year_m:
                            # ファイル名に月あり + PDF本文に年あり → 合成
                            try:
                                ym = (int(_year_m.group(1)), int(_month_m.group(1)))
                            except (ValueError, IndexError):
                                pass
                        elif _year_m and adapter.get("overwrite_past_months"):
                            # 全月テーブル（月がファイル名にない）→ ダミー月でoverwriteに委任
                            ym = (int(_year_m.group(1)), 1)
                    if not ym:
                        continue
                    year, month = ym
                    if year < since:
                        continue

                    # sub_date が無効（"0000-00-xx" 等）で ym が PDF本文から解決された場合、
                    # 下流関数（_extract_pdf_gemini_personal 等）が再度 _parse_year_month を
                    # 呼ぶと失敗するため、合成 sub_date で上書きする
                    if sub_date.startswith("0000-") or not sub_date:
                        sub_date = f"{year:04d}-{month:02d}-20"

                    rec = None

                    # --- extraction_method=gemini ---
                    if extraction_method == "gemini" and (batch_mode or _gemini_model_pdf):
                        if batch_mode:
                            _is_multi = bool(adapter.get("gemini_multi_month"))
                            _key = f"{ticker}__pdf__{len(_batch_lines)}"
                            _pdf_gcs_uri = f"gs://{GCS_BUCKET}/{blob.name}"
                            _prompt = _build_extract_prompt(
                                adapter, fname_stem, sub_date, is_multi_month=_is_multi,
                            )
                            _req = _build_batch_request_obj(
                                _key, _prompt, pdf_gcs_uri=_pdf_gcs_uri,
                                is_multi_month=_is_multi,
                            )
                            _batch_lines.append(json.dumps(_req, ensure_ascii=False))
                            _batch_meta[_key] = {
                                "ticker": ticker, "adapter": adapter,
                                "doc_title": fname_stem, "submission_date": sub_date,
                                "source_label": "pdf_gemini",
                                "multi_month": _is_multi, "since": since,
                            }
                        else:
                            try:
                                if adapter.get("gemini_multi_month"):
                                    multi = _extract_pdf_gemini_all_months(
                                        pdf_bytes, adapter, fname_stem, sub_date,
                                        _gemini_client, _gemini_model_pdf,
                                        since=since, logger=logger,
                                    )
                                    if multi:
                                        records.extend(multi)
                                        rec = None
                                    else:
                                        rec = _extract_pdf_gemini_personal(
                                            pdf_bytes, adapter, fname_stem, sub_date,
                                            _gemini_client, _gemini_model_pdf, logger,
                                        )
                                else:
                                    rec = _extract_pdf_gemini_personal(
                                        pdf_bytes, adapter, fname_stem, sub_date,
                                        _gemini_client, _gemini_model_pdf, logger,
                                    )
                            except Exception as e:
                                logger.debug(f"  [gemini] {fname_stem} 失敗: {e}")

                    # --- extraction_method=ocr ---
                    elif extraction_method == "ocr":
                        try:
                            ocr_text = _extract_pdf_ocr(pdf_bytes, logger)
                            if ocr_text.strip():
                                rec = extract_from_tdnet_text(ocr_text, adapter, fname_stem, sub_date)
                                if rec:
                                    rec["source"] = "pdf_ocr"
                            else:
                                logger.debug(f"  [ocr] {fname_stem} OCRテキストなし")
                        except Exception as e:
                            logger.debug(f"  [ocr] {fname_stem} 失敗: {e}")

                    # --- extraction_method=regex (デフォルト) ---
                    else:
                        # overwrite_past_months: 全月ループ抽出（年度跨ぎ対応: year と year-1 を試行）
                        if adapter.get("overwrite_past_months"):
                            for _y in [year, year - 1]:
                                if _y < since:
                                    continue
                                for _m in range(1, 13):
                                    _r = None
                                    if adapter.get("month_direction") == "row":
                                        _r = _extract_pdf_by_row(pdf_bytes, adapter, _m, _y, fname_stem, sub_date)
                                    else:
                                        _r = _extract_pdf_by_column(pdf_bytes, adapter, _m, _y, fname_stem, sub_date)
                                    if not _r:
                                        _r = _extract_pdf_single_month(pdf_bytes, adapter, _m, _y, fname_stem, sub_date)
                                    if _r and _r.get("fields"):
                                        records.append(_r)
                            rec = None  # 個別 rec は不要
                        else:
                            if adapter.get("month_direction") == "row":
                                rec = _extract_pdf_by_row(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                            else:
                                rec = _extract_pdf_by_column(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                            if not rec:
                                rec = _extract_pdf_single_month(pdf_bytes, adapter, month, year, fname_stem, sub_date)
                            if not rec:
                                text = _extract_pdf_text(pdf_bytes)
                                if text.strip():
                                    rec = extract_from_tdnet_text(text, adapter, fname_stem, sub_date)
                                    if rec:
                                        rec["source"] = "pdf"

                    if rec:
                        records.append(rec)
            except Exception as e:
                import traceback
                logger.warning(f"  [pdf] GCS アクセスエラー: {e}\n{traceback.format_exc()}")

        # バッチモードの Gemini 企業: 保存を遅延（バッチ結果待ち）
        if batch_mode and extraction_method in ("gemini", "excel_gemini"):
            _deferred_companies[ticker] = {
                "company": company, "name": name, "adapter": adapter,
                "records": records,
            }
            logger.info(f"  → バッチモード: {len([k for k in _batch_meta if _batch_meta[k]['ticker'] == ticker])} リクエスト収集済み")
            continue

        if not records:
            _elapsed = time.perf_counter() - _company_t0
            logger.info(f"  → 抽出レコードなし → スキップ (elapsed={_elapsed:.1f}s)")
            results["skip"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "no_records",
                "error_detail": "抽出レコード0件", "elapsed": round(_elapsed, 1),
            })
            continue

        # 重複除去（year_month ごとに最新提出日のもの優先）
        rdf = pd.DataFrame(records)
        if "submission_date" in rdf.columns:
            rdf = rdf.sort_values("submission_date", ascending=False)
        rdf = rdf.drop_duplicates(subset=["year_month"], keep="first")
        rdf = rdf.sort_values("year_month")
        records_clean = rdf.to_dict("records")

        logger.info(f"  抽出レコード {len(records_clean)} 件 (year_month: {records_clean[0].get('year_month')} 〜 {records_clean[-1].get('year_month')})")

        output = {
            "ticker": ticker,
            "company_name": name,
            "updated_at": datetime.now(JST).isoformat(),
            "record_count": len(records_clean),
            "records": records_clean,
        }

        # 保存
        records_gcs_path = f"{GCS_RECORD}/{ticker}/monthly_records.json"
        if no_gcs:
            local = TMP_DIR / f"monthly_records_{ticker}.json"
            local.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"  ✅ ローカル保存: {local}")
        else:
            gcs_write_json(gcs, records_gcs_path, output)
            logger.info(f"  ✅ GCS保存: gs://{GCS_BUCKET}/{records_gcs_path}")

        results["success"].append(ticker)
        _elapsed = time.perf_counter() - _company_t0
        logger.info(f"  完了: records={len(records_clean)} elapsed={_elapsed:.1f}s")
        if _elapsed > 120:
            logger.warning("[%s] 処理遅延: %.1fs (閾値 120s)", ticker, _elapsed)

    # ====================================================
    # Gemini Batch 実行 & 結果適用
    # ====================================================
    if _batch_lines:
        logger.info(f"--- Gemini Batch 投入: {len(_batch_lines)} リクエスト / {len(_deferred_companies)} 社 ---")
        batch_results = _submit_and_poll_extract_batch(_batch_lines, gcs, logger)

        if not batch_results:
            logger.warning("  [batch] バッチ結果0件 — 全 deferred 企業を fail 扱い")
            for ticker in _deferred_companies:
                results["fail"].append(ticker)
                _error_entries.append({
                    "ticker": ticker, "error_type": "batch_failed",
                    "error_detail": "Gemini Batch ジョブ失敗/タイムアウト",
                })

        # 結果を各企業の records に振り分け
        for key, resp_obj in batch_results.items():
            meta = _batch_meta.get(key)
            if not meta:
                continue
            t = meta["ticker"]
            comp_data = _deferred_companies.get(t)
            if not comp_data:
                continue

            if meta.get("multi_month"):
                recs = _parse_batch_result_multi_month(
                    resp_obj, meta["adapter"], meta["doc_title"],
                    meta["submission_date"], since=meta.get("since", since),
                    source_label=meta.get("source_label", "pdf_gemini_all"),
                )
                comp_data["records"].extend(recs)
            else:
                rec = _parse_batch_result_single(
                    resp_obj, meta["adapter"], meta["doc_title"],
                    meta["submission_date"], source_label=meta.get("source_label", "tdnet"),
                )
                if rec:
                    comp_data["records"].append(rec)

        # 遅延企業の保存
        for ticker, comp_data in _deferred_companies.items():
            records = comp_data["records"]
            name = comp_data["name"]

            if not records:
                logger.info(f"  [{ticker}] バッチ結果後もレコードなし → スキップ")
                results["skip"].append(ticker)
                _error_entries.append({
                    "ticker": ticker, "error_type": "no_records_after_batch",
                    "error_detail": "バッチ結果適用後レコード0件",
                })
                continue

            rdf = pd.DataFrame(records)
            if "submission_date" in rdf.columns:
                rdf = rdf.sort_values("submission_date", ascending=False)
            rdf = rdf.drop_duplicates(subset=["year_month"], keep="first")
            rdf = rdf.sort_values("year_month")
            records_clean = rdf.to_dict("records")

            logger.info(f"  [{ticker}] バッチ結果: {len(records_clean)} 件 (year_month: {records_clean[0].get('year_month')} 〜 {records_clean[-1].get('year_month')})")

            output = {
                "ticker": ticker,
                "company_name": name,
                "updated_at": datetime.now(JST).isoformat(),
                "record_count": len(records_clean),
                "records": records_clean,
            }

            records_gcs_path = f"{GCS_RECORD}/{ticker}/monthly_records.json"
            if no_gcs:
                local = TMP_DIR / f"monthly_records_{ticker}.json"
                local.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info(f"  [{ticker}] ✅ ローカル保存: {local}")
            else:
                gcs_write_json(gcs, records_gcs_path, output)
                logger.info(f"  [{ticker}] ✅ GCS保存: gs://{GCS_BUCKET}/{records_gcs_path}")

            results["success"].append(ticker)

        logger.info(f"  Gemini Batch 完了: 成功={len([t for t in _deferred_companies if t in results['success']])} / {len(_deferred_companies)} 社")

    # サマリー
    logger.info(f"データ抽出: 成功={len(results['success'])} スキップ={len(results['skip'])} 失敗={len(results['fail'])}")

    # P2-1: 構造化エラーサマリー JSON を GCS に保存
    if not no_gcs and _error_entries:
        _save_extract_error_log(gcs, results, _error_entries)

    return results


# ====================================================
# メイン
# ====================================================

def main():
    parser = argparse.ArgumentParser(description="月次データ抽出（Phase 2）- extract_adapter.json からメトリクス値を抽出")
    parser.add_argument("--tickers", nargs="+", help="対象ティッカー（省略時は --sample で自動選定）")
    parser.add_argument("--sample", type=int, default=30, help="自動選定する銘柄数（デフォルト30）")
    parser.add_argument("--all", action="store_true", dest="all_mode",
                        help="BQ TDNET月次全銘柄 + CSV active 全銘柄を対象（--tickers/--sample を無視）")
    parser.add_argument("--since", type=int, default=2020, help="データ抽出の開始西暦年（年度ではない。デフォルト2020）")
    parser.add_argument("--no-gcs", action="store_true", help="GCS 保存スキップ（data/tmp/ にローカル保存）")
    parser.add_argument("--batch", action="store_true", default=IS_CLOUD_RUN,
                        help="Gemini 抽出をバッチモードで実行（Cloud Run ではデフォルト有効）")
    parser.add_argument("--no-batch", action="store_true", help="バッチモードを無効化（同期モードで実行）")
    args = parser.parse_args()

    batch_mode = args.batch and not args.no_batch

    logger = setup_logging()
    logger.info(f"=== extract_monthly_data 開始 since={args.since} all={args.all_mode} batch={batch_mode} ===")

    # クライアント初期化
    gcs = get_gcs()
    bq = get_bq()

    # 対象銘柄選定
    companies = load_active_companies(args.tickers, args.sample, bq, all_mode=args.all_mode)
    logger.info(f"対象: {len(companies)} 社")
    for c in companies[:5]:
        logger.debug(f"  {c['ticker']} {c['company_name']} ({c.get('type','')})")

    # Phase 2: データ抽出
    logger.info("--- Phase 2: データ抽出 ---")
    phase_extract(companies, bq, gcs, args.since, args.no_gcs, logger, batch_mode=batch_mode)

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
