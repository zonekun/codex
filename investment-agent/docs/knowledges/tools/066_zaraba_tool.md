# ザラ場決算リアクションツール（ザラ場ツール）

**カテゴリ**: tools
**作成日**: 2026-04-05
**ステータス**: 有効（TDnetポーリング+XBRL抽出ほぼ完了、watch統合テスト残。2026-04-13 EDA因子Step1取込済み）

## 🔴 未解決 TODO（先頭掲示）

- **TMP パス: Linux 実装の検証要**
  - `zaraba_tdnet_poller.py:27` / `zaraba_earnings.py:53` は `CACHE_BASE = "/tmp/zaraba_cache"`（Linux）/ `C:\tmp\zaraba_cache`（Windows）の両対応済みに見えるが、Linux VM 運用時に実機検証されていない
  - XBRL ZIP DLパス、cache ディレクトリ生成、pathlib 相対パス等が Linux で正しく動くか一通り流して確認する必要あり
  - 関連: TDnet XBRL ZIP は日次バッチ（tdnet_download.py）では永続化しないため、ライブポーラー側の Linux 動作は必須条件

**関連ファイル**:
- `scripts/zaraba_earnings.py`
- `zara.py`（クロスプラットフォーム対話ランチャー。Linux/Windows共通）
- `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（Windows専用メニュー。CLI引数変更時は同期必須）
- `docs/plans/20260405_zaraba_tool.md`（設計ドラフト）
- `docs/knowledges/tools/059_earnings_model_eda.md`（Phase 2 として位置づけ）
- `docs/knowledges/tools/071_xbrl_to_jquants.md`（**XBRL勘定科目マッピングの本体**。TAG_CANDIDATES定義・検証結果・アダプター設計・営業収入合算ロジックはこちらで管理。`zaraba_tdnet_poller.py` の `TDNET_TAG_MAP` もこのプロジェクトが保守する）

## 概要

ザラバ中の決算発表を J-Quants API でポーリングし、期待値との乖離をスコアリングして買い/売り候補を rich Live でリアルタイム表示する裁量トレード支援ツール。自動トレードは行わない。

## サブコマンド

| コマンド | 用途 |
|---------|------|
| `prepare --date YYYYMMDD [--force] [--target scheduled\|all] [--data full\|consensus]` | 事前準備。BQ から銘柄情報を一括取得してキャッシュ |
| `catchup --date YYYYMMDD --until HH:MM` | 指定時刻までの発表済み DiscNo をキャッシュに記録 |
| `watch --date YYYYMMDD` | ザラバ監視。J-Quants ポーリング + スコアリング + リアルタイム表示 |
| `review --date YYYYMMDD` | 過去 watch 結果（results.csv）を時系列で表形式表示 |

## 使い方

```bash
# 1. 事前準備（前日夜 or 当日朝）
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407

# 全銘柄を対象に事前準備（watch はこの全銘柄キャッシュを使って評価）
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407 --target all

# コンセンサスだけ更新
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407 --data consensus

# 2. 監視開始
PYTHONUTF8=1 python scripts/zaraba_earnings.py watch --date 20260407

# 3. 中断後に別の時間帯を監視（11:00→14:00 等）
PYTHONUTF8=1 python scripts/zaraba_earnings.py catchup --date 20260407 --until 13:50
PYTHONUTF8=1 python scripts/zaraba_earnings.py watch --date 20260407
```

## データソース

### 事前準備（BQ）

同一日付のキャッシュが存在すれば BQ アクセスゼロ。`--force` で再取得。

| # | データ | BQ テーブル |
|---|--------|-----------|
| 1 | 決算予定銘柄 | `STOCK.EARNINGS_DISCLOSURE_CALENDAR` |
| 2 | 会社予想（通期） | `STOCK.fin_summary` |
| 3 | コンセンサス | `STOCK.CONSENSUS`（DATAAT 単位キャッシュ、全件取得） |
| 4 | QoQ・前年同期 | `STOCK.v_fin_summary_actual_for_q_on_q` |
| 5 | 事前修正有無 | `STOCK.fin_summary`（Revision） |
| 6 | 株価 + 出来高 | `STOCK.STOCK_PRICE_JQUANTS` |
| 7 | 信用残 | `STOCK.MARGIN_BALANCE` |
| 8 | 銘柄マスタ | `STOCK.STOCK_CODE_LIST` |

### コンセンサスキャッシュの特別扱い

- `C:\tmp\zaraba_cache\consensus_YYYYMMDD.csv` に全件保存（日付別ディレクトリの外）
- BQ の `MAX(DATAAT)` とローカルファイル名の日付を比較し、一致ならスキップ
- 頻繁に更新されないため日付をまたいでも再利用

### 当日ポーリング（J-Quants API）

- `/v2/fins/summary?date=YYYYMMDD` を最短間隔でポーリング（初期1秒、429でバックオフ）
- DiscNo キャッシュ（`seen_disc_nos.json`）との差分で新規発表を検知

## 累計→Q単独変換ロジック

J-Quants `/v2/fins/summary` の OP・NP 等は**累計値**。スコアリングでは Q 単独値が必要な因子がある。

```
standalone_op = J-Quants累計OP - prior.prev_cumulative_op
```

- `prev_cumulative_op`: BQ `fin_summary` の前回発表レコードの `OPERATING_PROFIT`（累計値）を prepare 時にキャッシュ
- 1Q の場合: 累計 = 単独（prev_cumulative = 0 扱い）
- EDA notebook の `v_fin_summary_actual_for_q_on_q` ビューと同等のロジック

| 用途 | 使う値 |
|------|--------|
| F1 進捗率 | **累計OP** ÷ 通期予想 = 進捗率 |
| F3 YoY | **Q単独OP** vs 前年同期Q単独OP |
| F5 出尽くし | **累計OP** ÷ 通期予想（3Q のみ） |

## スコアリング因子

| # | 因子 | ウェイト | 計算 | 対象Q |
|---|------|---------|------|-------|
| F1 | 進捗率サプライズ | ±1 | 累計OP÷通期予想の進捗率 vs 期待進捗率(25%/50%/75%/100%)。乖離±20%超で発火 | 1Q/2Q/3Q |
| F2 | ガイダンス修正 | ±1 | 今回 FOP vs 前回予想。±5%超で発火 | 全Q |
| F3 | YoY 営業利益 | ±1 | Q単独OP vs 前年同期Q単独OP。±30%超で発火（EDA側で廃止候補、F13で代替予定） | 全Q |
| F4 | 翌期見通し（FY のみ） | ±2 | NxFOP vs 今期予想。±10%超で発火 | FYのみ |
| F5 | 出尽くしリスク（3Q） | -2 | 3Q累計÷通期予想 > 90% かつ予想据え置きで発火 | 3Qのみ |
| F6 | 増配/減配（非対称） | +1/-2/-3 | FDivAnn vs 前回予想。増配>+5%→+1、減配<-5%→-2、大幅減配<-20%→-3 | 全Q |
| F7 | 折込度合い | -1 | 20日モメンタム>+10%(事前修正なし) or 出来高5日/20日>2倍 | 全Q |
| F8 | 信用売り残倍率 | +0.5 | 貸借倍率<1（売り長） | 全Q |
| F8b | 記念配当/特別配当 | +1 | TDnet TITLE に "記念配当" or "特別配当" を含む | 全Q |
| F10 | 自社株買い | +2 | 同一銘柄の TDnet 開示に "自己株式の取得" を含む | 全Q |
| F13 | QoQ OP急変 | +1/-2 | 前Q単独OP比。>+50%→+1、<-50%→-2 | 全Q |

**判定**: `>=3` STRONG_BUY / `2` BUY / `1` SLIGHT_BUY / `0` NEUTRAL / `-1` SLIGHT_SELL / `<=-2` SELL

**表示略称**（watch/review テーブルの Judge 列、2026-04-14 導入）:

| verdict | 略称 | 意味 |
|---|---|---|
| STRONG_BUY | `S-Buy` | Strong |
| BUY | `N-Buy` | Neutral（中位の買い） |
| SLIGHT_BUY | `W-Buy` | Weak |
| NEUTRAL | `中立` | 中立 |
| SLIGHT_SELL | `W-Sell` | Weak |
| SELL | `Sell` | 据え置き |

**テーブル列**: `Score / Code / Name / Cap / Judge / Pos / Neg`
- `Cap`: 時価総額（億円）。YF_STOCK_INFO.MARKET_CAP を1e8で割る
- `Pos` / `Neg`: factors を符号別に分離（下方・減配・進捗↓・QoQ-・折込・出尽くし等は Neg 側、残りは Pos 側）

### EDA因子との対応（2026-04-13更新）

059 EDA因子のうち未実装:
- **F4 コンセンサス乖離** → Step 2-a（prepare追加）
- **F7 成長加速/減速** → Step 2-b（prepare追加）
- **F9 テーマブースト** → Step 3-a（GCS+TOPIX連携）
- **F12 PER割安度(PEG)** → Step 2-c（prepare追加）

計画: `docs/plans/20260413_zaraba_factor_sync.md`

## キャッシュ構造

```
C:\tmp\zaraba_cache\
  consensus_YYYYMMDD.csv    # コンセンサス全件（日付別ディレクトリの外）
  20260407\
    calendar.csv            # 当日のザラバ決算銘柄一覧
    prior_data.json         # 各銘柄の事前情報
    seen_disc_nos.json      # 発表済み DiscNo キャッシュ
    results.csv             # スコアリング結果
```

## 運用フロー（典型的な1日）

```
前日夜 or 当日朝:
  $ zaraba_earnings.py prepare --date 20260407
  → 事前サマリー確認

11:00 の決算発表を監視:
  $ zaraba_earnings.py watch --date 20260407
  → Ctrl+C で終了

14:00 の決算発表を監視（中断後再開）:
  $ zaraba_earnings.py catchup --date 20260407 --until 13:50
  $ zaraba_earnings.py watch --date 20260407
```

## 銘柄カバレッジ仕様

| ケース | 動作 |
|--------|------|
| カレンダー予定あり（ザラバ/引け後問わず）→ ザラバ中に発表 | 事前情報あり。スコアリング全因子が有効 |
| カレンダー予定なし → ザラバ中に突然発表 | 事前情報なし。J-Quants レスポンス内で完結する因子のみ有効（許容） |
| ザラバ予定 → 引け後に発表 | watch 中は検知されない（許容） |
| 日付前倒し（別日に発表） | 拾えない（許容） |

`prepare` は当日カレンダー登録の**全銘柄**（ザラバ・引け後問わず）の事前情報を取得する。これにより、引け後予定の銘柄が急遽ザラバ中に発表された場合でもスコアリングが効く。

## 答え合わせ・精度改善ループ

→ 詳細: `docs/knowledges/tools/059_earnings_model_eda.md`

ザラ場ツールの答え合わせと因子改善は **predict notebook に統合**する。ザラ場ツール側に verify サブコマンドは作らない。

### 役割分担

| コンポーネント | 役割 |
|--------------|------|
| ザラ場ツール | 決定済みの因子・ウェイトでリアルタイムスコアリング実行。results を GCS に保存 |
| predict notebook | 因子の研究・EDA・検証・答え合わせ・精度集計を一元管理 |

### 精度改善サイクル

```
predict notebook (EDA/因子検証)
    → 因子ウェイト・閾値を決定
ザラ場ツール (スコアリング実行)
    → results を GCS に保存
predict notebook (答え合わせ)
    → ザラバ/引け後を is_intraday フラグで分けて精度集計
    → 因子別 IC・方向一致率を分析
predict notebook (因子改善)
    → ウェイト調整・新因子追加・不要因子除外
    → ザラ場ツールのスコアリングに反映
```

### ザラバ vs 引け後の違い

| | ザラバ（ザラ場ツール） | 引け後（predict notebook） |
|---|---|---|
| 反応タイムフレーム | 発表→当日引け（分〜時間） | 発表→翌営業日（一晩） |
| 答え合わせ指標 | 当日終値 vs 前日終値 | 翌日終値 vs 発表日終値 |
| スコアリング因子 | 同一（predict notebook で検証済みの因子を流用） |

### パラメータ管理

スコアリングの閾値・ウェイトは現在 `zaraba_earnings.py` にハードコーディング。将来的に config YAML に切り出し、predict notebook から自動生成も可能。

ザラバ決算モニター表の列幅は `zaraba_earnings.py` 先頭の `WATCH_TABLE_WIDTH_*` 定数で調整する。

## 既知の問題: watch コマンドが機能しない

**発見日**: 2026-04-07
**原因**: J-Quants `/v2/fins/summary` はリアルタイム更新されない（バッチ更新、当日夜〜翌営業日）。ポーリングしても永遠に0件が返る。
**確認銘柄**: 2659（サンエー）・2734（サーラコーポレーション）— 15:00発表済みだが watch で検知不可。
**対策**: TDnet適時開示ポーリング + XBRL数値抽出に切り替え。設計は `docs/plans/20260407_zaraba_watch_tdnet_xbrl.md`。
**ステータス**: TDnet適時開示ポーリング + XBRL抽出 ほぼ開発完了（2026-04-13）。`zaraba_tdnet_poller.py` に TDnet iXBRLパーサー・タグマッピング・営業収入合算ロジック実装済み。残作業: watch サブコマンドへの統合テスト。
**2026-04-14 追記**: 既定ポーラーを `yanoshin` → `tdnet_html` に切替（yanoshin 遅延で 1407 10:00 発表を取りこぼしたため）。下記「落とし穴: yanoshin の遅延」を参照。

## 設計方針: watch 時のデータ全件メモリロード

watch 起動時に、prepare でキャッシュした全データ（calendar, prior_data, consensus, 株価, 信用残等）を**すべてメモリ上に展開**する。ポーリングで新規発表を検知した際、ディスクI/Oゼロでスコアリングを完了させる。

- prepare 時に保存したCSV/JSONをwatch開始時に一括読み込み → dict/DataFrameとしてメモリ保持
- 新規発表の検知→スコアリング→表示までをメモリ内で完結させ、レイテンシを最小化
- メモリ使用量は数十MB程度（全銘柄でも問題なし）

## 設計方針: TDnet ポーリングソースの二層構成

watch の検知ソースは**インターフェースを統一**し、実装を差し替え可能にする。

| 優先 | ソース | レイテンシ | ページング | 備考 |
|------|--------|-----------|-----------|------|
| 1 | TDnet HTML 直接 | 500-2000ms | 必要（100件/P） | **公式。ザラバ検知はこちらを既定に** |
| 2 | yanoshin RSS/JSON | 60-100ms | 不要 | 個人運営。廃止リスク＋遅延発生実績あり |

- 共通インターフェース: `list[Disclosure]` を返す poller クラス（`YanoshinPoller` / `TdnetHtmlPoller`）
- `cmd_watch` は既定で `tdnet_html` を使用。環境変数 `ZARABA_POLLER=yanoshin` で切替可能
- TDnet HTML 側はページング対応必須（決算集中日300-400件 = 3-4ページ）
- 既存実装 `scripts/tdnet_download.py` の `fetch_disclosures_yanoshin()` / `fetch_one_page()` を参考

### 落とし穴: `fin.iloc[0]` が同FY最古の四半期行を拾い F2/F4/QoQ が全て崩壊（2026-04-14 修正済み）

**現象**:
- 9601 松竹 FY 発表で「翌期 +19%」と表示（真値は約 -45% 減益ガイダンス）
- 3892 QoQ +76%、3177 QoQ +287% など異常値を表示

**原因**:
- `cmd_prepare` の fin_summary SQL が `ORDER BY LOCAL_CODE, CURRENT_FISCAL_YEAR_END_DATE DESC, TYPE_OF_CURRENT_PERIOD`（昇順）
- `_build_prior_data` で `fin.iloc[0]` が **同一FYの最古四半期行**（1Q）になる
- `forecast_op` に 1Q時点の初期予想、`prev_cumulative_op` に 1Q累計が入る
- F2 ガイダンス比較・F4 翌期比較・F13 QoQ・standalone_op 全てが誤値で発火

**修正**:
1. SQL を `QUALIFY ROW_NUMBER() OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE DESC) <= 2` に変更
2. `_build_prior_data` で `sort_values("DISCLOSED_DATE", ascending=False)` 後に `iloc[0]` で最新開示を取得
3. 1Q 発表時の fallback: 直前開示が前期FYで FOP=NULL の場合、`NEXT_YEAR_FORECAST_OPERATING_PROFIT` に切替

**教訓**: `iloc[0]` に依存する場合、SQL の ORDER BY と ROW_NUMBER の両方を検証する。PARTITION BY の粒度と最終ソート順の相互作用で意図しない行が選ばれる。

### 落とし穴: F4 翌期因子の分母がガイダンスで YoY が見えない（2026-04-14 修正済み）

**現象**: FY 発表で「翌期↑/↓」がガイダンス差分になっており、市場が織り込む実績ベース YoY と乖離する。

**原因**: 旧実装 `nx_chg = (nx_op - effective_forecast_op) / abs(effective_forecast_op)` は「来期予想 vs 旧当期予想」のガイダンス変化。FY 時点では当期実績が確定しているため、ユーザーが見たい「来期予想 vs 今期実績」の YoY にならない。

**修正**: FY 時のみ分母を `cumulative_op`（= 今期通期実績）に変更。
- 9601 松竹 FY: 旧 +19% → 新 -45% ✓

**補足**: 非FY（1Q/2Q/3Q）は F4 発火しない設計のため影響なし。`F7g 成長加速/減速` は既に実績ベースなので対比として残す。

### 落とし穴: results.csv が watch 再起動で上書きされる（2026-04-14 修正済み）

**現象**: 「watch → Ctrl+C → watch → Ctrl+C」を繰り返すと最後の watch ぶんしか results.csv に残らない。

**原因**: `cmd_watch` で `scored_results: list[dict] = []` と空配列初期化 → `_save_results` が毎回新規で上書き書き込み。

**修正**: watch 起動時に既存 results.csv を `pd.read_csv` で読み込み `scored_results` に初期ロード。

### 落とし穴: yanoshin の遅延で 10:00 発表を取りこぼす（2026-04-14）

**現象**: 1407 ウェストHDが 10:00 に決算短信を発表したが、watch が検知できなかった。
**原因**: yanoshin JSON API が 09:05 までの開示7件しか返さず、10:00 以降の開示がレスポンスに含まれていなかった。TDnet HTML 側は同時刻に 12 件（1407 決算短信含む）を正常返却。
**恒久対策**: `cmd_watch` の既定ポーラーを `yanoshin` → `tdnet_html` に変更（2026-04-14）。yanoshin は `ZARABA_POLLER=yanoshin` で明示指定した場合のみ使用。
**教訓**: 非公式 API を低レイテンシ目当てで第一選択にしない。ザラバ検知はまず**公式**を当てること。

## 推奨 VM スペック

**`e2-medium`（1 vCPU / 4GB RAM）** がコスパ最良。XBRL ダウンロード+パース 8並列でも CPU 使用率は数%（99%がネットワーク I/O 待ち）。C4 は不要。

## XBRL予想値タグ修正（2026-04-09 xbrl_to_jquants側で実施済み）

`zaraba_tdnet_poller.py` の `TDNET_TAG_MAP` 予想値タグ名が間違っていたため修正済み:

| 修正前（誤） | 修正後（正） | 根拠 |
|---|---|---|
| `ForecastOperatingIncome` | `OperatingIncome` + ctx `ForecastMember` | タグ名は実績と同一。contextで区別 |
| `NextYearForecastOperatingIncome` | `OperatingIncome` + ctx `NextYearDuration*ForecastMember` | 同上 |
| `ForecastDividendPerShareAnnual` | `DividendPerShare` + ctx `AnnualMember*ForecastMember` | 同上 |

**追加修正:**
- `NextYearDuration`（通期）を`NextAccumulatedQ2Duration`（2Q累計）より優先するロジック追加
- 実績抽出時に`ForecastMember`を含むcontextを除外するガード追加
- 検証済み: 9972(1Q), 3382(FY) で全項目正しく抽出確認

## ランチャー（zara.py）

`zara.py` はクロスプラットフォーム対応の対話式ランチャー。PS1メニューと異なりLinuxでも動作する。
プロンプトは日本語で意味が分かるように記述すること（`--force?` のような内部用語を表示しない）。

## 将来 TODO

- **【ブロッカー】watch を TDnet XBRL ベースに改造**（上記「既知の問題」参照）
- results を predict notebook と同じ GCS 形式で保存（答え合わせ統合の前提）
- スコアリングパラメータ（閾値・ウェイト）を config YAML に切り出し。predict notebook の検証結果から自動反映可能にする
- TDnet 並行ポーリング（自社株買い・株式分割・優待変更のリアルタイム検知 → Gemini Flash で解析）
- 自社株買い過去パターン分析（常習 vs 初回サプライズ判定。TDnet 過去データ蓄積が前提）
