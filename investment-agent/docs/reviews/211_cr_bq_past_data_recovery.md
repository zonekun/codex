# コードレビュー: TDNET_DOCUMENTS_ENHANCED 過去データリカバリ計画

- 日時: 2026-05-18 23:45 JST
- 対象: `docs/plans/tools-013_bq_past_data_recovery_20260518_232030.md`
- パターン: 4（新規計画レビュー）
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `TDNET_DOCUMENTS_ENHANCED` テーブルの過去データに残存する `EXTRACTED_AT` NULL および アルファベット ticker の `FILER_NAME` 空文字を遡及修正する計画。A-1（EXTRACTED_AT）は NULL 許容案を採用し、B-1（FILER_NAME）は `FILE_NAME` からの `REGEXP_EXTRACT` で UPDATE する。
- 品質評価: **B** — A-1 の設計判断（NULL 許容）は合理的。B-1 の UPDATE 手順は概ね堅実だが、正規表現パターンの脆弱性・FILE_NAME NULL 残存行への対処・UPDATE の冪等性担保に改善余地がある。
- 主要リスク:
  1. REGEXP パターンが会社名に `_` を含む銘柄でサイレントに NULL を返し、UPDATE から除外される
  2. FILE_NAME が NULL の行は UPDATE 対象外のまま `FILER_NAME = ''` で残存するが、残存件数の確認・報告方針が計画に明記されていない
  3. パーティション分割実行の分割粒度（日 or 月 or 年）が計画に明示されておらず、実行者が判断を迫られる

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

- A-1（EXTRACTED_AT NULL 許容）: **妥当**。過去ロード日時は復元不能であり、虚偽固定値 UPDATE よりも NULL 許容 + クエリ側 DISTINCT が正しい設計判断。`bq_tdnet_documents.md §重複行への注意` のクエリ指針と整合している。
- B-1（REGEXP_EXTRACT UPDATE）: **条件付き妥当**。`FILE_NAME` は blob_name であり、実際のパスフォーマット `tdnet/TICKER/YYYYMMDD_TICKER_会社名_カテゴリ_タイトル_DOCID.pdf` が一定でれば有効。ただし会社名フィールドに `_` が含まれる場合（例: 「○○\_HD」「AB\_C」型の名称）に最初の `_` で切れて誤抽出になるリスクがある（後述 #1）。
- BQ DML UPDATE をパーティション分割で実行する方針: **妥当**。全件スキャン回避のコスト意識が計画に明示されており評価できる。

### 既存システムとの統合

- [x] `TDNET_DOCUMENTS_ENHANCED` テーブルのパーティションキー `SUBMISSION_DATE` を UPDATE 条件に組み込んでいる
- [x] 修正済みスクリプト `tdnet_load_parallel.py`（commit 8459c94a）との整合: 新規ロード分は `EXTRACTED_AT` 付き・`filer_name` 保存済みのため本計画の UPDATE 対象外となる。計画の「非スコープ: 新規ロード分は修正済み」記述と整合している
- [x] `bq_tdnet_documents.md §重複行への注意` でクエリ案1（DISTINCT）を標準化する方針（STEP 4）が計画に含まれている
- [ ] `013_tdnet_load.md` への追記（STEP 4）がリストされているが、追記すべき内容の具体的な見出し・セクション名が計画に記載されていない。実行者が判断を迫られる可能性あり

### リスク・コスト

- BQ DML UPDATE のコスト: 計画内で「数十 GB〜数百 GB 規模」と言及し、パーティション分割実行でコスト削減する方針を示している。ただし対象の実際のパーティション数・総行数は STEP 1 実行後に判明するため、事前コスト上限（例: $X を超えたら中断）の定義がない
- 失敗時の撤退基準: 記載なし。UPDATE 途中でジョブが失敗した場合（例: quota 超過・タイムアウト）、部分更新済み状態で処理が止まる可能性がある。BQ DML はトランザクションではないため、途中失敗後の再実行クエリ（同 WHERE 条件で実行すれば冪等）が有効かどうかを明示すべき
- 冪等性: B-1 UPDATE クエリは `WHERE FILER_NAME = ''` を条件にしているため、再実行しても既更新行（`FILER_NAME != ''`）は変更されない。**冪等性はある**が、計画に明示されていない

### 抜け漏れ

- [ ] **REGEXP パターンの NULL 返し件数の事前把握が計画に無い**: STEP 2 の dry-run 50件で「NULLがあれば確認する」とあるが、全件に対してどれだけ NULL が発生するかを定量確認する手順がない。UPDATE 後の残存確認クエリは `FILER_NAME = ''` を見ているが、REGEXP_EXTRACT が NULL を返した行（WHERE 句の最終条件で除外される）は永続的に `FILER_NAME = ''` のまま残る可能性がある
- [ ] **FILE_NAME が NULL の行数確認が明示されていない**: 注意事項に「FILE_NAME = NULL の行は自動除外」と記載はあるが、STEP 1 の確認クエリに FILE_NAME IS NULL の行数カウントが含まれていない。残存件数が多い場合の対応方針も「別途対応を検討」のみ
- [ ] **数字のみ ticker で `FILER_NAME = ''` の行の扱い**: 注意事項に「非スコープ」と書かれているが、旧アーキ期間中に数字のみ ticker でも同じ経路（_docs_from_ai_state で filer_name="" ハードコード）を通っていた可能性がある。diff を見ると `_docs_from_ai_state` は ticker によらず `filer_name=""` を返していたため、アルファベット ticker 限定という前提が要確認
- [ ] **完了条件の `remaining_empty = 0` は FILE_NAME NULL 行を暗黙に除外している**: STEP 3 の完了条件が `remaining_empty = 0（FILE_NAME が NULL の行は除く）` と括弧書きで但し書きしているが、残存確認クエリ自体には `AND FILE_NAME IS NOT NULL` が入っていない。クエリ結果が 0 でも実際には FILE_NAME NULL の `FILER_NAME = ''` 行が残っている可能性がある（クエリと完了条件の不整合）

### 目的・スコープの明確性

- 目的は1-2文で明確に言い切れている（EXTRACTED_AT NULL + FILER_NAME 空文字の遡及修正）
- 非スコープも列挙されており明快。ただし「アルファベット ticker 以外は非スコープ」の根拠（= diff で確認した _docs_from_ai_state の修正前動作）が計画に書かれておらず、後から読んだ人が判断を追えない

### 段階的検証計画

- STEP 2 の dry-run（LIMIT 50）で抽出パターンを事前確認する設計は評価できる
- STEP 3 で単日（2026-05-18）先行実行 → 問題なければ過去分という 2 段構成になっている
- ただし各 STEP の「失敗時は何をするか」の記述が全くない

### 完了条件の検証可能性

- STEP 1: 「影響行数・日付範囲が判明し、コスト概算が確定できること」→ 主観的。「STEP 1 クエリが正常完了し結果を記録した」の方が明確
- STEP 3: `remaining_empty = 0` → 原則として検証可能だが、上述のクエリと完了条件の不整合がある（#2 参照）
- STEP 4: 「両ファイルへの追記が完了し git commit 済み」→ 検証可能

### データカタログ整合

- 使用テーブル `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` は `data_catalog.md` → `docs/data_catalog/bq_tdnet_documents.md` に存在し、パーティションキー・スキーマが正確に記載されている
- STEP 4 で `bq_tdnet_documents.md` 更新を計画に含んでいる点は整合している

---

## 【重大な指摘】（即修正）

### #1 REGEXP パターンが会社名に `_` を含む銘柄でサイレント NULL を返す

- 箇所: `docs/plans/tools-013_bq_past_data_recovery_20260518_232030.md` §B-1 UPDATE クエリ
- 事象: パターン `r'/[0-9]+[A-Za-z]+/[0-9]{8}_[0-9A-Za-z]+_([^_]+)_'` の `[^_]+` は `_` を含まない最短マッチのため、会社名に `_` が含まれる銘柄（例: 「A\_B Holdings」相当の命名）では会社名の前半部分のみ取得される。会社名が `_` を含まない場合も、カテゴリ部分（例: 「決算短信」）まで一続きで取れる場合と取れない場合が混在する可能性がある
- トリガー: FILE_NAME の会社名フィールドに `_` が含まれる銘柄を UPDATE したとき
- 影響: 部分的な会社名（例: 「A」）が FILER_NAME に書き込まれる。空文字よりも発見が困難な誤データになる
- 根拠: パターン `[^_]+` は `_` が来た時点でマッチを打ち切る。ファイル名フォーマット `YYYYMMDD_TICKER_会社名_カテゴリ_タイトル_DOCID.pdf` の「会社名」フィールド自体に `_` が含まれうる
- 推奨対応: [方向性] STEP 2 のdry-run 50件で、取得した `extracted_name` が正しい会社名の完全形かどうかを確認する手順を明示的に強化する。具体的には「会社名に `_` が含まれる銘柄のサンプルを意図的に含めて確認する」手順を STEP 2 に追記する。パターン修正が必要な場合は `([^_]+(?: [^_]+)*)` のような空白許容パターンへの変更を検討するが、実データの命名規則の実態確認後に行う

### #2 完了条件クエリと実態の不整合（FILE_NAME NULL 行がカウントされない）

- 箇所: `docs/plans/tools-013_bq_past_data_recovery_20260518_232030.md` §STEP 3「UPDATE 後の残存確認」
- 事象: 残存確認クエリが `WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]') AND FILER_NAME = ''` のみで、`FILE_NAME IS NOT NULL` 条件を含まない。一方、完了条件は `remaining_empty = 0（FILE_NAME が NULL の行は除く）` と書かれており、クエリと完了条件の間に乖離がある
- トリガー: FILE_NAME が NULL の行が存在する場合、完了確認クエリが 0 を返さず STEP が終わらない、または NULL 行を無視してカウントする場合に誤って 0 と見なす
- 影響: STEP 3 完了判定が曖昧になる。`remaining_empty > 0` でも「FILE_NAME NULL のせいか、REGEXP NULL のせいか」が残存確認クエリだけでは判断できない
- 根拠: 計画 §STEP 3 の SQL と完了条件の記述を照合した結果
- 推奨対応: [検証済み] 残存確認クエリを以下の2本立てにする:
  1. `WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]') AND FILER_NAME = '' AND FILE_NAME IS NOT NULL` → 期待: 0（REGEXP NULL 行も含む残存）
  2. `WHERE REGEXP_CONTAINS(TICKER, r'[A-Za-z]') AND FILER_NAME = '' AND FILE_NAME IS NULL` → 期待: 件数を記録（対応不要 or 別途検討対象）
  完了条件は「クエリ1が 0 かつクエリ2の件数を記録した」とする

---

## 【改善提案】（可読性・保守性）

### #1 アルファベット ticker 限定の根拠を計画に記載する

- 箇所: §注意事項「アルファベット ticker 以外の銘柄は非スコープ」
- 現状: なぜアルファベット ticker に限定するかの技術的根拠（= 修正前の `_docs_from_ai_state` が ticker によらず `filer_name=""` を返していた可能性）が書かれていない
- 提案: 「修正前の `_docs_from_ai_state`（commit 8459c94a 前）は ticker に関わらず `filer_name=""` を返していたが、アルファベット ticker の銘柄に絞る根拠を確認してから非スコープを確定すること」という注記を追加する。もし数字 ticker でも同じ経路を通った期間がある場合は本スコープに含める必要がある

### #2 パーティション分割粒度と実行件数の目安を明示する

- 箇所: §STEP 3「パーティション単位で分割実行」
- 現状: 「日付を変えて順次実行」とのみ書かれており、1日ずつ実行するのか、1ヶ月ずつなのか、あるいは影響パーティション数に応じて変えるのか不明
- 提案: STEP 1 の結果（earliest_date と latest_date）から総パーティション数が判明した段階で、「N パーティション以下なら日単位、それ以上なら月単位でまとめる」等の判断基準を STEP 3 冒頭に追記する

### #3 各 STEP の失敗時対応を追記する

- 箇所: 各 STEP の完了条件
- 現状: 全 STEP で「何がうまくいったら完了か」しか書かれておらず、失敗時の対応（リトライ可否・エスカレーション先・中断判断基準）が無い
- 提案: 少なくとも STEP 3（破壊的 DML）について「UPDATE が中断した場合、同じ WHERE 条件で再実行すれば冪等（既更新行は FILER_NAME != '' のため対象外）」を明記する

### #4 013_tdnet_load.md への追記内容を具体化する

- 箇所: §STEP 4「013_tdnet_load.md に本修正の経緯・対応を追記」
- 現状: 「追記する」のみで、どのセクションに何を書くかが不明
- 提案: 「§現況サマリ 下部に B-1 修正経緯（発生原因: state.json filer_name 未保存、対応: REGEXP_EXTRACT UPDATE + commit 8459c94a）を追記する」等の具体的な記述を計画に含める

---

## 【確認できなかった事項】

- 数字のみ ticker で `FILER_NAME = ''` の行が実際に存在するかどうか: commit 8459c94a 前の `_docs_from_ai_state` は ticker 種別に関わらず `filer_name=""` を返していたため、数字 ticker でも旧 ai-finalize 経路を通った行が同様に `FILER_NAME = ''` である可能性がある。実態は STEP 1 相当の確認クエリを数字 ticker にも走らせて確認が必要
- `FILE_NAME` の実際の命名規則に `_` を含む会社名が存在するか: スクリプトコードから blob_name の生成ロジックを確認すればわかるが、本セッションでは未確認
- テーブルの実際の総行数・サイズ: コスト概算の精度に影響するが BQ クエリ実行なしには確認不能
