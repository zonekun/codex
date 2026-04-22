"""モデル・文書カテゴリ別の text 切り詰め共通ユーティリティ.

`scripts/tdnet_load_parallel.py` などの LLM 呼び出し前にフルテキストを
モデルのコンテキスト上限（16k token 等）に収まる範囲に切り詰める。

方針:
- サンプリング（中抜き・先頭末尾結合）は精度劣化を招くため原則使わない
- 末尾切り詰め（先頭保持）をデフォルトとする
- 決算短信のように情報密度が均一で末尾にも重要情報（受注情報等）が
  含まれるカテゴリは NO_TRUNCATE_CATEGORIES に登録し、切り詰めしない
  （長文はチャンク分割で対応する想定）

モデル別予算:
- gemma-4-* は 16k token ≒ 24-32k char だが、安全側で 20k char
- gemini-3-flash は 1M token コンテキストだが、プロンプト組立で
  余裕を持たせて 200k char

詳細: docs/knowledges/tools/013-1_ai_cost_and_gemma_poc.md
"""

from __future__ import annotations

__all__ = [
    "MODEL_CHAR_BUDGET",
    "NO_TRUNCATE_CATEGORIES",
    "get_char_budget",
    "truncate_for_model",
    "needs_chunking",
]


# モデル名 → 安全に収まる最大文字数（プロンプト定型文等を含んだ上での余裕込み）
MODEL_CHAR_BUDGET: dict[str, int] = {
    "gemma-4-31b": 20000,           # 16k tokens 想定、安全側
    "gemma-4-26b-a4b-it-maas": 20000,
    "gemini-3-flash-preview": 200000,
    "gemini-3-flash-batch": 200000,
    # 他モデルは呼び出し側で追加可
}

# 決算短信は切り詰め不可（受注情報の主出典。長文はチャンク分割で対応）
NO_TRUNCATE_CATEGORIES: frozenset[str] = frozenset({"決算短信"})


def get_char_budget(model: str) -> int:
    """モデル別の安全文字数を返す.

    Args:
        model: モデル名（`MODEL_CHAR_BUDGET` のキー）。

    Returns:
        安全に収まる最大文字数。

    Raises:
        ValueError: 未登録モデルの場合。
    """
    if model not in MODEL_CHAR_BUDGET:
        raise ValueError(
            f"未登録モデル: '{model}'. "
            f"MODEL_CHAR_BUDGET に追加してください。"
            f"登録済: {sorted(MODEL_CHAR_BUDGET.keys())}"
        )
    return MODEL_CHAR_BUDGET[model]


def truncate_for_model(
    text: str,
    model: str,
    doc_category: str | None = None,
) -> str:
    """モデル・カテゴリに応じて text を切り詰める.

    - `doc_category` が `NO_TRUNCATE_CATEGORIES` に含まれる場合は無改変で返す
      （長文のチャンク分割は呼び出し側の責務）。
    - それ以外は末尾切り詰め（サンプリング禁止方針のため先頭保持）。

    Args:
        text: 切り詰め対象のフルテキスト。
        model: モデル名（`MODEL_CHAR_BUDGET` のキー）。
        doc_category: 文書カテゴリ（例: "決算短信"）。`None` なら NO_TRUNCATE 判定なし。

    Returns:
        切り詰め後のテキスト。`text` が予算内なら無改変のまま返す。

    Raises:
        ValueError: 未登録モデルの場合。
    """
    if text is None:
        return ""
    if doc_category is not None and doc_category in NO_TRUNCATE_CATEGORIES:
        return text
    budget = get_char_budget(model)
    if len(text) <= budget:
        return text
    return text[:budget]


def needs_chunking(
    text: str,
    model: str,
    doc_category: str | None = None,
) -> bool:
    """決算短信でモデル予算を超過する場合のみ True.

    Args:
        text: 判定対象テキスト。
        model: モデル名。
        doc_category: 文書カテゴリ。

    Returns:
        `doc_category` が `NO_TRUNCATE_CATEGORIES` に含まれ、かつ
        `len(text) > get_char_budget(model)` なら True。それ以外は False。

    Raises:
        ValueError: 未登録モデルの場合。
    """
    if not text:
        return False
    if doc_category is None or doc_category not in NO_TRUNCATE_CATEGORIES:
        return False
    return len(text) > get_char_budget(model)
