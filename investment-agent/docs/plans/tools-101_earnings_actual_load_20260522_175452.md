# 作業計画: EARNINGS_DISCLOSURE_CALENDAR クリーニング + fin_summary ジョブ欠落対策

**作成日時**: 2026-05-22 17:54 JST
**ステータス**: 未着手
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/101_earnings_actual_load.md`
**関連アイディアID**: -

---

## 目的

EARNINGS_DISCLOSURE_CALENDAR（実績 A）の2種の汚れを解消する。
①QUARTER='不明' 2,568件の補完、②訂正書類・お知らせの混入防止、③fin_summary ジョブ欠落のクラッシュ対策。

---

## 背景・動機

調査（2026-05-22）で判明した事実:

| # | 問題 | 件数 | 根本原因 |
|---|------|------|---------|
| 1 | QUARTER='不明' / FISCAL_YEAR_END=NULL | 2,568行 / 2,503銘柄 | fin_summary ジョブが05-12〜05-16 **5日間欠落** → earnings-actual-load（05-17 05:00 JST）実行時に JOIN失敗 |
| 2 | 訂正書類・お知らせが正書類として混入 | 小規模（訂正を多発する銘柄のみ） | TDnet書類タイトルフィルタなし。訂正1Q/訂正2Q/お知らせが QUARTER='3Q' 等で論理PK重複 |

fin_summary ジョブ欠落ログ証跡:
```
05-15 17:00 UTC  取得期間: 05-16  ← ここで止まっている
05-17 09:28 UTC  取得期間: 05-12 ～ 05-16  ← バックフィル（手動？）
```
05-12〜05-16（月〜金）の日次分が5日間欠落。earnings-actual-load（05-16 20:00 UTC）はこの欠落状態でJOINを実行したため、大量の QUARTER='不明' が発生。

---

## 作業ステップ

### Phase 0: fin_summary ジョブ欠落の原因調査（クラッシュ対策前提）

- [x] **0-1.** `jquants-fin-summary` の05-12〜05-16ログを取得（`freshness=15d` で拡張）
  - `jquants-fin-summary-daily`（火〜土 02:00 JST）と `jquants-fin-summary-17`（月〜金 18:30 JST）の両方
  - Scheduler ログ: 05-12〜05-16 毎日正常トリガー（HTTP 200）
- [x] **0-2.** 原因を分類: A) ジョブエラー終了 / B) スケジューラ未トリガー / C) J-Quants API障害
  - **いずれでもない。D) shift_day設定変更前のサイレント0件取得**
  - 旧スクリプト（`--shift-day` 引数なし = shift_day=0）は当日を取得しようとしていたが、
    02:00 JST 時点では J-Quants API に当日の開示データが未反映 → 0件で正常終了
  - `--shift-day -1` が Cloud Run Job spec に追加されてから正常化（前日=確定済みを取得）
  - 05-17 09:28 に 05-12〜05-16 を手動バックフィル済み（誰かが実行）
- [x] **0-3.** 再発防止策を決定
  - Phase 3 で `earnings-actual-load` 実行前に fin_summary 最新日チェックを追加
  - fin_summary ジョブで0件取得時の警告通知追加（サイレント欠落防止）

### Phase 1: QUARTER='不明' の補完（即時対処）

- [x] **1-1.** `earnings-actual-load` を `--from=20260330 --to=20260515` で再実行（冪等: DELETE → INSERT）
  - 実行ID: `earnings-actual-load-tbprs`、deleted=4,345 → inserted=3,925（420件の訂正書類除外）
- [x] **1-2.** 再実行後の QUARTER='不明' 件数を確認（**2,568 → 221件**、03-30〜05-15範囲内）
- [x] **1-3.** 残存した '不明' を分析
  - 05-12 不明の全件が 1672-1697 系 J-REIT/ETF。fin_summary に一切存在しないカバー外銘柄
  - 仕様として記録: これらは今後も QUARTER='不明' で残存（J-Quants fin_summary 未対応の上場商品）
- [x] **1-4.** 05-16〜05-22 分の欠落も補完
  - 実行ID: `earnings-actual-load-cjzs2`、deleted=0 → inserted=51
  - **最終 QUARTER='不明' 件数: 247件**（ETF/REIT カバー外のみ）

### Phase 2: 訂正書類・お知らせの除外フィルタ追加（スクリプト改修）

**対象ファイル**: `scripts/earnings_actual_load.py` (commit 9c9cb2c2 時点)

**症状**: TDnet に「（訂正・数値データ訂正）」「のお知らせ」等のタイトルで投稿される書類が `MAIN_CATEGORY='決算短信'` として TDNET_DOCUMENTS_ENHANCED に入っており、earnings-actual-load がそのままロード → fin_summary の rn=1 書類に JOIN → 訂正書類が正書類と同一 QUARTER でロード（論理PK重複）

**該当箇所**: `scripts/earnings_actual_load.py:L131-L177` (EXTRACT_SQL)

```sql
-- 現行（問題のある箇所）
WHERE MAIN_CATEGORY IN ('決算短信', '業績予想')
  AND SUBMISSION_DATE BETWEEN @date_from AND @date_to
```

- [x] **2-1.** 訂正・お知らせのタイトルパターンを実データで網羅確認
  - 240,181件中 4,670件が除外対象（約2%）
- [x] **2-2.** EXTRACT_SQL の `tdnet_docs` CTE に除外フィルタを追加

```sql
-- 実装済みフィルタ
AND DOC_TITLE NOT LIKE '%訂正%'
AND DOC_TITLE NOT LIKE '%差替え%'
AND DOC_TITLE NOT LIKE '%のお知らせ'
AND DOC_TITLE NOT LIKE '%に関するお知らせ'
```

- [x] **2-3.** smoke test OK: 2162/05-11 が `3Q×1件`（`2026年３月期第３四半期決算短信〔日本基準〕（連結）`）のみ
- [x] **2-4.** デプロイ完了
  - Build ID: `ad11edd4-d3b6-4dc2-9c00-2f35ecf13da2`（1m15s、SUCCESS）
  - Image: `earnings-actual-load@sha256:c4848ea2058ff669c5764d02a202bede6deb54d7b58f1a0883faafad4d3e0c69`
  - 一時ビルドディレクトリ方式: `/c/tmp/cloudbuild-Pf6seW`（削除済み）
- [x] **2-5.** Phase 1 の補完再実行: 1-1〜1-4 で実施済み

### Phase 3: 再発防止 Guard 追加（Phase 0 結果次第）

- [ ] **3-1.** Phase 0-3 の決定を受けて実装（次セッションで対応）
- [ ] **3-2.** 候補A: `earnings-actual-load` 冒頭に fin_summary 最新日付チェックを追加
  ```python
  # fin_summary の最新 DISCLOSED_DATE が date_from - 1 以上あることを確認
  # 不足なら WARN ログ + ntfy 通知 → 処理続行（エラー停止はしない）
  ```
- [ ] **3-3.** 候補B: Cloud Monitoring アラート（jquants-fin-summary の失敗検知）

### Phase 4: 根本対策 — fin_summary 起点への転換（2026-05-22 設計確定、未着手）

**経緯**: Sonnet が追加した INDUSTRY_33_CODE フィルタ（git status `M scripts/earnings_actual_load.py`）は ETF/REIT 除外には効くが、訂正書類除外には DOC_TITLE LIKE フィルタに依存したまま。Phase 4 では訂正系除外と銘柄絞り込みの両方を根本対策に置き換える。

**方針**:
1. **データソース転換**: TDnet 主軸 → **fin_summary 主軸**
   - fin_summary は訂正版が出ると同一 `(LOCAL_CODE, CURRENT_FISCAL_YEAR_END_DATE, TYPE_OF_CURRENT_PERIOD)` で新 `DISCLOSURE_NUMBER` の別行として持つ
   - `ROW_NUMBER() OVER (PARTITION BY 3キー ORDER BY DISCLOSURE_NUMBER DESC) = 1` で最新版1件採用 → 1 (TICKER, FY, Q) = 1 カレンダー行に自動集約
   - **DOC_TITLE LIKE フィルタ完全廃止**（タイトル文字列依存ゼロ）

2. **TDnet JOIN 廃止**:
   - `DISCLOSURE_NUMBER` / `DOC_TITLE` / `TYPE_OF_DOCUMENT` 列は不要 → BQ DDL で DROP（または NULL 固定）
   - `SOURCE` 列を `'tdnet'` → `'jquants'` に変更
   - ロジックは fin_summary 1テーブルから完結（CATEGORY='R': `*FinancialStatements*`、CATEGORY='F': `EarnForecastRevision` / `REITEarnForecastRevision`）

3. **銘柄絞り込み**: INDUSTRY_33_CODE → **MARKET_CATEGORY フィルタ**に切り替え
   ```sql
   -- 通常運用: STOCK_CODE_LIST のみ
   INNER JOIN STOCK_CODE_LIST scl ON f.LOCAL_CODE = scl.TICKER AND scl.EXCHANGE = 'TSE'
   WHERE scl.MARKET_CATEGORY IN (
     'プライム（内国株式）','スタンダード（内国株式）','グロース（内国株式）'
   )
   ```
   実値検証済み（2026-05-22 BQ MCP）: 「（内国株式）」サフィックス必須

4. **バックフィル時の特例**: 廃止銘柄も対象に含めるため UNION
   ```sql
   WITH valid_tickers AS (
     SELECT TICKER FROM STOCK_CODE_LIST
      WHERE EXCHANGE='TSE' AND MARKET_CATEGORY IN ('プライム（内国株式）','スタンダード（内国株式）','グロース（内国株式）')
     UNION DISTINCT
     SELECT TICKER FROM DELISTED_STOCKS
      WHERE MARKET_SEGMENT IN (
        'プライム','スタンダード','グロース',
        '東証プライム','東証スタンダード','東証グロース',
        '第一部','第二部','マザーズ',
        'JQスタンダード','JQグロース'
      )
   )
   ```
   - DELISTED_STOCKS データ範囲は 2017年〜（バックフィル期間の上限）
   - 表記揺れ・旧市場区分全カバー（2026-05-22 BQ MCP 実値確認済み）
   - 外国株系（`第一部（外国株）` 等 4件）・札証 1件は除外

**作業ステップ**:
- [x] **4-1.** EXTRACT_SQL を fin_summary 起点に全面書き換え（`scripts/earnings_actual_load.py`）
- [x] **4-2.** 業績予想 (CATEGORY='F') の REVISION_SEQ 再設計
  - fin_summary の `EarnForecastRevision` 系を `(LOCAL_CODE, FY, Q)` 内で `DISCLOSURE_NUMBER ASC` 連番化
- [x] **4-3.** EARNINGS_DISCLOSURE_CALENDAR から不要列 DROP（`DISCLOSURE_NUMBER` / `DOC_TITLE` / `TYPE_OF_DOCUMENT`）
  - BQ DDL で物理削除
  - `bq_earnings_calendar.md` スキーマ更新
  - `scripts/earnings_actual_load.py` / `scripts/earnings_schedule_load.py` の出力スキーマから削除
- [x] **4-4.** Sonnet 追加の INDUSTRY_33_CODE フィルタ・DOC_TITLE LIKE フィルタを削除
- [x] **4-5.** smoke test
  - 2162/05-11: 3Q×1件のみ
  - 訂正書類が出た既知サンプルで「最新版採用」確認
  - 旧東証一部廃止銘柄が DELISTED_STOCKS 経由で拾われること（バックフィル用）
  - Codex検証: 2162/2026-05-11 は `3Q×1件`。同日の論理PK重複は0件。Fは 2026-05-01〜2026-05-22 サンプルで `REVISION_SEQ` 付与を確認。廃止銘柄は `5386` などが `DELISTED_STOCKS` 経由で対象になることを確認。
- [x] **4-6.** デプロイ（一時ビルドディレクトリ方式: 005 §⑥）
  - Cloud Build `7df5bacd-c8b0-4426-9f39-be8c8eab30fd` SUCCESS
  - Cloud Run Job `earnings-actual-load` generation 4 に更新
- [x] **4-7.** バックフィル実行（2026-05-24 実行済み、run_id: `20260524_124252`）
  - 対象期間: `20170101`〜`20260522`
  - 実行方針: BQバックアップ作成後、専用ローカルラッパーを小分けチャンクで実行
  - 専用プログラム: `scripts/earnings_actual_backfill.py`
  - dry-run確認: `--mode pilot` は 2017-01-01〜2017-01-31 で 1,300件（F=214, R=1,086）を抽出・変換
  - 実行開始は別途明示指示を待つ
- [x] **4-8.** 知見MD更新
  - `101_earnings_actual_load.md`: データフロー図 / カラムマッピング / 既知制約
  - `bq_earnings_calendar.md`: スキーマ・SOURCE 値・列削除
  - `bq_stock_code_list.md` L17: MARKET_CATEGORY 実値修正（「（内国株式）」サフィックス追記）
  - Codex実装メモ: `bq_earnings_calendar.md` は物理DROP後の現行スキーマとして更新。

**未確定事項**:
- 同一 (TICKER, DISCLOSED_DATE) で TDnet 書類が複数あるケースの扱いは TDnet JOIN 廃止により自然解消
- 業績予想 (CATEGORY='F') は `EarnForecastRevision` / `REITEarnForecastRevision` を対象にし、同一 `(LOCAL_CODE, FY, Q)` 内で `DISCLOSURE_NUMBER ASC` 連番とする
- 既存 EARNINGS_DISCLOSURE_CALENDAR の `DISCLOSURE_NUMBER` / `DOC_TITLE` / `TYPE_OF_DOCUMENT` は Phase 4 で物理DROPし、`scripts/earnings_schedule_load.py` からも出力を削除する

---

## Phase 4-7 バックフィル実行計画（2026-05-24 実行済み）

### 目的

Phase 4で実績Aロードを `fin_summary` 起点に切り替えたため、`EARNINGS_DISCLOSURE_CALENDAR` の過去実績Aを 2017-01-01 以降で再構築する。予定Sは対象外で、専用プログラム `scripts/earnings_actual_backfill.py` は対象期間の `RECORD_TYPE='A'` のみステージングテーブル経由で置換する。

### 前提

- BQ DDL DROP 済み: `DISCLOSURE_NUMBER` / `TYPE_OF_DOCUMENT` / `DOC_TITLE` は現行テーブルに存在しない
- Cloud Run Job `earnings-actual-load` は Phase 4版へデプロイ済み
- 4-7実行は Cloud Run Job ではなく `scripts/earnings_actual_backfill.py` を使う
- `scripts/earnings_actual_backfill.py` は dry-run 既定。BQ更新には `--execute` が必須
- Rは `2017-01-01`〜`2026-05-22` 全体で銘柄×FISCAL_YEAR_END×QUARTERごとに最新開示1件を採用する
- Fの `REVISION_SEQ` は `2017-01-01`〜`2026-05-22` 全体で採番してから対象チャンクを切り出す（年次チャンク境界で再始番しない）
- バックフィル対象上限は `2026-05-22`
- `fin_summary` の対象ソース行数: 176,299行（2017-01-04〜2026-05-22）
- 現行カレンダーのA行は2026年分のみ 3,976行（2026-05-23時点確認）

### 投入件数（Phase 4ロジック適用後、2026-05-24 実績）

| 年 | F | R | 合計 |
|---:|---:|---:|---:|
| 2017 | 3,114 | 14,205 | 17,319 |
| 2018 | 2,596 | 14,404 | 17,000 |
| 2019 | 2,596 | 14,567 | 17,163 |
| 2020 | 3,417 | 14,736 | 18,153 |
| 2021 | 3,191 | 14,907 | 18,098 |
| 2022 | 2,495 | 15,098 | 17,593 |
| 2023 | 2,352 | 15,215 | 17,567 |
| 2024 | 2,078 | 15,272 | 17,350 |
| 2025 | 1,876 | 15,230 | 17,106 |
| 2026-01-01〜2026-05-22 | 979 | 7,227 | 8,206 |
| **合計** | **24,694** | **140,861** | **165,555** |

### 実行前チェック

1. 現行スキーマ確認
   ```sql
   SELECT column_name
   FROM `gmailpj-357912.STOCK.INFORMATION_SCHEMA.COLUMNS`
   WHERE table_name = 'EARNINGS_DISCLOSURE_CALENDAR'
   ORDER BY ordinal_position;
   ```
2. 実行直前バックアップ
   ```sql
   CREATE OR REPLACE TABLE `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR_BAK_YYYYMMDD_HHMMSS` AS
   SELECT * FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`;
   ```
3. S予定行の件数を控える
   ```sql
   SELECT COUNT(*) AS scheduled_rows
   FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
   WHERE RECORD_TYPE = 'S';
   ```

### 実行順

まず1か月だけdry-runと実行を行い、件数・重複・代表銘柄を確認してから年次チャンクへ進む。

```bash
# 0. 先行パイロット dry-run
python scripts/earnings_actual_backfill.py --mode pilot

# 1. 先行パイロット実行（BQバックアップ作成 + 検証 + MDログ追記）
python scripts/earnings_actual_backfill.py --mode pilot --execute

# 2. 全チャンク dry-run
python scripts/earnings_actual_backfill.py --mode all

# 3. 全チャンク実行（別途明示GO後）
python scripts/earnings_actual_backfill.py --mode all --execute

# 任意の単一チャンク
python scripts/earnings_actual_backfill.py --mode chunk --from 20260101 --to 20260522
python scripts/earnings_actual_backfill.py --mode chunk --from 20260101 --to 20260522 --execute
```

### チャンクごとの検証

```sql
-- 年別・カテゴリ別件数
SELECT EXTRACT(YEAR FROM DISCLOSURE_DATE) AS y, CATEGORY, COUNT(*) AS rows
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE RECORD_TYPE = 'A'
  AND DISCLOSURE_DATE BETWEEN '2017-01-01' AND '2026-05-22'
GROUP BY y, CATEGORY
ORDER BY y, CATEGORY;

-- 論理PK重複
SELECT COUNT(*) AS duplicate_key_count
FROM (
  SELECT TICKER, FISCAL_YEAR_END, QUARTER, CATEGORY, RECORD_TYPE, REVISION_SEQ, COUNT(*) AS cnt
  FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
  WHERE RECORD_TYPE = 'A'
    AND DISCLOSURE_DATE BETWEEN '2017-01-01' AND '2026-05-22'
  GROUP BY 1,2,3,4,5,6
  HAVING cnt > 1
);

-- 予定Sが変化していないこと
SELECT COUNT(*) AS scheduled_rows
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE RECORD_TYPE = 'S';

-- 既知サンプル
SELECT TICKER, DISCLOSURE_DATE, DISCLOSURE_TIME, QUARTER, FISCAL_YEAR_END, CATEGORY, RECORD_TYPE, REVISION_SEQ, SOURCE
FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR`
WHERE RECORD_TYPE = 'A'
  AND TICKER = '2162'
  AND DISCLOSURE_DATE = '2026-05-11'
ORDER BY CATEGORY, REVISION_SEQ;
```

### 停止条件

- Cloud Run Job が失敗、またはログに `fatal` が出た場合
- 論理PK重複が1件以上出た場合
- S予定行数が実行前から変化した場合
- チャンク投入件数が想定件数から大きく乖離し、`fin_summary` 更新以外の説明がつかない場合
- 2162/2026-05-11 が `3Q` のR 1件にならない場合

### ロールバック

実行直前バックアップから復元する。

```sql
CREATE OR REPLACE TABLE `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` AS
SELECT * FROM `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR_BAK_YYYYMMDD_HHMMSS`;
```

### 完了条件

- 2017-01-01〜2026-05-22 のAレコードが Phase 4ロジックで再作成済み
- 年別・カテゴリ別件数が投入想定件数と整合
- 論理PK重複が0件
- S予定行数が不変
- 2162/2026-05-11 が `3Q` のR 1件
- 実行ログ、検証SQL結果、バックアップテーブル名を本MDへ追記

---

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| 実績開示カレンダー | BQ | `gmailpj-357912.STOCK.EARNINGS_DISCLOSURE_CALENDAR` |
| J-Quants 財務サマリ | BQ | `gmailpj-357912.STOCK.fin_summary` |
| TDnet 書類 | BQ | `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` |

---

## 成果物

- 改修済み `scripts/earnings_actual_load.py`（訂正書類除外フィルタ追加）
- QUARTER='不明' の大幅削減（2,503銘柄 → fin_summary カバー外のみ残存）
- fin_summary ジョブ欠落原因の記録と再発防止策
- `docs/knowledges/tools/101_earnings_actual_load.md` への追記（バグ修正履歴・注意事項）

---

## 完了条件

1. `QUARTER='不明'` 件数が fin_summary カバー外銘柄のみになること（≒ ETF/REIT 系のみ）
2. 2162/05-11 が 3Q×1件のみになること（訂正書類・お知らせが除外されること）
3. fin_summary ジョブ欠落の原因が特定・記録されること
4. デプロイ後のスケジュール実行で正常動作すること

---

## 実行順序（重要）

```
Phase 0（調査） → Phase 2（スクリプト改修 + デプロイ） → Phase 1（補完再実行） → Phase 3（Guard） → Phase 4（根本対策）
```

Phase 1 を先に走らせても構わないが、Phase 2 後に再実行が必要になるため、**可能なら Phase 2 完了後に Phase 1 を一度だけ実行**する方が効率的。

Phase 4 は Phase 1-3 とは独立した根本対策。Sonnet 追加版（git status `M scripts/earnings_actual_load.py`、INDUSTRY_33_CODE フィルタ）は **デプロイせず**、Phase 4 で fin_summary 起点版に置き換える方針。

---

## 見積もり

- 想定所要時間: Phase 0: 30分 / Phase 1: 15分 / Phase 2: 1時間 / Phase 3: 1時間
- 難易度: 中（Phase 0 の原因次第で Phase 3 の難易度が変わる）

---

## 振り返り（2026-05-22 作業後）

- **実際の所要時間**: Phase 0: 30分 / Phase 2: 2時間 / Phase 1: 15分（Phase 3 未着手）
- **うまくいった点**: 
  - smoke test で 2162/05-11 の訂正除外を確認してからデプロイ → 問題なし
  - Phase 1 再実行で 2,568 → 247件（90%削減）
  - 一時ビルドディレクトリ方式で Drive ロック問題なくデプロイ成功
- **改善点**:
  - 訂正フィルタパターン調査（Step 2-1）に時間がかかった。LIKE '%訂正%' / '%差替え%' / '%のお知らせ' / '%に関するお知らせ' の4パターンで十分
- **得られた知見**:
  - J-REIT/ETF系（1672-1697等）は fin_summary カバー外。QUARTER='不明'として残存するのは仕様
  - fin_summary の shift_day=0 → 02:00 JST では当日データ未反映でサイレント0件取得（根本原因）
  - 訂正書類タイトルには全角`（訂正`と半角`(訂正`の両方が混在するが `%訂正%` で一括除外可能

---

## Phase 4-7 実行ログ: 2026-05-24 11:25 JST

- run_id: `20260524_112440`
- backup_table: `EARNINGS_DISCLOSURE_CALENDAR_BAK_20260524_112440`
- status: completed

| chunk | from | to | expected | inserted | duplicate_keys | category_counts |
|---|---:|---:|---:|---:|---:|---|
| pilot_201701 | 2017-01-01 | 2017-01-31 | 1300 | 1300 | 0 | F=214, R=1086 |

---

## Phase 4-7 実行ログ: 2026-05-24 11:28 JST

- run_id: `20260524_112756`
- backup_table: `EARNINGS_DISCLOSURE_CALENDAR_BAK_20260524_112756`
- status: completed

| chunk | from | to | expected | inserted | duplicate_keys | category_counts |
|---|---:|---:|---:|---:|---:|---|
| pilot_201701 | 2017-01-01 | 2017-01-31 | 1300 | 1300 | 0 | F=214, R=1086 |

---

## Phase 4-7 実行ログ: 2026-05-24 12:47 JST

- run_id: `20260524_124252`
- backup_table: `EARNINGS_DISCLOSURE_CALENDAR_BAK_20260524_124252`
- status: completed

| chunk | from | to | expected | inserted | duplicate_keys | category_counts |
|---|---:|---:|---:|---:|---:|---|
| 201701 | 2017-01-01 | 2017-01-31 | 1299 | 1299 | 0 | F=214, R=1085 |
| 201702_201712 | 2017-02-01 | 2017-12-31 | 16020 | 16020 | 0 | F=2900, R=13120 |
| 2018 | 2018-01-01 | 2018-12-31 | 17000 | 17000 | 0 | F=2596, R=14404 |
| 2019 | 2019-01-01 | 2019-12-31 | 17163 | 17163 | 0 | F=2596, R=14567 |
| 2020 | 2020-01-01 | 2020-12-31 | 18153 | 18153 | 0 | F=3417, R=14736 |
| 2021 | 2021-01-01 | 2021-12-31 | 18098 | 18098 | 0 | F=3191, R=14907 |
| 2022 | 2022-01-01 | 2022-12-31 | 17593 | 17593 | 0 | F=2495, R=15098 |
| 2023 | 2023-01-01 | 2023-12-31 | 17567 | 17567 | 0 | F=2352, R=15215 |
| 2024 | 2024-01-01 | 2024-12-31 | 17350 | 17350 | 0 | F=2078, R=15272 |
| 2025 | 2025-01-01 | 2025-12-31 | 17106 | 17106 | 0 | F=1876, R=15230 |
| 202601_20260522 | 2026-01-01 | 2026-05-22 | 8206 | 8206 | 0 | F=979, R=7227 |
