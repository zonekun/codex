# コードレビュー: 受注アダプタ非先行指標メトリクス除去計画

- 日時: 2026-05-07 07:36 JST
- 対象: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md`
- パターン: 4（新規開発・設計計画のレビュー）
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 553社の structure.json から type:"completed" メトリクスを除去し、completedのみ49社はアダプタごと削除する一括クリーンアップ計画。
- 品質評価: **B** — 目的・ロジックは明快で手順も整っているが、structure.json の「バージョン構造」との整合性・49社カウントの根拠・GCS削除の順序誤りという重大な実装リスクが3件存在する。
- 主要リスク:
  1. **structure.jsonのversions配列を無視した修正ロジック**: バージョン構造がプランに記載されていない。誤実装すると全バージョンの metrics を削除したり、current_version の更新を忘れたりする。
  2. **GCS削除順序の誤り**: Step 3でstructure.json（data_available=false版）のGCSアップロードがextract_adapter削除より後に置かれており、中間状態で矛盾が発生する。
  3. **49社カウントの根拠が脆弱**: プランにカウント根拠クエリが示されておらず、実際の数値との乖離リスクがある（ローカル確認でも数値が合わない可能性）。

---

## 【パターン4のみ: 新規計画評価】

### 技術選定の妥当性

- Pythonスクリプト（`scripts/cleanup_completed_metrics.py`）による一括修正 + dry-run実装という選択は妥当。
- `--dry-run`モード必須がプランに明記されており、CLAUDE.md §破壊的操作必須ルールに準拠している。
- GCS操作に `gcloud storage cp` を使う選択は適切（gsutil rsyncではない点も良い）。

### 既存システムとの統合

- [x] **extract_order_backlog.py との整合**: プランは「extract_order_backlog.pyはstructure.jsonのmetricsを動的参照するため、metricsが減れば自動的に抽出範囲が縮小する」と記載しているが、これは**部分的に誤り**。`extract_order_backlog.py` の `build_extraction_prompt()`（L44）は `structure.get("metrics", [])` でflat accessしており、versions配列の「現在有効なバージョン」を選択するロジックを持たない。スクリプトが直接structure.jsonのトップレベル `metrics` キーを参照している場合は正しいが、structure.jsonスキーマ（089 MD §アダプタ正式スキーマ定義）では `versions[].metrics` 配下にある。この不整合は、スクリプトが実際どのようにstructure.jsonを読んでいるかに依存する。

- [x] **structure.jsonのバージョン構造**: 089 MD §structure.json正式スキーマでは `versions` 配列が必須であり、`metrics` は `versions[].metrics` に存在する。プランの「修正ロジック: completedのみ/leading=0 → `data_available=false`, `metrics=[]`」という記述が、versions配列のどのバージョンを操作するのかが明記されていない。「current_versionが指すバージョンのみ修正するのか」「全バージョンを修正するのか」「valid_until=nullのバージョンのみか」が不明確。誤実装リスクが高い。

- [x] **extract_order_backlog_batch.py への影響**: バッチスクリプトがGCSからstructure.jsonを直接読む場合、ローカル修正後のGCSアップロードタイミングとバッチ実行が競合する可能性がある（ただしPhase 7 BQロード前のため現時点では実害なし）。

- [x] **`data/quarterly_adapters/` ディレクトリの存在**: git statusに `data/quarterly_adapters/` が未追跡ファイルとして存在しており、こちらにも旧形式のアダプタJSONが存在する（`135A.json`, `135A_extract.json` 等の命名規則）。プランはこのディレクトリへの言及がなく、修正対象から漏れる可能性がある。

### リスク・コスト

- **GCPコスト**: GCS操作（481社のstructure.json cp + 49社のrm）は無視可能（$0.001未満）。
- **処理時間**: 481社のjson修正はローカルで1分以内。GCSアップロードは481 × 1KB = 481KB程度、十分高速。
- **ディスク**: 修正対象ファイルはローカルの `meta/quarterly/` にあり、一時ファイル不要。CLAUDE.md §長時間バッチジョブのディスク管理義務の対象外。
- **撤退基準**: 明示されていない。dry-run確認後に進める点は良いが、「本実行後に問題が判明した場合の復旧方法」がない（gitの追跡対象であるため `git restore` / `git diff` で確認可能だが、GCSはgitと連動しない）。

### 抜け漏れ

- [x] **GCS操作順序の問題**（重大）: Step 3の手順が以下の順序になっている:
  1. Step 5: 修正済み structure.json 481社をGCSアップロード
  2. Step 6: 削除対象49社の extract_adapter.json をGCSから削除
  3. Step 7: 削除対象49社の structure.json もGCSアップロード（data_available=false版）

  Step 5で481社（修正済み混在432社 + completedのみ49社）のstructure.jsonをアップロードするのか、Step 7で別途49社をアップロードするのかが矛盾している。49社のstructure.json（data_available=false版）はStep 5に含まれるべきだが、Step 7で「も」と書かれており、二重アップロードか、Step 5の481社が混在432社のみなのかが不明。また、Step 6でextract_adapter.jsonを削除した後に、対応するstructure.jsonがまだ旧版（data_available=true, completedのみ）のままの中間状態が一時的に生じる可能性がある。抽出バッチが同時実行されると旧アダプタで旧structureを参照しに行く（ただしextract_adapterがなければ処理はスキップされる）。

- [x] **breakdown_dimensions削除ロジックの曖昧さ**: プランの注意事項に「残存metricsが参照する場合のみ残す」とあるが、判定ロジックが曖昧。breakdown_dimensions の `items` がメトリクス名に含まれるかどうかの判定基準が未定義（substring? exact match? 日本語正規化の問題？）。089 MDの extract_order_backlog.py L49: `bd["items"]` との照合がどのように行われるかが計画から読み取れない。

- [x] **parent_metric連鎖削除の考慮**: プランの注意事項「`parent_metric`が削除対象のcompletedメトリクスを指す子メトリクスも連鎖削除」は記載あり。ただし連鎖が2段階以上（child → completedParent → さらにparent）あった場合の処理が明記されていない。

- [x] **git rm vs 通常削除**: Step 2で「`meta/quarterly/{ticker}_extract_adapter.json` を `git rm`」と記載。ただし git status の状況によっては `git rm` が失敗する場合がある（untracked fileの場合はgit rmは不要でただのrm）。実際のファイルがgit管理下かどうかの確認が必要。

- [x] **data_catalog.md 更新**: プランにデータカタログ更新の言及がない。structure.jsonの仕様変更（completedは収集対象外ルール確立）は `data_catalog.md` のGCS quarterly/meta/ セクションに反映すべき項目かもしれない。ただしdata_catalog.mdにquarterly/meta/の記載があるかどうか未確認のため「確認できなかった事項」参照。

- [x] **089知見MD更新の内容が薄い**: Step 10で「type: completed は収集対象外」のガードレール追記と記載されているが、具体的にどのセクションに追記するのか（落とし穴・設計指針・アダプタスキーマ定義のどこか）が明示されていない。

### 目的・スコープの明確性

- 目的は明確（先行指標のみを収集、completedメトリクスを除去）。
- 非スコープ（Phase 7 BQロードには手を付けない、四半期運用フローへの影響は対象外）が暗黙的にはわかるが明示されていない。

### 段階的検証計画

- dry-runで事前確認 → 本実行という2段階構成は良い。
- dry-run後の「10件目視」確認項目（サマリCSVで削除対象メトリクス名が売上高/完成工事高等であること）が具体的で良い。
- ただし「本実行後の検証」が「修正後の集計: leading metricsのみ504社・data_available=false 59社」の1行のみで、GCS反映確認手順がない。

### 完了条件の検証可能性

- 「修正後の集計: leading metricsのみ504社・data_available=false 59社(既存10+新規49)」は具体的で良い。
- GCS側の確認（`gcloud storage ls gs://stock_data_1930932/quarterly/meta/` で削除済み確認等）が含まれていない。

### データカタログ整合

- 使用するデータ（`meta/quarterly/*_structure.json`、GCS `quarterly/meta/`）は089 MDに定義されており、data_catalog.mdへの新規登録は不要と判断される。

---

## 【重大な指摘】（即修正）

### #1 structure.jsonのバージョン構造を無視した修正ロジック

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:37-41`（Step 1 ロジック記述）
- 事象: プランは「completedのみ → `data_available=false`, `metrics=[]`」「混在 → completedメトリクスを削除」と記述しているが、089 MDの正式スキーマでは `data_available` と `metrics` は `versions[]` の配下にある。cleanup_completed_metrics.py を実装する際に `structure["data_available"]` / `structure["metrics"]` とトップレベルアクセスしてしまうと、JSONが正常に読めずに全社スキップか、誤ったフィールドを修正することになる。
- トリガー: スクリプト実装時にスキーマ確認を怠った場合。extract_order_backlog.py の実装（L44: `structure.get("metrics", [])`）を見て、structure.jsonがトップレベルにmetricsを持つと誤認した場合。
- 影響: 481社のstructure.jsonが修正されないまま処理完了扱いになる（無言のスキップ）か、誤フィールドを修正してstructure.jsonを破損する。
- 根拠: 089 MD §structure.json正式スキーマ（L104-129）で `versions[].data_available` / `versions[].metrics` と明記。extract_order_backlog.pyのL44 `structure.get("metrics", [])` とのGAPは、このスクリプトがPOC段階のもので、本番パイプラインがstructure.jsonをどう読むかに依存する。
- 推奨対応: プランの「修正ロジック」節に「操作対象は `versions[].data_available` と `versions[].metrics`」「valid_until=null のバージョンを対象とする」を明記する。cleanup_completed_metrics.py実装時には089 MDのスキーマ定義から `versions` 配列の処理を正確に実装すること。

### #2 GCS削除順序とステップ構成の矛盾

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:55-61`（Step 3 手順）
- 事象: Step 5「修正済みstructure.json 481社をGCSアップロード」とStep 7「削除対象49社のstructure.jsonもGCSアップロード（data_available=false版）」が別ステップに分かれており、481社にcompletedのみ49社が含まれるのかどうかが不明。また、Step 6でextract_adapterを先に削除した後、対応するstructure.jsonがdata_available=true（completedのみ）のまま残る中間状態が発生する。
- トリガー: 実行手順をそのまま順番通りに実行した場合。
- 影響: GCS上でstructure.jsonがdata_available=true（completedのみ）なのにextract_adapterが存在しない状態が一時的に生じ、バッチ実行がこの中間状態に当たると予期しない動作をする可能性がある。また、481社に49社が含まれる場合はStep 7が冗長な二重アップロードになる。
- 根拠: Step 3各ステップの対象社数と対象ファイルの説明が矛盾している。
- 推奨対応: 49社について「先に structure.json（data_available=false）をアップロード → 次に extract_adapter.json を削除」の順序とし、"481社"を"混在432社"と"completedのみ49社"を明確に分けて記述する。

### #3 data/quarterly_adapters/ ディレクトリの修正漏れリスク

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md` 全体
- 事象: git statusに未追跡の `data/quarterly_adapters/` ディレクトリが存在し、内部には `{ticker}.json`, `{ticker}_extract.json` という別形式のアダプタファイルが2,628件存在する。プランは `meta/quarterly/{ticker}_structure.json` のみを対象としており、このディレクトリへの言及がない。
- トリガー: cleanup_completed_metrics.py が `meta/quarterly/` のみをスキャンし、`data/quarterly_adapters/` を見落とした場合。
- 影響: 旧形式アダプタに completedメトリクスが残存し続ける。下流スクリプトがどちらのパスを参照するかによって、除去の効果がゼロになる可能性がある。
- 根拠: `git status` で `data/quarterly_adapters/` が `??` (untracked)として存在。ファイル名形式が `meta/quarterly/` と異なる（`{ticker}.json` vs `{ticker}_structure.json`）。
- 推奨対応: `data/quarterly_adapters/` の役割（旧形式？廃止済み？並行管理？）を確認し、プランに「このディレクトリは対象外である理由」または「こちらも修正対象に含める」を明記する。

---

## 【改善提案】（可読性・保守性）

### #1 49社カウントの根拠を明記する

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:27-31`
- 現状: 「49社削除対象の内訳: completedのみ48社 + leading=0の1社(9216 ビーウィズ)」とあるが、48社のカウント根拠（どのBQクエリ・スクリプトで集計したか）が不明。
- 提案: dry-runのサマリCSVで事前確認する旨が既にStep 2に記載されているため、「dry-run後に実際の社数が49社と一致することを確認してから進む」という検証ポイントをStep 2末尾に1行追加する。

### #2 breakdown_dimensions保持条件の判定ロジックを明確化する

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:41`, `78-80`
- 現状: 「残存metricsが参照するもののみ残す」「残存metricsのname内にdimension item名が含まれる場合のみ保持」という注意事項があるが、実装時の具体的な判定ロジック（文字列一致？ substring match？）が不明。
- 提案: cleanup_completed_metrics.py のコメントまたはプランに「dimension item名をメトリクス名のいずれかに含む場合にretain」等の擬似コードを1行追加する。

### #3 GCS反映の確認コマンドを追記する

- 箇所: `docs/plans/tools-089-2_adapter_completed_metrics_cleanup_20260507_073346.md:62-70`（検証方法）
- 現状: ローカルの目視確認のみが記載されており、GCS側の確認手順がない。
- 提案: 検証方法に「`gcloud storage ls gs://stock_data_1930932/quarterly/meta/{削除対象ticker}/ | grep extract_adapter` で削除確認」の1行を追加する。

---

## 【確認できなかった事項】

- `data/quarterly_adapters/` の内部ファイルが `meta/quarterly/` と同一内容なのか、旧形式・廃止済みなのか（Readツールで確認可能だが、プランの解釈に依存するため不明点として記録）。
- `extract_order_backlog.py` L44 `structure.get("metrics", [])` が、実際のstructure.jsonのトップレベルを参照しているのか、versions配列から正しくバージョン選択して参照しているのか（スクリプトを再読すると、POC版の `structure` 変数はJSONファイルをそのままロードしたdict。089 MDスキーマでは `versions[].metrics` だが、POC段階のstructure.jsonがトップレベルに `metrics` を持っていた可能性がある。本番展開されている553社のstructure.jsonが正式スキーマ準拠かどうかで影響が分かれる）。
- `data_catalog.md` に `quarterly/meta/` の記載があるかどうか（最初の80行のみ確認済み）。
