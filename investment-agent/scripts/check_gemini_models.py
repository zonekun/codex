"""Gemini 新モデルの Vertex AI 利用可否確認スクリプト

使い方:
    PYTHONUTF8=1 python scripts/check_gemini_models.py

staged rollout 中のモデルが使えるようになったか週次で確認する。
"""

import json
import os
import sys
from datetime import datetime

import vertexai
from google.oauth2 import service_account

PROJECT = "gmailpj-357912"
LOCATION = "us-central1"
KEY_PATH = os.path.join(os.path.dirname(__file__), "..", "keys", "gcp-service-account.json")

# 確認対象モデル（staged rollout 中 / 新登場候補）
WATCH_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-preview-customtools",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro",
    "gemini-3.1-flash",
]

# 既知の利用可能モデル（確認不要）
KNOWN_OK = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-image",
]


def main() -> None:
    with open(KEY_PATH, encoding="utf-8") as f:
        key = json.load(f)
    creds = service_account.Credentials.from_service_account_info(
        key, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    vertexai.init(project=PROJECT, location=LOCATION, credentials=creds)

    from vertexai.generative_models import GenerativeModel

    print(f"=== Gemini モデル利用可否チェック ({datetime.now().strftime('%Y-%m-%d')}) ===")
    print(f"プロジェクト: {PROJECT} / リージョン: {LOCATION}\n")

    newly_available = []

    print("【監視中モデル】")
    for mid in WATCH_MODELS:
        try:
            m = GenerativeModel(mid)
            m.generate_content("hi")
            print(f"  ✅ 新たに利用可能: {mid}")
            newly_available.append(mid)
        except Exception as e:
            if "404" in str(e):
                print(f"  ⏳ まだ未到達:     {mid}")
            else:
                print(f"  ⚠️  別エラー:       {mid} → {str(e)[:60]}")

    print("\n【既知の利用可能モデル（参考）】")
    for mid in KNOWN_OK:
        try:
            m = GenerativeModel(mid)
            m.generate_content("hi")
            print(f"  ✅ {mid}")
        except Exception as e:
            print(f"  ❌ {mid} → {str(e)[:60]}")

    if newly_available:
        print(f"\n🎉 新たに使えるようになったモデル: {newly_available}")
        print("→ MEMORY.md の「使用可能」リストに追加してください")
    else:
        print("\n→ 監視中モデルはまだ利用不可。来週また確認してください。")


if __name__ == "__main__":
    main()
