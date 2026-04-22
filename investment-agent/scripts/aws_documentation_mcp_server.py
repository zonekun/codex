"""AWS Documentation MCP Server entrypoint for Cloud Run (SSE transport).

mcp SDK の TransportSecurityMiddleware は DNS rebinding 保護のため
Cloud Run のホスト名（*.run.app）を拒否する。
Cloud Run は Google が管理するプロキシ経由なので、ホスト検証を無効化して安全に使用できる。
（アクセス制御は Cloud Run の IAM 認証で行う）
"""

import logging
import os

# ── DNS rebinding 保護を無効化（Cloud Run 向け）──────────────────────────
import mcp.server.transport_security as _ts
_ts.TransportSecurityMiddleware._validate_host = lambda self, host: True
_ts.TransportSecurityMiddleware._validate_origin = lambda self, origin: True
# ─────────────────────────────────────────────────────────────────────────

import uvicorn
# デフォルトパーティション: aws（AWS_DOCUMENTATION_PARTITION 環境変数で変更可）
from awslabs.aws_documentation_mcp_server.server_aws import mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting AWS Documentation MCP Server (SSE) on 0.0.0.0:%d", port)
    app = mcp.sse_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
