# 作業計画: 月次開示KPI × 四半期PL 相関分析（Step1）

**作成日時**: 2026-05-09 13:52 (JST)
**更新日時**: 2026-05-09 14:33 (JST) — Web調査結果反映・探索方針追加・関連レビュー追記
**ステータス**: 進行中
**分類**: (a) 恒久知見型
**親知見 MD**: `docs/knowledges/analysis/012_monthly_disclosure_earnings_screening.md`
**関連アイディアID**: 012

## 目的

各社の月次開示KPIと四半期決算実績（売上高・営業利益）の相関を測定し、「月次KPIが決算を説明できる会社」と「できない会社」を選別する。Step2（株価織り込み度分析）の前提となるフィルタリング工程。

## 背景・動機

- 月次開示データの収集基盤は503社分が稼働済み（[042_monthly_disclosure_master.md](../knowledges/tools/042_monthly_disclosure_master.md)）
- しかし収集したデータの分析・活用は未着手
- 042知見MDの「最終目標」（月次開示→決算予測→未織り込み銘柄を買う）を実現する第一歩
- 経常利益・EPSは特損等の特殊要因が入るため除外。売上高・営業利益に絞る

### Web調査で得た投資家視点（2026-05-09追加）

- **投資家の重視順位**: ①既存店売上YoY ②全店売上YoY ③客数YoY ④客単価YoY
- **決算予測の基本手法**: 全店売上YoYの3ヶ月平均≒四半期売上成長率（月次累計→決算予測→株価判断）
- **織り込みの実態**: 月次開示直後に一定反映されるが、小型株・低流動性銘柄では不完全。機関投資家のカバー外が狙い目
- **見落とし**: 客数増+単価減パターンは売上維持でも営業利益悪化。売上×営業利益の両面チェックが必要

### 分析方針: 仮説段階 → 網羅的探索

**現段階は仮説の検証段階**であり、特定の指標に絞り込むのではなく、利用可能なKPIを網羅的にテストして「何が効くか」を発見する探索的アプローチを取る。

- 各社が持つ**全KPI**をPLと突合する（上位KPIだけでなく会社固有のマイナーKPIも）
- 相関係数だけでなく、複数の統計量（ピアソン・スピアマン・順位一致率）で評価
- 「意外なKPIが効く」発見も重要な成果（例: 店舗数変化率が営業利益と相関する等）
- 閾値は暫定設定し、結果分布を見てから調整

## ファイル・フォルダ命名規約

分析関連ファイルが散在しないよう、以下の規約で統一する。

```
scripts/012_monthly_screening/           ← スクリプト群（分析ID=012で固定）
  ├── download_monthly_records.py        ← Step0: GCS→ローカル一括DL
  ├── correlate_kpi_pl.py                ← Step2: KPI×PL相関分析（全KPI網羅）
  └── (今後追加するStep3以降もここ)

data/012_monthly_screening/              ← 中間データ・分析結果
  ├── monthly_records/                   ← DL済みJSON（{ticker}_monthly_records.json）
  ├── kpi_pl_correlation.csv             ← 相関分析結果（全組合せ）
  └── (今後追加する中間成果物もここ)
```

**命名ルール**:
- ディレクトリ名: `012_monthly_screening` で統一（アイデアID + slug）
- スクリプト: 機能を表す動詞始まり（`download_`, `correlate_`, `screen_`等）
- 中間CSV: 内容を表す名詞（`kpi_pl_correlation.csv`等）

## 作業ステップ

### Step 0: 月次レコード一括ダウンロード
1. [ ] GCS `monthly/record/{ticker}/monthly_records.json` を全ticker分ローカルDL
2. [ ] 保存先: `data/012_monthly_screening/monthly_records/{ticker}_monthly_records.json`
3. [ ] DL件数・レコード件数のサマリ出力

### Step 1: KPI候補の仮説整理 ✅ 完了（2026-05-09）
1. [x] AI知見 + Web調査（日経記事、外食月次速報等）でセクター別KPI仮説整理
   - 投資家視点: ①既存店売上YoY ②全店売上YoY ③客数YoY ④客単価YoY
   - 小売(216社): コア3指標（既存店売上/客数/客単価YoY）
   - 陸運(23社): 運輸収入YoY、輸送人員YoY
   - 輸送用機器(7社): 生産/販売台数YoY
   - 建設(11社): 受注額YoY（ラグ大）
2. [x] 503社のfield keyを集計 → 1,228種のユニークKPI、前年同月比保有375社
3. [x] セクター別仮説と実際のfield keyのマッピング完了 → 012知見MDに記録

### Step 2: KPI × PL 相関分析（網羅的探索）
1. [ ] 月次KPIの四半期累積値を算出
   - 各社の決算月（fiscal year end）をBQ `fin_summary` から特定
   - 四半期に含まれる月のKPIを集約:
     - 前年同月比KPI → 3ヶ月の単純平均
     - 絶対額KPI → 3ヶ月の合計
2. [ ] 四半期決算実績（売上高・営業利益）をBQから取得
   - テーブル: `STOCK.v_fin_summary_actual_for_q_on_q`（単独四半期P&Lビュー）
   - カラム: `NET_SALES`, `OPERATING_PROFIT`
   - 連結優先（`Consolidated`）、連結なしは単体
3. [ ] **全KPIを網羅的にテスト**
   - 各社が保有する**全field key**について相関を算出（上位KPIに限定しない）
   - 統計量: ピアソン相関、スピアマン順位相関、符号一致率（月次増→PL増の割合）
   - PL目的変数: ①売上高YoY ②営業利益YoY の2本
   - 最低4四半期分のペアがある組合せのみ対象
4. [ ] 結果をCSV出力: `data/012_monthly_screening/kpi_pl_correlation.csv`
   - カラム: ticker, sector, kpi_key, target(sales/op), n_quarters, pearson_r, pearson_p, spearman_r, spearman_p, sign_match_rate

### Step 3: 選別・レポート
1. [ ] 相関分布の全体像を把握（閾値は結果を見て設定）
2. [ ] セクター別・KPIカテゴリ別の相関分布を可視化（JupyterLabノートブック）
3. [ ] 「意外なKPIが効く」ケースも含めて発見を記録
4. [ ] 012知見MDに結果を追記（相関ランキング、セクター傾向、意外な発見）

## 必要データ

| データ | ストレージ層 | パス/テーブル | 状態 |
|--------|------------|--------------|------|
| 月次レコード | (b) GCS | `monthly/record/{ticker}/monthly_records.json` | ✅ 収集済み |
| 四半期PL（単独Q） | (a) BQ | `STOCK.v_fin_summary_actual_for_q_on_q` | ✅ 利用可能 |
| 銘柄マスタ（業種・決算月） | (a) BQ | `STOCK.STOCK_CODE_LIST` | ✅ 利用可能 |
| extract adapter定義 | (c) ローカル | `meta/monthly/{ticker}_extract_adapter.json` | ✅ 503社 |

## 成果物

1. **スクリプト**: `scripts/012_monthly_screening/download_monthly_records.py`, `correlate_kpi_pl.py`
2. **相関分析CSV**: `data/012_monthly_screening/kpi_pl_correlation.csv`（全社×全KPI×全統計量）
3. **知見MD更新**: `docs/knowledges/analysis/012_monthly_disclosure_earnings_screening.md` に結果追記
4. **ノートブック**: 相関分布の可視化（JupyterLab）

## 完了条件

- 月次レコード保有全社×全KPIについて相関係数が算出されている（KPIを絞らず網羅的）
- 相関分布の全体像が把握されている（どの程度の会社/KPIで有意な相関が出るか）
- 発見（高相関KPI、意外なパターン等）が012知見MDに記録されている

## 見積もり
- 想定所要時間: 3-4時間
- 難易度: 中（データ結合の複雑さ: 非標準KPI × 決算期対応付け）

## 関連ドキュメント

- 親知見: `docs/knowledges/analysis/012_monthly_disclosure_earnings_screening.md`
- 月次パイプライン: `docs/knowledges/tools/042_monthly_disclosure_master.md`
- fin_summary スキーマ: `docs/data_catalog/bq_fin_summary.md`
- 統計分析手法: `skills/idea_pipeline.md` Part 4
- コーディング規約: `docs/knowledges/tools/004_coding_conventions.md`
- 事故レビュー: `docs/reviews/134_mr_web_research_skip.md`（MR-134: Web調査スキップ命令違反）
- 事故レビュー: `docs/reviews/136_mr_ntfy_wait_flag_miss.md`（MR-136: LINE双方向モード--wait漏れ）
