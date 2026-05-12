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
- `docs/knowledges/tools/004_coding_conventions.md` §事故パターンDB — 現在のP-001定義
- `CLAUDE.md` L340 §指示の字義優先（メタルール） — SR-1実施済みの状態
