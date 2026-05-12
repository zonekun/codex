# コードレビュー: predict.py today サブコマンド追加 + backfill 引数オプショナル化 + PS メニュー連携

- 日時: 2026-05-07 21:04 JST
- 対象: `scripts/earnings_model/predict.py`（`cmd_today`, `_get_prev_business_day`, `_resolve_backfill_range`）、`C:\Users\zonekun\Dropbox\stock\script\claude-investment-agent.ps1`（IsPredict ハンドラ）、`docs/knowledges/tools/059_earnings_model_eda.md`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: 日次の答え合わせワークフローを1コマンド化する `today` サブコマンド（前営業日を BQ から自動算出して predict + answer を連続実行）の追加、`backfill` の `--from`/`--to` をオプショナル化（省略時は GCS 既存全期間再構築）、PS メニューに対話式ハンドラを追加
- 品質評価: **B** — 主要フローは正しく動作するが、`cmd_today` が `cmd_answer` の株価投入チェック（`sys.exit(1)`）に当たった場合にエラーメッセージが不十分であること、および SQL の f-string 組立（既存問題の横展開）に改善余地がある
- 主要リスク:
  1. `cmd_today` が `cmd_answer` 内の `sys.exit(1)` で即死するため、`cmd_predict` は成功・GCS 保存済みなのに answer 未完了の「片方だけ成功」状態が残る
  2. `_resolve_backfill_range` の blob 名パース仕様が将来のファイル名変更に脆弱（prediction_YYYYMMDD_HHMMSS.json のフォーマット前提がハードコード）
  3. `_get_prev_business_day` / `_resolve_backfill_range` に docstring はあるが Google style の Args/Returns/Raises が欠落

## 【重大な指摘】（即修正）

### #1 `cmd_today` で answer が `sys.exit(1)` するとプロセス全体が即死し、predict 成功の文脈が消失

- 箇所: `scripts/earnings_model/predict.py:1042-1052`
- 事象: `cmd_today` は `cmd_predict(pred_args)` → `cmd_answer(answer_args)` を直列実行する。`cmd_answer` 内の株価投入チェック（L899-902）で `n_rows < 3000` の場合、`sys.exit(1)` でプロセスが即終了する。この時 `cmd_predict` は既に成功し GCS に prediction JSON を保存済みだが、ユーザーには「predict は成功した」旨が示されない
- トリガー: 朝の早い時間帯（J-Quants の株価データ投入前）に `today` を実行した場合。日次運用で頻出するシナリオ
- 影響: ユーザーは predict が成功したか失敗したかわからず、再実行すると prediction ファイルが GCS に重複保存される（ファイル名に HHMMSS が含まれるため上書きにならない）
- 根拠: L893-902 の `sys.exit(1)` は `cmd_answer` が独立実行される前提の安全ガードだが、`cmd_today` からの呼び出しではプロセス全体を道連れにする
- 推奨対応: `cmd_today` 内で `cmd_answer` の呼び出しを try/except で囲み、株価未投入時は「predict は完了済み。株価投入後に `answer --date {predict_date} --actual-date {actual_date}` を実行してください」とガイダンスを出す。または `cmd_answer` の `sys.exit(1)` を例外 raise に変えて `cmd_today` 側でハンドリングする

### #2 `_resolve_backfill_range` が GCS にサブディレクトリ blob がある場合にクラッシュ

- 箇所: `scripts/earnings_model/predict.py:1057-1072`
- 事象: `gcs_list_blobs("earnings_model/predictions/")` は prefix 配下の全 blob を返す。もし将来 `earnings_model/predictions/archive/` のようなサブパスに blob が置かれた場合、`rsplit("/", 1)[-1]` でサブディレクトリ名が取れ、`parts[1]` が 8 桁数字でないため無視されるだけだが、`earnings_model/actuals/` 側の `"for_" in b` チェックでは `for_` を含む任意の blob 名にマッチする可能性がある
- トリガー: GCS パス配下にメタデータファイル（例: `accuracy_summary.json` が actuals/ に移動された場合等）やサブフォルダが存在する場合
- 影響: `dates` set に不正な文字列が混入し、`sorted_dates` の min/max が狂う。backfill 範囲が想定外に広くなり、大量の BQ クエリが発生する可能性
- 根拠: L1064-1068 のパース処理は `for_` 以降を取得した後に `len(d) == 8 and d.isdigit()` でガードしているため、現時点では安全。ただし `accuracy_summary.json` は L1226-1228 で `gcs_list_blobs("earnings_model/actuals/")` の結果に含まれ、これには `for_` が含まれないので問題ない。**現状安全だが、blob 命名規約への暗黙依存がドキュメント化されていない点が保守リスク**
- 推奨対応: blob 名フィルタで `b.startswith("earnings_model/predictions/prediction_")` / `b.startswith("earnings_model/actuals/actual_")` のように正規パターンに限定するガードを追加。改善提案に格下げ可

### #3 `cmd_backfill` の `--from` のみ指定 / `--to` のみ指定で意図しない全期間リビルドが発動

- 箇所: `scripts/earnings_model/predict.py:1077-1081`
- 事象: `if not date_from or not date_to:` の条件で、片方だけ指定（`--from 20260401` のみ、`--to` 省略）した場合にも `_resolve_backfill_range()` が呼ばれ、指定した `--from` が無視されて GCS 全期間リビルドになる
- トリガー: ユーザーが `backfill --from 20260401` と打った場合（`--to` を忘れた場合）
- 影響: 意図した範囲ではなく全期間の backfill が実行され、BQ クエリコスト・処理時間が無駄に大きくなる
- 根拠: L1079 の条件は `not date_from or not date_to` で OR 結合。片方が None（argparse の default）でも全置換される
- 推奨対応: 片方のみ指定はエラーにする（`if bool(date_from) != bool(date_to): raise ValueError("--from と --to は両方指定するか、両方省略してください")`）。または `--from` のみの場合は `--to` をデフォルトで今日にする等の明示的なフォールバック

## 【改善提案】（可読性・保守性）

### #1 `_get_prev_business_day` / `_resolve_backfill_range` / `cmd_today` の docstring が Google style でない

- 箇所: `scripts/earnings_model/predict.py:1030-1039`, `1055-1072`, `1042-1052`
- 現状: 1行の summary のみ。Args / Returns / Raises セクションがない
- 提案: CLAUDE.md §コーディング規約で docstring は Google style 必須。特に `_get_prev_business_day` は `ValueError` を raise するのに Raises が未記載

### #2 `cmd_answer` 内の `print()` + `sys.exit(1)` パターン

- 箇所: `scripts/earnings_model/predict.py:890-891`, `901-902`, `914-915`
- 現状: エラーメッセージを `print()` で出力してから `sys.exit(1)` している。CLAUDE.md §コーディング規約で `print` 禁止・`structlog` 使用が規定されている
- 提案: 既存コード（`cmd_predict` / `cmd_answer` / `cmd_backfill` 全体）に渡る pre-existing の問題。今回の新規追加分（`cmd_today`）では `print()` を使っていないので新規追加部分は規約準拠。ただし `cmd_today` から呼ばれる `cmd_answer` の `print` + `sys.exit` が `cmd_today` の UX を劣化させているのは #1 の指摘と関連

### #3 SQL の f-string 組立（`_get_prev_business_day`）

- 箇所: `scripts/earnings_model/predict.py:1033-1035`
- 現状: `f"""SELECT MAX(DATE) AS prev_bd ... WHERE DATE < '{ah}'"""` と f-string で SQL を構築している
- 提案: 004 アンチパターン C-1 違反（SQL injection）。ただし入力値は `hy()` で `YYYYMMDD` → `YYYY-MM-DD` に変換済みの値であり、外部入力が直接注入される経路は argparse 経由の `--actual-date` のみ。実質的なリスクは低い。また、これは既存コード全体（L333, L896, L1009 等）に渡る pre-existing の問題であり、今回の新規追加分に限った指摘ではない。横展開として記録

### #4 PS メニューの `$pDate` 入力バリデーション不足

- 箇所: `claude-investment-agent.ps1:271`
- 現状: `$pDate` が `"t"` でも `"range"` でもない場合は「無効な選択です」で弾いているが、空文字の場合は `$pDate -eq ""` で `"t"` として扱われる（L272）。これは意図的な設計（Enter でデフォルト `t` 選択）
- 提案: 動作自体は問題ないが、`$pDate -eq "t"` を先に書いて `$pDate -eq ""` を OR に追加する方が意図が明確

### #5 `_resolve_backfill_range` の prediction blob パースがフォーマット固定前提

- 箇所: `scripts/earnings_model/predict.py:1060-1062`
- 現状: `parts = b.rsplit("/", 1)[-1].replace(".json", "").split("_")` で `prediction_YYYYMMDD_HHMMSS` を想定し、`parts[1]` を日付として取得。prediction ファイル名フォーマットが L873 で生成される `prediction_{predict_date}_{now.strftime('%H%M%S')}.json` と整合しているが、フォーマット変更時に壊れる
- 提案: `re.match(r"prediction_(\d{8})_\d{6}\.json$", filename)` のような正規表現で明示的にパースするとフォーマット依存が可視化される

## 【修正例】（必要な箇所のみ）

#### #1 に対する修正案（`cmd_today` の answer 失敗ハンドリング）

```python
# before: scripts/earnings_model/predict.py:1042-1052
def cmd_today(args: argparse.Namespace) -> None:
    """今日を答え合わせ日として predict + answer を連続実行する."""
    actual_date = args.actual_date
    predict_date = _get_prev_business_day(actual_date)
    log.info("today_mode", predict_date=predict_date, actual_date=actual_date)

    pred_args = argparse.Namespace(date=predict_date)
    cmd_predict(pred_args)

    answer_args = argparse.Namespace(date=predict_date, actual_date=actual_date)
    cmd_answer(answer_args)

# after
def cmd_today(args: argparse.Namespace) -> None:
    """今日を答え合わせ日として predict + answer を連続実行する.

    Args:
        args: argparse.Namespace with actual_date (YYYYMMDD).

    Raises:
        ValueError: 前営業日が BQ から算出できない場合。
        SystemExit: 株価データ未投入で answer が実行できない場合
            （predict は成功済みの旨をログ出力してから exit）。
    """
    actual_date = args.actual_date
    predict_date = _get_prev_business_day(actual_date)
    log.info("today_mode", predict_date=predict_date, actual_date=actual_date)

    pred_args = argparse.Namespace(date=predict_date)
    cmd_predict(pred_args)
    log.info("predict_completed", predict_date=predict_date)

    try:
        answer_args = argparse.Namespace(date=predict_date, actual_date=actual_date)
        cmd_answer(answer_args)
    except SystemExit as e:
        if e.code != 0:
            log.warning(
                "answer_skipped_price_not_ready",
                predict_date=predict_date,
                actual_date=actual_date,
                msg=f"predict は完了済み。株価投入後に実行: "
                    f"answer --date {predict_date} --actual-date {actual_date}",
            )
            raise
```

#### #3 に対する修正案（backfill の片方だけ指定ガード）

```python
# before: scripts/earnings_model/predict.py:1077-1081
    date_from = getattr(args, "from")
    date_to = args.to
    if not date_from or not date_to:
        date_from, date_to = _resolve_backfill_range()

# after
    date_from = getattr(args, "from")
    date_to = args.to
    if bool(date_from) != bool(date_to):
        log.error("partial_range", date_from=date_from, date_to=date_to)
        raise ValueError("--from と --to は両方指定するか、両方省略してください")
    if not date_from:
        date_from, date_to = _resolve_backfill_range()
```

## 【確認できなかった事項】

- `_get_prev_business_day` が返す日付が実際に決算開示が存在する営業日かどうかの検証（BQ の `STOCK_PRICE_JQUANTS` に存在する最大日が「前営業日」であることは確かだが、その日に J-Quants の `fin_summary` に決算データがあるかは `compute_features` で初めて判明する。データが無い場合は `cmd_predict` が `df_feat is None` で早期 return するため動作上は問題ないが、`cmd_today` の答え合わせとしては空振りになる）
- PS メニューの `py_launcher` 分岐（`& py -3.12`）が predict.py の Python バージョン要件（pyproject.toml で 3.14 指定）と整合するか。3.12 で実行すると `ZoneInfo` 等は使えるが、3.14 固有機能を使っている場合は失敗する可能性がある
- `_resolve_backfill_range` が GCS 上のファイル数が数千件以上になった場合の `gcs_list_blobs` のレイテンシ（004 C-3 の prefix 全走査リスク。`earnings_model/predictions/` と `earnings_model/actuals/` の 2 prefix で 2 回走査）
