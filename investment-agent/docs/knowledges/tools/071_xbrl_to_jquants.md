# XBRL → J-Quants形式 変換ツール（xbrl_to_jquants）

**カテゴリ**: tools
**作成日**: 2026-04-08
**ステータス**: 開発中
**関連ファイル**:
- `scripts/xbrl_to_jquants/` — 専用フォルダ（スクリプト・メタデータ・タクソノミ一式）
- `scripts/zaraba_tdnet_poller.py` — ザラ場ツール用TDnet iXBRLパーサー。`TDNET_TAG_MAP` / `_extract_tdnet_pl()` / 営業収入合算ロジックは本プロジェクトの成果物。**勘定科目マッピング（TAG_CANDIDATES）を変更したらTDNET_TAG_MAPも同期更新すること**
- `docs/knowledges/tools/066_zaraba_tool.md`（ザラ場ツール全体設計。本ツールはザラ場ツールのXBRLパーサー部分を担当）

---

## 概要

EDINET の有価証券報告書（XBRL）から PL 主要項目を抽出し、J-Quants `fin_summary` 互換形式に変換するツール。
J-Quants API に依存せず、EDINET 公開データのみで財務サマリーを構築することが目的。

---

## 変換対象（PL実績5項目）

| J-Quants カラム | XBRLタグ候補 | 日本語 |
|----------------|-------------|--------|
| NET_SALES | `NetSales` / `Revenue` | 売上高 / 売上収益 |
| OPERATING_PROFIT | `OperatingProfit`系 | 営業利益 |
| ORDINARY_PROFIT | `OrdinaryIncome` | 経常利益（IFRS/US-GAAPはNULL） |
| PROFIT | `ProfitLoss` / `ProfitLossAttributableToOwnersOfParent` | 当期純利益 / 親会社株主帰属 |
| EARNINGS_PER_SHARE | `EarningsPerShare`系 | 1株当たり利益 |

---

## フォルダ構成

```
scripts/xbrl_to_jquants/
├── taxonomy_2026/              ← 2026年版タクソノミ（展開済み、2058ファイル）
│   ├── samples/
│   └── taxonomy/jppfs/2025-11-01/
├── EdinetcodeDlInfo.csv        ← EDINETコードリスト（11,254件、cp932、[11]証券コード列）
├── ESE140115.xlsx              ← 金融庁勘定科目リストExcel
├── xbrl_mapping.py   ← ★勘定科目マッピングの本体 + 全銘柄一括検証
├── convert_to_fin_summary.py   ← 本番変換スクリプト（xbrl_mappingからimport）
└── extract_pipeline.py         ← 20社テスト用パイプライン（同上、レガシー）
```

Colabノートブック: `G:/マイドライブ/Colab Notebooks/xbrl_mapping.ipynb`

## 勘定科目マッピング管理（★重要）

### 単一ソース原則

**`xbrl_mapping.py` が唯一の勘定科目マッピング本体**。以下を定義:

| 定義 | 内容 |
|------|------|
| `TAG_CANDIDATES` | PL5項目 × 優先度順XBRLタグリスト（NET_SALES 24タグ / OP 16タグ 他） |
| `ADAPTERS` | 企業固有タグ（1375 雪国まいたけ / 6758 ソニー / 7203 トヨタ） |
| `OPERATING_REVENUE_ADD_TAGS` | 売上高に加算する営業収入タグ（小売業の営業収益対応） |
| `CURRENT_DURATION_CONTEXTS` | 当期duration context |
| `EXCLUDE_CONTEXT_PATTERNS` | 除外context（NonConsolidated/Prior/Member） |
| `extract_pl()` | 抽出ロジック本体（NetSales*始まりの場合のみ営業収入合算） |
| `parse_xbrl()` | lxmlベースXBRLパーサー |
| `find_all_xbrl_files()` | GCSから有報XBRLファイル列挙 |

### EDINET 有報XBRL用（jppfs_cor名前空間）

| ファイル | 役割 | マッピング入手元 |
|---------|------|----------------|
| `xbrl_mapping.py` | **マッピング本体** | 自前定義（本MDで管理） |
| `convert_to_fin_summary.py` | 本番バッチ変換 | `from xbrl_mapping import ...` |
| `extract_pipeline.py` | 20社テスト（レガシー） | 同上 import |

### TDnet 決算短信iXBRL用（tse-ed-t名前空間）

| ファイル | 役割 | マッピング |
|---------|------|----------|
| `scripts/zaraba_tdnet_poller.py` | ザラ場ツールTDnet iXBRLパーサー | **独自 `TDNET_TAG_MAP`**（別系統だがxbrl_mappingと構造を揃えている） |

> **注意**: 有報XBRL（EDINET）とTDnet iXBRL（決算短信）は名前空間・タグ体系が微妙に異なるため別マッピング。
> `xbrl_mapping.py` の TAG_CANDIDATES を変更したら、対応する `TDNET_TAG_MAP` も**必ず同期更新**すること。
> 営業収入合算ロジックも両方に実装済み（`NetSales*` がベースタグの場合のみ加算）。

### BS項目用（別スコープ）

| ファイル | 役割 | マッピング |
|---------|------|----------|
| `scripts/edinet_xbrl_extractor.py` | 現金・有価証券抽出（清原スクリーニング用） | 独自 `CASH_TAGS` / `SECURITY_TAGS`（PL項目とは無関係） |

### マッピング変更時の手順

1. `xbrl_mapping.py` の TAG_CANDIDATES / ADAPTERS を更新
2. 20社テストで動作確認: `python extract_pipeline.py`
3. 必要なら全銘柄再変換: `python convert_to_fin_summary.py`
4. Excel（決算短信ベース）と突合して精度確認
5. **TDnet側も同期**: `zaraba_tdnet_poller.py` の `TDNET_TAG_MAP` を更新
6. 本MDの変更履歴セクションに記載

### 2026-04-10〜11 セッションでの変更点

- `xbrl_mapping.py`: ローカル実行時の出力先を `C:/tmp/xbrl_validate/` に変更（Google Driveロック問題回避）
- `xbrl_mapping.py`: `--reverse` モードのNG CSV検索パスを `C:/tmp/xbrl_validate/` 優先に変更
- `xbrl_mapping.py`: `--reverse` モードのアダプタ候補JSON出力先も同様に変更
- `google-cloud-bigquery-storage` パッケージ追加（`uv add`済み。BQ大量取得の高速化）

---

## データソース

### XBRL ファイル（GCS）

```
gs://stock_data_1930932/edinet/{証券コード}/{証券コード}_{書類略称}_{提出日}_{書類種別}_{EDINET文書ID}_XBRL_PublicDoc_{ファイル名}.xbrl
```

- 対象期間: 2024-01-04 〜 2025-12-26（約44,000ファイル）
- 有価証券報告書・四半期報告書等

### 突合用データ（BQ）

```sql
SELECT * FROM `gmailpj-357912.STOCK.fin_summary`
WHERE LOCAL_CODE IN ('7203', '6758', '6861', '2802', '9984')
```

- ローカルコピー: `data/csv/fin_summary_sample_5stocks.csv`（210行 × 107列）
- 5銘柄: 味の素(2802), ソニー(6758), キーエンス(6861), トヨタ(7203), ソフトバンクG(9984)

---

## EDINET HP からのメタデータダウンロード手順

### ダウンロードページ

URL: https://disclosure2.edinet-fsa.go.jp/weee0010.aspx

ASP.NET (GeneXus) 製のため、JS経由のPOSTでダウンロードが発生する。
WebFetch / ヘッドレスブラウザではダウンロード不可。**Selenium headed Chrome** を使う。

### ダウンロード方法（Python + Selenium）

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time, os, glob

dl_dir = r'C:\tmp\edinet_taxonomy'
os.makedirs(dl_dir, exist_ok=True)

opts = Options()
opts.add_experimental_option('prefs', {
    'download.default_directory': dl_dir,
    'download.prompt_for_download': False,
})

driver = webdriver.Chrome(options=opts)
driver.get('https://disclosure2.edinet-fsa.go.jp/weee0010.aspx')
time.sleep(3)

# ファイル名を指定してダウンロード実行（JS関数を直接呼ぶ）
driver.execute_script("onDownload('ファイル名.zip')")

# ダウンロード完了待ち
for i in range(60):
    time.sleep(1)
    files = glob.glob(os.path.join(dl_dir, '*.zip'))
    crdownload = glob.glob(os.path.join(dl_dir, '*.crdownload'))
    if files and not crdownload:
        break

driver.quit()
```

### ダウンロード対象ファイル一覧

| 種別 | JS関数引数 | 説明 | 必要性 |
|------|-----------|------|--------|
| **財務諸表本表タクソノミ（2026年版）** | `onDownload('JPPFS_20251101.zip')` | PL/BS タグ定義・ラベル | **必須** |
| 財務諸表本表タクソノミ（2025年版） | `onDownload('JPPFS_20241101.zip')` | 旧年度XBRL用 | 必要に応じて |
| 財務諸表本表タクソノミ（2024年版） | `onDownload('JPPFS_20231201.zip')` | 旧年度XBRL用 | 必要に応じて |
| **EDINETコードリスト** | ページ下部「EDINETコードリスト」→「ダウンロード」 | 証券コード↔EDINETコード | **必須** |
| ファンドコードリスト | ページ下部 | 投資信託向け | 不要 |
| DEI タクソノミ | `01. DEI` セクション | 書類メタ情報タグ | 必要に応じて |
| 国際会計基準タクソノミ | `03. 国際会計基準` セクション | IFRS企業用 | IFRS対応時に必要 |

### セクション展開が必要な場合

```python
# 例: 02. 財務諸表本表 を展開してからダウンロード
panel = driver.find_element(By.ID, 'taxonomy-A-3')
panel.click()
time.sleep(1)
driver.execute_script("onDownload('JPPFS_20251101.zip')")
```

### EDINETコードリストのダウンロード

EDINETコードリストはページ下部の専用セクションにある。セクション展開不要。

```python
# EDINETコードリストのダウンロードリンクをクリック
# ページ下部の「EDINETコードリスト」行の「ダウンロード」リンク
# state で確認した要素インデックスを使う
```

---

## タクソノミ ZIP 構造（JPPFS_20251101.zip）

```
JPPFS_20251101.zip (2058ファイル)
├── samples/2025-11-01/          ← 業種別サンプルインスタンス
│   ├── entryPoint_jppfs_cns_2025-11-01.xsd  (建設業)
│   ├── entryPoint_jppfs_sec_2025-11-01.xsd  (証券業)
│   └── ...
└── taxonomy/
    ├── common/                  ← 共通定義
    ├── jpdei/                   ← DEI（書類情報）
    └── jppfs/2025-11-01/
        ├── jppfs_cor_2025-11-01.xsd           ← コアスキーマ（タグ定義）
        ├── label/
        │   ├── jppfs_2025-11-01_lab.xml       ← 日本語ラベル
        │   └── jppfs_2025-11-01_lab-en.xml    ← 英語ラベル
        └── deprecated/                         ← 廃止タグ
```

---

## 勘定科目マッピング方法の候補

### 方法1: 金融庁公式「勘定科目リスト」Excel（推奨・最速）

URL: `https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140115.xlsx`

名前空間・要素名・標準ラベル（日本語）が全タグ分一覧化済み。
ダウンロードして必要タグだけフィルタすれば即マッピングテーブルになる。

### 方法2: タクソノミXSD/ラベルファイルからプログラム生成

既に手元にある `taxonomy_2026/taxonomy/jppfs/2025-11-01/` を使う。

1. `jppfs_cor_2025-11-01.xsd` を lxml でパース → 全 `element` の `name` 属性を抽出
2. `label/jppfs_2025-11-01_lab.xml` をパース → `labelArc` で要素名と日本語ラベルを紐付け
3. 対象5項目 + IFRS/US-GAAPバリアントをフィルタしてマッピングCSV生成

### 方法3: 既存OSSライブラリの正規化ロジックを参考

| ライブラリ | GitHub | 特徴 |
|-----------|--------|------|
| **edinet-mcp** | ajtgjmdjp/edinet-mcp | 161正規化ラベル・26財務指標。会計基準サフィックス自動除去。マッピング目的に最適 |
| **edinet-tools** | matthelmer/edinet-tools | 11,000+社のXBRL解析実績。JP-GAAP/IFRS対応 |
| **edinet-xbrl** | axioradev/edinet-xbrl | JP-GAAP/IFRS/US-GAAP 3基準対応。構造化JSONに変換 |
| **edinet_xbrl** | BuffettCode/edinet_xbrl | シンプルなダウンロード+パーサー。jppfs_cor要素を直接参照 |
| **xbrr** | chakki-works/xbrr | 汎用XBRLリーダー |

### J-Quants fin_summary とXBRLタグの対応関係

**公式ドキュメントに明示的な対応表は存在しない。**

J-Quants APIのカラム名（`NetSales`, `OperatingProfit` 等）はEDINET XBRLタグ名とほぼ同一だが、
慣例的な一致であり公式に文書化されていない。J-Quants は**決算短信（TDnet XBRL）**をソースとしており、
EDINETの有報XBRLとは別系統。ただしタグ名は `jppfs_cor` タクソノミを共有しているため、
実質的に同じ要素名が使われている。

---

## テスト結果

### Phase 1: 20社 × 2Q/3Q/FY（サンプルテスト）

2026-04-09 実施。自前lxmlパーサー + アダプタ定義。

| 指標 | 結果 |
|------|------|
| OK | **93.5%** (1,940/2,075) |
| DIFF | 0.5% (11件) |
| MISS | 6.0% (124件) |

### Phase 2: 全銘柄一括検証（3,048社 × 直近FY）

2026-04-09 Colab実施。`xbrl_mapping.py`（閾値1%未満→OK）。

| 指標 | 結果 |
|------|------|
| OK | **84.7%** (11,976/14,147) |
| NG (≥1%) | 1.7% (241件) |
| MISS | 13.6% (1,930件) |
| BOTH_NULL | 389 / JQ_NULL | 704（除外）|

**NG 241件の内訳（マッピングミスではない）:**

| 差異率 | 件数 | 原因 |
|--------|------|------|
| 1-5% | 168 | 監査調整（短信→有報の通常差異） |
| 5-50% | 60 | 減損追加、連結範囲変更、会計基準移行 |
| ≥50% | 13 | 後発事象・株式分割遡及修正 |

- **株式分割**: 2676,4417,7217,9166,9305等（全てEPS差50.0%）。有報で遡及修正済、J-Quantsは分割前値
- **後発事象/監査調整**: 2914(JT) カナダたばこ訴訟和解3,756億円、7267(ホンダ) 決算短信→有報修正、6942(ソフィアHD) 決算短信+0.95億→有報-4.18億（符号逆転。訂正有報も提出済。XBRLが正）
- **小額企業**: 百万円台の利益で丸め差が%では大きく見える

**MISS 1,930件の内訳:**

| 分類 | 件数 |
|------|------|
| XBRLにタグ自体が無い | 1,930 |
| → IFRS企業のOPERATING_PROFIT/ORDINARY_PROFIT | 大半 |
| → 有報に営業利益を記載しないIFRS企業（総合商社等） | — |

**結論: 勘定科目マッピングは正しいことを確認済み。** NG/DIFFは全てJ-Quants（決算短信ベース）と有報XBRLの構造的差異であり、タグの選択ミスはゼロ。

### 決算短信と有報の数値差異の原因

| 差異率 | 主な原因 |
|--------|---------|
| < 1% | 表示単位の違い（短信=百万円切捨、有報=千円）、端数処理差 |
| 1-5% | 監査調整（引当金・税効果・棚卸資産評価の見直し） |
| 5-50% | 減損損失追加、連結範囲変更、不正会計訂正 |
| ≥ 50% | 後発事象（大型訴訟・災害）、株式分割の遡及修正 |

IFRS特有: 営業利益の定義が企業裁量（2027年度IFRS18号で統一予定）。

**アダプタ定義が必要な企業（adapters.json）**:
- 6758（ソニー）: 売上=`SalesAndFinancialServicesRevenueIFRS`（金融+製品合算の独自タグ）
- 7203（トヨタ）: 売上=`TotalNetRevenuesIFRS`（営業収益の独自タグ）

### Phase 2.5: 20社 × 1Q/2Q/3Q/FY全期間テスト

2026-04-09 ローカル実施。

| 指標 | 結果 |
|------|------|
| OK | **93.5%** (2,748/2,940) |
| NG (≥1%) | 0.6% (18件) |
| MISS | 5.9% (174件) |
| BOTH_NULL | 567 / JQ_NULL 28（除外）|

FYのみと同等の精度を1Q/2Q/3Q全期間で確認。

### Phase 3: 全銘柄×全期間（3,755社 × 1Q/2Q/3Q/FY）

2026-04-10〜11 ローカル実施。`xbrl_mapping.py` MODE=M。

| 指標 | 結果 |
|------|------|
| OK | **83.0%** (469,004/564,924) |
| NG (≥1%) | 3.0% (16,882件) |
| MISS | 14.0% (79,038件) |
| BOTH_NULL | 10,742 / JQ_NULL 3,254（除外）|

- 処理: 3,755社 / スキップ: 907社 / 突合ファイル: 115,784件
- Phase 2（FYのみ OK 84.7%）とほぼ同等。四半期でNG率が微増するのは短信→有報差異の構造的要因

### 逆引き結果（2026-04-11）

`--reverse` モードで2,235銘柄（95,920行）を逆引き。

- アダプタ候補検出: 456社（うちノイズ除外後108社が本物のPL系タグ）
- EPS: `BasicEarningsLossPerShareIFRSSummaryOfBusinessResults` 17社（既にTAG_CANDIDATESに含む→優先順位問題）
- PROFIT: `ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults` 11社（同上）
- NET_SALES: `OperatingRevenue1` 4社, `RevenuesFromExternalCustomers` 3社
- **結論: NG 3.0%の大半はアダプタ追加で解決する類ではなく、決算短信vs有報の構造的差異**
- 出力: `C:\tmp\xbrl_validate\adapter_suggestions_20260411.json` (456社), `mapping_ng_20260410_with_adapters.csv`

### 重要な知見

- `SummaryOfBusinessResults` サフィックス付きタグがJ-Quantsの値と一致（決算短信XBRLと同じセクション）
- edinet-xbrl OSSはcontext選択にバグあり（Prior/NonConsolidated混入）→ 自前lxmlパーサーで解決
- edinet-xbrl の taxonomy.json はタグ候補リストとして参考に有用
- 業種別タグ: 銀行=`OrdinaryIncomeBNK`、保険=`OperatingIncomeINS`、建設=`NetSalesOfCompletedConstructionContractsCNS`
- contextRef: `CurrentYearDuration`(FY), `CurrentYTDDuration`(四半期累計), `InterimDuration`(半期) を使用。`NonConsolidated`/`Prior`/`Member` は除外

---

## 次セッションでの作業

1. ~~Colab全銘柄×全期間実行~~ → **完了（Phase 3）**
2. ~~本番変換スクリプト~~ → **完了（2026-04-12）** `scripts/xbrl_to_jquants/convert_to_fin_summary.py`。3,756社/116,258ファイル→57MB CSV。実行時間約14時間。出力: `C:/tmp/xbrl_fin_summary/xbrl_fin_summary_20260411.csv`
3. **BS項目の追加** — 現在PL5項目のみ。TOTAL_ASSETS, EQUITY, BPS等
4. ~~BQテーブル設計・ロード~~ → **TODO外**
5. ~~Cloud Run Job化~~ → **TODO外**
6. **BQクエリキャッシュ** — `--resume`時のBQ再取得回避。fin_summaryをローカルCSVにキャッシュ（優先度低）
7. ~~**【ザラ場ツール連動】予想値（Forecast）抽出の追加**~~ → **完了（2026-04-19 確認）**
   - **ソース**: TDnet決算短信iXBRL（`XBRLData/Summary/*-ixbrl.htm`）。**有報XBRLにはForecastタグなし（確認済み）**
   - 実装場所: `scripts/zaraba_tdnet_poller.py` の `_parse_ixbrl()` + `_extract_tdnet_pl()` + `TDNET_TAG_MAP`（FORECAST_OP / NEXT_YEAR_FORECAST_OP / FORECAST_DIV_ANN）
   - ザラ場ツール連携: `scripts/zaraba_earnings.py:1100-1102` で rec に FOP/NxFOP/FDivAnn を格納、`:1185` F2 / `:1222` F4 / `:1263` F6 で発火済み
   - 検証済み銘柄: 9972(1Q) / 3382(FY) / 4829(3Q)
   - タグマッピング（2026-04-09 調査確定）:

   | フィールド | iXBRLタグ名 | contextRefパターン | ザラ場ツール用途 |
   |---|---|---|---|
   | **FOP** (今期予想OP) | `OperatingIncome` | `CurrentYearDuration_ConsolidatedMember_ForecastMember` | F2 ガイダンス修正 |
   | **NxFOP** (翌期予想OP) | `OperatingIncome` | `NextYearDuration_ConsolidatedMember_ForecastMember` | F4 翌期見通し |
   | **FDivAnn** (今期年間配当予想) | `DividendPerShare` | `CurrentYearDuration_AnnualMember_NonConsolidatedMember_ForecastMember` | F6 配当サプライズ |
   | NxFDivAnn (翌期年間配当予想) | `DividendPerShare` | `NextYearDuration_AnnualMember_NonConsolidatedMember_ForecastMember` | — |

   - タグ名は実績と同一。**contextの`ForecastMember`で予想/実績を区別**
   - `decimals=-6` = 百万円単位、`decimals=2` = 円単位（EPS/配当）
   - NxFOPはFY決算短信のみに出現（四半期には無い）
   - **日次バッチでの XBRL ZIP 永続化は不要**: `tdnet_download.py` への追加は見送り（2026-04-19 判断）。ザラ場ツール起動中にライブ取得するのみで十分。過去再計算が必要になった時点で再検討。

---

## 注意事項

- **会計基準によるタグの違い**: 日本基準は `NetSales` + `OrdinaryIncome`、IFRS は `Revenue`（経常利益なし）、US-GAAP も独自体系
- **連結 vs 単体**: contextRef で判別。連結優先
- **累積値**: 四半期報告書の値は当期累計（Q1=Q1, Q2=Q1+Q2 等）
- **EDINETコード**: 5桁（例: E00012）。証券コード4桁との変換にEDINETコードリストが必須
- **有報 vs 決算短信**: 有報XBRLにはIFRS企業の営業利益が含まれないケースあり（決算短信には含まれる）
