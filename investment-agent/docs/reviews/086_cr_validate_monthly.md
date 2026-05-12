# コードレビュー: validate_monthly_first_run.py

- 日時: 2026-05-06 16:21 JST
- 対象: `scripts/validate_monthly_first_run.py`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: GCS上の正本（extract出力）とバックアップ（BC由来）のmonthly_records.jsonを比較し、桁違い・単位ミス・欠損フィールドを検出してCSVに出力する検証スクリプト
- 品質評価: **B** — 全体構造は明快で規約準拠だが、例外握り潰し・型変換エラー・exit code不備など運用時に問題を隠蔽しうる欠陥がある
- 主要リスク:
  1. `_load_records` の bare except が GCS認証エラー・ネットワーク障害をサイレントに握り潰し、正常な「backupなし」と区別不能
  2. `float()` 変換で非数値フィールド値（文字列・リスト等）が入った場合に未処理の ValueError/TypeError で処理中断
  3. main() が常に exit 0 で終了するため、上流スクリプト・Workflows から呼び出された場合に異常検知できない

---

## 【重大な指摘】（即修正）

### #1 `_load_records` の bare except による障害隠蔽

- 箇所: `scripts/validate_monthly_first_run.py:63`
- 事象: `except Exception` で全例外を握り潰し `None` を返す。GCS認証期限切れ・ネットワークタイムアウト・バケット権限エラー・JSON パースエラー等がすべて「ファイルが存在しない」と同じ扱いになる
- トリガー: サービスアカウントキーの期限切れ、ネットワーク一時障害、GCS一時的な5xx、不正なJSON（UTF-8 BOM付き等）
- 影響: 451社中の多数が `no_backup` / `no_extract` にカウントされるだけで、インフラ障害に気付けない。本来全tickerで読めるはずのデータが読めない異常を正常扱いする
- 根拠: L63 `except Exception: return None`。呼び出し側（L169, L178）は None をスキップ条件としてのみ使用し、理由を区別しない
- 推奨対応: `google.cloud.exceptions.NotFound` のみを catch して None を返し、それ以外は raise するか、少なくとも `log.warning("GCS読込失敗", path=blob_path, exc_info=True)` でログ出力する。さらに `no_backup` / `no_extract` の割合が異常に高い場合（例: 50%超）に警告を出すガードも検討

### #2 `float()` 変換時の型安全性欠如

- 箇所: `scripts/validate_monthly_first_run.py:230-231`
- 事象: `float(prod_val)` / `float(bc_val)` が文字列 "N/A"・リスト・dict等の非数値で ValueError/TypeError を raise し、当該tickerの残りフィールドが検証されない
- トリガー: monthly_records.json の fields 内に数値でない値が格納されている場合（実データにおいて文字列 "0" や "-" や "N/A" が混入する可能性は十分ある）
- 影響: 1件の型エラーでそのticker全体のvalidationが中断。catchされずプロセス全体が落ちる
- 根拠: L230 `alert_type = _classify_alert(float(prod_val), float(bc_val), ...)` に try/except がない。prod_val/bc_val は JSON 由来の `Any` 型
- 推奨対応: `float()` 変換を try/except ValueError/TypeError で囲み、変換失敗時は `TYPE_ERROR` 種別のアラートとして記録するか、そのフィールドをスキップしてログ出力する

### #3 main() が常に exit 0 で終了（004 A-1/A-7 違反）

- 箇所: `scripts/validate_monthly_first_run.py:249-289`
- 事象: main() は alerts 検出の有無に関わらず常に正常終了する。バッチ呼び出しや将来のWorkflows統合で「validation失敗」を検知する手段がない
- トリガー: 上流スクリプトやスケジューラから `validate_monthly_first_run.py` を実行し、exit code で成否を判定する場合
- 影響: 164件のアラートが検出されても exit 0 で終わるため、監視やパイプラインのgate判定に使えない
- 根拠: main() 末尾にアラートサマリのログはあるが `sys.exit(1)` がない。004 A-1「`main()` 末尾で `if errors > 0: sys.exit(1)` を徹底」に違反
- 推奨対応: アラート件数に応じて `sys.exit(1)` を返すか、`--strict` フラグを設けてアラート検出時に非ゼロ exit する。現時点で手動実行のみなら severity は下がるが、将来の自動化を見据えて対応すべき

### #4 `_find_bc_record_near` の月オフセット計算で13月以上の巻き戻し未対応

- 箇所: `scripts/validate_monthly_first_run.py:94-98`
- 事象: `month -= offset_months` で負値になった場合に `while month <= 0: month += 12; year -= 1` で補正するが、offset_months が 12 を超えると1回のループでは不十分（例: offset=13, month=1 → month=-12 → 1回補正で month=0 → 条件 `<= 0` で再度ループ → OK）。ただし、`offset=24, month=1` → month=-23 → 1回目 month=-11, year-1 → 2回目 month=1, year-2。実際には while ループなので正しく動作する
- トリガー: `--month-offset 24` 等の大きな値を指定した場合
- 影響: 再検証の結果、while ループのため正しく動作する。**本指摘は取り消し**
- 根拠: L95-98 の while ループが適切に多重巻き戻しを処理する

（#4 は精査の結果問題なし。重大な指摘は #1〜#3 の 3 件）

---

## 【改善提案】（可読性・保守性）

### #1 型ヒント不足: `records_data` の型が `dict` のみで内部構造が不明

- 箇所: `scripts/validate_monthly_first_run.py:78, 89`
- 現状: `records_data: dict` では内部のキー構造（`records` キーの存在、各レコードの `source` / `year_month` / `fields` キー）が読み手に伝わらない
- 提案: TypedDict または dataclass で `MonthlyRecordsFile` / `MonthlyRecord` の構造を定義するか、docstring で明示する。少なくとも `dict[str, Any]` とする

### #2 GCS パスのハードコード（004 E-1 への軽微な抵触）

- 箇所: `scripts/validate_monthly_first_run.py:29-31`
- 現状: `GCS_BUCKET`, `GCS_RECORD`, `GCS_BACKUP` がモジュール定数としてハードコードされている。特に `GCS_BACKUP` は `20260503_1527_bc_historical` という日付入りのパスで、今後バックアップ時点が変わると書き換えが必要
- 提案: `--backup-prefix` CLI引数で上書き可能にする。本スクリプトが一過性の検証ツールであれば現状維持も許容だが、再利用時に毎回コード改修が必要になる

### #3 `_is_valid_ym` のバリデーションが緩い

- 箇所: `scripts/validate_monthly_first_run.py:66-75`
- 現状: 年4桁・月2桁が int 変換可能かのみ確認。月が 00 や 13 でも通る。セパレータが `-` であることも検証していない
- 提案: `1 <= int(ym[5:7]) <= 12` のチェックを追加。不正な ym が比較計算（`_ym_diff`）に渡ると意味のない結果になる

### #4 stats カウンタの活用不足

- 箇所: `scripts/validate_monthly_first_run.py:163, 245`
- 現状: `stats` dict を structlog で出力するのみ。`no_extract` / `no_backup` が異常に多い場合の警告がない
- 提案: 検証終了後に `no_backup / total > 0.5` 等の異常比率を検知して `log.warning` を出す。#1 の障害隠蔽対策と合わせて有効

### #5 `log.info(f"  {t}: {c}件")` で f-string をログメッセージに使用

- 箇所: `scripts/validate_monthly_first_run.py:287`
- 現状: structlog のベストプラクティスは構造化キーワード引数（`log.info("alert_type_count", type=t, count=c)`）だが、f-string でフォーマット済み文字列を渡している
- 提案: `log.info("alert_type_count", alert_type=t, count=c)` に変更。構造化ログとして後から集計可能にする

---

## 【確認できなかった事項】

- monthly_records.json の `fields` 内に実際に非数値（文字列・null・配列）が含まれるケースがあるかどうかは、実データを確認しないと断言できない（ただし JSON 由来の Any 型として防御的に扱うべき）
- 本スクリプトが今後パイプラインの一部として自動実行される予定があるかどうか（あれば #3 の exit code 問題の優先度が上がる）
- `_get_gcs()` で取得したクライアントがスクリプト内で1回しか使われないため singleton 化（004 D-2）は不要だが、将来の拡張でループ内生成にならないかの懸念

---

## 【規約適合チェック】

| 規約項目 | 適合 | 備考 |
|---------|------|------|
| 型ヒント必須 | 部分的 | 関数シグネチャはあるが内部構造が不明瞭 |
| docstring必須（Google style） | OK | 各関数に1行docstringあり |
| structlog使用（print禁止） | OK | structlog のみ使用 |
| GCP認証方式 | OK | service_account.Credentials 明示構築 |
| datetime.now(tz=JST) | OK | L266 `datetime.now(tz=JST)` |
| encoding="utf-8" 明示 | OK | L274 `encoding="utf-8"` |
| TICKER文字列型 | OK | ticker は str のまま処理 |
| sys.exit(1) on error | NG | #3 で指摘 |
| bare except 禁止 | NG | #1 で指摘 |
