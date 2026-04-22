# irbank.net 月次開示検証スクリプト（verify_monthly_irbank.py）

**カテゴリ**: tools
**作成日**: 2026-03-11
**ステータス**: 有効
**関連ファイル**: `scripts/verify_monthly_irbank.py`, `scripts/tdnet_load_parallel.py`

## 概要

irbank.net の月次開示銘柄一覧をスクレイピングし、BigQuery `TDNET_DOCUMENTS_ENHANCED` との突合を行うスクリプト。
ETL のカテゴリ分類漏れ・ロード漏れを検出するための検証ツール。

---

## 実行方法

```bash
PYTHONUTF8=1 uv run python scripts/verify_monthly_irbank.py
```

出力例:
```
irbank.net 月次開示銘柄数: 197
BQ 月次開示銘柄数: 165
一致: 155件  一致率（irbank基準）: 78.7%
BQにない（irbank のみ）: 42件
```

---

## irbank.net のスクレイピング仕様

- URL: `https://irbank.net/td/月次・速報`
- **ティッカーは URL パス `/NNNN/` から抽出**（テーブルセルには書かれていない）
- 日付ヘッダ行: `<td class="lf" colspan="4">2026年3月11日</td>`
- ページネーション: `?y=タイムスタンプ` 付きリンク
- SSL エラーが出るため `verify=False` 必須

```python
for a in row.find_all("a", href=re.compile(r"^/\d{4}/")):
    m = re.match(r"^/(\d{4})/", a["href"])
    if m:
        tickers.add(m.group(1))
```

---

## BQ クエリ設計（2026-03-11 確定版）

月次開示の判定には以下の3条件を OR で組み合わせる。

```sql
WHERE SUBMISSION_DATE >= '2026-01-01'
  AND (
    MAIN_CATEGORY = '月次開示'
    OR EXISTS(SELECT 1 FROM UNNEST(SUB_CATEGORIES) s WHERE s = '月次')
    OR REGEXP_CONTAINS(DOC_TITLE, r'月次|月度売上|売上速報|売上推移速報|月度業績|受注速報')
  )
```

**理由**: ETL のカテゴリ分類が不完全なため DOC_TITLE でも補完が必要。

---

## ETL カテゴリ分類の問題と修正

### 問題

`parse_tdnet_filename()` は GCS ファイル名の `parts[3]` を MAIN_CATEGORY として使用する。
ファイル名に「業績予想」「その他（未分類）」が含まれる月次開示文書が存在し、BQ で月次として捕捉されない。

例:
- `20260128_2678_アスクル_その他（未分類）_...売上推移速報...` → BQ の MAIN_CATEGORY = 'その他（未分類）'
- `20260119_4666_パーク２４_その他（未分類）_...速報数値 12月度...` → BQ の MAIN_CATEGORY = 'その他（未分類）'

### 修正（2026-03-11 適用済み）

`tdnet_load_parallel.py` の `parse_tdnet_filename()` に補正ロジックを追加:

```python
_MONTHLY_DOC_PATTERN = re.compile(r"月次|月度売上|売上速報|売上推移速報|月度業績|受注速報")

def _correct_category_by_title(main_category: str, doc_title: str) -> str:
    if main_category != "月次開示" and _MONTHLY_DOC_PATTERN.search(doc_title):
        return "月次開示"
    return main_category
```

**注意**: 修正前にロード済みのデータは BQ 上で MAIN_CATEGORY が誤分類のままとなる（再ロード不要）。verify スクリプトの DOC_TITLE 検索で補完される。

---

## 検証結果（2026-03-11）

| 修正フェーズ | irbank | BQ | 一致 | 一致率 | 漏れ件数 |
|-------------|--------|-----|------|--------|---------|
| 修正前 | 197 | 144 | 141 | 71.6% | 56 |
| DOC_TITLE '月次' 追加 | 197 | 149 | 146 | 74.1% | 51 |
| パターン拡張後（確定） | 197 | 165 | 155 | 78.7% | 42 |

### 残り 42 件の内訳

| 原因 | 件数（推定） | 詳細 |
|------|------------|------|
| GCSにあるがBQ未ロード（画像PDF） | 数件 | マクドナルド(2702)・アスクル(2678)・大庄(9979)等。テキスト抽出ゼロでETLスキップ |
| GCS自体に存在しない | 残り | TDnet download で未取得、または irbank 独自収集 |

### BQ 独自 10 件（irbankにない）

アルファベット系 Ticker（グロース銘柄: 141A, 262A 等）が大半。irbank.net が4桁コード限定で収集しているため。

---

## キャッシュ

スクレイピング結果は `data/cache/irbank_monthly.json` に保存される（再実行時に再スクレイピングされる）。

---

## 根拠・出典

- 2026-03-11 セッションで実施した検証結果
- `docs/knowledges/tools/013_tdnet_load.md` - TDnet ETL の詳細
