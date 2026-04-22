# 月次NG銘柄 詳細調査手法

**カテゴリ**: tools
**作成日**: 2026-04-09
**ステータス**: 有効
**関連ファイル**: `scripts/investigate_monthly_ng.py`（正式版）, `C:\tmp\ng96_investigate.py`（旧版）

## 概要

BC突合でNGとなった銘柄について、1社ずつGCS PDF → pdfplumberテキスト/テーブル抽出 → PDF画像化 → Gemini 3 Flash画像分析の3段階で根本原因を特定する調査手法。

## 原則

- **効率化厳禁**: バッチ推定・Gemini丸投げ・パターンマッチによる一括処理は禁止。1社ずつPDFを読んで逆引きする
- **3段階の情報源を組み合わせる**: pdfplumberの構造化出力だけでも画像だけでも不十分。両方を突き合わせることで正確な原因特定が可能

## 調査フロー

### Step 1: NG詳細の取得
```python
# BC突合CSVからNG行を抽出
df = pd.read_csv('buffett_compare_YYYYMMDD.csv', encoding='utf-8-sig')
ng = df[(df['ticker'] == ticker) & (df['match'] == 'NG')]
# → year_month, our_field, our_value, bc_value, diff を確認
```

### Step 2: 正しいPDFの特定
- GCSから `tdnet/{ticker}/` の全PDFをリスト
- **月次PDFを正確に選別**: ファイル名に「月度」「月次」「概況」「売上速報」等を含むもの
- **除外**: 決算短信、配当通知、役員異動、四半期報告等
- NG年月に対応するPDFを選択（月次は通常翌月に開示）

```python
monthly_kw = ["月度", "月次", "概況", "売上速報", "月次売上", "月次業績", "KPI"]
exclude_kw = ["決算短信", "配当", "株式分割", "役員", "四半期"]
```

> **注意**: ファイル名の日付降順ソートで最新を取ると配当通知等を拾うミスが頻発する（2670で発生）

### Step 3: pdfplumber テキスト + テーブル抽出
```python
import pdfplumber
with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    for page in pdf.pages:
        text = page.extract_text()        # テキスト全文
        tables = page.extract_tables()     # テーブル構造（行×列のリスト）
```

確認ポイント:
- テーブルのヘッダー行（月列の配置、上期/下期分割の有無）
- 行ラベルの構造（全店/既存店、売上高/前年同月比の区別）
- テーブル検出数（0件の場合は画像ベースのPDF）

### Step 4: PDF→画像化
```python
import fitz  # PyMuPDF
with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        png_bytes = pix.tobytes("png")
```

### Step 5: Gemini 3 Flash 画像分析
```python
from google import genai
client = genai.Client(api_key=GEMINI_API_KEY)

contents = [
    genai.types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
    prompt,  # NG詳細 + pdfplumberテーブル情報 + 分析指示
]
resp = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=contents,
    config=genai.types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0,
    ),
)
```

プロンプトに含めるべき情報:
1. **NG詳細**: 各フィールドの抽出値・BC値・差分
2. **pdfplumberテーブル検出結果**: ページ番号、テーブルインデックス、ヘッダー行、行数
3. **分析タスク**: 各NGフィールドについて「BC値の所在」「抽出値の所在」「原因」「修正方法」を特定

### Step 6: 結果の記録
- `{output_dir}/{ticker}.json` に全情報を保存（NG詳細、PDF名、pdfplumberテキスト、Gemini分析結果）
- 画像も `{ticker}_page{N}.png` として保存（後から目視確認可能）

## よくあるNG原因パターン

| 原因 | 頻度 | 説明 | 修正方法 |
|------|------|------|---------|
| **月ずれ** | 高 | 上期/下期分割テーブルで隣の月を取得 | プロンプト改善（テーブルブロック判定強化） |
| **行の取り違え** | 高 | 売上高（実額）を前年同月比として取得、全店と既存店の混同 | プロンプト改善（行ラベル厳密化） |
| **列の取り違え** | 中 | 前月比と前年同月比の列を混同 | プロンプト改善（列ヘッダー指定） |
| **unit_scale** | 中 | PDF単位（百万円）とBC単位（円）の不一致 | adapter の unit_scale フィールド追加 |
| **yoy_offset** | 中 | PDFは増減率（-15%）、BCは100+増減率（85.0） | adapter の yoy_offset フィールド追加（※特殊ケースのみ） |
| **テーブル混同** | 中 | 当期/前期テーブル、件数/台数テーブルの混同 | プロンプト改善（テーブル見出し・セクション指定） |
| **PDF範囲外** | 低 | 対象月のデータがPDFに未掲載（未確定月、別期間のPDF） | 年月判定ロジック修正 |
| **微差/精度限界** | 低 | 浮動小数点誤差、OCR読み取り精度 | bc_ignore または閾値緩和 |
| **BC側の問題** | 低 | BC値がPDFと一致しない（修正値、別資料参照） | bc_ignore |

## 注意事項

- **Gemini分析結果を鵜呑みにしない**: Geminiは「もっともらしい」原因を作り出すことがある。pdfplumberのテーブル構造と照合して裏取りすること
- **PDFの選択ミスに注意**: 配当通知・決算短信をPDFとして分析すると全くの的外れになる
- **yoy_offsetは安易に追加しない**: ほとんどの企業はcolumn extractionがインデックス形式で抽出するため不要。増減率形式の特殊ケース（2670, 6627, 8798等）のみ
