# コードレビュー: extract_monthly_data.py — fields=[] アダプタのサイレントスキップ防止

- 日時: 2026-05-07 12:02 JST
- 対象: `docs/plans/refactor_extract_fields_validation_20260507_115942.md`
- パターン: 4 (新規計画 — `_template_refactor.md` 形式だが内容は設計提案段階のため P4 判定)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `phase_extract()` で adapter ロード直後に `fields=[]` チェックを追加し、新 error_type `adapter_no_fields` として明示的に分離する
- 品質評価: **A** — 根本原因分析が正確、修正範囲が最小限で副作用リスクが低い。フォーマット準拠も良好
- 主要リスク:
  1. `results["skip"]` と `_error_entries` の二重管理により、将来の集計ロジック変更時にカウント不一致が起こる余地（軽微）
  2. `042-1_monthly_error_fix_patterns.md` への新パターン追記が実装と同時に行われない場合、monthly-error-autofix が adapter_no_fields を「未知パターン」として扱う
  3. 既存の60社が「修正が必要なアダプタ」として可視化されるが、修正手順（build_monthly_extractor.py 再実行）の自動化や通知は計画外

---

## 【パターン4: 新規計画評価】

### 技術選定の妥当性

妥当。提案は既存の制御フローに 7 行の分岐を追加するのみで、新ライブラリ・新サービス・新テーブルを導入しない。問題の性質（入力検証の欠如）に対して最小限のアプローチであり、過剰設計の兆候はない。

`adapter.get("fields", [])` のデフォルト値 `[]` が既に多数の抽出関数（L243-245, L477, L607, L674, L1078, L1158, L1262, L1336, L1602, L2776 等）で使われており、各関数は `if not fields: return None` で自己防衛している。つまり **現状でもデータ破壊は起きていない**（正しく "no_records" になっている）。プランの主張する「サイレントスキップ」は技術的に正しいが、問題の深刻度は「分類精度の低下」であり「データ破壊」ではない点を確認できた。この正しい重要度認識がプラン内に反映されている（P0 定義が「品質保証ブロッカー」であり「データ破壊」ではない）。

### 既存システムとの統合

- [x] `phase_extract()` 内の制御フロー: 挿入位置（`_excluded` チェック後、`extraction_method` 取得前）が適切。L3313-3316 の「adapter なし」チェック、L3318-3323 の「excluded」チェックと同列の早期 continue パターンに統一されている
- [x] `_error_entries` リスト: 既存の `no_records` / `batch_failed` / `no_records_after_batch` と同構造の dict。`_save_extract_error_log()` (L3246-3261) は error_entries をそのまま JSON シリアライズするため、新 error_type の追加に追加コード不要
- [x] `results["skip"]`: 既存のスキップ分類に合流。サマリーログ (L4189) の `skip` カウントに自動的に含まれる
- [x] `validate_monthly_first_run.py`: `error_type` フィールドで分岐するロジックがない（grep 確認済み）ため、新 error_type による破壊なし
- [x] `monthly-error-autofix` スキル: error_type をキーにパターンDB検索するため、`042-1_monthly_error_fix_patterns.md` への追記が実装と同時に必要（プラン P1-1 で認識済み）

### リスク・コスト

- GCP課金影響: なし（チェックはメモリ内 dict 参照のみ）
- 処理時間影響: 無視可能（dict.get 1回 + list truthiness 判定）
- ディスク影響: なし
- 撤退基準: commit revert で完全復旧可能（プランに明記済み）

### 抜け漏れ

- [ ] **`042-1_monthly_error_fix_patterns.md` への追記タイミング**: P1-1 で言及しているが、「P0-1 と同一 commit で行う」のか「別タスク」なのかが不明確。monthly-error-autofix が未知 error_type を受けた際の挙動を確認し、同一 commit で追記することを推奨
- [ ] **月次バッチ結果サマリーの表示更新**: ログ出力 (L4189) は `skip` 合計のみ表示する。`adapter_no_fields` が何社あったかをサマリーに出さないと、修正対象の可視化という目的が「GCS error log JSON を直接見る」以外では達成されない。CLI の最終サマリーに `adapter_no_fields=N社` を追加表示する行が計画に含まれていない
- [ ] **batch_mode 時の挙動確認**: フィールド検証は `_src_norm` 分岐（TDnet/non-tdnet(pdf)/html_table）の**前**に挿入されるため、batch_mode の有無に関係なく動作する。これは正しい

### 目的・スコープの明確性

明確。「fields=[] のアダプタを明示的エラーとして分離する」「個別抽出関数の修正やadapter再生成は非スコープ」と明言されている。

### 段階的検証計画

4段階（smoke / dev / 本番適用判断基準 / 回収手順）が設計されている。各段階の成功基準が具体的（`adapter_no_fields` としてスキップされる / 正常アダプタの抽出結果が変わらない）で検証可能。

### 完了条件の検証可能性

smoke test のコマンドが具体的に記載されており、期待結果（error_type 値・ログメッセージ）も明確。検証可能。

### データカタログ整合

新規データを作成しない。GCS error log JSON の error_type vocabulary が拡張されるが、data_catalog.md への追記は不要（log は一時ファイル扱い）。

---

## 【重大な指摘】（即修正）

なし。計画の方向性・実装方針に重大な欠陥は検出されなかった。

---

## 【改善提案】（可読性・保守性）

### #1 サマリーログへの adapter_no_fields カウント追加

- 箇所: `scripts/extract_monthly_data.py:4189`（プラン非記載）
- 現状: `skip` の内訳が見えない。60社が adapter_no_fields でスキップされても合計数のみ表示
- 提案: L4189 付近のサマリーログに `adapter_no_fields={len([e for e in _error_entries if e['error_type'] == 'adapter_no_fields'])}` を追加。または `results` dict に `"adapter_no_fields"` キーを別管理して直接カウント

### #2 042-1 パターンDB追記の同一commit化

- 箇所: `docs/knowledges/tools/042-1_monthly_error_fix_patterns.md`
- 現状: P1-1 として「将来的に」と記載。同一リリースで行うか不明
- 提案: P0-1 の実装 commit に含める。パターンDB エントリ例:
  ```
  adapter_no_fields: adapter.fields が空リスト。build_monthly_extractor.py でアダプタ再生成が必要。
  対応: `--ticker XXXX` で build_monthly_extractor.py を再実行
  ```

### #3 warning ログの structlog 化確認

- 箇所: プラン P0-1 修正方針の `logger.warning(...)` 行
- 現状: `logger.warning("[%s] adapter fields=[] ...")` — %s フォーマット。プロジェクト全体が structlog を使う規約（CLAUDE.md §コーディング規約）
- 提案: 既存コード（L3314, L3350 等）が同様の `logger.info(f"...")` パターンを使っているため、現行の extract_monthly_data.py 内では標準 logging が実使用されている模様。プロジェクト規約との乖離だが、**本プランのスコープでは既存パターンに合わせる**のが正しい（部分的 structlog 化は逆に混乱を招く）。指摘は保留。

---

## 【フォーマット適合性チェック】

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか → `6dd20b7` 記載あり
- [x] 前提サマリで過去修正と残件数が明示されているか → 明示あり
- [x] 優先度の定義（P0/P1/P2 昇格基準）が冒頭にあるか → P0/P1 定義あり
- [x] 各項目が 7 フィールドを揃えているか → P0-1: 症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック 全揃い。P1-1 も同様
- [x] 修正方針に before/after の両方があるか → P0-1 に before/after コード対比あり
- [x] 呼び出し側への波及が該当行リストで明示されているか → L4059-L4067, validate_monthly_first_run.py を行番号付きで記載
- [x] 「既に〜がある」系の前提記述を実コードと照合 → L3318-L3326 の引用が実コードと一致（Read で確認済み）。L4059-L4067 の引用も一致
- [x] アンチパターン対応表が末尾にあるか → あり（P0-1: B-1, P1-1: 該当なし）
- [x] 検証戦略が 4 段を網羅しているか → smoke / dev / 本番適用判断基準 / 回収手順の 4 段あり
- [x] ロールバック手順があるか → 「commit revert で済む。データ破壊なし」明記
- [ ] 関連 commit・知見 MD・incident ログへのリンクがあるか → 知見MD 2件 + commit 記載あり。incident ログへのリンクは無いが本セッション内検出のためリンク対象が存在しない。問題なし

**フォーマット違反**: なし

---

## 【確認できなかった事項】

- `build_monthly_extractor.py` が再実行された場合に fields=[] が解消される保証（アダプタ生成ロジックの内部を未確認）。ただしこれは本プランのスコープ外（「adapter再生成ロジックは非スコープ」と明記）
- monthly-error-autofix スキルが未知の error_type を受けた際の具体的挙動（スキルMD内に error_type 分岐ロジックの grep で一致なし。おそらく「未知パターン → Layer2 調査」に移行するが確定できず）
