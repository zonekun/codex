---
name: Google Colab（無料枠）開発ノウハウ
description: 無料版 Google Colab でノートブックを開発・実行する際の知見。日本語フォント・認証・キャッシュ戦略等
type: tools
作成日: 2026-04-02
更新日: 2026-04-02
ステータス: 有効
関連ファイル:
  - scripts/earnings_model/earnings_model_eda.ipynb
  - docs/knowledges/tools/007_cross_platform_python.md
  - docs/knowledges/tools/059_earnings_model_eda.md
---

## 概要

Google Colab（無料枠）で本プロジェクトのノートブックを開発・実行する際のノウハウ集。
クロスプラットフォーム設計パターン自体は `007_cross_platform_python.md` を参照。本ファイルは Colab 固有の知見に特化する。

---

## 1. 日本語フォント表示（matplotlib）

Colab にはデフォルトで日本語フォントがインストールされていない。matplotlib で日本語ラベル・凡例を使うと **□（豆腐）** になる。

### 解決策

`japanize-matplotlib` パッケージを使う。IPAexGothic フォントを自動ダウンロード・設定してくれるため、apt-get やフォントキャッシュ再構築が不要:

```python
import matplotlib
try:
    import japanize_matplotlib
except ImportError:
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'japanize-matplotlib'], capture_output=True)
    import japanize_matplotlib
matplotlib.rcParams['axes.unicode_minus'] = False
```

**注意点**:
- `japanize_matplotlib` は import するだけで matplotlib のフォント設定を自動変更する。`rc()` 呼び出し不要
- `axes.unicode_minus = False` を設定しないとマイナス記号が文字化けする
- ローカル Windows でも import して問題ない（IPAexGothic が使われる）

---

## 2. 実行環境の自動判定

```python
try:
    from google.colab import auth, userdata
    RUNTIME = 'colab'
except ImportError:
    RUNTIME = 'local'
```

より詳細な判定（Enterprise Colab / Cloud Run 含む）は `007_cross_platform_python.md` の `detect_runtime()` を使う。

---

## 3. GCP 認証（環境別分岐）

| 環境 | 認証方法 |
|------|---------|
| Colab（無料） | `auth.authenticate_user()` → `bigquery.Client(project='gmailpj-357912')` |
| ローカル | サービスアカウントキー + SSL 回避パッチ（MEMORY.md 参照） |

```python
if RUNTIME == 'colab':
    from google.colab import auth
    auth.authenticate_user()
    bq = bigquery.Client(project='gmailpj-357912')
else:
    # ローカル: サービスアカウント認証
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(...)
    bq = bigquery.Client(credentials=creds, project='gmailpj-357912')
```

---

## 4. シークレット管理

| 環境 | 方式 | 取得方法 |
|------|------|---------|
| Colab | Colab Secrets（左サイドバー鍵アイコン） | `userdata.get('KEY_NAME')` |
| ローカル | `.env` ファイル | `dotenv.load_dotenv()` + `os.environ['KEY_NAME']` |

```python
if RUNTIME == 'colab':
    from google.colab import userdata
    api_key = userdata.get('JQUANTS_API_KEY')
else:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ['JQUANTS_API_KEY']
```

**Colab Secrets に登録済みのキー**: 必要に応じて Colab 左サイドバーの鍵アイコンから追加する。ノートブックごとにアクセス許可を有効にする必要がある。

---

## 5. 無料枠の制限と対策

| 制限 | 内容 | 対策 |
|------|------|------|
| GPU/TPU 時間 | 1日あたり数時間程度（変動あり） | GPU が不要な処理は CPU ランタイムで実行 |
| セッション切断 | アイドル90分 / 最大12時間で切断 | 長時間処理は途中経過を GCS に保存 |
| RAM | 約12.7GB | 大規模 DataFrame は dtype 最適化（float64→float32、category 型） |
| ディスク | `/content/` 以下、セッション切断で消失 | CSV キャッシュは GCS にもバックアップ |

### セッション切断への備え

```python
# 途中経過を GCS に保存するパターン
def save_checkpoint(df, step_name):
    """中間結果を GCS にチェックポイント保存."""
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket('stock_data_1930932')
    blob = bucket.blob(f'colab_checkpoints/{step_name}.csv')
    blob.upload_from_string(df.to_csv(index=False), content_type='text/csv')
```

---

## 6. データキャッシュ戦略（BQ コスト最適化）

BQ から毎回クエリを実行すると遅い + スキャン課金が発生する。**初回ダウンロード → CSV キャッシュ → 再実行時はキャッシュ読み込み** のパターンを使う。

```python
CACHE_DIR = Path('/content/cache/')  # Colab
# CACHE_DIR = Path('C:/tmp/cache/')  # ローカル

FORCE_RELOAD = False  # True にすると BQ から再取得

def cached_query(name: str, query: str) -> pd.DataFrame:
    """BQ クエリ結果を CSV キャッシュする."""
    cache_path = CACHE_DIR / f'{name}.csv'
    if not FORCE_RELOAD and cache_path.exists():
        return pd.read_csv(cache_path)
    df = bq.query(query).to_dataframe()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df
```

**注意**: Colab のローカルディスク `/content/` はセッション切断で消失する。長期保存が必要なキャッシュは GCS にも保存すること（`059_earnings_model_eda.md` の GCS 保存パターン参照）。

---

## 7. GCS 連携

```python
from google.cloud import storage

client = storage.Client()
bucket = client.bucket('stock_data_1930932')

# アップロード
blob = bucket.blob('path/to/file.csv')
blob.upload_from_string(df.to_csv(index=False), content_type='text/csv')

# ダウンロード
blob = bucket.blob('path/to/file.csv')
df = pd.read_csv(io.BytesIO(blob.download_as_bytes()))
```

gcloud CLI でも可能:
```python
subprocess.run(['gcloud', 'storage', 'cp', local_path, 'gs://stock_data_1930932/path/'])
```

---

## 8. パッケージインストール

無料 Colab はセッションごとに環境がリセットされるため、プロジェクト固有のパッケージは毎回インストールが必要。

```python
if RUNTIME == 'colab':
    import subprocess
    subprocess.run(['pip', 'install', '-q', 'structlog', 'tenacity', 'google-genai'],
                   capture_output=True)
```

**プリインストール済みの主要パッケージ**（再インストール不要）:
- pandas, numpy, matplotlib, scikit-learn, scipy
- google-cloud-bigquery, google-cloud-storage
- requests

---

## 9. グラフ表示

Colab ではインライン表示がデフォルトで有効。`%matplotlib inline` + `plt.show()` で問題なく表示される。

```python
%matplotlib inline
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(df['date'], df['value'])
plt.show()
```

ローカル Windows の `matplotlib.use("Agg")` 問題は Colab では発生しない。

---

## 10. クロスプラットフォーム設計チェックリスト

Colab とローカル Windows の両方で動くノートブックを作るための確認項目:

| 項目 | Colab | ローカル Windows |
|------|-------|-----------------|
| パス区切り | `/` | `pathlib.Path` で自動対応 |
| 認証 | `auth.authenticate_user()` | サービスアカウントキー |
| シークレット | `userdata.get()` | `.env` + `dotenv` |
| 日本語フォント | `apt-get install fonts-noto-cjk` | OS 標準フォント |
| BQ SSL | 問題なし | SSL 回避パッチ必要 |
| ノートブック保存先 | `G:\マイドライブ\Colab Notebooks\`（Google Drive OK） | `scripts/` 以下（git管理） |
| データ保存先 | `/content/`（一時） + GCS（永続） | `C:\tmp\`（Google Drive 禁止） |
| パッケージ | `pip install` 毎回 | `uv sync` で管理済み |

**既存の実装例**: `scripts/earnings_model/earnings_model_eda.ipynb` が上記すべてを実装済み。新規ノートブック作成時はこれを参考にする。

---

## 11. パッケージインストールの注意点

### PyPI パッケージ名 ≠ import 名

| import 名 | PyPI パッケージ名（pip install） |
|-----------|-------------------------------|
| `jquantsapi` | `jquants-api-client` |
| `google.genai` | `google-genai` |

**`pip install jquantsapi` は存在しない**。必ず `pip install jquants-api-client` を使う。

### Colab での pip install ベストプラクティス

```python
# ✅ 正解: %pip を別セルで実行（Setup セルとは分離）
%pip install -q jquants-api-client

# ❌ NG: os.system / subprocess.check_call で pip install
#   → exit status 1 で CalledProcessError になる場合がある
#   → 同一セル内の import に反映されない場合がある
```

- **`%pip` はマジックコマンド**。セル内で `!pip` より確実に動作する
- **pip install と import は別セルに分ける**。同一セル内だとインストール完了前に import が走ることがある
- **Setup セル内に pip install を書かない**。Cell 2（pip install専用）→ Cell 3（Setup + import）の順

---

## 12. Colab ノートブック自動実行（pyautogui + Edge）

ローカル Windows から Colab ノートブックの実行・エラー検出を自動化する方法。

### 前提条件

- Edge に Google アカウントでログイン済み
- `pyautogui` インストール済み（`uv add pyautogui`）
- 実行前に Edge を全て閉じる（`taskkill //F //IM msedge.exe`）

### スクリプト構成（`C:\tmp\colab_runner_auto.py`）

```python
import subprocess, time, pyautogui

COLAB_URL = "https://colab.research.google.com/drive/..."
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# 1. Edge 起動 + Colab URL
subprocess.Popen([EDGE, COLAB_URL])
time.sleep(12)

# 2. ダイアログ閉じ + 強制リロード（Drive同期反映）
pyautogui.press("escape")
time.sleep(1)
pyautogui.hotkey("ctrl", "shift", "r")
time.sleep(10)

# 3. ノートブック領域クリック + Ctrl+F9（全セル実行）
sw, sh = pyautogui.size()
pyautogui.click(sw // 3, sh // 2)
time.sleep(1)
pyautogui.hotkey("ctrl", "F9")

# 4. 実行完了待ち（5分）
time.sleep(300)

# 5. ズームアウト + スクロール + スクリーンショット
for _ in range(4):
    pyautogui.hotkey("ctrl", "minus")
pyautogui.hotkey("ctrl", "Home")
pyautogui.moveTo(sw // 3, sh // 2)
for page in range(1, 16):
    pyautogui.screenshot().save(f"C:/tmp/p{page:02d}.png")
    pyautogui.scroll(-300)  # 1画面分
pyautogui.hotkey("ctrl", "0")  # ズーム戻し
```

### スクロール量チューニング結果

| `pyautogui.scroll()` 値 | スクロール量 |
|------------------------|-----------|
| `-10` | 約1行 |
| `-30` | 約2-3行 |
| `-300` | **約1画面分**（推奨） |

- **マウスカーソルをノートブック領域に配置してからスクロール**（`moveTo(sw//3, sh//2)`）
- Colab の内部スクロールコンテナにイベントが届く必要がある
- `Ctrl+Home` / `Ctrl+End` はノートブック先頭/末尾へのジャンプに有効
- `Space` / `PageDown` はコードセルにフォーカスがあると文字入力になるため非推奨
- **15枚のスクリーンショット（`scroll(-300)` × 15回）** でノートブック全体をカバー可能

### Drive 同期のタイムラグ

ノートブックをローカルから `G:\マイドライブ\Colab Notebooks\` にコピーした後、Colab に反映されるまで**30秒以上**待つ必要がある。`Ctrl+Shift+R`（強制リロード）も併用する。

### browser-use CLI の制限（2026-04 時点）

- v0.11.13: `/tmp` パスハードコーディング問題あり（`profile.py` の `set_default_downloads_path` を `tempfile.gettempdir()` にパッチで解消）
- `--browser real` モード: セッション永続化に対応しておらず、`open` 後に `screenshot` できない
- Chromium モード: Google ログインが必要でColab操作に使えない
- **結論: pyautogui + Edge が最も安定**
