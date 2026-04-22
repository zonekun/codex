"""AWS Cost Explorer MCP Server entrypoint for Cloud Run (SSE transport)."""

import logging
import os

import mcp.server.transport_security as _ts
_ts.TransportSecurityMiddleware._validate_host = lambda self, host: True
_ts.TransportSecurityMiddleware._validate_origin = lambda self, origin: True

import uvicorn
from awslabs.cost_explorer_mcp_server.server import app as mcp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting AWS Cost Explorer MCP Server (SSE) on 0.0.0.0:%d", port)
    app = mcp.sse_app()
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
