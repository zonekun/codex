"""AWS MCP サーバー 自然言語検索（Colab Personal 用）

Gemini がエージェントとして複数のツールを自律的に呼び出し、AWS の料金・ドキュメントを調査する。
MCP の生レスポンスをそのまま Gemini に渡し、自然な日本語文章で回答させる。

使い方:
  1. このファイルを Colab にアップロード
  2. Colab Secrets に GCP_SA_KEY を登録（GCP サービスアカウント JSON の文字列）
  3. セルを上から順に実行 → 最後のセルでインタラクティブ検索が起動する

接続先 MCP サーバー:
  aws-pricing-mcp         https://aws-pricing-mcp-480182964684.us-west1.run.app
  aws-documentation-mcp   https://aws-documentation-mcp-480182964684.us-west1.run.app
  aws-cost-explorer-mcp   https://aws-cost-explorer-mcp-480182964684.us-west1.run.app
"""

# =============================================================================
# セル 1: パッケージインストール（初回のみ）
# =============================================================================
# !pip install -q mcp google-auth google-auth-httplib2 nest_asyncio google-cloud-aiplatform

# =============================================================================
# セル 2: 初期設定
# =============================================================================

import asyncio
import json
import os
import re

import nest_asyncio
nest_asyncio.apply()

import google.auth.transport.requests
from google.oauth2 import service_account
from mcp.client.sse import sse_client
from mcp import ClientSession

AWS_PRICING_URL      = "https://aws-pricing-mcp-480182964684.us-west1.run.app"
AWS_DOCS_URL         = "https://aws-documentation-mcp-480182964684.us-west1.run.app"
AWS_COST_EXPLORER_URL = "https://aws-cost-explorer-mcp-480182964684.us-west1.run.app"
GCP_PROJECT          = "gmailpj-357912"
GCP_LOCATION         = "us-central1"


# ── 認証 ──────────────────────────────────────────────────────────────────────

def _load_sa_key() -> dict:
    try:
        from google.colab import userdata  # type: ignore
        return json.loads(userdata.get("GCP_SA_KEY"))
    except Exception:
        key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
        with open(key_path, encoding="utf-8") as f:
            return json.load(f)


def _get_id_token(target_url: str) -> str:
    key_info = _load_sa_key()
    creds = service_account.IDTokenCredentials.from_service_account_info(
        key_info, target_audience=target_url
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ── MCP クライアント ──────────────────────────────────────────────────────────

async def _call_tool_async(base_url: str, tool_name: str, arguments: dict) -> str:
    token = _get_id_token(base_url)
    async with sse_client(
        f"{base_url}/sse",
        headers={"Authorization": f"Bearer {token}"},
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            texts = [getattr(c, "text", str(c)) for c in result.content]
            return "\n".join(texts)


def _call_tool(base_url: str, tool_name: str, arguments: dict) -> str:
    return asyncio.run(_call_tool_async(base_url, tool_name, arguments))




# ── Gemini エージェント ────────────────────────────────────────────────────────

_AGENT_SYSTEM_PROMPT = """
あなたは AWS 情報調査エージェントです。
ユーザーの質問に答えるため、3つの MCP サーバーを**横断的に**使って情報を収集してください。
1つのサーバーで情報が不足・未取得だった場合は、必ず他のサーバーも試みてから回答してください。

=== 利用可能なツール（AWS Pricing MCP）===
1. get_pricing
   - service_code : AWS サービスコード (例: "AmazonRDS", "AmazonEC2", "AWSLambda")
   - region       : リージョンコード (例: "ap-northeast-1")
   - max_results  : 取得件数上限（通常 50 を指定）
   - filters      : 【重要】キーは必ず大文字始まり Field / Type / Value
     例: [{"Field": "databaseEngine", "Type": "TERM_MATCH", "Value": "Aurora PostgreSQL"},
          {"Field": "instanceType",   "Type": "TERM_MATCH", "Value": "db.r5.large"}]

2. get_pricing_attribute_values
   - service_code    : AWS サービスコード
   - attribute_names : 値を調べたい属性名のリスト
   - filters         : キーは大文字始まり Field/Type/Value

3. get_pricing_service_codes
   - filter : 絞り込む文字列（任意）

=== 利用可能なツール（AWS Documentation MCP）===
4. search_documentation
   - query : 検索クエリ文字列
5. read_documentation
   - url : 取得する AWS ドキュメントの URL

=== 利用可能なツール（AWS Cost Explorer MCP）===
6. get_cost_and_usage  : 実際のAWS利用コストを取得（実績ベース）
7. get_cost_forecast   : AWSコストを予測

=== 重要な知識 ===
- Aurora PostgreSQL: service_code="AmazonRDS", Filter: {"Field":"databaseEngine","Type":"TERM_MATCH","Value":"Aurora PostgreSQL"}
- filters のキーは必ず大文字: "Field" / "Type" / "Value"
- リージョン: ap-northeast-1=東京, us-east-1=バージニア, us-west-2=オレゴン
- RI (Reserved Instance) の割引率は Pricing MCP に含まれる場合がある（purchaseOption / leaseContractLength フィルタで絞る）
  Pricing MCP で RI が見つからない場合は Cost Explorer MCP の get_cost_and_usage でも確認する

=== MCP 横断ルール（最重要）===
- **「見つからなかった」「提供されていなかった」で即 done にしない**
- あるサーバーで情報が不足・空だった場合は、別のサーバーを必ず試みること
- RI 割引率が Pricing MCP で取れなければ → Cost Explorer MCP で確認する
- 料金が不明なら → Documentation MCP で検索してドキュメントを参照する
- すべてのサーバーを試みて初めて「情報なし」と結論付けてよい

=== 絶対ルール ===
- 純粋な JSON のみ返す（コードブロック禁止）
- 同じツール+同じ引数を2回呼ばない

=== 回答形式 ===
ツール呼び出し: {"action": "call", "server": "pricing"|"docs"|"cost_explorer", "tool": "...", "args": {...}, "reason": "..."}
最終回答:       {"action": "done", "answer": "日本語の自然な文章で回答。数値の羅列ではなく読みやすい説明文にすること。どのサーバーから情報を取得したかも明記すること"}
"""

_CONTINUE_PROMPT = """
前のツール呼び出し結果:
{result}

次のアクションを純粋な JSON で返してください（コードブロック禁止）。
同じツール+引数の再呼び出し禁止。

【重要】情報が不足・空だった場合は別のサーバー（pricing/docs/cost_explorer）を試みること。
すべてのサーバーを試みて初めて "action": "done" で最終回答を返してください。
"""


def _gemini_step(history: list[dict]) -> dict:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Content, Part, GenerationConfig

    key_info = _load_sa_key()
    creds = service_account.Credentials.from_service_account_info(
        key_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION, credentials=creds)

    # thinking_budget=0 で思考トークンを無効化（Gemini 2.5 の thinking が JSON に混入するのを防ぐ）
    generation_config = GenerationConfig(response_mime_type="application/json")
    try:
        from vertexai.generative_models import ThinkingConfig  # SDK >= 1.71 で利用可能
        generation_config = GenerationConfig(
            response_mime_type="application/json",
            thinking_config=ThinkingConfig(thinking_budget=0),
        )
    except (ImportError, TypeError):
        pass  # 古い SDK では thinking_config 未対応 → response_mime_type のみで継続

    model = GenerativeModel(
        "gemini-2.5-flash",
        system_instruction=_AGENT_SYSTEM_PROMPT,
        generation_config=generation_config,
    )
    contents = [Content(role=m["role"], parts=[Part.from_text(m["text"])]) for m in history]

    response = model.generate_content(contents)

    # テキスト抽出: thinking パートを除外して output テキストのみ結合
    raw_parts = []
    try:
        for part in response.candidates[0].content.parts:
            if not getattr(part, "thought", False):  # thought=True は thinking パート
                if hasattr(part, "text") and part.text:
                    raw_parts.append(part.text)
        raw = "".join(raw_parts).strip()
    except Exception:
        raw = response.text.strip()  # フォールバック

    raw = re.sub(r"```(?:json)?\s*", "", raw).strip("`").strip()

    # 最初の { から対応する } までをネスト対応で抽出
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"JSON オブジェクトが見つかりません: {raw[:300]}")
    depth, end = 0, -1
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError(f"JSON の閉じ括弧が見つかりません: {raw[:300]}")
    return json.loads(raw[start:end + 1])


def _run_agent(user_query: str, max_steps: int = 100) -> str:
    history = [{"role": "user", "text": f"ユーザーの質問: {user_query}"}]
    called: set[str] = set()

    for step in range(max_steps):
        print(f"  [ステップ {step + 1}] Gemini が判断中...", end=" ", flush=True)

        try:
            action = _gemini_step(history)
        except Exception as e:
            print(f"→ エラー: {e}")
            history.append({"role": "model", "text": "(不正な JSON)"})
            history.append({"role": "user", "text": f"JSON パース失敗({e})。コードブロック禁止、純粋な JSON のみ返してください。"})
            continue

        if action.get("action") == "done":
            print("→ 回答生成")
            return action.get("answer", "（回答なし）")

        server = action.get("server", "pricing")
        tool   = action.get("tool", "")
        args   = action.get("args", {})

        if server == "pricing":
            base_url = AWS_PRICING_URL
        elif server == "cost_explorer":
            base_url = AWS_COST_EXPLORER_URL
        else:
            base_url = AWS_DOCS_URL

        # 重複呼び出し検出
        call_key = f"{tool}:{json.dumps(args, sort_keys=True)}"
        if call_key in called:
            print("→ 重複スキップ")
            history.append({"role": "model", "text": json.dumps(action, ensure_ascii=False)})
            history.append({"role": "user", "text": "その呼び出しは済み。別のアクションを取るか \"action\":\"done\" で回答してください。"})
            continue
        called.add(call_key)

        print(f"→ {tool}({json.dumps(args, ensure_ascii=False)[:80]})")

        try:
            raw = _call_tool(base_url, tool, args)
        except Exception as e:
            raw = f"ツールエラー: {e}"

        result_text = raw[:10000]
        print(f"    → {len(raw)} 文字 (先頭 10000 文字を渡す)")

        history.append({"role": "model", "text": json.dumps(action, ensure_ascii=False)})
        history.append({"role": "user", "text": _CONTINUE_PROMPT.format(result=result_text)})

    return "最大ステップ数（100）に達しました。"


# =============================================================================
# セル 3: インタラクティブ検索
# =============================================================================

def interactive_search() -> None:
    """自然言語で AWS を調査するインタラクティブループ。"""
    print("=" * 60)
    print("  AWS MCP 自然言語検索  (Powered by Gemini Agent)")
    print("  料金・RI割引率・ドキュメント、何でも日本語で聞けます")
    print("  「q」で終了")
    print("=" * 60)

    while True:
        query = input("\n> ").strip()
        if not query:
            continue
        if query.lower() in ("q", "quit", "exit", "終了"):
            print("終了します。")
            break

        print()
        answer = _run_agent(query)
        print("\n─── 回答 ───────────────────────────────────────────")
        print(answer)
        print("────────────────────────────────────────────────────")


# =============================================================================
# セル 4: 起動
# =============================================================================

if __name__ == "__main__":
    interactive_search()
