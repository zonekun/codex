"""J-Quants API V2 共通ユーティリティ.

全 J-Quants 系スクリプトからインポートして使用する。
- API キー取得（実行環境自動判別）
- paginated GET（レートリミット自動リトライ付き）

使い方:
    from jquants_common import get_jquants_api_key, jquants_get

    headers = {"x-api-key": get_jquants_api_key(RUNTIME)}
    records = jquants_get("/fins/summary", {"date": "20260307"}, headers)
"""

import os
import time
import requests

BASE_URL = "https://api.jquants.com/v2"


def get_jquants_api_key(runtime: str) -> str:
    """実行環境に応じて J-Quants API キーを取得する.

    Args:
        runtime: detect_runtime() の戻り値
                 "colab_personal" | "colab_enterprise" | "cloudrun" | "local"

    Returns:
        J-Quants API キー文字列

    Raises:
        KeyError: cloudrun / local で JQUANTS_API_KEY 環境変数が未設定の場合
    """
    if runtime in ("colab_personal", "colab_enterprise"):
        from google.colab import userdata
        return userdata.get("JQUANTS_API_KEY")
    else:  # cloudrun / local
        return os.environ["JQUANTS_API_KEY"]


def jquants_get(
    endpoint: str,
    params: dict,
    headers: dict,
    sleep_sec: float = 0.5,
    retry_wait_sec: float = 30.0,
) -> list[dict]:
    """J-Quants API V2 GET リクエスト（ページネーション対応・レートリミット自動リトライ）.

    Args:
        endpoint:       エンドポイントパス（例: "/fins/summary"）
        params:         クエリパラメータ辞書（pagination_key は自動付与するため含めない）
        headers:        リクエストヘッダー（例: {"x-api-key": "..."}）
        sleep_sec:      リクエスト前のスリープ秒数（レートリミット対策）
        retry_wait_sec: 429 受信時の待機秒数

    Returns:
        全ページの "data" フィールドを結合したリスト。
        エラー（400/500系）の場合はその時点で取得できた分のみ返す。

    Example:
        headers = {"x-api-key": get_jquants_api_key(RUNTIME)}

        # 1日分取得
        records = jquants_get("/fins/summary", {"date": "20260307"}, headers)

        # ページネーションも自動処理
        records = jquants_get("/listed/info", {}, headers)
    """
    all_records: list[dict] = []
    params = dict(params)  # 呼び出し元の辞書を変更しない

    while True:
        time.sleep(sleep_sec)
        resp = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params)

        if resp.status_code == 429:
            print(f"  Rate limited, waiting {retry_wait_sec:.0f}s...")
            time.sleep(retry_wait_sec)
            continue  # リトライ（スリープは次ループ先頭で実施）

        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:200]}")
            break  # エラーはそれ以上試みない

        body = resp.json()
        all_records.extend(body.get("data", []))

        pagination_key = body.get("pagination_key")
        if not pagination_key:
            break
        params["pagination_key"] = pagination_key

    return all_records
