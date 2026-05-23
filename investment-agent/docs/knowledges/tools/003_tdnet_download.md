# TDnet 適時開示ダウンロードスクリプト使用方法

**カテゴリ**: tools
**作成日**: 2026-02-25
**ステータス**: 有効
**関連ファイル**: `scripts/tdnet_download.py`

## 概要

TDnet（東証適時開示システム）から適時開示PDFを一括ダウンロードするスクリプト。
カテゴリ分類・優先度フィルタリング・証券コード別サブディレクトリ保存を行う。

## ⚠️ 保存先ポリシー（2026-04-19 変更）

- **ローカル実行はテスト用途のみ**。本番は Cloud Run Job（GCS 保存）で稼働
- ローカル `SAVE_DIR` は `C:/tmp/tdnet/`（CLAUDE.md「ローカルDL保存先 Dropbox / Google Drive 禁止」規約に準拠。旧 `C:/Users/zonekun/Dropbox/stock/script/tdnet` は廃止）
- 決算短信 XBRL ZIP の日次永続化は行わない（ザラ場ツール側で live DL のみ）。詳細: `docs/knowledges/tools/071_xbrl_to_jquants.md`

## 実行方法

```bash
# DATE_MODE に従う（デフォルト: 今日）
PYTHONUTF8=1 python scripts/tdnet_download.py

# 引数で日付を指定（DATE_MODE より優先）
PYTHONUTF8=1 python scripts/tdnet_download.py --from 20260224              # 1日のみ
PYTHONUTF8=1 python scripts/tdnet_download.py --from 20260217 --to 20260224  # 期間

# 保存先を変える
PYTHONUTF8=1 python scripts/tdnet_download.py --save-dir /path/to/dir

# ticker絞り込み（複数指定可、スペース区切り）
PYTHONUTF8=1 python scripts/tdnet_download.py --from 20260217 --to 20260224 --ticker 3746 9384 9834
```

引数の優先順位: `--from`/`--to` > ファイル冒頭の `DATE_MODE`

`--ticker` を指定した場合、TDNet全件取得後に該当tickerのみフィルタしてDLする。

## 日付指定（ファイル冒頭を直接書き換える）

`scripts/tdnet_download.py` の冒頭の設定ブロックで `DATE_MODE` を選択する:

```python
DATE_MODE   = "t"          # "t"=前日〜今日 / "1"=特定の1日 / "r"=期間

DATE_SINGLE = "20260224"   # MODE="1" のときの日付 (YYYYMMDD)

DATE_FROM   = "20260217"   # MODE="r" のときの開始日 (YYYYMMDD)
DATE_TO     = "20260224"   # MODE="r" のときの終了日 (YYYYMMDD)
```

| MODE | 動作 |
|------|------|
| `"t"` | 前日〜当日の2日分を取得。デフォルト（20:03以降開示・土日開示の取りこぼし防止） |
| `"1"` | `DATE_SINGLE` の1日のみ取得 |
| `"r"` | `DATE_FROM` ～ `DATE_TO` の期間を取得 |

## 出力

### ファイル構造

```
<SAVE_DIR>/
├── {証券コード4桁}/
│   └── {YYYYMMDD}_{コード}_{会社名}_{カテゴリ}_{タイトル}_{元ファイル名}.pdf
└── index_{from}_{to}.csv
```

例: `tdnet/7203/20260224_7203_トヨタ自動車_決算短信_2026年3月期Q3決算短信_140120260224567890.pdf`

### インデックス CSV

`index_{from}_{to}.csv` に以下の列を出力:

| 列名 | 内容 | 例 |
|------|------|----|
| `id` | TDnet PDF のファイル名（拡張子なし）。一意キー | `140120260224567890` |
| `pubdate` | **開示日時**（`YYYY-MM-DD HH:MM:SS` JST） | `2026-02-24 15:30:00` |
| `company_code` | 証券コード（TDnet 5桁） | `72030` |
| `company_name` | 会社名 | `トヨタ自動車株式会社` |
| `category` | 分類カテゴリ（`classify()` による） | `決算短信` |
| `priority` | 優先度（S/A/B/C） | `S` |
| `title` | 開示タイトル | `2026年3月期 第3四半期決算短信` |
| `filename` | GCS 保存ファイル名 | `20260224_7203_トヨタ自動車_決算短信_...pdf` |
| `url` | TDnet PDF の URL | `https://www.release.tdnet.info/...` |

> **注意**: `pubdate` の時刻は TDnet HTML の `kjTime` セルから取得。GCS ファイル名には日付（`YYYYMMDD`）のみ含まれ、時刻は含まれない。

## カテゴリ・優先度・フィルタリング

優先度 S/A/B/C の62カテゴリに分類。C（不要）と一部のB（不要）はスキップ。

| 優先度 | 内容 | DL |
|--------|------|-----|
| S | 決算短信・業績修正・TOB等 | ✅ |
| A | 決算説明・自己株取得・配当変更等 | ✅ |
| B（要） | 月次開示・提携・株主優待等 | ✅ |
| B（不要） | 役員人事・訂正・株主総会等 | ❌ |
| C | ETF系・定款変更・ESOP等 | ❌ |

詳細は `scripts/tdnet_download.py` 冒頭のカテゴリ分類テーブルコメントを参照。

## Cloud Run Job での実行

**スケジューラー**: `tdnet-download-daily`（us-west1、`50 23 * * 1-5` JST = **平日 23:50 JST**）

> ⚠️ ダウンロード（GCS保存）のみ。BQ `TDNET_DOCUMENTS_ENHANCED` への格納（tdnet-load）は日次スケジューラー未設定。月次バッチのみ。

手順書: `docs/plans/cloudrun_tdnet_deploy.md`

```bash
# 手動実行
gcloud run jobs execute tdnet-download --region us-west1

# 特定日を指定
gcloud run jobs execute tdnet-download --region us-west1 \
  --args="--from,20260224"

# イメージ更新（スクリプト変更時）
cd C:\Users\zonekun\Dropbox\claude\investment-agent
gcloud builds submit --config cloudbuild/cloudbuild.tdnet.yaml \
  --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
gcloud run jobs update tdnet-download \
  --image us-west1-docker.pkg.dev/gmailpj-357912/tdnet/tdnet-download:latest \
  --region us-west1
```

**注意**: Cloud Run Jobs の環境変数は `CLOUD_RUN_JOB`（`K_JOB` は誤り）。
`K_JOB` は存在しない。`detect_runtime()` はこれで判別している。

## データソース

- TDnet 公式サイト（東証）: `https://www.release.tdnet.info/inbs/I_list_NNN_YYYYMMDD.html`
- スクレイピング仕様の詳細: `docs/knowledges/api/003_tdnet_official_scraping.md`
- 旧実装（yanoshin 非公式 API）のバックアップも上記 md に記載済み

## yanoshin API モード（過去データ用）

```bash
# --yanoshin フラグ: yanoshin 非公式 API を使用（過去データのメタデータ取得用）
gcloud run jobs execute tdnet-download --region us-west1 \
  --args="--from=20250115,--to=20250131,--yanoshin"

# 環境変数でも指定可能
gcloud run jobs update tdnet-download \
  --set-env-vars TDNET_USE_YANOSHIN=true --region us-west1
```

**⚠️ 重要: TDnet PDF の保持期間は 30〜90 日程度**
2025年以前の PDF は TDnet サーバー上に存在しないため全件 404 になる。
`--yanoshin` モードはメタデータ（index CSV）のみ取得でき、PDF ダウンロードは失敗する。
過去データの PDF 取得が必要な場合は JPX 有料 API（月額 ¥70,000〜）が必要。
詳細: `docs/knowledges/api/003_tdnet_official_scraping.md`

## 注意事項

- ページ取得間隔: 0.5秒、PDFダウンロード間隔: 0.3秒（変更しないこと）
- 休日・祝日はTDnetにページが存在しないため自動スキップされる
- 既存ファイルは再ダウンロードせずスキップ（冪等性あり）
- `DATE_MODE="t"` は前日〜当日の2日分を取得する（2026-05-15改修: 20:03以降開示・土日開示の取りこぼし防止。GCSスキップにより重複コストなし）
- 実行時は必ず `PYTHONUTF8=1` を付ける（日本語の print が文字化けする）

---

## カテゴリ分類の改訂履歴

### 2026-03-01 改訂（`data_catalog.md` の46値リストに合わせた全面整合）

`data_catalog.md` > `TDNET_DOCUMENTS_ENHANCED` の `MAIN_CATEGORY`/`SUB_CATEGORIES` 46値リストを正として、`classify()` 関数を以下のように修正した。

#### 変更点

| 変更種別 | 変更前 | 変更後 | 備考 |
|--------|--------|--------|------|
| カテゴリ名修正 | `配当変更` | `配当変更（増減配）` | 46値リストと一致させた |
| カテゴリ名修正 | `受注・契約` | `大型受注・契約` | 46値リストと一致させた |
| 分離 | `特別損益計上`（1ルール） | `特別利益` / `特別損失` の2ルールに分離 | `特別損益計上` は46値リスト中に残存するため VALID_CATEGORIES には残す |
| 新規追加 | （なし） | `業績の重要な先行指標`（優先度B） | 稼働率・出荷台数・解約率・ARPU等 |
| 新規追加 | （なし） | `受注高/受注残高`（優先度B） | 受注高/受注残高/受注残 |

#### 重要: `受注高/受注残高` と `大型受注・契約` の順序

`classify()` 内でこの2ルールは**必ず `受注高/受注残高` を先に書くこと**。

理由: `大型受注・契約` のパターン `受注|契約締結|...` が `受注高` や `受注残高` にマッチしてしまうため、後に置くと `受注高/受注残高` が永遠にヒットしない。

```python
# ✅ 正しい順序
if re.search(r'受注高|受注残高|受注残', t):              return ("受注高/受注残高", "B")
if re.search(r'受注|契約締結|基本合意|覚書締結', t):      return ("大型受注・契約", "B")

# ❌ 逆順にしてはいけない（受注高が大型受注・契約に吸収される）
if re.search(r'受注|契約締結|基本合意|覚書締結', t):      return ("大型受注・契約", "B")
if re.search(r'受注高|受注残高|受注残', t):              return ("受注高/受注残高", "B")
```

#### `VALID_CATEGORIES`（`tdnet_load.py`）との関係

- `classify()` は `特別損益計上` を **`特別利益`/`特別損失` に分離して返す**
- 46値リストには `特別損益計上` も `特別利益` も `特別損失` もすべて存在する
- `tdnet_load.py` の `VALID_CATEGORIES` はこれら3値をすべて含む（Geminiが返す可能性があるため）
- `data_catalog.md` が唯一の正（カテゴリ値の追加・削除は必ずカタログ改訂→スクリプト両方更新）
