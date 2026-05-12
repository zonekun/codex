# STOCK.SIGNAL_011_4 + PAPER_TRADE_011_4
> 親: [`data_catalog.md`](../../data_catalog.md)

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.SIGNAL_011_4` | 011-4 戦略 日次シグナル（米国→日本セクターETFリードラグ） | 日次 | Cloud Run Job `signal-011-4-daily`（月〜金 06:30 JST、Scheduler: `signal-011-4-daily-scheduler`）⛔停止中 |

**`STOCK.SIGNAL_011_4` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATE | DATE | 取引日（この日の寄付でエントリー予定） |
| SIGNAL_DATE | DATETIME | シグナル計算日時（JST） |
| TICKER | STRING | 日本ETFティッカー（1617〜1633） |
| SIGNAL_VALUE | FLOAT64 | シグナル値 z_hat_JP |
| SIDE | STRING | 'LONG' / 'SHORT' / 'NONE' |
| RANK | INT64 | シグナル絶対値の順位（1=最強） |
| POSITION_JPY | FLOAT64 | 想定ポジション金額（円） |
| KILL_SWITCH | BOOL | Kill Switch 発動中なら True |
| ROLLING_IC | FLOAT64 | 26週 Rolling IC 値 |

- **収集スクリプト**: `scripts/signal_011_4_daily.py`
- **更新方式**: DELETE + WRITE_APPEND（リラン安全。当日分を削除してから挿入）
- **更新タイミング**: 毎営業日 06:30 JST（Cloud Scheduler `signal-011-4-daily-scheduler`）
- **データソース**: yfinance（米国ETF終値）+ BQ STOCK_PRICE_JQUANTS（日本ETF）+ GCS pickle（C0/V0）
- **GCS 依存**: `gs://stock_data_1930932/signal_011_4/c0_v0.pkl`（C0/V0 事前計算結果）

---

| テーブル名 | 説明 | 更新頻度 | 備考 |
|-----------|------|---------|------|
| `gmailpj-357912.STOCK.PAPER_TRADE_011_4` | 011-4 戦略 ペーパートレード P&L | 日次 | Cloud Run Job `paper-trade-011-4-pnl`（月〜金 19:00 JST、Scheduler: `paper-trade-011-4-pnl-scheduler`）⛔停止中 |

**`STOCK.PAPER_TRADE_011_4` スキーマ:**

| カラム名 | 型 | 説明 |
|---------|-----|------|
| DATE | DATE | 取引日 |
| TICKER | STRING | ETFティッカー |
| SIDE | STRING | 'LONG' / 'SHORT' |
| POSITION_JPY | FLOAT64 | ポジション金額（円） |
| OPEN_PRICE | FLOAT64 | 寄付値 |
| CLOSE_PRICE | FLOAT64 | 引け値 |
| RETURN_PCT | FLOAT64 | リターン（%） |
| PNL_JPY | FLOAT64 | 損益（円） |
| CUMULATIVE_PNL | FLOAT64 | 累積損益（円、初日からの積算） |
| KILL_SWITCH | BOOL | Kill Switch 状態 |

- **収集スクリプト**: `scripts/paper_trade_011_4_pnl.py`
- **更新方式**: DELETE + WRITE_APPEND（リラン安全）
- **更新タイミング**: 毎営業日 19:00 JST（Cloud Scheduler `paper-trade-011-4-pnl-scheduler`）
- **データソース**: BQ SIGNAL_011_4（シグナル）+ BQ STOCK_PRICE_JQUANTS（株価）
- **メール通知**: 毎日 P&L 結果を送信（Kill Switch ON 時もノートレード通知を送信）

