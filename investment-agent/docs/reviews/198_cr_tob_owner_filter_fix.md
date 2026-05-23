# CR-198: TOB MLモデル 候補CSV生成スクリプト新規作成（アクティビスト・上場事業法人除外）

**レビュー日**: 2026-05-17
**対象プランMD**: `docs/plans/analysis-007_tob_owner_filter_fix_20260517_175057.md`
**パターン**: 4（新規開発計画レビュー）
**レビュアー**: code-reviewer サブエージェント

---

## 総評

**判定: B（軽微な指摘あり。即着手可。指摘を踏まえて実装すること）**

計画の目的・スコープは明確。既存コード（`compute_owner_features.py`）への影響なし宣言、新規スクリプト限定という設計判断は適切。BQ汚染防止という P0 優先度付けも妥当。7フィールドはほぼ揃っている。以下に要注意点と軽微指摘を記す。

---

## A. 技術選定の妥当性

### A-1. アクティビスト名の照合方式（ゆるい一致 vs 完全一致）

**指摘レベル**: 軽微（実装前に判断を明示すること）

プランコード例は `df["株主名"].isin(activist_names)` による**完全一致**を採用している。`activist_aliases.csv` の ALIAS 列は確認した限り `UH Partners２`（全角数字）など表記揺れを含む可能性がある。BQ 上の株主名も全角/半角が混在しうるため、**完全一致で取りこぼしが出るリスク**がある。

現行 `classify_shareholder_names.py` は `_normalize_fullwidth()` で全角 Latin 文字を ASCII 変換してから照合している。同等の正規化を `load_activist_names()` に組み込むか、または取りこぼし許容の判断を明示すること。

### A-2. 上場事業法人除外の法人格プレフィックスリスト

**指摘レベル**: 軽微

`load_listed_company_names()` の prefix リストが `("株式会社", "㈱", "（株）", "(株)")` の4種のみ。`classify_shareholder_names.py` には `(株）`（混在ブラケット）・`（株)`（同）・`㈲`（有限会社略） 等も定義されている。プレフィックス除去後の照合で、同スクリプトのリストと合わせることを推奨する（根本原因の `classify_shareholder_names.py` との整合性確保）。

### A-3. BQ SQL の JOIN 冗長性

**指摘レベル**: 情報提供のみ

P1-1 の BQ SQL 骨格で `private_entries` に `LEFT JOIN STOCK_CODE_LIST` を行い `issuer_name` を取得しているが、`SELECT` の最終行で再度 `LEFT JOIN STOCK_CODE_LIST scl ON scl.TICKER = pe.TICKER` を実行している。`issuer_name` が `private_entries` 内と最終 SELECT で二重取得になっている。実行コストは軽微だが、実装時に整理すること。

---

## B. 既存システムとの統合

### B-1. `compute_owner_features.py` の OWNER_COUNT_IN_TOP10 への影響

**指摘レベル**: 確認事項

プランは「呼び出し側への波及なし」と明示しているが、1点確認を要する。`compute_owner_features.py` の `owner_agg` CTE では `entry_type IN ('INDIVIDUAL', 'ASSET_MGMT')` でカウントしている。`SHAREHOLDER_COMPOSITION_EXTEND` に現在 `PRIVATE_CORP` として登録されている上場事業法人（キーエンス等）は、`compute_owner_features.py` の集計には現在 **OWNER_COUNT に未算入**（PRIVATE_CORP は対象外）のため、本プランの暫定除外が OWNER_COUNT を変化させることはない。この点は想定通りだが、念のため実装後の smoke test で確認すること。

### B-2. `SHAREHOLDER_COMPOSITION_EXTEND.TYPE = 'PRIVATE_CORP'` の絞り込み

**指摘レベル**: 情報提供のみ

P1-1 の SQL で `INNER JOIN SHAREHOLDER_COMPOSITION_EXTEND SCE ON SCE.NAME = ... AND SCE.TYPE = 'PRIVATE_CORP'` としている。`SHAREHOLDER_COMPOSITION_EXTEND` に未登録（UNCLASSIFIED 相当）の株主名は候補から漏れる。これは意図通り（登録済み PRIVATE_CORP のみを対象）と理解しているが、プランに明示がないため、実装コメントに記載すること。

---

## C. リスク・抜け漏れ

### C-1. 除外後の候補数ゼロケースの未処置

**指摘レベル**: 軽微

アクティビスト除外・上場事業法人除外の両方を適用した結果、候補が0件になった場合の挙動がプランに記載されていない。新規スクリプトであり BQ 更新もないため致命的ではないが、`--mode full` 時の出力 CSV が空になった場合に無言で終了するとデバッグが困難。`logger.warning("no_candidates_after_filter", count=0)` 程度の出力を実装要件に加えること。

### C-2. CSV 出力先のパス管理

**指摘レベル**: 情報提供のみ

出力先が `C:\tmp\tob_prediction\` のハードコードになっている。`compute_owner_features.py` の `FAMOUS_CSV = Path(r"C:\tmp\tob_prediction\famous_investors.csv")` と同一ディレクトリで統一されており問題はないが、Cloud Run 移行時には変更が必要になる。現在はローカル専用スクリプトのため許容範囲。

### C-3. `--mode dry-run` の定義とプランの干渉

**指摘レベル**: 軽微

P1-1 の検証手順では `--mode dry-run` で「件数確認」のみとしているが、dry-run と full で「除外ログ出力」の有無が明記されていない。P0-1 / P0-2 の検証（除外ログで UH Partners3 / キーエンス が含まれることを確認）が dry-run でも出力されるのか、full 時のみなのかを実装コメントで明記すること。

### C-4. 根本修正（`classify_shareholder_names.py` 改修）との関係

**指摘レベル**: 情報提供のみ

プランは「根本修正は別プランで実施」と明記している。ただし、`classify_shareholder_names.py` が `STOCK_CODE_LIST` 照合を取り込んで `LISTED_CORP` 型を追加した場合、本スクリプトの上場事業法人除外ロジックと二重除外になる。根本修正プラン実施後は本スクリプトの P0-2 ブロックを削除することを `TODO` コメントとして実装時に明記すること。

---

## D. プランMD 書式チェック

| チェック項目 | 判定 | 備考 |
|---|---|---|
| 基準 commit hash 冒頭記載 | OK | `b24e7277` |
| 7フィールド（症状/該当/根本原因/修正方針/波及/検証/ロールバック） | OK | 全項目揃い |
| before/after 対比 | OK | P0-1/P0-2/P1-1 全て有り |
| 呼び出し側波及（行番号）| 条件付きOK | 新規スクリプトのため「無し」は妥当 |
| アンチパターン対応表 | OK | 末尾に記載あり |
| 検証戦略（smoke/dev/本番判断/回収） | OK | 4段揃い |
| ロールバック手順 | OK | CSV のみ・BQ変更なし |
| 提出前セルフチェック | OK | 全項目チェック済み |

---

## E. 推奨対応サマリー

| # | 指摘ID | 内容 | 推奨対応 | 優先度 |
|---|--------|------|----------|--------|
| 1 | A-1 | activist照合: 全角/半角正規化なし | `load_activist_names()` に `_normalize_fullwidth()` 相当処理追加 または取りこぼし許容を明示 | 推奨 |
| 2 | A-2 | 法人格プレフィックスが4種のみ | `classify_shareholder_names.py` の `CORP_SUFFIXES_JP` と照合・拡充 | 推奨 |
| 3 | A-3 | BQ SQL 二重JOIN | 実装時にSQLを整理 | 任意 |
| 4 | B-2 | UNCLASSIFIED株主名が候補から漏れる | 実装コメントで意図を明記 | 推奨 |
| 5 | C-1 | 候補0件時の無言終了 | `logger.warning` 追加 | 推奨 |
| 6 | C-3 | dry-run時の除外ログ出力有無が未定義 | 実装コメントで明記 | 推奨 |
| 7 | C-4 | 根本修正後の二重除外 | 実装時に `TODO` コメント追記 | 任意 |

---

## F. 確認できなかった事項

- `SHAREHOLDER_COMPOSITION_EXTEND` テーブルの実際の行数・TYPE 分布（BQ 直接確認不可）
- `activist_aliases.csv` の全315件における表記揺れ傾向（先頭5行のみ確認）
- `data/master/activists.csv`（39件マスタ）と `activist_aliases.csv`（315件エイリアス）の参照優先順位（プランは `activist_aliases.csv` のみ参照しており `activists.csv` の扱いが不明）
