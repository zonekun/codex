# ローカル CSV
> 親: [`data_catalog.md`](../../data_catalog.md)

### (c) ローカル CSV

| ローカルパス | 説明 | ソース | 備考 |
|-------------|------|--------|------|
| `data/csv/stock_price.csv` | 株価日次データ（分析用コピー） | BQ `STOCK.STOCK_PRICE` からエクスポート | 期間やTICKERを絞ってコピー |
| `meta/monthly/{ticker}_extract_adapter.json` | 月次extract adapter（ローカルGit管理） | GCS `monthly/meta/{ticker}/extract_adapter.json` | ローカル命名は `{ticker}_` + GCSファイル名 |
| `meta/monthly/{ticker}_url_adapter.json` | 月次url adapter（ローカルGit管理） | GCS `monthly/meta/{ticker}/url_adapter.json` | URL型。extract adapterとは別物 |
| `meta/monthly/{ticker}_structure.json` | 月次structure（ローカルGit管理） | GCS `monthly/meta/{ticker}/structure.json` | BC月次KPIメトリクス定義 |
| `meta/quarterly/{ticker}_structure.json` | 四半期structure（ローカルGit管理） | GCS `quarterly/meta/{ticker}/structure.json` | 受注高・受注残高など先行指標の定義 |
| `meta/quarterly/{ticker}_extract_adapter.json` | 四半期extract adapter（ローカルGit管理） | GCS `quarterly/meta/{ticker}/extract_adapter.json` | 四半期先行指標の抽出定義 |
| `meta/_index/monthly_adapter_index.csv` | 月次アダプタインデックス | ローカル管理 | URL探索・月次アダプタ状態の一覧 |
| `meta/_index/quarterly_adapter_index.csv` | 四半期アダプタインデックス | ローカル管理 | 四半期structure/extract adapterの一覧 |
| `data/csv/pbr_cleansed.csv` | PBR（クレンジング済み） | 事前計算 | 毎回LLMに計算させない |
| `data/csv/gyakuhibu_history.csv` | 逆日歩ヒストリカル | GCS からコピー | |
| `C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_1.xlsx` | 四季報 Excel（2026年1集） | 四半期ごとに手動更新 | シート名: `list`。`scripts/shikiho_reader.py` で読み込み。`scripts/kiyohara_screening.py` も参照。新しい四半期版が届いたら `SHIKIHO_EXCEL` 定数を更新すること |
| `C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx` | EDINET 大量保有変更報告書 遅延提出一覧 | Cloud Run Job `edinet-delay`（毎営業日18:30 JST自動追記） | シート: `MAIN`。カラム: A=銘柄コード(4桁), B=発行体名, C=提出者名, D=義務発生日, E=提出日, F=遅延日数。閾値60日以上。スクリーニング: `scripts/screen_edinet_delay_tob.py`。過去データ(2021〜2024): GCS `gs://stock_data_1930932/edinet_delay/backfill.csv` |
| `data/csv/bond_history.csv` | 債券・マクロ指標 日次ヒストリカル | `C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx` (シート: LIST) | **明示的な指示があったときのみ更新**。直近90日はクレンジング未完了の可能性あり（下記参照） |
| `data/csv/consensus_result.csv` | コンセンサス（経常利益）RAKU/IFIS 2ソース | RAKU: 楽天証券 IFIS iframe（Selenium）、IFIS: IFIS株予報直URL（requests） | RAKU: `scripts/update_conse_rakuten.py`、IFIS: `scripts/update_conse_ifis.py`。全上場銘柄対象。**明示的な指示があったときのみ更新** |
| `data/master/activists.csv` | アクティビストファンド一覧（**git管理**） | 手動作成（2026-03-29） | スキーマ: `REGION`（シンガポール/香港/米国/英国/国内）, `NAME`（ファンド名）, `CATEGORY`（アクティビスト固定）。39件→38件。**明示的な指示があったときのみ更新**。⚠️ **除外済み（復活候補）**: `キャピタル・マネジメント`（国内）→ 名称が一般的すぎて誤検知多発 + 活動不活発のため2026-03-29除外 |
| `data/master/activist_aliases.csv` | アクティビストエイリアス（**git管理**） | `scripts/generate_activist_aliases.py`（Gemini 2.5 Pro生成） | スキーマ: `ACTIVIST_NAME`（activists.csvのNAMEに対応）, `ALIAS`（別名）。300+件。`flag_activists_in_list.py` と組み合わせて使用。**明示的な指示があったときのみ更新** |
| `data/master/nikkei225_constituents.csv` | 日経平均株価 構成225銘柄（**git管理**） | `scripts/download_nikkei225_constituents.py`（Wikipedia日本語版） | スキーマ: `TICKER`(4桁), `CODE5`(5桁), `NAME`, `SOURCE`, `FETCHED_AT_JST`。225行。業種情報は `STOCK_CODE_LIST.INDUSTRY_CODE33` / `SIZE_CATEGORY` と JOIN して取得すること。**構成銘柄変更時（年1-2回）に再実行する** |
| `data/csv/shareholders_activist_flag.csv` | 四季報大株主アクティビストフラグ付きリスト | `scripts/flag_activists_in_list.py` | スキーマ: `shareholder_name`, `is_activist`(bool), `activist_name`, `activist_region`, `match_method`(exact/partial/word_start/partial_rev), `match_score`。入力: 四季報大株主テキスト（1行1株主名） |
| `C:\Users\zonekun\Dropbox\stock\DA_四季報_YYYY_Q.xlsx` | 東洋経済 四季報 全銘柄データ | 手動入手（四半期ごとに更新） | **パスは四半期ごとに変わる**（例: `DA_四季報_2026_1.xlsx` → `DA_四季報_2026_2.xlsx`）。シート `list`、1行目ヘッダ、2行目以降データ。`scripts/kiyohara_screening.py` の `SHIKIHO_EXCEL` 定数を更新して使用。**明示的な指示があったときのみ更新**。⚠️ **鮮度が落ちるため積極的に使用しない。他のデータカタログ・API（BQ・J-Quants等）で取得できない場合のみフォールバックとして使用すること** |

**`bond_history.csv` スキーマ:**

| カラム名 | 型 | 説明 | 元列 |
|---------|-----|------|------|
| DATE | DATE | 取引日 | Col 0 |
| BEI | FLOAT | 損益分岐点インフレ率（Break-Even Inflation） | Col 1 |
| US10Y | FLOAT | 米国10年国債利回り(%) | Col 2: Gbond 10y |
| US5Y | FLOAT | 米国5年国債利回り(%) | Col 3: Gbond 5y |
| US2Y | FLOAT | 米国2年国債利回り(%) | Col 4: Gbond 2y |
| SP500_DIV_YIELD | FLOAT | S&P500配当利回り(%) | Col 5 |
| AAA_YIELD | FLOAT | AAA格社債利回り(%) | Col 6 |
| AAA_SPREAD | FLOAT | AAAスプレッド（対国債） | Col 7 |
| USDJPY | FLOAT | ドル円レート（小数点以下2桁） | Col 8 |
| HYG | FLOAT | ハイイールド債ETF（HYG）価格 | Col 9 |
| USDX | FLOAT | ドル指数（小数点以下2桁） | Col 10 |
| SOX | FLOAT | フィラデルフィア半導体指数 | Col 11 |
| BADI | FLOAT | バルチック海運指数 | Col 12 |
| CRB | FLOAT | CRB商品指数 | Col 13 |
| SKEW | FLOAT | CBOE SKEW指数 | Col 14 |
| DOW | FLOAT | ダウ平均 | Col 15 |
| NASDAQ | FLOAT | NASDAQ総合指数 | Col 17 |
| VIX | FLOAT | VIX恐怖指数 | Col 19 |
| SP500 | FLOAT | S&P500指数 | Col 23 |
| JP10Y | FLOAT | 日本10年国債利回り(%) | Col 26 |
| WTI | FLOAT | WTI原油先物価格（$/バレル） | Col 29 |
| FEAR_GREED | FLOAT | CNN Fear & Greed 指数（0〜100） | Col 30 |

**除外した列（計算可能または不要）:**
- Col 16: DOW前日比(%) → 計算可能
- Col 18: NASDAQ前日比(%) → 計算可能
- Col 20: メモ列（ほぼ空）
- Col 21: Dummy Date（Excelワーク列）
- Col 22: イベントメモ（ほぼ空）
- Col 24: S&P500前日比(%) → 計算可能
- Col 25: 空列
- Col 27: JP10Y前日比(%) → 計算可能
- Col 28: 日米10年金利差（JP10Y - US10Y）→ 計算可能

