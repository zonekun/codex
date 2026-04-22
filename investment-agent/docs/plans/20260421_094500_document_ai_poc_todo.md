# Document AI PoC — Gemini アダプタ差し替え候補（残タスク記録）

**作成日時**: 2026-04-21 09:45 JST
**ステータス**: **未着手・記録のみ**（別途判断）
**対象読者**: 次セッション担当 / PoC 実施判断者
**目的**: Google Cloud Document AI を月次開示 PDF 処理の Gemini 経路の代替候補として調査・検証する計画の記録。本 MD は実装に進まず、**要否判断時に参照する TODO** として残す

---

## 背景

現状の `scripts/extract_monthly_data.py` では、regex 抽出が失敗した PDF に対して Gemini (2.5 Flash / 3 Flash Preview) を fallback として呼び出している (`extraction_method: "gemini"`)。

**課題**:
- Gemini は汎用 LLM のため、**表構造の厳密な数値読み取り**には不向きなケースがある（行/列の取り違え、複数行合算の誤り）
- コストが 1 PDF あたり数円〜数十円（500社 × 月次で 1 run 数千〜数万円オーダー）
- 月次開示 PDF は典型的な「フォーム／表」構造を持つ → **Document AI (Form Parser / Table Extractor) の方が適している可能性**

## Document AI とは

- Google Cloud の AI サービス: https://cloud.google.com/document-ai
- 特徴:
  - **Form Parser**: フォーム構造（key-value）を抽出
  - **Layout Parser / OCR Processor**: レイアウトを保持しながらテキスト化
  - **Custom Extractor**: カスタム学習で特定フォーマットに特化（追加コストあり）
  - 非構造化 PDF → 構造化 JSON の変換に最適化
- コスト（2026-04 時点、要確認）:
  - Document OCR: $1.50 / 1,000 ページ
  - Form Parser: $30 / 1,000 ページ
  - Layout Parser: $10 / 1,000 ページ

## PoC スコープ（起票時の想定）

### ゴール

Document AI (Layout Parser or Form Parser) が月次開示 PDF の数値抽出で Gemini 以上の精度を出せるか、月次 500社 × 年 6,000 PDF 処理で Gemini とコスト的に勝負になるかを実測する。

### PoC 対象

1. **NG 銘柄 5 社**: 現状 Gemini 経路でも 0 件 or 一部 NG の銘柄
   - 候補: `data/logs/download_failures_20260420.csv` の NG 全件 / `bc_ignore` された銘柄 / 難易度高いと判明している銘柄
2. **OK 銘柄 5 社**: 現状 Gemini 経路で 100% 取れている銘柄（回帰確認用）
3. **画像 PDF 銘柄 2 社**: 3034 クオールHD + 他 1 社（OCRmyPDF PoC と連携）

### 比較指標

| 観点 | 指標 |
|---|---|
| 精度 | 正解ラベル (BC KPI) との一致率 |
| 所要時間 | 1 PDF あたりの処理時間 |
| コスト | 1 PDF あたりの API 課金 |
| 構造保持 | 出力が「どの表のどのセルから拾った値か」追跡可能か |
| 月次量対応 | 500社 × 月次 × 12 KPI で月額試算 |

### 判定基準（PoC 結果の採否）

- **採用**: (a) 精度が Gemini 以上 かつ (b) コストが Gemini の 2 倍以内 または (c) 特定構造（罫線表）で劇的改善がある
- **見送り**: 精度 同等以下 かつ コスト増

## 実装計画（起票のみ、未着手）

### Phase 1: API 有効化 + ハロワ

1. GCP プロジェクト `gmailpj-357912` で Document AI API を有効化（要 IAM 権限確認）
2. サービスアカウント `bq-loader@...` に `roles/documentai.apiUser` 付与
3. ハロワスクリプト `scripts/experiments/document_ai_smoke.py`
   - PDF 1 件を Document OCR Processor に送って JSON 応答を取得
   - 所要時間・レスポンス構造を確認

### Phase 2: 比較 PoC

1. `scripts/experiments/document_ai_vs_gemini.py`
2. 対象: 上記「PoC 対象」12 社分の PDF
3. 処理:
   - 各 PDF を Document AI (Form Parser) で抽出
   - 同じ PDF を既存 `_extract_pdf_gemini_personal` (Gemini) で抽出
   - BC KPI と突合して一致率計算
4. 結果を `C:\tmp\document_ai_poc_results.csv` に保存
5. 本 MD「PoC 結果」セクションに要約追記

### Phase 3: 採用判断 → 本実装 or 見送り

**採用時の本実装**:
- `scripts/extract_monthly_data.py` に `_extract_pdf_document_ai()` を追加
- `extract_adapter.json` に `extraction_method: "document_ai"` を許容
- 難易度高い銘柄のみ `document_ai` に振り替え（全銘柄デフォルトではない）
- Cloud Run Dockerfile 更新（`google-cloud-documentai` 追加）

**見送り時**:
- 本 MD に「見送り理由」記載してクローズ

## 依存・前提

- **Google Cloud 権限**: Document AI API 有効化 + IAM 付与（事前確認要）
- **コスト予算**: PoC で 12 PDF × 3 Processor 種試行 = 36 回 API 呼び出し。数十円レベルで収まる
- **別プランとの関係**: `20260421_093823_monthly_pdf_strategy_integration.md` (P0-1/P1-1/P1-2/P2-1) とは独立。競合なし
- **採用時の波及**: Gemini ベースの既存 `custom_prompt` 運用（7378 等）と互換が取れないため、Document AI 採用銘柄は extract_adapter.json で明示分離

## リスク・留意点

| リスク | 対処 |
|---|---|
| Document AI が月次開示の日本語 PDF に最適化されていない可能性 | Phase 1 ハロワで 1 PDF を試して応答の質を見る。NG ならここで見送り判断 |
| Form Parser のスキーマが月次 KPI と合わない | Custom Extractor 学習が必要 → PoC 範囲拡大 or 見送り |
| コスト予想より高い | 月次 500社 × 月次 × 30 日 = 月 15,000 PDF 超、$30/1000 pages = $450/month で Gemini Flash より高くなる可能性。実測で見極め |
| IAM 権限追加が必要 | サービスアカウントへの付与はユーザー判断 |
| Cloud Run 移行時の依存増加 | PoC は local のみ、本実装時に Dockerfile 改修 |

## 実施判断の目安

### PoC 着手するタイミング

以下のいずれかに該当したら着手検討:
- `20260421_093823_monthly_pdf_strategy_integration.md` の P0-P2 が全完了し、かつ NG 銘柄がまだ残存する
- Gemini コストが想定を超えて問題化
- 新規で表構造が複雑な銘柄が大量発生

### 着手しない場合

以下なら見送り継続:
- 062 MD の 4 項目 (本セッション実装中) で一致率が十分改善
- Gemini 3 の精度向上で追加 layer 不要と判断

## PoC 結果（未実施）

（未着手）

---

## 関連ドキュメント

- **親プラン**: `docs/plans/20260420_223000_monthly_adapter_master_plan.md`
- **兄弟プラン** (PoC 対象と近接): `docs/plans/20260421_093823_monthly_pdf_strategy_integration.md` (OCRmyPDF / LAParams / 品質チェック)
- **関連知見**: `docs/knowledges/tools/062_pdf_processing_strategy.md` (PDF 処理戦略), `docs/knowledges/tools/042_monthly_disclosure_master.md`
- **Google Cloud Document AI 公式**: https://cloud.google.com/document-ai/docs
