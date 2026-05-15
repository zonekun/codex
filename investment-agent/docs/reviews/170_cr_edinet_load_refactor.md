# コードレビュー: EDINET load リファクタリング計画

- 日時: 2026-05-13 23:15 JST
- 対象: `docs/plans/tools-012_edinet_load_refactor_20260513_223000.md` / `scripts/edinet_load_parallel.py`
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: TDnet load で得た ETL アンチパターン対策（Load Job 化、numpy embedding、chunk マッピング安全化、exit code 修正等 13 項目）を EDINET load に反映し、7年バックフィルに耐える品質にする計画
- 品質評価: **A** — TDnet の実事故経験に基づく改善項目が網羅的で、アンチパターン対応表・検証戦略・回収手順も揃う。重大な設計リスクが 2 件あるが方向性は正しい
- 主要リスク:
  1. P0-3 の `custom_id` 方式は Vertex AI Embedding Batch で未サポートの可能性が高い（TDnet は別方式で解決済み）
  2. P0-1 の Load Job 化で冪等性設計（重複 INSERT 防止）が不十分
  3. Dockerfile に `numpy` 依存が未追加（P0-2 実装不可）

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

全体的に TDnet load で実証済みのパターン移植が主体であり、技術選定は妥当。以下の個別判断も適切:

- **Load Job 化 (P0-1)**: streaming insert の 90 分 DML 制約回避として正しい選択。004 C-5 に準拠
- **numpy float32 (P0-2)**: メモリ 1/8 圧縮の見積もりは正確（768 dim × 4B vs 24-32B）
- **NDJSON stream write (P1-2)**: 大量 doc の一括 JSON → gzip OOM 回避として適切。004 C-2 に準拠
- **polling deadline (P1-3)**: 無限ループ除去。004 D-1 に準拠

### 既存システムとの統合

- [x] BQ テーブル `STOCK.IR_DOCUMENTS_ENHANCED` への書き込み方式変更（streaming → Load Job）は既存データと互換
- [ ] **Cloud Build 定義 (`cloudbuild.edinet-load-parallel.yaml`)**: 計画に `py_compile` ステップ追加 (P2-4) が記載されているが、現行の Cloud Build 定義は Docker build + push のみ。`py_compile` を Docker build 内（Dockerfile の RUN）で実行するのか、Cloud Build ステップとして追加するのか方式が不明確
- [ ] **Dockerfile への numpy 追加**: P0-2 で `import numpy as np` を導入するが、`docker/Dockerfile.edinet-load-parallel` の `pip install` に `numpy` が含まれていない。Cloud Run 環境で `ImportError` になる
- [ ] **GCS 一時ファイルのパス**: P0-1 の `load_upload_{timestamp}.jsonl` は `GCS_BATCH_PREFIX` 配下に作成されるが、GCS lifecycle ルール（batch_prediction/ 配下の自動削除設定）との整合性が未記載
- [ ] **data_catalog への登録**: `STOCK.IR_DOCUMENTS_ENHANCED` の BQ テーブルカタログ MD が `docs/data_catalog/` に存在しない（`bq_tdnet_documents.md` には TDNET のみ）。新規カタログ作成が計画に含まれていない

### リスク・コスト

- GCP 課金影響: Load Job は streaming insert と同等かやや安価。コスト面のリスクなし
- 処理時間: Load Job は streaming より完了まで数秒〜数十秒遅いが、バッチ ETL では問題なし
- **失敗時の撤退基準**: 検証戦略に「本番適用判断基準: smoke + dev 両方 PASS で初めてバックフィル投入」と明記されており適切
- **回収手順**: 「Load Job は冪等（file_name ベース dedup）。最悪ケースは BQ の当該期間 DELETE → 再実行」とあるが、冪等性の実装方式（MERGE / INSERT 前の既存チェック）がプランに未記載（後述 #1 で詳述）

### 抜け漏れ

- [ ] **冪等性設計の欠如**: 現行コードは `_load_processed_file_names()` で BQ 既存の `FILE_NAME` を取得し、Phase 1 で重複スキップしている。Load Job 化後もこのフローは維持されるが、**Load Job 自体の冪等性**（同一 JSONL を 2 回ロードした場合の重複防止）がプランに記載されていない。`WRITE_APPEND` では重複行が生まれる。TDnet では `phase5_bq_insert_load` が DELETE → INSERT パターンを採用しているが、EDINET のプランではこれに言及がない
- [ ] **resume モードとの整合**: P0-1 で Phase 3 を Load Job 化すると、resume モードの `phase3_bq_insert()` 呼び出し（L951）も影響を受ける。resume 時は state から docs を復元 → Phase 2 結果適用 → Phase 3 という流れだが、GCS 一時 JSONL のパスが submit 時と resume 時で衝突しないかの考慮が必要
- [ ] **012_edinet_load.md の更新**: 成果物に「固有ルール（E-1〜E-4）追記」とあるが、EDINET の現況サマリ・GCS 構造・注意事項セクションの既存記載と新アーキの整合性更新が必要
- [ ] **Dockerfile の依存追加**: `numpy` を P0-2 で導入するなら Dockerfile に `numpy>=1.24` を追加する必要がある
- [ ] **`_serialize_docs` / `_deserialize_docs` の numpy 対応**: P0-2 で `embeddings` を `np.ndarray` に変更すると、既存の `_serialize_docs`（L183-198）は `embeddings` を serialize していない（意図的に省略している模様だが明記がない）。P1-2 で NDJSON stream write に変更する際、numpy array の serialize/deserialize 方式を決める必要がある

### 目的・スコープの明確性

- 目的は明確: 「大規模バックフィルに耐える品質にする」
- 非スコープが暗黙: EDINET のアーキテクチャ変更（TDnet のような load/ai 分離）は本プランの対象外と思われるが明示されていない

### 段階的検証計画

- smoke → dev → 本番の 3 段階が設計されており適切
- smoke test のコマンドライン例が具体的で再現可能

### 完了条件の検証可能性

- 「P0 全4件が実装・smoke test PASS」「P1 の少なくとも P1-1, P1-3 が実装」は検証可能
- 「Cloud Run dev 実機テスト（1ヶ月分）PASS」の PASS 基準（BQ 行数比較の閾値等）がやや曖昧

### データカタログ整合

- `STOCK.IR_DOCUMENTS_ENHANCED` の BQ テーブルカタログが `docs/data_catalog/` に存在しない。本計画で新規作成すべき

---

## 【重大な指摘】（即修正）

### #1 P0-3: `custom_id` 方式は TDnet の実装と異なり、Vertex AI Embedding Batch で未サポートの可能性が高い

- 箇所: プラン P0-3（L96-117）
- 事象: プランは Embedding JSONL に `custom_id` フィールドを埋め込み、結果照合に使用する方式を提案。しかし TDnet の `phase4_chunk_and_embed`（`scripts/tdnet_load_parallel.py:1311-1363`）は `custom_id` を使わず、`content_to_chunks: dict[str, list[tuple[DocInfo, int]]] = defaultdict(list)` で同一 `chunk_text` を複数 `(doc, ci)` にマッピングする方式で衝突問題を解決している
- トリガー: Vertex AI Batch Prediction（text-embedding-004）が `custom_id` フィールドを出力側に透過させない場合、照合が完全に失敗し全 embedding が紛失する
- 影響: 全 embedding 紛失（データ欠損）
- 根拠: TDnet 実装では `custom_id` が使われておらず、Vertex AI Embedding Batch の出力 JSONL 形式は `{"instance": {"content": "..."}, "predictions": [...]}` であり `custom_id` の透過を保証する仕様記載がない。プラン自身も「Vertex AI Batch Prediction の `custom_id` フィールド対応を要確認」と注記している
- 推奨対応: **[検証済み]** TDnet と同一の `defaultdict(list)` 方式を採用する。`content_to_chunks[chunk_text].append((doc, ci))` で 1:N マッピングを構築し、結果照合時に全 (doc, ci) に embedding を反映。EDINET 側の現行コード（L700, L757）は 1:1 の `dict` で衝突上書きしているため、これを `defaultdict(list)` に変更するだけで TDnet パターンと同一になる

### #2 P0-1: Load Job 化後の冪等性設計が未記載

- 箇所: プラン P0-1（L29-65）
- 事象: `WRITE_APPEND` で Load Job を実行すると、同一バッチの再実行で重複行が挿入される。プランの「回収手順」に「file_name ベース dedup」とあるが、dedup の実装方式（アプリ側の事前チェック / BQ MERGE / 事後 dedup クエリ）が記載されていない
- トリガー: Phase 3 が途中で失敗し再実行された場合、一部 doc が 2 回 Load される
- 影響: BQ に重複行が残存。下流の検索・分析で二重カウント
- 根拠: 現行コードの `_load_processed_file_names()` は Phase 1 の重複スキップに使われるが、Phase 3 の Load Job が部分成功した場合は同一 doc が再度 Load される
- 推奨対応: **[方向性]** 以下の選択肢から方式を決定しプランに追記すべき: (a) Load Job 後に `MERGE` で重複排除、(b) Load Job 前に BQ 既存行を DELETE してから APPEND、(c) resume 時に `_load_processed_file_names` を再取得して Phase 1 から再実行。TDnet の `phase5_bq_insert_load` は (b) の DELETE → INSERT を採用している

### #3 P0-2: Dockerfile に numpy 依存が未追加

- 箇所: `docker/Dockerfile.edinet-load-parallel:5-11`
- 事象: P0-2 で `import numpy as np` を導入するが、Dockerfile の `pip install` に `numpy` が含まれていない
- トリガー: 改修後の Docker イメージを Cloud Run で実行した時点で `ImportError: No module named 'numpy'`
- 影響: Cloud Run Job が即座に失敗。バックフィル不可
- 根拠: 現行 Dockerfile（L5-11）のパッケージリストに numpy がない
- 推奨対応: **[検証済み]** Dockerfile の `pip install` に `numpy>=1.24` を追加。プランの「成果物」にも Dockerfile 変更を明記

### #4 既存コードの例外握り潰し（004 A-3 違反）が計画に含まれていない

- 箇所: `scripts/edinet_load_parallel.py:714-724`, L790-801
- 事象: `phase2_poll_and_apply` と `phase2_chunk_and_embed` の結果取得ループ内で `except (KeyError, IndexError, json.JSONDecodeError): continue` が silent に失敗を握り潰している。失敗件数がログにもカウンターにも反映されない
- トリガー: Embedding 結果の JSONL 形式が想定と異なる場合（API 仕様変更等）、全行が silent skip されるが `embed_ok` が 0 のまま処理が完了する
- 影響: embedding が全く適用されていないのに Phase 3 に進み、embedding なしの行が BQ に格納される。Phase 2 完了ログの `embed_ok/embed_chunk_count` で異常は検出可能だが、`errors` カウンターには反映されないため exit 0 で終了する（A-1 との複合）
- 根拠: L714-724 と L790-801 は identical な構造で、いずれも bare `continue` で例外を捨てている
- 推奨対応: **[検証済み]** `except` 節に `logger.log(f"  Embedding parse失敗: {e}"); embed_errors += 1` を追加。`embed_ok` と `embed_errors` の合計が `embed_chunk_count` と一致するか検証ログを出力し、`errors` に合流させる

---

## 【改善提案】（可読性・保守性）

### #1 P1-1: ticker range フィルタの辞書順前提が脆弱

- 箇所: プラン P1-1（L138-150）
- 現状: 「ticker range はファイル名の辞書順で機能する」と記載。`1301`〜`3999` のような数字 4 桁なら辞書順 = 数値順だが、末尾アルファベット付き ticker（`174A`, `218A` 等）は辞書順で `1749` < `174A` < `1750` のように中間に入り、range 指定で意図しない包含/除外が起きる
- 提案: アルファベット付き ticker の存在を注意事項としてプランに明記。バックフィル運用ガイドに「アルファベット付き ticker は個別確認」の注記を追加

### #2 `_load_processed_file_names` の SQL パラメータ化 (P2-2) と ticker フィルタ追加 (P1-5) の実装順序

- 箇所: プラン P2-2 + P1-5
- 現状: P2-2 で SQL パラメータ化、P1-5 で ticker フィルタ追加を別項目として記載。両方とも `_load_processed_file_names` の同一関数を変更する
- 提案: 同一関数への 2 つの変更を 1 ステップにまとめ、実装漏れ（片方だけ適用）を防止

### #3 Cloud Build にジョブ自動 update ステップが未設定

- 箇所: `cloudbuild/cloudbuild.edinet-load-parallel.yaml`
- 現状: Docker build + push のみ。TDnet の Cloud Build（`cloudbuild.tdnet-load-daily.yaml`）は push 後に `gcloud run jobs update` を自動実行するステップを持つ（013 知見 MD の「イメージ」セクション記載）
- 提案: EDINET でも Cloud Build に `gcloud run jobs update edinet-load --image=... --region=us-west1` ステップを追加し、手動 update 忘れを防止。プランの成果物に Cloud Build 定義の改修を追加

### #4 P0-4: `sys.exit(1)` の位置

- 箇所: プラン P0-4（L124-128）
- 現状: `main()` 末尾に `if errors > 0: sys.exit(1)` を追加する方針。しかし現行の `run_edinet_batch_etl` は `errors` をローカル変数として持ち、`main()` には返さない（L939, L993, L1000）
- 提案: `run_edinet_batch_etl` の返り値に `errors` を含めるか、例外で伝播させる設計をプランに明記

---

## 【確認できなかった事項】

- Vertex AI Batch Prediction（text-embedding-004）の出力 JSONL が `custom_id` フィールドを透過するかどうか。API ドキュメントまたは実機テストでの確認が必要（#1 の根拠）
- `STOCK.IR_DOCUMENTS_ENHANCED` テーブルの BQ スキーマ詳細（EMBEDDING カラムの型が `FLOAT64` の `REPEATED` か `BIGNUMERIC` か等）。Load Job の `source_format=NEWLINE_DELIMITED_JSON` で EMBEDDING 列（配列型）が正しくパースされるかの確認
- GCS `batch_prediction/edinet/` 配下の lifecycle ルール設定の有無
- 後続プラン（`tools-012_edinet_backfill_20260513_221200.md`）との依存関係の詳細（本プランの P1 項目がバックフィル側で前提になっているか）
