# IS_TOB_MBO 分類スキル（/classify-tob）

## 目的

BQ `STOCK.DELISTED_STOCKS` の `IS_TOB_MBO = NULL` レコードを取得し、
TDNET開示テキストを読んで会話内で True/False を判定、BQ UPDATE する。

---

## 実行手順

### Step 1: 未判定レコード取得

BQ で IS_TOB_MBO IS NULL のレコードを取得する。

```sql
SELECT TICKER, COMPANY_NAME, DELISTING_DATE, DELISTING_REASON, MARKET_SEGMENT
FROM `gmailpj-357912.STOCK.DELISTED_STOCKS`
WHERE IS_TOB_MBO IS NULL
ORDER BY DELISTING_DATE DESC
```

件数をユーザーに報告し、処理を進めてよいか確認する（GOシグナル待ち）。

### Step 1.5: DELISTING_REASON による即判定（ショートカット）

以下のパターンに該当するレコードはTDNETテキストを読まずに即 False 判定する:

| パターン | 例 |
|----------|-----|
| 破産・民事再生・会社更生 | 「破産手続き」「民事再生手続き」「会社更生手続」 |
| 報告書提出遅延・虚偽記載 | 「有価証券報告書提出遅延」「半期報告書の提出遅延」 |
| 債務超過・基準不適合 | 「債務超過」「時価総額が所要額未満」「上場維持基準への不適合」 |
| 内部管理体制不備 | 「内部管理体制確認書〜」「宣誓書における重大な違反」 |

上記以外（「〜の完全子会社化」「株式の併合」「〜に合併」等）はTOBを伴う場合があるためStep 2で判定する。

ショートカットで判定したレコードも結果一覧に含めて報告する。

### Step 2: 1件ずつ判定（ショートカット対象外）

各レコードについて以下を実施:

1. BQ `STOCK.TDNET_DOCUMENTS_ENHANCED` から廃止日前180日の開示テキストを取得:
   ```sql
   SELECT DOC_TITLE, CHUNK_TEXT, SUBMISSION_DATE
   FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
   WHERE TICKER = '{ticker}'
     AND SUBMISSION_DATE BETWEEN DATE_SUB(DATE '{delisting_date}', INTERVAL 180 DAY) AND DATE '{delisting_date}'
   ORDER BY SUBMISSION_DATE, DOC_ID
   ```
2. テキスト内容を読み、以下の基準で判定:

#### True（プレミアム付き買収）
- 公開買付け（TOB）が実施された（買付者が第三者・支配株主・親会社いずれでも True）
- 経営陣による買収（MBO）
- TOB後の株式併合・株式売渡請求（スクイーズアウト）による完全子会社化

#### False（プレミアムなし・買収以外）
- 株式交換・株式移転によるグループ再編（プレミアムなし・TOBなし）
- 複数会社の合併による廃止
- 業績不振・財務危機企業の救済買収（実質倒産回避・TOBなし）
- 内部管理体制不備・基準不適合などテクニカルな上場廃止
- TDNET文書なし → False

3. 判定結果をユーザーに表示（TICKER, 会社名, 理由要約, 判定）

### Step 3: BQ UPDATE

判定結果をバッチで BQ UPDATE する。1件ずつではなく、全件の判定が終わったら CASE式でまとめて更新:

```sql
UPDATE `gmailpj-357912.STOCK.DELISTED_STOCKS`
SET IS_TOB_MBO = CASE
    WHEN TICKER = '{ticker1}' AND DELISTING_DATE = '{date1}' THEN {true/false}
    WHEN TICKER = '{ticker2}' AND DELISTING_DATE = '{date2}' THEN {true/false}
    ...
END
WHERE (TICKER, DELISTING_DATE) IN (
    ('{ticker1}', '{date1}'),
    ('{ticker2}', '{date2}'),
    ...
)
  AND IS_TOB_MBO IS NULL
```

更新件数を報告する。

---

## 注意事項

- 判定に迷う場合はユーザーに確認する（勝手に決めない）
- DELISTING_REASON の文字列（「他社による買収」「ＭＢＯ」等）も判断材料に使う
- 大量（20件超）の場合は10件ずつバッチ処理し、途中経過を報告
- TDNET文書が存在しない期間（2025/04〜12のBQ未ロード期間等）は False とし、理由を明記
