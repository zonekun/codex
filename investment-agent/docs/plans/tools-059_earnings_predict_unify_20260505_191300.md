# 決算反応予測モデル CLI統一（ノートブック+バッチ→1本化）

**作成日時**: 2026-05-05 19:13 JST
**ステータス**: 完了（Phase 1-4 全完了、バックテストのみデータ蓄積待ち）
**分類**: (b) 継続改修型
**親知見 MD**: `docs/knowledges/tools/059_earnings_model_eda.md`
**基準 commit**: `1134848`

## 目的

`earnings_model_predict.ipynb`（Colabノートブック）と `batch_rerun_predict.py`（バッチ再実行）を **CLI 1本に統合** し、メンテナンス性を改善する。合わせて CONSENSUS v3 スキーマ対応と EPS コンセンサス因子追加を取り込む。

## 背景・動機

- Colabノートブックは起動が面倒（ブラウザ→Colab→マウント→日付設定→全セル実行）
- バッチスクリプトはハードコードされた `DATE_PAIRS` を毎回書き換える必要あり
- 2ファイルで同じロジックを維持する二重メンテの負担
- CONSENSUS テーブルが v3（5項目、TARGET廃止）に移行済みだが、両ファイルとも旧スキーマ（`SOURCE='RAKU'`, `TARGET IN ('CURRENT','NEXT')`, `PROFIT`）を参照
- EPS コンセンサスが取得可能になったのに因子に組み込まれていない（6723 ルネサス等の見逃し事例あり）

## 現状分析

### ファイル構成

| ファイル | 行数 | 用途 | 最終更新 |
|---------|------|------|---------|
| `earnings_model_predict.ipynb` | ~650行(コード) | 日次運用（predict + answer + 精度集計） | a90a1b3 |
| `batch_rerun_predict.py` | 915行 | 過去日付バッチ再実行 | 9c0250f |
| `earnings_model_core.py` | 248行 | 共通スコアリングロジック（16因子） | 9c0250f |

### ロジック比較

| 観点 | ノートブック | バッチ |
|------|------------|--------|
| 日付指定 | 1日（手動設定） | 複数日（ハードコード `DATE_PAIRS`） |
| BQ効率 | 日付ごとにクエリ（N×8本） | 全日一括取得（8本）→pandas絞り込み |
| CONSENSUS | フィルタなし旧スキーマ | `SOURCE='RAKU'`旧スキーマ |
| TDnet取得 | yanoshin→HTML→BQの3段フォールバック | BQのみ |
| 可視化 | matplotlib（精度チャート） | なし |
| 実行環境 | Colab/ローカル双方 | ローカルのみ |
| 答え合わせ | ACTUAL_DATE安全ガード付き | ガードなし |
| GCS保存 | prediction + actual + accuracy_summary | 同上 |

### 結論: **バッチ側のアーキ（共通データ1-pass + per-date処理）が優秀。ノートブックのTDnetフォールバック・安全ガード・可視化を統合する。**

## 設計

### 統合CLI: `scripts/earnings_model/predict.py`

```bash
# 日次予測（当日）
PYTHONUTF8=1 python scripts/earnings_model/predict.py predict --date 20260505

# 答え合わせ（翌営業日に実行）
PYTHONUTF8=1 python scripts/earnings_model/predict.py answer --date 20260505 --actual-date 20260506

# 過去バッチ再実行（日付範囲）
PYTHONUTF8=1 python scripts/earnings_model/predict.py backfill --from 20260401 --to 20260428

# 精度集計（GCS全期間）
PYTHONUTF8=1 python scripts/earnings_model/predict.py accuracy
```

### 維持するファイル

| ファイル | 役割 |
|---------|------|
| `predict.py` | CLI統一ツール（新規） |
| `earnings_model_core.py` | 共通スコアリング（既存維持） |

### 削除するファイル

| ファイル | 理由 |
|---------|------|
| `batch_rerun_predict.py` | predict.py backfill に統合 |
| `earnings_model_predict.ipynb` | predict.py predict/answer に統合 |

### CONSENSUS v3 対応

**旧（両ファイル現状）**:
```sql
SELECT TICKER, QUARTER, TARGET, PROFIT AS CONSENSUS_PROFIT
FROM STOCK.CONSENSUS WHERE SOURCE = 'RAKU' AND TARGET IN ('CURRENT', 'NEXT')
```

**新（predict.py）— モード別データ取得方式**:

| モード | 取得方式 | 理由 |
|--------|---------|------|
| `predict`（単日） | `V_CONSENSUS_MERGED` VIEW | 当日最新1回で十分 |
| `backfill`（複数日） | 全CONSENSUS 1-pass取得 + pandas as-of フィルタ | BQコスト最小化（TVF日付別呼出しは課金N倍） |

```sql
-- predict 単日用（VIEW最新）
SELECT TICKER, FY, QUARTER, DATAAT, REVENUE, OP_PROFIT, ORD_PROFIT, NET_PROFIT, EPS
FROM `STOCK.V_CONSENSUS_MERGED`

-- backfill 用（1-pass全件取得 → pandas側で as-of フィルタ）
SELECT TICKER, FY, QUARTER, DATAAT, ORD_PROFIT, EPS
FROM `STOCK.CONSENSUS`
WHERE DATAAT <= '{max_predict_date}'
-- predict.py内で: df_cons[df_cons["DATAAT"] <= predict_date].groupby(["TICKER","FY","QUARTER"]).last()
```

> **設計決定**: backfill で TVF (`fn_consensus_merged_asof`) を日付ごとに呼ぶ方式は不採用。既存バッチの 1-pass アーキを維持し、pandas で `DATAAT <= predict_date` の最新行を取る方式とする。

### 当期/来期判定（`_derive_current_fy()` + 入力パイプライン）

`zaraba_earnings.py` と同じ `_derive_current_fy()` 方式（`fin_summary` の最新FY開示から導出）。

**入力データ取得パイプライン**（Phase 1 共通データ取得で実装）:

```sql
-- BQ fin_summary から前回開示の Q/FY末日 を取得
SELECT TICKER,
       TypeOfCurrentPeriod AS prev_disc_type,
       CurrentFiscalYearEndDate AS prev_disc_fy_end
FROM `STOCK.fin_summary`
WHERE DISCLOSED_DATE < '{predict_date}'
QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER ORDER BY DISCLOSED_DATE DESC) = 1
```

- **predict 単日**: 上記SQLを1回実行 → 銘柄ごとに `_derive_current_fy(prev_disc_type, prev_disc_fy_end)` で当期FY特定
- **backfill**: 1-passで `WHERE DISCLOSED_DATE <= '{max_predict_date}'` を取得 → per-date で pandas フィルタ（既存バッチ `df_prev` と同等）

### EPS コンセンサス因子（F4拡張）

現行 F4 は経常利益（ORD_PROFIT）コンセンサスのみ。v3 で EPS コンセンサスが利用可能になったため:

1. **F4a: ORD_PROFIT 乖離**（既存ロジック維持）
2. **F4b: EPS 乖離**（新規追加）— OP以下の毀損（税金・のれん償却・特損）を検知

**core.py インターフェース変更仕様**:

| 変更箇所 | 内容 |
|---------|------|
| `compute_score()` 入力 row | `eps_consensus_deviation` キー追加（Optional[float]、EPS コンセなし時は None） |
| F4 ロジック | `max(abs(ord_profit_dev), abs(eps_dev))` の符号付き値を採用（二重加算は過剰） |
| `PRED_COLUMNS` | `eps_consensus_deviation` 追加 |
| `f4_source` 値 | 既存 `"ORD_BEAT"/"ORD_MISS"` に加え `"EPS_BEAT"/"EPS_MISS"` 追加 |

**cons_map → compute_score() 入力への変換レイヤー**（predict.py `compute_features()` 内に配置）:

```python
# predict.py compute_features() 内
cons = cons_map.get((tk, quarter, "CURRENT"), {})
ord_dev = (actual_ord - cons.get("ORD_PROFIT", 0)) / abs(cons["ORD_PROFIT"]) if cons.get("ORD_PROFIT") else None
eps_dev = (actual_eps - cons.get("EPS", 0)) / abs(cons["EPS"]) if cons.get("EPS") else None
row["consensus_deviation"] = ord_dev       # F4a（既存キー維持）
row["eps_consensus_deviation"] = eps_dev   # F4b（新規キー）
```

ウェイト・閾値は既存 F4 と同一体系（段階スコア ±1〜±5）。

## 作業ステップ

### Phase 1: CLI 骨格 + CONSENSUS v3

**着手条件**: なし（初期フェーズ）

1. [x] `predict.py` 新規作成 — argparse（predict/answer/backfill/accuracy）+ 共通データ取得
2. [x] CONSENSUS v3 対応 — predict単日: VIEW / backfill: 1-pass+pandas as-of（TVF不使用）
3. [x] 前回開示情報取得 — BQ fin_summary から prev_disc_type/prev_disc_fy_end を取得するパイプライン
4. [x] 当期/来期判定 — `_derive_current_fy()` 移植（zaraba_earnings.py L854-872）+ 上記入力接続
5. [x] `predict` サブコマンド — ノートブック Cell 6-8 相当（特徴量→スコア→GCS保存）
6. [x] TDnet フォールバック — yanoshin→HTML→BQ 3段（ノートブック由来）

### Phase 2: answer + backfill

**着手条件**: Phase 1 の predict 単日実行が既存GCS結果と一致（スコア・因子完全一致）

7. [x] `answer` サブコマンド — 安全ガード付き（ACTUAL_DATE≠PREDICT_DATE、株価投入チェック）
8. [x] `backfill` サブコマンド — 1-pass全件BQ取得 + per-date pandas フィルタ + compute_features
9. [x] `accuracy` サブコマンド — GCS全actual集計 + JSON サマリー保存（可視化なし、JSON出力のみ）

### Phase 3: EPS コンセンサス因子

**着手条件**: Phase 2 backfill 結果が旧バッチ精度と方向一致率 ±2pp 以内

10. [x] `earnings_model_core.py` に F4b（EPS乖離）追加 — `eps_consensus_deviation` 入力、`max(|F4a|,|F4b|)` 採用、PRED_COLUMNS/f4_source 拡張
11. [x] `predict.py` compute_features() で cons_map → `eps_consensus_deviation` 変換追加
12. [-] バックテスト — CONSENSUS v3に過去データなし(RAKU削除済み/QUICK初回5/5)のため、5/5以降のデータ蓄積を待って実施

### Phase 4: 移行・削除

**着手条件**: Phase 3 バックテストで精度劣化なし（同等 or 改善）

13. [x] ノートブック削除（`earnings_model_predict.ipynb`）
14. [x] バッチスクリプト削除（`batch_rerun_predict.py`）
15. [x] 知見MD更新（`059_earnings_model_eda.md` — 関連ファイルリスト更新含む）
16. [x] data_catalog.md 更新 — predict.py GCS出力スキーマ追記

## CONSENSUS v3 の cons_map 構造（新設計）

```python
# 旧: cons_map[(ticker, quarter, "CURRENT"|"NEXT")] = profit_million
# 新: cons_map[(ticker, quarter, "CURRENT"|"NEXT")] = {
#     "ORD_PROFIT": ..., "OP_PROFIT": ..., "REVENUE": ..., "NET_PROFIT": ..., "EPS": ...
# }
```

`_derive_current_fy()` で当期FY特定 → FY一致=CURRENT、FY>current=NEXT として構築。

## GCS JSON 互換性ポリシー

- **既存キー不変**: 旧ノートブック/バッチが保存した prediction/actual JSON のスキーマは変更しない
- **新規キー追加のみ**: `eps_consensus_deviation`, `f4_source` 拡張値等は追加カラムとして付与
- **accuracy サブコマンド**: 旧JSON（EPS列なし）と新JSON（EPS列あり）を統一的に集計可能にする（欠損列は None 扱い）

## 可視化方針

- **CLI（predict.py）**: JSON出力のみ。matplotlib 依存なし。scriptable で軽量
- **可視化**: 精度チャートが必要な場合は別途軽量 ipynb（`earnings_model_accuracy_viz.ipynb`）で GCS JSON を読んで描画。本CLIの責務外

## 検証戦略

1. **predict 1日実行**: 既存ノートブック結果（GCS保存済み）と突合。スコア・因子**完全一致**確認（Phase 2 ゲート）
2. **backfill**: 既存バッチ結果（4/1-4/28）と突合。accuracy_summary の方向一致率が **±2pp 以内**（Phase 3 ゲート）
3. **EPS因子**: 6723ルネサス（4/24 1Q）で F4b が発火し DOWN 側に寄ることを確認
4. **回収**: predict.py は新規作成のため git revert で即回収可能

## リスク・注意事項

- **as-of TVF の精度**: `fn_consensus_merged_asof` が RAKU 旧データを返さないため、4月以前のバックフィル結果は旧バッチと異なる可能性（QUICK未投入期間）。IFIS のみで比較する想定
- **J-Quants API レート制限**: backfill で多日実行時、fin_summary API の呼び出しペースに注意（既存バッチと同じリスク）
- **EPS コンセ欠損**: IFIS は ORD_PROFIT のみ。QUICK 未取得銘柄は EPS コンセなし → F4b は silent skip（既存 F4 と同じ挙動）

## 関連ドキュメント

- `docs/knowledges/tools/059_earnings_model_eda.md` — 因子改善TODO、反省会ログ
- `docs/knowledges/tools/066_zaraba_tool.md` — ザラ場ツール（スコアリング因子共有）
- `docs/plans/tools-022_consensus_load_20260505_125600.md` — CONSENSUS再構成プラン

---

## レビュー追記: 2026-05-05 19:20 JST — code-reviewer

→ `docs/reviews/079_cr_earnings_predict_unify.md`
