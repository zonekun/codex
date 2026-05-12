# Pythonプラグインアーキテクチャで投資分析の再現性を確保する【投資分析システム設計記 #1】

- **URL**: https://zenn.dev/sktt_panda/articles/rakuscan-plugin-architecture
- **著者**: sktt_panda
- **取得日**: 2026-05-02

---

## RakuScan の全体像

個人開発の日本株投資分析システム。定量分析はPythonで確定的に実行し、Claudeには「複数の分析結果を統合して人間に伝える」部分だけを任せる設計。

日次ワークフロー（毎日16:00 JST自動実行）:
1. 投資対象の銘柄群（ユニバース、150〜200社）を取得する
2. 各銘柄に対して、複数の投資手法（プラグイン）を実行する
3. 結果を Claude が統合分析して、Discord に通知する

## なぜプラグイン型にしたのか

投資手法は頻繁に変わる。モノリシックなコードを修正するのではなく、各戦略を独立プラグインとして扱い、他に影響なく追加・削除・無効化できるようにした。

### プラグインアーキテクチャの原則
- 1つの投資手法 = 1つの Python ファイル
- すべてのプラグインは同じフォーマット（PluginResult）で結果を返す
- プラグイン同士は互いの存在を知らない

## 現在のプラグイン構成

9本のプラグインが稼働中。市場環境、財務、ファクター、テクニカル、リスクの5カテゴリ。

| プラグイン | カテゴリ | やっていること | 根拠 |
|----------|---------|----------------|------|
| regime | 市場環境 | TOPIX の200日移動平均で攻め/守りを判定 | Faber (2007) |
| piotroski | 財務 | 9項目の財務チェックで健全性をスコア化 | Piotroski (2000) |
| factor_momentum | ファクター | 12ヶ月リターンでモメンタムを評価 | Jegadeesh & Titman (1993) |
| factor_value | ファクター | PBR・PER・PCFRでバリューを評価 | Fama & French (1992) |
| factor_quality | ファクター | ROE・ROA・FCFで収益の質を評価 | Novy-Marx & Velikov (2016) |
| altman_z | 財務 | Z-Score で倒産リスクを判定 | Altman (1968) |
| sepa | テクニカル | トレンドテンプレートの条件を判定 | Minervini VCP |
| exit_rules | リスク | 5つの売却条件をチェック | ATR ベース |
| factor_composite | ファクター | モメンタム×バリュー×クオリティの複合スコア | Asness et al. (2013) |

## 共通インターフェース：PluginResult

| フィールド | 型 | 意味 |
|-----------|-----|------|
| signal | str | 5段階の判定（STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL） |
| score | float | 0.0〜1.0 に正規化されたスコア |
| confidence | float | 0.0〜1.0 の確信度 |
| summary | str | 1行の日本語要約 |
| details | dict | 手法固有の詳細データ |
| category | str | 5種類のカテゴリ |

score と confidence を分離することで、高スコアだがデータ不完全な状況も表現可能。

## プラグインの規約

- `scripts/` ディレクトリに Python ファイルを置く
- `run()` 関数を公開する
- `PluginResult` を返す

## 動的ロードの仕組み

YAML設定ファイル（`plugins.yaml`）でプラグインの有効/無効を管理。

```yaml
plugins:
  - name: regime
    display_name: マーケットレジーム
    enabled: true
    category: regime

  - name: piotroski
    display_name: ピオトロスキー F スコア
    enabled: true
    category: fundamental

  # 成績が悪ければ enabled: false にするだけ
  # - name: canslim
  #   enabled: false
```

## プラグインを束ねるスクリーナー

各プラグインの結果を集約し、コンセンサススコアを計算する。

## Claude の立ち位置：統合分析だけを任せる

| 役割 | 担当 | 理由 |
|-----|------|------|
| 個別手法の定量判定 | Python（各プラグイン） | 再現性が必要。同じデータなら同じ結果 |
| 結果の統合・言語化 | Claude | 複数の矛盾する判定を読み解いて自然言語にまとめるのは LLM の得意領域 |

当初は全分析をClaudeに通していたが、トークン効率と再現性の問題から、定量（Python）と統合（Claude）を分離する現設計に進化。

## 3つのインターフェースで同じエンジンを共有する

- **CLI**: `python -m scripts.daily_run` で日次パイプラインを実行
- **Claude Code スキル**: `/scan` でスクリーニング、`/analyze 7203` で個別分析
- **Discord Bot**: `!scan` で結果確認、`!research` で Claude を使った深掘り

## 段階的に育てられる設計

新プラグイン追加手順:
1. `scripts/new_strategy.py` を作る
2. `run()` 関数で `PluginResult` を返すように実装する
3. `plugins.yaml` に1行追加する

既存コード変更不要。7本の追加プラグインが設計フェーズで待機中。

## まとめ

- **1ファイル = 1手法**。プラグイン同士は互いを知らない
- **共通インターフェース**（PluginResult）で結果の形式を統一
- **YAML + 動的ロード**でプラグインの追加・削除がコード変更なしで可能
- **Python で定量、Claude で統合**。再現性とトークン効率を両立

## 次回予告

- 第2回: 外部API統合とキャッシュ戦略
- 第3回: Claudeとの統合レポート生成

---

## キーワード

RakuScan, プラグインアーキテクチャ, 投資分析, スクリーニング, PluginResult, Claude統合, Discord通知, Piotroski, Altman Z-Score, ファクター投資, モメンタム, バリュー, クオリティ, SEPA, Minervini
