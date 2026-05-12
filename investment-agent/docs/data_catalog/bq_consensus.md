# STOCK.CONSENSUS + V_CONSENSUS_MERGED
> 親: [`data_catalog.md`](../../data_catalog.md)

**`STOCK.CONSENSUS` BQテーブルスキーマ（2026-05-05 再構成）:**

| カラム名 | 型 | NULLABLE | 説明 |
|---------|-----|----------|------|
| DATAAT | DATE | NO | 取得日（スクレイピング実行日） |
| TICKER | STRING | NO | 銘柄コード（4桁） |
| FY | STRING | NO | 決算期（YYYYMM）。当期/来期の区別は読み取り側で判定 |
| QUARTER | STRING | NO | 四半期区分（`1Q` / `2Q` / `3Q` / `FY`） |
| REVENUE | INTEGER | YES | 売上高（百万円）。IFIS=NULL |
| OP_PROFIT | INTEGER | YES | 営業利益（百万円）。IFIS=NULL |
| ORD_PROFIT | INTEGER | YES | 経常利益（百万円）。旧PROFIT列 |
| NET_PROFIT | INTEGER | YES | 純利益（百万円）。IFIS=NULL |
| EPS | FLOAT64 | YES | EPS（円）。IFIS=NULL |
| SOURCE | STRING | NO | `IFIS` / `QUICK` |

> ⚠️ **TARGET列廃止（2026-05-05）**: 旧 `TARGET`（CURRENT/NEXT）列は廃止。当期/来期の判定は `V_LATEST_DISCLOSURE` VIEW の `CURRENT_FY` を参照し `fy >= current_fy` でフィルタ（C案、2026-05-12 VIEW化）。

> ⚠️ **DATAAT データ特性**: 各 SOURCE のスクリプトは1回の実行で全銘柄の全QUARTER を同一 DATAAT で insert する。QUICK と IFIS の最新 DATAAT は異なる可能性が高い。

> ⚠️ **ソース別データ特性**:
> - QUICK: FYのみ。5項目すべて。当期・来期・再来期
> - IFIS: 1Q/2Q/3Q/FY。ORD_PROFITのみ（他4列NULL）。当期のみ

> ⚠️ **下流クエリは `STOCK.V_CONSENSUS_MERGED` VIEW を参照**すること（QUICK優先・IFIS ORD_PROFIT補完のマージ済み）。

**`STOCK.V_CONSENSUS_MERGED` BQ VIEW 仕様（2026-05-05 再作成）:**

QUICK と IFIS の最新データを FULL OUTER JOIN し、QUICK 優先・IFIS（ORD_PROFIT）補完でマージした読み取り専用ビュー。

| カラム名 | 型 | 説明 |
|---------|-----|------|
| TICKER | STRING | 銘柄コード（4桁）。COALESCE(QUICK, IFIS) |
| FY | STRING | 決算期（YYYYMM）。COALESCE(QUICK, IFIS) |
| QUARTER | STRING | 四半期区分（`1Q` / `2Q` / `3Q` / `FY`）。COALESCE(QUICK, IFIS) |
| DATAAT | DATE | 取得日。COALESCE(QUICK, IFIS) |
| REVENUE | INTEGER | 売上高（百万円）。QUICKのみ |
| OP_PROFIT | INTEGER | 営業利益（百万円）。QUICKのみ |
| ORD_PROFIT | INTEGER | 経常利益（百万円）。**QUICK優先**、QUICK無ければIFIS |
| NET_PROFIT | INTEGER | 純利益（百万円）。QUICKのみ |
| EPS | FLOAT64 | EPS（円）。QUICKのみ |

> **マージロジック**:
> 1. QUICK: `PARTITION BY TICKER, FY, QUARTER ORDER BY DATAAT DESC` で最新行のみ抽出（5項目）
> 2. IFIS: 同上で最新行のみ抽出（ORD_PROFITのみ）
> 3. FULL OUTER JOIN on `(TICKER, FY, QUARTER)`
> 4. ORD_PROFIT は `COALESCE(q.ORD_PROFIT, i.ORD_PROFIT)` でQUICK優先・IFIS補完
> 5. 他4項目（REVENUE, OP_PROFIT, NET_PROFIT, EPS）はQUICKのみ

> **使用箇所**: `zaraba_earnings.py`（_load_or_fetch_consensus）、`lib_conse_csv_from_view.py`、`predict.py`（predict単日モード）

> **VIEW 不使用（as-of クエリ）**: `predict.py backfill` は `DATAAT <= predict_date` の過去時点参照が必要なため VIEW を使わず CONSENSUS テーブル 1-pass 取得 + pandas as-of フィルタを使用。

**`consensus_result.csv` スキーマ（廃止予定 — RAKU廃止に伴い不要）:**

> ⚠️ 楽天証券コンセンサス（RAKU）は2026-05-05で廃止。本CSVはレガシー。新しいコンセンサスCSVは `lib_conse_csv_from_view.py` が `V_CONSENSUS_MERGED` VIEW から生成する。

**四季報 Excel 主要カラム（単位ルール）:**

| カラム名 | 単位 | 備考 |
|---------|------|------|
| コード | - | 銘柄コード（4桁整数として格納） |
| CF単位 | - | CF関連項目の単位（"百万円" または "億円"）。`現金等` のみ適用 |
| 現金等 | CF単位に依存 | CF単位=百万円→×1,000,000 / 億円→×100,000,000 |
| 有利子負債 | **百万円固定** | CF単位に関係なく常に百万円 |
| 総資産 | **百万円固定** | 同上 |
| 自己資本 | **百万円固定** | 同上 |
| 時価総額 | **億円固定** | 全行共通 |
| 自己株保有 | - | 全件空欄のため使用不可 |

> **注意**: `scripts/kiyohara_screening.py` では有利子負債・現金等を四季報から取得し清原スクリーニングに使用。新しい四半期の四季報が届いたら `SHIKIHO_EXCEL` 定数を更新すること。

---

**固定情報:**
- 期間（FROM）: 1998-01-02（固定）
- 期間（TO）: 更新のたびに変わる。実際の最新日付は CSV を読んで確認すること
- ソースファイル: Excelシート LIST、1行目ヘッダー、6行目以降データ

**更新履歴（更新のたびに追記）:**
| 更新日 | 総行数 | データ期間（TO） |
|--------|--------|----------------|
| 2026-02-23 | 7,042件 | 2026-02-20 |
| 2026-03-02 | 7,047件 | 2026-02-27 |
| 2026-03-04 | 7,048件 | 2026-03-03 | 列追加: WTI（Col 29）、FEAR_GREED（Col 30）|

**更新ルール:**
- **更新タイミング**: 明示的な指示があったときのみ更新する（自動更新しない）
- **直近90日のデータ品質**: クレンジング未完了の可能性があるため、分析に使用する際は注意が必要
  - 更新時は「実行日 - 90日」より古いデータのみを「確定済み」として扱う
  - 直近90日のデータは参考値として保持するが、統計分析・バックテストの対象期間から除外することを推奨
  - 例: 2026-02-23 に更新した場合、〜2025-11-25 より前のデータが確定済み、2025-11-25〜2026-02-23 は暫定値

