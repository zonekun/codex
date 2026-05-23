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
- [ ] **4-1.** EXTRACT_SQL を fin_summary 起点に全面書き換え（`scripts/earnings_actual_load.py`）
- [ ] **4-2.** 業績予想 (CATEGORY='F') の REVISION_SEQ 再設計
  - fin_summary の `EarnForecastRevision` 系を `(LOCAL_CODE, FY, Q)` 内で `DISCLOSURE_NUMBER ASC` 連番化
- [ ] **4-3.** EARNINGS_DISCLOSURE_CALENDAR から不要列 DROP（`DISCLOSURE_NUMBER` / `DOC_TITLE` / `TYPE_OF_DOCUMENT`）
  - 既存データ移行: 旧データの該当列を NULL 化、または DDL 変更で物理削除
  - `bq_earnings_calendar.md` スキーマ更新
  - 参照側の影響調査（grep で利用箇所確認）
- [ ] **4-4.** Sonnet 追加の INDUSTRY_33_CODE フィルタ・DOC_TITLE LIKE フィルタを削除
- [ ] **4-5.** smoke test
  - 2162/05-11: 3Q×1件のみ
  - 訂正書類が出た既知サンプルで「最新版採用」確認
  - 旧東証一部廃止銘柄が DELISTED_STOCKS 経由で拾われること（バックフィル用）
- [ ] **4-6.** デプロイ（一時ビルドディレクトリ方式: 005 §⑥）
- [ ] **4-7.** バックフィル実行（`--from=20170101 --to=20260522` 想定、別途実行計画）
- [ ] **4-8.** 知見MD更新
  - `101_earnings_actual_load.md`: データフロー図 / カラムマッピング / 既知制約
  - `bq_earnings_calendar.md`: スキーマ・SOURCE 値・列削除
  - `bq_stock_code_list.md` L17: MARKET_CATEGORY 実値修正（「（内国株式）」サフィックス追記）

**未確定事項**:
- 同一 (TICKER, DISCLOSED_DATE) で TDnet 書類が複数あるケースの扱いは TDnet JOIN 廃止により自然解消
- 業績予想 (CATEGORY='F') のフィルタロジック詳細は 4-2 で詰める
- 既存 EARNINGS_DISCLOSURE_CALENDAR の DISCLOSURE_NUMBER 列利用箇所が無いことを 4-3 着手前に grep 確認

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
