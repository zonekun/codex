# コードレビュー: extract_monthly_data.py Gemini 初期化の Cloud Run / ローカル分岐

- 日時: 2026-05-13 JST
- 対象: `scripts/extract_monthly_data.py` L3700-3720, L3868-3891, L4073-4094
- パターン: 1 (新規変更のまっさらレビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: non-tdnet(pdf)、xlsx (excel_gemini)、html_table の3パスで Gemini クライアント初期化を Cloud Run (Vertex AI) / ローカル (個人APIキー) に分岐。従来は個人APIキーのみで Cloud Run では動作不能だった
- 品質評価: **A** — 3箇所の変更は一貫しており、TDnet パスとの設計差は意図的（TDnetはticker横断でクライアントを使い回すが、非TDnetパスはticker単位初期化）。致命的バグはない。改善提案2件
- 主要リスク:
  1. TDnet パスとの初期化パターン不統一（`phase_extract.__dict__` キャッシュ vs 毎ticker初期化）による初期化コスト増
  2. Cloud Run + batch_mode 時に `_gemini_client = None` のまま残るが、batch_mode パスでは client を使わないため実害なし（ただし可読性の課題）
  3. ローカルで `dotenv` 未インストールかつ `extraction_method == "gemini"` の場合、warning ログのみで暗黙にスキップ（既存挙動の継承なので新規リスクではない）

## 【重大な指摘】

なし。

3箇所とも以下の安全性を確認済み:

1. **`_gemini_client` が None のまま参照される可能性**: 3パスとも、下流で `_gemini_client` を使用する箇所は `batch_mode or _gemini_xxx_client` の条件分岐内にあり、`_gemini_client is None` かつ `batch_mode=False` の場合は Gemini 抽出パスに入らない。安全
2. **バッチモード時の挙動**: `batch_mode=True` の場合、3パスとも `if IS_CLOUD_RUN / else` 分岐自体に入らない（`not batch_mode` ガード）。バッチモードは Gemini クライアントを使わず JSONL リクエスト組み立てのみ行う設計。影響なし
3. **3箇所の一貫性**: xlsx パス (L3700-3720) は `_is_excel_gemini` 条件、pdf/html パスは `extraction_method == "gemini"` 条件だが、これは変更前からの設計差（xlsxはextraction_method自体が "excel_gemini" と独立している）。Cloud Run 分岐ロジック自体は3箇所で同一構造

## 【改善提案】（可読性・保守性）

### #1 Gemini 初期化ロジックの共通化

- 箇所: `scripts/extract_monthly_data.py:3700-3720`, `3871-3891`, `4074-4094`
- 現状: Cloud Run / ローカルの分岐ロジックが3箇所にコピペされている。今後 Gemini モデル切替やリージョン変更時に3箇所すべてを修正する必要がある
- 提案: `_init_gemini_client(logger) -> Optional[genai.Client]` のようなヘルパー関数に抽出する。TDnet パス (L3495-3500) の `phase_extract.__dict__` キャッシュパターンとは初期化タイミングが異なるため完全統一は不要だが、Cloud Run / ローカル分岐の判定ロジック自体は共通化できる

```python
# 提案: ヘルパー関数（L210付近、get_gemini() の直後に配置）
def _init_gemini_for_extract(logger: logging.Logger) -> tuple[Optional[Any], Optional[str]]:
    """Gemini クライアントを環境に応じて初期化する.

    Returns:
        (client, model_name) — 初期化失敗時は (None, None)
    """
    if IS_CLOUD_RUN:
        client = get_gemini()
        model = GEMINI_MODEL
        logger.info(f"  Gemini Vertex AI 初期化 (model={model})")
        return client, model
    try:
        from google import genai
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            client = genai.Client(api_key=api_key)
            logger.info(f"  Gemini 個人APIキー初期化 (model={GEMINI_MODEL})")
            return client, GEMINI_MODEL
        logger.warning("  GEMINI_API_KEY 未設定 → Gemini 抽出スキップ")
    except ImportError:
        logger.warning("  google-genai 未インストール → Gemini 抽出スキップ")
    return None, None
```

### #2 TDnet パスとの初期化パターン不統一

- 箇所: TDnet: `scripts/extract_monthly_data.py:3495-3500` vs 他3パス: `L3700-3720`, `L3871-3891`, `L4074-4094`
- 現状: TDnet パスは `phase_extract.__dict__` にクライアントをキャッシュし、全 ticker で使い回す（効率的）。一方、非 TDnet 3パスは ticker ループ内で毎回 `get_gemini()` を呼ぶ。`get_gemini()` は毎回 `genai.Client()` を新規生成するため、ticker 数分のクライアントインスタンスが生成される
- 提案: 非 TDnet パスも TDnet と同様にループ外でクライアントを初期化するか、少なくとも `get_gemini()` の戻り値をモジュールレベルでキャッシュすることを検討する。ただし、Cloud Run では各パスの ticker 数が少ない（数十社程度）ため、実害は軽微。優先度は低い

## 【修正例】

なし（改善提案 #1 に十分なコード例を記載済み）。

## 【確認できなかった事項】

- `get_gemini()` が返す `genai.Client(vertexai=True, ...)` と `genai.Client(api_key=...)` の API 互換性。`google-genai` ライブラリの仕様上、`generate_content` 等のメソッドインタフェースは同一と推定されるが、実行して確認はしていない（既に TDnet パスで `get_gemini()` の Vertex AI クライアントが同じ下流関数 `extract_from_text_gemini` 等で使われており、実績から互換性は確認済みと判断）
- Cloud Run 上での `dotenv` の有無。`IS_CLOUD_RUN=True` の場合は `dotenv` import パスに入らないため問題ないが、仮に Cloud Run 環境変数の誤設定で `IS_CLOUD_RUN=False` になった場合、`dotenv` 未インストールで warning スキップとなる。これは既存挙動の継承であり新規リスクではない
