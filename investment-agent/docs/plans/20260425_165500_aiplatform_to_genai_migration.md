# google-cloud-aiplatform → google-genai 横展開移行

**作成日時**: 2026-04-25 16:55 JST
**対象ファイル**: Python 9本 + Dockerfile 5本 + pyproject.toml
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: CLAUDE.md 規約「`google-cloud-aiplatform` 禁止、全 Vertex AI 操作は `google-genai` で行う」に未準拠のファイルを一括移行する。スコープは import 置換 + API 呼び出しパターン変換 + Dockerfile 依存整理。ロジック変更は含まない。
**分類**: (b) 継続改修型（既存規約の横展開）

---

## 前提サマリ

- 移行済み: 11 Python ファイル + 3 Dockerfile が `from google import genai` パターンに移行済み
- 残存: Python 7本（vertexai only）+ 2本（部分移行）+ Dockerfile 3本（aiplatform only）+ 2本（両方）+ pyproject.toml
- 実機検証: extract-monthly-data は google-genai 追加ビルド済み（2026-04-25 commit 494ac09 時点）
- 関連: CLAUDE.md §コーディング規約「Gemini ライブラリ」

---

## 優先度の定義

- **P0**: Cloud Run Job で実行されるスクリプト（Dockerfile 依存不整合 → 本番クラッシュリスク）
- **P1**: ローカル/Colab 実行スクリプト（動作するが規約違反）
- **P2**: 実験・ユーティリティスクリプト（使用頻度低）

---

## 移行パターン（共通）

### import 置換

```python
# before
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part
from vertexai.generative_models import Content

# after
from google import genai
from google.genai import types
```

### 初期化

```python
# before (Cloud Run / ADC)
vertexai.init(project=PROJECT, location=LOCATION)

# after (Cloud Run / ADC)
client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

# before (ローカル / サービスアカウント)
from google.oauth2 import service_account
creds = service_account.Credentials.from_service_account_file(KEY_FILE)
vertexai.init(project=PROJECT, location=LOCATION, credentials=creds)

# after (ローカル / サービスアカウント)
from google.oauth2 import service_account
creds = service_account.Credentials.from_service_account_file(KEY_FILE)
client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION, credentials=creds)
```

### generate_content

```python
# before
model = GenerativeModel("gemini-2.0-flash")
config = GenerationConfig(response_mime_type="application/json", temperature=0.0)
response = model.generate_content(prompt, generation_config=config)

# after
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0,
    ),
)
```

### Embedding（tdnet_load_recovery.py 専用）

```python
# before
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
model = TextEmbeddingModel.from_pretrained("text-embedding-005")
inputs = [TextEmbeddingInput(text, "RETRIEVAL_DOCUMENT") for text in texts]
embeddings = model.get_embeddings(inputs)

# after
response = client.models.embed_content(
    model="text-embedding-005",
    contents=texts,
    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
)
embeddings = response.embeddings
```

### ThinkingConfig（aws_mcp_search_colab.py 専用）

```python
# before
from vertexai.generative_models import ThinkingConfig
config = GenerationConfig(thinking_config=ThinkingConfig(thinking_budget=8192))

# after
config = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=8192),
)
```

---

## 指摘項目

### P0-1. extract_monthly_data.py 部分移行完了 ⚠️

**該当**: `scripts/extract_monthly_data.py:L208-209,L238,L3081,L3205`

**現状**: Cloud Run パスで `vertexai.init()` + `GenerativeModel()` 使用。`google-genai` は try/import でローカル fallback のみ。

**修正方針**: vertexai import を全削除。Cloud Run / ローカル両方とも `genai.Client(vertexai=True)` に統一。`_gemini_model` を `GenerativeModel` インスタンスから `genai.Client` インスタンスに変更し、呼び出し箇所を `client.models.generate_content()` に置換。

**検証**: `--tickers 2685 --limit 1` で 1 社テスト → GCS 出力確認

**ロールバック**: git revert で復元可

---

### P0-2. download_monthly.py 部分移行完了 ⚠️

**該当**: `scripts/download_monthly.py:L328-329,L337`

**現状**: Cloud Run パスで vertexai、ローカルで genai の二重実装。

**修正方針**: P0-1 と同様に `genai.Client(vertexai=True)` に統一。

**検証**: `--tickers 2685 --limit 1` で 1 社テスト

**ロールバック**: git revert

---

### P0-3. monthly_data_load.py 移行 ⚠️

**該当**: `scripts/monthly_data_load.py:L294-295`

**現状**: `vertexai.init()` + `GenerativeModel()` のみ。

**修正方針**: 共通パターンで置換。

**Dockerfile**: `docker/Dockerfile.monthly-data-load` — `google-cloud-aiplatform>=1.38` → `google-genai>=1.0`

**検証**: `--limit 1` で 1 社テスト

**ロールバック**: git revert + Dockerfile revert + 再ビルド

---

### P0-4. tdnet_load_recovery.py 移行（Embedding API 含む） ⚠️

**該当**: `scripts/tdnet_load_recovery.py:L37-39`

**現状**: `TextEmbeddingModel` + `GenerativeModel` の両方使用。

**修正方針**: Embedding は `client.models.embed_content()` に置換。GenerativeModel は共通パターンで置換。

**Dockerfile**: `docker/Dockerfile.tdnet-load-recovery` — `google-cloud-aiplatform>=1.38` → `google-genai>=1.0`

**検証**: `--dry-run` で embedding 出力 + generate_content 出力を確認

**ロールバック**: git revert + Dockerfile revert + 再ビルド

---

### P0-5. build_monthly_extractor の Dockerfile 整理 ⚠️

**該当**: `docker/Dockerfile.build-monthly-extractor:L13`

**現状**: `google-cloud-aiplatform>=1.40` のみ。スクリプト `scripts/build_monthly_extractor.py` は既に `from google import genai` 移行済み。

**修正方針**: `google-cloud-aiplatform>=1.40` → `google-genai>=1.0` に置換。

**検証**: Cloud Build → `--limit 1` テスト

**ロールバック**: Dockerfile revert + 再ビルド

---

### P1-1. scrape_jpx_delisted.py 移行

**該当**: `scripts/scrape_jpx_delisted.py:L190-191`

**修正方針**: 共通パターンで置換。ローカル実行スクリプト。

**検証**: `--limit 5` で 5 件分類テスト

---

### P1-2. find_monthly_page_urls.py 移行

**該当**: `scripts/find_monthly_page_urls.py:L242-243`

**修正方針**: 共通パターンで置換。

**検証**: `--limit 1` で 1 社テスト

---

### P1-3. aws_mcp_search_colab.py 移行（ThinkingConfig 含む）

**該当**: `scripts/aws_mcp_search_colab.py:L158-159,L170`

**修正方針**: ThinkingConfig を `types.ThinkingConfig` に置換。

**検証**: Colab で実行テスト

---

### P2-1. check_gemini_models.py 移行

**該当**: `scripts/check_gemini_models.py:L14,L47`

**修正方針**: 共通パターンで置換。モデル一覧取得ユーティリティ。

**検証**: 実行して出力確認

---

### P2-2. experiment_gemini_pro_pdf.py 移行

**該当**: `scripts/experiment_gemini_pro_pdf.py:L66-67`

**修正方針**: 共通パターンで置換。実験スクリプト。

**検証**: 任意 PDF で実行テスト

---

### P2-3. Dockerfile 両方入り → aiplatform 削除

**該当**:
- `docker/Dockerfile.extract-monthly-data` — aiplatform 残存（genai 追加済み）
- `docker/Dockerfile.tdnet-load-parallel` — aiplatform + genai 両方

**修正方針**: 各スクリプトの vertexai import 削除後に `google-cloud-aiplatform` 行を削除。イメージサイズ削減効果あり。

**検証**: Cloud Build → Job 実行テスト

---

### P2-4. pyproject.toml から google-cloud-aiplatform 削除

**該当**: `pyproject.toml:L54` — `google-cloud-aiplatform>=1.141.0`

**修正方針**: 全 Python ファイルの vertexai import 削除完了後に削除。

**検証**: `uv sync` → ローカルテスト

---

## 実施順序

```
Phase 1 (P0): Cloud Run 本番影響
  P0-5 → P0-3 → P0-4 → P0-1 → P0-2
  (Dockerfile のみ → 単純スクリプト → 複雑スクリプト)
  各ステップ: コード修正 → Dockerfile修正 → ビルド → smoke test

Phase 2 (P1): ローカルスクリプト
  P1-1 → P1-2 → P1-3

Phase 3 (P2): クリーンアップ
  P2-1 → P2-2 → P2-3 → P2-4
```

---

## 検証戦略

1. **smoke test**: 各スクリプト `--limit 1` で 1 件処理 → 出力正常確認
2. **Dockerfile ビルド**: `gcloud builds submit` 成功 + イメージサイズ確認
3. **本番適用判断**: smoke test PASS → Cloud Run Job update → 次回定期実行で本番確認
4. **回収手順**: git revert + Dockerfile revert + 再ビルドで即復旧可能

---

## 関連ドキュメント

- CLAUDE.md §コーディング規約「Gemini ライブラリ」
- 移行済みパターン参考: `scripts/edinet_load_parallel.py`, `scripts/tdnet_load_parallel.py`
- フォーマット���本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-25 18:50 JST — code-reviewer

### サマリー

- **変更の要約**: `google-cloud-aiplatform`（vertexai SDK）を使用している全ファイルを `google-genai` に一括移行するプラン。Python 9本 + Dockerfile 5本 + pyproject.toml が対象。
- **品質評価**: B — 対象スクリプトの網羅と移行パターンの定義は概ね良好だが、Dockerfile 1本の欠落・知見MD 1本の更新漏れ・API パターン差異（Part.from_data / Content 構築 / system_instruction / モデル一覧API）の記述不足が複数あり。
- **主要リスク**:
  1. `Dockerfile.download-monthly` が対象から完全に漏れている — Cloud Run で `google-genai` が未インストールのまま移行後コードが走りクラッシュする
  2. `Part.from_data()` → `types.Part.from_bytes()` の API 差異が 3 スクリプトで未言及 — 機械的な import 置換だけでは実行時エラーになる
  3. `check_gemini_models.py` (P2-1) の移行パターンが「共通パターンで置換」とだけ書かれているが、実際は `GenerativeModel(mid).generate_content("hi")` というモデル可用性テストであり、`google-genai` では `client.models.generate_content(model=mid, contents="hi")` への書き換えに加えてエラーハンドリングの response 構造変更がある

### 改修プラン評価

#### フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか — **違反**: commit hash は前提サマリに `494ac09` とあるが「対象ファイル」欄には未記載。テンプレートは「`scripts/foo/bar.py`（N 行、commit <hash> 時点）」を要求する
- [x] 前提サマリで過去修正と残件数が明示されているか — OK: 移行済み 11本 + 残存 9本 + Dockerfile を明示
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか — OK
- [ ] 各項目が 7 フィールドを揃えているか — **違反**: 「症状」「根本原因」「呼び出し側波及」が全項目で欠落。テンプレートは「症状 / 該当 / 根本原因 / 修正方針 / 呼び出し側波及 / 検証 / ロールバック」の 7 フィールドを要求する。本プランは「該当 / 現状 / 修正方針 / 検証 / ロールバック」の 5 フィールド構成
- [x] 修正方針に before/after の両方が書かれているか — OK（§移行パターン（共通）に before/after を集約）
- [ ] 呼び出し側への波及が該当行リストで明示されているか — **違反**: 全項目で「呼び出し側への波及」フィールド自体が無い。`extract_monthly_data.py` の `get_gemini()` は `extract_from_text_gemini()` から呼ばれ、`extract_from_text_gemini()` は L238 で `from vertexai.generative_models import GenerationConfig, Part` を独立 import している。この波及が未記載
- [ ] 「既に〜がある」系の前提を実コードと照合 — **違反あり（後述 #1）**
- [ ] アンチパターン対応表が末尾にあるか — **違反**: 無い
- [ ] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか — **部分的**: smoke と本番適用判断はあるが、dev 実機（スケール・期間・コストガード）が無い。回収手順は各項目に「git revert」とあるが、Dockerfile revert 後の「再ビルド」のビルドコマンドが不明
- [x] ロールバック手順が書かれているか — OK（各項目に記載）
- [x] 読みづらさ・デッドコードだけで P0 に置かれている項目が無いか — OK
- [ ] 関連 commit・知見 MD・incident ログへのリンクがあるか — **部分的**: 関連ドキュメントに CLAUDE.md と移行済みパターン参考はあるが、更新が必要な知見 MD（`029_aws_mcp_servers.md`、`005_vertex_ai_gemini_models.md`）への言及が無い

#### 妥当性

プランの方針（vertexai import → genai.Client パターンに一括置換）は CLAUDE.md 規約の真因に対処しており、妥当。`genai.Client(vertexai=True)` により Vertex AI バックエンドを引き続き利用しつつ SDK を統一する方向は正しい。

#### 副作用・デグレードチェック

- [x] **`vertexai.init()` のグローバル状態 → `genai.Client` のインスタンス状態**: `vertexai.init()` はプロセスグローバルに project/location/credentials を設定する。`genai.Client()` はインスタンス単位。`tdnet_load_recovery.py` の `_vertexai_lock` + `_init_vertexai()` パターン（L97, L164-168）はこのグローバル状態を守るためのロックであり、`genai.Client` 移行後は不要になるが、削除し忘れてもデッドコードになるだけで害はない。ただし、同スクリプト内で Gemini(us-central1) と Embedding(us-central1) を使い分けており（L206, L392）、`genai.Client` に移行する際は location を正しく指定すること
- [x] **`response_schema` のパススルー互換性**: `extract_monthly_data.py:L297-310` と `experiment_gemini_pro_pdf.py:L109-133` で `response_schema` を dict で渡している。`google-genai` の `types.GenerateContentConfig` でも dict は受け付ける（既に移行済みの `build_monthly_extractor.py:L401` が同パターンで動作確認済み）ので互換性問題なし
- [x] **Cloud Run の ADC (Application Default Credentials)**: Cloud Run 環境では `credentials=` を明示しなくても ADC が効く。`download_monthly.py:L330` は `vertexai.init(project=..., location=...)` で credentials なし。`genai.Client(vertexai=True, project=..., location=...)` でも ADC が効くので互換性あり

#### 抜け漏れ（対象ファイル漏れ + 関連ファイル漏れ）

##### 【重大な指摘】

#1 **`Dockerfile.download-monthly` がプランに完全に欠落**

- 箇所: `docker/Dockerfile.download-monthly` 全体
- 事象: `download_monthly.py` は Cloud Run パス（L326-334）で `import vertexai` / `from vertexai.generative_models import GenerativeModel, GenerationConfig` を使用する。移行後は `from google import genai` に変わるため Dockerfile に `google-genai>=1.0` が必要。しかし現在の `Dockerfile.download-monthly` は `google-cloud-aiplatform` も `google-genai` も含まない（Playwright ベースイメージで独自 pip install）
- トリガー: P0-2 の修正を適用して Cloud Run Job をビルド・デプロイすると、Cloud Run 実行時に `ModuleNotFoundError: No module named 'google.genai'` でクラッシュする
- 影響: 月次ダウンロードの Gemini 年月判定が全件失敗。ダウンロード自体は続行するが年月判定なし
- 根拠: `Dockerfile.download-monthly:L6-15` に google-genai が無い。プランの P0-2 に Dockerfile 言及なし。P2-3 の Dockerfile 一覧にも `download-monthly` 無し
- 推奨対応: P0-2 に「**Dockerfile**: `docker/Dockerfile.download-monthly` — `google-genai>=1.0` を追加」を追記

#2 **プラン §Embedding パターンのモデル名が実コードと不一致**

- 箇所: プラン L87 vs `scripts/tdnet_load_recovery.py:L393`
- 事象: プランの Embedding 移行パターンで `text-embedding-005` と記載されているが、実コードは `text-embedding-004` を使用している
- 影響: プラン通りに実装すると、意図せずモデルバージョンが変わり、既存の embedding ベクトルとの互換性が崩れる（cosine similarity のスケールが変わる）
- 推奨対応: プランのサンプルコードを `text-embedding-004` に修正するか、意図的なバージョンアップなら明示的にその旨を記載

#3 **`Part.from_data()` → `types.Part.from_bytes()` の API 差異が未記述**

- 箇所: `scripts/extract_monthly_data.py:L289`, `scripts/tdnet_load_recovery.py:L311`, `scripts/experiment_gemini_pro_pdf.py:L126`
- 事象: 3 スクリプトが `Part.from_data(data=pdf_bytes, mime_type="application/pdf")` を使用して PDF バイナリを Gemini に送信している。`google-genai` では `Part.from_data()` は存在せず、`types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")` を使う
- トリガー: 機械的に import 置換のみ行い `Part.from_data()` 呼び出しを残すと `AttributeError: module 'google.genai.types' has no attribute 'from_data'`
- 影響: extract_monthly_data の Gemini PDF 抽出モード、tdnet_load_recovery の PDF 分類、experiment_gemini_pro_pdf の全処理が停止
- 推奨対応: §移行パターン（共通）に「Part.from_data → types.Part.from_bytes���の before/after を追加。該当 3 ファイルの行番号を各指摘項目に明記

#4 **`aws_mcp_search_colab.py` の `Content`/`Part`/`system_instruction` 移行が「共通パターン」の範囲外**

- 箇所: `scripts/aws_mcp_search_colab.py:L159,L178-185`
- 事象: このスクリプトは単純な `model.generate_content(prompt)` ではなく:
  - `Content(role=..., parts=[Part.from_text(...)])` で多ターン会話履歴を構築（L183）
  - `GenerativeModel(..., system_instruction=..., generation_config=...)` でモデル初期化時に system_instruction を指定（L178-182）
  - `ThinkingConfig(thinking_budget=0)` を使用（L173）
- `google-genai` では:
  - `Content` → `types.Content`, `Part.from_text()` → `types.Part.from_text()`
  - `system_instruction` は `GenerateContentConfig` の `system_instruction` パラメータに移動
  - 会話履歴は `contents` パラメータにそのまま渡せるが型が `types.Content` である必要がある
- プラン P1-3 は「ThinkingConfig を `types.ThinkingConfig` に置換」とだけ記載し、Content/Part/system_instruction の移行に触れていない
- 推奨対応: P1-3 の修正方針を拡充し、Content/Part/system_instruction のパターンを明記

#5 **知見 MD `docs/knowledges/tools/029_aws_mcp_servers.md` のコード例が旧 SDK のまま放置される**

- 箇所: `docs/knowledges/tools/029_aws_mcp_servers.md:L158-166`
- 事象: `aws_mcp_search_colab.py` の知見 MD にある Gemini 2.5 対応のコード例が `from vertexai.generative_models import GenerationConfig` / `ThinkingConfig` のまま。P1-3 でスクリプトを移行しても知見 MD が旧 SDK コード例を残すと、次にこのスクリプトを改修する際に旧パターンをコピーしてしまう
- 推奨対応: P1-3 の実施時に `029_aws_mcp_servers.md:L158-166` のコード例も `google-genai` パターンに更新

#6 **`check_gemini_models.py` (P2-1) の移行がモデル一覧確認のユースケースと合致しない可能性**

- 箇所: `scripts/check_gemini_models.py:L57,L70`
- 事象: このスクリプトは `GenerativeModel(mid).generate_content("hi")` でモデルの可用性を確認するユーティリティ。`google-genai` では `client.models.generate_content(model=mid, contents="hi")` に置き換えるが、`google-genai` には `client.models.list()` や `client.models.get(model=mid)` があり、実際に `generate_content` を呼ばずにモデルの存在確認ができる可能性がある。ただし staged rollout 中のモデルが `list()` に表示されるかは確認が必要
- 影響: 軽微（P2 スクリプトであり、動作はする）
- 推奨対応: P2-1 の修正方針に「`client.models.list()` でモデル存在確認を試み、不可なら `generate_content("hi")` を維持」と方針を明記

##### 【改善提案】

#7 **`extract_monthly_data.py` の `get_gemini()` 戻り値の型変更に伴う波及箇所の明示**

- 箇所: `scripts/extract_monthly_data.py:L206-211, L238`
- 現状: `get_gemini()` は `(GenerativeModel, GenerationConfig)` タプルを返す。移行後は `genai.Client` を返すか `(genai.Client, types.GenerateContentConfig)` を返すか。呼び出し元の `extract_from_text_gemini()` の L238 で `from vertexai.generative_models import GenerationConfig, Part` を別途 import しており、`get_gemini()` の変更だけでは不十分
- 提案: P0-1 の修正方針に `get_gemini()` の戻り値型変更と、`extract_from_text_gemini():L238` の import 削除、`Part.from_data` → `types.Part.from_bytes` の変更を波及として明記

#8 **`tdnet_load_recovery.py` の `_vertexai_lock` / `_init_vertexai()` デッドコード化の明記**

- 箇所: `scripts/tdnet_load_recovery.py:L97, L164-168`
- 現状: `genai.Client` はインスタンスベースでグローバル状態を持たないため、`_vertexai_lock` と `_init_vertexai()` は移行後デッドコードになる。7 箇所（L206, L309, L360, L392, L567, L568）から呼ばれている
- 提案: P0-4 の修正方針に「`_init_vertexai()` を `_get_genai_client()` に置換。`_vertexai_lock` は削除。呼び出し元 7 箇所を更新」と明記

#9 **Dockerfile のイメージサイズ削減効果の定量化**

- 箇所: 全 Dockerfile 項目
- 現状: P2-3 に「イメージサイズ削減効果あり」とあるが定量値なし。`google-cloud-aiplatform` は pip install 後に約 100MB 以上のディスクを占有する（多数の依存パッケージを含む）
- 提案: 検証戦略に「ビルド前後のイメージサイズ比較（`docker image ls`）を記録」を追加

#### 新規リスク

1. **`google-genai` SDK バージョンの Dockerfile 間不整合**: Dockerfile ごとに `google-genai>=1.0` と書くと、ビルド時期によって異なるバージョンが入る可能性がある。`pyproject.toml` では `>=1.67.0` と指定しているが、Dockerfile は `>=1.0`。API の breaking change があった場合に Dockerfile 間で挙動が割れるリスク。Dockerfile でも `>=1.67.0` 等に揃えることを推奨
2. **Embedding API のレスポンス構造差異**: `tdnet_load_recovery.py:L398` で `results = model.get_embeddings(inputs)` → `embeddings.extend([r.values for r in results])` としている。`google-genai` の `client.models.embed_content()` は `response.embeddings` を返し、各要素は `ContentEmbedding` オブジェクトで `.values` 属性を持つ。プランの after コードでは `response.embeddings` までしか書かれておらず、`.values` の取得方法が省略されている。実装時に `[e.values for e in response.embeddings]` とする必要がある
3. **`download_monthly.py` のローカルパスで API キー認証**: L337-341 でローカル実行時は `genai.Client(api_key=...)` を使用（Google AI Studio 経由）。Cloud Run パスを `genai.Client(vertexai=True, ...)` に移行すると、同一スクリプト内に 2 つの異なる Client 初期化パスが残る。これ自体は問題ないが、将来の保守で混乱しないよう関数名やコメントで明確に分離すること

### 確認できなかった事項

1. `google-genai` の `types.Part.from_bytes()` が `Part.from_data()` と完全に同じ引数シグネチャかどうか（`data=` キーワード引数の有無）。実機で `from google.genai import types; help(types.Part.from_bytes)` を確認する必要がある
2. `check_gemini_models.py` で `client.models.list()` が staged rollout 中のモデルを返すかどうか。返さない場合は `generate_content("hi")` による可用性テストを維持する必要がある
3. `aws_mcp_search_colab.py` の `response.candidates[0].content.parts` の属性名が `google-genai` でも同一かどうか（L190-193 の `part.thought` 属性の有無）。`google-genai` では `part.thought` が `part.thought` として存在するか、別の属性名かを実機確認する必要がある
4. `extract_monthly_data.py:L313` の `_call_with_timeout(lambda: gemini_model.generate_content(...))` — `gemini_model` が `GenerativeModel` インスタンスから `genai.Client` に変わった場合、`_call_with_timeout` 内の lambda の呼び出しパターンが `client.models.generate_content()` に変わるが、timeout の挙動（`google-genai` のネイティブ timeout パラメータ vs 外部 timeout wrapper）が同等かどうか
