#!/usr/bin/env node
/**
 * FRED MCP Server - SSE entrypoint for Cloud Run
 *
 * StdioServerTransport の代わりに SSEServerTransport + Express を使い、
 * Cloud Run で /sse エンドポイントを公開する。
 * アクセス制御は Cloud Run IAM (--no-allow-unauthenticated) で行う。
 */
import express, { type Request, type Response } from "express";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import dotenv from "dotenv";
import { tools, type Tool } from "./tools.js";

dotenv.config();

if (!process.env.FRED_API_KEY) {
  throw new Error("FRED_API_KEY environment variable is required");
}

function createServer(): Server {
  const server = new Server(
    { name: "fred-mcp-server", version: "0.1.0" },
    { capabilities: { tools: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: tools.map(({ name, description, inputSchema }) => ({
      name,
      description,
      inputSchema,
    })),
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const tool = tools.find((t) => t.name === request.params.name) as Tool;
    if (!tool) {
      return {
        content: [{ type: "text", text: `Unknown tool: ${request.params.name}` }],
        isError: true,
      };
    }
    try {
      const result = await tool.handler(request.params.arguments ?? {});
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `FRED API error: ${error instanceof Error ? error.message : String(error)}`,
          },
        ],
        isError: true,
      };
    }
  });

  return server;
}

const app = express();
app.use(express.json());

// SSE セッションごとに transport を保持
const transports = new Map<string, SSEServerTransport>();

app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", server: "fred-mcp-server", version: "0.1.0" });
});

app.get("/sse", async (_req: Request, res: Response) => {
  console.log("New SSE connection");
  const transport = new SSEServerTransport("/messages/", res);
  transports.set(transport.sessionId, transport);

  const server = createServer();
  await server.connect(transport);

  res.on("close", () => {
    console.log(`SSE closed: ${transport.sessionId}`);
    transports.delete(transport.sessionId);
  });
});

app.post("/messages/", async (req: Request, res: Response) => {
  const sessionId = req.query.sessionId as string;
  const transport = transports.get(sessionId);
  if (!transport) {
    res.status(404).json({ error: "Session not found" });
    return;
  }
  await transport.handlePostMessage(req, res);
});

const port = parseInt(process.env.PORT ?? "8080", 10);
app.listen(port, "0.0.0.0", () => {
  console.log(`FRED MCP Server (SSE) listening on 0.0.0.0:${port}`);
});
