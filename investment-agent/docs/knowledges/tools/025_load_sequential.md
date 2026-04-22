# load_sequential.py - シーケンシャルジョブ実行スクリプト

**カテゴリ**: tools
**作成日**: 2026-03-07
**ステータス**: 有効
**関連ファイル**: `scripts/load_sequential.py`

## 概要

複数年の tdnet-load と edinet-load を順番に実行するオーケストレーションスクリプト。
年単位でジョブを投入し、両方が完了してから次の年に進む。

## 使い方

```bash
# 指定年を順に実行（デフォルト: 2024 2023 2022 2021）
PYTHONUTF8=1 python scripts/load_sequential.py 2016 2017 2018

# 単年
PYTHONUTF8=1 python scripts/load_sequential.py 2020
```

## 仕組み

1. 各年について tdnet-load と edinet-load を `--async` で同時投入
2. 両ジョブの完了を 60 秒間隔でポーリング
3. どちらかが失敗した場合、以降の年をスキップして終了（`sys.exit(1)`）
4. 全年完了で正常終了

## 重要な制約・注意事項

### tdnet-load と edinet-load を両方投入する

`load_sequential.py` は必ず **tdnet-load と edinet-load の2本セット**を投入する。
片方だけを再実行したい場合は `gcloud run jobs execute` を直接使うこと。

```bash
# tdnet-load だけ再実行（2016年）
gcloud.cmd run jobs execute tdnet-load \
  --region us-west1 \
  --args="--from=20160101,--to=20161231" \
  --async
```

### `--from/--to` 形式を使う（`--year` は廃止）

```bash
# ✅ 正しい
tdnet_args = f"--from={year}0101,--to={year}1231"
edinet_args = f"--from={year}0101,--to={year}1231"

# ❌ 誤り（edinet-load は --year オプション非対応）
edinet_args = f"--year={year}"
```

### クラッシュ再開時は既存ジョブを確認してから実行

load_sequential.py は起動時に既存の実行中ジョブを確認しない。
対象年のジョブが既に実行中の場合、重複投入になる。
```bash
# 既存の実行状況を確認してから起動
PYTHONUTF8=1 python scripts/check_jobs.py --limit 5
```

## get_status() の正しい実装

`Completed + Unknown` = **実行中**（起動直後の状態）。`Unknown` を `failed` と扱うと誤検知する。

```python
def get_status(execution: str) -> str:
    ...
    if cond_type == "Completed":
        if cond_status == "True" and not reason:
            return "success"
        elif cond_status == "False":
            return "failed"   # False のみ失敗確定
        else:
            return "running"  # Unknown = まだ実行中
    if cond_type == "Failed":
        return "failed"
    return "running"
```

詳細は `docs/knowledges/tools/024_cloudrun_job_monitoring.md` を参照。
