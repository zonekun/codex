# 作業計画: EDINET遅延TOBスクリーニング 残分析フェーズ（方向性3・4）

**作成日時**: 2026-05-22 22:29 (JST)
**ステータス**: 未着手
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/analysis/008_edinet_delay_tob_screening.md`
**関連アイディアID**: -

---

## 目的

「遅延報告 × アクティビスト」複合BT（250D +13.76%, N=22）の精度・サンプル数を改善する。
方向性3（訂正報告書差分解析）と方向性4（アクティビスト新規取得イベントBT）を検証し、
既存 BACKTEST_FAIL の単体シグナルを超える複合条件を確立する。

---

## 背景・動機

008 既存分析で確立済みの知見:
- 遅延報告単体ロング → **BACKTEST_FAIL**（平均CAR≒0、中央値▲14%）
- SCORE≥8 × アクティビスト → 250D CAR+13.76%（勝率65%）だが **N=22 で統計不十分**
- 真の駆動ファクターは「アクティビストによるガバナンス改善」と判明

残TODO:
- **方向性3**: 訂正報告書に含まれる保有割合の変化を解析し「買い増し中」フィルタを追加
- **方向性4**: SHAREHOLDER_COMPOSITION の時系列変化（non-activist→activist）をシグナル化

→ 詳細: `docs/knowledges/analysis/008_edinet_delay_tob_screening.md` §次に試す方向性

---

## 方向性3: 訂正報告書差分解析

### 仮説
訂正大量保有報告書に記載される「訂正前保有割合」と「訂正後保有割合」を比較し、
**保有割合が増加している訂正のみ**をシグナルとして使う。
保有割合が減少している訂正（売却中）はシグナルから除外することで S/N 比を改善する。

### 作業ステップ

1. [ ] **現状把握**: `scripts/edinet_delay.py` の訂正報告書取得ロジックを確認
   - docTypeCode の確認（350=大量保有変更報告書、360=訂正大量保有報告書）
   - 現在どのフィールドを保存しているか確認
2. [ ] **EDINET XML パーサー実装**: `scripts/parse_edinet_correction.py`（新規）
   - 訂正報告書 PDF/XBRL から「訂正前保有割合」「訂正後保有割合」を抽出
   - 差分（Δ保有割合）を計算
   - 増加 / 減少 / 不変 の3区分フラグ付与
3. [ ] **backtest_edinet_delay.py への組み込み**
   - 既存イベントデータに訂正方向フラグを JOIN
   - 「買い増し訂正のみ」条件でサブセットBT実施
   - 比較: 全訂正 vs 買い増し訂正 vs 売り減らし訂正
4. [ ] **結果記録**: 008 知見MD §方向性3 に結果追記

### 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| EDINET訂正大量保有報告書 | EDINET API | docTypeCode=360 |
| 既存遅延報告データ | Dropbox Excel | `C:\Users\zonekun\Dropbox\stock\AI分析優待\Edinet遅延.xlsx` |
| 株価日次 OHLCV | (a) BQ | `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` |
| TOPIX | (a) BQ | `gmailpj-357912.STOCK.INDEX_PRICE` (INDEX_CODE='0000') |

### 完了条件
- 訂正方向（増/減）別のBT結果が出ている
- 買い増し訂正サブセットの CAR / 勝率 / N が 008 知見MD に記載されている

---

## 方向性4: アクティビスト新規取得イベントBT

### 仮説
「アクティビスト保有あり」の静的フラグより、
**「前期: non-activist → 今期: activist（新規取得）」の変化時点**をエントリーとする方が
シグナルの鮮度が高く、先行性のある超過リターンを得られるはず。

### 作業ステップ

1. [ ] **SHAREHOLDER_COMPOSITION スキーマ確認**
   - `docs/data_catalog/` または BQ スキーマで period_type / 変化検知に必要なフィールドを確認
   - アクティビストフラグ列の定義（`is_activist` 等）確認
2. [ ] **新規取得イベント抽出クエリ作成** (`scripts/backtest_activist_entry.py` 新規)
   - 同一銘柄の前期・今期 SHAREHOLDER_COMPOSITION を比較
   - 前期: `is_activist=False`（or 不在）→ 今期: `is_activist=True` となったレコードを抽出
   - エントリー日: アクティビストが初めて現れた期の決算公告日（翌営業日エントリー）
3. [ ] **イベントスタディBT実施**
   - 同条件: 60/120/250営業日ホールド、TOPIX比CAR
   - 比較群: 全期間アクティビスト保有 vs 新規取得のみ
   - クロス集計: 新規取得 × SCORE≥8 の複合条件も試す
4. [ ] **結果記録**: 008 知見MD §方向性4 に結果追記

### 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| アクティビスト保有時系列 | (a) BQ | `gmailpj-357912.STOCK.SHAREHOLDER_COMPOSITION` |
| 株価日次 OHLCV | (a) BQ | `gmailpj-357912.STOCK.STOCK_PRICE_JQUANTS` |
| TOPIX | (a) BQ | `gmailpj-357912.STOCK.INDEX_PRICE` (INDEX_CODE='0000') |

### 完了条件
- 新規取得イベントのBT結果（CAR/勝率/N）が出ている
- 新規取得 vs 継続保有の差異が 008 知見MD に記載されている

---

## 成果物

- `scripts/parse_edinet_correction.py`（方向性3 新規、XMLパーサー）
- `scripts/backtest_activist_entry.py`（方向性4 新規）
- `backtest_edinet_delay.py` の訂正方向フラグ組み込み（既存改修）
- 008 知見MD §方向性3・4 への結果追記

---

## 優先順位

| 優先 | 理由 |
|------|------|
| 方向性4 を先行 | BQ だけで完結（EDINET XML パースより実装コストが低い）|
| 方向性3 は後続 | EDINET XML の訂正報告書パースが未実装で実装コスト高め |

---

## 見積もり

| 方向性 | 想定工数 | 難易度 |
|--------|---------|--------|
| 方向性4 (activist entry) | 2〜3時間 | 中（BQスキーマ確認が先決） |
| 方向性3 (correction diff) | 3〜5時間 | 高（EDINET XML パース新規実装が必要） |

---

## 振り返り（作業後に記入）

- 実際の所要時間:
- うまくいった点:
- 改善点:
- 得られた知見:（008 知見MD に追記したか？）
