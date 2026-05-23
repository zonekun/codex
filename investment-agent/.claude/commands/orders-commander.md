skills/orders_commander.md（受注高抽出コマンダー）を Read し、その手順に従って実行せよ。

## 入力受け渡し規約

引数:
- `mode`: `build` または `resume`（必須）
- `parallel`: 同時起動するソルジャー数（任意。デフォルト `3`、上限なし）

呼び出し例:
- スラッシュコマンド: `/orders-commander mode=build parallel=3`
- 位置引数: `/orders-commander build 3`
- Agent prompt 直書き: `skills/orders_commander.md の手順を mode=resume parallel=5 で実行せよ`

**引数解釈の正本はスキル本文 §入力 §「引数解釈の手順」**。本ラッパーは委譲のみで独自解釈しない。

実行冒頭に `🎯 [orders-commander] mode={mode} parallel={parallel}` を 1 行出力すること（CLAUDE.md §8 規約）。

## 役割境界

- 本スキルは **オーケストレータ**: 対象 ticker 一覧の管理、ソルジャー並列起動、結果の集約・インデックス更新、クラッシュリカバリ
- 実働（PDF 読み・JSON 生成）は **受注高抽出ソルジャー**（`skills/orders_soldier.md` / `/orders-soldier`）が担当
- ソルジャーは必ず **Agent ツール経由で起動**（直接 Read/Write/Bash で代行しない）

## 戻り値

skills/orders_commander.md §Step 5 の最終レポートフォーマット
（MODE / PARALLEL / TOTAL / COMPLETED / COMPLETED_PARTIAL / FAILED_* 4 種 / PENDING_REMAINING /
INDEX_PATH / LOG_PATH / ELAPSED）を返却すること。
