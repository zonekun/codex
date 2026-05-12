# BC Match Agent - Runbook

**対応 knowledge MD**: [`../../docs/knowledges/tools/042-1_bc_match_agent.md`](../../docs/knowledges/tools/042-1_bc_match_agent.md)

このランブックは Claude 自身がセッション開始時に最初に参照する手順書。各セッションで同じ手順を踏めるように定型化。

---

## セッション開始 (初期化)

### 1. 最新 reconcile CSV を確認

```bash
ls -t data/logs/bc_key_reverse_mapping_*.csv | head -3
```

無ければ reconcile を再実行:

```bash
PYTHONUTF8=1 python scripts/reconcile_bc_key_from_compare.py \
    --compare-csv <latest compare CSV> \
    --gemini-semantic \
    --output data/logs/bc_key_reverse_mapping_<ts>.csv
```

### 2. 未処理キュー生成

```bash
# 前回セッションの escalation があれば exclude
PYTHONUTF8=1 python scripts/monthly_bc_repair/list_ng_queue.py \
    --reconcile-csv data/logs/bc_key_reverse_mapping_<latest>.csv \
    --exclude-escalation data/logs/agent_bc_match_<prev_session>_escalation.csv \
    --limit 100 \
    --sort high_conf \
    --output data/logs/agent_bc_queue_<ts>.csv
```

### 3. セッション ID 生成

```bash
SESSION=$(date +"%Y%m%d_%H%M%S")
echo "Session: $SESSION"
```

---

## Step 0: PDF 種別確認（0件 / 全件 NG の場合の最優先チェック）

2026-04-21 追加 (062 §6, 042-1 Pattern J)。**0 件 / 全件 NG を見たら、regex 修正に進む前に必ず PDF 種別を確認する**。データ取得層が原因なのに regex 層で粘るのは無駄。

```bash
# 対象銘柄の直近 PDF を 1 件取得して種別判定
PYTHONUTF8=1 python -c "
import sys, io
from google.cloud import storage
ticker = sys.argv[1]
c = storage.Client(project='gmailpj-357912').bucket('stock_data_1930932')
# 直近 PDF を拾う (monthly/docs/<ticker>/)
blobs = sorted(
    [b for b in c.list_blobs(prefix=f'monthly/docs/{ticker}/') if b.name.endswith('.pdf')],
    key=lambda b: b.name, reverse=True,
)
if not blobs:
    print(f'NO_PDF ticker={ticker}'); sys.exit(0)
b = blobs[0]
import pdfplumber
with pdfplumber.open(io.BytesIO(b.download_as_bytes())) as pdf:
    text = ' '.join(p.extract_text() or '' for p in pdf.pages[:2])
    print(f'length={len(text.strip())}')
    print(f'sample={text[:200]!r}')
" <ticker>
```

**診断フロー**:

| 判定 | 対処 |
|---|---|
| 50 字未満 (抽出不能) | スキャン PDF → `extraction_method: "ocr"` へ変更 |
| 50 字以上 + 文字化け率 20% 超 | `extraction_method: "gemini"` に escalate |
| 50 字以上 + 質 OK | regex 層 (Pattern A〜I) を疑う |

文字化け率の判定は `scripts/extract_monthly_data.py` の `_text_quality_ok(text, min_len=50, garbled_threshold=0.20)` を使うのが確実:

```bash
PYTHONUTF8=1 python -c "
import sys, io
sys.path.insert(0, 'scripts')
import importlib.util
spec = importlib.util.spec_from_file_location('emd', 'scripts/extract_monthly_data.py')
emd = importlib.util.module_from_spec(spec); spec.loader.exec_module(emd)
# 上で取得した text を食わせて判定
print('quality_ok=', emd._text_quality_ok(open('/tmp/sample.txt', encoding='utf-8').read()))
"
```

**適用事例**:
- **3034 クオールHD**: 画像 PDF → OCR で救済
- **8255 / 6752**: pdfplumber tables で質 NG → P0-1 で ② PyMuPDF にフォールスルー

**References**:
- `docs/knowledges/tools/062_pdf_processing_strategy.md` §6 (0 件診断)
- `docs/knowledges/tools/042-1_bc_match_agent.md` Pattern J

---

## メインループ (per ticker)

### Step 1: 事実確認

```bash
PYTHONUTF8=1 python scripts/monthly_bc_repair/inspect_ticker.py --ticker <T>
```

確認ポイント:
- `adapter.source` (tdnet / non-tdnet(pdf) / non-tdnet(html_table))
- `adapter.extraction_method` (regex / gemini)
- `adapter.use_fy_history_correction` / `year_month_from_submission_minus_1`
- `records_count` / `ym_min/max`
- BC fields 一覧と latest 値
- PDF テーブル構造 (最新 1 件)

### Step 2: パターン分類

042-1 MD の「NG パターン完全カタログ」を参照して A〜I のどれか判定。

迷ったら多重仮説を立てる (例: B1+C 複合、B3+H の両方試す)。

### Step 3: Snapshot

```bash
PYTHONUTF8=1 python scripts/monthly_bc_repair/snapshot_adapter.py --ticker <T> --save
```

### Step 4: Fix 適用

パターンごと:

```bash
# B1: fy_corr
PYTHONUTF8=1 python scripts/monthly_bc_repair/apply_adapter_patch.py \
    --ticker <T> --patch '{"use_fy_history_correction": true}' --no-snapshot

# B2: submission_minus_1
PYTHONUTF8=1 python scripts/monthly_bc_repair/apply_adapter_patch.py \
    --ticker <T> --patch '{"year_month_from_submission_minus_1": true}' --no-snapshot

# C: yoy+100 (特定 field)
PYTHONUTF8=1 python scripts/monthly_bc_repair/apply_adapter_patch.py \
    --ticker <T> --field-key "全店 売上（前年同月比）" \
    --field-patch '{"yoy_offset": 100, "bc_key": "全店 売上（前年同月比）"}' --no-snapshot

# E: bc_key 明示 (複数 field 一括は apply_bc_key_reverse_mapping.py が便利)
# CSV 準備してから:
PYTHONUTF8=1 python scripts/apply_bc_key_reverse_mapping.py \
    --input data/logs/mapping_<ts>.csv --tickers <T> --min-ratio 0.5

# H: Gemini 切替
PYTHONUTF8=1 python scripts/switch_to_gemini_bg.py --tickers <T>
# その後 custom_prompt を個別調整

# I: bc_ignore (全 field)
# apply_adapter_patch で field 単位に適用、or 直接 edit
```

### Step 5: 検証

```bash
PYTHONUTF8=1 python scripts/monthly_bc_repair/reextract_and_compare.py --ticker <T>
```

末尾の `AGENT_RESULT_JSON` から `match_ratio` を確認。

### Step 6: 判定

- `after_ratio > before_ratio + 0.1` (10pt 以上改善) → **commit**
- `after_ratio < before_ratio - 0.05` (5pt 以上退行) → **即 rollback**
  ```bash
  PYTHONUTF8=1 python scripts/monthly_bc_repair/snapshot_adapter.py --ticker <T> --restore
  ```
- 変化なし → rollback して次仮説 (別パターン適用)
- 3〜5 回試して改善ゼロ → **Escalation** 登録

### Step 7: ログ

```bash
# 成功
PYTHONUTF8=1 python scripts/monthly_bc_repair/log_progress.py \
    --session-ts $SESSION --ticker <T> --status fixed \
    --before-ratio 0.0 --after-ratio 1.0 \
    --applied-fix "B1: fy_corr" --note "100% 達成"

# Escalation
PYTHONUTF8=1 python scripts/monthly_bc_repair/log_progress.py \
    --session-ts $SESSION --ticker <T> --status escalation \
    --tried "B1,B3,H" --note "Gemini も narrative 拾いで 同値複製"
```

---

## 30 銘柄ごと (中間チェックポイント)

1. jsonl 行数確認

   ```bash
   wc -l data/logs/agent_bc_match_${SESSION}_progress.jsonl
   ```

2. commit + push

   ```bash
   git add -u meta/monthly/ data/logs/agent_bc_* data/snapshots/
   git commit -m "feat: BC Match Agent session ${SESSION} - 中間 commit"
   git push origin master
   ```

3. LINE 通知 (確認事項あれば、なければ不要)

---

## 完了時

1. 最終 commit + push
2. Session summary 作成 (任意): `data/logs/agent_bc_match_${SESSION}_summary.md`
3. knowledges/tools/042-1 更新 (新しい教訓・パターン発見あれば追記)

---

## 困ったら

- **未知パターン**: `inspect_ticker.py` で PDF 内容を目視、Gemini 切替か bc_ignore で clos
- **Gemini が値を取れない**: custom_prompt に 「絶対額ではなく比率」「当月のみ」等を明示追加
- **BC データが古い/無い**: `download_bc_kpi.py --tickers <T> --resume` で再取得 (必ず --resume)
- **extract が 0 件**: `doc_title_pattern` が厳しすぎる可能性 → 緩和 or adapter の `source` 設定見直し
