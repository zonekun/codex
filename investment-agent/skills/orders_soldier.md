# TDnet 受注高・先行指標 抽出スキル — 「受注高抽出ソルジャー」（/orders-soldier）

> **コードネーム**: 受注高抽出ソルジャー（1 銘柄 1 起動の実働部隊）
> 別途、複数銘柄の進捗管理・順次起動を担う「受注高抽出コマンダー」が呼び出し元として動作する。

## 目的

指定された 1 社の TDnet 開示 PDF（2022-2026 範囲）を BigQuery + GCS 経由で取得・読み取り、
「売上高の先行指標となる数字（受注高/受注残高/繰越工事高 等）」をコンパクト JSON 1 ファイルに
まとめて `C:/gdrive/claude/work/{ticker}.json` に原子保存する。

呼び出し元（コマンダー）は 1 銘柄ごとに本スキルを Agent ツール経由で起動し、結果報告を
受けてから pending リスト等のインデックスを更新する。本スキルはインデックスを触らない。

---

## 入力

| 項目 | 例 | 必須 | 説明 |
|------|-----|------|------|
| ticker | `1780`, `142A` | Yes | 証券コード（4 桁英数。先頭ゼロなし） |

**コマンダーから受け取り方**: `.claude/commands/orders-soldier.md` 経由のスラッシュコマンド
で起動する場合は、第一引数として ticker を受け取る（`/orders-soldier 7011`）。Agent ツール
prompt で直接渡す場合は prompt 本文に `ticker=7011` のように明示する。

---

## 実行手順

### Step 1: 既存ファイル上書き方針

`C:/gdrive/claude/work/{ticker}.json` が既に存在しても **STATUS=completed / completed_partial の時は常に上書きする**。
スキップ判定は呼び出し元の責任。本スキルは「処理せよ」と指示されたら常に処理する。

**重要**: 呼び出し元 prompt に「JSON を保存せよ」と書かれていても、**§Step 8 §STATUS 一覧の「JSON 出力」列が最終決定**。`failed_*` 系 4 STATUS は JSON 出力しない（caller 指示より spec 優先）。

**並行 agent との競合は心配無用**:
- 出力ファイル名は ticker 単位（`{ticker}.json`）で完全分離されているため、他 ticker を処理中の並行ソルジャーと**ファイル競合は構造的に発生しない**
- `task-notification` や `orders_log.tsv` などのコマンダー側ログで他 ticker の処理進行が見える場合があるが、**それは他 ticker の話で自分の処理に一切影響しない**
- 「他に複数 BG agent が稼働中」「重複起動回避」「既存ファイル尊重」「並行作業を妨げない」等を理由に**処理中断・上書きスキップ・既存ファイル温存をしてはならない**
- コマンダーは 1 ticker = 1 ソルジャーで起動する（同一 ticker の二重起動はしない）規約
- 既存 JSON は内容問わず常に新規生成結果で上書き

→ 142A 事例（架空の並行競合リスクで既存 5KB JSON を温存してしまった）の再発防止
→ 166A 事例（並行 agent 競合を理由に Step 5 半ばで処理中断、prompt 強化で復帰）の再発防止

**事前ディレクトリ作成**:

```bash
mkdir -p /c/tmp/tdnet_orders /c/gdrive/claude/work /c/gdrive/claude/work/_heartbeat /c/gdrive/claude/work/_status
```

PowerShell の場合:

```powershell
New-Item -ItemType Directory -Force -Path `
  "C:\tmp\tdnet_orders","C:\gdrive\claude\work","C:\gdrive\claude\work\_heartbeat","C:\gdrive\claude\work\_status" | Out-Null
```

存在していてもエラーにならないので毎回叩いてよい。

**ハートビート初回書き出し（必須）**:

ディレクトリ作成と**同じ Bash 呼び出しの末尾**で即座に実行する（タイミングを Step 1 中に確定させる）:

```bash
mkdir -p /c/tmp/tdnet_orders /c/gdrive/claude/work /c/gdrive/claude/work/_heartbeat /c/gdrive/claude/work/_status && \
date -Iseconds > "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"
```

- 文字コード: UTF-8 ASCII（`date -Iseconds` の出力 `2026-05-18T19:42:01+09:00` 1 行のみ）
- 改行: `\n`
- ファイル内容自体は重要でなく、**mtime（最終更新時刻）のみコマンダーが参照**する

コマンダーがこの mtime を見て生存判定する。以降 Step 5 の各 PDF 処理ループで上書き更新する（後述）。

### Step 2: BQ で対象 DOC_ID + FILE_NAME 一覧取得

BigQuery `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED` に対し以下のクエリを実行する:

```sql
SELECT DISTINCT
  TICKER, FILER_NAME, DOC_ID, FILE_NAME,
  SUBMISSION_DATE, DOC_TITLE,
  MAIN_CATEGORY, SUB_CATEGORIES
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE TICKER = '{ticker}'
  AND EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '受注高/受注残高')
  AND MAIN_CATEGORY IN ('決算短信', '決算説明資料', 'その他（未分類）', '受注高受注残高')
  AND AI_STATUS = 'completed'
  AND SUBMISSION_DATE BETWEEN '2022-01-01' AND CURRENT_DATE("Asia/Tokyo")
ORDER BY SUBMISSION_DATE DESC
```

**ポイント（include list 方式の根拠 = 4 MAIN_CATEGORY ホワイトリスト厳格運用）**:
- DISTINCT で同 DOC_ID の複数チャンク行を重複排除する
- **MAIN_CATEGORY include list**: ユーザー判断で明示的に「対象」と確定した 4 カテゴリのみ受け入れる:
  - `決算短信` / `決算説明資料` / `その他（未分類）` / `受注高受注残高`（バグ表記、6324系四半期受注速報を救済）
  - 新カテゴリが BQ に出現しても自動除外、明示追加待ち（OR 条件を使わない理由: 過去 incident で「SUB 経由で正当な受注情報を持つ文書を巻き添えで除外」の集合論バグが発生した）
  - 実機検証 (2026-05-18): MAIN='受注高/受注残高' は 0 件、SUB に '受注高/受注残高' は 27,526 件
- **SUB_CATEGORIES**: '受注高/受注残高' を含むことを必須（Gemini 分類で受注関連と判定されたものに限定）
- `AI_STATUS='completed'` 必須（`pending` 等の未分類は除外）
- **CHUNK_INDEX IS NOT NULL を付けない**（2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する罠。BQ クエリ一般の注意事項）

### Step 3: 文書選定（最大 6 件）

Step 2 の SQL 結果から最新 6 件（`SUBMISSION_DATE DESC` の先頭 6 行）を採用する。
**1.5 年分 ≒ 6 文書**が目安（直近 6 四半期分の決算開示）。

訂正版（同一会計期に複数 DOC_ID）は全部含めてよい。`title` で識別可能。

### Step 4: PDF ダウンロード + PyMuPDF 抽出 (text + tables ハイブリッド) + キーワード前処理フィルタ

> **改修方針（B+C ハイブリッド方式 / v2: PyMuPDF text + pdfplumber tables）**: トークン削減を最大化するため、`Read` tool で PDF を直読せず、**PyMuPDF が抽出した text 全文 + pdfplumber が抽出した表構造 (2 次元配列) を JSON 化して assistant が直接解析する** 方式を採用する。Read tool は画像 PDF 時のみ fallback で使用。
>
> - **B+C ハイブリッドの根拠**: 純粋 C 案（表のみ）だと「受注残高は前年同期比で大幅に増加」等の文章記述が漏れる。純粋 B 案（テキストのみ）だと表の行/列対応が崩れる。両方を渡すことで取りこぼし防止 + 数値抽出精度向上
> - **役割分担 (v2)**: text 抽出は PyMuPDF（速度最速・全ページ走査でヒット判定）、**表抽出は pdfplumber**（062 知見§4.1「PyMuPDF find_tables は pdfplumber より弱く複雑表で崩れる」既知、月次開示 `scripts/extract_monthly_data.py` と仕組み統一）

#### Step 4-A: PDF ダウンロード（既存維持）

各文書 (FILE_NAME) について以下を実行:

```bash
gsutil cp "gs://stock_data_1930932/tdnet/{ticker}/{FILE_NAME}" "/c/tmp/tdnet_orders/{ticker}_{idx}.pdf"
```

ファイル名に空白や全角文字が含まれていてもダブルクォートでそのまま渡せば動く。
`{FILE_NAME}` は BQ で取得した `FILE_NAME` カラムの値をそのまま使う。

**フォールバック（gsutil 失敗時）**:

FILE_NAME にブラケット文字 `[` `]`、ワイルドカード相当 `*` `?` 等が含まれると gsutil の glob 解釈で失敗することがある。その場合は curl + access token で直接 DL する:

```bash
TOKEN=$(gcloud auth print-access-token)
URL_FILENAME=$(python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "{FILE_NAME}")
curl -fsSL -H "Authorization: Bearer $TOKEN" \
  -o "/c/tmp/tdnet_orders/{ticker}_{idx}.pdf" \
  "https://storage.googleapis.com/stock_data_1930932/tdnet/{ticker}/${URL_FILENAME}"
```

`gsutil cp` も `curl` フォールバックも両方失敗した場合のみ、その 1 件を DL 失敗としてカウントする（DOCS_READ には含めない）。

#### Step 4-B: PyMuPDF 抽出 (text + tables) + キーワード前処理フィルタ（**1 回の python サブプロセスで全 PDF 一括**）

DL 完了後、当該 ticker の全 PDF に対して PyMuPDF 抽出 + キーワード検索 + ヒットページの表抽出を**1 回の python サブプロセスで実施**する（サブプロセス起動オーバーヘッドを最小化）。

**ハートビート更新（PyMuPDF処理直前、必須）**:

```bash
date -Iseconds > "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"
```

**PyMuPDF text 抽出 + ヒットページ特定 + pdfplumber 表抽出（B+C ハイブリッド v2）**:

```bash
PYTHONUTF8=1 python <<'PYEOF'
import fitz, pdfplumber, json, re, sys
# キーワード正規表現（spec §Step 5 §キーワードリスト で一元定義。下記と完全一致させること）
pattern = re.compile(r'受注高|受注残|受注金額|受注工事|受注件数|受注棟数|受注戸数|受注社数|受注組数|受注機数|受注案件|受注実績|受注の実績|受注済|受注額|受注予想|受注予定|受注契約|受注ライセンス|受注LT|受注平均単価|新規受注|当期受注|繰越|手持工事|手持高|契約獲得|契約残高|仕入棟|仕入件|仕入区画|管理戸|管理棟|管理件|棚卸資産件|棚卸資産残|販売件|販売数|販売棟|販売戸|販売区画|販売台|売上件|売上棟|売上戸|売上数|売上数量|受入数|受入純増|パイプライン|生産高|生産実績|受注.{0,8}推移')

def normalize_table(tbl):
    """tbl.extract() 戻り値正規化:
    - 結合セル None → 空文字 ''
    - 改行入りセル '行A\\n行B' → '\\n' を半角空白に置換
    - マイナス記号 △/▲ → -（spec L409 「マイナスは -数値、△ は使わない」と整合）
    """
    out = []
    for row in tbl:
        out_row = []
        for cell in row:
            if cell is None:
                out_row.append('')
            else:
                s = str(cell).replace('\n', ' ').replace('△', '-').replace('▲', '-').strip()
                out_row.append(s)
        out.append(out_row)
    return out

result = {}
for pdf_path in sys.argv[1:]:
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result[pdf_path] = {'error': f'fitz.open failed: {e}', 'hit_pages': [], 'total_chars': 0, 'page_count': 0, 'pages_data': {}, 'table_extract_errors': []}
        continue
    # Phase 1: PyMuPDF で text 抽出 + ヒット判定
    page_texts = {}
    chars = 0
    hit_pages = []
    for i, page in enumerate(doc, 1):
        text = page.get_text("text")
        chars += len(text)
        page_texts[i] = text
        # スペース除去後に検索（設計決定: 「受 注 の 実 績」スペース挟み対策）
        if pattern.search(re.sub(r'\s+', '', text)):
            hit_pages.append(i)
    # Phase 2: ヒットページのみ pdfplumber で表抽出（062 知見§4.1「PyMuPDF find_tables は pdfplumber より弱い」既知 → pdfplumber に統一）
    pages_data = {}
    table_extract_errors = []
    if hit_pages:
        try:
            with pdfplumber.open(pdf_path) as plumber_pdf:
                for i in hit_pages:
                    page_data = {'text': page_texts[i], 'tables': []}
                    try:
                        tables = plumber_pdf.pages[i-1].extract_tables() or []
                        page_data['tables'] = [normalize_table(t) for t in tables]
                    except Exception as e:
                        # silent skip 禁止: エラー記録して後段 assistant が ERRORS/HIGHLIGHTS に転記
                        table_extract_errors.append(f'page={i} error={e}')
                    pages_data[i] = page_data
        except Exception as e:
            # pdfplumber 全体失敗時も text は残す
            for i in hit_pages:
                pages_data[i] = {'text': page_texts[i], 'tables': []}
            table_extract_errors.append(f'pdfplumber_open_failed={e}')
    result[pdf_path] = {
        'hit_pages': hit_pages,
        'total_chars': chars,
        'page_count': len(doc),
        'pages_data': pages_data,
        'table_extract_errors': table_extract_errors,
    }
print(json.dumps(result, ensure_ascii=False))
PYEOF
# 引数で対象PDFを渡す: $(ls /c/tmp/tdnet_orders/{ticker}_*.pdf)
# 出力は JSON を ticker 単位の中間ファイルに保存（後続 Step 5 で assistant が Read tool で読む）
```

出力 JSON は `/c/tmp/tdnet_orders/{ticker}.pages.json` に保存する。**この JSON 自体が PDF 全文より桁違いに軽い**（ヒットページのみ・text + tables のみ）ため、後段で Read tool 投入してもトークン消費は最小化される。

**判定軸**:
- `hit_pages` が空 かつ `total_chars >= 500`: 偽陽性扱い → Step 5 で全スキップ
- `hit_pages` が空 かつ `total_chars < 500`: 画像 PDF / 抽出失敗 → Step 5 で fallback 起動
- `hit_pages` が 1 以上: Step 5 で `{ticker}.pages.json` の `pages_data` を assistant が直接解析（Read tool で PDF を読まない）
- `error` キーあり (`fitz.open()` 例外): 画像 PDF / 抽出失敗 と同じ fallback パスに合流
- **`table_extract_errors` 非空**: assistant 必ず ERRORS or HIGHLIGHTS に転記（silent skip 禁止）。空配列なら無視

### Step 5: PDF 読み取り（pages.json 直接解析 + 画像PDF fallback）

**中間ファイル命名規約**:
- `/c/tmp/tdnet_orders/{ticker}_{idx}.pdf` — **連番** (1 ticker = N ファイル、idx は 1-6)。DL 順で採番
- `/c/tmp/tdnet_orders/{ticker}.pages.json` — **単数** (1 ticker = 1 ファイル)。Step 4-B サブプロセス出力を集約

#### Step 5-A: PDF ごとの分岐ルール

Step 4-B の結果 (`/c/tmp/tdnet_orders/{ticker}.pages.json`) を **assistant が Read tool で読む**（PDF 全文ではなく JSON テキストのみ、桁違いに軽い）。各 PDF について次のいずれかに分岐:

| 条件 | 動作 | DOCS_READ カウント |
|------|------|-------------------|
| `hit_pages` 1 件以上 | `pages_data[i].text` + `pages_data[i].tables` を assistant が直接解析（Read tool で PDF を読まない） | カウントする |
| `hit_pages` 空 かつ `total_chars >= 500` | **スキップ**（偽陽性確定） | カウントしない |
| `hit_pages` 空 かつ `total_chars < 500` | **画像 PDF fallback**（Step 5-B） | カウントする |
| `error` キーあり (`fitz.open()` 例外) | **画像 PDF fallback**（Step 5-B） | カウントする |

#### Step 5-B: 画像PDF / 抽出失敗時の fallback（Read tool による PDF 直読）

PyMuPDF 抽出文字数が 500 文字未満の場合、または `fitz.open()` 例外時は、PDF が画像化されている可能性が高い。この場合のみ Read tool 直読 fallback に合流:

**閾値 500 文字の根拠**: 典型的な 1 ページ決算短信本文 ≒ 1,500 文字、6 ページの最小決算短信でも 5,000 文字超。500 文字未満 = ほぼ確実に画像のみ or 抽出失敗（テキスト PDF を誤って fallback 行きにするリスクは極めて低い）。

1. **初回**: `Read` ツールで pages 指定なし（PDF まるごと）
2. **失敗時 (1)**: `pages: "1-5"` 指定で再試行
3. **失敗時 (2)**: `pages: "6-10"` 指定で再試行
4. **3 回試行しても空 or エラーなら**: その PDF はスキップして次の PDF へ（DOCS_READ に含めない）

fallback で読んだ PDF はヒット 0 でも DOCS_READ に **カウントする**（画像PDFを「処理した」と扱う）。

巨大スキャン PDF で Agent が無限ループ → context 枯渇するのを防ぐため、fallback 経路でも **1 PDF あたり最大 3 回** で打ち切る。

#### ハートビート（必須）

**Read tool 呼出直前ごとに**以下を実行する（**pages.json 読込前 / 画像 PDF fallback の各試行前**にそれぞれ叩く）:

```bash
date -Iseconds > "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"
```

これにより `_heartbeat/{ticker}.hb` の mtime が最新化される。コマンダーは
mtime を `find -mmin +30` で能動チェックし、**30 分以上更新が無い ticker をハング判定** → TaskStop 対象とする。

通常経路（B+C ハイブリッド）では Read tool 呼び出しは `{ticker}.pages.json` 1 回のみで完結する。画像 PDF fallback がある場合は各試行前に hb 更新する。Step 4-B PyMuPDF 直前 + Step 5 pages.json 読込前 + 画像 PDF fallback リトライ分の hb 更新が想定される。

> **ハートビート更新を忘れるとコマンダーから「ハング」と誤判定**されて強制停止される。Step 4-B PyMuPDF 処理直前、Step 5 の pages.json 読込直前、画像 PDF fallback リトライ前に必ず叩くこと。
> 特に Read tool 自体が応答返さず長時間ハングするケース（Google Drive 上 Grep 48 分ハング事例 2026-05-12 等）に備え、**リトライ前 hb 更新**は必須。

#### キーワードリスト（前処理フィルタ用）

PyMuPDF 経由のテキストに対し以下の正規表現でヒットするページのみを `{ticker}.pages.json` の `pages_data` に格納して assistant が直接解析する。0 ヒットの PDF は失敗判定（画像 PDF fallback 経路を除く）。

```regex
受注高|受注残|受注金額|受注工事|受注件数|受注棟数|受注戸数|受注社数|受注組数|受注機数|受注案件|受注実績|受注の実績|受注済|受注額|受注予想|受注予定|受注契約|受注ライセンス|受注LT|受注平均単価|新規受注|当期受注|繰越|手持工事|手持高|契約獲得|契約残高|仕入棟|仕入件|仕入区画|管理戸|管理棟|管理件|棚卸資産件|棚卸資産残|販売件|販売数|販売棟|販売戸|販売区画|販売台|売上件|売上棟|売上戸|売上数|売上数量|受入数|受入純増|パイプライン|生産高|生産実績|受注.{0,8}推移
```

**スペース除去前処理**: 設計上の重要決定通り、各ページテキストに `re.sub(r'\s+', '', text)` を適用してから検索する。元のページ番号は維持（PDF 内「受 注 の 実 績」のようにスペース挟み記述に対応するための前処理。Step 4-B の python サンプル参照）。

**抽出対象セクション (§Step 5 §抽出対象) との二重管理について**:
本リストは PyMuPDF 前処理フィルタ用、§抽出対象は assistant 抽出指示用。両者は目的が違うが、新キーワード追加時は **両方を同時に更新する義務がある**。片方だけ更新すると「フィルタ通過するが assistant 指示にないため抽出されない」「assistant 指示にあるがフィルタ通過しないため pages_data に載らない」のいずれかの取りこぼしが発生する。

#### 抽出対象

**受注ベースの売上先行指標のみ**を抽出する。業種は問わない。

文書に登場する以下のラベル相当の数値を `d` 配列に格納する:
- 受注高 / 受注金額 / 受注工事高 / 新規受注高
- 受注残高 / 繰越高 / 次期繰越高 / 繰越工事高 / 手持工事高
- 受注棟数 / 受注戸数 / 受注件数
- 建築請負契約棟数 / 着工棟数 / 管理戸数（戸建賃貸・住宅事業等で「受注」相当の運用指標として開示される場合）
- セグメント別/部門別/地域別/製品別の受注内訳

**金額単位と数量単位の同等扱い**:
- 金額（百万円 / 千円 / 億円）と数量（棟 / 戸 / 件数）は**等価で DOCS_WITH_DATA としてカウント**する
- 「金額が無いから抽出ゼロ」と判定しない（住宅事業・戸建賃貸・サブスクサービス等は数量ベースが標準開示）
- 単位は `u` フィールドに記録（`百万円` / `千円` / `億円` / `棟` / `戸` / `件` 等）
- 同一文書内で金額と数量が両方ある場合は **両方とも別 `d[]` 要素で記録**（`kind` で区別）

→ 139A 東日本地所 事例（棟/戸単位を金額外として誤除外）の再発防止

**`kind` の表記揺れ規約**:
- PDF 内に書かれているラベル文字列をそのまま `kind` に記録する（例: 「受注高」「受注残」「繰越工事高」「次期繰越高」）
- 同義語の名寄せ（例: 「受注残」→「受注残高」）は**後工程の正規化**で行う前提。本スキルでは行わない
- 「セグメント別受注高」「部門別受注高」のような複合ラベルもそのまま記録

#### 抽出対象外（誤抽出防止のため明示）

以下は **抽出しない**:

| 対象外カテゴリ | 例 |
|---------------|-----|
| 売上高・完成工事高 | 「売上高」「完成工事高」「製品売上高」 |
| 全社 P/L サマリ | 営業利益・経常利益・当期純利益 |
| 全社 BS サマリ | 総資産・純資産・自己資本比率 |
| 全社 CF サマリ | 営業 CF/投資 CF/財務 CF |
| 配当方針 | 1 株配当・配当性向・DOE |
| 後発事象・資本政策 | M&A・上場廃止・自己株 TOB・株式分割 |
| 構成比（%） | 「受注の構成比 65%」 |
| 業績予想の P/L 行 | 営業利益予想・経常利益予想 |
| 入居率・稼働率 | 「賃貸物件入居率 97.4%」 |
| 役務サービスの個別商品売上 | シロアリ予防・運輸単独売上等 |

#### 「期待する数値がなかった」場合の判定（偽陽性銘柄の検出）

以下のいずれかに該当する PDF 1 件は「抽出ゼロ」として扱う:
- PDF 内に「受注」「繰越」「手持」等のキーワードが一切ない
- キーワードはあるが**定性記述のみ**（「受注は堅調」等）で具体数値が無い
- 数字はあるが**構成比/前期比のみ**（金額が無い）
- 上記「抽出対象外」表に該当する数字しかない（売上高・営業利益等）

「6 文書全部が抽出ゼロ」だった場合、銘柄全体として **STATUS=failed_no_data** で報告する
（Step 8 参照）。これは **Gemini の SUB_CATEGORIES 分類が偽陽性** だった可能性が高い
（例: 水産・バイオ・サービス業等が「受注高/受注残高」に紛れ込んでいるケース）。

### Step 6: JSON 組み立て・原子保存

#### 数値抽出の優先順位（B+C ハイブリッド前提）

`{ticker}.pages.json` の `pages_data[i]` から `d[]` 要素を構築する際の優先順位:

1. **`pages_data[i].tables` を優先採用**: 表があれば行/列の対応が確実。Step 4-B `normalize_table()` で正規化済（結合セル `None` → `""`、改行 → スペース、`△`/`▲` → `-`）の 2 次元配列を、`h` (列ヘッダ) と `r` (行データ) にそのままマップする。assistant 側で追加処理が必要なのは以下のみ:
   - 数値文字列のカンマ除去 (`"1,234"` → `1234`)、int/float キャスト
   - 失敗時は元文字列のまま `r[]` に格納
   - **明らかに崩れた表**（行ヘッダが空 / 数値カラムが 0 / 行数が 1 のみ）は §2 にフォールバック
2. **表がない / 表が崩れている / 表に該当数値が無い場合は `pages_data[i].text` から数値抽出**: 文章記述（例: 「受注残高は前年同期比で大幅に増加」「当四半期の受注高は 1,234 百万円」）を正規表現や文脈解釈で拾う
3. **同一ページに複数の表が混在する場合**: 各表の見出し行（先頭行 [0] or 列ラベル）に §抽出対象 キーワード（受注高/受注残/繰越/手持/受注件数/受注棟数 等）がマッチする表だけを採用。マッチしない表（売上 P/L 表・配当方針表・配当推移表等）はノイズと判定して除外
4. **表 vs text 値不一致時の優先規約**:
   - **表優先（既定）**。表に明示数値あり、text に別値の場合は表を採用
   - 表に値なしで text のみある場合は text 採用
   - 両方値ありで矛盾する場合は表優先 + `note` に `value_conflict_table_priority` を追記
   - ただし表の値が桁ズレ（例: 表 = 123、text = 1,230,000）の場合は単位 `u` フィールドの整合性を再確認

画像 PDF fallback で Read tool を使った場合は従来通り Read 結果から数値抽出する（tables は無い）。

**`table_extract_errors` の取扱**: `pages.json` の `table_extract_errors` 配列が空でない場合、その内容を Step 8 戻り値の `ERRORS` または `HIGHLIGHTS` に必ず転記する（silent skip 禁止）。

#### JSON フォーマット（短縮キー）

```json
{
  "t": "<ticker>",
  "n": "<会社名>",
  "u": "<単位: 百万円/千円/億円 等>",
  "note": "<下記キー定義を参照。null も可>",
  "d": [
    {
      "doc_id": "<TDnet DOC_ID>",
      "src": "YYYY-MM-DD",
      "title": "<文書タイトル>",
      "kind": "<指標名: 受注高 / 受注残高 / 繰越工事高 等。PDF 記載どおり>",
      "cum": <true=期初累積 | false=各Q単独>,
      "axis": "<内訳軸: セグメント / 地域 / 製品 等。内訳なしは null>",
      "h": ["列ヘッダ1", "列ヘッダ2", ...],
      "r": [
        ["行名1", 数値1, 数値2, ...],
        ["行名2", 数値1, 数値2, ...]
      ]
    }
  ]
}
```

#### キー定義（全フィールド）

| キー | 型 | 必須 | 説明 |
|------|-----|------|------|
| `t` | string | required | ticker（4 桁英数、ゼロ埋めなし） |
| `n` | string | required | 会社名（BQ `FILER_NAME` をそのまま） |
| `u` | string | required | 単位（"百万円" / "千円" / "億円" 等。文書間で混在する場合は最頻値を採用し `note` に注釈） |
| `note` | string \| null | **optional** | 補足情報。下記ルール参照 |
| `d` | array | required | 表データ配列。`STATUS=completed_partial` 含む成功時は 1 要素以上。`failed_*` で JSON 出力する場合（後述）は `[]` |

**`note` のドメイン規約（セミコロン区切り）**:

**許可キーは以下の 5 種類のみ**（コマンダー側のパース仕様は `orders_commander.md` に分離 — 単一責任原則）:

- `partial; N_of_6_docs_read` — `STATUS=completed_partial` 時に**必須**で書く（N は実際の DOCS_WITH_DATA）。**理由が何であれ省略禁止**（上場前で物理的に <6 文書しか無い等の事情があっても省略しない）。理由を付記したい場合は後段の `reason=...` キーで併記する
- `reason=pre_ipo` / `reason=delisted` / `reason=corrected_disclosure` 等 — partial の理由付記（任意）
- `unit_mixed; 百万円_to_千円` — 単位混在を最頻値に揃えた場合の警告
- `pages_retry_used; doc_N` — pages 指定リトライで読んだ PDF がある場合
- `value_conflict_table_priority` — 表と text で数値矛盾があり表優先で採用した場合の警告（§数値抽出の優先順位 §4 参照）

**フォーマット**:
- 区切りは `; ` (セミコロン + 半角スペース)
- `note` が無い場合は **`null` を入れる**（キーごと省略しない）

**それ以外の独自キーは全て禁止**（コマンダー側の機械パースを安定させるため）。以下は禁止例:
- `saas_subscription_business` （153A 違反事例）
- `metric_is_contract_acquisition_not_orders`
- `failed_no_data; no_order_metrics_disclosed`
- その他 PDF 内容に基づく業種タグ・指標タグ全般

セマンティックな情報は `d[].kind` フィールドにすでに記録されているため、`note` で追加する必要はない。

**`d[]` 要素のキー定義**:

| キー | 型 | 必須 | 説明 |
|------|-----|------|------|
| `doc_id` | string | required | 出典 TDnet 文書 ID（BQ `DOC_ID`）。再検証用 trail |
| `src` | string | required | 開示日 `YYYY-MM-DD` |
| `title` | string | required | 文書タイトル（BQ `DOC_TITLE`） |
| `kind` | string | required | 指標名（PDF 記載どおり、後工程で正規化前提） |
| `cum` | boolean | required | true=期初累積、false=各 Q 単独 |
| `axis` | string \| null | required | "セグメント" / "地域" / "製品" / null。null は内訳なし |
| `h` | array[string] | required | 列ヘッダ |
| `r` | array[array] | required | 行データ。各行 1 列目は行名、2 列目以降は数値 or null |

**`cum` × `axis` の 4 通り組合せ**（`h`/`r` の構造はすべて共通）:

| `cum` | `axis` | 意味 | 行名の埋込み |
|-------|--------|------|-------------|
| `true` | `null` | 期初からの累計、内訳なし | `<期>` のみ（例: `2024/3`） |
| `true` | `"セグメント"` | 期初からの累計、セグメント別 | `<期>-<セグメント名>`（例: `2024/3-エナジー`） |
| `false` | `null` | 各 Q 単独、内訳なし | `<期>` のみ |
| `false` | `"セグメント"` | 各 Q 単独、セグメント別 | `<期>-<セグメント名>` |

**4 通り共通ルール**:
- `h` の 1 列目は常に `"期"`、2 列目以降は `"1Q"`, `"2Q"`, `"3Q"`, `"通期"` または `"4Q"`（決算期に応じて）
- `r` の各行 1 列目は **期キー**（`axis != null` のときは `<期>-<内訳名>` 合成）
- 欠損値は `null`
- マイナスは `-数値`（△ は使わない）

#### 例（三菱重工 7011）

`completed`、`note: null`、`d` に `cum × axis` の複数組合せを含むサンプル:

```json
{
  "t": "7011",
  "n": "三菱重工業",
  "u": "百万円",
  "note": null,
  "d": [
    {
      "doc_id": "140120240509000123",
      "src": "2024-05-09",
      "title": "2024/3期 決算短信",
      "kind": "受注高",
      "cum": true,
      "axis": null,
      "h": ["期", "1Q", "2Q", "3Q", "通期"],
      "r": [["2024/3", null, null, null, 5852300]]
    },
    {
      "doc_id": "140120240509000123",
      "src": "2024-05-09",
      "title": "2024/3期 決算短信",
      "kind": "受注残高",
      "cum": false,
      "axis": "セグメント",
      "h": ["期", "1Q", "2Q", "3Q", "4Q"],
      "r": [
        ["2024/3-エナジー", null, null, null, 5200000],
        ["2024/3-プラント", null, null, null, 3100000],
        ["2024/3-物流",     null, null, null, 2200000]
      ]
    },
    {
      "doc_id": "140120250509000456",
      "src": "2025-05-09",
      "title": "2025/3期 決算短信",
      "kind": "受注高",
      "cum": true,
      "axis": null,
      "h": ["期", "1Q", "2Q", "3Q", "通期"],
      "r": [["2025/3", null, null, null, 6912000]]
    }
  ]
}
```

`completed_partial` の場合（`note` 必須記載例）:

```json
{
  "t": "1780",
  "n": "ヤマウラ",
  "u": "百万円",
  "note": "partial; 3_of_6_docs_read; pages_retry_used; doc_2",
  "d": [
    /* ... 3 文書分の表 ... */
  ]
}
```

#### 原子保存

```bash
# 一時ファイルに書く
Write tool: C:/gdrive/claude/work/{ticker}.json.tmp

# アトミックに rename
mv "/c/gdrive/claude/work/{ticker}.json.tmp" "/c/gdrive/claude/work/{ticker}.json"
```

書き込み途中でクラッシュしても壊れた JSON が残らないようにするため、必ず `.tmp` → `mv` の
2 段階で保存する。

#### failed_* 系での既存 JSON 削除

`STATUS=failed_*`（4 種いずれか）で JSON 出力しない場合、`C:/gdrive/claude/work/{ticker}.json` が**既に存在していれば削除する**:

```bash
rm -f "/c/gdrive/claude/work/{ticker}.json"
```

これは過去 run で生成された古い JSON が STATUS と整合しなくなる事態を防ぐため（インデックス CSV の `json_path` が空文字なのに実ファイルが残る食い違いを防ぐ）。

### Step 7: PDF 削除 + status file + hb 終了処理（厳密な順序で必須）

**処理順序（race 回避のため厳守）**:

1. **PDF + 中間ファイル削除**（Step 4-B `{ticker}.pages.json` も同時クリーンアップ）:
   ```bash
   rm -f /c/tmp/tdnet_orders/{ticker}_*.pdf /c/tmp/tdnet_orders/{ticker}.pages.json
   # または glob 一括: rm -f /c/tmp/tdnet_orders/{ticker}_* /c/tmp/tdnet_orders/{ticker}.pages.json
   ```

2. **status file 書き出し（KEY=VALUE 全フィールド、必須）**:
   ```bash
   cat > "/c/gdrive/claude/work/_status/{ticker}.status" <<EOF
   TICKER={ticker}
   STATUS={status_value}
   DOCS_FOUND={docs_found}
   DOCS_READ={docs_read}
   DOCS_WITH_DATA={docs_with_data}
   JSON_PATH={json_path_or_none}
   JSON_BYTES={json_bytes}
   EOF
   ```
   - 文字コード: UTF-8、改行: `\n`、KEY=VALUE 形式
   - `{status_value}` は §Step 8 で確定した STATUS 値（6 種類のいずれか）
   - `{json_path_or_none}` は JSON 出力時のフルパス、failed_* 時は `(none)`
   - **コマンダーは task-notification が来なくても _status/ を polling し、この KEY=VALUE をパースして CSV 更新できる**（status 1 行のみでは数値フィールドが取れず CSV 空更新になる事故防止）

3. **hb 最終更新**（status file が確実に書かれた後）:
   ```bash
   date -Iseconds > "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"
   ```

4. **hb 削除**（完了シグナル）:
   ```bash
   rm -f "/c/gdrive/claude/work/_heartbeat/{ticker}.hb"
   ```

> **順序が重要**: status 先 → hb 後。コマンダーは「hb 消失 + status file 出現 = 完了」と解釈するため、status を先に書いてから hb を消す。逆順だと「hb 消失 → コマンダーが完了通知判定 → status まだ無い → 数値取れず CSV 空更新」の race が起こる。

### Step 8: 結果報告（呼び出し元への戻り値）

呼び出し元（受注高抽出コマンダー）に以下の形式で報告する:

```
TICKER: {ticker}
STATUS: <下記いずれか>
DOCS_FOUND: <BQ で取得できた DOC_ID 数（最大 6）>
DOCS_READ:  <Read に成功した PDF 数>
DOCS_WITH_DATA: <数値抽出できた PDF 数。0 ≤ DOCS_WITH_DATA ≤ DOCS_READ ≤ DOCS_FOUND ≤ 6 を必ず満たす>
JSON_PATH: C:/gdrive/claude/work/{ticker}.json | (none)
JSON_BYTES: <int> | 0
HIGHLIGHTS:
- <重要トピック1>
- <重要トピック2>
ERRORS: <あれば。複数なら箇条書き>
```

**DOCS_WITH_DATA 計算式（必須）**:

```
DOCS_WITH_DATA = len(set(d_elem['doc_id'] for d_elem in d))
```

つまり JSON の `d[]` 要素数 `len(d)` ではなく、`d_elem['doc_id']` のユニーク数を返す。1 PDF 内に複数の表（例: `kind=受注高` + `kind=受注残高`）があると `d[]` 要素は 2 以上になるが、それらは同じ `doc_id` を共有するため DOCS_WITH_DATA は 1 とカウントされる。

> **過去違反事例**: 166A ソルジャーが DOCS_WITH_DATA=13 を返却（spec 上 max=6）。原因は `len(d)` で代入していたため。この計算式を厳守すること。

**容量制約**:
- `HIGHLIGHTS`: **最大 5 行 / 1 行 100 文字以内**。
  - **根拠**: コマンダーは `HIGHLIGHTS` をインデックス CSV にも `orders_log.tsv` にも記録しない仕様（`skills/orders_commander.md` L530「ソルジャー戻り値の文言（HIGHLIGHTS 等）はインデックスには記録しない（ログにも記録しない。容量肥大防止）」）。
  - ただし debug 用に残す価値はある（人手でログ確認時に重要トピックを即把握できる）。**空にせず**要点を簡潔に絞ること。
- `ERRORS`: 該当なしの場合は `なし` の 1 行のみ。**Step 4-B `table_extract_errors` 配列が非空の場合は必ずここに転記**（silent skip 禁止、§Step 6 §`table_extract_errors` の取扱 参照）
- 数値フィールド（`DOCS_FOUND` 等）はゼロや空でも省略せず必ず行として出力

#### STATUS 一覧（DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA で排他的に判定）

| STATUS | 判定条件 | JSON 出力 | reason（_failed.csv 用） |
|--------|---------|-----------|--------------------------|
| `completed` | `DOCS_WITH_DATA >= 1` かつ `DOCS_READ == DOCS_FOUND` かつ `DOCS_FOUND == 6` | あり（`d` ≥ 1） | — |
| `completed_partial` | `DOCS_WITH_DATA >= 1` かつ（`DOCS_READ < DOCS_FOUND` または `DOCS_FOUND < 6`） | あり（`d` ≥ 1）、`note` に `partial; N_of_6_docs_read` 必須 | — |
| `failed_no_bq_records` | `DOCS_FOUND == 0` | なし | `no_bq_records` |
| `failed_no_gcs_files` | `DOCS_FOUND >= 1` かつ全 `gsutil cp` 失敗（DL 0 件） | なし | `no_gcs_files` |
| `failed_pdf_unreadable` | DL は 1 件以上成功、しかし `DOCS_READ == 0`（全 PDF が 3 回試行で読めず） | なし | `pdf_unreadable` |
| `failed_no_data` | `DOCS_READ >= 1` かつ `DOCS_WITH_DATA == 0` | なし | `no_data_classified_as_false_positive` |

**判定優先順位（上から評価）**:
1. `DOCS_FOUND == 0` → `failed_no_bq_records`
2. DL 0 件 → `failed_no_gcs_files`
3. `DOCS_READ == 0` → `failed_pdf_unreadable`
4. `DOCS_WITH_DATA == 0` → `failed_no_data`（**偽陽性銘柄**: Step 5 §「期待する数値がなかった」場合の判定で言及した、Gemini 分類エラーで紛れ込んだ業種）
5. `DOCS_WITH_DATA >= 1` かつ `DOCS_READ == DOCS_FOUND == 6` → `completed`
6. 上記以外（`DOCS_WITH_DATA >= 1` だが完璧でない） → `completed_partial`

呼び出し元（コマンダー）はこの STATUS と reason を `_failed.csv`（`ticker, reason, timestamp`）
への追記、または `pending → completed` への移動の判定に使う。本スキルは `_failed.csv` を書かない。

---

## 禁止事項

- **Python スクリプトで効率化しない**（PDF 読みは Read ツール経由でこのスキル自身が行う。ただし Step 4-B PyMuPDF 前処理は spec 必須経路で「効率化」ではなく「LLM 投入対象のフィルタリング」）
- **インデックスファイル（pending.txt / _failed.csv 等）を編集しない**（呼び出し元 = コマンダーの責任）
- **複数銘柄を同時処理しない**（1 起動 = 1 銘柄）
- **既存 JSON を「再利用」しない**（Step 1 通り常に上書き）
- **`failed_*` 系で JSON を出力しない**（caller が「保存せよ」と prompt で書いていても spec §STATUS 表が優先。Step 1 参照）
- **`note` に許可キー (`partial` / `reason=` / `unit_mixed` / `pages_retry_used`) 以外を書かない**（Step 6 §note ドメイン規約参照）
  - 禁止例: `saas_subscription_business`, `metric_is_contract_acquisition_not_orders`, `failed_no_data; no_order_metrics_disclosed` 等、業種タグ・指標タグ・理由タグの独自付与全て禁止
- **`completed_partial` で `partial; N_of_6_docs_read` を理由問わず省略しない**（同上）
- **「他 agent が並行起動中」を理由に Step 1 上書き方針を破らない**（ticker 単位ファイル分離で競合は構造的に発生しない。Step 1 参照）
- **他 ticker を処理中の並行 agent の存在を理由に、自分の ticker 処理を中断・スキップ・既存ファイル尊重しない**
- **タスク開始後の自己判断による処理中断は spec 違反として禁止**（spec が明示的に中断条件として定義しているケース＝ハートビート 30 分超 / context 枯渇接近 等を除く。並行 agent の存在・他 task の進行・自分への割り込み懸念等は中断理由にならない）
- **金額 (百万円/千円/億円) が無いことを理由に数量 (棟/戸/件) を「抽出対象外」と判定しない**（金額と数量は等価扱い。Step 5 §抽出対象 参照）
- **ハートビート更新を欠かさない**（Step 1 初回、Step 4-B PyMuPDF 処理直前、Step 5 pages.json 読込直前 + 画像 PDF fallback の各 Read 試行直前、Step 7 終了直前。欠落すると 30 分でコマンダーから強制終了される）
- **status file 書き出しを欠かさない**（Step 7 §2 で KEY=VALUE 全フィールドを書く。欠落するとコマンダーが status file polling 経路で CSV 更新できず空更新事故になる）
- **Step 7 の処理順序（PDF削除→status→hb更新→hb削除）を入れ替えない**（status を hb 削除より後にすると race で CSV 空更新が起きる。Step 7 参照）
- **Step 7 PDF 削除時、中間ファイル `{ticker}_*.pages.json` も同時にクリーンアップする**（`rm -f /c/tmp/tdnet_orders/{ticker}_*` のように glob で同時削除。残骸が次回 resume の汚染源になる）
- **DOCS_WITH_DATA に `len(d)` を返さない**。必ず `len(set(d_elem['doc_id'] for d_elem in d))` で計算する（Step 8 §DOCS_WITH_DATA 計算式 参照）
- **HIGHLIGHTS を 5 行超で返却しない**（要点を絞ること、ただし空にはしない。コマンダー側でインデックス・ログともに記録されないが debug 用に残す価値あり。Step 8 §容量制約 参照）
- **Step 4-B の `table_extract_errors` を silent skip しない**（pages.json 読込時に必ず確認し、非空なら ERRORS or HIGHLIGHTS に転記。§Step 6 §`table_extract_errors` の取扱 参照）
- **表抽出に PyMuPDF `find_tables()` を使わない**（v2 以降、表抽出は pdfplumber `extract_tables()` に統一。062 知見§4.1 で PyMuPDF find_tables の弱さ既知）
- **表 vs text 値が不一致時に text 優先で採用しない**（既定は表優先、`note` に `value_conflict_table_priority` 追記。§Step 6 §数値抽出の優先順位 §4 参照）

---

## 注意事項

- BQ 接続: プロジェクト `gmailpj-357912`、テーブル `STOCK.TDNET_DOCUMENTS_ENHANCED`
- データ取得元: `gs://stock_data_1930932/tdnet/{ticker}/{FILE_NAME}`
- 出力先: `C:/gdrive/claude/work/` 配下（git 管理外）
- 一時ファイル: `C:/tmp/tdnet_orders/` 配下（処理後削除）
- PDF ファイル名に日本語・空白・特殊記号が含まれることがある → `gsutil cp` 引数はダブルクォート必須
- Bash パス表記は Windows パス `C:\...` を直接使わず、フォワードスラッシュ `C:/...` または Unix 形式 `/c/...` を使う
- 実行時冒頭に `🎯 [orders-soldier] ticker={ticker}` を 1 行出力する（CLAUDE.md §8 規約）
- **PyMuPDF (`pymupdf` / `import fitz`) は本ソルジャーの必須依存**: `pyproject.toml` に `pymupdf>=1.27.2.2` 登録済、venv `C:/venvs/investment-agent` にインストール済（2026-05-18 確認）。再現性担保のため `uv sync` で venv 再構築すれば自動的に入る。本スキル内で個別に `uv add` を実行する必要はない
- **pdfplumber も本ソルジャー必須依存（v2 以降）**: `pyproject.toml` に `pdfplumber>=0.11` 登録済、venv に 0.11.9 インストール済（2026-05-19 確認）。表抽出本体は pdfplumber `extract_tables()` を使用（062 知見§4.1 の通り PyMuPDF `find_tables()` より精度が高い）
- **PyMuPDF AGPL ライセンス**: 個人用解析パイプラインのため AGPL 適用 OK（非配布）
- **CHUNK_INDEX IS NOT NULL の罠**: BQ クエリの WHERE 句に `CHUNK_INDEX IS NOT NULL` を**絶対に付けない**。2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する（BQ クエリ一般の注意事項）
