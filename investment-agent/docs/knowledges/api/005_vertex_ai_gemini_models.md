# 生成AI 利用ガイド（Gemini — ローカル / Cloud Run / Vertex AI）

**カテゴリ**: api
**作成日**: 2026-04-02
**更新日**: 2026-05-03
**ステータス**: 有効
**適用範囲**: GCPプロジェクト `gmailpj-357912` / ローカル個人APIキー / Cloud Run Job

---

## 使用可能（Gemini 2.5 系）

| モデルID | 用途目安 |
|---------|---------|
| `gemini-2.5-pro` | 高精度・複雑なタスク。自動更新エイリアス |
| `gemini-2.5-flash` | バランス（速度・精度）。エージェント用途に最適。自動更新エイリアス |
| `gemini-2.5-flash-lite` | 軽量・高速・低コスト。自動更新エイリアス |
| `gemini-2.5-flash-image` | 画像生成・画像処理特化 |

## Global のみ利用可能（リージョン指定では404）

| モデルID | 備考 |
|---------|------|
| `gemini-3-flash-preview` | Gemini 3 quickstart で `GOOGLE_CLOUD_LOCATION=global` 明記 |
| `gemini-3.1-flash-lite-preview` | 軽量版。バッチ予測対応確認済み（2026-04-06）。`us-central1` で404事故確認済み（2026-05-03 download_monthly.py） |
| `gemini-3.1-pro-preview` | 高精度版。Gemini 3 quickstart で global 明記 |
| `gemini-3.1-pro-preview-customtools` | ツール使用特化 |

## リージョン指定でも404のまま

- `gemini-2.5-flash-001` 等の番号・日付サフィックス付き
- `gemini-live-2.5-flash-native-audio` → generateContent 非対応（Live API 専用）

> **週次確認**: `PYTHONUTF8=1 python scripts/check_gemini_models.py` を毎週月曜に実行。利用可能になったら上の「使用可能」テーブルに移動し、**`scripts/check_gemini_models.py` は削除すること**。

## 個人APIキー（非Vertex AI）で利用可能なモデル

`GEMINI_API_KEY`（`.env`）で `genai.Client(api_key=...)` 経由で使うローカル実行用。
最終確認日: 2026-04-26

### テキスト生成（推奨順）

| モデルID | 用途目安 |
|---------|---------|
| `gemini-3-flash-preview` | **ローカル月次チェック標準**。画像認証等に使用 |
| `gemini-3-pro-preview` | 高精度タスク |
| `gemini-2.5-flash` | バランス。安定版 |
| `gemini-2.5-pro` | 高精度。安定版 |
| `gemini-2.5-flash-lite` | 軽量・低コスト |
| `gemini-2.0-flash` | 旧世代だが利用可能。疎通テスト用 |

### 廃止・新規利用不可

| モデルID | 状態 |
|---------|------|
| `gemini-2.0-flash-lite` | 新規ユーザー利用不可（404: no longer available to new users） |

### その他（特殊用途）

- `gemini-3.1-pro-preview` / `gemini-3.1-flash-lite-preview` — 最新プレビュー
- `gemini-2.5-flash-image` / `gemini-3-pro-image-preview` / `gemini-3.1-flash-image-preview` — 画像生成
- `gemini-embedding-2` — 埋め込み
- `imagen-4.0-*` / `veo-3.*` — 画像・動画生成

> **確認方法**: `client.models.list()` で最新一覧を取得可能

## 使わない方針

- `gemini-2.0-flash`, `gemini-2.0-flash-001` → 利用可能だが 2.0 は古いため使わない（疎通テスト用途は可）

## Global Endpoint（2026年以降の標準）

最新モデル（3.1系等）は **`location='global'`** に先行公開される。各リージョン（us-central1等）への展開は遅れる。

```python
# 最新モデルへのアクセス方法
client = genai.Client(project=PROJECT_ID, location="global", vertexai=True, credentials=creds)
```

- `global` = Vertex AI のルーティング層。背後で空きリージョンに自動割り当て
- バッチ予測も `global` で投入可能（確認済み 2026-04-06）
- 安定版モデル（gemini-2.5-flash等）は従来通り `us-central1` でも利用可能

## SDK ルール

- **`google-genai`（新SDK）を使うこと**。`from google import genai` でインポート。
- **`google-cloud-aiplatform`（旧SDK）は使用禁止**。`BatchPredictionJob` 等の旧APIは使ってはいけない。
- バッチジョブ投入・状態確認・結果取得はすべて `google-genai` の `client.batches` API を使う。
- 詳細なコード例は `docs/knowledges/tools/013_tdnet_load.md` > 「SDK ルール」セクション参照。

## Vertex AI location ルール（2026-05-03 確定）

### すべきこと

- **Gemini 3.x 系 preview モデルは `location="global"` を指定する**。`genai.Client(vertexai=True, project=..., location="global")`
- **Embedding モデル（`text-embedding-004` 等）は `location="us-central1"` を指定する**。Embedding はリージョナルで提供されており、`global` では動作しない
- **新しいモデルを使う前に、モデル個別ページで利用可能 location を確認する**。「2.5以降は全て global」等の一般化は不正確

### すべきでないこと

- **Gemini 3.x 系を `us-central1` 等リージョナルで呼んではいけない** → HTTP 404 になる
- **1つのクライアントで分析とEmbeddingを兼用してはいけない** → location が異なるためクライアントを分離する

### 対応状況（2026-05-03 横展開調査、Codex + Claude Code 共同）

| スクリプト | 分析 location | Embedding location | 状態 |
|-----------|--------------|-------------------|------|
| `download_monthly.py` | `global` | — | 修正済み（本事故で修正） |
| `tdnet_load_parallel.py` | `global` (L75) | `us-central1` (L100) | 対応済み |
| 他 Gemini 3.x 使用スクリプト | — | — | 個人APIキー（非Vertex AI）で影響なし |
| `extract_monthly_data.py` | `global` (L117) | — | 修正済み（gemini-3-flash-preview） |
| `check_gemini_models.py` | `global` | — | 修正済み |

### ソース

- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-1-flash-lite
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-flash
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/get-started-with-gemini-3

---

## 命名規則のポイント

- **自動更新エイリアス**（`gemini-2.5-flash` 等、サフィックスなし）= 常に最新安定版を指す → 本番推奨
- Gemini 3.1 は `location='global'` で利用可能。リージョン指定では404
- リージョンは `us-central1`（Vertex AI 安定版）/ `global`（最新モデル）/ `us-west1`（Cloud Run ジョブ）
