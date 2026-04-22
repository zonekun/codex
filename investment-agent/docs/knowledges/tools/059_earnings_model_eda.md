---
name: 決算反応モデル EDA ノートブック
description: 決算発表銘柄の株価反応予測モデル（決算内容・バリュエーション・需給等を総合判断）のEDAノートブック構成・データフロー・GCS保存先
type: tools
作成日: 2026-03-29
更新日: 2026-04-10
ステータス: 有効
関連ファイル:
  - scripts/earnings_model/earnings_model_eda.ipynb
  - scripts/earnings_model/earnings_model_predict.ipynb
---

## 概要

決算発表銘柄の翌日株価騰落率を予測するモデル構築用 EDA ノートブック。
決算内容（進捗率・修正・YoY等）に限らず、バリュエーション（PER/PBR/PEG）・需給（自社株買い・増配）・株価水準（過去騰落・織り込み度合い）等も因子として取り込む設計。
過去決算データの特徴量計算・ラベル生成・単変量IC検証・セクター分析を行う。

**実行環境**: Google Colab / Windows ローカル（RUNTIME 自動判定）

### 予測 & 答え合わせノートブック

`earnings_model_predict.ipynb`（別名: 答え合わせノートブック） — EDA で設計した特徴量・スコアリングを使い、当日のザラバ決算 + 引け後決算を一括スコアリング。`is_intraday` フラグで区別し、答え合わせの基準を切り替える。

- **ザラバ銘柄** (`is_intraday=True`): 当日終値 vs 前日終値（当日中に答え合わせ可能）
- **引け後銘柄** (`is_intraday=False`): 翌日(`ACTUAL_DATE`)終値 vs 当日終値

#### predict ノートブック構成（11セル）

| セル# | 種別 | 内容 |
|-------|------|------|
| 0 | markdown | タイトル・使い方 |
| 1 | code | Setup（EDAと同構成 + `jquantsapi.ClientV2` + GCS クライアント） |
| 2 | code | 設定: `PREDICT_DATE`（発表日）/ `ACTUAL_DATE`（翌営業日） |
| 3 | markdown | 1. 当日決算取得 + 特徴量生成 |
| 4 | code | J-Quants `get_fin_summary` → 全 FinancialStatements（`is_intraday` 付与）→ **訂正報告除外**（CurPerEn が9ヶ月以上前）→ BQ（銘柄名・当日/前日株価・コンセンサス・前回予想・QoQ）→ 特徴量計算 |
| 5 | markdown | 2. スコアリング & 予測 |
| 6 | code | 8因子スコアリング → 予測カテゴリ分類 → GCS 保存 |
| 7 | markdown | 3. 答え合わせ |
| 8 | code | ザラバ: prediction内の adj_close/prev_close でリターン計算、引け後: BQ ACTUAL_DATE 株価 → 統合 → 方向一致率・相関（ザラバ/引け後別）→ GCS 保存 |
| 9 | markdown | 4. 精度集計 |
| 10 | code | 全期間の予測/実績を集約 → 精度メトリクス → グラフ → GCS 保存 |

#### スコアリング因子クイックリファレンス（コード実装準拠）

> **運用ルール**: このテーブルは `earnings_model_predict.ipynb` の `compute_score()` 関数と**常に一致**させること。因子の追加・閾値変更・ウェイト変更・廃止をノートブックに反映したら、このテーブルも同時に更新する。反省会ではこのテーブルだけ見ればスコア分解できる状態を維持する。

**最終更新: 2026-04-13**

| # | 因子 | 発火条件(+) | Score(+) | 発火条件(-) | Score(-) | 対象Q | データソース |
|---|------|------------|----------|------------|----------|-------|-------------|
| 1 | 進捗率 | >期待×1.2 | +1 | <期待×0.8 | -1 | 1Q/2Q/3Q | J-Quants OP÷FOP |
| 2 | ガイダンス修正 | 上方 | +1 | 下方 | -1 | 全Q | J-Quants FOP vs BQ前回FOP |
| 3 | YoY OP | >+30% | +1 | <-30% | -1 | 全Q | BQ v_fin_summary_actual_for_q_on_q **（廃止候補）** |
| 4 | コンセンサス乖離 | >+10%/+5%/+0% | +3/+2/+1 | <0%/<-5%/<-10% | -1/-2/-3 | 全Q | BQ CONSENSUS (TARGET=CURRENT) |
| 5 | 翌期見通し | >+10% | +2 | <-10% | -2 | FYのみ | J-Quants NxFOP vs OP |
| 6 | 出尽くしリスク | — | — | 進捗>90% + 据え置き | -2 | 3Qのみ | F1+F2の組合せ |
| 7 | 成長加速/減速 | gap>+20pt | +1 | gap<-20pt | -1 | FYのみ | BQ 過去FY YoY OP median |
| 8 | 記念配当/特別配当 | TDnet TITLE一致 | +1 | — | — | 全Q | yanoshin API / TDnet HTML / BQ |
| 9 | テーマブースト | β>1.0 かつ TOPIX当日+ | +1 | — | — | 全Q | GCS beta_20d.csv + BQ INDEX_PRICE |
| 10 | 自社株買い | TDnet TITLE "自己株式の取得" | +2 | — | — | 全Q | yanoshin API / TDnet HTML / BQ |
| 11 | 増配/減配 | FDivAnn vs 前回 >+5% | +1 | <-5%→-2, <-20%→-3 | -2/-3 | 全Q | J-Quants FDivAnn vs BQ前回DPS |
| 12 | PER割安度(PEG) | PEG<1.0→+1, <0.5→+2 | +1/+2 | PEG>2.0 | -1 | FYのみ | 株価÷ForEPS÷(翌期OP成長率×100) |
| 13 | QoQ OP急変 | 前Q比>+50% | +1 | 前Q比<-50% | -2 | 全Q | BQ v_fin_summary_actual_for_q_on_q |

**スコア→予測**: `>=2` = UP / `-1〜1` = NEUTRAL / `<=-2` = DOWN

---

#### スコアリング因子（設計経緯・詳細）

**A. 決算内容因子（F1〜F9）**

| # | 因子 | +条件 | -条件 | ウェイト | 状態 |
|---|------|-------|-------|---------|------|
| 1 | 進捗率 | >期待×1.2 | <期待×0.8 | ±1 | 有効 |
| 2 | ガイダンス修正 | 上方 | 下方 | ±1 | 有効 |
| 3 | YoY OP | >+30% | <-30% | ±1 | **廃止候補**。前年が異常値なら歪む。F13（QoQ OP急変）で代替。EDA検証で除去影響を確認後に正式廃止 |
| 4 | コンセンサス乖離（段階的） | >+10%/+5%/+0% | <0%/<-5%/<-10% | ±1〜±3 | 有効（2026-04-06復活。段階的スコアに変更、データあり銘柄のみ適用） |
| 5 | 翌期見通し（FYのみ） | >+10% | <-10% | ±2 | 有効 |
| 6 | 出尽くしリスク（3Q） | - | 進捗>90%+据え置き | -2 | 有効 |
| 7 | 成長減速/加速（FYのみ） | gap>+20pt | gap<-20pt | ±1 | 有効（2026-04-06追加。翌期OP YoY vs 企業別baseline median） |
| 8 | 記念配当/特別配当 | TDnet公式サイト TITLE一致 | - | +1 | 有効（2026-04-06追加。TDnet HTMLスクレイピングで当日対象銘柄のみ検索） |
| 9 | テーマブースト | β>1.0 かつ TOPIX当日+ | - | +1 | 有効（2026-04-10追加。20日β×地合い交互作用。EDA検証 p=0.009 有意） |

**B. バリュエーション・需給因子（F10〜）**

| # | 因子 | +条件 | -条件 | ウェイト | 状態 | 参考 |
|---|------|-------|-------|---------|------|------|
| 10 | 自社株買い | yanoshin API / TDnet HTML / BQ で "自己株式の取得" TITLE一致 | - | +2 | 有効（2026-04-13実装。TSI HD +20.8% 外し契機） | — |
| 11 | 増配/減配 | J-Quants FDivAnn vs 前回予想 >+5% | <-5% → -2, <-20% → -3 | +1 / -2〜-3 | 有効（2026-04-13実装。タマホーム 196→125円減配で-10.0%外し契機） | — |
| 12 | PER割安度（PEGベース、FYのみ） | PEG<1.0 → +1, PEG<0.5 → +2 | PEG>2.0 → -1 | ±1〜±2 | 有効（2026-04-13実装。JINS HD +18.6% 外し契機） | `docs/references/web/20260413_buffett_code_per_by_growth_rate.md` |
| 13 | QoQ OP急変（単独四半期） | 前Q比 >+50% → +1 | 前Q比 <-50% → -2 | +1 / -2 | 有効（2026-04-13実装。大黒天物産 -10.5% 外し契機） | — |

**F12 PER割安度の設計根拠（バフェットコード記事より）:**
- 安定成長銘柄（2000年以降上場・4期連続増益）で回帰: `適正PER = 21.278 × EPS成長率 + 13.991`
- 売上成長率ベース: `適正PER = 23.732 × 売上成長率 + 14.889`
- PEGレシオ1倍以下（PER倍率 < EPS成長率%）が一定の割安水準
- 決定係数は低いため閾値は保守的に設定。EDA検証で有効性を確認してから有効化する

**F9 テーマブーストの設計意図**: 20日βを「個人投資家に人気の銘柄」の代理変数として使用している。β>1.0の銘柄は値動きが大きく個人の短期売買が集中しやすいため、地合いが良い日（TOPIX+）に決算サプライズがあると追随買いで反応が増幅される傾向がある。βは個人人気の完全な指標ではないが、銘柄マスタから取得可能で日次更新できる実用的な近似値として採用。

**F9 βキャッシュ**: Cloud Run Job `beta-calc`（日次 18:30 JST、stock-price-jquants-load後）で全銘柄の20日βを計算し `gs://stock_data_1930932/earnings_model/beta_20d.csv` に保存。predict ノートブックはGCSからCSV読み込み。スクリプト: `scripts/beta_calc.py`

**F5 ウェイト ±2→±3 増加案**: EDA検証で棄却（全体相関が悪化。±2 を維持）

スコア→予測: `>=2`=UP, `-1〜1`=NEUTRAL, `<=-2`=DOWN（2026-04-09変更。SLIGHT系は全期間で方向一致率25-40%と機能せず3段階に統合）

**F4 コンセンサス乖離バグ修正（2026-04-10）**: コンセンサス取得クエリに `TARGET='CURRENT'` フィルタと `PARTITION BY TICKER, QUARTER` を追加。修正前は NEXT（来期予想）や別QUARTERのコンセンサスが混入し、異常な乖離率が算出されていた（例: セブン&アイ FY で 1Q コンセンサスと比較→+580%）。4/2〜4/9 の過去 prediction のうち4件で予測カテゴリが変わる影響あり（未修正のまま据え置き）

#### バッチ再実行（過去日分の一括再スコアリング）

`scripts/earnings_model/batch_rerun_predict.py` — 複数日分の予測 + 答え合わせを**BQクエリ7本**で一括再生成するスクリプト（2026-04-16 新規）。ノートブック Cell5 を都度実行する `rerun_predict.py` 方式とは異なり、共通データをまたいで重複クエリしない設計。

```bash
# 04/01-04/13 の営業日9日を一括再実行（デフォルト）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/batch_rerun_predict.py
```

**対象日の変更**: スクリプト先頭の `DATE_PAIRS`（PREDICT_DATE:ACTUAL_DATE のタプルリスト）を編集する。

**共通取得データ（BQ クエリ原則1回のみ）**

| # | テーブル | 取得範囲 | 用途 |
|---|---------|---------|------|
| 1 | STOCK_CODE_LIST | 全件 | name / sector / market |
| 2 | STOCK_PRICE_JQUANTS | `DATE_PRICE_MIN`〜`DATE_MAX_ACTUAL` | 当日・前日・実績価格 |
| 3 | CONSENSUS | `DATAAT <= DATE_MAX_PREDICT`, per-group top 20 | as-of 最新をpandasで絞る |
| 4 | fin_summary | 直近18ヶ月 | 前回予想・前回配当 |
| 5 | v_fin_summary_actual_for_q_on_q | `FY_START >= 2020-04-01` | QoQ + baseline YoY |
| 6 | TDNET_DOCUMENTS_ENHANCED | 予測日範囲 | 記念配当・自社株買い |
| 7 | INDEX_PRICE (0000) | 予測日範囲 | TOPIX当日リターン |
| GCS | beta_20d.csv | — | 20日β |

**as-of フィルタの再現（重要）**: CONSENSUS・fin_summary・v_fin_summary 等は、各 PREDICT_DATE ごとに pandas 側で `<= ph` の絞り込みを行い per-date で最新を選ぶ。コンセンサスを「決算発表時点で最新のもの」にする要件を担保する（ユーザー指示 2026-04-16）。

**書き込み規則（上書きしない）**

- `prediction_{PREDICT_DATE}_{HHMMSS}.json` ／ `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json` を新規 timestamp で追加保存
- `accuracy_summary.json` は全 actual を再集約（古い predict_date もファイル名ソートで新しいものが残る）→ **DATE_PAIRS に含めなかった日（例: 04/14）の既存 actual も集計に含まれる**
- 04/14 の既存 predictions/actuals を読み書きしないため、「正しい既存結果を保持したまま他日を再実行」が可能

**スコアリングロジック**: `compute_score()` は `earnings_model_predict.ipynb` Cell7 と**完全一致**する必要がある。ノートブックの因子を変更したらこのスクリプトも同時更新する。

**想定ユースケース**
1. 因子追加・バグ修正後、過去発表分の予測をまとめて再生成
2. 答え合わせを一括で取り直す（翌営業日の株価が揃ってから）
3. accuracy_summary の粒度やカラムを拡張したい時（ここでは `predict_dates` リスト追加など）

#### 反省会の運用

**反省会開始時**に一括DLスクリプトを実行し、以降はローカルファイルから読む（GCSアクセスゼロ）。

```bash
# 冒頭に1回だけ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/download_review_data.py 20260410

# DL先: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json
```

銘柄ごとの因子分解は `scripts/earnings_model/show_prediction.py` を使う（ローカル優先、無ければGCS自動フォールバック）。

```bash
# 単一銘柄
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/show_prediction.py 20260413 9948

# 複数銘柄 + 実績併記
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/show_prediction.py 20260413 9948 6217 3168 --with-actual
```

出力: SCORE / スコア理由 / 進捗率 / YoY OP / コンセ乖離 / ガイダンス変化 / 特別配当 / 自社株買い等の全因子を整形表示。`--with-actual` で実績リターン・方向一致も併記。

#### 反省会ログ

##### 2026-04-13（対象: 4/10発表分、84銘柄、方向一致率36.9%、相関0.214）

| 銘柄 | Score | 予測 | 実績 | 結果 | 教訓 |
|-------|-------|------|------|------|------|
| TSI HD (3608) | +1 | NEUTRAL | +20.8% | **外れ** | 自社株買い5.58%・増配が未捕捉。F10/F11新設契機 |
| JINS HD (3046) | -2 | DOWN | +18.6% | **外れ** | 予想PER17×高成長=PEG割安が未捕捉。F12新設契機 |
| 大黒天物産 (2791) | -1 | NEUTRAL | -10.5% | **外れ** | 前Q比OP半減が未捕捉。F13新設・F3廃止候補の契機 |
| タマホーム (1419) | +2 | UP | -10.0% | **外れ** | 配当196→125円の大幅減配が未捕捉。F11減配方向追加の契機 |
| 近鉄百貨店 (8244) | -2 | DOWN | -6.0% | **的中** | F5（翌期OP減益-19.6%）単独で正解 |
| 安川電機 (6506) | +3 | UP | +7.0% | **的中** | F5（翌期+26.8%）+F7（成長加速 gap+33pt）。今期減益でも来期ガイダンスが効いた |

**4/10 所感: F5（翌期見通し）が最も信頼性の高い因子**。近鉄百貨店（DOWN的中）・安川電機（UP的中）ともにF5が主因。今期の数字が悪くても/良くても、市場は来期ガイダンスを重視する。

**4/10 の主な改善決定:**
- F3 YoY OP → **廃止候補**（前年異常値で歪む。F13 QoQ OPで代替）
- F10 自社株買い → 新設設計済み
- F11 増配/減配 → 新設設計済み（非対称ウェイト: +1 / -2〜-3）
- F12 PER割安度（PEG） → 新設設計済み（参考: バフェットコード記事）
- F13 QoQ OP急変 → 新設設計済み

##### 2026-04-14（対象: 4/14発表分、185銘柄、方向一致率40.9%、相関0.201）

**枝番（関連MD）**:
- [01 F5/F4 NaN バグ（2379 ディップ起点）](../../plans/059_review_20260414_01_f5_nan.md) — 翌期OP変化・コンセ乖離がNaN化する既存因子バグ
- [02 actual_return NaN バグ（運用起因）](../../plans/059_review_20260414_02_actual_nan.md) — Step3を翌日価格投入前に実行した運用ミス。恒久対応はガード節追加

**銘柄別ログ**:

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| 除外 | 3387 | クリレス | — | 引け後 | — | 子会社統合（特殊イベント）。登録済 |
| 除外 | 9842 | アークランズ | — | 引け後 | — | 経営統合（特殊イベント）。登録済 |
| ハズレ | 5243 | note | +2 | UP | **-16.2%** | **寄り天フェード**。β高グロース×好業績出尽くし |
| ハズレ | 277A | グロービング | +5 | UP | **-14.5%** | **寄り天フェード**。グロース市場、β=1.3、YoY+90%・コンセ+16.7%でも出尽くし |
| ハズレ | 2379 | ディップ | -1 | NEUTRAL | -9.4% | **F5/F4 NaN バグ**（枝番01）。本来 DOWN 的中 |
| ハズレ | 6058 | ベクトル | -1 | NEUTRAL | -9.0% | 業態要因（PR会社・機関投資家保有少）。業種係数で将来対応可能 |
| 中立大 | 1418 | インターライフHD | 0 | NEUTRAL | **+16.5%** | シグナルなしで+16.5%。隠れ材料疑い。スクリーニング鉱脈 |
| 的中 | 3994 | マネフォ | +4 | UP | **+7.7%** | NaN バグ（枝番02）修正前はハズレ判定。万年赤字→黒字化は F14 候補 |
| 的中 | 9602 | 東宝 | -4 | DOWN | -4.6% | NaN バグ修正で的中判定復帰 |

**4/14 新因子候補**（memory `project_new_factor_candidates.md` に集約）:
- **F14 黒字化サプライズ**（3994起点）
- **F15 株主優待新設**（3994起点）
- **寄り天フェード対策**（5243・277A起点）: 事前織り込み度、個人注目度プロキシ、PER/PEG
- **市場区分 market_division**（277A起点）: Colab編集済、次回PREDICT実行から反映
- **異常値スクリーニング**（1418起点）: score=0 & |actual|大 → 隠れ材料発掘

**4/14 ツール改善（実施済）**:
- `scripts/earnings_model/review_report.py` 新規作成: 当たり/中立/ハズレ/要確認 4分類 CSV+MD 出力（時価総額・市場区分・騰落率列含む）
- `scripts/earnings_model/exclusion_manager.py` 新規作成: 除外フラグ add/list/remove CLI
- `earnings_model_predict.ipynb` 改修:
  - actual_records に `compare_before_close`/`compare_after_close` 追加（ザラバ/引け後で意味統一）
  - actual ファイル名規約変更: `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`
  - `market_division` 追加（STOCK_CODE_LIST.MARKET_CATEGORY JOIN）

**4/14 残TODO**:
- [01] F5/F4 NaN バグ調査・修正（枝番01）
- FY発表全銘柄で F5/F4 NaN 率サーベイ
- 複数日分 actual 揃った後、因子ウェイト再検証（特に F9 テーマブースト の過剰反応疑い：277A）
- 新因子候補の妥当性検証

#### ML切り替え計画

現在はルールベース（手動閾値+ウェイト）でスコアリングしている。GCS `actuals/` のレコードが数百件に達した段階で、ロジスティック回帰 or LightGBM/XGBoost への切り替えを提案する。

- 因子の有無だけでなく数値の大小（コンセンサス乖離率、記念配当金額等）が重要だが、ルールベースでは適切に扱えない
- 連続値の特徴量（`consensus_deviation`, `yoy_op`, `baseline_yoy_op` 等）は prediction JSON に保存済みなので、そのまま学習データとして使える
- バリュエーション（PER/PEG）・需給（自社株買い・増配）・株価水準（過去N日騰落）等の非決算因子もML化時に特徴量として投入する
- 切り替え時は EDA ノートブックで因子ごとのリターン寄与を検証してから

#### GCS 保存先

```
gs://stock_data_1930932/earnings_model/
  predictions/prediction_YYYYMMDD.json   # 予測結果
  actuals/actual_YYYYMMDD.json           # 実績突合結果
  accuracy/accuracy_summary.json         # 累積精度サマリー
```

#### GCS ファイル確認時の注意（必須）

1. **タイムスタンプは UTC**: Colab の `datetime.now()` は UTC。ファイル名の `HHMMSS` も `created_at` も UTC。**必ず +9h して JST に変換**してから判断すること
2. **最新ファイルの特定**: ファイル名は `actual_{PREDICT_DATE}_{HHMMSS}.json` 形式。同一 PREDICT_DATE に複数ファイルが存在する場合、**ファイル名のソート順 ≠ 作成日時順**（異なる日に実行すると HHMMSS が前後する）。`created_at` を JST 変換して最新を判断する
3. **GCS リスト結果を鵜呑みにしない**: リスト結果にファイルが見つからない場合でも、ユーザーが「実行済み」と言っているなら**ファイル名を直接指定して `gcs_read` を試す**。リストのキャッシュ・遅延の可能性がある

#### 使い方

1. **当日引け後**: `PREDICT_DATE` を設定 → セル1〜6を実行（予測生成 + GCS保存）
2. **翌営業日引け後**: `ACTUAL_DATE` を設定 → セル8〜10を実行（答え合わせ + 精度集計）

## ノートブック起動

- **Colab**: `scripts/earnings_model/earnings_model_eda.ipynb` を開いて実行
- **ローカル**: `jupyter lab scripts/earnings_model/earnings_model_eda.ipynb`

### 保存先（2箇所）

| 保存先 | 用途 |
|--------|------|
| `scripts/earnings_model/earnings_model_eda.ipynb` | Git 管理（メイン） |
| `G:\マイドライブ\Colab Notebooks\` | Colab から直接開く用（手動コピー） |

## BQ コスト最適化（重要）

**BQ アクセスは初回1回のみ**。全8テーブルをまとめてダウンロードし CSV キャッシュ。2回目以降は `FORCE_RELOAD=False`（デフォルト）でローカル CSV から読み込み、BQ アクセスゼロ。

- Colab: `/content/earnings_model_cache/`
- ローカル: `C:\tmp\earnings_model_cache\`

## ノートブック構成（24セル）

| セル# | 種別 | 内容 |
|-------|------|------|
| 0 | markdown | タイトル・データソース一覧 |
| 1 | code | Setup: 実行環境自動判定（`RUNTIME`）・Colab OAuth / ローカル SSL 回避 + サービスアカウント認証・BQ クライアント・J-Quants API・Gemini Flash クライアント |
| 2 | code | Config: `DATE_FROM`/`DATE_TO`・`USE_BQ`・`FORCE_RELOAD` フラグ・`CACHE_DIR` |
| 3 | markdown | 1. データ取得セクションヘッダー |
| 4 | code | BQ 8テーブル一括ダウンロード → CSV キャッシュ or キャッシュ読み込み |
| 5 | code | DocType / CurPerType の分布確認 |
| 6 | code | フィルタリング: `FinancialStatements` のみ抽出 + 事前修正フラグ生成 |
| 7 | markdown | 2. データ結合セクションヘッダー |
| 8 | code | 発表タイミング判定（ザラ場/引け後）+ 株価反応ラベル生成 |
| 9 | code | 全テーブル結合 + コンセンサス百万円→円変換 + TDnet イベント抽出（自社株買い・分割・優待） |
| 10 | markdown | 3. 特徴量エンジニアリングセクションヘッダー |
| 11 | code | 特徴量計算（サプライズ・正確版サプライズ・YoY・同時アナウンス・事前修正等） |
| 12 | markdown | 3-b. GCS 保存/読み込みセクションヘッダー |
| 13 | code | 特徴量 CSV を GCS に保存・読み込み・一覧確認 |
| 14 | markdown | 4. EDA セクションヘッダー |
| 15 | code | 4-1. ラベル分布 |
| 16 | code | 4-2. 四半期別リターン分布 |
| 17 | code | 4-3. 因子 x ラベル散布図 |
| 18 | code | 4-4. 単変量 IC（情報係数）一覧 |
| 19 | code | 4-5. セクター別リターン中央値 |
| 20 | markdown | 5. クラスタ別精度分析セクションヘッダー |
| 21 | code | 5-1. クラスタラベル読み込み（残差相関モデル clusters_2024/2025.csv） |
| 22 | code | 5-2. クラスタ別方向一致率・相関分析 |
| 23 | markdown | 6. TODO / 次のステップ |

## 実行環境の自動判定（RUNTIME）

セル1（Setup）で `google.colab` の import 可否により自動判定:

| RUNTIME | 認証方式 | BQ クライアント | Gemini クライアント |
|---------|---------|---------------|-------------------|
| `colab` | `auth.authenticate_user()` | `bigquery.Client(project=...)` | `genai.Client(vertexai=True, ...)` |
| `local` | サービスアカウント + SSL 回避パッチ | `bigquery.Client(credentials=creds, ...)` | `genai.Client(vertexai=True, ..., credentials=creds)` |

**Gemini Flash**: 優待分類に `gemini-2.5-flash`（Vertex AI, us-central1）を使用。`google-genai` ライブラリ。

## フィルタリング（セル6）

分析対象は **`FinancialStatements` を含む DocType のみ**。業績修正（`EarnForecastRevision`）・配当修正（`DividendForecastRevision`）は除外し、「事前修正があったか」の特徴量（`has_prior_revision` / `has_prior_div_revision`）として活用する。

## fin-summary 取得モード（USE_BQ フラグ）

| `USE_BQ` | 取得元 | 用途 |
|----------|--------|------|
| `True`（デフォルト） | BQ `STOCK.fin_summary` | 過去データ・モデル構築 |
| `False` | J-Quants API `/fins/summary` | BQ未収録の最新データ |

### 修正決算の扱い

**BQ取得時のみ**: 同一銘柄・同一四半期で複数開示が存在する場合（修正決算）は最新 `DISCLOSED_DATE` を優先。

```sql
ROW_NUMBER() OVER (
    PARTITION BY LOCAL_CODE, TYPE_OF_CURRENT_PERIOD, CURRENT_FISCAL_YEAR_END_DATE
    ORDER BY DISCLOSED_DATE DESC
) AS rn
-- WHERE rn = 1
```

**API取得時**: 最新決算のため修正決算は存在しない前提。重複除去不要。

### BQ カラム → 短縮名マッピング（主要項目）

| BQ カラム名 | 短縮名 | 内容 |
|-------------|--------|------|
| `DISCLOSED_DATE` | `DiscDate` | 開示日 |
| `LOCAL_CODE` | `Code` | 銘柄コード（4桁） |
| `TYPE_OF_DOCUMENT` | `DocType` | 開示書類種別 |
| `TYPE_OF_CURRENT_PERIOD` | `CurPerType` | 四半期種別 |
| `NET_SALES` | `Sales` | 売上高 |
| `OPERATING_PROFIT` | `OP` | 営業利益 |
| `ORDINARY_PROFIT` | `OdP` | 経常利益 |
| `PROFIT` | `NP` | 当期純利益 |
| `FORECAST_NET_SALES` | `FSales` | 通期売上予想 |
| `FORECAST_ORDINARY_PROFIT` | `FOdP` | 通期経常利益予想 |
| `NEXT_YEAR_FORECAST_PROFIT` | `NxFNp` | 翌期純利益予想 |
| `RESULT_DIVIDEND_PER_SHARE_ANNUAL` | `DivAnn` | 実績年間配当 |
| `FORECAST_DIVIDEND_PER_SHARE_ANNUAL` | `FDivAnn` | 予想年間配当 |
| `NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_...` | `ShOutFY` | 期末発行済株式数 |
| `NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_...` | `TrShFY` | 期末自己株式数 |

## 特徴量一覧（セル11）

### 3-1. Q単独実績 vs 会社予想（従来近似版）

| 特徴量名 | 計算方法 |
|----------|---------|
| `surprise_sales_vs_forecast` | Q単独: Q実績 vs (通期予想-累積実績)/残りQ数。FY: 実績 vs 通期予想 |
| `surprise_op_vs_forecast` | 同上（営業利益） |
| `surprise_np_vs_forecast` | 同上（純利益） |

### 3-2. 実績 vs コンセンサス

| 特徴量名 | 計算方法 |
|----------|---------|
| `surprise_odp_vs_consensus` | 累積経常利益 vs CONSENSUS_PROFIT（百万円→円変換済み） |

### 3-3. 翌期ガイダンス・残Qペース

| 特徴量名 | 計算方法 |
|----------|---------|
| `guidance_fy_sales` / `guidance_fy_op` / `guidance_fy_np` | 翌期予想 vs 今期実績比（FY のみ有意） |
| `guidance_remaining_q_op` | 残りQ期間の会社予想ペース（Q1-Q3 で有効） |

### 3-4. 配当・時価総額

| 特徴量名 | 計算方法 |
|----------|---------|
| `dividend_surprise` | 実績年間配当 vs 予想年間配当（clip=3.0） |
| `market_cap` | log1p(ADJ_CLOSE x 浮動株数) |

### 3-6. 正確版サプライズ（前回発表時の会社予想ベース）

| 特徴量名 | 計算方法 |
|----------|---------|
| `surprise_sales_accurate` | Q実績 vs 前回発表時の(通期予想-累積実績)/前回時点の残りQ数。Q1 は従来近似版にフォールバック |
| `surprise_op_accurate` | 同上（営業利益） |
| `surprise_np_accurate` | 同上（純利益） |

前回の残りQ数マッピング: `{"2Q": 3, "3Q": 2, "FY": 1}`（Q1 は prev_forecast が存在しない）

### 3-7. 前年同期比（YoY）

| 特徴量名 | 計算方法 |
|----------|---------|
| `yoy_sales` | Q単独売上 vs 前年同期Q単独売上 |
| `yoy_op` | Q単独営業利益 vs 前年同期 |
| `yoy_np` | Q単独純利益 vs 前年同期 |

### 3-8. 決算同時アナウンス（TDnet イベント）

| 特徴量名 | 型 | 計算方法 |
|----------|-----|---------|
| `has_buyback` | int | 決算同日に自社株買いアナウンスあり |
| `buyback_pct` | float | 自社株買い割合（%）。CHUNK_TEXT から正規表現で抽出 |
| `has_stock_split` | int | 決算同日に株式分割アナウンスあり |
| `split_ratio` | float | 分割比率（例: 2.0 = 1:2分割）。CHUNK_TEXT から抽出 |
| `has_yutai` | int | 決算同日に優待変更アナウンスあり |
| `yutai_score` | int | Gemini Flash（`gemini-2.5-flash`）で分類: +1=拡充/新設, -1=改悪/廃止, 0=不明 |

### 3-9. 事前修正フラグ（織り込み度合いの代理変数）

| 特徴量名 | 計算方法 |
|----------|---------|
| `has_prior_revision` | 同一 Code で DiscDate 以前に `EarnForecastRevision` が存在（セル6で生成） |
| `has_prior_div_revision` | 同一 Code で DiscDate 以前に `DividendForecastRevision` が存在 |

### メタ・ラベル列

`Code`, `DiscDate`, `CurPerType`, `DocType`, `quarter`, `industry_33`, `size_cat`, `price_date`, `is_intraday`, `label_close_return`, `label_open_return`

## GCS 保存先

```
gs://stock_data_1930932/earnings_model/
  features/
    YYYYMMDD_YYYYMMDD.csv    # 特徴量 + ラベル（期間別）
  models/
    YYYYMMDD_<name>.pkl      # 学習済みモデル（日付+モデル名）
  reports/
    YYYYMMDD_metrics.json
    YYYYMMDD_feature_importance.csv
```

### 保存・読み込み関数（ノートブック内）

```python
# 保存（C:\tmp\ 経由）
save_features_to_gcs(feat, DATE_FROM, DATE_TO)

# 読み込み
feat = load_features_from_gcs('20260401', '20260415')

# 一覧確認
list_features_on_gcs()
```

## 時価総額の計算方法

`fin-summary` に `MarketCapitalization` フィールドは存在しない（2026-03-29確認）。
以下で代替計算：

```python
# 発表日終値 × 浮動株数（対数変換）
float_shares = ShOutFY - TrShFY
market_cap = log1p(ADJ_CLOSE × float_shares)
```

## データソース詳細（BQ 8テーブル）

| # | データ | BQテーブル/API | キャッシュファイル名 | 備考 |
|---|--------|---------------|-------------------|------|
| 1 | 決算実績・予想・配当・株式数 | BQ `STOCK.fin_summary` / J-Quants `/fins/summary` | `{tag}_fin_summary.csv` | USE_BQ で切替。修正決算は最新 DISCLOSED_DATE 優先 |
| 2 | セクター・市場区分 | BQ `STOCK.STOCK_CODE_LIST` | `sector_master.csv` | INDUSTRY_33_CODE・INDUSTRY_33_CATEGORY・SIZE_CODE |
| 3 | コンセンサス（経常利益） | BQ `STOCK.CONSENSUS` | `{tag}_consensus.csv` | DATAAT・TICKER・PROFIT。発表日以前の最新1件。**百万円単位→円変換が必要**（結合時に `* 1_000_000`） |
| 4 | 株価・ラベル | BQ `STOCK.STOCK_PRICE_JQUANTS` | `{tag}_price.csv` | ADJ_CLOSE・ADJ_OPEN・VOLUME。IS_PREFERRED=FALSE。発表日前後5日分 |
| 5 | Q単独実績（当期） | BQ `STOCK.v_fin_summary_actual_for_q_on_q` | `{tag}_yoy.csv` | 累積→単独四半期変換済みビュー。Consolidated のみ |
| 6 | 前回発表時の会社予想 | BQ `STOCK.fin_summary`（自己JOIN） | `{tag}_prev_forecast.csv` | 同一会計年度の前四半期レコード（2Q→1Q, 3Q→2Q, FY→3Q）。正確版サプライズ計算用 |
| 7 | 前年同期Q単独実績 | BQ `STOCK.v_fin_summary_actual_for_q_on_q`（自己JOIN） | `{tag}_prev_year_q.csv` | DATE_ADD(prev.start, INTERVAL 1 YEAR) = cur.start で結合。YoY 計算用 |
| 8 | TDnetイベント | BQ `STOCK.TDNET_DOCUMENTS_ENHANCED` | `{tag}_tdnet_events.csv` | MAIN_CATEGORY IN ('自己株式取得', '株式分割・併合') OR DOC_TITLE LIKE '%株主優待%'。決算同日分のみ |

## v_fin_summary_actual_for_q_on_q スキーマ詳細

**ソーステーブル**: `STOCK.fin_summary`
**用途**: 累積P&L（1Q累積・2Q累積・3Q累積・FY累積）を単独四半期値（Q1・Q2・Q3・Q4）に変換したビュー。前年同期比（QoQ）計算のメインデータソース。

### カラム一覧

| カラム名 | 型 | 内容 |
|---------|-----|------|
| `LOCAL_CODE` | STRING | 銘柄コード（4〜5桁） |
| `DISCLOSED_DATE` | DATE | 開示日 |
| `DISCLOSED_TIME` | TIME | 開示時刻 |
| `TYPE_OF_DOCUMENT` | STRING | 開示書類種別（例: `1QFinancialStatements_Consolidated_JP`） |
| `QUARTER` | STRING | 四半期ラベル（`1Q`/`2Q`/`3Q`/`4Q`）。元の `FY` を `4Q` に変換済み |
| `TYPE_OF_CURRENT_PERIOD` | STRING | 元の開示種別（`1Q`/`2Q`/`3Q`/`FY`）。デバッグ用に保持 |
| `CURRENT_PERIOD_START_DATE` | DATE | 当期開始日 |
| `CURRENT_PERIOD_END_DATE` | DATE | 当期終了日（ウィンドウ関数のORDER BY基準） |
| `CURRENT_FISCAL_YEAR_START_DATE` | DATE | 事業年度開始日（会計年度単位のPARTITION基準） |
| `CURRENT_FISCAL_YEAR_END_DATE` | DATE | 事業年度終了日 |
| `NET_SALES` | INT64 | **連結** 売上高（単独四半期値、円） |
| `OPERATING_PROFIT` | INT64 | **連結** 営業利益（単独四半期値、円） |
| `ORDINARY_PROFIT` | INT64 | **連結** 経常利益（単独四半期値、円） |
| `PROFIT` | INT64 | **連結** 当期純利益（単独四半期値、円） |
| `NON_CONSOLIDATED_NET_SALES` | INT64 | **非連結** 売上高（単独四半期値、円）。連結のみ開示企業はNULL |
| `NON_CONSOLIDATED_OPERATING_PROFIT` | INT64 | **非連結** 営業利益（単独四半期値、円） |
| `NON_CONSOLIDATED_ORDINARY_PROFIT` | INT64 | **非連結** 経常利益（単独四半期値、円） |
| `NON_CONSOLIDATED_PROFIT` | INT64 | **非連結** 当期純利益（単独四半期値、円） |

### ビュー定義の概要（累積→Q単独変換ロジック）

ビューは4ステップのCTE構成：

**Step 1: フィルタリング**
`TYPE_OF_CURRENT_PERIOD IN ('1Q', '2Q', '3Q', 'FY')` のみ対象（半期報告書等は除外）

**Step 2: 修正開示対応（重複除去）**
同一銘柄・事業年度・期区分で最新開示のみ残す：
```sql
PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE, TYPE_OF_CURRENT_PERIOD
ORDER BY DISCLOSED_DATE DESC, DISCLOSED_TIME DESC
```

**Step 3: 前期累計値をLAGで取得**
```sql
PARTITION BY LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE
ORDER BY CURRENT_PERIOD_END_DATE  -- 期末日順（1Q < 2Q < 3Q < FY）
```
- 半期報告企業（Q1/Q3なし）はprev=NULLになり COALESCE で0扱い
- 2Q standalone = 2Q累積そのまま（Q3=Bの方針に合致）

**Step 4: 単独四半期値の算出**
```sql
NET_SALES - COALESCE(_prev_net_sales, 0) AS NET_SALES
```
- 1Q: prev=NULL → value - 0 = value（累積=単独）
- 2Q/3Q: 累積 - 前期累積
- FY→4Q: FY累積 - 3Q累積

### サンプルデータ（LOCAL_CODE=1301、極洋）

| QUARTER | DISCLOSED_DATE | NET_SALES（連結） | OPERATING_PROFIT | PROFIT |
|---------|---------------|----------------:|----------------:|-------:|
| 4Q | 2016-05-09 | 226,626,000,000 | 2,433,000,000 | 1,799,000,000 |
| 1Q | 2016-08-05 | 52,206,000,000 | 467,000,000 | 551,000,000 |
| 2Q | 2016-11-04 | 57,364,000,000 | 704,000,000 | 635,000,000 |
| 3Q | 2017-02-10 | 70,405,000,000 | 1,701,000,000 | 1,263,000,000 |
| 4Q | 2017-05-11 | 56,586,000,000 | 851,000,000 | -27,000,000 |

**値域・NULL状況の注意点**:
- 金額単位は**円**（百万円単位ではない）。サンプルで2,200億円規模が確認できる
- 連結のみ開示企業の `NON_CONSOLIDATED_*` カラムはNULL（サンプルの1Q〜3QはNULL）
- 4Q（FY）では非連結も開示される場合がある（サンプルの4Q=228,083,000,000）
- 推定行数0は統計情報が未更新のため（実データは存在する）

### クエリ例（前年同期比計算）

```sql
WITH cur AS (
  SELECT * FROM `STOCK.v_fin_summary_actual_for_q_on_q`
  WHERE CURRENT_FISCAL_YEAR_START_DATE >= '2023-04-01'
),
prev AS (
  SELECT * FROM `STOCK.v_fin_summary_actual_for_q_on_q`
  WHERE CURRENT_FISCAL_YEAR_START_DATE >= '2022-04-01'
    AND CURRENT_FISCAL_YEAR_START_DATE < '2023-04-01'
)
SELECT
  cur.LOCAL_CODE,
  cur.QUARTER,
  cur.DISCLOSED_DATE,
  cur.PROFIT AS cur_profit,
  prev.PROFIT AS prev_profit,
  SAFE_DIVIDE(cur.PROFIT - prev.PROFIT, ABS(prev.PROFIT)) AS profit_yoy_ratio
FROM cur
JOIN prev
  ON cur.LOCAL_CODE = prev.LOCAL_CODE
  AND cur.QUARTER = prev.QUARTER
```

## J-Quants 利用可能エンドポイント（Standard プラン）

| エンドポイント | 用途 |
|---------------|------|
| `/v2/fins/summary` | 決算短信サマリー（メイン） |
| `/v2/equities/earnings-calendar` | 発表スケジュール（3月・9月期のみ） |

Premium 限定（現状不使用）: `/v2/fins/details`（BS/PL/CF詳細）、`/v2/fins/dividend`（配当詳細）

---

## プロジェクト全体設計

### フェーズ設計

| フェーズ | 内容 | 状態 |
|---------|------|------|
| Phase 1: モデル構築 | 全決算対象。月曜決算→火曜株価騰落で毎日蓄積 | 進行中 |
| Phase 2: ツール化 | ザラ場監視・自社株買いリアルタイム検知 | **実装済み** → `docs/knowledges/tools/066_zaraba_tool.md` |

- Phase 2 のザラ場監視: `scripts/zaraba_earnings.py` として実装済み。`/v2/fins/summary` の `DiscDate`/`DiscTime` をポーリング → 新規 DiscNo = ザラ場決算
- `eq-earnings-cal` はスケジュール把握の補助（3月・9月期のみ対応）

### ザラ場ツールとの連携

→ 詳細: `docs/knowledges/tools/066_zaraba_tool.md`

ザラ場ツール（`scripts/zaraba_earnings.py`）のスコアリング因子は本 EDA で検証済みの因子を流用。答え合わせ・因子改善は本 predict notebook に統合する。

| 役割 | 担当 |
|------|------|
| 因子の研究・EDA・検証・答え合わせ・精度集計 | predict notebook（本ノートブック） |
| リアルタイムスコアリング実行 | ザラ場ツール |

ザラバ決算と引け後決算は `is_intraday` フラグで区別し、答え合わせ指標を分ける:
- ザラバ: 当日終値 vs 前日終値
- 引け後: 翌日終値 vs 発表日終値

### モデル設計の重要原則

1. **分析対象は決算実績発表のみ**（`DocType` に `FinancialStatements` を含むもの）。業績修正・配当修正は分析対象外
2. **業績修正/配当修正は「事前フラグ」特徴量**として使う。事前に修正があると織り込み済みの可能性がある
3. **YoYとコンセンサスサプライズは別の情報**。YoYが良くてもコンセンサスを下回れば売られるし、逆もある（ただし F4 コンセンサスは現在一時除外中）
4. **ザラ場/引け後は特徴量ではない**。引け後決算について翌日どう動くかを検証するのが本筋
5. **同時アナウンス（自社株買い・分割・優待）は決算と同日のもののみ**。事前のものは対象外
6. **コンセンサス（STOCK.CONSENSUS）は百万円単位**。fin_summary（円単位）と比較時は `* 1_000_000` で変換必須

### コンセンサス因子除外の EDA 検証結果（2026-04-04）

案A（F4コンセンサス除外、5因子）vs 現行（6因子）の比較:

| 指標 | 6因子（旧） | 5因子・案A | 差分 |
|------|-----------|-----------|------|
| 全銘柄 active correlation | 0.2401 | **0.2666** | +0.027 |
| 大型株 active correlation | 0.2074 | **0.2504** | +0.043 |

案B（F5 ウェイト ±2→±3 増加）も検証したが案Aより劣化 → 棄却。

**結論**: STOCK.CONSENSUS の過去データが疎（蓄積開始が最近）であるため、バックテスト期間ではノイズとして作用。除外で全指標改善。データ蓄積後に再評価予定。

### クラスタ別精度分析（残差相関モデル × 5因子スコアリング）

**分析日**: 2026-04-04
**対象期間**: 2024-01-01 〜 2026-04-03（決算31,802件、うちクラスタ付き大型株3,690件）
**クラスタソース**: `docs/knowledges/analysis/010_factor_model_residual_corr.md`（clusters_2024/2025.csv）
**結合率**: 11.6%（時価総額500億以上のみ。中小型は評価外）

**全体精度**: 方向一致率 0.407、スコア×リターン相関 0.176

| クラスタ | 件数 | 方向一致率 | 相関 | 代表業種 | 備考 |
|---------|------|-----------|------|---------|------|
| 4 | 182 | 0.484 | **0.338** | 食料品 | Top: 5因子が最も効く |
| 5 | 175 | 0.377 | 0.256 | 情報・通信業 | |
| 13 | 252 | 0.417 | 0.247 | 食料品 | |
| 20 | 272 | 0.434 | 0.236 | 情報・通信業 | |
| 6 | 180 | 0.461 | 0.223 | 化学 | |
| 9 | 109 | 0.394 | 0.062 | 小売業 | Worst: 別因子が必要 |
| 1 | 120 | 0.367 | 0.078 | 食料品 | |

**所見**:
- 全20クラスタで正の相関（0.062〜0.338）→ 5因子は全般的に有効
- 食料品・情報通信クラスタで相関が高い → predict運用で優先的に活用可能
- 小売クラスタは相関が低い → スコアリング閾値調整 or 追加因子が必要

**結果CSV**: `C:\tmp\earnings_model_cache\cluster_accuracy_analysis.csv`

### 未実装の因子候補

- 上方修正後 vs コンセンサス比
- 配当 vs 四季報予想
- 日経観測記事フラグ（手動入力）
- 四半期バイアス（Q1〜Q4）
- セクターバイアス
- 発表順位（当日の何番目か）
- ~~残差相関クラスタ同業ピア決算シグナル~~ → **検証済み・不採用**（2026-04-04）。全体IC=0.018(p=0.277)で有意でない。クラスタ別に連鎖(C2,C3)と織り込み(C6,C7)が混在し打ち消し合う。スコアに組み込むと全指標悪化

### 因子発見方法論

1. EDA（翌日リターン分布を各軸で可視化）
2. 単変量ICスクリーニング
3. モデル残差分析（大きく外した事例から新因子を発見）
4. 木系モデルの特徴量重要度
5. 学術論文（PEAD、SUE）
6. 実経験からの仮説 → EDA → 統計検定サイクル

---

## 修正履歴（主要なもの）

| 日付 | 対象 | 内容 |
|------|------|------|
| 2026-04-04 | EDA | dedup バグ修正: yoy/prev_year_q 結合キーに `CurPerType` が欠落し [Code, DiscDate] のみで結合 → 行爆発（31K→94K）。結合キーに CurPerType 追加 + safety dedup で解消 |
| 2026-04-04 | EDA | セル1（Setup）とセル2（Config）の順序を入れ替え（Setup を先に） |
| 2026-04-04 | EDA | セル20-22追加: 残差相関クラスタラベル結合 + クラスタ別精度分析 |
| 2026-04-04 | predict | F4コンセンサス因子をコメントアウト（TODO: データ蓄積後に再有効化） |
| 2026-04-06 | predict | F4コンセンサス因子を復活（段階的スコアに変更、データあり銘柄のみ適用） |
| 2026-04-08 | predict | Cell 9 初期化ガードに `import matplotlib` 追加（Step 3→4実行時の NameError 修正） |

### モデル改善候補（答え合わせから得た知見）

| 日付 | 観察 | 改善案 | ステータス |
|------|------|--------|----------|
| 2026-04-08 | 三協立山(5932): 下方修正-75%でDOWN予測→実際+3.3%。超低PBR銘柄で下方硬直性 | PBR/バリュエーション下限ファクター追加を検討 | 未着手 |
| 2026-04-08 | SLIGHT_DOWN予測の累積精度20%、平均リターン+2.2%と予測と逆方向 | スコア-1帯の判定ロジック見直し | 未着手 |
| 2026-04-08 | NEUTRAL予測の累積精度14%、パルGHD(YoY OP+76%)を取り逃し | コンセ乖離の負方向相殺が強すぎる可能性 | 未着手 |
| 2026-04-14 | 5246 ELEMENTS(1Q): score=+1 NEUTRAL予測→+15.8%。高成長・毎Q赤字の銘柄が「売上高成長維持＋黒字転換」で急騰。現行スコアリングは黒転イベントを捉えていない | **黒字転換ファクター追加**: 前Qまで連続赤字 × 当Q黒字化（`op_profit_prev<0 AND op_profit_curr>0`）で加点。売上成長維持（YoY売上>0）を条件に追加すると精度向上が見込める | 未着手 |
| 2026-04-14 | 6505 東洋電機製造(3Q): score=+1 NEUTRAL予測→+15.1%。経常利益YoY+132%・売上YoY+7.6%。前Qが前年同Q比で利益半減と低調で期待薄だったため、当Qサプライズ幅が増幅した | **期待ベースライン調整**: 直近1-2Qの利益YoYが大きくマイナスだった銘柄（低期待）で当Q好決算を出した場合、サプライズ加点を増幅する。`prev_q_yoy_op<-0.3` かつ `curr_yoy_op>0.5` で追加加点 | 未着手 |
| 2026-04-14 | 6217 津田駒工業(1Q ザラバ): score=-3 DOWN予測→+18.0%（大外し）。万年大赤字企業で1Q赤字ながら赤字幅縮小。継続企業の前提に関する疑義注記付き銘柄の解消期待で急騰 | **疑義注記フラグ + 赤字幅縮小ファクター**: (1) 有報で継続企業の前提に関する疑義注記の有無をフラグ化（EDINET XBRL or TDnet本文から抽出）、(2) 赤字銘柄の前年同期比赤字幅縮小（`op_profit<0 AND prev_op_profit<0 AND abs(op_profit)<abs(prev_op_profit)` で加点）。疑義注記フラグ付き銘柄は絶対値赤字でもDOWN判定しない（需給が特殊） | 未着手 |
| 2026-04-14 | 3168 MERF(2Q): score=+2 UP予測→-11.9%（大外し）。TOB期待で決算前に買い上がられていた銘柄。決算発表でTOBなしが確定し期待剥奪で売られた（好決算でも売り優勢） | **期待織り込み済み減衰ファクター**: 決算発表前N日（例: 20営業日）の騰落率が高い銘柄はUPスコアを減衰。特にTOB/M&A観測銘柄（SNS・掲示板で話題化、短期出来高急増）はフラグ化して別扱い。`pre_earnings_return_20d>+X%` の銘柄はUP判定を NEUTRAL化 | 未着手 |
| 2026-04-14 | 7516 コーナン商事(FY): score=+1 NEUTRAL予測→-3.7%（方向外し）。現モデルのコンセ乖離は「今期実績20,754 / 今期コンセ19,800 = +4.82%」で上振れ判定。実態は「来期会社予想21,000 / 来期コンセ22,500 = -6.67%」で下振れ。FY発表では来期ガイダンスの織り込み済みコンセ比較が市場反応を支配する | **FY用コンセ乖離の計算変更**: FY発表時は `NxFOdP / CONSENSUS(TARGET='NEXT', QUARTER='FY') - 1` を使う。1Q/2Q/3Q は `curr_actual / CURRENT_consensus`（現行維持）。`cons_map` を `(ticker, quarter, target)` キーに変更 | **修正済（2026-04-14）**: `earnings_model_predict.ipynb` Cell5 を修正。次回予測から反映 |

## 既知の障害と再発防止策

### BQ クエリのバッククォートエスケープ問題（2026-04-10 修正）

**症状**: Cell[8]（データ取得+特徴量生成）が `BadRequest: 400 Syntax error: Unexpected "¥" at [2:10]` で失敗。

**原因**: 因子8（記念配当/特別配当）の BQ 過去データパスで、f-string 内のバッククォートが `\`` とエスケープされていた。

```python
# NG: エスケープされたバッククォート（Linux/Colab で ¥ に化ける）
_q_special = f"""SELECT DISTINCT TICKER
    FROM \`gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED\`
    ..."""

# OK: Python f-string ではバッククォートのエスケープは不要
_q_special = f"""SELECT DISTINCT TICKER
    FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
    ..."""
```

**根本原因**: Windows（cp932）ではバックスラッシュ `\` と円記号 `¥` が同一コードポイント（0x5C）だが、Linux/Colab（UTF-8）では `\`（U+005C）と `¥`（U+00A5）は別文字。ノートブック編集時にエスケープが混入し、Windows では動作するが Colab では BQ がパースに失敗する。

**再発防止ルール**:
1. **Python f-string / triple-quoted string 内のバッククォートはエスケープしない**。Python の文字列リテラルではバッククォート `` ` `` は特殊文字ではないのでエスケープ不要
2. ノートブック修正後に `\`` がソースに混入していないか確認: `grep '\\\\' *.ipynb | grep backtick` 相当のチェック
3. Colab でのみ発生するため、**Colab で全セル実行テストを行う**（Windows ローカルでは検出できない）

## Colab Notebooks コピー（自動化済み）

PostToolUseフック (`scripts/sync_colab_notebooks.py`) で NotebookEdit/Write 後に自動コピーされる。
対象ノートブックを追加する場合は `COLAB_SYNC_MAP` に追記。
