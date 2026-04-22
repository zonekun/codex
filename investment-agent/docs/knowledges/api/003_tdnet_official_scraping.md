# TDnet 適時開示データ取得仕様

**カテゴリ**: api
**作成日**: 2026-02-24
**ステータス**: 有効
**関連ファイル**: `scripts/tdnet_download.py`

## 概要

TDnet（東証の適時開示システム）から適時開示の一覧とPDFファイルを取得する方法。
現行実装は公式サイトのHTMLスクレイピングを採用。
yanoshin非公式APIは廃止リスクのため不使用だが、将来の代替手段としてバックアップ記録を残す。

## ⚠️ TDnet のデータ保持期間（重要）

**TDnet は PDF ファイルの保持期間が限定的**。確認済みの挙動:

| データ | 保持期間の目安 | 備考 |
|--------|-------------|------|
| 一覧ページ（HTML） | 直近 30〜90 日程度 | 古い日付は `I_list_NNN_YYYYMMDD.html` が 404 になる |
| PDF ファイル | 直近 30〜90 日程度 | 古い日付の PDF は `140120250115XXXXXX.pdf` が 404 になる |
| yanoshin API | 少なくとも 2025年分は返却可能 | メタデータのみ。PDF は TDnet サーバに存在しないためダウンロード不可 |

**2026-03-03 に確認**: 2025-01-15 の yanoshin API → 180件のメタデータ取得成功、しかし PDF 全件 404。

### 対策（過去データ取得不可の場合）

1. **メタデータのみ取得**: index CSV（会社名・カテゴリ・タイトル等）は yanoshin API 経由で取得可能。PDF なしのカタログとして活用できる
2. **JPX 有料 API**: 月額 ¥70,000〜（5年分の履歴 PDF 取得可能）
3. **諦める**: 過去分は取得不可として割り切り、今後の分のみ蓄積する

---

## 現行実装: 公式TDnetサイト スクレイピング

### URL構造

```
# 1日分の開示一覧（100件/ページ、複数ページあり）
https://www.release.tdnet.info/inbs/I_list_NNN_YYYYMMDD.html
                                          ^^^        ^^^^^^^^
                                        ページ番号    日付(YYYYMMDD)

例:
  1ページ目: https://www.release.tdnet.info/inbs/I_list_001_20260224.html
  2ページ目: https://www.release.tdnet.info/inbs/I_list_002_20260224.html
  3ページ目: https://www.release.tdnet.info/inbs/I_list_003_20260224.html
```

### HTMLテーブル構造

開示テーブルは `kjTime` クラスのセルを含む `<table>` を探す（テーブルのインデックスに依存しない）。

| CSSクラス（奇数行） | CSSクラス（偶数行） | 内容 |
|--------------------|--------------------|------|
| `oddnew-kjTime`    | `evennew-kjTime`   | 開示時刻（HH:MM） |
| `oddnew-M kjCode`  | `evennew-M kjCode` | 銘柄コード（5桁） |
| `oddnew-kjName`    | `evennew-kjName`   | 会社名 |
| `oddnew-kjTitle`   | `evennew-kjTitle`  | タイトル + PDFリンク（`<a href>`） |
| `oddnew-kjXbrl`    | `evennew-kjXbrl`   | XBRLリンク（決算短信のみ） |

奇数行は `oddnew-*`、偶数行は `evennew-*` と交互に変わる。
→ `"kjTime" in cls` のように部分マッチで判定する。

### PDFファイルURL

```
https://www.release.tdnet.info/inbs/<filename>.pdf

ファイル名の構造:
  1401      20260224  567356   .pdf
  ↑固定4桁  ↑開示日   ↑連番6桁
```

- 先頭 `1401` は全ファイル共通の固定プレフィックス
- 続く8桁が開示日（YYYYMMDD）
- 末尾6桁が日次連番（yanoshin APIの `id` とは別採番）

### ページネーション

- 1ページあたり最大100件
- 100件超で複数ページに分割
- ページが存在しない場合はHTTP 404 → `fetch_one_page()` が空リストを返す
- 休日・祝日のページは存在しない（自動スキップ）
- ページャー要素で総件数も確認可能：
  ```html
  <div class="kaijiSum">1～100件 / 全296件</div>
  ```

### 実装上の注意

- ページ取得間隔: **0.5秒**（TDnetサーバへの礼儀。変更しないこと）
- PDFダウンロード間隔: **0.3秒**
- `robots.txt` は 403 で非公開。常識的なアクセス頻度を保つ
- `<a href>` の値は相対パスの場合あり → `urljoin(TDNET_BASE_URL, href)` で絶対URLに変換

### スクレイピング依存ライブラリ

```
beautifulsoup4>=4.12  （pyproject.toml に記載済み）
lxml>=4.9             （同上）
httpx>=0.27           （同上）
```

---

## バックアップ: yanoshin TDnet WEB-API（非公式）

> **注意**: 非公式APIのため廃止リスクあり。現行実装では**使用していない**。
> 公式サイトの構造が大きく変わった場合の代替手段として仕様を記録する。

### 概要

- 提供者: やの信者（個人運営）
- URL: https://webapi.yanoshin.jp/tdnet/
- 認証: 不要（完全無料）
- 更新: 東証データと数分間隔で同期

### APIエンドポイント

```
ベースURL: https://webapi.yanoshin.jp/webapi/tdnet/list/{condition}.{format}

condition（日付・銘柄の指定）:
  recent                 最新情報
  today / yesterday      本日 / 昨日
  YYYYMMDD               単一日付
  YYYYMMDD-YYYYMMDD      日付範囲（例: 20260217-20260224）
  7203                   単一銘柄コード
  7203-9984-4689         複数銘柄（ハイフン区切り）

format:
  json / xml / rss / atom / html

クエリパラメータ:
  ?limit=9999   取得件数上限（デフォルト300）
  ?hasXBRL=1    XBRLあり開示のみ
```

### レスポンス構造（JSON）

```json
{
  "total_count": 1245,
  "items": [
    {
      "Tdnet": {
        "id": "1228305",
        "pubdate": "2026-02-24 19:30:00",
        "company_code": "40120",
        "company_name": "アクシス",
        "title": "新中期経営計画「Go Beyond」策定に関するお知らせ",
        "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260224568061.pdf",
        "url_report_type_summary": null,
        "url_report_type_fs_consolidated": null,
        "url_report_type_fs_non_consolidated": null,
        "url_xbrl": null,
        "markets_string": "東"
      }
    }
  ]
}
```

`document_url` はリダイレクト形式。実際のURLは `rd.php?` 以降を取り出す：
```python
real_url = doc_url.split("rd.php?", 1)[1]
```

### yanoshinの利点（復活させる場合の参考）

| 項目 | yanoshin | 公式スクレイピング |
|------|---------|-----------------|
| 日付範囲 | 1リクエストで完結 | 1日1ページずつループ |
| 書類種別フィールド | `url_report_type_summary` 等あり | なし |
| 市場区分 | `markets_string` あり | なし |
| ページネーション | 不要 | 必要 |
| 廃止リスク | △ 非公式 | ◎ 公式 |

### Python復旧コード（yanoshin）

```python
import httpx

def fetch_disclosures_yanoshin(date_from: str, date_to: str) -> list[dict]:
    """yanoshin APIから開示一覧を取得する（バックアップ用）."""
    url = (
        f"https://webapi.yanoshin.jp/webapi/tdnet/list/"
        f"{date_from}-{date_to}.json?limit=9999"
    )
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = httpx.get(url, timeout=60, headers=headers, follow_redirects=True)
    data = resp.json()
    items = [item["Tdnet"] for item in data.get("items", []) if "Tdnet" in item]

    # document_url のリダイレクトを解決
    for item in items:
        doc = item.get("document_url", "")
        if "rd.php?" in doc:
            item["document_url"] = doc.split("rd.php?", 1)[1]
    return items
```

---

## 公式JPX有料API（参考）

- URL: https://www.jpx.co.jp/markets/paid-info-listing/tdnet/02.html
- 月額: ¥70,000〜（個人用途には不向き）
- 5年間の履歴取得可能
- Swagger仕様書: `https://apidoc-tdnet.jpx-dataservice.com/`（要認証）

---

## 関連ファイル

- `scripts/tdnet_download.py` — 実装本体（現行: 公式スクレイピング）
- `data_catalog.md` — TDnetデータのカタログエントリ
- `docs/knowledges/api/001_jquants_api.md` — J-Quants API仕様
