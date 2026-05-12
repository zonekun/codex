# 決算反応モデル YoY/QoQ 計算バグ修正

**作成日時**: 2026-04-27 16:00 JST
**対象ファイル**:
- `scripts/earnings_model/earnings_model_predict.ipynb`（Cell 5, commit 48b4499 時点）
- `scripts/earnings_model/batch_rerun_predict.py`（L191-349, commit 48b4499 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: YoY OP 計算が1年古い比較をしているバグ（F3）、QoQ が常に None で F13 が死んでいるバグの修正。batch_rerun の look-ahead bias 修正も含む。

---

## コードレビュー結果（2026-04-27）

**判定: Approve with conditions（条件付き承認）**

### Blocking（実装前に解決必須）
- **F1**: J-Quants OP と BQ ビュー OP の単位を実データで確認すること（初めて直接比較するため）
- **F2**: 半期報告企業（1Q/3Q未開示、~200社）への対応追加。前Q累積が無い場合は J-Quants 累積値 = 単独値として扱う（BQ ビューの COALESCE(prev, 0) と同じロジック）
- **F3**: prev_cum_op_map の BQ クエリを具体的に定義すること

### Before batch_rerun deploy
- **F10**: baseline_yoy_op（F7）のクエリにも DISCLOSED_DATE フィルタ漏れ → 同時修正
- **F9**: per-date as-of フィルタの具体ロジック定義

### 横展開（非ブロッキング）
- **F12**: zaraba ツールにも同じ standalone OP 計算 → 将来的に共通化
- **F8**: DISCLOSED_DATE フィルタが BQ ビューの dedup 後に適用される → 訂正決算の元データが消える制約あり（known limitation として記録）

---

## 前提サマリ

- 過去修正: F4 コンセンサスバグ（2026-04-10, 2026-04-14）は修正済み
- 残存: 本プランで扱う 3 件（P0×1, P1×2）+ レビュー追加 1 件（F10: baseline_yoy as-of）
- 実機検証: 未検証。6858 小野測器のデータで再現確認可能
- 関連 incident: Codex 反省メモ 2026-04-26（6858 小野測器 YoY OP +267% → 正 +28.2%）

---

## 優先度の定義

- **P0**: 全銘柄のスコアリングに毎日影響。誤った YoY で F3 が発火/不発火 → 予測品質直撃
- **P1**: QoQ 死亡は F13 が常に不発火で済むため損害は「機会損失」。look-ahead bias は batch_rerun の再集計精度に影響

---

## 指摘項目

### P0-1. YoY OP が1年古い比較をしている（F3 全銘柄影響） 🚨

**症状**: 6858 小野測器（1Q, PREDICT_DATE=20260423）で YoY OP +267% と算出されたが、正しくは +28.2%。全銘柄で同じパターンのズレが発生している。

**該当**: `earnings_model_predict.ipynb` Cell 5 §1-7 / `batch_rerun_predict.py:L320-349`

```python
# ── 1-7. BQ: Q-on-Q（YoY計算用） ──
q_qoq = f"""SELECT LOCAL_CODE, QUARTER, CURRENT_FISCAL_YEAR_START_DATE, OPERATING_PROFIT, PROFIT
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
WHERE SUBSTR(LOCAL_CODE, 1, 4) IN ({tickers_sql})
ORDER BY LOCAL_CODE, CURRENT_FISCAL_YEAR_START_DATE DESC"""
df_qoq = bq.query(q_qoq).to_dataframe()

# ... (per-ticker loop)
tk_qoq = df_qoq[(df_qoq['LOCAL_CODE'].str[:4] == tk_raw) & (df_qoq['QUARTER'] == q_label)]
tk_qoq = tk_qoq.sort_values('CURRENT_FISCAL_YEAR_START_DATE', ascending=False)
if len(tk_qoq) >= 2:
    cur_op = tk_qoq.iloc[0]['OPERATING_PROFIT']   # ← BQ に当日データが無い → 前年
    prev_op = tk_qoq.iloc[1]['OPERATING_PROFIT']   # ← 前々年
```

**根本原因**: ノートブックは当日発表の決算データを J-Quants API で取得するが、BQ `v_fin_summary_actual_for_q_on_q` には未反映（BQ ロードは翌日以降）。そのため BQ ビューの「最新」= 前年同期 Q、「2番目」= 前々年同期 Q となり、**YoY が (N-1年) vs (N-2年) の比較になる**。

| 6858 の実データ | FY | 1Q OP (百万円) |
|---|---|---|
| BQ iloc[0]（"最新"）| FY2025 | 330 |
| BQ iloc[1]（"2番目"）| FY2024 | 90 |
| J-Quants API（当日取得） | FY2026 | 423 |

- 誤: (330 - 90) / 90 = **+266.7% ≈ +267%**
- 正: (423 - 330) / 330 = **+28.2%**

**影響範囲**: **全銘柄**。ライブ予測（ノートブック）は 100% 影響。batch_rerun は過去日のためBQに当日データが存在するケースが多いが、BQロード遅延日は同じバグが出る。

**修正方針**: J-Quants API の当日データ（`df_fin`）から当期の単独四半期 OP を算出し、BQ ビューは前年同期の参照のみに使う。

**⚠️ 実装前の前提確認（レビュー指摘 F1）**: J-Quants `OP` と BQ ビュー `OPERATING_PROFIT` の**単位**を実データで確認すること。既存コードでは両者を直接比較していないため、単位不一致があると YoY が破壊的に壊れる。確認方法: 同一銘柄の同一四半期について J-Quants API 返却値と BQ ビュー値を並べて比較。

```python
# ── 修正後: 当期OPは J-Quants から算出、前年同期は BQ から取得 ──

# J-Quants の OP は累積値。1Q は累積=単独。2Q+は前Q累積を引く。
# 前Q累積は BQ fin_summary（前回開示 = 既にBQに入っている）から取得。
CUM_PREV_Q = {'2Q': '1Q', '3Q': '2Q', 'FY': '3Q'}

for _, row in df_fin.iterrows():
    tk = row['ticker']
    cur_per = row.get('CurPerType', '')
    q_label = Q_MAP.get(cur_per, '')
    jq_op_cum = row.get('OP')  # J-Quants 累積 OP

    # Step 1: 当期の単独Q OP を算出
    cur_standalone_op = None
    if pd.notna(jq_op_cum):
        if cur_per == '1Q':
            cur_standalone_op = float(jq_op_cum)  # 1Q: 累積=単独
        else:
            # 2Q+: 前Q累積を BQ fin_summary から取得して差引
            prev_q_type = CUM_PREV_Q.get(cur_per)
            if prev_q_type and tk in prev_cum_op_map:
                prev_cum = prev_cum_op_map[tk]  # BQ から取得した前Q累積OP
                cur_standalone_op = float(jq_op_cum) - float(prev_cum)
            else:
                # 半期報告企業対応（レビュー指摘 F2）:
                # 前Q累積が無い場合（1Q/3Q 未開示企業の 2Q 発表等）、
                # 累積値 = 単独値として扱う（BQ ビューの COALESCE(prev, 0) と同じ）
                cur_standalone_op = float(jq_op_cum)

    # Step 2: 前年同期の単独Q OP は BQ ビューから取得（こちらは正しい）
    tk_qoq = df_qoq[
        (df_qoq['LOCAL_CODE'].str[:4] == tk) & (df_qoq['QUARTER'] == q_label)
    ].sort_values('CURRENT_FISCAL_YEAR_START_DATE', ascending=False)

    prev_year_op = None
    if len(tk_qoq) >= 1:
        # BQ最新 = 前年同期（当期はBQ未反映なので最新が前年）
        prev_year_op = tk_qoq.iloc[0]['OPERATING_PROFIT']

    # Step 3: YoY 計算
    yoy_op = None
    if cur_standalone_op is not None and prev_year_op is not None and pd.notna(prev_year_op) and prev_year_op != 0:
        yoy_op = (cur_standalone_op - float(prev_year_op)) / abs(float(prev_year_op))
```

**前Q累積OP の取得（レビュー指摘 F3 対応: 具体クエリ定義）**:

§1-6 の前回発表クエリを拡張し、`OPERATING_PROFIT`（累積）も取得する。同一銘柄・同一会計年度・前四半期の累積 OP が必要。

```python
# ── §1-6 拡張: 前回発表の累積OP も取得 ──
q_prev = f"""SELECT LOCAL_CODE, DISCLOSED_DATE,
    FORECAST_OPERATING_PROFIT, FORECAST_PROFIT,
    OPERATING_PROFIT,  -- ← 追加: 累積OP（前Q累積の算出用）
    TYPE_OF_CURRENT_PERIOD,  -- ← 追加: 開示種別
    CURRENT_FISCAL_YEAR_START_DATE  -- ← 追加: 会計年度
FROM `gmailpj-357912.STOCK.fin_summary`
WHERE SUBSTR(LOCAL_CODE, 1, 4) IN ({tickers_sql})
  AND DISCLOSED_DATE < '{PREDICT_DATE_HYPHEN}'
ORDER BY LOCAL_CODE, DISCLOSED_DATE DESC"""

# prev_cum_op_map: ticker → 前Q累積OP
# 同一銘柄・同一会計年度・前四半期の累積 OP を取得
prev_cum_op_map: dict[str, float] = {}
for _, row in df_fin.iterrows():
    tk = row['ticker']
    cur_per = row.get('CurPerType', '')
    cur_fy_start = row.get('CurFYStartDt')  # J-Quants の会計年度開始日
    prev_q_type = CUM_PREV_Q.get(cur_per)
    if not prev_q_type:
        continue
    # BQ fin_summary から同一銘柄・同一FY・前四半期の最新レコード
    mask = (
        (df_prev['LOCAL_CODE'].str[:4] == tk)
        & (df_prev['TYPE_OF_CURRENT_PERIOD'] == prev_q_type)
        & (df_prev['CURRENT_FISCAL_YEAR_START_DATE'].astype(str) == str(cur_fy_start))
    )
    matched = df_prev[mask].sort_values('DISCLOSED_DATE', ascending=False)
    if not matched.empty and pd.notna(matched.iloc[0]['OPERATING_PROFIT']):
        prev_cum_op_map[tk] = float(matched.iloc[0]['OPERATING_PROFIT'])
```

**呼び出し側への波及**:
- `compute_score()` — 変更なし（入力の `yoy_op` 値が正しくなるだけ）
- `batch_rerun_predict.py:L320-349` — 同一ロジックの修正が必要（下記 P1-2 参照）
- GCS predictions JSON — `yoy_op` フィールドの値が変わる。過去データとの互換性問題なし

**検証**:
1. 6858（1Q, PREDICT_DATE=20260423）で `yoy_op` が +0.282（+28.2%）になることを確認
2. FY銘柄で `cur_standalone_op` = J-Quants OP - BQ 3Q累積 OP が正しいことを確認
3. 2Q/3Q銘柄でも同様に確認（前Q累積の差引が正しいか）

**ロールバック**: コミット revert で済む。GCS の過去 prediction JSON は修正不要（batch_rerun で再生成可能）

---

### P1-1. QoQ OP が常に None（F13 死亡） ⚠️

**症状**: F13（QoQ OP急変）が一度も発火したことがない。`qoq_op` は全銘柄で `None`。

**該当**: `earnings_model_predict.ipynb` Cell 5 §1-7 / `batch_rerun_predict.py:L338-348`

```python
# QoQ: 同一決算期内の直前Q単独OP との比較
_same_fy = tk_qoq[tk_qoq['CURRENT_FISCAL_YEAR_START_DATE'] == tk_qoq.iloc[0]['CURRENT_FISCAL_YEAR_START_DATE']]
qoq_op_val = None
if len(_same_fy) >= 2:  # ← 常に False
    ...
```

**根本原因**: `tk_qoq` は既に `QUARTER == q_label`（例: '1Q'）でフィルタ済み。各行は異なる FY の同一四半期。`_same_fy` は FY start が同じ行だけ取るが、同一 FY 内の同一 QUARTER は1行しかない。よって `len(_same_fy) >= 2` は常に `False`。

QoQ（例: 2Q vs 1Q）を計算するには、**同一 FY 内の異なる QUARTER のデータ**が必要だが、事前に QUARTER でフィルタしてしまっている。

**修正方針**: QoQ 計算は YoY とは別のデータスコープで行う。同一 FY の全 QUARTER を取得して隣接 Q を比較する。

```python
# ── QoQ 計算: 同一FY内の前Qと比較 ──
PREV_Q_MAP = {'2Q': '1Q', '3Q': '2Q', '4Q': '3Q'}  # 1Q は QoQ なし（前FYとの比較は YoY）
prev_q_label = PREV_Q_MAP.get(q_label)
if prev_q_label:
    # 当期 FY の前 Q を BQ ビューから検索
    cur_fy_start = ...  # J-Quants の CurFYStartDt
    prev_q_row = df_qoq_all[
        (df_qoq_all['tk'] == tk)
        & (df_qoq_all['QUARTER'] == prev_q_label)
        & (df_qoq_all['CURRENT_FISCAL_YEAR_START_DATE'] == cur_fy_start)
    ]
    if len(prev_q_row) == 1:
        prev_q_op = prev_q_row.iloc[0]['OPERATING_PROFIT']
        if pd.notna(cur_standalone_op) and pd.notna(prev_q_op) and prev_q_op != 0:
            qoq_op = (cur_standalone_op - float(prev_q_op)) / abs(float(prev_q_op))
```

**注意**: QoQ 計算用に BQ クエリのフィルタを変更する必要がある。現行は `WHERE QUARTER = q_label` 相当だが、QoQ には同一 FY の隣接 Q も必要。ただし現行のクエリは QUARTER フィルタがない（pandas 側で絞っている）ので、`df_qoq` にはすでに全 Q のデータが含まれている。pandas 側のフィルタロジックを変更するだけで済む。

**呼び出し側への波及**:
- `compute_score()` — 変更なし
- `batch_rerun_predict.py:L338-348` — 同一修正

**検証**:
1. 2Q 銘柄で QoQ = (2Q単独OP - 1Q単独OP) / |1Q単独OP| が正しく算出されることを確認
2. 1Q 銘柄で QoQ = None（前 Q なし）になることを確認
3. F13 が正しく発火する銘柄を確認（QoQ > +50% or < -50%）

**ロールバック**: コミット revert。F13 が「発火しない」→「発火する」への変化なので、過去との比較で score が変わる銘柄が出る

---

### P1-2. batch_rerun_predict.py の look-ahead bias ⚠️

**症状**: 過去日の再スコアリングで、予測日時点では未開示だったデータ（将来の決算実績）が YoY 計算に混入する。

**該当**: `batch_rerun_predict.py:L191-196`

```python
q_qoq = f"""SELECT LOCAL_CODE, QUARTER,
  CURRENT_FISCAL_YEAR_START_DATE, OPERATING_PROFIT, PROFIT
FROM `gmailpj-357912.STOCK.v_fin_summary_actual_for_q_on_q`
WHERE CURRENT_FISCAL_YEAR_START_DATE >= '{_qoq_from}'"""
```

**根本原因**: BQ ビューには `DISCLOSED_DATE` カラムがあるが、クエリで使っていない。全期間の確定データが返るため、過去 PREDICT_DATE の再実行時に「その時点ではまだ未開示だった決算」が YoY の分子に入る。

**batch_rerun 固有の事情**: batch_rerun は過去日をバッチ処理する。BQ には当日データが既に反映済みのため、P0-1 の「BQ に当日データがない」問題は逆に発生しにくい。**しかし逆に、未来のデータも含まれてしまう**（例: 4/10 の再実行で 4/14 に開示された修正値が使われる）。

**修正方針**: BQ クエリに `DISCLOSED_DATE` フィルタを追加。

```sql
-- before
WHERE CURRENT_FISCAL_YEAR_START_DATE >= '{_qoq_from}'

-- after
WHERE CURRENT_FISCAL_YEAR_START_DATE >= '{_qoq_from}'
  AND DISCLOSED_DATE <= '{max_predict_date_hyphen}'
```

ただし batch_rerun は複数日分を一括処理するため、per-date の as-of フィルタはクエリ後に pandas 側で行う（BQ クエリ回数最小化の原則）。`max_predict_date` で上限フィルタし、per-date の精密 as-of は pandas `merge_asof` 的に処理。

**呼び出し側への波及**: なし（batch_rerun 内完結）

**検証**: 4/10 分を再実行し、4/10 以降に開示された修正決算の影響がないことを確認

**ロールバック**: コミット revert

---

### P1-3. baseline_yoy_op（F7）にも同じ as-of フィルタ漏れ（レビュー指摘 F10）⚠️

**症状**: `baseline_yoy_op_map`（F7: 成長加速/減速）の算出クエリにも DISCLOSED_DATE フィルタがなく、batch_rerun で look-ahead bias が発生する。

**該当**: `earnings_model_predict.ipynb` Cell 5 §1-7b / `batch_rerun_predict.py:L351-365`

**修正方針**: P1-2 と同時に DISCLOSED_DATE フィルタを追加。ライブ予測（ノートブック）では FY 履歴は十分に過去のデータなので影響軽微だが、batch_rerun では対応必須。

---

## 対応アンチパターン

| plan ID | 004 | 備考 |
|---|---|---|
| P0-1 | — | データ取得タイミングの不整合（API vs BQ のラグ） |
| P1-1 | — | フィルタ順序の論理バグ（先に絞りすぎて必要データを消す） |
| P1-2 | — | as-of フィルタ欠如（look-ahead bias） |

---

## 検証戦略

1. **smoke test**: 6858 小野測器（1Q, PREDICT_DATE=20260423）で `yoy_op` = +0.282 を確認。QoQ は None（1Q なので前 Q なし）
2. **横展開確認**: 4/22-4/23 発表の全銘柄で修正前後の `yoy_op` を比較。修正後の方が J-Quants API の OP 値と整合していることを確認
3. **本番適用判断基準**: smoke test PASS + 横展開で明らかな異常値がないこと
4. **回収手順**: batch_rerun で過去分を一括再生成可能。GCS predictions/actuals を再生成すれば accuracy_summary も再集計される

---

## 実装手順（推奨順序）

1. **ノートブック Cell 5 修正**: P0-1（YoY）+ P1-1（QoQ）を同時修正。§1-7 を書き換え
2. **ローカルで smoke test**: PREDICT_DATE=20260423 で 6858 の yoy_op を確認
3. **batch_rerun_predict.py 修正**: 同一ロジック + P1-2（as-of フィルタ）
4. **Colab Notebooks にコピー**: PostToolUse フックで自動同期されるが、手動確認
5. **batch_rerun で 4/22-4/23 を再実行**: 修正効果を横展開確認

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/059_earnings_model_eda.md`（親知見・反省会ログ）
- BQ ビュー定義: `scripts/create_fin_summary_view.py`
- 関連 commit: 48b4499（現時点 HEAD）
- Codex 反省メモ: `C:\Users\zonekun\Documents\codex\investment-agent\docs\codex-to-claude-handoff.md` §2026-04-26（6858 分析）
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット
