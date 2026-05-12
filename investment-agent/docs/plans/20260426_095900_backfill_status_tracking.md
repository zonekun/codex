# バックフィル進捗追跡の構造的改善

**作成日時**: 2026-04-26 09:59 JST
**対象ファイル**: `scripts/monitor_backfill.py`、`docs/plans/20260425_001000_tdnet_2024_gap_backfill.md`
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: バックフィルの実際の完了状態と記録・報告の乖離を構造的に排除する。スコープは (1) 完了状態の自動検証スクリプト追加、(2) プランMD の運用ルール追加。
**ステータス**: ✅ 実装完了（2026-04-26 10:25 JST）— レビューC評価を受け構造的対案で再実装。`scripts/check_backfill_status.py` 新設 + `monitor_backfill.py` の完了/キャンセル通知にBQ集計を自動埋め込み。

---

## 前提サマリ

- 過去修正: なし（新規の運用改善）
- 残存: 本プランで 2 件対処
- 実機検証: prod で事象発生済み
- 関連 incident: 2026-04-25〜26 Q4 誤投入→完了未検知→誤報告

---

## 事象の詳細

### タイムライン（2026-04-25〜26）

1. **04-25 夕方**: 2024 Run2 H2 の Load (L2) は完了済み（20240701〜20241231 の gap docs を BQ に pending で投入済み）
2. **04-25 夜**: Q3 AI処理を投入するつもりが、**誤って Q4（20241001〜20241231）を投入**（Workflow `0461983e`）
3. **04-25 夜**: Q4 Workflow が `run_gemma_and_wait` で時間がかかっていると報告 → ユーザーがキャンセル指示
4. **04-25 夜**: Q4 Workflow のキャンセルを実行。この時点で「Q4 は未処理」と認識
5. **04-26 朝**: Q3 を正しく投入 → サロゲート修正有効で成功
6. **04-26 09:50**: Q3 完了報告時に「次は Q4 投入か 2025 に進むか」と報告 → **Q4 は既に全件 completed だった**

### 何が起きていたか

Q4 誤投入の Workflow `0461983e` は、キャンセル指示の前に **ai-prepare → gemma-runner → ai-finalize の全工程が完走していた**（または途中でキャンセルされたが、ai-finalize が BQ 更新を完了した後だった）。結果として Q4 の全 docs が `completed` になっていたが、以下の理由で検知されなかった:

1. **キャンセル指示後、Workflow の最終状態を確認しなかった**: `gcloud workflows executions describe` で state=SUCCEEDED/CANCELLED を確認すべきだったが、キャンセルコマンド実行後に「キャンセル済み」と断定した
2. **BQ の実データで状態を確認しなかった**: `SELECT AI_STATUS, COUNT(*) ... WHERE SUBMISSION_DATE BETWEEN '2024-10-01' AND '2024-12-31'` の 1 クエリで判明する事実を、プラン MD の文面（「Q4 未実行」）に依存して推測した
3. **プラン MD のステータスを更新しなかった**: 事実上 Q4 が完了したにもかかわらず、プランの「Run2 H2 実行中」を更新せず放置した

### 影響

- ユーザーに誤った進捗報告をした（「Q4 投入しますか？」← 既に完了済み）
- ユーザーの時間を無駄にした（確認のやり取りが発生）
- 信頼性の毀損

---

## 優先度の定義

- **P0**: 同種の誤報告を構造的に防ぐ
- **P1**: プラン MD 運用の改善

---

## 指摘項目

### P0-1. バックフィル完了状態の検証が手動・記憶依存 🚨

**症状**: バックフィルの各 Q/H の完了状態がプラン MD の手動更新に依存しており、実際の BQ 状態と乖離する。報告時に BQ を確認するルールはあるが（CLAUDE.md「推測で発言してから確認するな」）、ルールの存在は見落とし防止にならなかった。

**該当**: 運用プロセス全体（特定コード行なし）

**根本原因**: 「BQ を確認してから報告する」は行動ルールであり、忘れれば機能しない。Cloud Build の `jobs update` 忘れと同じ構造 — **手動ステップを人間の記憶に依存させている**。

**修正方針**: バックフィル完了状態を BQ から自動取得するスクリプトを作成し、進捗報告の前に必ず実行する。

```python
# scripts/check_backfill_status.py
# 引数: --year 2024 (or 2025)
# 動作: Q1-Q4 の AI_STATUS 集計を BQ から取得し、
#        completed / pending / pending_gemma の件数を表示
# 出力例:
#   === 2024 Backfill Status ===
#   Q1 (Jan-Mar): 245,678 completed, 0 pending, 0 pending_gemma ✅
#   Q2 (Apr-Jun): 301,234 completed, 0 pending, 0 pending_gemma ✅
#   Q3 (Jul-Sep): 303,138 completed, 0 pending, 0 pending_gemma ✅
#   Q4 (Oct-Dec): 328,863 completed, 0 pending, 0 pending_gemma ✅
```

**呼び出し側への波及**:
- CLAUDE.md に「バックフィル進捗報告の前に `check_backfill_status.py` を実行」ルールを追加
- `docs/knowledges/tools/013_tdnet_load.md` のバックフィルセクションに参照追加

**検証**: スクリプト実行で 2024/2025 の正しい状態が表示されること

**ロールバック**: スクリプト削除で済む。影響なし

---

### P1-1. プラン MD のステータス更新が忘れられる ⚠️

**症状**: バックフィルの各ステップ完了時にプラン MD のステータスを手動更新する運用だが、作業の興奮・次タスクへの移行・セッション切断等で忘れられる。

**該当**: `docs/plans/20260425_001000_tdnet_2024_gap_backfill.md` のステータス行

**根本原因**: ステータス更新が「完了報告」と別の手動アクションになっている。

**修正方針**: `monitor_backfill.py` の完了時処理にプラン MD の自動更新を追加するのが理想だが、YAML の backfill config にプランファイルパスを持たせる必要があり、過剰。代わりに以下を CLAUDE.md の「監視する／見張る」セクションに追加:

> **バックフィル完了時の義務**: Workflow が SUCCEEDED/FAILED になったら、(1) BQ で AI_STATUS 集計を確認、(2) プラン MD のステータスを更新、(3) LINE 通知に実データ（BQ 集計結果）を含める。プラン MD の更新を忘れた場合でも、BQ が真実であることを常に確認する。

**呼び出し側への波及**: CLAUDE.md 更新

**検証**: 次回バックフィル完了時に遵守されることを確認

**ロールバック**: 不要

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P1-1 | — | — | — |

> 既存アンチパターン分類には直接該当なし。「手動プロセスの記憶依存」はコーディングではなく運用の問題。

---

## 検証戦略

1. **smoke test**: `check_backfill_status.py --year 2024` で 4Q すべて completed と表示されること
2. **dev 実機**: 2025年の状態（pending あり）が正しく表示されること
3. **本番適用判断基準**: 次回バックフィル報告時に本スクリプトを実行し、BQ 実データに基づく報告ができること
4. **回収手順**: スクリプト削除で済む。データ影響なし

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/013_tdnet_load.md`
- バックフィルプラン: `docs/plans/20260425_001000_tdnet_2024_gap_backfill.md`
- バックフィルプラン: `docs/plans/20260425_091000_tdnet_2025_gap_backfill.md`
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット

---

## レビュー追記: 2026-04-26 10:30 JST — code-reviewer

- 日時: 2026-04-26 10:30 JST
- 対象: `docs/plans/20260426_095900_backfill_status_tracking.md`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)

---

### 【サマリー】

- 変更の要約: バックフィル完了状態の報告が BQ 実データではなくプラン MD の手動記述に依存していたことで誤報告が発生した問題に対し、BQ 集計スクリプト `check_backfill_status.py` の新設と CLAUDE.md への運用ルール追加を提案。
- 品質評価: **C** — 問題の分析は的確だが、提案された対策が「手動ルールの上に手動ルールを重ねる」構造から脱却できていない。ユーザーの懸念（"小手先"）は正当。
- 主要リスク:
  1. P0-1 の `check_backfill_status.py` は**新しい手動ステップ**であり、忘れられる可能性は既存ルール（「BQ を確認してから報告する」）と同等
  2. P1-1 は CLAUDE.md にルール追記するだけであり、`jobs update` 忘れの二の舞になる構造的同型性がある
  3. 真のフィックス（`monitor_backfill.py` の完了通知に BQ 集計を組み込む）が「過剰」として見送られているが、実はこちらのほうが簡潔で確実

### 【パターン2: 改修プラン評価】

#### 妥当性

プランの問題分析（タイムライン、3つの検知失敗原因）は正確。しかし**提案された対策が問題の根本原因に対処していない**。

根本原因は「**完了報告のワークフローに BQ 検証が組み込まれていない**」ことであり、提案は：

- P0-1: `check_backfill_status.py` を作って「報告前に実行するルール」を追加 → **ルールを忘れたから起きた問題に対し、新しいルールを追加している**
- P1-1: CLAUDE.md にルールを追加 → **既に CLAUDE.md に「推測で発言してから確認するな」があったのに機能しなかったのと同じ構造**

プラン自身が P0-1 の「根本原因」で「手動ステップを人間の記憶に依存させている」と正しく診断しておきながら、修正方針で同じ誤りを繰り返している。スクリプトを作ること自体は有用だが、「忘れずに実行する」保証がないため、対症療法に留まる。

**構造的な対案**: `monitor_backfill.py` の完了通知（L359-363）に BQ 集計結果を埋め込む。これは既存の完了フローに**自動的に組み込まれる**ため、忘れようがない。具体的には：

1. `monitor_backfill.py` の `main()` 末尾（L359 の `notify` 呼び出し直前）で、YAML config の `workflows[].data.date_from` / `date_to` を使って BQ の `AI_STATUS` を集計する
2. 集計結果（completed/pending/pending_gemma の件数）を LINE 通知メッセージに含める
3. pending > 0 なら通知 priority を `high` に昇格させる

プランが「YAML にプランファイルパスを持たせる必要があり過剰」と判断したのは P1-1（プラン MD 自動更新）についてであり、BQ 集計の自動実行とは別の話。BQ 集計に必要なのは日付範囲だけで、それは YAML config の `workflows[].data` に既にある。追加の設定は不要。

#### 副作用・デグレードチェック

- [ ] **BQ クエリコスト**: monitor_backfill 完了時に 1 クエリ追加。TDNET_DOCUMENTS_ENHANCED は SUBMISSION_DATE で partition されており、3ヶ月範囲なら数 MB スキャン。コストは無視可能
- [ ] **monitor_backfill.py への BQ 依存追加**: 現在 `monitor_backfill.py` は `gcloud` CLI のみに依存し、BQ クライアントを使っていない。BQ 集計を組み込む場合、`google-cloud-bigquery` の import が追加される。ただし `scripts/` 内の他スクリプトは全て BQ クライアントを使っており、venv には既にインストール済み。影響は軽微
- [ ] **通知メッセージ長**: LINE (ntfy) の body に集計結果を追加すると数行増える。ntfy のメッセージ上限は十分（数 KB）で問題なし

#### 抜け漏れ（類似観点での横展開含む）

- [ ] **キャンセル後の状態検証**: 今回の事故の直接原因は「Workflow キャンセルコマンド実行 = 処理されていない」という誤認。`monitor_backfill.py` は現在 Workflow の `CANCELLED` を失敗扱いするが（L201, L347-357）、キャンセルは `monitor_backfill.py` の外から `gcloud workflows executions cancel` で行われるため、monitor 側では検知しない。これは monitor の責務外だが、**キャンセル後に BQ 状態を確認する手順**が必要。構造的対策としては、`check_backfill_status.py`（P0-1 提案のスクリプト）をキャンセル直後にも実行する運用になるが、これもまた手動ステップ
- [ ] **2025年バックフィルプラン (`docs/plans/20260425_091000_tdnet_2025_gap_backfill.md`) にも同じ問題がある**: プランのステータス欄が陳腐化するリスクは 2024 プラン固有ではない
- [ ] **`check_backfill_status.py` の引数設計**: プランでは `--year` だけだが、実際のバックフィルは Q 単位で実行される。Q 単位の集計（`--year 2024 --quarter Q3`）も必要。さもなければ「2024年全体で pending=0」を見て安心し、個別 Q の内訳を確認しないリスクがある（ただし monitor_backfill 組み込みなら YAML の date 範囲で自動的に正しい範囲になる）

#### 新規リスク

- `check_backfill_status.py` を独立スクリプトとして作る場合、BQ 認証の設定（`settings.google_application_credentials`）やプロジェクト ID のハードコードなど、既存の `src/core/config.py` パターンとの整合性を取る必要がある。些細だが、スクリプトが増えるたびにメンテ負荷が上がる
- CLAUDE.md に「バックフィル報告前に実行」ルールを追加すると、CLAUDE.md のルール量がさらに増加し、コンテキスト圧縮時に落ちるリスクが高まる

### 【重大な指摘】（即修正）

#### #1 対策が問題と同型（手動ルール追加で手動ルール忘れを防ごうとしている）

- 箇所: P0-1 修正方針、P1-1 修正方針
- 事象: プランが診断した根本原因（「手動ステップを人間の記憶に依存させている」）と、提案された対策（「スクリプトを報告前に手動実行する」「CLAUDE.md にルールを追加する」）が構造的に同型。`jobs update` 忘れが既に起きたプロジェクトで、同じ対策パターンを繰り返している
- トリガー: 次回バックフィル完了時に、オペレーター（Claude）が `check_backfill_status.py` の存在を忘れる、または「急いでいるから省略」する
- 影響: 今回と同じ誤報告が再発する
- 根拠: P0-1 の「根本原因」欄が「手動ステップを人間の記憶に依存させている」と明記している。修正方針はその認識に矛盾する
- 推奨対応: `monitor_backfill.py` の完了通知（L359-363）に BQ 集計を自動で含める。YAML config の `workflows[].data` から date 範囲を取得し、BQ で `AI_STATUS` を集計して通知に含める。`check_backfill_status.py` は ad-hoc 確認用として残してもよいが、**主たる防止策にしてはいけない**

#### #2 キャンセル時のフローが未設計

- 箇所: プラン全体（言及なし）
- 事象: 今回の事故の直接トリガーは「キャンセル指示 → キャンセル完了と断定 → BQ 未確認」だが、プランはキャンセル時の確認フローを設計していない。`monitor_backfill.py` はキャンセルを検知するが、外部からの `gcloud workflows executions cancel` は monitor の外で起きる
- トリガー: 次回 Workflow を手動キャンセルした時
- 影響: キャンセルしたはずのジョブが完了していたことに気付かず、同じバッチを二重投入する可能性
- 根拠: 事象の詳細 §3-4 で「キャンセル指示後、Workflow の最終状態を確認しなかった」と記述しているが、対策に反映されていない
- 推奨対応: `monitor_backfill.py` のキャンセル検知時（L347-357 の `CANCELLED` ケース）でも BQ 集計を実行し、「キャンセルされたが BQ 上は N 件 completed」を通知に含める。これにより「キャンセル前に処理が完了していた」ケースを自動検知できる

### 【改善提案】（可読性・保守性）

#### #1 フォーマット違反の修正

- 箇所: プラン全体
- 現状: テンプレート（`_template_refactor.md`）との差異が複数ある
  - P0-1/P1-1 に「該当」フィールドはあるが実コード抜粋（```python:Lxxx-Lyyy```）がない（運用プロセス改善のため該当コード行なしは妥当だが、`monitor_backfill.py:L359-363` の完了通知コードは該当箇所として示せる）
  - 「呼び出し側への波及」が行番号リストではなく散文（CLAUDE.md に追加、013 に参照追加）
  - アンチパターン対応表が全て `---` で、既存パターンへのマッピングが未検討（A-8「exit code だけで gate」は類似する概念であり、「手動 gate の記憶依存」として新パターン候補にできる）
  - 検証戦略の「dev 実機」が実質 smoke test と同じ内容（「2025年の pending あり表示」は smoke の変種であり dev スケール検証ではない）
- 提案: フォーマット違反は重大ではないが、レビュー記録の一貫性のために修正する

#### #2 `check_backfill_status.py` を作るなら monitor_backfill からも呼べる設計に

- 箇所: P0-1 修正方針
- 現状: `check_backfill_status.py` がスタンドアロンスクリプトとして設計されている
- 提案: BQ 集計ロジックを関数（例: `get_backfill_status(date_from, date_to) -> dict`）として `check_backfill_status.py` に実装し、`monitor_backfill.py` から `import` して完了通知に使う。これにより：
  - ad-hoc 実行（`python check_backfill_status.py --year 2024`）も可能
  - monitor_backfill の完了通知に自動組み込みも可能
  - ロジックの重複がない

### 【確認できなかった事項】

- `monitor_backfill.py` が Bash `run_in_background=true` で実行される場合、BQ クライアントの認証が `gcloud` CLI 経由の ADC で通るか、`settings.google_application_credentials` のサービスアカウントキーが必要かは実行環境依存。現在 monitor_backfill は `gcloud` CLI のみ使用しており BQ Python クライアントは未使用のため、組み込み時に認証方式の確認が必要
- 今回のキャンセルが `gcloud workflows executions cancel` で行われたのか、GCP Console から行われたのかは不明。Console からのキャンセルは API レベルでは同じだが、オペレーターの行動パターンとして異なる可能性がある
