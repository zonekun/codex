# Cloud Run Job 実行状況モニタリング

**カテゴリ**: tools
**作成日**: 2026-03-07
**ステータス**: 有効
**関連ファイル**: `scripts/check_jobs.py`

## 概要

Cloud Run Job の `conditions[0].type` は成功・失敗・実行中にかかわらず常に `"Completed"` を返す。
実際の状態は `status`（True/False/Unknown）と `reason` フィールドで判断する必要がある。

## conditions の正しい解釈

| type | status | reason | 実際の状態 |
|------|--------|--------|----------|
| `Completed` | `True` | 空 | **成功** |
| `Completed` | `False` | `NonZeroExitCode` | **失敗**（exit code != 0） |
| `Completed` | `False` | 空（または `The configured memory limit was reached` 等） | **失敗**（タイムアウト・OOM） |
| `Completed` | `Unknown` | 空 | **実行中**（まだ判定されていない） |
| `Failed` | — | — | **失敗** |
| `Cancelled` | — | — | **キャンセル済み** |

### 重要: `Completed + Unknown` = 実行中

ジョブ起動直後は `type=Completed, status=Unknown` となる。これは**まだ判定が確定していない**状態（実行中）であり、失敗ではない。`status=False` になって初めて失敗が確定する。

タイムアウトによる失敗は `status=False` かつ `reason` 空。ログに `The configured timeout was reached` が記録される。

## ✅ 推奨: GCP MCP で確認する（旧 gcloud コマンドより簡単）

```
# 実行中ジョブのログ確認
logging_job("tdnet-load-parallel")

# 特定 execution_id のログ確認（check_exec_log.py の代替）
logging_job("tdnet-load-parallel", execution_id="tdnet-load-parallel-xcj5r")
```

`logging_job` は実行状態（成功/失敗/実行中）も自動判定して返す。

---

## gcloud コマンドで確認する（参考・旧方法）

```bash
# 実行状態の詳細確認（type + status + reason を同時取得）
gcloud.cmd run jobs executions describe <execution-name> \
  --region us-west1 \
  --format="value(status.conditions[0].type,status.conditions[0].status,status.conditions[0].reason,status.conditions[0].message)"

# 例: OOM 失敗の場合の出力
# Completed  False  (空)  Task failed and message: The configured memory limit was reached.

# 例: 成功の場合の出力
# Completed  True  (空)  (空)
```

## check_jobs.py の status_icon() 実装（修正済み）

```python
def status_icon(cond_type: str, cond_status: str, reason: str, completed: str) -> str:
    """Cloud Run の conditions から表示アイコンを決定する."""
    # 完了時刻がない = まだ実行中
    if not completed or completed == "-":
        return "🔄"
    if cond_type == "Cancelled":
        return "⛔"
    # Completed だが status=False or reason あり → 実際は失敗
    if cond_type == "Completed" and (cond_status == "False" or reason):
        return "❌"
    if cond_type == "Completed" and cond_status == "True":
        return "✅"
    if cond_type in ("Failed", "Running"):
        return "❌" if cond_type == "Failed" else "🔄"
    return "❓"
```

**ポイント**: 完了時刻（`completionTime`）がない場合を最初にチェックして 🔄 を返す。これにより `Unknown` ステータスのジョブも正しく「実行中」と表示できる。

## gcloud format で3フィールドを同時取得する

```bash
# ✅ 正しい: type + status + reason を tab 区切りで取得
--format="value(status.conditions[0].type,status.conditions[0].status,status.conditions[0].reason)"
# 出力例（成功）: Completed\tTrue\t
# 出力例（失敗）: Completed\tFalse\tNonZeroExitCode
# 出力例（実行中）: Completed\tUnknown\t

# ❌ 誤り: type だけ取ると成功・失敗・実行中を区別できない
--format="value(status.conditions[0].type)"
# 出力: Completed（常にこの値）
```

## load_sequential.py の get_status() 実装（修正済み）

ポーリングで実行完了を待つ際も同じ判定ロジックが必要。

```python
def get_status(execution: str) -> str:
    """実行状態を返す: 'success' | 'failed' | 'running'"""
    result = subprocess.run(
        [GCLOUD, "run", "jobs", "executions", "describe", execution,
         "--region", REGION,
         "--format=value(status.conditions[0].type,status.conditions[0].status,status.conditions[0].reason)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    out = result.stdout.strip()
    if not out:
        return "running"
    parts = out.split("\t")
    cond_type   = parts[0] if len(parts) > 0 else ""
    cond_status = parts[1] if len(parts) > 1 else ""
    reason      = parts[2] if len(parts) > 2 else ""

    if cond_type == "Completed":
        if cond_status == "True" and not reason:
            return "success"
        elif cond_status == "False":
            return "failed"  # status=False → 失敗確定
        else:
            return "running"  # Unknown → まだ実行中
    if cond_type == "Failed":
        return "failed"
    return "running"
```

`Unknown` を `failed` 扱いにすると起動直後のジョブを誤検知するので注意。

## 背景

2026-03-07 に発覚した監視バグ。`check_jobs.py` が `type` フィールドのみ確認していたため、OOM 失敗したジョブを ✅ と誤表示していた。これにより複数の失敗ジョブが見落とされた。
