# PDF 抽出層刷新 + 月次 NG 救済（pdfplumber / Camelot / Document AI）

**作成日時**: 2026-04-21 09:42 JST
**着手条件**: 2023 E/F/G backfill 完走後（現行ジョブ中は変更禁止、062 §3 確定方針）
**完了目標**: 2022/2021/2020 backfill 開始前
**関連**:
- `docs/knowledges/tools/062_pdf_processing_strategy.md` §3 / §4
- `docs/plans/20260421_063341_tdnet_load_code_review.md`（P0/P1 本命、本 plan は P1 拡張）

---

## Goal

1. `scripts/tdnet_load_parallel.py` の PDF テキスト抽出層を **pdfplumber 1st / pdfminer 2nd / PyPDF2 3rd** に刷新
2. 品質チェック関数導入（文字化け率）で下流への低品質データ流出防止
3. Vision OCR フォールバック閾値緩和
4. 月次 NG 救済として **Camelot-py** を `build_monthly_extractor.py` / `investigate_monthly_ng.py` に組込
5. **Document AI Layout Parser PoC**（NG 13 社で Gemini 2.5 Pro と精度・コスト比較、採否判断）

---

## 背景

### 現状（2026-04-21）

- `tdnet_load_parallel.py` は PyPDF2 1st → `len(text)<50` で pdfminer フォールバック
- 2023 バックフィル E/F/G（ticker 3691-6366）実行中に PyPDF2 の精度劣化が顕在化
  - 信越化学（4063）/ テクマト（3762）等で pdfminer フォールバック経由の救済が多発
  - PyPDF2 は「長さ 50 以上で通ってしまうが文字化け・順序崩れ・抜け落ち」が多い
  - 下流（Gemini Batch 分類、Embedding、Gemma）が誤認するリスク
- 2023 E/F/G はこのまま旧版で完走、再抽出はしない（062 §3 確定方針）

### 外部調査結論（062 §4）

- **pdfplumber** が日本語決算短信で最も安定（罫線ベースの表認識）
- pdfminer は罫線なしレイアウト・特殊フォント救済用
- PyPDF2 は最終保険
- **Camelot-py** が pdfplumber で取れない表の救済に有効（`lattice` + `stream` 2 モード）
- **Document AI Layout Parser** は Gemini 2.5 Pro の置換候補（pages 単位課金で TDnet と相性良）

---

## スコープ外

- **2023 E/F/G データの再抽出**: 062 §3 確定方針によりコスト優先で実施しない
- `scripts/extract_monthly_data.py` / `build_monthly_extractor.py` の主ライブラリ変更: 既に pdfplumber + PyMuPDF ベース（影響なし）
- `scripts/earnings_compare/pdf_loader.py`: 4K字制限の独立実装（影響なし）

---

## Task 一覧

### Task 1: `_extract_text_pdfplumber` 新規実装

**対象**: `scripts/tdnet_load_parallel.py`

**実装位置**: `_extract_text_pypdf2` / `_extract_text_pdfminer` の近傍（L368-416 周辺）

**仕様**:
```python
def _extract_text_pdfplumber(pdf_bytes: bytes) -> tuple[str, int]:
    """pdfplumber でテキスト抽出（主手段）。

    TDnet 決算短信の罫線ベース表抽出に最適。ページ境界は `[PAGE N]` マーカーで保持。
    既存 `_extract_text_pypdf2` / `_extract_text_pdfminer` と戻り値形式を揃える。
    """
    try:
        import pdfplumber
        buf: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            for idx, page in enumerate(pdf.pages, start=1):
                extracted = page.extract_text() or ""
                normalized = _normalize_page_text(extracted)
                buf.append(f"[PAGE {idx}]\n{normalized}")
        return "\n\n".join(buf).strip(), page_count
    except Exception:
        return "", 0
```

**検証**: 既知の TDnet 決算短信 PDF 3 本（テクマト / 信越化学 / 任意）で `len(pdfplumber) > len(PyPDF2)` かつ日本語文字率 > 50% を確認。

---

### Task 2: 抽出順序の入れ替え

**対象**:
- `phase1_scan_and_extract` L577-588（commit 2e0c418 基準）
- `phase1_extract_for_docs` L1597-1603

**仕様**: PyPDF2 1st → pdfplumber 1st に入れ替え。フォールバック順は pdfplumber → pdfminer → PyPDF2 → Vision。

```python
# 修正後（phase1_scan_and_extract 例）
text, page_count = _extract_text_pdfplumber(pdf_bytes)
extract_method = "pdfplumber"

if len(text) < _MIN_TEXT_LEN or _is_text_corrupted(text):
    text_pm = _extract_text_pdfminer(pdf_bytes)
    if len(text_pm) >= _MIN_TEXT_LEN and not _is_text_corrupted(text_pm):
        text = text_pm
        extract_method = "pdfminer"
        logger.log(f"  フォールバック抽出 (pdfminer) 成功: {blob.name}")
    else:
        text_p2, _ = _extract_text_pypdf2(pdf_bytes)
        if len(text_p2) >= _MIN_TEXT_LEN and not _is_text_corrupted(text_p2):
            text = text_p2
            extract_method = "pypdf2"
            logger.log(f"  フォールバック抽出 (pypdf2) 成功: {blob.name}")

needs_vision = len(text) < _MIN_TEXT_LEN or _is_text_corrupted(text)
```

**注意**: `phase1_extract_for_docs` も同ロジックに揃える。関数として抽出して共通化する手も検討（`_extract_text_best_effort(pdf_bytes, logger) -> tuple[str, int, str]`）。

---

### Task 3: 品質チェック関数 `_is_text_corrupted`

**対象**: `scripts/tdnet_load_parallel.py` 新規関数

**仕様**:
```python
_CJK_RANGES = [
    (0x3040, 0x309F),  # ひらがな
    (0x30A0, 0x30FF),  # カタカナ
    (0x4E00, 0x9FFF),  # 漢字（CJK 統合漢字）
    (0xFF00, 0xFFEF),  # 全角英数
]

def _is_text_corrupted(text: str, threshold: float = 0.2) -> bool:
    """抽出テキストの文字化け率を判定する.

    ASCII 英数・日本語（ひら・カナ・漢字・全角）・空白以外の文字が
    `threshold` を超えたら「破損」とみなす。PyPDF2 の文字化け検出用。
    """
    if not text:
        return False
    total = 0
    bad = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        cp = ord(ch)
        if cp < 128:  # ASCII
            continue
        if any(lo <= cp <= hi for lo, hi in _CJK_RANGES):
            continue
        bad += 1
    if total == 0:
        return False
    return (bad / total) > threshold
```

**検証**:
- 正常な日本語 PDF テキストで `_is_text_corrupted()` が False
- PyPDF2 で文字化けした出力（bytes を unicode として誤解釈した結果）で True
- 英語のみ PDF でも False（ASCII は OK）

---

### Task 4: Vision OCR 閾値緩和

**対象**: `phase1_scan_and_extract` / `phase1_extract_for_docs` の `needs_vision` 判定

**仕様**: `len(text) < _MIN_TEXT_LEN` だけでなく `_is_text_corrupted(text)` も条件に含める。

```python
# 修正前
needs_vision = len(text) < _MIN_TEXT_LEN

# 修正後
needs_vision = len(text) < _MIN_TEXT_LEN or _is_text_corrupted(text)
```

**副次効果**: Vision Batch の件数が増える可能性あり。現行 `phase2_vision_batch` の Gemini コストと要バランス。実運用で `vision_needed_count` がどれくらい増えるか先に測定し、しきい値調整する（threshold=0.2 → 0.3 等）。

---

### Task 5: Camelot-py 統合（月次 NG 救済）

**対象**:
- `scripts/build_monthly_extractor.py` — pdfplumber フォールバック層として組込
- `scripts/investigate_monthly_ng.py` — 併用で NG 銘柄の PDF 表を救済

**依存ライブラリ**:
- `camelot-py[cv]`（OpenCV 込み）
- Ghostscript（システム install、Windows + Cloud Run 両方）

**Dockerfile 修正**:
```dockerfile
# Cloud Run 用（tdnet-load-daily 含む共通 image）
RUN apt-get update && apt-get install -y \
    ghostscript \
    python3-opencv \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt に追加
camelot-py[cv]>=0.11.0
```

**Windows ローカル**: `choco install ghostscript` + `pip install camelot-py[cv]`

**利用パターン（build_monthly_extractor.py 想定）**:
```python
# 既存 pdfplumber で表が取れないときのフォールバック
if not tables_found:
    try:
        import camelot
        # lattice mode（罫線あり、決算短信向け）
        tables = camelot.read_pdf(pdf_path, flavor='lattice', pages='all')
        if len(tables) == 0:
            # stream mode（罫線なし、月次 KPI 向け）
            tables = camelot.read_pdf(pdf_path, flavor='stream', pages='all')
    except Exception as e:
        logger.warning(f"camelot failed: {e}")
```

**検証**:
- NG 銘柄で pdfplumber が空返し → Camelot lattice で救済される件数を測定
- 救済率 > 30% なら採用確定

---

### Task 6: Camelot インフラ整備

**Docker image 再ビルド**:
- `cloudbuild/cloudbuild.tdnet-load-daily.yaml` の Dockerfile で Ghostscript + OpenCV install
- image サイズ増加（+~200MB 想定）に注意、Cloud Run 起動時間への影響測定

**CI / 既存スクリプトへの影響確認**:
- `scripts/build_monthly_extractor.py` / `extract_monthly_data.py` は既に別 image（`build-monthly-extractor` / `extract-monthly-data` ジョブ）で稼働。これらの Dockerfile も同様に更新
- Windows ローカル実行用 venv（C:\venvs\investment-agent）にも Camelot + Ghostscript 入れる

---

### Task 7: Document AI PoC 必須 → 採否判定（PoC 結果次第、事前決定しない）

**方針（2026-04-21 確定）**: 採否は **PoC を経てから**決める。PoC 結果なしで採用も見送りも判断しない。

**対象**: NG 13 社「要確認」分（`docs/knowledges/tools/070_monthly_ng_investigation.md` 参照予定）

**PoC 手順**:
1. Google Cloud Document AI Layout Parser Processor を dev project で有効化
2. NG 13 社の PDF を同一セットとして Gemini 2.5 Pro / Document AI 双方に投入
3. 抽出精度（数値項目の適中率・漏れ率）を突合
4. コスト計測
   - Gemini 2.5 Pro: 入力トークン × 単価 + 出力トークン × 単価
   - Document AI: 処理 pages 数 × pages 単価
   - 同一 13 社での実測値で比較
5. 運用摩擦（認証経路・レイテンシ・エラー率）も記録

**採否判断は PoC 結果サマリを元に実施**:
- 精度・コスト・運用摩擦の 3 軸で Gemini 2.5 Pro と比較
- 単純な優劣でなく、NG 救済の「最終手段」として置換する価値があるかで判断
- 見送り時は現行の Gemini 2.5 Pro 依存維持、PoC 結果は `docs/knowledges/tools/070_monthly_ng_investigation.md` に記録

**PoC 未実施時の扱い**: 本 Task は保留、採用判断しない。勝手に採用・見送りしない。

---

## 実装順序

1. **Task 1-4**（`tdnet_load_parallel.py` 刷新）: 同一 commit で一気に。2022 backfill 着手前に deploy
2. **Task 5-6**（Camelot 統合）: Dockerfile 変更 + スクリプト修正は別 commit
3. **Task 7**（Document AI PoC）: Task 1-6 完了後の余裕時に実施

---

## 検証戦略

### Task 1-4 検証

1. **ローカル smoke**: 10 doc 程度の PDF（文字化け / 正常 / 表中心 / スキャン）で 4 方式の抽出結果比較、`_is_text_corrupted` 挙動確認
2. **Cloud Build → dev image → 100 doc ai-finalize** で end-to-end、Vision Batch 件数の変動を測定
3. **2022 先行 1 ticker range load**（例: `ticker_from=1301 ticker_to=1500`）で本番同等検証、成功後に 2022 全体投入

### Task 5-6 検証

1. **ローカル smoke**: NG 銘柄 10 社で pdfplumber→camelot lattice→stream の救済率測定
2. **Docker image 再ビルド + Cloud Run Job 実行** でインフラ動作確認
3. **月次 NG 100 社再投入**で救済率測定、30% 超えなら採用

---

## リスク

### R1: pdfplumber 速度劣化
- pdfplumber は PyPDF2 の 2-3 倍遅い
- 2022 backfill で 1 ticker あたり 10-20 秒 → 2500 ticker × 15 秒 = 10h と load phase が長時間化する可能性
- 対策: Task 2 の順序入替後、実測で 2022 load phase の所要を見て許容範囲か判断。超過なら threading 並列化

### R2: Vision OCR 件数爆増
- Task 4 で `_is_text_corrupted` 閾値が緩すぎると Vision Batch が大量に走る
- 対策: dev 環境で `needs_vision` フラグの件数を測定、閾値 0.2 → 0.3 等で調整

### R3: Camelot 依存インフラ
- Ghostscript + OpenCV install で image サイズ増 (~200MB)
- Cloud Run 起動時間が 5-10 秒延びる可能性
- 対策: image サイズ監視、起動時間測定。許容できなければ Camelot 使用スクリプトを別 image に分離

### R4: 2023 データ品質劣化
- 旧版 PyPDF2 で抽出した 2023 E/F/G データは低品質のまま固定
- 対策: 062 §3 で「再抽出しない」と確定済。分析時は ticker 3691-6366 の 2023 分を他年と異なる注意扱い

---

## 関連

- `docs/knowledges/tools/062_pdf_processing_strategy.md` §3 / §4（本 plan の起源）
- `docs/plans/20260421_063341_tdnet_load_code_review.md`（code-review P0/P1、本 plan と併走）
- `docs/knowledges/tools/013_tdnet_load.md`（TDnet load 本体）
- `docs/knowledges/tools/070_monthly_ng_investigation.md`（Task 5-7 の対象 NG 銘柄）
- `docs/knowledges/tools/004_coding_conventions.md` §バッチジョブ・ETL アンチパターン集（実装時の準拠）

---

## 担当メモ

- 実装は 2023 E/F/G 完走（ETA 2026-04-21 18:30 JST 頃）後に着手可能
- P0（code-review 指摘）は既に 2e0c418 で commit 済、本 plan は P1 拡張
- 2022 backfill 起動前に Task 1-4 を完了させる
- Camelot 統合（Task 5-6）は月次 NG 救済が別途走るタイミングで実施
- Document AI PoC（Task 7）は余裕で
