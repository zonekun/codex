# CR-112: extract_monthly_data.py — monthly_kw_override + html gemini_multi_month

- **対象**: `scripts/extract_monthly_data.py`
- **レビュー日**: 2026-05-08
- **品質**: A

## 変更概要

1. **monthly_kw_override**: PDF blob フィルタで adapter の `monthly_kw_override` キーがあればデフォルト `_MONTHLY_KW` の代わりに使用（8511 日本証券金融向け）
2. **_extract_html_gemini_all_months()**: html_table ソースで `gemini_multi_month: true` の累積型 HTML から全月データを一括抽出。`_extract_pdf_gemini_all_months` の HTML 版。call site 2箇所（ローカル/GCS）+ batch_mode 対応

## 判定

| # | 種別 | 内容 | 重要度 |
|---|------|------|--------|
| 1 | Warning | batch mode の `_build_batch_request_obj` は multi-month HTML でも 20K 文字制限。非batch は 40K | medium |
| 2 | Warning | `re.compile(monthly_kw_override)` に try/except なし | low |
| 3 | Note | batch prompt が HTML multi-month でも "PDF" と表記（既存問題） | cosmetic |
| 4 | Note | PDF版 `_extract_pdf_gemini_all_months` は `gemini_custom_prompt` のみ参照。HTML版は `custom_prompt` も参照（正しい挙動、既存不整合） | pre-existing |

## 回帰リスク

なし。両変更とも adapter キー不在時は元のコードパスを通る（`monthly_kw_override` → `_MONTHLY_KW`、`gemini_multi_month` なし → `_extract_html_gemini_personal`）
