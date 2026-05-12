# 月次開示エラー全自動解決エージェント

## 役割

月次開示パイプライン（download_monthly.py / extract_monthly_data.py）のCloud Run Job実行後エラーを**自律的に**診断・修正・検証する。人間の指示を待たず、エージェント起動1回で完結させる。

---

## 社訓（最優先原則、全工程で常に適用）

1. **1社ずつ丁寧に**: 時間制限なし。品質最優先。バッチ的な推定・一括処理・「たぶんこうだろう」禁止
2. **修正→ローカル検証→本番は不可侵**: adapter修正後、必ずローカルで1社テスト実行して結果を確認してからGCSアップロード・本番投入。この3段階を省略・短縮しない

---

## 起動方式

Agent ツールによるサブエージェント起動。

```
Agent(
  description="月次エラー全自動修復",
  prompt="skills/monthly-error-autofix.md を Read してエージェントとして実行せよ。
    対象: <execution名 or '直近'>"
)
```

---

## Gemini API使用許可

本スキル実行時、以下の用途に限りGemini API使用を許可する:
- ローカル検証実行時（`--no-batch` モードで `extraction_method: "gemini"` のadapterをテスト）
- extraction_methodをgeminiに設定する判断のためのPDF構造確認

上記以外の目的でGemini APIを呼び出さない。

---

## 実行環境（迷走防止）

| 項目 | 値 |
|------|---|
| Python | `C:/venvs/investment-agent/Scripts/python.exe`（探し回らない。これが唯一のパス） |
| 実行時環境変数 | `PYTHONUTF8=1` を必ず付ける |
| gcloud / gsutil | Bash ツール（Git Bash）で実行。PowerShell 不可 |
| Bash パス表記 | フォワードスラッシュ（`C:/gdrive/...`）。バックスラッシュ禁止 |
| GCS バケット | `gs://stock_data_1930932/` |
| GCS adapter パス | `monthly/meta/{ticker}/extract_adapter.json` |
| GCS docs パス | `monthly/docs/{ticker}/` |
| ローカル作業用 | `C:/tmp/`（一時ファイル置き場） |
| プロジェクトルート | `C:/gdrive/claude/investment-agent/`（= `G:\マイドライブ\claude\investment-agent\`） |

**禁止**: venv ディレクトリの探索・which python の実行・pip install の試行。上記パスをそのまま使う。

---

## 実行フロー

### Step 0: 知識ロード

| ファイル | 目的 | いつ読むか |
|---------|------|-----------|
| `docs/knowledges/tools/042_monthly_disclosure_master.md` §ファイルマッピング絶対表 + §extract_adapter.json正式スキーマ定義 | adapter種別・パス構造・スキーマ | **常に必須** |
| `docs/knowledges/tools/042-1_monthly_error_fix_patterns.md` | パターンDB | **DL系エラー（D1-D4）+ E6系（bc_ignore判定）**。E1-E5では事前ロード不要 |

### Step 1: ログ取得・エラー検出

```bash
# 直近の execution を特定
gcloud run jobs executions list --job=extract-monthly-data --region=us-west1 --limit=1
gcloud run jobs executions list --job=download-monthly --region=us-west1 --limit=1

# ログ取得
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=<job> AND labels.\"run.googleapis.com/execution_name\"=<exec>" --limit=100 --format="value(timestamp,textPayload)"
```

「失敗=N」「ERROR」「スキップ」を抽出。失敗tickerをリスト化。

### Step 2: エラー分類

各tickerのエラーを以下のカテゴリに分類:

| コード | 症状 | 対応方針 |
|--------|------|---------|
| D1 | 404/ドメイン変更 | url_adapter修正 |
| D2 | CSS selector不一致 | url_adapter修正 |
| D3 | DNS解決不可 | skip（企業サイト停止） |
| D4 | その他ネットワーク | retry / 翌日再確認 |
| E1-E5 | extract adapter系（regex不一致・年月検出失敗・regex限界・Gemini応答異常・空文字キー・format不一致） | → **Step 3A（自律修復）** |
| E6 | GCS文書不在・内容ミスマッチ・bc_ignore判定 | → **Step 3C（bc_ignore判定フロー）**。DLアダプタ上流チェック必須 |

### Step 3A: extract adapter 自律修復

**extract adapter（E1-E5）は過去事例の検索・パターンDB照合を行わない。** PDF/HTML実物を直接確認して修正する。パターンDBは参考にはなるが、事前ロード・網羅照合はトークン浪費。E6（bc_ignore判定）は → Step 3C。

#### 判断の指針（修復サイクルの前に頭に入れる。探索しない）

- regex で取れそうなら regex。テーブル構造が複雑・画像PDF・英語混在なら `extraction_method: "gemini"`
- fields の `key` / `bc_key` は structure.json の `monthly_items[].name` と一致させる
- GCS文書にデータがない/文書自体がない → **Step 3C（bc_ignore判定フロー）** へ。ここで直接bc_ignoreを設定しない
- 累積型PDF（1ファイルに全月分）は `overwrite_past_months: true`

#### 修復サイクル（1社分）

1. **現行adapter取得**: GCSから取得して一時保存
   ```bash
   gsutil cat gs://stock_data_1930932/monthly/meta/<ticker>/extract_adapter.json > C:/tmp/<ticker>_extract_adapter.json
   ```
2. **PDF/HTML実物取得**: GCS docs から最新1件を `C:/tmp/` にDL
   ```bash
   gsutil ls gs://stock_data_1930932/monthly/docs/<ticker>/ | tail -1
   gsutil cp gs://stock_data_1930932/monthly/docs/<ticker>/<filename> C:/tmp/
   ```
3. **実物を読んで判断**: テキスト抽出して構造を把握し、042スキーマ定義に照らしてadapterを修正
   ```bash
   # PDF の場合
   PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe -c "
   import pdfplumber
   with pdfplumber.open('C:/tmp/<filename>') as pdf:
       for page in pdf.pages:
           print(page.extract_text())
   "
   # HTML の場合: Read ツールで C:/tmp/<filename> を直接閲読
   ```
4. **Edit**: `C:/tmp/<ticker>_extract_adapter.json` を修正（1社1Edit、コンテキスト蓄積禁止）
5. **ローカル検証**: Step 4 へ（検証OKなら次へ）
6. **正式パス保存 + GCS同期**:
   ```bash
   cp C:/tmp/<ticker>_extract_adapter.json meta/monthly/<ticker>_extract_adapter.json
   gsutil cp meta/monthly/<ticker>_extract_adapter.json gs://stock_data_1930932/monthly/meta/<ticker>/extract_adapter.json
   rm C:/tmp/<ticker>_extract_adapter.json C:/tmp/<filename>
   ```
7. **中間commit**: 5社処理ごと、または15分経過ごとに `git commit`（CLAUDE.md §逐次永続化義務）

#### やらないこと（E1-E5に適用。E6はStep 3Cで対応）

- 042-1パターンDB の事前ロード・網羅検索
- 同業種他社adapter の横展開参照
- git log での過去修正履歴調査
- 仮説3個立てるような形式的手順

> **要するに**: 現物を見て、スキーマを知っていて、直す。それだけ。

### Step 3C: bc_ignore判定フロー（DLアダプタ上流チェック必須）

GCSに月次開示文書がない、または文書内容が月次データと無関係な場合に適用する。
**bc_ignoreはextract adapter側の最終手段**。設定前に必ずDLアダプタ（url_adapter）起因でないことを確認する。

#### フロー（省略禁止）

1. **GCS文書確認**: `gcloud storage ls gs://stock_data_1930932/monthly/docs/{ticker}/` でファイル一覧取得
   - ファイルあり → **中身を必ず確認してから判断**（ファイル名・拡張子だけで「無関係」と断定禁止。PDF→pdfplumberテキスト抽出、HTML→テーブル構造確認）→ 月次データあり: Step 3A修復サイクルへ / 月次データなし: 次へ
   - **ファイルなし** → 次へ（DLアダプタ上流チェック）

2. **DLアダプタ上流チェック**（★ここがbc_ignore前の必須ゲート）
   a. url_adapter.json を取得: `gcloud storage cat gs://stock_data_1930932/monthly/meta/{ticker}/url_adapter.json`
   b. `ir_page_url` が有効か確認: WebFetch or curl_cffi でアクセス
   c. 企業IRページに月次開示があるか確認:
      - 月次PDF/HTMLテーブル/eIRウィジェットが**実在する** → **DLアダプタ不備（D系修復に回す）**。bc_ignore **設定禁止**
      - 月次開示ページ自体が存在しない → **企業が月次開示していない（bc_ignore正当）**
      - 月次開示を過去やっていたが停止した → **開示中断（bc_ignore + extraction_notesに停止時期記録）**

3. **判定結果**:
   - DLアダプタ不備 → Step 3B（D系修復）へ
   - 企業側に月次データなし → bc_ignore設定。以下の分類で `bc_ignore_reason` を明記:
     - E6-1: GCS文書種別ミスマッチ（GCSに別種文書のみ）
     - E6-2: HTML空テーブル（JSレンダリング不可）
     - E6-3: HTML内容ミスマッチ（テーブルはあるが月次データでない）
     - E6-4: セグメントミスマッチ（月次PDFはあるが対象セグメントのデータなし）
     - E6-5: 開示中断・停止

#### ツールレシピ

- **GCSファイル一覧**: `gcloud storage ls gs://stock_data_1930932/monthly/docs/{ticker}/`
- **HTMLテーブル数カウント**:
  ```bash
  PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe -c "
  import re, sys
  html = open(sys.argv[1], encoding='utf-8').read()
  tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
  print(f'テーブル数: {len(tables)}')
  for i, t in enumerate(tables):
      headers = re.findall(r'<th[^>]*>(.*?)</th>', t)
      print(f'  [{i}] ヘッダ: {headers[:5]}')
  " C:/tmp/monthly_table.html
  ```
- **BC structure.json逆引き**: `gcloud storage cat gs://stock_data_1930932/monthly/meta/{ticker}/structure.json | python -c "import json,sys; d=json.load(sys.stdin); [print(m['name']) for m in d.get('monthly_items',[])]"`

### Step 3B: DL系エラー修復（D1-D4）

DL系エラーは従来通り調査が必要:

| 分類コード | 調査戦略 |
|---|---|
| D1 | `git log --all --grep='<ticker>'` で過去修正履歴 + WebSearchで企業IR最新URL調査 |
| D2 | WebFetchでIRページ取得 → 現在のHTML構造を確認 → selector修正 |
| D3 | 企業サイト生存確認。閉鎖なら `_excluded: true` で論理削除 |
| D4 | 翌日再実行で解消するか確認。解消しなければD1-D3再判定 |

解決しない場合は全リソース投入:
1. **企業IR情報の最新状態確認**: WebSearch/WebFetchで企業IRページ直接確認
2. **仮説→検証ループ**: 仮説を立て1ファイルで検証。最大3仮説
3. **コード本体の制約調査**: adapter修正では不可能と判断した場合、修正案を添えてエスカレーション

### Step 4: ローカル検証（★社訓）

修正したadapterで1社テスト実行:

```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_monthly_data.py --tickers <ticker> --no-batch
```

- 正しい値が抽出されたか確認（件数、年月範囲、数値の妥当性）
- NG → Step 3に戻り修正
- OK → Step 5へ

DL系修正の場合:
```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/download_monthly.py --tickers <ticker> --dry-run
```

### Step 5: 本番投入

> extract adapter修復の場合、Step 3A-6 でローカル正式パス保存+GCS同期は完了済み。ここではCloud Run再実行のみ。

全社のローカル検証が完了した後にまとめて実行:
```bash
gcloud run jobs execute extract-monthly-data --region us-west1 --args="--tickers,<全修正ticker>" --async
```

### Step 6: 本番結果検証

30秒間隔でジョブ完了をポーリング（最大10分）:

```bash
gcloud run jobs executions describe <exec-name> --region us-west1 --format="value(status.completionTime,status.succeededCount)"
```

完了後、ログから結果確認:
- 全成功 → Step 7
- 一部失敗 → 2回目の診断ループ（Step 2に戻る。同一tickerは最大2回まで）
- 全失敗 or 2回失敗 → エスカレーション

### Step 7: 報告・記録

1. **LINE通知**: 結果サマリーを `send_ntfy --sender ATP --task "月次エラー修復"` で送信
2. **パターンDB追記**: DL系修復で新たに発見したパターンを042-1に追記（フォーマット遵守）
3. **git commit**: 修正したadapterファイル + パターンDB更新

---

## ガードレール

1. **ローカル検証必須**: adapter修正後、`--no-batch` で1社テスト。テストなしの本番投入禁止
2. **修正上限10社/回**: 超過時はエスカレーション
3. **regex→Gemini変換**: regex 2パターン以上試して失敗した場合のみ
4. **overwrite_past_months**: 累積型PDFのみ。個別月PDF銘柄には絶対に付けない
5. **adapter backup**: 修正前に旧adapter内容をログに記録（gsutil catで表示）
6. **2回失敗でエスカレーション**: 同一tickerが2ループ失敗 → 人間に報告して停止
7. **コード変更禁止**: extract_monthly_data.py / download_monthly.py 本体の修正が必要な場合はエスカレーション（修正案は提示してよい）

---

## エスカレーション時の報告内容

人間に報告する際、以下を必ず含める:
- ticker と企業名
- 試したこと（どこまで進んだか）
- 排除した仮説と理由
- コード修正が必要な場合は具体的な修正案
- 「何が分からなかったか」の明示
