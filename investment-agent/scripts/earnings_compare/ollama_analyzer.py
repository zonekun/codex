"""Ollama HTTP API クライアント.

Ollama サーバー（localhost:11434）に対してプロンプトを送信し、
Qwen2.5:7b からのレスポンスを取得する。
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

import requests
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()

OLLAMA_HOST = "http://localhost:11434"
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen2.5:7b")
OLLAMA_STARTUP_WAIT_SEC = 120   # 最大待機秒数（GCS FUSE からのモデルロードを考慮）
OLLAMA_CHECK_INTERVAL_SEC = 3
REQUEST_TIMEOUT_SEC = 1800      # 生成タイムアウト（30分）


def start_ollama_server() -> subprocess.Popen:
    """Ollama サーバーをサブプロセスで起動する.

    Returns:
        起動した Popen オブジェクト。
    """
    logger.info("ollama_server_starting")
    proc = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_ollama(timeout_sec: int = OLLAMA_STARTUP_WAIT_SEC) -> bool:
    """Ollama サーバーが起動するまで待機する.

    Args:
        timeout_sec: 最大待機秒数。

    Returns:
        起動成功なら True、タイムアウトなら False。
    """
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
            if r.status_code == 200:
                logger.info("ollama_server_ready",
                            elapsed_sec=round(time.time() - start, 1))
                return True
        except requests.RequestException:
            pass
        time.sleep(OLLAMA_CHECK_INTERVAL_SEC)

    logger.error("ollama_server_timeout", timeout_sec=timeout_sec)
    return False


def check_model_loaded() -> bool:
    """指定モデルが Ollama に認識されているか確認する.

    Returns:
        モデルが存在すれば True。
    """
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
def generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = MODEL_NAME,
    temperature: float = 0.1,
) -> str:
    """Ollama に推論リクエストを送信してレスポンスを取得する.

    Args:
        prompt: ユーザープロンプト。
        system_prompt: システムプロンプト（None の場合は使用しない）。
        model: 使用するモデル名。
        temperature: 生成温度（低いほど決定論的）。

    Returns:
        生成テキスト。

    Raises:
        requests.HTTPError: Ollama API エラー時。
    """
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 32768,
        },
    }
    if system_prompt:
        payload["system"] = system_prompt

    logger.info("ollama_generate_start", model=model, prompt_chars=len(prompt))
    start = time.time()

    r = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json=payload,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    r.raise_for_status()

    response_text = r.json().get("response", "")
    elapsed = round(time.time() - start, 1)
    logger.info("ollama_generate_done", model=model,
                response_chars=len(response_text), elapsed_sec=elapsed)
    return response_text
