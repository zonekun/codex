# 013-1: サプライチェーンマスタ構築記録（2026年5月版）

**親文書**: `docs/knowledges/analysis/013_supply_chain_earnings_cascade.md`
**作成日**: 2026-05-16
**対象マスタ**: `data/master/supply_chain.csv`（302行）、`data/master/association_pairs.csv`（736行）

本文書は supply_chain.csv の2026年5月版マスタの構築方法・品質管理・偽陽性除去の設計記録。年次更新時の参照用。

---

## 偽陽性除去（2026-05-16実施）

### 目的

`data/master/supply_chain.csv`（240行 EDINET + 66行 LLM）のfuzzy match / LLM名寄せで生じた偽陽性（customer_codeが誤った銘柄にマッピングされているエントリ）を修正し、連想ペアCSVの品質を向上させる。

### 背景

supply_chain.csv は EDINET有報「主要な販売先」から抽出した取引ファクト。名寄せ3段階（rapidfuzz WRatio≥90 → LLM判定 → BQ逆引き）を経ているが、match_score=90のエントリに**部分文字列一致による誤マッチ**が多数存在する。

この偽陽性は `build_association_pairs.py` で連想ペア（847行）に増幅され、スクリーニング結果にノイズとして出現する。決算シーズン初回運用テスト中のため早期修正が必要。

→ 既知問題: 013知見MD §データ品質 既知問題（6083 ERIバグ3件のみ記載）

## 偽陽性の類型

### 類型1: 441A (NE) 大量誤マッチ

441Aは外国企業・無関係企業の受け皿として誤使用。

| 行 | supplier | customer_name | 正しい帰属 |
|----|----------|--------------|-----------|
| L11 | 2303 ドーン | STNet | NONLISTED（四国電力系ISP） |
| L49 | 3441 山王 | JAE Philippines Inc. | 6807 日本航空電子 or PRIVATE_FOREIGN |
| L118 | 4772 ストリームメディア | ON THE LINE | NONLISTED |
| L120 | 4777 ガーラ | Wemade Connect Co., Ltd. | PRIVATE_FOREIGN（韓国ゲーム） |
| L154 | 6047 Gunosy | Mediavine, Inc. | PRIVATE_FOREIGN（米国AdTech） |

### 類型2: 4378 (CINC) 大量誤マッチ

| 行 | supplier | customer_name | 正しい帰属 |
|----|----------|--------------|-----------|
| L13 | 2337 いちご | 合同会社KCR1 | NONLISTED（SPV） |
| L54 | 3624 アクセルマーク | CTW株式会社 | NONLISTED（ゲーム） |
| L184 | 6836 ぷらっとホーム | SB C&S | NONLISTED（SBG子会社） |

### 類型3: 6083 (ERI) 北米子会社誤マッチ（既知バグ）

| 行 | supplier | customer_name | 正しい帰属 |
|----|----------|--------------|-----------|
| L151 | 5970 ジーテクト | Honda of America Mfg.,Inc. | 7267 ホンダ |
| L194 | 7266 今仙電機 | NHK Seating of America,Inc. | 5991_SUB 日本発条子会社 |
| L198 | 7291 日本プラスト | Nissan North America, Inc. | 7201 日産自動車 |

### 類型4: 部分文字列一致による誤マッチ（match_score=90）

| 行 | customer_name | 誤マッチ先 | 正しい帰属 |
|----|--------------|-----------|-----------|
| L69 | バンダイナムコエンターテインメント | 7217 テイン | 7832 バンダイナムコHD |
| L71 | NHKエンタープライズ | 4849 エン | NONLISTED（NHK子会社） |
| L73 | 明治安田システムテクノロジー | 2269 明治HD | NONLISTED（明治安田生命子会社） |
| L77 | Advanced Micro Devices Inc. | 300A MIC | AMD（外国ティッカー） |
| L90 | ゲームフリーク | 4478 フリー | NONLISTED（非上場） |
| L111 | ViiV Healthcare Ltd. | 5857 AREホールディングス | PRIVATE_FOREIGN（GSK/Pfizer/塩野義JV） |
| L132 | トラストバンク | 3286 トラストHD | NONLISTED（SBG系子会社） |
| L138 | 一建設 | 1799 第一建設工業 | 3475_SUB（飯田グループHD子会社） |
| L140 | 日本ライフサポート | 217A サポート | NONLISTED |
| L148 | セキスイハイム工業 | 7857 セキ | 4204_SUB（積水化学子会社） |
| L158 | ブルックス | 8029 ルックHD | NONLISTED（コーヒー通販） |
| L165 | ホクレンくみあい飼料 | 3076 あいHD | NONLISTED（農協系） |
| L166 | JA全農くみあい飼料 | 3076 あいHD | NONLISTED（農協系） |
| L236 | NTTドコモ | 2224 コモ | 9432 NTTドコモ（→親9432） |

### 類型5: データ破損・ガベージ

| 行 | 問題 |
|----|------|
| L152 | customer_name = "of America, LLC" → 4769 IC（名前が切断） |

### 類型6: 重複エントリ

| 行ペア | supplier | customer | 問題 |
|--------|----------|----------|------|
| L34/L35 | 3204 トーア紡 | 7209 林テレンプ | 表記ゆれ（㈱ vs 株式会社） |
| L91/L93 | 4238 ミライアル | 3436 SUMCO | 微差（末尾ピリオド） |
| L94/L96 | 4262 ニフティライフスタイル | 6098 リクルート | 表記ゆれ |
| L95/L97 | 4262 ニフティライフスタイル | 2120 LIFULL | 表記ゆれ |

### 類型7: 要検証（判断保留）

| 行 | customer_name | 現マッチ | 疑義 |
|----|--------------|---------|------|
| L3 | 合同会社第一トラスト太陽光発電 | 3286 トラストHD | SPV、トラストHD関連か不明 |
| L17 | JMインダス５合同会社 | 3539 JMホールディングス | JM HD子会社SPVの可能性 |
| L31 | イオンモール㈱ | 8267 イオン | 8905 イオンモールが正しい可能性 |
| L37 | エムエル・エステート | 4951 エステー | 不動産≠日用品。正しくはNONLISTED? |
| L41 | フィル・パーク鎌倉プロジェクト | 9246 プロジェクトHD | SPV |
| L50 | ㈱鈴木 | 6785 鈴木 | 6785は鈴木株式会社。同名か要確認 |
| L68 | ソリューションズ株式会社 | 2338 クオンタムソリューションズ | 汎用名。正体不明 |
| L72 | 大和総研 | 8247 大和 | 大和証券G(8601)子会社 |
| L109 | PIPO (SG) Pte. Ltd. | 9143 SGホールディングス | 無関係の可能性高い |
| L141 | 伊藤忠丸紅住商テクノスチール | 8002 丸紅 | 3社JV。丸紅のみに帰属させるのは不正確 |
| L160 | ミクロ技研 | 1443 技研HD | 1443はミクロ技研ではなく技研HD |

## 要検証エントリの検証手法

### 手段A: BQ銘柄マスタ逆引き

マッチ先stock_codeが実際にどの会社か確認。1クエリで一括検証。

```sql
SELECT stock_code, company_name
FROM `gmailpj-357912.STOCK.stock_code_list`
WHERE stock_code IN ('6785','8247','300A','1443','9246','217A','8905')
```

**対象**: L31(イオンモール→8267 or 8905)、L50(鈴木→6785)、L72(大和総研→8247)、L160(ミクロ技研→1443)

### 手段B: 子会社・SPV関係の裏取り

「この顧客名はマッチ先企業の子会社か？」を判定。Codex自身のドメイン知識またはWeb検索で確認。

**対象**: L3(第一トラスト太陽光→トラストHD)、L17(JMインダス→JM HD)、L141(伊藤忠丸紅住商テクノスチール→丸紅)

### 手段C: EDINET原文チャンク再確認

汎用名で正体不明なケース。BQ `ir_documents_enhanced` で該当supplierのチャンクを再読みし前後文脈で正体を特定。

```sql
SELECT chunk_text
FROM `gmailpj-357912.STOCK.ir_documents_enhanced`
WHERE security_code = '<supplier_code>'
  AND submission_date >= '2025-01-01'
  AND doc_type = '有価証券報告書'
  AND (chunk_text LIKE '%販売実績%' OR chunk_text LIKE '%主要な販売先%')
```

**対象**: L68(ソリューションズ株式会社→2338)

### 各エントリの検証割り振り

| # | エントリ | 手段 | 予想結論 |
|---|---------|------|---------|
| L3 | 第一トラスト太陽光発電 | B | SPV → NONLISTED濃厚 |
| L17 | JMインダス５ | B | JM HD子会社SPVの可能性。要確認 |
| L31 | イオンモール | A | 8905が正。8267イオンは親 |
| L37 | エムエル・エステート | — | 不動産≠日用品、NONLISTED確定 |
| L41 | フィル・パーク鎌倉 | — | SPV → NONLISTED確定 |
| L50 | ㈱鈴木 | A | 6785が何かBQで確認 |
| L68 | ソリューションズ | C | EDINET原文で正体確認 |
| L72 | 大和総研 | A | 8247確認→8601_SUB |
| L109 | PIPO (SG) | — | SG文字列一致だけ。PRIVATE_FOREIGN確定 |
| L141 | 伊藤忠丸紅住商テクノスチール | B | 3社JV。NONLISTED扱いが正確 |
| L160 | ミクロ技研 | A | 1443が何かBQ確認 |

**判定フロー**: 手段なし（ドメイン知識で即決）4件 → 手段A（BQ 1クエリ）4件 → 手段B（子会社確認）3件 → 手段C（原文再読み）1件。実質BQ 1本 + 原文1件で大半が決着する。

### 実施結果

- match_score=90 全80件を精査: WRONG 60件 / VERIFY→解決 11件 / OK 17件
- supply_chain.csv: 306行→302行（60件修正 + 4組重複統合）
- association_pairs.csv: 847行→736行（偽sibling 111件消滅、正規化で4件追加）
- commit: `eaacdd22`

### 年次更新時の注意

- match_score=90エントリは全件目視レビュー必須
- 短い社名（NE/CINC/ERI/テイン/エン/コモ等）が誤マッチ受け皿化しやすい
- 子会社は `<親コード>_SUB` で統一（sibling派生対象外になる）
- 重複は表記ゆれ（㈱ vs 株式会社）で発生。revenue_pct大きい方を残す
