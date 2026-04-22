# 株主構成データ抽出（fetch_shareholder_composition.py）

**カテゴリ**: tools
**作成日**: 2026-04-20
**ステータス**: 有効
**関連ファイル**:
- `scripts/fetch_shareholder_composition.py` — 新規作成
- `scripts/fetch_tob_shareholders.py` — XBRL大株主抽出の原型
- `scripts/flag_activists_in_list.py` — アクティビスト判定ロジック（import流用）
- `data/master/activists.csv` / `activist_aliases.csv` — マスタ
- BQ `STOCK.SHAREHOLDER_COMPOSITION` — 出力先
- 計画: `docs/plans/tools-081_shareholder_composition_20260420_155621.md`

---

## 概要

EDINET 有価証券報告書 XBRL から株主構成データを抽出し、BQ `STOCK.SHAREHOLDER_COMPOSITION` に年次スナップショットとして格納する。TOB予測モデル（`analysis/007_tob_ml_prediction.md`）の説明変数として使用。

## 対象データ

| カラム | XBRL取得元 |
|---|---|
| FOREIGN_RATIO | `jpcrp_cor:PercentageOfShareholdingsForeignIndividuals` + `ForeignersOtherThanIndividuals` |
| INDIVIDUAL_RATIO | `jpcrp_cor:PercentageOfShareholdingsIndividualsAndOthers` |
| FINANCIAL_INST_RATIO | `jpcrp_cor:PercentageOfShareholdingsFinancialInstitutions` + `FinancialServiceProviders` |
| OTHER_CORP_RATIO | `jpcrp_cor:PercentageOfShareholdingsOtherCorporations` |
| TREASURY_RATIO | 1.0 - (他カテゴリ合計) の概算 |
| TOP_SHAREHOLDER_NAME / TOP_SHAREHOLDER_RATIO | `jpcrp_cor:MajorShareholdersTextBlock` の HTMLテーブル1行目 |
| TOP10_CONCENTRATION | MajorShareholdersTextBlock の上位10合計 |
| HAS_ACTIVIST / ACTIVIST_NAMES / ACTIVIST_MAX_SCORE | `flag_activists_in_list.py::match_activist()` でtop10判定 |

## 実行コマンド

```bash
# 単一ticker/year で動作確認（BQ書き込みなし）
PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --ticker 7203 --year 2020 --dry-run

# 全銘柄 × 2013-2026 バックフィル
PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --all

# 欠損のみ補完（未実装：今後TODO）
PYTHONUTF8=1 python scripts/fetch_shareholder_composition.py --missing-only
```

## データソース取得優先順位

1. **GCS キャッシュ**: `gs://stock_data_1930932/edinet/{ticker}/` 配下の `有報年_*_S100*.xbrl`
2. **EDINET API 直接取得**: docTypeCode=120（有価証券報告書）を日付スキャンで見つけてZIPダウンロード→展開

ローカルキャッシュ: `C:\tmp\shareholder_xbrl\{ticker}_{year}\`

日付レベルキャッシュ: `C:\tmp\edinet_cache\dates\YYYY-MM-DD.json`（TOBバックフィル時と共有）

## 実装ノウハウ（落とし穴）

### 1. context 選択: OrdinaryShareMember 優先
XBRL の `<jpcrp_cor:PercentageOfShareholdings*>` タグは複数 context で値を持つ:
- `CurrentYearInstant_OrdinaryShareMember` → 普通株式 ✓ 正解
- `CurrentYearInstant_FirstSeriesModelAAClassSharesMember` → AA種株式（例: トヨタ）

最後のマッチを取ると AA種等のゼロ値を拾うので、**`OrdinaryShareMember` / `OrdinarySharesMember` context を優先** する。

### 2. PublicDoc 優先（AuditDoc除外）
EDINET API の ZIP には `AuditDoc/` と `PublicDoc/` の両方に .xbrl がある。`PublicDoc/` が本体。walk で最初に見つかった方を選ぶと AuditDoc を誤選択することがある。

### 3. 比率は常に /100

有報の大株主テーブルは **原則 percentage 表記** (`"21.25"` = 21.25%、`"1.49"` = 1.49%)。

**失敗例**（採用してはいけないヒューリスティック）:
```python
# ❌ 1.5 以下は「既に比率 (0-1)」と誤判定
ratio = v / 100 if v > 1.5 else v
```

SoftBank 9984 のように **TOP1 は 21.25%** (> 1.5 → 正しく /100)、**TOP7-10 は 1.49%, 1.41%, 1.37%, 1.14%** (<= 1.5 → /100 されず) で **top10_concentration が 5.84** (=584%) という明らかに異常な値が出る。

**正解**:
```python
# ✅ 常に /100 を適用（mixed 表記は想定しない）
ratio = v / 100
```

有報テーブル内で percentage と ratio が混在するケースは実質ゼロ（あっても破棄してよいレアケース）。シンプルに /100 固定が安全。

### 4. fiscal_year_end は `jpdei_cor:CurrentFiscalYearEndDateDEI`
`submit_date` は `<xbrli:context id="FilingDateInstant">/instant` で取得。

### 5. 筆頭株主「国内上場」判定の正規化マッチ過剰検出に注意

`scripts/apply_shareholder_listing_flag.py` で正規化後の部分マッチ（`len(norm) >= 8` かつ `key in norm` or `norm in key`）は **誤検出多発**。

**失敗例（ドライラン時に発覚）**:
- `ADVANTECH CO., LTD.` → 8247（無関係、8文字以上の部分一致で誤マッチ）
- `Apaman Network株式会社` → 441A（部分文字列一致で誤マッチ）
- `BAIN CAPITAL SKYLARK HONG KONG LIMITED` → 7462（スカイラーク=ticker 3197とは無関係）

**採用した方針**: **正規化後の厳密一致のみ**。それ以外はすべて Gemini フォールバックへ委譲。

```python
# ✅ 厳密一致のみ
if norm in name_to_ticker:
    matched[name] = name_to_ticker[norm]
else:
    unmatched.append(name)  # Gemini に委譲
```

マッチ率は 9.1% (516/5,663) に下がるが、残り 5,147 は `gemini-3-flash-preview` バッチで適切に判定（751 が上場、残りは信託口・個人・外国機関等で非上場）。**精度 >> 速度/コスト**。

## 全量バックフィル実績 (2026-04-20完了)

| 指標 | 値 |
|---|---|
| BQ 総行数 | 37,657 |
| unique TICKER | 4,411 |
| FY範囲 | 2015-2026 |
| HAS_ACTIVIST=TRUE | 2,217行 (5.9%) |
| TOP_SHAREHOLDER_IS_PUBLIC=TRUE | 7,153行 (19%) |
| TOP_SHAREHOLDER_IS_PUBLIC=FALSE | 30,454行 (81% = 信託口・個人・外国機関等) |
| FOREIGN_RATIO 有 | 26,453行 (70%) |
| エラー率 | 0.27% (86/31,543) |

処理時間: ~1時間（10並列、逐次削除、BQ batch INSERT）

## 5銘柄 検証実績 (2026-04-20)

| ticker | 会社 | TOP_RATIO | FOREIGN | INDIV | FIN_INST | OTHER |
|---|---|---|---|---|---|---|
| 7203 | トヨタ | 12.71% | 19.72% | 25.77% | 34.24% | 20.28% |
| 9984 | ソフトバンクG | 21.25% (孫正義) | 38.69% | 30.44% | 25.79% | 5.06% |
| 6861 | キーエンス | 15.07% | 49.24% | 10.70% | 24.57% | 15.49% |
| 7974 | 任天堂 | 6.43% | 51.02% | 15.65% | 29.85% | 3.48% |
| 4502 | 武田薬品 | 7.98% | 46.33% | 18.47% | 32.59% | 2.62% |

5銘柄すべて合計 ≈ 100%（+ 自己株式）で論文SHAP特徴量を再現可能。

## 関連

- 計画ファイル: `docs/plans/tools-081_shareholder_composition_20260420_155621.md`
- TOB予測モデル: `docs/knowledges/analysis/007_tob_ml_prediction.md`
- EDINET API: `docs/knowledges/api/006_edinet_api.md`
