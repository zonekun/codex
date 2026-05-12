# コードレビュー: zaraba_earnings.py CONSENSUS v3 スキーマ対応改修

- 日時: 2026-05-05 18:37 JST
- 対象: `scripts/zaraba_earnings.py` (L481-932, L760-805, L966-1004, L1534-1542, L1761-1793), `scripts/sql/create_v_consensus_merged.sql`
- パターン: 1 (新規作成コードのまっさらレビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: STOCK.CONSENSUS テーブルの TARGET列廃止・5項目対応に伴い、FY判定ロジックを fin_summary の prev_disc_type/prev_disc_fy_end から導出する方式に全面書き換え。prior_data.json のスキーマを v3 (nested dict) に移行。
- 品質評価: **B** — 全体設計は健全だが、sub-annual quarter の FY フィルタ漏れが実データで誤値を生む可能性あり
- 主要リスク:
  1. `_consensus_to_prior_fields` が 1Q/2Q/3Q 行を FY 無関係に格納し、複数FY存在時に不定値が入る
  2. `_derive_current_fy` が変則決算期変更（12ヶ月超/未満の経過期）で誤った current_fy を返す
  3. `_print_prepare_summary` で consensus=0.0 のとき表示が "-" になる（軽微）

---

## 【重大な指摘】（即修正）

### #1 sub-annual quarter (1Q/2Q/3Q) が FY 無関係に by_q に格納される

- 箇所: `scripts/zaraba_earnings.py:920`
- 事象: `if q in {"1Q", "2Q", "3Q"}: by_q[q] = vals` は row の FY 値を検証していない。V_CONSENSUS_MERGED は (TICKER, FY, QUARTER) 粒度でデータを返すため、同一 TICKER に FY=202503/1Q と FY=202603/1Q が共存する場合、DataFrame のイテレーション順（BQ クエリの返却順に依存、不定）で最後にイテレートされた行が勝つ。
- トリガー: IFIS が毎期ごとに 1Q/2Q/3Q コンセンサスを登録している銘柄で、旧 FY の行が BQ に残存している場合（=通常運用で発生）。
- 影響: F4c スコアリングで前期の 1Q コンセンサス値を今期の実績と比較し、乖離率が異常値になる。2026-04-30 の 1878 大東建託事故（v2 の iloc[0] バグ）と同構造の問題が v3 で再発する可能性。
- 根拠: V_CONSENSUS_MERGED の QUALIFY は (TICKER, FY, QUARTER) 単位で最新 DATAAT を選ぶが、異なる FY の同 QUARTER 行は別パーティションとして両方残る。`_consensus_to_prior_fields` は FY 列を sub-annual quarter に対してフィルタしていない。
- 推奨対応: 1Q/2Q/3Q も `current_fy` と一致する FY のみ格納する。FY==None 時（current_fy 導出不能）は全 sub-annual を格納する現行動作で可。

```python
# before: scripts/zaraba_earnings.py:920
if q in {"1Q", "2Q", "3Q"}:
    by_q[q] = vals

# after
if q in {"1Q", "2Q", "3Q"}:
    if current_fy is None or fy == current_fy:
        by_q[q] = vals
```

### #2 変則決算期変更時に `_derive_current_fy` が誤値を返す【無視可能・deferred】

> **判断**: 年間数十社・FY発表時のみ・silent skip（クラッシュなし）。影響限定的のため対応延期。

- 箇所: `scripts/zaraba_earnings.py:854-872`
- 事象: `prev_disc_type == "FY"` の場合、一律で `year + 1` + 同月を返す。しかし決算期変更（例: 3月決算→12月決算に変更、経過期が9ヶ月）があると、翌年の FY_END が `{y+1}03` ではなく `{y}12` や `{y+1}12` になる。この関数は「次の FY_END は今の FY_END の12ヶ月後」という仮定に基づいているが、決算期変更企業ではこの仮定が崩れる。
- トリガー: 決算期変更を実施した企業（年間数十社程度、例: 2782 セリア 2024年2月期→3月期変更）の FY 発表後のサイクル。
- 影響: current_fy が実際の次期 FY と一致せず、FY 行のコンセンサスが CURRENT にも NEXT にも格納されない（スコアリングでコンセンサス乖離が計算されない = silent skip）。データ欠損だがクラッシュはしない。
- 根拠: `return f"{y + 1}{m}"` は FY_END の年を+1して月はそのまま返す。決算期変更後の新 FY_END は異なる月になるため、BQ CONSENSUS の FY 値（新 FY_END の YYYYMM）と一致しない。
- 推奨対応: 影響範囲が限定的（年間数十社・FY発表時のみ）かつ根本対処には BQ クエリ追加が必要なため、現時点では docstring に制約を明記し、将来的にカレンダーマスタから次期 FY_END を引く拡張を検討。ただし #1 の修正で sub-annual quarter は current_fy でフィルタされるため、影響は FY 行のみに局所化される。

---

## 【改善提案】（可読性・保守性）

### #1 `_print_prepare_summary` の零値表示バグ

- 箇所: `scripts/zaraba_earnings.py:992`
- 現状: `conse_s = _fmt_yen(conse * 1e6) if conse else "-"` — `conse` が `0.0` の場合、`if conse` が False となり "-" が表示される。コンセンサス経常利益が0（赤字転換直後の企業で理論上ありうる）の場合に misleading。
- 提案: `if conse is not None` に変更する。

### #2 `_consensus_to_prior_fields` の "9" マジック文字列

- 箇所: `scripts/zaraba_earnings.py:926`
- 現状: `out.get("consensus_next_fy", "9")` — YYYYMM 形式の最小 NEXT FY を選ぶロジックで、デフォルト値 `"9"` を使用。動作は正しい（全ての YYYYMM 文字列は "9" より小さい）が、意図が不明瞭。
- 提案: `"999999"` のように YYYYMM フォーマットに合わせた sentinel 値にするか、コメントで意図を補足する。

### #3 `_guidance_vs_consensus` の None ガード不足

- 箇所: `scripts/zaraba_earnings.py:1538`
- 現状: `cons_next = prior.get("consensus_next")` は v3 未格納時に None を返す。次行 `cons_next.get("ORD_PROFIT") if isinstance(cons_next, dict) else None` で isinstance ガードしているので crash はしないが、`prior` がそもそも空 dict（prepare 対象外銘柄で `p = {"ticker": ..., "name": ...}` が入る L1575 のケース）の場合も安全に動作する。問題なし — 確認のみ。

### #4 TypedDict `ConsensusFields` の total=False と実態の整合

- 箇所: `scripts/zaraba_earnings.py:878-886`
- 現状: `total=False` で全キーがオプショナル。`consensus_by_q` がなくても `consensus_next` / `consensus_next_fy` だけ返る可能性がある（FY > current_fy の行のみ存在する銘柄）。下流の `_print_prepare_summary` と `_score_record` は `by_q` が None/空でも安全に動作するため実害なし。
- 提案: docstring に「consensus_next のみ存在するケース（sub-annual コンセなし銘柄）がありうる」旨を補記すると将来の保守者に親切。

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: scripts/zaraba_earnings.py:920-921
        if q in {"1Q", "2Q", "3Q"}:
            by_q[q] = vals

# after
        if q in {"1Q", "2Q", "3Q"}:
            # current_fy が既知の場合、同FYの行のみ格納（他FYの古い行を排除）
            if current_fy is None or fy == current_fy:
                by_q[q] = vals
```

---

## 【確認できなかった事項】

- BQ STOCK.CONSENSUS に実際に複数 FY の sub-annual quarter 行が共存しているかどうか（IFIS の過去データ残存状況）。VIEW の QUALIFY は FY 別なので論理的には共存するが、IFIS スクリプトが過去 FY の行を DELETE しているかは未確認。DELETE していない場合 #1 は必ず発火する。
- `CURRENT_FISCAL_YEAR_END_DATE` が pandas で読み込まれた際の正確な文字列表現（DATE 型 → str 変換が `"2026-03-31"` なのか `"20260331"` なのか）。`replace("-", "")` で両方カバーしているが、`Timestamp` 型のまま来た場合に `str()` が `"2026-03-31 00:00:00"` になる可能性がある（この場合も `replace("-", "")[:8]` = `"20260331"` で正しく動作する）。
- 決算期変更企業の CONSENSUS テーブル上の FY 値がどう格納されているか（変更後の新 FY_END で登録されているか、経過期の FY_END で登録されているか）。
