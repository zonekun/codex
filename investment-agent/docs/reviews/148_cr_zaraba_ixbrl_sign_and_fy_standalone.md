# レビュー提出: ザラ場ツール iXBRL sign属性 + FY standalone バグ修正

- レビューパターン: 1（新規コードのまっさらレビュー — バグ修正差分）
- 対象ファイル:
  - `scripts/zaraba_tdnet_poller.py` — `_parse_ixbrl()` sign属性修正
  - `scripts/zaraba_earnings.py` — `_build_prior_data()` prev_cumulative_op FY修正

## 事象・背景

2026-05-11 のザラ場ツール結果で2件のバグが発覚し修正を実施。

### Bug #1: iXBRL `sign` 属性無視（zaraba_tdnet_poller.py L397-400）

- **症状**: 3401 帝人 FY OP実績 = -707億（営業赤字）が +707億として抽出 → 進捗↑1414%、コンセ乖離+268.7%、score +4 (S-Buy) と真逆の判定
- **原因**: `_parse_ixbrl()` が iXBRL `ix:nonFraction` 要素の `sign="-"` 属性を読んでいなかった
- **修正**: `attrs.get("sign") == "-"` のとき `scaled_value = -scaled_value` を追加
- **正しい結果**: score -7 (SELL) 相当

### Bug #2: FY時の `prev_cumulative_op` が None（zaraba_earnings.py L695-705）

- **症状**: 7931 未来工業 FY で QoQ+436%（通期合計67億 vs Q3単独12億の無意味な比較）
- **原因**: prepare時に最新BQ開示がFYの場合 `prev_cumulative_op = None` に設定（「次は1Q」想定）。しかし catchup 等でFY自体を処理するとstandalone_op = cumulative（通期合計）になる
- **修正**: latest==FY時、rn=2（3Q）の累計値を `prev_cumulative_op` に格納。1Qは `cur_per=="1Q"` ガードで影響なし
- **正しい結果**: Q4単独 = 67.23 - 54.68 = 12.55億、QoQ = -46%

## 横展開調査結果

### Bug #1 横展開

| ファイル | 形式 | 影響 | 理由 |
|---------|------|------|------|
| `zaraba_tdnet_poller.py` | iXBRL | **修正済み** | TDnet決算短信はiXBRL。`sign="-"` で負値表現 |
| `xbrl_to_jquants/extract_pipeline.py` | 標準XBRL | 影響なし | EDINET有報は標準XBRL。負値はテキスト内 `-70714` |
| `xbrl_to_jquants/xbrl_mapping.py` | 標準XBRL | 影響なし | 同上 |
| `edinet_xbrl_extractor.py` | iXBRL | 低リスク | 現金・有価証券のみ（正値） |

### Bug #2 横展開

| ファイル | 影響 | 理由 |
|---------|------|------|
| `zaraba_earnings.py` F13/F3 | **修正済み** | rn=2の3Q累計を格納するよう修正 |
| `earnings_model/predict.py` | 影響なし | `CUM_PREV_Q={"FY":"3Q"}` で正しくマッピング |
| BQ view `v_fin_summary_actual_for_q_on_q` | 影響なし | SQL LAG()で正しく算出 |

## 補足

- 知見MD更新済み: `docs/knowledges/tools/071_xbrl_to_jquants.md` §注意事項に両バグの記録を追記

---

# コードレビュー: ザラ場ツール iXBRL sign属性 + FY standalone バグ修正

- 日時: 2026-05-12 23:30 JST
- 対象: `scripts/zaraba_tdnet_poller.py` L394-401, `scripts/zaraba_earnings.py` L695-708（未コミット差分）
- パターン: 1 (新規 — バグ修正差分のまっさらレビュー)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: iXBRLパーサーの`sign="-"`属性未処理による符号反転バグと、FY catchup再処理時の`prev_cumulative_op`=None によるQ4単独OP異常値バグの2件を修正
- 品質評価: **A** — 両修正とも根本原因に対する最小修正で、既存の正常パスへの影響が小さい設計。1点の潜在エッジケースあり
- 主要リスク:
  1. Bug #2 修正で `fin.iloc[1]` が3Q以外（別FY等）の場合、`prev_cumulative_op` に誤った値が入る（改善提案 #1）
  2. `prev_cumulative_np` は設定されるが消費コードが存在せず、Bug #2 修正の NP 側は現状デッドコード（改善提案 #2）
  3. Bug #1 横展開の `edinet_xbrl_extractor.py` 評価は妥当。ただしBS項目でも将来的にiXBRL形式を扱うなら要対応

## 【重大な指摘】（即修正）

### #1 `fin.iloc[1]` が3Qである保証がない（Bug #2 修正のエッジケース）

- 箇所: `scripts/zaraba_earnings.py:699-701`
- 事象: `fin.iloc[1]`（rn=2）が3Qではなく別のFY（前年度）であった場合、`prev_cumulative_op` に前年度の通期累計OPが格納される。このとき `standalone_op = 今期FY_OP - 前期FY_OP` となり、Q4単独OPではなくYoY差分になる
- トリガー: 四半期報告書を出さない企業（一部の持株会社等）、または変則決算で3Q開示がスキップされた場合
- 影響: F13 QoQ が異常値になる（修正前のNoneフォールバックとは異なる方向の誤り）
- 根拠: SQLは `QUALIFY ROW_NUMBER() OVER (PARTITION BY LOCAL_CODE ORDER BY DISCLOSED_DATE DESC) <= 2` であり、`TYPE_OF_CURRENT_PERIOD` でフィルタしていない。コメントには「rn=2（3Q）」とあるが、rn=2が3Qである保証はない
- 推奨対応: **[方向性]** `prev_row` の `TYPE_OF_CURRENT_PERIOD` を検証し、`"3Q"` でない場合は `prev_cumulative_op = None` にフォールバックする。これにより、3Qが取れない場合はBug修正前と同等の動作（`prev_cumulative_op = None` → `standalone_op = cumulative_op`）になり、少なくとも「YoY差分をQ4単独と誤認」するリスクを排除できる

**[採用]** — `fin.iloc[1]` の `TYPE_OF_CURRENT_PERIOD == "3Q"` ガードを追加。3Q以外はNoneフォールバック。

## 【改善提案】（可読性・保守性）

### #1 `prev_cumulative_np` がデッドコード

- 箇所: `scripts/zaraba_earnings.py:702, 705, 708`
- 現状: `prev_cumulative_np` は `_build_prior_data()` で設定（Bug #2 修正でOP側と同一のロジックを適用済み）されるが、`_score_record()` を含むスコアリングコードのどこにも参照がない。`prev_cumulative_op` のみがL1646で読まれ、`standalone_op` 算出に使用されている
- 提案: 将来的にNP版のQ単独因子を追加する予定がなければ、デッドコードとして削除を検討。残す場合はコメントで「将来のstandalone_np算出用に予約」等と明記すると保守性が上がる

**[見送り: standalone_np因子の追加は検討中。現時点では残置]**

### #2 Bug #1 修正コメントの補足

- 箇所: `scripts/zaraba_tdnet_poller.py:398-400`
- 現状: コメント `# iXBRL sign 属性: sign="-" → 値を反転（営業損失等の負値表現）` は的確だが、iXBRL仕様への参照がない
- 提案: 知見MD `071_xbrl_to_jquants.md` §注意事項に既に記録されているため、コメントに `# → 071_xbrl_to_jquants.md §注意事項` 等のポインタを追加すると、将来のメンテナ（人間・AI問わず）がiXBRL仕様の文脈を即座に参照できる

**[採用]** — ポインタコメントに変更済み

## 【横展開調査結果の検証】

### Bug #1 横展開: 妥当

- `xbrl_to_jquants/extract_pipeline.py` / `xbrl_mapping.py`: lxml ベースの標準XBRLパーサー(`parse_xbrl()`)。`tag.text` から直接数値を取得しており、iXBRL の `sign` 属性は関係しない。**影響なし: 正しい判定**
- `edinet_xbrl_extractor.py`: BeautifulSoup で標準XBRLをパース。CASH_TAGS / SECURITY_TAGS のみ抽出。`tag.text.strip()` で値取得。iXBRL の `ix:nonFraction` タグは扱わない。BS項目（現金・有価証券）は本来正値のみ。**低リスク: 正しい判定**

### Bug #2 横展開: 妥当

- `earnings_model/predict.py`: `CUM_PREV_Q = {"2Q": "1Q", "3Q": "2Q", "FY": "3Q"}` マッピングで、FY処理時に明示的に `TYPE_OF_CURRENT_PERIOD == "3Q"` の行を BQ から検索。zaraba_earnings.py のように rn=2 の行を暗黙に使うのではなく、TYPE_OF_CURRENT_PERIOD でフィルタしている。**影響なし: 正しい判定**。ただし predict.py の方がより堅牢な実装であり、重大指摘 #1 の修正方針の参考になる
- BQ view `v_fin_summary_actual_for_q_on_q`: SQL の LAG() ウィンドウ関数で前Q値を計算。Python 側の `prev_cumulative_op` とは独立。**影響なし: 正しい判定**

## 【確認できなかった事項】

- `edinet_xbrl_extractor.py` が実際に iXBRL 形式の文書を処理する運用パスがあるかどうか（現在はEDINET有報の標準XBRLのみを処理している認識だが、将来的にBS項目のiXBRL抽出に拡張する可能性は不明）
- 四半期報告書を出さない上場企業（=`fin.iloc[1]`がFYになるケース）が実際に存在するか。日本の金融商品取引法では四半期報告書の提出義務があるが、2024年の四半期報告書廃止（四半期決算短信への移行）以降、fin_summaryにおける3Q相当レコードの有無は確認が必要
- `sign` 属性が `"-"` 以外の値（例: `"+"` や空文字）を取るiXBRLデータが実在するか（仕様上は `"-"` のみ有効）
