"""
generate_activist_aliases.py
============================
activists.csv の各ファンドについて Gemini 2.5 Pro に関連法人名・ファンドビークル名を
生成させ、activist_aliases.csv に追記する。

使用方法:
  PYTHONUTF8=1 python scripts/generate_activist_aliases.py
  PYTHONUTF8=1 python scripts/generate_activist_aliases.py --dry-run  # 生成結果を確認のみ
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pandas as pd
import structlog
from google import genai
from google.oauth2 import service_account

ACTIVISTS_CSV = Path(__file__).parent.parent / "data/master/activists.csv"
ALIASES_CSV   = Path(__file__).parent.parent / "data/master/activist_aliases.csv"
KEY_FILE      = Path(__file__).parent.parent / "keys/gcp-service-account.json"

GCP_PROJECT  = "gmailpj-357912"
GCP_LOCATION = "us-central1"
MODEL        = "gemini-2.5-pro"

PROMPT_TEMPLATE = """\
以下のアクティビスト投資ファンドが、日本の株式市場（大量保有報告書・有価証券報告書の大株主欄）で使用する関連法人名・ファンドビークル名・SPC名・グループ会社名・個人名義などを列挙してください。

ファンド名: {name}
本拠地: {region}

条件:
- 日本の開示書類に実際に登場する名称のみ（推測・架空は除く）
- 本体名称と完全に同一のものは除く
- **英語名がある場合は、日本の書類で使われるカタカナ表記も必ずセットで含める**
  例: "Oasis Management Company Ltd." → "オアシス・マネジメント・カンパニー・リミテッド" も追加
- 英語とカタカナは別エントリとして列挙する
- 不明な場合は空リストを返す
- JSON配列のみ返す（説明文不要）

出力形式（例）:
["ファンドビークル英語名", "ファンドビークルカタカナ名", "SPC名", "関連法人名"]
"""

log = structlog.get_logger()


def build_client() -> genai.Client:
    """サービスアカウントキーで Vertex AI クライアントを構築する."""
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", str(KEY_FILE))
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
        credentials=creds,
    )


def load_existing_aliases() -> set[tuple[str, str]]:
    """既存エイリアスの (activist_name, alias) セットを返す."""
    if not ALIASES_CSV.exists():
        return set()
    df = pd.read_csv(ALIASES_CSV, encoding="utf-8")
    return {(r["ACTIVIST_NAME"], r["ALIAS"]) for _, r in df.iterrows()}


def generate_aliases_for(client: genai.Client, name: str, region: str) -> list[str]:
    """Gemini に1ファンドのエイリアスを生成させる."""
    prompt = PROMPT_TEMPLATE.format(name=name, region=region)
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        # JSONブロックを抽出
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        aliases = json.loads(text)
        if isinstance(aliases, list):
            return [str(a).strip() for a in aliases if a]
    except Exception as e:
        log.warning("alias生成エラー", name=name, error=str(e))
    return []


def main() -> None:
    """メイン処理."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="CSVに保存せず結果を表示のみ")
    args = parser.parse_args()

    client = build_client()
    df_activists = pd.read_csv(ACTIVISTS_CSV, encoding="utf-8")
    existing = load_existing_aliases()

    new_rows: list[dict] = []

    for _, row in df_activists.iterrows():
        name   = row["NAME"]
        region = row["REGION"]
        log.info("処理中", name=name, region=region)

        aliases = generate_aliases_for(client, name, region)
        if not aliases:
            log.info("エイリアスなし", name=name)
            continue

        added = 0
        for alias in aliases:
            if (name, alias) not in existing:
                new_rows.append({"ACTIVIST_NAME": name, "ALIAS": alias})
                existing.add((name, alias))
                added += 1
                log.info("追加", activist=name, alias=alias)
            else:
                log.info("既存スキップ", activist=name, alias=alias)

        time.sleep(1)

    if args.dry_run:
        log.info("dry-run完了", new_count=len(new_rows))
        return

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        if ALIASES_CSV.exists():
            df_existing = pd.read_csv(ALIASES_CSV, encoding="utf-8")
            df_out = pd.concat([df_existing, df_new], ignore_index=True)
        else:
            df_out = df_new
        df_out.to_csv(ALIASES_CSV, index=False, encoding="utf-8")
        log.info("保存完了", new_count=len(new_rows), path=str(ALIASES_CSV))
    else:
        log.info("新規エイリアスなし")


if __name__ == "__main__":
    main()
