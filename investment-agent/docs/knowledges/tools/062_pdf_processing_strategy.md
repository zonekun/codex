# TDnet/EDINET PDF処理戦略（ライブラリ・LLMルーティング）

**カテゴリ**: tools
**作成日**: 2026-04-02
**ステータス**: 有効
**関連ファイル**: `scripts/build_monthly_extractor.py`, `scripts/tdnet_load_parallel.py`, `scripts/extract_monthly_data.py`

## 概要

適時開示PDFの処理において、ライブラリ選択と難易度別LLMルーティングの方針を定める。

## 1. PDF前処理ライブラリ構成

PyMuPDF 単体ではなく、用途別に使い分ける。

| ライブラリ | 用途 |
|-----------|------|
| **PyMuPDF** | 本文テキスト抽出・ページMarkdown化・座標付き抽出 |
| **pdfplumber** | 罫線あり表の抽出（PyMuPDFより高精度） |
| **OCRmyPDF** | スキャンPDF・画像PDF専用（中小企業開示で出現） |

**pdfplumber の利点:** 罫線情報を使って表セルを正確に特定できるため、数値の取り違えが減る。

**OCRmyPDF の利点:** スキャン系PDFの「抽出0件」問題を解決。extract_adapter の抽出0件バグ調査時、まずスキャンPDFかどうかを確認し、該当ならOCRmyPDFを前段に挟む。

## 2. 難易度別LLMルーティング

```
① PyMuPDF でテキスト抽出成功 → regex で数値取得（コスト最小）
② regex 失敗 → Gemini 2.5 Flash（速い・安い）
③ 画像PDF or 表崩れ → Gemini 2.5 Pro または Claude PDF（精度重視）
```

**方針:** 普段はローカル/安価モデルで処理し、難所だけ高精度APIに逃がすことでコスト削減と精度を両立する。

**適用箇所:** `build_monthly_extractor.py` や `tdnet_load_parallel.py` の Gemini フォールバック部分を、PDF種別判定ロジック付きの3段構成に改修する際に適用。

---

## 3. テキスト抽出ライブラリの優先順（2026-04-21 追記、2026-04-21 改訂）

### 現状と問題

`scripts/tdnet_load_parallel.py` の `phase1_scan_and_extract` / `phase1_extract_for_docs` は
PyPDF2 を 1st、`len(text) < 50` のとき pdfminer にフォールバック、という順序で実装されている（`_extract_text_pypdf2` → `_extract_text_pdfminer`）。

**観測された問題（2026-04-21 2023 バックフィル E/F/G 範囲実行時）**:
- PyPDF2 で「テキストは取れるが精度が低い」PDF が大量に存在
- 具体的には TDnet の多くの PDF で pdfminer フォールバック（`len(text) < _MIN_TEXT_LEN (=50)` 閾値）でしか救済されないケースが頻発
  - 例: 信越化学（4063）や テクマト（3762）の 2023 年開示群
- PyPDF2 は**文字列が取れる場合でも文字化け・抜け落ち・順序崩れが多い**。閾値チェック `len(text) < 50` は数個の文字さえあればパスするため、実質**質が悪いまま後段に流れる**
- 下流（Gemini Batch 分類、Embedding、Gemma）が誤認する原因になりうる

### 次回改修方針（P1-改訂版、2026-04-21）

当初は「pdfminer 1st 昇格」方針だったが、日本語決算短信の抽出精度を外部調査と突き合わせた結果、
**pdfplumber が決算短信系 PDF で最も安定**と判明したため 1st は pdfplumber に変更する。

1. **pdfplumber を 1st に昇格**、pdfminer を 2nd、PyPDF2 を 3rd に降格
   - pdfplumber は罫線ベースで表構造を認識でき、決算短信の BS/PL/CF 等の格子状表の抽出精度が高い
   - pdfminer は罫線なしレイアウトや特殊埋め込みフォントの救済に回す（LAParams は既存の TDnet 向けチューニングを維持）
   - PyPDF2 は最終保険（pdfplumber/pdfminer の両方が例外を投げた場合のみ）
2. **閾値 `_MIN_TEXT_LEN=50` だけでなく質のチェックを追加**
   - 文字化け率（ASCII 外の「非ひらがな・非カタカナ・非漢字・非英数字」の割合が 20% 超なら破損判定）
   - 「長いが読める日本語が少ない」ケースを検知して次段へフォールバック
3. **Vision OCR 閾値の緩和**
   - pdfplumber / pdfminer / PyPDF2 いずれも質が悪ければ即 Vision OCR へ回す（現状は text 空のときのみ）

### 該当コード行（2026-04-21 時点、commit 2e0c418）

- `_extract_text_pypdf2` L368-385
- `_extract_text_pdfminer` L388-416
- `phase1_scan_and_extract` L577-588（抽出順序の本丸）
- `phase1_extract_for_docs` L1597-1603（ai-prepare 側も同一ロジック）
- pdfplumber ラッパ `_extract_text_pdfplumber` は新規実装が必要

### 該当スクリプト・他プロジェクト

- `scripts/tdnet_load_parallel.py` — 今回の main
- `scripts/extract_monthly_data.py` / `build_monthly_extractor.py` — 既に pdfplumber / PyMuPDF ベースで別系統（本項目の影響なし）
- ~~`scripts/earnings_compare/pdf_loader.py`~~ — 削除済み（Ollama PoC 廃止）
- `scripts/update_conse_rakuten*.py` — PDF 未使用

### 優先度

P1（次の backfill phase = 2022/2021/2020 開始前に対応）。E/F/G 2023 backfill の途中変更はしない（走行中のため）。

### 2023 データ取扱方針（2026-04-21 確定）

- 現行 E/F/G 2023 backfill は **旧版（PyPDF2 1st）のまま完走**
- §3 改訂は完走後に実装
- **2022/2021/2020 backfill は新版（pdfplumber 1st）で実施**
- **2023 データの再抽出は行わない**（再 load + AI 処理のコストに対し、分類/Embedding への影響が限定的と判断）
- 2023 E/F/G の品質劣化は許容範囲として固定

---

## 4. ライブラリ比較（日本語決算短信・外部調査、2026-04-21）

### 4.1 既存ライブラリの棲み分け（調査結論）

| ライブラリ | 強み | 弱み | 本プロジェクトでの役割 |
|----------|-----|-----|---------------------|
| **pdfplumber** | 罫線ベースの表認識で決算短信 BS/PL/CF の抽出精度が最も高い | 処理速度遅め、段組み・結合セルは苦手 | **全スクリプトの 1st 候補** |
| **PyMuPDF (fitz)** | 速度最速、テキスト抽出高精度 | `find_tables()` は pdfplumber より弱く、複雑表で崩れる | Markdown化・座標抽出用途のみ |
| **pdfminer-six** | 特殊フォント・埋め込みフォント対応、LAParams で日本語チューニング可 | 表認識なし、速度遅 | 罫線なしレイアウトの救済 2nd |
| **PyPDF2 / pypdf** | 軽量・高速 | 文字化け・順序崩れ多発 | 最終フォールバック 3rd |
| **OCRmyPDF** | スキャンPDF → テキスト埋め込みPDF変換 | Tesseract依存、通常の決算短信は不要 | 画像PDF限定の前段処理 |

### 4.2 未導入ライブラリの採否判定

| ライブラリ | 判定 | 理由 |
|----------|-----|-----|
| **Camelot-py** | ✅ 採用候補（月次NG救済） | `lattice`（罫線あり）/ `stream`（罫線なし）2モードで pdfplumber で取れない表を救済。決算短信/業績予想の「表抽出0件」問題に直接効く。**Ghostscript + OpenCV 依存**に注意（Windows ローカル + Cloud Run 両方に導入要） |
| **Google Document AI (Layout Parser)** | △ 条件付き PoC | Gemini 2.5 Flash/Pro の逃がし先の置換候補。GCP 課金統合で運用摩擦小。pages単位課金なので TDnet の短い PDF と相性良。NG 13社「要確認」で Gemini 2.5 Pro と精度/コスト比較してから採否判断 |
| **Azure Document Intelligence** | × 見送り | 精度は商用トップクラスだが GCP 重複運用コスト大。Document AI で代替可 |
| **unstructured / unstructured-io** | × 見送り | 内部で pdfplumber/pdfminer/detectron2 を束ねるだけ。既存の pdfplumber + Gemini 構成で要素化相当を実現済みのため中間層を増やす実益乏しい |
| **PaddleOCR + PP-Structure** | × 見送り | GPU 前提。Cloud Run CPU 運用と噛み合わない。表認識は強いが Document AI で代替可 |

### 4.3 採用アクション（優先順）

1. **§3 の P1 改訂**（pdfplumber 1st 昇格、pdfminer 2nd、PyPDF2 3rd）
   - 実装工数: 数時間。2022/2021/2020 backfill 開始前に対応
2. **Camelot PoC**（月次 NG 救済）
   - `scripts/investigate_monthly_ng.py` / `build_monthly_extractor.py` の pdfplumber フォールバック層として組み込み
   - NG で残った銘柄（adapter 修正でも取れないもの）に lattice で再抽出 → 救済率を測定
3. **Document AI PoC**（任意）
   - NG 13 社「要確認」に Layout Parser を試し、Gemini 2.5 Pro と精度/コスト比較

### 4.4 現行スクリプト別の現状サマリ（2026-04-21 時点）

| スクリプト | 用途 | 現行ライブラリ | 強化余地 |
|-----------|-----|-------------|---------|
| `tdnet_load_parallel.py` | 分類/Embedding 用テキスト抽出 | PyPDF2 1st → pdfminer 2nd | 大（§3 P1 改訂対象） |
| `build_monthly_extractor.py` / `extract_monthly_data.py` | 月次の**表からの数値抽出** | pdfplumber + PyMuPDF | 中（Camelot 救済層の追加） |
| ~~`earnings_compare/pdf_loader.py`~~ | 削除済み（Ollama PoC廃止） | — | — |
| `investigate_monthly_ng.py` | NG銘柄の画像分析 | pdfplumber + Gemini画像 | 小（Camelot 併用検討） |
