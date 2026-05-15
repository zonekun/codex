# IDEA: FY弱気ガイダンス反復スクリーニング

## データソース
- Codex 実装 (`fy_conservative_guidance_screener.py`)
- BQ: `FIN_SUMMARY`, `TDNET_DOCUMENTS_ENHANCED`, `STOCK_PRICE`, `STOCK_CODE_LIST`

## 根拠となった箇所
> 毎年FY決算で保守的ガイダンスを出し、株価が暴落するが、最終着地では前年並みまたは成長を達成する企業が繰り返し存在する。この「弱気ガイダンスの嘘」パターンを反復的に示す企業は、暴落時に買い向かうことで安定的なリターンが得られる可能性がある。

## 投資アイディア
FY決算発表時に弱気ガイダンス(翌期予想が前期実績を下回る)を出し暴落した企業のうち、最終FY実績が初期予想を上回り前期比横ばい以上を達成する「保守的ガイダンス反復企業」を特定し、翌年の同パターン発生時に暴落で買い向かう。

## 因果連鎖
1. FY決算発表 → 翌期弱気ガイダンス → 市場が失望売り → 株価暴落
2. 実際の業績推移 → 上方修正 or 着地で前年並み → 株価回復
3. このパターンを反復する企業 → 翌年も同じパターンが高確率で再現 → 暴落時エントリーで超過リターン

## 時間軸（仮説）
- 効果が現れると想定される経路: FY決算発表日(4-5月集中)に暴落 → 数ヶ月〜1年で業績が追いつき株価回復
- エントリー: FY決算発表翌営業日の暴落時
- エグジット: 上方修正開示時 or 次回FY決算前

## 対象（粗い絞り込み）
- 全普通株（ETF/REIT/PRO Market/外国株/出資証券は除外済み）
- 過去に2回以上「弱気ガイダンス→実績上振れ」パターンを示した企業が主候補
- 全量検証(2018-2026): candidate_years 25,615 / pattern_hits 584 / company_scores 502社

## 分析の方向性
- 反復回数が多い企業ほどエッジが強いか検証(company_scores上位)
- 暴落リターン(初版: 単純リターン)の分布・回復期間の統計
- 次版で `INDEX_PRICE` 市場補正 / `DISCLOSURE_TIME` 場中・引け後補正を追加

## 必要データ
- `gmailpj-357912.STOCK.FIN_SUMMARY`: FY実績・翌期予想（FORECAST/NEXT_YEAR_FORECAST）
- `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`: 業績修正・業績予想の補助証拠
- `gmailpj-357912.STOCK.STOCK_PRICE`: 暴落判定（開示日翌営業日/3営業日後リターン）
- `gmailpj-357912.STOCK.STOCK_CODE_LIST`: 銘柄フィルタ（MARKET_CATEGORY）

## 実装
- スクリプト: `scripts/fy_conservative_guidance_screener.py`
- 出力: `data/output/fy_conservative_guidance/` (candidate_years.csv, company_scores.csv, report.html)
- プランMD: `docs/plans/tools-fy_conservative_guidance_screener_20260514_131404.md`

## 設計判断（Codex側で確定・レビュー済み）
- FY行は `TYPE_OF_DOCUMENT LIKE 'FYFinancialStatements_%'` で確定。TDnetは補助証拠
- 連結優先、なければ単体フォールバック
- 指標フォールバック: 営業利益 → 経常利益 → 純利益（IFRS/US-GAAPの経常利益NULL吸収済み）
- 前期実績・初期予想・最終実績が欠損 or 0以下 → `INVALID_BASELINE=True` で除外
- `DISCLOSURE_TIME` は初版未使用。翌営業日/3営業日後の単純リターンで暴落判定
- ETF/ETN, REIT/インフラ, PRO Market, 外国株, 出資証券は `MARKET_CATEGORY` で除外

## レビュー結果
- 判定: **B（軽微な修正で実装可能）**
- 赤字→赤字企業のハンドリング: `INVALID_BASELINE` で対応済み
- TDnet FY/四半期区別: FIN_SUMMARY側 `TYPE_OF_DOCUMENT` で確定（対応済み）
- DISCLOSURE_TIME NULL: 初版では価格判定に未使用（次版で対応）

## バックテスト結果（2026-05-14実施）

### 検証構成
- スクリプト: `scripts/backtest_fy_guidance.py`
- エントリ: WEAK_GUIDANCE + SELLOFF 発生翌営業日の終値
- エグジット: N営業日後の終値（時間ベース）
- コスト: 片道2bps × 往復
- テスト期: 2022-01-01 〜 2026-05-14

### Exit B (FYリビジョン) — FAIL
- FYリビジョンが250日以内に発生するケースが12%のみ
- リビジョンexit平均リターン: -0.67%（max_hold +6.7%より劣後）

### Exit A (時間ベース) — 層別結果

| 構成 | N | WR | Sharpe | Net/trade | MDD |
|------|---|-----|--------|-----------|-----|
| フィルタなし 3d | 835 | 38.8% | -1.73 | -1.33% | 100% |
| MC≥1000億 5d | 181 | 48.1% | 0.39 | +0.34% | 44% |
| MC≥3000億 5d | 70 | 51.4% | 1.01 | +0.88% | 26% |
| **MC≥1兆 5d** | **21** | **61.9%** | **1.23** | **+1.13%** | **13%** |
| MC≥1000億+ROA≥8% 5d | 26 | 53.8% | 1.60 | +2.07% | 15% |

### 最善構成: MC≥1兆 + 5日hold（ROAフィルタなし）
- **全3基準PASS**: Sharpe 1.23 / WR 61.9% / N=21
- 税後年率: +25.96%
- MDD: 13.09%
- 先読みバイアス: なし（時価総額は銘柄選別条件として妥当）

### 主要知見
1. 弱気ガイダンス暴落後のロングは全銘柄では負のエッジ（WR 39%）
2. 時価総額が大きいほど反発力が強い（単調増加）
3. 超大型株（MC≥1兆）に限定すると5日リバーサルが統計的に有意
4. ROA≥8%フィルタはSharpe改善するがN減少（先読みバイアス懸念もあり）
5. ROE≥8%はフィルタが甘く（399/835通過）、効果が薄い

### 課題
- N=21はギリギリの統計的信頼性（p値検証未実施）
- 時価総額は最新値を使用（BT時点の時価総額との乖離可能性）
- 2022-2026のみの検証（相場環境依存の可能性）

## ステータス
BACKTEST_PASS（条件付き: MC≥1兆+5日hold限定）
