# コードレビュー: Faber (2007) 5資産タイミングモデル 計画

- 日時: 2026-05-02 23:29 JST
- 対象: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md`
- パターン: 4 (新規計画)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Faber (2007) 論文に基づく5資産クラスの200日SMAタイミングモデルを新規実装し、日次の攻め/守りシグナルを Cloud Run Job で自動配信する計画。
- 品質評価: **B** — 全体構成は明快でデータソース・フェーズ分けも適切だが、GBOND 処理の設計未確定・Cloud Run での Dropbox 認証パターン未整理・既存コードとの重複排除が不十分。
- 主要リスク:
  1. GBOND（利回り→価格）の変換方式が未確定のまま Phase 1 に含まれており、判定ロジックの根幹が曖昧
  2. Excel 全シート個別パースの設計が欠落 — BB_債券履歴_new.xlsx の LIST シートには SP500/CRB/VIX 等が統合済みだが、計画は「SP500シート」「CRBシート」と個別シートの存在を前提としている
  3. Cloud Run 環境での Dropbox 認証情報の Secret Manager 格納手順が Phase 2 に先送りされているが、Phase 1 のローカル実装段階でハードコードを作り込むとリファクタコストが発生する

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

**全体方針は適切**。Faber ルール自体は「価格 vs 200日SMA」の単純比較であり、Python + pandas で十分。yfinance + Dropbox API DL の組み合わせも既存パターン（`edinet_delay.py`、`menu_bond_update.py`）の流用で実装可能。Cloud Run Job + Cloud Scheduler の日次実行も既存インフラに乗る。

**懸念点**:

1. **Excel シート構造の前提誤り（重大）**: 計画の §5資産クラスとデータソース（L30-36）では SP500 を「`SP500` シート」、CRB を「`CRB` シート」、GBOND を「`GBOND` シート」と記載しているが、**実際の `BB_債券履歴_new.xlsx` には `LIST` という統合シートがあり、SP500 は列インデックス 23、CRB は列インデックス 13 として格納されている**（`scripts/convert_bond_history.py:17-39` の `COLUMNS` マッピング参照）。個別シート名でのアクセスはファイル構造と一致しない可能性が高い。`GBOND` に相当する列は `US10Y`（列インデックス 2）として既に `bond_history.csv` に変換済み。

2. **既存の `data/csv/bond_history.csv` との重複**: `menu_bond_update.py` / `convert_bond_history.py` が既に `BB_債券履歴_new.xlsx` → `data/csv/bond_history.csv` の変換パイプラインを持ち、SP500、CRB、VIX、SKEW、FEAR_GREED 等を CSV 化している。Faber スクリプトが独自に Dropbox API で Excel を DL してパースするのは**二重取得**。`bond_history.csv` を入力とすれば Dropbox API 依存を Phase 1 から排除でき、ローカル実装が大幅に簡素化される。

3. **EFA / VNQ の yfinance 取得は妥当**。ただし yfinance の rate limit や API 変更リスクへの言及がない。既存の `stock_price_load.py` が yfinance を使っているため、エラーハンドリングパターンはそちらを踏襲すべき。

### 既存システムとの統合

- [x] **Dropbox Excel DL パターン**: `016_dropbox.md` に `files_download` の基本パターンあり。ただし、上述の通り `bond_history.csv` 経由のほうが効率的。
- [ ] **`menu_bond_update.py` / `convert_bond_history.py` との関係が未整理**: 両スクリプトは `BB_債券履歴_new.xlsx` の `LIST` シートから SP500/CRB/US10Y 等を `bond_history.csv` に変換済み。Faber スクリプトがこの CSV を入力とするか、独自に Excel を取得するかの設計判断が必要。CSV 入力ならば Phase 1 は Dropbox API 不要になり、Cloud Run デプロイ時のみ Dropbox API を追加する段階的アプローチが取れる。
- [ ] **`menu_signal_check.py` との統合可能性**: SKEW/VIX/F&G のテールリスクシグナルは `bond_history.csv` を入力とする同種のシグナルツール。Faber シグナルも同じ CSV を入力とすれば、将来的にシグナル統合ダッシュボードが自然に構成できる。計画の Phase 3 (L80-82) で言及はあるが、Phase 1 の設計段階で入力データの統一を決めておくべき。
- [ ] **Cloud Run Job 名・Artifact Registry リポジトリ**: 計画に `faber-timing` という Job 名が記載されている (L98) が、Artifact Registry のリポジトリ名（`005_cloudrun_job_deploy.md` の `<repo>` に相当）が未定義。既存パターンでは `stock/`, `tdnet/`, `edinet/`, `tools/` 等のカテゴリ別リポジトリが使われているが、`strategies/` は前例なし。
- [ ] **ntfy 通知**: `scripts/notify.py` の `send_ntfy()` を使う想定だが、呼び出しパターンの記載がない。

### リスク・コスト

**GCP 課金**: Cloud Run Job の日次実行（1回あたり数秒〜十数秒の軽量処理）は事実上無料。yfinance API もフリー。Dropbox API のレート制限も1日1回なら問題なし。**コストリスクは極めて低い**。

**失敗時の撤退基準**: 未定義。Faber モデル自体はバックテスト不要の判定ツール（投資判断のインプット）なので、精度による撤退ではなく「データ取得失敗時の挙動」「通知が来ない場合の検知」が定義されるべき。

**GBOND 変換リスク**: 計画 L38 で「利回りがSMA下回ればBUY」「TLT等の債券ETFで代替するか要検討」と記載されているが、**どちらを採用するかが Phase 1 の完了条件に含まれていない**。これは5資産のうち1資産の判定ロジックが未確定ということであり、Phase 1 完了の定義が曖昧になる。

### 抜け漏れ

- [ ] **`data_catalog.md` への追記計画が欠落**: Faber スクリプトが新たに生成・参照するデータ（日次判定結果、Phase 3 の BQ テーブル）の `data_catalog.md` 登録が計画に含まれていない。
- [ ] **INDEX.md 更新**: 知見 MD `docs/knowledges/strategies/004_faber_timing_model.md` 作成時の `docs/knowledges/INDEX.md` 更新が作業ステップに含まれていない（完了条件 L118 では言及あり）。
- [ ] **Dockerfile の依存ライブラリ**: Phase 2 の Dockerfile に必要な依存（`openpyxl`, `dropbox`, `yfinance`, `pandas`）の一覧が未記載。特に `yfinance` は依存が重い（`requests`, `lxml`, `html5lib` 等）ため、イメージサイズとビルド時間に影響する。
- [ ] **Cloud Run の GBOND Excel パース時の日本語パス問題**: Dropbox API で DL した Excel を `io.BytesIO` でメモリ上パースする場合は問題ないが、一時ファイルに保存する場合は `PYTHONUTF8=1` の設定が必要。Dockerfile テンプレートには `ENV PYTHONUTF8=1` が含まれているが、計画に明記がない。
- [ ] **`bond_history.csv` に存在しない列（GBOND / FEAR_GREED→FAG）**: 計画 L47-53 で補助指標として `FAG`（Fear & Greed）シートを挙げているが、`convert_bond_history.py` では列名 `FEAR_GREED`（列インデックス 30）として既にCSV化済み。シート名と CSV 列名の対応が整理されていない。
- [ ] **`--dry-run` 実装の詳細**: 計画 L68 で `--dry-run` に言及があるが、dry-run で何を検証するかの定義がない（データ取得のみ？ SMA 算出まで？ 通知は送らない？）。
- [ ] **エラーハンドリングの方針**: Dropbox API 失敗時、yfinance 失敗時、Excel パース失敗時のフォールバック戦略が未定義。既存パターン（`016_dropbox.md` の容量不足対応等）の踏襲を明記すべき。
- [ ] **SMA 計算期間の初期データ要件**: 200日SMA を算出するには最低200営業日（約10ヶ月）の履歴が必要。EFA/VNQ の yfinance 取得でどこまで遡るかの指定が欠落。`bond_history.csv` には十分な履歴があるが、yfinance のデフォルト期間（1ヶ月）では不足する。

### 目的・スコープの明確性

**良い点**: 目的（L9-11）は「5資産×200日SMAタイミングモデルの実装」と明快。Phase 分けでスコープが段階的に定義されている。

**改善点**: 非スコープが明示されていない。以下を非スコープとして明記すべき:
- バックテスト・パフォーマンス検証（Faber 論文の再現実験ではない）
- ポジションサイジング・売買執行（シグナル生成のみ）
- 既存 `menu_phase_analyzer.ipynb` の置き換え（補完関係）

### 段階的検証計画

Phase 1 → Phase 2 → Phase 3 の段階分けは適切。ただし:

- Phase 1 の完了条件（L116）「5資産の BUY/SELL 判定が出力される」は**手動目視確認**であり、自動検証可能な条件ではない。例: 「既知の過去日付（例: 2024-01-02）を入力し、手計算と一致すること」のような具体的な検証ケースが望ましい。
- Phase 2 の完了条件（L117）「ntfy 通知が届く」も同様に曖昧。「Cloud Scheduler による自動実行が1回以上成功し、通知が届くこと」と具体化すべき。

### 完了条件の検証可能性

上述の通り、完了条件は存在するが具体性が不足。特に GBOND の利回り/価格反転ロジックの選定が完了条件に含まれていないため、Phase 1 の「完了」の判定基準が不明確。

### データカタログ整合

- SP500、CRB、US10Y（GBOND 相当）は `data/csv/bond_history.csv` に存在（`data_catalog.md` 記載済み）。
- EFA、VNQ は yfinance からの取得であり、`data_catalog.md` に未登録。Phase 1 完了時にカタログ追加が必要。
- Phase 3 の BQ テーブル化が実現する場合、`data_catalog.md` の (a) BigQuery セクションへの追記が必要。

---

## 【重大な指摘】（即修正）

### #1 Excel シート構造の前提が実態と乖離

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:30-36`
- 事象: 計画では SP500/CRB/GBOND を個別シート（`SP500`シート、`CRB`シート、`GBOND`シート）からパースする設計だが、実際の `BB_債券履歴_new.xlsx` の主要データは `LIST` シートに列インデックスベースで格納されている。個別シート名でのアクセスは `FileNotFoundError` or `ValueError` でクラッシュする可能性がある。
- トリガー: Phase 1 Step 2 の Excel パース実装時
- 影響: スクリプト実装の根本設計が変わる。LIST シートからの列インデックスベース抽出か、`bond_history.csv` を入力とするかの設計判断が必要。
- 根拠: `scripts/convert_bond_history.py:17-39` の `COLUMNS` dict で SP500 = col 23, CRB = col 13, US10Y = col 2 として `LIST` シートから抽出している。`scripts/menu_bond_update.py:13-36` の `COL_MAP` も同一構造。
- 推奨対応: (A案) `data/csv/bond_history.csv` を入力とする設計に変更。SP500/CRB/US10Y は既にCSV化済み。Dropbox API は Cloud Run デプロイ時のみ必要。(B案) `LIST` シートからの列インデックスベースパースに修正。いずれにせよ、個別シート前提は修正が必要。**A案を強く推奨** — 既存の変換パイプラインとの二重化を避けられる。

### #2 GBOND 判定ロジックが未確定のまま Phase 1 に含まれている

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:38, 69`
- 事象: 5資産のうち1資産（米10年国債）の判定方法が2案（利回り反転 vs TLT代替）で未確定。Phase 1 の Step 4 で「検証」とあるが、完了条件（L116）に反映されていない。
- トリガー: Phase 1 実装時に方式選択で迷い、実装が停滞する
- 影響: 総合スコア（0-5）の1/5が未定義 = シグナル全体の信頼性に直結
- 根拠: L38 「利回りがSMA下回ればBUY」か「TLT等の債券ETFで代替するか要検討」、L69 「GBOND 利回り→価格反転ロジックの検証（TLT代替も比較）」
- 推奨対応: Phase 1 開始前に方式を確定する。推奨は「US10Y 利回りの SMA を算出し、現在利回り < SMA なら BUY（債券価格上昇中）」— 追加データソース不要で `bond_history.csv` の既存列がそのまま使える。TLT 比較は Phase 3 の拡張として分離。完了条件に「GBOND の判定方式を確定し、知見 MD に根拠を記載」を追加。

### #3 yfinance 取得期間の未指定

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:91-92`
- 事象: `yf.download("EFA")` / `yf.download("VNQ")` のデフォルト期間は直近1ヶ月であり、200日SMA の算出に必要な最低10ヶ月分のデータが取れない。
- トリガー: スクリプト初回実行時に SMA が NaN になり、全資産が判定不能になる
- 影響: 5資産中2資産（EFA/VNQ）の判定が不可能
- 根拠: yfinance `download()` のデフォルトは `period="1mo"`。200日SMA には最低200営業日 + 当日 = 約10ヶ月の履歴が必要。余裕を持って `period="2y"` 程度が適切。
- 推奨対応: 計画の §必要データ に「EFA/VNQ は yfinance `period='2y'` で取得」と明記。スクリプト実装時に `yf.download("EFA", period="2y")` とする。

---

## 【改善提案】（可読性・保守性）

### #1 Phase 1 のデータ取得設計を既存 `bond_history.csv` ベースに再構成

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:60-69`
- 現状: Phase 1 で Dropbox API DL + Excel パース + yfinance 取得をすべて含む設計。実装量が大きく、テスト対象も広い。
- 提案: Phase 1 は `data/csv/bond_history.csv`（SP500/CRB/US10Y 済み）+ yfinance（EFA/VNQ）のみで実装。Dropbox API は Phase 2 の Cloud Run デプロイ時に `bond_history.csv` 相当の Excel DL + パースを追加。これにより Phase 1 の実装が大幅に簡素化され、ローカル動作確認が容易になる。

### #2 出力フォーマットの構造化

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:103-112`
- 現状: 出力イメージがテキストフォーマットのみ。
- 提案: structlog による構造化ログ出力（JSON）と、人間可読なテキスト出力の両方を設計に含める。Cloud Run 実行時はログ集約の観点から構造化ログが望ましい。ntfy 通知用のテキストフォーマットは別途生成。CLAUDE.md のコーディング規約で `print禁止、structlog使用` が定められている。

### #3 非スコープの明示

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:9-11`
- 現状: 目的は記載されているが、非スコープが未定義。
- 提案: 以下を非スコープとして「目的」セクション直下に追記:
  - Faber 論文の再現バックテスト
  - ポジションサイジング・売買執行の自動化
  - `menu_phase_analyzer.ipynb` の置き換え

### #4 Cloud Run Job 作成時の Scheduler 時刻の検討

- 箇所: `docs/plans/strategies-004_faber_timing_model_20260502_232018.md:77`
- 現状: JST 7:00 = 米国市場終了後と記載。
- 提案: 米国市場は EST/EDT で閉まる。夏時間では NYSE 終了が JST 5:00、冬時間では JST 6:00。JST 7:00 は概ね適切だが、夏時間/冬時間で yfinance のデータ反映タイミングが異なる可能性がある。「JST 8:00」にするか、「yfinance のデータ反映遅延（通常30分〜1時間）を考慮して JST 8:00 を推奨」と記載すべき。また、土日は米国市場が休場のため、営業日のみ実行（`is-holiday` Job との連携または cron 式で平日限定）の検討が必要。

---

## 【確認できなかった事項】

- `BB_債券履歴_new.xlsx` に `SP500`、`CRB`、`GBOND` という個別シートが存在するか否か。`LIST` シートの存在は `convert_bond_history.py` / `menu_bond_update.py` から確認済みだが、個別シートの有無はファイルを実際に開かないと確定できない。ただし、既存の全スクリプト（`convert_bond_history.py`、`menu_bond_update.py`、`menu_signal_check.py`）がいずれも `LIST` シートのみを参照している事実から、個別シートは存在しないか使われていないと推定する。
- EFA / VNQ の yfinance 取得における rate limit の具体的な閾値。日次1回の取得であれば問題ないと推定するが、確実な情報は yfinance のドキュメントを要確認。
- Cloud Run 環境から Dropbox API へのアクセスにネットワーク制限（egress policy）がないか。既存の `edinet-delay` Job が Dropbox API を使用しているため問題ないと推定。
