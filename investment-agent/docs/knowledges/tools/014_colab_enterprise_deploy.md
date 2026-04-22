# Colab Enterprise へのノートブックデプロイ方法

**カテゴリ**: tools
**作成日**: 2026-03-01
**ステータス**: 有効
**関連ファイル**: `scripts/*.py`

## 概要

Python スクリプトを Colab Enterprise にアップロードし、Colab Enterprise UI から手動実行する方法。

## 仕組み

Colab Enterprise のノートブックは **GCS または Google Drive** にファイルを置くだけで開ける。「デプロイ」という概念はなく、`.ipynb` を所定の場所にアップロードしておけばよい。

## 手順

### 1. `.py` をラップする `.ipynb` を作成

Colab Enterprise は `.py` を直接開けないため、`.py` を呼び出すだけの1セルノートブックを作る。

```python
# nbformat でラッパーノートブックを生成するスクリプト例
import nbformat

nb = nbformat.v4.new_notebook()
nb.cells = [
    nbformat.v4.new_code_cell("!PYTHONUTF8=1 python scripts/tdnet_load.py")
]
with open("notebooks/tdnet_load.ipynb", "w", encoding="utf-8") as f:
    nbformat.write(nb, f)
```

または `jupytext` を使って `.py` → `.ipynb` 変換も可能。

### 2. GCS へアップロード（コンソールから）

```bash
gcloud storage cp notebooks/tdnet_load.ipynb gs://stock_data_1930932/notebooks/
```

### 3. Colab Enterprise UI で開く

Colab Enterprise → 「ファイルを開く」→ GCS タブ → `gs://stock_data_1930932/notebooks/tdnet_load.ipynb` を選択 → 手動実行。

## 注意事項

- **対象は `.ipynb` のみ**。`.py` を直接 Colab Enterprise で開くことはできない
- **認証は自動**。Colab Enterprise ランタイムは ADC（Application Default Credentials）が有効なため、本プロジェクトの `detect_runtime()` が `"colab_enterprise"` を返し、追加認証不要で GCP にアクセスできる
- **Google Drive への保存も可能**。Drive API や `gdrive` CLI でアップロードすれば Drive 経由でも開ける（GCS の方がシンプル）
- **スケジュール実行も可能**（今回は不要のため省略）

## Cloud Run Jobs との使い分け

| 用途 | 推奨 |
|------|------|
| 定期バッチ（自動） | Cloud Run Jobs |
| 手動実行・ad-hoc | Colab Enterprise（本手順） |
| インタラクティブ分析 | Colab Enterprise |
