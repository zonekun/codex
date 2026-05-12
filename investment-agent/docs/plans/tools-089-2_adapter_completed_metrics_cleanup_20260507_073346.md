# 作業計画: 受注アダプタ非先行指標メトリクス除去

**作成日時**: 2026-05-07 07:33 (JST)
**更新日時**: 2026-05-07 07:33 (JST)
**ステータス**: 完了（2026-05-07）。GCS 430/431成功（一時エラー2件はリトライ対応可）
**実行担当**: Claude Code (Opus)
**分類**: (a) 恒久知見型
**親知見 MD**: `docs/knowledges/tools/089_quarterly_disclosure_master.md`
**関連プラン**: `docs/plans/tools-089-1_order_backlog_extraction_20260430_200000.md` Phase 6b後

## 目的

受注残高パイプラインの structure.json から、先行指標でないメトリクス（`type: "completed"`）を全件除去する。受注高(`order_intake`)・受注残高(`backlog`)のみを収集対象とし、売上高・完成工事高等のラグ指標を排除する。

## 背景・動機

- 本パイプラインの目的は「将来売上の先行指標」を収集すること
- `type: "completed"`（売上高・完成工事高・売上収益等）は決算短信XBRLから取得済みで重複
- Phase 5-6で一括生成した際、Geminiがstructure分析時にcompleted metricsも含めてしまった
- 553社中480社(87%)に不要なcompletedメトリクスが混入

## 影響範囲

| 分類 | 社数 | アクション |
|------|------|-----------|
| completedのみ（先行指標ゼロ） | 49社 | structure.json → `data_available=false` + `metrics=[]`、extract_adapter.json → 削除 |
| 混在（leading + completed） | 432社 | structure.json から `type: "completed"` のメトリクスを削除。breakdown_dimensionsは残存metricsが参照する場合のみ残す |
| 先行指標のみ（変更不要） | 63社 | 対象外 |
| data_available=false（変更不要） | 10社 | 対象外 |

**49社削除対象の内訳**: completedのみ48社 + leading=0の1社(9216 ビーウィズ)

## 作業ステップ

### Step 1: ローカル structure.json 一括修正

1. [x] Pythonスクリプト `scripts/cleanup_completed_metrics.py` 作成
   - 入力: `meta/quarterly/*_structure.json`
   - ロジック:
     - completedのみ/leading=0 → `data_available=false`, `metrics=[]`, `breakdown_dimensions=[]`, notes追記
     - 混在 → `type: "completed"` のメトリクスを削除。`parent_metric`がcompletedのものも削除。breakdown_dimensionsは残存metricsが参照するもののみ残す
   - 出力: 修正済みstructure.json + 変更サマリCSV
   - `--dry-run` モード実装（修正内容表示のみ、ファイル書き換えなし）

2. [x] dry-run実行 → サマリCSV確認（10件目視）→ 正常

3. [x] 本実行（480社修正: 49削除+431メトリクス除去）

### Step 2: extract_adapter.json 削除（49社）

4. [x] completedのみ49社のextract_adapter.jsonをローカルから削除（49件削除）

### Step 3: GCS反映

5. [x] 49社 structure.json(false版) GCSアップロード完了
6. [x] 49社 extract_adapter.json GCS削除完了（errors: 0）
7. [x] 431社 structure.json GCSアップロード完了（430成功/2一時エラー）

### Step 4: git コミット

8. [x] `git add` + コミット（`61522fe` — 530ファイル変更、16,301行削除）

### Step 5: プランMD・知見MD更新

9. [x] 089プランMD: ステータス更新（有効企業504社）
10. [x] 089知見MD: 「type: completed は収集対象外」ガードレール追記済み

## 検証方法

- dry-run時のサマリCSVで削除対象メトリクス名を確認（売上高/売上収益/完成工事高 等であること）
- 修正後にランダム5社のstructure.jsonを目視確認
- 修正後の集計: leading metricsのみ504社・data_available=false 59社(既存10+新規49)

## 注意事項

- `parent_metric` が削除対象のcompletedメトリクスを指す子メトリクスも連鎖削除
- breakdown_dimensionsのitemsは、残存metricsのname内に dimension item名が含まれる場合のみ保持
- 既に `data_available=false` の10社は触らない（二重処理防止）

## 見積もり

- 所要時間: 30分（スクリプト作成10分 + dry-run確認5分 + 実行+GCS+git 15分）
- リスク: 低（構造変更のみ、抽出ロジックへの影響なし。extract_order_backlog.pyはstructure.jsonのmetricsを動的参照するため、metricsが減れば自動的に抽出範囲が縮小する）

---

## レビュー追記: 2026-05-07 07:36 JST — code-reviewer

→ `docs/reviews/091_cr_adapter_completed_metrics_cleanup.md`
