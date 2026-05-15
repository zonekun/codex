# コードレビュー: extract_monthly_data.py regex adapter PDF フォールバック修正

- 日時: 2026-05-13 16:30 JST
- 対象: `scripts/extract_monthly_data.py` L3617-3631（未コミット差分）+ コミット `5bcea27`
- パターン: 1 (新規コード変更)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: regex adapter で `_extract_pdf_by_column` が None を返した場合（`if not rec:` 分岐）のフォールバック先を、BQ `full_text`（テーブル構造崩壊テキスト）から `_extract_pdf_text()` によるPDFテキストに変更。先行コミット 5bcea27 で `matched_blob` 欠落時の `continue` ガードも追加済み。
- 品質評価: **A** — 既存の `elif _has_row_regex:` 分岐（L3632-3652）と対称的な構造で一貫性が高く、根本原因（BQテキストではregexが絶対マッチしない）に正しく対処している。重大バグはないが、条件分岐のカバレッジに1点の隙間あり。
- 主要リスク:
  1. `_has_row_regex` が False のアダプターでは依然 BQ `full_text` にフォールバックする（意図的か要確認）
  2. PDF download 失敗時に `_cached_pdf=None` となり、PDFフォールバック不能で BQ テキストに落ちる
  3. 5bcea27 の `not matched_blob` ガード後の `if matched_blob:` が常に True で冗長コード化

## 【重大な指摘】（即修正）

### #1 `_has_row_regex=False` のアダプターで BQ フォールバックが残存

- 箇所: `scripts/extract_monthly_data.py:3617-3631`
- 事象: `if not rec:` 分岐で PDF テキストへの切替条件が `_has_row_regex and _cached_pdf` であるため、`row_label_regex` を持たないフィールドのみで構成されたアダプターでは `_fallback_text = full_text`（BQ テキスト）のまま `extract_from_tdnet_text` に渡される
- トリガー: `_extract_pdf_by_column` が None を返し、かつアダプターの全フィールドが `row_label_regex` を持たない場合
- 影響: 提出 MD に記載の「BQ テキストでは regex が絶対マッチしない」問題がこのパスでは未解消。ただし `row_label_regex` を持たないフィールドは旧来の `regex` + `group` 形式であり、こちらは BQ テキスト（DOTALL マッチ）で動作する可能性がある。設計意図として BQ テキストでも動く旧形式フィールドを意図的に残しているなら問題なし
- 根拠: L3619 の条件 `_has_row_regex and _cached_pdf` は `_has_row_regex=False` のとき False。L3618 `_fallback_text = full_text` のまま L3629 に到達
- 推奨対応: **[方向性]** 意図的な設計判断であれば、コメントで「旧形式 regex フィールドのみのアダプターは BQ テキストで動作するため full_text を使用」と明記する。もし全アダプターで PDF テキスト優先にすべきなら、条件を `_cached_pdf` のみに変更する

## 【改善提案】（可読性・保守性）

### #1 5bcea27 ガード後の `if matched_blob:` が Dead Guard 化

- 箇所: `scripts/extract_monthly_data.py:3576-3582`
- 現状: L3576-3581 で `if not matched_blob: continue` ガードが挿入されたため、L3582 の `if matched_blob:` は**常に True**。ネストが1段深くなるだけで論理的な意味がない
- 提案: L3582 の `if matched_blob:` を除去し、インデントを1段下げてフラット化する。現状でもバグではないが、将来の改修者が「matched_blob が None のケースもあるのか」と誤解する余地がある

### #2 PDF download 失敗時のフォールバック挙動のコメント不足

- 箇所: `scripts/extract_monthly_data.py:3583-3598`
- 現状: `matched_blob.download_as_bytes()` が例外を投げた場合、`_cached_pdf` は `None` のまま。この場合 L3600 `if not rec and _cached_pdf:` で単月フォールバックもスキップされ、L3619 `_has_row_regex and _cached_pdf` も False となり、BQ テキストにフォールバックする。この挙動が意図的かどうかコードから読み取れない
- 提案: L3598 の except ブロック直後に「PDF DL 失敗時は BQ テキストフォールバックに委ねる」旨のコメントを1行追加する

### #3 `_extract_pdf_text` の二重呼び出し

- 箇所: `scripts/extract_monthly_data.py:3621` と `scripts/extract_monthly_data.py:3635`
- 現状: `if not rec:` パスと `elif _has_row_regex:` パスは排他的なので実行時に二重呼び出しにはならないが、将来 `if not rec:` の条件が変更されて `rec` が部分的に埋まったケース（空の fields dict 等）で `_extract_pdf_text` が2回呼ばれる可能性がある。現時点では問題にならない
- 提案: 現状維持で可。ただし `_extract_pdf_text` は pdfplumber/PyMuPDF を毎回 import するため、頻繁に呼ばれるようになったらキャッシュ化を検討

## 【確認できなかった事項】

- `row_label_regex` を持たない regex アダプターが実際に存在するか、またその場合に BQ テキストで `extract_from_tdnet_text` がマッチ可能かどうか（アダプター JSON の全件精査が必要）
- `_extract_pdf_text` が返すテキストの品質が `extract_from_tdnet_text` の旧形式 `regex`+`group` フィールドでも十分マッチするかどうか（テキストフォーマットの違い）
- 042-1 E5-1（L405-412）の「参照ソース: L3611（BQフォールバック分岐）」が今回の修正で行番号がずれている点の整合性（知見 MD 更新の要否）
