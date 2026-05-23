# 作業計画: 決算説明資料 チャンクロジック改善 + gemini-embedding-001 移行評価

**作成日時**: 2026-05-21 15:55 (JST)
**ステータス**: 進行中（Phase B-1/B-2/B-3 実装完了、Phase A 未着手・A-0 から着手）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`
**関連アイディアID**: -

---

## 目的

1. 決算説明資料（スライド型PDF）のチャンク分割ロジックを「スライド単位 + 見出しprefix」に変更し、093 じっくり分析の検索（REGEXP共起 + Vector検索）精度を向上させる。
2. 同時に `text-embedding-004` → `gemini-embedding-001` への移行可否を、実測ベースで評価する。

---

## 背景・動機

### 現状の問題
- 現行チャンク化は `RecursiveCharacterTextSplitter`（chunk_size=400, overlap=50）で文字数ベース → スライド見出しとビュレット本文が分断され、093 Step 1（テーマワード × 好調表現の同一CHUNK共起）のヒット率が落ちる
- 決算説明資料は本来「1スライド=1意味単位」なのに、現実装は文字数で機械的に分割している
- 例（2026-05-21 smoke test で観測）: doc_id 140120260213558907 (5301 東海カーボン 決算説明資料) は 21 ページあるがビュレット記号 `◼`/`⚫` のみで本文ほぼ抽出できず（ラスタライズ系スライド）。改善対象は **テキストが拾えているスライド資料**

### embedding モデル評価の動機
- `text-embedding-004` は 2024 世代、`gemini-embedding-001` は 2025 世代（MRL 採用、日本語性能向上）
- 既存埋め込み 13.2M 行・4.46B chars。移行は all-or-nothing（混在は ML.DISTANCE 破壊）
- 一括再埋め込みコスト見込み: 約 $111-167（要 batch、online は数日かかる）

### 判断方針
- Phase A（チャンク改善）は **確定実施**: 単独でも 093 の検索精度向上が見込まれる
- Phase B（embedding 評価）は **実測で判断**: 精度差わずかなら現状維持、明確な改善あれば Phase C 実施
- Phase C（gemini 移行）は **Phase B 結果次第**

### 関連
- 親知見 MD: `docs/knowledges/tools/013_tdnet_load.md` §チャンク化仕様・§BQ Vector Index 設定
- 093 用途: `docs/knowledges/analysis/093_earnings_deep_analysis.md`
- データカタログ: `docs/data_catalog/bq_tdnet_documents.md` §チャンク化仕様 / §Vector Index 仕様
- 直近のパイプライン改修: `docs/plans/tools-013_gemma_only_pipeline_20260520_220510.md`（完了済）

---

## 作業ステップ

### PHASE A: 決算説明資料 スライド単位チャンク化（確定実施）

> **Codex 意見取り込み（2026-05-22）**: feature flag による切り替え制御・`資料種別` prefix 追加・Dockerfile 先行確認を統合。

1. - [ ] **A-0**: PyMuPDF の Docker 依存確認（先行チェック）
   - Cloud Run イメージ（`cloudbuild/cloudbuild.tdnet-load-daily.yaml` の Dockerfile）に `pymupdf` が含まれるか確認
   - 未収録の場合: `RUN pip install pymupdf` を最小追加（A-6 前にビルドが死ぬのを防ぐ）

2. - [ ] **A-1**: PyMuPDF `get_text("dict")` でフォントサイズ取得 → スライド見出し検出ヘルパー追加
   - `scripts/tdnet_load_parallel.py` に `_extract_slide_headings(pdf_bytes) -> dict[int, str]` 追加
   - 各ページのフォントサイズ最大行を見出しとして抽出（heuristic: 最大フォント or 上部 1/4 領域の文字）
   - フォールバック: 見出し検出失敗時は空文字（prefix なし）
   - **feature flag**: 環境変数 `USE_SLIDE_CHUNKER=1` が未設定または `0` の場合は現行 RCS パスを使用。本番誤動作防止のため A-5 smoke test 完了まで `0` を維持

3. - [ ] **A-2**: `create_chunks_for_tdnet()` を MAIN_CATEGORY 別に分岐
   - 決算説明資料（`USE_SLIDE_CHUNKER=1` 時のみ）: スライド単位チャンク化（`[PAGE N]` 境界を絶対に割らない）
     - 1ページ = 1チャンク 原則（テキスト >2000 chars のスライドのみ RCS で 400 chars 分割、その場合も separators の先頭に `\n\n[PAGE` を強制）
     - prefix（Codex提案の `資料種別` 列を追加）:
       ```
       文書タイトル: {doc_title}
       資料種別: 決算説明資料
       ページ: {n}
       スライドタイトル: {heading}

       {text}
       ```
   - 決算短信・月次開示: 現状維持（chunk_size=400 の RCS）
   - `USE_SLIDE_CHUNKER=0` または未設定: 全カテゴリ現行 RCS（後退互換）

4. - [ ] **A-3**: ユニットテスト追加
   - スライド見出し抽出: サンプル PDF（5301 東海カーボン 等）で見出し検出精度を確認
   - チャンク化: 同サンプルで `[PAGE N]` 境界が割れないこと、prefix（資料種別・ページ・スライドタイトル）が正しく付与されること
   - feature flag OFF 時に現行 RCS パスが動くことを確認
   - 出力先: `tests/test_tdnet_chunk_logic.py`（新規）

5. - [ ] **A-4**: 影響範囲確認
   - 既存 BQ 行（13.2M）の CHUNK_TEXT は変更しない（過去chunkとの混在を一旦受容）
   - 新規ロード以降の決算説明資料のみ新形式（`USE_SLIDE_CHUNKER=1` 時）
   - 混在影響: 093 検索結果が一部「旧chunk + 新chunk」混じるが致命的でない

6. - [ ] **A-5**: smoke test（dev / Cloud Run）
   - 環境変数 `USE_SLIDE_CHUNKER=1` を設定して実行
   - 2026-02-13 の 5994 ファインシンター 決算説明資料 等で再 ai-prepare → ai-finalize
   - BQ で CHUNK_TEXT prefix（資料種別・ページ・スライドタイトル）と境界を確認
   - 確認SQL: `SELECT DOC_ID, CHUNK_INDEX, LEFT(CHUNK_TEXT, 100) FROM ... WHERE DOC_ID = '...' ORDER BY CHUNK_INDEX`
   - smoke test 合格後に Cloud Run Job 環境変数へ `USE_SLIDE_CHUNKER=1` を本設定

7. - [ ] **A-6**: Cloud Build + 3 Job デプロイ
   - 既存テンプレート `cloudbuild/cloudbuild.tdnet-load-daily.yaml` をそのまま使用
   - 一時ビルドディレクトリ方式（005 §⑥）
   - A-0 で Dockerfile 追記が必要だった場合はここで含める

8. - [ ] **A-7**: 知見 MD 更新
   - `013_tdnet_load.md` §チャンク化仕様 に分岐ロジック・feature flag・prefix仕様を追記
   - `bq_tdnet_documents.md` §チャンク化仕様 にも反映（決算説明資料の特例）

---

### PHASE B: gemini-embedding-001 精度評価（評価フェーズ）

8. - [x] **B-1**: 評価クエリセット作成（35 件）
   - 7テーマ × 5クエリ（半導体/光電融合/DC電力/航空宇宙/自動化/EV/インバウンド）
   - gold_tickers: 各テーマの主要銘柄（ticker-level relevance）
   - 出力先: `data/eval/embedding_eval_queries_20260521.json`（git管理）

9. - [x] **B-2**: 評価用 embedding 生成スクリプト実装
   - 対象: 直近6ヶ月の決算短信+決算説明資料 CHUNK_TEXT、ランダム 1万チャンク
   - text-embedding-004: BQ EMBEDDING 列を流用（APIコスト $0）
   - gemini-embedding-001 768dim: batch API（MRL truncation, output_dimensionality=768）
   - スクリプト: `scripts/eval/compare_embeddings.py`（新規、3ステップ CLI）
   - コスト見込み: ~$2-3（gemini-embedding-001 corpus のみ）
   - 実行: `python scripts/eval/compare_embeddings.py --step sample` → embed → eval

10. - [x] **B-3**: 評価指標計測ロジック実装（スクリプト内）
    - 各クエリで Top10 結果を両モデルで取得（cosine similarity）
    - 評価指標:
      - **nDCG@10** （gold ticker-level relevance）
      - **Precision@10** （上位10件中の正解率）
      - **主観評価**: subjective 列を手動入力
    - 出力先: `data/output/embedding_eval_results_20260521.csv`

11. - [ ] **B-4**: 移行可否判断
    - 判断基準:
      - **明確な改善 (実施)**: nDCG@10 が 0.10 以上向上、かつ主観評価で「悪化」が 10% 未満
      - **見送り (現状維持)**: nDCG@10 改善が 0.05 未満、または主観評価で「悪化」が 20% 以上
      - **保留 (追加調査)**: 上記の間（0.05〜0.10 改善）→ サンプル拡大して再評価
    - 結果を本プラン MD §振り返り に記録

---

### PHASE C: gemini-embedding-001 全データ移行（評価で「実施」判断時のみ）

> **発動条件**: Phase B-4 で「明確な改善」判定の場合のみ実施。

12. - [ ] **C-1**: 移行戦略の確定
    - dimension: 768 維持（MRL truncation）→ BQ スキーマ変更不要
    - 一括 batch 再埋め込み: 13.2M 行・4.46B chars
    - コスト: ~$167（要 Vertex AI Pricing 最終確認）
    - 段階移行禁止（混在は ML.DISTANCE 破壊）

13. - [ ] **C-2**: 移行スクリプト実装
    - `scripts/migrate_embedding_to_gemini.py`（新規）
    - BQ → CHUNK_TEXT 抽出（dedup 必須、send 側 uniq 化）
    - Vertex AI Batch Prediction（gemini-embedding-001）
    - 結果を BQ MERGE で EMBEDDING 列のみ UPDATE
    - 段階実行（年単位 or 月単位、checkpoint 必須）

14. - [ ] **C-3**: コード変更
    - `scripts/tdnet_load_parallel.py:909` の model 名を `gemini-embedding-001` に変更
    - 必要なら `genai.types.EmbedContentConfig` で output_dimensionality=768 指定
    - 既存 batch API 呼び出し維持（online 切替は別タスク）

15. - [ ] **C-4**: Vector Index 再構築
    - 既存 `tdnet_doc_vector_index` を DROP
    - 再 CREATE（同 OPTIONS: IVF / COSINE / num_lists=1000）
    - 詳細: `013_tdnet_load.md §BQ Vector Index 設定`

16. - [ ] **C-5**: 検証
    - 移行前後で Phase B の評価クエリセットを再実行
    - 期待値（B-4 で想定した改善幅）を再現することを確認
    - 不一致なら原因究明（MRL truncation か Vector Index 設定差か）

17. - [ ] **C-6**: 知見 MD 更新 + コミット
    - `013_tdnet_load.md` の embedding model 記述を更新
    - `bq_tdnet_documents.md` §Vector Index 仕様 のモデル名更新
    - コスト記述更新（`013-1`）

---

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| TDnet PDF 本体 | (b) GCS | `gs://stock_data_1930932/tdnet/{TICKER}/...` |
| 既存埋め込み行 | (a) BigQuery | `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` |
| 評価クエリセット | (c') マスタCSV | `data/eval/embedding_eval_queries_20260521.json`（新規） |
| 評価結果 | (c) ローカル | `data/output/embedding_eval_results_20260521.csv` |

---

## 成果物

### Phase A 完了時（確定）
- `scripts/tdnet_load_parallel.py` の chunk logic 改修
- `tests/test_tdnet_chunk_logic.py` 新規
- `docs/knowledges/tools/013_tdnet_load.md` §チャンク化仕様 更新
- `docs/data_catalog/bq_tdnet_documents.md` §チャンク化仕様 更新

### Phase B 完了時（評価結果記録）
- `scripts/eval/compare_embeddings.py` 新規
- `data/eval/embedding_eval_queries_20260521.json`
- `data/output/embedding_eval_results_20260521.csv`
- 本プラン MD §振り返り に判断記録

### Phase C 完了時（移行実施した場合のみ）
- `scripts/migrate_embedding_to_gemini.py` 新規
- 13.2M 行 EMBEDDING 列 UPDATE 完了
- Vector Index 再構築済み
- 知見 MD 更新

---

## 完了条件

### Phase A
- 決算説明資料の新規チャンクが「1スライド=1チャンク」原則で生成される
- 各チャンクに `スライド見出し: ...` prefix が付与される（見出し検出成功時）
- smoke test で BQ 行を目視確認、ユニットテスト全 PASS
- Cloud Run 3 Job デプロイ済み

### Phase B
- 評価レポート（CSV）が出力され、nDCG@10 / Precision@10 / 主観評価が算出される
- 移行可否判断が本プラン MD に記録される

### Phase C（実施時のみ）
- 全 13.2M 行の EMBEDDING が gemini-embedding-001 に更新
- Vector Index 再構築完了
- 移行前後で評価クエリの結果が想定改善幅を再現

---

## 見積もり

| Phase | 想定所要時間 | 難易度 | コスト |
|-------|------------|-------|-------|
| A | 4-6h（実装3h + テスト1h + デプロイ1h + 知見MD1h） | 中 | $0 |
| B | 4-6h（クエリ作成2h + 評価実行1h + 集計分析2h） | 中 | ~$5 |
| C（実施時） | 6-8h（実装3h + 移行実行2-4h + 検証1h + 文書化1h） | 高 | ~$167 |

**合計（C 実施時）**: 14-20h / ~$172
**合計（C 見送り時）**: 8-12h / ~$5

---

## 振り返り（作業後に記入）

- 実際の所要時間:
- うまくいった点:
- 改善点:
- Phase B-4 判定結果:
- Phase C 実施有無:
- 得られた知見:
