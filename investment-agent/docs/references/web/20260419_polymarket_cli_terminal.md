# Polymarket CLI Terminal（2026年版）

- 取り込み日: 2026-04-19
- URL: https://zenn.dev/komlock_lab/articles/polymarket-cli-terminal-2026
- 著者: komlock_lab（Zenn）

## 概要

Polymarket 公式 Rust 製 CLI ツール `polymarket` の使い方と応用例の解説記事。Polymarket は世界最大の分散型予測市場プラットフォームで、政治選挙・BTC 価格・経済イベント等の確率をリアルタイム取引している。

## 機能区分

**認証不要（データ取得のみ）:**
- 市場検索・一覧表示
- オッズ・オーダーブック確認
- 価格履歴・イベント・タグ取得

**ウォレット認証が必要:**
- 取引注文
- ポジション管理
- CTF（Conditional Token Framework）操作

## インストール

**Homebrew（推奨）:**
```bash
brew tap Polymarket/polymarket-cli https://github.com/Polymarket/polymarket-cli
brew install polymarket
```

**シェルスクリプト:**
```bash
curl -sSL https://raw.githubusercontent.com/Polymarket/polymarket-cli/main/install.sh | sh
```

**ソースビルド:**
```bash
git clone https://github.com/Polymarket/polymarket-cli.git
cd polymarket-cli
cargo install --path .
```

> 注意: Linux VPS では GLIBC 2.38 要件による互換性問題が発生することがある。

## 基本コマンド

**市場検索（テーブル表示）:**
```bash
polymarket markets search "Bitcoin" --limit 5
```

**JSON 出力:**
```bash
polymarket markets search "Bank of Japan" --limit 1 -o json
```

**オーダーブック:**
```bash
polymarket clob book <TOKEN_ID>
```

**価格情報:**
```bash
polymarket clob midpoint <TOKEN_ID>
polymarket clob spread <TOKEN_ID>
polymarket clob price-history <TOKEN_ID> --interval 1d
```

**その他:**
```bash
polymarket events list --limit 5
polymarket tags list
polymarket data leaderboard
```

## 識別子の使い分け

- `slug`（市場）
- `conditionId`
- `tokenId`

3 種の識別子が混在する。JSON 出力で各 ID を事前確認してから使うことが推奨。

## 実用ユースケース

1. **オッズ変動監視** - ニュースより先に市場変化を検知
2. **投資判断支援** - 予測市場オッズを補助データとして活用
3. **AI エージェント連携** - 自動監視・分析システム構築
4. **データ分析** - 推移グラフ・相関分析

## Node.js 連携例（オッズ変動アラート）

```javascript
const { execSync } = require("child_process");
const fs = require("fs");

const THRESHOLD = 5.0; // 5%以上の変動でアラート

function searchMarkets(query, limit = 3) {
  const stdout = execSync(
    `polymarket markets search "${query}" --limit ${limit} -o json`,
    { timeout: 15000, encoding: "utf-8" }
  );
  return JSON.parse(stdout);
}
```

定期実行して前回オッズとの差分が閾値超過時にアラートを出す構成。

## 注意点

- レート制限は明示されていないが、大量リクエストで 429 が返ることがある
- 自動実行は数秒〜数分の間隔を推奨
- 日本の法規制上グレーゾーン。データ閲覧は問題ないが取引は法的検討が必要

## 本プロジェクトでの活用想定

- 予測市場（Polymarket）のオッズ推移を BQ に蓄積し、国内株式の政治・マクロ感応度銘柄のタイミングシグナルとして利用可能か検証
- 既存の `tools/053_prediction_market_bot_ideas.md` と関連づけて検討
