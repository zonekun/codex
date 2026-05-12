# コードレビュー: backfill --from/--to を actual_date ベースに統一

- 日時: 2026-05-09 17:52 JST
- 対象: `scripts/earnings_model/predict.py`（3関数の修正）, `C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（2箇所の修正）
- パターン: 1 (新規レビュー・直接依頼)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: backfill サブコマンドの `--from`/`--to` が predict_date（決算発表日）ベースだったものを actual_date（答え合わせ日）ベースに統一。today サブコマンドが actual_date を受け取る設計と一貫性を持たせた。
- 品質評価: **A** — 設計方針が明確で、ロジック変更の整合性が取れている。重大バグなし。改善提案1件のみ。
- 主要リスク:
  1. `_resolve_backfill_range` の BQ クエリで NULL 結果時のハンドリングが不十分（既存問題だが新コードで顕在化しやすい）
  2. `fetch_shared_data` の TDNET クエリ範囲が actual_date ベースの date_to と整合するか要確認
  3. PS1 側の変更なし部分（argparse ヘルプ文変更）はユーザーのメンタルモデルとの一貫性に限定

---

## 【重大な指摘】（即修正）

### #1 `_resolve_backfill_range` の BQ クエリで min_actual / max_actual が NULL になる可能性

- 箇所: `scripts/earnings_model/predict.py:1097-1106`
- 事象: BQ サブクエリ `SELECT MIN(d2.DATE) FROM dates d2 WHERE d2.DATE > '{max_pd_h}'` が `DATE_ADD('{max_pd_h}', INTERVAL 5 DAY)` の範囲内に翌営業日を見つけられない場合、`max_actual` が NULL になる。`str(None).replace("-", "")[:8]` は `"None"[:8]` = `"None"` を返し、下流の `_get_business_day_pairs("None", ...)` で不正な BQ SQL が生成される。
- トリガー: max_pd_h が BQ の STOCK_PRICE_JQUANTS テーブルの最新日付より5日以上先の未来日を指す場合。具体的には、GCS に当日分の prediction を保存した直後（まだ翌営業日の株価データが未投入）に `backfill`（引数なし・リビルド）を実行したとき。
- 影響: 不正なBQクエリでエラー終了。データ破壊はないが、ユーザーにとって原因不明のエラーになる。
- 根拠: L1105-1106 で `str(df.iloc[0]["max_actual"])` を呼ぶが、BQ の NULL は pandas で `None` / `NaT` になる。`.replace("-", "")` は `None` に対して AttributeError を起こす（NaT の場合は `str(NaT)` = `"NaT"` で同様に不正値）。
- 推奨対応: **[方向性]** `min_actual` / `max_actual` それぞれに対して `pd.isna()` でチェックし、NULL の場合は明示的なエラーメッセージで raise する。旧コードでは predict_date をそのまま返していたので NULL は発生しなかったが、新コードは BQ 依存のため防御が必要。

**[採用]** NULLガード追加済み。併せて INTERVAL 5 DAY → 10 DAY に拡張（年末年始対応）。

---

## 【改善提案】（可読性・保守性）

### #1 `fetch_shared_data` への引数 `date_min_predict` の意味的整合

- 箇所: `scripts/earnings_model/predict.py:1129-1131`
- 現状: 修正後のコードは `fetch_shared_data(date_min_predict, date_to, date_max_actual)` を呼ぶ。第1引数 `date_min_predict` は `pairs[0][0]`（最小 predict_date）で正しい。第2引数 `date_to` は actual_date ベースの終了日。`fetch_shared_data` 内部では `date_max`（= `date_to`）を以下に使う:
  - L385: CONSENSUS の `DATAAT <= date_max` — actual_date ベースだと CONSENSUS の as-of 日付が predict_date より1営業日分余分になるが、as-of フィルタは `compute_features` 内の `ph`（predict_date）で再度かかるので実害なし
  - L403: fin_summary の `DISCLOSED_DATE <= date_max` — 同上
  - L415: QoQ の `DISCLOSED_DATE <= date_max` — 同上
  - L425: TDNET の `SUBMISSION_DATE BETWEEN date_min AND date_max` — date_min は predict_date ベースだが date_max が actual_date ベースなので、predict_date を正確にカバーする。問題なし
  - L437: INDEX_PRICE の `DATE BETWEEN price_min AND date_max` — date_max が actual_date なのでむしろ以前（predict_date）より広くなり、TOPIX リターン計算に必要な日付も含まれる。正しい
- 提案: 整合性は保たれているが、`fetch_shared_data` のパラメータ名が `date_min`/`date_max` なのに、実引数が predict_date/actual_date と混在するのは将来の保守者にとって混乱源。docstring に「date_max は actual_date ベースの終了日を渡してよい（内部の as-of フィルタが正しく機能する）」旨を明記すると良い。

**[採用]** docstring に補足追加済み。

### #2 PS1 の range プロンプトと Python ヘルプ文の一致確認

- 箇所: `claude-investment-agent.ps1:351`, `predict.py:1360-1361`
- 現状: PS1 のプロンプトが `"答え合わせ日の範囲 (YYYYMMDD YYYYMMDD  例: 20260501 20260508)"` に変更済み。Python の argparse ヘルプも `"開始答え合わせ日"` / `"終了答え合わせ日"` に変更済み。一致しており問題なし。
- 提案: ドキュメント（スクリプト冒頭の Usage コメント L14）に `backfill --from 20260401 --to 20260428` の例が残っているが、これが predict_date なのか actual_date なのかコメントだけでは判別できない。Usage コメントに `(答え合わせ日)` と補足するとユーザーの混乱を防げる。

**[採用]** Usage コメントに「答え合わせ日の範囲」を補足済み。

---

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案

```python
# before: scripts/earnings_model/predict.py:1105-1106
    min_actual = str(df.iloc[0]["min_actual"]).replace("-", "")[:8]
    max_actual = str(df.iloc[0]["max_actual"]).replace("-", "")[:8]

# after
    raw_min = df.iloc[0]["min_actual"]
    raw_max = df.iloc[0]["max_actual"]
    if pd.isna(raw_min) or pd.isna(raw_max):
        raise ValueError(
            f"翌営業日が算出できません（min_pd={sorted_pds[0]}, max_pd={sorted_pds[-1]}）。"
            "株価データが最新日まで投入されているか確認してください"
        )
    min_actual = str(raw_min).replace("-", "")[:8]
    max_actual = str(raw_max).replace("-", "")[:8]
```

---

## 【確認できなかった事項】

- PS1 ファイルは git 管理外（Dropbox 配置）のため、diff 取得ができず修正前の状態を目視確認できなかった。ユーザー申告の修正内容（プロンプト文言・説明文の変更）に基づいてレビューした
- `_resolve_backfill_range` の新 BQ クエリが期待通りの翌営業日を返すかは、実データ（STOCK_PRICE_JQUANTS テーブル）に依存するため、脳内シミュレーションでは祝日パターンまでの網羅的検証は不可能。ただし `DATE_ADD(..., INTERVAL 5 DAY)` のマージンは土日+祝日3連休（最大5営業日ギャップ）に対して十分
- `_get_business_day_pairs` のループで `dates` リストの先頭要素が `date_from_h` と一致する場合、`j = i - 1 = -1` から逆方向探索を開始するが、`range(i - 1, -1, -1)` で `i = 0` のとき `range(-1, -1, -1)` は空なので break に到達せず、ペアが生成されないだけ。これは正しい動作（先頭の actual_date に対応する前営業日が取得範囲に入っていない場合）だが、`DATE_SUB(..., INTERVAL 5 DAY)` のマージンにより通常は前営業日が dates に含まれるはず。ただし年末年始の長期休暇（6営業日以上のギャップ）では先頭ペアが欠落する可能性がある。日本市場の最大連続非営業日は通常5日（12/31-1/3 + 土日）なので INTERVAL 5 DAY で十分だが、完全な安全マージンを取るなら INTERVAL 10 DAY が望ましい
