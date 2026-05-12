# XBRL → J-Quants形式 変換ツール（xbrl_to_jquants）

**カテゴリ**: tools
**作成日**: 2026-04-08
**ステータス**: 開発中
**関連ファイル**:
- `scripts/xbrl_to_jquants/` — 専用フォルダ（スクリプト・メタデータ・タクソノミ一式）
- `scripts/zaraba_tdnet_poller.py` — ザラ場ツール用TDnet iXBRLパーサー。`TDNET_TAG_MAP` / `_extract_tdnet_pl()` / 営業収入合算ロジックは本プロジェクトの成果物。**勘定科目マッピング（TAG_CANDIDATES）を変更したらTDNET_TAG_MAPも同期更新すること**
- `docs/knowledges/tools/066_zaraba_tool.md`（ザラ場ツール全体設計。本ツールはザラ場ツールのXBRLパーサー部分を担当）

---

## 🔴 最優先確認 TODO

- **`extract_pipeline.py` の `valid_entries[0]` 順序依存バグ確認**（2026-04-28 発見）
  - `xbrl_mapping.py` の `CURRENT_DURATION_CONTEXTS` に `CurrentQuarterDuration` が含まれており、`extract_pipeline.py:164-173` の `valid_entries[0]` で Q4 単独値が通期累計値より先に採用される可能性がある
  - 同種バグが `zaraba_tdnet_poller.py` で 6902 デンソー FY「翌期↑+792%」として顕在化し修正済み（累計 context 優先フィルタ追加）
  - `EXCLUDE_CONTEXT_PATTERNS` に `Member` があるため `ResultMember` 付き context は除外されるが、`Member` を含まない `CurrentQuarterDuration` 単独 context が存在する場合はすり抜ける
  - **対応**: EDINET XBRL の実データで FY 決算書に `CurrentQuarterDuration`（Q4単独）context が出現するか確認 → 出現するなら `zaraba_tdnet_poller.py` と同様の累計優先ロジックを追加
  - レビュー詳細: `docs/reviews/013_zaraba_xbrl_quarter_vs_cumulative.md` 重大指摘 #2

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

---

## 検証結果サマリー（Phase 3: 全銘柄×全期間）

2026-04-10〜11 ローカル実施。`xbrl_mapping.py` MODE=M。3,755社 × 1Q/2Q/3Q/FY、115,784突合ファイル。

| 指標 | 結果 |
|------|------|
| OK (差異<1%) | **83.0%** (469,004/564,924) |
| NG (≥1%) | 3.0% (16,882件) |
| MISS | 14.0% (79,038件) |
| BOTH_NULL / JQ_NULL（除外） | 10,742 / 3,254 |

**NG 3.0% の内訳（マッピングミスではない）:**

| 差異率 | 主な原因 |
|--------|---------|
| < 1% | 表示単位の違い（短信=百万円切捨、有報=千円）、端数処理差 |
| 1-5% | 監査調整（引当金・税効果・棚卸資産評価の見直し） |
| 5-50% | 減損損失追加、連結範囲変更、会計基準移行 |
| ≥ 50% | 後発事象（大型訴訟・災害）、株式分割の遡及修正 |

**MISS 14% の主因**: IFRS企業のOPERATING_PROFIT/ORDINARY_PROFIT — 有報XBRLにはIFRS企業の営業利益が含まれないケースあり（決算短信には含まれる）。

**結論: 勘定科目マッピングは正しい。** NG/DIFFはJ-Quants（決算短信ベース）と有報XBRLの構造的差異であり、タグの選択ミスはゼロ。

### 逆引き結果（2026-04-11）

`--reverse` モードで2,235銘柄（95,920行）を逆引き。
- アダプタ候補検出: 456社（ノイズ除外後108社が本物のPL系タグ）
- **結論**: NG 3.0%の大半はアダプタ追加で解決する類ではなく、決算短信vs有報の構造的差異

---

## 重要な知見

- **`SummaryOfBusinessResults` サフィックス付きタグ**がJ-Quantsの値と一致（決算短信XBRLと同じセクション）
- **edinet-xbrl OSSはcontext選択にバグあり**（Prior/NonConsolidated混入）→ 自前lxmlパーサーで解決
- **contextRef パターン**: `CurrentYearDuration`(FY), `CurrentYTDDuration`(四半期累計), `InterimDuration`(半期) を使用。`NonConsolidated`/`Prior`/`Member` は除外（連結優先）
- **業種別タグ**: 銀行=`OrdinaryIncomeBNK`、保険=`OperatingIncomeINS`、建設=`NetSalesOfCompletedConstructionContractsCNS`
- **アダプタが必要な企業**:
  - 6758（ソニー）: 売上=`SalesAndFinancialServicesRevenueIFRS`（金融+製品合算の独自タグ）
  - 7203（トヨタ）: 売上=`TotalNetRevenuesIFRS`（営業収益の独自タグ）
- **J-Quants とXBRLタグの対応関係**: 公式ドキュメントに明示的な対応表は存在しない。J-Quantsは決算短信（TDnet XBRL）をソースとしており有報XBRLとは別系統だが、タグ名は `jppfs_cor` タクソノミを共有しているため実質的に同じ要素名が使われている

---

## 予想値（Forecast）抽出仕様（ザラ場ツール連動）

**ソース**: TDnet決算短信iXBRL（`XBRLData/Summary/*-ixbrl.htm`）。**有報XBRLにはForecastタグなし（確認済み）**

実装場所: `scripts/zaraba_tdnet_poller.py` の `_parse_ixbrl()` + `_extract_tdnet_pl()` + `TDNET_TAG_MAP`

| フィールド | iXBRLタグ名 | contextRefパターン | ザラ場ツール用途 |
|---|---|---|---|
| **FOP** (今期予想OP) | `OperatingIncome` | `CurrentYearDuration_ConsolidatedMember_ForecastMember` | F2 ガイダンス修正 |
| **NxFOP** (翌期予想OP) | `OperatingIncome` | `NextYearDuration_ConsolidatedMember_ForecastMember` | F4 翌期見通し |
| **FDivAnn** (今期年間配当予想) | `DividendPerShare` | `CurrentYearDuration_AnnualMember_NonConsolidatedMember_ForecastMember` | F6 配当サプライズ |
| NxFDivAnn (翌期年間配当予想) | `DividendPerShare` | `NextYearDuration_AnnualMember_NonConsolidatedMember_ForecastMember` | — |

- タグ名は実績と同一。**contextの`ForecastMember`で予想/実績を区別**
- `decimals=-6` = 百万円単位、`decimals=2` = 円単位（EPS/配当）
- NxFOPはFY決算短信のみに出現（四半期には無い）
- **日次バッチでの XBRL ZIP 永続化は不要**（ザラ場ツール起動中にライブ取得のみ。2026-04-19 判断）

### 予想 context の扱い（当期/翌期 × 通期/累計Q の4象限）

| context | 期間 | 扱い |
|---|---|---|
| `CurrentYearDuration_..._ForecastMember` | 今期通期 | `FOP`（通期予想。進捗率・上方/下方修正に使用） |
| `NextYearDuration_..._ForecastMember` | 翌期通期 | `NxFOP`（翌期通期予想。F4 翌期見通しに使用） |
| `CurrentAccumulatedQ1/2/3Duration_..._ForecastMember` | 今期1Q/2Q/3Q 累計 | `ShortFOP`（短期予想。**スコアリング評価に使わない**） |
| `NextAccumulatedQ1/2/3Duration_..._ForecastMember` | 翌期1Q/2Q/3Q 累計 | 翌期短期予想。**スコアリング評価に使わない** |

`AccumulatedQ` を含む context は累計四半期予想であり通期予想ではない。短期予想を通期予想として代用すると進捗率・上方/下方修正・翌期見通しの全てで誤判定につながる。

```python
# scripts/zaraba_tdnet_poller.py
TDNET_FORECAST_CURRENT_PATTERNS    = ["CurrentYearDuration"]   # FOP
TDNET_FORECAST_NEXTYEAR_PATTERNS   = ["NextYearDuration"]      # NxFOP
TDNET_FORECAST_SHORT_TERM_PATTERNS = ["CurrentAccumulatedQ"]   # SHORT_TERM_FORECAST_OP
```

---

## 注意事項

- **iXBRL `sign` 属性の処理必須**: `ix:nonFraction` 要素の `sign="-"` 属性は値の符号反転を意味する（営業損失等）。`_parse_ixbrl()` で `attrs.get("sign") == "-"` なら `scaled_value = -scaled_value` する。**未対応だと赤字企業（帝人3401 FY OP -707億）が黒字（+707億）に誤認され、SELL→S-Buy と真逆の判定になる**（2026-05-12 修正）
- **FY再処理時の `prev_cumulative_op`**: prepare時にlatestがFYの場合、rn=2（3Q）の累計値を`prev_cumulative_op`に格納する。None のままだと FY 処理時に standalone_op = cumulative（通期合計）となり F13 QoQ が異常値になる（未来工業7931 QoQ+436%事故、2026-05-12 修正）。1Q処理は `cur_per=="1Q"` ガードで影響なし
- **会計基準によるタグの違い**: 日本基準は `NetSales` + `OrdinaryIncome`、IFRS は `Revenue`（経常利益なし）、US-GAAP も独自体系
- **連結 vs 単体**: contextRef で判別。連結優先
- **累積値**: 四半期報告書の値は当期累計（Q1=Q1, Q2=Q1+Q2 等）
- **EDINETコード**: 5桁（例: E00012）。証券コード4桁との変換にEDINETコードリストが必須
- **有報 vs 決算短信**: 有報XBRLにはIFRS企業の営業利益が含まれないケースあり（決算短信には含まれる）
- **ローカル実行時の出力先**: `C:/tmp/xbrl_validate/`（Google Driveロック問題回避）
- **本番変換スクリプト実績**: `convert_to_fin_summary.py` で3,756社/116,258ファイル→57MB CSV、実行時間約14時間
