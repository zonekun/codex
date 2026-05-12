> **関連プラン**: [月次KPI×PL相関分析](../../plans/analysis-012_monthly_disclosure_earnings_screening_20260509_135210.md) | [BC突合NG13社修復](../../plans/tools-042_monthly_bc_ng13_20260512_180300.md)
> **関連レポート**: [90%達成記録](../../plans/20260409_monthly_pipeline_90pct.md)

# 月次開示パイプライン 全体概要 & 収集マスタ

**カテゴリ**: tools | **更新日**: 2026-05-05 | **姉妹MD**: [`089_quarterly_disclosure_master.md`](089_quarterly_disclosure_master.md)
月次開示データの収集・抽出パイプラインの全体設計・GCSパス・スクリプト・運用手順を一元管理。

---

## 最終目標

月次開示データを活用し、**月次開示が決算に織り込まれているか否かを株価から判断し、織り込まれていないが好決算が見込まれる企業を決算前に買う**。

---

## ⚠️ ファイルマッピング絶対表 (2026-05-03 改定)

**ファイル名が同じでも意味が違うケースがある**。同期・コピー・上書き時は必ずこの表を参照。

### ローカル命名ルール

**ローカル = `{ticker}_` + GCSファイル名**。例外なし。

### 現行マッピング

| 種別 | ローカルパス | GCSパス | 判定キー |
|------|------------|---------|---------|
| **extract adapter** | `meta/monthly/{ticker}_extract_adapter.json` | `monthly/meta/{ticker}/extract_adapter.json` | `fields` (list) あり |
| **url adapter** | `meta/monthly/{ticker}_url_adapter.json` | `monthly/meta/{ticker}/url_adapter.json` | `ir_page_url` / `type` あり |
| **structure** | `meta/monthly/{ticker}_structure.json` | `monthly/meta/{ticker}/structure.json` | `metrics` (list) |

### 不変ルール

1. **ローカル↔GCSペアリング**: 上表の通り。`{ticker}_xxx.json` ↔ GCS `xxx.json`
2. **URL型とextract型は物理分離**。ファイル名から種別が一意に決まる。同期スクリプトは `_detect_adapter_kind()` で検証（詳細: `004_coding_conventions.md`）
3. **廃止パス**（`adapter.json` / `ir_url.json` / `download_adapter.json` / `monthlyir/`）には書き込まない・読み取らない
4. **正本はGCSとローカルを同時に反映**。片方だけ更新して「後で同期」は禁止
5. **テスト用アダプタは `C:\tmp\`** で管理。`meta/monthly/` に混在させない

---

## 本運用パイプライン定義（2026-04-10 確定）

| Step | 内容 | 実行頻度 | 本運用コマンド |
|------|------|---------|--------------|
| Step 1 | 企業一覧・構造取得 | 随時（手動指示時のみ） | `buffett_monthly_scrape.py` |
| Step 2+3+4 | URL探索・adapter更新 | 随時（手動指示時のみ） | `update_monthly_adapters.py --force` |
| Step 5 → 6b | 月次抽出 | 月次 | **`extract_monthly_data.py --all --since 2020`** |
| Step 6a | アダプタ自動生成 | **運用停止** | — |
| DL失敗フィードバック | Step 5→6bのDL失敗をStep 2+3+4に戻す | 月次 | `/monthly-error-autofix` |

> **注意**: `extract_monthly_data.py` のデフォルトは `--sample 30`（開発テスト用）。本運用時は必ず `--all` を指定すること。`--all` なしでは全銘柄の一部しか処理されない。

### 本運用実行前チェック

- [ ] `--all` フラグ指定の確認（デフォルトは30社サンプル）
- [ ] `--since` 西暦年の確認（年度ではなく西暦。初回: 2020、増分: 前回実行年）
- [ ] extract_adapter.json が GCS に存在する銘柄のみ対象
- [ ] TDnet source + non-TDnet source 両方が含まれることを確認

---

## GCSパス構成（2026-04-11 再配置実施済み）

`monthly/` 配下に機能別サブディレクトリで統合（meta/record/docs/log/bcdata）。

| パス | 内容 |
|------|------|
| `monthly/meta/{ticker}/structure.json` | BC 月次 KPI メトリクス定義 |
| `monthly/meta/{ticker}/url_adapter.json` | IRページURL + DLルール |
| `monthly/meta/{ticker}/extract_adapter.json` | テキスト→数値マッピング・正規表現 |
| `monthly/record/{ticker}/monthly_records.json` | 抽出済み数値データ |
| `monthly/docs/{ticker}/*.pdf\|xlsx\|html` | ダウンロード済みファイル実体 |
| `monthly/log/{timestamp}.json` | DL実行ログ。エラーリカバリ入力に使用 |
| `csv/bc_monthly_kpi.csv` | **BC月次KPIデータ正本**（ticker, year_month, field, value）。`download_bc_kpi.py` が生成・同期。仕様: [`091_download_bc_kpi.md`](091_download_bc_kpi.md) |

定数: `GCS_META="monthly/meta"` / `GCS_RECORD="monthly/record"` / `GCS_DOCS="monthly/docs"` / `GCS_LOG="monthly/log"`

---

## パイプライン全体像（各Step詳細）

> 各ステップの対象範囲に注意。TDNET開示企業と非開示企業では経路が異なる。

```
Step 1  企業一覧・構造取得 [両方]     → buffett_monthly_scrape.py → GCS structure.json
Step 2-4 IRページURL探索 [非TDnetのみ] → update_monthly_adapters.py → GCS url_adapter.json
Step 5  月次ファイルDL [非TDnetのみ]   → download_monthly.py → GCS monthly/docs/
Step 6a アダプタ生成 [両方]           → build_monthly_extractor.py(Gemini) → GCS extract_adapter.json
Step 6b 月次数値抽出 [両方]           → extract_monthly_data.py --all (regex/Gemini) → GCS monthly_records.json
```

**6b 詳細**: TDNET: `tdnet/{ticker}/*.pdf` → pdfplumber→PyMuPDF→extract_text() の順。年月判定: タイトル年号→提出日ヒューリスティック(day≤15→前月, day>16→当月)。非TDnet: `monthly/docs/{ticker}/` の PDF/XLSX/HTML。

## 手動修正の上書き防止フラグ

自動再生成時に手動修正が消えないよう保護するフラグ。

**company_list.json**: `manual_add: true`（BC外追加企業。再生成時マージ保持）

### structure.json

BC側メトリクス定義。**adapter の `bc_key` は `monthly_items[*].name` と完全一致**が必要。

| フラグ | 説明 |
|--------|------|
| `name` | **BC正名**。adapter.bc_key の唯一の参照元。旧 `metrics[]` も同義（compare側は両方対応） |
| `source` | メトリクスの作成基準。`"bc"`(default) = BC定義基準、`"original"` = 独自基準。省略時は `"bc"`。`"original"` のメトリクスはBC突合・adapter突合から除外される |
| `manual_override: true` | 全体上書き禁止 |
| `manual_add: true` | BC外追加メトリクス。再生成時マージ保持 |
| `collection_excluded: true` | 収集対象外（adapter生成・抽出・突合すべてスキップ） |
| `_bc_value_invalid: true` | BC側値が不正。我々の値を正とする |

### extract_adapter.json（正式スキーマ定義）

> **この定義が正本**。adapter 生成・修正時はここに定義されたキー名・型・値のみ使用すること。未定義キーの独自追加は禁止。

#### トップレベルキー

| キー | 型 | 必須 | 説明 |
|------|---|------|------|
| `ticker` | string | ✅ | 証券コード |
| `company_name` | string | ✅ | 企業名 |
| `source` | string | ✅ | データソース種別。値: `"tdnet"` / `"download"` / `"non-tdnet(pdf)"` / `"non-tdnet(html_table)"` |
| `extraction_method` | string | — | 抽出方式。値: `"regex"`(default) / `"gemini"` / `"excel_gemini"` / `"ocr"` |
| `format` | string | — | DLファイル形式（source=download時）。値: `"xlsx"`(default) / `"csv"` / `"pdf"` |
| `encoding` | string | — | CSV/HTMLのエンコーディング。default: `"utf-8-sig"`。CSVでshift_jisの場合は `"cp932"` |
| `sheet_name` | string\|int | — | Excel シート指定（excel_gemini用）。default: `0`（先頭シート） |
| `fields` | array | ✅ | 抽出フィールド定義（下表） |
| `custom_prompt` | string | — | Gemini系抽出の補足指示テキスト。全Gemini系method共通キー |
| `doc_title_pattern` | string | — | 文書タイトル inclusion filter（regex） |
| `fiscal_year_start_month` | int | — | 会計年度開始月。default: `1`（1月開始=暦年） |
| `month_direction` | string | — | `"row"` = 月が行方向に並ぶ。省略時は列方向 |
| `overwrite_past_months` | bool | — | 累積型PDF/XLSX: 最新ファイルから全月分を抽出し既存レコード上書き |
| `gemini_multi_month` | bool | — | Gemini抽出で1ファイルから複数月を抽出する。PDF/HTML両対応 |
| `monthly_kw_override` | string | — | PDF blob フィルタの正規表現を個別指定。省略時はデフォルト `_MONTHLY_KW` 使用 |
| `column_map` | object | — | 物理列index指定（regex抽出用） |
| `manual_override` | bool | — | `true` = 自動上書き禁止 |
| `_excluded` | bool | — | `true` = 論理削除。全工程でスキップ。**正式キー名はアンダースコア付き `_excluded`**。`excluded`（アンダースコアなし）は非正規 — 使用禁止、既存は `_excluded` に移行済み |
| `_excluded_reason` | string | — | 除外理由 |
| `_excluded_at` | string | — | 除外日時（ISO 8601） |

#### fields[] 要素

| キー | 型 | 必須 | 説明 |
|------|---|------|------|
| `key` | string | ✅ | 抽出結果の保存キー名。monthly_records.json に書き込まれるフィールド識別子。adapter固有の命名可（BC側と異なってよい） |
| `name` | string | — | 表示用ラベル。突合ロジックでは使用されない |
| `source` | string | — | フィールドの作成基準。`"bc"`(default) = BC定義基準、`"original"` = 独自基準。省略時は `"bc"` |
| `description` | string | — | フィールドの説明（Gemini抽出時にプロンプトに含まれる） |
| `value_type` | string | — | 値の型。`"number"`(default) / `"integer"` / `"float"` / `"percentage"` |
| `bc_key` | string | — | **BC側正名マッピング**（= `structure.json` の `metrics[*].name`）。省略時は `key` が暗黙的に使用される。`source: "original"` 時は不要 |
| `row_label_regex` / `group` / `use_last_number` | — | — | regex抽出パラメータ（§アダプター拡張フィールド参照） |
| `yoy_offset` | number | — | 比較値補正: `adj_val = our_val + yoy_offset`。例: 変化率+5.2→BC表記105.2なら `yoy_offset: 100` |
| `unit_scale` | number | — | 比較値補正: `adj_val = our_val * unit_scale`。例: 百万円→円なら `unit_scale: 1000000` |
| `bc_ignore` | bool | — | `true` = **BC突合スキップ**。bc_ignoreエントリでは `key` に structure.json のメトリクス名を入れる |
| `bc_ignore_reason` | string | — | bc_ignore の理由。BC値の逆引き結果を含めて記載する |
| `_calc` | string | — | 複数行合算等の特殊計算 |
| `_manual_description` | bool | — | description手動上書き済み。rebuild時保持 |

#### key / bc_key の突合フロー

```
compare_monthly_buffett.py 突合ロジック:
  target_bc_key = adapter.fields[i].bc_key ?? adapter.fields[i].key
  ↓
  structure.json の metrics[*].name とマッチ（precheck）
  bc_monthly_kpi.csv の field とマッチ（値比較）※将来廃止
```

**パターン別の設定例**:

| パターン | key | bc_key | 説明 |
|---------|-----|--------|------|
| key = BC名 | `全店 売上（前年同月比）` | (省略) | 暗黙マッチ。最も一般的 |
| key ≠ BC名 | `串カツ田中グループ 直営全店 売上（前年同月比）` | `直営全店 売上（前年同月比）` | adapter独自命名。bc_keyでstructure.jsonの名前を明示 |
| bc_ignore | `既存店 客数（前年同月比）` | `既存店 客数（前年同月比）` | 突合対象外。どのmetricsに対するignoreかを記録 |

#### unit_scale / yoy_offset 実例

**`yoy_offset`** — PDFの抽出値が「変化率」表記で、BCが「100+変化率」表記の場合に使う

| ticker | ソース文書の表記 | 抽出値 | yoy_offset | 補正後 | BC値 |
|--------|---------------|--------|-----------|--------|------|
| 2670 (ABC-MART) | 前年同期比 +7.1% | 7.1 | 100 | 107.1 | 107.1 |
| 3191 (ジョイフル本田) | 既存店 -2.1% | -2.1 | 100 | 97.9 | 97.9 |

**`unit_scale`** — ソースとBCで数値の桁（単位）が異なる場合に使う

| ticker | ソース単位 | 抽出値 | unit_scale | 補正後 | BC単位 | BC値 |
|--------|----------|--------|-----------|--------|--------|------|
| 3796 (いい生活) | 百万円 | 251 | 1000000 | 251,000,000 | 円 | 251,000,000 |
| 3983 (オロ) | 千ライセンス | 0.316 | 1000 | 316 | ライセンス | 316 |
| 2705 (大戸屋) | 前年比 114.1（千分率？） | 114100 | 0.001 | 114.1 | % | 114.1 |

計算式: `adj_val = our_val * unit_scale + yoy_offset`（unit_scale default=1, yoy_offset default=0）

#### bc_ignore 判定基準

bc_ignoreを設定する前に、以下の手順で逆引き検証を行うこと:

1. `bc_monthly_kpi.csv` から当該ticker・fieldのBC値を取得
2. BC値がある場合 → ソース文書（PDF/Excel/HTML）内でその数値を探す（単位変換 ×100, ×1000000 等を考慮）
3. ソース文書に値が見つかる → **抽出フィールドとして追加**（bc_ignoreにしない）
4. ソース文書に値がない → bc_ignore設定。理由にBC値と探索結果を記載
5. 同一データが別の`key`名で既に抽出済み → bc_ignore設定。理由に「BC上は〇〇として管理。adapterの既存フィールドで抽出・突合済み」と記載

#### extraction_method 別の必須・推奨キー

| method | 必須追加キー | 推奨キー |
|--------|------------|---------|
| `regex` | — | `column_map`, `month_direction`, `row_label_regex`(fields内) |
| `gemini` | — | `custom_prompt`, `gemini_multi_month` |
| `excel_gemini` | `format`(`"xlsx"` or `"csv"`) | `custom_prompt`, `sheet_name`, `encoding`, `gemini_multi_month` |
| `ocr` | — | — |

`key` と `bc_key` は異なっても OK（drift 許容）。**`--rebuild` 前に GCS スナップショットを取ること**。

### BC未収集フィールドの扱い

判定基準: 「この KPI を**継続的に追跡する価値があるか**」→ Yes なら A/B、No なら C。いずれも `manual_override: true` 付与。

- **A（BC外追加）**: structure に `_manual_add`, adapter に `bc_ignore: true`
- **B（BC値不正）**: structure に `_bc_value_invalid`, adapter に `bc_ignore: true`
- **C（管理不要）**: structure に `collection_excluded`, adapter/records から該当field削除

**adapter 手動作成**: `update_monthly_adapters.py` 未生成の銘柄は Google 検索で IR ページ URL 特定 → 手動で `url_adapter.json` 作成（`manual_add: true`）。手法: `docs/knowledges/tools/048_google_search_local_chrome.md`。検索時の情報サイト除外は `SKIP_DOMAINS` で定義済み。

## スクリプト一覧

### 現役

| スクリプト | 役割 |
|-----------|------|
| `buffett_monthly_scrape.py` | Step 1: 企業一覧・structure.json → GCS |
| `update_monthly_adapters.py` | Step 2-4: curl_cffi+Playwright で IR URL探索 → url_adapter.json |
| `build_adapter_index.py` | GCS 全アダプター → `monthly_adapter_index.csv` |
| `download_monthly.py` | Step 5: type別に月次ファイルDL |
| `build_monthly_extractor.py` | Step 6a: Gemini で extract_adapter.json 生成 |
| `extract_monthly_data.py` | Step 6b: 月次数値抽出 → monthly_records.json |
| `download_bc_kpi.py` / `_v2.py` | BC月次KPIデータ取得（Win/Linux）。仕様: [`091_download_bc_kpi.md`](091_download_bc_kpi.md) |
| `buffett_monthly_local.py` | BC structure.json のみ取得（Win版） |
| `upload_bc_historical.py` | BC過去データ → GCS records（source=bc_historical、既存上書き不可） |
| `apply_bc_key_reverse_mapping.py` | reconcile CSV → adapter bc_key 反映 |
| `reconcile_bc_key_from_compare.py` | NG出力から bc_key 逆引き候補生成 |
| `compare_monthly_buffett.py` | BC突合。NGリストCSV出力 |
| `verify_monthly_irbank.py` | irbank.net カバレッジ照合（新規月次開示銘柄の発見・カバー漏れ検出に使用。詳細: `040_monthly_irbank_verification.md`） |

### BC突合NG調査（頻出パターン）

調査結果・個別銘柄OK/NG: `docs/plans/20260405_ng84_investigation.md`。頻出: 前期テーブル誤取得(9社) / 絶対額vs%(8社) / 行マッチ(8社) / 2分割テーブル(7社) / 速報/確報差(6社)。

### 実行コマンド例

```bash
# Step 6b: 全社月次抽出（本運用）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_monthly_data.py --all --since 2020
# Step 6b: テスト実行（30社サンプル）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/extract_monthly_data.py --sample 30 --since 2024

PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/update_monthly_adapters.py --tickers 9887 --dry-run
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/update_monthly_adapters.py --force 1417  # 強制再スクレイプ
PYTHONUTF8=1 uv run python scripts/build_monthly_extractor.py --phase all --tickers 3097 --no-gcs  # テスト
```

レートリミット: `049_google_search_scraping_rate_limit.md`。build_monthly_extractor: `response_schema` の `source` は `"enum": ["tdnet"]` で強制（会社名混入防止）。

---

## extract_monthly_data.py — アダプター拡張フィールド（2026-03-24 追加）

### `group` パラメータ

1行に複数数値が並ぶ表で特定列の値を取得。省略時は `1`（最初のキャプチャグループ）。指定数がグループ数超なら `1` にフォールバック。

### `use_last_number` パラメータ

累積テーブル等で同一パターンが複数回出現する場合、最後の出現（＝最新月）を採用。capture group ありならキャプチャ内末尾、なしならマッチ後テキストの末尾数値。

### DOTALL フォールバック

`row_label_regex` に `[\s\S]*?` または `.*?` を含むと全テキスト DOTALL 検索にフォールバック。

---

## フィールド命名設計方針

**`fields[].key` = `structure.json.monthly_items[].name`（BC日本語表記）をそのまま使う**。BC未掲載の独自指標も日本語命名。突合は完全一致・正規化一致（旧 `_keyword_score` は廃止）。BC_NODATA は仕様。

---

## 特殊銘柄・例外扱い

`adapter.json.type` に特殊フロー値（例: `disco_quarterly`）が設定された銘柄。新規タイプ追加時は本セクションに追記。

**INDEXによる除外（絶対ルール）**: `monthly_adapter_index.csv` の `category=excluded` 銘柄は月次パイプラインの全工程（DL・adapter生成・extract・BC突合・検証）で**一切処理しない**。excluded 銘柄の record が GCS に存在する場合は孤立データとして物理削除する。新規スクリプト・検証ツール作成時もINDEXの active フィルタを必ず実装すること。

**論理削除**: `_excluded: true` + `_excluded_at` + `_excluded_reason` → 全パイプラインが early-skip。手順: ① adapter追記 → ② GCS同期 → ③ index更新 → ④ `20260418_monthly_bc_round2_followup.md` 「管理対象外銘柄」表に追記。GCS上の records/docs は保全（復帰可能）。

---

## 月次開示分類の設計方針

BQクエリ側で DOC_TITLE 正規表現フォールバック追加は**禁止**。分類責務は `tdnet_load_parallel.py` のロード時点にある。拾えない銘柄は `_MONTHLY_DOC_PATTERN` か Gemini フォールバックを改善する（広すぎるキーワード追加は不採用）。

---

## 落とし穴（詳細は 042-1 パターンDB参照）

以下の DL/抽出エラーパターンは [`042-1_monthly_error_fix_patterns.md`](042-1_monthly_error_fix_patterns.md) に修復手順付きで移行済み:

| パターン | 042-1 ID | 要点 |
|---------|----------|------|
| pdfplumber テーブル破損 | E3-3 | Col0にラベル連結 → `extraction_method: "gemini"` 化 |
| 英語のみ公開 | E3-4 | 5ステップ gemini_custom_prompt 設計（日英対応表/FY境界/海外除外/Year total除外/ラベル階層） |
| リンク未検出ログ鵜呑み | D2-3 | TLSフィンガープリント拒否。curl_cffi で再検証必須 |
| adapter type 誤設定 | D2-1 | scrape_and_classify で正しい type 判定 |
| iframe/JS動的ロード | D2-2 | Playwright page.frames で子フレーム DOM 収集 |
| URL微妙な違い(404) | D2-1 | DL 0件時に curl_cffi 検証 → WebSearch 再取得 |

---

## adapter 運用ルール

| ルール | 内容 |
|--------|------|
| **source 命名** | `tdnet` / `non-tdnet(pdf)` / `non-tdnet(html_table)` の3値。旧表記（`pdf`, `html_table`）は後方互換accept。`non-tdnet()` を見れば TDnet外と即判断できる |
| **YYYYMM 判定** | regex → validation(2015〜now+1, 月1-12) → Gemini fallback(`gemini-3.1-flash-lite-preview`, lru_cache)。submission_date は渡さない（推測ミス防止） |
| **description overfit 禁止** | adapter description に月固定指定（「最新月（2月）」等）・サンプル値を入れない。構造情報（テーブル名・行ラベル・列ヘッダー・単位）のみ。月は prompt 側で `{month_val}月` と動的に渡す |
| **健全性チェック** | ① `sample_doc_title` に「決算短信」があれば疑う ② `source: tdnet` でも GCS `monthly/docs/` に PDF あれば `non-tdnet` 検討 ③ BC値とオーダー100倍超なら経路誤り |
| **季節別フィールド** | 季節限定 KPI は field 2本分離。季節外の BC_NODATA は仕様 |

---

## コード実装ノート（extract_monthly_data.py）

### ThreadPoolExecutor — context manager 使用禁止

`_call_with_timeout()` で Gemini API hang 時に timeout が効かない事故が発生。原因: `with ThreadPoolExecutor() as ex:` の exit が `shutdown(wait=True)` を呼び、hung スレッド完了まで全体ブロック。

**現行設計**: module-level `_TIMEOUT_EXECUTOR = ThreadPoolExecutor(max_workers=4)` を使用。timeout 発火時は `fut.cancel()`（queued ならキャンセル、稼働中は leak → プロセス終了で消える）。context manager に「改善」してはいけない — 同じ事故が再発する。

### Gemini プロンプト共通禁止事項（extract_from_text_gemini）

field 個別 description の禁止指示は Gemini が無視する傾向あり。対策として **prompt 先頭に全フィールド共通ルール**を配置（field 個別より遵守率が高い）:
1. 集計列禁止（1Q/2Q/3Q/4Q/上期/下期/累計/通期/YTD）→ 月別単月列のみ
2. 対象月厳守（`{target_month_str}` のみ、なければ null）
3. 当年/前年・全店/既存店・サブカテゴリ取り違え禁止
4. %値は水準値（差分変換しない）/ 年度数字を値として返さない

末尾に【自己検証】ステップ追加。

### pdfplumber / HTML 実装上の注意点

- `page.bbox` がマイナスマージン（`-14.4`）を返す → `crop()` は必ず `page.bbox` を起点に使うこと
- `_extract_pdf_text()` はテーブル内容のみ返す → 年推定には `page.extract_text()` を使う
- FY年度でPDF列数が変わる → 列ヘッダーマッチが一般的に必要
- `△ 22.9`（スペース入り負数）は `[\d,.\-+▲△]+` でヘッダー行と誤認 → `[▲△\-+]?\s*[\d,]+\.?\d*\s*[%％]?` に改善済み
- JS レンダリング前の空HTML → `handle_html_table` に Playwright フォールバック追加済み

---

## extract_monthly_data.py — 抽出機能マップ

| 関数 | 主要機能 |
|------|---------|
| `_extract_pdf_by_row` | セクション検出(`page.crop()`) / 列ヘッダーregexマッチ / ▲△→負数変換 / テーブル跨ぎ |
| `_extract_pdf_single_month` | 単月PDF用: 全列スキャン + 前年比列自動検出 |
| `extract_from_html` | 月方向自動検出(column/row) / △→負数 / 年度推定 / table_index指定 |
| `_extract_pdf_ocr` | PyMuPDF→PIL→pytesseract(jpn+eng) 画像PDF対応 |
| `_extract_pdf_gemini_personal` | 個人APIキー Gemini PDF→JSON抽出 |
| `_follow_iframe()` | 同一ドメイン iframe 自動追跡 |
| `phase_extract` | 000000_プレフィックス合成 / overwrite_past_months(年度跨ぎ) |

**デバッグ知見**: §コード実装ノート「pdfplumber / HTML 実装上の注意点」参照

---

### TODO: `--since-data-month YYYY-MM`（未実装）

月次cronで前月分だけ処理する増分フィルタ。データ月（content月）ベース。非TDnet: ファイル名 `YYYYMM_` 比較、TDnet: `SUBMISSION_DATE >= '{content+1}-01'`。DL側は対応済み、Extract側は未実装。cron: `--since-data-month $(date -d "3 months ago" +%Y-%m)`。

---

### BC突合スコープ・一致率規約

- **正式数値**: records全社対象（`compare_monthly_buffett.py` デフォルト）のみ。`--tickers` は debug用（「N社のみ」と注記）
- `bc_ignore: true` field は分母からも除外

### TODO: `_parse_year_month` null-safety

`year_from_title_regex` の alternation で `group("year")=None → int(None)` TypeError。回避: adapter側で当該キー削除。本来は `groupdict().get("year")` で None 安全化。

### TODO: BC突合 残存NG 13社58件修復

2026-05-08セッション残存。`/monthly-error-autofix` で1社ずつ診断・修復。→ [プランMD](../../plans/tools-042_monthly_bc_ng13_20260512_180300.md)

---

## 子 MD

- [`042-1_monthly_error_fix_patterns.md`](042-1_monthly_error_fix_patterns.md) — `/monthly-error-autofix` パターンDB（Layer 1 即答パターン: D1-D2, E1-E4）
- [`042-1_bc_match_agent.md`](042-1_bc_match_agent.md) — BC突合 NG の自律修正エージェント (パターン A-I, ツール一覧, Runbook)
