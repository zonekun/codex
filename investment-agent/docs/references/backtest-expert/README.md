# backtest-expert リファレンス

**ソース**: [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/backtest-expert)
**ライセンス**: MIT License
**取得日**: 2026-05-14

## 概要

Claude Code 用バックテスト設計スキル。"find strategies that break the least, not profit the most" を哲学とし、Seven Sins of Quantitative Investing (Deutsche Bank) をベースに体系的なバックテスト手法を提供する。

## ファイル構成

| ファイル | 内容 |
|---------|------|
| SKILL.md | スキル本体（ワークフロー6ステップ、ストレステスト、判定基準） |
| methodology.md | 方法論詳細（Seven Sins、Walk-Forward、Regime Analysis、Slippage） |
| failed_tests.md | 失敗パターン6類型 + Case Study Framework + Red Flags Checklist |
| evaluate_backtest.py | 5次元スコアリングCLI（Sample Size / Expectancy / Risk Mgmt / Robustness / Exec Realism） |

## 本プロジェクトでの利用

- `skills/backtest_design.md` の全面再構築のベース
- `045_backtest_evaluation_metrics.md` の合格基準テーブル刷新（5次元スコアリング導入）
- プラン: `docs/plans/tools-backtest_skill_rebuild_20260514_201840.md`
