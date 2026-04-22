# convert_bond_history.py の使い方

**カテゴリ**: tools
**作成日**: 2026-02-23
**ステータス**: 有効
**関連ファイル**: `scripts/convert_bond_history.py`, `data/csv/bond_history.csv`, `data_catalog.md`

## 概要

`BB_債券履歴_new.xlsx`（シート: LIST）を読み取り、`data/csv/bond_history.csv` に変換するスクリプト。

## 実行方法

```bash
# プロジェクトルートから実行
PYTHONUTF8=1 python scripts/convert_bond_history.py
```

> `PYTHONUTF8=1` 必須。省略すると日本語パスで UnicodeEncodeError が発生する（Windows cp1252 問題）。

## 実行タイミング

**明示的な指示があったときのみ実行する**（自動・定期実行しない）。

## 処理内容

1. `C:\Users\zonekun\Dropbox\stock\BB_債券履歴_new.xlsx`（シート: LIST）を読み込む
2. 1行目をヘッダー、2〜5行目をスキップ、6行目以降をデータとして扱う
3. 必要な20列のみ抽出・リネーム（詳細は `data_catalog.md` のスキーマ参照）
4. USDJPY・USDX は小数点以下2桁に丸める
5. DATE列を日付型に変換、無効行を除去、昇順ソート
6. `data/csv/bond_history.csv` に UTF-8 で保存

## 直近90日の扱い

実行日から遡って90日以内のデータはクレンジング未完了の可能性あり。
スクリプトは警告を表示するが、データ自体は保持する（分析時に除外することを推奨）。

## 実行後にすること

`data_catalog.md` の「更新履歴」テーブルに1行追記する。

```markdown
| YYYY-MM-DD | X,XXX件 | YYYY-MM-DD |
```

## 注意事項

- uv の `.venv` は uv トランポリン方式のため `uv` 本体がないと動作しない
- この端末ではシステム Python（`python` コマンド）を使用する
- `uv` は `C:\Users\Administrator\.local\bin\uv.exe` にインストール済みだが、Git Bash のデフォルト PATH には含まれていない
