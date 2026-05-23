# 作業計画: supply_chain.csv EDINET由来行のrelationship列補填

**作成日時**: 2026-05-16 19:55 (JST)
**ステータス**: 未着手
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/analysis/013_supply_chain_earnings_cascade.md`
**関連アイディアID**: -

## 目的

`data/master/supply_chain.csv` のEDINET由来行（236行）のrelationship列（現在全て空）を、取引内容を表す短い記述で埋める。これにより `build_association_pairs.py` のsibling派生時にセグメント整合性を検証可能にし、異セグメント間の偽sibling（例: イビデン↔アドバンテスト via AMD）を排除する基盤を作る。

## 背景・動機

### 現状の問題

`build_association_pairs.py` の `_build_sibling_pairs()` は、同一leader（共通需要ドライバー）を持つfollower同士をsibling化する。このとき **relationship列を一切参照しない**。

結果、以下のような偽siblingが発生する:

- `S_4062_6857_viaAMD`（イビデン↔アドバンテスト via AMD）
  - イビデン→AMD: 「パッケージ基板」（EDINET由来、relationship空）
  - AMD→アドバンテスト: 「テスト装置」（LLM由来、relationship記述あり）
  - 両者はAMDの**別の需要セグメント**に属するが、relationship列が空のため区別できない

### 根本原因

EDINET有報「主要な販売先」は売上依存度（%）のみ開示しており、取引内容の記述は任意。`supply_chain_extract.py` は依存度のみ抽出し、取引内容は捨てている。一方LLM由来行はrelationshipに「AIチップ向けパッケージ基板を独占的供給」等の記述がある。

### 解決戦略（2段階）

1. **本プラン**: EDINET由来行のrelationshipを埋める（データ補填）
2. **後続プラン**: `build_association_pairs.py` のsibling派生ロジックにrelationshipベースのセグメント整合性チェックを追加（ロジック改修）

データが先。ロジック改修は埋まってから。

## EDINET由来行の内訳

| 区分 | 行数 | relationship埋めの方法 |
|------|------|----------------------|
| customer_code=上場企業(JP) | 約100行 | EDINET原文チャンク + supplierの事業内容で判断 |
| customer_code=_SUB | 約25行 | 同上 |
| customer_code=NONLISTED | 約60行 | sibling派生対象外（_is_tradeable=false）→ **埋め不要** |
| customer_code=PRIVATE_FOREIGN | 約10行 | 同上 → **埋め不要** |
| customer_code=GOV_* | 約6行 | 同上 → **埋め不要** |
| customer_code=外国ティッカー | 約15行 | EDINET原文 + LLM知識で判断 |

**実質の作業対象**: customer_codeが上場企業(JP) + _SUB + 外国ティッカーの約140行。NONLISTED/PRIVATE_FOREIGN/GOV行はsibling派生に関与しないため埋め不要。

## relationship記述の粒度

LLM由来行と同程度の粒度を目指す。長すぎず、セグメント判定に十分な情報。

**良い例**（LLM由来既存）:
- `AIチップ向けパッケージ基板を独占的供給`
- `半導体モールディング装置(コンプレッション)`
- `カスタムLSI・マスクROM(売上約50%)`

**EDINET由来での目標例**:
- `4062イビデン→AMD`: `パッケージ基板(ABFサブストレート)`
- `6627テラプローブ→6723ルネサス`: `半導体テスト受託`
- `3932アカツキ→7832バンダイナムコ`: `モバイルゲーム共同開発`

**避けるべき例**:
- `売上14.7%依存`（依存度はrevenue_pct列に既にある。取引内容を書く）
- `顧客`（情報量ゼロ）

## 作業ステップ

1. [ ] **対象行抽出**: supply_chain.csvからEDINET由来 かつ customer_codeがtradeable（上場JP/外国ティッカー/_SUB）の行を抽出 → 作業リスト化
2. [ ] **EDINET原文バッチ取得**: 対象supplierのsecurity_codeでBQ `ir_documents_enhanced` から「販売実績」チャンクを一括取得 → JSONL保存
3. [ ] **relationship記述**: 各行について、EDINET原文チャンクの文脈 + supplier/customerの事業内容から取引関係を記述。原文に記載なければLLM知識で補完
4. [ ] **supply_chain.csv更新**: relationship列を埋める
5. [ ] **association_pairs.csv再生成**: `build_association_pairs.py` 実行（現時点ではrelationshipは出力に反映されるがsibling派生ロジックは未変更）
6. [ ] **013知見MD更新**: relationship補填の実施記録

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| サプライチェーンマスタ | (c') マスタCSV | `data/master/supply_chain.csv` |
| EDINET有報チャンク | (a) BQ | `gmailpj-357912.STOCK.ir_documents_enhanced` |
| 銘柄コードリスト | (a) BQ | `gmailpj-357912.STOCK.stock_code_list` |

## 成果物

- 更新済み `data/master/supply_chain.csv`（relationship列が埋まった状態）
- 再生成 `data/master/association_pairs.csv`
- 013知見MD更新

## 完了条件

- tradeable顧客を持つEDINET由来行のrelationshipが全件記入済み
- 記述粒度がLLM由来行と同程度（セグメント判定に十分な情報量）
- association_pairs.csv再生成済み

## 見積もり
- 想定所要時間: 2時間（BQチャンク取得30分 + relationship記述1時間 + CSV更新・再生成30分）
- 難易度: 低（判断は容易、量の問題）

## 後続プラン（本プラン完了後に別途作成）

`build_association_pairs.py` の `_build_sibling_pairs()` にrelationshipベースのセグメント整合性チェックを追加。同一leaderのfollower同士でrelationshipのセグメントが異なればsibling派生をスキップする。

## 振り返り（作業後に記入）
- 実際の所要時間:
- うまくいった点:
- 改善点:
- 得られた知見:
