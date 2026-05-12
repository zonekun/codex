# コードレビュー: TDnet PDF テキスト抽出 ページマーカー閾値すり抜けバグ修正

- 日時: 2026-05-06 11:33 JST
- 対象: `docs/plans/tools-013_tdnet_load_20260506_112815.md`（プランMD） / `scripts/tdnet_load_parallel.py`（対象コード） / `src/llm/page_aware_text.py`（ユーティリティ）
- パターン: 2 (既存コード改修 / バグ修正)
- レビュアー: Claude (code-reviewer runbook)

---

## 【サマリー】

- 変更の要約: `[PAGE N]` マーカー文字列が `_MIN_TEXT_LEN` 閾値判定に含まれ、PyPDF2/pdfminer が実コンテンツを抽出できない PDF でもフォールバック（pdfminer / Gemini Vision）が発動しないバグの修正。`_content_length()` 関数を導入してマーカーを除外した実コンテンツ長で判定する。
- 品質評価: **A** — 根本原因の特定は正確で修正方針も妥当。ただし pdfminer 結果のマーカー汚染に対する判定箇所が 2 箇所漏れている。
- 主要リスク:
  1. pdfminer フォールバック結果にも `[PAGE N]` マーカーが含まれるが、L626/L1638 の判定が修正対象から漏れている
  2. `_content_length()` 内の `PAGE_MARKER_PATTERN.sub('', text).strip()` はマーカー間の改行を残すため、多ページ PDF で改行だけの文字列が残り微量の「実コンテンツ長」として計上される可能性
  3. P1-1 リカバリスクリプトの設計が概要レベルにとどまり、具体的な冪等性保証・並列度・Vision API 課金見積が未記述

## 【パターン2: 改修プラン評価】

### プラン MD フォーマット適合性チェック

- [x] 冒頭に対象ファイルの基準 commit hash が書かれているか → `4bf506e`
- [x] 前提サマリで過去修正と残件数が明示されているか → commit `cd9567c` / 約 2,100 件
- [x] 優先度の定義（P0/P1 昇格基準）が冒頭にあるか
- [x] 各項目が 7 フィールドを揃えているか
- [x] 修正方針に before/after の両方が書かれているか
- [x] 呼び出し側への波及が該当行リストで明示されているか — **ただし L626/L1638 が漏れ（下記 #1）**
- [x] 「既に〜がある」系の前提記述を実コードと照合 → `PAGE_MARKER_PATTERN` 存在確認済み
- [x] アンチパターン対応表が末尾にあるか
- [x] 検証戦略が smoke / dev / prod / 回収手順の 4 段を網羅しているか
- [x] ロールバック手順が書かれているか
- [ ] **フォーマット違反**: 呼び出し側波及リストに L626/L1638 が記載されていない

### 妥当性

根本原因の特定は正確。`_extract_text_pypdf2()` が `[PAGE N]` マーカーを付加するため、`page.extract_text()` が空文字を返しても結合後テキスト長がマーカー分だけ膨らみ、`_MIN_TEXT_LEN = 50` を超えてフォールバックが発動しない。プランの修正方針（`_content_length()` でマーカーを除外して判定）はこの真因に直接対処しており、方向性は妥当。

独立に推定した根本原因も同一: commit `cd9567c` で `[PAGE N]` マーカーを導入した際に、閾値判定のセマンティクス（「実コンテンツが十分か」）を更新せず `len(text)` のままにした設計漏れ。対症療法ではなく根本対処と判断する。

### 副作用・デグレードチェック

- [x] `_content_length()` 適用により、従来フォールバックが発動しなかったケースで pdfminer / Vision が発動するようになる。これは意図した動作であり、既存の正常系（テキスト抽出成功ケース）には影響しない。正常系では実コンテンツ長 >> 50 のため判定結果は変わらない
- [x] `_content_length()` は新関数であり、既存関数の signature / 戻り値型を変更しない。呼び出し元への型的波及は無し
- [x] `PAGE_MARKER_PATTERN` を `src/llm/page_aware_text.py` から import する設計は、既存の `__all__` に公開済みのシンボルを使うため互換性問題なし
- [x] Phase 2 Vision 結果の L804 判定はマーカーが含まれないため変更不要 — プランの判断は正しい
- [x] BQ 既存データとの互換性: 修正はテキスト抽出ロジックのみ。既存 BQ 行のスキーマ変更なし

### 抜け漏れ（類似観点での横展開含む）

- [x] **#1（重大）**: `_extract_text_pdfminer()` (L407-435) も `[PAGE N]` マーカーを挿入する（L432: `buf.append(f"[PAGE {idx}]\n{normalized}")`）。したがって L626 の `if len(text_pm) >= _MIN_TEXT_LEN:` と L1638 の同判定も、マーカー込みの長さで判定しており同じバグが存在する。プランの修正対象 4 箇所（L624, L631, L1636, L1645）にはこの 2 箇所が含まれていない。漏れを追加すべき
- [ ] リカバリスクリプト（P1-1）で Gemini Vision API を使う場合の課金見積が未記述。2,100 件全てに Vision が必要になるわけではないが、pdfminer でもテキストが取れない件数の想定と、それに対する Vision API コスト概算があるとリスク判断しやすい
- [ ] `_content_length()` の配置先について、module-level import への移動が明記されているが、`src/llm/page_aware_text.py` は既に `scripts/tdnet_load_parallel.py` の imports に含まれているかの確認が必要（現状含まれていない場合、新規 import 追加が必要）
- [ ] 013_tdnet_load.md（親知見MD）の更新がプランに含まれていない。T-1〜T-6 に本バグの再発防止ルール（T-7 相当: 前処理で付加したメタデータが後段の品質閾値を汚染しないよう、閾値判定は実コンテンツ長で行う）の追記を検討すべき

### 新規リスク

- `_content_length()` で `PAGE_MARKER_PATTERN.sub('', text)` を適用した後に `.strip()` するが、マーカー間の改行文字（`\n\n`）は `strip()` では全て除去されない（先頭末尾のみ）。例えば 10 ページの PDF で PyPDF2 が全ページ空文字を返した場合、マーカー除去後に `\n\n\n\n...` が残り `len()` が 18 程度になる。`_MIN_TEXT_LEN = 50` を下回るため実害は無いが、ページ数が 50 以上の場合は改行だけで 50 を超える可能性がある。`.strip()` の後に更に空白行を圧縮するか、`len(text.strip())` ではなく非空白文字数でカウントする方がロバスト
- P1-1 リカバリで `--reextract` モードを `tdnet_load_parallel.py` に追加する場合、既存の `--job-mode` 引数体系（`load` / `ai-prepare` / `ai-finalize`）との整合性設計が必要。新モードの追加はスクリプトの複雑性を増すため、スタンドアロンスクリプト（`tmp_reextract_failed_docs.py`）の方がリスクが低い

---

## 【重大な指摘】（即修正）

### #1 pdfminer フォールバック結果の閾値判定にもマーカー汚染がある（2 箇所漏れ）

- 箇所: `scripts/tdnet_load_parallel.py:626` および `scripts/tdnet_load_parallel.py:1638`
- 事象: `_extract_text_pdfminer()` (L407-435) は L432 で `buf.append(f"[PAGE {idx}]\n{normalized}")` としてマーカーを挿入する。したがって pdfminer の結果 `text_pm` にも `[PAGE N]` マーカーが含まれ、`len(text_pm) >= _MIN_TEXT_LEN` は PyPDF2 と同じくマーカー分で膨らんだ長さで判定される。
- トリガー: PyPDF2 が空テキストを返し、かつ pdfminer も同様に空テキストを返す PDF（画像 PDF や暗号化 PDF）。pdfminer のマーカーだけで `_MIN_TEXT_LEN` を超え、pdfminer の空テキスト（マーカーのみ）が「成功」扱いになる。
- 影響: pdfminer で実コンテンツが取れていないのに pdfminer テキストが採用され、`needs_vision = False` のまま Vision OCR が発動しない。結果としてマーカーのみのテキストが BQ に格納される（P0-1 と同根のデータ欠損）。
- 根拠: `_extract_text_pdfminer()` L430-432 を参照。`raw_pages = raw.split("\x0c")` でページ分割した後、各ページに `f"[PAGE {idx}]\n{normalized}"` を付加。pdfminer が全ページ空テキストを返した場合でもマーカー文字列は生成される。
- 推奨対応: プランの修正対象に L626 と L1638 を追加し、`if len(text_pm) >= _MIN_TEXT_LEN:` を `if _content_length(text_pm) >= _MIN_TEXT_LEN:` に変更する。プランの「呼び出し側への波及」セクションにもこの 2 行を追記する。

---

## 【改善提案】（可読性・保守性）

### #1 `_content_length()` の改行残留対策

- 箇所: プラン P0-1 の `_content_length()` 関数定義
- 現状: `PAGE_MARKER_PATTERN.sub('', text).strip()` はマーカーを除去するが、マーカー間の改行（`\n\n`）は中間に残る。50 ページ以上の PDF ではマーカー除去後の改行だけで 50 文字を超えうる。
- 提案: 改行・空白も除去した「非空白文字数」でカウントするか、マーカー除去後に連続空行を圧縮する。例: `len(PAGE_MARKER_PATTERN.sub('', text).split())` で単語数ベースにするか、`len(re.sub(r'\s+', '', PAGE_MARKER_PATTERN.sub('', text)))` で非空白文字数にする。TDnet PDF は実コンテンツがあれば数百〜数万文字になるため、閾値 50 との比較では「非空白文字数」が最もロバスト。

### #2 再発防止ルールの知見 MD 追記

- 箇所: `docs/knowledges/tools/013_tdnet_load.md` §T-1〜T-5
- 現状: T-6 までしか定義されていない。今回のバグパターン（前処理で付加したメタデータが後段の品質閾値を汚染する）は T-1〜T-6 のいずれにも該当しない新規パターン。
- 提案: T-7 として追記。内容例: 「`_extract_text_pypdf2` / `_extract_text_pdfminer` が返すテキストには `[PAGE N]` マーカーが含まれる。テキスト品質の閾値判定（`_MIN_TEXT_LEN` 比較）では `_content_length()` を使い実コンテンツ長で判定する。`len(text)` 直接比較は禁止」。記載先: `docs/knowledges/tools/013_tdnet_load.md` §T-1〜T-5 テーブルに T-7 行を追加。

### #3 リカバリスクリプトの設計補強

- 箇所: プラン P1-1
- 現状: リカバリの概要手順（4 Step）と dry-run 方針は記述されているが、以下が未記述: (a) 並列度設計（GCS DL + pdfminer は I/O bound だが Vision API にはレート制限あり）、(b) Vision API 課金の概算、(c) スクリプトの配置（`scripts/` vs `C:\tmp\` 一時スクリプト）
- 提案: P1-1 に補足セクションを追加し、(a) `ThreadPoolExecutor(max_workers=10)` 程度で GCS DL + テキスト抽出を並列化、Vision 対象は逐次またはバッチ API、(b) Vision API 課金は 2,100 件のうち推定 30-50% が Vision 必要 → 600-1,000 件 × $0.0025/page → $1.5-5 程度、(c) 一時スクリプトとして `C:\tmp\` に配置し完了後削除、を記載

---

## 【確認できなかった事項】

- pdfminer が全ページ空テキストを返す頻度の実測値（PyPDF2 と pdfminer が同時に空テキストを返すケースがどの程度あるか）。BQ で `EXTRACT_METHOD = 'pdfminer'` かつ `TEXT_LENGTH < 300` の件数を確認すれば判明するが、本レビューではクエリ実行不可
- `_MIN_TEXT_LEN = 50` の閾値自体が適切かどうか。マーカー除外後の実コンテンツ長 50 文字は、日本語テキストとして約 25 文字（半角換算）であり、1 文程度。もう少し高い閾値（例: 100-200）の方が偽陽性を減らせる可能性があるが、既存の正常系への影響を実データで検証しないと判断できない
- P1-1 リカバリで「TEXT_LENGTH < 300」を対象条件にしているが、300 の根拠（マーカーのみで 300 に達するには何ページ必要か等）が不明。`[PAGE N]\n\n` は約 12 文字/ページなので 25 ページ程度で 300 を超える。この閾値で全ての対象をカバーできるか
