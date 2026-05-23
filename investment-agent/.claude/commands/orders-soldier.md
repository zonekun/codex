skills/orders_soldier.md（受注高抽出ソルジャー）を Read し、Agent ツールで独立実行せよ。

## 入力受け渡し規約

引数として渡された ticker（4 桁英数。例: `7011`, `142A`）1 銘柄分の JSON を生成し、
結果報告のみ呼び出し元に返す。

呼び出し例:
- スラッシュコマンド: `/orders-soldier 7011` （第一引数を ticker として解釈）
- Agent prompt 直書き: `skills/orders_soldier.md の手順を ticker=7011 で実行せよ`

実行冒頭に `🎯 [orders-soldier] ticker={ticker}` を 1 行出力すること（CLAUDE.md §8 規約）。

## 呼び出し元（受注高抽出コマンダー）の責任

- pending リスト / `_failed.csv` / completed リストの更新は呼び出し元の責任
- 本スキルは戻り値（STATUS / DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA / JSON_PATH / reason）のみ返す
- 複数銘柄を一度に渡してはならない（1 起動 = 1 銘柄）

## 戻り値

skills/orders_soldier.md §Step 8 §STATUS 一覧 のフォーマットに従う。
6 STATUS（completed / completed_partial / failed_no_bq_records / failed_no_gcs_files /
failed_pdf_unreadable / failed_no_data）のいずれかを返却すること。
