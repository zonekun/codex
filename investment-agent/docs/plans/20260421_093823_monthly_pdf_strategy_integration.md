# 月次開示パイプライン PDF 処理戦略 (062 MD) 統合プラン

**作成日時**: 2026-04-21 09:38 JST
**対象ファイル**:
- `scripts/extract_monthly_data.py` (3,256 行、commit `d16ca20` 時点)
- `docs/knowledges/tools/042-1_bc_match_agent.md`
- `scripts/agent_bc/RUNBOOK.md`
- `scripts/experiments/ocrmypdf_vs_pytesseract.py` (新規作成)
**対象読者**: code-reviewer サブエージェント / 次セッション担当
**目的**: 062 PDF 処理戦略 MD の知見のうち、月次開示パイプラインに適用可能な 4 項目（#1 OCRmyPDF / #4 pdfminer LAParams / #5 テキスト品質チェック / #6 0件診断ルール化）を実装する。却下項目（#2 LLM 3段ルーティング / #7 `llm_tier` / #8 コスト定量指針）は本プラン対象外。

---

## 前提サマリ

- **過去修正**: 月次 BC 突合改善 `d16ca20`（6 銘柄 adapter 修復 98.3% + 7345 gemini 化 100%）ほか多数。詳細は親プラン `20260420_223000_monthly_adapter_master_plan.md` Phase 1-3 参照
- **残存**: 本プランで扱う 4 項目（#1, #4, #5, #6）。非 TDnet 138 銘柄で再スクレイプ BG 走行中（`bmiof4xyl`、`update_monthly_adapters.py`、本プランと非競合ファイル）
- **実機検証の有無**: #4/#5 は本プラン実行時の dev 実機検証必須。#1 は PoC フェーズで 3 社以上の画像 PDF 銘柄検証、本実装フェーズは PoC 結果後判断。#6 は MD 追記のみでコード検証不要

---

## 優先度の定義

- **P0**: 現時点で NG を救う可能性が明確にあり、回帰リスクが低い改修。scheduler RESUME 前必須・SLO 違反相当
- **P1**: 次の `extract_monthly_data` バックフィル run 前に消化すべき改修
- **P2**: 余力で、ブロッカーではない（PoC・体系化）

---

## P0-1. テキスト品質チェック（062 #5）📖

**症状**: `_extract_pdf_text` の各抽出フェーズで「テキストが取れたが文字化け」のケースを検知できず、質の悪い文字列がそのまま regex / Gemini に流れて誤抽出または 0 件となる（8255 / 6752 等の pdfplumber 破損抽出で確認）。

**該当**:
- `scripts/extract_monthly_data.py:L910-L976` `_extract_pdf_text` 3段構造
- `scripts/extract_monthly_data.py:L606-L648` `_extract_pdf_ocr` text_check

```python:L910-L976
def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出。精度優先で3段フォールバック。"""
    # ① pdfplumber テーブル抽出
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_lines = []; has_any_table = False
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    has_any_table = True
                    all_lines.extend(_table_to_lines(tables))
                else:
                    t = page.extract_text() or ""
                    if t.strip(): all_lines.append(t)
            if has_any_table and all_lines:
                return "\n".join(all_lines)      # ← L956: 質の検証無しで返す
    except Exception: pass
    # ② PyMuPDF
    try:
        import fitz
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
            if text.strip(): return text          # ← L966: 空でなければ返す
    except Exception: pass
    # ③ pdfplumber extract_text
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)  # ← L974: 無条件
    except Exception: return ""
```

```python:L614-L623
# _extract_pdf_ocr 内
import pdfplumber
with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    text_check = " ".join((p.extract_text() or "") for p in pdf.pages[:2])
    if len(text_check.strip()) > 50:              # ← L619: 量だけチェック
        return text_check  # テキストPDF → OCR不要
```

**根本原因**: 062 L51 で指摘されているアンチパターンそのもの。`len(text) < 50` の量チェックのみで質を見ないため、文字化けテキストも PASS してしまう。

**修正方針**: 062 L59-61 の定義に従い、文字化け率（非ひらがな・非カタカナ・非漢字・非英数字の比率）を判定する `_text_quality_ok(text: str) -> bool` を新設し、3 段フォールバックと OCR text_check の計 4 箇所に適用。

```python
# before (L956)
if has_any_table and all_lines:
    return "\n".join(all_lines)

# after
if has_any_table and all_lines:
    result = "\n".join(all_lines)
    if _text_quality_ok(result):
        return result
    # 質 NG は次フェーズ (② PyMuPDF) にフォールスルー

# 新関数
def _text_quality_ok(text: str, min_len: int = 50, garbled_threshold: float = 0.20) -> bool:
    """抽出テキストの質を判定する (062 MD §3 準拠)。

    Args:
        text: 抽出結果。
        min_len: 最小文字数（この値未満は NG）。
        garbled_threshold: 文字化け率の閾値。非 CJK / 非英数字 / 非記号の比率が
            この値を超えると NG。

    Returns:
        長さ十分 + 文字化け率 < threshold で True。
    """
    import re
    s = (text or "").strip()
    if len(s) < min_len:
        return False
    # 「正常な」文字クラス: ひらがな/カタカナ/漢字/英数字/一般的な記号・スペース
    normal_re = re.compile(
        r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF"  # CJK
        r"A-Za-z0-9"                                  # 英数字
        r"\s\.\,\(\)\[\]\{\}\-\+\*\/\%\:\;\!\?\|\~\=\<\>\_\"\'\#\&"  # 記号
        r"\u3000-\u303F\uFF00-\uFFEF]",                # 全角記号・全角英数
    )
    normal = len(normal_re.findall(s))
    ratio_abnormal = 1 - (normal / len(s))
    return ratio_abnormal < garbled_threshold
```

**呼び出し側への波及**:
- `scripts/extract_monthly_data.py:L619` — `_extract_pdf_ocr` text_check で `len>50` の代わりに `_text_quality_ok(text_check)` を用いる。質 NG なら OCR 経路に進む
- `scripts/extract_monthly_data.py:L956` — ① pdfplumber tables 結果に適用。NG なら ② PyMuPDF にフォールスルー
- `scripts/extract_monthly_data.py:L966` — ② PyMuPDF 結果に適用。NG なら ③ pdfplumber extract_text にフォールスルー
- `scripts/extract_monthly_data.py:L974` — ③ 最終フォールバックでも `_text_quality_ok` を適用し、質 NG なら `""` を返して `phase_extract` 側で判別可能にする
- `scripts/extract_monthly_data.py:phase_extract` 呼び出し部 — 既存ロジックでテキスト空なら Gemini fallback に進むため追加改修不要だが、ログ `[text_quality_fail]` を挟んで追跡可能にする

**検証**:
- 単体テスト `tests/test_extract_monthly_data_quality.py` 新規
  - 正常日本語テキスト（"2024年3月度 売上高 1,234 百万円"）で True
  - 文字化けテキスト（ランダム制御文字 50% 混入）で False
  - 短すぎる (`len<50`) で False
  - 境界: ASCII 英文のみ（業績速報でもあり得る）で True
  - 境界: 数値と記号のみ（少ない場合）で False
- **閾値妥当性検証**: 正常 OK 銘柄 20 銘柄の直近 PDF で非 CJK/非英数字/非記号比率を実測しヒストグラム化。P95 < 20% であることを確認し、NG 銘柄 (8255/6752) で 20% 超であることを確認
- **統合テスト**: 8255 / 6752 の NG PDF で ① pdfplumber tables 後に `_text_quality_ok` False → ② PyMuPDF にフォールスルーすることを log で確認

**ロールバック**: `_text_quality_ok` の呼び出しをコメントアウトして L956/L966/L974 を元に戻す。`_extract_pdf_ocr` L619 も `len>50` 単独に戻す。1 commit 取り消しで戻せるため低リスク。

---

## P1-1. 0 件診断の運用ルール化（062 #6）📖

**症状**: extract_0件 または全件 NG の調査時、PDF がテキストレイヤー有りか画像型かを判定せずに regex を手当たり次第に修正するケースがある。原因の深さが「データ取得層」にあるのに「regex 層」で粘って時間浪費する。

**該当**:
- `docs/knowledges/tools/042-1_bc_match_agent.md:L34-L202` NG パターンカタログ A〜I
- `scripts/agent_bc/RUNBOOK.md` のセッション開始手順

```markdown:042-1_bc_match_agent.md:L32-L35
## NG パターン完全カタログ (A〜I)

### A. BC CSV に ticker データなし
```

**根本原因**: パターンカタログ A〜I は「データが取れた後の regex / Gemini 層」の議論が中心で、「データ取得層の質」を扱う Step 0 が欠落している（062 L24 の指摘と同じ）。

**修正方針**:
1. `042-1 MD` の NG パターンカタログに **Pattern J: 0 件 = PDF 種別問題** を追加
2. `scripts/agent_bc/RUNBOOK.md` に **Step 0: PDF 種別確認** フローをセッション開始手順の直後に追加

```markdown
# 042-1 MD に追加 (Pattern I の後)

### J. 0件 = PDF 種別問題

**症状**: 特定銘柄で全件 0 件 or 直近数ヶ月まるごと 0 件。
regex 修正しても挙動変わらず、Gemini fallback も 0 件。

**診断フロー** (scripts/agent_bc/RUNBOOK.md Step 0 参照):
1. `pdfplumber.extract_text()` で直近 1 PDF を抽出 → 50 字未満ならスキャン PDF 確定
2. スキャン PDF → `extraction_method: "ocr"` に変更
3. 50 字以上だが質 NG (文字化け率 20% 超、P0-1 の `_text_quality_ok` 判定) → `extraction_method: "gemini"` に escalate
4. 上記で解決しないときのみ regex 層 (Pattern A〜I) を疑う

**事例**: 3034 クオールHD (画像PDF → OCR で救済、d16ca20 以前)
```

```markdown
# scripts/agent_bc/RUNBOOK.md の「セッション開始 (初期化)」直後に追加

---

## Step 0: PDF 種別確認（0件 / 全件 NG の場合の最優先チェック）

0 件 or 全件 NG を見たら、regex 修正に進む前に必ず PDF 種別を確認する。

```bash
# 対象銘柄の直近 PDF を 1 件取得して種別判定
PYTHONUTF8=1 python -c "
import sys
from google.cloud import storage
c = storage.Client(project='gmailpj-357912').bucket('stock_data_1930932')
# 直近 PDF を拾う（monthly/docs/ or tdnet/）
blobs = sorted([b for b in c.list_blobs(prefix=f'monthly/docs/{ticker}/') if b.name.endswith('.pdf')], key=lambda b: b.name, reverse=True)
b = blobs[0] if blobs else None
import pdfplumber, io
with pdfplumber.open(io.BytesIO(b.download_as_bytes())) as pdf:
    text = ' '.join(p.extract_text() or '' for p in pdf.pages[:2])
    print(f'length={len(text.strip())}')
    print(f'sample={text[:200]!r}')
"
```

- 50 字未満 → スキャン PDF → `extraction_method: "ocr"` へ
- 50 字以上 + 文字化け率 20% 超 → `extraction_method: "gemini"` へ
- 50 字以上 + 質 OK → regex 層 (Pattern A〜I) を疑う
```

**呼び出し側への波及**: 無し（MD 追記のみ、コード改修なし）

**検証**: レビュアーが 042-1 MD の論理的一貫性を確認。3034 / 8255 / 6752 等の過去事例で Step 0 フローを辿ってパターン分類が妥当か机上検証

**ロールバック**: MD コミットを revert すれば戻せる

---

## P1-2. pdfminer LAParams チューニング（062 #4）⚙️

**症状**: `_extract_pdf_text` の ③ pdfplumber extract_text フォールバック (L970-L976) で LAParams 未指定。TDnet 向けに tdnet_load_parallel で実績のあるパラメータ (`char_margin=1.0, word_margin=0.2, line_margin=0.3`) を適用すれば、日本語 PDF の文字列分割精度が向上する。

**該当**:
- `scripts/extract_monthly_data.py:L970-L976`

```python:L970-L976
# ③ pdfplumber extract_text()（最終フォールバック）
try:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)
except Exception:
    return ""
```

**根本原因**: pdfplumber の `open()` に `laparams` kwarg を渡せることを利用していない。デフォルト LAParams は英文向けで日本語 PDF では文字列の分割・結合が不適切になる。

**修正方針**:

```python
# before (L970-L976)
with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
    return "\n".join(p.extract_text() or "" for p in pdf.pages)

# after
_LAPARAMS_JA = {"char_margin": 1.0, "word_margin": 0.2, "line_margin": 0.3}
with pdfplumber.open(io.BytesIO(pdf_bytes), laparams=_LAPARAMS_JA) as pdf:
    return "\n".join(p.extract_text() or "" for p in pdf.pages)
```

同じ LAParams を L942 ① pdfplumber テーブル抽出にも適用するか、効果差をまず PoC で見る。初回コミットは ③ のみ（影響範囲を最小化）。

**呼び出し側への波及**:
- `scripts/extract_monthly_data.py:L942` — ① pdfplumber extract_tables にも適用するか検証後決定。初回コミットは ③ のみで波及なし

**検証**:
- smoke: 既存 OK 銘柄 5 社 + NG 銘柄 5 社（8255/6752 含む）で LAParams 前後の extract_text 結果を DIFF 確認
- 回帰: 既存 OK 5 社で regex マッチ結果が変化しないこと

**ロールバック**: LAParams 引数を削除

---

## P2-1. OCRmyPDF 採用検討（062 #1）🔬

**症状**: 画像 PDF (3034 クオールHD 等) を `pytesseract` 直接で処理しているため、OCR 後は全ページ flat text のみで pdfplumber の表構造抽出メリットを得られない。表構造を保持した抽出ができれば数値取り違えが減る可能性。

**該当**:
- `scripts/extract_monthly_data.py:L606-L648` `_extract_pdf_ocr`

```python:L624-L646
with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
    for page_num, page in enumerate(doc):
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        ocr_text = pytesseract.image_to_string(img, lang="jpn+eng")
        if ocr_text.strip():
            lines.append(ocr_text)
```

**根本原因**: `pytesseract` は画像 → テキスト直接変換で、中間の検索可能 PDF を生成しない。OCRmyPDF なら OCR 層を元 PDF に追加して検索可能 PDF を作れるため、後段の pdfplumber.extract_tables() が使える。

**修正方針 (Phase 1: PoC)**:
1. `uv add ocrmypdf` で依存追加（Windows: Tesseract + Ghostscript 必要）
2. 新規スクリプト `scripts/experiments/ocrmypdf_vs_pytesseract.py`
   - Windows 環境で Tesseract / Ghostscript の `shutil.which()` 検出、未導入なら即スキップで異常終了
   - 対象銘柄: 画像 PDF 3〜5 社（3034 クオールHD + 他 画像PDF 銘柄を `monthly_adapter_index.csv` から抽出）
   - 指標: (1) OCR 所要時間、(2) 抽出テキスト長、(3) pdfplumber.extract_tables() の成功行数、(4) 既存 pytesseract 経路との抽出テキスト DIFF
3. PoC 結果を本プラン MD「PoC 結果」セクションに追記（未完時は空欄のまま）

**見送り判定基準**:
- 速度: OCRmyPDF が `pytesseract` 直接より 2 倍以上遅い → 不採用
- 精度: `pdfplumber.extract_tables()` が OCRmyPDF 出力でも表抽出できない → 不採用（OCR → 画像 PDF 戻し → 表構造なし、の実質 pytesseract と同値）
- 環境依存: Cloud Run 用 Dockerfile に Tesseract + Ghostscript 導入追記が必要（本実装時に追加）

**修正方針 (Phase 2: 本実装、PoC PASS 時のみ)**:
- `_extract_pdf_ocr` に OCRmyPDF 経路を追加、`extraction_method: "ocr"` はこちらに振り替え
- 既存 pytesseract 経路は `extraction_method: "ocr_legacy"` として残置（緊急時の fallback）
- `docker/Dockerfile.extract-monthly-data` に `RUN apt-get install -y tesseract-ocr tesseract-ocr-jpn ghostscript` 追加
- `cloudbuild/cloudbuild.extract-monthly-data.yaml` で base image を確認の上反映

**呼び出し側への波及** (本実装時):
- `scripts/extract_monthly_data.py:L606-L648` — `_extract_pdf_ocr` の実装分岐追加
- `docker/Dockerfile.extract-monthly-data` — 新規依存追加
- `cloudbuild/cloudbuild.extract-monthly-data.yaml` — 再ビルドコマンド確認

**検証**:
- PoC: 3〜5 社の画像 PDF で指標 (1)-(4) 比較、本 MD に結果追記
- PoC 環境: ローカル Windows（Tesseract/Ghostscript 導入済前提）
- 本実装時: 画像 PDF 銘柄全社で精度・速度比較、回帰確認

**ロールバック**:
- Phase 1 PoC は単発スクリプト実装のため、スクリプトを削除すれば戻せる
- Phase 2 本実装時は `extraction_method: "ocr_legacy"` に切替で即戻せる

---

## 対応アンチパターン

| plan ID | 004 | 062 | その他 |
|---|---|---|---|
| P0-1 | 「取れた/取れないの量だけチェック」 | #5 (L51, L59-61) | - |
| P1-1 | - | #6 (L24) | 042-1 既存カタログ補完 |
| P1-2 | - | #4 (L57) | TDnet 向け実績あり |
| P2-1 | - | #1 (L19-L24) | 画像 PDF 汎用化 |

---

## 検証戦略

1. **smoke test**:
   - P0-1: `_text_quality_ok` 単体テスト（正常・文字化け・短すぎ・境界 5 ケース）
   - P1-1: レビュアーが 042-1 と RUNBOOK の追加論理を目視
   - P1-2: LAParams 前後で extract_text の DIFF を 10 銘柄で確認
   - P2-1: 3 社の画像 PDF で OCRmyPDF vs pytesseract 比較
2. **dev 実機**:
   - P0-1 + P1-2: 並行 BG (`bmiof4xyl`) 完了後、ローカルで `extract_monthly_data.py --tickers 8255 6752 3034 --since 2024` を実行し、Gemini fallback ログの変化を観察。コストガード: `--limit` で最大 50 件に絞り込み
   - P2-1: PoC 対象 3〜5 社のみ
3. **本番適用判断基準**:
   - P0-1: unittest + 統合テスト PASS + dev 実機で 8255/6752 に改善あり
   - P1-1: レビュアー APPROVE + 042-1 と RUNBOOK の整合性確認
   - P1-2: dev 実機で既存 OK 5 社の regex マッチ結果に変化なし、NG 5 社で改善あり
   - P2-1: PoC 結果で「見送り判定基準」に該当しないこと + Cloud Run Dockerfile 更新完了
4. **回収手順**:
   - P0-1 回帰発覚: `_text_quality_ok` 呼び出し 4 箇所を revert（1 commit 取り消し）
   - P1-2 回帰発覚: LAParams 引数削除（1 commit 取り消し）
   - P2-1 本実装後の問題: `extraction_method: "ocr_legacy"` に切替（全銘柄一括で可）
   - **CLAUDE.md L471-L488 破壊的操作ルール適用**: #5 / #4 変更で既存 `monthly_records.json` が再抽出経由で上書きされうる。dry-run モード (`extract_monthly_data.py --dry-run`) で 10 銘柄比較し、抽出結果が改善方向（量増加 or NG→OK）であることを確認してから本番適用

---

## 実施順序

1. **P0-1**（最優先、即実装）: `_text_quality_ok` 関数 + 4 箇所適用 + unittest + 閾値実測
2. **P1-1**（MD 追記のみ）: 042-1 に Pattern J + RUNBOOK に Step 0 （P0-1 とほぼ同時でも可、MD は独立）
3. **P1-2**（小修正）: LAParams 適用 + DIFF 確認
4. **P2-1**（PoC → 判断 → 本実装）: 画像 PDF 3〜5 社で PoC、本 MD に結果追記してから本実装可否判断

## リスク・留意点

| リスク | 対処 |
|---|---|
| P0-1 の 20% 閾値で既存 OK 銘柄が過剰 escalate | 閾値実測検証（OK 20 銘柄 P95 確認）、必要なら 15%/25% で再調整 |
| P1-2 の LAParams 適用で既存 OK 銘柄の extract_text が変化 | dry-run DIFF 確認、変化ある銘柄は個別検証 |
| P2-1 OCRmyPDF Windows 非対応 / Tesseract 未導入 | PoC 冒頭で `shutil.which('tesseract')` + `which('gs')` チェック、未導入なら即スキップ |
| P2-1 Cloud Run で Tesseract/Ghostscript 未インストール | 本実装時に Dockerfile 更新、まずはローカルのみ |
| 並行 BG (`bmiof4xyl`) との競合 | BG は `update_monthly_adapters.py` 改修、本プランは `extract_monthly_data.py` 改修、ファイル非競合。安全のため BG 完了後に本プラン開始 |
| `_text_quality_ok` 実装が 042-1 Pattern J と齟齬 | P1-1 を P0-1 と同じ PR で実施、同一コミットで整合維持 |

## PoC 結果（P2-1 実施後に追記）

（未着手）

---

## 関連ドキュメント

- **知見 MD**: `docs/knowledges/tools/062_pdf_processing_strategy.md`（原典）、`docs/knowledges/tools/042_monthly_disclosure_master.md`（月次パイプ本体）、`docs/knowledges/tools/042-1_bc_match_agent.md`（BC 突合）、`docs/knowledges/tools/004_coding_conventions.md`（コーディング規約、プラン MD テンプレート）
- **親プラン**: `docs/plans/20260420_223000_monthly_adapter_master_plan.md` D セクション
- **関連 commit**: `d16ca20` (6 銘柄 adapter 修復 + 7345 gemini 化)、`a5d485c` (update_monthly_adapters.py 精緻化 Phase 7)、`364e138` (042 から 062 へのクロスリンク追加)
- **並行中 BG**: `bmiof4xyl` (138 銘柄 url_adapter.json 再スクレイプ、`data/logs/update_adapters_138_20260421.log`)
