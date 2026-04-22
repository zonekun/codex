# BC Match Agent 仕様書

**作成日**: 2026-04-19
**ステータス**: Phase 1 着手予定
**関連**: `docs/plans/20260418_monthly_bc_round2_followup.md`

---

## 目的

`bc_key_reverse_mapping` に残る NG / BC_NODATA 行を、user 指示なしで自律的に解消する。BC を正解データとして、records / adapter / (必要なら) extract コードを調整して一致させる。

---

## 前提

**Claude 自身が各 ticker を吟味しつつ進めるループ**。Python スクリプトは道具。判断（NG パターン分類、PDF の読み方、Gemini 切替の要否 等）は Claude が行う。Python だけの自動化では未知パターンに対応できないため、Claude 駆動とする。

---

## Claude の役割と Python の役割

| 役割 | 担当 |
|---|---|
| NG パターン分類 | **Claude**（records/BC/PDF を読んで判定）|
| 修正方針決定（A〜I のどれを当てるか）| **Claude** |
| 修正適用 | **Claude** が Edit/Write で adapter 更新、または既存 Python を呼ぶ |
| 検証（compare 実行・結果解釈）| **Claude** |
| 退行検知・ロールバック | **Claude** |
| Escalation 判断 | **Claude** |
| BG 並列実行管理 | **Claude** |
| 繰り返し作業（CSV 読込・GCS 同期・backup）| **Python ヘルパ** |

---

## Python ヘルパ（`scripts/agent_bc/` 配下、6 本）

```
scripts/agent_bc/
├── inspect_ticker.py        # 1 ticker dump (adapter/records/BC/PDF頭/compare結果)
├── apply_adapter_patch.py   # JSON patch (merge dict) + GCS sync + backup
├── reextract_and_compare.py # records 削除 → extract → compare を 1 コマンドで
├── snapshot_adapter.py      # snapshot/restore (timestamped JSON copy)
├── list_ng_queue.py         # 最新 reconcile CSV から未処理 ticker キュー取得 (apply済・bc_ignore 除外)
└── log_progress.py          # per-ticker 結果を agent_progress.jsonl に追記
```

各ツールは CLI 1 コマンドで呼べる。引数は最小限（例: `--ticker 8218`）。

---

## Claude の runbook（per ticker）

```
1. inspect_ticker.py <T> で事実を把握
   - adapter.fields
   - records 最新 6 件
   - BC 同 year_month / 同 ticker の全 fields
   - PDF 最新 1 件のテーブル dump
   - compare の NG 詳細

2. Claude が原因分類 (A〜I or NEW pattern)

3. 修正を提案 → Edit で adapter 書換 or apply_adapter_patch.py 呼出

4. snapshot_adapter.py <T> で backup

5. reextract_and_compare.py <T> 実行

6. Claude が結果を解釈:
   - 改善 (Δ > +10pt): commit
   - 退行 (Δ < -5pt): 即 rollback → 次の仮説
   - 変化なし: rollback → 次の仮説
   - 3 回試して改善ゼロ → Escalation

7. log_progress.py で記録

8. 次の ticker へ
```

---

## NG パターン分類（蓄積知見）

| 記号 | パターン | 対処 |
|---|---|---|
| **A** | BC CSV に ticker データなし | `download_bc_kpi.py --tickers X --resume` で再取得、無ければ `bc_ignore=true` |
| **B1** | 会計年度ズレ（例: 2025-10 が 2026-10 と抽出される）| `use_fy_history_correction=true` |
| **B2** | 提出月 = 対象月 + 1 の銘柄（8218/7564）| `year_month_from_submission_minus_1=true` |
| **B3** | year/month_from_title_regex が fiscal 年末月を誤抽出（8410）| regex を `YYYY年MM月` 限定化、doc_title_pattern 緩和 |
| **C** | 値が常に +100 差（records 3.1 vs BC 103.1）| `yoy_offset=100` |
| **D** | 値が unit_scale 差（×1000 / ×100）| `unit_scale=N` |
| **E** | 値は一致するが bc_key が違う | adapter.fields の `bc_key` 明示 |
| **F1** | tokens 甘さ（「全店」が ＰＷ全店等のサブブランド行に誤マッチ）| adapter.key を「全店舗」等厳格名に変更、bc_key 保持 |
| **F2** | 3 テーブル同ラベル区別失敗（売上/客数/客単価）| `match_occurrence=1/2/3` |
| **F3** | テーブル選択ミス | `table_index` 明示 |
| **G** | 複数月が同値 / extract 破綻 | `overwrite_past_months` 見直し、row_label_regex 再設計 |
| **H** | 文章式 PDF / 複雑表 / 符号判定 | `extraction_method=gemini` + `custom_prompt` |
| **I** | BC 側ブランド名変更・統合（7532 ドン・キホーテ→国内小売）| `bc_key` 差替 or `bc_ignore=true` |

---

## Claude が判断に使う観点（チェックリスト）

- [ ] BC に field が完全にない → A
- [ ] records と BC の year_month が 1 月ズレ → B1 / B2
- [ ] year_month=2026-20 等不正 → B3
- [ ] 値が一様に +100/-100 差 → C
- [ ] 値が一様に ×10/×100 差 → D
- [ ] 同月で複数 field が同値 → F1 / F2（tokens 甘さ or table 未区別）
- [ ] 連続月で records 同値 → G（overwrite_past_months / match_occurrence）
- [ ] PDF が文章式 or 表複雑 → H（Gemini 切替）
- [ ] BC 側ブランド名変更 → I（bc_key 差替）
- [ ] PDF に該当 field 記載なし → bc_ignore

---

## 安全制約

1. **BC CSV は編集しない**（正解固定）
2. **`extract_monthly_data.py` 等コアコードは Claude の明示承認なしに変更しない**（adapter 変更で対応可能なら優先）
3. **adapter 変更前に必ず snapshot**
4. **3 回試行で改善ゼロなら escalation**（深追い禁止、次へ）
5. **退行検知（Δ < -5pt）は即 rollback**
6. **Gemini は個人 API キー + `gemini-3-flash-preview`**（Vertex AI 禁止、ローカル実行）

---

## 進捗管理

| ファイル | 用途 |
|---|---|
| `data/logs/agent_bc_match_<session_ts>_progress.jsonl` | 1 行 = 1 ticker 結果（before/after ratio, applied_fix, evidence）|
| `data/logs/agent_bc_match_<session_ts>_escalation.csv` | 自動修正不能、人手必要な ticker 一覧 |
| `data/logs/agent_bc_match_<session_ts>_summary.md` | 全件完了時の最終レポート |

中断時は `progress.jsonl` から resume 可能（fixed 済 ticker は skip）。

---

## Claude のセッション運用

- 1 回の回答で 1〜3 ticker 処理（context 節約）
- BG で extract/compare を走らせる間、次の ticker を inspect
- **30 ticker 処理ごとに中間報告**（user に任意介入 window を提供）
- 全件完了 or `--max-tickers` 到達で最終レポート

---

## 呼出し例（Claude 視点）

```bash
# 1 ticker 精査
PYTHONUTF8=1 python scripts/agent_bc/inspect_ticker.py --ticker 8218

# adapter patch 適用 + GCS 同期
PYTHONUTF8=1 python scripts/agent_bc/apply_adapter_patch.py \
    --ticker 8218 --patch '{"year_month_from_submission_minus_1": true}'

# records 削除 → extract → compare を一括実行
PYTHONUTF8=1 python scripts/agent_bc/reextract_and_compare.py --ticker 8218

# snapshot/restore
PYTHONUTF8=1 python scripts/agent_bc/snapshot_adapter.py --ticker 8218 --save
PYTHONUTF8=1 python scripts/agent_bc/snapshot_adapter.py --ticker 8218 --restore

# 未処理 ticker キュー取得
PYTHONUTF8=1 python scripts/agent_bc/list_ng_queue.py \
    --reconcile-csv data/logs/bc_key_reverse_mapping_20260419_170039_gemini.csv

# 進捗ログ追記
PYTHONUTF8=1 python scripts/agent_bc/log_progress.py \
    --session-ts 20260419_200000 --ticker 8218 --status fixed \
    --before-ratio 0.39 --after-ratio 1.0 --applied-fix "F1: tokens 全店舗 厳格化"
```

---

## 実装順

| Phase | 内容 | 成果物 |
|---|---|---|
| 1 | Python ヘルパ 6 本を作成 | `scripts/agent_bc/*.py` |
| 2 | 本 MD を runbook として Claude が参照開始 | (Claude 側) |
| 3 | 最新 reconcile CSV で実行開始、高信頼 → 低信頼 順 | `progress.jsonl` |
| 4 | 30 銘柄ごとに中間報告 | (session 内) |
| 5 | 全件完了後 final summary.md 作成 | `summary.md` |

---

## この方式の利点 / 懸念

**利点**:
- 未知パターンに遭遇しても Claude が柔軟に判断
- PDF を Read/Bash で直接確認可能
- user 介入は中間報告時のみ（30 銘柄毎 or 完了時）
- Python ヘルパで反復作業を自動化 → セッション効率化

**懸念と対策**:
- Claude コンテキスト窓消費 → 中間報告でセッション分割（`progress.jsonl` 経由で resume）
- 修正の副作用で他 field が壊れる可能性 → snapshot + 退行検知で即 rollback
- 未知パターンに深追いする可能性 → 3 回試行で escalation

---

## 参考資料

- `docs/plans/20260418_monthly_bc_round2_followup.md` — Round 2 の作業履歴、個別銘柄調査結果
- `docs/knowledges/tools/042_monthly_disclosure_master.md` — 月次パイプライン全体像、scripts 一覧
- `docs/knowledges/tools/055_extract_adapter_design_patterns.md` — adapter 設計パターン
- `docs/knowledges/tools/056_compare_monthly_buffett.md` — compare 仕様
- `scripts/reconcile_bc_key_from_compare.py` — semantic guard + Gemini matching
- `scripts/apply_bc_key_reverse_mapping.py` — 承認済 CSV 適用
