# extract_adapter.json 設計パターン集

**カテゴリ**: tools
**作成日**: 2026-03-24
**ステータス**: 有効
**関連ファイル**: `scripts/extract_monthly_data.py`, `scripts/build_monthly_extractor.py`

## 概要

月次開示データ抽出アダプター（`extract_adapter.json`）の `row_label_regex` フィールド設計で
Round3〜9 の修正作業を通じて確立したパターン集。Gemini 自動生成後の手動修正で必ず参照すること。

---

## ❌ やってはいけないパターン

### 1. `^` アンカーの使用

```json
{ "row_label_regex": "^全店売上.*?([\\d.]+)%" }
```

**問題**: BQ の `STRING_AGG(CHUNK_TEXT, ' ')` でチャンクがスペース連結された場合、
テキスト全体が1行になり、行頭 `^` が機能しないことがある。

**修正**: `^` を除去し `\s*` で代替するか、パターン先頭に十分な文脈を持たせる。

```json
{ "row_label_regex": "全店売上\\s*.*?([\\d.]+)%" }
```

---

### 2. 月番号を固定でハードコード

```json
{ "row_label_regex": "^11月.*?([\\d.]+)" }
```

**問題**: 月が変わるとマッチしなくなる。

**修正**: `{month_num}` プレースホルダーを使う（実行時に実際の月番号に置換される）。

```json
{ "row_label_regex": "{month_num}月.*?([\\d.]+)" }
```

---

### 3. 多列階層テーブルで OR-regex の左側をシンプルに書く

```json
{ "row_label_regex": "定期外|合計.*Sub Total" }
```

**問題**: JR東日本のように「定期外」列が3列に分かれる（近距離/中長距離/Sub Total）階層ヘッダーPDFで、OR左側「定期外」がヘッダーセル "…定期外 Non-Commuter Passes 近距離 Short Distance" にも広くマッチしてしまう。`_extract_pdf_by_row` は最初にマッチした列(ci=2, 近距離)で `break` → 近距離値を誤採用。BC正解は Sub Total 列(ci=4)。

**修正**: OR左側にも**階層コンテキストを必ず含める**（列/セクションのアンカーを共通化）。

```json
{ "row_label_regex": "定期外.*Sub\\s*Total" }
```

**教訓**: 多列階層テーブルでは OR 左側の「シンプルキーワード」は必ず右側の階層アンカーも含める。実例: 9020 JR東日本 2026-04-15 修正。

---

### 4. 英数字サフィックス付き月ヘッダーに `$` 末尾アンカー

```json
{ "row_label_regex": "(\\d{1,2})\\s*月[度]?$" }
```

**問題**: バイリンガルPDF（コスモス薬品3349等）で月ヘッダーが `'６月\nJun.'` → 正規化後 `'6月 Jun.'` となるため、`$` 末尾マッチが "Jun." で失敗する。さらに silent failure でフォールバックに落ち、値重複バグを引き起こすケースがある。

**修正**: 英日併記PDFは regex ではなく `extraction_method: "gemini"` 化を推奨（3349で実証）。どうしても regex で対応する場合は `$` を外し、ヘッダー末尾の英字略称・改行・空白を許容する。

```
# regex で対応する場合
"(\\d{1,2})\\s*月[度]?(?:\\s|\\n|$|[A-Za-z]+\\.?)"
```

**実例**: 3349 コスモス薬品 2026-04-15 修正（Gemini multi-month method化）。

---

## ✅ 推奨パターン

### 3. 複数列テーブルで特定の列を取得 → `group`

1行に複数月の値が並ぶ表（例: 11月・12月・1月・累計・2月）で、特定列の値が必要な場合。

```json
{
  "row_label_regex": "全店計\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)",
  "value_type": "percentage",
  "group": 5
}
```

- `group` は 1始まり。省略時は `1`（最初のグループ）
- 指定グループ数がマッチ数を超える場合は `group=1` にフォールバック

---

### 4. 累積テーブルで最新月値を取得 → `use_last_number`

同一パターンが複数回出現し（月ごとに行が追加される累積形式）、最後の行が最新月の値である場合。

```json
{
  "row_label_regex": "月次売上\\s+前年同月比\\s+([\\d,]+)\\s+[\\d.]+%",
  "value_type": "integer",
  "use_last_number": true
}
```

- `has_capture_group` ありの場合: キャプチャ内の最後の数値
- `has_capture_group` なしの場合: マッチ後テキストの数値リストの最後の値

---

### 5. 複数行にまたがるパターン → `[\s\S]*?`

セクションヘッダーとデータが別行に分かれている場合。

```json
{
  "row_label_regex": "グループ\\s*合計[\\s\\S]*?在籍技術者数\\s*([\\d,]+)",
  "use_last_number": true
}
```

- `[\s\S]*?` は `.*?` と異なり、改行を含む任意の文字にマッチ
- `[\s\S]` または `.*?` を含むパターンは自動的に DOTALL 全テキスト検索にフォールバックする

---

### 6. コメント文からの抽出

表形式ではなく文章中に埋め込まれた値の場合。

```json
{
  "key": "all_store_sales_yoy",
  "row_label_regex": "全店で前年同月比\\s*([\\d.]+)%"
}
```

```json
{
  "key": "existing_store_sales_yoy",
  "row_label_regex": "既存店で同\\s*([\\d.]+)%"
}
```

---

### 7. Markdown テーブル形式

開示文書がMarkdownで構造化されている場合。

```json
{
  "row_label_regex": "\\|\\s*{month_num}月\\s*\\|\\s*([\\d.]+)"
}
```

---

### 8. チャンク順序問題の回避

`STRING_AGG` のチャンク順序が不定なため、セクションヘッダーに依存せず
データ直前の固有アンカーを使う。

```json
{
  "row_label_regex": "在籍数\\s*[(（]名[)）]\\s*稼働率\\s*\\d{4}年\\d{1,2}月末時点\\s+(\\d{1,3}(?:,\\d{3})*)"
}
```

---

## アダプター修正の実行方法

修正は `scripts/monthly_bc_repair/apply_adapter_patch.py` で行う（旧 `apply_batch_fixes_round*.py` は全削除済み）。
修正後は必ず `extract_monthly_data.py --tickers ... --no-gcs` で動作確認してから GCS に反映すること。

## 根拠・出典

- Round3〜9 の adapter 修正作業（2026-03-xx 〜 2026-03-24）で蓄積したパターン
- `scripts/diagnose_zero_companies.py`（BQテキストに対して regex をテストする診断スクリプト）
