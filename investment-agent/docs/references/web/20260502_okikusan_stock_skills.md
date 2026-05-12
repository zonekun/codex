# Claude Code Skills による株スクリーニング自動化システム

- **URL**: https://zenn.dev/okikusan/articles/eacc59ca26e566
- **著者**: okikusan
- **GitHub**: https://github.com/okikusan-public/stock_skills
- **取得日**: 2026-05-02

---

## コアの問題解決

「割安株を探す」という繰り返し作業をWeb版Claudeで自動化しようとして4つの壁に直面:

1. **レスポンス不安定性** — Web検索は時間がかかり、参照ソースが毎回変わる
2. **コンテキスト消失** — 前回の分析結果がセッション間で失われる
3. **再現性欠如** — スクリーニング基準が微妙に変動する
4. **処理量制限** — 複数銘柄の同時分析で出力が途切れる

## システムアーキテクチャ

### 3層構造

| 層 | 役割 | 実体 |
|----|------|------|
| Skills 層 | 自然言語インターフェース（薄い） | `.claude/skills/stock-skills/SKILL.md` |
| Core 層 | ビジネスロジック（17モジュール） | `src/data/` |
| Data 層 | yfinance ラッパー（キャッシュ・レート制限・サニタイズ） | `src/data/yahoo_client/` |

### 5つのスキル

1. スクリーニング（4エンジン対応）
2. 個別銘柄レポート
3. ポートフォリオ管理
4. ストレステスト
5. ウォッチリスト管理

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

- ポートフォリオ → CSV（人間編集可能）
- 設定値 → YAML
- アーキテクチャ知識 → CLAUDE.md
- キャッシュ → JSON
- JSON ファイルが master（常に書き込み成功）
- Neo4j は view（GraphRAG、graceful degradation）

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

## 開発プラクティス

- iTerm2の複数ウィンドウとgit worktreeを活用した「バイブコーディング」フロー
- PdM窓でLinearの課題管理、開発窓でClaude Codeとの対話、テスト窓で検証を並行実施
- 740以上のユニットテスト
- Teams機能で4地域の並列スクリーニングと統合テストの同時実行

## 実装上の教訓

- ETF判定: 「売上履歴が空リスト」を返す → `bool()` チェックで解決
- ETFリターン過大評価（単利12倍）→ CAGR + 2年期間に修正
- アナリスト少数時のシナリオ崩壊 → 自動スプレッド付与
- Graceful degradation: Grok API等の外部サービスは未設定時にスキップする設計

## Skills 開発の知見

1. **SKILL.md は「インターフェース仕様書」** — ロジックはスキル外の Core 層に分離
2. **CLAUDE.md にアーキテクチャ記載** — エージェントの全体像を明示
3. **コンテキスト階層化** — コード / ルール / 設計判断を分離
4. **routing.yaml で few-shot** — ユーザー意図→エージェント選定の例示

---

## キーワード

Claude Code, Skills, Agents, yfinance, スクリーニング, ポートフォリオ, ヘルスチェック, バリュースコア, routing.yaml, orchestration, マルチLLMレビュー, Graceful degradation, Teams並列
