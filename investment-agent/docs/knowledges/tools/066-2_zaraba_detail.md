# ザラ場ツール 詳細リファレンス

**カテゴリ**: tools（066サブファイル）
**親知見**: `docs/knowledges/tools/066_zaraba_tool.md`

> 因子表(§スコアリング因子)・判定基準(§判定)・サブコマンド(§サブコマンド)・運用フロー(§運用フロー)は親知見を参照。
> 反省会ログは `066-1_zaraba_retrospective.md` を参照。

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
