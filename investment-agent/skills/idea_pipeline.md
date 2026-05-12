# 投資アイデア パイプライン（抽出 → 分析）

## このファイルの目的

**投資アイデアの発見・抽出・記録・分析・判定に関わる全ルールを一元管理するスキルファイル**。

| 管理対象 | 内容 |
|---------|------|
| アイデアの抽出テンプレート | ソースからアイデアを構造化する書式（Part 2） |
| データ取得フロー | 分析に必要なデータの調達手順（Part 3） |
| 統計分析の手法選択・前処理 | アルゴリズムカタログ、見せかけの相関防止（Part 4） |
| PASS / FAIL 判定基準 | 分析フェーズの合否条件（Part 5） |
| データ源別パターン | データ種別ごとの推奨分析手法（Part 5） |
| **枝番採番ルール** | 同一論文/仮説から派生するバリエーションの管理方法（Part 7） |

**バックテスト** の設計・コスト・合格基準は → `skills/backtest_design.md` に分離。

**対象フェーズ**: NEW → QUEUED → ANALYZING → ANALYZED_PASS / ANALYZED_FAIL
**次のフェーズ**: バックテスト → `skills/backtest_design.md`

---

## 全体フロー

```
[ソース取得]  X(Twitter) / YouTube字幕・ライブチャット / 論文 / ユーザー手入力
    ↓
[アイデア抽出]  因果連鎖・時間軸・対象・分析方向性を構造化
    ↓
[データ取得]  data_catalog.md → J-Quants MCP → 自動探索の順で調達
    ↓
[統計分析]  系列診断 → アルゴリズム選択 → 前処理 → 検定・CAR分析
    ↓
ANALYZED_PASS（有意）または ANALYZED_FAIL（無意）→ 知見を必ず記録
```

---

## Part 1: ソース取得

### X (Twitter) ブックマーク

**URL指定の場合** → `skills/twitter_reader.md` スキルを使用してツイートを読み取る。
```
このツイートから投資アイデアを抽出して: https://x.com/user/status/123456789
```
twitter_reader がURL・本文・添付画像のテキスト化を出力するので、その結果を Part 2 テンプレートに流す。

**テキスト貼り付けの場合** → そのまま抽出。
```
以下のポストから投資アイディアを抽出してください:
[ポストのテキスト]
```

### YouTube 字幕（アーカイブ動画）

```
YouTube動画 https://www.youtube.com/watch?v=XXXXX から字幕を取得してください
```

**実装方針:**
- `youtube-transcript-api` で自動字幕を取得
- タイムスタンプ付きで `data/lake/youtube/` に保存
- 日本語字幕を優先、なければ自動生成字幕を使用

**出力形式:**
```
(00:32:27) つまり、買方で持っていれば逆日歩をもらえるので...
```

### YouTube ライブチャット

```
YouTubeライブ https://www.youtube.com/watch?v=XXXXX からチャットを取得してアイデアを抽出してください
```

**実装方針:**
- YouTube Data API または yt-dlp でライブチャットリプレイを取得
- 配信者の字幕（発言） + 視聴者コメントの両方を取得
- リアルタイムの市場参加者感情が含まれる → 根拠箇所のタイムスタンプを記録

### 論文（ArXiv等）

論文URLまたはPDFのパスを渡す。GitHubコードが付属している場合はURLも添える。

---

## Part 2: アイデア抽出テンプレート

> **注意**: テンプレートは出発点。アイデアの性質に応じてフィールドを自由に追加・削除してよい。
> 使いながら継続的にブラッシュアップする。

---

```markdown
# IDEA: [アイディアタイトル]

## データソース
[ソース名・アカウント・投稿日時・URL等]

## 根拠となった箇所
> [ソースの該当引用 / タイムスタンプ]

[引用箇所の補足説明]

## 投資アイディア
[何をするアイディアか 1〜3文で]

## 因果連鎖
1. [要因A] → [中間変数B] → [業績・市場C] → [株価D]
2. （逆方向の因果も記載）

## 時間軸（仮説）
- 効果が現れると想定される経路: [例: 週次で即時反応、四半期決算で業績に反映、数ヶ月後のマクロ影響 等]
- ※ 実際のラグは ANALYZING フェーズ以降で確認する

## 対象（粗い絞り込み）
- 想定される対象: [銘柄・セクター・資産クラス等の大まかなイメージ]
- ※ 詳細な絞り込み条件は ANALYZING フェーズで設計する

## 分析の方向性（粗いイメージ）
- [どんな切り口で検証するか、大まかな方向性]
- ※ 具体的な手法・指標は ANALYZING フェーズで決定する

## 必要データ（想定）
- [データ1]: [取得先・形式（わかる範囲で）]
- [データ2]: [取得先・形式（わかる範囲で）]

## ステータス
NEW
```

---

### 抽出品質チェックリスト

- [ ] 因果連鎖が「A→B→C→株価」の形で記述されているか
- [ ] 仮説上の時間軸（効果経路）が記述されているか
- [ ] 対象の大まかなイメージが記述されているか
- [ ] 分析の方向性が記述されているか
- [ ] 必要データの目星がついているか

---

## Part 3: データ取得

### 調達フロー（5段階フォールバック）

```
① data_catalog.md を確認
   → 既存データで対応可能ならそれを使う

② BigQuery(a) / GCS(b) / ローカルCSV(c) / API+キャッシュ(d) を確認

③ J-Quants 系データが必要な場合 → J-Quants MCP サーバーで検索
   search_endpoints(keyword="...")    # 取得可能エンドポイントを探す
   describe_endpoint("...")           # パラメータ・レスポンス詳細
   generate_sample_code("...")        # 実行可能な Python コード生成
   ※ 詳細: docs/knowledges/tools/027_jquants_mcp_server.md

   マクロ経済データが必要な場合 → FRED MCP サーバーで検索
   search("GDP Japan")               # シリーズをキーワード検索
   get_series("UNRATE", startDate="2019-01-01")  # 観測値を取得
   ※ 詳細: docs/knowledges/tools/028_fred_mcp_server.md
   ※ 主な利用シーン: 金利・為替・景気指標・物価・雇用統計 など

④ それでも見つからない場合 → 自動探索
   - Web検索で公開データソースを探す
   - スクレイピング / ファイルDL
   - 取得したデータを data/csv/ または data/cache/ に保存
   - data_catalog.md にエントリを追加（ソースURL・取得日時・スキーマ）
   - 失敗した場合も「何を試みて何がダメだったか」を知見ファイルに記録
```

### 保存先の判断

| 条件 | 保存先 |
|------|--------|
| 繰り返し使う構造化データ | `data/csv/` |
| 一時的なAPI応答 | `data/cache/` |
| 定期取得が必要と判明 | 収集モジュール化を提案 → 将来的に BQ/GCS へ |

---

## Part 4: 統計分析

### Step 0: 系列の性質を把握する（最初に必ずやる）

アルゴリズムを選ぶ前に、**シグナル系列と目的変数の時系列的性質**を確認する。
ここを怠ると「見せかけの相関（spurious correlation）」を本物の関係と誤認する。

```python
from statsmodels.tsa.stattools import adfuller, acf

def diagnose(series, name):
    adf_p = adfuller(series, autolag='AIC')[1]
    acf1  = acf(series, nlags=2, fft=False)[1]
    print(f"{name}: ADF p={adf_p:.4f}, ACF(lag1)={acf1:.3f}")
```

| ACF(lag1) | ADF p値 | 診断 | 対処 |
|-----------|---------|------|------|
| ≈ 0 | < 0.05 | ホワイトノイズ（定常）| そのまま使える |
| 0.3〜0.7 | < 0.05 | 弱い持続性（定常）| 白色化を推奨 |
| **> 0.7** | **< 0.05** | **強い持続性（擬似非定常）** | **差分化 + 白色化必須** |
| 任意 | > 0.05 | 非定常（ランダムウォーク等）| 差分化 or 共和分検定 |

> **実例（野菜価格分析）**: 野菜平年比（水準）の ACF(lag1)=0.96。
> 技術的にはADF検定でI(0)と判定されたが、スピアマン相関を適用すると
> バローHDとの偽の正相関が出た。差分化して初めて正しい分析が可能になった。

---

### Step 1: アルゴリズム選択フロー

```
[シグナル X と目的変数 Y を決める]
    ↓
[Step A] 各系列の ACF(lag1) と ADF を確認
         ↓
    ┌─────────────────────────────────────┐
    │  シグナルX が強い持続性(ACF>0.7) ? │
    └─────────────────────────────────────┘
         ↓ YES                    ↓ NO
    差分化 Δx_t               そのまま使用
         ↓                         ↓
[Step B] 目的変数 Y（株価リターン）は定常か？
         ↓ 週次リターンはほぼ WN → 変換不要
         ↓ 株価水準を使う場合   → 差分化

[Step C] 両系列が非定常(I(1)) の場合
         → 共和分検定を先に実施
         → 共和分あり: 誤差修正モデル(VECM)
         → 共和分なし: 差分化してから Step D

[Step D] 定常系列同士の分析
         ┌─────────────────────────────────────────────────┐
         │ 目的                    → 手法                  │
         ├─────────────────────────────────────────────────┤
         │ どのラグで効くか探索    → 白色化CCF             │
         │ 予測力があるか検定      → グレンジャー因果性     │
         │ 急変イベントの影響測定  → CARイベント分析        │
         │ 過去の類似パターン検索  → DTW                   │
         └─────────────────────────────────────────────────┘

[Step E] 多重比較の補正
         テスト数 = 銘柄数 × ラグ数 × 指標数
         → Benjamini-Hochberg(BH) で FDR 補正
```

---

### Step 2: アルゴリズムカタログ

#### CCF（相互相関関数）

**答える問い**: 「シグナルXはどのラグ（何日/週/月後）で目的変数Yに現れるか？」

```
CCF(X_t, Y_{t+k}) = k週ラグでのXとYの相関係数
正ラグ(k>0): X → Y を先行（シグナルとして使える）
負ラグ(k<0): Y → X を先行（シグナルになれない）
```

- 95%信頼区間: `±1.96/√n`（白色化後の有効サンプル数 n）
- 非定常系列に直接適用すると偽の高 CCF が出る

#### グレンジャー因果性検定

**答える問い**: 「Xの過去の値は、Yの未来を（Yの過去だけより）予測できるか？」

```
H0: X は Y を Granger-cause しない
→ VARモデルで F検定（またはχ²検定）
→ p < 0.05 で棄却 = 「XはYの予測に統計的に有効」
```

- 「予測的因果」であり、真の因果ではない
- **多重比較補正必須**: n銘柄 × m商品でテストするとBH補正(FDR)を適用
- ラグ次数はAIC/BICで自動選択（週次なら通常p=1〜4が経済的に自然）

#### 共和分検定

**答える問い**: 「2つの非定常系列は長期的な均衡関係があるか？」

- 両系列がI(1)（非定常・1階差分で定常）の場合に使用
- 用途: ペアトレード候補の選定、株価 vs 長期EPS乖離分析、為替×輸出企業株価

#### CAR イベント分析

**答える問い**: 「イベント発生後の累積超過リターン（CAR）は有意に正/負か？」

- イベント系データ（決算サプライズ、自社株買い開示、急変シグナル）に使用
- **エントリーラグが最重要**（下記参照）

#### DTW（動的時間伸縮法）

**答える問い**: 「形が似ているが時間軸がずれた2つのパターンの類似度は？」

- 点予測には使えない。「類似検索」「クラスタリング」用途に限る

---

### Step 3: 見せかけの相関を防ぐ前処理

| 問題 | 対処 |
|------|------|
| トレンドの共有 | 差分化 or トレンド除去 |
| 季節性の共有 | 季節調整済み指標（前年同期比、平年比）を使う |
| 強い持続性（ACF高） | AR(p)白色化で残差を使う |
| 外れ値 | 上下1〜2%をwinsorize or 除外（ABS(ret)>40%等） |

**白色化の実装:**

```python
from statsmodels.tsa.ar_model import AutoReg
import numpy as np

def prewhiten(series, max_ar=8):
    """AR(p) AIC最小で白色化し残差を返す。"""
    best_aic, best_resid, best_p = np.inf, series - series.mean(), 0
    for p in range(1, max_ar + 1):
        try:
            fit = AutoReg(series, lags=p, old_names=False).fit()
            if fit.aic < best_aic:
                best_aic, best_resid, best_p = fit.aic, fit.resid, p
        except Exception:
            break
    return best_resid, best_p
```

---

### Step 4: CARイベント分析の注意点

#### エントリーラグの設定（最重要）

```python
# ❌ 誤り: シグナル週当日から累積（バックテストで再現不可）
car = sum(r_resid[i : i + window])

# ✅ 正しい: 翌週（実際にエントリーできる週）から累積
car = sum(r_resid[i+1 : i+1 + window])
```

| データ頻度 | 現実的なエントリーラグ |
|-----------|----------------------|
| 週次 | **翌週月曜**（同週は不可） |
| 日次 | 翌日寄り付き or 当日引け |
| 月次 | 翌月第1営業日 |

#### イベント定義（水準値 vs 変化量）

```
水準値イベント: X_t >= threshold（例: 平年比 >= 1.15）
  → 強い持続性があると連続イベントが発生して独立性が崩れる → 非推奨

変化量イベント: ΔX_t >= threshold（例: 白色化残差 >= 85パーセンタイル）
  → 急激な変化を検出、独立性が保たれる → 推奨
```

**ACF≈0.96 のような強持続性系列に水準値閾値を使うと**、閾値超過が10週連続などの「連続イベント」が多発し、CAR計算結果が互いに相関してp値が実際より小さく見える（偽の有意性）。

**対処法（推奨順）:**

1. **白色化残差で閾値を設定する（最も正確）**
   ```python
   threshold = np.nanpercentile(ar_resid_train, 85)
   events = np.where(ar_resid_test >= threshold)[0]
   ```

2. **クールダウン期間を設ける**
   ```python
   COOLDOWN = 4  # イベント後4週はイベント対象外
   selected, last_event = [], -COOLDOWN - 1
   for i in events_raw:
       if i - last_event > COOLDOWN:
           selected.append(i); last_event = i
   ```

---

### Step 5: 分析 PASS / FAIL 判定

```
分析 PASS 条件（すべて揃うと強い）:
  ① CCF で有意なラグが1つ以上存在
  ② グレンジャー因果性で BH補正後 p<0.05
  ③ CAR のイベント→翌週以降で有意かつ係数が実用サイズ
  ④ 係数の符号が仮説と一致
```

有意でなくても「効かなかった」という知見を `docs/knowledges/analysis/` に記録すること。

---

## Part 5: データ源別の典型パターン

| データ源 | シグナル例 | 系列の性質 | 推奨アルゴリズム |
|---------|-----------|-----------|----------------|
| J-Quants `/fins/statements` | EPS予想乖離率（四半期） | 低ACF | **CARイベント分析** |
| J-Quants `/prices/daily_quotes` | 株価リターン（週次） | ほぼWN | CCF or Granger の目的変数 |
| 日証金 | 逆日歩・品貸料（週次変化） | 低ACF（定常） | CCF or Granger or CAR |
| EDINET | 自社株買い開示ダミー | イベント系 | **CARイベント分析** |
| 農林水産省統計 | 野菜平年比（水準） | ACF≈0.96（擬似非定常）| **差分化→白色化CCF→Granger** |
| 気象データ | 気温偏差 | 低ACF（定常） | CCF or Granger |
| 日銀統計 | 鉱工業生産指数 | トレンドあり（非定常）| **共和分 or 差分化→Granger** |
| e-STAT | 景気動向指数DI | 0〜100の有界・低ACF | CCF or Granger |
| X(Twitter) | センチメント変化量 | 低ACF（変化量なら）| CCF or Granger |
| 株価ペア | 価格差 | 非定常 | **共和分検定→VECM** |

---

## Part 6: 実装リファレンス

| 手法 | ライブラリ | 主要関数 |
|------|----------|---------|
| ADF検定 | statsmodels | `statsmodels.tsa.stattools.adfuller` |
| ACF | statsmodels | `statsmodels.tsa.stattools.acf` |
| AR白色化 | statsmodels | `statsmodels.tsa.ar_model.AutoReg` |
| CCF | numpy | `np.corrcoef`（手動ラグシフト）|
| グレンジャー因果性 | statsmodels | `statsmodels.tsa.api.VAR` + `test_causality` |
| 共和分 | statsmodels | `statsmodels.tsa.stattools.coint` |
| DTW | dtaidistance | `dtaidistance.dtw.distance` |
| BH補正 | statsmodels | `statsmodels.stats.multitest.multipletests(method='fdr_bh')` |
| Mann-Whitney U | scipy | `scipy.stats.mannwhitneyu(alternative='less')` |

実装例: `scripts/analyze_yasai_stock_v3.py`

---

## Part 7: 枝番採番ルール

### 原則

1つの論文・仮説・データソースから**複数のバリエーション**（定義の変更、パラメータの切り口、応用方向）が派生した場合、**親アイデアの番号に枝番を付けて管理する**。投資アイデア・分析・バックテストの全工程に共通で適用する。

### 採番規則

```
docs/knowledges/analysis/NNN_<親slug>.md           ← 親（アイデア集、応用候補カタログ）
docs/knowledges/analysis/NNN-1_<子slug>.md         ← 枝番1（具体的な検証・バリエーション）
docs/knowledges/analysis/NNN-2_<子slug>.md         ← 枝番2
docs/knowledges/analysis/NNN-3_<子slug>.md         ← 枝番3
```

### 相互参照（必須）

**親ファイル**に枝番一覧テーブルを設ける:

```markdown
## 枝番アイデア一覧（本ファイルから派生）
| 枝番 | ファイル | 内容 | ステータス |
|---|---|---|---|
| NNN-1 | [NNN-1_xxx.md](NNN-1_xxx.md) | ... | ANALYZED_FAIL |
| NNN-2 | [NNN-2_yyy.md](NNN-2_yyy.md) | ... | Pass 1 実行中 |
```

**各枝番ファイル**のヘッダに親・兄弟への相互リンクを記載:

```markdown
**親アイデア**: [NNN_parent.md](NNN_parent.md)（タイトル）
**兄弟**:
- [NNN-1_xxx.md](NNN-1_xxx.md)（タイトル、ステータス）
- [NNN-3_zzz.md](NNN-3_zzz.md)（タイトル、ステータス）
```

### いつ枝番を使うか

- **使う**: 同じ論文/仮説から派生した複数バリエーション、パラメータ定義の切り替え検証
- **使わない**: 独立した仮説・データソースに基づく別アイデア → 新しい NNN 番号を採番

### 実例

```
011_subspace_pca_leadlag_applications.md    ← 親（論文 SIG-FIN-036-13 の応用アイデア集）
011-1_cluster_overnight_daytime_leadlag.md  ← overnight→daytime × クラスタ（FAIL）
011-2_size_leadlag_multidef.md              ← 大型→中小型リードラグ（FAIL）
011-3_cluster_cross_leadlag.md              ← クラスタ間クロスリードラグ（NEW）
```
