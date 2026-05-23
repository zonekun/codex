# TOB MLモデル: オーナー色候補CSV生成の問題整理

**作成日時**: 2026-05-17 17:50 JST
**ステータス**: クローズ（2026-05-18: 問題整理内容を 007_tob_ml_prediction.md § 既知の限界・残課題 に反映済み）
**対象読者**: 次セッション担当 / 実装担当

---

## 背景

`compute_owner_features.py` でオーナー色因子（OWNER_COUNT/RATIO_IN_TOP10等）を26変数モデルに追加後、  
PRIVATE_CORP かつ1社のみ出現する「創業家資産管理会社候補」を絞り込む CSV (`C:\tmp\tob_prediction\family_holding_candidates.csv`) を  
インライン生成した。目視確認時に3つの問題が判明。

---

## 問題①: アクティビスト誤包含

**症状**: `7500 西川計測 / 株式会社UH Partners3` がリスト入り。光通信系アクティビスト（保有目的=圧力）。

**原因**: 候補フィルタ「PRIVATE_CORP + 1社のみ出現 + 5%以上」はアクティビストも満たす。  
アクティビストは1社を集中保有するため「1社のみ出現」に引っかかる。

**影響**: 誤って ASSET_MGMT に昇格させると、オーナー色因子が汚染される。

**対処**: `data/master/activist_aliases.csv` (315件のエイリアス) で名前照合して除外。  
→ 実装プラン: `analysis-007_tob_owner_filter_fix_20260517_175057.md`

---

## 問題②: 上場事業法人の誤分類（PRIVATE_CORP に混入）

**症状**:
- `4686 ジャストシステム / 株式会社キーエンス` — キーエンスは東証上場企業
- `1429 日本アクア / 株式会社桧家ホールディングス` — 桧家HDは東証上場企業

**原因**:  
`SHAREHOLDER_COMPOSITION_EXTEND` の TYPE には `LISTED_CORP` が存在しない。  
上場事業法人はルール分類で `PRIVATE_CORP` に落ちる（`CORP_SUFFIXES_JP` にマッチするため）。  
`classify_shareholder_names.py` は `STOCK_CODE_LIST` との照合を行っていない。

**影響**: 候補リストに「親子上場でも創業家でもない事業法人投資」が混入する。

**対処案（暫定）**: 候補CSV生成クエリ段階で `STOCK_CODE_LIST.STOCK_NAME` と名前照合して除外。  
**根本対処（将来）**: `classify_shareholder_names.py` に `STOCK_CODE_LIST` 照合を追加し TYPE=INSTITUTION に修正。  
→ 実装プラン（暫定対処）: `analysis-007_tob_owner_filter_fix_20260517_175057.md`

---

## 問題③: 事業法人による戦略的投資を新因子として切り出す必要性

**観察**: 上場事業法人（キーエンス→ジャストシステム、桧家HD→日本アクア）が大株主のケースは  
「親子上場」でも「創業家オーナー」でもない「取引先・事業パートナー持合い」。

**モデルへの意味**:
- TOB予測的には「安定株主による持合い＝買収防衛」として働く可能性がある
- 現状の `owner_count_in_top10` には混入しない（PRIVATE_CORP なので INDIVIDUAL/ASSET_MGMT カウントに含まれない）
- しかしこの関係性を表す因子が存在しない

**提案新因子**:
- `has_strategic_corp_investor`: TOP10 に上場事業法人が含まれるか（bool）
- 定義: TYPE=INSTITUTION かつ TOP_SHAREHOLDER_IS_PUBLIC=TRUE の株主、または STOCK_CODE_LIST にマッチする PRIVATE_CORP

**対処**: 問題②の根本修正（LISTED_CORP type追加）後に因子設計・実装。別プランで管理。

---

## 対処優先度

| # | 問題 | 優先度 | 対処方針 |
|---|------|--------|---------|
| ① | アクティビスト誤包含 | **P0** | `generate_family_holding_candidates.py` で activist_aliases 除外 |
| ② | 上場事業法人誤分類（暫定） | **P0** | 候補CSV生成時に STOCK_CODE_LIST 名前照合除外 |
| ② | 上場事業法人誤分類（根本） | P1 | `classify_shareholder_names.py` 改修（別プラン） |
| ③ | 事業法人投資の新因子 | P2 | 問題②根本修正後に設計・実装（別プラン） |
