# 090 okikusan/stock_skills リファレンス

> **出典**: https://zenn.dev/okikusan/articles/eacc59ca26e566
> **原文リファレンス**: `docs/references/web/20260502_okikusan_stock_skills.md`
> **ローカルインストール先**: `G:\マイドライブ\claude\okikusan`（読み取り専用参照用）
> **GitHub**: https://github.com/okikusan-public/stock_skills

## 概要

Claude Code の **Skills + Agents** 機能と **yfinance** を組み合わせた株式スクリーニング・ポートフォリオ管理システム。自然言語で投資分析を自動化する個人投資家向け OSS。

## アーキテクチャ

### 3層構造

| 層 | 役割 | 実体 |
|----|------|------|
| Skills 層 | インターフェース（薄い） | `.claude/skills/stock-skills/SKILL.md` |
| Core 層 | ビジネスロジック（17モジュール） | `src/data/` |
| Data 層 | yfinance ラッパー（キャッシュ・レート制限・サニタイズ） | `src/data/yahoo_client/` |

### エージェント体系（7エージェント）

| エージェント | 役割 | 定義 |
|-------------|------|------|
| Screener | 銘柄探し・スクリーニング（4エンジン） | `.claude/agents/screener/` |
| Analyst | バリュエーション・割安度判定 | `.claude/agents/analyst/` |
| Researcher | ニュース・センチメント（Grok API） | `.claude/agents/researcher/` |
| Health Checker | PFの事実・数値（判断しない） | `.claude/agents/health-checker/` |
| Strategist | 投資判断・レコメンド | `.claude/agents/strategist/` |
| Risk Assessor | 市場リスク判定（risk-on/neutral/risk-off） | `.claude/agents/risk-assessor/` |
| Reviewer | 品質・矛盾・リスクチェック（マルチLLM） | `.claude/agents/reviewer/` |

### オーケストレーション

- `SKILL.md` が routing.yaml を参照し、ユーザー意図→エージェントをルーティング
- `orchestration.yaml` で自律修正ループ（0件→条件緩和リトライ、Reviewer FAIL→差し戻し）
- 連鎖 vs 並列: `agents` 配列は原則順序付き連鎖。独立なら並列（Agent同時発行で強制化）

### データ永続化

- JSON ファイルが master（常に書き込み成功）
- Neo4j は view（GraphRAG、graceful degradation）
- CSV でポートフォリオ・売買記録

## スクリーニング 4エンジン

| エンジン | 方式 |
|---------|------|
| QueryScreener | バリュエーション指標ランキング |
| PullbackScreener | テクニカル押し目判定 |
| AlphaScreener | 業績改善銘柄（4段パイプライン） |
| ValueScreener | レガシー互換 |

**バリュースコア配分**: PER(25pt) + PBR(25pt) + 配当利回り(20pt) + ROE(15pt) + 売上成長率(15pt)

## ヘルスチェック: 2軸判定

テクニカル + ファンダメンタル条件の両方を要求:

- **早期警告**: SMA50割れ / RSI急落
- **注意**: SMA50≈SMA200 かつ変化スコア悪化
- **撤退**: デッドクロス かつ複数指標悪化

## 設計上の参考ポイント

### Skills 開発の知見（記事から）

1. **SKILL.md は「インターフェース仕様書」** — ロジックはスキル外の Core 層に分離
2. **CLAUDE.md にアーキテクチャ記載** — エージェントの全体像を明示
3. **コンテキスト階層化** — コード / ルール / 設計判断を分離
4. **Graceful degradation** — 外部API未設定時スキップ（Neo4j不要でも動作）
5. **routing.yaml で few-shot** — ユーザー意図→エージェント選定の例示

### 並列実行（Teams）

- 4地域を同時探索で壁時計時間を 1/N に短縮
- サブエージェントに並列を指示しても逐次実行される → オーケストレーターが Agent を同時発行して強制並列化

### yfinance の癖

- ETF判定: 「売上履歴が空リスト」を返す → `bool()` チェックで解決
- ETFリターン過大評価（単利12倍）→ CAGR + 2年期間に修正
- アナリスト少数時のシナリオ崩壊 → 自動スプレッド付与

## 当プロジェクトとの違い

| 観点 | okikusan/stock_skills | 当プロジェクト（investment-agent） |
|------|----------------------|-----------------------------------|
| データソース | yfinance（無料） | J-Quants + BQ + GCS + TDnet + EDINET |
| AI処理 | Claude Skills + Agents | Cloud Run Job + Gemini/Gemma バッチ |
| 永続化 | JSON/CSV + Neo4j（GraphRAG） | BigQuery + GCS + ローカルCSV |
| 分析スタイル | リアルタイム対話型 | バッチ処理 + 統計モデル |
| 市場 | 日本 + 米国 + 60+取引所 | 日本株特化 |
| ポートフォリオ管理 | CSV + ヘルスチェック + ストレステスト | なし（将来構想） |

## 参考にできる点

- **Routing YAML パターン**: 自然言語→エージェント選定の few-shot 設計は、当プロジェクトの skills/ 拡張時に参考可
- **Reviewer のマルチLLM**: GPT+Gemini+Claude 3並列レビューは DeepThink 的
- **Session State Reconcile**: セッション開始時にディスク状態を確認する仕組み（AI memory だけで判断しない）
- **Output & Visibility v1**: 4層出力フォーマット（ヘッダ→進捗→本体→フッタ）は構造化出力の参考
