# ザラバ watch 改造計画: J-Quants → TDnet XBRL

**作成日**: 2026-04-07
**ステータス**: 設計済み・**TDnet適時開示ポーリング開発待ち**（多大な開発が必要。ザラバツールとしてはブロック中）
**関連**: `docs/knowledges/tools/066_zaraba_tool.md`, `scripts/zaraba_earnings.py`

## 背景・問題

J-Quants `/v2/fins/summary` はリアルタイム更新されない（バッチ更新、当日夜〜翌営業日）。
そのため watch コマンドでポーリングしても永遠に0件が返り、ザラバ中の決算発表を検知できない。

2026-04-07 に 2659（サンエー）・2734（サーラコーポレーション）で確認。prepare済み・15:00発表済みだが watch で拾えなかった。

## 改造方針

### 1. 検知: TDnet適時開示ポーリング

- J-Quants fins/summary ポーリング → **TDnet適時開示ページのポーリング**に切り替え
- TDnetはリアルタイム更新されるため、決算短信の新規開示を即座に検知可能
- 既存の tdnet 系スクリプト（`scripts/tdnet_load_parallel.py` 等）のTDnetアクセスロジックを参考に実装

### 2. 数値取得: XBRL抽出（PDF解析ではない）

- TDnetから決算短信のXBRLを取得
- XBRLから売上高・営業利益・経常利益・純利益・配当予想等を抽出
- **PDF解析（Gemini等）は使わない** — XBRLは構造化データなので正確・高速

### 3. prepare フェーズの拡張: XBRL勘定科目の事前解析

**課題**: 企業ごとにXBRLで使用する勘定科目名が異なる（例: 営業利益の要素名が企業・会計基準で違う）

**解決策**:
- prepare 時に、各銘柄の**前四半期のXBRL**をTDnetから取得
- XBRLの名前空間・要素名を事前解析し、各銘柄が使う勘定科目のマッピングをキャッシュ
- watch 時に新規XBRLを受け取ったら、事前解析済みのマッピングで即座に数値抽出

**キャッシュ構造（追加）**:
```
C:\tmp\zaraba_cache\
  20260407\
    xbrl_mappings.json    # 銘柄別 XBRL勘定科目マッピング（prepare時生成）
    calendar.csv
    prior_data.json
    seen_disc_nos.json
    results.csv
```

### 4. watch フローの変更

```
[現行]
while True:
  records = J-Quants /fins/summary?date=YYYYMMDD  ← リアルタイムじゃない
  新規DiscNo検知
  スコアリング

[改造後]
while True:
  新規開示 = TDnet適時開示ページをポーリング（決算短信のみフィルタ）
  for 開示 in 新規開示:
    XBRL取得（TDnetからダウンロード）
    事前解析済みマッピングで数値抽出
    スコアリング（既存の _score_record を改修）
```

### 5. スコアリング因子への影響

| 因子 | XBRL で取得可能か |
|------|------------------|
| F1 進捗率 | OK（累計OP + 通期予想はXBRLに含まれる） |
| F2 ガイダンス修正 | OK（予想値はXBRLに含まれる） |
| F3 YoY | OK（前年同期は prepare キャッシュから） |
| F4 翌期見通し | OK（翌期予想はXBRLに含まれる） |
| F5 出尽くし | OK（累計 + 通期予想） |
| F6 配当サプライズ | OK（配当予想はXBRLに含まれる） |
| F7 折込度合い | OK（株価系は prepare キャッシュから） |
| F8 信用売残倍率 | OK（prepare キャッシュから） |

全因子が引き続き利用可能。

## 実装時の注意事項

- TDnetへのアクセス頻度に注意（レートリミット）。既存の scraper 基盤（`docs/knowledges/tools/011_scraper.md`）の作法に従う
- XBRL解析は `docs/knowledges/tools/038_edinet_xbrl_extractor.md` の既存知見を参考（EDINET向けだがXBRL解析ロジックは共通部分あり）
- TDnetのXBRL形式はEDINETと異なる部分があるため、事前調査が必要
