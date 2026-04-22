# 四季報読み込みユーティリティ（shikiho_reader.py）

**カテゴリ**: tools
**作成日**: 2026-03-11
**ステータス**: 有効
**関連ファイル**: `scripts/shikiho_reader.py`, `scripts/kiyohara_screening.py`

## 概要

四季報 Excel からコード→会社名・セクター等を引ける汎用モジュール。
他スクリプトから `from shikiho_reader import get_company_name, get_company_names` でインポートして使う。

## 使い方

### 他スクリプトからインポート

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shikiho_reader import get_company_name, get_company_names, load_shikiho

# 単一コード
name = get_company_name("7203")  # → "トヨタ自動車"

# 複数コード
names = get_company_names(["7203", "6758", "9984"])  # → {"7203": "トヨタ...", ...}

# 全データ（DataFrame）
df = load_shikiho()
```

### 単体実行（動作確認）

```bash
PYTHONUTF8=1 uv run python scripts/shikiho_reader.py
```

## 四季報 Excel の仕様

- **パス**: `C:\Users\zonekun\Dropbox\stock\DA_四季報_2026_2.xlsx`
- **シート名**: `list`
- **主要カラム**: コード, 名前, セクター, URL, 特色, 連結事業, プロフィール, 記事１, 記事２, 株式数, ...
- **コード形式**: 数値 or 文字列で格納。読み込み時に `str.zfill(4)` で4桁文字列に正規化

## 四季報更新時の手順

新しい四半期版（例: 2026年2集）が届いたら:
1. `scripts/shikiho_reader.py` の `SHIKIHO_EXCEL` 定数を更新
2. `scripts/kiyohara_screening.py` の `SHIKIHO_EXCEL` 定数も同様に更新
3. `data_catalog.md` のファイル名記載を更新

## 注意事項

- `load_shikiho()` はモジュールレベルでキャッシュするため、同一プロセス内での再読み込みは発生しない
- コード `"8975"` 等、四季報に未登録の銘柄は `get_company_name()` が空文字列を返す
- 四季報は国内上場株のみ。グロース銘柄（`141A` 等のアルファベット付き）も含まれる場合がある（要確認）
