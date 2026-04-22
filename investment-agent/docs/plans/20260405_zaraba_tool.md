# ザラ場ツール（決算リアルタイム監視・スコアリング）

**ステータス**: 進行中
**作成日**: 2026-04-05
**関連**: `docs/knowledges/tools/059_earnings_model_eda.md` Phase 2

---

## 概要

ザラバ中の決算発表に即座に反応し、**期待値との乖離**をスコアリングして買い/売り候補を表示する裁量トレード支援ツール。自動トレードは行わない。

---

## サブコマンド一覧

| コマンド | 用途 |
|---------|------|
| `prepare --date YYYYMMDD` | 事前準備。BQからキャッシュ取得 |
| `catchup --date YYYYMMDD --until HH:MM` | 指定時刻までの発表済みDiscNoキャッシュを作成 |
| `watch --date YYYYMMDD` | ザラバ監視。J-Quants + TDnet を最短間隔でポーリング |

---

## Phase 1: 事前準備（前日〜当日朝）

```bash
PYTHONUTF8=1 python scripts/zaraba_earnings.py prepare --date 20260407
```

### 1-1. ターゲット銘柄取得

| ソース | 内容 | 制約 |
|--------|------|------|
| BQ `STOCK.EARNINGS_DISCLOSURE_CALENDAR` | ghostrader.net 由来。時刻・四半期種別あり | 全決算期対応 |
| J-Quants `eq-earnings-cal` | JPX公式。翌営業日分 | 3月期・9月期のみ |

→ CALENDAR をメイン、eq-earnings-cal を補完に使う。ザラバ時間帯（9:00〜15:00）の銘柄のみ抽出。

### 1-2. 各銘柄の事前情報キャッシュ

以下を BQ から取得し、`C:\tmp\zaraba_cache\{date}\` に JSON/CSV 保存。

**BQ アクセスポリシー**: 同一日付のキャッシュが存在すれば BQ アクセスゼロ（全データをローカルキャッシュから読み込み）。日付が変わったら改めて取得。`prepare` 実行時に `prior_data.json` が既に存在する場合はスキップし、`--force` で再取得。

**BQ クエリ設計**: 対象銘柄リスト（1-1 で取得済み）を WHERE IN に渡し、**1回の BQ セッションで全テーブルをまとめて取得**。個別銘柄ごとのクエリは禁止。

| # | データ | ソース | 用途 | キャッシュ |
|---|--------|--------|------|----------|
| 1 | **会社予想（通期）** | BQ `STOCK.fin_summary` 最新レコード | 期待値のベースライン | 日付単位 |
| 2 | **コンセンサス** | BQ `STOCK.CONSENSUS` | 市場期待値 | DATAAT単位（後述） |
| 3 | **直近QoQ（Q単独実績）** | BQ `STOCK.v_fin_summary_actual_for_q_on_q` | 前四半期のモメンタム | 日付単位 |
| 4 | **前年同期Q単独実績** | 同上（自己JOIN） | YoY 計算用 | 日付単位 |
| 5 | **事前修正有無** | BQ `STOCK.fin_summary` (DocType LIKE '%Revision%') | 織り込み度合い | 日付単位 |
| 6 | **直近株価（20日）+ 出来高** | BQ `STOCK.STOCK_PRICE_JQUANTS` | 折込度合い（モメンタム＋出来高急増） | 日付単位 |
| 7 | **信用残** | BQ `STOCK.MARGIN_BALANCE` | 売り残多→上昇圧力 | 日付単位 |
| 8 | **銘柄マスタ** | BQ `STOCK.STOCK_CODE_LIST` | セクター・時価総額区分 | 日付単位 |
| 9 | **前回発表時の会社予想** | BQ `STOCK.fin_summary` (自己JOIN) | 正確版サプライズ計算用 | 日付単位 |

#### コンセンサスキャッシュ（#2）の特別扱い

コンセンサスは頻繁に更新されないため、**銘柄指定なし全件取得**し、日付をまたいでも再利用する。

```
C:\tmp\zaraba_cache\
  consensus_YYYYMMDD.csv    # DATAAT=最新取得日の全銘柄コンセンサス（日付別ディレクトリの外）
```

**取得判断フロー**:
1. ローカルに `consensus_*.csv` が存在するか確認
2. 存在する場合、ファイル名の DATAAT と BQ の `MAX(DATAAT)` を比較（軽量クエリ1回）
3. **一致** → ローカルキャッシュをそのまま使用（BQ全件取得スキップ）
4. **不一致**（新しい DATAAT がある）→ 全件再取得してキャッシュ更新
5. 存在しない場合 → 全件取得して新規作成

### 1-3. 事前サマリー出力

prepare 完了時に各銘柄の概要を表示:

```
=== 2026-04-07 ザラバ決算 事前サマリー（12銘柄） ===
時刻   | Code | 銘柄名     | Q種別 | 会社予想OP | コンセ  | 前Q YoY | 修正 | 折込  | 出来高
11:00  | 7203 | トヨタ自動車 | 3Q   | 4.5兆    | 4.8兆  | +15%   | 無   | 高⚠  | 通常
11:00  | 6758 | ソニーG    | 3Q   | 1.2兆    | 1.3兆  | +8%    | 上方 | 中    | 通常
13:00  | 3697 | SHIFT    | FY   | 50億     | 55億   | +22%   | 無   | 高⚠  | 急増⚠
```

---

## Phase 2: DiscNo キャッシュ埋め（catchup）

複数時間帯を見張る場合に、前の時間帯の発表済みDiscNoをキャッシュに埋める。

```bash
# 例: 11:00の監視後、14:00から監視再開したい → 13:50までの分を埋める
PYTHONUTF8=1 python scripts/zaraba_earnings.py catchup --date 20260407 --until 13:50
```

**動作**:
1. J-Quants `/v2/fins/summary?date=YYYYMMDD` を1回呼び出し
2. DiscTime ≤ 指定時刻 のレコードの DiscNo をキャッシュファイルに書き込み
3. 以降の `watch` はこのキャッシュを読み込み、差分検知に使用

---

## Phase 3: 当日監視（watch）

```bash
PYTHONUTF8=1 python scripts/zaraba_earnings.py watch --date 20260407
```

### 3-1. ポーリング設計

**J-Quants ポーリング**:
- `watch` 開始と同時に即座にポーリング開始（発表時刻を待たない）
- ポーリング間隔: **API仕様の許す限り最短**（初期値1秒、429応答時にバックオフ）
- `date` パラメータで全銘柄一括取得 → 1リクエストで全決算カバー
- DiscNo キャッシュ（`seen_disc_nos.json`）と突合し、新規 DiscNo を検知

**TDnet 並行ポーリング**:
- TDnet RSS (`https://www.release.tdnet.info/inbs/I_list_001_YYYYMMDD.html`) を並行でスキャン
- J-Quants に無い情報（自社株買い・株式分割・優待変更等）を補完
- **一次判断**: 開示タイトルのキーワードマッチ（`自己株式`, `株式分割`, `株主優待` 等）
- **二次判断**: PDF ダウンロード → Gemini Flash（個人キー `google-genai`）で内容解析
  - 自社株買い → 取得株数・取得割合を抽出
  - 株式分割 → 分割比率を抽出
  - 優待 → 新設/拡充/改悪/廃止を分類

**DiscNo キャッシュファイル**: `C:\tmp\zaraba_cache\{date}\seen_disc_nos.json`

```json
{
  "jquants": ["20260407123456", "20260407123457", ...],
  "tdnet": ["140120260407XXXXXX", ...]
}
```

### 3-2. スコアリング（期待値乖離ベース）

#### 累計→Q単独変換

J-Quants OP は累計値。Q単独値が必要な因子では `standalone_op = 累計OP - prev_cumulative_op` で算出。
`prev_cumulative_op` は prepare 時に BQ `fin_summary` の前回発表レコードからキャッシュ。1Q は累計=単独。

EDA で検証済みの5因子 + 追加因子:

| # | 因子 | +条件 | -条件 | ウェイト | 備考 |
|---|------|-------|-------|---------|------|
| F1 | **進捗率サプライズ** | 累計OP÷通期予想 vs 期待進捗率(25%/50%/75%/100%) 乖離>+20% | 乖離<-20% | ±1 | EDA検証済み |
| F2 | **ガイダンス修正** | 上方修正 | 下方修正 | ±1 | EDA検証済み |
| F3 | **YoY 営業利益** | Q単独OP vs 前年同期Q単独OP >+30% | <-30% | ±1 | EDA検証済み |
| F4 | **翌期見通し**（FYのみ） | >+10% | <-10% | ±2 | EDA検証済み |
| F5 | **出尽くしリスク**（3Q） | — | 進捗>90%+据え置き | -2 | EDA検証済み |
| F6 | **配当サプライズ** | 増配 | 減配 | ±1 | fin-summary の FDivAnn で判定 |
| F7 | **折込度合い**（事前調整） | — | 下記いずれか該当で -1 | -1 | 複合判定 |
| F8 | **信用売り残倍率** | 売り長（貸借倍率<1） | — | +0.5 | 踏み上げ期待 |

**F7 折込度合い 詳細**（以下いずれかで -1）:
- 20日株価モメンタム > +10% かつ事前修正なし（好業績期待で上昇済み）
- 直近5日出来高が20日平均の **2倍超**（特に小型株で思惑買い→織り込み済みリスク大）

### TDnet 並行スキャンによるリアルタイム追加因子

| # | 因子 | 検知方法 | ウェイト | 備考 |
|---|------|---------|---------|------|
| T1 | **自社株買い** | TDnet タイトル `自己株式` → Gemini Flash で取得割合抽出 | +1（1%超で+2） | 将来TODO: 過去パターン分析 |
| T2 | **株式分割** | TDnet タイトル `株式分割` → 分割比率抽出 | +1 | 小型株で特に有効 |
| T3 | **優待変更** | TDnet タイトル `株主優待` → Gemini Flash で新設/廃止分類 | +1/-1 | |

### 3-3. リアルタイム表示

決算が出るたびにターミナルを更新（rich Live display）:

```
=== ザラバ決算モニター 2026-04-07 ===  [ポーリング中... 11:00:12 | JQ: 1.0s | TDnet: 3.0s]

 ★買い候補
  スコア | Code | 銘柄名       | 判定        | 主要因子                    | TDnet
  +5     | 6758 | ソニーG      | STRONG_BUY | 進捗↑ YoY↑ 翌期↑           | 自社株買い2.3%
  +2     | 7203 | トヨタ自動車   | BUY        | 進捗↑ 増配                  |

 ★売り候補
  スコア | Code | 銘柄名       | 判定        | 主要因子                    | TDnet
  -1     | 4901 | 富士フイルム   | SLIGHT_SELL| 出尽くし                    |
  -3     | 9984 | ソフトバンクG  | SELL       | ガイダンス↓ 翌期↓ 折込⚠       |

 発表済: 4銘柄  |  未発表: 8銘柄  |  Ctrl+C で終了
```

表示は新規検知のたびにリフレッシュ。買い/売りそれぞれスコア順にソート。

---

## 自社株買いパターン分析（将来TODO）

### 概要

自社株買いの期待値をスコアに組み込むための過去データ分析。TDnet 過去データのBQロードが前提。

### 分析観点

1. **常習 vs 初回**: 過去3年間で自社株買い実績がある企業は「常習」。株価反応は限定的
   - 大型株は特にパターン化している（毎期の決算時に機械的に実施）
   - **初回・久々の自社株買いは強いサプライズ** → ウェイト増
2. **取得割合**: 発行済株式数に対する取得割合。1%未満は形式的、3%超はインパクト大
3. **タイミング**: 決算と同時発表 vs 決算後単独発表。同時の方が市場インパクト大

### データ要件

- BQ `STOCK.TDNET_DOCUMENTS_ENHANCED` に `MAIN_CATEGORY='自己株式取得'` の過去データが必要
- 現状: 直近分のみ蓄積中。過去データは TDnet の保持期間制約（30〜90日）のため取得困難
- → irbank.net からの過去データバックフィル or 今後の蓄積を待つ

---

## 技術設計

### ファイル構成

```
scripts/zaraba_earnings.py     # メインスクリプト（prepare / catchup / watch サブコマンド）
```

### 依存ライブラリ

- `jquantsapi` (ClientV2) — fins/summary ポーリング
- `google-cloud-bigquery` — 事前キャッシュ取得
- `rich` — ターミナル Live display
- `structlog` — ロギング
- `click` — CLI サブコマンド
- `requests` + `beautifulsoup4` — TDnet HTML スクレイピング
- `google-genai` — Gemini Flash（個人キー、TDnet PDF 解析用）

### キャッシュ構造

```
C:\tmp\zaraba_cache\
  20260407\
    calendar.csv          # 当日のザラバ決算銘柄一覧
    prior_data.json       # 各銘柄の事前情報（会社予想・コンセ・QoQ・修正有無・株価・出来高・信用残）
    seen_disc_nos.json    # 発表済み DiscNo キャッシュ（JQuants / TDnet）
    results.csv           # スコアリング結果（watch 中に随時更新）
```

### ポーリング実装方針

```python
import asyncio

async def poll_loop(date: str):
    seen = load_seen_disc_nos(date)  # catchup で作成済みなら読み込み
    prior = load_prior_data(date)

    while True:
        # J-Quants と TDnet を並行実行
        jq_task = asyncio.create_task(poll_jquants(date))
        td_task = asyncio.create_task(poll_tdnet(date))

        jq_data, td_data = await asyncio.gather(jq_task, td_task)

        # 新規 DiscNo 検知
        new_jq = [r for r in jq_data if r["DiscNo"] not in seen["jquants"]]
        new_td = [r for r in td_data if r["id"] not in seen["tdnet"]]

        if new_jq or new_td:
            scores = compute_scores(new_jq, new_td, prior)
            update_display(scores)
            save_seen(seen, date)

        await asyncio.sleep(POLL_INTERVAL)  # API許容の最短間隔
```

### J-Quants ポーリング詳細

```python
# date指定で全銘柄の当日決算を一括取得
resp = cli.get_fin_summary(date_yyyymmdd=target_date)
# → pagination_key がある場合は全ページ取得
# → 新規 DiscNo の差分を検知
# → キャッシュ済み prior_data と突合してスコアリング
```

**レートリミット戦略**: 初期1秒間隔。HTTP 429 応答時に指数バックオフ（2秒→4秒→8秒、上限30秒）。正常応答で1秒に復帰。

---

## 運用フロー（典型的な1日）

```
前日夜 or 当日朝:
  $ zaraba_earnings.py prepare --date 20260407
  → 事前サマリー確認、気になる銘柄をチェック

11:00 の決算発表を監視:
  $ zaraba_earnings.py watch --date 20260407
  → 11:00 前後の決算をリアルタイムスコアリング
  → Ctrl+C で終了

14:00 の決算発表を監視（中断後再開）:
  $ zaraba_earnings.py catchup --date 20260407 --until 13:50
  → 13:50 までの発表済み DiscNo をキャッシュに埋める
  $ zaraba_earnings.py watch --date 20260407
  → 13:50 以降の新規発表のみ検知・スコアリング
```

---

## 将来TODO

- [ ] **自社株買い過去パターン分析**: TDnet 過去データ蓄積後に常習/初回判定を実装（上記セクション参照）
- [ ] **earnings_model_predict.ipynb 連携**: 5因子モデルの予測スコアを事前キャッシュに統合（データ蓄積後）
- [ ] 結果の自動保存 → 翌日答え合わせ（predict notebook と統合）
- [ ] Dropbox/Slack 通知
