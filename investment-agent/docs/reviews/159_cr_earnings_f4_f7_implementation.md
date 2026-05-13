# コードレビュー: 決算反応モデル F4/F7因子バグ修正 — 実装コード

- 日時: 2026-05-13 00:15 JST
- 対象: `scripts/earnings_model/predict.py`（変更4箇所）、`scripts/sql/create_fn_consensus_merged_asof.sql`
- パターン: 2 (改修) — プラン `docs/plans/earnings-model-fix_20260512_220000.md` に基づく実装。前回レビュー 154_cr はプラン段階、今回は実装コード
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: P0-1（F4コンセンサスNaN）の根本原因を `df_prev_disc` のFY同一日dedup順序にあると特定し修正。QUICK優先マージをpandas側に実装。P1-1（F7 baseline不整合）を4Q単独OP→通期OP集約に修正。BQ TVFを `F_CONSENSUS_MERGED_ASOF` に一本化。
- 品質評価: **A** — 154_cr の指摘3件（`dff`変数保持・NaN安全性・診断確定後の修正）を全て踏まえた堅実な実装。根本原因の特定（df_prev_disc の同一日dedup順序）はプラン段階の3候補仮説より的確。
- 主要リスク:
  1. outer join でIFIS-only行の TICKER/FY/QUARTER が `_ifis_ord` リネーム側から来ないケースでの列NaN
  2. `df_prev_disc` のFY後回しソートが `dfpv_asof` (prev_forecast_map) に波及する意図しない副作用
  3. baseline通期集約でNaN四半期を `dropna` → 4Q揃いチェックする順序が一部エッジケースで過小計算を招く

## 【改修プラン評価】

### 妥当性

プラン段階では P0-1 の根本原因が未確定（3候補仮説）だったが、実装時に真の原因を「`df_prev_disc` の同一日FYdedup順序」と特定した。これは独自推定とも一致する: 2395は2026-02-06に3Q実績とFY予想修正を同時開示 → FY行が先に `drop_duplicates("tk")` で採用 → `_derive_current_fy("FY", ...)` が current_fy を202703に進める → CONSENSUS FY202703がCURRENT扱い → F4はNEXTを探すが空 → NaN。修正方針（`_fy_last` カラムによるsecondary sort）は根本原因に対処しており、方向性は正しい。

P1-1（baseline通期集約）も方向性は正しい。154_cr #1 指摘の `dff` 変数保持が実装されている。

### 副作用・デグレードチェック

- [x] `dff` 変数はL633で `dfq_asof[dfq_asof["QUARTER"] == "4Q"]` として残され、L654-660の `median_5y_op_map` 計算に使用 — 154_cr #1 指摘への対応済み。影響なし
- [x] `df_prev_disc` のFY後回しソート(L534-538)が `dfpv_asof = df_prev_disc.copy()` (L543) → `prev_forecast_map` (L544-546) に波及 — **問題なし**: FY行が後回しになることで、3Q実績の `FORECAST_OPERATING_PROFIT` が `prev_forecast_map` に入る。FY予想修正の予想値ではなく3Q時点の予想値が前回予想となる。3Qと同日にFY予想修正がある場合、FY予想修正行の方が「最新の予想」だが、predict.py の設計意図として「決算発表当日より前の予想」を使うので3Q時点が正しい。ただし下記 #2 のエッジケースあり
- [x] outer join (L204) でIFIS-only行: `q_rows` にない (TICKER, FY, QUARTER) 組が `i_rows` にのみ存在する場合、マージ後の行で `DATAAT`, `REVENUE`, `OP_PROFIT`, `NET_PROFIT`, `EPS` が全てNaN。`cons_map` 構築ループ (L209-232) では `pd.notna(row.get("ORD_PROFIT"))` 等で個別チェックするので **データロストはない**。ただし TICKER 列がNaN になる可能性あり — 下記 #1 参照
- [x] BQ TVF `F_CONSENSUS_MERGED_ASOF`: V_CONSENSUS_MERGED と同等ロジック + `DATAAT <= as_of_date` フィルタ。SQL定義を確認したところ、FULL OUTER JOINのCOALESCE設計はV_CONSENSUS_MERGEDと一致。旧 `fn_consensus_merged_asof` のDROP済みにより、旧TVFを参照する既存コードがあれば壊れるが、submissionでは「アドホック・単日クエリ向け」とあり、predict.py 内からの参照はない。影響なし

### 抜け漏れ（類似観点での横展開含む）

- [ ] **F3 YoY OPの比較軸不整合は未修正**: 154_cr #3 で指摘済み。F3の `yoy_op` (L604-611) はFY期でも4Q単独OP YoYを使用。F7のbaselineを通期に揃えてもF3が4Q単独のままでは不整合が残る。提出MDでは「スコープ外」とされており意図的な除外と判断するが、7951ヤマハの `YoY OP +1209%` 問題は残存
- [ ] **`is_low_base` 判定 (L770-777)**: `median_5y_op_map` は4Q単独OPベース (`dff` 使用) のまま。`next_year_op_change` の再計算 (L776-777) で `_median_5y_op` を分母にするが、これは4Q単独OP中央値。通期OPの `next_year_op_change` と4Q単独OP中央値の比較は不整合だが、F7同様に本タスクのスコープ外
- [ ] **059_earnings_model_eda.md の因子定義更新**: プランMD §残タスクに記載なし。F7の定義が「4Q単独OP baseline」から「通期OP baseline」に変更されたが、知見MDの因子説明は未更新

### 新規リスク

- baseline計算のパフォーマンス: 全銘柄で `dfq_asof.groupby` → 4Q揃いチェック → sum を実行。154_cr で指摘した性能懸念だが、`dfq_asof` は `CURRENT_FISCAL_YEAR_START_DATE >= '2020-04-01'` フィルタ済み（L430, 最大6年分）かつ predict/backfill の1日分のtickerに限定されるため、実用上は問題ないと推定

---

## 【重大な指摘】（即修正）

### #1 outer join でIFIS-only行のTICKER列がNaNになる

- 箇所: `scripts/earnings_model/predict.py:L192-206`
- 事象: `q_rows` (QUICK) と `i_rows` (IFIS) の `merge(..., how="outer")` で、IFIS-onlyの (TICKER, FY, QUARTER) 組はマージキーの `TICKER`, `FY`, `QUARTER` 列が `i_rows` 側から供給される。しかし `i_rows` は L201 で `[["TICKER", "FY", "QUARTER", "ORD_PROFIT"]]` に絞った後 `.rename(columns={"ORD_PROFIT": "_ifis_ord"})` しており、マージキー列 `TICKER`, `FY`, `QUARTER` は `i_rows` 側に残っている。pandas の `merge(on=["TICKER", "FY", "QUARTER"], how="outer")` では、on列は両側から取られ、IFIS-only行ではi_rows側の値が使われる。**したがってTICKER列がNaNになることはない。**
- 結論: **指摘取り下げ** — 脳内シミュレーションで再検証した結果、pandas の `merge(on=..., how="outer")` はon列をCOALESCEする設計。IFIS-only行でもTICKER/FY/QUARTER は正しく値が入る。問題なし。

### #2 df_prev_disc のFY後回しソートが翌日以降の開示にも影響する

- 箇所: `scripts/earnings_model/predict.py:L531-538`
- 事象: FY後回しソートは「同一日に3Q実績とFY予想修正がある場合」のための修正だが、ソート条件 `ascending=[False, True]` はDISCLOSED_DATE降順→`_fy_last`昇順（FY=1が後）。これにより**同一日にFY実績開示のみの銘柄**（3Qと同日でない通常のFY開示）では問題なし：FY行しかないので `drop_duplicates("tk")` でそのまま最初のFY行が採用される
- トリガー: 同一日に**1Q実績とFY予想修正**、**2Q実績とFY予想修正**が同時開示される銘柄（2395の3Q+FYパターンの類似ケース）
- 影響: これらのケースでも正しくQ実績行を優先してFY予想修正を後回しにする。**意図通りの動作**
- 結論: **問題なし** — ただし、同一日に**FY実績と翌期1Q予想修正**が同時開示されるケースでは、FY実績行が後回しになり、翌期1Qの予想修正行が先に採用される。この場合 `_derive_current_fy` に「1Q（または2Q/3Q）」のTYPE_OF_CURRENT_PERIODが渡り、current_fy がFY末日のYYYYMM（翌期ではなく当期）になる。FY実績開示の翌日に predict が走る場合、current_fy が当期のまま → コンセンサスNEXTキーが翌期ではなく翌々期を指す → NaN。**ただし、fin_summaryのTYPE_OF_CURRENT_PERIODはその行自体の期区分であり、FY実績と翌期Q予想は別行として記録されるため、同一日に同一銘柄で異なるTYPE_OF_CURRENT_PERIODの行が存在する場合に限りこの問題が発生する。**
- 推奨対応: **[方向性]** FY実績行と予想修正行が同一日に共存するパターンをBQでカウントし、発生頻度を確認。もし稀であれば現行実装で許容。頻度が高ければ `_fy_last` の条件を「FY」単純一致ではなく「同一FY期の実績Q行が同日にある場合のみFYを後回し」に精緻化する

### #3 baseline通期集約でNaN四半期のOPが0として合算される

- 箇所: `scripts/earnings_model/predict.py:L639-642`
- 事象: L639で `grp_valid = grp.dropna(subset=["OPERATING_PROFIT"])` して NaN行を除外、L640で `set(grp_valid["QUARTER"].tolist()) >= {"1Q", "2Q", "3Q", "4Q"}` で4Q揃いをチェック。この順序は正しく、NaN行が除外された後に4Q揃いを確認するので、**1Q〜4Qの全四半期にデータがある場合のみsum**される。154_cr改善提案#1 の指摘（NaN値を含む四半期もカウント）は対応済み
- トリガー: **ただし**、同一銘柄×同一FY期に同一QUARTERが複数行存在する場合（修正開示による重複）。ビュー `v_fin_summary_actual_for_q_on_q` は「同一銘柄×事業年度×期区分で最新開示のみ残す」設計（bq_fin_summary.md L316-317）なので通常は重複しないが、`dfq_asof` は `DISCLOSED_DATE < ph` でフィルタした後に重複排除していない
- 影響: 修正開示が同一FY期にある場合、同一QUARTERが複数行 → sumが二重計算になる可能性。ただしビュー側の重複排除が効いていれば問題なし
- 結論: **ビュー定義に依存しており、ビュー側で重複排除済みであれば問題なし**。実害の可能性は低い
- 推奨対応: **[方向性]** `grp_valid` に対して `drop_duplicates(["QUARTER"], keep="first")` を入れれば二重計算を防げる。コスト: 1行追加のみ

---

## 【改善提案】（可読性・保守性）

### #1 `_fy_last` の意図をコメントで補足

- 箇所: `scripts/earnings_model/predict.py:L534`
- 現状: `dfpv_before["_fy_last"] = (dfpv_before["TYPE_OF_CURRENT_PERIOD"] == "FY").astype(int)` — 変数名から「FYを最後にする」意図は読み取れるが、**なぜ** FYを後回しにするのか（2395の同一日3Q+FY開示問題）のコメントがコード内に3行ある（L532）。これは十分。ただし `_fy_last` という変数名よりも `_sort_fy_after_q` のような意図明示的な名前の方が保守性が高い
- 提案: 変数名変更は任意。現状のコメントで実用上は問題なし

### #2 `SOURCE` 列のdrop漏れ安全策

- 箇所: `scripts/earnings_model/predict.py:L206`
- 現状: `dfc = dfc.drop(columns=["_ifis_ord", "SOURCE"], errors="ignore")` — `errors="ignore"` により、`SOURCE` がIFIS-only行で存在しなくてもエラーにならない。ただし、outer joinの結果 `SOURCE` 列は `q_rows` 側にのみ存在し、IFIS-only行では NaN になる。`drop(columns=["SOURCE"])` で列自体が消えるので問題なし
- 提案: 現状で問題なし。`errors="ignore"` は安全策として適切

### #3 F3/F7の比較軸統一を次タスクとして明示追跡

- 箇所: プランMD §残タスク
- 現状: F7のbaselineは通期に揃えたが、F3 yoy_op（L604-611）はFY期でも4Q単独OPのYoYを使用。154_cr #3 で指摘済みだが、プランMDの残タスクに記載がない
- 提案: `docs/knowledges/tools/059_earnings_model_eda.md` の因子改善TODOに「F3 FY期のyoy_opを通期OPベースに変更」を追記。同時にF7定義変更（4Q単独→通期baseline）の反映も

---

## 【確認できなかった事項】

- `v_fin_summary_actual_for_q_on_q` ビューの重複排除が `DISCLOSED_DATE < ph` 条件でも保持されるか（ビュー定義はROW_NUMBER()で最新のみ残すが、predict.py側のフィルタ後に重複が再発しないかの実データ検証）
- 同一日にFY実績と同時にQ予想修正が開示される銘柄の実在頻度（BQクエリ未実行）
- backfill全期間でのF4/F7スコア変化の定量的影響（実行して確認が必要）
- IFIS-only銘柄でcons_mapにORD_PROFITのみ入るケースが、F4b（NP_CONSENSUS_DEVIATION）のNaN増加に繋がるか（データ特性依存）
