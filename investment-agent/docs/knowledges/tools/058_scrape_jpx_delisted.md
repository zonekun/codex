# 上場廃止銘柄スクレイピング（JPX + 松井証券）

**カテゴリ**: tools
**作成日**: 2026-03-28
**ステータス**: 有効
**関連ファイル**: `scripts/scrape_jpx_delisted.py`, `scripts/scrape_matsui_delisted.py`

---

## 概要

上場廃止銘柄を2つのソースから取得し、BQ `STOCK.DELISTED_STOCKS` に投入する。

| スクリプト | ソース | 取得対象 | タイミング |
|-----------|--------|---------|-----------|
| `scrape_matsui_delisted.py` | 松井証券 公開買付ページ | TOB進行中で廃止予定の銘柄 | 先行（廃止前） |
| `scrape_jpx_delisted.py` | JPX 上場廃止銘柄一覧 | 廃止確定済み銘柄 | 後追い（廃止後） |

**運用フロー**: 松井が先行INSERT（DELISTING_DATE=NULL, IS_TOB_MBO=NULL, TOB_PRICE有）→ JPX が後追いUPDATE（DELISTING_DATE, DELISTING_REASON, FISCAL_YEAR を補完）→ `/classify-tob` で IS_TOB_MBO 未判定分を分類。

### IS_TOB_MBO の意味

| 値 | 意味 | 用途 |
|----|------|------|
| TRUE | プレミアム付き買収による廃止（TOB/MBO/スクイーズアウト） | TOB予測モデル（`007_tob_ml_prediction.md`）の正解ラベル候補。さらに `IS_PAPER_TOB_LABEL` で最終フィルタ |
| FALSE | 買収以外の廃止（株式交換・合併・救済・テクニカル廃止・TDNET文書なし） | 正解ラベル対象外 |
| NULL | 未判定（スクレイピング直後の状態） | `/classify-tob` で判定する |

## 実行コマンド

### 松井証券（廃止予定の先行取得）

```bash
# 「上場廃止予定」銘柄を取得→BQ INSERT
PYTHONUTF8=1 python scripts/scrape_matsui_delisted.py

# BQ書き込みなし
PYTHONUTF8=1 python scripts/scrape_matsui_delisted.py --dry-run
```

Selenium不要（requests + BeautifulSoup）。備考列「上場廃止予定」の行のみ抽出。
INSERT時: DELISTING_DATE=NULL, IS_TOB_MBO=NULL, TOB_PRICE=買付価格。
既にBQに存在するTICKERはスキップ。

### JPX（廃止確定後の本登録）

```bash
# 全年度（新規分のみ INSERT + 松井先行分 UPDATE）
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py

# 特定年のみ
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026

# BQ書き込みなし
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026 --dry-run
```

松井で先行INSERT済み（DELISTING_DATE=NULL）のTICKERが JPX にも出現した場合、INSERT ではなく UPDATE（DELISTING_DATE, DELISTING_REASON, FISCAL_YEAR を補完）。

## IS_TOB_MBO 判定方式

スクレイピング時は `IS_TOB_MBO = NULL` で挿入。判定は `/classify-tob` スキルで実施（Claude が TDNET テキストを読んで会話内で判定）。

スキル定義: `skills/classify_tob.md`

## BQ テーブル

`STOCK.DELISTED_STOCKS`（スキーマは `data_catalog.md` 参照）

既存レコード（TICKER + DELISTING_DATE）はスキップ。IS_TOB_MBO を後から修正する場合は BQ UPDATE で直接変更。

## TOB 詳細情報のバックフィル

`scripts/scrape_jpx_delisted.py` は「廃止日・会社名・理由・IS_TOB_MBO」までしか取得しない。
TOB公告日・買付価格・プレミアム率・買付者等は EDINET 公開買付届出書から別途バックフィル:

```bash
# 未抽出レコードのみ（TOB_PRICE IS NULL）
PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --missing-only

# 特定1銘柄
PYTHONUTF8=1 python scripts/fetch_tob_announcements.py --ticker 7088 --dry-run
```

詳細: `docs/knowledges/api/006_edinet_api.md`（「TOB公告情報抽出」セクション）。

## 個別裏取り事例

EDINET 自動取得で TOB-F が見つからないケースを外部情報で補完する必要がある。以下は 2026-04-20 の手動調査結果。

### 7968 TASAKI (2017-07-27 上場廃止)

- **実態**: MBK Partners Fund III 主導の MBO / PE バイアウト（SPC 方式）
- **公告日**: 2017-03-24
- **買付者**: 株式会社スターダスト（SPC、最終支配 MBK Partners Fund III, L.P.）
- **買付価格**: **2,205円/株**
- **公告前終値**: 1,550円 (2017-03-23)
- **プレミアム**: **+42.3%**
- **買付期間**: 2017-03-27〜2017-05-11（応募83.47%、下限66.67%超過で成立）
- **EDINET 自動取得不可**: `subjectEdinetCode` 未設定、secCode=79680 マッチなし
- **BQ 手動UPDATE 済み** (`IS_PAPER_TOB_LABEL=TRUE`)
- **ソース**: 日経 / 山田コンサル / 日本M&Aセンター記事

### 7825 ダンロップスポーツ (2017-12-27 上場廃止)

- **実態**: **TOB非実施**（親子間吸収合併）
- 住友ゴム工業（5110）が 60%+ 既存保有、株式交換方式で完全子会社化→吸収合併
- 合併比率: ダンロップスポーツ 1株 : 住友ゴム 0.784株
- 対価が現金でなく株式、プレミアム 5% 超を一次資料で立証不可
- **IS_PAPER_TOB_LABEL=FALSE** のまま（論文対象外で正しい）
