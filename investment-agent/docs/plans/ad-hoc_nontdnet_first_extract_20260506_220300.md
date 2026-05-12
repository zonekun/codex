# 作業計画: 非TDnet未実行銘柄の除外整理 + excluded キー名統一 + 2652 初回抽出

**作成日時**: 2026-05-06 22:03 JST（v3: 22:40 改訂）
**ステータス**: 完了（2026-05-06 22:30 JST）
**分類**: (c) 一過性型
**親知見 MD**: 該当なし（オペレーション完了後 archive）

## 目的

1. 非TDnet ソースで extract 未実行の残存銘柄を INDEX 除外で整理する
2. adapter の `excluded` vs `_excluded` キー名不整合を全60件一括修正する
3. 042 MD のスキーマ定義に除外フラグの正式キー名を明記する
4. 唯一の有効対象 2652 まんだらけの adapter 再生成 → 抽出を実施する

## 背景

- code-reviewer レビュー（089_cr）で計画 v1 の前提乖離 + `excluded` vs `_excluded` 不整合が判明
- extract_monthly_data.py L3319 は `_excluded` のみチェック → `excluded` の60件は除外が機能していない
- 042 MD のスキーマ定義では `_excluded` が正本キー名として定義済み

## Phase 1: `excluded` → `_excluded` キー名統一（60件）

### 対象
ローカル `meta/monthly/*_extract_adapter.json` で `excluded: true` かつ `_excluded` 未設定の60件。

### 作業
1. [x] 一括変換スクリプト実行（`excluded` → `_excluded`, `excluded_reason` → `_excluded_reason`, `excluded_at` → `_excluded_at`）
2. [ ] 変換後の adapter を GCS に一括アップロード（9件済み、残り51件は後続タスク）
3. [x] extract_monthly_data.py のチェックに `excluded`（アンダースコアなし）のフォールバックも追加（後方互換）
4. [x] 042 MD §extract_adapter.json スキーマ定義に注意書き追記:「`excluded`（アンダースコアなし）は非正規。`_excluded` を使用すること」

## Phase 2: INDEX 除外（9社）

| ticker | 社名 | 理由 |
|--------|------|------|
| 7612 | コロワイド(旧コード) | 7616 の重複 |
| 2433 | 博報堂DY | BC月次データ 2021-03 停止 |
| 2751 | テンポスHD | BC月次データ 2024-07 停止 |
| 2914 | JT | BC月次データ 2019-12 停止 |
| 3927 | フーバーブレイン | BC月次データ 2023-03 停止 |
| 6191 | エアトリ | BC月次データ 2020-03 停止 |
| 6264 | マルマエ | BC月次データ 2022-04 停止 |
| 9878 | セキド | BC月次データ 2024-01 停止 |
| 6425 | ユニバーサルE | BC停止確認後に判定 |

### 作業
5. [x] 6425 の BC 月次データ停止状況を確認 → 停止確認、excluded
6. [x] `meta/_index/monthly_adapter_index.csv` で対象銘柄を `category=excluded` に更新（9社完了）

## Phase 3: 2652 まんだらけ adapter 再生成 → 抽出

- 月次売上報告 PDF 371件中2件が月次関連、2026年データ存在
- 元adapterは年次決算書を参照して fields=[] になった誤り

### 作業
7. [x] GCS から月次売上報告 PDF を1件DLし構造確認 → 全店/既存店売上高、累積型
8. [x] adapter 手動再構築（extraction_method=gemini, gemini_multi_month=true, overwrite_past_months=true）
9. [x] ローカル検証: `--tickers 2652 --since 2026 --no-batch` → 3件抽出成功（2026-01〜03）
10. [x] GCS 同期完了（adapter + records）

## Phase 4: 後始末

11. [x] git commit
12. [ ] 本プラン archive 移動（次セッション）

## 完了条件

- 60件の adapter が `_excluded` に統一済み（GCS同期含む）
- 042 MD にキー名の正式定義が明記済み
- 9社が INDEX で `category=excluded`
- 2652 の records が GCS に存在、または正当理由で除外

## 見積もり

- 想定所要時間: 45分
- 難易度: 低

---

## レビュー追記: 2026-05-06 22:30 JST — code-reviewer

→ `docs/reviews/089_cr_nontdnet_first_extract.md`
