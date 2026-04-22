# ファクターモデル残差相関行列による銘柄グループ構造分析

**カテゴリ**: analysis
**作成日**: 2026-04-03
**ステータス**: ANALYZED_PASS（構造分析ツールとして継続利用）
**アイデアソース**: 手動入力（ファクターモデル分解による銘柄間の隠れた連動構造の発見）
**関連ファイル**:
- `scripts/factor_model/factor_model_residual_corr.ipynb`（親ツール・Colab / ローカル両対応）
- `scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py`（子ツール①・同業種×低残差相関ペア抽出）
- `scripts/factor_model/factor_model_residual_corr_unique_movers.py`（子ツール②・個別株の因子除去後ユニーク動意抽出）
- Colab URL: `https://colab.research.google.com/drive/1-czT5Q2Hy3o5Niidnni0Ebzm3kWWKjoR`

## 概要 — 何ができるか

**東証33業種分類では見えない、本当の銘柄グループ構造を炙り出す**ためのノートブック。

### やっていること

1. 3ファクター（市場・業種33・サイズ）でリターンを回帰 → 残差（説明できない部分）を抽出
2. 残差同士の相関行列を年次6枚（2020〜2025）で計算
3. Ward法クラスタリング + PCA で銘柄をグループ化・可視化
4. 四季報テキスト類似度とクロス検証（オプション）

### 何がわかるか

- **同じ業種なのに連動しないペア** → 市場が差別化している
- **違う業種なのに連動するペア** → サプライチェーン・株主構造・隠れた事業関連
- **PCAの隠れファクター** → 業種分類では見えないテーマ（EV・半導体等）

### 使い道

| 用途 | 使う出力 |
|------|---------|
| ペアトレード候補探し | 同クラスタ・高残差相関ペア |
| ポートフォリオのリスク推定 | 残差相関行列 |
| 決算後の波及先予測 | 同クラスタ銘柄 |
| テーマシフト検出 | PCAローディングの年次変化 |

---

## 仮説

業種・サイズ・市場全体の影響を除いた残差に、サプライチェーン・株主構造・事業類似性等の隠れた銘柄間連動が残る → 残差相関行列をクラスタリング・PCAで可視化すれば、業種分類では見えないグループ構造を発見できる

---

## 分析フェーズ1: 3ファクターモデル回帰 → 残差相関（ANALYZED_PASS）

### 使用データ ★必須

| 項目 | 内容 |
|------|------|
| データソース | BQ `STOCK.STOCK_PRICE_JQUANTS`（調整済み株価 ADJ_CLOSE） |
|  | BQ `STOCK.STOCK_CODE_LIST`（業種33・サイズ分類） |
|  | BQ `STOCK.fin_summary`（発行済株式数 → 時価総額算出） |
|  | BQ `STOCK.INDEX_PRICE`（TOPIX INDEX_CODE='0000'） |
|  | 四季報Excel（連結事業・特色テキスト、オプション） |
| 対象銘柄 | 時価総額500億円以上、欠損率5%以下、ETF/REIT除外（各年約400〜500銘柄） |
| 粒度 | 日次 |
| 主要項目 | 対数リターン `log(P_t / P_{t-1})` |
| 期間 | 2020〜2025 の年次ローリング窓（6枚） |
| 前処理 | 対数リターン変換、重複(DATE,TICKER)は`keep='last'`で除去、欠損は0埋め |

### 使用アルゴリズム ★必須

| アルゴリズム | 選択理由 |
|------------|---------|
| OLS 3ファクター回帰 | MKT(TOPIX)+IND33(業種33平均)+SIZE(サイズ区分平均)で系統的リスクを除去。Fama-French的アプローチだが日本市場向けに業種33を使用 |
| バッチ回帰（同一デザイン行列グループ） | 同じ(industry, size)の銘柄はXが同一 → `np.linalg.lstsq` でバッチ処理して高速化 |
| Ward法階層的クラスタリング | 残差相関距離 `d=1-ρ` でクラスタリング。Ward法はクラスタ内分散を最小化し、バランスの良いクラスタを生成 |
| PCA（主成分分析） | 残差に潜むファクター構造を低次元化。寄与率でファクター数、ローディングで所属銘柄を特定 |
| TF-IDF（文字n-gram） + コサイン類似度 | 四季報テキストの事業類似度。形態素解析不要な文字n-gram(2-4)で日本語に対応 |

### パラメータ

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| `MARKET_CAP_MIN` | 500億円 | 時価総額下限 |
| `MISSING_RATE_MAX` | 5% | 年内の欠損率上限 |
| `N_CLUSTERS` | 20 | クラスタ数 |
| `N_PCA_COMPONENTS` | 20 | PCA抽出主成分数 |
| `YEAR_WINDOWS` | [2020,2021,2022,2023,2024,2025] | 年次ウィンドウ |

### 結果

構造分析ツールのため定量的なパス/フェイル判定ではなく、出力品質で評価。

### 判定と理由

**判定**: ANALYZED_PASS

残差相関行列が年次で安定した構造を示し、業種分類とは異なるクラスタが出現。PCA寄与率もファクター構造の存在を示唆。四季報テキストとのクロス検証で「残差相関高×テキスト類似低」のペアが隠れた関係の候補として抽出可能。

---

## 年次ウィンドウの仕様

- 各年窓は **暦年フル** (`YYYY-01-01 〜 YYYY-12-31`) を対象期間とする
- `build_universe(year)` 内で `start=Timestamp(f'{year}-01-01')`, `end=Timestamp(f'{year}-12-31')` 固定
- 実行時点の翌年以降に当年を指定しないと中途半端な窓になる。**当年を含めたい場合は年度越え後の再実行必須**
- 各窓は独立に処理。`YEAR_WINDOWS` リストで対象年を制御

## 出力ファイル

保存先:
- **Colab 実行時**: `G:\マイドライブ\analysis\factor_model\`（Google Drive）
- **ローカル実行時**: `C:\tmp\factor_model_output\`

Colab 実行結果は Google Drive に保存されるため、ローカルから参照する場合も上記 Google Drive パスを直接読み込めば OK。

| ファイル | 内容 | 用途 |
|---------|------|------|
| `residual_corr_YYYY.csv` | N×N 残差相関行列 | 銘柄間の固有連動度 |
| `clusters_YYYY.csv` | TICKER / CLUSTER / STOCK_NAME / INDUSTRY_33 | グループ分類ラベル |
| `pca_loadings_YYYY.csv` | 銘柄×PC ローディング | 隠れグループの所属度 |
| `factor_betas_all_years.csv` | 銘柄×年×β | ファクター感応度の時系列変化 |
| `residual_corr_YYYY.png` | デンドログラム＋ヒートマップ | 可視化 |
| `pca_variance_YYYY.png` | PCA寄与率グラフ | 可視化 |
| `text_vs_residcorr_YYYY.png` | テキスト類似度×残差相関散布図 | クロス検証可視化 |

---

## 実行方法

### Colab（推奨）

1. URL `https://colab.research.google.com/drive/1-czT5Q2Hy3o5Niidnni0Ebzm3kWWKjoR` を開く
2. `Ctrl+F9`（全セル実行）
3. 初回はBQ認証ダイアログ → 承認。2回目以降はキャッシュ（`FORCE_RELOAD=False`）
4. 四季報Excelは Google Drive にアップロード済みなら自動読み込み（なければスキップ）

### ローカル Windows

```bash
jupyter lab scripts/factor_model/factor_model_residual_corr.ipynb
```

キャッシュ: `C:\tmp\factor_model_cache\`（BQクエリ結果CSV）

### pyautogui 自動実行

`C:\tmp\colab_runner_auto.py` でEdge経由の無人実行が可能。「ページの復元」ダイアログが出る場合は `--no-restore-session-state` フラグで抑制。

---

## 推奨実行頻度

構造分析ツールのため高頻度不要。

| 頻度 | タイミング | 目的 |
|------|-----------|------|
| 年次（必須） | 1月初旬 | 前年窓を正式に追加・更新 |
| 四半期 | 3/6/9/12月末 | クラスタ・PCA寄与率のドリフト監視 |
| 随時 | 大型再編・テーマ急変時 | 半導体/AI等テーマ変化、大型M&A・TOB後 |

**注意**: 年次窓は暦年が完結していることが前提。**当年（例: 実行時点 2026-04 での 2026 年窓）は途中までの部分データになるため、参考値扱い**。暦年確定前に本運用で参照しない。

---

## 子ツール①: 同業種×低残差相関ペア抽出

**スクリプト**: `scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py`

親ツール出力（`residual_corr_YYYY.csv` / `clusters_YYYY.csv`）を読み、同じ `INDUSTRY_33` に属するペアのうち残差相関が低いもの（市場が差別化している候補）をCSVに抽出する。

### 使い方

```bash
# 推奨: 完結暦年 2024/2025 両年で stable に低連動なペア
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py \
    --years 2024 2025 --threshold 0.2 --stable

# 最新年単独
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py \
    --years 2025 --threshold 0.2

# 入力ディレクトリを明示（既定: G:\マイドライブ\analysis\factor_model）
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py \
    --years 2024 2025 --stable --input-dir "C:\tmp\factor_model_output"
```

### 入出力

- **入力**: `G:\マイドライブ\analysis\factor_model\{residual_corr_YYYY.csv, clusters_YYYY.csv}`（親ツール出力）
- **出力**: `C:\tmp\factor_model_output\same_sector_low_corr_<years>[_stable].csv`
- 出力列: `industry_33 / a / name_a / b / name_b / r_YYYY... / r_mean`（`r_mean` 昇順）

### オプション解釈

| オプション | 意味 |
|----------|------|
| `--years 2024 2025` | 使用する年窓（昇順推奨） |
| `--threshold 0.2` | 残差相関の閾値（未満を採用） |
| `--stable` | 全指定年で閾値未満のペアのみ（構造的非連動の堅い候補） |
| `--input-dir <path>` | 親ツール出力の読み込み元 |

### 解釈のコツ

| r_2024 | r_2025 | 解釈 |
|:-:|:-:|------|
| 低 | 低 | 構造的に非連動（本命候補） |
| 高 | 低 | 最近差別化が進んだ（テーマ・決算要因、個別確認） |
| 低 | 高 | 連動再開（候補から外す） |
| 高 | 高 | 通常の同業種連動（対象外） |

---

## 子ツール②: 個別株の因子除去後ユニーク動意抽出

**スクリプト**: `scripts/factor_model/factor_model_residual_corr_unique_movers.py`

指定期間 (`--start-date` 以降) の各銘柄リターンから **MKT/IND33/SIZE の3因子効果を除去した残差** を計算し、同クラスタの中央値残差との差 (deviation) をランキングして「クラスタから逸脱した独特な動き」をしている個別株を抽出する.

### なぜファクター除去が必要か

生リターンで「stock return − cluster median」を見ると、株のβとクラスタのβが異なるときに市場全体の動きが残差として残ってしまう。真に「このクラスタから逸脱した動き」を見るには、期待リターン分（`α + β_mkt·MKT + β_ind·IND + β_size·SIZE`）を引いてからクラスタ中央値と比較する必要がある。親ツールのクラスタ構築自体が「3因子残差相関ベース」なので、整合的に同じ因子を使って除去する。

### 使い方

```bash
# 2026-04-01 以降の動意を 2025 クラスタ構造と β を使って抽出 → Dropbox上書き
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_unique_movers.py \
    --start-date 2026-04-01 --benchmark-year 2025 --top-n 30 --dropbox

# β 許容度を緩める (小型・新興を含める)
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_unique_movers.py \
    --start-date 2026-04-01 --beta-cap 10

# 厳選したい (OLS が安定な銘柄のみ)
PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_unique_movers.py \
    --start-date 2026-04-01 --beta-cap 3
```

### 入力

- `clusters_{benchmark_year}.csv`（親ツール・クラスタ情報）
- `factor_betas_all_years.csv`（親ツール・年×銘柄β）
- BQ `STOCK.STOCK_PRICE_JQUANTS`（daily 株価）
- BQ `STOCK.INDEX_PRICE`（TOPIX INDEX_CODE='0000'）
- BQ `STOCK.STOCK_CODE_LIST`（INDUSTRY_33_CODE / SIZE_CODE）

### 出力

- **ローカル**: `C:\tmp\factor_model_output\unique_movers_residual_<START>_to_<END>_vs_<YEAR>.csv`
- **Dropbox** (`--dropbox` 指定時): `/stock/temp/unique_movers_residual_<START>_to_<END>_vs_<YEAR>.csv`
- 出力列: `TICKER / STOCK_NAME / INDUSTRY_33 / CLUSTER / period_return / period_expected / period_residual / cluster_median_residual / deviation_residual / r_2025 / r_2025_cluster / raw_resid_2025 / commentary`

### 計算アルゴリズム

1. 分析期間の daily log return を各銘柄で取得
2. 3因子日次系列を構築（MKT=TOPIX、IND33=業種33別等金額平均、SIZE=サイズ区分別等金額平均。親ツールと同一定義）
3. 各銘柄 × 各日: `eps_t = r_t − (α + β_mkt·MKT_t + β_ind33·IND_t + β_size·SIZE_t)`
4. 期間合計 `period_residual = Σ eps_t`（log return は加算可）
5. CLUSTER ごとに `cluster_median_residual = median(period_residual)`
6. `deviation_residual = period_residual − cluster_median_residual`
7. `|deviation_residual|` 降順で top_n を抽出

### 落とし穴: β 不安定銘柄の除外必須

親ツールの OLS は多重共線性に弱く、業種×サイズ内の銘柄数が少ない銘柄では `|β_mkt|≈30, |β_ind|≈50` のような暴走値が発生する（例: インフロニア(5076) / 伊藤園(2593)）。このまま 2026-04 の因子に当てると `period_expected = -124%` 等の非現実的な値になり、残差がその分だけ真逆に跳ね上がる. `--beta-cap`（既定 5.0）で `|β_mkt|, |β_ind33|, |β_size|` 全てが上限以下の銘柄のみに絞る。起動ログ `betas_filtered dropped=N` で除外件数を確認すること。

### オプション解釈

| オプション | 既定 | 意味 |
|----------|------|------|
| `--start-date` | 2026-04-01 | 分析開始日（この日以降の日次リターンを使用） |
| `--benchmark-year` | 2025 | β とクラスタ割当の元になる年 |
| `--top-n` | 30 | 抽出するランキング件数 |
| `--beta-cap` | 5.0 | \|β\| 上限。超える銘柄は除外 |
| `--input-dir` | `G:\マイドライブ\analysis\factor_model` | 親ツール出力の読み込み元 |
| `--dropbox` | false | `/stock/temp/` に上書きアップロード |

### 寸評ロジック

各銘柄について以下3パターンで自動分類:

| 2025 raw 逸脱 | 2026-04 因子除去後 deviation | ラベル |
|:-:|:-:|------|
| 同方向 | 同方向 | 傾向継続・加速 |
| 逆方向 | — | 転換 |
| \|差\|<5pt | — | 最近になって逸脱 |

### 使い道

- **決算シーズン前後のユニーク動意スクリーニング**: `--start-date` を決算開示日以降に設定
- **週次/月次定点観測**: 週初・月初を `--start-date` に入れて直近のずれを抽出
- **テーマ銘柄検出**: 2025 クラスタと違う動きをしている銘柄は新テーマの芽の可能性

### この子ツールの対象外

- **β 不安定銘柄**（`--beta-cap` で除外された銘柄）はファクターモデル自体が当該銘柄に対して機能しないためスキップ対象. 個別に評価したい場合は親ノートブックの β 推定を ridge 等に置き換える必要あり（未実装）
- **新規上場銘柄**（`clusters_<year>.csv` 非掲載）は分析対象外

---

## 既知の問題・修正履歴

- **2026-04-03**: `pivot()` で `ValueError: Index contains duplicate entries` → `drop_duplicates(subset=['DATE','TICKER'], keep='last')` を追加して解消
- **2026-04-17**: 子ツール `factor_model_residual_corr_same_sector_pairs.py` を追加（同業種×低残差相関ペア抽出）
- **2026-04-17**: 子ツール `factor_model_residual_corr_unique_movers.py` を追加（個別株の因子除去後ユニーク動意抽出）. 初期実装は raw リターン比較だったが「市場全体の動きが残差に混ざって意味がない」との指摘を受けて3因子除去版に全面書き換え. 同時に β 暴走銘柄のフィルタ (`--beta-cap`) を追加

---

## 他モデルでの利用例

- **リスクモデル**: 残差相関行列でポートフォリオ分散を推定
- **ペアトレード候補**: 同クラスタ・高残差相関ペアをスクリーニング
- **セクターローテーション**: PCAローディングの時系列変化でテーマシフトを検出
- **イベントスタディ**: 決算発表後、同クラスタ銘柄への波及効果を定量化

---

## 次に試す方向性

1. 残差相関ベースのペアトレード戦略のバックテスト
2. PCAローディングの年次変化を追跡し、テーマ（EV・半導体等）のシフトを検出
3. 四季報「隠れた関係」ペアの実際のリターン連動を検証
