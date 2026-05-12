# MD AI可読性レビュー: Codex依頼 月次adapter 51社作成ドラフト

- 日時: 2026-05-07 14:00 JST
- 対象: `C:\tmp\codex_delegation_draft_51companies.md`
- パターン: 1（まっさらレビュー）
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/097_mr_codex_delegation_51companies.md`
- ユーザー指定重点: (1) RegexとGeminiの使い分け基準の明確性・詳細性 (2) 紛らわしい表現 (3) 通常AI可読性

---

## 【サマリー】

Codex（GPT）に月次開示PDFのextract_adapter.jsonを51社分作成させるための委譲指示書。テスト10社の教訓が反映されており、extraction_method判断基準も具体例付きで詳しい。

- **AI可読性評価: B**
- **誤読リスク評価: C**
- **主要リスク**:
  1. HTMLソースの場合の`extraction_method`の記述が実コードの挙動と矛盾しており、Codexが誤ったadapterを作成するリスク
  2. 出力フォーマットに042正式スキーマで定義されていないキー（`year_from_title_regex`等）が含まれず、一方で正式スキーマにある重要キー（`bc_key`, `custom_prompt`等）への言及が不足
  3. `description` overfit禁止ルール（042知見MD記載）への言及が完全に欠落しており、Codexが月固定・サンプル値入りのdescriptionを量産するリスク

---

## 【Markdown 品質評価】

### Accuracy / 正確性: C

- **HTMLソースの`extraction_method`記述が実コード（`extract_monthly_data.py`）と矛盾**: 対象MD L147「source が HTML だった場合、`extraction_method` はHTMLパーサーが使われるため regex / gemini とは独立」と記載。しかし実コード（L3698-3781）では `source == "non-tdnet(html_table)"` かつ `extraction_method == "gemini"` の場合に `_extract_from_html_gemini_personal()` を呼び出しており、extraction_methodは独立ではなく明確に参照されている。「独立」という表現はCodexに「HTMLなら`extraction_method`は何でもよい」と誤読させる
- **出力フォーマットに `bc_key` が欠落**: 042正式スキーマ（042_monthly_disclosure_master.md L149）で `bc_key` はBC側正名マッピングとして定義されている。テスト10社の既存adapterでは省略されているケースもあるが、Codexに`bc_key`の存在を知らせないと、key名がBC名と不一致の場合に突合で全NGになる
- **出力フォーマットの `company_name` が `"2502"` のようにtickerのまま**: 既存adapter（2502, 2910等）で `company_name` がtickerのまま放置されている。これは既存の不備であり、ドラフトは「`{会社名}`」と正しく指示しているが、対象テーブルの51社中45社が `?` のまま — Codexが会社名を調べる方法への言及がない

### Completeness / 完全性: B

- **`description` overfit禁止ルール（042知見MD L277）が欠落**: 「adapter description に月固定指定（「最新月（2月）」等）・サンプル値を入れない。構造情報（テーブル名・行ラベル・列ヘッダー・単位）のみ」が未記載。gemini選択時のdescriptionの書き方は詳しいが、禁止事項が書かれていない
- **`custom_prompt` への言及不足**: 042正式スキーマのextraction_method別推奨キー（L160-163）では gemini に `custom_prompt` が推奨。ドラフトの出力フォーマットに `custom_prompt` が含まれていない
- **既存adapterに含まれるキー（`year_from_title_regex`, `month_from_title_regex`）への説明なし**: 既存adapter（2502等）にはこれらのキーが含まれている。正式スキーマでは未定義だが、コード上で参照されている（042 L340で不具合報告あり）。Codexがこれらを含めるべきか含めないべきか判断できない
- **`_excluded` 銘柄チェックへの言及なし**: 51社のうちに既に `_excluded: true` の銘柄が含まれていないかの確認手順が欠落

### Relevance / 関連性: A

- テスト10社の教訓が冒頭にまとまっており、具体例付きで実用的
- 不要な情報が少なく、作業に集中できる構成

### Actionability / 実行可能性: B

- 1社ずつの作業手順は明確で順序通りに実行可能
- ただしHTMLソースの場合の具体的な作業手順が不足（何を`extraction_method`に設定するか、HTMLパーサー用のdescriptionの書き方）
- 会社名が `?` の45社の名前をどう特定するかの指示がない

---

## 【AI 誤読リスク】

### R1. HTMLソースの extraction_method 記述（L147）

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:147`
**問題の記述**: 「source が HTML だった場合、`extraction_method` はHTMLパーサーが使われるため regex / gemini とは独立」
**AI の誤読パターン**: 「HTMLなら `extraction_method` は設定不要/何でもよい」と解釈し、`extraction_method` を省略するか `"regex"` のまま残す。しかし実コードでは `extraction_method == "gemini"` を明示的にチェックしてGemini HTML抽出を呼び出すため、省略するとデフォルト `"regex"` となり、HTMLのregex抽出パス（精度が低い）に入る
**トリガー**: テスト10社で4社がHTMLだった実績から、51社でも複数社がHTMLの可能性が高い
**影響**: HTML企業でadapterが機能しない、または低精度のregex抽出になる

### R2. gemini description の「含めるべき情報」リスト（L139-143）に禁止事項がない

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:139-143`
**問題の記述**: 「description に含めるべき情報: どのセクション/テーブルか、どの行か、どの列か、除外条件」
**AI の誤読パターン**: 「含めるべき」のみで「含めてはいけない」がないため、Codexが具体的な月名（「2月」）やサンプル値（「売上98.5%」）を参考としてdescriptionに含める可能性がある。042知見MDのdescription overfit禁止ルール（L277）に違反するadapterが量産される
**影響**: 月固定descriptionにより、他の月のデータ抽出時にGeminiが混乱し、誤った値を返す

### R3. 「迷ったら gemini」が regex 条件判断と競合する可能性

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:13, 98`
**問題の記述**: L13「迷ったら gemini」、L98「原則: 迷ったら "gemini" を選ぶ」
**AI の誤読パターン**: これ自体は良い指示だが、L99-106のregex5条件を満たすかの判定自体に迷いが生じうる。特に条件4「pdfplumberのテキスト抽出で行ラベルと数値が同一行に出る」はCodexがpdfplumberを実行して確認する必要があるが、作業手順にはpdfplumberでの抽出テストの具体的なコードが示されていない
**影響**: Codexがpdfplumber確認をスキップし、見た目の印象だけでregex/geminiを判断する可能性。ただし「迷ったらgemini」が安全方向のデフォルトであるため、影響は限定的

---

## 【MD 構成リスク】

### S1. 「最重要セクション」の見出しが他セクションと同レベル

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:94`
**現状**: `## extraction_method 判断基準（最重要セクション）` が他の `##` セクション（作業手順、出力フォーマット等）と同レベル
**リスク**: 「最重要」とラベルしているが、構造的には他セクションと同格。Codexが作業手順を上から順に読み進める際に、このセクションを「参考情報」として流し読みする可能性
**推奨**: セクション自体の構造は変更不要だが、作業手順のStep 5に「→ 下記 extraction_method 判断基準参照」のリンクがあるのは良い。ただしStep 5の記述がポインタのみで、判断基準セクションを読まないと作業が進まないことが明示されていない

### S2. 出力フォーマットと042正式スキーマの関係が不明

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:149-179`
**現状**: 出力フォーマットが独自定義されているが、042知見MDの正式スキーマ（042 L117-164）との関係が説明されていない
**リスク**: Codexが「このフォーマットが全て」と解釈し、正式スキーマの他のキー（`encoding`, `sheet_name`, `column_map`等）が必要な場合でも含めない。あるいは逆に、Codexが042知見MDを参照した場合に出力フォーマットとの差異で混乱する
**推奨**: 「これは042正式スキーマのサブセット。上記以外のキーが必要な場合は042知見MDの正式スキーマを参照」と一文追加

---

## 【指示優先順位・文脈境界】

### P1. 情報源テーブル（L196-201）で042知見MDを参照させているが、競合する記述の優先順位が未定義

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:196-201`
**現状**: `042_monthly_disclosure_master.md` を情報源として列挙。Codexが042を読んだ場合、本ドラフトの記述と042の記述が矛盾する箇所（特にHTML extraction_method、description禁止事項）がある
**リスク**: Codexがどちらの記述に従うか判断できない
**推奨**: 「本ドラフトの記述が042知見MDと矛盾する場合、042知見MDを優先する」と明記

---

## 【重大な指摘】（即修正）

### #1 HTMLソースの extraction_method 記述が実コードと矛盾

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:147`
**問題**: 「extraction_method はHTMLパーサーが使われるため regex / gemini とは独立」は誤り。実コード（`extract_monthly_data.py` L3698-3781）では `extraction_method == "gemini"` を明示的にチェックしてGemini HTML抽出を使用する
**AI の誤読パターン**: HTMLならextraction_methodは無関係と解釈し、省略または不適切な値を設定
**トリガー**: GCS実体確認でHTMLを発見した場合（テスト10社で40%がHTML）
**影響**: HTML企業のadapterが期待通りに機能しない
**根拠**: `extract_monthly_data.py` L3702 `if extraction_method == "gemini" and not batch_mode:` で明確に参照
**推奨対応**: 以下に修正:
> source が HTML だった場合:
> - `source` を `"non-tdnet(html_table)"` に変更
> - `extraction_method` は `"gemini"` を設定（HTMLもGeminiで抽出される）
> - fields の description にはHTMLテーブルの位置情報（何番目のテーブルか、セクション名等）を記述

**MD修正だけで足りるか**: 足りる

### #2 `description` overfit禁止ルールが欠落

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:127-143`（gemini description の書き方セクション全体）
**問題**: 042知見MD L277の「adapter description に月固定指定（「最新月（2月）」等）・サンプル値を入れない」ルールが未記載。「含めるべき情報」だけでは、Codexが自然にサンプル値を含めてしまう
**AI の誤読パターン**: 確認に使ったPDFから「2月」「98.5%」等の具体値をdescriptionに含める。本番抽出時に他の月で混乱
**トリガー**: 全gemini企業（大半がgeminiになる見込み）のdescription作成時
**影響**: 51社のadapterのdescriptionが月固定になり、本番抽出でGeminiの精度低下
**根拠**: 042知見MD L277 description overfit禁止ルール
**推奨対応**: gemini description の書き方セクションに以下を追加:
> **description に含めてはいけない情報（overfit禁止）**:
> - 特定の月名（「2月」「最新月」等）— 月は抽出スクリプト側でプロンプトに動的挿入される
> - サンプル値（「売上98.5%」等）
> - descriptionには構造情報（テーブル名・行ラベル・列ヘッダー・単位）のみ記載する

**MD修正だけで足りるか**: 足りる

### #3 出力フォーマットに `bc_key` の言及がない

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:149-172`
**問題**: `bc_key`（042正式スキーマ L149）は fields[] の要素で、BC側正名マッピングに使用される。keyとBC名が一致しない場合、bc_keyを設定しないと突合で全NGになる
**AI の誤読パターン**: Codexがkey名をPDF上の表記に合わせて設定。structure.jsonのmetrics名（=BC名）と不一致の場合、bc_keyなしでは突合不可能
**トリガー**: PDFの表記とstructure.jsonのmetrics名が異なる企業（頻出）
**影響**: 後工程のBC突合で大量のNGが発生し、手動修正が必要
**根拠**: 042知見MD L149 `bc_key` 定義、L234 フィールド命名設計方針
**推奨対応**: 出力フォーマットの fields 内に `"bc_key"` を追加し、以下の指示を追記:
> - `bc_key`: structure.json の `monthly_items[].name` と完全一致させる。`key` はPDFの表記に合わせてよいが、`bc_key` は必ずstructure.jsonの名前にする。両者が同一なら省略可

**MD修正だけで足りるか**: 足りる

### #4 会社名（`company_name`）の特定方法が不明

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:19-73`（対象テーブル）
**問題**: 51社中45社の `company_name` が `?` のまま。出力フォーマットでは `"{会社名}"` と指定されているが、会社名をどこから取得するかの指示がない
**AI の誤読パターン**: Codexがtickerだけでadapterを作成し、company_nameをtickerのままにする（既存adapter 2502, 2910で既にこの状態）。または適当に推測する
**トリガー**: 全51社のadapter作成時
**影響**: company_nameが不正確なadapterが51社分作成される
**根拠**: 既存adapter（2502, 2910）で `company_name` がtickerのまま放置されている事実
**推奨対応**: 「company_name は structure.json の `company_name` フィールド、または GCS PDF のファイル名から取得する」と明記

**MD修正だけで足りるか**: 足りる

### #5 regex 条件4「pdfplumberで同一行に出る」の確認方法が不明

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:104`
**問題**: 「pdfplumber のテキスト抽出で行ラベルと数値が同一行に出る」をCodexがどう確認するかの手段が不明。作業手順Step 3でGPT Visionは言及されているが、pdfplumberの実行コード・手順はない
**AI の誤読パターン**: pdfplumber確認をスキップし、PDFの見た目だけでregexの適否を判断。結果としてregexが不適切な企業にregexを設定
**トリガー**: regex 5条件を確認しようとした場合
**影響**: 「迷ったらgemini」の安全弁があるため限定的だが、Codexがregexを選択する場合に判断根拠が弱くなる
**根拠**: 作業手順Step 3にpdfplumber実行の具体的コードがない
**推奨対応**: 作業手順Step 3に pdfplumber でのテキスト抽出テストのコードスニペットを追加、または regex条件4を「GPT Vision でテーブルが単純な構造であることを確認」に緩和

**MD修正だけで足りるか**: 足りる

---

## 【改善提案】（中優先度）

### #1 出力フォーマットと042正式スキーマの関係を明示

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:149`
**現状**: 出力フォーマットが独自定義で、042正式スキーマとの関係が不明
**提案**: 出力フォーマットの直前に「本フォーマットは042知見MD §extract_adapter.json正式スキーマのサブセット。上記以外のキーが必要な場合は042を参照」と追記
**期待効果**: Codexが正式スキーマを意識し、必要に応じて追加キーを設定できる

### #2 `custom_prompt` の使い所を説明

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:149-172`
**現状**: 042正式スキーマでgemini推奨の `custom_prompt` が出力フォーマットに含まれていない
**提案**: 出力フォーマットの追加フィールドに `custom_prompt` を追加。用途: 英語PDFの日英対応、会計年度境界の指示等
**期待効果**: 特殊なPDF構造の企業でGemini抽出精度が向上

### #3 51社テーブルの `fields数（仮）` に structure.json ベースである旨を強調

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:75`
**現状**: 注記 `> fields数（仮）は structure.json から仮再構築した値。実PDFと突合して変わる可能性がある。` はあるが、テーブル直下に1行のみ
**提案**: テーブルのヘッダ列名を `fields数（仮・structure.json基準）` に変更し、「PDFに存在しない指標は含めない」を再強調
**期待効果**: Codexがstructure.jsonの値を鵜呑みにせず、実PDF確認を優先する

### #4 build_log.csv の書き込みタイミングを明示

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:205`
**現状**: build_log.csv への追記は期待成果物に記載されているが、1社完了ごとか全社完了後かが不明
**提案**: 「1社処理完了ごとに即追記する」と明記
**期待効果**: 途中で中断した場合に進捗が失われない

### #5 GCS PDFのDLコマンド例を追加

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:79`
**現状**: 「GCSからPDFをDL」とあるが具体的なgsutilコマンドがない
**提案**: `gsutil cp "gs://stock_data_1930932/monthly/docs/{ticker}/*" C:\tmp\monthly_pdf\{ticker}\` のコマンド例を追加
**期待効果**: Codexがパス構造を間違えずにDLできる

### #6 6412（平和）の特別扱いを作業手順にも反映

**箇所**: `C:\tmp\codex_delegation_draft_51companies.md:17, 192`
**現状**: 教訓L17と制約L192に記載があるが、作業手順には「6412は既存adapterを破棄して再作成」のステップが明示されていない
**提案**: 作業手順の冒頭またはStep 1の注記に「6412は既存adapterを参照せず、現行PDFのみから作成」を追加
**期待効果**: 6412処理時に古いadapterの構造を参考にしてしまうリスクを排除

---

## 【ソースコード・仕組み側への波及】

MD修正で十分対応可能。ソースコード側の変更は不要。

---

## 【修正文案】

### 重大指摘 #1: HTMLソースの extraction_method（L147 を置換）

**before**:
```
### HTMLの場合

source が HTML だった場合、`extraction_method` はHTMLパーサーが使われるため regex / gemini とは独立。fields の `description` にセレクタやテーブル位置の情報を書く。
```

**after**:
```
### HTMLの場合

GCS実体がHTMLだった場合:
- `source` を `"non-tdnet(html_table)"` に変更する
- `extraction_method` は `"gemini"` を設定する（コード上で明示的に参照される。省略するとデフォルトの `"regex"` になりHTML regex抽出パスに入る）
- fields の `description` にHTMLテーブルの位置情報（何番目のテーブルか、セクション名等）を記述する
```

### 重大指摘 #2: description overfit禁止（L143 の後に追加）

```
**description に含めてはいけない情報（overfit禁止）**:
- 特定の月名（「2月」「最新月」等）— 月は抽出スクリプトのプロンプトで `{month_val}月` と動的に渡される
- サンプルの具体的な数値（「売上98.5%」等）
- descriptionには構造情報（テーブル名・行ラベル・列ヘッダー・単位）のみ記載すること
```

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 推奨はすべて対象MD（`C:\tmp\codex_delegation_draft_51companies.md`）への修正に閉じている
- 042知見MDへの変更は推奨していない（042は正式スキーマの正本であり、対象MDが042に合わせるべき方向）
- memory への書き込み推奨なし — 問題なし

### 8b. 上位ルール整合性チェック

- 推奨はすべて042知見MD（正式スキーマ）との整合を図る方向であり、CLAUDE.mdのルールに違反しない
- description overfit禁止は042知見MDに明文化済みのルールであり、対象MDへの転記を推奨しているのみ
- bc_key の追加推奨は042正式スキーマに基づいており、既存ルールとの矛盾なし

**8b チェック質問**:
- Q1: description overfit禁止は042知見MDに明文化済み。CLAUDE.md追加は不要
- Q2: 「Codex委譲時に042正式スキーマとの整合を確認する」という汎用ルールはCLAUDE.mdに存在しない。ただし083 Codex分業ワークフローに「コードレビュー・プロジェクト規約との整合性確認を実施」とあり、現行のカバー範囲内
- Q3: 本件は個別MD固有のfixで十分。Codex委譲MDは定型ではなくタスク固有の都度作成物

### 8c. 副作用シミュレーション

**(i) 単体副作用**: 修正後のMDで新たな誤読リスクはない。HTMLの`extraction_method`が明確になり、description禁止事項が追加されることで、Codexの行動がより制約される方向（安全側）

**(ii) クロスルール競合**: 「迷ったらgemini」（L98）とHTMLの「`extraction_method`は`"gemini"`を設定」は矛盾しない（HTML時はgemini一択なので迷う余地がない）。regex 5条件とdescription overfit禁止も独立した観点であり競合しない

**(iii) 状態依存シナリオ**: 本MDは状態を持たない（1回限りの委譲指示書）。該当なし

**(iv) 再発防止策の実効性**: 本件は新規作成MDのレビューであり、再発防止策は対象外。指摘は全てMD記述の修正であり、意志依存の注意喚起ではない

### 8d. 事後確認事項

- Codex作成のadapterで `extraction_method` が空欄・省略されたHTML企業がないか確認する
- Codex作成のadapterの `description` に月名やサンプル値が含まれていないか確認する
- `bc_key` が未設定のadapterについて、key と structure.json の metrics名が一致するか確認する

---

## 【確認できなかった事項】

1. 51社のうち実際に `_excluded: true` の銘柄が含まれていないか — 現行adapterファイルを全件確認する必要があるが、本レビューでは閲読範囲を限定した
2. 対象テーブルの `source（現設定）` と実際のGCS上のファイル形式の一致 — GCSを直接確認していないため、HTML化されている企業数は不明
3. Codexの実行環境でGCSアクセスが可能かどうか（認証キーのパスが `C:\gdrive\...` で記載されているが、Codex実行環境でこのパスが有効か）
4. `extract_monthly_data.py` の最新コードで `year_from_title_regex` が実際に参照されているか — 042 L340で不具合が報告されているが、現行コードでの扱いは未確認
