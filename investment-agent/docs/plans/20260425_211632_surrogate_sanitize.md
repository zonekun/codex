# PDF テキスト抽出時のサロゲート文字除去

**作成日時**: 2026-04-25 21:16 JST
**対象ファイル**: `scripts/tdnet_load_parallel.py`（2,600行、commit 8b42eb1 時点）
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: PyPDF2/pdfminer の CMap デコーダが生成する不正サロゲート文字（U+D800〜U+DFFF）を抽出直後に除去し、後段の JSON シリアライズ（`_save_ai_prepare_state`）での `UnicodeEncodeError` を防止する。スコープは `_normalize_page_text` への 1 行追加のみ。PDF 抽出ロジック・state.json フォーマット・後段 Gemma パイプラインは変更しない。

---

## 前提サマリ

- 過去修正: なし（新規バグ）
- 残存: 本プランで 1 件対処
- 実機検証: prod で再現済み（2024 gap backfill Run2 Q3、Workflow `0a9be078`、2026-04-25 17:51-19:10 JST）
- 関連 incident: `tdnet-ai-prepare-28cd5` が exit code 1 で 2 回失敗（初回 09:32 UTC / retry 10:10 UTC）。エラー: `UnicodeEncodeError: 'utf-8' codec can't encode characters in position 2759-2760: surrogates not allowed`

---

## 優先度の定義

- **P0**: バックフィル Run2 Q3 の再実行をブロックしている

---

## 指摘項目

### P0-1. `_normalize_page_text` でサロゲート文字を除去していない 🚨

**症状**: `_save_ai_prepare_state()` L1701 の `tmp.write(json.dumps(doc_obj, ensure_ascii=False))` で `UnicodeEncodeError: surrogates not allowed` が発生し、ai-prepare ジョブが全件失敗する。

**該当**: `scripts/tdnet_load_parallel.py:L369-L386` / `_normalize_page_text()`

```python:L369-L386
def _normalize_page_text(page_text: str) -> str:
    """ページ内の連続する空白・タブを単一スペースに圧縮。改行は保持する。"""
    lines = []
    for line in page_text.split("\n"):
        collapsed = _INLINE_WS_PATTERN.sub(" ", line).strip()
        lines.append(collapsed)
    result_lines: list[str] = []
    prev_empty = False
    for line in lines:
        if line == "":
            if not prev_empty:
                result_lines.append("")
            prev_empty = True
        else:
            result_lines.append(line)
            prev_empty = False
    return "\n".join(result_lines).strip()
```

**根本原因**: PyPDF2/pdfminer の CMap デコーダが BMP 外文字のサロゲートペアを個別コードポイントとして Python str に格納する。`_normalize_page_text` は空白正規化のみでサロゲートを素通しさせるため、後段の UTF-8 エンコード（`json.dumps(ensure_ascii=False)` → `tmp.write()`）で爆発する。既存アンチパターン分類には直接該当なし（入力データの不正文字サニタイズの問題）。

**修正方針**: `_normalize_page_text` の冒頭で `re.sub(r'[\ud800-\udfff]', '', page_text)` を適用。サロゲートは PDF テキストとして意味のある情報を持たないため、置換ではなく除去（空文字）で問題ない。

```python
# before
def _normalize_page_text(page_text: str) -> str:
    """ページ内の連続する空白・タブを単一スペースに圧縮。改行は保持する。"""
    lines = []

# after
_SURROGATE_RE = re.compile(r'[\ud800-\udfff]')

def _normalize_page_text(page_text: str) -> str:
    """ページ内の連続する空白・タブを単一スペースに圧縮。改行は保持する。"""
    page_text = _SURROGATE_RE.sub('', page_text)
    lines = []
```

**呼び出し側への波及**:
- `_extract_text_pypdf2` L402 — 変更なし（`_normalize_page_text` 呼び出し済み）
- `_extract_text_pdfminer` L433 — 変更なし（同上）
- `phase2_vision_batch` L805 — Gemini Vision 出力は `_normalize_page_text` を経由しないが、Gemini API が返す UTF-8 テキストにサロゲートは含まれないため対処不要

**検証**: Cloud Build + `py_compile` → 1 件 PDF で `_normalize_page_text` 通過後のテキストが valid UTF-8 であることを確認（`text.encode('utf-8')` が例外を出さない）。その後 Q3 バックフィル再実行。

**ロールバック**: コミット revert で済む。サロゲート除去は情報損失だが、元々 BMP 外文字の断片（不正データ）であり実用上の影響なし。

---

## 対応アンチパターン

| plan ID | 004 | T-x | G-x |
|---|---|---|---|
| P0-1 | — | — | — |

> 既存アンチパターン分類には直接該当なし。PDF ライブラリ由来の不正文字混入は新規カテゴリ。

---

## 検証戦略

1. **smoke test**: `py_compile` + サロゲート含有テストデータで `_normalize_page_text` 単体呼び出し → 出力が `str.encode('utf-8')` 可能であること
2. **dev 実機**: Cloud Build → Q3 バックフィル再実行（`date_from=20240701, date_to=20240930`）。state.json が GCS に正常保存されることを確認
3. **本番適用判断基準**: Q3 Workflow が SUCCEEDED になること
4. **回収手順**: revert commit → 再ビルド → 再実行。state.json は上書きされるためデータ補正不要

---

## 関連ドキュメント

- 知見 MD: `docs/knowledges/tools/013_tdnet_load.md`（親知見）
- 関連 incident: Workflow `0a9be078-5ed2-41a8-9ab2-b5326cd32021` (FAILED)、execution `tdnet-ai-prepare-28cd5`
- バックフィルプラン: `docs/plans/20260425_001000_tdnet_2024_gap_backfill.md`
- フォーマット正本: `skills/planning.md` §改修プラン / バグ修正指示書 MD フォーマット
