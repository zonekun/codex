# コードレビュー: save_backlog_record.py — 受注残高GCS upsertスクリプト

- 日時: 2026-05-07 14:30 JST
- 対象: `scripts/save_backlog_record.py`
- パターン: 1 (新規)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: Gemini Vision抽出済みJSONをGCS `quarterly/record/{ticker}/records.json` にupsert保管するスクリプトの新規作成。structure.jsonの有効メトリクス検証・最新期特定・upsert・dry-runフラグ付きバッチ処理を実装。
- 品質評価: **C** — ロジックの骨格は設計仕様に沿っているが、exit code不整合・ハードコードパス・print使用・period文字列比較の脆弱性・dry-runとwet-runの非対称性等、規約違反と潜在バグが複数存在する。
- 主要リスク:
  1. `errors > 0` でも `sys.exit(1)` を呼ばず正常終了（A-1違反）。Cloud Run Job等で偽SUCCESS扱いになる
  2. `find_latest_period()` が文字列比較に依存。period フォーマットが企業毎に異なる場合（`"2026年3月期"` vs `"2025-4Q"` の混在）に最大値が不定になる
  3. `keys/gcp-service-account.json` が相対パスのハードコード（E-1違反）。実行ディレクトリ依存でCloud Run等の本番環境で失敗する

---

## 【重大な指摘】（即修正）

### #1 errors > 0 でも sys.exit(0) で終了（A-1違反）

- 箇所: `scripts/save_backlog_record.py:163-166`
- 事象: `errors` カウントがどれだけ大きくても `sys.exit()` 呼び出しが存在せず、常に終了コード 0 で終わる
- トリガー: GCS 読み書きエラー・JSON decode 失敗等が1件でも発生した場合
- 影響: Cloud Run Job が `SUCCEEDED` 扱いになり、上流 Workflows / 監視クエリがデータ欠損を検知できない。`004_coding_conventions.md` §A-1 直接違反
- 根拠: `main()` 末尾に `sys.exit()` 呼び出しが存在しない（L163-166 のみ print サマリ）
- 推奨対応: `print(...)` の後に `sys.exit(1 if errors else 0)` を追加する。記載先: `004_coding_conventions.md` §A-1 既存ルールの適用

### #2 find_latest_period() の文字列比較が period フォーマット混在に脆弱

- 箇所: `scripts/save_backlog_record.py:45-49`
- 事象: `max(periods, key=lambda p: p.get("period", ""))` はUnicodeコードポイント比較。period文字列が `"2026年3月期"` 等の日本語形式と `"2025-4Q"` 等のASCII形式が混在する場合、正しい最新期を選べない
- トリガー: 同一企業の extracted JSON 内で複数期間のフォーマットが混在するとき（例: 1Q決算短信で `2025-1Q` と前期比較の `2024年3月期` が両方 periods に入る場合）
- 影響: 古い期間のデータを「最新期」と誤判定してrecords.jsonに書き込む。上書きupsertなので正しい最新データを古いデータで破壊する可能性がある
- 根拠: 89_quarterly_disclosure_master.md §抽出プロンプトのガードレールに「同一テーブルに複数期間がある場合、全期間を個別の `periods` 要素として抽出」と明記されており、period フォーマットの統一保証がない
- 推奨対応: 文字列比較に依存せず、`period_type` フィールドと数値化可能なキーで比較する関数を設計する。暫定的には、period 文字列から西暦4桁を抽出して数値比較するか、`extraction_date` で最新を判定するフォールバックを検討すること。設計が確定するまで未知フォーマット混在時にwarningを出してskipする安全策を推奨

### #3 GCS認証キーパスと STRUCTURE_DIR が相対パスのハードコード（E-1違反）

- 箇所: `scripts/save_backlog_record.py:15, 20-23`
- 事象: `STRUCTURE_DIR = Path("meta/quarterly")` と `keys/gcp-service-account.json` が実行カレントディレクトリ依存。Cloud Run Job等ではコンテナのワーキングディレクトリが異なる
- トリガー: プロジェクトルート以外からスクリプトを実行、またはCloud Run Jobとしてコンテナ内実行
- 影響: `FileNotFoundError` でスクリプト全体が停止。Cloud Run環境では認証失敗
- 根拠: CLAUDE.md §コーディング規約「設定値はハードコーディング禁止。config/以下のYAMLまたは環境変数で管理」、004 §E-1 直接違反
- 推奨対応:
  - `keys/gcp-service-account.json` → `settings.google_application_credentials` 経由（CLAUDE.md規約準拠）
  - `STRUCTURE_DIR` → `Path(__file__).parent.parent / "meta" / "quarterly"` でスクリプト位置基準の絶対パスに変更するか、環境変数で注入する

### #4 print() 使用（structlog 規約違反）

- 箇所: `scripts/save_backlog_record.py:103, 149, 163-166` (および他print使用箇所)
- 事象: CLAUDE.md §コーディング規約「print禁止。structlogを使用」に違反
- トリガー: スクリプト実行時（常に）
- 影響: Cloud Run Logging との統合不可。structured field での監視クエリが書けない（004 §A-7 の「structured field に出力すると監視クエリが書ける」前提を破壊）
- 根拠: L103, L149, L161, L163-166 すべて `print()`
- 推奨対応: `import structlog; logger = structlog.get_logger()` を追加し、`print()` を `logger.info()/logger.warning()/logger.error()` に置換する。サマリは `logger.info("完了", saved=saved, skipped=skipped, errors=errors)` の形式で structured fields として出力する

### #5 dry-run と wet-run で upsert 結果が非対称

- 箇所: `scripts/save_backlog_record.py:140-155`
- 事象: dry-run モードでは既存 records.json を GCS から読み込まないため、upsert の衝突検出（同一 period の上書き）が動作しない。表示されるのは常に「新規追加」ケースのみ
- トリガー: `--dry-run` フラグ付きで実行し、既に records.json が存在する銘柄を処理するとき
- 影響: dry-run の検証値（新規 vs 上書きの件数）が本番実行と乖離する。dry-run の主目的である「本番前の動作確認」が形骸化する
- 根拠: L140 の `if not args.dry_run:` ブロックで dry-run 時は `existing` が空オブジェクトで固定されている（L139）
- 推奨対応: dry-run 時も GCS から既存データを読み込んで upsert 判定を実行する。GCS への書き込みのみを `if not args.dry_run:` で抑制する。表示も「上書き」「新規追加」を区別して出力する

### #6 `adapter_version` が常に空文字

- 箇所: `scripts/save_backlog_record.py:77`
- 事象: 知見MD §レコード保管設計のレコード構造例では `"adapter_version": "2026-03"` となっているが、`build_record()` は常に `"adapter_version": ""` を設定する
- トリガー: 全件処理
- 影響: 将来の再抽出・デバッグ時に「どの structure.json バージョンで抽出したか」の追跡不能。adapter バグ修正後の再抽出対象特定が困難
- 根拠: L77 `"adapter_version": ""`、知見MDレコード例では `"adapter_version": "2026-03"`（structure.json の `current_version` 値と一致するはず）
- 推奨対応: `build_record()` の引数に `adapter_version: str` を追加し、呼び出し元で `valid_metrics` 取得時に同時に取得した `current_version` を渡す。`load_structure_metrics()` の戻り値を `(set[str], str)` に変更するか、別の `load_structure_version()` 関数を追加する

---

## 【改善提案】（可読性・保守性）

### #1 `source_doc_id` の抽出ロジックが脆弱

- 箇所: `scripts/save_backlog_record.py:76`
- 現状: `.split("/")[-1].split("_")[-1].replace(".pdf", "")` の多段チェーンで `_source_pdf` から doc_id を抽出。`_source_pdf` が空文字 or フォーマット外の場合に空文字を設定してしまう
- 提案: 知見MD §structure.json スキーマに `_source_doc_id` フィールドが定義されている。structure.json から直接 `data.get("_source_doc_id", "")` で取得する方が堅牢。extracted JSON 側にも `_source_doc_id` を含めるかは仕様確認が必要

### #2 `get_gcs_client()` が毎回サービスアカウントファイルを読み込む

- 箇所: `scripts/save_backlog_record.py:18-23`
- 現状: `main()` から1回だけ呼ばれているので実害はないが、関数として設計する場合は module-level で singleton 化する慣習（004 §D-2）に合わせる
- 提案: 関数定義を維持するなら `@functools.lru_cache(maxsize=None)` デコレータを付ける。または `main()` のスコープ内でのみ使うことを docstring に明記する

### #3 型ヒントの `dict | None` は Python 3.10+ 構文

- 箇所: `scripts/save_backlog_record.py:45`
- 現状: `def find_latest_period(periods: list[dict]) -> dict | None:` は Python 3.10 以降の union 型表記
- 提案: `.python-version` で 3.14 が指定されているため問題なし。ただし `dict` 型は `dict[str, Any]` と具体化すると可読性が上がる（型ヒント精度の向上）

### #4 `extracted_files` フィルタの ticker マッチが前方一致のみ

- 箇所: `scripts/save_backlog_record.py:110`
- 現状: `f.stem.startswith(args.ticker + "_")` で前方一致フィルタ。ticker `135A` 指定時に `135A_extracted.json` を正しく拾う
- 提案: 現行実装で基本的に問題なし。ただし ticker が `1` 等の短い値の場合に `1_extracted.json` もヒットする。実用上 ticker は4桁以上なので問題は低いが、完全一致 `f.stem == f"{args.ticker}_extracted"` の方が安全

### #5 `upsert_record()` は既存リストを直接変更して返す（副作用あり）

- 箇所: `scripts/save_backlog_record.py:82-90`
- 現状: `existing_records[i] = new_record` でインプレース変更し、かつ同じリストを `return` する。呼び出し元では `existing["records"] = upsert_record(...)` で戻り値を代入するため機能的には問題ないが、副作用あり関数として設計するか純粋関数として設計するか一貫性がない
- 提案: `return` を活用するなら `existing_records.copy()` を作って返す純粋関数にする。副作用として直接変更するなら戻り値を `None` にする。現状はどちらでもない混在設計

### #6 dry-run 時の表示が `saved < 5` の場合のみ

- 箇所: `scripts/save_backlog_record.py:148`
- 現状: `if saved < 5:` で先頭5件のみ詳細表示。それ以降は `saved` カウントのみ増加
- 提案: 5件制限の理由が不明。dry-run 時は全件詳細表示するか、`--verbose` フラグで制御する設計が望ましい。また、skipped/error の詳細（どの ticker が skipped か）も表示する方が診断に有用

---

## 【修正例】（必要な箇所のみ）

#### #1 (A-1違反: exit code) に対する修正案

```python
# before: scripts/save_backlog_record.py:163-166 — 末尾のprint後にreturn
    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}結果:")
    print(f"  保管: {saved}社")
    print(f"  スキップ: {skipped}社 (false/メトリクスなし/データなし)")
    print(f"  エラー: {errors}社")

# after — sys.exit追加
    logger.info(
        "完了",
        dry_run=args.dry_run,
        saved=saved,
        skipped=skipped,
        errors=errors,
    )
    sys.exit(1 if errors else 0)
```

#### #6 (adapter_version空文字) に対する修正案

```python
# before: scripts/save_backlog_record.py:26-42
def load_structure_metrics(ticker: str) -> set[str]:
    ...
    return {
        m["name"]
        for m in v.get("metrics", [])
        if m.get("type") in ("order_intake", "backlog")
    }

# after: 戻り値を (metrics, version) のタプルに変更
def load_structure_metrics(ticker: str) -> tuple[set[str], str]:
    """現行structure.jsonからorder_intake/backlogメトリクス名と現行バージョンを取得."""
    path = STRUCTURE_DIR / f"{ticker}_structure.json"
    if not path.exists():
        return set(), ""
    data = json.loads(path.read_text(encoding="utf-8"))
    current_ver = data.get("current_version", "")
    for v in data.get("versions", []):
        if v.get("valid_from") == current_ver:
            if not v.get("data_available", True):
                return set(), current_ver
            return (
                {
                    m["name"]
                    for m in v.get("metrics", [])
                    if m.get("type") in ("order_intake", "backlog")
                },
                current_ver,
            )
    return set(), current_ver

# 呼び出し元(main内)も対応:
# before: valid_metrics = load_structure_metrics(ticker)
# after:  valid_metrics, adapter_version = load_structure_metrics(ticker)
# build_record() の引数に adapter_version を追加して渡す
```

---

## 【確認できなかった事項】

- `periods` 配列内の `period` フィールドの実際のフォーマット統一性: 複数企業・複数期間のextracted JSONを確認しないと、`"2026年3月期"` vs `"2025-4Q"` 等の混在が実際に起きているか不明。設計上は両方の可能性がある
- `extracted.get("_source_pdf", "")` が空文字になる実ケースの頻度: 抽出処理でこのフィールドが保証されているかは `extract_order_backlog.py` のコードを確認しないと判定不能
- Cloud Run Job として本スクリプトを実行する計画の有無: ローカルスクリプトとして使い続けるならパスのハードコードの緊急度は低い（ただし規約違反は残る）
- `settings.google_application_credentials` の実装場所: プロジェクトに `src/core/config.py` 等が存在し `Settings` クラスが定義されているかを未確認
