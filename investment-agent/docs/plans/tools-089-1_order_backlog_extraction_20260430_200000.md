# 作業計画: 四半期受注・受注残高 抽出パイプライン

**作成日時**: 2026-04-30 20:00 (JST)
**更新日時**: 2026-05-07 07:00 (JST)
**ステータス**: Phase 6b完了 + completedメトリクス除去完了。有効企業504社(order_intake/backlogのみ)。次: Phase 7 BQテーブル設計��ロード
**実行担当**: Phase 5以降はCodex（Opus以外）
**分類**: (a) 恒久知見型
**親知見 MD**: `docs/knowledges/tools/089_quarterly_disclosure_master.md`
**関連アイディアID**: -

## 目的

建設業等の決算参考資料PDFから「受注高」「完成工事高」「繰越工事高（受注残高）」を自動抽出し、BQに蓄積する。四半期ごとの受注動向を定量的に追跡可能にする。

## 背景・動機

- 建設・設備工事業の受注残高は将来売上の先行指標。受注残が増えている企業は業績拡大が期待できる
- これらの数値は決算短信XBRLには含まれない（2026-04-30調査確定）
- POCとして川崎設備工業(1777)の決算参考資料PDFで2段階抽出パイプラインを検証済み
- 月次開示パイプライン（042）と同様のアダプタ3層構造を採用するが、構造はゼロベースで設計

## 設計方針（2026-04-30 LINE会話で確定）

### アダプタ3層構造

月次開示パイプラインの3層（url_adapter / extract_adapter / structure）を参考にしつつ、受注残高固有の最適設計をゼロベースで行う。

| アダプタ | 月次パイプライン相当 | 受注残高での役割 | 設計方針 |
|---------|-------------------|----------------|---------|
| **structure.json** | structure.json（BC KPI定義） | 各社「何を抽出するか」の定義 | 自前で定義（BC不使用）。企業ごとに異なる |
| **extract_adapter.json** | extract_adapter.json（抽出ルール） | 各社「どう抽出するか」の定義 | ゼロベースで最適構造を設計 |
| **source adapter** | url_adapter.json（IR URL+DLルール） | PDF特定ルール | TDnetカテゴリベース。将来非TDnet対応可能な設計 |

### 確定事項

- **BQカテゴリのみ使用**: ベクトル検索は不採用（precision不足）。TDNETカテゴリ「受注高/受注残高」でPDF特定
- **パターン化しない**: 各社のPDFを1社ずつ読み、個別にstructure.jsonを作成。効率化のための類型化は手抜きになるため禁止
- **structure.json先行**: 「何を抽出するか」を先に定義し、それに基づいてextract_adapterを設計
- **XBRL不可確定**: 受注高・繰越工事高のXBRLタグは存在しない。PDF抽出が唯一の構造化手段

## POC実施結果（2026-04-30）

### 対象PDF
- URL: `https://data.sbisec.co.jp/data/tdnet/nTSPb7d06c_20260428_130007.pdf`
- 川崎設備工業(1777) 2026年3月期 決算参考資料（全13ページ）

### 2段階パイプライン

| Stage | 手法 | コスト | 結果 |
|-------|------|--------|------|
| Stage 1 | pdfplumber テキスト抽出 → キーワード(`繰越`, `手持`)でページ特定 | 0 | P.2(目次), P.9, P.10 の3ページを特定 |
| Stage 2 | 該当ページPNG化(pymupdf, 200dpi) → Gemini Vision(`gemini-3-flash-preview`) | ~$0.001 | 合計値・ブレークダウンとも正確に抽出 |

### 抽出結果（正確性確認済み）

```json
{
  "company_name": "川崎設備工業株式会社",
  "unit": "百万円",
  "periods": [
    {
      "period": "2025年3月期",
      "total": 27152,
      "breakdown": {
        "一般ビル": 24456,
        "産業施設": 2122,
        "電気工事": 573
      }
    },
    {
      "period": "2026年3月期",
      "total": 29718,
      "breakdown": {
        "一般ビル": 25671,
        "産業施設": 2963,
        "電気工事": 1083
      }
    }
  ]
}
```

### POCスクリプト
- `scripts/poc_extract_backlog.py` — 2段階パイプラインの実装

### 検討した代替案と選定理由

| 案 | 方式 | 評価 |
|----|------|------|
| 案1 | pdfplumber + regex | グラフラベルの混在で合計値とブレークダウンの区別困難。× |
| 案2 | Gemini Vision（全ページ） | 汎用だがコスト無駄。Stage 1で絞れば改善 |
| 案3 | PyMuPDF 座標ベース | 企業ごとにレイアウト異なり汎用化困難。× |
| **案4改** | **pdfplumber→ページ特定→PNG→Gemini Vision** | **コスト最小＆精度最高。採用** |
| 案5 | 決算短信XBRL | **不可**。受注高・繰越工事高のXBRLタグが存在しない |

## サンプル30件解析結果（2026-04-30）

BQカテゴリ「受注高/受注残高」ヒットの30件をDL・解析。

### 全体分布

| 分類 | 件数 | 割合 |
|------|------|------|
| 受注残高データあり | 19 | 66% |
| 受注高のみ（繰越なし） | 1 | 3% |
| 誤判定（受注関連なし） | 9 | 31% |
| GCS欠損 | 1 | - |

### 抽出方法の分布（19社中）

| 抽出方法 | 件数 | 代表例 |
|---------|------|-------|
| pdfplumber テーブル抽出可能 | 14 | 洋エンジ, 住友電設, 船場, ナガオカ |
| Gemini Vision 必須（グラフ/プレゼン） | 3 | IWI, タクマ, SCSK |
| テキスト記述のみ | 2 | 寺崎電気 |

### 偽陽性（30%）の意味

カテゴリ「受注高/受注残高」でヒットしたが受注残高データが存在しない企業が30%。
→ structure.json作成時にPDFを個別確認する工程で自然にフィルタされる。

## 作業ステップ

### Phase 1: データソース確定 ✅ 完了（2026-04-30）

1. [x] 決算短信XBRL（建設業タクソノミ）に受注高・繰越工事高が含まれるか確認 → **XBRLタグなし**
2. [x] PDF抽出が唯一のパス確定。2段階パイプライン（pdfplumber→Gemini Vision）で進める
3. [x] POC実施（川崎設備工業1777）→ 成功
4. [x] サンプル30件DL・解析 → 19/29社に受注残高データあり

### Phase 2: structure.json 設計・作成 ✅ 完了（2026-04-30）

**作業方針**: 各社のPDFを1社ずつ読み、個別にstructure.jsonを作成する。パターン化・類型化による効率化は禁止。

5. [x] structure.json のスキーマ設計（全社共通のフォーマット定義）
6. [x] サンプル19社のPDFをGemini Visionで個別分析し、structure.jsonを自動生成
   - `scripts/generate_backlog_structure.py` で19/19社処理完了（全社gemini-3-flash-preview）
   - 出力先: `C:/tmp/order_backlog_survey/structure/{ticker}_structure.json`
   - 偽陽性2社(3423,9161): PDFに数値データなし → 除外対象
7. [ ] GCSパス構造の決定（Phase 5で実施）

### Phase 3: 抽出テスト実行 ✅ 完了（2026-04-30）

**実施方針**: structure.jsonに基づき、Gemini Visionで各社PDFから数値データを抽出。extract_adapter.jsonは不要と判断（structure.jsonが抽出ガイドを兼ねる）。

8. [x] `scripts/extract_order_backlog.py` で17社の抽出実行
   - 全17社 gemini-3-flash-preview で成功（フォールバック不要）
   - 出力先: `C:/tmp/order_backlog_survey/extracted/{ticker}_extracted.json`
9. [x] 品質検証 → 13社A品質 / 2社B品質(名前修正要) / 2社C品質(偽陽性除外)
10. [x] エラーログ記録 → `docs/plans/088_error_log.md` に5件記録

### Phase 4: 抽出スクリプト本格化・品質改善 ✅ 完了（2026-04-30）

11. [x] B品質2社のプロンプト改善と再抽出 → ルール1「日本語name値をキーに使え」追加で解決
12. [x] 抽出結果の統合サマリCSV作成 → `C:/tmp/order_backlog_survey/extraction_summary.csv`
13. [ ] source adapter設計（Phase 5以降で実施）

### Phase 1-4 サンプル完遂結果

| 指標 | 値 |
|------|-----|
| 対象企業（BQカテゴリヒット） | 30社 |
| GCS欠損 | 1社 |
| PDF分析対象 | 29社 |
| structure.json生成 | 19社（10社は受注関連データなし） |
| 抽出対象（data_available=true） | 17社 |
| 偽陽性除外 | 2社（3423,9161: 数値データなし） |
| 最終有効抽出 | 15社 |
| 全社gemini-3-flash-preview | 100%（フォールバック不要） |
| 総データポイント | 1,012 |
| 充填率（非null） | 89.7% |
| 受注残高関連データ | 160件（15社全社） |
| エラー件数 | 5件（全件対処済み） |

---

## 全社展開計画（Phase 5〜8）

### 母集団（BQ実測、2024-10月以降）

| セグメント | 企業数 |
|-----------|--------|
| 決算短信のみ | 506 |
| 決算説明資料のみ | 491 |
| 両方あり | 295 |
| **合計** | **1,292** |

※ BQカテゴリ「受注高/受注残高」でフィルタ済みの母集団。Step 1でPDF特定後、Step 2のキーワードスキャンでさらに絞り込む。

### 基本方針: 1社ずつ丁寧に

**サンプル15社の比率を母集団に外挿しない。** 各社のPDFを個別に確認し、その企業に最適な抽出方式を判定する。パターン化・類型化による一括処理は禁止。

### PDFソース選択方針

**決算短信を主ソースとする。** 決算短信は全上場企業が提出義務を持つ構造化度の高い文書であり、建設業等では「生産、受注および販売の状況」セクションが必須記載事項。決算短信に受注関連データがない場合のみ決算説明資料にフォールバック。

### 1社ごとのアダプタ生成パイプライン

各企業について以下のステップを**順番に**実行する。バッチ一括処理ではなく、1社完了→次の1社のフロー。

```
Step 1: PDF取得
  BQ → 最新の決算短信PDF特定 → GCSダウンロード
  短信なし → 決算説明資料にフォールバック

Step 2: 事前フィルタ（コスト$0）
  pdfplumberキーワードスキャン（受注残/繰越/手持/受注高）
  → 該当ページなし → data_available=false → 次の企業へ

Step 3: 複雑度の軽量判定（コスト$0）
  pdfplumberテーブル抽出を試行し、以下を確認:
  a. テーブルが得られるか
  b. テーブル数（1つか複数か）
  c. 列数・行数
  d. セル結合の有無（Col 0に複数ラベル連結 = 042の6752型）
  e. 単位混在の有無（同一PDF内に百万円と億円が共存等）
  → 判定結果: simple_table / complex_table / no_table / ppt_chart

Step 4: structure.json生成（Gemini Vision、response_schema適用）
  該当ページをPNG化 → Gemini Visionで構造分析
  - response_schemaでJSON構造を強制（list/dict混在防止）
  - few-shot例3社をプロンプトに含める
  - data_available偽陽性チェック:
    notes内に「数値データなし」→ data_available=false
    metrics空 → data_available=false

Step 5: extract_adapter.json生成（方式はStep 3の結果に基づく）
  a. simple_table → Geminiでregexパターン生成（1回のみ）
  b. complex_table → Gemini Vision adapter（extraction_method: gemini_vision）
  c. no_table / ppt_chart → Gemini Vision adapter
  ※ regexは構造が単純な場合のみ。複雑なテーブルにregexを強制しない

Step 6: 即時テスト（同じPDFで抽出実行）
  生成したadapterで実際に抽出 → 品質チェック:
  - 充填率 < 50% → Step 6b へ
  - メトリクス名が英語化 → 再抽出
  - 値が明らかに異常（負値、0、桁違い） → 要確認リストへ

Step 6b: Gemini失敗時のフォールバック（regex企業のみ）
  **Geminiの再試行はしない（ループ禁止）。**
  Claude(Opus)がPDFテキストを直接読み、regexを手動記述する。
  → 即時テスト → 成功 → 保存
  → それでもダメ → extraction_method: gemini_vision に切替

Step 7: GCS保存 + チェックポイント
  structure.json + extract_adapter.json → GCSアップロード
  処理済みticker記録 → 次回実行時スキップ（レジューム対応）

→ 次の1社へ（Step 1に戻る）
```

### 月次開示パイプラインからの教訓適用

| 月次での失敗 | 本パイプラインでの回避策 | 実装箇所 |
|---|---|---|
| regex生成→テスト→修正ループ（トークン浪費の主因） | **Gemini regex生成は1回のみ（ループ禁止）**。失敗時はClaude(Opus)が直接regex記述 → それでもダメ→Vision切替。複雑テーブルには最初からregex適用しない | Step 3, 5, 6, 6b |
| Gemini出力不安定(list/dict混在) | **response_schema**で構造強制（constrained decoding） | Step 4, 5 |
| description月固定/サンプル値依存(overfit) | few-shot例で安定化。**期間固有値をpromptに含めない** | Step 4 |
| structure.json ↔ extract_adapter.json混同事故 | ファイル名で区別（月次§ファイルマッピング絶対表と同方針） | Step 7 |
| PPT/プレゼンPDFでregex不可（月次6752型） | **Step 3でno_table/ppt_chart判定 → Vision直行**。regex試行すらしない | Step 3, 5 |
| adapter生成→外部テスト待ち→修正のラウンドトリップ | **即時テスト(Step 6)**: 同じPDFで即座に検証。外部待ちなし | Step 6 |
| pdfplumberテーブル破損（Col 0ラベル連結、042 L383-398） | **Step 3でCol 0文字数チェック**。10+文字かつ複数キーワード含む → complex扱い | Step 3 |
| クラッシュ時のやり直し | **チェックポイント/レジューム**(Step 7)。処理済みticker記録 | Step 7 |

### 2層アダプタ設計

#### structure.json（何を抽出するか）

structure.jsonにも有効期間を持たせる。セグメント再編・メトリクス変更・単位変更等が発生した場合にバージョニング。

```json
{
  "ticker": "6330",
  "company_name": "東洋エンジニアリング",
  "current_version": "2024-04",
  "versions": [
    {
      "valid_from": "2024-04",
      "valid_until": null,
      "data_available": true,
      "metrics": [...],
      "breakdown_dimensions": [...],
      "presentation_format": "table|mixed|text",
      "complexity": "simple_table|complex_table|no_table|ppt_chart"
    }
  ]
}
```

**変更検知トリガー**: 四半期更新時、新PDFから抽出したメトリクス名・ブレークダウン項目が既存structure.jsonと不一致 → 開示変更の可能性 → 新バージョン作成

#### extract_adapter.json（どう抽出するか）

regex方式（simple_table企業）:
```json
{
  "ticker": "6330",
  "current_version": "2024-04",
  "versions": [
    {
      "valid_from": "2024-04",
      "valid_until": null,
      "extraction_method": "regex",
      "source": "tanshin",
      "page_keywords": ["繰越", "受注残", "手持"],
      "fields": [
        {
          "key": "次期繰越工事高（合計）",
          "row_label_regex": "次期繰越工事高.*計\\s+([\\d,]+)",
          "value_type": "integer",
          "unit": "百万円"
        }
      ]
    }
  ],
  "manual_override": false
}
```

Gemini Vision方式（complex/PPT企業）:
```json
{
  "ticker": "9719",
  "current_version": "2024-04",
  "versions": [
    {
      "valid_from": "2024-04",
      "valid_until": null,
      "extraction_method": "gemini_vision",
      "source": "tanshin",
      "page_keywords": ["受注残", "受注高"],
      "gemini_custom_prompt": "...(企業固有の注意事項)"
    }
  ],
  "manual_override": false
}
```

#### アダプタ有効期間設計（structure.json / extract_adapter.json 両方）

企業が開示フォーマットを変更した場合（表現変更・内容変更・セグメント再編等）に両アダプタをバージョニングする。

| フィールド | 意味 | 例 | 対象ファイル |
|-----------|------|-----|------------|
| `valid_from` | このバージョンが有効な最初の決算期 | `"2024-04"` | structure + extract |
| `valid_until` | このバージョンが有効な最後の決算期。null = 現在有効 | `"2025-03"` or `null` | structure + extract |
| `current_version` | 最新バージョンのvalid_from値 | `"2024-04"` | structure + extract |

**運用フロー**:
1. 初回作成時: `valid_from = 最新期の年月`, `valid_until = null`
2. 開示変更検知時: 旧バージョンに`valid_until`設定 → `versions`に新バージョン追加
3. 抽出時: 対象期間に`valid_from <= period <= valid_until`(or null)のバージョンを選択

**変更検知**: 四半期更新時、新PDFの抽出結果が既存structure.jsonのメトリクス名・ブレークダウンと不一致→新バージョン作成を提案

**GCSパス**: `quarterly/meta/{ticker}/extract_adapter.json`（1ファイル、有効期間はJSON内）

**ファイル内バージョニング例**:
```json
{
  "ticker": "6330",
  "current_version": "2024-04",
  "versions": [
    {
      "valid_from": "2024-04",
      "valid_until": null,
      "extraction_method": "regex",
      "fields": [...]
    }
  ]
}
```
開示変更時: 既存バージョンに`valid_until`設定 → `versions`に新バージョン追加

#### レコード保管（月次開示と同パターン）

抽出結果は月次開示の`monthly_records.json`と同じJSONファイル形式でGCSに保管する。

```json
// quarterly/record/{ticker}/backlog_records.json
{
  "ticker": "6330",
  "company_name": "東洋エンジニアリング",
  "records": [
    {
      "period": "2025年3月期 第3四半期",
      "period_type": "cumulative",
      "extraction_date": "2026-04-30T22:00:00+09:00",
      "source_pdf": "6330_20250213_...",
      "adapter_version": "2024-04",
      "data": {
        "次期繰越工事高（合計）": {
          "total": 355124,
          "breakdown": {"海外": 281404, "国内": 73720}
        }
      }
    }
  ]
}
```

GCSパス: `quarterly/record/{ticker}/backlog_records.json`

### Phase 5: 全社アダプタ生成 ✅ 完了（2026-04-30〜05-01）

14. [x] BQ → 1,292社の最新PDF特定クエリ（決算短信優先、なければ説明資料）
15. [x] `build_backlog_adapter.py` 実装（Codex）
    - response_schema定義、few-shot例、複雑度判定、チェックポイント/レジューム機構
    - `_detect_unit_mixed()` バグ修正: 百万円/千円/億円を除去後にbare円を検出する方式に変更
16. [x] 全社実行（1,292社 → 133社再分類実施）
17. [x] 処理結果サマリ:

| 分類 | 社数 | 備考 |
|------|------|------|
| regex | 34 | simple_table → regexパターン自動生成 |
| gemini_vision | 99 | complex_table / PPT / 同名ラベル重複等 |
| **再分類合計** | **133** | unit_mixed修正により complex→simple に再分類 |
| still-complex | 104 | 修正後も unit_mixed=True（正当な複数単位混在） |
| 非対象 | 1,077 | unit_mixed以外の理由でcomplex（変更なし） |

**Phase 5 検証結果**: `docs/plans/20260501_060000_verify_unit_mixed_fix.md` 参照
- Step1 独立再分類: 15/15 PASS
- Step2 regex抽出: 3/5→修正後5/5（1793,4667個別修正済み）
- Step3 gemini必要性: regex可能<1% PASS
- Step4 差分検証: 5/5 PASS
- Step5 単体テスト: 11/11 PASS
- Step6 still-complex: 5/5 PASS

### Phase 6: 品質検証・修正 ✅ 完了（2026-05-06 品質確定）

18. [x] structure.json品質保証（別計画 `20260501_202600_structure_json_quality_assurance.md`）
    - Phase A: 13.3%エラー率（CONDITIONAL）→ Phase B: 自動チェッカー+全社修正 → Phase C: 再サンプル0%（PASS）
    - error 69社 + warning 69社 + 補完チェッカー2社 = 計140社修正・GCSアップ済み
    - Phase C': プロンプト/schema改善完了（偽陽性対策・enum単位制約・漏れ対策・重複対策）
19. [x] ランダム10社PDF手動照合 → 10/10 correct

### Phase 6b: extract_adapter 検証・補修

**背景**: extract_adapterは全553社で生成済み。gemini_vision方式(519社)はスケルトン(page_keywordsのみ)で、抽出時にstructure.jsonのmetricsを動的参照する設計（`extract_order_backlog.py` L41-87）。structure.json修正は自動反映される。regex方式(34社)のみ、structure修正でメトリクス追加された企業のfields再生成が必要。

**検証結果（2026-05-06確認）:**
- gemini_vision (519社): 作り直し不要。structure.jsonが修正済みなら抽出指示は自動整合
- regex (34社): 2社で structure↔adapter 不整合確認（1793: 受注高欠落、6248: 4指標欠落）

20. [x] regex不整合企業のfields再生成（2社: 1793受注高追加、6248→gemini_vision切替）
21. [x] **E2Eサンプルテスト（15社）**: PASS 12 / PARTIAL 1 / FAIL 2 → 基準達成(12/15≥80%)
22. [x] **全社バッチ抽出テスト（553社）**: PASS 392 / PARTIAL 116 / FAIL 45（PASS率70.9%、PASS+PARTIAL率91.9%）
    - バッチスクリプト: `scripts/extract_order_backlog_batch.py`（Codexリポ）
    - 抽出JSON: 549件（`C:\tmp\e2e_test\extracted\`）
    - GCSアップロード未実施（Claude Code検証待ち）
23. [x] 失敗企業の個別修正（45社→残FAIL 13社。PASS+PARTIAL率 97.6%達成。structure 26社修正+adapter 10社修正→GCS+git反映済み `437c018`）
23b. [x] Claude Code側検証（fix_summary.csv目視 + 修正JSONサンプル確認。スキーマ違反10件発見→修正→GCS反映 `6057113`）
23c. [x] 残FAIL 13社の方針決定・実行（2026-05-07）
    - **A群（4社: JSON Parse Error → 次バッチでリトライ）**: 2395, 4719, 5071, 7827
    - **B群（4社: data_available=false化 完了）**: 3246, 3450, 6113, 6976（GCS+git反映済み）
    - **C群（5社: 現状維持）**: 4069, 6501, 6578, 7409, 7438（部分抽出データあり、FAIL許容）
    - 最終品質: PASS 415 / PARTIAL 118 / FAIL 9(A4+C5) / 除外 11(既存7+B4) → PASS+PARTIAL率 98.3%

### Phase 6c: 初回レコード保管（E2E結果 → GCS records.json）

**目的**: Phase 6b の抽出結果(549社)を正式なGCSレコードとして保管する。

24. [x] GCS保管ロジックを `scripts/extract_order_backlog.py` に統合（2026-05-07完了。save_backlog_record.py廃止）
    - 入力: 抽出済みJSON (`C:/tmp/e2e_test/extracted/{ticker}_extracted.json`)
    - ロジック:
      a. 抽出JSONの `periods[]` から最新期（max period）を特定
      b. 最新期の `data` から、現行structure.jsonに存在す���メトリクス(order_intake/backlog)のみ抽出
      c. `quarterly/record/{ticker}/records.json` にupsert（なければ��規作成）
    - GCSパス: `gs://stock_data_1930932/quarterly/record/{ticker}/records.json`
    - `--dry-run` 実装
25. [x] dry-run確認済み（462社成功/87スキップ/0エラー）
26. [ ] 本実行（504社。data_available=false/excluded企業はスキップ）
27. [ ] A群4社リ��ライ（JSONパースエラー。再抽出→保管）

### ~~Phase 7: BQテーブル設計・ロード~~ → 廃止（2026-05-07）

BQ不使用に決定。GCS JSON（`quarterly/record/{ticker}/records.json`）が唯一のデータストア。
分析はGCS→pandas直接ロードで対応。データが数期分溜まった段階で必要に応じて再検討。

### Phase 8: 四半期運用フロー（当面保留）

27. [ ] 四半期更新スクリプト（手動実行前提。自動化は当面不要）
    - 新規決算短信の検出（BQ `TDNET_DOCUMENTS_ENHANCED` から新着検索）
    - 既存企業: adapter適用 → 抽出 → records.json upsert（最新期のみ追記）
    - 新規企業: structure.json生成 → adapter生成 → 抽出 → records.json作成
28. [ ] 開示変更検知（structure.jsonバージョニング: 088計画 L291）
29. [ ] 知見MD更新（089-1）

### コスト見積もり

| 項目 | Geminiコスト | 備考 |
|------|------------|------|
| Phase 5 structure.json生成 | ~$0.002/社 | response_schema + few-shot |
| Phase 5 extract_adapter(regex) | ~$0.002/社 | simple_table企業のみ |
| Phase 5 extract_adapter(Vision) | ~$0.002/社 | complex/PPT企業 |
| Phase 5 即時テスト(Vision) | ~$0.002/社 | Vision方式の企業のみ |
| Phase 5 手戻りバッファ(20%) | 上記×0.2 | 方式切替・再生成 |
| **四半期運用(regex企業)** | **$0** | pdfplumber+regexで完結 |
| **四半期運用(Vision企業)** | **~$0.002/社** | 毎回Gemini Vision |

※ 母集団1,292社中の有効企業数・方式別比率は各社個別判定のため事前推定しない

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| TDnet開示PDF | (b) GCS | `gs://stock_data_1930932/tdnet/` |
| TDnet開示メタデータ | (a) BQ | `STOCK.TDNET_DOCUMENTS_ENHANCED` |
| 銘柄マスタ（業種コード） | (a) BQ | `STOCK.STOCK_CODE_LIST` |
| structure.json | (b) GCS | `gs://stock_data_1930932/quarterly/meta/{ticker}/structure.json` |
| extract_adapter.json | (b) GCS | `gs://stock_data_1930932/quarterly/meta/{ticker}/extract_adapter.json` |
| 抽出レコード | (b) GCS | `gs://stock_data_1930932/quarterly/record/{ticker}/backlog_records.json` |
| BQ連携（任意） | (a) BQ | `STOCK.ORDER_BACKLOG`（GCS JSONからロード） |

## 成果物

- `scripts/build_backlog_adapter.py` — 全社アダプタ生成スクリプト（1社ずつパイプライン）
- `scripts/extract_order_backlog.py` — 本番抽出スクリプト（regex + Vision 2層対応）
- GCS `quarterly/meta/{ticker}/structure.json` — 企業別抽出対象定義（2026-05-05 移行完了）
- GCS `quarterly/meta/{ticker}/extract_adapter.json` — 企業別抽出ルール（有効期間付き）
- GCS `quarterly/record/{ticker}/records.json` — 抽出レコード（月次と同パターン）
- `docs/knowledges/tools/089_quarterly_disclosure_master.md` — 知見ファイル（スキーマ定義・アーキテクチャ含む）

## 最終完了条件

- 全社展開後に確定した有効企業のstructure.json + extract_adapter.json作成済み
- 抽出精度90%以上（手動照合10社以上）
- BQテーブルにロード済み
- 四半期更新フローが定義済み

## 見積もり
- Phase 5（全社アダプタ生成）: 1-2セッション（自動実行中心）
- Phase 6（品質検証・修正）: 1セッション
- Phase 7-8（BQ・運用）: 1セッション
- 難易度: 中〜高（企業ごとのフォーマット差が最大のリスク）

## TODO: GCSパス移行（order_backlog/ → quarterly/） — 移行完了(2026-05-05)

~~2026-05-02にGCSパス構造を `quarterly/` に統一決定（089 MD §GCSパス構成）。~~ 移行完了。

- [x] GCS上のデータを `order_backlog/meta/{ticker}/` → `quarterly/meta/{ticker}/` にコピー（2628 blobs）
- [x] `scripts/validate_backlog_structure.py` の `GCS_META_PREFIX` を `"quarterly/meta"` に変更
- [x] 他スクリプト確認済み（`generate_backlog_structure.py`, `extract_order_backlog.py` はローカルパスのみ、GCS参照なし）
- [ ] 旧パス `order_backlog/` の削除判断（後日）

**旧パス `order_backlog/` は使用厳禁。** 新規コードは必ず `quarterly/` を参照すること。

## 振り返り（作業後に記入）
- 実際の所要時間:
- うまくいった点:
- 改善点:
- 得られた知見:
