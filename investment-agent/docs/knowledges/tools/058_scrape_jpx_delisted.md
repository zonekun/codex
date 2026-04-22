# JPX 上場廃止銘柄スクレイピング（scrape_jpx_delisted.py）

**カテゴリ**: tools
**作成日**: 2026-03-28
**ステータス**: 有効
**関連ファイル**: `scripts/scrape_jpx_delisted.py`

---

## 概要

JPX 上場廃止銘柄一覧をスクレイピングし、BQ `STOCK.DELISTED_STOCKS` に追加する。
ローカル随時起動。Chrome + Selenium で JPX サイトを取得。

## 実行コマンド

```bash
# 全年度（新規分のみ INSERT）
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py

# 特定年のみ
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026

# BQ書き込みなし（スクレイピング結果確認）
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --year 2026 --dry-run

# IS_TOB_MBO 判定精度を既存10件で評価（BQ更新なし）
PYTHONUTF8=1 python scripts/scrape_jpx_delisted.py --eval --eval-year 2025
```

## IS_TOB_MBO 判定方式

BQ `TDNET_DOCUMENTS_ENHANCED` から廃止日前180日分のテキストを取得し Gemini で判定。

| 判定 | 条件 |
|------|------|
| True | TOB・MBO・スクイーズアウト（プレミアム付き買収） |
| False | 株式交換・合併・テクニカル上場廃止・業績不振救済 |
| False | TDNET 文書なし（BQ未ロード期間含む） |

**注意**: 2025/04〜12 の TDNET データは BQ 未ロード。この期間に廃止した銘柄は文書なし → False になるため手動確認推奨。

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
