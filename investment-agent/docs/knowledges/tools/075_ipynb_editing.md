# Jupyter Notebook (.ipynb) プログラム的編集ノウハウ

**カテゴリ**: tools
**作成日**: 2026-04-13
**ステータス**: 有効

## 概要

Claude Code から `.ipynb` ファイルをプログラム的に編集する際の落とし穴と対策。Read/Edit ツールではトークン超過で読めないことが多く、Python スクリプトで JSON 操作する必要がある。

## .ipynb の構造

```json
{
  "cells": [
    {
      "cell_type": "code",
      "source": ["line1\n", "line2\n", "last_line"],
      "metadata": {},
      "outputs": []
    }
  ]
}
```

- `source` は **文字列の配列**（各要素が1行、末尾行以外は `\n` 付き）
- `"".join(cell["source"])` で結合して編集し、書き戻し時に分割する

## 落とし穴

### 1. fstring 内の BQ バッククォートが Python 構文エラーになる

**症状**: ヒアドキュメント (`<< 'EOF'`) や raw string 内に BQ クエリの `` `project.dataset.table` `` があると、Python パーサーが数値リテラルと誤認して `SyntaxError: invalid decimal literal`

**対策**: 置換テキストは **外部テキストファイルに分離**して `Path.read_text()` で読み込む。Python ソースコード内に BQ クエリを直接埋め込まない

```python
# ❌ SyntaxError になる
new_code = """
q = f"SELECT * FROM `proj.dataset.table` WHERE ..."
"""

# ✅ 外部ファイルから読み込む
new_code = Path("C:/tmp/new_cell_code.txt").read_text(encoding="utf-8")
```

### 2. bash ヒアドキュメントとの引用符競合

**症状**: `bash << 'PYEOF' ... PYEOF` 内に Python の三重引用符・fstring・エスケープが混在すると、bash パーサーが予期しない箇所で引用符を閉じてしまう

**対策**: 編集スクリプトは **独立した .py ファイル** (`C:/tmp/update_xxx.py`) として Write → Bash で実行。bash ヒアドキュメントは使わない

```python
# ❌ bash ヒアドキュメント内で Python を書く
Bash("PYTHONUTF8=1 python << 'EOF'\n...\nEOF")

# ✅ 独立 .py ファイルを書いて実行
Write("C:/tmp/edit_nb.py", script_content)
Bash("PYTHONUTF8=1 python C:/tmp/edit_nb.py")
```

### 3. Unicode エスケープ問題

**症状**: 日本語文字列を Python ソースに直接書くと、JSON シリアライズ時にエスケープされる場合がある

**対策**: 日本語文字列は Unicode エスケープ (`\uXXXX`) で書くか、外部ファイルから読む。`json.dump(..., ensure_ascii=False)` を必ず指定

### 4. source 配列の書き戻しフォーマット

**症状**: 結合→編集→書き戻し時に改行が崩れる

**対策**:
```python
lines = edited_src.split("\n")
cell["source"] = [line + "\n" for line in lines[:-1]] + [lines[-1]]
```

末尾行には `\n` を付けない。空行で終わる場合は最後の要素が空文字列 `""` になる。

### 5. Read ツールでのトークン超過

**症状**: `Read` ツールで `.ipynb` を読もうとすると `exceeds maximum allowed tokens` エラー

**対策**: Python スクリプトで特定セルのみ抽出して表示

```python
import json
with open("path.ipynb", encoding="utf-8") as f:
    nb = json.load(f)
# セル一覧
for i, c in enumerate(nb["cells"]):
    print(f'{i}: [{c["cell_type"]}] {"".join(c["source"])[:60]}')
# 特定セルのソース
print("".join(nb["cells"][5]["source"]))
```

### 6. 文字列検索による置換位置の特定

**症状**: `str.replace()` で意図しない箇所が置換される、または一意でなくて失敗

**対策**: `str.index()` でマーカー文字列の位置を特定し、スライスで切り出して置換

```python
i_start = src.index("# ── セクション開始 ──")
i_end = src.index("# ── セクション終了 ──") + len("# ── セクション終了 ──")
src = src[:i_start] + new_content + src[i_end:]
```

## 編集スクリプトのテンプレート

```python
"""ノートブック編集スクリプト."""
import json
from pathlib import Path

NB_PATH = "C:/gdrive/claude/investment-agent/scripts/xxx/yyy.ipynb"

with open(NB_PATH, encoding="utf-8") as f:
    nb = json.load(f)

# セルのソースを結合
cell_src = "".join(nb["cells"][N]["source"])

# 置換（外部ファイルから読み込む場合）
new_block = Path("C:/tmp/new_block.txt").read_text(encoding="utf-8")
marker_start = "# ── 置換開始 ──"
marker_end = "print('置換終了')"
i_start = cell_src.index(marker_start)
i_end = cell_src.index(marker_end) + len(marker_end)
cell_src = cell_src[:i_start] + new_block + cell_src[i_end:]

# 書き戻し
lines = cell_src.split("\n")
nb["cells"][N]["source"] = [line + "\n" for line in lines[:-1]] + [lines[-1]]

# 保存
with open(NB_PATH, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
```

## 検証チェックリスト

編集後に必ず実施:

```python
# 全因子・全キーワードの存在確認
cell_src = "".join(nb["cells"][N]["source"])
for kw in ["Factor 10", "has_buyback", ...]:
    assert kw in cell_src, f"MISSING: {kw}"
```
