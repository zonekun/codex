# BC Match Agent - 月次 BC 突合 NG 自律修正エージェント

**カテゴリ**: tools
**作成日**: 2026-04-20
**ステータス**: 有効
**親 MD**: [`042_monthly_disclosure_master.md`](042_monthly_disclosure_master.md)
**関連ファイル**: `scripts/monthly_bc_repair/` 配下全て, `docs/plans/20260419_bc_match_agent.md`

---

## 概要

`compare_monthly_buffett.py` の NG / BC_NODATA 行を、**Claude 自身**が各 ticker を吟味しながら自律的に解消するエージェント。BC (buffett-code) を正解データとし、`adapter.json` / extract コード / records を調整して一致させる。

**設計思想**: Python 自動化だけでは未知パターンに対応できないため、Claude が判断・実施し、Python ヘルパは反復作業の道具に徹する。

## Claude と Python の役割分担

| 役割 | 担当 |
|---|---|
| NG パターン分類 | **Claude** (records/BC/PDF を読む) |
| 修正方針決定 | **Claude** (A〜I から選択 or 新規考案) |
| 修正適用 | Claude が Edit/Write で adapter 更新、または Python helpers を呼ぶ |
| 検証 (compare 実行・結果解釈) | **Claude** |
| 退行検知・ロールバック | **Claude** |
| Escalation 判断 | **Claude** |
| 繰り返し作業 (CSV 読込・GCS sync・backup・records 削除再抽出) | **Python helpers** |

---

## NG パターン完全カタログ (A〜I)

### A. BC CSV に ticker データなし

**判定**: `bc_monthly_kpi.csv` で該当 ticker の行数が 0

**対処**:
1. `download_bc_kpi.py --tickers X --resume` で再取得を試す (必ず `--resume` 付与、overwrite 事故防止)
2. 再取得しても取れない → `bc_ignore=true` を全 field に適用

---

### B1. 会計年度ズレ (fy_end_month 補正要)

**判定**: records の `year_month` に未来日付 (例: 2027-02) or 不整合がある。adapter.source=tdnet。

**原因**: doc_title の「2026年8月期10月度」等で year_from_title_regex が fy_end_year (2026) をそのまま使い、10月度 → 2026-10 と誤計算 (実際は 2025-10)。

**対処**: adapter に `"use_fy_history_correction": true` 追加。BQ `ticker_fiscal_year_history` から fy_end_month を取得し、`target_month > fy_end_month` なら year-1 補正。

**注意**: fy_corr 単体では解決しないケースがある（他要因と複合）。適用後に compare 検証必須。

---

### B2. submission_date - 1 month 型 (8218 コメリ系)

**判定**: ファイル名 YYYYMM プレフィックス + 「N月度」で、提出月の翌月が対象月 (8218/7564 パターン)

**対処**: adapter に `"year_month_from_submission_minus_1": true` 追加。`extract_monthly_data.py` の `_parse_year_month` が Step 0 で submission_date - 1 month を返す。

---

### B3. year/month regex が雑すぎる (year_month 異常)

**判定**: records の `year_month` に `2026-20` 等不正値が混在

**原因**: `year_from_title_regex: "(\\d{4})"`, `month_from_title_regex: "(\\d{1,2})"` だと、doc_title 中の任意 4 桁数字 / 2 桁数字にマッチ。

**対処**: パターンを `"(\\d{4})年\\d{1,2}月"` / `"\\d{4}年(\\d{1,2})月"` に限定化。

---

### C. yoy_offset=100 不足

**判定**: records の値が -30 〜 +30 の範囲 (narrative の +X.X%/-X.X% 値)、BC 側は 70-130 の指数形式 (100+X.X)。records と BC の差が常に 100 に近い。

**対処**: 該当 field に `"yoy_offset": 100` 追加。

**落とし穴**: 全 field に一括適用してはいけない (店舗数等の integer field は対象外)。% field のみ。

---

### D. unit_scale 違い

**判定**: records の値が BC の N 倍差 (records 2673 vs BC 358 = 7.5倍 等)

**対処 (案)**: `"unit_scale": N` を適用。ただし実例では**定義差**の可能性が高く、bc_ignore が妥当なケースが多い (例: records=子会社合算, BC=単一 segment)。

---

### E. bc_key 明示 (キー名相違)

**判定**: records.field 名と BC.field 名が完全一致しないが、意味は同じ (例: 「エンジニア合計在籍数（名）」 ≒ 「エンジニア在籍数（人）」)

**対処**: adapter.fields[].bc_key に BC 側 field 名を明示。

---

### F1/F2/F3. 行/テーブル選択ミス (tokens 甘さ / match_occurrence / table_index)

**判定**: records の複数 field が**同値** (= 同じ row の値を重複取得)

**原因例**:
- **8218 コメリ**: `全店` tokens が `ＰＷ全店` (サブブランド行) にも hit、意図した「全店舗」行に到達できない
- **8914 エリアリンク**: `稼働率(%)` regex の `^` アンカーが fallback で剥がされ、別テーブル `2021.6 ...` 行を誤マッチ

**対処**:
- F1: adapter.key を厳格化 (例: `"全店"` → `"全店舗"`)、bc_key は旧名保持
- F2: `"match_occurrence": N` で同一 regex の N 番目のマッチを採用
- F3: `"table_index": N` で特定テーブルのみ対象化

---

### G. overwrite_past_months / match_occurrence 競合

**判定**: records の連続月が全て同値 (extract 破綻、最新 PDF の累計値が全月に複製される等)

**対処**: adapter.overwrite_past_months の見直し。必要に応じて row_label_regex 再設計 + match_occurrence 付与。対応工数が高い場合は bc_ignore が実務的。

---

### H. Gemini 切替 (regex 限界対応)

**判定**:
- PDF が文章式 narrative + 表混在 (KeePer 6036 パターン)
- 複雑な表レイアウト (3 テーブル並列、月列位置可変 等)
- regex 再設計で工数高すぎる

**対処**: adapter に `"extraction_method": "gemini"` + `"overwrite_past_months": true` + `"custom_prompt"` を明示。

**custom_prompt の要点**:
1. 抽出対象月 `{year}年{month}月度` を明示
2. 単位/スケール/符号 (「X.X%増」→ 100+X.X 返す 等) を明示
3. サブブランド/累計/当月値の区別を明示
4. 見つからない field は `null`

**適用失敗パターン (bc_ignore に落ちる)**:
- narrative 系で Gemini が最終月値を全月に複製 (全月同値になる)
- 売上額 field と前年比率 field を混同する
- 値の桁が根本的に違う (定義差)

---

### I. bc_ignore (定義差で追跡不能)

**判定**: records と BC で**定義・単位・セグメント**が根本的に違う (値が 2-10 倍差、符号逆転、範囲逸脱)

**対処**: adapter.fields[].bc_ignore=true + `_bc_ignore_reason` 明記

**判断基準**:
- records=子会社合算, BC=親会社のみ → bc_ignore
- records=% 前年比, BC=金額の前年比 → bc_ignore (unit_scale では救えない)
- 速報値 vs 確報値の差で恒常的に diff >5pt → bc_ignore
- Gemini も regex も破綻 → bc_ignore

---

### J. 0件 = PDF 種別問題 (2026-04-21 追加)

**症状**: 特定銘柄で全件 0 件、または直近数ヶ月まるごと 0 件。
regex をいくら直しても挙動が変わらず、Gemini fallback でも 0 件が続く。
原因の深さが「データ取得層 (PDF テキスト抽出)」にあるのに「regex 層」で
粘ってしまう典型的なアンチパターン。

**診断フロー** (`scripts/monthly_bc_repair/RUNBOOK.md` Step 0 も参照):
1. 直近 1 PDF を `pdfplumber.extract_text()` で抽出して長さを確認
2. **50 字未満 → スキャン PDF 確定** → `extract_adapter.json.extraction_method: "ocr"` へ変更
3. **50 字以上だが質 NG** (文字化け率 20% 超、P0-1 の `_text_quality_ok` で判定) → `extraction_method: "gemini"` に escalate
4. **上記で解決しないときのみ** regex 層 (Pattern A〜I) を疑う

**判定ヘルパ**: `extract_monthly_data.py` の `_text_quality_ok(text, min_len=50, garbled_threshold=0.20)`。量（長さ）だけでなく質（文字化け率）を判定するため `len(text) > 50` より厳密。詳細: `062_pdf_processing_strategy.md` §3, §6。

---

## ツール一覧 (`scripts/monthly_bc_repair/`)

### コア 6 本 (常用)

| スクリプト | 用途 |
|---|---|
| `inspect_ticker.py --ticker X` | ticker 全容 dump (adapter / records / BC / PDF 最新テーブル / compare NG) |
| `apply_adapter_patch.py` | JSON patch を adapter に deep merge + snapshot + GCS 同期 |
| `snapshot_adapter.py --save / --restore` | adapter のタイムスタンプ付き backup / 復元 |
| `reextract_and_compare.py --ticker X` | records 削除 → extract → compare を 1 コマンド、末尾 `AGENT_RESULT_JSON` 出力 |
| `list_ng_queue.py` | reconcile CSV から apply 済・escalation 済を除外して未処理 ticker キュー生成 |
| `log_progress.py` | per-ticker 結果を jsonl 追記 + escalation CSV 追記 |

### 一時ツール (archive/)

本セッションで使用した一括処理 BG 群は `scripts/monthly_bc_repair/archive/` に移動。再利用時は内部を参照してパラメータ調整。

- `batch_apply_fy_corr_bg.py`: fy_corr を複数 ticker に一括適用
- `mass_loosen_title_pattern_bg.py`: doc_title_pattern を緩い定型に一括変更
- `bulk_inspect_bg.py` / `deep_inspect_14_bg.py`: 複数 ticker の概要 dump
- `mass_process_bg.py`: 全自動 diagnose + apply (パターン A/C の検出に限定)
- `fix_14_remaining_bg.py`: 最終 14 銘柄の pattern 別一括 fix
- `consolidate_bg.py`: 本まとめ作業のスクリプト (自己言及)

---

## セッション運用手順 (Runbook)

詳細: `scripts/monthly_bc_repair/RUNBOOK.md` を参照。

要約:
1. `list_ng_queue.py` で未処理 ticker キュー取得
2. 優先順: `match_ratio` 高信頼 → 中 → 低
3. ticker ごと:
   - `inspect_ticker.py` で事実確認
   - パターン分類 (A〜I) → 該当 fix 適用
   - `snapshot_adapter.py --save` で backup
   - `apply_adapter_patch.py` で変更
   - `reextract_and_compare.py` で検証
   - 改善 → commit、退行 → rollback → 次仮説
   - 3-5 回試して改善ゼロなら escalation
4. `log_progress.py` で記録
5. 30 銘柄ごと (or 全件完了) で commit + push

---

## 安全制約

1. **BC CSV を編集しない** (正解データ固定)
2. **コアコード (`extract_monthly_data.py` 等) は明示承認なしに変更しない** (adapter 優先)
3. **adapter 変更前に必ず snapshot**
4. **3 回試行で改善ゼロなら escalation** (深追い禁止)
5. **退行検知 (Δ < -5pt) で即 rollback**
6. **Gemini は個人 API キー + `gemini-3-flash-preview`** (Vertex AI 禁止、ローカル実行)
7. **`download_bc_kpi.py` 実行時は必ず `--resume`** (overwrite 事故防止)

---

## 再開時の手順 (セッション間継続)

1. `data/logs/agent_bc_match_<session_ts>_progress.jsonl` を確認 (前回処理済)
2. `list_ng_queue.py --exclude-escalation ...` で残キュー生成
3. 本 MD (042-1) を読んで NG パターンと適用例を復習
4. Runbook に沿って per-ticker loop 再開
5. 完了時は commit + push、`docs/plans/` に session 記録

---

## 再発防止・後追い TODO

次回以降の月次パイプライン改修時に検討すべき恒常的な改善テーマ。

### 1. 定義差系 → BC スクレイパー側のドキュメント化

**症状**: records 取得は成功しているが、BC 値と意味が違う（子会社合算 vs 親会社単体 等）

**対応案**:
- `download_bc_kpi.py` に field ごとの「定義メモ」を出力する機能を追加
- BC ページのヘッダー・注釈（「※子会社を含む」等）を scrape して保存
- `data/csv/bc_field_definitions.csv`（ticker, field, definition_text）を別途生成し、reconcile 時に Claude が読んで semantic 判定できるようにする

### 2. extract 破綻系 → non-tdnet adapter の生成ロジック見直し

**症状**: records 0 件 or gcs_missing で extract が何も取得できない（adapter の `doc_title_pattern` が厳しすぎて PDF をほぼ全拒否）

**対応案**:
- `build_monthly_extractor.py`（adapter 生成）で `doc_title_pattern` をもっと緩く設定する（BQ `MAIN_CATEGORY='月次開示'` が既に一次フィルタ）
- TDnet source はコード側 skip 化済（`extract_monthly_data.py` L2535-2545）。**non-tdnet source についても同様の緩和検討**
- adapter 生成時に「サンプル PDF 3 件でマッチ率 ≥ 50% を保証」する validation を入れる

### 3. regex/Gemini 両方失敗系

**症状**: narrative 系 PDF で Gemini が月別値を複製する / 売上額を前年比 field に誤格納

**対応案**:
- Gemini prompt のテンプレート改善（`switch_to_gemini_bg.py` 内）
  - 「月ごとに異なる値を出すこと（同値を避ける）」
  - 「絶対額ではなく前年比率（50-200 範囲）」
  - few-shot example を付与
- `custom_prompt` の per-ticker チューニング支援ツールを用意（diff を見て prompt 追記する helper）

### 4. 文字化け系 → GCS upload 時の encoding 正規化

**症状**: GCS に保存されたファイル名が文字化けしてタイトル解析不能

**対応案**:
- `download_monthly.py` の GCS upload 時にファイル名を NFKC 正規化 + 非 ASCII を base64 退避
- 既存 GCS ファイルを一括リネーム（ticker ごとに metadata から正しい名前を復元）
- TDnet ID を fallback に使う

**優先度**: non-tdnet adapter 生成（§2）は月次パイプライン改修時に最優先（影響範囲が最広）。BC スクレイパー・Gemini prompt は中、GCS ファイル名は低。

---

## バッチ分類アプローチ（2026-04-25 セッション知見）

042-1 の「1社ずつ Opus 診断」は品質最高だが、**80社超の大量NG一掃**には時間がかかりすぎる。以下のバッチ分類→一括適用アプローチで大量NGを短時間で解消できる（compare CSV ベース、PDF読解なし）。

### 前提: いつ使うか

- **1社ずつ Opus 診断**: 10〜30社、時間に余裕があるとき。根本原因の特定精度が高い
- **バッチ分類**: 50社超の大量NG、または「まず NG=0 にして後から品質改善」の戦略時

### Step 1: compare CSV から diff パターン分類

compare CSV（`C:\tmp\buffett_compare_YYYYMMDD_HHMMSS.csv`）を pandas/csv で読み、NG 行を ticker ごとにグループ化。各 ticker の diff 分布で5カテゴリに自動分類:

| カテゴリ | 判定条件 | 対応 | 期待効果 |
|----------|----------|------|----------|
| **remove_yoy** | diff≈100 かつ yoy_offset 列に `+100.0` | adapter から yoy_offset 削除 | NG→OK |
| **add_yoy** | diff≈100、yoy_offset なし、`our+100≈bc` | adapter に yoy_offset=100 追加 | NG→OK |
| **bc_ignore_huge** | max(diff) > 10,000 | bc_ignore=true + 理由 | NG除外 |
| **bc_ignore_small** | all(diff < 5) | bc_ignore=true + 理由 | NG除外 |
| **investigate** | 上記以外 | 個別調査 or bc_ignore | — |

### Step 2: investigate カテゴリの再分類

investigate に残った ticker をさらに手動/半自動で分類:

| サブパターン | 特徴 | 対応 |
|-------------|------|------|
| 絶対値 vs % | our=9470, bc=95.9 等。桁が根本的に違う | bc_ignore |
| 店舗数定義差 | our=17, bc=332 等。直営 vs 全店 vs FC含む | bc_ignore |
| 同一値全月複製 | 3ヶ月とも同じ our 値 | bc_ignore（overwrite_past_months 問題） |
| index/change 混在 | 月によって抽出形式が変わる | bc_ignore |
| 中差（5-30） | 抽出元が違う行/列 | bc_ignore |

### Step 3: 一括適用スクリプト

各 ticker の adapter を load → 対象 field に `bc_ignore=True` / `_bc_ignore_reason` / `manual_override=True` を設定 → save。ループ実装は `apply_adapter_patch.py` の deep merge パターンを流用。

### Step 4: GCS 同期 → compare 再実行

**重要**: `compare_monthly_buffett.py` は **GCS 上の adapter** を読む（ローカルではない）。ローカル修正だけでは compare 結果に反映されない。**必ず GCS にアップロードしてから再実行**すること。GCS アップロードは `apply_adapter_patch.py` が自動で行う（手動同期は `monthly/meta/{ticker}/extract_adapter.json` へ blob.upload_from_filename）。

### 落とし穴・学んだこと

1. **yoy_offset 二重適用の検出方法**: `our_value ≈ bc_value` なのに `diff ≈ 100` → 抽出値が既にインデックス形式なのに +100 されている。**yoy_offset 列に `+100.0` が表示されていたら疑え**

2. **bc_ignore は「追跡放棄」ではなく「BC検証対象外」**: 抽出自体は継続する。BC側の定義と合わないだけ。品質改善フェーズで adapter 再設計すれば bc_ignore 解除可能

3. **バックグラウンドエージェントのレートリミットリスク**: 6エージェント並列でレートリミット全滅した。大量並列より、メインプロセスでバッチスクリプト実行の方が確実

4. **「微差も全件修正」vs「bc_ignore で割り切り」のトレードオフ**: 
   - 042-1 のプラン（微差も全件修正）は原則正しいが、80社超では非現実的
   - bc_ignore で NG=0 にした後、priority 順に bc_ignore 解除していく方が progressive

5. **compare CSV の `our_value` はオフセット適用後**: `yoy_offset=+100.0` と表示されている場合、`our_value` は適用後の値。diff は `our_value(適用後)` と `bc_value` の差

---

## 参考資料

- 親 MD: [`042_monthly_disclosure_master.md`](042_monthly_disclosure_master.md) - 月次パイプライン全体像
- [`055_extract_adapter_design_patterns.md`](055_extract_adapter_design_patterns.md) - adapter 設計パターン
- [`056_compare_monthly_buffett.md`](056_compare_monthly_buffett.md) - compare 仕様
- `docs/plans/20260419_bc_match_agent.md` - 初期仕様書 (発案記録)
