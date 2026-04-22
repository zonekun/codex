# download_bc_kpi.py Nodriver改善版 実装プラン

作成日: 2026-04-08

## 目的

`scripts/download_bc_kpi.py` のSeleniumをNodriverに置き換え、bot対策を強化する。

## 実行環境

- **端末**: Linux VM（GCP）
- **RAM**: 1GB（無料枠。ダメなら2GBに変更）
- **ディスプレイ**: Xvfb（仮想フレームバッファ）でヘッドあり動作
- **ブラウザ**: Chromium（apt install）

## 実装内容

### 1. 依存パッケージ

```bash
# Chromium + Xvfb インストール
sudo apt install -y chromium-browser xvfb

# Python パッケージ
uv add nodriver
```

### 2. ブラウザ起動構成

- Xvfb で仮想ディスプレイ作成（:99）
- nodriver でヘッドあり Chrome 起動
- `DISPLAY=:99` を環境変数に設定

### 3. 低スペック向け最適化

- 画像・CSS・フォントのネットワークブロック（必要に応じて）
- `--js-flags=--max-old-space-size=256`（1GB制約のため縮小）
- `--disable-gpu`（GPU不要）
- `--disable-extensions`

### 4. ランダム待機（bot対策）

- 基本待機: 正規分布ベース（平均5秒、σ=1.5、下限3秒・上限9秒）
- 10社ごとに長めの休憩: 20〜40秒（一様乱数）
- WAF検知時: 指数バックオフ（60s → 120s → 240s、最大3回）

### 5. その他bot対策

| 対策 | 内容 |
|------|------|
| User-Agentローテーション | 実在するChrome UAリストからランダム選択（セッション開始時） |
| ウィンドウサイズランダム化 | 1200x800 固定 → 1024〜1920 × 768〜1080 範囲でランダム |
| ランダムスクロール | データ取得前にページを自然にスクロール |
| リファラー設定 | Google検索経由に見せる |
| セッションクッキー保持 | 再起動時に引き継ぎ（--resume 時） |

### 6. 出力・オプション（現行維持）

- 出力: `data/csv/bc_monthly_kpi.csv`（ticker, year_month, field, value）
- `--tickers` / `--resume` オプションそのまま

## 実装ステップ（再起動後）

1. VM メモリ確認（`free -h` で 2GB 確認）
2. Chromium + Xvfb インストール
3. `uv add nodriver`
4. `scripts/download_bc_kpi_v2.py` 新規作成
5. 1社テスト実行（`--tickers 3097`）
6. WAF突破確認 → 問題なければ全量実行

## 参考

- 現行スクリプト: `scripts/download_bc_kpi.py`
- 知見: `docs/knowledges/tools/015_curl_cffi_impersonation.md`
