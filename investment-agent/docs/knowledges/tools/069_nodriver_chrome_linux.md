# nodriver + Google Chrome + Xvfb（Linux 1GB RAM）

**カテゴリ**: tools
**作成日**: 2026-04-09
**ステータス**: 有効
**関連ファイル**: `scripts/download_bc_kpi_v2.py`

## 概要

1GB RAM の Linux VM（GCP 無料枠）で nodriver を使ってブラウザ自動操作する構成。
Xvfb で仮想ディスプレイを作り、Google Chrome をヘッドあり相当で動かす。

---

## インストール

```bash
# 1. Google Chrome（snap Chromium は NG → 後述）
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo dpkg -i google-chrome-stable_current_amd64.deb
sudo apt-get install -f -y  # 依存解決

# 2. Xvfb（仮想フレームバッファ）
sudo apt install -y xvfb

# 3. Python パッケージ
uv add nodriver google-cloud-storage
```

---

## スワップ設定（必須）

1GB RAM では Chrome 起動だけでメモリ枯渇する。**スワップがないと起動失敗**。

```bash
# スワップ作成（再起動で消えるので毎回必要）
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
free -h  # Swap: 2.0Gi が出ればOK
```

> スワップファイル自体（/swapfile）は再起動後も残る。`swapon` だけ再実行すれば復旧できる。

---

## Xvfb 起動

```bash
# 仮想ディスプレイ :99 を起動（バックグラウンド）
Xvfb :99 -screen 0 1280x1024x24 -ac &
sleep 2
export DISPLAY=:99
```

起動確認:
```bash
pgrep -a Xvfb  # PID が出ればOK
```

---

## Chrome 起動（nodriver 接続分離パターン）

nodriver のデフォルト接続タイムアウト（2.75秒）は 1GB RAM では間に合わない。
**Chrome 起動 → ポート待機 → nodriver 接続** を分離するのが正解。

```python
import subprocess, time, urllib.request, os

DEBUG_PORT = 9222
DISPLAY = ":99"

def start_chrome() -> subprocess.Popen:
    cmd = [
        "/usr/bin/google-chrome",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--password-store=basic",
        "--disable-infobars",
        "--disable-breakpad",
        "--user-data-dir=/tmp/nodriver_chrome_profile",
        "--window-size=1280,800",
        "--js-flags=--max-old-space-size=256",  # 1GB RAM 向け縮小
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
    ]
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    # ポートが開くまで最大30秒待機
    for i in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1)
            print(f"Chrome 接続可 ({i+1}秒後)")
            return proc
        except Exception:
            pass
    raise RuntimeError(f"Chrome がポート {DEBUG_PORT} を開かなかった（30秒タイムアウト）")
```

nodriver 接続:
```python
import nodriver as uc

async def create_browser():
    browser = await uc.start(
        browser_executable_path="/usr/bin/google-chrome",
        headless=False,  # Xvfb 上でヘッドあり動作
    )
    return browser
```

---

## 残留プロセス対策

前回のChromeが残っているとポート 9222 が競合して起動失敗する。

```bash
# 確認
fuser 9222/tcp

# 強制終了
pkill -f "google-chrome" || true
kill -9 <PID>
```

スクリプト内でも Chrome 起動前に既存プロセスを kill する:
```python
import subprocess
subprocess.run(["pkill", "-f", "google-chrome"], capture_output=True)
time.sleep(2)
```

---

## snap Chromium は NG

snap 版 Chromium は nodriver の CDP（Chrome DevTools Protocol）接続と非互換。
`--remote-debugging-port` が期待通り動作しない。**必ず Google Chrome の deb 版を使う**。

---

## 実行コマンド

```bash
# Xvfb + swap 確認後
pgrep -a Xvfb || (Xvfb :99 -screen 0 1280x1024x24 -ac & sleep 2)
swapon --show  # Swap 確認

PYTHONUTF8=1 DISPLAY=:99 uv run --no-sync python scripts/download_bc_kpi_v2.py
PYTHONUTF8=1 DISPLAY=:99 uv run --no-sync python scripts/download_bc_kpi_v2.py --resume
```

---

## BCスクレイピング WAF 対策・注意点

| 対策 | 内容 |
|------|------|
| 待機間隔 | 正規分布ベース（平均10秒、σ=2.0、下限8/上限15秒） |
| バッチ休憩 | 10社ごとに30〜60秒の長休憩 |
| WAF バックオフ | 検知時: 60s → 120s → 240s（最大3回で中断） |
| UA ローテーション | Chrome 実在UAリストからランダム選択 |
| ウィンドウサイズ | 1024〜1920 × 768〜1080 でランダム |

**IP BAN 注意**: 連続200社程度でIPがブロックされる場合がある（2026-04-09 実績）。
ブロックされたら数時間〜1日待機してから `--resume` で再試行。
ブロック時のログ `empty` は次回再処理のために削除しておく。

**GCP IP レンジ全体がブロック対象（2026-04-10 確認）**: buffett-code.com の AWS WAF は
GCP の ASN（AS396982 Google LLC）を識別しており、IP を変えても同一リージョン内では突破不可。
トップページ (`/`) すら HTTP 202 + "Human Verification" を返す。
→ **Windows 住宅 IP での実行が必要**。待機間隔は10秒ベースに変更済み。

---

## 銘柄リスト取得ロジック

```python
# structure.json が存在する銘柄（= BCにデータがある銘柄）
# monthly_records.json ではない（月次開示パイプライン対象のみになってしまう）
def list_tickers_with_records(gcs) -> list[str]:
    inactive = {p.stem for p in Path("meta/monthly").glob("*.json")
                if json.loads(p.read_text("utf-8")).get("inactive_reason")}
    tickers = []
    for blob in gcs.list_blobs(BUCKET, prefix="monthlydata/"):
        if blob.name.endswith("/structure.json"):
            ticker = blob.name.split("/")[1]
            if ticker not in inactive:
                tickers.append(ticker)
    return sorted(tickers)
```

> 2026-04-09時点: structure.json 507社、inactive 3社（4015/9223/9399）、net 504社。

---

## resume ロジック

CSV ではなく **ログファイルベース** でスキップするのが確実。
（CSV は正常取得分のみ書き込まれるが、ログは empty/error も記録される）

```python
# --resume 時: LOG_FILE 記録済みティッカーをスキップ
if args.resume and LOG_FILE.exists():
    done = {row["ticker"] for row in csv.DictReader(open(LOG_FILE))}
    tickers = [t for t in tickers if t not in done]
```
