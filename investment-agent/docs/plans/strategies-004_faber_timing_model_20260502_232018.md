# 作業計画: Faber (2007) 5資産タイミングモデル

**作成日時**: 2026-05-02 23:20 (JST)
**ステータス**: 未着手
**分類**: (a) 恒久知見型
**親知見 MD**: `docs/knowledges/strategies/004_faber_timing_model.md`（新規作成）
**関連ア���ディアID**: -

## 目的

Faber (2007) "A Quantitative Approach to Tactical Asset Allocation" に基づき、5資産クラスの10ヶ月SMA（≒200日移動平均）タイミングモデルを実装する。日次で攻め/守りシグナルを出す市場センチメント指数。

## 背景・動機

- 既存の `menu_phase_analyzer.ipynb` は金融政策×景気の2軸で4局面判定するが、Faber式の「価格 vs 長期MA」というシンプルなルールベース判定は未実装
- RakuScan の regime プラグイン（リファレンス #3）が TOPIX×200日MA で同種の判定をしており、これを5資産に拡張
- 論文原文: `docs/references/web/Faber2007_SSRN-id962461.pdf`（リファレンス #3a）

## 設計方針

### Faber ルール

> 月末終値が10ヶ月SMA（≒200日移動平均）を上回れば BUY（攻め）、下回れば SELL（守り）

- 判定は**日次**（論文は月末だが、日次で最新状態を出す）
- 5資産のうち BUY が多いほどリスクオン、SELL が多いほどリスクオフ

### 5資産クラスとデータソース

| # | 資産クラス | Faber 原論文 | データソース | 取得方法 |
|---|-----------|-------------|-------------|---------|
| 1 | 米国株 | S&P 500 | BB_債券履歴_new.xlsx `LIST` シート col[23] | Dropbox API DL |
| 2 | 先進国株（除く米加） | MSCI EAFE | ETF `EFA` | yfinance |
| 3 | 商品 | S&P GSCI | BB_債券履歴_new.xlsx `LIST` シート col[13]（CRB代替） | Dropbox API DL |
| 4 | REIT | NAREIT | ETF `VNQ` | yfinance |
| 5 | 米10年国債 | 米10年国債利回り | BB_債券履歴_new.xlsx `LIST` シート col[2]（利回り反転） | Dropbox API DL |

**LISTシートを使う理由**: 個別シート（SP500/CRB/GBOND）はヘッダ構造がバラ��ラで欠損あり。LISTシートは欠損補完済み（99.7〜100%）で1998年〜7000行超。col[0]=Date, col[2]=Gbond10y, col[13]=CRB, col[23]=SP500。

**GBOND 判定（確定）**: 利回りデータなので SMA 判定を**反転**する。現在利回り < SMA200 → BUY（債券価格上昇中＝資産クラスとして強い）。追加データソース不要。

**TOPIX は不要**: 米国市場がダメなら日本市場もダメの前提。

### 補助指標（Excel から追加取得）

既に手持ちの Excel にある指標を組み合わせてリッチにできる:

| 指標 | シート | 用途 |
|------|--------|------|
| VIX | `VIX` | 恐怖水準の閾値判定 |
| Fear & Greed | `FAG` | センチメント補助 |
| SKEW | `SKEW` | テールリスク |
| BEI | `BEI` | インフレ期待 |
| HYG | `HYG` | クレジットスプレッド（リスクオン/オフ） |

→ Phase 1 では Faber 5資産のみ。Phase 2 で補助指標追加を検討。

## 作業ステップ

### Phase 1: コア実装

1. [ ] 知見MD作成: `docs/knowledges/strategies/004_faber_timing_model.md`
2. [ ] スクリプ���作成: `scripts/faber_timing.py`
   - Dropbox API で Excel DL（016_dropbox.md パターン）
   - SP500 / CRB / GBOND シートをパース
   - yfinance で EFA / VNQ 取得
   - 各資産の 200日SMA 算出 → BUY/SELL ���定
   - 5資産の総合スコア（0〜5 の BUY 数）算出
   - 結果を構造化ログ出力 + ntfy 通知
3. [ ] ローカル動作確認（`--dry-run`）

### Phase 2: Cloud Run デプロイ

5. [ ] Dockerfile 作成（005_cloudrun_job_deploy.md 準拠）
6. [ ] Dropbox 認証情報を Secret Manager に格納
7. [ ] Cloud Run Job 作成 + Cloud Scheduler 登録（日次 JST 7:00 = 米国市場終了後）
8. [ ] 本番動作確認

### Phase 3: 拡張（後日）

9. [ ] 補助指標（VIX / F&G / SKEW / BEI / HYG）追加
10. [ ] menu_phase_analyzer.ipynb との統合ダッシュボード検討
11. [ ] BQ への履歴蓄積（日次判定結果をテーブル化）

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| S&P 500 日次 | Dropbox xlsx | `/stock/BB_債券履歴_new.xlsx` → `SP500` シート |
| CRB 日次 | Dropbox xlsx | `/stock/BB_債券履歴_new.xlsx` → `CRB` シー�� |
| 米国債利回り | Dropbox xlsx | `/stock/BB_債券履歴_new.xlsx` → `GBOND` シート |
| MSCI EAFE (EFA) | yfinance | `yf.download("EFA", period="2y")` |
| 米REIT (VNQ) | yfinance | `yf.download("VNQ", period="2y")` |

## 成果物

- `scripts/faber_timing.py` — メインスク���プト
- `docs/knowledges/strategies/004_faber_timing_model.md` — 知見MD
- Cloud Run Job `faber-timing` — 日次実行ジョブ
- ntfy 通知 — 日次の攻め/守りシグナル

## 出力イメージ

```
=== Faber Timing Model (2026-05-02) ===
S&P 500:  5,800 vs SMA200 5,420  → BUY  ✓
EFA:        82.3 vs SMA200  78.1  → BUY  ✓
CRB:       285.2 vs SMA200 290.5  → SELL ✗
VNQ:        91.0 vs SMA200  88.7  → BUY  ✓
US10Y Bond: 4.21 vs SMA200  4.35  → BUY  ✓ (利回り < SMA = 価格上昇)

総合: 4/5 BUY → リスクオン（攻��）
```

## 完了条件

- [ ] ローカルで `scripts/faber_timing.py` が正常実行し、5資産の BUY/SELL 判定が出力される
- [ ] Cloud Run Job として日次自動実行され、ntfy 通知が届く
- [ ] 知見MD・INDEX.md が更新されている

## 見積もり

- Phase 1: 2時間
- Phase 2: 1時間
- 難易度: 低〜中（データ取得パターンは既存流用、ロジックはシンプル）

## 振り返り（作業後に記入）

- 実際の所要時間:
- うまくいった点:
- 改善点:
- 得られた知見:

---

## レビュー追記: 2026-05-02 23:29 JST — code-reviewer

→ `docs/reviews/063_cr_faber_timing_model.md`
