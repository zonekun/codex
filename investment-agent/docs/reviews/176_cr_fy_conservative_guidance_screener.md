# コードレビュー: FY弱気ガイダンス反復スクリーニングツール

- 日時: 2026-05-14 18:33 JST
- 対象: `scripts/fy_conservative_guidance_screener.py`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: FY決算発表時に弱気ガイダンスを出し株価が下落するも、最終的に実績が初期ガイダンスを大幅に上回る「保守的ガイダンス反復パターン」を銘柄単位でスクリーニングするツール。BQ SQL で FIN_SUMMARY / STOCK_PRICE / TDNET_DOCUMENTS_ENHANCED を結合し、Python側で分類・集計・CSV/HTML出力する
- 品質評価: **B** — SQL設計（fin_all/price_window CTE統合）は適切。price_window ROW_NUMBERロジックも正しい。ただしメトリクス種別混在・exit code・エラーハンドリングに中程度のリスクがある
- 主要リスク:
  1. ACTUAL_METRIC と INITIAL_FORECAST_METRIC で異なる利益指標（operating_profit vs profit等）が混在し得る
  2. main() が常に return 0 でありエラー時も exit 0（A-1 違反）
  3. BQ クエリ失敗時の例外が未ハンドルで即死（スタックトレースのみ、サマリなし）

## 【重大な指摘】（即修正）

### #1 ACTUAL_METRIC と INITIAL_FORECAST_METRIC の利益指標種別不一致

- 箇所: `scripts/fy_conservative_guidance_screener.py:69-86` (SQL `fin_all` CTE)
- 事象: `ACTUAL_METRIC` は `COALESCE(OPERATING_PROFIT, ORDINARY_PROFIT, PROFIT)` で決定し、`INITIAL_FORECAST_METRIC` は `COALESCE(NEXT_YEAR_FORECAST_OPERATING_PROFIT, NEXT_YEAR_FORECAST_ORDINARY_PROFIT, NEXT_YEAR_FORECAST_PROFIT)` で決定する。IFRS/US-GAAP企業では `ORDINARY_PROFIT` が NULL のため、実績が `OPERATING_PROFIT` で取得され、予想が `NEXT_YEAR_FORECAST_OPERATING_PROFIT` で取得される場合は一致する。しかし、当期FY実績の開示と翌期ガイダンスの開示で**開示項目の充足パターンが異なるケース**（例: 前期は連結で OPERATING_PROFIT あり、翌期予想は ORDINARY_PROFIT で開示）が発生すると、営業利益の実績と経常利益の予想を比較する `INITIAL_GROWTH` が意味不明な値になる
- トリガー: J-GAAP企業の一部で、OPERATING_PROFIT が実績にはあるが翌期予想ではNULL（ORDINARY_PROFIT のみ公表）のケース。データカタログ注記「経常利益は IFRS・US-GAAP では空欄」から、逆に J-GAAP では両方存在するが、企業によって予想の開示粒度が異なる
- 影響: INITIAL_GROWTH / ACTUAL_VS_INITIAL の計算が異種利益指標の比較になり、パターン判定が誤分類される。HIT数が過大/過小にずれる
- 根拠: `ACTUAL_METRIC_NAME` と `INITIAL_FORECAST_METRIC_NAME` を出力しているが、`classify_row` (line 430) では両者の一致を検証していない
- 推奨対応: **[方向性]** `classify_row` で `ACTUAL_METRIC_NAME != INITIAL_FORECAST_METRIC_NAME` のとき `invalid_baseline` 扱いにして除外する。または SQL 側で同一指標のみ比較するよう COALESCE の優先順位を統一する。どちらの方式を取るかは分析要件による

**[採用]**

### #2 main() が常に return 0（A-1 アンチパターン）

- 箇所: `scripts/fy_conservative_guidance_screener.py:872`
- 事象: `main()` は最終行で `return 0` を返す。BQ クエリ失敗（認証エラー、タイムアウト等）が発生した場合、例外が `main()` から漏れて `SystemExit(main(sys.argv[1:]))` でトレースバックが出るが、中間で `write_candidate_years` が実行される前に死ぬため出力ファイルが不完全になる可能性がある。また、将来このスクリプトを Cloud Run Job や Workflows から呼ぶ場合、A-1（exit 0 嘘）の温床になる
- トリガー: BQ クエリ失敗、GCP 認証切れ、ディスク容量不足による CSV 書き込み失敗
- 影響: 上流から見た成功/失敗判定が曖昧になる
- 根拠: 004 §A-1「`main()` 末尾で `if errors > 0: sys.exit(1)` を徹底」
- 推奨対応: **[検証済み]** `main()` 全体を try/except で囲み、例外時は `log.exception(...)` + `return 1`。正常系は `return 0` のまま。004 の A-1/A-7 パターンに準拠

**[採用]**

### #3 REVISION_FORECAST_METRIC の COALESCE 順序がリビジョンと初期ガイダンスで意味的に二重

- 箇所: `scripts/fy_conservative_guidance_screener.py:87-94` (SQL `fin_all` CTE)
- 事象: `REVISION_FORECAST_METRIC` は `COALESCE(FORECAST_OPERATING_PROFIT, FORECAST_ORDINARY_PROFIT, FORECAST_PROFIT, NEXT_YEAR_FORECAST_OPERATING_PROFIT, NEXT_YEAR_FORECAST_ORDINARY_PROFIT, NEXT_YEAR_FORECAST_PROFIT)` と定義されている。`EarnForecastRevision` 行では `FORECAST_*` が当期修正予想、`NEXT_YEAR_FORECAST_*` が翌期修正予想を表す。この COALESCE は当期修正（FORECAST_*）を翌期修正（NEXT_YEAR_FORECAST_*）より優先するが、`revision_source` CTE (line 159) では `CURRENT_FISCAL_YEAR_END_DATE` を `TARGET_FISCAL_YEAR_END_DATE` にマッピングしている。`initial_events` の `TARGET_FISCAL_YEAR_END_DATE` は **翌期** の FY 末日（line 135: `NEXT_FISCAL_YEAR_END_DATE`）であるため、revision_source の JOIN は翌期のリビジョンを検索する意図であるが、`REVISION_FORECAST_METRIC` の COALESCE が `FORECAST_*`（当期予想）を先に取得してしまうと、翌期リビジョンではなく当期リビジョンの値が取れてしまう
- トリガー: `EarnForecastRevision` 行に `FORECAST_OPERATING_PROFIT`（当期修正）と `NEXT_YEAR_FORECAST_OPERATING_PROFIT`（翌期修正）の両方が入っている場合
- 影響: `MAX_REVISION_FORECAST_METRIC` が当期の修正予想値になり、`MAX_REVISION_VS_INITIAL`（翌期ガイダンスとの比較）が異なるFYの値同士の比較になる
- 根拠: FIN_SUMMARY の EarnForecastRevision 行では `CURRENT_FISCAL_YEAR_END_DATE` が当期を指すが、`FORECAST_*` も当期を指す。翌期修正は `NEXT_YEAR_FORECAST_*` にのみ入る
- 推奨対応: **[方向性]** `EarnForecastRevision` 用の `REVISION_FORECAST_METRIC` は `COALESCE(FORECAST_OPERATING_PROFIT, ...)` ではなく、JOIN のコンテキスト（翌期を探している）に合わせて `NEXT_YEAR_FORECAST_*` を優先するか、`revision_source` CTE の JOIN 条件で `NEXT_FISCAL_YEAR_END_DATE` を使うか、設計意図を明確にする必要がある。ただし `EarnForecastRevision` の `TYPE_OF_CURRENT_PERIOD = 'FY'` かつ `CURRENT_FISCAL_YEAR_END_DATE` が当期を表す点を踏まえると、そもそも `revision_source` が意図するリビジョン対象期が曖昧。実データで検証が必要

**[見送り: revision_sourceのJOINはCURRENT_FISCAL_YEAR_END_DATE=TARGET_FISCAL_YEAR_END_DATEで結合。EarnForecastRevisionのFORECAST_*は当期=ターゲット期の修正予想なのでCOALESCE順序は正しい。レビュワーの「翌期を探している」という前提が誤り]**

### #4 STOCK_CODE_LIST の重複行による初期イベントの膨張

- 箇所: `scripts/fy_conservative_guidance_screener.py:34-55` (SQL `universe` CTE) + `scripts/fy_conservative_guidance_screener.py:122-146` (SQL `initial_events` CTE)
- 事象: `STOCK_CODE_LIST` は TICKER に対して複数行を持つ可能性がある（上場区分変更、市場再編等による複数レコード）。`universe` CTE で `ROW_NUMBER() OVER (ORDER BY TICKER)` を使い `@limit_tickers` で絞っているが、TICKER の重複排除は行っていない。`initial_events` で `fy_rows JOIN universe u USING (TICKER)` すると、TICKER が `universe` に複数行ある場合に行が増幅する
- トリガー: 同一 TICKER が STOCK_CODE_LIST に複数行存在する場合
- 影響: 同一 TICKER x FY の組み合わせに対して複数の `initial_events` 行が生成され、下流の LEFT JOIN で行数がファンアウトする。最終結果で同一イベントが重複カウントされ、HIT_COUNT や集計値が不正確になる
- 根拠: `universe_base` に DISTINCT がなく、`STOCK_CODE_LIST` のスキーマ上 TICKER はユニーク制約がない
- 推奨対応: **[検証済み]** `universe_base` で `SELECT DISTINCT TICKER, ...` を使うか、`universe_ranked` の ROW_NUMBER に `PARTITION BY TICKER` を追加して各 TICKER から1行のみ残す

**[採用]**

## 【改善提案】（可読性・保守性）

### #1 エラーサマリ出力の欠如（A-7）

- 箇所: `scripts/fy_conservative_guidance_screener.py:826-876` (`main()`)
- 現状: `main()` は正常系のサマリログ（line 863-871 `screener_done`）を出すが、BQ クエリや CSV 書き込みが途中で失敗した場合のエラーサマリがない。004 §A-7「終了時 processed/skipped/errors サマリを必ず出力」に非準拠
- 提案: try/except で大枠を囲み、正常系・異常系ともに `processed` / `errors` をサマリログに出す

### #2 classify_row での float() 変換の冗長性

- 箇所: `scripts/fy_conservative_guidance_screener.py:440-487`
- 現状: `row.get("INITIAL_GROWTH")` 等の値を取得した後、判定時に毎回 `float(initial_growth)` で変換している。BQ から返される値は既に数値型（float/int）であり、`row_to_dict` で dict 化した時点で Python の数値型が保持されている
- 提案: BQ 返却値は Python 数値型なので `float()` 呼び出しは不要。ただし None チェック後であれば実害はないため優先度は低い

### #3 HTML レポートの XSS 対策は適切だがインライン CSS が長大

- 箇所: `scripts/fy_conservative_guidance_screener.py:698-801`
- 現状: `html.escape()` を正しく使っておりセキュリティ上問題はない。ただしインラインで HTML を文字列結合しており 100 行超と長い
- 提案: 将来的にテンプレートエンジン（Jinja2 等）への分離を検討。現時点では機能するため優先度低

### #4 default の date_to が実行時の date.today()

- 箇所: `scripts/fy_conservative_guidance_screener.py:810`
- 現状: `parser.add_argument("--date-to", default=date.today().isoformat())` — `date.today()` はモジュールロード時ではなく `build_parser()` 呼び出し時に評価されるため問題なし。ただし CLAUDE.md §日時ルール「`datetime.now()` は禁止。`datetime.now(tz=ZoneInfo('Asia/Tokyo'))` を使う」との整合性で、`date.today()` が UTC の日付境界で JST と1日ずれる可能性がある（UTC 15:00-23:59 は JST では翌日だが `date.today()` は UTC ベースの場合がある）
- 提案: `datetime.now(tz=ZoneInfo('Asia/Tokyo')).date().isoformat()` に変更して JST 基準を明示する

### #5 CSV エンコーディング utf-8-sig（BOM 付き UTF-8）

- 箇所: `scripts/fy_conservative_guidance_screener.py:651`
- 現状: `encoding="utf-8-sig"` — Excel で開くことを想定した BOM 付き UTF-8。CLAUDE.md §6「ソースコード/JSON/YAML/MD → utf-8、CSV出力（外部共有）→ shift_jis」のルールとは異なるが、分析用 CSV は外部共有目的ではなく AI/プログラムが読むため、utf-8-sig は合理的な選択
- 提案: コメントで「Excel 直接オープン対応のため BOM 付き」と意図を記載するのが望ましい

### #6 company_scores ソートの型安全性

- 箇所: `scripts/fy_conservative_guidance_screener.py:593-600`
- 現状: `float(row["AVG_ACTUAL_VS_INITIAL"] or 0.0)` — `AVG_ACTUAL_VS_INITIAL` が None の場合 `None or 0.0` で `0.0` になるため動作する。しかし値が `0`（int の 0）の場合も `0 or 0.0` で `0.0` になり意図通り動作するが、可読性が低い
- 提案: 明示的に `float(row["AVG_ACTUAL_VS_INITIAL"]) if row["AVG_ACTUAL_VS_INITIAL"] is not None else 0.0` とするか、ヘルパー関数を使う

## 【修正例】（必要な箇所のみ）

#### #2 (main() エラーハンドリング + exit code) に対する修正案

```python
# before: scripts/fy_conservative_guidance_screener.py:826-876
def main(argv: list[str] | None = None) -> int:
    """Run the screener."""
    configure_logging()
    args = build_parser().parse_args(argv)
    # ... (省略) ...
    return 0

# after
def main(argv: list[str] | None = None) -> int:
    """Run the screener."""
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        # ... (既存の処理) ...
        log.info("screener_done", ...)
        return 0
    except Exception:
        log.exception("screener_failed")
        return 1
```

#### #4 (STOCK_CODE_LIST 重複排除) に対する修正案

```sql
-- before: universe_base CTE
SELECT TICKER, STOCK_NAME, MARKET_CATEGORY, EXCHANGE,
       INDUSTRY_33_CATEGORY, INDUSTRY_17_CATEGORY
FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
WHERE ...

-- after: DISTINCT を追加
SELECT DISTINCT TICKER, STOCK_NAME, MARKET_CATEGORY, EXCHANGE,
       INDUSTRY_33_CATEGORY, INDUSTRY_17_CATEGORY
FROM `gmailpj-357912.STOCK.STOCK_CODE_LIST`
WHERE ...
```

## 【確認できなかった事項】

- `STOCK_CODE_LIST` に同一 TICKER の複数行が実際に存在するかは BQ を実行しないと確認不能。存在しなければ #4 は潜在リスクに留まる
- `EarnForecastRevision` 行の `FORECAST_*` と `NEXT_YEAR_FORECAST_*` の両方に値が入るケースの頻度は実データ確認が必要（#3 の影響範囲の確定）
- `FIN_SUMMARY` の `ACTUAL_METRIC_NAME` と `INITIAL_FORECAST_METRIC_NAME` が実際にどの程度不一致するかは実データ確認が必要（#1 の影響範囲の確定）
- price_window CTE の 10 日間ウィンドウで、GW/年末年始等の長期休場時に PREV/AFTER の両方が取れない（NULL になる）ケースの頻度。10 営業日相当（約 14 暦日）をカバーすべきかの判断は業務要件による
- `STOCK.STOCK_PRICE` テーブルの `CLOSE` 列は INTEGER 型（整数）であるため、低位株での精度は問題ないが、株式分割前後の調整が入っていない可能性がある。リターン計算への影響は分析目的次第

---

## price_window CTE の ROW_NUMBER ORDER BY ロジック検証（重点確認事項）

依頼で重点確認を求められた `price_window` CTE の ROW_NUMBER ロジック (line 274-279) について、正当性を確認した。

**ロジック:**
```sql
ROW_NUMBER() OVER (
  PARTITION BY b.TICKER, b.TARGET_FISCAL_YEAR_END_DATE,
    CASE WHEN p.YEARDATE < b.INITIAL_DISCLOSED_DATE THEN 'PREV' ELSE 'AFTER' END
  ORDER BY CASE WHEN p.YEARDATE < b.INITIAL_DISCLOSED_DATE
    THEN -1 * UNIX_DATE(p.YEARDATE) ELSE UNIX_DATE(p.YEARDATE) END
)
```

**PREV 側 (YEARDATE < INITIAL_DISCLOSED_DATE):**
- ORDER BY `-1 * UNIX_DATE(YEARDATE)` = 日付が新しいほど値が小さい（より負）
- ROW_NUMBER の昇順で rn=1 は最も負の値 = **開示日に最も近い前営業日**
- 正しい: 直前の終値を取得する意図と合致

**AFTER 側 (YEARDATE > INITIAL_DISCLOSED_DATE):**
- JOIN 条件 `p.YEARDATE != b.INITIAL_DISCLOSED_DATE` により開示当日は除外済み
- ORDER BY `UNIX_DATE(YEARDATE)` = 日付が古いほど値が小さい
- rn=1 は **開示日の翌営業日**、rn=3 は **3営業日後**
- 正しい: 翌日リターン (RET_1D) と3日リターン (RET_3D) の意図と合致

**10 日ウィンドウ制限 (line 283-284):**
- `DATE_SUB/DATE_ADD(..., INTERVAL 10 DAY)` でスキャン範囲を制限
- STOCK_PRICE への full table scan を防ぎ、BQ コスト最適化に寄与
- 通常の営業日パターンでは10暦日で十分だが、GW（最大9連休）はギリギリ

**結論:** ROW_NUMBER ORDER BY ロジックは正しく動作する。
