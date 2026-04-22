# 大型→中小型 リードラグ戦略（大型定義の多重検証）

**カテゴリ**: analysis
**作成日**: 2026-04-11
**ステータス**: **ANALYZED_FAIL**（2026-04-12 Pass 1 検証で税後 Sharpe 最良 0.47、基準 1.0 未達）
**アイデアソース**: 手動（論文 SIG-FIN-036-13 応用案 + Hou (2007) リードラグ原典）
**親アイデア**: [`011_subspace_pca_leadlag_applications.md`](011_subspace_pca_leadlag_applications.md)（部分空間正則化PCAリードラグ 応用アイデア集、D-1 の元記載）
**兄弟**:
- [`011-1_cluster_overnight_daytime_leadlag.md`](011-1_cluster_overnight_daytime_leadlag.md)（overnight→daytime × クラスタ、**ANALYZED_FAIL**）
- [`011-3_cluster_cross_leadlag.md`](011-3_cluster_cross_leadlag.md)（クラスタ間クロスリードラグ）

**関連ファイル**:
- `docs/references/japan_us_sector_leadlag_pca/`（原論文）

---

## 仮説

Hou (2007) "Industry information diffusion and the lead-lag effect in stock returns" で示された原典リードラグ効果（**大型株の価格変化が中小型株に遅延伝播**）の日本版。

因果連鎖:
```
マクロ/セクター情報 → 流動性が高く機関投資家が即座に織り込む大型株で反応
  → 翌営業日（または同日日中後半）に中小型株がキャッチアップ
    → 中小型株の翌日〜数日リターンが大型株の本日リターンで予測可能
```

論文手法（部分空間正則化付きPCA）と組み合わせることで、少数の共通ファクターに縮約したクリーンなシグナルが得られる期待。

---

## 核心: 「大」「小」の定義は 1 通りではない ★重要

リードラグの強さは「大型」と「小型」の切り方次第で変わる。**単一定義で試して失敗したら終わりではなく、複数の定義を横断的に検証すべき**。

### 検証すべき「大」の定義候補

| # | 定義 | 銘柄数 | データソース | 特徴 |
|---|------|--------|-------------|------|
| L1 | **TOPIX Core30** | 30 | `STOCK_CODE_LIST.SIZE_CATEGORY='TOPIX Core30'` | 最も流動性が高い超大型株のみ。機関投資家の即時織り込み度最大 |
| L2 | **TOPIX Core30 + Large70 (=TOPIX100)** | 100 | 同 `SIZE_CATEGORY IN ('TOPIX Core30','Large70')` | 大型株の主力。情報密度とサンプル数のバランスが良い |
| L3 | **Nikkei225** | 225 | 指数 `INDEX_PRICE` + 構成銘柄マスタ（別途取得必要） | 時価総額+業種代表性で選出。流動性重視の市場のベンチマーク |
| L4 | **時価総額上位50** | 50 | `YF_STOCK_INFO.MARKET_CAP` 動的算出 | 日本株の「最も大きい50」を時価総額で動的決定 |
| L5 | **時価総額上位200** | 200 | 同上 | Large + Mid 境界付近までカバー |
| L6 | **出来高/売買代金 上位 N** | 可変 | `STOCK_PRICE_JQUANTS.TURNOVER` の移動平均 | 流動性ベース定義。機関投資家の織り込み速度と直結 |

### 検証すべき「小」の定義候補

| # | 定義 | 銘柄数 | データソース | 特徴 |
|---|------|--------|-------------|------|
| S1 | **TOPIX Mid400** | 400 | `SIZE_CATEGORY='TOPIX Mid400'` | 中型株。Hou の原典はこの層が主ターゲット |
| S2 | **TOPIX Small** | ~1500+ | `SIZE_CATEGORY='TOPIX Small'` | 小型株。執行難だが効果が大きい可能性 |
| S3 | **グロース市場全体** | ~500 | `MARKET_SEGMENT` 別 | 個人投資家比率が高く遅延しやすい |
| S4 | **時価総額下位（プライム内）** | 可変 | プライムかつ時価総額下位 | 板薄さを緩和しつつリードラグを捕捉 |
| S5 | **出来高比率が低い銘柄** | 可変 | 出来高/時価総額比の下位 | 流動性不足銘柄 = 遅延反応が強いはず |

### 検証の組合せ数

「大」6候補 × 「小」5候補 = **30組合せ**。多重比較の観点から**BH補正必須**。
ただし実用上は「大」は L1/L2/L3/L4/L6 の5本、「小」は S1/S2/S4 の3本に絞って **15組合せ** で十分。

---

## 方法論: 部分空間正則化付きPCA（論文手法の転用）

### 結合構造

```
A（先に織り込む市場）= 大型株 N_L 銘柄の当日リターン
B（遅れて反応する市場）= 中小型株 N_S 銘柄の翌営業日リターン
```

取引時間帯は同じなので非同期性は「取引時刻」ではなく「情報拡散の遅延」に由来する。論文の米日リードラグとはメカニズムが異なるが、数学構造は同じ（低ランク共通因子の射影→復元）。

### V₀ 設計（サイズベース事前部分空間）

論文はグローバル/国スプレッド/シクリカル-ディフェンシブの3本。ここでは:

- **v₁**: 全銘柄等ウェイト（市場全体ファクター）
- **v₂**: 大型プラス / 小型マイナス（サイズスプレッド）
- **v₃**: シクリカル / ディフェンシブ（既存業種33を景気敏感度で符号化）
- **v₄**: 業種33 の上位固有ベクトル（オプション）

K₀ = 3〜4 本で λ=0.9 縮約。結合相関行列は (N_L + N_S) × (N_L + N_S)。

### 予測器

```
B_t^(K) = V_S,t^(K) V_L,t^(K)ᵀ ∈ ℝ^(N_S × N_L)
ẑ_S,t+1 = B_t^(K) z_L,t
```

大型株のショックを K 次元部分空間へ射影し、小型株側ローディングで翌日シグナルを復元。

---

## 既存資産の流用度

| 必要なもの | 既存資産 | 流用度 |
|---|---|---|
| 大型/中小型の銘柄リスト | `STOCK_CODE_LIST.SIZE_CATEGORY` | **そのまま**（L1/L2/S1/S2） |
| 日次リターン | `STOCK_PRICE_JQUANTS.ADJ_CLOSE` | **そのまま** |
| 時価総額 | `YF_STOCK_INFO.MARKET_CAP` | **そのまま**（L4/L5/S4 用） |
| 出来高・売買代金 | `STOCK_PRICE_JQUANTS.VOLUME / TURNOVER` | **そのまま**（L7/S5 用） |
| Nikkei225 構成銘柄 | `data/master/nikkei225_constituents.csv` | **そのまま**（本ファイル作成時に同時取得） |
| 業種33 | `STOCK_CODE_LIST.INDUSTRY_CODE33` | **そのまま** |
| 残差相関基盤 | `scripts/factor_model/factor_model_residual_corr.ipynb` | **そのまま**（012 PoC と共通） |
| バックテスト | `scripts/backtest_datr_long.py` パターン | 改修で対応 |

**新規必要**: なし（Nikkei225 構成銘柄は `data/master/nikkei225_constituents.csv` 取得済み）

---

## 実装の優先順位

### Phase 1: 最小PoC（1-2日）

定義の組合せを**最小限に絞って**まず実装:

1. **L2（TOPIX100）→ S1（Mid400）**: データが揃っており、銘柄数も手頃
2. ベースライン: (a) 単純ラグ付きモメンタム、(b) λ=0 の PCA、(c) 業種別リードラグ

評価: 年率リターン、Sharpe、MDD、Carhart4 α（Newey-West）

### Phase 2: 大型定義の多重検証（Phase1 で有意な結果が出た場合のみ）

L1/L2/L4/L5/L7 × S1/S2/S4 の **15組合せ**を回して、**どの切り方が最もリードラグを強く捕捉するか**を定量化。

- BH補正で FDR < 0.05 を満たす組合せを残す
- 各組合せの Sharpe を並べ、**定義の頑健性**を確認
  - 全組合せで Sharpe が一貫して高ければリアルなリードラグ
  - 1つだけ高くて他はダメなら過学習の疑い

### Phase 3: Nikkei225 構成225銘柄での検証

L3（Nikkei225）は構成銘柄リスト（`data/master/nikkei225_constituents.csv`）を使って他定義との頑健性比較に使う。

---

## 懸念点

1. **執行コスト（最重要）**: 中小型株の板薄さで実執行は困難。ADTV（日次平均売買代金）フィルタ必須。例えば「直近60日ADTV ≥ 5億円」の銘柄のみ
2. **サバイバーシップバイアス**: 期間内に上場廃止された銘柄の扱い。`STOCK_CODE_LIST` の上場日・廃止日を使って正しく除外する
3. **TOPIX構成銘柄の変更**: SIZE_CATEGORY は現時点のスナップショット。過去時点では違う可能性 → バックテストでは in-sample バイアスが入る可能性。対処: 年次スナップショットを保存して使う
4. **業績発表集中期間の影響**: 5月・11月等の決算集中期に「大型株の決算 → 中小型株への連想買い」が効きやすい。季節性フィルタも試す価値あり
5. **多重比較**: 15組合せ × 多パラメータ（ウィンドウ長・ラグ・分位点）で検定数が膨張。必ず BH補正

---

## 必要データ（全て既存BQにあり）

```sql
-- Phase1 用: TOPIX100 → TOPIX Mid400 の日次リターン
WITH universe AS (
  SELECT
    TICKER,
    SIZE_CATEGORY,
    CASE
      WHEN SIZE_CATEGORY IN ('TOPIX Core30','Large70') THEN 'LARGE'
      WHEN SIZE_CATEGORY = 'TOPIX Mid400' THEN 'SMALL'
    END AS SIZE_GROUP
  FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
  WHERE EXCHANGE = 'TSE'
    AND SIZE_CATEGORY IN ('TOPIX Core30','Large70','TOPIX Mid400')
)
SELECT
  p.DATE,
  p.TICKER,
  u.SIZE_GROUP,
  LN(p.ADJ_CLOSE / LAG(p.ADJ_CLOSE) OVER (PARTITION BY p.TICKER ORDER BY p.DATE)) AS log_ret
FROM `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` p
INNER JOIN universe u ON p.TICKER = u.TICKER
WHERE p.IS_PREFERRED = FALSE
  AND p.DATE BETWEEN '2016-04-01' AND '2026-04-01'
```

※ 実際のカラム名は data_catalog.md で要確認。

---

## 012 との関係

- **012 (overnight→daytime × クラスタ)**: 非同期性 = 取引時間外、V₀ = 残差相関クラスタ20本
- **013 (大型→小型 × サイズ定義)**: 非同期性 = 情報拡散遅延、V₀ = サイズベース3-4本

両者は **V₀ の設計思想が異なる**。012 はデータドリブン（残差相関で発見したクラスタ）、013 は経済直観（サイズ順）。どちらが強いかは試してみないとわからない。

**さらに組合せも可能**: 「大→小 × 残差相関クラスタ」というハイブリッドも 013 Phase 4 として考えられる。大型株のショックをクラスタ経由で中小型株に伝播させる構造。

---

## 次のアクション

1. 012 の overnight/daytime PoC が完了したら、そのコード資産を **013 Phase1（L2→S1）** に流用する
2. Phase1 で 015 Carhart4 α が有意に出れば Phase2 の多重検証に進む
3. 多重比較補正を最初から設計に組み込む（後付けするとデータスヌーピングになる）

---

## ステータス

NEW（未実装）。012 PoC の完了待ち。012 のコード資産（部分空間正則化PCA実装、バックテスト基盤）をそのまま流用できるため、013 Phase1 の追加工数は **0.5-1日** 見込み。
