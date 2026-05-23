# レビュー依頼: TDnet ENHANCED テーブルへ CHUNK_INDEX 列追加プラン

- 日時: 2026-05-17 22:45 JST
- 提出者: メインエージェント（Claude Opus）
- 対象スキル: code-reviewer
- レビューパターン: **4（新規開発・設計計画レビュー）**

## レビュー対象ファイル

- `docs/plans/tools-013_tdnet_chunk_index_column_20260517_223000.md`

## 事象・背景

直前のレビュー `docs/reviews/202_cr_tdnet_orders_extract.md` の **【重大な指摘】#2「同一 DOC_ID 内のチャンク順序を保証する `ORDER BY` 列が不明」** で発覚した、`STOCK.TDNET_DOCUMENTS_ENHANCED` テーブルの設計不備を恒久是正するプラン。

主旨:
- BQ テーブルに `CHUNK_INDEX INT64` 列を `ALTER TABLE ADD COLUMN` で追加（NULLABLE）
- ロード側コード（`scripts/tdnet_load_parallel.py`）の 3 経路（`phase5_bq_insert_finalize` / legacy `phase5_bq_insert` / `phase5_bq_insert_load`）で `enumerate(doc.chunks)` の `ci` を row 辞書に明示
- データカタログ MD のスキーマ反映 + 利用側クエリの段階移行ガイド

**スコープ制約（ユーザー指示・重要）**:
- **過去データの遡及採番・再ロードは行わない**（既存行は `CHUNK_INDEX=NULL` のまま据え置き）
- これから新規ロードされるデータのみ採番対象
- **利用側に「縛り（必ず WHERE CHUNK_INDEX IS NOT NULL や cutoff date で新規分のみに絞る）」はかけない方針**。CHUNK_INDEX が NULL の過去データでも有効に使える場面（Vector Search で類似チャンク発見、単一チャンク文書、順序不要の集計等）があるため、利用側の判断に委ねる。プランに記載済みの「段階移行ガイド（P2-1）」の文言は「必ず使う」ではなく「順序復元が必要な場合の参考」レベルに緩める方向で **レビューバック後に修正予定**（本レビューでは P2-1 を「使い分けガイド」として評価してほしい。「縛り化」前提の指摘は不要）

## レビュー観点（パターン4の必須項目に従う）

1. **技術選定の妥当性**: ALTER ADD COLUMN（NULLABLE）方式の妥当性。代替案（新テーブル作成 + 移行、SECTION_CATEGORY + EXTRACTED_AT 等で擬似順序化、等）との比較
2. **既存システムとの統合**:
   - 書込み3経路（ai-finalize / legacy / load）の修正カバレッジに漏れがないか
   - 既存 `SELECT *` クエリ・既存スキーマ互換性（EDINET `ir_documents_enhanced` との UNION ALL 含む）への影響
   - `phase5_bq_insert_finalize` の DELETE→INSERT 非原子パターン（004 B-4 / `013_tdnet_load.md` §T-1）との干渉
3. **リスク・コスト**:
   - 過去データ NULL のまま据え置く方針の長期的なリスク（利用側で混在を考慮し続ける負荷）
   - ALTER ADD COLUMN のコスト（partition prune / クラスタリングへの影響、論理削除7日復元の意味づけ）
   - dev → prod 段階適用時の事故シナリオ（書込み修正と ALTER の順序によっては既存行が壊れないか）
4. **抜け漏れ**:
   - `enumerate(doc.chunks)` の `ci` が「BQ 行の本文順序」と本当に一致するか（chunk 化処理が順序保存を保証しているか）の検証手順
   - `phase4_chunk_and_embed` の embedding バッチ並列実行時に順序が崩れる可能性は無いか
   - recovery 系（`tdnet_load_recovery.py`）・他経路の取りこぼし
   - dev 環境の有無・smoke test の現実性

## 補足情報

### 派生元レビュー
`docs/reviews/202_cr_tdnet_orders_extract.md` 【重大な指摘】#2（同一 DOC_ID チャンク順序復元不能）から派生。

### データカタログ
- `docs/data_catalog/bq_tdnet_documents.md`（現行スキーマ・DDL）
- `docs/knowledges/tools/013_tdnet_load.md` §T-3（Python 内部の `(doc_id, chunk_index)` tuple key 実装）

### 関連コード（基準 commit `64c1b812`）
- `scripts/tdnet_load_parallel.py:L1400-L1432` legacy `phase5_bq_insert`
- `scripts/tdnet_load_parallel.py:L1463-L1515` `phase5_bq_insert_load`
- `scripts/tdnet_load_parallel.py:L1953-L2020` `phase5_bq_insert_finalize`

### 非スコープ（プラン記載通り、再指摘不要）
- 過去データの遡及採番（ユーザー指示）
- 旧 `tdnet-load-recovery` の書込み経路（廃止予定）
- 受注抽出タスク本体（別プラン `ad-hoc_tdnet_orders_extract_20260517_214618.md`）
- EDINET 側テーブルの対応

## 期待するレビュー粒度

- 計画段階のレビューなので、コード詳細よりも **設計判断の妥当性** と **抜け漏れ** を重点
- 特に「`enumerate(doc.chunks)` の順序保証が本当に BQ 書込み順 = 本文順と一致するか」を critical に評価
- 段階適用シナリオ（ALTER 先 / コード修正先 / 両者の同時適用）のリスク分析を希望
- 「過去データ NULL 据え置き」方針のトレードオフ（運用負荷 vs 再ロードコスト）の妥当性

---

# コードレビュー: TDnet ENHANCED テーブルへ CHUNK_INDEX 列追加プラン

- 日時: 2026-05-17 23:05 JST
- 対象: `docs/plans/tools-013_tdnet_chunk_index_column_20260517_223000.md`
- パターン: 4（新規開発・設計計画レビュー）
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `STOCK.TDNET_DOCUMENTS_ENHANCED` に `CHUNK_INDEX INT64 NULLABLE` を追加し、書込み 3 経路（ai-finalize / legacy full / load）の row 辞書に `enumerate(doc.chunks)` の `ci` を明示する。過去行は NULL 据え置き。
- 品質評価: **A-** — 設計方針（ALTER ADD COLUMN + 書込み 3 経路同期修正）は妥当。`enumerate(doc.chunks)` の順序保証は実コード上問題なし。ただし (a) ロールバック手順の `ignore_unknown_values` 前提が実コードと不一致、(b) `tdnet_load_recovery.py:L487` の既存 `CHUNK_INDEX` 書込みバグが非スコープ宣言で素通り、(c) `phase5_bq_insert_finalize` の INSERT→DELETE 既知問題（B-4 順序入替済み）と CHUNK_INDEX の組合せで重複 doc が同一 CHUNK_INDEX を 2 セット持つ regression リスク、の 3 点が抜けている。
- 主要リスク:
  - ロールバック手順の前提誤り（`ignore_unknown_values` 未設定）で「DROP COLUMN すれば Load Job は走り続ける」が成立しない可能性
  - `tdnet_load_recovery.py:L487` が `CHUNK_INDEX=0` を全 chunk に書く既存バグ。新スキーマ稼働後に旧 recovery を走らせると全 chunk が `CHUNK_INDEX=0` で永続化
  - ai-finalize INSERT→DELETE 非原子の既知症状（重複 completed 行）+ CHUNK_INDEX により `ORDER BY CHUNK_INDEX` でも同 ci が 2 行返る regression 拡大

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

- **ALTER TABLE ADD COLUMN NULLABLE 方式**: 妥当。BQ の `ADD COLUMN NULLABLE` は無停止・無コスト・既存行の物理書き換えなし（列はメタデータ追加 + 既存行は NULL を返す論理動作）。代替案（新テーブル + 移行 / `SECTION_CATEGORY` + `EXTRACTED_AT` 擬似順序化）と比較して優位。
  - 新テーブル+移行: 19 万件超の既存データを再ロードする必要があり、Embedding 再課金 + Vector Index 再構築のコストが膨大。今回のスコープ（過去再ロードしない）と不整合。
  - `SECTION_CATEGORY` 擬似順序: `SECTION_CATEGORY` は全 chunk で `doc.doc_title` を入れる固定値（`tdnet_load_parallel.py:L1416/L1996`）であり順序情報を持たない。`EXTRACTED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()` も Load Job 単位の同一秒値となり順序復元不可。→ 列追加が唯一の解。
- **NULLABLE 採用**: 過去行に NULL を残す方針と整合。REQUIRED にすると既存行のスキーマ違反になるため避けるべきで、判断は正しい。`description` で「NULL = メタデータのみ行 / 旧データ」を明示しており、利用側に意図が伝わる設計。

### 既存システムとの統合

- [x] **書込み 3 経路カバレッジ**: P0-2 (`phase5_bq_insert_finalize:L2001-L2020`) / P0-3 (legacy `phase5_bq_insert:L1419-L1432`) / P0-4 (`phase5_bq_insert_load:L1495-L1515`) は実コード位置と一致。網羅性 OK。
- [x] **`enumerate(doc.chunks)` の順序保証** (critical 評価):
  - `doc.chunks` は `phase4_chunk_and_embed:L1293` で `create_chunks_for_tdnet()` の戻り値（`langchain_text_splitters.RecursiveCharacterTextSplitter.split_text()` の出力）を**そのまま代入**。`split_text()` は本文順の list を返す関数（langchain 仕様）。`tdnet_load_parallel.py:L445-L459` を読む限り `splitter.split_text(text)` 結果に list comprehension をかけているだけで順序を撹乱しない。
  - Embedding バッチは並列で結果が前後し得るが、`phase4_chunk_and_embed:L1311-L1318` で `content_to_chunks: dict[str, list[tuple[DocInfo, int]]]` を構築済み。受信ループ（L1346-L1367）では `for doc, ci in mappings: doc.embeddings[ci, :] = emb_arr` と **ci を明示キー**として元位置に書き戻している。並列順序が崩れても `doc.embeddings[ci]` / `doc.embedding_set[ci]` の対応関係は保たれる。
  - 結論: `enumerate(doc.chunks)` の `ci` は **「splitter が返した本文順の index」と一意対応**し、BQ row の `CHUNK_INDEX` として正しく機能する。`013_tdnet_load.md §T-3`（`(doc_id, chunk_index)` tuple key）の前提とも整合。**プランの設計は妥当**。
- [x] **EDINET UNION ALL 互換性**: `ir_documents_enhanced`（`012_edinet_load.md:L36-L50`）には `CHUNK_INDEX` 列が存在しない。プランの非スコープで明示されているが、**現状でも `SELECT *` UNION ALL は型・列数不一致でエラー**になる（既存問題、本プランで悪化はしない）。利用側が明示列指定 UNION ALL を書く限り影響なし。
  - 注: EDINET 側カラム名は小文字スネークケース（`chunk_text`）、TDnet 側は大文字（`CHUNK_TEXT`）で命名規約が既にズレている。これは既存事項のため本レビュー対象外。
- [x] **`phase5_bq_insert_finalize` の DELETE→INSERT 非原子（004 B-4）との干渉**:
  - 現行コード（`tdnet_load_parallel.py:L2308-L2317`）は **INSERT を先、DELETE を後** に入れ替え済み（B-4 対策）。`_delete_pending_gemma_rows` は `AI_STATUS != 'completed'` を条件にしているため、INSERT 成功後・DELETE 失敗の中断時、再実行で同 DOC_ID の `completed` 行が**追加**される（既存の `completed` は消されない）。
  - **新リスク**: CHUNK_INDEX 列追加により、この重複行は同 ci を 2 セット保持する。利用側が `WHERE DOC_ID = 'X' ORDER BY CHUNK_INDEX` を書いても、同じ `CHUNK_INDEX=0` の行が 2 行返る → 重複検知に `DISTINCT` や `ROW_NUMBER` が必要になる。これは既存問題の悪化ではなく**症状の見え方が変わる**ケースだが、プランに記述なし。【重大な指摘 #2】参照。

### リスク・コスト

- **ALTER 自体のコスト**: BQ の `ADD COLUMN NULLABLE` は無料・即時。partition / clustering 設計（`SUBMISSION_DATE` partition, `TICKER, MAIN_CATEGORY` cluster）への影響なし。
- **過去データ NULL 据え置きの長期負荷**: 利用側コードに `IS NOT NULL` チェックが永続的に必要。プランは「利用側に縛りはかけない」方針だが、実運用では「順序復元が必要なケース」だけ気にする利用者が出てくる。`bq_tdnet_documents.md` の注意事項に **cutoff date を明記**する（P1-1 修正方針通り）必要があり、これは妥当。
- **段階適用順序の事故シナリオ** (critical 評価):
  - **A. ALTER 先 / コード後**: ALTER 実行直後〜コード deploy 前の間に走る ai-finalize は CHUNK_INDEX 列に何も書かない → 該当行は NULL になる。Load Job の既定動作（欠落 key は NULL 補完）で**安全**。ただし「新規ロード分なのに NULL」という意図しない混在期が発生する。
  - **B. コード先 / ALTER 後**: コード deploy 完了直後〜ALTER 実行前に走る Load Job は **`CHUNK_INDEX` キーを持つ row JSON を BQ に送るが、列が存在しない**。`LoadJobConfig` に `ignore_unknown_values=True` が設定されていれば silently drop されるが、**`tdnet_load_parallel.py` 全体で `ignore_unknown_values` の設定は無い**（Grep 全文 0 件確認）。→ Load Job が `Unknown name "CHUNK_INDEX"` でエラー終了する。これは**事故**。
  - **C. ALTER + コード deploy 同期**: 推奨。
  - **プランの記述漏れ**: 検証戦略（L316-L339）は ALTER → コード deploy の順を暗黙の前提にしているが、明示されていない。回収手順も「全行 NULL なら revert + DELETE → 再 ai-finalize」とあるが、シナリオ B 発火時の対処が無い。【重大な指摘 #1】参照。
- **撤退基準**: プラン L78（ロールバック）に「`LoadJobConfig` が `ignore_unknown_values` で吸収するため、列削除後も Load Job は走る」と書かれているが、**この前提は実コードに存在しない**。`ignore_unknown_values` が未設定（デフォルト False）の状態で `ALTER DROP COLUMN` を打つと、書込みコードが revert される前に走った Load Job は `Unknown name` エラーで失敗する。ロールバック手順を **「コード revert → ALTER DROP COLUMN」の順で行う**よう明示する必要がある。【重大な指摘 #3】参照。

### 抜け漏れ

- [x] **`tdnet_load_recovery.py:L487` の既存 `CHUNK_INDEX` 書込みバグ**: 旧 recovery スクリプトは既に `"CHUNK_INDEX": chunk_data.get("chunk_index", 0)` を row 辞書に入れているが、`create_chunks_for_tdnet`（同ファイル L383-L393）の戻り dict には `chunk_index` キーが**存在しない**（`{"chunk_text": ...}` のみ）。`.get("chunk_index", 0)` がフォールバックして **全 chunk が `CHUNK_INDEX=0` で BQ insert される**。スキーマ追加（P0-1）が走った瞬間、この旧バグが「動作する形でデータ汚染」する。プランは「廃止予定のため触らない」と非スコープ宣言しているが、**現に呼ばれた瞬間に汚染が始まる構造的バグ**。最低限「旧 recovery は新スキーマ稼働後は実行禁止」のガード（CLI 起動時の警告 / exit）を入れる必要がある。【重大な指摘 #4】参照。
- [x] **計画が触れていない関連ファイル**:
  - `scripts/tdnet_load_recovery.py:L487` — 上記バグ（プランは非スコープ宣言だが、ガードレール追加は本プランの責務とすべき）
  - `docs/data_catalog/bq_tdnet_documents.md` — プランで言及済み（P1-1）。OK。
  - `docs/knowledges/tools/013_tdnet_load.md §T-3` — プランで言及済み（P1-1 修正方針 4 番目）。OK。
  - `data_catalog.md`（親）— TDnet テーブルへのリンクは存在するが、CHUNK_INDEX 列追加で親の表現を更新する必要は薄い。OK。
- [x] **smoke test の現実性**: プラン検証戦略 L316-L325 は ai-finalize `--limit 1` を smoke 対象としているが、`--limit` フラグが `tdnet_load_parallel.py` に実装されているかは未確認（Grep 未実施）。実装されていない場合は smoke が回らない。**確認できなかった事項**に記録。
- [x] **dev 環境の有無**: プラン L73「dev プロジェクトの同等テーブル（あれば）」と保留形になっている。dev BQ プロジェクトが無い場合の代替（同一 prod に `STOCK.TDNET_DOCUMENTS_ENHANCED_DEV` を別途作成する等）が記載なし。**確認できなかった事項**に記録。
- [x] **同種バグの横展開**: EDINET 側 `ir_documents_enhanced` にも同じ chunk 順序復元不能問題が存在する（プラン非スコープ）。本プランで解消はしないが、「EDINET 側にも別途同種プランが必要」と関連プラン誘導を明記すべき。**改善提案 #2** 参照。
- [x] **monitoring / alerting**: ai-finalize 後に `CHUNK_INDEX IS NULL AND AI_STATUS='completed' AND SUBMISSION_DATE >= '<cutoff>'` 件数を監視するクエリの記載なし。回収手順（L337-L339）に「該当行を特定」とあるが、定期監視で早期検知する仕組みが無い。**改善提案 #3** 参照。

### 段階的検証計画

- smoke / dev / 本番判断基準 / 回収手順の 4 段は記載されている。ただし上記「段階適用順序」の A / B / C シナリオ別の検証が分離されていない。**改善提案 #1** 参照。

### 完了条件の検証可能性

- L332-L334「`CHUNK_INDEX IS NULL` 件数が ai-finalize 後 0 件（CHUNK_TEXT NOT NULL 行のみ対象）」は具体的かつ検証可能。OK。

### データカタログ整合

- P1-1 で `bq_tdnet_documents.md` 更新が含まれている。OK。スキーマ表（L10-L29）と DDL（L144-L166）の両方を更新する方針も明示されており妥当。

---

## 【重大な指摘】（即修正）

### #1 段階適用シナリオ「コード先 / ALTER 後」で Load Job が `Unknown name "CHUNK_INDEX"` エラーで停止

- 箇所: `docs/plans/tools-013_tdnet_chunk_index_column_20260517_223000.md:L73-L78` (検証戦略) / `scripts/tdnet_load_parallel.py:L1434-L1437` (LoadJobConfig 全 3 箇所同様)
- 事象: コード deploy が先、ALTER が後の場合、書込みコードは `CHUNK_INDEX` キー入りの row JSON を生成して Load Job に投入するが、BQ テーブルに該当列がない。`LoadJobConfig` に `ignore_unknown_values` が設定されていないため、Load Job は `Unknown name "CHUNK_INDEX"` エラーで失敗する。
- トリガー: 「コードを先にデプロイし、後で ALTER を実行する」運用順序。Cloud Run Job のデプロイと BQ DDL は別系統で発火するため、オペレータの判断ミスで容易に起こり得る。
- 影響: tdnet-load-daily（毎日 02:00 JST）/ ai-finalize 経路の Load Job が全件失敗 → 該当日の全文書が BQ に入らないデータ欠損。Cloud Run Job exit 1 → アラート。
- 根拠: `scripts/tdnet_load_parallel.py` 全文 Grep で `ignore_unknown_values` / `schema_update_options` / `autodetect` のいずれもヒットなし（0 件確認）。`google-cloud-bigquery` の `LoadJobConfig.ignore_unknown_values` デフォルトは False（Google 公式ドキュメント）。
- 推奨対応 **[方向性]**: プラン検証戦略に「**deploy 順序: 必ず ALTER → コード deploy の順**。同時 / 逆順を禁止」と明記する。回収手順にもシナリオ B（コード先 deploy で Load Job が `Unknown name` エラー）の対処（ALTER 即時実行で復旧）を追記する。代替策として `LoadJobConfig(ignore_unknown_values=True)` を全 3 箇所に設定する案もあるが、これは別観点（型不一致 silent drop）の regression を生むため、設定するなら影響範囲を別途評価する必要がある（本レビューでは順序明示を推奨）。

### #2 INSERT→DELETE 非原子（004 B-4）の重複行に CHUNK_INDEX 重複が乗る regression

- 箇所: `scripts/tdnet_load_parallel.py:L2308-L2317` / プラン P2-1 の利用側ガイド
- 事象: 現行 ai-finalize は INSERT を先、DELETE を後に実行（B-4 対策で順序入替済み）。INSERT 成功・DELETE 失敗で中断 → 再実行 → 同 DOC_ID の `completed` 行が 2 セット存在。CHUNK_INDEX 列追加後、両セットとも 0,1,2,... の連番を持つため、`SELECT ... WHERE DOC_ID='X' ORDER BY CHUNK_INDEX` で同じ `CHUNK_INDEX=0` の行が 2 行返る。
- トリガー: ai-finalize Job の DELETE 段階で BQ DML quota 不足 / network failure / Cloud Run Job timeout で中断。低頻度だが既知の障害モード。
- 影響: 利用側で本文順復元クエリを書くと重複チャンクを取得 → LLM 全文プロンプトに同じテキストが 2 回入る / 順序が不安定（DISTINCT で潰すと片方の chunk 内容が落ちる可能性）。
- 根拠: `_delete_pending_gemma_rows`（L1928-L1933）の DELETE 条件は `AI_STATUS != 'completed'` のため、既存 `completed` 行は消されない。INSERT は WRITE_APPEND（L1517-L1519 / L2024-L2025）のため**既存 completed と新規 completed が共存**する。CHUNK_INDEX 追加自体は問題を作らないが、利用側に「ORDER BY CHUNK_INDEX で本文順復元可能」と案内するため、重複時の挙動を明記しないと誤用される。
- 推奨対応 **[方向性]**: プラン P2-1 の利用側ガイドに「同 DOC_ID の重複 `completed` 行が存在し得るため、本文連結時は `(DOC_ID, CHUNK_INDEX)` で `ROW_NUMBER() OVER (PARTITION BY DOC_ID, CHUNK_INDEX ORDER BY EXTRACTED_AT DESC) = 1` で重複排除する」または「監視クエリで `COUNT(*) GROUP BY DOC_ID, CHUNK_INDEX HAVING COUNT(*) > 1` を定期実行する」のいずれかを明記する。**根本対処は別タスク**（004 B-4 の MERGE 化）であり本プランのスコープ外で正解だが、CHUNK_INDEX 利用ガイドには重複前提の記述が必要。

### #3 ロールバック手順の `ignore_unknown_values` 前提が実コードに存在しない

- 箇所: `docs/plans/tools-013_tdnet_chunk_index_column_20260517_223000.md:L78`（P0-1 ロールバック）
- 事象: プラン記述「書込みコード（P0-2/P0-3）は CHUNK_INDEX 列が無くても LoadJobConfig が `ignore_unknown_values` で吸収するため、列削除後も Load Job は走る」は **誤り**。実コードに `ignore_unknown_values` 設定が無い（0 件 Grep 確認）。ロールバックで `ALTER DROP COLUMN` を先に打つと書込みコード revert 前に Load Job がエラー停止する。
- トリガー: P0-1 ロールバック実行時。
- 影響: ロールバック手順そのものが破綻。緊急時にプラン通りに進めると tdnet-load-daily が止まる。
- 根拠: `Grep "ignore_unknown_values|schema_update_options|autodetect" scripts/tdnet_load_parallel.py` → 0 件。`LoadJobConfig` の利用箇所 3 箇所（L1434, L1517, L2022）はいずれも `source_format` と `write_disposition` のみ設定。
- 推奨対応 **[検証済み]**: プラン L78 のロールバック手順を以下に修正する:
  ```
  ロールバック順序（厳守）:
    1. 書込みコード revert（P0-2/P0-3/P0-4）
    2. Cloud Run Job 再 deploy
    3. ALTER TABLE DROP COLUMN CHUNK_INDEX
  逆順実行（DROP 先）は Load Job 失敗を招くため禁止。
  ```
  併せて P0-1 の検証手順 #4「`SELECT *` 系クエリが壊れていないことを確認」だけでなく、**書込み側の Load Job が新スキーマで動作することを smoke で確認**するステップを追加する（順序保証: ALTER → コード deploy → smoke の順）。

### #4 `tdnet_load_recovery.py:L487` の既存 `CHUNK_INDEX=0` 書込みバグが新スキーマで稼働

- 箇所: `scripts/tdnet_load_recovery.py:L487` / プラン非スコープ宣言（L57「旧 `tdnet-load-recovery` の書込み経路（廃止予定）」）
- 事象: 旧 recovery スクリプトは既に `"CHUNK_INDEX": chunk_data.get("chunk_index", 0)` を row 辞書に含めているが、同ファイル `create_chunks_for_tdnet`（L383-L393）の戻り dict には `chunk_index` キーが存在しない（`{"chunk_text": ...}` のみ）。`.get(..., 0)` のフォールバックが効いて **全 chunk が `CHUNK_INDEX=0`** で BQ に書き込まれる。
- トリガー: ALTER ADD COLUMN 実行後、旧 `tdnet-load-recovery` がオペレータ手動・スケジューラ残骸・障害復旧手順書のいずれかで起動された瞬間。
- 影響: 該当日の全 chunk が `CHUNK_INDEX=0` で永続化。`ORDER BY CHUNK_INDEX` クエリは同 ci=0 の N 行を任意順序で返す（順序復元失敗）。さらに「過去データ NULL」と「新規データ 0,1,2,...」の二項分布前提の利用側コードが、第三のパターン「全行 0」に遭遇して誤動作する。
- 根拠: `scripts/tdnet_load_recovery.py:L487` の `chunk_data.get("chunk_index", 0)` と同 `create_chunks_for_tdnet` 戻り値が `{"chunk_text": ...}` のみであることをコード上で確認。プランは「廃止予定」を理由に非スコープ宣言しているが、廃止完了までは実行され得る。
- 推奨対応 **[方向性]**: プラン P0 に**1 件追加**して、旧 recovery のガードを最低限以下のいずれかで入れる:
  - 案 A（最小）: `tdnet_load_recovery.py` のメイン入口で `sys.stderr.write("DEPRECATED: 新アーキ稼働中。recovery は禁止")` + `sys.exit(2)` を入れて起動を遮断
  - 案 B（中）: `chunk_data.get("chunk_index", 0)` を `enumerate` の `ci` で書き換える（recovery 廃止までの暫定）
  - 案 C（簡易）: row 辞書から `CHUNK_INDEX` キーを削除し NULL 補完に任せる（誤データを残さない）
  - **推奨は案 A**: 「廃止予定」と言いつつ稼働可能な状態を放置することが構造的リスク。CLAUDE.md §4.4「破壊的操作」の文脈で、誤起動 1 回で混在パターンが汚染するため、起動遮断が最も安全。

---

## 【改善提案】（可読性・保守性）

### #1 段階適用シナリオ A/B/C 別の検証手順を分離

- 箇所: プラン L316-L339（検証戦略）
- 現状: smoke / dev / 本番判断基準は記載されているが、deploy 順序（ALTER 先 / コード先 / 同時）別の検証が分離されていない。
- 提案: 「**deploy 順序**」セクションを検証戦略の冒頭に追加し、「ALTER → コード deploy → smoke の順固定」と明記。各段階の事前確認クエリ（ALTER 直後の `INFORMATION_SCHEMA.COLUMNS` 確認、コード deploy 直後の py_compile + 1 件 Load Job 確認）を箇条書きする。

### #2 EDINET 側の同種プラン誘導

- 箇所: プラン L364「EDINET 側テーブル（`ir_documents_enhanced`）の対応（UNION 互換性は別途検討）」
- 現状: 非スコープ宣言のみで、後続プランへの誘導なし。
- 提案: 「EDINET 側にも同種の chunk 順序復元不能問題が存在。`docs/plans/` に別プランとして起票予定」と明記し、本案件完了後の TODO として追跡可能にする。`scripts/edinet_load_parallel.py` 側にも `enumerate(chunks)` パターンが存在するなら、本プラン雛形を流用できる。

### #3 監視クエリの追加

- 箇所: プラン全体（監視・アラート観点が薄い）
- 現状: 回収手順（L337-L339）は事後検出。事前監視がない。
- 提案: 以下 2 つの監視クエリを `bq_tdnet_documents.md` または別途監視テーブルに追記:
  ```sql
  -- 異常 1: ai-finalize 後に CHUNK_INDEX=NULL の completed 行
  SELECT COUNT(*) FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE AI_STATUS='completed' AND CHUNK_TEXT IS NOT NULL
    AND CHUNK_INDEX IS NULL
    AND SUBMISSION_DATE >= '<cutoff>';
  -- 期待: 0

  -- 異常 2: 同 (DOC_ID, CHUNK_INDEX) の重複（B-4 中断起因）
  SELECT DOC_ID, CHUNK_INDEX, COUNT(*) AS cnt
  FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
  WHERE CHUNK_INDEX IS NOT NULL
    AND SUBMISSION_DATE >= '<cutoff>'
  GROUP BY DOC_ID, CHUNK_INDEX HAVING cnt > 1;
  -- 期待: 0 行
  ```

### #4 P0-1 OPTIONS description の日付明示

- 箇所: プラン L62（ALTER DDL の `OPTIONS(description=...)`）
- 現状: 「NULL = メタデータのみ行 / 旧データ」と書かれているが、「旧データ」の cutoff date が記載なし。
- 提案: `description="同一 DOC_ID 内のチャンク順序（0 始まり）。NULL = メタデータのみ行 / 2026-05-17 以前ロード分（順序復元不能）"` と日付を埋め込む。BQ INFORMATION_SCHEMA から AI が直接読める情報になる。

### #5 アンチパターン対応表の T-3 適用範囲明示

- 箇所: プラン L301-L308（対応アンチパターン表）
- 現状: P0-2/P0-3 に T-3 が紐づけられているが、T-3 は「Python 内部 key の tuple 化」の話で、本プランは「BQ 列として露出」の派生。
- 提案: 表の脚注で「T-3 は Python 内部実装、本プランは BQ 列露出。両者は補完関係」と一言補足する。または T-3 知見 MD 側（`013_tdnet_load.md §T-3`）に「BQ 列としても露出（2026-05-17 〜）」を追記する（P1-1 で既に方針記載済み、OK）。

---

## 【確認できなかった事項】

- `tdnet_load_parallel.py` に `--limit` フラグが実装されているか（smoke 戦略 L317 の前提）。`scripts/` の CLI 定義を確認できなかった。実装されていない場合は smoke の手順を「最小 docs のテストデータで 1 件流す」に書き換える必要がある。
- dev プロジェクトの BQ 同等テーブルが存在するか（L73「dev プロジェクトの同等テーブル（あれば）」）。存在しない場合の代替（prod 内の `*_DEV` テーブル等）の方針。
- `LoadJobConfig.ignore_unknown_values` を全 3 箇所に追加した場合の副作用（型不一致行が silent drop される regression）の評価。本レビューでは「順序明示でロールバックを安全化」を推奨したが、`ignore_unknown_values=True` 設定で「列削除後も Load Job が走る」案も技術的には可能。トレードオフの選択は提出元に委ねる。
- BQ DROP COLUMN の課金挙動（7 日論理削除→物理削除）が partition 課金カラムに与える影響。プラン L78 で「7日後解放」と記載があるが、Vector Index 再構築の要否は未確認。

---

## 評価サマリ（再掲）

- 設計の中核（ALTER NULLABLE + 3 経路同期修正）は妥当。`enumerate(doc.chunks)` の順序保証は実コード上問題なし。
- ロールバック・段階適用順序・旧 recovery バグ・B-4 重複行との相互作用の 4 点を修正すれば実装着手可能。
- 過去データ NULL 据え置き方針は妥当（再ロードコスト > 運用負荷）。利用側ガイドの記述粒度（縛りはかけない方針）も問題なし。

---

## 返却 2026-05-17

### 重大な指摘
- #1 段階適用順序の明示: [採用] ALTER → コード deploy → smoke の順固定を検証戦略に追記
- #2 重複行に CHUNK_INDEX 重複が乗る regression: [採用] 利用側ガイド (P2-1) に DISTINCT / ROW_NUMBER 注記
- #3 ロールバック手順の ignore_unknown_values 前提誤り: [採用・必須] ロールバック順序を「コード revert → ALTER DROP COLUMN」に修正
- #4 tdnet_load_recovery.py:L487 既存バグ: [採用] P1-2 として起動遮断ガード (sys.exit(2)) 追加項目を新設

### 改善提案
- #1 段階適用シナリオ別検証分離: [採用] 検証戦略をシナリオ A/B/C で分離
- #2 EDINET 側同種プラン誘導: [採用] 非スコープに「EDINET 側は別プランで対応」明示
- #3 監視クエリ追加: [採用] 本番適用判断基準に CHUNK_INDEX NULL 監視 SQL を追加
- #4 OPTIONS description に cutoff date 明示: [採用] ADD COLUMN の OPTIONS description を更新
- #5 アンチパターン表の T-3 適用範囲補足: [採用] 表に注釈追加

### 確認できなかった事項
- --limit フラグ実装有無: 実装着手時に確認（プランに TODO 追記）
- dev プロジェクト BQ 同等テーブル: 実装着手時に確認（プランに TODO 追記）
- ignore_unknown_values=True 3 箇所追加の代替案: [見送り] 順序遵守 (ALTER 先) で対処、設定追加は他経路への影響評価コストが見合わない
- BQ DROP COLUMN の Vector Index 再構築要否: ロールバック実行直前に要確認、プランの回収手順に注記追加

### ユーザー既指示の前段方針
- P2-1 「縛り」表現を「順序復元が必要な場合の参考」レベルに緩和: [採用]（ユーザー指示）
