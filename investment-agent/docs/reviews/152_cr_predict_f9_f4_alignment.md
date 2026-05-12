# コードレビュー: F9テーマブースト廃止 + F4コンセ乖離v3→v4統一改修

- 日時: 2026-05-12 23:30 JST
- 対象: `scripts/earnings_model/earnings_model_core.py`, `scripts/earnings_model/predict.py`, `scripts/earnings_model/review_report.py`, `docs/knowledges/tools/059_earnings_model_eda.md`, `docs/knowledges/tools/066_zaraba_tool.md`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: F9テーマブースト廃止（core.py/predict.py/review_report.py の3箇所削除）、F4コンセンサス乖離をEPSベースからNET_PROFITベースに変更（v3→v4）、OPフォールバック廃止、FY当期フォールバック廃止。関連MD（059/066）の更新。
- 品質評価: **B** — 方向性は正しく3ファイルの整合的な修正が行われているが、データ取得層に致命的な不整合が1件残存。
- 主要リスク:
  1. BQ CONSENSUS クエリと `_build_cons_map_from_df` が NET_PROFIT を取得・格納していないため F4b が全銘柄で常時 None（Silent failure）
  2. `_build_cons_map_from_df` の docstring が旧スキーマ（EPS）のまま
  3. `review_report.py` に `np_consensus_deviation` 列の表示が無く、F4b の寄与が反省会で不可視

## 【重大な指摘】（即修正）

### #1 BQ CONSENSUS クエリが NET_PROFIT を取得しておらず F4b が全銘柄で不発

- 箇所: `scripts/earnings_model/predict.py:389-391`, `scripts/earnings_model/predict.py:216-219`
- 事象: `fetch_shared_data()` の CONSENSUS クエリは `SELECT TICKER, FY, QUARTER, DATAAT, ORD_PROFIT, EPS` だが、F4b は `cons_map[key].get("NET_PROFIT")` を参照する。NET_PROFIT 列は BQ から取得されず、`_build_cons_map_from_df()` (L216-219) も ORD_PROFIT と EPS のみを格納するため、`cons_next_np` / `cons_cur_np` は常に None になる。
- トリガー: 全銘柄・全日付で F4b（`np_consensus_deviation`）が発火しない。全パス（predict / backfill / today）が `fetch_shared_data` → `_build_cons_map_from_df` → `compute_features` の同一経路を通るため例外なく影響する。
- 影響: F4b による NET_PROFIT 乖離検知が完全に無効化される。旧 EPS 乖離は廃止済みのため、F4 は ORD_PROFIT のみで動作し、NET_PROFIT 側の情報が欠落。IFRS 企業で OdP が欠損し F4a がスキップされた場合、F4b も不発となり F4 全体が沈黙する。ユーザーは「F4b が実装済み」と認識しているが実際には全く機能していない。
- 根拠: L389 の SQL に `NET_PROFIT` が含まれない。L216-219 の `_build_cons_map_from_df` も `ORD_PROFIT` と `EPS` のみ格納。一方 L707, L723 で `cons_next.get("NET_PROFIT")` / `cons_cur.get("NET_PROFIT")` を呼ぶが辞書にキーが存在しない。
- 推奨対応: **[検証済み]** 2箇所の修正が必要:
  1. L389 の SQL を `SELECT TICKER, FY, QUARTER, DATAAT, ORD_PROFIT, NET_PROFIT, EPS` に変更（BQ `STOCK.CONSENSUS` テーブルに NET_PROFIT 列が存在することは `022_consensus_load.md` L36 で確認済み）
  2. L216-219 の `_build_cons_map_from_df` に `if pd.notna(row.get("NET_PROFIT")): cons_map[key]["NET_PROFIT"] = float(row["NET_PROFIT"])` を追加
  - 波及: `_build_cons_map_from_df` の docstring (L170) も `{"ORD_PROFIT": ..., "EPS": ...}` → `{"ORD_PROFIT": ..., "NET_PROFIT": ..., "EPS": ...}` に更新

### #2 `_build_cons_map_from_df` の docstring が旧スキーマのまま

- 箇所: `scripts/earnings_model/predict.py:167-170`
- 事象: docstring に `cons_map[(ticker, quarter, "CURRENT"|"NEXT")] = {"ORD_PROFIT": ..., "EPS": ...}` と記載されている。v4 では NET_PROFIT を追加すべき（#1 の修正前提）。また、コメント L167 で「CONSENSUS v3 DataFrame から」と書いているが、本改修は v4 への移行。
- トリガー: コードの実動作には影響しないが、メンテナンス時に誤解を招く。
- 影響: 次回のコンセンサス改修時に docstring を信用して NET_PROFIT の存在を見落とす可能性。
- 根拠: L167 `"""CONSENSUS v3 DataFrame から cons_map を構築する.`、L170 `{"ORD_PROFIT": ..., "EPS": ...}`
- 推奨対応: **[検証済み]** L167 を `v4` に、L170 に `"NET_PROFIT": ...,` を追加。

## 【改善提案】（可読性・保守性）

### #1 `review_report.py` に `np_consensus_deviation` 列がなく F4b の寄与が反省会で不可視

- 箇所: `scripts/earnings_model/review_report.py:261`
- 現状: `build_row()` の出力に `コンセ乖離` 列（L261: `consensus_deviation`）はあるが、`np_consensus_deviation` に相当する列がない。core.py の `compute_score()` は F4a/F4b の max(|a|,|b|) を取って reasons に「純利コンセ乖離」として出力するため、reasons 列から間接的には分かる。しかし、F4a と F4b の個別値を反省会 CSV/MD で確認できないため、因子分解が困難。
- 提案: `build_row()` に `"純利コンセ乖離": fmt_pct(p.get("np_consensus_deviation"))` 列を追加する。PRED_COLUMNS にも既に `np_consensus_deviation` が含まれているため、prediction JSON には保存されている。

### #2 `split_reasons()` に「純利コンセ乖離」プレフィックスの分類ルールがない

- 箇所: `scripts/earnings_model/review_report.py:217-229`
- 現状: `split_reasons()` は reasons 文字列を POS/NEG に分割するが、`_POS_PREFIX` / `_NEG_PREFIX` に「純利コンセ乖離」が含まれていない。L217-219 のフォールバックで `frag.startswith("コンセ乖離")` にはマッチするが、`"純利コンセ乖離"` は `"コンセ乖離"` で始まらないため**未分類扱い**になり、L228 のデフォルトで NEG 側に寄せられる。正の純利コンセ乖離もネガ理由に分類される。
- 提案: L217 の判定を `frag.startswith("YoY OP") or frag.startswith("コンセ乖離") or frag.startswith("純利コンセ乖離")` に拡張するか、`_POS_PREFIX` に `"純利コンセ乖離"` を追加し符号判定するか。ただし `startswith` の順序に注意（「純利コンセ乖離」は「コンセ乖離」の前にチェックする必要がある）。

### #3 F9 廃止に伴い `beta_20d` が GCS ロードされ続ける

- 箇所: `scripts/earnings_model/predict.py:452-455`, `scripts/earnings_model/predict.py:808`
- 現状: `compute_features()` L808 で `"beta_20d": beta` を特徴量として出力し続けている。しかし PRED_COLUMNS にはリストされておらず、GCS prediction JSON には保存されない。`fetch_shared_data()` L452-455 では beta_20d.csv を GCS からロードし続けている。提出MDに「将来EDA用途に残存（意図的）」と記載があるため意図的な判断と理解。
- 提案: 意図的残存であれば、コードにコメントとして `# F9廃止後もEDA分析用途に保持` を付記する。将来不要になった場合の削除候補として明示。

### #4 059 MD の因子改善 TODO #3 のステータスが実態と乖離

- 箇所: `docs/knowledges/tools/059_earnings_model_eda.md:132`
- 現状: 因子改善 TODO #3「IFRS OdP→OPフォールバック」のステータスが「**実装済 (2026-05-03)**」だが、本改修で OP フォールバック自体が廃止されている。廃止を反映したステータス更新（「廃止 (2026-05-12)」等）が必要。
- 提案: ステータスを「廃止（OPフォールバック→OdP欠損時スキップに変更、2026-05-12）」に更新する。

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: scripts/earnings_model/predict.py:389
    q_cons = f"""SELECT TICKER, FY, QUARTER, DATAAT, ORD_PROFIT, EPS
    FROM `gmailpj-357912.STOCK.CONSENSUS`
    WHERE DATAAT <= '{hy(date_max)}'"""

# after
    q_cons = f"""SELECT TICKER, FY, QUARTER, DATAAT, ORD_PROFIT, NET_PROFIT, EPS
    FROM `gmailpj-357912.STOCK.CONSENSUS`
    WHERE DATAAT <= '{hy(date_max)}'"""
```

```python
# before: scripts/earnings_model/predict.py:216-219
        if pd.notna(row.get("ORD_PROFIT")):
            cons_map[key]["ORD_PROFIT"] = float(row["ORD_PROFIT"])
        if pd.notna(row.get("EPS")):
            cons_map[key]["EPS"] = float(row["EPS"])

# after
        if pd.notna(row.get("ORD_PROFIT")):
            cons_map[key]["ORD_PROFIT"] = float(row["ORD_PROFIT"])
        if pd.notna(row.get("NET_PROFIT")):
            cons_map[key]["NET_PROFIT"] = float(row["NET_PROFIT"])
        if pd.notna(row.get("EPS")):
            cons_map[key]["EPS"] = float(row["EPS"])
```

## 【確認できなかった事項】

- BQ `STOCK.CONSENSUS` テーブルの NET_PROFIT 列の充填率（QUICK ソースは全件あるか、IFIS ソースは NULL か）。022_consensus_load.md L36 に「IFIS=NULL」とあるため、IFIS のみカバーの銘柄では NET_PROFIT が NULL の可能性がある。その場合 F4b は実質 QUICK カバレッジ銘柄限定になる。
- zaraba_earnings.py のコンセンサス取得経路（V_CONSENSUS_MERGED VIEW 経由で5項目取得）と predict.py の取得経路（CONSENSUS テーブル直接、ORD_PROFIT + EPS のみ）が異なる。zaraba 側は NET_PROFIT を取得しており動作するが、predict 側は取得していない。この差異が意図的なものかは不明。
- GCS に保存済みの過去の prediction JSON には `eps_consensus_deviation` キーで値が保存されている。`np_consensus_deviation` キーに変更されたため、過去データを読み込む処理（answer コマンドで既存 prediction を読む箇所 L908）で旧キー名の値が無視される可能性がある。ただし answer は `df_pred["score"]` 等の既存カラムのみ参照するため実害は限定的。
