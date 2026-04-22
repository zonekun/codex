# Vertex AI 利用可能モデル（Gemini）

**カテゴリ**: api
**作成日**: 2026-04-02
**ステータス**: 有効
**適用範囲**: GCPプロジェクト `gmailpj-357912` / リージョン `us-central1`

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
| `gemini-3.1-flash-lite-preview` | 軽量版。バッチ予測対応確認済み（2026-04-06） |
| `gemini-3.1-pro-preview` | 高精度版 |
| `gemini-3.1-pro-preview-customtools` | ツール使用特化 |

## リージョン指定でも404のまま

- `gemini-2.5-flash-001` 等の番号・日付サフィックス付き
- `gemini-live-2.5-flash-native-audio` → generateContent 非対応（Live API 専用）

> **週次確認**: `PYTHONUTF8=1 python scripts/check_gemini_models.py` を毎週月曜に実行。利用可能になったら上の「使用可能」テーブルに移動し、**`scripts/check_gemini_models.py` は削除すること**。

## 使わない方針

- `gemini-2.0-flash`, `gemini-2.0-flash-001` → 利用可能だが 2.0 は古いため使わない

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

## 命名規則のポイント

- **自動更新エイリアス**（`gemini-2.5-flash` 等、サフィックスなし）= 常に最新安定版を指す → 本番推奨
- Gemini 3.1 は `location='global'` で利用可能。リージョン指定では404
- リージョンは `us-central1`（Vertex AI 安定版）/ `global`（最新モデル）/ `us-west1`（Cloud Run ジョブ）
