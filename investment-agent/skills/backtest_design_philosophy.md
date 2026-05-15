# backtest_design.md を backtest-expert ベースで全面再構築

**作成日時**: 2026-05-14 20:18 JST
**改定**: 2026-05-14 21:30 JST（code-reviewer #177 指摘反映）
**ステータス**: 完了
**対象ファイル**:
- `skills/backtest_design.md`（331行、commit `4dab85c` 時点）
- `docs/knowledges/tools/045_backtest_evaluation_metrics.md`（合格基準テーブル刷新）
**対象読者**: 次セッション担当
**目的**: 現行の自前バックテストスキルを、tradermonty/backtest-expert の体系的手法に置き換える。日本株個人投資家固有の制約は保持・統合する。045 の合格基準テーブルを evaluate_backtest.py の5次元スコアリングに刷新する。
**分類**: (b) 継続改修型（スキルは随時改善していく前提）

> **非スコープ**: evaluate_backtest.py のスクリプト本体の移植（スコアリング定義のみ045に取り込み、スクリプト化は将来TODO）

---

## 前提サマリ

- 現行スキルは自前蓄積（§0-§9、331行）。体系性が弱く、失敗から学んだ断片の集合体
- 014 FY弱気ガイダンスBTで 67回BQクエリを実行する非効率が発生 → データ取得原則を 045 に追記済み
- 同BTでパラメータ探索のオーバーフィット懸念、指標スクリーニング不足が表面化
- WEB調査で tradermonty/backtest-expert が最もプロジェクトに適合と判断
  - 哲学: "find strategies that break the least, not profit the most"
  - Seven Sins of Quantitative Investing（Deutsche Bank）ベース
  - Walk-forward / Monte Carlo / Regime analysis / Multiple testing correction を体系的にカバー
- code-reviewer #177 でレビュー済み（B判定）。重大指摘3件・改善提案4件を反映

---

## ソース（backtest-expert リポジトリ）

URL: `github.com/tradermonty/claude-trading-skills/tree/main/skills/backtest-expert`

| ファイル | 用途 | リファレンス保存 |
|---------|------|----------------|
| SKILL.md | スキル本体（置換ベース） | 保存済み |
| references/methodology.md | Seven Sins + 方法論詳細 | 保存済み |
| references/failed_tests.md | 失敗パターン集 + Case Study Framework | 保存済み |
| scripts/evaluate_backtest.py | 5次元スコアリング（045合格基準の刷新元） | **未取得** |

---

## ステップ

### Step 1: リファレンス保存・ToC登録 [P0]

**ソース取得: 3ファイル完了済み**。`docs/references/backtest-expert/` に保存済み:
- `SKILL.md` — スキル本体
- `methodology.md` — Seven Sins + 方法論詳細
- `failed_tests.md` — 失敗パターン集

**残作業**:
1. evaluate_backtest.py を WebFetch で取得し `docs/references/backtest-expert/` に保存
2. ソースREADME（`docs/references/backtest-expert/README.md`）を作成（ソースURL・ライセンス・取得日を明記）
3. リファレンスToC（`docs/references/README.md`）に登録:
   - フォルダ見出し行 `**backtest-expert/**` を追加
   - 通し番号 22〜25 でエントリ追加（SKILL.md / methodology.md / failed_tests.md / evaluate_backtest.py）
   - `Cited in` 列: `skills/backtest_design.md`, `045_backtest_evaluation_metrics.md`

**完了条件**: 4ファイル保存済み、ソースREADME作成済み、リファレンスToC登録済み

---

### Step 2: 保持すべき現行コンテンツの抽出 [P0]

現行 `skills/backtest_design.md` から、backtest-expert に**存在しない**かつ**本プロジェクト固有**の内容を特定・抽出する。

| 現行セクション | 判定 | 理由 | コードサンプル |
|--------------|------|------|-------------|
| §0 設計前チェック | △ 統合 | backtest-expert の pre-flight に統合 | なし |
| §1 仮説の対称性 | ★ 保持 | プロジェクト固有教訓（野菜BT事故） | なし |
| §2 エントリーラグ | △ 統合 | backtest-expert の look-ahead bias 節に統合 | CAR ラグ対応例 → 保持（短い） |
| §3 訓練・テスト分割 | △ 統合 | backtest-expert の walk-forward に吸収 | ARモデルOOS適用40行 → **不要**（011固有。原則のみ記載） |
| §4 コスト設定 | ★★ 保持 | 日本株個人投資家固有（最重要）。丸ごと保持 | コスト計算例 → 保持 |
| §4.5 旧コスト（機関） | 保持 | 参考として残す | なし |
| §5 合格基準 | 削除 | 045 を5次元スコアリングに刷新するため旧基準は不要 | なし |
| §6 結果の解釈 | △ 統合 | backtest-expert の判定フロー + FAIL記録テンプレートに統合 | なし |
| §7 命名規則 | ★ 保持 | プロジェクト固有規約 | なし |
| §8 シグナル寿命条件 | ★★ 保持 | 011系で導出。他に記載なし | 検証テーブル → 保持 |
| §9 枝番ルール | ★ 保持 | idea_pipeline.md への参照のみ | なし |

**完了条件**: ★★/★マーク項目 + △のうち保持コードサンプルを退避用テキストとして整理完了

---

### Step 3: 新スキル作成 [P0]

backtest-expert SKILL.md をベースに、以下の構成で新 `skills/backtest_design.md` を作成。

#### 3-1. backtest-expert 由来コンテンツ（ローカライズ）

- 通貨・市場: USD/US → JPY/日本株
- 取引時間: NYSE → TSE（9:00-15:30、昼休みなし ※2024年11月以降）
- データソース: Yahoo Finance → BQ / J-Quants / yfinance
- ベンチマーク: S&P 500 → TOPIX / 日経225
- レジーム分類: VIX → 日経VI（閾値は実施時に調査）、Fed policy → 日銀政策環境、S&P trend → TOPIX trend
- 時価総額区分: USD建て（>$200B等）→ JPY建て（>1兆円等）。現行§4のコスト設定と整合させる
- 英語の専門用語は日本語化 + 英語併記（例: 「先読みバイアス（Look-Ahead Bias）」）
- Seven Sins は methodology.md から要約を本文に組み込む

#### 3-2. プロジェクト固有コンテンツ（Step 2 で抽出分）

| 保持コンテンツ | 統合先（新スキル内） |
|--------------|-------------------|
| §4 コスト設定（全体） | 「コストモデル」セクションとして独立維持 |
| §1 仮説の対称性 | 「設計前チェック」に追加 |
| §8 シグナル寿命条件 | 「実行可能性フィルタ」に追加 |
| §2 CARラグ対応コード例 | 「ルックアヘッドバイアス」節に追加 |
| §3 訓練/テスト分割の原則 | 「Walk-Forward」節に記載（コードサンプルなし、原則のみ） |
| §6 FAIL分岐 | 「BACKTEST_FAIL記録テンプレート」に統合（failed_tests.md §3 Case Study Frameworkと合流） |
| §7 命名規則 | 付録 |
| §9 枝番ルール | 付録（参照のみ） |

#### 3-3. 045 への参照（正本は045に維持）

045 §6/§7 の内容はスキルにコピーしない。導線 + BT固有の補足のみ記載する。

| 045の内容 | スキル側の記載 |
|----------|-------------|
| §6 指標スクリーニング手順 | 「045 §6 のスクリーニング手順を実施」+ BT固有補足（スクリーニング期間はIS前半に限定等） |
| §7 BQクエリ最小化 | 「045 §7 のデータ取得原則に従う」+ BT固有補足（パラメータスイープ時の注意等） |

#### 3-4. 014 BT で学んだ教訓の追加

| 教訓 | 追加先 |
|------|-------|
| パラメータ探索とデータスヌーピング | Seven Sins の「オーバーフィッティング」節に統合 |
| MC≥1兆で単調増加の発見 | 教訓として「層別分析」セクションに追記 |

**完了条件**: 新スキルファイルが完成し、想定構成の全セクションが存在し、Step 2 の★★/★項目が全て統合されていることを確認済み

---

### Step 4: 045 合格基準テーブル刷新 [P0]

045 の現行合格基準テーブル（§バックテスト PASS 基準）を evaluate_backtest.py の5次元スコアリングに置き換える。

**現行（閾値 Yes/No）**:
```
| Sharpe Ratio | > 1.0 |
| Max Drawdown | < 20% |
| Profit Factor | > 1.5 |
| Win Rate | > 50% |
| Brier Score | < 0.25 |
| VaR(95%) 日次 | < 資産の 2% |
```

**新（5次元スコアリング + Red Flag）**:

| 次元 | 配点 | 評価内容 |
|------|------|---------|
| Sample Size | 20 | トレード数（30+で基礎点、100+で十分、200+で高信頼） |
| Expectancy | 20 | 勝率 × 平均勝ち/負け比で期待値 |
| Risk Management | 20 | MDD + Profit Factor |
| Robustness | 20 | テスト期間（5年+）+ パラメータ数（<5推奨） |
| Execution Realism | 20 | スリッページテスト実施有無 |

判定: ≥70 Deploy / 40-69 Refine / <40 Abandon

Red Flag 自動検出: 小サンプル、スリッページ未テスト、MDD>50%、パラメータ≥7個、テスト期間短い、負の期待値、結果が良すぎる

**作業内容**:
1. 045 の合格基準テーブルを上記スコアリングに書き換え
2. Red Flag リストを追記
3. Deploy/Refine/Abandon の判定基準を追記
4. 既存の数式定義（Brier/VaR/MDD/PF）はそのまま維持（スコアリングの構成要素として参照）
5. 新スキルの §6 からは「045 のスコアリング基準で判定」と参照

**完了条件**: 045 の合格基準セクションが5次元スコアリング + Red Flag に書き換え済み

---

### Step 5: 整合性チェック [P1]

1. 045 と新スキルの間に重複記載がないか確認（正本は045、スキルは参照+補足の原則）
2. CLAUDE.md §10 知見索引の参照が正しいか確認
3. `docs/knowledges/INDEX.md` の記載を更新（必要なら）
4. 他のスキル（`idea_pipeline.md`）からの参照が壊れていないか確認
5. `/backtest-design` でスキルが正しく起動するか確認（smoke test）

**完了条件**: 参照整合が取れ、スキル起動を確認済み

---

### Step 6: コミット [P1]

```
docs: backtest_design.md を backtest-expert ベースで全面再構築、045 合格基準を5次元スコアリングに刷新
```

---

## 想定される新スキルのセクション構成（案）

```
# バックテスト設計スキル

## 0. 哲学
  - "壊れにくい戦略を見つける" ≠ "利益最大化"
  - Seven Sins of Quantitative Investing（methodology.md から要約）

## 1. 設計前チェック
  - データ・シグナル・仮説の棚卸し（現行§0ベース）
  - 仮説の対称性確認（現行§1）
  - 指標スクリーニング → 045 §6 を実施（BT固有補足: IS前半限定）

## 2. データ取得原則
  - BQクエリ最小化 → 045 §7 に従う（BT固有補足: パラメータスイープ時の注意）
  - ルックアヘッドバイアス回避（CARラグ対応コード例含む）
  - サバイバーシップバイアス対策

## 3. バックテスト構造
  - Walk-Forward Analysis（backtest-expert由来）
  - 訓練/テスト分割の原則（コードサンプルなし）
  - Monte Carlo シミュレーション
  - Regime Analysis（日経VI / TOPIX trend / 日銀政策でローカライズ）

## 4. コストモデル（日本株個人投資家）
  - §4.1-4.5 現行をそのまま保持

## 5. 実行可能性フィルタ
  - シグナル寿命 > 執行ラグ（現行§8）
  - 個人投資家の執行制約（現行§4.3）

## 6. 合格基準・判定
  - 045 の5次元スコアリングで判定（Deploy/Refine/Abandon）
  - Red Flag チェック → 045 参照

## 7. 結果の解釈
  - PASS → デモトレード移行フロー
  - FAIL → 失敗パターン分類（failed_tests.md §2 由来）
  - BACKTEST_FAIL 記録テンプレート（failed_tests.md §3 + 現行§6 統合）

## 付録
  A. 命名規則（現行§7）
  B. 枝番ルール（idea_pipeline.md参照）
  C. 参考文献（backtest-expert, Deutsche Bank 等。ライセンス表記含む）
```

---

## リスク・注意点

| リスク | 対処 |
|-------|------|
| backtest-expert が米国株前提で日本株に合わない部分がある | Step 3-1 で明示的にローカライズ。不適合部分は削除 or 注記 |
| 現行の教訓が統合時に脱落する | Step 2 で★★/★項目 + コードサンプル保持判定を明示。Step 3完了後にチェック |
| ファイルが肥大化する（500行超え等） | 045を正本として参照方式を採用。スキル本体はワークフロー・チェックリスト・参照リンクに絞る |
| backtest-expert のライセンス | MIT License。出典を付録Cに明記 |
| 5次元スコアリングの閾値が日本株に合わない | Step 4 でローカライズ検討。必要なら閾値調整 |

---

## 関連ドキュメント

- 知見MD: `docs/knowledges/tools/045_backtest_evaluation_metrics.md`（数式辞書・スコアリング基準・データ取得原則・指標スクリーニング）
- 知見MD: `docs/knowledges/analysis/014_fy_conservative_guidance_screener.md`（BT実例・教訓元）
- 現行スキル: `skills/backtest_design.md`（commit `4dab85c`）
- ソース: tradermonty/claude-trading-skills backtest-expert（MIT License）
- リファレンス: `docs/references/backtest-expert/`（SKILL.md / methodology.md / failed_tests.md / evaluate_backtest.py）
- 関連スキル: `skills/idea_pipeline.md`（§枝番ルール参照元）
- レビュー: `docs/reviews/177_cr_backtest_skill_rebuild_plan.md`

---

## レビュー #177 採否

| # | 種別 | 指摘 | 採否 | 対応 |
|---|------|------|------|------|
| 重大#1 | 045移管方針 | 移管は二重管理を招く | **[採用]** | Step 3-3 を「参照+BT固有補足」に変更。045を正本維持 |
| 重大#2 | Case Study Framework脱落 | 失敗記録テンプレートの統合先がない | **[採用]** | 構成案§7にFAIL記録テンプレート追加 |
| 重大#3 | Regimeローカライズ未定義 | VIX→日経VI等が漏れ | **[採用]** | Step 3-1にレジーム・時価総額・金融政策のローカライズ追加 |
| 改善#1 | 合格基準統一 | 現行§5と045で数値が違う | **[解消]** | 045を5次元スコアリングに刷新するため閾値ベース判定自体がなくなる |
| 改善#2 | ARモデルコード | 40行の行き先未定 | **[採用]** | 不要と判断（011固有）。原則のみ記載 |
| 改善#3 | evaluate_backtest.py | 非スコープ明記必要 | **[方針変更]** | スクリプト移植は非スコープだが、スコアリング定義は045に取り込み |
| 改善#4 | README 2つの混乱 | 呼称で区別すべき | **[採用]** | Step 1で「ソースREADME」「リファレンスToC」と呼称区別 |
