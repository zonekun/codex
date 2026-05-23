# 決算特別シフト MD自動更新 + ドキュメント整備

**作成日時**: 2026-05-15 22:29 JST
**ステータス**: 進行中
**対象ファイル**: `scripts/tdnet_shift_md_updater.py`（新規, 78行）、`workflows/tdnet_schedule_revert.yaml`（改修）、`docs/knowledges/tools/013_tdnet_load.md`、`docs/knowledges/tools/005_cloudrun_job_deploy.md`
**基準 commit**: `0e55e5f`
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 決算特別シフト復帰時に 013 MD タイトルの【】マーカーを自動除去する仕組みを構築し、年4回の特別シフト運用で手動 MD 更新漏れを防止する。ドキュメント整備も含む。

> **分類**: (b) 継続改修型
> **親知見 MD**: `docs/knowledges/tools/013_tdnet_load.md`

---

## 前提サマリ

- 決算特別シフトは年4回（2月/5月/8月/11月）、有効化→自動復帰のサイクルで運用
- 復帰は `tdnet-schedule-revert` Workflow + one-shot Cloud Scheduler で自動化済み
- しかし 013 MD タイトルの【決算特別シフトON M/DD〜M/DD】除去は手動だった
- 今回の改修で特別シフトON時に開始日・終了日を記載する義務を追加（開始日不明で復旧ジョブの期間特定不可だったため）
- 実機検証: Cloud Run Job テスト実行済み（`no_shift_marker_found` 正常終了確認）

---

## 優先度の定義

- **P0**: revert Workflow に MD 自動更新を組み込む（コード・Workflow・デプロイ）
- **P1**: ドキュメント整備（013 MD の手順更新・005 ジョブ一覧追加・依存関係記載）
- **P2**: なし

---

## 指摘項目

### P0-1. tdnet-shift-md-updater Cloud Run Job の新規作成 ✅

**症状**: 復帰時に 013 MD の【】マーカーが残り続け、次のセッションがシフト中と誤認する

**該当**: 新規ファイル `scripts/tdnet_shift_md_updater.py`

```python
SHIFT_PATTERN = re.compile(r"【[^】]*決算特別シフト[^】]*】")
```

**根本原因**: revert Workflow がスケジューラ状態変更のみで MD を触らない設計だった

**修正方針**:
- Secret Manager `github-pat` → GitHub Contents API (GET → regex置換 → PUT) でファイル更新+コミット
- 冪等: 【】が無ければ何もしない（exit 0）
- BOM対策: `decode("utf-8-sig").strip()` でトークン読み取り

**呼び出し側への波及**:
- `workflows/tdnet_schedule_revert.yaml` — Step 4 として `update_shift_md` を追加（P0-2で対応）

**検証**: Cloud Run Job 単体実行で `no_shift_marker_found` ログ確認済み（GitHub上に【】が無い状態で冪等性確認）

**ロールバック**: Cloud Run Job 削除 + Workflow から Step 4 を除去。GitHub上のコミットは手動 revert

**デプロイ済みリソース**:

| リソース | 種別 | 状態 |
|---------|------|------|
| `github-pat` | Secret Manager | v2 作成済み（v1はBOM混入で無効） |
| `tdnet-shift-md-updater` | Cloud Run Job (us-west1) | 作成済み、テスト成功 |
| `us-west1-docker.pkg.dev/gmailpj-357912/tools/tdnet-shift-md-updater:latest` | Artifact Registry | push済み |

---

### P0-2. revert Workflow への Step 4 追加 ✅

**症状**: Workflow が MD 更新ステップを持たない

**該当**: `workflows/tdnet_schedule_revert.yaml`

```yaml
# before: done ステップの直前に何もない

# after: Step 4 追加
- update_shift_md:
    call: googleapis.run.v2.projects.locations.jobs.run
    args:
      name: ${"projects/" + project_id + "/locations/" + cloudrun_region + "/jobs/tdnet-shift-md-updater"}
      connector_params:
        timeout: 120
    result: md_update_result
```

**根本原因**: 初期設計時に MD 更新の自動化が要件に含まれていなかった

**修正方針**: init に `cloudrun_region: "us-west1"` 追加、done の return に `md_updated: true` 追加

**呼び出し側への波及**: 無し（Workflow は Scheduler から呼ばれるのみ）

**検証**: Workflow デプロイ済み（revision `000004-6b6`、CR-184 指摘対応適用済み）。5/18 00:00 JST の自動発火で E2E 検証予定

**ロールバック**: 旧 revision に戻す（`gcloud workflows deploy --source=<旧yaml>`）

---

### P1-1. 013 MD 決算特別シフトセクション更新 ✅

**症状**: 以下が未記載:
1. 有効化手順 Step 4 のタイトル形式が `M/DD〜M/DD`（開始日・終了日両方必須）に変更されたが、復帰時の自動除去について未記載
2. 無効化セクションに MD 自動更新の記載がない
3. 関連リソーステーブルに `tdnet-shift-md-updater` が無い
4. `github-pat` Secret の依存が未記載

**該当**: `docs/knowledges/tools/013_tdnet_load.md:59-83`

**修正方針**:
- 無効化セクション: 「013 MD タイトルの【】は `tdnet-shift-md-updater` Cloud Run Job が自動除去」を追記
- 関連リソーステーブルに2行追加（`tdnet-shift-md-updater` / `github-pat`）
- 依存注記: 「GitHub PAT の有効期限切れ時は Secret Manager `github-pat` を更新。Job は冪等なので失敗しても復帰処理は完遂する（MD更新のみスキップ）」

**呼び出し側への波及**: 無し

**検証**: MD を Read して記載内容を確認

**ロールバック**: git revert

---

### P1-2. 005 ジョブ一覧に追加 ✅

**症状**: `tdnet-shift-md-updater` が既存 Job 一覧に無い

**該当**: `docs/knowledges/tools/005_cloudrun_job_deploy.md:353-371`

**修正方針**: テーブルに1行追加

```markdown
| `tdnet-shift-md-updater` | `scripts/tdnet_shift_md_updater.py` | `tools/tdnet-shift-md-updater` | 120s | Workflow `tdnet-schedule-revert` から呼び出し |
```

**呼び出し側への波及**: 無し

**検証**: MD を Read して行が追加されていることを確認

**ロールバック**: git revert

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |
| P0-2 | — | — | — |
| P1-1 | — | — | — |
| P1-2 | — | — | — |

新規機能追加のため、既存アンチパターンへの該当なし。

---

## 検証戦略

1. **smoke test**: `gcloud run jobs execute tdnet-shift-md-updater --region us-west1 --wait` → exit 0 + `no_shift_marker_found` ログ ✅ 完了
2. **dev 実機**: GitHub上のファイルに【テスト】を付けて実行 → 除去されてコミットされることを確認（未実施、5/18の本番発火で代替）
3. **本番適用判断基準**: 5/18 00:00 JST の `tdnet-schedule-revert-20260518` 自動発火で E2E 確認。013 MD タイトルから【】が除去され、GitHub上にコミットが作成されること
4. **回収手順**: Job 失敗時は手動で `gcloud workflows execute tdnet-schedule-revert --location=us-central1` を再実行。MD更新のみ失敗の場合は手動で【】除去+コミット

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/013_tdnet_load.md`（親知見）
- 知見 MD: `docs/knowledges/tools/005_cloudrun_job_deploy.md`（ジョブ一覧）
- 知見 MD: `docs/knowledges/tools/080_workflows_runbook.md`（Workflows ノウハウ）
- 関連 commit: `0e55e5f` — TDnet DL前日分取りこぼし修正（同セッション内の先行改修）

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか（P0-2: yaml diff 記載）
- [x] 呼び出し側への波及が行番号リストで明示されているか（「無し」明示含む）
- [x] 対応アンチパターン表が末尾にあるか（該当なし明示）
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか

---

## レビュー追記: 2026-05-15 23:15 JST — code-reviewer

→ `docs/reviews/184_cr_tdnet_shift_md_auto_update.md`
