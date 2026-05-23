# 210_cr_orders_soldier_pymupdf_plan

**提出日**: 2026-05-18
**提出者**: Claude (メインエージェント)
**レビュースキル**: code-reviewer
**レビューパターン**: 2（既存改修）

---

## レビュー対象ファイル

| パス | 役割 |
|------|------|
| `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md` | 改修プラン MD（PyMuPDF 経路一本化 + 既知問題是正） |

参考情報（編集対象外、コンテキスト用）:
- `skills/orders_soldier.md` — 改修対象 spec（493 行、commit a8515859 時点）
- `skills/orders_commander.md` — 連動改訂対象（本レビュー対象外、別プランで対応予定）
- `.claude/commands/orders-soldier.md` — ラッパー（変更なし）
- 過去レビュー: 204 / 206 / 207
- 実機検証データ: `C:/gdrive/claude/work/_index/orders_index.csv` (60件) + `orders_log.tsv` (実走ログ)
- 167A検証PDF: `C:/Users/zonekun/Dropbox/stock/temp/167A_verification/` (3件)

---

## 事象・背景

### なぜ改修するか

2026-05-18 に build 30件 + resume 32件の実走を行った結果、以下 2 大問題が明確化:

1. **トークン浪費**: 60 件 build の総トークン消費が数百万〜千万トークン規模。偽陽性銘柄 (24/60 = 40%) も全 PDF を LLM 直読してから failed_no_data 判定するため。
2. **CHUNK_TEXT 代替案の不成立**: 「決算短信は BQ CHUNK_TEXT で前処理」案を検証したが、**167A 事例で CHUNK_TEXT が PDF 全文をカバーしないことが判明**（PDF 内に「受注の実績」セクションがあるのに CHUNK_TEXT に含まれず）。CHUNK_TEXT 経路は本質的に取りこぼし発生。

ユーザー判断: **PyMuPDF 経路一本化**で進める。GCS 費用・時間は気にせず、token のみ最小化。

加えて、2026-05-18 実走で観察された 5 件のソルジャー spec 違反も同時是正:
- 166A: docs_with_data=13 を返却（spec 上 max=6）
- 153A: note に spec ドメイン外キー `saas_subscription_business` 付与
- 166A 初回: 「並行 agent 競合心配」で処理中断（spec 違反、prompt 強化で復帰）
- HIGHLIGHTS 5 行超の散発（1417 / 166A / 150A）
- DOCS_WITH_DATA と「JSON d[] 要素数」の混同

### 改修方針

a. **PyMuPDF 経路一本化** (P0-1): gsutil cp + PyMuPDF テキスト抽出 + キーワードヒットページ特定 + 該当ページのみ Read tool。偽陽性銘柄は LLM 呼び出しゼロで判定
b. **BQ MAIN_CATEGORY 除外** (P0-2): 月次開示 / 業績修正 / 第三者割当・公募増資 / 中期経営計画 / 事業計画（グロース） を除外
c. **DOCS_WITH_DATA cap 明示** (P0-3): max=DOCS_READ ≤ DOCS_FOUND ≤ 6 を強調
d. **note ドメイン外キー徹底排除** (P1-1)
e. **並行 agent 競合心配排除徹底** (P1-2)
f. **HIGHLIGHTS 上限徹底** (P1-3)
g. **キーワードリスト spec 内明示** (P1-4): DRY 化
h. **PyMuPDF 依存パッケージ requirements 明記** (P1-5)

### 設計上の重要決定

1. **PyMuPDF 経路の決定根拠**: 167A 事例で CHUNK_TEXT は PDF 全文をカバーしないと判明。ユーザー指示「トークン削減優先、GCS 費用・時間は無視」に合致
2. **キーワード正規表現は ユーザー確定リスト** (前ターン採用済): 50 トークン超、構造化スコア付き不要
3. **スペース除去前処理**: PyMuPDF 抽出テキストの「受 注 の 実 績」スペース挟み対策（167A 事例で再現性確認済み）
4. **コマンダー連動改訂は別プラン**: 本プランはソルジャー側のみ

---

## レビュー観点

### 1. PyMuPDF 経路の実装妥当性

- Step 4 で `python -c "import fitz; ..."` をインラインで実行する設計が、Windows + Git Bash 環境で安定動作するか
- 中間ファイル `.pages.json` の管理（temp 配下、PDF 削除と同時にクリーンアップ要否）
- PyMuPDF が大きな PDF (10MB+ スキャン PDF) で OOM や長時間処理にならないか
- ハートビート更新タイミング（PyMuPDF テキスト抽出直前 / Read tool 呼び出し直前）の挿入位置妥当性
- キーワードヒットゼロで「即 failed_no_data」とするロジックの誤判定リスク（PDF 内に受注情報があっても画像化されていれば PyMuPDF テキスト抽出ゼロ → 誤skip の可能性）

### 2. BQ クエリ改修の整合性

- WHERE 句に `MAIN_CATEGORY NOT IN (...)` 追加で他カテゴリ（その他（未分類）/ 訂正 / 業績予想 / 自己株式取得 等）が漏れずに対象継続することを担保しているか
- コマンダー側 BQ クエリ（build モード）との連動が「別プラン対応」と明記されているが、整合性のずれリスクが残らないか
- 5 カテゴリ除外で対象 ticker 数が大幅変動しないか（既存 build で 60 件 → 改修後で何件減るか試算なし）

### 3. DOCS_WITH_DATA cap の妥当性

- 「DOCS_WITH_DATA ≤ DOCS_READ ≤ DOCS_FOUND ≤ 6」の不等式が、`completed_partial` 判定ロジックと整合しているか
- ソルジャー実装側で「JSON の d[] 要素数」と「数値抽出 PDF 数」を**どこで明示的に分離するか**の指示が plan に含まれているか

### 4. note ドメイン規約強化

- 「コマンダー側のパース失敗例」追加で抑止力が十分か
- 既存 153A の note 違反例を spec 中に**反面教師**として記載すべきか（spec 肥大化リスク vs 抑止効果）

### 5. 並行 agent 競合心配排除

- 強化文に追加した「task-notification や JSONL ログで他 ticker の処理進行が見える場合があるが」が現実の harness 仕様と一致しているか
- 「コマンダーは 1 ticker = 1 ソルジャーで起動する規約」がコマンダー spec で本当に保証されているか

### 6. HIGHLIGHTS 上限

- 「コマンダーは破棄」が事実か（コマンダー spec で確認）
- 5 行制限が複雑銘柄（166A タスキHD 等）で実際に守れるか

### 7. キーワードリスト DRY 化

- spec 内一元管理する形式（正規表現リテラル直書き vs YAML 別ファイル）の選択妥当性
- スペース除去前処理が常時必要か、PyMuPDF の `get_text("text")` モードで回避できないか

### 8. PyMuPDF 依存追加

- プロジェクト venv (`C:/venvs/investment-agent`) に既に入っているか確認手順がプランに含まれているか
- `uv add pymupdf` を実装と同時に行う宣言が明示的か

### 9. プラン MD フォーマット適合性（pattern 2 必須チェック）

- 冒頭に対象ファイルの基準 commit hash 明示 → ✓ a8515859
- 過去修正と残件数の前提サマリ → ✓
- 優先度定義 (P0/P1) → ✓
- 各項目が「症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック」7 フィールド → ✓ ただし「該当」がコード行番号でなく Step 名のみの項目あり
- before/after の両方 → ✓
- 呼び出し側波及の該当行リスト明示 → △ (skill MD 改修なので Step 名表示で OK か要判断)
- 「既に〜がある」系の前提が実コードと照合済か → ユーザー検証済（実走ログで確認）
- アンチパターン対応表 → ✓ ただし 004 アンチパターン番号が一部 "??" or 未マップ
- 検証戦略 smoke/dev/prod 4 段網羅 → △ 個人用パイプラインで dev/prod 区別なしと明記
- ロールバック手順 → ✓ 各項目に記載
- 優先度インフレ検出 → P0 が 3 件、P1 が 5 件 = ほぼ全て P0/P1。P2 ゼロ。妥当性レビュー要

---

## スコープ外

- コマンダー spec 改修（P0-2 / P0-3 連動）→ 別プラン
- 失敗時のフォールバック設計（CHUNK_TEXT 補助検索）→ プラン §検証戦略 §回収手順で言及のみ
- PyMuPDF OCR 機能（画像化 PDF）→ 別プラン
- LLM 直読 vs PyMuPDF テキスト品質比較 → 改修後運用観察で別評価
- 089/042 知見 MD の内容
- LINE 会話モード関連の運用ルール

---

## レビュー結果記入欄

（code-reviewer から返却された指摘・提案を以下に追記）

---

# コードレビュー: 受注高抽出ソルジャー PyMuPDF経路一本化 改修プラン

- 日時: 2026-05-18 (JST)
- 対象: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md`（改修プラン本体）／改修対象 `skills/orders_soldier.md` (commit a8515859)
- パターン: 2（既存改修プラン）
- レビュアー: Claude (code-reviewer runbook, sub-agent)

> **出力先の特記**: パターン 2 標準では新規 `docs/reviews/NNN_cr_*.md` を作成するが、提出元ユーザー指示により**既存 210 ファイルの「レビュー結果記入欄」末尾に直接追記**（CLAUDE.md §4.2 ユーザー指示優先）。連動する 004-1 不備蓄積ログへの追記も併せて実施。

---

## 【サマリー】

- 変更の要約: ソルジャー `Step 4-5` を **CHUNK_TEXT 経由 / LLM 直読** から **gsutil cp + PyMuPDF テキスト抽出 + キーワードヒットページ特定 + 該当ページのみ `Read` tool** に置換。偽陽性銘柄（実走 40%）の LLM 呼び出しをゼロにして token を 1/8 程度に削減。同時に BQ MAIN_CATEGORY 除外（5 カテゴリ）、DOCS_WITH_DATA cap 強調、note ドメイン規約強化、並行 agent 競合心配排除強化、HIGHLIGHTS 上限再強調、キーワードリスト DRY 化、`pymupdf` 依存明記の 8 項目（P0×3 / P1×5）。
- 品質評価: **B** — 方向性は妥当（CHUNK_TEXT が PDF 全文を保証しないという 167A 事例の根本対処として PyMuPDF 一本化は適切）。ただし、(1) **P0-2 BQ クエリ除外条件の集合論バグ**（SUB_CATEGORIES 経由で受注情報を持つ正当な決算短信を MAIN_CATEGORY 単独条件で誤除外する可能性）、(2) **P0-1 PyMuPDF 経路で画像 PDF / 暗号化 PDF のヒットゼロ判定が偽陽性銘柄判定と区別できない構造**、(3) **P1-5 pymupdf 依存追加が実施済み事実と矛盾**（pyproject.toml に既存）の重大 3 件が残る。フォーマット面はおおむね 7 フィールド充足、アンチパターン対応表が `T-?` / 未マップ含むなどの形式不備がある。
- 主要リスク:
  1. **P0-2 SUB_CATEGORIES 経由の正当受注情報の誤除外**: MAIN_CATEGORY='業績修正' かつ SUB_CATEGORIES に '受注高/受注残高' を含む文書を 5 カテゴリ除外で弾くと、現行の OR 条件で拾えていた受注情報文書が失われる
  2. **P0-1 画像 PDF / 暗号化 PDF / フォントエンコーディング異常 PDF の誤 skip**: PyMuPDF テキスト抽出ゼロ → キーワードヒットゼロ → Read 呼び出しゼロ → DOCS_WITH_DATA=0 → 真陽性銘柄が `failed_no_data` 誤判定
  3. **P1-5 前提が実態と不一致**: `pyproject.toml` に既に `pymupdf>=1.27.2.2` が登録済、venv にもインストール済。プランの「未インストールの場合は uv pip install pymupdf」「`uv add pymupdf` 相当の dependency 明示を本プラン実装時に同時実施」は事実誤認

## 【改修プラン評価】

### 妥当性

- **方向性照合（独立仮説）**: 独立に根本原因を推定すると、(a) 偽陽性 40% 銘柄に対して LLM 全文読みを強制している → 事前 token-0 フィルタが必要、(b) Gemini SUB_CATEGORIES 分類が偽陽性を含む → 受注キーワードを含まない PDF を即座に弾く構造が必要、(c) CHUNK_TEXT は PDF 全文をカバーしない（167A 実証） → CHUNK_TEXT 経由前処理は採用不可。本プランの「PyMuPDF 経路一本化」はこの 3 点に**根本対処**しており、対症療法ではない。206/207 の重大指摘も「assistant 内蔵タイマー依存 → ファイル mtime 観測に転換」と構造を変える設計だったが、本プランも同じく「LLM 全文 → ローカル抽出 + ヒットページのみ LLM」と構造を変える設計で品質方針は揃っている。
- **対症療法パターン検出**: 該当なし。「特定条件でのワークアラウンド」「try/except 吸収」「入力値の事後フィルタ」のいずれにも該当しない。
- **代替案検討の透明性**: §「設計上の重要決定」で CHUNK_TEXT 代替案を 167A 事例で具体的に却下しており、判断根拠が明示されている。プラン MD として透明性高い。

ただし以下の方向性懸念あり:

- **P0-2 (BQ MAIN_CATEGORY 除外) は OR 条件の集合論を見落とし**: 既存 Step 2 SQL は `MAIN_CATEGORY = '受注高/受注残高' OR EXISTS(SUB_CATEGORIES に '受注高/受注残高')` で、後者の OR 経由でメイン分類が別カテゴリ（業績修正・自己株式取得・配当・etc）の文書も拾われる。プランの修正 SQL は WHERE 末尾に `AND MAIN_CATEGORY NOT IN ('月次開示', '業績修正', '第三者割当・公募増資', '中期経営計画', '事業計画（グロース）')` を追加するため、**MAIN='業績修正' かつ SUB に '受注高/受注残高' を含む文書**が消える。プランの根拠は「業績修正/中期経営計画/事業計画(グロース)/第三者割当 は受注実数を含まないことが多い」だが、これは MAIN 単独の場合の話で、SUB に '受注高/受注残高' があるならば受注実数を含む可能性が十分にある（Gemini が SUB に '受注高/受注残高' を付けた根拠は本文に該当キーワードあり）。「多くは受注実数を含まない」を理由に「全部除外」する論理飛躍がある（後述 重大指摘 #1）。

### 副作用・デグレードチェック

- [ ] **P0-1 画像 PDF / フォント埋め込み異常 PDF の PyMuPDF テキスト抽出ゼロ問題**: PyMuPDF の `page.get_text()` は画像のみで構成される PDF（スキャン PDF / ベクター画像化された決算短信）からはテキストを取れない。実走 60 件中に画像 PDF が混在している場合、その PDF だけ「キーワード 0 ヒット → Read 呼び出しスキップ → DOCS_WITH_DATA 寄与 0」となるが、これは「偽陽性銘柄」とは別の原因。**改修後 spec の Step 5 §「期待する数値がなかった」場合の判定 (現行 spec L210-220) が、`failed_no_data` の意味を「Gemini 分類エラーで紛れ込んだ業種」と定義しているのに対し、PyMuPDF 抽出失敗で生じる「真陽性なのに skip」も同じ `failed_no_data` に流れ込むため、後段の偽陽性銘柄ストック把握が不正確になる**。プラン §検証 §1414 で「既存通り全 PDF からデータ抽出可能」と書かれているが、1414 はテキスト PDF 前提。画像 PDF 検知（`get_text()` 結果サイズが 0 or 極端に小さい）→ Read tool への fallback 経路は spec 化されていない（後述 重大指摘 #2）。
- [ ] **P0-1 中間ファイル `.pages.json` のクリーンアップ未記載**: プラン §Step 4 修正案で `/c/tmp/tdnet_orders/{ticker}_{idx}.pages.json` を生成するが、現行 spec Step 7 の `rm -f /c/tmp/tdnet_orders/{ticker}_*.pdf` は `.pdf` glob のため `.pages.json` を削除しない。長期運用で `/c/tmp/tdnet_orders/` に json が無限蓄積する。プラン §ロールバックで「temp 配下なので残骸無害」と書かれているが、これは 1 回試行の話で、本番運用での累積を考慮していない。**Step 7 PDF 削除 glob を `{ticker}_*` に拡張**するなど、cleanup 拡張も spec 改訂対象に含めるべき。
- [ ] **P0-1 hb 更新タイミング欠如**: プラン §「呼び出し側への波及」で「ハートビート (_heartbeat/{ticker}.hb) 更新タイミングは『PyMuPDF テキスト抽出直前』『Read tool 呼び出し直前』に挿入」と書かれているが、**PyMuPDF テキスト抽出は数 GB スキャン PDF で数十秒〜数分かかる可能性**があり、その間 hb 更新が無いと 30 分タイムアウト誤発火する。207 重大 #2 で「Read 1 回が長時間ハング → hb 古い → 誤発火」を直したのと同じ問題が PyMuPDF にも再発する。**「PyMuPDF 抽出前 1 回 + Read tool 呼び出し直前 1 回」だけでなく、`fitz.open()` / `page.get_text()` ループ内でもページごとに hb 更新する**ことを spec 化すべき（PDF 200 ページの大物では数十秒の処理になる）。
- [ ] **P0-3 cap 再強調が DOCS_FOUND ≤ 6 の上限を強化していない**: プラン §修正方針で「`0 ≤ DOCS_WITH_DATA ≤ DOCS_READ ≤ DOCS_FOUND ≤ 6` を必ず満たすこと」と書いたが、**現行 spec Step 3 で「最新 6 件採用」と決まっているため、DOCS_FOUND は構造的に ≤ 6**。166A で DOCS_WITH_DATA=13 を返した事象の原因は「ソルジャーが d[] 要素数を返した」であり、ここは正しく診断できている。ただし P0-3 の§呼び出し側への波及で「コマンダー §Step 4B で DOCS_WITH_DATA > DOCS_READ なら DOCS_READ にキャップ」とあるが、**コマンダー側の現行 spec §Step 4 §B-1 (b) では status file 経路で DOCS_WITH_DATA を受け取るため、cap 処理はソルジャー側で必須**。コマンダー側 cap は防御的二重キャップに過ぎず、ソルジャー側で「JSON 生成と同時に len(set(doc_id for d_elem in d)) を計算して書き出す」アルゴリズム指示が spec で欠落している（**強調文だけでは違反を構造的に防げない**）。
- [ ] **P1-1 (note ドメイン) 反面教師の追加位置**: プラン §修正方針でコマンダー側パース失敗例を追記する設計だが、ソルジャー側 spec の §Step 6 §note ドメイン規約は「ソルジャーが書く側のルール」で、コマンダー側 (caller) の解釈詳細が混在すると spec の主体が曖昧になる。代わりに「ソルジャー側は許可キー以外を書かない」だけを残し、コマンダー側パース仕様は `skills/orders_commander.md` に分離する方が単一責任原則的に正しい（**プランで「本プランスコープ外＝コマンダー spec 別改訂」と明記しながら、ソルジャー側にコマンダー解釈を埋め込むのは矛盾**）。
- [ ] **P1-2 (並行 agent 競合) 文言強化が「中断シグナル全般」を抑止できない**: 強化文は「処理進行の競合」に焦点を当てているが、根本原因は「ソルジャーが他 ticker の話を見て自分の処理を再考する」行動パターン。spec 文言で抑止しても、別の言い回しで同じ判断（例: 「リソース節約のため」「ユーザー意図確認のため」）で中断する変種が出る可能性。**「タスク開始後の処理中断は spec 違反として禁止」というメタルールを §禁止事項に追加**する方が広範に効く。
- [ ] **P1-3 (HIGHLIGHTS 上限) コマンダー破棄を強調する論理の副作用**: 「コマンダーは破棄するため token 浪費」を明記すると、**ソルジャーが HIGHLIGHTS を空で返す行動への誘導**になる可能性。HIGHLIGHTS は debug 用に残す価値があり（後段で「なぜ partial 判定か」「特定銘柄の特殊事情」を人間が見るとき有用）、「破棄」という強い言葉が逆効果。「token 浪費なので 5 行以内に絞る」だけで十分。
- [ ] **P1-4 (キーワードリスト) スペース除去前処理は元ページ番号維持と整合するか**: プラン §修正案で `re.sub(r'\s+', '', text)` を実施するが、**実装で `pages = json.load(); for p in pages: text = re.sub(...); if pattern.search(text): hit_pages.append(p['page'])` と書く必要**がある（プラン記述では現行の Python 一行コードに統合可能だが）。実装手順としては明確化が必要。
- [ ] **P1-5 (PyMuPDF 依存) 前提が虚偽**: pyproject.toml には既に `pymupdf>=1.27.2.2` が登録済 (`grep pymupdf pyproject.toml` で確認)、`uv.lock` にも記録済、venv `C:/venvs/investment-agent/Lib/site-packages/` に `fitz/`, `pymupdf/` ディレクトリも存在。プラン §症状「未インストールの場合は」「`uv add pymupdf` 相当の dependency 明示を本プラン実装時に同時実施」「未インストールの場合は実行時 ImportError」は全て**事実誤認**。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **CHUNK_TEXT 経路と PyMuPDF 経路のハイブリッドの可能性検討漏れ**: §設計上の重要決定 §1 で CHUNK_TEXT を「不採用」としているが、PyMuPDF のテキスト抽出失敗時（画像 PDF）の fallback として CHUNK_TEXT を補助検索する選択肢がある。プラン §回収手順で「PyMuPDF ヒットゼロ かつ MAIN_CATEGORY='決算短信' の場合、CHUNK_TEXT に対し追加検索する fallback も検討余地あり（本プランスコープ外）」と末尾で言及されているが、**P0-1 の本体 spec から外す判断根拠が薄い**（167A 事例は「CHUNK_TEXT が完全でない」だが「補助検索なら効く」とは別問題）。
- [ ] **DOCS_FOUND の意味の整合性**: PyMuPDF 経路で「キーワードヒットゼロ PDF」を Read tool 呼び出しスキップする場合、DOCS_FOUND は「BQ 取得数」のまま 6、DOCS_READ は「PyMuPDF 抽出に成功した PDF 数」、DOCS_WITH_DATA は「Read tool で数値抽出できた PDF 数」となる。**プラン本文で DOCS_READ の新定義が明示されていない**（Read tool スキップでも PyMuPDF で抽出は成功しているなら DOCS_READ=1 として数えるのか？）。現行 spec L444 「`DOCS_READ`: Read に成功した PDF 数」の定義改訂が必要だが、プランで触れられていない。
- [ ] **コマンダー側 BQ クエリの除外条件追加の同期タイミング**: プラン §呼び出し側への波及で「コマンダー側 BQ クエリ（§ Step 2B build モード）にも**同じ除外条件を追加必須**」と書かれているが、**ソルジャー実装とコマンダー実装が別プランで時間差で行われると、その期間中はコマンダーが pending リストに5カテゴリ ticker を含む → ソルジャー実装側で「BQ 0 件 → failed_no_bq_records」を返す → 大量 failed 計上、という副作用がある**。「コマンダー spec 改訂を本プラン実装と**同時 commit**する」を §検証戦略に明記すべき（あるいはソルジャー側のクエリ改修を後出しにする順序制御）。
- [ ] **既存 60 件 build 結果との互換性**: P0-2 で 5 カテゴリ除外が走ると、過去 build の `orders_index.csv` 60 件のうち**5 カテゴリ起因の completed/completed_partial 行**が「再 build で対象外」になる。これらは「過去成功した結果」として残るべきか、削除すべきか、プランで明示されていない。実走で 1431 (リブワーク 月次開示3件+決算短信3件+決算説明資料2件) が「改修後は決算短信+決算説明資料 5件のみ処理」と書かれているが、**この銘柄は MAIN_CATEGORY が何で SUB に何が入っていたか**の確認が無いため、改修後の実際の挙動が不確定（後述 重大指摘 #1 と直結）。
- [ ] **キーワード正規表現の同期義務**: P1-4 で「spec 内一元管理」を導入するが、**ソルジャー側 spec のキーワードリストと、Step 5 §抽出対象（既存 L171-188）の表記揺れキーワード**が二重管理になる。両者は目的が違う（前者は前処理フィルタ、後者は LLM 抽出指示）が、ユーザーが「新キーワード追加」する際に片方だけ更新する漏れリスク。**「キーワード追加時は前処理リストと抽出対象リストの両方を更新する」を spec 化**するか、両者を同じテーブルにまとめる構造改善が必要。
- [ ] **正規表現の単純 `|` 連結の優先順位**: プラン §修正案の正規表現は `受注高|受注残|...|生産実績` の 60+ 個の `|` 連結。`re.compile` で問題は無いが、**「受注高」が「新規受注高」の中にもマッチする**ため、`受注高` と `新規受注高` が両方マッチする冗長があり、また `受注` 単独語（例: 「受注は堅調」）にもマッチして「定性記述しかないページ」もヒット扱いになる。これは現行 spec Step 5 §「期待する数値がなかった」場合の判定で「定性記述のみは抽出ゼロ」と書かれているが、**PyMuPDF 経路での「ヒット扱い」と Step 5 偽陽性判定の「数値ゼロ扱い」の整合が必要**（前者でヒットしても後者で 0 になれば DOCS_WITH_DATA に寄与しない）。動作上は問題ないが、token 効率の観点から「数値を含むパターン」（例: `受注[^0-9]{0,30}\d+`）に絞る選択肢もある。
- [ ] **PyMuPDF バージョン要件の妥当性**: P1-5 で「バージョン要件: 1.23+ (page.get_text() の text モード対応)」とあるが、`page.get_text()` の text モードは PyMuPDF 1.18 以降の機能。1.23 要件の根拠が薄い。venv は 1.27.2.2 で十分。
- [ ] **アンチパターン対応表の `T-?` 未マップ**: 表 §P0-2 が `T-?` と未マップ。`docs/knowledges/tools/013_tdnet_load.md §T-x` の該当を確認して埋めるか、明示的に「該当なし」を書くか、どちらか必要。
- [ ] **検証戦略の smoke/dev/prod/回収手順 4 段欠如**: §検証戦略 §1-4 で「smoke test → dev 実機 → 本番適用判断基準 → 回収手順」を一応並べているが、**プランで「個人用パイプラインで dev/prod 区別なし」と注記**（前提サマリ §実機検証 L24）。本プロジェクトの性質上適切だが、テンプレ仕様 §プラン MD フォーマット適合性 §「検証戦略が smoke/dev/prod/回収手順の 4 段を網羅しているか」に対しては「個人用なので dev=prod、smoke のみ別段」と明示するのが正しい（暗黙にせず明示）。
- [ ] **ロールバック手順の中間ファイル残骸への配慮欠如**: P0-1 ロールバックで「中間ファイル `.pages.json` は temp 配下なので残骸無害」と書かれているが、**ロールバック後に旧 spec で動作 → `/c/tmp/tdnet_orders/{ticker}_*.pdf` だけ削除 → `.pages.json` 残存**となる。前述「副作用 #2」と同根。

### 新規リスク

- **PyMuPDF の Python サブプロセス invocation コスト**: プラン §修正案で `python -c "import fitz; ..."` を各 PDF ごとに 2 回（抽出 + キーワード検索）呼ぶ。1 回あたり 1-2 秒の Python 起動オーバーヘッド × 6 PDF × 60 銘柄 = 数百〜千秒の追加コスト。**「ユーザー指示：GCS 費用・時間は無視」とのことだが、Python サブプロセス起動コストは spec で言及していない**ため、後で「思ったより遅い」事案になる可能性。1 回の `python -c` で全 PDF 一括処理（複数引数受け取り）にすれば 1/6 に短縮可能。
- **PyMuPDF テキスト品質の事前確認不足**: プラン §設計上の重要決定 §「LLM 直読 vs PyMuPDF テキスト品質比較 → 改修後の運用観察で別評価」と書かれているが、**LLM 直読は OCR 等で図表内テキストを認識できる場合がある（特に Gemini 系）のに対し、PyMuPDF は純粋なテキスト層のみ**。決算短信の「受注高表」が画像で埋め込まれているケース（PowerPoint 由来 PDF 等）では PyMuPDF だけでは抽出できず、現行の LLM 直読では取れていた数値が落ちる可能性。**事前比較（167A 以外で 5 件程度）を smoke test に組み込むべき**。
- **PyMuPDF 1.27.2.2 の license**: PyMuPDF は AGPL-3.0 ライセンス（商用利用には別途商用ライセンス購入が必要）。本プロジェクトは個人用解析パイプラインで非配布なので問題ないが、**spec に「個人用解析のため AGPL OK」のような注記**があると後の運用判断が明確。

## 【重大な指摘】（即修正）

### #1 P0-2 BQ MAIN_CATEGORY NOT IN 除外が SUB_CATEGORIES 経由の正当受注情報を誤除外する集合論バグ

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:151-164`（修正方針 SQL）
- 事象: 現行 SQL は `(MAIN_CATEGORY = '受注高/受注残高' OR EXISTS(SUB に '受注高/受注残高'))` の OR 条件で、MAIN がほぼ任意のカテゴリでも SUB に '受注高/受注残高' が付いていれば対象に含む設計。プランの修正案は WHERE 末尾に `AND MAIN_CATEGORY NOT IN ('月次開示', '業績修正', '第三者割当・公募増資', '中期経営計画', '事業計画（グロース）')` を追加するため、**MAIN='業績修正' かつ SUB に '受注高/受注残高' を含む文書**が削除される。
- トリガー: Gemini AI 分類が「業績修正の付帯情報として受注高を SUB に追加」したケース。例: 「2026年3月期 業績予想の修正に関するお知らせ」内で受注高が予想修正根拠として記載されているケース、または「中期経営計画策定に関するお知らせ」内で受注計画が中計の主要 KPI として記載されているケース。これらは MAIN='業績修正' / '中期経営計画' のまま SUB に '受注高/受注残高' が追加される。
- 影響:
  - 受注情報を含む正当な開示文書が build 対象から消える → DOCS_FOUND 過少 → completed → completed_partial に悪化、または 6 件枠が他文書で埋まらず空き
  - プランの想定（1431 リブワーク 月次開示3件+決算短信3件+決算説明資料2件 → 改修後5件のみ処理）は MAIN だけで判定しているが、**実際は SUB 経由で月次開示文書も拾われている可能性**があり、その場合は MAIN_CATEGORY NOT IN ('月次開示') で SUB 経由月次開示も全部弾かれる
  - 改修後の build 結果が「ユーザーの期待した除外」とずれる
- 根拠:
  - 現行 spec Step 2 SQL (orders_soldier.md L87-95) で `MAIN_CATEGORY = '受注高/受注残高' OR EXISTS(SUB に '受注高/受注残高')` が OR で並列。
  - `docs/data_catalog/bq_tdnet_documents.md` L72-106 に MAIN_CATEGORY の全種が列挙されており、「受注高/受注残高」は MAIN_CATEGORY 値として存在しない（つまり SUB のみで付与される）。**現行クエリはほぼ SUB_CATEGORIES 経由で文書を拾っており、MAIN_CATEGORY = '受注高/受注残高' の OR 左辺は実質ダミー**。実機の MAIN_CATEGORY 分布を確認すべき。
  - data_catalog L72-106 のカテゴリ表に '受注高/受注残高' は MAIN_CATEGORY 値として登録されていない（'月次開示' '業績修正' '決算短信' 等が並ぶ）。
- 推奨対応 (**[方向性]**):
  - **論理を逆転**: 5 カテゴリ除外は MAIN_CATEGORY だけでなく **SUB_CATEGORIES の主要分類で受注情報が付帯であるケースも区別**する。簡単な方法は「`MAIN_CATEGORY NOT IN (...)` ではなく、**MAIN_CATEGORY が受注情報の主要分類（決算短信/決算説明資料）であることを条件にする**」（include list 方式）:
    ```sql
    WHERE TICKER = '{ticker}'
      AND (
        MAIN_CATEGORY IN ('決算短信', '決算説明資料')
        AND EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '受注高/受注残高')
      )
      AND AI_STATUS = 'completed'
      AND SUBMISSION_DATE BETWEEN '2022-01-01' AND CURRENT_DATE("Asia/Tokyo")
    ```
  - もしくは現行の OR 構造を維持しつつ、**プラン §呼び出し側への波及で「実機で MAIN_CATEGORY 別件数を集計してから除外リストを確定する」**を §smoke test に組み込む（実機データ確認なしで除外リストを spec 固定しない）。
  - **記載先**: プラン §P0-2 §修正方針 SQL の論理見直し + §検証戦略 §smoke test に「BQ で MAIN_CATEGORY × SUB_CATEGORIES の組み合わせ件数を集計、5 カテゴリ除外で削除される件数のうち SUB 経由分を確認」を追加。

### #2 P0-1 PyMuPDF 経路で画像 PDF / 暗号化 PDF / フォント異常 PDF が「偽陽性銘柄」と同じ failed_no_data 扱いになる構造的問題

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:91-119`（新 Step 5 + 検証）/ `skills/orders_soldier.md:210-220`（現行 STATUS 判定）
- 事象: プランの新 Step 5 は「キーワードヒットゼロ → Read tool 呼び出しスキップ → DOCS_WITH_DATA 寄与 0」。一方、現行 spec STATUS 判定では `failed_no_data = DOCS_READ >= 1 かつ DOCS_WITH_DATA == 0` で「Gemini 分類エラーで紛れ込んだ偽陽性」と意味づけられている。**PyMuPDF が画像 PDF / 暗号化 PDF / フォントエンコーディング異常 PDF からテキスト抽出に失敗すると、ヒットゼロ判定 → Read スキップ → DOCS_READ にカウントするか / しないかで分岐**。プラン §修正方針 §「4. ヒットゼロ → その PDF の DOCS_WITH_DATA への寄与 0、Read tool 呼び出しスキップ」と書かれているが、**DOCS_READ に含めるか含めないかが不明**。
- トリガー: 決算短信 PDF がスキャン版（紙原本のスキャン）、または PowerPoint 由来の画像埋め込み PDF（特に決算説明資料に多い）、または暗号化された PDF（一部証券会社の決算開示）の場合に発生。1414 ショーボンドのような「素直な決算短信」とは別の構造を持つ PDF が存在する。
- 影響:
  - **真陽性銘柄が偽陽性銘柄として誤判定**される: PyMuPDF 抽出失敗 → ヒットゼロ → Read スキップ → 6 PDF 全部 PyMuPDF 失敗の場合 DOCS_WITH_DATA=0 → `failed_no_data` 判定。実は真陽性銘柄で LLM 直読なら抽出できる
  - インデックス CSV に `failed_no_data` で記録 → ユーザーが偽陽性銘柄として削除候補にする → データロスト
  - 改修動機（偽陽性 token 削減）の効果検証が「PyMuPDF 失敗銘柄」の混入で歪む
- 根拠:
  - PyMuPDF の `page.get_text()` は画像のみで構成された PDF からはテキストを取れない（OCR 機能なし、`get_text()` は空文字列を返す）。
  - 暗号化 PDF（パスワード保護）は `fitz.open()` 自体が例外を返す可能性（プラン §修正案の `python -c` ブロックは例外時の動作未定義）。
  - 現行 spec の `failed_no_data` 意味付け (L218-220) は「Gemini SUB_CATEGORIES 分類が偽陽性」前提で、PyMuPDF 抽出失敗は範囲外。
  - プラン §検証 §1301 (極洋、failed_no_data 確定) は「全 PDF ヒットゼロで Read tool ゼロ呼び出し」を期待値としているが、**「真の偽陽性銘柄」と「PyMuPDF 失敗銘柄」が同じ結果を返す**ため、smoke test だけでは区別できない。
- 推奨対応 (**[方向性]**):
  - **a) PyMuPDF 抽出結果のテキスト総量を判定材料に加える**: `sum(len(p['text']) for p in pages)` を計算し、極端に小さい（例: 500 文字未満）場合は「画像 PDF 可能性」とみなして Read tool fallback を強制実行。spec §Step 5 で「PyMuPDF 抽出文字数 < N → 画像 PDF 想定 → Read tool 呼ぶ」を追加。
  - **b) `fitz.open()` 例外を spec で吸収**: try/except でラップして、例外時は「PDF 異常」として該当 PDF を Read tool 経由（旧経路）で読む fallback を spec 化。
  - **c) STATUS の意味づけを更新**: `failed_no_data` の reason 「Gemini 分類エラーで紛れ込んだ業種」だけでなく「PyMuPDF 抽出不能 (画像/暗号化)」も区別。新 reason `failed_pdf_extract_unreadable` を追加するか、`failed_no_data` の reason を 2 種類に分岐するか。
  - **記載先**: プラン §P0-1 §修正方針に「画像 PDF / 暗号化 PDF / 抽出失敗時の fallback」サブセクション追加 + ソルジャー spec §Step 5 §STATUS 判定 / §「期待する数値がなかった」判定を改訂。

### #3 P1-5 「PyMuPDF 依存追加」前提が事実誤認（既にインストール済・pyproject 登録済）

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:356-379`（P1-5 全体）
- 事象: プランは「PyMuPDF (`pymupdf` / `import fitz`) はソルジャー新仕様の必須依存」「プロジェクト venv (`C:/venvs/investment-agent`) にインストール済みかは不明」「実行時 ImportError で初回ソルジャーが失敗するリスク」「未インストールの場合は `uv pip install pymupdf` でプロジェクト venv に入れる」「`pyproject.toml` または `uv add pymupdf` 相当の dependency 明示を本プラン実装時に **同時実施**」を全て前提として書かれているが、いずれも事実誤認。
- トリガー: プラン作成時に `pyproject.toml` を Read していない。
- 影響:
  - 実装担当者（次セッション）が「`uv add pymupdf` を実行」「ImportError 対策が必要」と判断し、**既に登録済の依存に対して二重追加処理を行う**（uv 側で no-op になるはずだが、無駄な作業）
  - 「インストール済かは不明」の不確実性に基づいて実装が進むため、smoke test / dev 検証で確認工程が冗長化
  - プラン MD の信頼性低下: 既存環境を確認せずに前提を立てた → 他の前提（166A の DOCS_WITH_DATA=13、CHUNK_TEXT 不採用の 167A 事例 etc）の検証品質も疑われる
- 根拠:
  - `pyproject.toml` に `pymupdf>=1.27.2.2` 登録済（`grep pymupdf pyproject.toml` で確認）
  - `uv.lock` に `pymupdf 1.27.2.2` ロック済
  - venv `C:/venvs/investment-agent/Lib/site-packages/` に `fitz/`, `pymupdf/`, `pymupdf-1.27.2.2.dist-info/` 存在
  - プラン §症状の 3 行が全て事実と不一致
- 推奨対応 (**[方向性]**):
  - **P1-5 を全面書き換え**: 「症状」を「PyMuPDF (pymupdf>=1.27.2.2) は pyproject.toml に登録済み、venv にインストール済み（2026-05-18 確認）。**spec MD には現在 PyMuPDF 依存の明記なし** → spec §注意事項に依存を明記して再現性を担保するのみ」と修正。
  - 修正方針も「依存追加」ではなく「spec への明記のみ」に変更。`uv add` 系コマンドは不要。
  - ロールバックも「spec 記述削除のみ」（依存は他で使用していなければ削除可能だが、影響範囲を確認してから）。
  - **記載先**: プラン §P1-5 を全面修正 + プラン §前提サマリに「pyproject.toml 確認済み」を追加（プラン MD フォーマット適合性チェックの「既に〜がある」系前提検証義務 §10）。

## 【改善提案】（可読性・保守性）

### #1 PyMuPDF 抽出は 1 回の python サブプロセスでまとめて処理する

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:73-106`（新 Step 4/5 の python -c 2 回呼び）
- 現状: PDF 1 件あたり「抽出」「キーワード検索」で 2 回 python -c を起動。Python 起動オーバーヘッド 1-2 秒 × 6 PDF × 2 回 = 12-24 秒のオーバーヘッド。
- 提案: 1 回の python サブプロセスで「全 PDF の抽出 + キーワード検索 + ヒットページ一覧を JSON で出力」までまとめる。Bash 側は結果 JSON を `cat .hit_pages.json` で受け取る。

### #2 spec 内のキーワードリストを別ファイル `skills/orders_soldier.keywords.json` に分離

- 箇所: P1-4 修正方針（spec 内に正規表現リテラル直書き）
- 現状: spec MD 内に 60+ キーワードの長い `|` 連結正規表現を埋め込む設計。可読性低下 + 抽出対象セクションとの重複。
- 提案: `skills/orders_soldier.keywords.json` に `{"prefilter_keywords": [...], "extraction_categories": {...}}` で集約。Python 側は `json.load()` で参照、spec MD は「キーワード正本は keywords.json」とポインタのみ記載。DRY 化が徹底。
- ただしファイル数増加のトレードオフあるため、ユーザー判断による。

### #3 plan §設計上の重要決定 §3 「スペース除去前処理」の位置付け明示

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:59`（設計上の重要決定）
- 現状: 「スペース除去前処理」が決定事項として独立して書かれているが、P0-1 §修正方針と P1-4 §修正方針に重複して登場（プラン §P0-1 L108 + §P1-4 L345）。
- 提案: 設計上の重要決定で 1 回触れて、後の修正方針セクションは「§設計 §3 のスペース除去前処理を Step 5 で実施」とポインタ参照に統一。

### #4 アンチパターン対応表の `T-?` を実値で埋める or 明示的に「該当なし」記載

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:386`（対応表 P0-2 が T-? のまま）
- 現状: 表 §P0-2 `004=データ取得フィルタ T-?`。`T-?` は未マップ。
- 提案: `013_tdnet_load.md §T-x` 内に該当があるか確認し、無ければ「`T-該当なし`」と明示。改修プランの「アンチパターン対応表」フィールドの完全性確保。

### #5 検証戦略の「個人用 dev=prod」の明示

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:401-405`（検証戦略）
- 現状: §1 smoke test + §2 dev 実機 + §3 本番適用判断基準 + §4 回収手順 で 4 段揃えているが、プラン §前提サマリ §実機検証 (L22) で「本プロジェクトは個人用解析パイプラインで dev/prod 区別なし」と書かれており、§検証戦略でも dev と prod が事実上同一。
- 提案: §検証戦略 §2 と §3 を「dev 実機（=本番）」に統合、または冒頭で「本プロジェクトは個人用パイプラインで dev=prod、smoke test のみ別段の検証層」を明示。プラン MD フォーマット適合性 §「検証戦略が smoke/dev/prod/回収手順の 4 段を網羅しているか」への明示的回答。

### #6 ロールバック手順への `.pages.json` 残骸への配慮追加

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:119`（P0-1 §ロールバック）
- 現状: 「中間ファイル `.pages.json` は temp 配下なので残骸無害」と書かれているが、`/c/tmp/tdnet_orders/` は新 spec / 旧 spec 共用なので残骸が混じる。
- 提案: ロールバック手順に「`rm -f /c/tmp/tdnet_orders/*.pages.json` を併せて実行」を追加。または現行 Step 7 PDF 削除 glob を `{ticker}_*` に拡張する spec 改修を本プランに含める。

### #7 提出元観点 §6「コマンダーは破棄」の事実確認の追記

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:300`（P1-3 §修正方針）
- 現状: 「コマンダーは HIGHLIGHTS を破棄するため」と書かれているが、コマンダー spec で確認したところ `skills/orders_commander.md:530` 「ソルジャー戻り値の文言（HIGHLIGHTS 等）はインデックスには記録しない（ログにも記録しない。容量肥大防止）」と明記済みで、事実は正しい。
- 提案: プラン §P1-3 §修正方針に「コマンダー spec L530 で確認済」と根拠リンクを追加するだけで OK。レビュー観点 §6「『コマンダーは破棄』が事実か」への明示的回答。

### #8 P1-2 並行 agent 競合の禁止リストへの 1 行追加

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:275-278`（P1-2 §修正方針 §禁止事項）
- 現状: 「他 ticker を処理中の並行 agent の存在を理由に...処理を中断・スキップ・既存ファイル尊重しない」と禁止事項追加案あり。
- 提案: 加えて「**タスク開始後の自己判断による処理中断は禁止**（spec が明示的に中断条件として定義しているケース＝ハートビート 30 分超 / context 枯渇接近 等を除く）」というメタルールを §禁止事項に追加。並行 agent 競合に限定せず、ソルジャーが「リソース節約」「ユーザー意図確認」「重複起動回避」等の別理由で中断する変種を広範に抑止できる。

### #9 提出元観点 §3「JSON の d[] 要素数と PDF 数の分離」のアルゴリズム指示追加

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:194-216`（P0-3）
- 現状: 強調文と禁止事項追加で対処しているが、ソルジャー実装側の計算アルゴリズムが明示されていない。
- 提案: P0-3 §修正方針に以下を追加: 「DOCS_WITH_DATA は `len(set(d_elem['doc_id'] for d_elem in d))` で計算する（doc_id がユニークな PDF 数）。`len(d)` を返してはいけない」をアルゴリズムレベルで指示。spec の表現が「JSON 内の d[] 要素数ではない」と否定形のため、肯定形での計算式提示で誤実装防止。

### #10 P0-1 §呼び出し側への波及で hb 更新タイミングを §修正方針本体に統合

- 箇所: `docs/plans/refactor_orders_soldier_pymupdf_20260518_225508.md:110-113`（呼び出し側への波及）
- 現状: 「ハートビート更新タイミングは『PyMuPDF テキスト抽出直前』『Read tool 呼び出し直前』に挿入」が §呼び出し側への波及に書かれているが、これはソルジャー spec 内部の話で「呼び出し側」ではない。
- 提案: §修正方針の python サブプロセスコード内に `date -Iseconds > {hb}` を「PyMuPDF 抽出ループ内（各ページ抽出直前）」「キーワード検索後の Read tool 呼び出し直前」に明示的に配置。207 重大 #2 の対応（リトライ前 hb 更新）と整合させる。

## 【プラン MD フォーマット適合性チェック】

| 項目 | 結果 | 備考 |
|------|------|------|
| 冒頭に基準 commit hash | ✓ | `a8515859` 明記 |
| 過去修正と残件数の前提サマリ | ✓ | 4 件 commit 明記 + 残 8 件 |
| 優先度定義（P0/P1/P2 昇格基準） | ✓ | 「token 削減目的達成必須」「削減効率高め」と意味付け明示 |
| 各項目 7 フィールド | △ | 全項目「症状/該当/根本原因/修正方針/呼び出し側波及/検証/ロールバック」を概ね揃えているが、「該当」がコード行番号でなく Step 名のみの項目が多い（spec MD 改修なので妥当） |
| 修正方針 before/after | ✓ | P0-1, P0-2 等で旧コード→新コードの対比あり |
| 呼び出し側波及の該当行リスト明示 | △ | spec MD 改修のため Step 名で示しているが、コマンダー側 spec への波及は「別プラン対応」と明示で OK |
| 「既に〜がある」系の前提が実コードと照合済 | ✗ | **P1-5 で pyproject.toml 確認漏れ（重大指摘 #3）** |
| アンチパターン対応表 | △ | 表は存在するが `T-?` 未マップ（改善提案 #4） |
| 検証戦略 smoke/dev/prod/回収手順 4 段網羅 | △ | 4 項目あるが個人用 dev=prod なので明示的説明が欲しい（改善提案 #5） |
| ロールバック手順 | △ | 全項目に記載あり、ただし P0-1 `.pages.json` 残骸への配慮欠如（改善提案 #6） |
| P0/P1 のインフレ検出 | ✓ | P0×3, P1×5, P2×0。token 削減という改修目的に直結する項目を P0、効率化・保守性を P1 と適切に区別 |
| 関連 commit / 知見 MD / incident ログへのリンク | ✓ | 4 commit + 過去レビュー 204/206/207 + 167A 検証 PDF パス + orders_index.csv パス |

**総合**: フォーマット面はおおむね合格水準。最大の欠陥は「既に〜がある」系前提の未照合（P1-5 pymupdf 既存）と、アンチパターン対応表の未マップ。

## 【確認できなかった事項】

- **MAIN_CATEGORY × SUB_CATEGORIES 実機分布**: 重大指摘 #1 の「MAIN='業績修正' かつ SUB に '受注高/受注残高'」が実機で何件存在するかは BQ 実行が必要。レビュアーは BQ 実行禁止のため未確認。プラン §smoke test に追加すべき項目。
- **1431 リブワーク / 1719 安藤ハザマの MAIN_CATEGORY 内訳実機確認**: プラン §検証 §1431 「月次開示3件+決算短信3件+決算説明資料2件」と書かれているが、MAIN_CATEGORY の分布までは未確認。SUB 経由で含まれている可能性があり、改修後の挙動は実機検証で確認が必要。
- **画像 PDF / 暗号化 PDF の実存件数**: 重大指摘 #2 のリスクが本プロジェクトの 60 件で何件発生するかは PyMuPDF 実走しないと分からない。Phase 1 smoke test (5 件) で確認推奨。
- **PyMuPDF サブプロセス起動オーバーヘッド実測**: 改善提案 #1 で「1-2 秒 × 6 PDF × 2 回」と推定したが、Windows Python 起動時間は実機で計測必要。
- **コマンダー spec 連動改訂タイミング**: 別プランで対応とされているが、本プラン適用とコマンダー spec 適用の時間差で何が起きるかの実機検証は未実施。

---

## 不備蓄積ログ追記内容（004-1）

本レビューの重大指摘 3 件 + 改善提案 10 件 + フォーマット不備 2 件を `docs/knowledges/tools/004-1_code_review_findings_log.md` の 2026-05-18 セクションに [CR-210] タグで追記する。タグは既存カタログから採用:
- `content:unverified-assumption`（#3 pymupdf 既存未確認）
- `content:similar-bug-uncovered`（#1 BQ OR 条件集合論）
- `content:regression-risk-missed`（#2 画像 PDF 誤判定）
- `format:antipattern-map-missing`（T-? 未マップ）
- `content:missing-precondition`（hb 更新タイミング欠如）
- 他


