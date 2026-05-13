# 端末間引き継ぎボード

別端末のClaude Codeに作業を引き継ぐためのファイル。
git push/pull で同期される。

## ルール

- `from` / `to` は Claude Code 端末（Windows / Linux VM）に限定。Codex 宛の伝言は禁止
- Codex との引き継ぎは `docs/knowledges/tools/083_codex_collaboration.md` を参照
- エントリは新しい順（上が最新）
- 受け取り側が完了したら `status` を `done` に変更し、結果を追記
- 不要になったエントリは削除してよい

---

### 2026-05-13: 決算反応モデル 反省会改善フォローアップ（2件）

- **from**: Windows
- **to**: 次セッション
- **status**: pending
- **優先度**: 高

#### 前提

反省会20260512フィードバックに基づく改善をコミット済み（`aff444f`）。プランMD: `docs/plans/tools-059_earnings_model_eda_20260513_165133.md`（完了、backfill未実施）。レビュー: `docs/reviews/167_cr_earnings_median5y_f5_lowprofit.md`（品質A）。

#### 引継ぎ①: F5a+F5bスコア上限+4/-4の過大評価問題 → **設計完了・Codex実装待ち**

**完了した作業**:
- 問題分析: F5だけでなく、来期見通し系(F5a+F5b+F7+F12(FY))で最大+7、当期業績系(F3+F12(非FY)+F15)で最大+5の二重・三重加算を特定
- プランMD作成: `docs/plans/tools-059_group_cap_20260513_201511.md`
- レビュー実施: `docs/reviews/169_cr_group_cap.md`（品質A、重大指摘2件採用済み）
- 知見MD更新: `059_earnings_model_eda.md` スコアリング因子テーブルにグループ帰属・F5a±1縮小・キャップ注記を反映
- Codex伝言板に実装依頼記載済み

**残**: Codexによる `earnings_model_core.py` 実装 → backfill検証

#### 引継ぎ②: 5/12反省会ログの深堀検討

**内容**: 059知見MDの反省会ログ 2026-05-13（5/12発表分）に追記した7件について、別途深堀検討をユーザーが指示する。詳細は新セッションでユーザーが直接伝える。

**参照**:
- `docs/knowledges/tools/059_earnings_model_eda.md` L549-568（5/12反省会ログ）
- フィードバック JSONL: `C:\tmp\earnings_review\claude_feedback_20260512.jsonl`

---

### 2026-05-13: 6040/2736 月次regex化 残作業

- **from**: Windows
- **to**: 次セッション
- **status**: pending
- **優先度**: 高

#### 完了済み

1. **2736 フェスタリアHD**: regex adapter作成・GCSアップロード・Cloud Run再抽出・BC突合14/14全一致 → **完了**
2. **6040 日本スキー場開発**: regex adapter作成（`C:\tmp\6040_extract_adapter.json`）・GCSアップロード済み・ローカルテストで全値一致確認済み

#### 6040 未完了の根本原因

`extract_monthly_data.py` のregex抽出パスはPDFの `table_to_lines` テキスト（~6K文字、行構造あり）を前提としているが、**6040にはGCS上にPDFが存在しない**（`monthly/download/6040/` が空）。

- PDFが無い理由: 6040には `url_adapter.json` が無く、`download_monthly.py` のダウンロード対象外（ダウンロードジョブは `url_adapter.json` を読む）
- PDFが無い場合、コードは `full_text`（BQ CHUNK_TEXT の STRING_AGG、チャンク順不同・テーブル構造崩壊）にサイレントフォールバック → regexが絶対マッチしない
- デバッグログで確認済み: `path=fallback(no rec) full_text_len=5994`

#### 残タスク（優先順）

1. **6040の `url_adapter.json` 作成 → GCSアップロード** — 他銘柄（TDnet follow_links型）を参考に作成。TDnet月次速報ページのURLパターン設定
2. **`download-monthly` ジョブで6040 PDFダウンロード** — `--tickers 6040` で実行
3. **`extract-monthly-data` ジョブ再実行** — PDFがGCSに入ればtable_to_linesパスに入り、regexマッチするはず（ローカル検証済み）
4. **BC突合** — WN計来場者の値がBC値と一致するか確認
5. **extractコード修正** — L3611 `if not rec:` の分岐修正: `_cached_pdf` があり `_has_row_regex` なら BQ full_text ではなく `_extract_pdf_text(_cached_pdf)` で試す。現状のwarning+continue（matched_blob欠落時）は残すが、本質はこちら。修正後Docker再ビルド・デプロイ

#### 参照ファイル

- extract adapter: `gs://stock_data_1930932/monthly/meta/6040/extract_adapter.json`（アップロード済み）
- ローカルコピー: `C:\tmp\6040_extract_adapter.json`
- 2736 adapter: `C:\tmp\2736_extract_adapter.json`（参考、完了済み）
- プランMD: `docs/plans/tools-042_monthly_bc_ng13_20260512_180300.md`
- 親知見: `docs/knowledges/tools/042_monthly_disclosure_master.md`

---

### 2026-05-10: BQ知見MD追記（2件）

- **from**: Windows
- **to**: 次セッション
- **status**: pending
- **優先度**: 中

#### 内容

1. `data_catalog.md` BQセクション冒頭に「プロジェクト使い分け」注意書き追加（BQ=gmailpj-357912 / GCS=and-and-and）
2. `docs/knowledges/api/002_bigquery.md` にMCPツール経由セクション＋「よくあるエラー」に403 jobs.create事例追加

#### 追記内容（コピペ用）

**#1 data_catalog.md** L16直後:
```
> **プロジェクト使い分け**: BQジョブ実行=`gmailpj-357912` / GCSバケット=`and-and-and`。BQに`and-and-and`を使うと403。
```

**#3 002_bigquery.md** 認証セクション末尾:
```
### MCP ツール (`mcp__gcp__bq_query`)
- プロジェクト指定不要（MCP設定で`gmailpj-357912`設定済み）
- テーブル参照は `gmailpj-357912.STOCK.TABLE_NAME` フルパス

### よくあるエラー追記
| 403 bigquery.jobs.create | project=`and-and-and`を指定した | BQジョブは`gmailpj-357912`。`and-and-and`はGCS専用 |
```

---

### 2026-05-09: CLAUDE.md再発防止アーキテクチャ転換のプラン化

- **from**: Windows（本セッション）
- **to**: 次セッション（端末問わず）
- **status**: pending
- **優先度**: 高

#### 背景

MR-071/131/134/136/139の5件が同根パターン「AIの判断がユーザー指示/ドキュメントを上書きする」で発生。現在の対応は「事故ごとにCLAUDE.mdに表層特化ルールを追加」するモデルだが、4件のルール追加後も5件目が発生し、このモデルの限界が実証された。

#### 何をプラン化すべきか

**ルール増殖モデル → パターン認識モデルへの転換**。structure-optimizer（`docs/reviews/138_so_ai_override_pattern_structural.md`）が分析済み。

現状の問題:
```
事故発生 → 表層特化ルール追加 → 次の変種発生 → 新ルール追加 → ...
```
このモデルはO(n)でCLAUDE.mdが肥大化し、かつ次の変種を事前に防げない。各ルールは特定のトリガー語彙（「そのまま移植」「手動で」「A+Bで」「動いていない」）に反応する設計であり、トリガー語彙が異なる次の変種には発火しない。

#### 現時点で実施済みのこと

1. **SR-1**: CLAUDE.md §指示の字義優先をメタルール化（抽象的な適用判定基準を追加）
2. **RD-1の一部**: `004_coding_conventions.md` に§事故パターンDB P-001を新設（パターン定義+判定フロー+事故事例テーブル4件）
3. CLAUDE.md §指示の字義優先からパターンDBへのポインタ追加

#### プラン化で検討すべきこと

- RD-1の本質的な部分: 「5件目以降はCLAUDE.mdにルール追加せず、パターンDBに事例追記のみ」を実際に運用できる制度設計
- P-001以外のパターン候補の洗い出し（004-1蓄積ログからの抽出）
- 既存の個別ルール群（§既存コード移植ルール、§AI直接処理の指示ルール等）の扱い — 残すか、パターンDBに統合するか
- CLAUDE.md肥大化の定量評価と削減目標

#### 参照すべきファイル

- `docs/reviews/138_so_ai_override_pattern_structural.md` — structure-optimizerの全分析（SR-1/SR-2/RD-1の提案詳細）
- `docs/reviews/134_mr_web_research_skip.md` — 系譜テーブル・再発防止策
- `docs/reviews/136_mr_ntfy_wait_flag_miss.md` — MR-136事故詳細
- `docs/reviews/139_mr_unauthorized_qf_execution.md` — MR-139事故（P-001の5件目）
- ~~`docs/knowledges/tools/004_coding_conventions.md` §事故パターンDB~~ — 廃止済み（2026-05-13: 防止効果が実証されず削除。事故記録はreviews/MR + 004-1蓄積ログに一本化）
- `CLAUDE.md` L340 §指示の字義優先（メタルール） — SR-1実施済みの状態
