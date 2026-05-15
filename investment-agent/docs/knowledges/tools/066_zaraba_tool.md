# ザラ場決算リアクションツール（ザラ場ツール）

**カテゴリ**: tools
**作成日**: 2026-04-05
**ステータス**: 有効（watch/catchup とも TDnet HTML ポーリング + XBRL 抽出に移行完了。2026-04-23 予想 context 限定化完了）

## 🔴 未解決 TODO（先頭掲示）

- **P1: コンセンサス比較が全銘柄で不発**: `obs_guidance_vs_consensus` が全87行空。今期OP実績 vs 今期OPコンセの比較が未実装。v3スキーマ対応（2026-05-05）でデータ構造は整備済み、ロジック接続が未実施（起源: 4/28反省会）
- **P2: 強弱混在銘柄の「危険」フラグ**: POS因子とNEG因子が拮抗している銘柄を NEUTRAL ではなく「MIXED/危険」として明示摘出。方向感なしでポジション取ると大けがするリスクの警告用（起源: 4/28反省会）
- ~~**P2: TMP パス Linux 実装の検証要**~~: 解決済み（2026-05-12）。poller を `~/zaraba_cache` に統一。backup コマンドで xbrl キャッシュも保管対象
- **P2: config YAML 切り出し**: スコアリングパラメータ（閾値・ウェイト）を config YAML に分離。predict notebook から自動反映可能にする
- **P2: TDnet 並行ポーリング**: 自社株買い・株式分割・優待変更のリアルタイム検知 → Gemini Flash で解析
- **P2: 自社株買い過去パターン分析**: 常習 vs 初回サプライズ判定。TDnet 過去データ蓄積が前提（スケール判定は F10 スケール化で実装済み、残りは初回サプライズ判定）
- **P2: 個人投資家関心度マーキング**: 旧F9テーマブースト（β×TOPIX+）は廃止。βでは個人投資家関心度を代理できなかった。別指標（出来高急増・信用買残変化・SNS言及数等）を検討

**関連ファイル**:
- `scripts/zaraba_earnings.py`
- `zara.py`（クロスプラットフォーム対話ランチャー。Linux/Windows共通）
- `zara.sh`（Linux 一発起動ラッパー。`uv run python zara.py` を呼ぶ。`~/.local/bin/zara.sh` は本ファイルへの symlink）
- `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（Windows専用メニュー。CLI引数変更時は同期必須。詳細は下記「PSメニュー依存パッケージチェック」節）
- `docs/plans/20260405_zaraba_tool.md`（設計ドラフト）
- **計画**: `docs/plans/tools-066_zaraba_consensus_quarter_match_20260430_153500.md`（F4c コンセ乖離の四半期/FY 不一致バグ修正、2026-04-30 1878 +320.5% 誤検出契機）
- **計画**: `docs/plans/tools-066_zaraba_gcs_rename_20260508_152000.md`（Q列追加 + GCSアップ/表示 + earnings_modelフォルダリネーム）
- **計画**: `docs/plans/tools-066_zaraba_tool_20260513_005500.md`（F10 自社株買いスケール化 — PDF解析 + ToSTNeT-3判定 + ウェイト段階化）
- `docs/knowledges/tools/066-1_zaraba_retrospective.md`（反省会ログ）
- `docs/knowledges/tools/059_earnings_model_eda.md`（Phase 2 として位置づけ）
- `docs/knowledges/tools/071_xbrl_to_jquants.md`（**XBRL勘定科目マッピングの本体**。TAG_CANDIDATES定義・検証結果・アダプター設計・営業収入合算ロジック・予想context仕様はこちらで管理。`zaraba_tdnet_poller.py` の `TDNET_TAG_MAP` もこのプロジェクトが保守する）
- `docs/knowledges/tools/099_xbrl_lookup.md`（**XBRL四半期推移ツール**。`zaraba_tdnet_poller.py` を共有。ポーラー/抽出ロジック変更時は両方確認必須）

## 概要

事前準備を BQ で行い、ザラバ中の決算発表を TDnet 適時開示ポーリング + XBRL 数値抽出でリアルタイム検知し、期待値との乖離をスコアリングして買い/売り候補を rich Live で表示する裁量トレード支援ツール。自動トレードは行わない。watch 起動時に全データをメモリ展開しディスクI/Oゼロでスコアリング。

## サブコマンド

| コマンド | データソース | 用途 |
|---------|------------|------|
| `prepare --date YYYYMMDD [--force]` | BQ | 事前準備。BQ から銘柄情報を一括取得してキャッシュ |
| `catchup --date YYYYMMDD --until HH:MM` | TDnet HTML + XBRL | 指定時刻までの決算短信を TDnet から取得し、XBRL 抽出 & スコアリング → results.csv 追記 |
| `watch --date YYYYMMDD` | TDnet HTML + XBRL | ザラバ監視。TDnet ポーリング + XBRL 抽出 + スコアリング + rich Live 表示。起動直後に対話プロンプトで指定時間 HHMM (4桁、例 `1100`) と時価総額フィルタ（億円）を入力する。時価総額フィルタ: `500`=500億以下、`+500` or `>500`=500億以上、無入力=全社。指定時間±15秒は 0.05秒間隔、+15〜+60秒は 0.2秒間隔、それ以外は 1.0秒間隔の動的ポーリング |
| `review --date YYYYMMDD` | ローカル CSV | 過去 watch/catchup 結果（results.csv）を時系列で表形式表示。時価総額フィルタ対応（gcs-review と同仕様） |
| `backup` | ローカル | キャッシュ全体を zip でスナップショット保管（バグ調査用）。`backup_*.zip` は除外 |

> **サブコマンド間整合性**: watch と catchup は同じ TDnet ポーラー + XBRL 抽出 + スコアリングパスを共有する。データソースやスコアリングロジックを変更した場合、**両サブコマンドで整合性を確認すること**（教訓: catchup が J-Quants のまま放置され0件返却した事故あり）。
>
> **サブコマンド追加・改修時は4箇所を同時更新**:
> 1. `scripts/zaraba_earnings.py`（CLI argparse）
> 2. `zara.py`（クロスプラットフォームランチャー）
> 3. `claude-investment-agent.ps1`（PS1 サブメニュー + switch ハンドラ）
> 4. 本MD + `023_powershell_menu.md`（サブコマンド表）

## 使い方

```bash
# 1. 事前準備（前日夜 or 当日朝）
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407

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
- **v3スキーマ（2026-05-05〜）**: `V_CONSENSUS_MERGED` VIEW から5項目取得（TICKER, FY, QUARTER, DATAAT, REVENUE, OP_PROFIT, ORD_PROFIT, NET_PROFIT, EPS）。旧TARGET/PROFIT列は廃止

### prior_data.json のコンセンサス構造（v3）

```json
{
  "consensus_by_q": {
    "1Q": {"REVENUE": ..., "OP_PROFIT": ..., "ORD_PROFIT": ..., "NET_PROFIT": ..., "EPS": ...},
    "FY": {"REVENUE": ..., ...}
  },
  "consensus_next": {"REVENUE": ..., "OP_PROFIT": ..., ...},
  "consensus_next_fy": "202703"
}
```

- **当期/来期判定**: `_derive_current_fy()` が fin_summary の `prev_disc_type` / `prev_disc_fy_end` から現在FY(YYYYMM)を導出。BQ追加アクセスなし
- **FYフィルタ**: 1Q/2Q/3Q行は `current_fy` と一致するFYのみ格納（複数FY共存時の誤値防止）
- **制約**: 変則決算期変更企業（年間数十社）は silent skip（deferred、レビュー078#2）

### 当日ポーリング（TDnet HTML + XBRL）

- TDnet 適時開示一覧を HTML スクレイピングでポーリング（`TdnetHtmlPoller`、ページング対応、決算集中日300-400件 = 3-4ページ）
- 決算短信 + XBRL 付きの新規開示を検知 → XBRL ZIP をダウンロードし iXBRL パースで数値抽出
- seen キャッシュ（`seen_disc_nos.json` の `tdnet` キー）との差分で新規発表を検知
- 環境変数 `ZARABA_POLLER=yanoshin` で yanoshin に切替可能（非推奨、遅延リスクあり。2026-04-14に10:00発表の取りこぼし実績）
- 共通インターフェース: `list[Disclosure]` を返す poller クラス（`YanoshinPoller` / `TdnetHtmlPoller`）

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
| F4 | 翌期見通し（FY のみ） | ±2 | 翌期予想ODP vs 今期実績ODP（経常利益ベース、ODP不在時はOPフォールバック）。±10%超で発火 | FYのみ |
| F4c | コンセンサス乖離 | +1〜+3 / -1〜-5 | 実績ODP vs コンセンサスORD_PROFIT（全Q経常利益ベース）。>+10%→+3、>+5%→+2、>0→+1、<-30%→-5、<-20%→-4、<-10%→-3、<-5%→-2、<0→-1 | 全Q |
| F4n | 翌期コンセンサス乖離 | +1〜+3 / -1〜-5 | 翌期会社予想NP vs 翌期コンセNET_PROFIT（純利益ベース）。ウェイトはF4cと同一。表示: `翌コ純` | FYのみ |
| F5 | 出尽くしリスク（3Q） | -2 | 3Q累計÷通期予想 > 90% かつ予想据え置きで発火 | 3Qのみ |
| F6 | 増配/減配（非対称） | +1/-2/-3 | FDivAnn vs 前回予想。増配>+5%→+1、減配<-5%→-2、大幅減配<-20%→-3 | 全Q |
| F7 | 折込度合い | -1 | 20日モメンタム>+10%(事前修正なし) or 出来高5日/20日>2倍 | 全Q |
| F7g | 成長加速/減速 | ±1 | 翌期YoY(ODP、不在時OP) vs 基準YoY(baseline_yoy_op)。差分>+20%→+1、<-20%→-1 | FYのみ |
| F8 | 信用売り残倍率 | +0.5 | 貸借倍率<1（売り長） | 全Q |
| F8b | 記念配当/特別配当 | +1 | TDnet TITLE に "記念配当" or "特別配当" を含む | 全Q |
| F9 | ~~テーマブースト~~ | 廃止 | 旧: 高β×TOPIX+。βでは個人投資家関心度を代理できず廃止 | - |
| F10 | 自社株買い（スケール） | 0〜+4 | `_is_buyback_title()` で自社株買い開示を検知 → PDF 解析で発行済比率を取得し段階スコアリング。ToSTNeT-3/N-NET3 のみの場合はウェイト0（タグ表示のみ）。PDF 解析失敗時はフォールバック +2。閾値: <3%→+1, 3-5%→+3, >=5%→+4 | 全Q |
| F12 | PER割安度 PEG | +2/+1/-1 | FY時、翌期成長率(ODP、不在時OP)>0の場合: PEG=PER÷成長率(%)。PEG<0.5→+2、<1.0→+1、>2.0→-1。株式分割時は無効化 | FYのみ |
| F13 | QoQ OP急変 | +1/-2 | 前Q単独OP比。>+50%→+1、<-50%→-2。FY除外（4Q implied はノイジー→F15で代替）。小分母ガード: \|prev_q\|<年間参照値×5%時スキップ | 1Q-3Q |
| F14 | 株式分割 | +1 | 同一銘柄の TDnet 開示に "株式分割" を含む。F6/F12 を無効化する副作用あり | 全Q |
| F15 | 通期着地サプライズ | ±1/±2 | FY実績ODP vs 直前通期予想ODP（経常利益ベース、ODP不在時はOPフォールバック）。±20%超→±2、±5%超→±1。表示: `着地経↑/↓` | FYのみ |

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

**テーブル列**: `Time / Score / Code / Name / Cap / Q / Judge / Pos / Neg`
- `Cap`: 時価総額（億円）。YF_STOCK_INFO.MARKET_CAP を1e8で割る
- `Pos` / `Neg`: factors を符号別に分離（下方・減配・進捗↓・QoQ-・折込・出尽くし等は Neg 側、残りは Pos 側）

**テーブルレイアウト（列幅）**: `zaraba_earnings.py` 冒頭の `WATCH_TABLE_WIDTH_*` 定数で定義。列幅変更はここだけ触れば全サブコマンド（watch/review/gcs-review）に反映される。

### EDA因子との対応（2026-05-12更新）

059 EDA因子は全て実装済み:
- **F4c コンセンサス乖離** — 同期済み（全Q経常利益ベース。059側もOPフォールバック廃止・当期フォールバック廃止で統一）
- **F4n 翌期コンセンサス乖離** — 実装済み（FYのみ、純利益ベース。表示: `翌コ純`）。059側はF4b(NET_PROFIT)として全Qで適用
- **F7g 成長加速/減速** — 実装済み（FYのみ、翌期YoY vs baseline_yoy_op）
- **F9 テーマブースト** — 059/066とも廃止（βでは個人投資家関心度を代理できず）
- **F12 PER割安度(PEG)** — 実装済み（FYのみ、株式分割時は無効化）

計画: `docs/plans/20260413_zaraba_factor_sync.md`

## キャッシュ構造

```
C:\tmp\zaraba_cache\
  consensus_YYYYMMDD.csv    # コンセンサス全件（日付別ディレクトリの外）
  backup_YYYYMMDD_HHMMSS.zip # キャッシュ全体スナップショット（バグ調査用）
  20260407\
    calendar.csv            # 当日のザラバ決算銘柄一覧
    prior_data.json         # 各銘柄の事前情報
    seen_disc_nos.json      # 発表済み DiscNo キャッシュ
    results.csv             # スコアリング結果
```

## GCS パス参照

| GCS パス | 用途 | 読み書き |
|---------|------|---------|
| `earnings_model/zaraba_beta_20d/beta_20d.csv` | 全銘柄20日β | 読み取り（`_load_beta_20d`） |
| `earnings_model/zaraba_scoring_results/results_YYYYMMDD.csv` | スコアリング結果 | 書き込み（`cmd_upload_results`）/ 読み取り（`cmd_gcs_review`） |

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

## 推奨 VM スペック

**`e2-medium`（1 vCPU / 4GB RAM）** がコスパ最良。XBRL ダウンロード+パース 8並列でも CPU 使用率は数%（99%がネットワーク I/O 待ち）。C4 は不要。

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

## catchup の処理フロー（2026-04-28〜）

catchup は watch と同じ TDnet + XBRL パスを使い、指定時刻までの決算をバッチ処理する。

```
1. prior / master / TOPIX をメモリロード（watch と同じ）
2. TDnet HTML ポーラーで当日の全開示を取得
3. until_time 以前 + 決算短信 + XBRL ありでフィルタ
4. 既存 seen["tdnet"] にない新規決算のみ抽出
5. XBRL ダウンロード & パース & スコアリング（自社株買いPDF fetch含む）を並列実行（最大8ワーカー）
6. results.csv に追記（既存結果を保持）
7. seen["tdnet"] を更新して保存
```

**watch との共通点**: ポーラー / XbrlExtractor / `_xbrl_to_jquants_rec` / `_score_record` を共有。`_process_one` でXBRL取得+rec構築+スコアリング（PDF fetch含む）を一括並列実行。
**watch との差異**: watch はリアルタイム + rich Live 表示。catchup はバッチ一括 + 結果サマリーのみ。

## XBRL 予想 context（ザラ場固有の評価ロジック）

予想 context の扱い（当期/翌期 × 通期/累計Q の4象限）→ `docs/knowledges/tools/071_xbrl_to_jquants.md` §予想値（Forecast）抽出仕様

ザラ場スコアリングでの評価ルール:
- `ShortFOP`（短期予想）は進捗率の分母・上方修正/下方修正には使わない
- `通期予想非開示` ペナルティは **FY 決算発表時のみ** 付与。1Q/2Q/3Q で通期予想タグが無くてもネガティブ扱いしない
- `翌期予想非開示`: FY 発表で `NxFOP`（= `NextYearDuration`）が取れない場合のみ付与。`NextAccumulatedQ*` を代用しない

## ランチャー（zara.py / zara.sh）

`zara.py` はクロスプラットフォーム対応の対話式ランチャー。PS1メニューと異なりLinuxでも動作する。
プロンプトは日本語で意味が分かるように記述すること（`--force?` のような内部用語を表示しない）。

Linux では `zara.sh`（リポジトリ直下）が `uv run python zara.py` を呼ぶラッパーとして用意されており、`~/.local/bin/zara.sh` を本ファイルへの symlink にしておけばパスを通すだけで `zara.sh` 一発起動できる。

## PSメニュー依存パッケージチェック

`claude-investment-agent.ps1` は起動時に `$REQUIRED_PACKAGES` リストで `import` チェックを行い、不足パッケージがあればインストールコマンドを案内する。

**メンテルール**: メニュー配下のスクリプトに新しいサードパーティ `import` を追加した場合、`claude-investment-agent.ps1` の `$REQUIRED_PACKAGES` 配列にも追加すること。Import名（Pythonの `import` 文）と Pip名（PyPIパッケージ名）は異なる場合があるので注意（例: `bs4` → `beautifulsoup4`、`dotenv` → `python-dotenv`）。

**現行リスト（2026-05-07）**:

| Import名 | PyPIパッケージ名 |
|----------|-----------------|
| `numpy` | `numpy` |
| `pandas` | `pandas` |
| `requests` | `requests` |
| `dotenv` | `python-dotenv` |
| `google.cloud.bigquery` | `google-cloud-bigquery` |
| `google.cloud.storage` | `google-cloud-storage` |
| `google.oauth2` | `google-auth` |
| `jquantsapi` | `jquants-api-client` |
| `structlog` | `structlog` |
| `bs4` | `beautifulsoup4` |

## 教訓（解決済み落とし穴）

- **`fin.iloc[0]` 最古行取得（設計制約）**: prepare SQL は `QUALIFY ROW_NUMBER() OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE DESC) <= 2` で最新2行に絞り、`_build_prior_data` で `sort_values("DISCLOSED_DATE", ascending=False)` 後に `iloc[0]`。**ROW_NUMBER + sort_values の2段構えが必須**。片方だけでは同FY最古四半期行（1Q）を拾い、F2/F4/QoQ/standalone_op が全壊する（9601松竹FY事故）
- **F4/F7g/F12 翌期因子**: 経常利益ベース（`NxFODP` vs `OrdinaryProfit`）。ODP不在時（IFRS等）は営業利益にフォールバック
- **results.csv 上書き防止**: watch 起動時に既存 results.csv を read_csv で初期ロード済み。空配列初期化禁止
- **非公式API（yanoshin）を既定にしない**: TDnet HTML 公式が既定（2026-04-14〜）。yanoshin は遅延・取りこぼし実績あり
