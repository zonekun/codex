# IFIS直取得コンセンサス追加 実装プラン

**作成日時**: 2026-04-27 21:44 JST  
**対象ファイル**:
- `scripts/update_conse_rakuten.py`
- 新規 `scripts/update_conse_ifis.py`
- BigQuery `gmailpj-357912.STOCK.CONSENSUS`

**対象読者**: source-code-reviewer / 次実装担当  
**目的**: 楽天証券経由とは別に、IFIS株予報ページ直取得の経常利益コンセンサスを `STOCK.CONSENSUS` に別レコードとして保存する。既存RAKUデータとは `SOURCE` で分離する。

---

## レビュー結果（2026-04-27）

**判定**: Request changes

実装前に以下をプランへ反映する。

- IFISレコードを本テーブルへ1行でも投入する前に、既存本番系クエリを `SOURCE='RAKU'` 固定へ修正する。
- IFIS全件投入は `insert_rows_json` ではなく Load Job + staging + MERGE または DELETE+INSERT で冪等化する。
- IFIS抽出は `rows[2]` 固定ではなく、行ヘッダ `コンセンサス予想` を探す。
- `FY` 抽出は NFKC 正規化後に `今期 YYYYMM` を抽出し、月 `01`〜`12` を検証する。
- 既存行 `SOURCE='RAKU'` 埋め戻しは streaming buffer 制約を考慮する。

---

## 結論

`STOCK.CONSENSUS` に `SOURCE STRING` だけを追加する。

- 既存楽天版: `SOURCE='RAKU'`
- 新IFIS直版: `SOURCE='IFIS'`

`RAW_SOURCE_URL` / `SCRAPED_AT` は追加しない。取得日は既存の `DATAAT` で足りる。

IFIS直版では `FY='000000'` を使わない。IFISページ内の「今期 YYYYMM」セルから `FY` を抽出し、取れない銘柄はスキップする。

---

## 背景

既存 `scripts/update_conse_rakuten.py` は楽天証券内 iframe のIFISコンセンサスを取得し、`STOCK.CONSENSUS` にロードしている。

今回追加するIFIS直版は、Excel VBA `Sub setValueB` と同じページ構造を使う。

対象URL:

```text
https://kabuyoho.ifis.co.jp/index.php?action=tp1&sa=report&bcode={ticker}
```

VBA該当箇所:

```vba
Set t = objIE.Document.getElementsByClassName("prog_quarter")(0)

rn = 2
c1q = t.getElementsByTagName("tbody")(0).getElementsByTagName("tr")(rn).getElementsByTagName("td")(0).innertext
c2q = t.getElementsByTagName("tbody")(0).getElementsByTagName("tr")(rn).getElementsByTagName("td")(1).innertext
c3q = t.getElementsByTagName("tbody")(0).getElementsByTagName("tr")(rn).getElementsByTagName("td")(2).innertext
c4q = t.getElementsByTagName("tbody")(0).getElementsByTagName("tr")(rn).getElementsByTagName("td")(3).innertext
```

IFISページでは、FY判定に以下のセルが使える。

```html
<th class="none_left em_red">
  今期 202612<br>
  進ちょく率
</th>
```

---

## BigQuery変更

### DDL

```sql
ALTER TABLE `gmailpj-357912.STOCK.CONSENSUS`
ADD COLUMN SOURCE STRING;
```

### 既存行の埋め戻し

```sql
UPDATE `gmailpj-357912.STOCK.CONSENSUS`
SET SOURCE = 'RAKU'
WHERE SOURCE IS NULL;
```

### streaming buffer 注意

楽天版は現行 `insert_rows_json` による streaming insert を使っている。直近投入行が streaming buffer に残っている状態では、BigQuery の `UPDATE ... WHERE SOURCE IS NULL` が失敗する可能性がある。

埋め戻し実行前に以下を確認する。

- `scripts/update_conse_rakuten.py` が実行中でないこと
- 直近の楽天コンセンサス投入直後ではないこと
- `UPDATE` が streaming buffer 制約で失敗した場合は待機して再実行する
- 待機で解決しない場合は CTAS/swap または一時テーブル経由の移行を検討する

### 今後の楽天版insert修正

`scripts/update_conse_rakuten.py` のinsert行に `SOURCE='RAKU'` を追加する。

```python
bq_rows = [
    {
        "DATAAT": dataat,
        "TICKER": code,
        "FY": "000000",
        "SOURCE": "RAKU",
        **rec,
    }
    for rec in records
]
```

既存楽天版の `FY='000000'` は今回の修正対象外とする。

---

## 新規スクリプト案

新規:

```text
scripts/update_conse_ifis.py
```

既存楽天版とは別スクリプトにする。ログイン・画像認証など楽天固有処理を持ち込まない。

主な構成:

```text
get_bq_client()
get_japanese_stock_tickers()
clean_number()
extract_current_fy_from_ifis()
extract_ifis_consensus()
fetch_ifis_page()
insert_to_bq()
process_codes()
run()
```

### 状態ファイル

楽天版と衝突させない。

```text
data/logs/conse_ifis_resume.json
```

中身:

```json
{"dataat": "2026-04-27", "last_ticker": "7203"}
```

### CSV

必要なら楽天版とは別ファイルにする。

```text
C:\Users\zonekun\Dropbox\stock\py\conse_ifis.csv
```

---

## IFIS抽出ロジック

### FY抽出

```python
import unicodedata

def extract_current_fy_from_ifis(soup: BeautifulSoup) -> str | None:
    prog = soup.select_one(".prog_quarter")
    if not prog:
        return None

    for th in prog.select("th"):
        text = unicodedata.normalize("NFKC", th.get_text(" ", strip=True))
        text = " ".join(text.split())
        m = re.search(r"今期\s*(\d{6})", text)
        if not m:
            continue
        fy = m.group(1)
        if "01" <= fy[4:6] <= "12":
            return fy

    return None
```

`FY` が取れない場合は `000000` を入れず、当該銘柄をスキップしてログに残す。

### コンセンサス抽出

```python
def extract_ifis_consensus(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    fy = extract_current_fy_from_ifis(soup)
    if not fy:
        return []

    prog = soup.select_one(".prog_quarter")
    if not prog:
        return []

    cons_row = None
    for row in prog.select("tbody tr"):
        header = row.find("th")
        header_text = unicodedata.normalize(
            "NFKC",
            header.get_text(" ", strip=True) if header else "",
        )
        if "コンセンサス予想" in header_text:
            cons_row = row
            break
    if cons_row is None:
        return []

    cells = cons_row.select("td")
    quarters = ["1Q", "2Q", "3Q", "FY"]

    records = []
    for quarter, td in zip(quarters, cells[:4]):
        val = clean_number(td.get_text())
        if not val:
            continue
        records.append({
            "FY": fy,
            "QUARTER": quarter,
            "PROFIT": int(val),
            "TARGET": "CURRENT",
        })
    return records
```

`FY` が取れない、または `コンセンサス予想` 行が取れない銘柄は保存しない。skip reason と ticker を集計ログに出す。

### insert行

```python
{
    "DATAAT": dataat,
    "TICKER": code,
    "FY": rec["FY"],
    "QUARTER": rec["QUARTER"],
    "PROFIT": rec["PROFIT"],
    "TARGET": rec["TARGET"],
    "SOURCE": "IFIS",
}
```

---

## 累計/単独Qの扱い

初回実装では `PROFIT` は既存 `STOCK.CONSENSUS` と同じ累計値として保存する。

- 1Q = 1Q累計
- 2Q = 1Q+2Q累計
- 3Q = 1Q+2Q+3Q累計
- FY = 通期

単独Qへの変換は今回実装しない。既存データカタログでも `STOCK.CONSENSUS.PROFIT` は累計値と定義されているため、同一カラムに単独Qを混ぜない。

---

## 下流クエリへの注意

同じ `STOCK.CONSENSUS` に `RAKU` と `IFIS` が共存するため、下流クエリは `SOURCE` を意識しないと重複混入する。

**IFISレコードを本テーブルへ投入する前に、既存本番系クエリは `SOURCE='RAKU'` 固定へ修正する。**

対象:

- `scripts/zaraba_earnings.py` `_load_or_fetch_consensus()`
  - `MAX(DATAAT)` は `WHERE SOURCE='RAKU'`
  - 最新日全件取得も `WHERE SOURCE='RAKU'`
- `scripts/earnings_model/batch_rerun_predict.py`
  - CONSENSUS as-of SQL に `AND SOURCE='RAKU'`
  - 将来比較時のみ `SOURCE` を `SELECT` と `PARTITION BY` に含める
- `scripts/export_consensus_csv.py`
  - 最新日判定・全件取得とも `SOURCE='RAKU'`

IFIS smoke は、上記修正後に行う。修正前に本テーブルへ IFIS を投入しない。

通常運用の安全策:

```sql
WHERE SOURCE = 'RAKU'
```

比較検証時のみ:

```sql
WHERE SOURCE IN ('RAKU', 'IFIS')
```

決算反応モデルなど既存処理は、IFIS版の検証が終わるまでは `SOURCE='RAKU'` 固定を推奨する。

---

## CLI案

```bash
PYTHONUTF8=1 python scripts/update_conse_ifis.py --ticker 6723 --dry-run
PYTHONUTF8=1 python scripts/update_conse_ifis.py --ticker 6723
PYTHONUTF8=1 python scripts/update_conse_ifis.py --fresh
```

`--dry-run` はBQ insertせず、抽出結果だけ表示する。

---

## 実装順序

1. `STOCK.CONSENSUS` に `SOURCE` を追加
2. 既存 `SOURCE IS NULL` 行を `RAKU` で埋め戻し
3. `update_conse_rakuten.py` の今後insertに `SOURCE='RAKU'` を追加
4. 既存下流クエリを `SOURCE='RAKU'` 固定へ修正
   - `scripts/zaraba_earnings.py`
   - `scripts/earnings_model/batch_rerun_predict.py`
   - `scripts/export_consensus_csv.py`
5. `update_conse_ifis.py` 新規作成
6. `--ticker 6723 --dry-run` で `FY=202612` とコンセンサス4列を確認
7. `--ticker 6723` でBQ insert smoke
8. 全件実行

---

## BQ書き込み・冪等性

`update_conse_ifis.py` は全件実行時に `insert_rows_json` を使わず、Load Job を使う。

同一キーを重複させない。

```text
(SOURCE, DATAAT, TICKER, FY, QUARTER, TARGET)
```

実装方針:

- `--dry-run` は BQ 書き込みなし
- `--ticker` smoke は投入前に同一キー既存行を確認し、存在すれば skip
- 全件は staging table に load し、MERGE または DELETE+INSERT で冪等化
- 完了後に件数と重複を確認する

確認SQL:

```sql
SELECT SOURCE, DATAAT, COUNT(*) AS n
FROM `gmailpj-357912.STOCK.CONSENSUS`
GROUP BY SOURCE, DATAAT
ORDER BY DATAAT DESC, SOURCE;
```

```sql
SELECT SOURCE, DATAAT, TICKER, FY, QUARTER, TARGET, COUNT(*) AS n
FROM `gmailpj-357912.STOCK.CONSENSUS`
GROUP BY SOURCE, DATAAT, TICKER, FY, QUARTER, TARGET
HAVING COUNT(*) > 1;
```

---

## ドキュメント更新

実装時に `data_catalog.md` の `STOCK.CONSENSUS` 定義へ以下を追記する。

- `SOURCE`: `RAKU` / `IFIS`
- `RAKU` は既存仕様どおり `FY='000000'` が残る
- `IFIS` は `FY=YYYYMM`
- `IFIS` は当面 `TARGET='CURRENT'` のみ

---

## レビュー観点

- `SOURCE` 追加だけでRAKU/IFISの共存に十分か
- 既存行 `SOURCE='RAKU'` 埋め戻しのタイミング
- 既存 `update_conse_rakuten.py` のinsert修正漏れがないか
- IFISページの `prog_quarter` / `今期 YYYYMM` 抽出が銘柄横断で安定しているか
- `FY` が取れない銘柄を `000000` で保存せずスキップする方針でよいか
- 下流クエリの `SOURCE` フィルタ追加範囲
