# 四半期開示パイプライン（受注・受注残高）

**カテゴリ**: tools
**作成日**: 2026-04-30
**更新日**: 2026-05-07
**ステータス**: Phase 6b完了（PASS+PARTIAL 97.6%、553社）。Phase 7 BQ設計待ち
**適用範囲**: 建設業・設備工事業等の決算短信・決算参考資料PDF
**姉妹MD**: [`042_monthly_disclosure_master.md`](042_monthly_disclosure_master.md)（月次開示パイプライン）
**計画**: `docs/plans/tools-089-1_order_backlog_extraction_20260430_200000.md`

**役割**: 四半期（受注高・受注残高等）開示データの収集・抽出パイプラインの全体設計・GCSパス構成・アダプタスキーマ・スクリプトマッピング・運用手順を一元管理する。

---

## 最終目標

四半期開示データのうち**先行指標**（受注高・受注残高・繰越工事高等）を収集・蓄積し、将来の業績を予測する材料とする。

---

## 抽出アーキテクチャ: 2段階パイプライン

```
PDF → [Stage 1: pdfplumber] → 該当ページ特定 → [Stage 2: pymupdf PNG化 → Gemini Vision] → 構造化JSON
```

### Stage 1: ページ特定（コスト0）

- pdfplumber でページごとにテキスト抽出
- キーワード `繰越`, `手持` でフィルタ
- 目次ページも拾うが、Stage 2の精度に影響なし

### Stage 2: Gemini Vision 抽出

- pymupdf で該当ページを 200dpi PNG 化
- `gemini-3-flash-preview` に画像投入
- `response_mime_type="application/json"` + `temperature=0.0` で安定化

### 抽出方式（2種）

| 方式 | 対象 | 社数 | 特徴 |
|------|------|------|------|
| gemini_vision | complex_table / PPT / 同名ラベル重複 | 519 | structure.jsonのmetricsを動的プロンプト埋込。extract_adapterはpage_keywordsのみ |
| regex | simple_table | 34 | extract_adapterのfieldsにregexパターン定義。Gemini不要 |

### 依存パッケージ

- `pdfplumber` — PDFテキスト抽出
- `pymupdf` — PDF→PNG変換
- `google-genai` — Gemini API

### スクリプト

| スクリプト | 用途 |
|-----------|------|
| `scripts/extract_order_backlog.py` | 抽出+GCS保管一体型（`extract_with_gemini` → `_save_record_to_gcs`）。`--no-save` で保管スキップ可 |
| `scripts/extract_order_backlog_batch.py` | GCS対応バッチ版（553社一括抽出、チェックポイント/レジューム付き） |
| `scripts/build_backlog_adapter.py` | 全社アダプタ生成（structure.json + extract_adapter.json） |
| `scripts/generate_backlog_structure.py` | structure.json生成（Gemini Vision + response_schema） |
| `scripts/poc_extract_backlog.py` | POC版（1777川崎設備で検証済み） |

---

## GCSパス構成

バケット: `gs://stock_data_1930932/`

```
quarterly/
├── company_list.json                           # 対象企業一覧
├── meta/{ticker}/
│   ├── structure.json                          # 抽出対象メトリクス定義
│   └── extract_adapter.json                    # 抽出方式定義
├── record/{ticker}/
│   └── records.json                            # 抽出済み数値データ
├── docs/{ticker}/
│   └── *.pdf|xlsx|html                         # DL済みファイル実体（非TDNETルート用）
└── log/                                        # ジョブエラーログ
```

定数: `GCS_META="quarterly/meta"` / `GCS_RECORD="quarterly/record"` / `GCS_DOCS="quarterly/docs"` / `GCS_LOG="quarterly/log"`

### 月次との差異

| 項目 | monthly/ | quarterly/ | 理由 |
|------|----------|------------|------|
| レコードファイル名 | `monthly_records.json` | `records.json` | 親ディレクトリで文脈が明確 |
| meta配下JSON | structure / url_adapter / extract_adapter | structure / extract_adapter | url_adapterは不要（TDnet BQ経由） |

### 廃止パス（書き込み・読み取りともに禁止）

| 廃止パス | 代替 |
|---------|------|
| `order_backlog/meta/{ticker}/` | `quarterly/meta/{ticker}/`（2026-05-05移行完了） |

---

## アダプタ正式スキーマ定義

### structure.json

GCSパス: `quarterly/meta/{ticker}/structure.json`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `ticker` | string | ✓ | 銘柄コード |
| `company_name` | string | ✓ | 企業名 |
| `current_version` | string | ✓ | 最新バージョンの `valid_from` 値（例: `"2025-08"`） |
| `versions` | array | ✓ | バージョン配列（有効期間付き） |
| `versions[].valid_from` | string | ✓ | 有効開始期（`"YYYY-MM"`） |
| `versions[].valid_until` | string\|null | ✓ | 有効終了期。null = 現在有効 |
| `versions[].data_available` | boolean | ✓ | PDFに数値付き受注データがあるか |
| `versions[].metrics` | array | ✓ | 抽出対象メトリクス（data_available=false時は空配列） |
| `versions[].metrics[].name` | string | ✓ | 日本語メトリクス名。抽出JSONのキーとして使用 |
| `versions[].metrics[].type` | enum | ✓ | `order_intake` / `backlog`（**`completed` は収集対象外。使用禁止**） |
| `versions[].metrics[].description` | string | ✓ | 日本語の説明文 |
| `versions[].metrics[].unit` | string | ✓ | 許容値は下記の VALID_UNITS 参照 |
| `versions[].metrics[].is_total` | boolean | | true = 合計行、false = 内訳行 |
| `versions[].metrics[].parent_metric` | string | | 内訳行の場合の親メトリクス名 |
| `versions[].breakdown_dimensions` | array | | ブレークダウン軸の定義 |
| `versions[].presentation_format` | enum | | `table` / `mixed` / `text` |
| `versions[].complexity` | enum | | `simple_table` / `complex_table` / `no_table` / `ppt_chart` |
| `versions[].notes` | string | | 補足説明 |
| `_source_pdf` | string | | GCSパス（`tdnet/{ticker}/...`） |
| `_source_doc_id` | string | | TDnet文書ID |
| `_source_type` | string | | `tanshin` / `setsumei` |
| `_generated_at` | string | | ISO 8601 JST |
| `_model_used` | string | | 生成に使用したモデル |
| `_complexity_report` | object | | 複雑度判定の詳細 |

**VALID_UNITS**: `百万円`, `千円`, `億円`, `兆円`, `円`, `件`, `戸`, `棟`, `台`, `基`, `本`, `隻`, `機`, `組`, `口`, `室`, `人`, `区画`, `棟・区画`, `月`, `MW`, `kW`, `t`, `m²`, `ha`, `馬力`, `万総トン`, `USD`, `EUR`, `CNY`, `THB`, `SGD`, `AUD`, `GBP`, `KRW`, `TWD`, `VND`, `IDR`, `MYR`, `PHP`, `INR`, `BRL`, `CAD`, `NZD`, `CHF`, `HKD`, `SEK`, `NOK`, `DKK`, `ZAR`, `MXN`, `PLN`, `CZK`, `HUF`, `TRY`, `RUB`, `百万USD`, `百万EUR`, `千USD`, `千EUR`, `百万ドル`, `千ドル`, `千米ドル`, `億ドル`, `USDmil`, `万元`

### extract_adapter.json

GCSパス: `quarterly/meta/{ticker}/extract_adapter.json`

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `ticker` | string | ✓ | 銘柄コード |
| `company_name` | string | ✓ | 企業名 |
| `current_version` | string | ✓ | 最新バージョンの `valid_from` 値 |
| `versions` | array | ✓ | バージョン配列 |
| `versions[].valid_from` | string | ✓ | 有効開始期 |
| `versions[].valid_until` | string\|null | ✓ | 有効終了期 |
| `versions[].extraction_method` | enum | ✓ | **`gemini_vision`** or **`regex`** |
| `versions[].source` | string | ✓ | `tanshin` / `setsumei` |
| `versions[].page_keywords` | array[string] | ✓ | 該当ページ特定用キーワード |
| `versions[].fields` | array | ✓ | regex方式のみ使用。gemini_vision時は空配列 `[]` |
| `versions[].fields[].key` | string | | メトリクス名（structure.jsonのmetrics[].nameと一致すること） |
| `versions[].fields[].row_label_regex` | string | | 行ラベル+数値の正規表現 |
| `versions[].fields[].value_type` | string | | `integer` / `float` |
| `versions[].fields[].unit` | string | | 単位 |
| `versions[].gemini_custom_prompt` | string | | gemini_vision方式の企業固有補足指示 |
| `versions[].notes` | string | | 方式選択理由等 |
| `manual_override` | boolean | ✓ | true = 自動再生成禁止 |
| `_source_pdf` | string | | GCSパス |
| `_source_doc_id` | string | | TDnet文書ID |
| `_generated_at` | string | | ISO 8601 JST |
| `_model_used` | string | | 生成に使用したモデル |

**キー名の統一ルール**（月次パイプライン A1/A2 事故からの教訓）:
- `gemini_custom_prompt` が正式キー名（`custom_prompt` ではない）
- `manual_override` が正式キー名（`_manual_override` ではない）
- フィールド追加時はこのスキーマ表を先に更新してからコードに実装すること

---

## 抽出プロンプトのガードレ��ル

### メトリクス type の制約（2026-05-07 確定）

**収集対象は `order_intake`（受注高）と `backlog`（受注残高）のみ。**

`type: "completed"`（売上高・完成工事高・売上収益等）は決算短信XBRLから取得済みであり、本パイプラインでは**収集対象外**。structure.json生成時にcompletedメトリクスが含まれていた場合は除去する。completedのみの企業は `data_available=false` とする。

### プロンプト記述ルール

`build_extraction_prompt()` (`scripts/extract_order_backlog.py` L41) のルール1-8に加え、以下を遵守:
- 構成比（%）は金額ではないため抽出対象外
- 通貨記号（¥, $, €）はノイズ。数値のみ抽出
- ヘッダ行・ラベル行は数値として拾わない
- 予想・計画値と実績値は `period_type` で区別し混同しない
- 同一テーブルに複数期間がある場合、全期間を個別の `periods` 要素として抽出
- 新規プロンプト関数を作成する際は、既存 `build_extraction_prompt()` のルール1-8を必ず踏襲すること（月次パイプライン B4 事故: ガードレール未継承で誤抽出）

---

## アダプターのローカル Git 管理

四半期アダプター（`structure.json` / `extract_adapter.json`）は GCS と**ローカル `meta/quarterly/` の二重管理**。月次（`meta/monthly/`）と同じ設計。

### ローカル命名ルール

**ローカル = `{ticker}_` + GCSファイル名**。例外なし。月次と共通ルール。

### ファイルマッピング

| 種別 | ローカルパス | GCSパス | 判定キー |
|------|------------|---------|---------|
| **structure** | `meta/quarterly/{ticker}_structure.json` | `quarterly/meta/{ticker}/structure.json` | `versions` (list) / `metrics` (list) |
| **extract adapter** | `meta/quarterly/{ticker}_extract_adapter.json` | `quarterly/meta/{ticker}/extract_adapter.json` | `fields` (list) / `method` |

### 不変ルール

1. **ローカル `{ticker}_structure.json` ↔ GCS `structure.json`** が正しいペアリング
2. **ローカル `{ticker}_extract_adapter.json` ↔ GCS `extract_adapter.json`** が正しいペアリング
3. **正本はGCSとローカルを同時に反映する**。片方だけ更新して「後で同期」は禁止。修正→ローカル保存→GCSアップロード→git commitを1セットで行う
4. **開発中・テスト用アダプタを `meta/quarterly/` に混在させない**。テスト用は `C:\tmp\` 等の一時ディレクトリで管理し、検証完了後に正本ディレクトリへ移動する
5. 同期時は内容種別を検証する（月次の `_detect_adapter_kind()` と同じ原則）

### 同期手順

```bash
# GCS → LOCAL（全量同期）
PYTHONUTF8=1 <python> -c "
from google.cloud import storage
from google.oauth2 import service_account
from pathlib import Path
creds = service_account.Credentials.from_service_account_file('keys/gcp-service-account.json')
client = storage.Client(project='gmailpj-357912', credentials=creds)
bucket = client.bucket('stock_data_1930932')
out = Path('meta/quarterly')
out.mkdir(parents=True, exist_ok=True)
for blob in client.list_blobs('stock_data_1930932', prefix='quarterly/meta/'):
    parts = blob.name.split('/')
    if len(parts) < 4: continue
    ticker, fn = parts[2], parts[3]
    if fn == 'structure.json':
        blob.download_to_filename(str(out / f'{ticker}_structure.json'))
    elif fn == 'extract_adapter.json':
        blob.download_to_filename(str(out / f'{ticker}_extract_adapter.json'))
"
```

---

## 設計指針

### 1. monthly/ との構造対称性

`quarterly/` は `monthly/` のディレクトリ構造を原則引き継ぐ。新しいサブディレクトリを追加する場合、monthly側にも同等の概念が存在するか確認し、存在すれば同じ名前・構造にする。

### 2. エラーリカバリプロセス

月次開示パイプラインの「エラーログからリカバリするプロセス」を四半期にも実装する。`quarterly/log/` にジョブのエラーログを保管し、後続のリカバリジョブがこれを読んで再試行する設計。

### 3. TDnet PDF のGCS保持は不要

TDnet開示PDFをGCSに別途保持する設計は採用しない。PDFの参照が必要な場合は BQ `TDNET_DOCUMENTS_ENHANCED` → GCS `tdnet/` の既存パスを利用する。

### 4. 非TDNETルートのファイル保管

TDnet で開示されない企業の四半期データは `quarterly/docs/{ticker}/` に保管する。

---

## レコード保管設計（2026-05-07 確定）

### 基本方針: 最新期のみ追記・upsert

GCSパス: `quarterly/record/{ticker}/records.json`

**各開示文書から「その文書が報告している最新期」のデータのみを records.json に追記する。**

- 抽出時にPDFから複数期間のデータが取れても（前期比較用等）、records.jsonに反映するのは**報告期間（最新期）の1レコードのみ**
- 同じ期間のレコードが既に存在する場合は**上書き（upsert）**
- BQテーブルは不使用。GCS JSON が唯一のデータストア

### 時系列の蓄積イメージ

```
2025 1Q開示処理 → records.json: [{period: "2025-1Q", ...}]
2025 2Q開示処理 → records.json: [{period: "2025-1Q", ...}, {period: "2025-2Q", ...}]
2025 3Q開示処理 → records.json: [..., {period: "2025-3Q", ...}]
2025 4Q開示処理 → records.json: [..., {period: "2025-4Q", ...}]
```

### レコード構造

```json
{
  "ticker": "1777",
  "company_name": "川崎設備工業",
  "records": [
    {
      "period": "2026年3月期",
      "period_type": "annual",
      "extraction_date": "2026-05-07T08:00:00+09:00",
      "source_doc_id": "140120260428130007",
      "data": {
        "繰越工事高": {
          "value": 29718,
          "unit": "百万円",
          "breakdown": {"一般ビル": 25671, "産業施設": 4047}
        }
      }
    }
  ]
}
```

### upsertロジック

1. 抽出結果から報告期間（periods配列の最新）を特定
2. records.jsonをGCSから読み込み（初回は空オブジェクト作成）
3. `records[]` から同一 `period` のエントリを検索
4. 存在すれば上書き、なければ追加
5. GCSに書き戻し

### 過去データの扱い

- 抽出時に付随する過去期間データは**破棄**（records.jsonに書かない）
- 過去データが必要になった場合は、対象期間のPDFを再抽出すればよい（TDnet PDFはGCS `tdnet/` に永続保存済み）

---

## XBRL調査結果（2026-04-30）

決算短信XBRLには受注高・繰越工事高のタグが**存在しない**。

- 建設業タクソノミのXBRLタグは BS/PL/CF の勘定科目のみ
- `qualitative.htm` にプレーンテキストで記載されるが `ix:nonFraction` タグ付けなし
- J-Quants fin-summary にも該当フィールドなし
- **結論**: PDF抽出（本パイプライン）が唯一の構造化抽出手段

---

## POC結果（2026-04-30）

川崎設備工業(1777) 2026年3月期 決算参考資料（13ページ）で検証。

| 期 | 繰越工事高合計 | 一般ビル | 産業施設 | 電気工事 |
|----|-------------|---------|---------|---------|
| 2025/3 | 27,152 | 24,456 | 2,122 | 573 |
| 2026/3 | 29,718 | 25,671 | 2,963 | 1,083 |

PDFグラフのラベル値と完全一致。

---

## 落とし穴・注意事項

- グラフ主体のPDFではpdfplumberのテキスト抽出だけでは数値の区別が困難。画像投入が必須
- 目次ページもキーワードにヒットするが、Geminiが文脈で判別するため実害なし
- ブレークダウンの項目名は企業・業種によって異なる。プロンプトで柔軟に対応する設計が必要
- **旧パス `order_backlog/` は使用厳禁**。GCSパスは `quarterly/meta/{ticker}/` に統一済み（2026-05-05移行完了）

---

## 未定事項

- `company_list.json` のスキーマ
- 実行頻度・トリガー方式（Phase 8で定義）
- BQテーブル `STOCK.ORDER_BACKLOG` 設計（Phase 7で定義）
- **累計→Q単独変換**: 6367等、受注高が累計で開示される企業がある。BQロード or 利用側で累計→Q単独変換が必要（cf. `fin_summary` の `v_fin_summary_actual_for_q_on_q` と同等のロジック）。データイメージ（6376日機装、受注高/百万円）:

  | 期 | 1Q | 2Q | 3Q | 通期 |
  |---|---:|---:|---:|---:|
  | 2022/12期 | 53,432 | 114,039 | 155,855 | 205,175 |
  | 2023/12期 | 54,427 | 98,510 | 144,462 | 198,501 |
  | 2024/12期 | 54,684 | 118,394 | 171,829 | 222,024 |
  | 2025/12期 | 52,523 | 105,494 | 163,748 | 231,411 |

  2Qは1Q+2Q累計。Q単独は `2Q単独 = 2Q累計 - 1Q累計` で算出
- **ザラ場ツール連携**: 抽出済み受注データをザラ場スコアリングの因子として活用するインターフェース設計（起源: 5/15反省会、6376日機装）
