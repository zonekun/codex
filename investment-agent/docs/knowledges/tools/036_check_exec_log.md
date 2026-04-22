# Cloud Run 実行ID ログ確認ツール（check_exec_log.py）

**カテゴリ**: tools
**作成日**: 2026-03-09
**ステータス**: 有効
**関連ファイル**: `scripts/check_exec_log.py`

## 概要

Cloud Run ジョブの**実行ID**を指定してログを確認する汎用ツール。
`check_jobs.py`（ジョブ一覧）とは別物。実行ID（例: `tdnet-load-zxd8j`）を指定して
その実行の詳細・ログ・進捗サマリを確認するために使う。

> **⚠️ GCP MCP に切り替え済み**: `check_exec_log.py` の代わりに `logging_job()` MCP ツールを使う。
> ```
> logging_job("tdnet-load", execution_id="tdnet-load-zxd8j")
> ```
> スクリプト実行不要・トークン消費少・即時結果。`check_exec_log.py` は廃止候補。

---

## 使い方

```bash
# 最新30件のログを表示（デフォルト）
python scripts/check_exec_log.py tdnet-load-zxd8j

# 件数指定
python scripts/check_exec_log.py tdnet-load-zxd8j --limit 50

# 先頭N件（起動ログ・初期化確認）
python scripts/check_exec_log.py tdnet-load-zxd8j --head

# キーワードフィルタ
python scripts/check_exec_log.py tdnet-load-zxd8j --grep "インサート"
python scripts/check_exec_log.py tdnet-load-zxd8j --grep "エラー"
python scripts/check_exec_log.py tdnet-load-zxd8j --grep "TICKER"

# 進捗サマリ自動生成（BQ件数・TICKER範囲・スキップ数）
python scripts/check_exec_log.py tdnet-load-zxd8j --summary
```

---

## 出力例

```
======================================================================
  実行ID : tdnet-load-zxd8j
  ジョブ : tdnet-load
  状態   : ✅ 完了   （🔄 実行中 / ❌ 失敗 のこともある）
  開始   : 03/08 10:23:45
  完了   : 03/09 14:51:02
  パラメ : --from=20160101 --to=20161231
======================================================================

📋 最新 30 件:

  03/09 14:50:58  BQ インサート: 100 件
  03/09 14:50:45  ロード成功: tdnet/1301/20161228_1301_...pdf
  ...
```

```
📊 進捗サマリ生成中...
  取込済み（スキップ）: 1,234 件
  今回BQインサート累計: 45,600 件以上
  処理済みTICKER範囲: 1301 〜 3927 （512 銘柄）
```

---

## 仕組み

```python
# 実行IDからジョブ名を推定（末尾5文字のハッシュを除去）
job_name = re.sub(r"-[a-z0-9]{5}$", "", exec_name)
# 例: tdnet-load-zxd8j → tdnet-load

# gcloud logging read でフィルタ
filter = (
    f'resource.type=cloud_run_job '
    f'AND resource.labels.job_name={job_name} '
    f'AND labels."run.googleapis.com/execution_name"={exec_name}'
)
```

---

## `check_jobs.py` との違い

| ツール | 用途 |
|-------|------|
| `check_jobs.py` | ジョブ一覧・最新実行状況・スケジューラ確認 |
| `check_exec_log.py` | 特定の実行IDのログ詳細・進捗確認 |

実行IDは `check_jobs.py` の出力から取得するか、`gcloud run jobs executions list` で確認する。

---

## 根拠・出典

長時間実行（年単位バックフィル）中のジョブの進捗確認ニーズから作成。
毎回 gcloud コマンドを組み立てるトークン消費を削減するための汎用ツール。
