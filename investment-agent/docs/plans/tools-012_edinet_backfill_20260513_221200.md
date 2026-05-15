# 作業計画: EDINET ir_documents_enhanced バックフィル

**作成日時**: 2026-05-13 22:12 (JST)
**ステータス**: ✅ 完了（Phase 1-3 全完了）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/012_edinet_load.md`, `docs/knowledges/tools/009_edinet_download.md`
**関連アイディアID**: -

## 目的

BQ `ir_documents_enhanced` の 2017-2023年カバレッジを充足させ、2026年の欠損期間を埋める。

## 背景・動機

`ir_documents_enhanced`（452万行、2016-2026）は EDINET 有報等のチャンク＋Embeddingを格納するBQテーブル。

### GCS vs BQ カバレッジ比較（2026-05-14 調査）

**重要発見**: GCS には全年のデータが充実しており、Phase 1（DL）は既に完了済み。
BQ ロードが未実施のため、BQ 側のカバレッジのみが低い。

| 年 | GCS files | GCS tickers | BQ files | BQ tickers | BQ カバー率 | 状態 |
|----|-----------|-------------|----------|------------|------------|------|
| 2016 | 9,414 | 2,937 | 9,410 | 2,937 | 99.9% | ✅ OK |
| 2017 | 12,514 | 3,009 | 921 | 427 | 7.4% | **BQロード要** |
| 2018 | 12,985 | 3,100 | 808 | 376 | 6.2% | **BQロード要** |
| 2019 | 12,536 | 3,180 | 722 | 338 | 5.8% | **BQロード要** |
| 2020 | 12,739 | 3,262 | 851 | 387 | 6.7% | **BQロード要** |
| 2021 | 14,570 | 3,386 | 1,945 | 743 | 13.3% | **BQロード要** |
| 2022 | 14,578 | 3,489 | 1,790 | 704 | 12.3% | **BQロード要** |
| 2023 | 14,961 | 3,597 | 1,490 | 625 | 10.0% | **BQロード要** |
| 2024 | 12,369 | 3,669 | 5,665 | 3,378 | 45.8% | **BQロード要** |
| 2025 | 9,610 | 3,738 | 8,905 | 3,621 | 92.7% | ほぼOK |
| 2026 | 1,691 | 1,327 | 366 | 281 | 21.6% | **BQロード要** |
| **合計** | **127,967** | - | **32,873** | - | **25.7%** | **95,094件未ロード** |

**原因（修正）**: GCS へのダウンロードは完了済みだった。BQ へのロード（`edinet_load_parallel.py`）が 2016年分しか実行されていなかった。

**利用目的**:
- 008 EDINET遅延TOBスクリーニング 方向性3（訂正報告書差分解析）: 過去の大量保有変更報告書を時系列で追跡するための基盤
- 007 TOB ML予測モデルの特徴量拡充: 有報テキストからのシグナル抽出
- セマンティック検索: 過去有報の横断検索（現状 2017-2023 が使えない）→ Embedding は後日追加時に有効化

## 投入方針

**段階的拡大**: テストロット → OK確認 → 年単位で拡大。

- **テストロット**: 2026年1月分（GCS→BQ ロードのみ）で E2E を通す
- **拡大判断基準**: BQ 行数が期待値に近いこと、エラー 0、Cloud Run OOM なし

## 作業ステップ

### ~~Phase 1: GCS ダウンロード~~ → 完了確認済み

2026-05-14 の調査で、GCS に全年のデータが既に存在することを確認。
テストロット DL 2回（2026年1月、2023年7月）とも新規 DL 0件で正常終了。

- [x] テストロット DL: 2026年1月（edinet-download-7z45z, 0件 DL, 既存充足）
- [x] テストロット DL: 2023年7月（edinet-download-gm66r, 0件 DL, 既存充足）
- [x] GCS 年別集計: 全年 12K-15K files / 3K+ tickers で充実を確認

> **残存レジュームログ**: 7件残っているが、GCS データは充足しているため実害なし。クリーンアップは Phase 3 で実施。

### Phase 1.5: バックフィル専用 Cloud Run Job 作成

日次ジョブ `edinet-load` と分離し、バックフィル専用ジョブを作成する。

| 項目 | 日次 `edinet-load` | バックフィル `edinet-load-backfill` |
|------|-------------------|-------------------------------------|
| メモリ | 2Gi | 512Mi（Embedding なし） |
| RUN_MODE | full（デフォルト） | backfill（固定） |
| タイムアウト | 36000s | 36000s |
| Docker イメージ | 同一（edinet-load-parallel） | 同一 |

1. [x] `edinet-load-backfill` Cloud Run Job を `gcloud run jobs create` で作成（512Mi/1CPU）
2. [x] smoke test: `--from=20260101,--to=20260131` でテストロット BQ ロード → **成功**
3. [ ] 知見MD `012_edinet_load.md` にバックフィル専用ジョブの記載追加

#### テストロット結果（2026-05-14）

| 項目 | 値 |
|------|-----|
| 実行ID | edinet-load-backfill-xmvdj |
| 対象期間 | 2026-01-01 〜 2026-01-31 |
| GCS スキャン | 248 files |
| BQ ロード | 131 docs / 21,136 chunks |
| エラー | 0 |
| 所要時間 | ~3 分 |
| メモリ | 512Mi（OOM なし） |

**所見**: 512Mi で十分稼働。Embedding スキップにより高速。本番投入可。

#### コードレビュー（CR-172）結果

テストロット前にコードレビュー実施（C 評価）。5件のバグを修正:
1. ticker 範囲スキャン: `prefix=` → `start_offset`/`end_offset` 方式
2. 日付なし blob フィルタ: else ブランチ追加
3. doc.text メモリ解放: チャンク後に `doc.text = ""` 
4. genai_client 遅延初期化: backfill モードでの不要な API 初期化を回避
5. print() 違反: 見送り（structlog 移行は別タスク）

#### ビルド改善（MR-171）

Cloud Build コンテキスト肥大（3,216 files / 20.5MB）→ temp build dir 方式で 2 files / 47KB に削減。

### Phase 2: BQ ロード（edinet-load-backfill）

GCS 既存データを年単位で BQ ロード。Embedding スキップ、1コマンドで完結。

**2025年分布ベースの月別ファイル数参考値**:
- 軽量月（1,4,7,9,12月）: 228-319件/月
- 中量月（2,5,10月）: 349-497件/月
- 重量月（3,8月）: 680-709件/月
- 超集中月（6,11月）: 2,472-2,490件/月

**ロット分割**（年×4ロット、Cloud Run 5h timeout 内に収まる）:

| ロット | 対象月 | 推定件数 | 推定時間 |
|--------|--------|----------|----------|
| A | 1-5月 | ~2,000 | ~2.3h |
| B | 6月 | ~2,500 | ~2.8h |
| C | 7-10月 | ~1,600 | ~1.8h |
| D | 11-12月 | ~2,800 | ~3.1h |

**投入順**: 直近→過去（2026→2024→2023→...→2017）

4. [x] テストロット BQ ロード結果検証 → 131 docs / 0 エラー（上記テストロット結果参照）
5. [x] 2026年 BQ ロード — h86x5: 1,099docs / 750BQ / 0err / 20min + xmvdj: 248docs / 131BQ / 0err / 3min
6. [x] 2024年 BQ ロード — 5nrfk: 6,704docs / 2,890BQ / 0err / 98min
7. [x] 2023年 BQ ロード — kgnkn: 13,474docs / 3,752BQ / 0err / 171min
8. [x] 2022年 BQ ロード — pgpn2: 12,789docs / 3,565BQ / 0err / 161min
9. [x] 2021年 BQ ロード — 6v5cg: 12,625docs / 3,908BQ / 0err / 170min
10. [x] 2020年 BQ ロード — w294l: 11,890docs / 2,714BQ / 0err / 151min
11. [x] 2019年 BQ ロード — gt4nv: 11,814docs / 2,784BQ / 0err / 151min
12. [x] 2018年 BQ ロード — 2qp8z: 12,177docs / 3,518BQ / 0err / 161min
13. [x] 2017年 BQ ロード — ldkx2: 11,593docs / 3,312BQ / 0err / 156min
14. [x] 2025年 BQ ロード補完 — vkgp6: 554docs / 551BQ / 0err / 5min

### Phase 3: 検証・クリーンアップ

15. [x] 年別カバレッジ再集計 — 8,394,782行。全年 2,400-3,600+ tickers
16. [x] GCS レジュームログ 7件削除完了
17. [x] 2026年 日次 DL ジョブ（`edinet-download-daily`）の稼働状態確認 — ENABLED, 直近3日 succeeded=1
18. [x] data_catalog `gcs.md` の記述更新

## 実行コマンドテンプレート

### バックフィル専用ジョブ作成（初回のみ）

```bash
gcloud run jobs create edinet-load-backfill \
    --region us-west1 \
    --image us-west1-docker.pkg.dev/gmailpj-357912/cloud-run-source-deploy/edinet-load-parallel:latest \
    --memory 1Gi \
    --cpu 1 \
    --task-timeout 36000s \
    --max-retries 0 \
    --set-env-vars RUN_MODE=backfill,PYTHONUTF8=1,PYTHONUNBUFFERED=1 \
    --service-account investment-agent@gmailpj-357912.iam.gserviceaccount.com
```

### BQ ロード（edinet-load-backfill）

```bash
# テストロット（2026年1月）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20260101,--to=20260131"

# 年×ロットA（1-5月、例: 2023年）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20230101,--to=20230531"

# 年×ロットB（6月、例: 2023年）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20230601,--to=20230630"

# 年×ロットC（7-10月、例: 2023年）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20230701,--to=20231031"

# 年×ロットD（11-12月、例: 2023年）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20231101,--to=20231231"

# ticker 範囲指定（GCS走査の高速化）
gcloud run jobs execute edinet-load-backfill --region us-west1 \
    --args="--from=20230101,--to=20230531,--ticker-from=1301,--ticker-to=4999"
```

> `RUN_MODE=backfill` はジョブ固定。実行時の `--update-env-vars` 不要。
> Cloud Functions ポーラーも不要（Embedding 待ちがないため）。

### GCS ダウンロード（参考: 追加DLが必要な場合のみ）

```bash
gcloud run jobs execute edinet-download --region us-west1 \
    --update-env-vars EDINET_PRODUCTION=true \
    --args="--from=20230101,--to=20230131"
```

## 必要データ

| データ | ストレージ層 | パス/テーブル |
|--------|------------|--------------|
| EDINET 有報 HTML | (b) GCS | `gs://stock_data_1930932/edinet/{ticker}/` |
| ir_documents_enhanced | (a) BQ | `gmailpj-357912.STOCK.ir_documents_enhanced` |
| レジュームログ | (b) GCS | `gs://stock_data_1930932/edinet/_resume_*.txt` |
| BQロードログ | (b) GCS | `gs://stock_data_1930932/edinet/bq_load_*.txt` |

## 制約・リスク

| リスク | 対策 |
|--------|------|
| Cloud Run OOM | Load Job（GCS経由）+ numpy float32 で対策済み。backfill は 1Gi で十分 |
| BQ 重複 | DELETE→INSERT 冪等パターンで対策済み（リファクタで実装） |
| 長時間ロット | 年×4ロット分割で各ロット 3h 以内。36000s timeout 内に収まる |

> **Embedding コスト**: backfill モードでスキップのため **$0**。需要発生時に後日追加。

## 成果物

- BQ `ir_documents_enhanced`: 全年 3,000+ 銘柄カバレッジに到達
- 2026年 1-3月 BQ ロード完了
- `docs/data_catalog/gcs.md` の記述更新
- 本知見MD（`012_edinet_load.md`）のバックフィル実績追記

## 完了条件

- 全年の BQ 銘柄カバレッジが 2,500+ tickers（GCS カバレッジと同等）
- 2026年の BQ データが 1/1 から連続して存在
- レジュームログ（`_resume_*.txt`）が全てクリーンアップされている

## 見積もり

- ~~想定所要時間: DL 420h~~ → **DL 不要**（GCS 完了確認済み）
- ~~BQ ロード: 年あたり ~10h × 9年（2017-2025）= ~90h Cloud Run 時間~~ → 実績: 全10年合計 ~21h
- 難易度: 中（スクリプトはリファクタ済み。オペレーション量が大きい）
- コスト: Embedding スキップのため **$0**（Cloud Run 実行費のみ）

## 振り返り（作業後に記入）
- 実際の所要時間: テストロット完了まで約4時間（ビルド事故対応・コードレビュー・監視事故対応含む）
- うまくいった点: Phase 1 の GCS 調査で DL 不要を早期発見 → 420h 削減
- うまくいった点: テストロット前のコードレビューで5件のバグを事前修正 → 本番障害を回避
- 改善点: 初期カバレッジ分析時に GCS と BQ を個別に調べるべきだった
- 改善点: Cloud Build コンテキストは temp dir で構成すべき（MR-171）
- 改善点: ジョブ投入後の監視を怠った（MR-174）。093 に監視定義を追記して再発防止
- 得られた知見: BQ カバレッジの低さ ≠ GCS カバレッジの低さ。必ず両方を調査する
- 得られた知見: 512Mi で Embedding なし BQ ロードは十分動作する（1Gi 不要）
- 改善点: Phase 1 で全 HTML をメモリに載せる設計が OOM の根本原因（MR-176）。ストリーミングバッチ（50件/batch）に改修で解決
- 得られた知見: 年単位一括投入（ロット分割不要）で安定稼働。全年 ~75-82 docs/min の安定スループット
- 実際の BQ ロード所要時間: 全10年合計 ~21h（2017-2026）。エラー 0件

### Phase 2 実績サマリー（2026-05-14 〜 2026-05-15）

| Year | Execution | Docs | BQ Loaded | Errors | Duration |
|------|-----------|------|-----------|--------|----------|
| 2026 (Jan) | xmvdj | 248 | 131 | 0 | ~3min |
| 2026 (Feb-May) | h86x5 | 1,099 | ~750 | 0 | ~20min |
| 2024 | 5nrfk | 6,704 | 2,890 | 0 | 98min |
| 2023 | kgnkn | 13,474 | 3,752 | 0 | 171min |
| 2022 | pgpn2 | 12,789 | 3,565 | 0 | 161min |
| 2021 | 6v5cg | 12,625 | 3,908 | 0 | 170min |
| 2020 | w294l | 11,890 | 2,714 | 0 | 151min |
| 2019 | gt4nv | 11,814 | 2,784 | 0 | 151min |
| 2018 | 2qp8z | 12,177 | 3,518 | 0 | 161min |
| 2017 | ldkx2 | 11,593 | 3,312 | 0 | 156min |
| 2025 | vkgp6 | 554 | 551 | 0 | ~5min |
| **合計** | — | **94,967** | **26,875** | **0** | **~21h** |
