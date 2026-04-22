# Cloud Run ジョブ実行状況確認 CLI（check_jobs.py）

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**:
- `scripts/check_jobs.py` — メインスクリプト

---

## 概要

Cloud Run ジョブの実行状況と Cloud Scheduler の次回実行予定を
ターミナルで一覧表示する CLI ツール。`gcloud` コマンドをラップしている。

---

## 使い方

```bash
# 全ジョブの直近10件を表示（+ スケジューラ一覧）
PYTHONUTF8=1 python scripts/check_jobs.py

# 特定ジョブのみ
PYTHONUTF8=1 python scripts/check_jobs.py edinet-download

# 取得件数を変更
PYTHONUTF8=1 python scripts/check_jobs.py --limit 30

# 管理対象ジョブ名を一覧
PYTHONUTF8=1 python scripts/check_jobs.py --all
```

---

## 出力例

```
=== Cloud Run ジョブ実行状況 (2026-03-08 09:00 JST) ===

───────────────────────────────────────────────────────────────────────────────────────────────────
  edinet-download  （直近10件）
───────────────────────────────────────────────────────────────────────────────────────────────────
  状態    開始(JST)      完了(JST)      パラメータ                       失敗理由              実行ID
  ✅     03/08 06:00  03/08 06:45  20260301-20260308              -                    edinet-download-xxxxx
  ❌     03/07 06:00  03/07 06:12  20260201-20260228              NonZeroExitCode       edinet-download-yyyyy
  🔄     03/08 07:00  -            20260101-20260131              -                    edinet-download-zzzzz

───────────────────────────────────────────────────────────────────────────────────────────────────
  次回スケジュール実行予定
  ジョブ名                              cron                    次回実行(JST)   状態
  edinet-download-daily               0 21 * * 1-5 (UTC)     03/09 06:00   ✅ ENABLED
```

---

## ステータスアイコン

| アイコン | 意味 | 判定条件 |
|---------|------|---------|
| ✅ | 成功 | `conditions[0].type=Completed` かつ `status=True` かつ `reason` 空 |
| ❌ | 失敗 | `type=Failed` または `status=False` または `reason` 非空 |
| 🔄 | 実行中 | 完了時刻（completionTime）がない |
| ⛔ | キャンセル | `type=Cancelled` |
| ❓ | 不明 | 上記いずれにも該当しない |

> **注意**: Cloud Run は成功・失敗どちらも `type=Completed` を返すため、
> `status=True/False` と `reason` の両方を確認する必要がある。

---

## 管理対象ジョブ（KNOWN_JOBS）

```
dividend-date-load, edinet-download, edinet-load, edinet-load-parallel,
edinet-delay, edinet-xbrl-extractor, irbank-tdnet-download, is-holiday, jquants-fin-summary,
shina-margin-balance-load, stock-code-list-load, stock-price-load,
tdnet-download, tdnet-load, tdnet-load-parallel
```

### ジョブ追加時の手順

**新しい Cloud Run Job を作成したら必ず以下を実施する:**

1. `scripts/check_jobs.py` の `KNOWN_JOBS` リスト（21行目付近）に追記する
2. 本ファイル（033_check_jobs.md）の上記リストも更新する

```python
# scripts/check_jobs.py の KNOWN_JOBS に追加する例
KNOWN_JOBS = [
    ...
    "新しいジョブ名",   # ← 追記
]
```

> **忘れると**: `check_jobs.py` の全ジョブ一覧に表示されず、実行状況が見えなくなる。
> `python scripts/check_jobs.py --job 新しいジョブ名` で個別確認はできるが、定常監視から漏れる。

---

## 技術メモ

- Windows: `gcloud.cmd` を使用（`platform.system() == "Windows"` で自動判別）
- パラメータ表示: `--from=20220101,--to=20220131` → `20220101-20220131` に圧縮表示
- UTC→JST 変換: `completionTime` は UTC で返るため `+9h` 変換して表示
- スケジューラ: `--location=us-west1` で取得し、次回実行時刻でソート
