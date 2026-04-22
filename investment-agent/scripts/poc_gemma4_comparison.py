"""Gemma 4 PoC: TDnet分類精度比較スクリプト.

既存Gemini分析結果（BQ）とGemma 4の出力を比較し、
分類精度（MAIN_CATEGORY, SUB_CATEGORIES）の一致度を評価する。

Usage:
    PYTHONUTF8=1 python scripts/poc_gemma4_comparison.py --date 20240124
    PYTHONUTF8=1 python scripts/poc_gemma4_comparison.py --date-from 20240101 --date-to 20240131
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.truncation import truncate_for_model  # noqa: E402
from src.llm.page_aware_text import reconstruct_from_chunks  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
logger = structlog.get_logger()

# poc_gemma4_comparison.py は Gemma 4 26B (MaaS) を比較対象とする前提
_GEMMA_MODEL_FOR_TRUNCATE = "gemma-4-26b-a4b-it-maas"

# ── 設定 ──────────────────────────────────────────
GCP_PROJECT = "gmailpj-357912"
SA_KEY_PATH = "keys/gcp-service-account.json"
BQ_TABLE = f"{GCP_PROJECT}.STOCK.TDNET_DOCUMENTS_ENHANCED"
OUTPUT_DIR = Path("C:/tmp/gemma4_poc")

# テスト対象モデル
GEMMA_MODELS = [
    "gemma-4-26b-a4b-it-maas",
]

# Phase 3 対象カテゴリ（tdnet_load_parallel.py と同一）
# この MAIN_CATEGORY のドキュメントのみが Gemini Phase 3 に送られる
_NEEDS_GEMINI_ANALYSIS: set[str] = {
    "その他（未分類）",           # is_monthly 判定のみ
    "業績予想", "大型受注・契約", "受注・契約",
    "業績の重要な先行指標", "受注高/受注残高",  # is_monthly 判定のみ
    "決算短信", "決算説明資料",   # is_monthly + sub_categories 保存
}
# sub_categories が BQ に保存されるのは以下のみ
_NEEDS_SUB_CATEGORIES: set[str] = {"決算短信", "決算説明資料"}

# Gemini側のバリッドカテゴリ（tdnet_load_parallel.pyと同一）
VALID_CATEGORIES = [
    "決算短信", "決算説明資料", "業績修正", "業績予想", "月次開示",
    "配当", "配当変更（増減配）", "株主優待", "自己株式取得", "自己株式消却",
    "株式分割・併合", "第三者割当・公募増資", "新株予約権発行", "株式売出し",
    "TOB・MBO", "子会社化・買収", "資産売却（不動産）",
    "合併・組織再編", "会社分割", "上場廃止", "主要株主異動",
    "大型受注・契約", "提携・協業", "事業計画（グロース）", "中期経営計画",
    "リストラ・希望退職", "特別利益", "特別損失",
    "役員異動（代表クラス）", "監査人異動",
    "インシデント（セキュリティ）", "訴訟・法的手続き",
    "受注高/受注残高", "業績の重要な先行指標",
]
VALID_CATEGORIES_STR = "\n".join(f"- {c}" for c in VALID_CATEGORIES)

# 2分割版カテゴリ（グループ1: 業績・財務系、グループ2: コーポレートアクション系）
VALID_CATEGORIES_G1 = [
    "決算短信", "決算説明資料", "業績修正", "業績予想", "月次開示",
    "配当", "特別利益", "特別損失",
    "受注高/受注残高", "業績の重要な先行指標",
    "大型受注・契約", "事業計画（グロース）", "中期経営計画",
    "リストラ・希望退職", "インシデント（セキュリティ）",
]
VALID_CATEGORIES_G2 = [
    "配当変更（増減配）",  # 先頭に配置
    "株主優待", "自己株式取得", "自己株式消却",
    "株式分割・併合", "第三者割当・公募増資", "新株予約権発行", "株式売出し",
    "TOB・MBO", "子会社化・買収", "資産売却（不動産）",
    "合併・組織再編", "会社分割", "上場廃止", "主要株主異動",
    "提携・協業", "役員異動（代表クラス）", "監査人異動", "訴訟・法的手続き",
]
VALID_CATEGORIES_G1_STR = "\n".join(f"- {c}" for c in VALID_CATEGORIES_G1)
VALID_CATEGORIES_G2_STR = "\n".join(f"- {c}" for c in VALID_CATEGORIES_G2)


def _get_credentials():
    """GCP認証情報を取得."""
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_bq_client():
    """BigQueryクライアント."""
    from google.cloud import bigquery
    return bigquery.Client(
        project=GCP_PROJECT,
        credentials=_get_credentials(),
    )


def _get_genai_client():
    """google-genai クライアント（global endpoint）."""
    from google import genai
    return genai.Client(
        vertexai=True,
        project=GCP_PROJECT,
        location="global",
        credentials=_get_credentials(),
    )


def fetch_gemini_results(
    bq_client, date_from: str, date_to: str,
) -> list[dict]:
    """BQから既存Gemini分類結果をドキュメント単位で取得.

    CHUNK_TEXT を連結して元テキストを復元する。
    新フォーマット（[PAGE N] マーカー付き）と旧フォーマットが混在しているため、
    `reconstruct_from_chunks()` で適切に連結する。
    """
    sql = f"""
    WITH doc_texts AS (
        SELECT
            DOC_ID,
            TICKER,
            FILER_NAME,
            DOC_TITLE,
            MAIN_CATEGORY,
            SUB_CATEGORIES,
            TEXT_LENGTH,
            ARRAY_AGG(CHUNK_TEXT ORDER BY CHUNK_TEXT) AS chunk_list
        FROM `{BQ_TABLE}`
        WHERE SUBMISSION_DATE BETWEEN '{date_from}' AND '{date_to}'
        GROUP BY DOC_ID, TICKER, FILER_NAME, DOC_TITLE,
                 MAIN_CATEGORY, SUB_CATEGORIES, TEXT_LENGTH
    )
    SELECT *
    FROM doc_texts
    ORDER BY TICKER, DOC_ID
    """
    rows = list(bq_client.query(sql).result())
    docs = []
    for row in rows:
        chunks = [c for c in (row.chunk_list or []) if c]
        docs.append({
            "doc_id": row.DOC_ID,
            "ticker": row.TICKER,
            "filer_name": row.FILER_NAME,
            "doc_title": row.DOC_TITLE,
            "main_category": row.MAIN_CATEGORY,
            "sub_categories": list(row.SUB_CATEGORIES) if row.SUB_CATEGORIES else [],
            "text_length": row.TEXT_LENGTH,
            "full_text": reconstruct_from_chunks(chunks),
        })
    return docs


def build_prompt_gemini(doc_title: str, text: str) -> str:
    """Gemini版プロンプト（tdnet_load_parallel.py _build_merged_prompt と同一）."""
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」（月次売上・月次業績・月次受注、月次顧客数等の、企業業績に影響ある定期的な月次報告）ですか？

【タスク2: サブカテゴリ抽出】
投資判断に影響を与える【他カテゴリの重要情報】が内包されているか抽出してください。

以下のカテゴリ名からのみ選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_STR}

※カテゴリ選択における特記事項:
- 「業績の重要な先行指標」: SaaSの解約率やARPU、小売の新規出店数/退店数、販売数量・出荷台数、不動産の客室稼働率・オフィス入居率など
- 「受注高/受注残高」: 上記の先行指標の一部だが、極めて重要な情報のため独立カテゴリとして選択

文書タイトル: {doc_title}
テキスト: {truncate_for_model(text, _GEMMA_MODEL_FOR_TRUNCATE)}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def build_prompt_gemma(doc_title: str, text: str) -> str:
    """Gemma最適化版プロンプト.

    変更点:
    - 網羅性を明示的に指示（メインテーマだけでなく言及レベルも拾う）
    - 受注高/受注残高と業績の重要な先行指標を独立カテゴリとして明確化
    - 見落としやすいカテゴリの判定基準を具体化
    - 月次開示の定義に除外条件を明示（四半期KPI開示の誤認防止）
    """
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」ですか？

月次開示の定義: 毎月1回の頻度で開示される、月単位の売上・業績・受注・顧客数等の定期報告。
該当する例: 「2024年3月度 月次売上高」「12月 月次IRニュース」
該当しない例:
- 四半期決算に伴う開示（「第3四半期の売上状況」「Q3主要KPI」等）→ 開示頻度が四半期なので月次ではない
- 四半期末時点のストック数（「第3四半期末現在の○○数」）→ 四半期スナップショットであり月次ではない
- タイトルに「月」を含んでも四半期単位なら月次ではない

【タスク2: サブカテゴリ抽出】
この文書に含まれる投資判断に関連する情報カテゴリをすべて抽出してください。

★重要ルール:
- 文書のメインテーマだけでなく、具体的な事実・実績・数値・施策・計画・見通しとして記載されている情報カテゴリを抽出すること
- 除外するのは以下のみ: 「株主還元方針として自己株式取得を検討」「成長を目指す」等の**具体性のない方針表明**、および連結決算における「連結子会社○社」等の**単なる事実の列挙**
- 配当金額・合併・提携・組織再編など、具体的な施策や金額が記載されていれば必ず含めること
- 以下のカテゴリ名から選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_STR}

★カテゴリ判定の具体的基準:
- 「受注高/受注残高」: 受注高・受注残高・受注額・オーダーの数値や前年比の記載があれば必ず選択。「業績の重要な先行指標」とは別に独立して選択すること（両方該当すれば両方出力）
- 「業績の重要な先行指標」: SaaS解約率・ARPU・新規出店数/退店数・販売数量・出荷台数・稼働率・入居率など、将来業績を予測するKPIの記載があれば選択
- 「配当変更（増減配）」: 以下のいずれかに該当すれば必ず選択:
  - 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
  - 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
  - 前期比・前回予想比で配当金額が変更されている記載
  「配当」とは別に独立して選択すること
- 「業績修正」: 以下のいずれかの文言・文脈があれば必ず選択:
  キーワード: 「修正」「上方修正」「下方修正」「前回発表予想からの変更」「予想との差異」「業績予想の修正」「修正に関するお知らせ」
  上方修正の文脈: 「当初予想を上回る」「通期予想を増額」
  下方修正の文脈: 「下回る見込み」「慎重な見通し」「減額」
  「業績予想」とは別カテゴリであり、両方同時に該当することが多い（予想値の開示＝「業績予想」、予想値の変更＝「業績修正」）
- 「事業計画（グロース）」: 新規事業への投資計画・事業拡大戦略・TAM/SAM分析・成長投資の具体的計画があれば選択。上場市場（グロース/プライム等）は問わない。「中期経営計画」とは別カテゴリであり、両方同時に該当することが多い
- 「中期経営計画」: 中期経営計画・中計の策定・進捗・見直しの記載がある場合のみ選択。以下は該当しない:
  - 決算短信の「今後の見通し」「経営方針」セクションの一般記述
  - 「成長を目指す」「収益力強化」等の抽象的な方針表明
- 「子会社化・買収」: 子会社の新規取得・株式取得・買収の具体的記載がある場合のみ選択。以下は該当しない:
  - 「連結子会社○社」「関係会社○社」等の社数記載
  - セグメント説明中の既存子会社名の列挙
  - 連結範囲の変更に関する注記の定型文
- 「自己株式取得」: 自己株式の取得枠設定・取得実施・取得状況の具体的記載がある場合のみ選択。株主還元方針として「自己株式取得を検討」等の一般的方針記述だけでは該当しない
- 「大型受注・契約」: 個別の大型受注・大口契約・大型案件の獲得の具体的記載がある場合のみ選択。受注残高の増減や一般的な受注動向は「受注高/受注残高」で対応
- 「資産売却（不動産）」: 固定資産・不動産・土地建物の売却・譲渡の具体的記載があれば選択
- 「株式分割・併合」: 株式分割・株式併合の実施や予定を発表する記載があれば選択（過去の分割を参考情報として言及しているだけの場合は除く）
- 「特別利益」「特別損失」: 特別利益・特別損失の計上の記載があればそれぞれ選択
- 「リストラ・希望退職」: 「希望退職」「人員削減」「構造改革費用」「事業撤退」の記載があれば選択
- 「配当」: 配当金額・配当予想・配当方針の記載があれば選択。決算短信の配当欄に数値があれば該当
- 「合併・組織再編」: 合併・会社分割・事業統合・組織再編の具体的記載があれば選択
- 「提携・協業」: 業務提携・資本提携・協業・共同開発の具体的記載があれば選択

文書タイトル: {doc_title}
テキスト: {truncate_for_model(text, _GEMMA_MODEL_FOR_TRUNCATE)}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def build_prompt_gemma_split_g1(doc_title: str, text: str) -> str:
    """2分割版 グループ1: 業績・財務系カテゴリ + is_monthly判定."""
    return f"""以下のTDnet適時開示文書を分析し、2つの判定を行ってください。

【タスク1: 月次開示判定】
タイトル「{doc_title}」は「月次開示」ですか？

月次開示の定義: 毎月1回の頻度で開示される、月単位の売上・業績・受注・顧客数等の定期報告。
該当する例: 「2024年3月度 月次売上高」「12月 月次IRニュース」
該当しない例:
- 四半期決算に伴う開示（「第3四半期の売上状況」「Q3主要KPI」等）→ 開示頻度が四半期なので月次ではない
- 四半期末時点のストック数（「第3四半期末現在の○○数」）→ 四半期スナップショットであり月次ではない
- タイトルに「月」を含んでも四半期単位なら月次ではない

【タスク2: サブカテゴリ抽出（グループ1: 業績・財務系）】
この文書に含まれる投資判断に関連する情報カテゴリをすべて抽出してください。

★重要ルール:
- 文書のメインテーマだけでなく、具体的な事実・実績・数値・施策・計画・見通しとして記載されている情報カテゴリを抽出すること
- 除外するのは以下のみ: 具体性のない方針表明、および連結決算における「連結子会社○社」等の単なる事実の列挙
- 以下のカテゴリ名から選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_G1_STR}

★カテゴリ判定の具体的基準:
- 「受注高/受注残高」: 受注高・受注残高・受注額・オーダーの数値や前年比の記載があれば必ず選択。「業績の重要な先行指標」とは別に独立して選択すること
- 「業績の重要な先行指標」: SaaS解約率・ARPU・新規出店数/退店数・販売数量・出荷台数・稼働率・入居率など、将来業績を予測するKPIの記載があれば選択
- 「業績修正」: 以下のいずれかの文言・文脈があれば必ず選択:
  キーワード: 「修正」「上方修正」「下方修正」「前回発表予想からの変更」「予想との差異」「業績予想の修正」「修正に関するお知らせ」
  上方修正の文脈: 「当初予想を上回る」「通期予想を増額」
  下方修正の文脈: 「下回る見込み」「慎重な見通し」「減額」
  「業績予想」とは別カテゴリであり、両方同時に該当することが多い
- 「事業計画（グロース）」: 新規事業への投資計画・事業拡大戦略・TAM/SAM分析・成長投資の具体的計画があれば選択。上場市場は問わない。「中期経営計画」とは別カテゴリであり、両方同時に該当することが多い
- 「中期経営計画」: 中期経営計画・中計の策定・進捗・見直しの記載がある場合のみ選択。以下は該当しない:
  - 決算短信の「今後の見通し」「経営方針」セクションの一般記述
  - 「成長を目指す」「収益力強化」等の抽象的な方針表明
- 「大型受注・契約」: 個別の大型受注・大口契約・大型案件の獲得の具体的記載がある場合のみ選択
- 「配当」: 配当金額・配当予想・配当方針の記載があれば選択。決算短信の配当欄に数値があれば該当
- 「特別利益」「特別損失」: 特別利益・特別損失の計上の記載があればそれぞれ選択
- 「リストラ・希望退職」: 「希望退職」「人員削減」「構造改革費用」「事業撤退」の記載があれば選択

文書タイトル: {doc_title}
テキスト: {truncate_for_model(text, _GEMMA_MODEL_FOR_TRUNCATE)}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "is_monthly": true, "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- is_monthly: 月次開示なら true、そうでなければ false
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


def build_prompt_gemma_split_g2(doc_title: str, text: str) -> str:
    """2分割版 グループ2: コーポレートアクション系カテゴリ."""
    return f"""以下のTDnet適時開示文書から、投資判断に関連する情報カテゴリをすべて抽出してください。

★重要ルール:
- 具体的な事実・実績・数値・施策として記載されている情報カテゴリを抽出すること
- 除外するのは以下のみ: 具体性のない方針表明、および連結決算における「連結子会社○社」等の単なる事実の列挙
- 配当金額・合併・提携・組織再編など、具体的な施策や金額が記載されていれば必ず含めること
- 以下のカテゴリ名から選択すること（一言一句違わず出力）:
{VALID_CATEGORIES_G2_STR}

★カテゴリ判定の具体的基準:
- 「配当変更（増減配）」: 以下のいずれかに該当すれば必ず選択:
  - 決算短信の「直近に公表されている配当予想からの修正の有無：有」の記載
  - 「増配」「減配」「配当予想の修正」「記念配当」「特別配当」の文言
  - 前期比・前回予想比で配当金額が変更されている記載
- 「子会社化・買収」: 子会社の新規取得・株式取得・買収の具体的記載がある場合のみ選択。以下は該当しない:
  - 「連結子会社○社」「関係会社○社」等の単なる社数記載
  - セグメント説明中の既存子会社名の列挙
  - 連結範囲の変更に関する注記の定型文
- 「自己株式取得」: 自己株式の取得枠設定・取得実施・取得状況の具体的記載がある場合のみ選択。株主還元方針として「自己株式取得を検討」等の一般的方針記述だけでは該当しない
- 「資産売却（不動産）」: 固定資産・不動産・土地建物の売却・譲渡の具体的記載があれば選択
- 「株式分割・併合」: 株式分割・株式併合の実施や予定を発表する記載があれば選択（過去の分割を参考情報として言及しているだけの場合は除く）
- 「合併・組織再編」: 合併・会社分割・事業統合・組織再編の具体的記載があれば選択
- 「提携・協業」: 業務提携・資本提携・協業・共同開発の具体的記載があれば選択

文書タイトル: {doc_title}
テキスト: {truncate_for_model(text, _GEMMA_MODEL_FOR_TRUNCATE)}

【出力形式】必ず単一のJSONオブジェクトのみを返してください。配列で包まないこと。
{{ "sub_categories": ["カテゴリ1", "カテゴリ2"] }}
- sub_categories: 抽出したカテゴリのリスト（該当なしは空リスト []）"""


# デフォルトはGemma版を使用（--gemini-prompt でGemini版に切替可能）
build_prompt = build_prompt_gemma


def call_gemma(
    client, model_name: str, prompt: str,
    max_retries: int = 3,
) -> dict | None:
    """Gemma モデルを呼び出し、JSON結果を返す."""
    from google.genai.types import GenerateContentConfig

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            text = resp.text.strip()
            # JSON解析
            if text.startswith("["):
                result = json.loads(text)[0]
            else:
                result = json.loads(text)
            return result
        except json.JSONDecodeError:
            # JSONパース失敗 → テキストから抽出試行
            try:
                import re
                m = re.search(r"\{[^}]+\}", resp.text)
                if m:
                    return json.loads(m.group())
            except Exception:
                pass
            logger.warning("json_parse_failed", attempt=attempt, text=resp.text[:200])
        except Exception as e:
            logger.warning("gemma_call_failed", attempt=attempt, error=str(e)[:200])
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def compare_results(
    gemini_doc: dict, gemma_result: dict | None,
) -> dict:
    """Gemini結果とGemma結果を比較.

    BQ補正ロジックを考慮した正確な比較:
    - is_monthly: BQ MAIN_CATEGORY=="月次開示" はパイプライン補正後の値。
      元カテゴリが "その他（未分類）" かつ Gemini is_monthly=True → "月次開示"に上書き。
      元カテゴリが {業績予想,大型受注・契約,受注・契約,業績の重要な先行指標,受注高/受注残高}
      かつ is_monthly=True → sub_categoriesに"月次開示"追加（MAIN_CATEGORYは変わらない）。
      → BQ MAIN_CATEGORY=="月次開示" は「元々月次開示」or「その他→月次上書き」の2パターン。

    - sub_categories: BQ には MAIN_CATEGORY ∈ {"決算短信","決算説明資料"} のドキュメントのみ
      Geminiのsub_categoriesが保存される。それ以外は空[]。
      → 比較はこの2カテゴリに限定すべき。
    """
    if gemma_result is None:
        return {
            "doc_id": gemini_doc["doc_id"],
            "ticker": gemini_doc["ticker"],
            "doc_title": gemini_doc["doc_title"],
            "status": "GEMMA_FAILED",
        }

    # ── is_monthly 比較 ──
    # BQ上 MAIN_CATEGORY=="月次開示" = パイプラインが is_monthly=True と判断
    gemini_is_monthly = gemini_doc["main_category"] == "月次開示"
    # 補足: MAIN_CATEGORY が _MONTHLY_SUB_CATEGORIES かつ "月次開示" ∈ sub_categories
    # の場合も実質的に月次判定されている
    _monthly_sub_cats = {"業績予想", "大型受注・契約", "受注・契約",
                         "業績の重要な先行指標", "受注高/受注残高"}
    if (gemini_doc["main_category"] in _monthly_sub_cats
            and "月次開示" in gemini_doc["sub_categories"]):
        gemini_is_monthly = True
    gemma_is_monthly = gemma_result.get("is_monthly", False)

    # ── sub_categories 比較 ──
    # BQにsub_categoriesが保存されるのは決算短信・決算説明資料のみ
    _needs_sub = {"決算短信", "決算説明資料"}
    has_bq_subs = gemini_doc["main_category"] in _needs_sub

    gemini_subs = set(gemini_doc["sub_categories"])
    gemma_subs_raw = gemma_result.get("sub_categories", [])
    gemma_subs = set(s for s in gemma_subs_raw if s in VALID_CATEGORIES)

    if has_bq_subs:
        # BQにGemini sub_categoriesが保存されている → 公平な比較可能
        if gemini_subs or gemma_subs:
            intersection = gemini_subs & gemma_subs
            union = gemini_subs | gemma_subs
            jaccard = len(intersection) / len(union) if union else 1.0
        else:
            jaccard = 1.0
        sub_comparable = True
    else:
        # BQ側は空[]（パイプラインが捨てた）→ 比較不能
        jaccard = None
        sub_comparable = False

    return {
        "doc_id": gemini_doc["doc_id"],
        "ticker": gemini_doc["ticker"],
        "doc_title": gemini_doc["doc_title"][:40],
        "text_length": gemini_doc["text_length"],
        "status": "OK",
        "monthly_match": gemini_is_monthly == gemma_is_monthly,
        "gemini_monthly": gemini_is_monthly,
        "gemma_monthly": gemma_is_monthly,
        "gemini_main_cat": gemini_doc["main_category"],
        "sub_comparable": sub_comparable,
        "gemini_subs": sorted(gemini_subs),
        "gemma_subs": sorted(gemma_subs),
        "gemma_subs_invalid": [s for s in gemma_subs_raw if s not in VALID_CATEGORIES],
        "sub_jaccard": jaccard,
        "sub_exact_match": gemini_subs == gemma_subs if sub_comparable else None,
    }


def run_poc(
    date_from: str, date_to: str,
    limit: int | None = None,
    prompt_fn=None,
    split_mode: bool = False,
) -> None:
    """PoC実行メイン."""
    if prompt_fn is None:
        prompt_fn = build_prompt_gemma
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    logger.info("poc_start", date_from=date_from, date_to=date_to)

    # 1. BQから既存結果取得
    bq_client = _get_bq_client()
    docs = fetch_gemini_results(bq_client, date_from, date_to)
    logger.info("gemini_results_fetched", count=len(docs))

    # Phase 3 対象のみにフィルタ（パイプラインと同一条件）
    phase3_docs = [d for d in docs if d["main_category"] in _NEEDS_GEMINI_ANALYSIS]
    logger.info(
        "phase3_filtered",
        total=len(docs),
        phase3_target=len(phase3_docs),
        skipped=len(docs) - len(phase3_docs),
    )
    docs = phase3_docs

    if limit:
        docs = docs[:limit]
        logger.info("limited_to", count=len(docs))

    # 2. Gemma モデルで分類
    genai_client = _get_genai_client()

    for model_name in GEMMA_MODELS:
        logger.info("model_test_start", model=model_name, docs=len(docs))
        results = []
        ok = failed = 0
        monthly_correct = monthly_total = 0
        # sub_categories: 比較可能なドキュメント（決算短信・決算説明資料）のみ集計
        sub_comparable_count = 0
        jaccard_sum = 0.0
        sub_exact = 0

        for i, doc in enumerate(docs):
            if split_mode:
                # 2分割: G1 + G2 を別々に呼び出して結果をマージ
                p1 = build_prompt_gemma_split_g1(doc["doc_title"], doc["full_text"])
                r1 = call_gemma(genai_client, model_name, p1)
                time.sleep(0.3)
                p2 = build_prompt_gemma_split_g2(doc["doc_title"], doc["full_text"])
                r2 = call_gemma(genai_client, model_name, p2)
                # マージ
                if r1 and r2:
                    gemma_result = {
                        "is_monthly": r1.get("is_monthly", False),
                        "sub_categories": list(set(
                            r1.get("sub_categories", []) + r2.get("sub_categories", [])
                        )),
                    }
                elif r1:
                    gemma_result = r1
                elif r2:
                    gemma_result = {"is_monthly": False, "sub_categories": r2.get("sub_categories", [])}
                else:
                    gemma_result = None
            else:
                prompt = prompt_fn(doc["doc_title"], doc["full_text"])
                gemma_result = call_gemma(genai_client, model_name, prompt)
            comparison = compare_results(doc, gemma_result)
            results.append(comparison)

            if comparison["status"] == "OK":
                ok += 1
                if comparison["monthly_match"]:
                    monthly_correct += 1
                monthly_total += 1
                if comparison["sub_comparable"]:
                    sub_comparable_count += 1
                    jaccard_sum += comparison["sub_jaccard"]
                    if comparison["sub_exact_match"]:
                        sub_exact += 1
            else:
                failed += 1

            # 進捗表示
            if (i + 1) % 10 == 0 or i == len(docs) - 1:
                logger.info(
                    "progress",
                    done=i + 1,
                    total=len(docs),
                    ok=ok,
                    failed=failed,
                )

            # レートリミット対策
            time.sleep(0.5)

        # 3. サマリー
        summary = {
            "model": model_name,
            "date_range": f"{date_from}~{date_to}",
            "total_docs": len(docs),
            "ok": ok,
            "failed": failed,
            "monthly_accuracy": monthly_correct / monthly_total if monthly_total else 0,
            "sub_comparable_docs": sub_comparable_count,
            "sub_jaccard_avg": jaccard_sum / sub_comparable_count if sub_comparable_count else 0,
            "sub_exact_match_rate": sub_exact / sub_comparable_count if sub_comparable_count else 0,
        }

        logger.info("model_test_complete", **summary)

        # 4. 結果保存
        output_file = OUTPUT_DIR / f"gemma4_poc_{timestamp}_{model_name}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {"summary": summary, "details": results},
                f, ensure_ascii=False, indent=2,
            )
        logger.info("results_saved", path=str(output_file))

        # 5. 不一致詳細表示
        mismatches = [r for r in results if r["status"] == "OK" and not r["monthly_match"]]
        if mismatches:
            logger.info("monthly_mismatches", count=len(mismatches))
            for m in mismatches[:10]:
                logger.info(
                    "mismatch",
                    ticker=m["ticker"],
                    title=m["doc_title"],
                    gemini=m["gemini_monthly"],
                    gemma=m["gemma_monthly"],
                    gemini_cat=m["gemini_main_cat"],
                )

        # sub_categories不一致（比較可能なドキュメントのみ）
        sub_mismatches = [
            r for r in results
            if r["status"] == "OK" and r["sub_comparable"] and not r["sub_exact_match"]
        ]
        if sub_mismatches:
            logger.info("sub_category_mismatches_comparable", count=len(sub_mismatches))
            for m in sub_mismatches[:15]:
                logger.info(
                    "sub_mismatch",
                    ticker=m["ticker"],
                    title=m["doc_title"],
                    gemini=m["gemini_subs"],
                    gemma=m["gemma_subs"],
                )


def parse_args() -> argparse.Namespace:
    """引数パース."""
    parser = argparse.ArgumentParser(description="Gemma 4 PoC: TDnet分類精度比較")
    parser.add_argument("--date", help="対象日 YYYYMMDD（1日分）")
    parser.add_argument("--date-from", dest="date_from", help="開始日 YYYYMMDD")
    parser.add_argument("--date-to", dest="date_to", help="終了日 YYYYMMDD")
    parser.add_argument("--limit", type=int, default=None, help="処理件数制限（デバッグ用）")
    parser.add_argument("--gemini-prompt", action="store_true",
                        help="Gemini版プロンプトを使用（デフォルトはGemma最適化版）")
    parser.add_argument("--split", action="store_true",
                        help="カテゴリ2分割モード（G1:業績系 + G2:コーポレートアクション系）")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.date:
        d = args.date
        date_from = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        date_to = date_from
    elif args.date_from:
        f = args.date_from
        date_from = f"{f[:4]}-{f[4:6]}-{f[6:8]}"
        t = args.date_to or args.date_from
        date_to = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    else:
        print("--date or --date-from is required")
        sys.exit(1)

    prompt_fn = build_prompt_gemini if args.gemini_prompt else build_prompt_gemma
    run_poc(date_from, date_to, limit=args.limit, prompt_fn=prompt_fn, split_mode=args.split)
