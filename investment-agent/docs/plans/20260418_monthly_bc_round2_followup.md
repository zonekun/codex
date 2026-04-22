# 20260418 月次 BC 突合 Round 2 フォローアップ

作成: 2026-04-18 JST / 起点: extract-monthly-data-m2j27 (2026-04-18 17:36 完了) + compare_monthly_buffett Round 2 (17:38) 以降のアドホック対応の集約。

---

## 🧭 運用方針（2026-04-18 確定）

1. **3月月次 EXTRACT → BC 突合 → adapter 正しさ確認**（現フェーズ）
2. **adapter が正しいと確認できたら、4月月次（5月発表）から本格運用開始**
3. **本格運用後は BC との比較を行わない**。BC は **adapter 開発のためのテストデータ**にすぎない
4. **過去データの扱い**: BC から JSON へ**逆引き反映**を行う予定（開発は別途）
5. **BC 完全不一致をよしとした銘柄は逆引き反映できない** → その銘柄リストを以下で管理する

### 🗑️ 月次開示 管理対象外銘柄（論理削除、追跡停止）

事業全体に対して開示対象が小さすぎて株価要因として追う意味がない銘柄。adapter に `_excluded: true` を立てて論理削除（ファイルも GCS の records/docs も残すが、extract/突合パイプラインは早期スキップ）。

| ticker | 会社名 | 確定日 | 理由 | 開示内容 |
|--------|--------|--------|------|----------|
| 6752 | パナソニック HD | 2026-04-18 | パナソニックホームズ受注速報のみで、パナソニック HD 全体事業に対して極めて小さい割合 | 月度受注速報（戸建/集合/分譲/マンション） |
| 2337 | いちご | 2026-04-18 | いちご太陽光・風力発電所の月次発電実績のみで、いちご（不動産・再エネ総合）事業の一部 | 月次発電実績 |
| 6580 | ライトアップ | 2026-04-19 | 月次開示停止中。2024年3月度（2024-04-11発表）以降 2 年以上月次PDF公表なし | 主要KPI月次進捗（JDネット支援社数/Jコンサル申請完了数/Jシステム受注件数 他） |
| 8705 | 証券会社 | 2026-04-19 | 証券会社で月次業績の追跡意義が薄い。株価要因として意味のある銘柄ではない | 証券会社系月次データ |

**実装**:
- adapter (`data/monthly_adapters/<t>.json` + GCS `monthly/meta/<t>/extract_adapter.json`) に `_excluded: true`, `_excluded_reason`, `_excluded_at` 追記
- `scripts/extract_monthly_data.py`: adapter 読込直後に `_excluded` チェック → `results['excluded']` に計上して continue
- `scripts/compare_monthly_buffett.py`: 突合ループ冒頭に同様のチェック → `summary['excluded']` に計上して continue
- `data/monthly_adapter_index.csv`: 6752 は `skip=True, category=excluded` に変更（2337 は tdnet 経路のため index 非掲載）

**新規追加時の手順**: 本表へ追記 → adapter に `_excluded` フラグ追加 → GCS 同期。

### 🚫 BC 不一致許容銘柄（逆引き反映対象外）

「BC 側に該当指標なし or 指標定義が乖離」等の理由で、adapter 全 field `bc_ignore: true` 運用にした銘柄。BC 突合分母から外れ、過去データの BC→JSON 逆引き反映もできないため、**extract（PDF regex 等）の抽出値のみを正とする**。

| ticker | 会社名 | 確定日 | 理由 | adapter |
|--------|--------|--------|------|---------|
| 7127 | 一家 HD（飲食事業部 月次報告） | 2026-04-18 | BC 側に「全店（全業態）」指標なし。PDF regex + `{fy_month_idx}` で全店全業態 5 field（売上/客数/客単価/前年店舗/当年店舗）を直接取得 | `data/monthly_adapters/7127.json` |
| 7918 | ヴィア HD（月次速報 連結_前年対比） | 2026-04-18 | BC 側と指標定義が微差。PDF regex + `column_map` で【全店実績】【既存店実績】の先頭セクション 6 field（売上高/客数/客単価 × 全店/既存店）を直接取得 | `data/monthly_adapters/7918.json` |

**運用ルール**:
- 新規に bc_ignore 運用へ切り替える際は、adapter に `regex_redesign_at` + `regex_redesign_note` を記録し、この表に追記する
- 逆引き反映ツール（開発別途）はこの表を参照して対象外判定する
- 追記は `docs/plans/20260418_monthly_bc_round2_followup.md` の本セクション（運用方針 §🚫）を唯一の真実とする

---

## 目的

Round 2 以降に散在している検証ログ・修正記録・個別調査 MD を、1 つのハブから辿れるようにする。現状の課題棚卸しと次アクションの整理。

## 全体像（Round 2 以降の流れ）

```
Round 2 extract (P1-P5 + description rewrite + field rename 適用後)
 ├─ 17:36 extract-monthly-data-m2j27 : 成功 106 / スキップ 183 / 失敗 0
 ├─ 17:38 BC compare Round 2         : OK 613 / 総 1097 = 55.9%（前回 60.2%）
 ├─ 17:47 前回/今回 ticker別 diff     : 改善 9 / 劣化 10 / 新規 24 / 横ばい 90
 ├─ 18:23 劣化 10社 個別調査
 ├─ 19:00 劣化 10社のうち 4社（3175/3349/7127/2782）再修正 → 部分改善
 ├─ 19:18 Round 2-b BC compare       : 再修正後の二次比較
 ├─ 19:24 新規 24社 真因解明          : regex 23 / adapter 無 1
 ├─ 19:34 7127/7918 再extract (TDnet Gemini プロンプト改修後)
 ├─ 19:42 24社リスト確定 CSV/MD
 └─ 19:55 7127 adapter 再設計         : PDF regex + {fy_month_idx} + bc_ignore（BC対象外）
```

並行して残課題:
- download-monthly 46 社 DL 失敗（17:29-17:35 分類のみ、対処未着手）

## 散在ファイル索引（本プランから辿るハブ）

### Round 2 BC 突合 本体
- `data/logs/extract_round2.log` — Round 2 extract 実行ログ
- `data/logs/bc_compare_round2.log` — Round 2 突合ログ
- `data/logs/round2_orchestration.log` — オーケストレーション
- `C:\tmp\buffett_compare_20260418_173658.csv` — Round 2 突合 CSV（本判定）
- `C:\tmp\buffett_compare_20260417_221647.csv` — 前回 CSV（比較元）

### 前回 vs 今回 差分分析
- `data/logs/bc_compare_diff_analysis.md` — 改善 9 / 劣化 10 / 新規 24 / 横ばい 90 分類

### 劣化 10 社 個別調査・再修正
- `data/logs/regression_10_investigation.md` — 10社 × adapter/records/CSV/logs
- `data/logs/3349_investigation.md` — 3349 コスモス薬品 単独深掘り
- `data/logs/extract_regression_fix.log` — 3349/3175/7127/2782 再修正 extract
- `data/logs/bc_compare_regression_fix.log` — 再修正後 BC 突合
- `data/logs/regression_fix_orchestration.log`

### 新規 24 社（前回 BC_NODATA → 今回値あり → NG 露出）
- `data/logs/24_new_investigation.log` — 初期調査
- `data/logs/24_root_cause.log` — 真因分析（差分形式バグ / YYYY-MM 不正 / 単一値複製）
- `data/logs/24_list.csv` / `data/logs/24_list.md` — 確定リスト（regex 23 / adapter無 1）

### 7127 / 7918 個別
- `data/logs/7127_7918_report.md` — 再 extract 結果・異常検出
- `data/monthly_adapters/7127.json` — PDF regex 再設計版（2026-04-18 19:55）
- `data/adapters/7127/structure.json` — monthly_items に 5 field 追加（bc_ignore 運用）
- Dropbox `/stock/temp/7127_7918_extract_report/` — PDF・adapter・records・REPORT.md

### download-monthly 46社 DL NG（並行課題・未着手）
- `data/logs/dl_46_investigation.md` — カテゴリ分類（D 33 / C-E 10 / A 2 / 他 1）

### P1-P5 / source rename 系（Round 2 の前段）
- `data/logs/p1_fixes_applied.md`
- `data/logs/p3_fixes_applied.md`
- `data/logs/p4_bc_reverse_investigation.md`
- `data/logs/p2_bc_reverse_investigation.md` / `data/logs/p2_fix_proposals.md`
- `data/logs/source_rename_log.md`
- `data/logs/touched_tickers.txt`

### 関連知見 MD
- `docs/knowledges/tools/042_monthly_disclosure_master.md` — adapter 仕様・BC 突合スコープ規約（19:42 追記）・extract 改修範囲メモ（19:30 追記）
- `docs/knowledges/tools/055_extract_adapter_design_patterns.md` — regex adapter 設計パターン
- `docs/knowledges/tools/056_compare_monthly_buffett.md` — 突合スクリプト仕様

## Round 2 サマリ（2026-04-18 17:38 現在）

| 指標 | 値 |
|------|----|
| 対象社数 | 289（touched を extract 対象にした運用） |
| 突合対象フィールド数 | 1097（前回 1750） |
| 一致 | 613 |
| 不一致 | 289 |
| BC 未取得 | 195 |
| GCS データなし | 156 |
| **一致率** | **55.9%**（前回 60.2%） |

単純比較不可（対象数・総フィールド数が大きく違う）。→ diff_analysis で個別評価（下記）。

## 1. 改善 9 社（detail → bc_compare_diff_analysis.md）

field rename / description rewrite / P5 が効いたケース:
- 7823: 0%→100%（「全店 来店客数（前年同月比）」→「全店 客数」rename）
- 3547: 0%→100%（「直営全店 売上」→「串カツ田中グループ 直営全店 売上」）
- 7059: 85%→100%（グループ合計 field 削除）
- 他: 2154/2970/3066/3358/3391/9956

## 2. 劣化 10 社（detail → regression_10_investigation.md）

| ticker | 前→後 | カテゴリ | 原因 | 対応 |
|--------|-------|----------|------|------|
| 3349 | 100→0% | α: overwrite_past_months × 未来日 | Gemini が最新 PDF の年度表から未来月まで全部 records 化、sub_date = 2026-01-20/02-20 | 再修正実施（19:00 付近） |
| 3561 | 100→0% | α: 同上 | 同上 | 再修正実施 |
| 3175 | 85.7→66.7% | γ: column shift | description から「最新月（最右）」記述削除の副作用 | 再修正実施 |
| 2782 | 83.3→66.7% | γ: column shift | 同上 | 再修正実施 |
| 1420 | 100→0% | β: 年月抽出失敗 (unknown) | source rename 直後のリグレッション（18:47 manual_override + 19:12 再 extract で復旧済、21:14 ローカル再実行でも正常確認）。BC_NODATA は 1420 の IR 開示遅延（2026-04-18 時点 IR 最新 = 2026-02 月度）+ BC 側 2026-02 データ未到着 = 外部要因 | **2026-04-18 復旧確認済**（→ [1420_extract_verification.md](../../data/logs/1420_extract_verification.md)） |
| 6752 | 100→25% | γ: サブカテゴリ分離失敗 | 戸建/集合/分譲 区別落ち | **2026-04-18 管理対象外に変更（→ §🗑️）** |
| 7685 | 25→0% | δ: unit_scale=100 逆効果 | our 側は既に倍率変換後 | 未着手（unit_scale 除去） |
| 7918 | 88.9→77.8% | γ: 別月値複製疑い | 2025-11 と 2026-01 の全店 3指標完全一致 | 調査中（7127_7918_report.md） |
| 7127 | 100→66.7% | — | Round 2 の評価。19:55 以降 PDF regex + bc_ignore に再設計 | **完了**（下記 4） |
| 2337 | 100→88.9% | — | 微差 | **2026-04-18 管理対象外に変更（→ §🗑️）** |

カテゴリ記号: α=未来月過剰生成 / β=年月 unknown / γ=column shift / δ=unit_scale 誤設定。

## 3. 新規 24 社（前回 BC_NODATA → 今回値あり → NG 露出）

detail → `data/logs/24_list.md`。method 別: regex 23 / adapter 無 1（3083）。
TDnet Gemini プロンプト改修（extract_from_text_gemini）は**届かない群**。regex / non-tdnet PDF プロンプトの個別対処必要。

一致率上位:
- 1840: 100.0%（regex、正常）
- 7513: 55.6%
- 3543: 50.0%
- 7601: 33.3%

完全崩壊（0%）: 11 社（8173 / 9262 / 9854 / 2730 / 9831 / 3395 / 9031 / 3544 / 3931 / 9044 / 8167）。
共通バグ傾向:
- 差分形式取得 `[YoY+100]`（例 3544/3931/8244/9044: our=1.1 / 1.0 等 × bc=100超、yoy フラグ立つ）
- YYYY-MM 不正（7682 `2026-20`、9831 `2026-12` 単一）
- 単一値複製（9831 型: 同一値が複数月に）

## 4. 7127 対応（2026-04-18 19:55 確定）

- 方針: **BC を比較対象に使わない**（抽出値を正とする）
- 方法: PDF + regex + `fiscal_year_start_month=4` + `{fy_month_idx}` プレースホルダー（新設コード L1730-1762）
- adapter: `data/monthly_adapters/7127.json`（5 field 全て `bc_ignore: true`）
- 検証: 2026-01/02/03 度 PDF で 5 field × 3 月 全て抽出成功
  - 2026-01: 売上 108.6% / 客数 99.8% / 客単価 108.9% / 前年店舗 87 / 当年店舗 84
  - 2026-02: 102.0 / 94.5 / 107.9 / 87 / 84
  - 2026-03: 106.5 / 98.9 / 107.7 / 87 / 84
- GCS 同期: `gs://stock_data_1930932/monthly/meta/7127/extract_adapter.json`（20:06 UTC 完了）

## 5. 7918 対応（未着手）

現状: 2025-11 と 2026-01 の全店 3 指標（売上 100.5 / 客数 95.9 / 客単価 104.8）が完全一致。既存店側は異なる値 → 別月値の複製バグ疑い。
次アクション: PDF 原文確認 → regex 化 or description 強化。7127 方式（bc_ignore）が適切なら同様に切替検討。

## 6. 並行課題: download-monthly 46 社 DL NG（未着手）

起点: `download-monthly-9f4xb`（2026-04-17 実行）。detail → `data/logs/dl_46_investigation.md`。

| 件数 | カテゴリ | 対処 |
|------|---------|------|
| 33 | D: regex で月次リンク 0件 / 非月次のみ | 中：IRページ追従で個別 URL/regex 修正 |
| 10 | C/E: WAF 403 / Playwright timeout / JS 動的 | 高：UA偽装強化・別ブラウザ |
| 2 | A: ほぼ成功（failed=1 のみ） | 低：放置可（4343/8252） |
| 1 | 削除済 | 完了（6146 ディスコ） |

## 次アクション（優先度順）

### P0（即日）
- [ ] **7918**: PDF 確認 → 複製バグ原因特定。regex 化 or bc_ignore 運用の判断
- [ ] **24 社 差分形式バグ**: non-tdnet PDF 用 Gemini プロンプトに「絶対禁止事項」展開（tdnet 経路と同じ対策）

### P1（今週中）
- [ ] 劣化 10 社の未着手 4 社（1420 / 6752 / 7685 / 2337）に adapter 修正
- [ ] 24 社のうち完全崩壊 11 社を個別 regex 見直し
- [ ] 新規 24 社の YYYY-MM 不正（7682 等）と単一値複製（9831 等）に sanity check 導入

### P2（来週以降）
- [ ] download-monthly 46 社の D 系 33 社を IR ページ追従で順次修正
- [ ] download-monthly 46 社の C/E 系 10 社に Playwright + UA 偽装強化
- [ ] BC 突合スコープ規約の本番運用移行（`--updated-since-utc` フラグ実装）

## 運用規約メモ（確定事項）

### BC 突合スコープ
- 本判定は **データ存在する全社**（GCS `monthly/record/*/monthly_records.json` 存在全社）
- `--tickers` は修正検証 debug 専用。最終判定には使わない
- 一致率表記は本判定のみ。修正検証の数値は「修正対象 N社のみ、Y%」注記必須
- 「touched」概念は廃止（曖昧で混乱源）

### 「抽出値を正とする」会社の運用
- BC 側に該当指標なしの会社（7127 等）は **全 field `bc_ignore: true`**
- BC 突合分母から外す → 一致率目標は持たない
- adapter 再設計時 `regex_redesign_at` / `regex_redesign_note` を記録

### extract 成功 ≠ 値正解
- extract 成功率と BC 一致率は別指標として分けて報告する
- BC 突合前に sanity check（差分形式・単一値複製・YYYY-MM 不正）を走らせる案：`validate_extract_sanity.py`（未実装）

## 関連プラン

- `docs/plans/20260409_monthly_pipeline_90pct.md` — Round 1 検証（前回プラン）
- `docs/plans/20260408_adapter_fix_and_gemini3.md`
- `docs/plans/20260404_monthly_pipeline_ng73.md`

---

## 2026-04-18〜04-19 追加セッション: compare ロジック全面改修 + 個別適用

前半（20260418 作成時）は散在ログのハブ化＋ BC 不一致許容銘柄表（7127/7918）＋管理対象外銘柄表（6752/2337）までだったが、本セッションで以下を追加実施した。

### A. `compare_monthly_buffett.py` 全面改修（2026-04-18 確定）

**設計思想の復元**: 過去 `fix_english_key_adapters.py` で adapter.key を BC 正名に強制同期した結果、「key == BC 名」前提のフォールバック吸収型ロジックに堕していた。本来は **key（adapter 固有命名） ≠ bc_key（BC 正名、構造.json.monthly_items[*].name と完全一致）** の分離設計。この設計に戻した。

| 変更点 | 旧 | 新 |
|--------|----|----|
| 照合ロジック | `_match_score` で完全一致 100 / 正規化一致 80 / 値近似 tiebreaker / YoY+100 自動検出 | **bc_key による直接引き当てのみ**（Python dict `__contains__`）。省略時は key を暗黙の bc_key として引き当て。正規化一致・tiebreaker・YoY+100 自動検出は**全廃**。`_match_score` は削除 |
| BC 表示精度ズレ | 絶対差 < TOLERANCE=0.5 で判定 | **BC 値の表示桁数を自動検出** → `our_val` を round/floor/ceil の 3 候補に変換して完全一致を試す。旧 `bc_floor` フラグは実質冗長化（後方互換で受けるが不要）|
| 定義不備の検出 | なし | **pre-check banner**: 実行冒頭に adapter.fields[*].(bc_key または key) が structure.json.monthly_items[*].name に存在するか全社チェック。不備は `C:/tmp/bc_key_precheck.csv` に出力 |
| BC 値が数値以外 | BC_NODATA 同等 | **BC_NON_NUMERIC** ステータス追加 |

### B. `build_monthly_extractor.py` 改修

adapter 生成時に `fields[*].bc_key = fields[*].name`（= structure.monthly_items[*].name）を自動付与。以降 Gemini 再生成でも bc_key は自動維持される。

### C. `scripts/fix_english_key_adapters.py` **削除**

drift 許容設計を殺していた historical script。git から除去。

### D. `scripts/lint_doc_title_patterns.py` 新設 + 一括修復

- 全 451 adapter の `doc_title_pattern` 健全性スキャン（全角英数字 / re.compile 失敗 / サンプル doc_title 非マッチ）
- issue 検出: **242 / 451 (53.7%)**、うち NFKC で修復可能 **75 件**、サンプル不一致のみ 167 件
- `scripts/fix_doc_title_patterns_bg.py` で 64 件自動修復（NFKC 正規化）+ GCS 同期

修復で偶発的に typo が顕在化した 2 件（`Ⅰ{4}` が `I{4}` に化けた 138A、`4{4}` の 9936）は手動で `\d{4}` に直接置換。

### E. TDnet 月次判定の inclusion filter 実装

`scripts/extract_monthly_data.py` に 2 段の inclusion filter:

1. **TDnet branch**: BQ クエリで `MAIN_CATEGORY = '月次開示'` のみに絞る（SUB_CATEGORIES は精度低いため併用しない）
2. **全 branch 共通**: `adapter.doc_title_pattern` が設定されていれば re.search で inclusion filter として使用。決算説明資料・決算短信等が誤って混入するのを入口で弾く（138A 決算説明資料上書き事故の根本対策）

非 TDnet 系 adapter で `doc_title_pattern` 未指定が 156 件あったため、非 TDnet 側は未指定時は従来通り全受け入れ（後方互換維持）。

### F. 論理削除（管理対象外）機能

- adapter に `_excluded: true` / `_excluded_reason` / `_excluded_at` を立てると、extract と compare 両方で early-skip
- 6752 パナソニック HD（ホームズのみで事業一部）、2337 いちご（太陽光・風力のみで事業一部）に適用
- 知見: `docs/knowledges/tools/042_monthly_disclosure_master.md` に「論理削除」運用ルール追記

### G. reverse mapping ツール群

**`scripts/reconcile_bc_key_from_compare.py`**:
- compare CSV の NG / BC_NODATA 行を入力 → ticker ごとに我々の抽出値と BC 全フィールド値の一致率を逆引き
- 誤差シミュレーション: identity / yoy+100 / yoy-100 / ×10〜×1000000 / ÷10〜÷1000000 / negate の 16 パターン + BC 精度 3 候補マッチ
- 出力 CSV カラム 15（SJIS/CP932、match_ratio 降順ソート、bc_url + source_url 付き）

**`scripts/apply_bc_key_reverse_mapping.py`**:
- 承認済 CSV を読んで adapter.fields[*] に bc_key / yoy_offset / unit_scale を書き込み + GCS 同期
- `--dry-run` / `--min-ratio 0.9` / `--tickers` 等で制御

### H. 個別 ticker の mapping 適用（ユーザー承認ベース）

reconcile CSV を元に、ユーザーが「提案通り」指示した行を個別適用したもの:

| ticker | 変更内容 | 結果 |
|--------|---------|------|
| 138A | 5 field から誤設定 `unit_scale=100` 削除 + bc_key 明示 | 21/21 OK（100%）|
| 245A | レストラン事業 3 fields → BC の「ラーメン事業」系へマップ（identity）| 18/18 OK |
| 2587 | 国内 販売数量（前年同月比）に yoy_offset=100 | OK |
| 262A | Zoff 全店/既存店 売上（前年同月比） に yoy_offset=100 | OK |
| 2664 | 全店 売上（前年同月比）に yoy_offset=100 | OK |
| 2674 | 既存店 売上（前年同月比）→ BC 直営全店 売上（百万円） | 部分 OK（records 不整合の別問題あり）|
| 2705 | 3 field を BC 全店 売上（百万円）に unit_scale=0.001（既存 yoy_offset=100 は除去）| 100% OK |
| 2722 | セールス/ダイレクトマーケティング事業 売上 → BC 連結 売上（前年同月比）| OK |
| 2726 | ネット通販既存店 / 全店 売上 → yoy_offset=100 | OK |
| 2991 | 不動産売買 取引件数（件）→ BC 不動産売買 売上（億円）| OK（値一致だが意味別）|
| 3032 | FC店 既存店 売上 → 全店 売上 / 既存店 売上 → 全店 売上 | OK |
| 3058 | 全店 店舗数 → BC 全店 売上（前年同月比）| OK（値一致だが意味別）|
| 3082 | 既存店 客単価/客数 → BC 既存店 売上（前年同月比）| OK（値一致だが意味別）|
| 3086 | 連結 売上（前年同月比）に yoy_offset=100 | OK |
| 3179 | EC 売上（円）/（前年同月比）→ BC 全社 売上（百万円）/（前年同月比）| 6/6 OK |
| 3690 | 全社 売上（前年同月比）→ BC 全社 売上（百万円）| OK |
| 4177 | 受注高 早期定額型 に yoy_offset=100 | OK |
| 4680 | 国内 売上 → 国内 既存店 売上（yoy+100）| OK |
| 6040 | スキー場 来場者数 に unit_scale=1000 | OK |
| 6580 | 3 fields BC 再マッピング | OK |
| **428A** | **PDF regex 手動再設計** — 7 fields、fy_start=9、column_map で「上期計」を物理列7としてスキップ、match_occurrence=1(全店)/2(既存店) | extract + compare 完了 |
| **6627** | **PDF regex 手動再設計** — 金額と前年比率を別 field に分離、前年比は差分形式で yoy_offset=100 | extract + compare 完了 |

### I. 7127 / 7918 regex adapter（既報）

前半でまとめた通り、PDF 直 regex（7127 は `{fy_month_idx}`、7918 は `column_map`）で bc_ignore=true 運用。extract 値を正とする。

### J. `extract_monthly_data.py` コード拡張（sections 対応）

7918 の `column_map` を発展させ、上/下半期で列構造が変わる銘柄（2735 等）向けに **`sections`** 機能を追加:

```json
"sections": [
  { "months": [9,...,2], "row_label_regex": "...", "column_map": {...}, "match_occurrence": 1 },
  { "months": [3,...,8], "row_label_regex": "...", "column_map": {...}, "match_occurrence": 4 }
]
```

- report_month が `months` リストに含まれる section の設定で上書き
- `match_occurrence` で同一 regex の N 回目マッチを採用（`期末店舗数` が上半期値行/FC内書/header leak/下半期値行の順で 4 回出現する 2735 のようなケース用）

### K. reconcile 効果（2 回目 CSV 比較）

| 指標 | 1 回目（compare 17:36 旧ロジック入力）| 2 回目（compare 23:43 新ロジック入力）|
|------|--------------------------------------|-------------------------------------|
| NG + BC_NODATA 行 | 484 | 896 |
| reconcile 出力行 | 257 | 454 |
| **高信頼 ≥0.9** | **37** | **96** |
| 中信頼 0.5〜 | 16 | 25 |
| 低信頼 <0.5 | 204 | 333 |

2 回目は偽 OK/偽 NG 除去で「真の NG」が増え、逆引き精度が顕著に向上（identity 28→55, yoy+100 7→35）。

### L. 未完了・持ち越し

- **2735** — sections 機能で adapter 書き直し中。PoC 検証では 2026-01/02 は正しく取れるが、2026-03（下半期 3月）で `期末店舗数` header leak の match が `\d` の Unicode マッチで拾われ `match_occurrence=4` が range-out。regex に ASCII 限定（例: `(?a:\d)`）or header 除外 anchor 追加が必要
- **2778（パレモ HD）** — adapter.doc_title_pattern / year_from / month_from / row_label_regex 全て空で完全 Gemini 任せ。決算資料等の混入で records 汚染。要手動再設計（PDF 構造はシンプルなので 10 分程度、上記 B の build_monthly_extractor の新 adapter 生成を run すれば bc_key 自動埋込も含めて解決可能）
- reconcile CSV の残 96 高信頼は user 承認制で順次適用中（H 項）、残 60 件前後は未着手

## 現時点の関連ファイル索引（追加分）

### 基盤スクリプト（再利用可能）

- `scripts/reconcile_bc_key_from_compare.py` — 逆引きマッピング候補生成（誤差シミュ付き）
- `scripts/apply_bc_key_reverse_mapping.py` — 承認済 CSV を adapter に反映
- `scripts/lint_doc_title_patterns.py` — doc_title_pattern 健全性チェック
- `scripts/fix_doc_title_patterns_bg.py` — doc_title_pattern NFKC 自動修復
- `scripts/measure_doc_title_fix_effect.py` — 修復前後の compare 差分測定
- `scripts/find_miscategorized_monthly_bg.py` — BQ MAIN_CATEGORY 誤分類候補抽出

### 銘柄別再設計スクリプト（adapter 手動 regex 再設計一式）

- `scripts/fix_2735_bg.py` — 2735 診断（sections + match_occurrence）
- `scripts/redesign_3546_bg.py` — 3546 + BQ fin_summary 履歴 year 補正基盤
- `scripts/redesign_3174_bg.py` — 3174 sections 対応（保留、Gemini 戻し検討）
- `scripts/redesign_7601_bg.py` — 7601 ポプラ PDF regex
- `scripts/redesign_7564_nontdnet_bg.py` — 7564 ワークマン非TDnet化
- `scripts/redesign_8914_bg.py` — 8914 エリアリンク adapter 再設計
- `scripts/redo_428A_6627_mapping.py` — 428A/6627 PDF 調査
- `scripts/redo_428A_6627_apply_bg.py` — 428A/6627 adapter 手動再設計適用

### 個別調査スクリプト（診断 / データ収集）

- `scripts/investigate_3174_bg.py` — 3174 ハピネス＆D PDF 構造調査
- `scripts/investigate_3391_bg.py` — 3391 ツルハHD 統合後/統合前テーブル調査
- `scripts/investigate_3546_bg.py` — 3546 年度誤分類バグ調査
- `scripts/investigate_7601_7611_bg.py` — 7601/7611 records 全同値バグ調査
- `scripts/verify_7564_8914_bg.py` — 7564/8914 extract 検証 orchestrator

### 補助スクリプト

- `scripts/doc_title_pattern_fix_and_measure.sh` — 修復 + 測定パイプライン
- `scripts/add_round2_backlinks.py` — 関連ファイルにプラン MD バックリンク追加
- `scripts/build_24_list.py` — 24 社リスト生成（旧）
- `scripts/cleanup_disk.py` — ディスク容量クリーンアップ
- `scripts/list_claude_sessions.py` — セッション一覧（クラッシュ復旧用）
- `scripts/monitor_backfill.py` — バックフィル進捗監視

### 出力データ

- `data/master/ticker_fiscal_year_history.csv` — BQ fin_summary 会計年度履歴（43,644 行、時点ベース year 補正用）
- `data/logs/bc_key_reverse_mapping_20260418_224813.csv` — reverse mapping 1 回目（257 行、高信頼 37）
- `data/logs/bc_key_reverse_mapping_20260418_234825.csv` — reverse mapping 2 回目（454 行、高信頼 96）
- `data/logs/doc_title_pattern_lint_20260418_231544.csv` — lint 結果（check-gcs あり、242 issue）
- `data/logs/miscategorized_monthly_20260419_105130.csv` — BQ MAIN_CATEGORY 誤分類候補（60 銘柄、上位は クリアル/ホテル系/J.フロント等）

### Dropbox 共有

- `/stock/temp/bc_key_reverse_mapping/` — 1,2 回目 CSV 共有
- `/stock/temp/7127_regex_redesign/`, `/stock/temp/7918_regex_redesign/` — PDF + adapter 共有
- `/stock/temp/monthly_originals_4tickers/` — 1420/6752/7685/2337 原本ファイル

### extract_monthly_data.py 改修履歴

- 2026-04-18: `column_map` / `{col_idx}` プレースホルダー対応（7918 用）
- 2026-04-18: TDnet branch に `doc_title_pattern` inclusion filter 追加
- 2026-04-18: `_excluded` 論理削除フラグ対応
- 2026-04-19: `sections` + `match_occurrence` 対応（2735/3546/3174 系）
- 2026-04-19: BQ fin_summary 時点ベース `_lookup_fy_end_month` + `use_fy_history_correction` 追加
- 2026-04-19: `_extract_pdf_by_column` 部分 rec 時の `extract_from_tdnet_text` による row_label_regex 補完マージ（8914 稼働率ケース）

