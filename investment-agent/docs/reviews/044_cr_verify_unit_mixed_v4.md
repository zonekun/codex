# コードレビュー: Phase 5 unit_mixed 検証プラン v4 + コード妥当性

- 日時: 2026-05-01 08:10 JST
- 対象: `docs/plans/20260501_060000_verify_unit_mixed_fix.md` (v4), `scripts/build_backlog_adapter.py` (`_detect_unit_mixed()` L280-287), `scripts/regenerate_backlog_unit_mixed_adapters.py`
- パターン: 2 (改修)
- レビュアー: Claude (code-reviewer runbook)
- 前回レビュー: `docs/reviews/043_cr_verify_unit_mixed_plan_and_prompt.md` (v2/v3 時点)

---

## 【サマリー】

- 変更の要約: v4 では CR-043 の High/Medium 指摘 6 件中 5 件が解消済み。最重要の Step 3（gemini_vision 98社必要性検証）がユーザーの「Gemini コスト削減」ニーズに応え、1社反証テストから98社全数 PDF DL + pdfplumber 分析に全面書き換えされた。20% 閾値による合否判定が導入された。
- 品質評価: **A** — ユーザーの3度の苦言を正確に反映し、検証の独立性・網羅性が大幅に改善。残存する問題は軽微。
- 主要リスク:
  1. `_detect_unit_mixed()` の「円安」「円建て」偽陽性が Step 5 で既知限界として明文化されたが、still-complex 104 社に紛れ込む false negative の定量的影響が不明
  2. Step 3a の「ラベル一意性」判定が regex 抽出可否の十分条件ではない（同名ラベルが無くてもテーブル構造が regex 不向きなケースがある）
  3. 98 社全数 PDF DL の GCS egress コストと実行時間の見積が未記載

---

## 【パターン2: 改修プラン評価】

### 妥当性

v4 のプランは真因（`_detect_unit_mixed()` の部分文字列バグが simple_table 企業を complex_table に誤分類し、不要な gemini_vision 分類を増やしていた）に正しく対処している。検証の軸が「Codex の自己申告を照合する」から「実 PDF を DL して独立検証する」に完全に移行しており、対症療法ではない。

v4 の最大の改善である Step 3 は、ユーザーの核心的な問いである「98 社は本当に Gemini が必要なのか？」に直接答える構造になっている。pdfplumber でテーブルラベルの一意性を自動判定し、regex 可能候補を定量的に洗い出すアプローチは、5 社サンプリングでの反証テスト（v3）よりも格段に説得力がある。

### 副作用・デグレードチェック

- [x] **修正前アダプタとの改善対比** (CR-043 Medium): v4 Step 2 方法 4.a-b に「修正前アダプタの extraction_method が gemini_vision かつ fields が空であることを確認」「修正により regex 化 + fields 定義追加 = 改善であることを定量的に示す」が追加済み。解消。
- [x] **field_count=0 退行チェック** (CR-043 High): v4 Step 3c に「98社全ての修正後アダプタの field_count を確認」「修正前アダプタの field_count と比較」「field_count が減少した社があれば個別調査」が追加済み。解消。
- [x] **104社サンプルサイズ** (CR-043 High): v4 Step 6 で 3社→5社に増加、Step 1 と合わせて計15社の再分類テスト。false negative 検出時の全数再スキャン追加ステップも明記。解消。
- [x] **Step 5 エッジケース期待結果** (CR-043 Medium): v4 Step 5 に全テストケースの期待結果・理由列が表形式で追加済み。「円安」「円建て」は True（既知の限界）と明記。解消。
- [ ] **Step 3 反証テスト 1社→3社** (CR-043 Medium): v4 では 1社→3社ではなく、98社全数に拡張されたため、この指摘は実質的に解消を超えて大幅改善されている。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **Step 3a のラベル一意性 ≠ regex 抽出可否**: 行ラベルが一意であっても、以下のケースでは regex 抽出が困難: (a) ラベルと数値が同一セルに結合されている（pdfplumber の表抽出精度依存）、(b) ヘッダ行が複数段で group 指定が必要、(c) 同一テーブル内に期別列が横並びで値の位置特定が必要。ラベル一意性は必要条件であるが十分条件ではない。Step 3b の手動検証 10 社でこれを補完する設計になっているが、Step 3a の自動判定結果を「regex 可能候補」と呼ぶのはやや楽観的。「regex 候補（要手動確認）」とするのが正確。
- [ ] **20% 閾値の根拠**: Step 3 の合否判定で「regex 可能候補 20% 未満なら gemini_vision 分類は概ね妥当」とあるが、この閾値の設計根拠が不明。98 社中 20 社が regex 可能なら、Phase 6 で 20 社分の Gemini Vision API コール（20 社 × 推定 $0.01-0.05/call）を節約できるが、それが「是正アクション必要」の閾値として適切かはコスト試算に依存する。閾値の設定理由を 1 行で注記すべき。
- [ ] **GCS egress コストの見積**: 98 社の PDF を全数 DL する場合の GCS egress を考慮すべき。決算短信 PDF は概ね 0.5-5MB/社。98 社なら最大 500MB 程度で、GCS egress の無料枠（1GB/月）内に収まる可能性が高いが、Step 1/2/4/6 の DL 分も合算すると超過の可能性あり。既に「1社ずつ DL して即削除」のディスク管理は明記済みだが、GCS egress の合計見積を一文追加すると安心。
- [ ] **Step 3a のテーブル抽出失敗ケースの判定**: Step 3 方法の箇条書き 3 番目に「テーブルが抽出できない（画像/チャート等）→ gemini_vision が必要」とあるが、pdfplumber が空テーブルを返すケース（テーブルはあるがセル認識に失敗）と本当にテーブルが存在しないケースの区別が曖昧。analyze_complexity 側では `table_count == 0` で `no_table` に分類しているが、Step 3a の 98 社は既に complexity が `complex_table`（= テーブルあり）なので、この条件に該当する社は理論上ゼロ。条件を削除するか、「relevant_pages 内の一部ページでテーブルが取れない場合」と限定すべき。

### 新規リスク

- **実行時間**: 98 社の PDF DL + pdfplumber 分析は、1 社あたり 3-10 秒（DL + open + extract_tables）として 5-16 分。Step 1/2/4/5/6 と合算すると検証全体で 30-60 分。双方向 LINE 会話モードで実行する場合、Step 間の報告タイミングを設計しないと、ユーザーが長時間待つことになる。
- **Step 3a で regex 候補が 0 社だった場合**: 判定基準は「20% 未満 → gemini_vision 分類は妥当」だが、0 社なら Step 3b（手動検証 10 社）が空集合となり、Step 3 全体が「pdfplumber で自動判定した結果、全 98 社で gemini_vision が必要」という結論になる。これ自体は問題ないが、その場合に Step 3 の最終結論をどう報告するか（「検証完了、修正の効果は 35/237 = 15% に留まる」等）を明記すべき。

---

## 【重大な指摘】（即修正）

### #1 `_detect_unit_mixed()` の「独立円」判定で false positive が運用上有害なケースが未評価

- 箇所: `scripts/build_backlog_adapter.py:280-287` + プラン Step 5 テストケース表 L137-138
- 事象: Step 5 で「円安の影響で受注高 1,500百万円」→ True を「既知の限界: 運用影響は軽微（complex_table フォールバック）」と結論付けているが、**104 社 still-complex のうちいくつが「円安」「円高」「円建て」等のコンテキスト文言に起因する false positive なのかを定量的に調査していない**。
- トリガー: 建設業の決算短信には「円安の影響」「円建て取引」「円高リスク」等の文言が頻出する。受注残高パイプラインの対象企業は建設・インフラ業が多く、これらの文言と `百万円` が同一ページに出現する頻度は相当高い可能性がある。
- 影響: 「運用影響は軽微」の判断が正しいかどうかが、104 社の内訳によっては覆る。仮に 104 社中 20+ 社がこの false positive のみで complex_table に留まっているなら、`_detect_unit_mixed()` の修正は不十分であり、追加修正（`円` の前後コンテキストチェック）が必要になる。
- 根拠: `_detect_unit_mixed()` L283-286 で `百万円`/`千円`/`億円` を strip した後に残る `円` は、「円安」「円建」「円高」「円滑」等の熟語内の `円` を拾う。建設業の決算短信では `百万円` + 為替関連文言の組み合わせが一般的。
- 推奨対応: Step 6（still-complex 5 社検証）の方法に「unit_mixed=True の原因となった具体的な文字列マッチを特定する（百万円 + 円安 等）」を追加。5 社中 2+ 社で「独立円」の false positive が原因なら、`_detect_unit_mixed()` への追加修正（`円` の前後を `\b` 相当でチェック）を是正アクションとして起票する。
  - 波及: Step 6 の方法セクション L155 への追加のみ。他ファイルへの波及なし。

### #2 `regenerate_backlog_unit_mixed_adapters.py` の `is_unit_mixed_only_candidate()` が structure.json の `_complexity_report` のキー不在でクラッシュしない保証が不十分

- 箇所: `scripts/regenerate_backlog_unit_mixed_adapters.py:162-180`
- 事象: `is_unit_mixed_only_candidate()` は `structure.get("_complexity_report") or {}` で None ガードしているが、`report.get("unit_mixed")` が `False`（正常な値）の場合に早期 return False する。一方、unit_mixed キー自体が structure.json に存在しない古い形式のデータでは `report.get("unit_mixed")` → `None` → `not None` → `True` → **unit_mixed=True として通過してしまう**。
- トリガー: Phase 5 初期に生成された structure.json が `_complexity_report` に `unit_mixed` キーを持っていない場合（形式変更前のデータ）。
- 影響: 本来候補でない ticker が候補に入り、不要な PDF DL + Gemini 呼び出しが発生する。データ破壊には至らないが、コスト面で問題。
- 根拠: L168 `if not report.get("unit_mixed"): return False` は `unit_mixed` が `None`（キー不在）、`False`、`0`、`""` のいずれでも `False` を返すので、**キー不在時は正しく弾かれる**。訂正: 脳内シミュレーションの結果、この指摘は誤りであった。`not None` = `True` → `not True` → return False しない、ではなく、`report.get("unit_mixed")` が `None` なら `not None` = `True` → `if True: return False` となる。**キー不在時は正しく弾かれる。** この指摘は撤回する。

→ **撤回**: `report.get("unit_mixed")` が `None`（キー不在）の場合、`not None` は `True` なので `return False` が実行され、正しく弾かれる。問題なし。

### #2 (実質) プラン Step 3a のプログラマティック分析が `analyze_complexity()` のロジックを**部分的にしか再現しない**

- 箇所: プラン Step 3a L79-83 + `scripts/build_backlog_adapter.py:290-340`
- 事象: Step 3a は「行ラベルの一意性」で regex 可能性を判定するが、`analyze_complexity()` の complex_table 判定は `broken_col0 or unit_mixed or table_count > 3 or max_cols > 12` の 4 条件の OR。98 社は既に修正後の `_detect_unit_mixed()` で unit_mixed=False になっているが、table_count > 3 や max_cols > 12 で complex_table に留まった社は含まれない（`is_unit_mixed_only_candidate()` で弾かれている）。したがって Step 3a の対象 98 社は全て「unit_mixed のみが原因で complex_table だった → 修正後は simple_table だが、Gemini が gemini_vision を選んだ」社のはず。
- 問題: 実は 98 社は「修正後 simple_table になった 133 社のうち、Gemini が gemini_vision に分類した」社ではなく、`regenerate_backlog_unit_mixed_adapters.py` の処理結果を見ると、133 社が再分類→ simple_table → Gemini に regex adapter 設計を依頼 → Gemini が `extraction_method=regex` と返した 35 社と `extraction_method=gemini_vision` と返した 98 社、という分岐。Step 3a のラベル一意性チェックは Gemini の判断を独立検証する意味があるが、**Gemini がなぜ gemini_vision を選んだのかの理由（notes フィールド）をまず確認してから pdfplumber 分析すべき**。notes を読めば regex 不可の理由が判明し、98 社全数 DL せずに済む可能性がある。
- 推奨対応: Step 3 の冒頭に「Step 3 事前確認: 98 社の extract_adapter.json の notes フィールドを GCS から一括取得し、Gemini が gemini_vision を選んだ理由を分類する（同名ラベル重複 N 社、セグメント分割 M 社、テーブル認識不可 K 社、etc.）。理由の分類結果を見て、regex 可能性が低い理由カテゴリの社は Step 3a の PDF DL 対象から除外する」を追加。これにより PDF DL 数を削減でき、GCS egress コストと実行時間を節約できる。

---

## 【改善提案】（可読性・保守性）

### #1 プランの「フォーマット適合性」— テンプレートとの乖離

- 箇所: `docs/plans/20260501_060000_verify_unit_mixed_fix.md` 全体
- 現状: このプランは `_template_refactor.md` の 7 フィールド形式（症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック）に従っていない。基準 commit hash、優先度定義（P0/P1/P2）、アンチパターン対応表、検証戦略（smoke/dev/prod/回収手順の 4 段）も欠落している。
- 提案: ただし、このプランは「改修/バグ修正」ではなく「検証手順書」であり、テンプレートの適用対象外と解釈できる。検証プランに固有のフォーマットは現状定義されていないため、フォーマット違反ではなく「適用範囲の明確化が必要」とする。今回は指摘のみに留め、検証プラン用テンプレートの策定は別途検討。

### #2 Step 5 テストケースの「円安」「円建て」の期待結果が「既知の限界」で済まされている

- 箇所: プラン Step 5 L137-138
- 現状: 「True を返すが運用影響は軽微（complex_table フォールバック）」と注記されているが、#1 で指摘した通り、still-complex 104 社における影響の定量評価が欠けている。Step 5 は単体テストなので「現行コードの挙動として True を返す」は正しい期待結果だが、「運用影響は軽微」は Step 6 の結果に依存する判断であり、Step 5 の段階で結論付けるのは早い。
- 提案: Step 5 テストケース表の「理由」列から「運用影響は軽微」を削除し、「Step 6 で実際の影響を評価」に差し替え。

### #3 Step 1 と Step 6 のサンプリング対象が重複する可能性

- 箇所: プラン Step 1 L27-31 + Step 6 L152-153
- 現状: Step 1 は「133 社からランダム 10 社」+ 「104 社から 5 社」、Step 6 は「104 社から 5 社」。Step 1 と Step 6 で 104 社側の 5 社が別の社になるかは明記されていない。同じ社を選ぶと検証範囲が狭まる。
- 提案: Step 6 に「Step 1 で選んだ 5 社とは別の 5 社を選定」と明記。合計 10 社（104 社中の 9.6%）で false negative 検証となり、統計的にもサンプル数が改善する。

### #4 `regenerate_backlog_unit_mixed_adapters.py` のコード品質

- 箇所: `scripts/regenerate_backlog_unit_mixed_adapters.py:39`
- 現状: `import build_backlog_adapter as phase5` は相対 import に近い記法で、`sys.path` に `scripts/` が含まれていることに依存。Codex 環境での実行は問題ないかもしれないが、CLAUDE.md のコーディング規約（モジュール import は明示的パスを使う）との整合性が曖昧。
- 提案: 検証プランの対象外（コード修正は検証プランのスコープ外）なので、本レビューでは記録のみ。

---

## 【修正例】（必要な箇所のみ）

修正例の提示は不要。指摘はプラン MD への注記追加のみで対応可能。

---

## 【確認できなかった事項】

- 98 社の extract_adapter.json の notes フィールドの実際の内容（Gemini が gemini_vision を選んだ理由の分布）。GCS からの読み取りは本レビューのスコープ外（code-reviewer は閲読系コマンドのみ許可、GCS API 呼び出し禁止）。
- 104 社 still-complex のうち、`_detect_unit_mixed()` の「独立円」false positive が原因で complex_table に留まっている社の実数。これは Step 6 の実行結果に依存する。
- 98 社全数 PDF DL の実行時間とGCSエグレスコストの実測値。概算は本文に記載済み。
- checkpoint.json における 237 社→133 社+104 社の分割の正確な内訳（Codex の自己申告値を信用していないため、Step 1 での独立検証で確認する設計は正しい）。

---

## 【CR-043 指摘解消状況】

| CR-043 指摘 | 深刻度 | v4 での対応 | 解消? |
|---|---|---|---|
| gemini_vision field_count=0 退行チェック欠落 | High | Step 3c に全数確認を追加 | **解消** |
| 104社サンプルサイズ不足 (3社) | High | Step 6 を 5 社に増加 + false negative 時全数再スキャン明記 | **解消** |
| Step 2 修正前との改善対比なし | Medium | Step 2 方法 4.a-b に修正前アダプタ比較を追加 | **解消** |
| Step 5 エッジケース期待結果不明 | Medium | テストケース表に期待結果・理由列を追加 | **解消** |
| Step 3 反証テスト 1社のみ | Medium | 98社全数に拡張 (1社→98社) | **大幅改善** |
| 6:40起動プロンプトの動作曖昧 | High | (起動プロンプト側の問題、プラン v4 のスコープ外) | 対象外 |

---

## 【総合判定】

### 検証プラン v4: 承認（軽微な注記追加推奨）

v4 は CR-043 の主要指摘を全て解消し、ユーザーの「Gemini コスト削減が本当に必要か確認せよ」というニーズに的確に応えている。Step 3 の 98 社全数 pdfplumber 分析は v3 の 5 社反証テストから質的に飛躍した改善。

実行前に推奨する軽微な追加:
1. Step 3 冒頭に notes フィールド事前確認ステップの追加（PDF DL 対象の絞り込み）
2. Step 6 に「Step 1 と別の 5 社を選定」の明記
3. Step 5 テストケース表の「運用影響は軽微」を「Step 6 で評価」に差し替え
4. Step 6 に「unit_mixed=True の原因文字列を特定」の追加

これらは全て注記レベルの修正であり、プランの構造変更は不要。
