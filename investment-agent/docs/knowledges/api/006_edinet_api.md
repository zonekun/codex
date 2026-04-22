# EDINET API ノウハウ

**カテゴリ**: api
**作成日**: 2026-04-03
**ステータス**: 有効
**関連ファイル**:
- `scripts/edinet_download.py` — 遅延報告取得
- `scripts/edinet_xbrl_extractor.py` — XBRL現金・有価証券抽出
- `scripts/fetch_tob_shareholders.py` — 有報から大株主抽出
- `scripts/fetch_delay_shareholders.py` — 同上（遅延報告全銘柄版）

## 概要

金融庁 EDINET API v2。有価証券報告書・大量保有報告書等の書類検索・ZIPダウンロードが可能。**無料**（APIキー必要）。

## 認証

- APIキー: `scripts/edinet_download.py` のデフォルト値に埋め込み
- リクエストパラメータ `Subscription-Key` で渡す

## エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/v2/documents.json?date=YYYY-MM-DD&type=2` | 指定日の提出書類一覧 |
| GET | `/api/v2/documents/{docID}?type=1` | XBRL ZIP ダウンロード |

## 大株主抽出ノウハウ

### XBRLタグ

有価証券報告書（docTypeCode=`120`）のXBRL内に大株主テキストブロックがある:

- **タグ名**: `MajorShareholdersTextBlock`
- **内容**: HTMLテーブル形式。カラムは「氏名又は名称」「持株数」「割合」等

### パース手順

1. XBRL ZIP ダウンロード（`/api/v2/documents/{docID}?type=1`）
2. `PublicDoc/` 配下の `.xbrl` or `.htm` を取得
3. BeautifulSoup で `MajorShareholdersTextBlock` タグを探す
4. 見つからない場合は「大株主.*状況」テキストの直後テーブルを探す
5. テーブル内の「氏名」「名称」ヘッダーで列位置特定 → データ行から株主名抽出

### 証券コードのマッチ

EDINET API の `secCode` は **5桁**（例: `18900`）。4桁銘柄コードとの照合は `secCode[:4]` で行う。

## 日付スキャンの最適化

**銘柄ごとに日付ループは非効率**。1日の書類一覧に全上場企業の提出が含まれるため:

- 日付ごとに1回API呼び出し → 全対象銘柄を一括マッチ
- 576社のdocID収集に約15分（4,400日分スキャン、0.1秒/回）
- 各社の検索ウィンドウ（対象日±180日）を事前算出し、該当日のみAPIを呼ぶ

**実績**:
- TOB上場廃止576社: Phase1(docID収集)15分 + Phase2(XBRL抽出)10分 = 計25分
- 遅延報告1,029社: TOBキャッシュ流用 + 新規937社で計3時間（Phase1が長い）

## レートリミット

- 明示的なレート制限なし（ただしリクエスト間0.1秒のスリープ推奨）
- 429/5xx に対するリトライ（5回、バックオフ1秒）を設定

## TOB公告情報抽出（公開買付届出書）

**対象書類**: docTypeCode=**240** = 公開買付届出書（TOB-F）
- 040 は「訂正有価証券届出書」であり TOB-F ではない（要注意）
- 290 = 意見表明報告書（対象会社が提出。TextBlockのみで構造化価格データなし）
- 220/230 = 自己株券買付状況報告書（自社株買い。TOB予測では除外）

**検索アルゴリズム**:
1. 対象 ticker の `secCode = TICKER+"0"` で EDINET 日付リストをスキャン → target の `edinetCode` を取得
2. 同じ期間で `docTypeCode=240` かつ `subjectEdinetCode=target_edinet` でフィルタ → TOB公告書が特定

**XBRL TextBlock タグ（prefix: `jptoo-ton_cor:`）**:

| タグ | 抽出対象 |
|------|---------|
| `PriceOfPurchaseEtcTextBlock` | 「普通株式１株につき、金X,XXX円」から TOB価格、および「前営業日の終値Y,YYY円に対してZZ.ZZ％」から公告前終値・プレミアム率 |
| `PurposesOfPurchaseEtcTextBlock` | 買付目的（参考のみ。MBO判定には使わない／ノイズ多い）|
| `OriginalPeriodAtFilingTextBlock` | 「公告日 YYYY年MM月DD日」から TOB_ANNOUNCEMENT_DATE |
| `NameOfSubjectCompanyTextBlock` | 対象会社名 |
| `FullNameOrNameOfFilerOfNotificationCoverPage` | 公開買付者名 |

**重要**: XBRL本文で MBO 判定しないこと。「対象者の代表取締役」「対象者の経営陣」等の文字列は一般的な他社株TOBでも頻出。
MBO判定は `DELISTED_STOCKS.DELISTING_REASON` に「ＭＢＯ」or「MBO」が含まれるか否かで行う（JPX公式分類）。

**実装**: `scripts/fetch_tob_announcements.py`

## 注意事項

- 2014年以前のデータは不安定（EDINET v2 API の対象期間外の可能性）
- **2013-2015年の有価証券報告書 (docTypeCode=120) は完全ゼロ件** (2026-04-20 検証確定): 2013-01-01〜2015-12-31 の 4,858日をスキャンしたが docTypeCode=120 ヒット0。EDINET v2 API が有報カバレッジ開始したのは 2016年から。論文再現で 2013-2015 の訓練データが必要な場合は代替ソースが必要
- **年別 有報件数** (`data/logs/asr_docid_index.csv` 実測): 2016年 2,841 / 2017年 3,351 / 2018年 3,689 / 2019年 3,764 / 2020年 3,803 / 2021年 3,861 / 2022年 3,895 / 2023年 3,935 / 2024年 3,948 / 2025年 3,924
- 訂正報告書は `docDescription` に「訂正」が含まれるので除外
- 上場廃止企業の有報は廃止前にしか存在しない（検索ウィンドウに注意）
- **日付レベルキャッシュ**: `C:\tmp\tob_edinet_cache\dates\YYYY-MM-DD.json` に TOB関連docのみ保存してAPI呼び出し回数を削減。複数ticker処理で重複日付は1回のみAPI叩く
