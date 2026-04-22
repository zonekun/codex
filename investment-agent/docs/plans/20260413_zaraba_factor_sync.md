# ザラ場ツール因子同期計画

**作成日**: 2026-04-13
**目的**: `earnings_model_predict.ipynb`（059 EDA）で新設された因子をザラ場ツール（`zaraba_earnings.py`）に取り込む

## 因子差分マップ

| EDA因子 | zaraba現状 | 差分 |
|---------|-----------|------|
| F1 進捗率 ±1 | F1 ✅ 同一 | — |
| F2 ガイダンス修正 ±1 | F2 ✅ 同一 | — |
| F3 YoY OP ±1（廃止候補） | F3 ✅ 同一 | EDA側で正式廃止後に除去（Step 4） |
| **F4 コンセンサス乖離 ±1~±3** | **❌ 未実装** | consensus は prepare 取得済みだがスコアリング未使用（Step 2-a） |
| F5 翌期見通し ±2 | F4 ✅ 同一 | — |
| F6 出尽くしリスク -2 | F5 ✅ 同一 | — |
| **F7 成長加速/減速 ±1** | **❌ 未実装** | BQ 過去FY YoY OP median が必要（Step 2-b） |
| **F8 記念配当/特別配当 +1** | **❌ 未実装** | TDnet TITLE 一致判定（Step 1-b） |
| **F9 テーマブースト +1** | **❌ 未実装** | GCS beta_20d.csv + 当日TOPIX（Step 3-a） |
| **F10 自社株買い +2** | **❌ 未実装** | TDnet TITLE 判定（Step 1-a） |
| **F11 増配/減配 +1/-2~-3** | F6 ⚠️ 閾値違い | 非対称ウェイトに更新（Step 1-c） |
| **F12 PER割安度(PEG) ±1~±2** | **❌ 未実装** | 株価÷ForEPS÷成長率。FYのみ（Step 2-c） |
| **F13 QoQ OP急変 +1/-2** | **❌ 未実装** | 前Q比OP。prior_data に latest_q_op あり（Step 1-d） |
| — | F7 折込度合い -1 | zaraba固有（維持） |
| — | F8 信用売り残 +0.5 | zaraba固有（維持） |

## Step 1: データ準備不要・即実装可能 ✅ DONE (2026-04-13)

ポーリング中のデータまたは既存 prior_data で完結する因子。

### 1-a: F10 自社株買い +2 ✅

- watch ループで `related_titles_by_code` 辞書に全開示タイトルを蓄積
- `_xbrl_to_jquants_rec()` に `related_titles` 引数追加、`rec["_related_titles"]` に格納
- `_score_record()` で "自己株式の取得" を含む場合 +2

### 1-b: F8b 記念配当/特別配当 +1 ✅

- `_title` + `_related_titles` の全タイトルから "記念配当" or "特別配当" を検索
- 1-a と同じ `related_titles` 蓄積の仕組みを共用

### 1-c: F6 増配/減配の非対称化 ✅

- 変更前: ±1（対称）
- 変更後: 増配 >+5% → +1 / 減配 <-5% → -2 / 大幅減配 <-20% → -3

### 1-d: F13 QoQ OP急変 +1/-2 ✅

- `prior["latest_q_op"]`（前Q単独OP）と `standalone_op` の比較
- >+50% → +1 / <-50% → -2

## Step 2: prepare 時のキャッシュ追加が必要 ✅ DONE (2026-04-13)

### 2-a: F4 コンセンサス乖離 ±1~±3 ✅

- **データ**: `consensus_YYYYMMDD.csv` は prepare で全件取得済み
- **追加実装**: 
  1. prepare 時に TICKER 別に TARGET='CURRENT' の OP コンセンサスを prior_data に格納
  2. `_score_record()` で `cumulative_op` or `standalone_op` vs コンセンサス OP の乖離率を段階判定
- **段階**: >+10% → +3 / >+5% → +2 / >+0% → +1 / <0% → -1 / <-5% → -2 / <-10% → -3
- **注意**: コンセンサスは累計 vs 単独の単位合わせが必要。EDA側の実装を参考

### 2-b: F7 成長加速/減速 ±1（FYのみ） ✅

- **データ**: BQ `STOCK.fin_summary` から過去 FY の YoY OP を複数期取得 → median 計算
- **追加実装**: prepare に BQ クエリ追加 → prior_data に `baseline_yoy_op` として格納
- **判定**: 翌期 OP YoY - baseline_yoy_op = gap。gap >+20pt → +1 / gap <-20pt → -1

### 2-c: F12 PER割安度(PEG) ±1~±2（FYのみ） ✅

- **データ**: 株価（取得済み）、ForEPS（fin_summary から計算可能）、翌期 OP 成長率
- **追加実装**: prepare に ForEPS 計算追加 → prior_data に `forward_per`, `peg_ratio` 格納
- **判定**: PEG <0.5 → +2 / PEG <1.0 → +1 / PEG >2.0 → -1

## Step 3: 外部データ連携が必要 ✅ DONE (2026-04-13)

### 3-a: F9 テーマブースト +1 ✅

- **データ**: GCS `gs://stock_data_1930932/earnings_model/beta_20d.csv` + BQ INDEX_PRICE (TOPIX当日)
- **追加実装**: 
  1. prepare で GCS から beta CSV ダウンロード → prior_data に `beta_20d` 格納
  2. watch 開始時に当日 TOPIX リターンを取得（BQ or J-Quants）→ メモリ保持
  3. ポーリング時にβ >1.0 かつ TOPIX 当日+ なら +1
- **課題**: ザラ場中の TOPIX リアルタイム値の取得方法（J-Quants は遅延あり）

## Step 4: F3 YoY OP 廃止（EDA側で正式決定後）

EDA 側で F13 が F3 の代替として機能することを確認後、zaraba からも F3 を除去。

## zaraba 固有因子（維持）

| zaraba因子 | 説明 | EDA側 |
|-----------|------|-------|
| F7 折込度合い -1 | 20日モメンタム>+10%(事前修正なし) or 出来高5日/20日>2倍 | EDAにはない。ザラ場特有の警戒因子として維持 |
| F8 信用売り残 +0.5 | 貸借倍率<1（売り長） | EDAにはない。ザラ場特有の需給因子として維持 |
