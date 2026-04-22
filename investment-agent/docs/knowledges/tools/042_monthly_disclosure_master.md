> **改修プラン索引**: [月次開示アダプタプログラム改修 マスタープラン](../../plans/20260420_223000_monthly_adapter_master_plan.md) — 全プラン MD・アーキテクチャ確定事項・未完了タスクを一元管理

# 月次開示パイプライン 全体概要 & 収集マスタ

**カテゴリ**: tools
**作成日**: 2026-03-11
**更新日**: 2026-04-11
**ステータス**: 有効
**関連ファイル**: `scripts/` 月次開示関連スクリプト群全般

---

## 最終目標

月次開示データを活用し、**月次開示が決算に織り込まれているか否かを株価から判断し、織り込まれていないが好決算が見込まれる企業を決算前に買う**。

---

## ⚠️ ファイルマッピング絶対表 (2026-04-20 確定、変更禁止)

**ファイル名が同じでも意味が違うケースがある**。同期・コピー・上書き時は必ずこの表を参照。

### 現行マッピング（#A 完全分離 2026-04-20 適用後）

| 位置 | パス | 意味 | 判定キー |
|---|---|---|---|
| **LOCAL** | `data/monthly_adapters/{ticker}.json` | **extract adapter** (抽出ルール) | `fields` (list) あり |
| GCS | `monthly/meta/{ticker}/extract_adapter.json` | **extract adapter** (LOCAL と同内容) | `fields` (list) あり |
| GCS | `monthly/meta/{ticker}/url_adapter.json` | **URL adapter** (IR ページ URL + 抽出パターン) | `ir_page_url` / `type` あり |
| GCS | `monthly/meta/{ticker}/structure.json` | BC 月次 KPI メトリクス定義 | `metrics` (list) |
| GCS | `monthly/record/{ticker}/monthly_records.json` | 抽出済み月次数値 | `records` |
| GCS | `monthly/docs/{ticker}/*.{pdf,xlsx,html}` | 月次開示ファイル本体 | - |

### 廃止されたパス（書き込み・読み取りともに禁止）

| 廃止パス | 廃止理由 | 代替 |
|---|---|---|
| `monthly/meta/{ticker}/adapter.json` | URL 型と extract 型が混在で事故原因。#A で物理分離 | → `url_adapter.json` (URL型) / `extract_adapter.json` (extract型) |
| `monthly/meta/{ticker}/ir_url.json` | adapter.json に統合済 (現: url_adapter.json) | `url_adapter.json.ir_page_url` |
| `monthly/meta/{ticker}/download_adapter.json` | adapter.json に統合済 (現: url_adapter.json) | `url_adapter.json.css_selector` / `link_*_pattern` |
| `monthlyir/{ticker}/*.{pdf,xlsx,html}` | 2026-04-05 に `monthly/docs/{ticker}/` へ移行済 (旧パス削除) | `monthly/docs/{ticker}/` |

### 不変ルール

1. **ローカル `{ticker}.json` ↔ GCS `extract_adapter.json`** が正しいペアリング
   - ローカル `{ticker}.json` を GCS `url_adapter.json` と同期してはいけない（内容種別が違う）
2. **ローカルには URL adapter を保持しない**。URL adapter は GCS のみ
3. **url_adapter.json (URL型) と extract_adapter.json (extract型) は別物**
   - 判定: `fields` キーの有無 / `ir_page_url` キーの有無
   - ファイル名も分離済なので名前だけで判断可能
4. **同期スクリプトは内容種別を `_detect_adapter_kind()` で検証せよ**
   - 詳細: `docs/knowledges/tools/004_coding_conventions.md` 「ファイル同期・コピー・上書き時の内容種別検証」
5. **廃止パスには書き込まない・読み取らない**
   - 例: `adapter.json` / `ir_url.json` / `download_adapter.json` / `monthlyir/`
   - 既存コードで参照している箇所はすべて `url_adapter.json` / `monthly/docs/` に移行

### 事故事例（2026-04-20）

`scripts/agent_bc/sync_latest_adapters_bg.py` がローカル `{ticker}.json` (extract) と
GCS `adapter.json` (URL) を同期対象として誤マッピング。GCS 側 URL adapter を extract で上書き。
**245 本破壊 → `monthly_adapter_index.csv` から復旧成功**（全件リカバリ完了）。

**再発防止 #A**: `adapter.json` を廃止し、URL 型は `url_adapter.json`、extract 型は
`extract_adapter.json` へ物理分離。ファイル名から意味が一意に決まるようにした。

---

## 本運用パイプライン定義（2026-04-10 確定）

| Step | 内容 | 実行頻度 | 備考 |
|------|------|---------|------|
| Step 1 | 企業一覧・構造取得 | 随時（手動指示時のみ） | |
| Step 2+3+4 | URL探索・adapter更新 | 随時（手動指示時のみ） | |
| Step 5 → 6b | 月次抽出 | 日次/週次/月次（検討中） | 本運用パイプライン |
| Step 6a | アダプタ自動生成 | **運用停止** | スクリプトは残す。自動生成は精度不足と判断 |
| DL失敗フィードバック | Step 5→6bのDL失敗をStep 2+3+4に戻す | 月次 | 実行方法は検討中 |

---

## GCSパス構成（2026-04-11 再配置実施済み）

`monthly/` 配下に機能別サブディレクトリで統合。

```
monthly/
  company_list.json                    ← 月次開示企業一覧
  meta/{ticker}/structure.json         ← 抽出したいメトリクス定義
  meta/{ticker}/adapter.json           ← IRページURL + DLルール
  meta/{ticker}/extract_adapter.json   ← テキスト→数値マッピング・正規表現
  meta/{ticker}/ir_url.json            ← （旧フォーマット）
  meta/{ticker}/download_adapter.json  ← （旧フォーマット）
  meta/_progress.json                  ← monthly_data_load 進捗
  record/{ticker}/monthly_records.json ← 抽出済み数値データ
  record/{ticker}/{yyyy-mm}.json       ← 月別データ（monthly_data_load）
  docs/{ticker}/*.pdf|xlsx|html        ← ダウンロード済みファイル実体
  log/*.json                           ← ダウンロード・実行ログ
  bcdata/*.csv                         ← BC突合CSV
```

**設計判断:**
- meta（定義系）/ record（抽出結果）/ docs（生ファイル）/ log / bcdata に機能分離
- `docs/{ticker}/` はticker単位で格納。`list_blobs(prefix=f'monthly/docs/{ticker}/')` で高速取得

**スクリプト側の定数:**
```python
GCS_META   = "monthly/meta"    # structure, adapter, extract_adapter
GCS_RECORD = "monthly/record"  # monthly_records.json, {yyyy-mm}.json
GCS_DOCS   = "monthly/docs"    # PDF/XLSX/HTML
GCS_LOG    = "monthly/log"     # ログ
```

> **旧パス `monthlydata/`, `monthlyir/`, `buffett_compare/` は残存**（削除は手動確認後）

---

## パイプライン全体像（各Step詳細）

> 各ステップの対象範囲に注意。TDNET開示企業と非開示企業では経路が異なる。

```
【Step 1】企業一覧・構造取得  ← TDNET開示・非開示 両方対象
  buffett_monthly_scrape.py / buffett_monthly_local.py
    ↓ GCS: monthly/company_list.json（月次開示企業一覧 ~500社）
    ↓ GCS: monthly/meta/{ticker}/structure.json（抽出したいメトリクス定義）
    ※ TDNET経由で開示する企業も、自社IRページのみで開示する企業も両方含む

【Step 2+3+4】月次IRページ URL 探索 & adapter.json 更新
              ← TDNET非開示（会社IR HPのみ）が対象。TDNET開示企業はスキップ
  update_monthly_adapters.py  ← Google検索（curl_cffi, chrome124偽装）+ Playwright ページ解析の統合版
    ↓ curl_cffi Google検索 → reCAPTCHA回避で月次IRページURL探索
    ↓ Playwright でページ解析 → eIR SPA検出 / DLリンクパターン推定 / html_table判定
    ↓ GCS: monthly/meta/{ticker}/adapter.json 更新（type / url / DLルールを一括設定）
    ↓ data/monthly_adapter_index.csv 差分更新
    ※ adapter.json の status=skip 銘柄が対象（--tickers で個別指定も可）
    ※ 検出強化（2026-04-12）: IRサブページ探索 / iframe XJ-Storage検出 / HTMLテーブル月次検出
    ※ Yahoo+Gemini 判定は精度改善まで無効化中（コード残し）
    使い方:
      PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
        scripts/update_monthly_adapters.py --force 1417 1420 ...
      --force: active銘柄も再スクレイプ
      --no-headless: ブラウザ可視化（WAF回避時に使用）
      --dry-run: adapter.json書き込みスキップ

【Step 5】月次ファイルダウンロード  ← TDNET非開示（会社IR HPのみ）が対象
  download_monthly.py
    ↓ テスト（ローカル実行）: data/monthlyir/{ticker}_{会社名}/*.pdf / *.xlsx / *.html
    ↓ 本番（Cloud Run 実行）: GCS: monthly/docs/{ticker}/*.pdf / *.xlsx / *.html
    ※ adapter.json の type 別に保存形式が変わる:
       - scrape_links    → PDF/XLSX ファイルをそのまま保存
       - eir_api         → eIR API 経由で PDF 取得
       - html_table      → IRページの HTML をそのまま保存（*.html）← テーブル変換は Step 6 が行う

【Step 6a】抽出アダプター生成  ← TDNET開示・非開示 両方対象
  build_monthly_extractor.py
    Gemini で extract_adapter.json を生成（正規表現パターン + メトリクス定義）
      - TDNET開示企業: BQ の TDNET_DOCUMENTS_ENHANCED からテキストサンプルを取得
        条件: MAIN_CATEGORY='月次開示' OR '月次開示' IN SUB_CATEGORIES
        （MAIN_CATEGORY のみでは漏れる。tdnet_load_parallel.py の Gemini フォールバックで
          SUB_CATEGORIES に '月次開示' が付与されるケースあり）
        --gemini-method フラグ: Gemini が直接数値を抽出するアダプターを生成（精度高）
      - TDNET非開示企業: Step 5 でダウンロードした PDF/XLSX/HTML ファイルから取得
        （html_table 型: HTML テーブルのパースも Step 6a が担う）
    主要オプション:
      --all      : BQ TDNET月次全銘柄 + GCS monthly/docs/ 全銘柄（件数制限なし）
      --rebuild  : 既存アダプターがあっても再構築
      --gemini-method : TDNET企業に高精度アダプターを生成
    ↓ GCS: monthly/meta/{ticker}/extract_adapter.json

【Step 6b】月次データ抽出（数値化）← TDNET開示・非開示 両方対象
  extract_monthly_data.py
    Step 6a で生成した extract_adapter.json の正規表現を使って数値を抽出
      - TDNET開示企業: GCS tdnet/{ticker}/*.pdf を pdfplumber → PyMuPDF の順で解析
        PDF抽出順: ① pdfplumber extract_tables()（表構造を保持、最高精度）
                  ② PyMuPDF fitz（テキストレイヤー）
                  ③ pdfplumber extract_text()（最終フォールバック）
        ※ PDF 処理戦略の総論: `docs/knowledges/tools/062_pdf_processing_strategy.md`
          （OCRmyPDF / LLM 3段ルーティング / PyPDF2 vs pdfminer の議論）
        年月判定: タイトル内の年号（令和/平成/昭和対応）→ 提出日ヒューリスティック
                  （day≤15 → 前月が報告月、day>16 → 当月）
                  ※ デフォルト regex が「YYYY年M月期N月度」形式にヒットした場合も
                    提出日ヒューリスティックへフォールスルー（2026-03-31修正）
      - TDNET非開示企業: GCS monthly/docs/{ticker}/ 以下の PDF/XLSX/HTML を解析
    主要オプション:
      --all      : BQ TDNET月次全銘柄 + monthly_adapter_index.csv active 全銘柄
      --since    : 抽出開始年（デフォルト2020）
    ↓ GCS: monthly/record/{ticker}/monthly_records.json（年月 × メトリクスの数値データ）
```

## GCS ファイル構成（1銘柄分）

```
gs://stock_data_1930932/monthly/meta/{ticker}/
  structure.json          ← buffett_monthly_scrape.py が生成
  adapter.json            ← update_monthly_adapters.py が生成（IRページURL + DLルール）
  extract_adapter.json    ← build_monthly_extractor.py が生成（テキスト→数値マッピング・正規表現）

gs://stock_data_1930932/monthly/record/{ticker}/
  monthly_records.json    ← extract_monthly_data.py が生成（抽出済み数値データ）

gs://stock_data_1930932/monthly/docs/{ticker}/
  *.pdf / *.xlsx / *.html  ← download_monthly.py が保存（月次開示ファイル本体。TDNET非開示企業のみ）
```

> ローカル実行時は `data/monthlyir/{ticker}_{会社名}/` に保存される（ローカルパスは変更なし）
>
> `download_adapter.json` / `ir_url.json` は旧フォーマット。`adapter.json` に統合済み。

## 手動修正の上書き防止フラグ

自動生成プログラム（buffett_monthly_scrape.py / build_monthly_extractor.py）が
GCS ファイルを再生成する際、手動で追加・修正した内容が消えないよう以下のフラグで保護する。

### company_list.json
| フラグ | 場所 | 説明 |
|--------|------|------|
| `manual_add: true` | companies[] 要素 | BC 以外の方法で追加した企業。再生成時にマージ保持 |

### structure.json

`buffett_monthly_scrape.py` が buffett-code 月次 KPI ページから生成する BC 側のメトリクス定義ファイル。GCS `monthly/meta/{ticker}/structure.json` に保存。**adapter の `bc_key` はこのファイルの `monthly_items[*].name` と完全一致すること**を要求する（drift 許容の明示マッピング設計）。

#### スキーマ（公式定義、2026-04-18）

```jsonc
{
  // ─── トップレベル（必須） ───────────────────────
  "ticker":       "1234",                  // 銘柄コード（4桁）
  "name":         "会社名",                // 会社名（BC 表記）
  "scraped_at":   "2026-04-18T12:00:00+09:00",  // スクレイプ時刻 (JST ISO8601)
  "source":       "tdnet_bq",              // 生成元 ("tdnet_bq" 等)
  "doc_count":    42,                      // BQ 上の月次開示件数（参考）
  "latest_date":  "2026-04-10",            // 最新月次開示の発表日
  "latest_title": "2026年3月度 月次...",   // 最新月次開示のタイトル

  // ─── BC 側フィールドリスト（新版）──────────────
  "monthly_items": [
    {
      "name": "全店 売上（前年同月比）",   // BC 正名（adapter.bc_key の参照先）
      // 以下 optional フラグ
      "_manual_add":       true,           // (オプション) BC 以外の方法で追加
      "collection_excluded": true,         // (オプション) 収集対象から除外
      "_excluded_reason":  "...",          // collection_excluded の理由
      "_bc_value_invalid": true,           // BC 側に値あるが信頼できない
      "_note":             "..."           // 自由記述メモ
    },
    ...
  ],

  // ─── レガシー形式（旧版）─────────────────────
  "metrics": [
    {"name": "全店 売上（前年同月比）"}
  ],
  // ※ 古い銘柄では BC 正名リストが "metrics" に入っている。
  //    新版 "monthly_items" と同じ意味だが、buffett_monthly_scrape.py の
  //    旧バージョンで生成された。compare 側は両方読む。
  //    段階移行中のため削除しない。

  // ─── 保護フラグ ──────────────────────────────
  "manual_override": true                  // (オプション) 全体上書き禁止
}
```

#### フラグ一覧

| フラグ | 場所 | 説明 |
|--------|------|------|
| `manual_override: true` | トップレベル | 全体上書き禁止。`buffett_monthly_scrape.py` 再実行時にスキップ |
| `manual_add: true` (= `_manual_add`) | monthly_items[] 要素 | BC 以外の方法で追加したメトリクス。再生成時にマージ保持 |
| `collection_excluded: true` | monthly_items[] 要素 | **収集対象から除外**。adapter生成・抽出・突合 全てから除外。BC未収集 + 管理価値なし時に使用 |
| `_excluded_reason` | monthly_items[] 要素 | `collection_excluded: true` の理由メモ |
| `_bc_value_invalid: true` | monthly_items[] 要素 | BC側に値はあるが信頼できない。我々の抽出値を正とする旨の注記 |
| `_note` | monthly_items[] 要素 | 自由記述メモ |
| `name` フィールド | monthly_items[] / metrics[] | **BC 正名**。adapter.fields[*].bc_key の唯一の参照元 |

#### 新版 `monthly_items` vs 旧版 `metrics`

- 両者とも「BC 側フィールド名のリスト」で意味は同じ
- 新規に `buffett_monthly_scrape.py` が生成するのは `monthly_items` 側
- 古い銘柄の structure.json には `metrics` がそのまま残っている → compare 側は両方集合として扱う
- 段階移行: 古い structure.json は `buffett_monthly_scrape.py` を再実行すれば `monthly_items` 側に移る（`manual_override: true` 未設定の銘柄のみ）

### extract_adapter.json
| フラグ | 場所 | 説明 |
|--------|------|------|
| `manual_override: true` | トップレベル | 全体上書き禁止。`--rebuild` でもスキップ |
| `_excluded: true` | トップレベル | **論理削除**（管理対象外）。extract / compare 全パイプラインが early-skip |
| `_excluded_reason` | トップレベル | `_excluded: true` の理由 |
| `_excluded_at` | トップレベル | 論理削除日時（JST ISO8601）|
| `key` | fields[] 要素 | records に保存されるフィールド名。adapter 固有命名可（日本語/英語自由）|
| `bc_key` | fields[] 要素 | **BC 側正名へのマッピング**（= structure.json.monthly_items[*].name）。compare 時 BC と突合するキー。省略時は `key` を暗黙の `bc_key` とみなす |
| `yoy_offset` | fields[] 要素 | `our_val + yoy_offset` を比較値とする（例: 前年同月比 +100 補正）|
| `unit_scale` | fields[] 要素 | `our_val * unit_scale` を比較値とする（例: 百万円→円で 1000000）|
| `bc_floor` | fields[] 要素 | 旧: BC 側整数切り捨て補正。**2026-04-18 改修以降は実質不要**（compare 側が BC 表示精度を自動検出し floor 候補を含めて比較するため）|
| `bc_ignore: true` | fields[] 要素 | **BC突合のみスキップ**（抽出は継続）。BC側データ誤り or BC側未収集（我々の値を正とする）時に使用 |
| `_bc_ignore_reason` | fields[] 要素 | `bc_ignore: true` の理由メモ |
| `_manual_description` | fields[] 要素 | description を手動で上書き済み。rebuild 時に保持 |
| `_calc` | fields[] 要素 | 複数行合算等の特殊計算指示 |
| `row_label_regex` | fields[] 要素 | regex 抽出用パターン（`extraction_method: "regex"` 時に使用）|
| `group` | fields[] 要素 | regex の対象 capture group 番号（`{fy_month_idx}` / `{col_idx}` プレースホルダー可）|
| `use_last_number` | fields[] 要素 | regex マッチ内の末尾数値を採用（月度 PDF の当月値取得等）|
| `column_map` | トップレベル | `{"report_month": phys_col_idx}` — 「上期/下期/累計」等が挟まる表の物理列 index を明示（7918 が事例）|
| `fiscal_year_start_month` | トップレベル | 会計年度開始月（`{fy_month_idx}` 計算に使用、デフォルト 1）|
| `month_direction` | トップレベル | "row": 月が行方向のテーブル（デフォルトは列方向） |
| `overwrite_past_months` | トップレベル | 最新PDFから過去月データも上書き |

#### 設計思想: key と bc_key の分離（2026-04-18 復元）

過去の一時期、`fix_english_key_adapters.py` で adapter の `key` を BC 日本語名に強制書き換えて「両者同じ文字列」に統一した経緯があるが、これは本来の設計から逸脱していた。2026-04-18 改修で**元の設計に戻した**:

- **`key`** — records に保存するフィールド名。adapter 固有命名（independent naming）。英語でも日本語でも可
- **`bc_key`** — BC 側の正式フィールド名（= structure.json.monthly_items[*].name に完全一致）。**BC 突合時の唯一の mapping 情報**
- **両者が異なっても OK**（drift 許容）。compare 側は `bc_key` で直接引き当てるだけで、正規化一致・部分一致・値近似 tiebreaker は**使わない**

`build_monthly_extractor.py` が Gemini で adapter を生成するときは `bc_key = name = structure.monthly_items[*].name` を自動付与する。後から `key` を別名にリネームしても `bc_key` が残っていれば BC 突合は継続できる。

> **注意**: `--rebuild` 実行前に GCS アダプターのスナップショットを取ること（教訓: 2026-04-04）

### BC未収集フィールドの扱い（一般ルール、2026-04-18確定）

BC側に該当フィールドが存在しない/値が信頼できない場合、**管理価値で判断**して以下3パターンのいずれかで対応:

| パターン | 判断 | structure.json | extract_adapter.json | monthly_records.json |
|---------|------|--------------|---------------------|---------------------|
| **A: 収集する（BC外ルート追加）** | 我々の値を正として管理する | `_manual_add: true`, `collection_excluded: false` | `bc_ignore: true` + `_bc_ignore_reason` | 通常保存 |
| **B: 収集する（BC値不正、我々の値が正）** | BCの値が信頼できないが管理価値あり | `_manual_add: true`, `_bc_value_invalid: true` | `bc_ignore: true` + `_bc_ignore_reason` | 通常保存 |
| **C: 収集しない（管理不要）** | 価値なし、ノイズ | `collection_excluded: true` + `_excluded_reason` | 該当 field を fields[] から削除 | 該当 key を records から削除 |

**運用注意:**
- A/B/C いずれも `manual_override: true` を トップレベルに付与（自動再生成からの保護）
- BC外ルートで追加したことを必ず明示（structure.json `_manual_add: true`）して上書き混乱を防ぐ
- 判定の基準: 「この KPI を**継続的に追跡する価値があるか**」 → Yes なら A/B、No なら C

**実例:**
- 7685 BuySell 「グループ合計（店舗数）」 → A（バイセル+タイムレス合算、BC未収集）
- 7610 テイツー 「グループ 売上高（前年同月比）」 → B（BC=0.0で信頼性ゼロ）
- 7059 コプロHD 「グループ合計 在籍技術者数（人）」 → C（事業別合算で管理価値なし）

### adapter.json の手動作成（ローカル Chrome 検索経由）

`update_monthly_adapters.py` で adapter.json が生成されなかった銘柄について、
ローカル Chrome で Google 検索して月次 IR ページ URL を特定し、手動で adapter.json を作成する。
Google 検索の具体的手法は `docs/knowledges/tools/048_google_search_local_chrome.md` 参照。

```python
# GCS にアップロード
adapter = {
    "ticker": ticker,
    "company_name": name,
    "type": "scrape_links",    # or "eir_api", "html_table"
    "url": found_url,
    "manual_add": True,        # 自動生成プログラムによる上書きを防止
    "created_by": "chrome_local_search",
}
bucket.blob(f"monthly/meta/{ticker}/adapter.json").upload_from_string(
    json.dumps(adapter, ensure_ascii=False, indent=2),
    content_type="application/json",
)
```

### 情報サイトの除外（検索時）

`update_monthly_adapters.py` の SKIP_DOMAINS で定義。金融情報サイト・ニュースサイト・証券チャートサイト等を除外し、
企業の公式IRページのみを選別する。

## スクリプト一覧

### 現役

| スクリプト | 役割 |
|-----------|------|
| `buffett_monthly_scrape.py` | BQ TDNET から月次開示企業一覧・structure.json を GCS に保存 |
| `update_monthly_adapters.py` | curl_cffi Google検索（chrome124偽装）+ Playwright ページ解析で月次IRページURL探索 → adapter.json 更新（URL探索 + ページ解析一体化版） |
| `build_adapter_index.py` | GCS の全アダプター情報を `data/monthly_adapter_index.csv` に集約 |
| `download_monthly.py` | adapter.json の type 別に月次ファイル（PDF/XLSX/CSV）をダウンロード |
| `build_monthly_extractor.py` | Step 6a: Gemini で extract_adapter.json を生成（正規表現パターン・メトリクス定義）|
| `extract_monthly_data.py` | Step 6b: extract_adapter.json を使って月次数値を抽出 → monthly_records.json 生成 |
| `download_bc_kpi.py` | **BC 月次 KPI データを `data/csv/bc_monthly_kpi.csv` に取得（Windows Chrome + Selenium）**。`--tickers <T...>` / `--resume` 対応。ローカル Chrome (`C:\Program Files\Google\Chrome\Application\chrome.exe`) を起動して WAF 通過 → `/company/{code}/kpi` から月次時系列を抽出 |
| `download_bc_kpi_v2.py` | 同上の Linux VM 版（Google Chrome + Xvfb + nodriver）。1GB RAM 向け、`DISPLAY=:99` 必須。`claude-high-vm` 等のリモート実行用 |
| `buffett_monthly_local.py` | BC の月次 KPI **構造** (structure.json) のみを取得する Windows 版（全銘柄の項目定義スキーマ用、monthly データは download_bc_kpi.py 側で取る） |
| `apply_bc_key_reverse_mapping.py` | reconcile CSV の承認済み bc_key マッピングを adapter.json に反映 |
| `reconcile_bc_key_from_compare.py` | compare_monthly_buffett.py の NG 出力から bc_key 逆引き候補を生成（semantic guard 付き、V4 以降）|
| `find_miscategorized_monthly_bg.py` | BQ TDNET_DOCUMENTS_ENHANCED の MAIN_CATEGORY 誤分類候補を抽出（V4: KPI/売上速報/Monthly Report/ポートフォリオ運営実績/前年比速報 等）|
| `apply_main_category_correction_bg.py` | 上記結果を BQ UPDATE で一括補正（MAIN_CATEGORY='月次開示' 変更）|

### 調査・検証系（随時）

| スクリプト | 役割 |
|-----------|------|
| `verify_monthly_irbank.py` | irbank.net との月次開示カバレッジ照合 |
| `check_monthly_coverage_gaps.py` | カバレッジギャップ確認 |
| `compare_monthly_buffett.py` | バフェットコード月次KPIとの突合。NGリストCSV出力 |
| `fix_monthly_category_bq.py` | BQ の MAIN_CATEGORY 誤分類を一括修正（単発実行済み） |

### BC突合NG調査（2026-04-05〜06実施）

**調査結果ファイル**: `docs/plans/20260405_ng84_investigation.md`
**アダプター一括DL**: `data/monthly_adapters/{ticker}.json`（GCSから420件DL、git管理下）

BC突合でNGとなった84社を1社ずつPDFを読んで逆引き調査した結果。調査方法・パターン分類・修正優先度を含む。
将来同様の突合調査を行う際の手順テンプレートとしても使用可能。

**主要パターン（頻出順）**:
1. 前期テーブル誤取得（9社）— `use_last_number:true` で参考テーブルが優先
2. フィールド区別失敗・絶対額vs%（8社）— 百万円と前年比%が同じregexにマッチ
3. フィールド区別失敗・行マッチ（8社）— 部門別行やグループ合計行にマッチ
4. 2分割テーブル（7社）— 上期/下期の物理分割で月列検出失敗
5. 速報/確報差（6社）— 速報PDF値と最新確報値の差異

### 旧世代・完了済み（再実行不要）

| スクリプト | 役割 |
|-----------|------|
| `find_monthly_page_urls.py` | ローカル Chrome + Playwright でURL探索（手動調査用に残存） |
| `build_monthly_adapters.py` | `ir_url.json` + `download_adapter.json` → `adapter.json` に移行（完了済み） |
| `upload_adapters_batch.py` / `upload_adapters_batch2.py` | アダプターの GCS バッチアップロード（単発実行済み） |
| `update_eir_adapters.py` | 46社の eir_api 型アダプターを一括更新（単発実行済み） |

### update_monthly_adapters.py 実行コマンド

```bash
# ローカルテスト（dry-run）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
  scripts/update_monthly_adapters.py --tickers 9887 4839 --dry-run

# 特定銘柄のみ（adapter.json 更新あり）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
  scripts/update_monthly_adapters.py --tickers 9887 2702

# 既存 active 銘柄も強制再スクレイプ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe \
  scripts/update_monthly_adapters.py --force 1417 1420
```

**仕組み:**
- curl_cffi (chrome124偽装) で Google 検索 → reCAPTCHA 回避（2026-04-11 時点 43 社連続 0 件）
- SKIP_DOMAINS 除外で金融情報サイト・ニュースサイト・証券チャートサイト等を排除
- Playwright でページ解析 → eIR SPA 検出 / DL リンクパターン推定 / html_table 判定
- ローカル Chrome & Cloud Run 両対応（認証：GEMINI_API_KEY or Vertex AI）

**レートリミット対策:** `docs/knowledges/tools/049_google_search_scraping_rate_limit.md` 参照。

---

### build_monthly_extractor.py 実行コマンド

```bash
# ローカルテスト（GCS保存なし）
PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --phase all --tickers 3097 --no-gcs

# BQ TDNET あり銘柄から30社サンプル
PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --phase all --sample 30 --no-gcs

# 本番実行（GCS保存あり）
PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --phase all --sample 100
```

**Gemini JSON 生成改善（2026-03-17 実施・A+C+E）:**

| 対策 | 内容 | 効果 |
|-----|------|------|
| A: max_output_tokens 拡張 | 8192 → 32768 | 長い JSON が切れなくなる |
| C: response_schema | 出力構造を宣言（constrained decoding） | コンパクト・整合性保証 |
| E: 分割リトライ | パース失敗時に metrics を半減して2バッチで再試行 | 最終フォールバック |

**C の落とし穴 — `enum` なしの STRING は値制約にならない:**
```python
# NG: Gemini が source に会社名を入れてしまう
"source": {"type": "STRING"}

# OK: enum で値を強制
"source": {"type": "STRING", "enum": ["tdnet"]}
```
さらに生成後に安全弁として `adapter["source"] = "tdnet"` で強制上書きすること（enum だけでは不十分な場合がある）。

**30社テスト結果比較:**

| | 旧（8192 tokens） | 新（A+C+E + enum修正） |
|--|-----------------|----------------------|
| Phase 1 成功 | 22/30 | **28/30** |
| Phase 2 成功 | 18/30 | **24/30** |
| 残失敗1社 | JSON切り詰め | TDNET文書なし・ファイルなし（データ欠如） |

**既知の制約（TODO）**
- TDNET フィルタで `MAIN_CATEGORY='月次開示'` のみ使用中。`SUB_CATEGORIES` も活用すべき
- 全店/既存店で同一 `row_label_regex` → 区別できないケースあり

---

## extract_monthly_data.py — アダプター拡張フィールド（2026-03-24 追加）

### `group` パラメータ（`row_label_regex` パス対応済み）

1行に複数の数値が並ぶ表形式で、特定の列（グループ番号）の値を取得したい場合に使用。

```json
{
  "key": "all_store_sales_yoy",
  "row_label_regex": "全店計\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)",
  "value_type": "percentage",
  "group": 5
}
```

- `group` 省略時は `1`（最初のキャプチャグループ）
- 指定グループ数がマッチのグループ数を超える場合は `group=1` にフォールバック

### `use_last_number` パラメータ（`row_label_regex` パス対応済み）

同一パターンが文書中に複数回出現し（累積テーブル等）、最後の出現が最新月の値である場合に `true` を指定。

```json
{
  "key": "monthly_sales",
  "row_label_regex": "月次売上\\s+前年同月比\\s+([\\d,]+)\\s+[\\d.]+%",
  "value_type": "integer",
  "use_last_number": true
}
```

- `has_capture_group` ありの場合: キャプチャ内の最後の数値を返す
- `has_capture_group` なしの場合: マッチ後テキストから取得する数値リストの最後の値を返す

### DOTALL フォールバック条件（`[\s\S]` 対応済み）

`row_label_regex` で複数行にまたがるパターンを書く場合、`[\s\S]*?` を使うと自動的に全テキスト DOTALL 検索にフォールバックする（`.*?` に加えて `[\s\S]` も条件に追加済み）。

```json
{
  "row_label_regex": "(?:1[.．]\\s*)?グループ\\s*合計[\\s\\S]*?在籍技術者数\\s*([\\d,]+)"
}
```

### `_FIELD_SCHEMA`（`build_monthly_extractor.py`）に `group` / `use_last_number` 追加済み

Gemini がアダプターを自動生成する際にもこれらのフィールドを出力できるようスキーマを更新。プロンプトにも使用条件（ルール6・7）を追記済み（2026-03-24）。

---

<!-- 以下削除済み（2026-04-10整理）:
  月次開示収集マスタ（csv）、GCSパス/スキーマ、取得方法の定義、
  Type C URL調査パイプライン、adapter.jsonインデックス構築、
  support.google.com URL汚染バグ、adapter.json修正・追加事項、
  Step 5実行結果&品質問題、DDG版評価結果、Step 6 Phase 1実行結果
  → 旧方式・修正済み・過去実行ログのため削除 -->


## フィールド命名設計方針（2026-03-29 確定）

### 本来あるべき設計

```
structure.json（BC スクレイピング結果）
    monthly_items[].name = "単体 売上（百万円）"  ← BC の正式表記
         ↓ build_monthly_extractor.py がこれを key として使う
extract_adapter.json
    fields[].key = "単体 売上（百万円）"           ← 日本語・BC 準拠
         ↓ extract_monthly_data.py がそのまま出力
monthly_records.json
    fields = {"単体 売上（百万円）": 1679, ...}    ← 日本語キー
         ↓ compare_monthly_buffett.py が直接マッチ
compare_monthly_buffett.py
    BC の "単体 売上（百万円）" と完全一致          ← ヒューリスティック不要
```

### 経緯・反省

- 当初 `build_monthly_extractor.py` のプロンプトが `key は英語スネークケース` と指定していたため、全アダプターのフィールド名が英語になった（例: `monthly_sales_million_yen`、`total_engineer_utilization_pct`）
- `structure.json` の `monthly_items[].name` には BC の正式日本語表記が入っているのに、`key` フィールドは空文字のまま放置されていた
- `compare_monthly_buffett.py` は英語 key と BC 日本語フィールド名を `_keyword_score`（ヒューリスティック）でマッチングしていたが、精度が低く（一致率 31.3%）、誤マッチが多発した

### ルール

1. **`extract_adapter.json` の `fields[].key` は `structure.json` の `monthly_items[].name`（BC 日本語表記）をそのまま使う**
2. `build_monthly_extractor.py` のプロンプトから「英語スネークケース」の指定を削除し、BC 準拠の日本語フィールド名を生成するよう変更する
3. BC に掲載されていない独自指標（BC_NODATA になるもの）も日本語で命名する（例: `"自販機 販売本数（前期比%）"`）
4. `compare_monthly_buffett.py` は `_keyword_score` を廃止し、日本語文字列の完全一致・正規化一致で突合する

### BC 未掲載指標の扱い

`structure.json` に存在しない指標（自販機販売本数等）は BC_NODATA のまま。これは問題ではなく仕様。突合対象外として扱う。

### 影響ファイル（再作成対象）

| ファイル | 対応 |
|---------|------|
| `build_monthly_extractor.py` | プロンプト修正（英語→日本語キー） |
| `compare_monthly_buffett.py` | `_keyword_score` 廃止・完全一致マッチに刷新・YoY +100 対応 |
| GCS `extract_adapter.json` × 180社 | 全社再ビルド |
| GCS `monthly_records.json` × 180社 | 全社再生成 |

> 正しい対策: `link_text_pattern` か `link_href_pattern` を必ず設定し、EIR_MONTHLY_RE に頼りすぎない。

---

## 特殊銘柄・例外扱い

通常の月次開示パイプラインに乗らず、銘柄固有の収集フローを持つもの。整合性チェック（extract_adapter.json / structure.json の有無）の対象外。

### 汎用ルール

`adapter.json` の `type` に特殊フロー値（過去: `disco_quarterly` 等）が設定されている銘柄は通常パイプラインに乗らない例外扱い。

新規の特殊タイプを追加する場合は本セクションに追記する。

### 論理削除（管理対象外）

事業全体に対して開示内容が小さすぎて株価要因として追う意味がない銘柄は、物理削除せず **論理削除** で管理対象外化する。

**手順**:

1. `data/monthly_adapters/<t>.json` 先頭に追記:
   ```json
   "_excluded": true,
   "_excluded_at": "YYYY-MM-DDTHH:MM:SS+09:00",
   "_excluded_reason": "（開示内容が事業全体に対し軽微である等の理由）"
   ```
2. GCS 同期: `gs://stock_data_1930932/monthly/meta/<t>/extract_adapter.json`
3. `data/monthly_adapter_index.csv` に当該銘柄がある場合（non-tdnet のみ）: `skip=True, category=excluded, adapter_note` を更新
4. `docs/plans/20260418_monthly_bc_round2_followup.md` の「🗑️ 月次開示 管理対象外銘柄」表に追記（唯一の真実）

**パイプライン側の挙動**:
- `scripts/extract_monthly_data.py`: adapter 読込直後に `_excluded` チェック → `results['excluded']` に計上して early continue
- `scripts/compare_monthly_buffett.py`: 突合ループ冒頭に同様のチェック → `summary['excluded']` に計上して early continue
- GCS 上の既存 records / docs は**削除しない**（過去データ保全）。「論理削除」という名の通り、必要になれば `_excluded` フラグ除去で復帰可能

**物理削除との違い**: 6146 ディスコのような「制度上そもそもパイプラインに載せない（`disco_quarterly` 型）」ケースは物理削除（adapter_index 行削除 + adapter ファイル削除）したが、6752 / 2337 のような「形式上載せられるが追う意味がない」ケースは論理削除で保全性を優先する。

---

## 月次開示分類の設計方針

BQ クエリ（`build_monthly_extractor.py`）側で DOC_TITLE 正規表現フォールバックを追加するのは**誤り**。

**理由:** 分類は `tdnet_load_parallel.py` のロード時点で行うべき責務。BQ に格納された `MAIN_CATEGORY` / `SUB_CATEGORIES` を信頼するのが正しい設計。ワークアラウンドをクエリ側に入れると、分類精度の問題がどこにあるか分からなくなる。

**対処方針:**
- 月次文書がうまく拾えない銘柄が出たら、`tdnet_load_parallel.py` の `_MONTHLY_DOC_PATTERN` または Gemini フォールバックの精度を改善する
- `_MONTHLY_DOC_PATTERN` への追加は広すぎるキーワード（例: `月度` 単独）は不採用。Gemini フォールバックを活用する

---

## BC突合 NG 40社 個別調査結果（2026-04-10）

**一致率**: 90.3%（OK=1145, NG=123, BC_NODATA=576）
**最新コミット**: `b4b843a`
**突合CSV**: `buffett_compare_20260410_094735.csv`

### 調査前提

- `extract_monthly_data.py` は BQ `MAIN_CATEGORY='月次開示'` でフィルタ → 月次以外は取らない
- GCS PDFフィルタ（`_MONTHLY_KW_re`）も正しく動作
- overwrite_past_months: 33社に設定済み。未設定NG 7社は速報/確報差ではない

### 個別調査結果

| ticker | 判定 | NG原因 |
|--------|------|--------|
| 212A | 合格 | overwrite到達範囲外（2025-11） |
| 2670 | 合格 | GCSに2026年1月度PDF欠損。バックフィル後解消見込み |
| 2674 | 修正済 | Gemini逆引きでプロンプト再設計。全店売上金額と既存店前年比の混同を解消 |
| 2750 | 修正済 | Gemini逆引きでプロンプト再設計。gemini抽出に変更 |
| 2997 | 合格 | bc_floor未設定（diff=0.9） |
| 2998 | 不合格 | Gemini抽出ミス（GMVと投資家数混同） |
| 3070 | 対象外 | 月次開示終了（2022年2月に開示終了告知済み。excluded=true設定済） |
| 3077 | 合格 | 2025-08のみNG（overwrite到達範囲外）、直近月OK |
| 3195 | 合格 | overwrite到達範囲外（diff=4〜40）、直近月OK |
| 3333 | 合格 | 店舗数diff=1、直近月OK |
| 3690 | 修正済 | unit_scale=0.001除去（PDFもBCも千円単位） |
| 4015 | 対象外 | 解読不可能なPDF（excluded=true設定済） |
| 4177 | 仮合格 | 抽出ミス（2026-01のみ、our=10.0 vs bc=242.9）。2026-02はOK。次月で再検証要 |
| 4666 | 修正済 | Gemini逆引きでプロンプト再設計。件数/台数混同を解消 |
| 5892 | 合格 | overwrite到達範囲外（2025-11） |
| 6045 | 修正済 | extraction_method=None→gemini変更。3月データで再検証要 |
| 6071 | 合格 | overwrite到達範囲外（2025-12、累計値混同） |
| 6627 | 合格 | overwrite到達範囲外（Gemini列取り違え、直近月OK） |
| 7545 | 留保付き合格 | 解析可能だがデータ不足。次月で再検証 |
| 7562 | 要修正 | 年度取り違え（2024年データに2025年の値が入る）。Geminiなしで修正要 |
| 7606 | 修正済 | Gemini逆引きでプロンプト再設計。再抽出で検証要 |
| 7610 | 合格 | BCデータ欠損（bc=0） |
| 7615 | 合格 | overwrite到達範囲外 |
| 7625 | 合格 | overwrite到達範囲外 |
| 7643 | 合格 | overwrite到達範囲外 |
| 8705 | 合格 | custom_prompt触ると必ず悪化する特殊銘柄 |
| 8739 | 修正済 | unit_scale=100除去（PDFから既に百万円単位） |
| 8798 | 合格 | overwrite到達範囲外（直近月OK） |
| 9007 | 修正済 | Gemini逆引きでプロンプト再設計。yoy_offset除去 |
| 9022 | 合格 | overwrite到達範囲外 |
| 9145 | 合格 | overwrite到達範囲外（2025-09、diff=1.0） |
| 9223 | 合格 | 古い月の抽出ミス（2025-01,02）。直近月（2025-03）はOK |
| 9245 | 不合格 | diff小だが速報/確報差と断定できる根拠なし。次月以降継続調査 |
| 9412 | 仮合格 | 最新月しか取れない。次月で検証要 |
| 9519 | 合格 | overwrite到達範囲外 |
| 9616 | 仮合格 | 2月しかない。次月で検証要 |
| 9936 | 合格 | BCデータ誤り（our=97.2はPDFと一致、bc=104.6がBC側間違い） |
| 9962 | 留保付き合格 | 最新月しかない。データ不足、次月で再検証 |
| 9973 | 合格 | overwrite到達範囲外（2026-01、直近月OK） |
| 9997 | 留保付き合格 | 最新月しかない。データ不足、次月で再検証 |

### 判定集計

| 判定 | 社数 |
|------|------|
| 合格 | 22 |
| 修正済（再検証要） | 8 |
| 仮合格・留保付き合格 | 6 |
| 不合格（継続調査） | 2 |
| 対象外（excluded） | 2 |

### Gemini逆引きプロンプト設計手法

PDF + BC正解値の両方をGeminiに渡して「BC値がPDFのどこにあるか特定し、抽出プロンプトを設計せよ」と指示する手法。
適用済み: 2674, 2750, 4666, 7606, 9007。個人Gemini APIキー（gemini-2.5-flash）でローカル実行。

### 一致率推移

16.8% → 46.9% → 59.0% → 63.7% → 68.1% → 67.3% → 64.3% → **90.3%**

---

## 落とし穴 — pdfplumberテーブル破損（2026-04-15）

特定の PDF（例: 6752 パナソニックHD 月次受注速報）で、pdfplumber が複数のサブカテゴリラベルを**1行目に連結した破損形式**でテーブルを返す。

**症状（実例）**:
```
Row 0: ['戸建集合分譲住宅計マンション合計', '戸建集合分譲住宅計', '戸建', '84%', '90%']
```

全サブカテゴリラベル（戸建 / 集合 / 分譲 / マンション / 合計）が Col 0 に詰め込まれる。`row_label_regex` でどのキーワードを書いても必ず 1 行目にマッチ → 戸建値が全カテゴリにコピーされる。住宅計 / 合計 行は col[0..2]=None のためラベル自体が存在せず regex では区別不能。

**判定方法**: `.extract_tables()[0][0]` の Col 0 が 10+ 文字かつ複数サブカテゴリキーワード（例: "戸建", "集合", "分譲" など）を同時に含む場合、このパターンに該当する。

**対策**: `extraction_method: "gemini"` 化。PyMuPDF のテキスト抽出もラベルと数値を別行に分離してしまい regex では対応不能。

**実例**: 6752 パナソニックHD 2026-04-15 修正（BC突合 25% → 100%）。

---

## 落とし穴 — 英語のみ公開に切替わった場合の対応（2026-04-15）

月次開示を途中から**英語のみ公開に切り替える**企業がある（例: 3561 力の源HD が 2026-03 から英語版のみ公開、日本語版停止）。IR国際化のトレンドに伴い今後増加する可能性大。

**症状**:
- `adapter.json` が日本語パターン（`link_text_pattern: "月次|業績動向"`）のみなら、英語PDFが DL 対象から外れて 0件エラー
- 抽出アダプタの `row_label_regex` が日本語前提（`全店.*売上` 等）なら英語PDFを抽出できない

**対応の型パターン（3561実証済）**:
1. `adapter.json` の `link_text_pattern` に英日両対応のキーワードを含める（例: `月次|Monthly|業績動向|Store Performance`）。`manual_override: true` セット
2. `extract_adapter.json` を **`extraction_method: "gemini"` + `gemini_multi_month: true`** に切り替え
3. `gemini_custom_prompt` に以下を必須記載:
   - 日英ヘッダー対応表（例: `Apr=4月, May=5月, ...`）
   - 会計年度の境界（例: 3月決算なら `Fiscal Year ending March 31, 2026 = 2025年4月〜2026年3月`）
   - 海外店舗テーブル除外指示（国内のみ抽出）
   - `Year total` 列除外
   - ラベル階層の明記（例: `Store count は All stores を選ぶ`）
4. `fields[].key` は structure.json の日本語 key に完全一致させる（BC突合のため）
5. `year_from_title_regex` は**削除**（英語ファイル名で `group(1)=None` を返し `_parse_year_month` が `int(None)` で落ちる。ファイル名先頭 `YYYYMM_` のヒューリスティックに委ねる）

**実例**: 3561 力の源HD 2026-04-15 修正（BC突合 100% 維持）。

---

## 落とし穴 — 「リンク未検出」ログの鵜呑み禁止（2026-04-12）　※汎用ルールは CLAUDE.md「注意事項」参照

`download_monthly.py` が「ダウンロード対象リンクなし」と報告しても、**リンクが存在しないとは限らない**。HTTPクライアントがTLS拒否やタイムアウトでページ取得自体に失敗している場合でも、同じログが出力される。

**原因**: `requests` ライブラリは Python 固有の TLS フィンガープリントを持つため、多くの企業 IR サイトで bot 判定される。`curl_cffi`（Chrome TLS 偽装）なら取得できるページが `requests` では取得できない。

**対策（スクリプト実行前の必須確認事項）**:
1. スクリプトの HTTP 取得方式を確認（`requests` / `curl_cffi` / `Playwright`）
2. `curl_cffi` がプライマリで使われているか確認。`requests` のみの場合は TLS 問題を疑う
3. 「0件」「対象なし」ログは**上流のHTTPステータス・例外メッセージ**を必ず確認してから報告
4. バッチ実行前に **1社分を curl_cffi で手動テスト**し、ページ取得可否を確認

**修正履歴**: 2026-04-12 に `download_monthly.py` の `handle_scrape_links` / `handle_html_table` / ファイルDL部分を `curl_cffi` プライマリ + `requests` フォールバック構成に改修。

---

## 落とし穴 — URLが正しくてもダウンロード失敗するパターン（2026-04-12）

ダウンロード対象の IR ページ URL が正しいのにファイル取得が 0 件になる「ありがちな罠」一覧。
**プログラム（`update_monthly_adapters.py` / `download_monthly.py`）が自動検出すべきパターンであり、ユーザーの手動介入なしで解決できるように改善すること。**

### 1. adapter.json の type 誤設定

| 実態 | 誤設定 | 症状 | 事例 |
|------|--------|------|------|
| ページ内 HTML テーブル | `eir_api` | eIR API 404 → 0件 | 8153 モスフード |
| ページ内 HTML テーブル | `scrape_links` | PDF リンク探索 → 0件 | 2664 カワチ薬品 |
| PDF 直リンク一覧 | `html_table` | HTML保存 → テーブル無し | 2587 サントリー食品, 9020 JR東日本 |

**対策**: `update_monthly_adapters.py` の `scrape_and_classify` でページ内容を分析し、PDFリンクの有無・HTMLテーブルの有無・eIR ウィジェットの有無から正しい type を判定する。

### 2. adapter.json の URL 微妙な違い

| パターン | 事例 |
|----------|------|
| パス末尾の違い（`/library/monthly.html` vs `/library_monthly/`） | 2587 サントリー食品 |
| サブドメイン変更（`www.tokyu.co.jp` → `ir.tokyu.co.jp`） | 9005 東急 |
| ページリニューアルで 404（`/ir/monthly/` → `/company/irlist/`） | 1417 ミライト・ワン |

**対策**: DL 0件時に curl_cffi で URL アクセスを検証し、404/403/リダイレクトなら WebSearch で最新 URL を再取得する自動リカバリ。

### 3. データが iframe 内・動的ロード

| パターン | 事例 |
|----------|------|
| 同一ドメイン iframe（`juchu_YYYYMM.html`） | 1420 サンヨーホームズ |
| 別ドメイン iframe + XJ-Storage ウィジェット | 3561 力の源HD |
| eIR v5 ウィジェット（JS 動的描画、iframe なし） | 8153 モスフード, 3034 クオールHD |

**対策**: Playwright で `page.frames` をチェックし、子フレームの DOM からもリンク・テーブルを収集する（2026-04-12 `scrape_and_classify` に追加済み）。

### 4. eIR API ページ番号が 0 始まりでない

page=0 が 404 でも page=24 にデータがある企業がある（例: 8153 モスフード）。

**対策**: 降順探索ロジック追加済み（2026-04-12 `download_monthly.py` `_fetch_eir_category` 改修）。

### 5. 月次キーワードフィルタの不統一

`download_monthly.py` の `EIR_MONTHLY_RE` と `extract_monthly_data.py` の `_MONTHLY_KW` でキーワードが異なり、DL は成功するが抽出で漏れる（またはその逆）。

**対策**: 両スクリプトのキーワードリストを統一する。新キーワード追加時は両方を同時に更新する。

---

## 設計判断・教訓（2026-04-18 追記、深夜セッション）

### source 厳格化命名規則（2026-04-18 確定）

extract_adapter.json の `source` フィールドを以下3種に統一:

| 値 | 意味 |
|----|----|
| `tdnet` | BQ TDNET_DOCUMENTS_ENHANCED 経由 |
| `non-tdnet(pdf)` | IR HP DL → GCS monthly/docs/{ticker}/ の PDF |
| `non-tdnet(html_table)` | IR HP DL → GCS monthly/docs/{ticker}/ の HTML |

**経緯:** 旧 `source: pdf`（曖昧）/ `html_table` だと「TDnet外」であることが直感的でなく、8739 SPARX 型の事故（決算短信ベース誤生成）を見逃す要因に。`non-tdnet()` プレフィックスで TDnet外であることを明示。

**移行履歴:**
- 2026-04-18: GCS 全493社中 243社を新表記にリネーム（pdf→non-tdnet(pdf): 230社、html_table→non-tdnet(html_table): 13社）
- `extract_monthly_data.py` / `build_monthly_extractor.py` 両方で新表記を採用、旧表記は後方互換でacceptする dispatch ロジック追加

### download_monthly.py `_extract_yyyymm` regex+Gemini hybrid（2026-04-18 改修）

**旧バグ:** regex `(20\d{2})([01]\d)` で年月範囲 validation なし → 「202904」「203510」「206018」等の未来年/不正月を YYYYMM として採用 → ファイル名が `202904_*.pdf` 等になる。下流の extract_monthly_data がこれを year=2029 として記録する事故（実例: 2686 ジーフット）。

**改修内容:**
- `_extract_yyyymm_regex()`: 年範囲（2015〜現在年+1）+ 月範囲（1-12）validation 追加。マッチしても範囲外なら `re.finditer` で次候補へ
- `_extract_yyyymm_gemini_cached()`: regex 失敗時の Gemini Flash Lite preview fallback。`functools.lru_cache(maxsize=2048)` でキャッシュ
- Gemini プロンプト: 会計期表記の +1月変換ルール明記、和暦変換、URL内DOC_ID除外を指示
- **submission_date は使わない**: 推測ミスを避けるため Gemini に渡さない、提出日ヒューリスティックも不採用

**Gemini モデル設定:**
- Vertex AI / 個人キー両方で **`gemini-3.1-flash-lite-preview`** を統一使用
- timeout 90s、失敗時 `"000000"` フォールバック

### Gemini auto-generated adapter の overfit 落とし穴

**問題:** `build_monthly_extractor.py` が単一サンプルPDF（多くは 2026-03-06 提出分）から description を生成すると、サンプル固有の表記が紛れ込む:
- 「最新月（2月）」「対象月（2月）」等の月固定指定
- 「具体的には、『2026年7月期 - - - 0 274 617 616』という行の、12月の『274』、1月の『617』、2月の『616』を指す」のようなサンプル値依存
- 「3月のデータは集計期間外のため含まれない」等の固有ルール

**結果:** 翌月度の新PDFを処理すると Gemini が「指定月が違う」と判定して record 生成失敗 or 古い月の値を取る（実例: 6040 が 3月度 PDF を処理しても 2月度の値しか出さなかった）。

**対策（2026-04-18 適用済 38社+5社+α）:**
- description から月指定（「最新月（X月）」「『1月』列」「2月度」固定）を全削除
- サンプル値（「274」「133,175」等）を全削除
- 構造情報（テーブル名・行ラベル・列ヘッダー名・単位）のみ残す
- prompt 側で `{month_val}月 の値を抽出` と動的に月を渡す → description で月指定する必要がなくなる

### 8739 SPARX 型事故の検出: sample_doc_title が決算短信ならレッドフラグ

**事象:** 8739 の `extract_adapter.json` が `sample_doc_title: "2026年３月期 第３四半期決算短信"` で `source: tdnet` で生成されていた。実際は IR HP の eir_api 経由で「月末運用資産残高のお知らせ」PDF が GCS に297件 DL 済（規模は数兆円のAUM）、対して adapter は決算短信から取った別値（159億円規模）を保存していた。BC値（2.3兆円）と完全乖離。

**チェックリスト（adapter 健全性確認）:**
1. `sample_doc_title` に「決算短信」「説明資料」「四半期報告書」等の語があれば疑う
2. `source: tdnet` でも、GCS `monthly/docs/{ticker}/` に PDF があれば `non-tdnet(pdf)` を検討
3. BC値とのオーダー（規模）が極端に違う場合（100倍超）は別経路の可能性大

**修正:** source を `non-tdnet(pdf)` に変更、sample_doc_title を AUM PDF に更新、fields/regex 再設計。

### 6040 型: 季節別フィールドの分離

**事象:** 6040 日本スキー場開発は **2つの季節別 KPI** がある:
- ウィンターシーズン: スキー場別来場者数（11月〜3月のみ値、6-10月休み）
- グリーンシーズン: 索道を利用した施設の来場者数（8月〜11月のみ値、12-3月休み）

BC は両方を別フィールド名で持つが、adapter は1フィールドのみ定義 + key 名「索道利用」と内部抽出が WINTER スキー場 計行（不一致）。BC側は冬季の索道利用は値なしのため BC_NODATA に。

**対応:** field を2本に分離。それぞれ structural な description で WINTER / GREEN セクション + 「計」行を明示。

### Gemini timeout バグ: ThreadPoolExecutor context manager の罠

**事象:** Gemini API が hang しても `_call_with_timeout()` のtimeoutが効かない（10分以上待機）。原因: `with ThreadPoolExecutor() as ex:` の context manager exit が `shutdown(wait=True)` を呼び、hung中のスレッド完了を待機する。timeout で fut.result() が TimeoutError 投げても、その後 `with` 抜ける際に shutdown が再びブロック。

**修正（extract_monthly_data.py の `_call_with_timeout()`）:**
- module-level `_TIMEOUT_EXECUTOR = ThreadPoolExecutor(max_workers=4)` を使用（context manager は使わない）
- timeout 発火時は `fut.cancel()` で queued ならキャンセル、稼働中ならスレッドはleak（プロセス終了で消える）
- これで本処理を先に進めながら、 hung スレッドは放置可能

### extract_from_text_gemini プロンプト改修（TDnet経由のみ、2026-04-18）

**事象:** TDnet経由のNG銘柄（7127/7918 等）が field 個別 description に「集計列禁止」を書いても Gemini が無視し、4Q列・累計列を取得してしまう。fieldレベルの注意書きは軽視される傾向。

**改修対象:** `scripts/extract_monthly_data.py:250-280` `extract_from_text_gemini()` の prompt 先頭のみ。残3箇所（NON-TDnet PDF単月L592 / PDF全期間L670 / HTML L776）は未改修で温存（効果検証後に展開判断）。

**追加した「絶対禁止事項」（プロンプト先頭・全フィールド共通・最優先）:**
1. 集計列禁止（1Q/2Q/3Q/4Q/上期/下期/累計/通期/年計/YTD）→ 必ず**月別の単月列**から取得
2. 対象月厳守（`{target_month_str}` の列のみ、隣月禁止、見つからなければ null）
3. 当年/前年の取り違え禁止（年度比較表で当年（最新期）行のみ）
4. 全店/既存店の取り違え禁止（全社/グループ合計/単体含む）
5. サブカテゴリ取り違え禁止（直営/FC、国内/海外、戸建/集合/分譲、電気/ガス、商品/サービス）
6. パーセント値=100前後の水準値（差分形式に変換しない）
7. 年度数字（2015〜2035）を値として返さない

末尾に【自己検証】ステップ追加（「対象月の単月列・description指定の行/カテゴリから取った値か再確認、不安なら null」）。

**設計判断:**
- field 個別 description より prompt 先頭の共通ルールの方が遵守率が高い（経験則）
- TDnet 1箇所先行 → 効果検証 → NON-TDnet 3箇所に展開、の段階導入
- field 例示（戸建/集合/分譲）はユーザー指摘で「もっと一般的なもの（前年/今年、既存店/全店）」を追加

### filename YYYYMM 不正値の発生メカニズム（2686 型、修正済）

**事象:** GCS `monthly/docs/2686/` に `202904_*.pdf` `203510_*.pdf` `206018_*.pdf` 等の不正YYYYMM filename が複数存在。下流 extract が year=2029 等を採用 → monthly_records に未来日 year_month が記録される。

**原因:** `download_monthly.py` 旧 `_extract_yyyymm()` の regex `(20\d{2})([01]\d)` が範囲 validation なしで、URL/タイトル中の偶然の数字列を YYYYMM として採用。`[01]\d` は `00-19` を許容するため month=13/18/20 等も通る。

**対策:** 「download_monthly.py `_extract_yyyymm` regex+Gemini hybrid」セクション参照。年月範囲 validation で根本対処。

---

## 残作業: 非TDnet 月次抽出（2026-04-12 完了 8社 / 残 1社）

### 前提

2026-04-12 セッションで以下を完了:
- 43社のダウンロードアダプタ再構築（`update_monthly_adapters.py` Yahoo検索 + Playwright + CDP 調査）
- `download_monthly.py` の curl_cffi プライマリ化、eIR 降順探索、マンスリーKW追加
- `extract_monthly_data.py` の GCS パスルーティング修正（monthly/docs/ 優先）、月次KW統一、look-ahead 行結合修正
- BC突合 100% 達成銘柄: 1873, 2587, 9201, 8282, 3561, 4641, 3678, 6580, 7345, 8214, 8278

### 残り 9社: 抽出エンジン改修が必要

#### A. 列テーブル構造（month_direction=row 対応必要）— 5社

| ticker | 社名 | PDF構造 | BC期待フィールド | 修正内容 |
|--------|------|---------|-----------------|---------|
| 9005 | 東急 | 行=月、列=定期計/定期外/合計。輸送人員+運賃収入の2セクション。日英バイリンガル | 輸送人員・運賃収入 各3指標 | `_extract_pdf_by_row` が列ヘッダ区別に非対応。セクション（輸送人員 vs 運賃収入）の区別も必要 |
| 9020 | JR東日本 | 行=月、列=定期/定期外/合計。年度ファイルが月々更新 | 鉄道営業収入 3指標 | 9005 と同パターン。`month_direction=row` adapter 設定済みだが `_extract_pdf_by_row` の列マッチロジック不足 |
| 3349 | コスモス薬品 | 行=指標（全店売上/既存店売上/店舗数）、列=月。日英バイリンガル | 全店売上/既存店売上/店舗数 | `_extract_pdf_by_column` が日英交互行に対応できない。テキスト正規化 or 行スキップロジック要 |
| 6752 | パナソニックHD | 行=指標（戸建/集合/分譲/マンション/合計）、列=前年同月比/累計比 | ホームズ受注金額 5指標 | 単月テーブル構造。ファイル名に YYYYMM なし（`000000_`）。PDF内テキストから年月抽出が必要 |
| 8165 | 千趣会 | 行=月、列=単月/累計。暦年ベース | 通信販売売上 前年比 | 2025年度版1件のみDL済。2026年度版の追加取得要。テーブル構造はシンプルだが暦年→year_month変換要 |

**共通の根本原因**: `_extract_pdf_by_row` / `_extract_pdf_by_column` が非TDnet PDF のテーブル構造（日英バイリンガル、セクション区切り、列ヘッダマッチ等）に対応していない。

**対策案**:
1. `_extract_pdf_by_row` に列ヘッダ regex マッチ機能を追加（adapter.fields[].column_header_regex 等）
2. `_extract_pdf_by_column` の日英バイリンガル行スキップ
3. または: これら5社は Gemini method（`--gemini-method` フラグ）で対応するのが現実的

#### B. HTML テーブル解析改修 — 3社

| ticker | 社名 | HTML構造 | 修正内容 |
|--------|------|---------|---------|
| 2664 | カワチ薬品 | 列=月（4月～3月）、行=全店/既存店。△記号=マイナス。値は増減率（3.1 = +3.1%） | `extract_from_html` に △ 記号処理追加。+100 変換は BC 突合側自動検出に委任 |
| 8698 | マネックス | 列=マネックス証券/TradeStation/コインチェック取引所/販売所、行=年月。単位: 億円 | `extract_from_html` で列ヘッダからフィールドマッチ。単位変換（億円→円 ×1億）は adapter に `unit_scale` 設定 |
| 1420 | サンヨーホームズ | メインHTML は iframe シェルのみ。データは `juchu_YYYYMM.html`（同一ドメイン iframe）内 | adapter の `ir_page_url` を iframe URL に変更 or `download_monthly.py` に iframe follow ロジック追加。テーブル構造自体はシンプル（住宅/マンション/合計 × 前年同月比/累計比） |

**共通の根本原因**: `extract_from_html` が汎用テーブル解析に対応しておらず、特定構造のみサポート。

**対策案**:
1. `extract_from_html` に △ 記号→負数変換、列ヘッダマッチ、unit_scale 対応を追加
2. 1420 は adapter `ir_page_url` を最新 iframe URL（`juchu_YYYYMM.html`）に更新して再DL

#### C. 特殊対応 — 2社（3034 は別途、8153 は B と重複）

| ticker | 社名 | 問題 | 修正内容 |
|--------|------|------|---------|
| 3034 | クオールHD | 画像PDF（フォント埋め込みなし）。pdfplumber/PyMuPDF 両方テキスト 0 | OCR（pytesseract）対応を `extract_monthly_data.py` に追加。adapter に `extraction_method: "ocr"` 設定済み。画像は鮮明（150dpi で十分読取可能） |
| 6301 | コマツ | 4地域（日本/北米/欧州/インドネシア）が横に並ぶ特殊レイアウト。[A]vs[B] 列が増減率% | regex 対応困難。Gemini method（`--gemini-method`）推奨。adapter に `extraction_method: "gemini"` 設定済み |
| 8153 | モスフード | DL した HTML が JS レンダリング前の空データ。ページ内にインライン月次テーブルあり（CDP で確認済み） | adapter type を `html_table` に修正済み。`download_monthly.py` で Playwright DL（ヘッドあり）or curl_cffi で取得→テーブルが空の場合 Playwright フォールバック |

### 実施済みプログラム改修（2026-04-12）

| ファイル | 改修内容 |
|---------|---------|
| `download_monthly.py` | curl_cffi プライマリ化（handle_scrape_links / handle_html_table / ファイルDL） |
| `download_monthly.py` | eIR 降順探索（`_fetch_eir_category` page=30→0） |
| `download_monthly.py` | `EIR_MONTHLY_RE` に「マンスリー」追加 |
| `download_monthly.py` | Playwright ヘッドあり（ローカル実行時 `headless=False`） |
| `download_monthly.py` | 0件時の原因詳細ログ出力 |
| `extract_monthly_data.py` | GCS パスルーティング: `monthly/docs/` 優先 + `tdnet/` フォールバック |
| `extract_monthly_data.py` | `_MONTHLY_KW` キーワード統一（download 側と同期） |
| `extract_monthly_data.py` | look-ahead 行結合: ラベル行に値がない場合、次1-3行を結合して再試行 |
| `extract_monthly_data.py` | HTML テーブル一時ファイル Windows ロック修正 |
| `update_monthly_adapters.py` | Google 検索 → Yahoo Japan 置換（curl_cffi。CAPTCHA 回避） |
| `update_monthly_adapters.py` | eIR 検出時でも HTML テーブルがあれば html_table 優先 |
| `update_monthly_adapters.py` | IR サブページ探索 / iframe 検出 / HTML テーブル月次検出 追加 |
| `update_monthly_adapters.py` | GCS パス `monthlydata/` → `monthly/meta/` 修正 |
| `update_monthly_adapters.py` | git 復活（コミット `12d4645` で誤削除されていた） |

### 2026-04-12 セッション2 で完了した改修

| 対象 | 改修内容 |
|------|---------|
| `_extract_pdf_by_row` | セクション検出（`page.crop()`）、`row_label_regex`列マッチ、▲/△→負数、テーブル跨ぎ（break削除）、データ行判定改善 |
| `_extract_pdf_by_column` | `row_label_regex`対応済み（既存） |
| `_extract_pdf_single_month` | `row_label_regex`対応、全列スキャン、前年比列自動検出 |
| `extract_from_html` | 月方向自動検出（column/row）、△→負数変換、年度推定 |
| `phase_extract` source=pdf | `extraction_method=gemini`（個人APIキー）, `extraction_method=ocr`（pytesseract）ルーティング追加 |
| `phase_extract` source=pdf | `000000_`プレフィックス年月解析（`page.extract_text()`で年、ファイル名で月）、`YYYYMM_`プレフィックス対応 |
| `phase_extract` source=pdf | `overwrite_past_months`全月ループ（year/year-1の年度跨ぎ対応） |
| 新関数 `_extract_pdf_ocr` | PyMuPDF→PIL→pytesseract（jpn+eng）画像PDF対応 |
| 新関数 `_extract_pdf_gemini_personal` | 個人APIキー+google-genai でPDF→JSON抽出 |

### 対応結果サマリー

| ticker | 社名 | 方式 | レコード数 | 状態 |
|--------|------|------|-----------|------|
| 9005 | 東急 | pdf_row（セクション区別） | 26件 | ✅ 完了 |
| 9020 | JR東日本 | pdf_row（overwrite全月） | 24件 | ✅ 完了 |
| 3349 | コスモス薬品 | pdf_column（overwrite全月） | 24件 | ✅ 完了 |
| 6752 | パナソニックHD | pdf_single_month | 27件 | ✅ 完了 |
| 8165 | 千趣会 | pdf_row（overwrite全月） | 24件 | ✅ 完了 |
| 2664 | カワチ薬品 | html_table（月=column、△対応） | 12件 | ✅ 完了 |
| 8698 | マネックス | html_table（月=row） | 72件 | ✅ 完了 |
| 6301 | コマツ | pdf_gemini（個人キー） | 1件 | ✅ 完了 |
| 3034 | クオールHD | pdf_ocr | 0件 | ⏸ pytesseract未インストール |
| 1420 | サンヨーホームズ | html_table | 0件 | ⏸ iframe空HTML（DL側の問題） |
| 8153 | モスフード | html_table | 0件 | ⏸ JS空HTML（DL側の問題） |

---

## 作業記録: 2026-04-12（2セッション）

### セッション1（日中）: DLアダプタ再構築 + 抽出エンジン基礎改修

**目的**: 非TDnet月次開示（IR HP直DL）43社のパイプライン構築

1. `update_monthly_adapters.py` 改修: Yahoo検索、Playwright CDP、eIR+HTMLテーブル自動判定
2. `download_monthly.py` 改修: curl_cffi プライマリ化、eIR 降順探索、Playwright ヘッドあり
3. `extract_monthly_data.py` 改修: GCS パスルーティング、月次KW統一、look-ahead 行結合
4. 13社のBC突合100%達成（1873, 2587, 9201, 8282, 3561, 4641, 3678, 6580, 7345, 8214, 8278, 7378, 7512）
5. 残り9社の未対応パターン特定 → A(列テーブル5社)/B(HTML3社)/C(特殊2社)に分類

### セッション2（夜）: 9社一括対応 → 8社完了

**目的**: 残り9社の抽出エンジン改修（`extract_monthly_data.py` 大幅改修）

| 改修箇所 | 改修内容 | 対象銘柄 |
|---------|---------|---------|
| `_extract_pdf_by_row` | `page.crop()` セクション検出（テーブル間テキスト）→ 複数テーブルのセクション区別 | 9005, 9020, 8165 |
| `_extract_pdf_by_row` | `row_label_regex` で列ヘッダーマッチ（key tokenization の代替） | 9005, 9020, 8165 |
| `_extract_pdf_by_row` | ▲/△→負数変換、隣接列フォールバック、テーブル跨ぎ（break削除） | 全PDF銘柄 |
| `_extract_pdf_by_row` | データ行判定改善（`△ N.N` パターン含む数値行を正しくスキップ） | 9005 |
| `_extract_pdf_by_row` | `is_ratio` 判定: セクションテキストに前年比マーカーがある場合、列ヘッダーチェック省略 | 8165 |
| `_extract_pdf_by_row` | テーブルが1つの場合、token チェック省略（セクション区別不要） | 8165 |
| `_extract_pdf_single_month` | `row_label_regex` 対応、全列スキャン、前年比列自動検出 | 6752 |
| `extract_from_html` | 月方向自動検出: A) 月=column（ヘッダーに「N月」×3+）、B) 月=row（col0に「YYYY年N月」×3+） | 2664, 8698 |
| `extract_from_html` | △→負数変換、年度推定（HTMLテキスト/ファイル名）、複数テーブル対応 | 2664 |
| `phase_extract` source=pdf | `extraction_method=gemini`: 個人APIキー（`google-genai` + `gemini-3-flash-preview`） | 6301 |
| `phase_extract` source=pdf | `extraction_method=ocr`: PyMuPDF→PIL→pytesseract（jpn+eng） | 3034 |
| `phase_extract` source=pdf | `000000_` プレフィックス: `page.extract_text()` で年、ファイル名で月を合成 | 9005, 9020, 3349, 6752, 8165 |
| `phase_extract` source=pdf | `YYYYMM_` プレフィックス: day=20で提出日ヒューリスティック正常動作 | 9005 |
| `phase_extract` source=pdf | `overwrite_past_months` 全月ループ（year/year-1 の年度跨ぎ対応） | 9020, 3349, 8165 |
| `phase_extract` source=pdf | `doc_title_pattern` フィルター: 20件超のみ適用（少数PDFは全件処理） | 9005 |
| 新関数 `_extract_pdf_ocr` | 画像PDF用OCR: pdfplumber テキスト確認→PyMuPDF pixmap→PIL→pytesseract | 3034 |
| 新関数 `_extract_pdf_gemini_personal` | 個人APIキー Gemini でPDF→JSON抽出 | 6301 |

**GCSアダプター更新**:
- 8165: `month_direction=row`, `overwrite_past_months=true` 追加
- 6752: `row_label_regex` を実際のPDFセルテキストに合わせて修正（`戸.*建.*住.*宅` → `戸\s*建` 等）
- 9005: `doc_title_pattern` を `鉄道.*月次|Railways.*Monthly` に修正
- 9020: `overwrite_past_months=true` 追加
- 3349: `overwrite_past_months=true` 追加

**デバッグで得た知見**:
- pdfplumber の `page.bbox` がマイナスマージン（`-14.4`）を返す場合がある → `crop()` は `page.bbox` を使う
- `_extract_pdf_text()` はテーブル内容のみ返す（非テーブル領域のタイトル・日付が欠落）→ 年推定には `page.extract_text()` を使う
- FY2025→FY2026 でPDF列数が変わる（16→11: 2019年度比列の廃止）→ 列ヘッダーマッチが一般的に必要
- データ行に `△ 22.9`（スペース入り）があると `[\d,.\-+▲△]+` パターンでヘッダー行と誤認 → `[▲△\-+]?\s*[\d,]+\.?\d*\s*[%％]?` に改善

---

## 今後の予定

### 2026-04-13 セッション実績

#### 完了タスク

1. **✅ 3034 クオールHD: Geminiアダプター確定**
   - `extraction_method: "ocr"` → `"gemini"` に変更
   - GCSアダプター更新済み: `monthly/meta/3034/extract_adapter.json`

2. **✅ 1420 サンヨーホームズ / 8153 モスフード: DL修正+抽出+BC突合**
   - 1420: `download_monthly.py` に iframe auto-follow (`_follow_iframe()`) 追加 + extract_adapter Gemini化 + `_extract_html_gemini_personal()` 新規追加
   - 8153: `download_monthly.py` の `handle_html_table` に Playwright対応追加 + `extract_from_html` に Pattern B'（年なし月行）検出追加
   - Cloud Run動作確認: 両社とも1回目で成功
   - BC突合: 1420 3/3 (100%), 8153 12/12 (100%)

3. **✅ 8698 マネックス: アダプター修正**
   - 問題: Table 0のR1(366セル巨大行)でヘッダー誤検出→列ズレ + 単位変換未定義
   - 修正: `extract_monthly_data.py` に `table_index` サポート追加（3行） + アダプターに `table_index: 1`, 各フィールドに `unit_scale`（億円/百万円→円）
   - BC突合: 9/9 (100%)

4. **✅ 非TDnet9社 GCS保存+BC突合**

#### 9社BC突合結果（2026-04-13 `buffett_compare_20260413_101921.csv`）

| ticker | 社名 | OK | NG | BC未取得 | 一致率 | 備考 |
|--------|------|----|----|---------|--------|------|
| **9005** | 東急 | 18 | 0 | 0 | **100.0%** | |
| **8165** | 千趣会 | 3 | 0 | 0 | **100.0%** | |
| **6301** | コマツ | 4 | 0 | 0 | **100.0%** | Gemini method |
| **3034** | クオール | 3 | 0 | 0 | **100.0%** | Gemini method（今日変更） |
| **9020** | JR東日本 | 9 | 0 | 0 | **100.0%** | 2026-04-15 定期外を Sub Total 列に固定して修正 |
| **2664** | カワチ薬品 | 5 | 1 | 0 | **83.3%** | 1件 diff=1.0 軽微 |
| **6752** | パナソニック | 3 | 9 | 0 | **25.0%** | サブカテゴリ混同（戸建値が全カテゴリにコピー） |
| **8698** | マネックス | 0 | 6 | 0 | **0.0%** | → **修正後 9/9 100%** |
| **3349** | コスモス薬品 | 0 | 0 | 9 | **N/A** | BC未取得 + 年月パース誤り + 値重複 |

**全体（8698修正前）: 44 OK / 17 NG (72.1%)**
**8698修正後の実質: 53 OK / 8 NG (86.9%)**（3349のBC未取得9件除外）

### 今後のTODO

#### 直近（次セッション）

1. **6752 パナソニック adapter修正**（9件NG）
   - 原因: サブカテゴリ（戸建/集合住宅/分譲/マンション）の区別ができず、1行目（戸建）の値が全カテゴリにコピーされている
   - 対策: `row_label_regex` を各サブカテゴリ固有のパターンに修正、またはGemini method化

2. **3349 コスモス薬品 抽出修正**（BC未取得9件 + 年月パース + 値重複）
   - 年月パース: 2026-10/11/12（未来日付）→ 2025-10/11/12 に修正（5月決算の年度跨ぎ）
   - 値重複: 全月の売上前年同月比が同一値(105.9)、店舗数が0 → 抽出ロジックのバグ
   - BC未取得: BCデータ側の問題か、フィールド名不一致か要調査

3. **9020 / 2664 軽微NG確認**
   - 9020: ✅ **2026-04-15 修正完了（100%）**。原因は diff=0.7 の速報差ではなく「定期外列の取り違え」。PDF の「定期外」は3列（近距離/中長距離/Sub Total）あり、元 regex `定期外|合計.*Sub Total` では左端の近距離列(ci=2)で最初にマッチして値採用 → 近距離値が抽出されていた。BC は Sub Total 列が正解。regex を `定期外.*Sub\s*Total` に変更して Sub Total 列(ci=4)に固定
   - 2664: diff=1.0 → 許容範囲か判断

4. **1420 / 8153 / 3034 の過去月ダウンロード+抽出**
   - 現在は直近1-3ヶ月分のみ。過去月のDL → 抽出 → BC突合で網羅性向上

#### 中期（今週中）

5. **全社BC突合**
   - `compare_monthly_buffett.py --all` で全銘柄の一致率を再計測
   - 前回 90.3%（TDnet銘柄含む）→ 非TDnet修正分の上乗せ確認

6. **既存TDnet銘柄への波及確認**
   - 今回の改修（`table_index` / Pattern B' / iframe follow / Playwright html_table）が既存銘柄を壊していないか
   - `--all --since 2025` で全銘柄再抽出 → 前回結果と diff

7. **download_monthly.py Cloud Run再デプロイ**
   - iframe follow / Playwright html_table 対応を含むイメージをビルド・デプロイ

#### 長期（パイプライン本運用化）

8. **日次自動実行の設計**
   - Step 5（DL）→ Step 6b（抽出）→ BC突合 を Cloud Run Job で自動化
   - スケジュール: 毎月1-15日に月次DL → 抽出 → アラート通知

9. **月次開示→決算予測モデル構築**
   - 抽出済み月次データ（`monthly_records.json`）をBQにロード
   - 月次売上トレンド → 四半期決算予測 → 決算サプライズ予測
   - バックテスト: 月次開示後に買い、決算発表後に売りの P&L

---

## 壊れているジョブ・TODO

### ファイナルフェーズ: 本稼働前タスク（2026-04-15起票）

本稼働入り前に以下2ステップを実施する。

**Step 1: 3月BCデータ再取得（待機中）**
- 現状: `data/csv/bc_monthly_kpi.csv` の 507社中 263社が 2026-03 未満
  - うち 2026-02止まり 177社（多数はBC側が3月分未反映）
  - 2025-12〜2026-01止まり 16社
  - それ以前 69社（月次開示停止の可能性大）
- サンプル確認（2686/3034/3196/4374/9023）で3月未掲載の社が存在。**今週後半まで待機**してから再取得
- 再取得対象リスト: `data/bc_redownload_targets.csv`（管理外2社除外済、261社）
  - **ワークファイルの扱い**: このCSVは本作業専用の一時ファイル。Step 2 完了後に**必ず削除**すること（git管理対象外）
  - 生成コマンド: `data/csv/bc_monthly_kpi.csv` を ticker ごとに groupby して max(year_month) < '2026-03' を抽出。`data/monthly_adapters/*.json` の `inactive_reason` / `excluded` フラグで管理外銘柄を除外
- 再取得コマンド（Windows実行）: `download_bc_kpi*.py` を tickers 指定で実行（Linux VMではない。2026-04-15時点ではWindows実行が本運用）

**Step 2: 3月月次レポート全ダウンロード+BC突合**
- Step 1完了後、全銘柄の2026-03月次開示を一括DL→抽出→BC突合
- 突合結果レビュー → NG銘柄対応 → 本稼働入り
- **完了後**: `data/bc_redownload_targets.csv` を削除

### 本運用向け改修: データ月ベースの増分処理（月次本稼働前の必須改修、2026-04-17起票）

**背景**: 月次 cron 発動時に毎回 2020年以降の全文書を処理するのは無駄。既存の `--since YEAR` は年粒度のため、「前月のデータだけ」を絞り込めない。

**新フラグ: `--since-data-month YYYY-MM`**

- **セマンティクス**: **データ月（content月 = 月次報告の対象月）ベース**のフィルタ
  - 提出月（submission month）ではない。ユーザーは「どの月のデータを取りたいか」だけ考えれば良い
  - 例: 5月 cron で前月4月度のデータを取り込みたい → `--since-data-month 2026-04`
- **スクリプト内部で経路ごとに翻訳する**:
  - 非TDnet: データ月 = ファイル名先頭の `YYYYMM_` → そのまま比較
  - TDnet: データ月 +1ヶ月（content月の翌月が提出月になるため） → `SUBMISSION_DATE >= '{content+1}-01'`

**DL側（`download_monthly.py`）**

- **現状維持で OK**: 既に `_extract_yyyymm()` で content月ベースのファイル名 `{YYYYMM}_{ticker}_{title}_{hash}.{ext}` を付与済み
  - scrape_links / html_table / disco_quarterly / eir_api すべて対応済み
  - 取得元: リンクテキスト・タイトル・URL から正規表現で `YYYY年MM月` / `20YYMM` / `YYYY-MM` 等を抽出
  - 失敗時は `000000_` プレフィックス（フォールバック）
- **DL日付方式は不採用**: リラン・URL変更・adapter修正で名前が変わると「新規扱い」の誤検出が起きる（冪等性を失う）。`blob.time_created` も同じ理由で不採用
- **release_date（IR ページ上の発表日）方式も不採用**: 取得ロジック実装コスト大（各IRページのDOM解析、JSレンダリング対応）。冪等性の利得に見合わない

**Extract側（`extract_monthly_data.py`）**

- **新フラグ `--since-data-month YYYY-MM`**（TDnet / 非TDnet 両経路で共有）
- `--since YYYY`（現行）は後方互換として残す。`--since-data-month` 優先
- **TDnet経路**: BQ クエリで `SUBMISSION_DATE >= '{data_month + 1月}-01'` に変更
  - 例: `--since-data-month 2026-04` → 内部で `SUBMISSION_DATE >= '2026-05-01'`
- **非TDnet経路**: `_extract_ym_from_blob(blob)` ヘルパーで年月判定 → `ym < data_month` なら skip
  - 判定優先順: ① ファイル名 `YYYYMM_` ② `blob.metadata["year_month"]`（DL側で将来補完する場合） ③ PDF内容解析（`000000_` レガシーファイル用フォールバック）
- `overwrite_past_months: true` 銘柄は現行動作維持（最新PDFから過去月も書き換え）

**スケジューラ**: monthly-data-load の cron で前月を data_month として渡す

```bash
# 毎月15日 10:00 JST cron
PREV_MONTH=$(TZ=Asia/Tokyo date -d "last month" +%Y-%m)  # 例: 2026-04
gcloud run jobs execute extract-monthly-data \
  --args="--all,--since-data-month=$PREV_MONTH"
```

**呼び出し結果（5月cron `--since-data-month 2026-04` の場合）:**

| 経路 | 内部フィルタ | ヒット対象 |
|------|------------|----------|
| TDnet | `SUBMISSION_DATE >= '2026-05-01'` | 5月に提出された月次開示（=4月content） |
| 非TDnet | `filename_ym >= '2026-04'` | `202604_*.pdf` 以降（=4月content） |

**エッジケース:**

- **TDnet遅延提出**（content=2026-04 が 2026-06提出等）: 該当月の cron でカバーできない可能性。月次 cron は rolling window で過去2-3ヶ月を許容する運用が安全（例: `--since-data-month $(date -d "3 months ago" +%Y-%m)`）
- **非TDnet遅延公開**（4月度PDFが6月にIR公開）: 同様に rolling window で拾う
- **`000000_*.pdf`（content月抽出失敗）**: フィルタ通過可否は PDF内容解析次第。フィルタ済みPDFもフォールバックで `_parse_year_month` が走るので落ちない

**実装コスト**: 半日程度（DL側は改修不要）。回帰テスト: `scripts/extract_monthly_data.py --all --since-data-month 2026-04` で既存 BC突合100%銘柄が壊れていないことを確認

---

### BC突合スコープ規約: 「touched」概念を廃止（2026-04-18 確定）

「修正検証対象」と「BC突合対象」は別概念。BC突合のスコープ判断に「私が今回触った会社」のような曖昧な"touched"概念を持ち込まない。

- **本判定モード**: BC突合は **records が存在する全社** を対象とする（`compare_monthly_buffett.py` のデフォルト）。一致率の正式数値はこのモードの結果のみ
- **修正検証モード**: `--tickers` で個別指定するのは debug 用。報告時には必ず「修正対象 N社のみ、Y%」と注記し、本判定としない
- adapter 修正により副次的に extract 成功した会社（前回0件→今回有り）も自動的に本判定モードのスコープに入るため、検出漏れが起きない

### bc_ignore 設定 field の一致率扱い（2026-04-18 確定）

`extract_adapter.json` の field に `bc_ignore: true` を付けた場合、その field は **BC突合から除外**するだけでなく、**一致率計算の分母からも除外**する。

理由:
- bc_ignore は「BC側にデータがないため比較不能」を意味する
- これを分母に含めると、一致しようがないフィールドのせいで一致率が下がってしまい、運用上の指標として誤解を招く

実装:
- `compare_monthly_buffett.py` で adapter の `bc_ignore: true` field をループ内で skip
- CSV 出力からも除外（OK/NG/BC_NODATA いずれにもカウントしない）
- ログサマリの「対象フィールド数」「一致率」の分母から除外

7685 の例:
- 4 fields のうち 2 件が bc_ignore (グループ合計など)
- 旧: 「4件中2件 OK = 50%」と表示 → 誤解を招く
- 新: 「比較対象 2件中 2件 OK = 100%」と表示

### コード改善TODO: `_parse_year_month` null-safety（2026-04-15起票）

`scripts/extract_monthly_data.py` の `_parse_year_month` で `year_from_title_regex` に alternation（`|`）を含むパターン（例: 英語月名 `Apr|May|...`）を設定すると、**非マッチグループに対して `group("year")` が None を返し `int(None)` で TypeError** が発生する。

**現状の回避策**: adapter 側で `year_from_title_regex` キーを削除し、ファイル名先頭の `YYYYMM_` ヒューリスティックに委ねる（3561 で採用）。

**本来の対応**: `_parse_year_month` 内で `groupdict().get("year")` を None安全にし、None の場合は次のフォールバックに抜けるパッチを当てる。英語PDF対応がスケールしてきたら必須。

### GCSパス再配置の残作業（2026-04-11実施）

旧パス `monthlydata/`（2249件）, `monthlyir/`（11129件）, `buffett_compare/`（30件）は安全のため**残存**している。新パスでの動作確認後に手動削除する。

```bash
# 動作確認OKなら削除（要慎重）
gsutil -m rm -r gs://stock_data_1930932/monthlydata/
gsutil -m rm -r gs://stock_data_1930932/monthlyir/
gsutil -m rm -r gs://stock_data_1930932/buffett_compare/
```

未マッピング17件の内訳は未調査（移行スクリプト `scripts/migrate_gcs_monthly_paths.py` の `compute_new_path()` で対象外と判定されたファイル）。削除前に内容確認推奨。


---

## 子 MD

- [`042-1_bc_match_agent.md`](042-1_bc_match_agent.md) — BC突合 NG の自律修正エージェント (パターン A-I, ツール一覧, Runbook)
