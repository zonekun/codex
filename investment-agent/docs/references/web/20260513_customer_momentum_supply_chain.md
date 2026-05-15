# Customer Momentum / サプライチェーン決算連鎖 — 論文サーベイ

## 概要

サプライチェーン上の顧客-サプライヤー関係を利用した株式リターン予測可能性（Customer Momentum）に関する主要論文群。投資家の限定的注意（limited attention）により、顧客企業の情報がサプライヤー株価に遅れて反映されるアノマリー。

---

## 原典

### Cohen & Frazzini (2008) "Economic Links and Predictable Returns"

- **著者**: Lauren Cohen, Andrea Frazzini
- **掲載**: Journal of Finance, 2008
- **PDF**: [CohenFrazzini2008_economic_links.pdf](CohenFrazzini2008_economic_links.pdf)
- **URL**: http://www.econ.yale.edu/~shiller/behfin/2006-04/cohen-frazzini.pdf

**要旨**: 企業間の経済的リンク（顧客-サプライヤー関係）に沿って情報が緩慢に伝播するため、顧客企業のリターンがサプライヤー企業の将来リターンを予測できることを実証。

**戦略（Customer Momentum）**:
- 毎月、各企業の主要顧客の前月リターンでソート
- 顧客リターン上位（Long）- 下位（Short）のL/Sポートフォリオ
- 月次リターン: 150bps（EW）、αは業種モメンタム控除後も有意

**データソース**: Compustat Segment Files（米SEC提出の主要顧客開示）

**メカニズム**: Limited Attention（投資家が経済的に関連する企業の情報を十分に処理しない）

---

## 再検証・発展

### Pinchuk (2023) "Customer Momentum"
- **URL**: https://arxiv.org/abs/2301.11394
- EW decile L/S: 月次122bps（t>4）、VW: 106bps（t>2.8）
- **発見後の効果縮小を報告**: 統計的有意性が低下。米国大型株では裁定が進行

### Chen (2012) "Information Diffusion of Upstream and Downstream Industry-Wide Earnings Surprises"
- **URL**: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2020850
- **業種レベル**の決算サプライズが川上・川下に波及
- 顧客/サプライヤー業種リターンを組み合わせたL/S戦略が有意に profitable

### Madsen (2017) "Anticipated Earnings Announcements and the Customer-Supplier Anomaly"
- **URL**: https://ideas.repec.org/a/bla/joares/v55y2017i3p709-741.html
- **決算発表タイミング**に焦点。サプライヤー決算発表直前に顧客情報への注目が増加
- 決算発表前には予測力あり、発表後は消失

### Shahrur, Becker, Rosenfeld (2010) "Return Predictability Along the Supply Chain: The International Evidence"
- **URL**: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1583927
- **先進国横断**で顧客→サプライヤーの予測可能性を確認

### Gupta (2023) "Supplier-Customer Linkages and Stock Return Predictability"
- **URL**: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4602687
- 1,083ペア / 1983-2011。顧客決算発表前後のサプライヤーCARが正の関係

### Jain & Wu (2023) "Can Global Sourcing Strategy Predict Stock Returns?"
- **URL**: https://ssrn.com/abstract=3606884
- グローバル調達戦略で年次α 6-9.6%（4ファクター調整後）

---

## 実務的拡張

### FactSet: Supply Chain Signals (2026)
- **URL**: https://insight.factset.com/supply-chain-signals-enhancing-the-customer-momentum-strategy-with-network-centrality
- FactSet Supply Chain Relationships DB（53,000社）使用
- **Katz中心性**で重み付け → Sharpe 0.38→0.71に改善
- Russell 1000、20年バックテスト（2006-2025）

### Yamamoto, Kawadai, Miyahara (2021): Propagating Momentum Through Global Supply Chain Networks
- **URL**: https://insight.factset.com/propagating-momentum-information-through-global-supply-chain-networks
- **日本含むグローバル**。27,000社、2003-2019
- **12ヶ月**顧客モメンタムが1ヶ月より優秀（ターンオーバー低減）
- **4層先**まで見ると最適
- Edge Betweenness Centrality で重み付け

---

## 日本市場への示唆

- 日本ではモメンタム自体が効きにくいとされる（AQR "Momentum in Japan: The Exception That Proves the Rule"）
- ただしサプライチェーンモメンタムは通常のモメンタムとは異なるメカニズム（情報伝播遅延）
- Shahrur et al. (2010) は先進国横断で効果を確認（日本含む可能性）
- Yamamoto et al. (2021) は明示的に日本市場を含む
- **個人投資家レベルでのデータソース**: EDINET有報の「主要な販売先」、TDnet適時開示

---

## キーワード

customer momentum, supply chain, earnings surprise, limited attention, upstream, downstream, Cohen Frazzini, サプライチェーン, 決算またぎ, 連想買い, 川上, 川下
