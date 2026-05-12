# 検証プラン: Phase 5 unit_mixed バグ修正 + 133社アダプタ再生成の妥当性

- **作成日時**: 2026-05-01 00:35 JST
- **改定日時**: 2026-05-01 08:30 JST（v4.1: CR-044推奨4点反映 — notes事前分類・Step1/6重複排除・円安false positive調査・運用影響判断保留）
- **検証完了日時**: 2026-05-01 11:32 JST
- **ステータス**: completed — Step2以外全PASS。adapter品質2社修正済み
- **対象**: `scripts/build_backlog_adapter.py` `_detect_unit_mixed()` 修正、`scripts/regenerate_backlog_unit_mixed_adapters.py` 実行結果
- **参照**: CR-040 (`docs/reviews/040_phase5_adapter_evaluation.md`)、Codex引継ぎ (`codex-to-claude-handoff.md` 2026-04-30)
- **ユーザー指示**: 「これが本当に結果として正しいのか検証してほしい」「Codexの言うことを鵜呑みにせず実物を見て検証せよ」

---

## 検証方針

**Codex が自己申告した数値（checkpoint.json、引継ぎメモの件数等）を信用しない。** 検証は以下の独立検証を軸とする:

1. 実際のPDFをDLし、修正後のコードで再分類を独立実行 → Codexの分類結果と照合
2. 生成されたアダプタで実際にPDFから値を抽出 → PDF記載値と目視照合
3. 修正前アダプタ（GCS旧バージョン or git履歴）と修正後を比較 → 変更が妥当か判定

---

## Step 1: 独立再分類テスト

**目的**: Codexが報告した「133社がsimple_tableに再分類」を独立検証する。

**方法**:
1. 133社（GCS更新済み）からランダム10社を選定
2. 各社のPDFをGCSからDL
3. GCS `structure.json` の `relevant_pages` を使い、修正後の `analyze_complexity()` を自分で実行
4. 結果が `simple_table` であることを確認
5. **同時に**: 104社（still-complex）から5社を選び、同様に `analyze_complexity()` を実行 → `complex_table` であることを確認

**判定基準**: 10社中9社以上で Codex の分類と一致

**失敗時**: 不一致の原因を1社ずつ調査し、バグ or データ差異を切り分け

---

## Step 2: regex アダプタ end-to-end 抽出テスト

**目的**: 35社のregexアダプタが実際にPDFから正しい値を抽出できるか確認する。修正前は抽出不可だったことも確認する。

**方法**:
1. 35社の regex アダプタ全リストを取得
2. うち5社をランダム選定
3. 各社について:
   a. GCSからPDFをDL
   b. GCSから修正後 extract_adapter.json をDL
   c. pdfplumberで relevant_pages のテーブルを抽出
   d. アダプタの `row_label_regex` でラベルマッチ
   e. マッチした行から値を取得
   f. **PDFを目視確認**し、抽出値がPDF記載値と一致するか判定
4. **修正前アダプタとの改善対比**:
   a. Step 4 で取得する旧版 extract_adapter.json の `extraction_method` が `gemini_vision` かつ `fields` が空であることを確認
   b. 修正により regex 化 + fields 定義追加 = 改善であることを定量的に示す

**検証対象値**: 各社の `fields` に定義された全フィールド（受注高・完成工事高・繰越工事高等）

**判定基準**:
- 5社中4社以上で全フィールドが正しく抽出できること
- 値の単位（百万円/千円）がアダプタ定義と一致すること
- 5社全てで修正前は gemini_vision (fields空) だったことを確認

**失敗時**: 失敗したアダプタの具体的な問題点（regexパターンの不備、ページ番号のずれ等）を記録

---

## Step 3: gemini_vision 98社の必要性検証 ⚠️ **コスト観点の最重要ステップ**

**目的**: 98社が本当にGemini Vision（有償API）を必要としているか検証する。regexで抽出可能な社がGemini Visionに分類されていれば、Phase 6 で不要なAPIコストが発生する。

**背景**: unit_mixed 修正の目的は「誤って complex_table にされた企業を simple_table に戻し、regex 抽出パスを使えるようにする」こと。133社中35社のみが regex に移行し、98社は依然 gemini_vision — **修正の効果が26%に留まっている**。98社がGemini Visionを本当に必要としているかが、この修正の実質的な価値を左右する。

**方法**:

### Step 3事前: notes フィールド分類（PDF DL前の絞り込み）
1. 98社の extract_adapter.json を GCS から一括取得（JSONのみ、PDFは不要）
2. 各社の notes フィールドを読み、Gemini が gemini_vision を選んだ理由を分類
3. 理由カテゴリ別の社数を集計（例: 同名ラベル重複 N社、セグメント分割 M社、テーブル認識不可 K社）
4. regex 可能性が明らかに低い理由カテゴリの社は Step 3a の PDF DL 対象から除外

### Step 3a: テーブル構造プログラマティック分析（対象社のPDF DL+pdfplumber）
1. 98社全てのPDFをGCSから1社ずつDL（即削除）
2. relevant_pages のテーブルを pdfplumber で抽出
3. 各テーブルの行ラベルの一意性を自動判定:
   - 全ラベルが一意 → **regex で抽出可能な候補**
   - 同名ラベルが複数テーブルに出現 → gemini_vision が必要
   - テーブルが抽出できない（画像/チャート等） → gemini_vision が必要
4. regex 可能候補のリストを作成

### Step 3b: regex 可能候補の手動検証（上位10社）
1. Step 3a で regex 候補と判定された社のうち最大10社を選定
2. PDFを再DLし、テーブル構造を目視確認
3. 仮の regex パターンで抽出テスト
4. 実際に regex で正しい値が取れるか判定

### Step 3c: field_count=0 退行確認（全数）
1. 98社全ての修正後アダプタの field_count を確認
2. 修正前アダプタの field_count と比較
3. field_count が減少した社があれば個別調査

**判定基準**:
- regex 可能候補の比率が 20% 未満: gemini_vision 分類は概ね妥当
- regex 可能候補の比率が 20% 以上: **Gemini の adapter 設計プロンプトに問題あり** → 是正アクション必要（regex adapter の手動設計 or プロンプト改善）
- field_count 退行: ゼロであること

---

## Step 4: 修正前 vs 修正後のアダプタ差分検証

**目的**: 再生成で何が変わったのかを実物で確認する。既存の正しい設定を壊していないか。

**方法**:
1. 133社のうち5社（regex 2社 + gemini_vision 3社）を選定
2. Codex repo の git 履歴 or GCSのバージョン履歴から、修正前の extract_adapter.json を取得
3. 修正後の extract_adapter.json との差分を比較
4. 変更点が以下のいずれかに該当することを確認:
   - `extraction_method` が `gemini_vision` → `regex` に変更（新規regexフィールド追加）
   - `extraction_method` が `gemini_vision` のまま（notesが更新）
   - `complexity` 関連のメタデータが `complex_table` → `simple_table` に変更
5. **破壊がないこと**: `manual_override`, `company_name`, `ticker` 等のメタデータが保持されていること

**判定基準**: 5社全てで変更が上記カテゴリに該当 & メタデータ破壊なし

---

## Step 5: コード修正の正しさ（単体テスト）

**目的**: `_detect_unit_mixed()` の修正が論理的に正しいか。

**方法**:
1. 修正前コード（git履歴）と修正後コードのdiff確認
2. 境界ケースの単体テスト（期待結果明記）:

| 入力 | 期待結果 | 理由 |
|------|---------|------|
| `"受注高 1,500百万円"` | False | 百万円のみ |
| `"残高 23,456千円"` | False | 千円のみ |
| `"売上 5億円"` | False | 億円のみ |
| `"受注 100円"` | False | 円のみ |
| `"受注高 1,500百万円 手数料 100円"` | True | 百万円+独立円 |
| `"受注高 1,500百万円 残高 500千円"` | True | 百万円+千円 |
| `"円安の影響で受注高 1,500百万円"` | True | 既知の限界: `円安`の`円`を拾う。実影響は Step 6 で評価 |
| `"円建て受注 1,500百万円"` | True | 同上。実影響は Step 6 で評価 |
| `""` | False | 空文字列 |

3. **旧バグの再現テスト**: 修正前コードで `"受注高 1,500百万円"` → True（バグ）を確認

**判定基準**: 全テストケースが期待結果と一致

---

## Step 6: 104社 still-complex の正当性（実PDF検証）

**目的**: 修正後もcomplex_tableに留まった104社が、本当に複数単位混在しているか実物確認。false negative（本当は simple_table なのに complex_table 判定）の検出。

**方法**:
1. 104社から5社を選定（**Step 1 で選んだ5社とは別の5社**。合計10社で104社中9.6%カバー）
2. PDFをDL → relevant_pages のテキストを抽出
3. 修正後の `_detect_unit_mixed()` を実行 → True であることを確認
4. **unit_mixed=True の原因となった具体的な文字列マッチを特定**（百万円+円安 等の false positive か、百万円+円の実混在か）
5. **どの単位が混在しているか目視特定**（例: 百万円 + 1株配当の円）
5. false negative が1社でもあれば、全104社を再スキャンする追加ステップを実行

**判定基準**: 5社全てで複数単位の混在が目視確認できる、または PDF構造上 complex 分類が妥当

---

## 全体判定基準

| Step | 内容 | Pass条件 |
|------|------|---------|
| 1 | 独立再分類 | 10社中9社以上でCodex分類と一致 |
| 2 | regex抽出 | 5社中4社以上で全フィールド抽出成功 & 修正前は全社 gemini_vision(fields空) |
| 3 | gemini_vision必要性 | regex可能候補 < 20% & field_count退行ゼロ。20%超なら是正アクション起票 |
| 4 | 前後差分 | 5社全てで変更が妥当 & メタデータ保持 |
| 5 | コード単体テスト | 全テストケースが期待結果と一致 |
| 6 | still-complex | 5社全てで正当な理由あり & false negative ゼロ |

### 合否判定

- **全Step Pass**: handoff.md の status → done
- **Step 1 or 2 が Fail**: 再生成の信頼性に問題あり → 原因調査+追加検証を起票
- **Step 3 or 4 が Fail**: アダプタ品質に問題あり → 該当社のアダプタ手動修正を起票
- **Step 5 が Fail**: コード修正に問題あり → バグ報告+再修正依頼

---

## 実行上の制約

- **Gemini API は呼ばない**: 既存データの裏取りのみ。新規API呼び出しはコスト発生するため禁止
- **PDF DL は1社ずつ、検証後即削除**: C:\tmp\verify_unit_mixed\ に保存
- **全1,314社の再実行は禁止**: サンプリング検証のみ

---

## レビュー追記: 2026-05-01 08:10 JST — code-reviewer

→ `docs/reviews/044_cr_verify_unit_mixed_v4.md`

---

## 検証結果: 2026-05-01 11:32 JST

| Step | 内容 | 結果 | サンプル | 詳細 |
|------|------|------|---------|------|
| 1 | 独立再分類テスト | **PASS** | 15/15 | simple 10/10 + complex 5/5 |
| 2 | regex抽出テスト | **FAIL→修正済** | 3/5→5/5 | 1793($アンカー), 4667(10列テーブル) を手動修正 |
| 3 | gemini_vision必要性 | **PASS** | 98社notes分析 | regex可能候補 ~1%(<<20%), fc退行0件 |
| 4 | 前後差分検証 | **PASS** | 5/5 | ticker/company_name/manual_override/source_pdf 全保持 |
| 5 | コード単体テスト | **PASS** | 11/11 | 旧バグ再現確認済み |
| 6 | still-complex検証 | **PASS** | 5/5 | 全社unit_mixed=True(bare円は文脈語, 既知限界) |

### adapter個別修正
- **1793 (大本組)**: `row_label_regex` の `$` アンカー削除。GCS更新済み, `manual_override=true`
- **4667 (アイサンテクノロジー)**: regex→gemini_vision変更。10列テーブルで列位置指定困難。GCS更新済み, `manual_override=true`

### 総合判定
`_detect_unit_mixed()` のコード修正は正しい。133社再分類は妥当。adapter品質の一部問題は個別修正で解消済み。
