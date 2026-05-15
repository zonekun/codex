# STOCK.fin_summary
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.fin_summary` | J-Quants 財務サマリー（/fins/summary） | 日次 | Cloud Run Job `jquants-fin-summary` で WRITE_APPEND ロード |
| `gmailpj-357912.STOCK.V_LATEST_DISCLOSURE` | 各銘柄の最新開示情報 + 当期FY導出 VIEW | リアルタイム（VIEW） | fin_summary から導出。コンセンサスFY判定の標準参照先 |

---

**`STOCK.V_LATEST_DISCLOSURE` — 最新開示 + 当期FY VIEW（2026-05-12 作成）**

各銘柄の直近 FinancialStatements 開示から、当期FY（YYYYMM）を導出する VIEW。`_derive_current_fy` パターン（`zaraba_earnings.py` / `predict.py` 由来）を SQL 化。

| カラム名 | 型 | 説明 |
|---------|-----|------|
| TICKER | STRING | 銘柄コード（4桁） |
| LATEST_TYPE | STRING | 直近開示の期種別（`FY` / `4Q` / `3Q` / `2Q` / `1Q`） |
| FY_END | STRING | 直近開示の会計年度末（YYYYMM） |
| DISCLOSED_DATE | DATE | 直近開示日 |
| CURRENT_FY | STRING | 当期FY（YYYYMM）。LATEST_TYPE が FY/4Q なら翌年度、1Q/2Q/3Q なら同年度 |

> **導出ロジック**: `DISCLOSED_DATE DESC, DISCLOSED_TIME DESC` で各銘柄の最新 FinancialStatements を特定。`TYPE_OF_CURRENT_PERIOD` が FY/4Q なら `FY_END` の年+1、それ以外なら同年を `CURRENT_FY` とする。
>
> **使用箇所**: `lib_conse_csv_from_view.py`（コンセンサスCSV出力）、`export_consensus_csv.py`（旧RAKU形式CSV）

**`STOCK.fin_summary` — J-Quants API `/v2/fins/summary` 仕様**

> API仕様参照: https://jpx.gitbook.io/j-quants-ja/api-reference/statements

---

**■ TYPE_OF_DOCUMENT（開示書類種別）**

書類の期間・連結区分・会計基準の組み合わせ。全45種。

| パターン | 説明 |
|---------|------|
| `{期間}FinancialStatements_{連結区分}_{会計基準}` | 定期開示（決算・四半期） |
| `DividendForecastRevision` | 配当予想修正 |
| `EarnForecastRevision` | 業績予想修正 |
| `REITDividendForecastRevision` | REIT配当予想修正 |
| `REITEarnForecastRevision` | REIT業績予想修正 |

**期間プレフィックス:** `FY`（通期）/ `1Q`〜`3Q`（四半期）/ `OtherPeriod`（変則期間）

**連結区分:** `Consolidated`（連結）/ `NonConsolidated`（単体）

**会計基準:**
| 値 | 内容 | 経常利益 |
|----|------|---------|
| `JP` | 日本基準（J-GAAP） | あり |
| `IFRS` | 国際財務報告基準 | **なし（空欄）** |
| `US` | 米国基準（US-GAAP） | **なし（空欄）** |
| `JMIS` | 修正国際基準 | あり |
| `Foreign` | 外国会計基準（外国株等） | ケースによる |
| `REIT` | J-REIT（不動産投資信託） | なし |

完全一覧（45種）:
```
FYFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
1QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
2QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
3QFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
OtherPeriodFinancialStatements_Consolidated_JP / US / NonConsolidated_JP
FYFinancialStatements_Consolidated_JMIS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_NonConsolidated_IFRS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_IFRS / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_NonConsolidated_Foreign / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_Foreign / 1Q / 2Q / 3Q / OtherPeriod
FYFinancialStatements_Consolidated_REIT
DividendForecastRevision / EarnForecastRevision
REITDividendForecastRevision / REITEarnForecastRevision
```

**■ TYPE_OF_CURRENT_PERIOD（当期種別）**

`1Q` / `2Q` / `3Q` / `4Q` / `5Q` / `FY`

---

**■ 全カラム一覧（107列）**

**【開示メタ情報】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| DISCLOSED_DATE | DiscDate | DATE | 開示日 |
| DISCLOSED_TIME | DiscTime | TIME | 開示時刻 |
| LOCAL_CODE | Code | STRING | 銘柄コード（4桁。API は5桁で返すが末尾除去済み） |
| DISCLOSURE_NUMBER | DiscNo | STRING | 開示番号（昇順ソートキー） |
| TYPE_OF_DOCUMENT | DocType | STRING | 書類種別（上記参照） |
| TYPE_OF_CURRENT_PERIOD | CurPerType | STRING | 当期種別（1Q/2Q/3Q/4Q/5Q/FY） |
| CURRENT_PERIOD_START_DATE | CurPerSt | DATE | 当期開始日 |
| CURRENT_PERIOD_END_DATE | CurPerEn | DATE | 当期終了日 |
| CURRENT_FISCAL_YEAR_START_DATE | CurFYSt | DATE | 当会計年度開始日 |
| CURRENT_FISCAL_YEAR_END_DATE | CurFYEn | DATE | 当会計年度終了日 |
| NEXT_FISCAL_YEAR_START_DATE | NxtFYSt | DATE | 次会計年度開始日（空欄あり） |
| NEXT_FISCAL_YEAR_END_DATE | NxtFYEn | DATE | 次会計年度終了日（空欄あり） |

**【連結 実績（PL・BS・CF）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NET_SALES | Sales | INTEGER | 売上高（IFRS/US-GAAP では売上収益） |
| OPERATING_PROFIT | OP | INTEGER | 営業利益 |
| ORDINARY_PROFIT | OdP | INTEGER | 経常利益（**IFRS・US-GAAP は空欄**） |
| PROFIT | NP | INTEGER | 当期純利益（親会社株主帰属） |
| EARNINGS_PER_SHARE | EPS | FLOAT | EPS（一株当たり利益） |
| DILUTED_EARNINGS_PER_SHARE | DEPS | FLOAT | 希薄化後EPS |
| TOTAL_ASSETS | TA | INTEGER | 総資産 |
| EQUITY | Eq | INTEGER | 純資産（自己資本） |
| EQUITY_TO_ASSET_RATIO | EqAR | FLOAT | 自己資本比率（%） |
| BOOK_VALUE_PER_SHARE | BPS | FLOAT | BPS（一株当たり純資産） |
| CASH_FLOWS_FROM_OPERATING_ACTIVITIES | CFO | INTEGER | 営業CF |
| CASH_FLOWS_FROM_INVESTING_ACTIVITIES | CFI | INTEGER | 投資CF |
| CASH_FLOWS_FROM_FINANCING_ACTIVITIES | CFF | INTEGER | 財務CF |
| CASH_AND_EQUIVALENTS | CashEq | INTEGER | 現金及び現金同等物 |

**【連結 配当実績】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| RESULT_DIVIDEND_PER_SHARE_1ST_QUARTER | Div1Q | FLOAT | 一株配当（第1四半期末） |
| RESULT_DIVIDEND_PER_SHARE_2ND_QUARTER | Div2Q | FLOAT | 一株配当（第2四半期末・中間） |
| RESULT_DIVIDEND_PER_SHARE_3RD_QUARTER | Div3Q | FLOAT | 一株配当（第3四半期末） |
| RESULT_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | DivFY | FLOAT | 一株配当（期末） |
| RESULT_DIVIDEND_PER_SHARE_ANNUAL | DivAnn | FLOAT | 一株配当（年間合計） |
| DISTRIBUTIONS_PER_UNIT_REIT | DivUnit | FLOAT | 一口当たり分配金（REIT） |
| RESULT_TOTAL_DIVIDEND_PAID_ANNUAL | DivTotalAnn | INTEGER | 配当金総額（年間） |
| RESULT_PAYOUT_RATIO_ANNUAL | PayoutRatioAnn | FLOAT | 配当性向（年間、%） |

**【連結 配当予想（当期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER | FDiv1Q | FLOAT | 予想一株配当（第1四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER | FDiv2Q | FLOAT | 予想一株配当（第2四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER | FDiv3Q | FLOAT | 予想一株配当（第3四半期末） |
| FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | FDivFY | FLOAT | 予想一株配当（期末） |
| FORECAST_DIVIDEND_PER_SHARE_ANNUAL | FDivAnn | FLOAT | 予想一株配当（年間合計） |
| FORECAST_DISTRIBUTIONS_PER_UNIT_REIT | FDivUnit | FLOAT | 予想一口当たり分配金（REIT） |
| FORECAST_TOTAL_DIVIDEND_PAID_ANNUAL | FDivTotalAnn | INTEGER | 予想配当金総額（年間） |
| FORECAST_PAYOUT_RATIO_ANNUAL | FPayoutRatioAnn | FLOAT | 予想配当性向（年間、%） |

**【連結 配当予想（翌期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_1ST_QUARTER | NxFDiv1Q | FLOAT | 翌期予想一株配当（Q1末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_2ND_QUARTER | NxFDiv2Q | FLOAT | 翌期予想一株配当（Q2末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_3RD_QUARTER | NxFDiv3Q | FLOAT | 翌期予想一株配当（Q3末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_FISCAL_YEAR_END | NxFDivFY | FLOAT | 翌期予想一株配当（期末） |
| NEXT_YEAR_FORECAST_DIVIDEND_PER_SHARE_ANNUAL | NxFDivAnn | FLOAT | 翌期予想一株配当（年間） |
| NEXT_YEAR_FORECAST_DISTRIBUTIONS_PER_UNIT_REIT | NxFDivUnit | FLOAT | 翌期予想一口分配金（REIT） |
| NEXT_YEAR_FORECAST_PAYOUT_RATIO_ANNUAL | NxFPayoutRatioAnn | FLOAT | 翌期予想配当性向（%） |

**【連結 業績予想（当期 第2四半期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NET_SALES_2ND_QUARTER | FSales2Q | INTEGER | 予想売上高（第2四半期累計） |
| FORECAST_OPERATING_PROFIT_2ND_QUARTER | FOP2Q | INTEGER | 予想営業利益（第2四半期累計） |
| FORECAST_ORDINARY_PROFIT_2ND_QUARTER | FOdP2Q | INTEGER | 予想経常利益（第2四半期累計） |
| FORECAST_PROFIT_2ND_QUARTER | FNP2Q | INTEGER | 予想純利益（第2四半期累計） |
| FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER | FEPS2Q | FLOAT | 予想EPS（第2四半期累計） |

**【連結 業績予想（翌期 第2四半期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NET_SALES_2ND_QUARTER | NxFSales2Q | INTEGER | 翌期予想売上高（Q2累計） |
| NEXT_YEAR_FORECAST_OPERATING_PROFIT_2ND_QUARTER | NxFOP2Q | INTEGER | 翌期予想営業利益（Q2累計） |
| NEXT_YEAR_FORECAST_ORDINARY_PROFIT_2ND_QUARTER | NxFOdP2Q | INTEGER | 翌期予想経常利益（Q2累計） |
| NEXT_YEAR_FORECAST_PROFIT_2ND_QUARTER | NxFNp2Q | INTEGER | 翌期予想純利益（Q2累計） |
| NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE_2ND_QUARTER | NxFEPS2Q | FLOAT | 翌期予想EPS（Q2累計） |

**【連結 業績予想（当期 通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NET_SALES | FSales | INTEGER | 予想売上高（通期） |
| FORECAST_OPERATING_PROFIT | FOP | INTEGER | 予想営業利益（通期） |
| FORECAST_ORDINARY_PROFIT | FOdP | INTEGER | 予想経常利益（通期） |
| FORECAST_PROFIT | FNP | INTEGER | 予想純利益（通期） |
| FORECAST_EARNINGS_PER_SHARE | FEPS | FLOAT | 予想EPS（通期） |

**【連結 業績予想（翌期 通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NET_SALES | NxFSales | INTEGER | 翌期予想売上高（通期） |
| NEXT_YEAR_FORECAST_OPERATING_PROFIT | NxFOP | INTEGER | 翌期予想営業利益（通期） |
| NEXT_YEAR_FORECAST_ORDINARY_PROFIT | NxFOdP | INTEGER | 翌期予想経常利益（通期） |
| NEXT_YEAR_FORECAST_PROFIT | NxFNp | INTEGER | 翌期予想純利益（通期） |
| NEXT_YEAR_FORECAST_EARNINGS_PER_SHARE | NxFEPS | FLOAT | 翌期予想EPS（通期） |

**【会計変更フラグ（BOOLEAN）】**

| BQカラム名 | API略称 | 説明 | 備考 |
|-----------|--------|------|------|
| MATERIAL_CHANGES_IN_SUBSIDIARIES | MatChgSub | 重要な子会社の異動 | |
| SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION | SigChgInC | 連結範囲の重要な変更 | **2024-07-22以前は空欄** |
| CHANGES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD | ChgByASRev | 会計基準等の改正に伴う変更 | |
| CHANGES_OTHER_THAN_ONES_BASED_ON_REVISIONS_OF_ACCOUNTING_STANDARD | ChgNoASRev | 会計方針の変更（基準改正以外） | |
| CHANGES_IN_ACCOUNTING_ESTIMATES | ChgAcEst | 会計上の見積りの変更 | |
| RETROSPECTIVE_RESTATEMENT | RetroRst | 修正再表示 | |

**【株式数】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NUMBER_OF_ISSUED_AND_OUTSTANDING_SHARES_AT_THE_END_OF_FISCAL_YEAR_INCLUDING_TREASURY_STOCK | ShOutFY | INTEGER | 発行済株式数（自己株式含む、期末） |
| NUMBER_OF_TREASURY_STOCK_AT_THE_END_OF_FISCAL_YEAR | TrShFY | INTEGER | 自己株式数（期末） |
| AVERAGE_NUMBER_OF_SHARES | AvgSh | INTEGER | 加重平均株式数 |

**【単体 実績（PL・BS）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NON_CONSOLIDATED_NET_SALES | NCSales | INTEGER | 単体 売上高 |
| NON_CONSOLIDATED_OPERATING_PROFIT | NCOP | INTEGER | 単体 営業利益 |
| NON_CONSOLIDATED_ORDINARY_PROFIT | NCOdP | INTEGER | 単体 経常利益 |
| NON_CONSOLIDATED_PROFIT | NCNP | INTEGER | 単体 当期純利益 |
| NON_CONSOLIDATED_EARNINGS_PER_SHARE | NCEPS | FLOAT | 単体 EPS |
| NON_CONSOLIDATED_TOTAL_ASSETS | NCTA | INTEGER | 単体 総資産 |
| NON_CONSOLIDATED_EQUITY | NCEq | INTEGER | 単体 純資産 |
| NON_CONSOLIDATED_EQUITY_TO_ASSET_RATIO | NCEqAR | FLOAT | 単体 自己資本比率（%） |
| NON_CONSOLIDATED_BOOK_VALUE_PER_SHARE | NCBPS | FLOAT | 単体 BPS |

**【単体 業績予想（当期 第2四半期・通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER | FNCSales2Q | INTEGER | 単体 予想売上高（Q2累計） |
| FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER | FNCOP2Q | INTEGER | 単体 予想営業利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER | FNCOdP2Q | INTEGER | 単体 予想経常利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER | FNCNP2Q | INTEGER | 単体 予想純利益（Q2累計） |
| FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER | FNCEPS2Q | FLOAT | 単体 予想EPS（Q2累計） |
| FORECAST_NON_CONSOLIDATED_NET_SALES | FNCSales | INTEGER | 単体 予想売上高（通期） |
| FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT | FNCOP | INTEGER | 単体 予想営業利益（通期） |
| FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT | FNCOdP | INTEGER | 単体 予想経常利益（通期） |
| FORECAST_NON_CONSOLIDATED_PROFIT | FNCNP | INTEGER | 単体 予想純利益（通期） |
| FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE | FNCEPS | FLOAT | 単体 予想EPS（通期） |

**【単体 業績予想（翌期 第2四半期・通期）】**

| BQカラム名 | API略称 | 型 | 説明 |
|-----------|--------|-----|------|
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES_2ND_QUARTER | NxFNCSales2Q | INTEGER | 単体 翌期予想売上高（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT_2ND_QUARTER | NxFNCOP2Q | INTEGER | 単体 翌期予想営業利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT_2ND_QUARTER | NxFNCOdP2Q | INTEGER | 単体 翌期予想経常利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT_2ND_QUARTER | NxFNCNP2Q | INTEGER | 単体 翌期予想純利益（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE_2ND_QUARTER | NxFNCEPS2Q | FLOAT | 単体 翌期予想EPS（Q2累計） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_NET_SALES | NxFNCSales | INTEGER | 単体 翌期予想売上高（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_OPERATING_PROFIT | NxFNCOP | INTEGER | 単体 翌期予想営業利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_ORDINARY_PROFIT | NxFNCOdP | INTEGER | 単体 翌期予想経常利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_PROFIT | NxFNCNP | INTEGER | 単体 翌期予想純利益（通期） |
| NEXT_YEAR_FORECAST_NON_CONSOLIDATED_EARNINGS_PER_SHARE | NxFNCEPS | FLOAT | 単体 翌期予想EPS（通期） |

---

**データ収集フロー:**
```
J-Quants API V2 /fins/summary（日付ループ + ページネーション）
  → scripts/jquants_get_fin_summary.py
  → Cloud Run Job: jquants-fin-summary（us-west1、毎日21:00 JST）
  → BigQuery STOCK.fin_summary（WRITE_APPEND）
```

**注意事項:**
- **経常利益は IFRS・US-GAAP では空欄**（OdP / NCOdP）。会計基準でフィルタ必須
- **ORDINARY_PROFIT が空の場合**: `TYPE_OF_DOCUMENT` に `IFRS` または `US` を含む行
- **累積値に注意**: 財務数値は当期累計（Q1=Q1、Q2=Q1+Q2、Q3=Q1+Q2+Q3）。単独四半期への変換は差分計算が必要
- **LOCAL_CODE は4桁**（API が返す5桁コードの末尾 "0" を除去済み）
- **WRITE_APPEND**: 同一日付を二重実行すると重複ロードになる。補完実行後に日次ジョブが同日をロードしないよう注意
- **欠損値は NULL**（API は空文字列 `""` で返すが preprocess で None 変換済み）
- **SIGNIFICANT_CHANGES_IN_THE_SCOPE_OF_CONSOLIDATION**: 2024-07-22以前のデータは空欄
- 詳細は `docs/knowledges/tools/008_jquants_fin_summary.md` を参照

**データ範囲:**
- 期間（FROM）: 2016-02-26（既存データの最古日付）
- 期間（TO）: 毎日21:00 JST 自動更新
  ```sql
  SELECT MAX(DISCLOSED_DATE) FROM `gmailpj-357912.STOCK.fin_summary`
  ```

**確認履歴（確認のたびに追記）:**
| 確認日 | 総行数 | 最新開示日 |
|--------|--------|----------|
| 2026-02-28 | 187,003件 | 2026-02-27 |
| 2026-03-05 | 187,045件 | 2026-03-04 |
| 2026-04-09 | — | 2026-04-09 |

**欠損補完履歴:**
| 補完日 | 欠損期間 | 原因 | 追加件数 |
|--------|---------|------|---------|
| 2026-04-09 | 2026-01-01〜2026-02-26（41日） | Cloud Run Job投入漏れ | 4,091件 |
| 2026-04-09 | 2026-03-20〜2026-03-27（6日） | Cloud Run Job投入漏れ | 122件 |

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q` | 実績値のみ抽出・累積値→単独四半期値変換ビュー | ビュー（実体なし） | `scripts/create_fin_summary_view.py` で作成 |

**`STOCK.v_fin_summary_actual_for_q_on_q` — 単独四半期 P&L ビュー**

`fin_summary` の累積P&L値をQ単独値に変換したビュー。前期同四半期比（Q on Q）や四半期トレンド分析に使用する。

**用途:**
- 各Qの単独売上高・利益を直接比較（例: 2025Q2 vs 2024Q2）
- LAG() で前期累積値を引いて単独Q値を算出済みなので分析コードが簡潔になる

**ビュー定義の方針:**
- **実績値のみ**: 予想値・配当情報は除外。`TYPE_OF_CURRENT_PERIOD IN ('1Q', '2Q', '3Q', 'FY')`
- **修正開示対応**: 同一銘柄×事業年度×期区分で最新開示（DISCLOSED_DATE + DISCLOSED_TIME 降順）のみ残す
- **FY → 4Q 変換**: `QUARTER` 列は FY を 4Q に変換（元の値は `TYPE_OF_CURRENT_PERIOD` に保持）
- **半期報告企業対応**: Q1/Q3 がない場合は LAG が NULL → `COALESCE(prev, 0)` により 2Q 単独 = 2Q 累積のまま（東証には半期報告は存在しないが念のため対応済み）

**スキーマ（出力列）:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| LOCAL_CODE | STRING | 銘柄コード（4桁） |
| DISCLOSED_DATE | DATE | 開示日 |
| DISCLOSED_TIME | TIME | 開示時刻 |
| TYPE_OF_DOCUMENT | STRING | 書類種別（`fin_summary` と同じ） |
| QUARTER | STRING | 四半期ラベル（1Q/2Q/3Q/4Q。FYは4Qに変換済み） |
| TYPE_OF_CURRENT_PERIOD | STRING | 元の当期種別（1Q/2Q/3Q/FY。デバッグ用） |
| CURRENT_PERIOD_START_DATE | DATE | 当期開始日 |
| CURRENT_PERIOD_END_DATE | DATE | 当期終了日 |
| CURRENT_FISCAL_YEAR_START_DATE | DATE | 当会計年度開始日（PARTITION相当。銘柄×会計年度の識別キー） |
| CURRENT_FISCAL_YEAR_END_DATE | DATE | 当会計年度終了日 |
| NET_SALES | INTEGER | 売上高（**単独四半期値**） |
| OPERATING_PROFIT | INTEGER | 営業利益（**単独四半期値**） |
| ORDINARY_PROFIT | INTEGER | 経常利益（**単独四半期値**。IFRS/US-GAAPは空欄） |
| PROFIT | INTEGER | 当期純利益（**単独四半期値**） |
| NON_CONSOLIDATED_NET_SALES | INTEGER | 単体 売上高（**単独四半期値**） |
| NON_CONSOLIDATED_OPERATING_PROFIT | INTEGER | 単体 営業利益（**単独四半期値**） |
| NON_CONSOLIDATED_ORDINARY_PROFIT | INTEGER | 単体 経常利益（**単独四半期値**） |
| NON_CONSOLIDATED_PROFIT | INTEGER | 単体 当期純利益（**単独四半期値**） |

**クエリ例:**

```sql
-- トヨタ (7203) の直近4Qの単独売上高・営業利益
SELECT
  QUARTER,
  CURRENT_FISCAL_YEAR_START_DATE,
  NET_SALES,
  OPERATING_PROFIT
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
WHERE LOCAL_CODE = '7203'
ORDER BY CURRENT_FISCAL_YEAR_START_DATE, CURRENT_PERIOD_END_DATE

-- 前期同四半期比（Q on Q）の計算例
SELECT
  LOCAL_CODE,
  QUARTER,
  CURRENT_FISCAL_YEAR_START_DATE,
  NET_SALES,
  LAG(NET_SALES) OVER (
    PARTITION BY LOCAL_CODE, QUARTER
    ORDER BY CURRENT_FISCAL_YEAR_START_DATE
  ) AS PREV_YEAR_NET_SALES
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
```

**ビュー作成・再作成:**

```bash
# ビュー作成（初回 or 定義変更時）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py

# SQL 確認のみ（BQ 非接触）
PYTHONUTF8=1 python scripts/create_fin_summary_view.py --dry-run
```

- **作成スクリプト**: `scripts/create_fin_summary_view.py`
- **ビューID**: `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
- **ベーステーブル**: `gmailpj-357912.STOCK.fin_summary`
- **設計詳細**: `docs/knowledges/tools/008_jquants_fin_summary.md` の「ビュー設計」セクション参照

**注意事項:**
- ビューなので実体データはなし。`fin_summary` の更新（毎日21:00）で自動的に最新になる
- `ORDINARY_PROFIT` は IFRS/US-GAAP の銘柄では NULL（`fin_summary` と同様）
- 連結・非連結の両方が同一行に入っている。連結のみ使う場合は `TYPE_OF_DOCUMENT LIKE '%Consolidated%'` でフィルタ
- **金額単位は円**（百万円単位ではない）。極洋(1301)で2,000億円超の値を確認済み
- `NON_CONSOLIDATED_*` は連結のみ開示企業（Q1〜Q3）では NULL になる。FY（4Q）では開示される場合あり

**作成確認履歴:**
| 確認日 | 確認内容 |
|--------|---------|
| 2026-03-07 | トヨタ(7203) 2023年度の1Q〜4Q単独値が正しいことを確認 |
| 2026-03-29 | BQ MCP でスキーマ・DDL・サンプルデータ（極洋1301、2016〜2017）を実確認。金額単位=円を確認 |

