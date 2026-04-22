# 過去データバックフィル シーケンシャル実行

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**:
- `scripts/backfill_sequential.py` — メインスクリプト

---

## 概要

`irbank-tdnet-download` と `edinet-download` の2ジョブを、
指定した年リストに対して年ごとにシーケンシャルに実行する。
1年分が全件完了してから次の年へ進む安全設計。

---

## 使い方

```bash
# デフォルト: 2019, 2018, 2017, 2016 年を順に処理
PYTHONUTF8=1 python scripts/backfill_sequential.py

# 年を指定
PYTHONUTF8=1 python scripts/backfill_sequential.py 2021 2020
```

---

## 動作フロー

```
年リスト [2019, 2018, ...] を順に処理
  ↓ 各年を17分割（月次ほぼ半月ごと）
  ↓ irbank-tdnet-download × 17 + edinet-download × 17 = 34 ジョブを --async 投入
  ↓ 60秒ごとにポーリング → 全完了を確認
  ↓ Failed があれば以降の年をスキップして終了（sys.exit(1)）
  ↓ 次の年へ
```

---

## 分割ロジック

1年を17スプリットに分割（月ごと + 2月は閏年対応で2分割）：

| スプリット | 期間例 |
|-----------|--------|
| 01 | 1/1〜1/31 |
| 02 | 2/1〜2/15 |
| 03 | 2/16〜2/28(29) |
| 04〜17 | 3月〜12月（月単位 or 前後半） |

---

## 注意事項

- Windows では `GCLOUD = "gcloud.cmd"`（gcloud.cmd を使用）
- ポーリング間隔は `POLL_INTERVAL = 60` 秒
- 1年あたり34ジョブを並列投入するため Cloud Run の同時実行上限に注意
- Failed 判定: `status.conditions[0].type` が `Failed` または `status=False + reason あり`
- `025_load_sequential.md`（日次シーケンシャル）とは別スクリプト。こちらは**過去年度バックフィル専用**

---

## 関連知見

- `docs/knowledges/tools/021_irbank_tdnet_download.md` — irbank-tdnet-download の仕様
- `docs/knowledges/tools/009_edinet_download.md` — edinet-download の仕様
- `docs/knowledges/tools/024_cloudrun_job_monitoring.md` — ジョブ監視全般
