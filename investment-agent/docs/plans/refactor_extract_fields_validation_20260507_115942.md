# extract_monthly_data.py — fields=[] アダプタのサイレントスキップ防止

**作成日時**: 2026-05-07 12:00 JST
**対象ファイル**: `scripts/extract_monthly_data.py`（4238 行、commit `6dd20b7` 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: `fields=[]` のアダプタでextract実行時にエラーが出ない設計不備を修正する。adapterの必須フィールド欠如を早期検出し、明示的にエラーログ+error_entryとして記録する。スコープ: phase_extract() 内のアダプタ検証ロジック追加。非スコープ: 個別抽出関数の修正、adapter再生成ロジック。

---

## 前提サマリ

- 過去修正: INDEX整合チェックで fields=[] のアダプタ60社を検出（2026-05-07 本セッション）
- 残存: 本プランで扱う — fields=[] アダプタがextractをサイレントに素通りする設計不備 1件
- 実機検証の有無: 未検証（`--limit 1` で再現可能）
- 関連 incident: 60社が「no_records」としてスキップ扱いされ、adapter設定不備か抽出失敗かの区別不能

---

## 優先度の定義

- **P0**: fields=[] の adapter が no_records に紛れてサイレントスキップする。月次パイプラインの品質保証ブロッカー（未設定adapterが正常処理に見える）
- **P1**: error_type の細分化（現在 "no_records" が全パターンを兼用）

---

## 指摘項目

### P0-1. phase_extract() adapter 検証: fields=[] のサイレントスキップ 🚨

**症状**: `fields=[]` のアダプタで extract を実行すると、各抽出関数（`extract_from_text_gemini`, `_extract_pdf_by_column` 等）が `return None` または空ループで何も返さず、最終的に L4059 `if not records:` で `error_type="no_records"` としてスキップ。ログ上は「PDF が見つからなかった」場合と区別不能。

**該当**: `scripts/extract_monthly_data.py:L3318-L3326` / `phase_extract()`

```python:L3318-L3326
        # 論理削除フラグ: _excluded=true の銘柄は管理外として処理しない
        if adapter.get("_excluded") or adapter.get("excluded"):
            reason = adapter.get("_excluded_reason") or adapter.get("excluded_reason", "(reason 未記載)")
            logger.info(f"  → _excluded=true → 管理対象外スキップ: {reason}")
            results.setdefault("excluded", []).append(ticker)
            continue

        records: list[dict] = []
        extraction_method = adapter.get("extraction_method", "regex")
```

**根本原因**: adapter ロード直後に `fields` の存在・非空チェックがない。`_excluded` チェック(L3318)の後に即 extraction_method 取得(L3326)に進んでいる。fields が empty list の場合の分岐が存在しない。004 アンチパターン B-1「入力検証なしで処理続行」に該当。

**修正方針**: `_excluded` チェックの後、`extraction_method` 取得の前にフィールド検証を挿入。2パターンの検証を行う:

1. **fields=[]**: adapter.fieldsが空 → adapter_no_fields でスキップ
2. **fields不足**: structure.json に metrics 定義があるが adapter.fields でカバーされていない → 同じく adapter_no_fields でスキップ

structure.json は GCS `monthly/meta/{ticker}/structure.json` またはローカル `meta/monthly/{ticker}_structure.json` から読み込む。

**実装済み**: 2026-05-07 14:20 JST、L3325付近に挿入。コンパイルOK。設計変更（16:30 JST）: パターン2を会社スキップ→項目レベル警告のみに修正（ユーザー指示「スキップするのは項目のみ」）。error_type=`adapter_fields_incomplete`。

**呼び出し側への波及**:
- `scripts/extract_monthly_data.py:L4059-L4067` — `error_type="no_records"` のケースが減少。集計に影響なし（追加 error_type を出力するだけ）
- `scripts/validate_monthly_first_run.py` — error_entry を読む場合に新 error_type `adapter_no_fields` を認識する必要あり（ただし現在は error_type で分岐するロジックなし）
- 無し（他ファイルへの波及なし）

**検証**: `PYTHONUTF8=1 python scripts/extract_monthly_data.py --ticker 1419 --since 2026 --no-gcs` で fields=[] アダプタが `adapter_no_fields` error_type でスキップされることを確認

**ロールバック**: commit revert で済む。データ破壊なし（skip 動作の分岐変更のみ）

---

### P1-1. error_type 細分化の文書化 ⚠️

**症状**: `error_type="no_records"` が「PDFなし」「fields定義なし」「regex不一致」「Gemini応答パースエラー」等の全パターンを兼用しており、monthly-error-autofix の自動分類で「何が原因か」を判別できない。

**該当**: `scripts/extract_monthly_data.py:L4059-L4067`

```python:L4059-L4067
        if not records:
            _elapsed = time.perf_counter() - _company_t0
            logger.info(f"  → 抽出レコードなし → スキップ (elapsed={_elapsed:.1f}s)")
            results["skip"].append(ticker)
            _error_entries.append({
                "ticker": ticker, "error_type": "no_records",
                "error_detail": "抽出レコード0件", "elapsed": round(_elapsed, 1),
            })
            continue
```

**根本原因**: error_type の vocabulary が設計されていない。既存コードで `no_records` / `batch_failed` / `parse_error` 等が散在しているが体系的定義がない。

**修正方針**: P0-1 で `adapter_no_fields` を追加するのが第一歩。将来的に error_type を enum 化することは本プランのスコープ外とする（知見MD `042-1_monthly_error_fix_patterns.md` に error_type 定義表を追記する形で対応）。

**呼び出し側への波及**:
- `docs/knowledges/tools/042-1_monthly_error_fix_patterns.md` — 新 error_type `adapter_no_fields` のパターン追加が必要
- 無し（コード上の呼び出し側変更なし）

**検証**: P0-1 の検証と同時に確認可能

**ロールバック**: commit revert で済む

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | B-1 (入力検証なし) | — | — |
| P1-1 | — | — | — |

> 参照: `docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集

---

## 検証戦略

1. **smoke test**: `--ticker 1419 --since 2026 --no-gcs` で 1419 (fields=[]) が `adapter_no_fields` としてスキップされる。ログに `adapter fields=[]` 警告あり。正常アダプタ (e.g. 2652) は従来通り抽出成功することを確認
2. **dev 実機**: `--since 2026` で全社実行。fields=[] の60社が skip(adapter_no_fields) に分類され、従来の success/fail/skip カウントが整合することを確認。GCSへの書き込みなし（`--no-gcs`）
3. **本番適用判断基準**: smoke + dev 両方で (a) 正常アダプタの抽出結果が変わらない (b) fields=[] が明示的 error_type で区別される の2点を確認後に本番適用
4. **回収手順**: 検証段階のため `--no-gcs` 必須。本番 GCS データへの影響なし。万一問題あれば commit revert のみ

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/042_monthly_disclosure_master.md`（月次パイプライン全体像）
- 知見 MD: `docs/knowledges/tools/042-1_monthly_error_fix_patterns.md`（エラーパターンDB）
- 関連 commit: `6dd20b7` — 直近 HEAD
- 関連 incident: INDEX整合チェック（2026-05-07 本セッション）で60社の fields=[] アダプタを検出

---

## 提出前セルフチェック（必須）

- [x] 冒頭に基準 commit hash があるか
- [x] 全項目が 7 フィールド（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）を揃えているか
- [x] 修正方針に before/after の両方があるか
- [x] 呼び出し側への波及が行番号リストで明示されているか（「影響あり」等の曖昧表現は不可）
- [x] 対応アンチパターン表が末尾にあるか（該当なしでも「該当なし」テーブルを明示）
- [x] 検証戦略が smoke / dev / 本番適用判断基準 / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順があるか（破壊的修正時は必須）
- [x] 「既に〜がある」系の前提を実コードで Read 確認したか

---

## レビュー追記: 2026-05-07 12:02 JST — code-reviewer

→ `docs/reviews/094_cr_extract_fields_validation.md`

---

## レビュー追記: 2026-05-07 15:56 JST — code-reviewer

→ `docs/reviews/098_cr_extract_fields_validation_impl.md`（パターン2: 実装済みコードレビュー。重大指摘 #1: structure.json metrics 突合ロジックが正常アダプタをスキップするリグレッション）
