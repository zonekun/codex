# 清原スクリーニング（kiyohara_screening.py）

**カテゴリ**: tools
**作成日**: 2026-03-10
**ステータス**: 有効
**関連ファイル**:
- `scripts/kiyohara_screening.py` — メインスクリプト
- `C:\Users\zonekun\Dropbox\stock\DA_四季報_YYYY_Q.xlsx` — 四季報データ（`data_catalog.md` 参照）

---

## 概要

全上場銘柄（4000社超）に対して「清原メソッド」で実質的な企業価値を算出し、
実質PER 昇順のCSVをデスクトップに出力するスクリーニングツール。

---

## 清原メソッドの計算式

```
ネットキャッシュ    = 現預金 + 有価証券×0.7 - 有利子負債
実質時価総額       = 時価総額（自己株除く） - ネットキャッシュ
実質PER           = 実質時価総額 ÷ 次期純利益予想
```

- 時価総額は **自己株除き**（`shares_total - treasury_shares`）で算出
- 有価証券（持合い含む）は保守的に 0.7 掛け
- 銀行・保険・証券・その他金融業は除外（財務構造が異質なため）

---

## 実行方法

```bash
PYTHONUTF8=1 python scripts/kiyohara_screening.py
```

出力先: `C:\Users\zonekun\Desktop\kiyohara_screening_YYYYMMDD.csv`

---

## データソース

| データ項目 | ソース | 備考 |
|-----------|--------|------|
| 銘柄一覧・業種 | BQ `STOCK.STOCK_CODE_LIST` | 除外業種フィルタ用 |
| 株価（直近終値） | BQ `STOCK.STOCK_PRICE` | `TICKER`/`YEARDATE` カラム |
| 現預金・自己株・発行済株数・次期純利益予想 | BQ `STOCK.fin_summary` | 最新FY確定決算のみ |
| 有利子負債・現金等（補完） | 四季報 Excel (`DA_四季報_YYYY_Q.xlsx`) | 有利子負債は常に百万円固定 |

### BQ → 四季報 フォールバック

> ⚠️ **四季報は鮮度が落ちるため積極的に使用しない。**
> BQ・J-Quants 等の他ソースで取得できない項目のみフォールバックとして使用すること。

- 現預金: `fin_summary.CASH_AND_EQUIVALENTS` を優先 → なければ四季報の `現金等`
- 有利子負債: fin_summary には存在しないため **四季報のみ**（他ソースで代替不可の例外）
- 有価証券（持合い）: 現状 0 として計算（将来対応余地あり）

### XBRL（edinet_xbrl_extractor）との連携フロー

現金・有価証券は四季報より **EDINET XBRL** の方が精度・鮮度ともに優位（2026-03-10 検証済み）。
高精度版を作る場合の手順:

```bash
# 1. Cloud Run で XBRL 全件取得（1年分）
gcloud run jobs update edinet-xbrl-extractor --region us-west1 --remove-env-vars XBRL_TEST_LIMIT
gcloud run jobs execute edinet-xbrl-extractor --region us-west1 --async

# 2. 完了後、GCS から TSV をローカルにダウンロード
gcloud storage cp gs://stock_data_1930932/edinet_xbrl/edinet_financial_YYYYMMDD.tsv \
  "C:\Users\zonekun\Dropbox\stock\edinet_financial_YYYYMMDD.tsv"

# 3. kiyohara_screening.py 内で TSV を読み込み、四季報の現預金列を上書き
#    （コード改修が必要。merge on 銘柄コード で XBRL 値を優先）
```

XBRL TSV スキーマ: `証券コード / 会社名 / 提出日 / 現金(百万円) / 有価証券(百万円)`
詳細: `docs/knowledges/tools/038_edinet_xbrl_extractor.md`

---

## 四季報 Excel の単位ルール（重要）

| カラム | 単位 |
|--------|------|
| 有利子負債 | **百万円固定**（CF単位に関係なく） |
| 現金等 | CF単位列（"百万円" or "億円"）に依存 |
| 総資産・自己資本 | 百万円固定 |
| 時価総額 | 億円固定 |
| 自己株保有 | 全件空欄（使用不可） |

---

## 四季報パスの更新方法

四季報は四半期ごとに新しいファイルが届く。スクリプト冒頭の定数を更新する:

```python
# scripts/kiyohara_screening.py の先頭付近
SHIKIHO_EXCEL = Path(r"C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_1.xlsx")
#                                                                ^^^^^^^^
#                           ここを新しいファイル名に変更（例: DA_四季報_2026_2.xlsx）
```

---

## 出力CSVのカラム

| カラム名 | 説明 |
|---------|------|
| code | 銘柄コード（4桁） |
| company_name | 銘柄名 |
| industry | 業種 |
| price | 直近終値 |
| shares_net | 自己株除き発行済株数（株） |
| market_cap | 時価総額（円） |
| cash | 現預金（円） |
| securities | 有価証券（円）※現状0 |
| interest_debt | 有利子負債（円） |
| net_cash | ネットキャッシュ（円） |
| adj_market_cap | 実質時価総額（円） |
| forecast_profit | 次期純利益予想（円） |
| adj_per | 実質PER（倍） |

ソート: `adj_per` 昇順（実質PERが低い＝割安な順）

---

## 除外業種

```python
EXCLUDE_INDUSTRIES = {"銀行業", "保険業", "証券・商品先物取引業", "その他金融業"}
```

---

## 既知の制約・注意点

- **有価証券（持合い）は現状 0**: 持合い株式の詳細データが J-Quants V2 から取得できないため、
  `securities = 0` として計算。今後データソースが確保できれば改善余地あり
- **J-Quants V2 のみ使用**: `/fins/statements`・`/fins/fs_details` は Standard プランでは不可（403/400）。
  有利子負債の取得は四季報 Excel 経由が唯一の現実的手段
- **fin_summary は確定決算のみ**: `TYPE_OF_DOCUMENT LIKE 'FY%'` でフィルタ。四半期予想は除外
- **四季報カバレッジ外銘柄**: 有利子負債が 0 扱いになるため実質PERが過小評価される可能性あり
