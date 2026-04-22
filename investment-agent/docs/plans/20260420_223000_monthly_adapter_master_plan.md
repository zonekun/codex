# 月次開示アダプタプログラム改修 マスタープラン

**作成日**: 2026-04-20
**ステータス**: 進行中
**目的**: 月次開示パイプライン（adapter / extractor / download_monthly / BC 突合）の設計・実装を一元管理し、改修プランを歴史的系列で追跡可能にする

**関連知見 MD**:
- `docs/knowledges/tools/042_monthly_disclosure_master.md` — パイプライン全体概要（マスタ）
- `docs/knowledges/tools/042-1_bc_match_agent.md` — BC 突合 NG 修復エージェント（子 MD）
- `docs/knowledges/tools/004_coding_conventions.md` — 内容種別検証ルール（2026-04-20 追加）
- `docs/knowledges/tools/055_extract_adapter_design_patterns.md` — extract_adapter 設計パターン

---

## ゴール

1. **BC 月次 KPI との突合一致率を最大化する**（現在は大半 100%、一部未対応）
2. **月次開示 URL 自動発見の精度向上**（141 銘柄 DL 失敗問題の解決）
3. **adapter ファイル構造の整理**（#A 物理分離完了 = URL / extract 両者のファイル名分離）
4. **再現性の高いパイプライン運用**（フェーズ毎 commit/push、知見 MD 常時最新化）

---

## 関連プラン MD（時系列・作業系列）

### Phase 1: 初期 NG 調査 + 修正ラウンド（2026-04 前半）

| 日付 | プラン | 内容 |
|---|---|---|
| 2026-04-04 | [`20260404_monthly_pipeline_ng73.md`](./20260404_monthly_pipeline_ng73.md) | 一致率 16.8% → 64.3% へ改善する **NG73社** 画像確認・修正計画 |
| 2026-04-04 | [`ng73_analysis_detail.md`](./ng73_analysis_detail.md) | NG73 分析詳細 |
| 2026-04-05 | [`20260405_ng84_investigation.md`](./20260405_ng84_investigation.md) | NG84社 1社ずつ PDF 逆引き調査、パターン分類、修正優先度整理 |
| 2026-04-06 | [`20260406_ng78_cloudrun_6a6b.md`](./20260406_ng78_cloudrun_6a6b.md) | NG78 に対する Cloud Run 6a/6b (build_extractor + extract_data) 適用 |
| 2026-04-08 | [`20260408_adapter_fix_and_gemini3.md`](./20260408_adapter_fix_and_gemini3.md) | adapter 修正 + Gemini 3 導入 |
| 2026-04-08 | [`20260408_download_bc_kpi_nodriver.md`](./20260408_download_bc_kpi_nodriver.md) | BC 月次 KPI DL を nodriver に切替 |

### Phase 2: 一致率 90% 到達 + Round 2 フォローアップ（2026-04 中盤）

| 日付 | プラン | 内容 |
|---|---|---|
| 2026-04-09 | [`20260409_monthly_pipeline_90pct.md`](./20260409_monthly_pipeline_90pct.md) | 一致率 90% 到達プラン |
| 2026-04-09 | [`20260409_ng12_investigation_result.md`](./20260409_ng12_investigation_result.md) | 残 NG12社 の投資結果 |
| 2026-04-09 | [`20260409_ng21_investigation_result.md`](./20260409_ng21_investigation_result.md) | NG21 調査結果 |
| 2026-04-18 | [`20260418_monthly_bc_round2_followup.md`](./20260418_monthly_bc_round2_followup.md) | extract-monthly-data Round 2 完了後のアドホック対応集約 |

### Phase 3: BC Match Agent 自律修復 + 大規模精緻化（2026-04 後半）

| 日付 | プラン | 内容 |
|---|---|---|
| 2026-04-19 | [`20260419_bc_match_agent.md`](./20260419_bc_match_agent.md) | BC Match Agent 仕様書（Phase 1 着手予定 → 完了、042-1 へ昇格） |
| 2026-04-20 | [`20260420_220000_session_log.md`](./20260420_220000_session_log.md) | **本日のセッション記録**: 6社修復 98.3%、7345 100%、update_monthly_adapters.py 12項目精緻化、sync 事故＋復旧、#A 物理分離 |

---

## 本日 (2026-04-20) のアーキテクチャ確定事項

### 1. ファイルマッピング絶対表（#A 物理分離 2026-04-20 適用後）

`docs/knowledges/tools/042_monthly_disclosure_master.md` の先頭「⚠️ ファイルマッピング絶対表」参照。

**要点**:
- ローカル `data/monthly_adapters/{ticker}.json` = **extract adapter**
- GCS `monthly/meta/{ticker}/extract_adapter.json` = **extract adapter** (ローカルと同一)
- GCS `monthly/meta/{ticker}/url_adapter.json` = **URL adapter** (新設、旧 `adapter.json` からリネーム)

**廃止パス**:
- `monthly/meta/{ticker}/adapter.json` → `url_adapter.json` (URL型) / `extract_adapter.json` (extract型) に物理分離
- `monthly/meta/{ticker}/ir_url.json` → `url_adapter.json` に統合（legacy）
- `monthly/meta/{ticker}/download_adapter.json` → `url_adapter.json` に統合（legacy）
- `monthlyir/{ticker}/*.{pdf,xlsx,html}` → `monthly/docs/{ticker}/` に移行済（2026-04-05 以前の旧パス、2026-04-20 物理削除）

### 2. 運用ルール

- **ファイル同期・コピー・上書き時は内容種別検証を必須**（`004_coding_conventions.md`）
- **破壊的操作は必ず dry-run 先行** (`CLAUDE.md`)
- **adapter.json への書き込み禁止**（廃止済、url_adapter.json / extract_adapter.json を使用）

---

## 未完了タスク（本日時点）

### A. #A 物理分離の残作業（横道、直近完了予定）

- **Step C**: コード 5 本の `adapter.json` 参照を `url_adapter.json` に置換
  - `download_monthly.py`
  - `update_monthly_adapters.py`
  - `build_monthly_extractor.py`
  - `build_adapter_index.py`
  - `extract_monthly_data.py`
- **Step E**: 旧 GCS パス削除
  - `monthly/meta/{ticker}/adapter.json` (516 件、url_adapter.json へコピー済み 293 + extract 型 172 + other 51)
  - `monthly/meta/{ticker}/ir_url.json` (324 件、legacy)
  - `monthly/meta/{ticker}/download_adapter.json` (318 件、legacy)

### B. 141 銘柄 DL 失敗の根本解決（本筋）

**現状**: GCS に adapter はあるが PDF 生成ゼロ or 少数（`data/logs/download_failures_20260420.csv` 141 行）

**原因**: adapter の URL / link_pattern が不適切、または対象ページが実データを持っていない

**対応方針**:
1. 改良版 `update_monthly_adapters.py` (C-1 バグ修正済) で再スクレイプ → 大半修復見込み
2. 残った NG をマニュアル調査（042-1 RUNBOOK に沿って）
3. TDnet 経由で解決可能な銘柄は移行 (`setup_tdnet_4_bg.py` パターン)

### C. Cloud Run 本番適用

- `#A Step C` 完了後、Cloud Run Job のコンテナイメージ再ビルド・デプロイ
  - `download-monthly`
  - `update-monthly-adapters`

### E. Document AI PoC（オプショナル、Gemini 差し替え候補）

**記録のみ未着手**: `docs/plans/20260421_094500_document_ai_poc_todo.md`

- Google Cloud Document AI を Gemini 経路の代替候補として検証する計画
- 着手判断は PDF 処理戦略 (D セクション) が完了し NG 残存時 or Gemini コスト問題化時

### D. PDF 処理戦略の導入（062 MD 由来）

`docs/knowledges/tools/062_pdf_processing_strategy.md` の方針を月次パイプラインにも適用する。

- **D-1 (優先度: M, コスト: S)** OCRmyPDF の採用検討
  - 現状: `pytesseract` 直接使用 (3034 クオールHD 等、画像PDF で成功実績あり)
  - 改修案: OCRmyPDF で検索可能 PDF に戻せば、以降は pdfplumber/PyMuPDF で処理可能
  - パイプライン簡素化 + pdfplumber の表構造保持メリット享受
  - PoC: 画像PDF 1-2 社 (3034 等) で精度・速度比較
- **D-2 (優先度: M, コスト: M)** LLM 3段ルーティング明文化
  - 現状: `extract_monthly_data.py` の Gemini フォールバックは `gemini-2.5-flash` 一択 (一部 `gemini-3-flash-preview`)
  - 改修案: ① regex → ② Gemini Flash (速い・安い) → ③ Gemini Pro / Claude PDF (精度重視) の 3段化
  - コスト最適化 (月次 500社 × 多数PDF の大量処理で効果大)
  - 062 MD §2 のパターンをそのまま採用

### E. Google 検索 DOM 変更対応（2026-04-21 完了）

**背景**: Google は 2025-09 以降 DOM を変更し、単純な `<a href>` 直取りでは検索結果が
取れなくなった。`_extract_google_links` の 2 段戦略（h3 親 a / 全 a 走査）+ `udm=14`
パラメータ（案 A）を追加したが、curl_cffi / Playwright 経路ともに実効ヒットが
ほぼ 0 となっていた。

**実装**:

- **案 B (完了): 公開 SearXNG HTTP 直叩き** (2026-04-21 commit `d0e9eb0` + `a6f56e1`)
  - `scripts/update_monthly_adapters.py` に `_searxng_search()` 追加
  - `_SEARXNG_INSTANCES` (7 インスタンス) を順に叩き、最初に JSON 応答が
    得られたものをそのまま返す
  - 検索フロー: curl_cffi Google → Playwright Google → **SearXNG** → curl_cffi Yahoo → Playwright Yahoo
  - search_source_breakdown で `searxng` カウントが自動集計される

**34 (+1 active 含む 35) 銘柄 retry 結果** (2026-04-21 13:05):

| 区分 | 件数 | 備考 |
|---|---|---|
| active 化 | **4社** | 2769 ヴィレッジヴァンガード / 4350 メディカルシステムネットワーク / 7088 フォーラムエンジニアリング / 9828 Genki |
| no_links_confirmed | 21社 | 検索はヒットしたが適切な URL 無し |
| エラー | 10社 | PDF 直リンク (Download start) や ERR_NAME_NOT_RESOLVED |
| search_source_breakdown | google_cffi=1 / yahoo_cffi=12 / yahoo_pw=20 / n/a=2 / **searxng=0** | Yahoo が広範に機能しており SearXNG まで到達せず |

**ログ**: `data/logs/update_adapters_retry35_searxng_20260421.log`

**既知制約と今後**:

- 公開 SearXNG はレート制限が厳しく (429)、短時間に連続ヒットで全インスタンス落ちる。
  本番 Cloud Run では IP が毎回新鮮なので問題は小さい想定
- 今回 retry では Yahoo がほぼ全クエリで応答したため SearXNG の出番なし。Yahoo が
  長期停止した場合の保険として有効
- 残 21 no_links_confirmed + 10 エラーは案 A/B の検索系問題ではなく、
  「適切な IR 月次 URL がそもそも当該サイトに存在しない / PDF 直リンクで Playwright が
  goto できない」等、別種の問題。TDnet 経由移行 (setup_tdnet_4_bg.py パターン)
  または手動調査に送る

---

## クラッシュリカバリ時の指示

1. **本 MD を最初に読む** (プランMD索引)
2. セッションログの最新: [`20260420_220000_session_log.md`](./20260420_220000_session_log.md) を読む
3. BG 状況確認: `data/logs/` の最新ログ
4. git log で push 済 commit を確認
5. `#A Step C` の改修は慎重に（間違えると DL 全停止）
6. その後、本 MD「未完了タスク」セクションに従って作業継続

---

## 更新ルール

本 MD は**月次開示アダプタ改修の入口索引**として常時最新化する:
- 新規プラン MD 作成時は「関連プラン MD」表に追記
- Phase 切替時は本 MD に結果サマリを追加
- 大規模アーキ変更時は「アーキテクチャ確定事項」を更新
