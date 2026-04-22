# アーキテクチャ・技術スタック・開発ロードマップ

## ディレクトリ構造

```
investment-agent/
├── CLAUDE.md
├── setup.sh / setup_machine.ps1
├── pyproject.toml
├── .env                         # git管理外
├── keys/gcp-service-account.json  # git管理外
│
├── config/
│   ├── settings.yaml
│   ├── sources.yaml
│   └── broker.yaml
│
├── src/
│   ├── core/          # agent.py / scheduler.py / config.py / logger.py
│   ├── idea/          # extractor.py / sources/ / models.py / store.py
│   ├── datastore/     # bigquery.py / gcs.py / local_csv.py / api_client.py / cache.py
│   ├── collector/     # runtime.py / stock_price.py / shina_rates.py / margin_balance.py
│   ├── analysis/      # analyzer.py / statistics.py / factor_model.py / cross_section.py 等
│   ├── backtest/      # engine.py / strategy.py / metrics.py
│   ├── trading/       # portfolio.py / risk.py / candidate.py / trader.py / demo.py / broker/
│   ├── knowledge/     # manager.py / feedback.py / templates/
│   └── api/           # FastAPI app / routes/
│
├── dashboard/app.py
├── skills/            # Claude Codeスキル定義
├── reference_code/    # 参考分析コード
├── data_catalog.md    # 全ストレージのデータ定義
├── data/csv/ / data/cache/ / data/logs/
├── tests/
├── scripts/
└── docs/
    ├── plans/         # YYYYMMDD_HHMMSS_<slug>.md
    ├── knowledges/    # analysis/ data/ tools/ api/ strategies/
    ├── architecture.md（本ファイル）
    └── commands.md
```

---

## 技術スタック

| カテゴリ | 技術 | 用途 |
|---------|------|------|
| 言語 | Python 3.12+ | メイン |
| パッケージ管理 | uv | 仮想環境・依存関係 |
| LLM | Claude API (claude-sonnet-4-6) | アイディア抽出・分析支援 |
| データストア(a) | Google BigQuery | 構造化データ（株価・銘柄マスタ等） |
| データストア(b) | Google Cloud Storage | 非構造化データ（EDINET・e-STAT等） |
| データストア(c) | ローカルCSV (data/csv/) | BQ/GCS一部コピー。分析メインソース |
| データストア(d) | 外部API + キャッシュ | J-Quants, yfinance等 |
| スケジューラ | APScheduler | 定刻タスク |
| API | FastAPI | REST API |
| テスト | pytest | ユニット・統合テスト |
| データ分析 | pandas, numpy, scipy, statsmodels | 統計分析 |
| 可視化 | JupyterLab + matplotlib | グラフ表示 |
| GCP SDK | google-cloud-bigquery, google-cloud-storage | GCPアクセス |

---

## 外部API・データソース

詳細は `data_catalog.md` を参照。主要ソース：

| API / ソース | 認証キー |
|-------------|---------|
| J-Quants API | `JQUANTS_API_KEY` |
| EDINET API | `EDINET_API_KEY` |
| e-STAT API | `ESTAT_API_KEY` |
| X (Twitter) API | `TWITTER_BEARER_TOKEN` |
| YouTube Data API | `YOUTUBE_API_KEY` |
| Anthropic API | `ANTHROPIC_API_KEY` |
| Google Cloud | `GOOGLE_APPLICATION_CREDENTIALS`（サービスアカウントJSON） |

---

## 設定ファイル仕様（config/settings.yaml）

```yaml
datastore:
  gcp_project_id: "gmailpj-357912"
  bigquery_dataset: "STOCK"
  gcs_bucket: "stock_data_1930932"
  local_csv_dir: "data/csv"
  cache_dir: "data/cache"
  cache_ttl_hours: 24

analysis:
  significance_level: 0.05
  min_sample_size: 30

backtest:
  initial_capital: 10000000
  commission_rate: 0.001
  slippage_bps: 5
  min_sharpe_ratio: 1.0
  min_win_rate: 0.50
  max_drawdown: 0.20

trading:
  demo_period_days: 20
  max_position_pct: 0.10
  max_total_positions: 20
  daily_order_time: "08:30"
```

---

## クロスプラットフォーム実行環境（Colab / Cloud Run / Local）

収集モジュールは3環境で動作可能。詳細は `docs/knowledges/tools/007_cross_platform_python.md` を参照。

| 環境 | 判別方法 |
|------|---------|
| Google Colab（個人） | `google.colab` import可 & `GOOGLE_CLOUD_PROJECT` 未設定 |
| Google Cloud Enterprise Colab | `google.colab` import可 & `GOOGLE_CLOUD_PROJECT` 設定済み |
| ローカルPC | `google.colab` import不可 |

---

## 開発ロードマップ

### Phase 1: 基盤構築
1. プロジェクト骨格（ディレクトリ・設定・ロギング）
2. データストア接続層（BigQuery, GCS, ローカルCSV, API+キャッシュ）
3. データカタログ（data_catalog.md）の初期作成

### Phase 2: アイディア抽出
4. YouTube文字起こし取得
5. X(Twitter) 監視
6. ArXiv論文取得
7. LLMによるアイディア抽出エンジン

### Phase 3: 分析・バックテスト
8. 統計分析モジュール
9. バックテストエンジン

### Phase 4: トレーディング
10. デモトレード → 証券会社API連携 → 本運用

### Phase 5: ダッシュボード・運用
11. FastAPI / Streamlit / 監視・アラート
