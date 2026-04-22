# matplotlib グラフ表示（Windows ローカル）

**カテゴリ**: tools
**作成日**: 2026-03-08
**ステータス**: 有効
**関連ファイル**: なし（パターンとして記録）

---

## 概要

Claude Code（Windows ネイティブ）から matplotlib でグラフを表示する方法。
tkinter が未インストールのため `TkAgg` バックエンドは使えない。
PNG 保存 → `explorer` で開く方式が安定動作する。

---

## 動作確認済み環境

- OS: Windows 10
- Python: 3.12（`C:\Users\zonekun\AppData\Local\Programs\Python\Python312\python.exe`）
- matplotlib インストール済み
- **tkinter は未インストール**（`ModuleNotFoundError: No module named 'tkinter'`）

---

## 正しいパターン（PNG保存 → explorer で開く）

```python
import matplotlib
matplotlib.use("Agg")  # ← 必須。tkinter不要のバックエンド
import matplotlib.pyplot as plt
import subprocess, os

# 日本語フォント設定（Yu Gothic を使用）
plt.rcParams['font.family'] = 'Yu Gothic'

# --- グラフ描画 ---
fig, ax = plt.subplots(figsize=(12, 6))
# ... ax.plot(...) etc.
fig.tight_layout()

# PNG保存 → Windowsの標準ビューアで開く
outpath = os.path.join(os.environ.get("TEMP", "C:/Temp"), "chart.png")
fig.savefig(outpath, dpi=150)
subprocess.Popen(["explorer", outpath])
```

---

## 利用可能な日本語フォント（確認済み）

| フォント名 | 備考 |
|-----------|------|
| `Yu Gothic` | 推奨。Windows 10標準搭載 |
| `Meiryo` | 標準搭載 |
| `MS Gothic` | 標準搭載 |
| `Noto Sans JP` | インストール済み |

```python
plt.rcParams['font.family'] = 'Yu Gothic'
```

---

## NG パターン

```python
# ❌ tkinter未インストールのためエラーになる
matplotlib.use("TkAgg")

# ❌ plt.show() はGUIウィンドウを開こうとするが、
#    Aggバックエンドでは何も起きない（エラーも出ない）
plt.show()

# ❌ 別コンソールを start で開いても、スクリプト終了と同時にウィンドウが閉じる
start "" python.exe plot.py  # → すぐ閉じる
```

---

## 実行方法

Claude Code から実行する場合:

```python
# スクリプトファイルに書いて実行
PYTHONUTF8=1 /C/Users/zonekun/AppData/Local/Programs/Python/Python312/python.exe /path/to/plot.py 2>&1 | grep -v "UserWarning\|Glyph\|missing from"
```

> `grep -v "UserWarning..."` で日本語フォントの警告ログを抑制できる
> （フォントを正しく設定していれば実際には警告は出ない）

---

## FRED データ取得 → グラフ表示の実例

```python
import urllib.request, json

API_KEY = "d65e47bf3adc90feda8f3fb5ad3c75e5"  # FRED API Key

url = (
    f"https://api.stlouisfed.org/fred/series/observations"
    f"?series_id=UNRATE&api_key={API_KEY}&file_type=json"
    f"&observation_start=2024-01-01&frequency=m"
)
with urllib.request.urlopen(url) as r:
    obs = json.loads(r.read())["observations"]
```

FRED API Key は Cloud Run `fred-mcp` サービスの環境変数から取得可能:
```bash
gcloud run services describe fred-mcp --region us-west1 --format "value(spec.template.spec.containers[0].env)"
```
