---
name: 決算反応モデル EDA ノートブック
description: 決算発表銘柄の株価反応予測モデル（決算内容・バリュエーション・需給等を総合判断）のEDAノートブック構成・データフロー・GCS保存先
type: tools
作成日: 2026-03-29
更新日: 2026-05-13
ステータス: 有効
関連ファイル:
  - scripts/earnings_model/earnings_model_eda.ipynb
  - scripts/earnings_model/predict.py
  - scripts/earnings_model/earnings_model_core.py
  - scripts/earnings_model/download_review_data.py
  - scripts/earnings_model/review_report.py
  - scripts/earnings_model/hanseikai_summary.py
  - scripts/earnings_model/show_prediction.py
  - scripts/earnings_model/hanseikai_notebook.ipynb
---

## 概要

決算発表銘柄の翌日株価騰落率を予測するモデル構築用 EDA ノートブック。
決算内容（進捗率・修正・YoY等）に限らず、バリュエーション（PER/PBR/PEG）・需給（自社株買い・増配）・株価水準（過去騰落・織り込み度合い）等も因子として取り込む設計。
過去決算データの特徴量計算・ラベル生成・単変量IC検証・セクター分析を行う。

**実行環境**: Google Colab / Windows ローカル（RUNTIME 自動判定）

### 予測 & 答え合わせ CLI

`scripts/earnings_model/predict.py` — EDA で設計した特徴量・スコアリングを使い、当日のザラバ決算 + 引け後決算を一括スコアリング。`is_intraday` フラグで区別し、答え合わせの基準を切り替える。

- **ザラバ銘柄** (`is_intraday=True`): 当日終値 vs 前日終値（当日中に答え合わせ可能）
- **引け後銘柄** (`is_intraday=False`): 翌日(`ACTUAL_DATE`)終値 vs 当日終値

#### predict.py サブコマンド

> **日付の意味**: 全サブコマンドで日付は**答え合わせ日（actual_date = 株価反応を確認する日）**を基準とする。内部で前営業日（predict_date = 決算発表日）を自動算出する。

```bash
# 今日の答え合わせ（前営業日を BQ から自動算出して predict + answer を連続実行）
PYTHONUTF8=1 python scripts/earnings_model/predict.py today --actual-date 20260508

# 日次予測（当日）
PYTHONUTF8=1 python scripts/earnings_model/predict.py predict --date 20260505

# 答え合わせ（翌営業日に実行）
PYTHONUTF8=1 python scripts/earnings_model/predict.py answer --date 20260505 --actual-date 20260506

# 過去バッチ再実行（答え合わせ日の範囲指定）
PYTHONUTF8=1 python scripts/earnings_model/predict.py backfill --from 20260501 --to 20260508

# リビルド（GCS既存全期間を現行ロジックで再構築。--from --to 省略）
PYTHONUTF8=1 python scripts/earnings_model/predict.py backfill

# 精度集計（GCS全期間）
PYTHONUTF8=1 python scripts/earnings_model/predict.py accuracy
```

#### PS メニュー連携

`C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1` のメニュー項目「決算反応予測（predict + 答え合わせ）」から対話的に実行可能。

| Step | 画面 | 選択肢 |
|------|------|--------|
| 1 | モード選択 | `Enter`=default（日次）/ `rbld`=リビルド（GCS全期間再構築） |
| 2 | 日付選択（defaultのみ） | `t`=今日（前営業日を自動算出）/ `range`=期間指定 |
| 3 | 期間入力（rangeのみ） | `YYYYMMDD YYYYMMDD`（答え合わせ日の from to） |

- `default` + `t`: `today --actual-date <今日>` を実行（BQ で前営業日を自動算出 → predict + answer 連続実行）
- `default` + `range`: `backfill --from <開始答え合わせ日> --to <終了答え合わせ日>` を実行
- `rbld`: `backfill`（引数なし）を実行

#### GCS blob 命名規約（`_resolve_backfill_range` が依存）

`backfill` 引数省略時の日付範囲自動算出は、GCS 上の blob 名から日付を抽出する。以下の命名規約を変更する場合は `_resolve_backfill_range` のパースロジックも同時に修正すること。

| prefix | 命名パターン | 日付位置 |
|--------|-------------|---------|
| `earnings_model/earnings_reaction_predictions/` | `prediction_YYYYMMDD_HHMMSS.json` | `split("_")[1]` |
| `earnings_model/earnings_reaction_actuals/` | `actual_YYYYMMDD_HHMMSS_for_YYYYMMDD.json` | `"for_"` 以降 |

#### データ取得方式（モード別）

| モード | CONSENSUS取得 | 理由 |
|--------|--------------|------|
| `predict`（単日） | `V_CONSENSUS_MERGED` VIEW | 当日最新1回で十分 |
| `backfill`（複数日） | CONSENSUS 1-pass取得 + pandas as-of フィルタ | BQコスト最小化 |

#### TDnet フォールバック（3段）

yanoshin API → TDnet HTML スクレイピング → BQ。特別配当・自社株買い・株式分割の検出に使用

#### スコアリング因子クイックリファレンス（コード実装準拠）

> **運用ルール**: このテーブルは `earnings_model_core.py` の `compute_score()` 関数と**常に一致**させること。因子の追加・閾値変更・ウェイト変更・廃止を core.py に反映したら、このテーブルも同時に更新する。反省会ではこのテーブルだけ見ればスコア分解できる状態を維持する。

**最終更新: 2026-05-13**

| # | 因子 | 発火条件(+) | Score(+) | 発火条件(-) | Score(-) | 対象Q | データソース |
|---|------|------------|----------|------------|----------|-------|-------------|
| 1 | 進捗率 | >期待×1.2 | +1 | <期待×0.8 | -1 | 1Q/2Q/3Q | J-Quants OP÷FOP |
| 2 | ガイダンス修正 | 上方 | +1 | 下方 | -1 | 全Q | J-Quants FOP vs BQ前回FOP |
| 3 | YoY OP | >+30% | +1 | <-30% | -1 | 全Q | BQ v_fin_summary_actual_for_q_on_q。**is_low_profit時は無効化**。**当期業績Grp** |
| 4 | コンセンサス乖離 | >+10%/+5%/+0% | +3/+2/+1 | <0%/<-5%/<-10%/<-20%/<-30% | -1/-2/-3/-4/-5 | 全Q | BQ CONSENSUS v4（V_CONSENSUS_MERGED）。F4a=ORD_PROFIT乖離、F4b=NET_PROFIT乖離。max(\|F4a\|,\|F4b\|)の符号付き値を採用（二重加算回避）。IFRS企業: OdP欠損時F4aスキップ（OPフォールバック廃止）。FY時は翌期予想vs翌期コンセのみ（当期フォールバック廃止） |
| 5b | 翌期EPS見通し（主） | >+10% | +2 | <-10% | -2 | FYのみ | J-Quants NxFEPS vs EPS。F14発火時は無効化。**来期見通しGrp** |
| 5a | 翌期OP見通し（従） | >+10%（F5bと同符号時のみ） | +1 | <-10%（F5bと同符号時のみ） | -1 | FYのみ | J-Quants NxFOP vs OP。低ベース時は5年中央値比較。F5bと矛盾時（符号逆）は無効化。**来期見通しGrp** |
| 6 | 出尽くしリスク | — | — | 進捗>90% + 据え置き | -2 | 3Qのみ | F1+F2の組合せ |
| 7 | 成長加速/減速 | gap>+20pt | +1 | gap<-20pt | -1 | FYのみ | BQ 過去FY **通期OP** YoY median。**is_low_profit時は無効化**。**来期見通しGrp** |
| 8 | 記念配当/特別配当 | TDnet TITLE一致 | +1 | — | — | 全Q | yanoshin API / TDnet HTML / BQ |
| 9 | ~~テーマブースト~~ | 廃止 | — | — | — | — | βでは個人投資家関心度を代理できず廃止（066と同期） |
| 10 | 自社株買い | TDnet TITLE "自己株式の取得" | +2 | — | — | 全Q | yanoshin API / TDnet HTML / BQ |
| 11 | 増配/減配 | FDivAnn vs 前回 >+5% | +1 | <-5%→-2, <-20%→-3 | -2/-3 | 全Q | J-Quants FDivAnn vs BQ前回DPS （※F14発火時は無効化） |
| 12 | PER割安度(PEG) | PEG<0.5→+2, <1.0→+1 | +1/+2 | PEG>2.0→-1, >5.0→-2, >10.0→-3 | -1/-2/-3 | **全Q** | FY: 来期OP成長率、非FY: YoY OP成長率で代替 （※F14発火時は無効化）。**FY→来期見通しGrp / 非FY→当期業績Grp** |
| 13 | QoQ OP急変 | 前Q比>+50% | +1 | 前Q比<-50% | -2 | 全Q | BQ v_fin_summary_actual_for_q_on_q。**is_low_profit時は無効化** |
| 14 | 株式分割 | TDnet TITLE "株式分割" | +1 | — | — | 全Q | yanoshin / TDnet HTML / BQ |
| 15 | 黒字転換サプライズ | 前年同期赤字→今期黒字 | +2 (YoYあり) / +1 | — | — | 全Q | BQ v_fin_summary_actual_for_q_on_q (prev_year_op < 0 & cur_standalone_op > 0)。**当期業績Grp** |
| 16 | FY予想未達ペナルティ | — | — | 達成率<80%→-1, <65%→-2 | -1/-2 | FYのみ | J-Quants OP÷FOP |

> **F14注記**: F14発火時はF11(増配/減配)・F12(PEG)・F5b(EPS成長率)を無効化（暫定措置、分割比率未調整のため）
> **低ベースフィルタ**: FY時、今期OPが5年**通期**中央値OPの50%未満→低ベースフラグ。F5aの比較対象を「今期→来期」から「5年中央値→来期」に差替え（2026-05-13修正: median_5y_opを通期OPに修正）
> **低利益率フィルタ (is_low_profit)**: 通期OP中央値 < 5億円 → F3/F7/F13を無効化。閾値は`LOW_PROFIT_THRESHOLD`定数（EDA調整予定）
> **グループキャップ（二重加算防止）**: 同一経済的事実を複数因子が独立にスコア加算する問題を防止。F4の`max(|a|,|b|)`と同じ思想。
> - **来期見通しGrp** (FY): F5a+F5b+F7+F12(FY) 合計を **±3** にclamp。`GUIDANCE_CAP`定数。キャップ発火時はreasonsに明記
> - **当期業績Grp** (全Q): F3+F12(非FY)+F15 合計を **±3** にclamp。`PERFORMANCE_CAP`定数。キャップ発火時はreasonsに明記
> - F5非開示ペナルティ(-1)はグループ外（`score`直接加算）。「ガイダンスの不在」は内容評価と別次元

**スコア→予測**: `>=2` = UP / `-1〜1` = NEUTRAL / `<=-2` = DOWN

---

#### 因子改善TODO（優先度順）

> **運用ルール**: 反省会で出た改善候補の一元管理場所。反省会ログの詳細はそのまま残し、ここには優先度・ステータス・次アクションのみ集約。

| # | 名称 | 優先度 | 起点 | ステータス | 次アクション |
|---|------|--------|------|----------|------------|
| 1 | 低ベース効果フィルタ | **最優先** | 4307 NRI, 9551 メタウォーター | **実装済 (2026-05-03)** | predict Cell5: 5年中央値OP算出、FY+低ベース時にF5比較対象を中央値に差替え |
| 2 | FY予想未達ペナルティ (F16) | **高** | 8793 NECキャピタル | **実装済 (2026-05-03)** | predict Cell7: F16追加。達成率<80%→-1, <65%→-2 |
| 3 | IFRS OdP→OPフォールバック | **高** | 6702富士通, 6701NEC, 6902デンソー他8社 | **廃止 (2026-05-12)** | OPフォールバック廃止→OdP欠損時F4aスキップ。F4bのNET_PROFITで補完 |
| 4 | F16 事前期待並みペナルティ | 中 | 4733 OBC, 1959 クラフティア | 設計中 | サンプル蓄積中。コンセ近辺(±7%)で来期成長継続→市場が許容するパターン。閾値調整 or 連動ルールをEDA |
| 5 | 寄り天フェード（事前織込み因子） | 中 | 5243 note, 277A, 5576, 6858 | 設計中(4例) | EDA: 事前5日/20日/前回決算後 の3タイムスパンIC比較。市場区分×時価総額の交互作用 |
| 6 | F2 Qタイミング修飾子 | 中 | 7751 キヤノン | 設計済 | EDA: 1Qでの下方修正の翌日リターン vs 3Q/FYの差。N数確認 |
| 7 | F_margin ガイダンスマージン変化 | 中〜高 | 6723 ルネサス | 設計済 | EDA: guided_margin - current_margin のIC確認 |
| 8 | コンセNaN時YoY補正 | 中 | 2737 トーメンデバイス | 設計済 | EDA: コンセNaN & YoY>100% のN数・リターン |
| 9 | F15 黒字転換サプライズ | 低〜中 | 3994 マネフォ | **実装済 (2026-05-03)** | predict Cell5/7: 前年同期赤字→今期黒字で+2/+1。qoq_mapにis_turnaround追加 |
| 10 | F15 株主優待新設 | 低 | 3994 マネフォ | 構想のみ | TDnetタイトル検索で取得可能。N数要確認 |
| 11 | 来期未開示大型株補正 | 低 | 6861 キーエンス | 構想のみ | F5無効ペナルティ緩和。構造的盲点だが対象少数 |
| 12 | ~~F3 FY期yoy_opを通期OPベースに変更~~ → 取り下げ | — | 7951 ヤマハ (5/11) | **却下 (5/13)** | ユーザー判断: F3は「四半期単独OPの前年同期比」で全Q統一。FY期だけ通期に変えると因子の意味が変わる。異常値は#15の低ベースガードで対処 |
| 15 | median_5y_op 4Q単独→通期OP修正 + is_low_profit | **最高** | 5449 大阪製鐵 (5/12) | **実装済 (2026-05-13)** | P0-1: median_5y_op通期化。P1-2: `is_low_profit`(median<5億)でF3/F7/F13無効化 |
| 16 | F5 EPS主因子/OP従因子分離 | **高** | 6644 大崎電気工業 (5/12) | **実装済 (2026-05-13)** | P1-3: F5b(EPS主)+F5a(OP従)。EPS減益時OP増益を無効化。旧max(OP,EPS)廃止 |
| 17 | 相関因子グループキャップ | **高** | レビュー167重大#1 (5/13) | **Codex実装待ち** | 来期見通しGrp(F5a±1縮小+F5b+F7+F12(FY))cap±3。当期業績Grp(F3+F12非FY+F15)cap±3。F5非開示はGrp外。キャップ発火時reasons追記。プラン: `tools-059_group_cap_20260513_201511.md` |
| 13 | 還元強化因子 | 中 | 5445 東京鐵鋼 (5/7) | 構想 | 配当性向方針変更・DOE導入・累進配当宣言・自社株買い枠拡大等を包括的に捕捉。F10/F11は実績値ベースだが本因子は「方針変更」を検知。逆方向（還元減退）は理論上あるが実例少。データソース: TDnet短信テキスト or Gemini抽出 |
| 14 | 個人投資家関心度マーキング | 中 | F9テーマブースト廃止 | 構想 | 旧F9(β×TOPIX+)は廃止。別指標（出来高急増・信用買残変化・SNS言及数等）を検討。066側TODOと同期 |
| J | F5 EPS成長率追加 | **最高** | 4362 日本精化 | **実装済 (2026-05-03)** | predict Cell5/7: NxFEPS/EPSからEPS成長率算出、F5でOP/EPSの大きい方を使用。F14発火時はEPS無効化 |
| I | F12 PEG全Q拡張+下方強化 | **高** | 4479 マクアケ | **実装済 (2026-05-03)** | predict Cell7: FY制約撤廃、非FYはYoY OPで代替。PEG>5→-2, >10→-3 追加 |

**サンプル蓄積待ち案件**（対策案は出ているがN不足で検証不可）:
- F4×FY×4Q好調問題（1959クラフティア型）: FY発表時に累積コンセ未達-5%〜-10%で-2が過剰反応の疑い。**累積コンセ未達は3Qまでのビハインド引きずりであり、4Q単独が好調なら市場は「成長モメンタム回復」と評価して上昇する**。サンプル蓄積中（現2例、3-4例でEDA）
  - 事例: 1959クラフティア (コンセ未達93%/来期+8%/Q4 YoY+52% → score-2 DOWN → 実際+6.1%)
  - 事例: 1942関電工 (コンセ未達96%/来期+9%/Q4 YoY+50% → score 0 NEUTRAL → 実際+13.6%)
  - **対策A案: F4をFY時に「4Q単独 vs コンセ4Q推定値」で判定**
    - コンセンサスは通期ベースで4Q単独を直接提供しない
    - 類推アプローチ: `コンセ4Q推定 = 通期コンセ - 3Q累積実績`（3Q実績は既知）
    - メリット: 4Q単独の市場期待との差分を直接測れる。本質的
    - データ: 3Q累積実績はBQ `fin_summary` から取得可能（前回発表の3Q OP）
  - **対策B案: FY×4Q好調時にF4減点を緩和（相殺ルール）**
    - 条件: `FY かつ Q4 YoY > +30% かつ F4 ∈ [-1, -2]` → F4スコアを+1緩和
    - メリット: 実装容易。既存データのみで完結
    - リスク: サンプル少ない段階で入れると過学習
- 小型株(時価総額100億未満)ノイズ: 5903シンポ(+24.7%), 3439三ツ知(+2.6%), 3622ネットイヤー(-4.4%)。フィルタ or ウェイト低減を検討

---

#### 設計ノート

**F3 廃止候補**: 前年が異常値なら YoY が歪む。F13（QoQ OP急変）で代替予定。EDA検証で除去影響を確認後に正式廃止。

**F9 テーマブースト設計意図**: 20日βを「個人投資家人気」の代理変数として使用。β>1.0 の銘柄は個人短期売買が集中しやすく、地合い+（TOPIX+）の日に決算サプライズがあると追随買いで反応が増幅。日次更新可能な実用的近似値として採用（EDA検証 p=0.009 有意）。**βキャッシュ**: `beta-calc` Job（日次 18:30 JST）→ `gs://stock_data_1930932/earnings_model/zaraba_beta_20d/beta_20d.csv`

**F12 PER割安度の設計根拠**: バフェットコード記事（`docs/references/web/20260413_buffett_code_per_by_growth_rate.md`）より。安定成長銘柄で `適正PER ≒ 21.3 × EPS成長率 + 14`。PEGレシオ1倍以下が割安水準。決定係数は低いため閾値は保守的に設定。

**スコア→予測**: `>=2`=UP, `-1〜1`=NEUTRAL, `<=-2`=DOWN（2026-04-09変更。SLIGHT系は方向一致率25-40%と機能せず3段階に統合）

**F4 コンセンサス乖離バグ（2026-04-10修正済み）**: `TARGET='CURRENT'` フィルタと `PARTITION BY TICKER, QUARTER` 追加。修正前は NEXT（来期予想）や別QUARTERのコンセンサスが混入し異常乖離率が発生（例: セブン&アイ FY で 1Q コンセと比較→+580%）。

#### 共通ロジックモジュール（earnings_model_core.py）

`scripts/earnings_model/earnings_model_core.py` — predict ノートブックと batch_rerun の**共通ロジックを一元管理**（2026-05-03 新規）。因子追加・閾値変更は core.py を1箇所修正するだけで両方に反映される。

**提供する関数・定数**:

| エクスポート | 種別 | 用途 |
|-------------|------|------|
| `compute_score(row)` | 関数 | 16因子スコアリング（F1-F16） |
| `score_to_prediction(score)` | 関数 | スコア→UP/NEUTRAL/DOWN変換 |
| `classify_return(ret)` | 関数 | 実績リターン→UP/NEUTRAL/DOWN分類 |
| `PRED_COLUMNS` | 定数 | GCS prediction JSON の保存カラム一覧（34列） |
| `Q_MAP`, `CUM_PREV_Q`, `PREV_Q_MAP` | 定数 | 四半期マッピング |

**import 方法**: ノートブック Cell 1 で pip install、Cell 2 で Drive マウント + `sys.path` 設定（`%pip` が `sys.path` をリセットするためこの順序が必須）、Cell 4 で `from earnings_model_core import ...`。batch_rerun は冒頭で同様に import。

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

> **TODO（TVF化）**: 現在 CONSENSUS は `SOURCE = 'RAKU'` 固定で参照しているが、IFIS データも as-of で IFIS 優先マージするには BQ TVF `fn_consensus_merged_asof(date)` の作成が必要。VIEW `V_CONSENSUS_MERGED` は最新時点のみのため as-of 非対応。

**書き込み規則（上書きしない）**

- `prediction_{PREDICT_DATE}_{HHMMSS}.json` ／ `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json` を新規 timestamp で追加保存
- `accuracy_summary.json` は全 actual を再集約（古い predict_date もファイル名ソートで新しいものが残る）→ **DATE_PAIRS に含めなかった日（例: 04/14）の既存 actual も集計に含まれる**
- 04/14 の既存 predictions/actuals を読み書きしないため、「正しい既存結果を保持したまま他日を再実行」が可能

**スコアリングロジック**: `compute_score()` は `earnings_model_predict.ipynb` Cell7 と**完全一致**する必要がある。ノートブックの因子を変更したらこのスクリプトも同時更新する。

**ユースケース**: 因子追加・バグ修正後の過去分一括再生成 / 翌営業日株価が揃ってからの答え合わせ一括取り直し / accuracy_summary カラム拡張。

#### 反省会の運用手順（3ステップ）

ユーザーが「決算答え合わせ 反省会 PREDICT_DATE = 'YYYYMMDD'」と指示したら、**以下を順に実行**。ブック実行済み＝ノートブックのStep1-3は完了済みなのでGCSにデータあり。

```bash
# Step 1: GCS → ローカルDL（prediction + actual）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/download_review_data.py YYYYMMDD

# Step 2: レポート生成（CSV + MD）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/review_report.py YYYYMMDD --out-dir C:/tmp/earnings_review

# Step 3: MD を Read して分析・報告
# → C:/tmp/earnings_review/earnings_review_YYYYMMDD.md
```

**ワンコマンド代替（推奨）**: 上記3ステップを一発で実行し、Claude が直接読めるコンパクトなサマリーを stdout に出力するスクリプト:

```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
    scripts/earnings_model/hanseikai_summary.py YYYYMMDD
```

DL → CSV/MD生成 → 構造化サマリー（`C:/tmp/earnings_review/summary_YYYYMMDD.md`）を出力。3区分（当たり/中立/ハズレ）セクション見出し + 7列 Markdown テーブル（Ticker/銘柄/Q/score/予測→実績/ポジ/ネガ）。

**ブラウザ表示（推奨）**: `scripts/earnings_model/hanseikai_notebook.ipynb` を JupyterLab で開き、`PREDICT_DATE` を設定して全セル実行。DL → レポート生成 → Markdown テーブルをブラウザ上で表示。JupyterLab パス: `C:\Users\zonekun\AppData\Local\Programs\Python\Python312\Scripts\jupyter-lab.exe`。Colab コピー: `G:\マイドライブ\Colab Notebooks\hanseikai_notebook.ipynb`。

**出力MD構造**: 結果(当たり/中立/ハズレ) × score降順。列: Ticker/銘柄/Q/score/予測→実績/ポジ/ネガ + カテゴリ別平均リターン・方向一致率

**反省会で報告すべき内容**:
1. サマリー（件数・当たり/ハズレ内訳・方向一致率・相関）
2. 大当たり: |score|≥3 かつ direction_match=True → 効いた因子を称賛
3. 大外し: |score|≥2 かつ direction_match=False → 原因仮説を立てる
4. NEUTRAL取りこぼし: score∈[-1,1] かつ |actual_return|≥5% → なぜ拾えなかったか
5. 因子別パフォーマンス評価
6. 改善候補の提案

**銘柄別ログの検証ルール**:
- ユーザーがコメント・議論した銘柄 → 区分に「✓」を付与（検証済み）
- Claudeが自動生成した考察のみ → 無印（未検証）
- **テーマ・TODOとして採用するのは✓付き案件から導出されたもののみ**。無印の考察は仮説扱いで、改善方針の根拠にしない

**銘柄ごとの因子分解**: `show_prediction.py <DATE> <TICKER...> [--with-actual]`（ローカル優先、無ければGCS自動フォールバック）。全因子を整形表示。`--with-actual` で実績リターン・方向一致も併記。

#### 反省会ログ

##### 2026-04-13（対象: 4/10発表分、84銘柄、方向一致率36.9%、相関0.214）

| 銘柄 | Score | 予測 | 実績 | 結果 | 教訓 |
|-------|-------|------|------|------|------|
| TSI HD (3608) | +1 | NEUTRAL | +20.8% | **外れ** | 自社株買い5.58%・増配が未捕捉。F10/F11新設契機 |
| JINS HD (3046) | -2 | DOWN | +18.6% | **外れ** | 予想PER17×高成長=PEG割安が未捕捉。F12新設契機 |
| 大黒天物産 (2791) | -1 | NEUTRAL | -10.5% | **外れ** | 前Q比OP半減が未捕捉。F13新設・F3廃止候補の契機 |
| タマホーム (1419) | +2 | UP | -10.0% | **外れ** | 配当196→125円の大幅減配が未捕捉。F11減配方向追加の契機 |

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
| 的中 | 3994 | マネフォ | +4 | UP | **+7.7%** | 万年赤字→黒字化は F14 候補 |

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

##### 2026-04-24（CODEX 反省会メモ）

**銘柄別ログ**:

| 区分 | Ticker | 銘柄 | 論点 | 教訓 |
|------|--------|------|------|------|
| ハズレ | 4733 | オービックビジネスコンサルタント | 会社予想 OP 265億 / 経常 282.6億に対し**四季報予測 OP 263億 / 経常 276億** とほぼ同水準。現行モデルは「来期 +12.4% 増益」を強く取りすぎ | 「増益」ではなく「**コンセ並みでサプライズなし**」を見るべき。F5 を**事前期待（コンセ／四季報）との差分**で減点する枠組みが必要 |
| ハズレ | 5576 | オービーシステム | 小型株、決算前5営業日で大幅高。決算自体は良くても事前織り込み負けで下落 | YoY + 来期増益で強気寄り。**短期急騰済みの小型株は出尽くしリスク**（既出の寄り天フェード因子と同パターン、小型株で増幅） |

**4/24 新因子候補 / 方向性**:
- **F16 事前期待並みペナルティ**（4733起点・新規）: 会社来期予想が四季報予測・コンセンサスとほぼ一致していれば F5 増益ボーナスを減衰。「increase vs consensus」を分子に取る設計
- **寄り天フェード因子の強化**（5576起点・既出強化）: 事前織り込み度（決算前 N 日の対 TOPIX 超過リターン）× 時価総額小フラグ の交互作用を重視。277A / 5243 / 5576 と累積3例目
- 共通テーマ: **「事前期待・事前織り込みを現行スコアより強く扱う」** — 絶対値ベースの増益/YoY だけでなく、市場が何を織り込んでいるかとの差分でスコアリングする

##### 2026-04-27（4/22-4/23発表分、Codex反省メモ + 分析）

**銘柄別ログ**:

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| ハズレ | 296A | — | — | — | DOWN -7.29% | グロース市場で次期FY予想が四季報超えでも「ちょっと上」程度ではサプライズ不足。F16+市場区分interactionで吸収可能 |
| 的中寄り | 6146 | ディスコ | — | NEUTRAL | DOWN -3.78% | 大型優良で好数字は当然、通期非開示はお約束。NEUTRAL予測は実質的中。**対応不要** |
| ハズレ | 6858 | 小野測器 | — | UP | DOWN -8.11% | **YoY OP +267%はデータバグ**（正: +28.2%、1年古い比較を拾っている疑い→要調査）。1月本決算で増配+自社株買い→ストップ高済み。今回1Qは「予定通り好調」でサプライズ不足 |
| ハズレ | 7751 | キヤノン | — | NEUTRAL | DOWN -7.90% | 1Qで早々にFY下方修正（-2%）。修正幅は小さいが**1Qで修正すること自体が会社計画への信頼低下**シグナル |
| ハズレ | 7931 | 未来工業 | — | NEUTRAL | DOWN -10.00% | 中計延期+通期非開示+地政学理由。複合ネガティブ。PBR0.8/自己資本80%超でも下支えにならず |

**4/27 分析結果・取り込み方針**:

**テーマA 事前織り込み/サプライズ不足**（296A・6858）:
- 既存の「寄り天フェード因子」設計に**タイムスパン選択肢を追加**: 5日/20日に加え**前回決算後〜今回決算前日の対TOPIX超過リターン**（6858型: 前回イベントで消化済みパターン）
- 3タイムスパンでEDA IC比較 → 効くものを採用。市場区分×時価総額との交互作用も検証
- 296Aのグロース閾値分離はF16+市場区分interactionで吸収。独立因子不要

**テーマB 信頼毀損シグナル**（7751）:
- **F2（ガイダンス修正）にQタイミング修飾子追加**: 1Qでの下方修正は追加ペナルティ-1（合計-2）。3Q/FYは現行-1のまま
- 7931の「中計延期」はN=1・テキスト解析コスト高のため保留。同パターン2-3例溜まったら再検討

##### 2026-04-27（4/24発表分、38銘柄、方向一致率48.6%、相関0.341）

**過去最高精度**。F11（配当因子）の的中が目立つ。

**銘柄別ログ**:

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| 大的中 | 3891 | ニッポン高度紙 | +7 | UP | +15.9% | YoY+168%, コンセ+12.8%, 来期+24.5%, β=1.8。複合因子の理想形 |
| 大的中 | 6954 | ファナック | +4 | UP | +16.0% | コンセ+0.9%, 来期+15.5%, β=1.7 |
| 大的中 | 6999 | KOA | -4 | DOWN | -11.3% | コンセ-35.3%, 来期-22.4% |
| 大的中 | 4519 | 中外製薬 | -3 | DOWN | -15.8% | 大幅減配-47%。F11的中 |
| 大的中 | 6663 | 太洋テクノレックス | -2 | DOWN | -8.0% | 減配-50%。F11的中 |
| ハズレ | 4307 | 野村総研(NRI) | +4 | UP | **-13.4%** | **低ベース効果**。Celonis減損で今期OP赤字→来期+200%は正常化。F5/F7が過大評価 |
| ハズレ | 9551 | メタウォーター | +4 | UP | -9.7% | コンセ+0.4%（実質ゼロ）、過去最高＆増配＆来期+16%なのに急落。X上「PERほどじゃなかった」の声多数。高バリュエーション×コンセ並み＝失望。F12(PEG)が`per=null`で不発＋F16(期待並みペナルティ)の領域。やや難ケース |
| ハズレ | 6723 | ルネサス | +2 | UP | -4.8% | 1Q数字は強い(OP+321%)が**EPS コンセ-36%未達**(F4=NaN不発) + **2Qガイダンス営業利益率-4.7pt**低下を嫌気。F3バグではなくF4データ欠損+マージン因子未実装が真因 |
| 見逃し | 2737 | トーメンデバイス | +1 | NEUTRAL | **+19.1%** | YoY+272%だがコンセNaN。カバレッジなし銘柄のサプライズ見逃し |
| 見逃し | 6861 | キーエンス | +1 | NEUTRAL | **+15.8%** | コンセ+5%だが来期未開示でF5/F7/F12無効。構造的盲点 |
| 織込済 | 5423 | 東京製鐵 | -7 | DOWN | -1.9% | コンセ-125%,来期-155%で壊滅スコアだが事前に完全織り込み済み |
| 見逃し | 8604 | 野村HD | +1 | NEUTRAL | -6.2% | 来期未開示、テーマブーストのみ。証券株の構造問題 |
| NaN | 3260 | エスポア | +1 | NEUTRAL | NaN | ネクスト市場、価格データなし。除外 |

**4/24 改善方針**:

**テーマC 低ベース効果フィルタ**（4307 NRI・9551 メタウォーター — **最優先**）:

単純閾値（赤字or-50%超で減衰）は小手先。**直近5年程度のOP分布に対して今期が特殊要因で低いかを判定する**のが本質。

- 実装: predict時にBQ `fin_summary` から対象銘柄の**直近5期FY実績（OP/SALES/DivAnn）**を取得
- 5年中央値を算出し、今期値が中央値の50%未満（or 2σ外）→「低ベース」フラグ
- 低ベースフラグ時: F5（来期OP変化）の比較対象を「今期→来期」ではなく「**5年中央値→来期**」に差し替え。F7（成長加速）も同様
- F11（配当）にも適用: 前期配当が5年分布の外れ値なら、回復を「増配サプライズ」として扱わない
- SALES も然り: 売上が一時的に落ちた後の「V字回復」を過大評価しない

**来期コンセンサスとの優先順位**: 来期コンセが存在する場合、**会社来期予想 vs 来期コンセ**の比較がF5より優先度が高い（市場期待との差分が真のサプライズ）。来期コンセが無い場合のみ5年中央値ベースにフォールバック。

**テーマC補足: F12(PEG)不発問題 — 次回実装必須（高優先）**: PERデータは**yfinance週次ロードでBQに取得済み**だが、predictノートブックで結合していないため`per=null`→F12が構造的に不発。結合実装すればメタウォーター型（高PER×コンセ並み＝失望）を捕捉可能。**次回predict改修時に必ず実装する**。

**テーマC補足: IFRS企業OdP→OPフォールバック — 次回即修正（高優先）**: IFRS企業（ルネサス等）はJ-Quantsの`OdP`（経常利益）がNULL。F4のQ比較で`pd.notna(odp)`が偽→コンセがBQにあるのにNaN。修正: `_compare_profit = odp if pd.notna(odp) else op` にフォールバック。

**TODO: EPSコンセンサスデータ拡充**: 現行CONSENSUSテーブルは経常利益/OP相当のみ。EPS（1株利益）コンセンサスを持っていないため、OP以下の毀損（税金・のれん償却・特損）による未達を検知不能。ルネサス1Qは OP=コンセ並み だがEPS=-36%で急落。楽天コンセのスクレイピング範囲拡張 or 別ソース（IFIS直接等）要検討。

**TODO: IFRS企業のORD_PROFIT問題**: IFRS適用企業は「経常利益」が会計基準上存在しない。現在のCSV出力は全銘柄 ORD_PROFIT（経常利益）のみだが、IFRS企業では OP_PROFIT（営業利益）を使うべきか検討が必要。QUICKが ORD_PROFIT 列に何を入れているか（税引前利益？）の実態調査を含む。

**テーマD コンセNaN時のYoY補正**（2737 トーメンデバイス）:
- アナリストカバレッジなし銘柄はF4不発。YoY OP +100%超で追加加点の案（中優先）

**テーマE 来期未開示大型株**（6861 キーエンス）:
- 来期予想を毎期出さない常連銘柄（キーエンス・ディスコ等）はF5/F7/F12が構造的に無効
- FY好業績（コンセ+）×プライム大型でF5無効ペナルティを緩和する補正（低優先）

##### 2026-04-30（4/28発表分、98銘柄、方向一致率38.3%、相関0.346）

**総評**: 方向一致率は低い(38.3%)がスコア×リターン相関は高水準(0.346)。シグナル検出力は高いがNEUTRAL帯の取りこぼしが多い。電力DOWN群の大量的中が光る一方、IFRS大型のコンセ欠損問題が拡大。

**銘柄別ログ**（✓=ユーザー検証済み、無印=自動生成未検証）:

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| 大的中 | 9504 | 中国電力 | -9 | DOWN | -6.3% | コンセ-48%/来期-42%/QoQ急落。F4+F5+F13合わせ技 |
| 大的中 | 9505 | 北陸電力 | -7 | DOWN | -13.2% | コンセ-31%/来期-54%/成長減速 |
| 大的中 | 4661 | OLC | -5 | DOWN | -10.1% | コンセ-14%/QoQ急落。FY期末で出尽くしリスク |
| 大的中 | 9022 | JR東海 | -5 | DOWN | -8.0% | コンセ-10%/来期-15%/成長減速 |
| 大的中 | 1964 | 中外炉工業 | +6 | UP | +12.0% | 来期+26%/PEG0.5/QoQ急伸。複合ポジ理想形 |
| 大的中 | 6617 | 東光高岳 | +4 | UP | +16.5% | YoY+77%/コンセ+12%/β高 |
| ✓ハズレ | 8793 | NECキャピタル | +6 | UP | **-4.9%** | YoY+155%/来期+55%/PEG0.2 全ポジ発火なのに下落。**実績OP 106億 vs 会社予想155億＝-31.5%大幅未達**。F15新設契機 |
| ✓ハズレ | 6702 | 富士通 | +2 | UP | **-13.9%** | 来期OP+19%のみ。IFRSコンセ欠損+ガイダンス未達感。来期最終利益-31%で「わかりやすい」ケース |
| ✓除外 | 5903 | シンポ | -4 | DOWN | **+24.7%** | 下方-34%/減配-100%で全ネガ→DOWN予測は正当。**TOB発表により急騰**。決算反応ではないため評価対象外 |
| ✓ハズレ | 6526 | ソシオネクスト | — | — | — | コンセ-42%で-3止まりは過小。**F4下方スケール拡張実装済み**（<-20%→-4, <-30%→-5） |
| ✓見逃し | 1942 | 関電工 | 0 | NEUTRAL | +13.6% | 次予109/コ未096。4Q単独好調だが累積コンセ未達で-が付き相殺→0。サンプル蓄積待ち案件 |
| ✓見逃し | 1959 | 九電工 | — | — | — | 次予108/コ未093。1942と同パターン。サンプル蓄積待ち案件 |
| 見逃し | 5482 | 愛知製鋼 | 0 | NEUTRAL | +12.8% | 成長加速/テーマ vs YoY-32%/PEG割高で相殺 |
| ✓見逃し | 6701 | NEC | +1 | NEUTRAL | -7.7% | IFRS/OdP欠損でF4不発。翌期非開示cap。同業NRI大幅安との連動→010残差相関モデルに未収録（バグ記録済み） |
| ✓見逃し | 9267 | Genky DrugStores | 0 | NEUTRAL | -10.1% | 3Qコンセ欠損（小型カバレッジ外）。F5非FY表示ノイズ問題契機→FYガード修正実装済み |
| ✓ハズレ | 4479 | マクアケ | +2 | UP | **-6.0%** | PER574/YoY+45%/PEG12.7。F9グロース誤発火+F12(PEG)2Qで不発。テーマH/I契機 |
| ✓見逃し | 4362 | 日本精化 | +1 | NEUTRAL | +6.7% | 次予OP+6.7%（F5不発）だがEPS+18.5%。OP/EPS乖離問題。テーマJ契機 |

**4/28 新因子候補・改善方針**:

**テーマF FY予想未達ペナルティ（信頼毀損型） — 高優先**（8793起点）:

- **問題**: FY実績がQ3時点で据え置かれた会社予想を大幅に下回った場合、市場は「予想未達」と評価して失望売り。現行モデルはYoY比・コンセ比で判断するが会社予想達成率を直接見ていない
- **設計案**: FYのみ。`実績OP / 会社通期予想FOP` で判定
  - `< 0.80` → **-1**（20%以上未達）
  - `< 0.65` → **-2**（35%以上未達）
- **F5連動**: F15発火時はF5（来期見通し）スコアを半減。「今期予想すら当てられない会社の来期予想」の信頼性を割り引く
- **データ**: 既にpredictノートブックで `op`（実績）と `fop`（会社通期予想）を取得済み。実装コスト低
- **検証**: 過去データで FY達成率<80% のN数とリターン分布をEDAで確認

**テーマI F12(PEG)全Q拡張 + 下方スケール強化（高優先）**（4479 マクアケ起点）:

- **問題**: F12(PEG)はFY限定。2Qのマクアケ(PER574/YoY+45%/PEG=12.7)で不発。F3(+1)だけが発火し「好業績」判定→実際は-6%
- **F3とF12の役割分担**: F3は業績の方向性（残す）、F12はバリュエーション対比（拡張する）。別問いなので両方必要
- **設計案**:
  - FY: 従来通り `PEG = PER / (来期OP成長率×100)`
  - 1Q/2Q/3Q: `PEG = PER / (YoY OP成長率×100)` で算出（来期予想が無いため代替）
  - 下方スケール拡張: PEG > 2.0→-1（現行）、> 5.0→-2、> 10.0→-3
- **注意**: YoY OPが負（赤字/減益）の場合PEG算出不能。その場合は高PER(>50)×減益で別途減点するか要検討
- **前提**: PERデータが全銘柄で取得済みであること（テーマC補足のper結合実装が先行タスク）

**テーマJ F5にEPS成長率を追加（最高優先・次回即実装）**（4362 日本精化起点）:

- **問題**: F5は来期OP成長率のみで判定。4362はOP+6.7%で不発だがEPS+18.5%。市場（特に米国式評価）はEPS成長率を重視。自社株買い・特別利益・税率変動でOP→EPS間が乖離する銘柄で見逃しが発生
- **設計案**: F5を OP成長率 と EPS成長率 の**大きい方**で判定（保守的に取る）
  - `next_year_eps_change = (NxFEPS - EPS) / |EPS|`
  - `effective_growth = max(next_year_op_change, next_year_eps_change)`
  - 閾値は従来通り: > +10% → +2, < -10% → -2
- **データ**: BQ `fin_summary` に `NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE`(NxFEPS) / `EARNINGS_PER_SHARE`(EPS) あり。追加取得コストなし
- **注意**: EPS は株式分割で非連続になる。F14(分割)発火時はEPS成長率を無効化すること

**テーマH F9テーマブースト：グロース市場で誤発火（中優先）**（4479 マクアケ起点）:

- **問題**: F9は `β(TOPIX対比) > 1.0 かつ TOPIX当日+` で発火するが、グロース市場銘柄はTOPIX連動を前提にできない。マクアケ(β=1.6)でTOPIX順行日に+1加点→実際は-6%
- **対策案**: (a) グロース市場銘柄はF9無効化（簡易） (b) market_divisionで分岐しグロース指数対比に変更（正確） (c) ML移行で吸収
- **補足**: 高PER×成長減速（QoQ -30%）パターンも同時に見逃し。PER×YoYの交互作用はスコアリング方式の構造的限界 → GBDT移行で解決

**テーマG IFRS企業コンセ欠損問題（継続）**:

- 4/28でIFRS/OdP欠損によりF4が効かなかった銘柄: NEC(6万億), 三菱電機(12万億), デンソー(5万億), マキタ(1.4万億), TDK(5万億), 小松(6万億), アイシン, 豊田自動織機
- テーマC補足（4/24）の「OdP→OPフォールバック」実装を再度催促。大型で集中的に不発しており影響大

##### 2026-05-03（4/30発表分、126銘柄、方向一致率45.5%、相関0.333）

**総評**: 方向一致率は過去最高水準(45.5%)。新因子F15(黒字転換)が初回2/2的中。電力DOWN的中が継続する一方、鉄道セクター6銘柄が集中ハズレ（事前織り込み）。F4 FY_NEXTロジックは正常動作確認済みだが、経常利益のみで純利益を見ていないデータ盲点が東武鉄道で顕在化。

**銘柄別ログ**（✓=ユーザー検証済み、無印=自動生成未検証）:

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| 大的中 | 7976 | 三菱鉛筆 | +10 | UP | +9.1% | F1進捗34%+F3+F4コンセ+26%+F10自社株買い+F11増配+F12 PEG0.3。全因子フル発火の理想形 |
| 大的中 | 9506 | 東北電力 | -9 | DOWN | -5.9% | YoY-129%/コンセ-35%/翌期非開示cap/QoQ-154% |
| 大的中 | 9503 | 関西電力 | -9 | DOWN | -4.1% | コンセ-30%/来期-18%/QoQ-55% |
| 大的中 | 6961 | エンプラス | -7 | DOWN | **-23.4%** | コンセ-20%/PEG6.9/QoQ-55%。最大下落 |
| 大的中 | 3137 | ファンデリー | +7 | UP | +12.7% | **F15黒字転換(+2)初的中**。成長加速+PEG0.8 |
| 大的中 | 9501 | 東京電力 | +6 | UP | +3.5% | コンセ+54%+QoQ+92%+**F15黒字転換** |
| ✓ハズレ | 9001 | 東武鉄道 | -6 | DOWN | +2.3% | F4 FY_NEXT正常動作。来期**経常**コンセ乖離-11.7%でDOWN判定は妥当。しかし来期**純利益** 560億 vs QUICKコンセ 499億 = +12.0%で市場は好感。**経常と純利で方向が真逆**。CONSENSUSテーブルに純利益がないデータ盲点 |
| ✓見逃し | 5332 | TOTO | +1 | NEUTRAL | **+18.4%** | 新領域事業(セラミック)利益289億が住設279億を初逆転。セグメント構造転換を評価。→ `093_earnings_deep_analysis.md` に材料記録 |
| ハズレ | 9044 | 南海電鉄 | -7 | DOWN | +2.7% | 鉄道セクター事前織り込み |
| ハズレ | 9021 | JR西日本 | -7 | DOWN | +0.9% | 同上 |
| ハズレ | 9202 | ANA | -6 | DOWN | +4.2% | 同上 |
| ハズレ | 9020 | JR東日本 | -3 | DOWN | **+9.2%** | 同上。コンセ-11%→大幅上昇。出尽くし典型 |
| ハズレ | 2579 | コカコーラBJH | +6 | UP | -3.9% | IFRS。1Q進捗率-1%(赤字)なのにコンセ+92%で+6。F1(-1)では不十分 |
| 見逃し | 3696 | セレス | +1 | NEUTRAL | **+20.1%** | 1Q進捗62%+YoY+101%+PEG0.2。F11減配-25%で-3相殺→+1。1Qの「減配」は前期確定値で市場は無視 |
| 見逃し | 8035 | 東京エレクトロン | +1 | NEUTRAL | +6.9% | 翌期非開示cap=20.9兆円。構造的盲点 |

**4/30 改善方針**:

**テーマK 純利益コンセンサスの取得・F4二重チェック — 高優先（9001 東武鉄道起点）**:

- **問題**: CONSENSUSテーブルは経常利益のみ。鉄道・不動産等で特別損益が大きいセクターでは経常と純利で方向が逆転し、F4が市場と逆の判定を出す
- **データ確認**: QUICKコンセ(日経新聞)で経常63,500 vs コンセ75,500(-15.9%)、純利56,000 vs コンセ49,985(+12.0%)。方向が真逆
- **対策**: 楽天/IFISスクレイピングで**純利益**コンセンサスも取得。F4をOdP(経常)とNP(純利)の両方で算出し、方向が逆転する場合はフラグ立て or 中和
- **関連**: 4/24反省会「EPSコンセンサスデータ拡充」TODO、4/28 6702富士通「来期最終利益-31%」ケースと同根

**テーマL 鉄道セクター集中ハズレ — 事前織り込み因子（継続・6例追加）**:

- 9001東武(+2.3%), 9044南海(+2.7%), 9021JR西(+0.9%), 9202ANA(+4.2%), 9020JR東(+9.2%), 9104商船三井(-0.5%)
- コンセ未達でもDOWN不発。「既知の構造問題」は事前に織り込み済み
- 4/27設計済みの3タイムスパン事前リターンEDAと合わせて検証

**テーマM F11四半期分岐 — 中優先（3696 セレス起点）**:

- 1Qで「減配-25%」が-3発火したが、これは前期配当確定値であり業績悪化シグナルではない
- 設計案: 1Q/2QのF11は来期FDivAnn vs 前回FDivAnnに限定。前期実績配当の前年比は無視

##### 2026-05-08（対象: 5/7発表分、53銘柄、方向一致率50.9%、相関0.485）

| 区分 | Ticker | 銘柄 | score | 予測 | 実績 | 考察 |
|------|--------|------|-------|------|------|------|
| 大的中 | 1911 | 住友林業 | -10 | DOWN | -4.8% | 進捗率低15%/コンセ-34.6%/大幅減配-73%。全因子合致 |
| 大的中 | 6841 | 横河電機 | -4 | DOWN | -9.8% | コンセ-13.7%/PEG割高8.5 |
| 大的中 | 9325 | ファイズHD | +4 | UP | +10.5% | YoY OP+64%/来期OP+46.9%/成長加速 |
| 大的中 | 8005 | スクロール | +3 | UP | +23.1% | YoY OP+64%/来期EPS+57.6% |
| ✓ハズレ | 5445 | 東京鐵鋼 | -4 | DOWN | +6.2% | **還元強化**: 配当性向30%→35-40%引上げ+自社株買い1.38%。業績悪化でも還元方針変更で上昇。新因子候補#13 |
| ハズレ | 1723 | 日本電技 | -4 | DOWN | +3.6% | EPSコンセ-76.4%だが小幅上昇。閾値内の誤差か |
| 見逃し | 3914 | JIG-SAW | +1 | NEUTRAL | +18.8% | YoY OP+64%のみ。恒常的に業績予想非開示（Forecast全NULL）でF5/F7/F12無効 |
| 見逃し | 7972 | イトーキ | +1 | NEUTRAL | -11.0% | 進捗率高51%/増配+32%だがコンセ-4.8%/PEG割高2.1で相殺→score=1。DOWN方向に拾えず |

**5/7 改善方針**:
- **新因子候補#13 還元強化因子**（5445起点）: 配当性向方針変更・DOE導入・累進配当宣言・自社株買い枠拡大等を包括捕捉。F10/F11は実績値ベース、本因子は「方針変更」検知。逆方向（還元減退）は理論上あるが実例少
- **JIG-SAW型: 恒常非開示企業の1Q-3Qフラグ漏れ**: FY時のみ「来期予想未開示」フラグが立ち、1Q-3Qでは未検知。要コード確認

##### 2026-05-13（対象: 5/12発表分、Codex調査付きフィードバック7件）

**計画**: `docs/plans/tools-059_earnings_model_eda_20260513_165133.md`
**計画**: `docs/plans/tools-059_hanseikai_split_20260513_200110.md` — 反省会ログ分離・TODO集約・トークン最小化

| 区分 | Ticker | 銘柄 | 考察 |
|------|--------|------|------|
| ✓バグ | 5449 | 大阪製鐵 | **P0バグ**: `median_5y_op`が4Q単独OPで計算。通期5年中央値53億のところ4Q単独中央値12億を使用し、低ベース補正+F5/F7が不正値。Codex調査で特定 |
| ✓バグ | 7030 | スプリックス | F3 `yoy_op +1394%`。4Q単独OPの前年比で分母極小→異常値。低ベースガードで対処（F3の通期化はしない＝因子の意味を変えない） |
| ✓モデル | 7918 | ヴィア・HD | 4Q単独の小幅黒字化(推定+19百万)を `YoY +137%`/`来期+541%` と過大表現。通期は営業赤字-68百万のまま。Codex分析で%表現と実態の乖離を確認 |
| ✓因子不足 | 6644 | 大崎電気工業 | 今期NP減益予想を未捕捉。OP増益+NP減益乖離の新因子候補（P2調査） |
| ✓テーマ | 6946 | 日本アビオニクス | 防衛テーマ飽き+高PBR(約6倍)。テーマサイクル・バリュエーション因子は構造的限界 |
| ✓モデル | 7918 | ヴィア・HD | ユーザー: 「Codex分析は良い。因子だけだと実態と離れる。モデルの問題あり」 |
| — | 262A | インターメスティック | M&A連結効果（メガネスーパー買収）。利益率低下懸念。分析不要（ユーザー判断） |

**5/12 改善決定・実施結果**:

- **P0-1 median_5y_op通期化バグ修正** [実装済]: `median_5y_op`が4Q単独OPで計算されていたバグを修正。`baseline_yoy_op`と同じ通期OP集約ロジックに統合。検証: 5449 median 12.4億(4Q)→53.3億(通期)
- **P1-2 低利益率企業ガード(is_low_profit)** [実装済]: `median_5y_op < 5億円`の企業をフラグ化し、F3/F7/F13を無効化。%ベース因子の異常値（7918 YoY+137%等）を上流で遮断。検証: 7918(median=-93百万)→フラグON、7030(median=21.7億)→フラグOFF
- **P1-3 F5 EPS主因子/OP従因子分離** [実装済]: F5をF5b(EPS,主)とF5a(OP,従)に分離。EPS減益時はOP増益を無効化。検証: 6644 score +5→+1（EPS-16%が-2、OP+24%は符号不一致で無効化）
- **P1-1 F3 FY通期化** [却下]: ユーザー判断「F3は四半期単独OPの前年同期比で全Q統一。FY期だけ通期に変えると因子の意味が変わる」。異常値は#15(P0-1)のis_low_profitで対処
- `LOW_PROFIT_THRESHOLD`定数をcore.pyに追加（500_000_000）、`PRED_COLUMNS`にis_low_profit追加
- `show_prediction.py`にnext_year_eps_change・is_low_profit表示追加

#### ML切り替え計画

GCS `actuals/` が数百件に達したら LightGBM/XGBoost への切り替えを検討。連続値特徴量（`consensus_deviation`, `yoy_op` 等）は prediction JSON に保存済みで学習データとして使える。切り替え時は EDA ノートブックで因子ごとのリターン寄与を検証してから。

**ML移行時に取り込む追加特徴量**:

| 特徴量 | ソース | 狙い |
|--------|--------|------|
| 同クラスタ先行決算銘柄の翌日騰落 | `clusters_2025.csv` + actuals | NRI大幅安→NEC連れ安のようなセクター波及を捕捉 |
| 対象銘柄と同クラスタ銘柄の残差相関 | `residual_corr_YYYY.csv` | 連動度の強さで波及リスクを重み付け |
| 先行決算からの経過日数 | actuals timestamp | 時間減衰（直近ほど影響大） |

> **参照**: `docs/knowledges/analysis/010_factor_model_residual_corr.md` — 3因子除去後の残差相関クラスタ（年次20群、500億以上約400-500銘柄）。業種分類では見えない「市場が認識する同業」を定量化済み。スコアリング方式では段階設計が困難だが、GBDTなら連続値のまま最適分割される。

#### GCS 保存先

```
gs://stock_data_1930932/earnings_model/
  earnings_reaction_predictions/prediction_{PREDICT_DATE}_{HHMMSS}.json            # 予測結果
  earnings_reaction_actuals/actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json    # 実績突合結果
  earnings_reaction_accuracy/accuracy_summary.json                                  # 累積精度サマリー
```

> **ファイル名規約の要点**: prediction は `PREDICT_DATE` ベース、actual は `SAVE_DATE`（実行日）ベース + `_for_{PREDICT_DATE}` サフィックス。actual を探す時は **SAVE_DATE（=答え合わせ実行日）** または **`_for_{PREDICT_DATE}`** で検索する。ACTUAL_DATE（翌営業日）はファイル名に含まれない。

#### GCS ファイル確認時の注意（必須）

1. **タイムスタンプは JST**: ローカル実行時は `datetime.now(tz=JST)` で JST。Colab の `datetime.now()` は UTC なので **+9h して JST に変換**してから判断すること
2. **actual ファイルの検索方法**: ファイル名は `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`。**SAVE_DATE は答え合わせを実行した日（≠ ACTUAL_DATE）**。検索プレフィックスの候補は:
   - `earnings_model/earnings_reaction_actuals/actual_{今日の日付}` — 当日実行分
   - `_for_{PREDICT_DATE}` が含まれるか目視確認 — 過去実行分
   - **❌ `actual_{ACTUAL_DATE}` で検索するのは誤り**（2026-04-27 事故: SAVE_DATE=20260427 なのに ACTUAL_DATE=20260425 で検索→0件→見逃し）
3. **GCS リスト結果を鵜呑みにしない**: リスト結果にファイルが見つからない場合でも、ユーザーが「実行済み」と言っているなら**ファイル名を直接指定して `gcs_read` を試す**。リストのキャッシュ・遅延の可能性がある

#### 使い方

1. **当日引け後**: `PREDICT_DATE` を設定 → セル1〜6を実行（予測生成 + GCS保存）
2. **翌営業日引け後**: `ACTUAL_DATE` を設定 → セル8〜10を実行（答え合わせ + 精度集計）

## ノートブック起動・保存先

- **EDA**: `scripts/earnings_model/earnings_model_eda.ipynb`（Git管理）+ `G:\マイドライブ\Colab Notebooks\`（Colab用コピー）
- **起動**: Colab で直接開く / ローカル: `jupyter lab scripts/earnings_model/earnings_model_eda.ipynb`

**BQ コスト最適化**: BQ アクセスは初回1回のみ。全8テーブルをCSVキャッシュ（Colab: `/content/earnings_model_cache/`、ローカル: `C:\tmp\earnings_model_cache\`）。`FORCE_RELOAD=False`（デフォルト）でキャッシュ使用。

## ノートブック構成（EDA: 24セル、predict: 11セル）

**EDA ノートブック** (`earnings_model_eda.ipynb`) のセクション構成:
1. Setup (Cell1: RUNTIME判定・認証・BQ/JQuants/Gemini) + Config (Cell2)
2. データ取得 (Cell4: BQ 8テーブル一括DL→CSV) + フィルタリング (Cell6: FinancialStatements抽出＋事前修正フラグ)
3. データ結合 (Cell8: ザラバ/引け後判定＋ラベル生成、Cell9: 全テーブル結合＋TDnetイベント抽出)
4. 特徴量エンジニアリング (Cell11) + GCS保存 (Cell13)
5. EDA (Cell15-19: ラベル分布・四半期別・因子散布図・単変量IC・セクター)
6. クラスタ別精度 (Cell21-22: clusters_2024/2025.csv 結合＋方向一致率・相関)

## 実行環境・データ取得の要点

- **RUNTIME 自動判定**: `google.colab` の import 可否で `colab` / `local` を自動選択。Colab は `auth.authenticate_user()`、ローカルはサービスアカウント + SSL 回避パッチ
- **Gemini Flash**: 優待分類に `gemini-2.5-flash`（Vertex AI, us-central1）、`google-genai` ライブラリ
- **フィルタリング**: 分析対象は `FinancialStatements` を含む DocType のみ。業績修正（`EarnForecastRevision`）は除外し `has_prior_revision` 特徴量として使う
- **USE_BQ フラグ**: `True`（デフォルト）= BQ、`False` = J-Quants API（最新データ用）
- **修正決算の重複除去（BQ）**: `ROW_NUMBER() OVER (PARTITION BY LOCAL_CODE, TYPE_OF_CURRENT_PERIOD, CURRENT_FISCAL_YEAR_END_DATE ORDER BY DISCLOSED_DATE DESC)` で最新1件のみ使用。API取得時は不要

### BQ カラム → 短縮名マッピング（非自明なもの）

| BQ カラム名 | 短縮名 | 内容 |
|-------------|--------|------|
| `ORDINARY_PROFIT` | `OdP` | 経常利益（OPERATING_PROFIT=OP とは別） |
| `FORECAST_ORDINARY_PROFIT` | `FOdP` | 通期経常利益予想 |
| `NEXT_YEAR_FORECAST_PROFIT` | `NxFNp` | 翌期純利益予想 |
| `RESULT_DIVIDEND_PER_SHARE_ANNUAL` | `DivAnn` | 実績年間配当 |
| `FORECAST_DIVIDEND_PER_SHARE_ANNUAL` | `FDivAnn` | 予想年間配当 |
| `NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_...` | `ShOutFY` | 期末発行済株式数 |
| `NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_...` | `TrShFY` | 期末自己株式数 |

## 特徴量一覧（セル11）

| グループ | 特徴量名 | 計算方法 |
|---------|---------|---------|
| サプライズ（近似） | `surprise_{sales/op/np}_vs_forecast` | Q実績 vs (通期予想-累積実績)/残りQ数。FY: 実績 vs 通期予想 |
| サプライズ（正確） | `surprise_{sales/op/np}_accurate` | Q実績 vs 前回発表時点の同計算。Q1はフォールバック。残りQ数 `{"2Q":3,"3Q":2,"FY":1}` |
| コンセンサス | `surprise_odp_vs_consensus` | 累積経常 vs CONSENSUS_PROFIT（百万円→円変換済み） |
| ガイダンス | `guidance_fy_{sales/op/np}` | 翌期予想 vs 今期実績比（FYのみ有意） |
| ガイダンス | `guidance_remaining_q_op` | 残りQ期間の会社予想ペース（Q1-Q3） |
| YoY | `yoy_{sales/op/np}` | Q単独実績 vs 前年同期Q単独実績 |
| 配当 | `dividend_surprise` | 実績年間配当 vs 予想年間配当（clip=3.0） |
| 時価総額 | `market_cap` | log1p(ADJ_CLOSE × 浮動株数) |
| TDnetイベント | `has_buyback`, `buyback_pct` | 決算同日自社株買い / CHUNK_TEXT正規表現 |
| TDnetイベント | `has_stock_split`, `split_ratio` | 決算同日株式分割 / 分割比率 |
| TDnetイベント | `has_yutai`, `yutai_score` | 決算同日優待変更 / Gemini Flash分類(+1/-1/0) |
| 事前修正 | `has_prior_revision` | 同一CodeでDiscDate以前にEarnForecastRevision存在 |
| 事前修正 | `has_prior_div_revision` | 同上（DividendForecastRevision） |
| メタ/ラベル | — | `Code`, `DiscDate`, `CurPerType`, `DocType`, `quarter`, `industry_33`, `size_cat`, `price_date`, `is_intraday`, `label_close_return`, `label_open_return` |

## GCS 保存先（EDA）

```
gs://stock_data_1930932/earnings_model/
  earnings_reaction_features/YYYYMMDD_YYYYMMDD.csv    # 特徴量 + ラベル（期間別）
  models/YYYYMMDD_<name>.pkl
  reports/YYYYMMDD_metrics.json
```

ノートブック内関数: `save_features_to_gcs(feat, DATE_FROM, DATE_TO)` / `load_features_from_gcs(...)` / `list_features_on_gcs()`

## 時価総額の計算方法

`MarketCapitalization` フィールドは fin-summary に存在しない。代替: `log1p(ADJ_CLOSE × (ShOutFY - TrShFY))`

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

### 主要カラム

- `QUARTER`: 四半期ラベル（`1Q`/`2Q`/`3Q`/`4Q`）。元の `FY` を `4Q` に変換済み
- `TYPE_OF_CURRENT_PERIOD`: 元の開示種別（`1Q`/`2Q`/`3Q`/`FY`）。デバッグ用に保持
- `CURRENT_PERIOD_END_DATE`: ウィンドウ関数のORDER BY基準
- `CURRENT_FISCAL_YEAR_START_DATE`: 会計年度単位のPARTITION基準
- `NET_SALES`, `OPERATING_PROFIT`, `ORDINARY_PROFIT`, `PROFIT`: 連結・単独四半期値（円）
- `NON_CONSOLIDATED_*`: 非連結値。連結のみ開示企業はNULL

### ビュー定義の概要（累積→Q単独変換ロジック）

4ステップCTE: (1) `CurPerType IN ('1Q','2Q','3Q','FY')` フィルタ → (2) 重複除去（`PARTITION BY LOCAL_CODE, FY_START, CurPerType ORDER BY DISCLOSED_DATE DESC`） → (3) LAGで前期累計取得（`PARTITION BY LOCAL_CODE, FY_START ORDER BY CURRENT_PERIOD_END_DATE`） → (4) `cur - COALESCE(prev, 0)` で単独四半期値。半期報告企業（Q1/Q3なし）はprev=NULL→COALESCE(0)扱い。

**値域・NULL注意**: 金額単位は**円**（百万円単位ではない）。連結のみ開示企業の `NON_CONSOLIDATED_*` はNULL。

**YoY結合パターン**: `cur` と `prev`（FY_START を1年ずらして取得）を `ON LOCAL_CODE = LOCAL_CODE AND QUARTER = QUARTER` でJOIN。リターン計算に `SAFE_DIVIDE(cur - prev, ABS(prev))` を使う。

## J-Quants 利用可能エンドポイント（Standard プラン）

- `/v2/fins/summary` — 決算短信サマリー（メイン）
- `/v2/equities/earnings-calendar` — 発表スケジュール（3月・9月期のみ）
- Premium 限定（現状不使用）: `/v2/fins/details`, `/v2/fins/dividend`

---

## プロジェクト全体設計

- **Phase 1**: モデル構築 — 全決算対象。毎日蓄積中
- **Phase 2**: ツール化 — ザラ場監視実装済み → `scripts/zaraba_earnings.py`、詳細 `docs/knowledges/tools/066_zaraba_tool.md`

### ザラ場ツールとの連携

→ 詳細: `docs/knowledges/tools/066_zaraba_tool.md`

ザラ場ツール（`scripts/zaraba_earnings.py`）のスコアリング因子は本 EDA で検証済みの因子を流用。因子の研究・EDA・検証・答え合わせ・精度集計は predict notebook（本ノートブック）、リアルタイムスコアリング実行はザラ場ツールが担当。

### モデル設計の重要原則

1. **分析対象は決算実績発表のみ**（`DocType` に `FinancialStatements` を含むもの）。業績修正・配当修正は分析対象外
2. **業績修正/配当修正は「事前フラグ」特徴量**として使う。事前に修正があると織り込み済みの可能性がある
3. **YoYとコンセンサスサプライズは別の情報**。YoYが良くてもコンセンサスを下回れば売られるし、逆もある（ただし F4 コンセンサスは現在一時除外中）
4. **ザラ場/引け後は特徴量ではない**。引け後決算について翌日どう動くかを検証するのが本筋
5. **同時アナウンス（自社株買い・分割・優待）は決算と同日のもののみ**。事前のものは対象外
6. **コンセンサス（STOCK.CONSENSUS）は百万円単位**。fin_summary（円単位）と比較時は `* 1_000_000` で変換必須

### クラスタ別精度分析（残差相関モデル × 5因子スコアリング）

**分析日**: 2026-04-04 / **対象**: 2024-01-01〜2026-04-03（31,802件、クラスタ付き大型株3,690件）/ **全体精度**: 方向一致率 0.407、相関 0.176  
**クラスタソース**: `docs/knowledges/analysis/010_factor_model_residual_corr.md`（clusters_2024/2025.csv）/ **結合率**: 11.6%（時価総額500億以上のみ）

| クラスタ | 件数 | 方向一致率 | 相関 | 代表業種 |
|---------|------|-----------|------|---------|
| 4 | 182 | 0.484 | **0.338** | 食料品（Top） |
| 5 | 175 | 0.377 | 0.256 | 情報・通信 |
| 6 | 180 | 0.461 | 0.223 | 化学 |
| 9 | 109 | 0.394 | 0.062 | 小売業（Worst: 追加因子必要） |

全20クラスタで正の相関（0.062〜0.338）→ 5因子は全般的に有効。食料品・情報通信が高、小売が低。結果CSV: `C:\tmp\earnings_model_cache\cluster_accuracy_analysis.csv`

### 未実装の因子候補

上方修正後 vs コンセンサス比 / 配当 vs 四季報予想 / 四半期バイアス / セクターバイアス / 発表順位（当日何番目か）/ ~~残差相関クラスタ同業ピア~~ → **検証済み・不採用**（IC=0.018, p=0.277。クラスタ別に連鎖・織り込みが混在し打ち消し）

### 因子発見方法論

EDA → 単変量IC → モデル残差分析（大外し事例から新因子発見）→ 木系特徴量重要度 → 学術論文（PEAD/SUE）→ 仮説→EDA→統計検定サイクル

---

### モデル改善候補（答え合わせから得た知見）

| 日付 | 観察・契機 | 改善案 | ステータス |
|------|-----------|--------|----------|
| 2026-04-14 | 5246 ELEMENTS(1Q): score=+1 NEUTRAL→+15.8%。毎Q赤字→売上成長維持＋黒字転換で急騰 | **黒字転換ファクター**: `op_profit_prev<0 AND op_profit_curr>0`（YoY売上>0 も条件）で加点。F14 候補 | 未着手 |
| 2026-04-14 | 6505 東洋電機製造(3Q): score=+1 NEUTRAL→+15.1%。直近2Q低調→当Q YoY+132%で期待ベース低い分サプライズ増幅 | **低期待増幅**: `prev_q_yoy_op<-0.3` かつ `curr_yoy_op>0.5` で追加加点 | 未着手 |
| 2026-04-14 | 6217 津田駒工業(1Q): score=-3 DOWN→+18.0%。疑義注記付き銘柄の赤字幅縮小で急騰 | **疑義注記フラグ + 赤字幅縮小**: EDINET/TDnet本文から疑義注記フラグ化。赤字縮小（`abs(op)<abs(prev_op)`）で加点。疑義注記付きはDOWN判定しない | 未着手 |
| 2026-04-14 | 3168 MERF(2Q): score=+2 UP→-11.9%。TOB期待で事前急騰→TOBなし確定で期待剥奪 | **事前騰落減衰**: `pre_earnings_return_20d>+X%` でUP判定をNEUTRAL化。TOB観測フラグで別扱い | 未着手 |
| 2026-04-14 | 7516 コーナン商事(FY): 今期コンセ比上振れでも来期コンセ比下振れが市場支配 | **FY用コンセ乖離**: FY発表は `NxFOdP / CONSENSUS(TARGET='NEXT')` を使う | **修正済（2026-04-14）** |
| 2026-04-27 | 6858 小野測器(1Q): YoY OP +267%はデータバグ（正: +28.2%）。1月本決算の増配+自社株買いでストップ高済み→1Q好調は織り込み済み | **事前織り込み度に「前回決算後〜今回決算前日」タイムスパン追加**: 3タイムスパン（5日/20日/前回決算後）でEDA IC検証。寄り天フェード因子に統合 | 未着手 |
| 2026-04-27 | 7751 キヤノン(1Q): FY下方修正-2%だが1Qで修正すること自体が会社計画への信頼低下 | **F2 Qタイミング修飾子**: 1Q下方修正は追加-1（合計-2）。3Q/FYは現行-1のまま | 未着手 |

## 既知の障害と再発防止策

### BQ クエリのバッククォートエスケープ問題（2026-04-10 修正）

**症状**: `BadRequest: 400 Syntax error: Unexpected "¥"` — f-string内のバッククォートが `\`` とエスケープされていた。

**根本原因**: Windows（cp932）では `\` と `¥` が同じコードポイント（0x5C）だが、Linux/Colab（UTF-8）では別文字。Windows では動作するが Colab では BQ がパースに失敗する。

**再発防止**: Python f-string/triple-quoted文字列内でバッククォートはエスケープしない（Python文字列では特殊文字でないため不要）。Colabでのみ発生するのでColab実行テストが必須。

### YoY OP 1年ズレバグ + QoQ 死亡 + batch_rerun look-ahead bias（2026-04-27 発見、未修正）

**修正計画**: `docs/plans/20260427_160000_earnings_model_yoy_qoq_bug.md`

**症状**:
1. **P0: F3 YoY OP が全銘柄で1年古い比較**。BQ ビューに当日発表データが未反映のため、(N-1年) vs (N-2年) を比較。6858 小野測器で +267%（正: +28.2%）
2. **P1: F13 QoQ OP が常に None（死亡）**。`tk_qoq` を QUARTER フィルタ後に same_fy 比較 → 常に1行 → QoQ 不発火
3. **P1: batch_rerun に DISCLOSED_DATE フィルタ欠如** → 過去日の再スコアリングで将来データが混入（look-ahead bias）

**根本原因**: ノートブックは当日決算を J-Quants API で取得するが、YoY/QoQ は BQ ビュー（翌日以降に更新）から取得。BQ に当日データがないため「最新」が前年データになる。QoQ は QUARTER フィルタの適用順序の論理バグ。

**影響**: F3 は全ライブ予測に影響。F13 はリリース以来一度も発火していない。

## Colab Notebooks コピー（自動化済み）

PostToolUseフック (`scripts/sync_colab_notebooks.py`) で NotebookEdit/Write 後に自動コピーされる。
対象ノートブックを追加する場合は `COLAB_SYNC_MAP` に追記。
