"""決算特別シフト復帰時に 013_tdnet_load.md のタイトルから【...】を除去してGitHubコミットする.

revert Workflow の末尾から Cloud Run Job として呼び出される。
冪等: タイトルに【】が無ければ何もしない。
"""

import base64
import re
import sys

import httpx
import structlog
from google.cloud import secretmanager

logger = structlog.get_logger()

PROJECT_ID = "gmailpj-357912"
REPO = "zonekun/claude"
FILE_PATH = "investment-agent/docs/knowledges/tools/013_tdnet_load.md"
SECRET_NAME = f"projects/{PROJECT_ID}/secrets/github-pat/versions/latest"
SHIFT_PATTERN = re.compile(r"【[^】]*決算特別シフト[^】]*】")


def get_github_token() -> str:
    """Secret Manager から GitHub PAT を取得する."""
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=SECRET_NAME)
    return response.payload.data.decode("utf-8-sig").replace("﻿", "").strip()


def main() -> None:
    """013 MD のタイトルから決算特別シフトマーカーを除去する."""
    token = get_github_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    resp = httpx.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    content = base64.b64decode(data["content"]).decode("utf-8")
    sha = data["sha"]

    lines = content.split("\n")
    lines[0] = SHIFT_PATTERN.sub("", lines[0]).rstrip()
    updated = "\n".join(lines)
    if updated == content:
        logger.info("no_shift_marker_found", file=FILE_PATH)
        return

    resp = httpx.put(
        url,
        headers=headers,
        timeout=30,
        json={
            "message": "docs: 決算特別シフト自動復帰 — 013 MDタイトル更新",
            "content": base64.b64encode(updated.encode("utf-8")).decode("ascii"),
            "sha": sha,
        },
    )
    resp.raise_for_status()
    commit_sha = resp.json()["commit"]["sha"]
    logger.info("md_updated", commit=commit_sha, file=FILE_PATH)


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as e:
        logger.error(
            "github_api_error",
            status_code=e.response.status_code,
            response_body=e.response.text[:500],
            url=str(e.request.url),
        )
        sys.exit(1)
    except Exception:
        logger.exception("failed")
        sys.exit(1)
