# md-reviewer 提出: GCS actual ファイル検索ミス（ファイル名規約の旧記載）

- 提出日: 2026-04-27 21:30 JST
- 提出者: メインエージェント
- パターン: 2（誤読・ミス原因レビュー）

---

## 事象

ユーザーが「決算予測 答え合わせ PREDICT_DATE='20260424' BOOK実行済み」と指示。
メインエージェントが GCS の actual ファイル存在確認のため `gcs_list` を実行したが、**プレフィックス `earnings_model/actuals/actual_20260425` で検索**して 0 件と判定。
実際のファイルは `actual_20260427_205253_for_20260424.json`（SAVE_DATE=20260427）で存在していた。

ユーザーに「actuals はまだ未作成」と報告しかけたところ、ユーザーが「あるぞ」と指摘して発覚。

## 直接原因

メインエージェントが actual ファイル名の構造を `actual_{ACTUAL_DATE}_...` と誤認し、ACTUAL_DATE=20260425 で検索した。
実際の命名規約は `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`。

## MD上の原因（修正済み）

`docs/knowledges/tools/059_earnings_model_eda.md` の GCS 保存先セクション（旧 L226-228）に以下の旧記載があった:

```
actuals/actual_YYYYMMDD.json           # 実績突合結果
```

2026-04-15 にノートブック cell-9 のファイル名規約が変更（`actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`）されたが、知見ファイルの GCS パス表記と「GCS ファイル確認時の注意」セクションが旧形式のまま残っていた。

さらに注意書き L234 に:
```
最新ファイルの特定: ファイル名は `actual_{PREDICT_DATE}_{HHMMSS}.json` 形式。
```
と書かれており、SAVE_DATE ベースの命名に言及がなかった。

## 既に実施した修正

メインエージェントが 059 MD を以下のように修正済み:
- GCS 保存先パス表記を `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json` に更新
- 検索方法の正誤パターンを明記（`actual_{ACTUAL_DATE}` で検索するのは誤り）
- 事故事例を具体的に記載

## レビュー依頼

1. 修正後の 059 MD の AI 可読性は十分か（修正が足りているか / 新たな誤読リスクがないか）
2. 根本原因（ノートブック変更時に知見ファイルが追随しなかった）への再発防止策
3. 不備蓄積ログ（004-1）への追記

## 関連ファイル

- `docs/knowledges/tools/059_earnings_model_eda.md` — 修正対象（修正済み）
- `scripts/earnings_model/earnings_model_predict.ipynb` cell-9 — actual ファイル名生成コード

---

## AI可読性レビュー追記: 2026-04-27 22:00 JST — md-reviewer

- パターン: 2（誤読・ミス原因レビュー）
- レビュアー: Claude (md-reviewer skill)
- 対象: `docs/knowledges/tools/059_earnings_model_eda.md`（GCS ファイル確認時の注意セクション修正の妥当性）

---

### 【サマリー】

- レビュー対象の要約: 059 MD の GCS パス表記が旧形式のまま残存し、AI が ACTUAL_DATE ベースで検索して actual ファイルを見逃した事象への修正レビュー
- AI可読性評価: B — 修正後は検索方法の正誤パターンが明記されたが、3つの日付概念の定義が分散しており初見の AI が混同する余地がある
- 誤読リスク評価: B — 検索の禁止パターンが具体的に書かれた点は良いが、cell-11 のコードコメントに旧形式の残骸があり精度集計時に混乱を起こす残存リスクがある
- 主要リスク:
  - cell-11（精度集計）のコメントと `_by_date` ロジックが旧命名前提のまま残存（コード側）
  - 3つの日付（PREDICT_DATE / ACTUAL_DATE / SAVE_DATE）の定義が MD 冒頭のセル構成表にしか記載がなく、GCS セクションを単独参照する AI が定義を把握できない
  - 059 MD L126 の `download_review_data.py` DL先パスコメント `actual_YYYYMMDD.json` は旧形式表記だが、ローカル保存ファイル名としては正しい（GCS ファイル名とは異なる）ため、区別が曖昧

---

### 【Markdown 品質評価】

#### Accuracy / 正確性
- 修正後の GCS パス表記（L226-228）は実コード（cell-9 L1170: `actual_{_save_date}_{_ts_a}_for_{PREDICT_DATE}.json`）と一致。正確
- 修正後の検索方法（L236-239）は正しい。禁止パターン `actual_{ACTUAL_DATE}` の明示は有効
- `data_catalog.md` L1077 も新命名規約を反映済みで整合
- `batch_rerun_predict.py` L825 も同一命名規約。整合

#### Completeness / 完全性
- 検索の正誤パターンが3つ列挙されており十分
- ただし「SAVE_DATE を特定できない場合」（答え合わせを別日に実行した可能性）のフォールバック戦略がない。`_for_{PREDICT_DATE}` サフィックス検索は記載されているが、それが「SAVE_DATE 不明時の主戦略」であることが明示されていない
- L231 に「ACTUAL_DATE はファイル名に含まれない」と明記されている点は良好

#### Relevance / 関連性
- 事故事例の具体的記載（L239: `SAVE_DATE=20260427 なのに ACTUAL_DATE=20260425 で検索→0件`）は AI の注意を引くのに有効
- 旧タイムスタンプ注意書きの削除（旧: `created_at` で最新判断せよ）は適切。新命名ではファイル名ソートで最新判断可能

#### Actionability / 実行可能性
- 「actual を探す時」の具体的手順が3つの候補として列挙されている点は良好
- ただし AI が最初にどれを試すべきかの優先順位が不明。「まず `_for_{PREDICT_DATE}` で検索し、なければ `actual_{今日の日付}` で検索」のような順序指定があるとより安全

---

### 【AI 誤読リスク】

1. **「今日の日付」の曖昧さ** (L237): 「当日実行分」と書かれているが、AI が答え合わせを実行するのは必ずしも当日ではない。2日後に実行する場合、`actual_{今日の日付}` は「2日後の日付」であり SAVE_DATE として正しいが、AI が「答え合わせ対象日の当日」（= ACTUAL_DATE）と混同する余地がある
2. **PREDICT_DATE / ACTUAL_DATE / SAVE_DATE の3概念**: 059 MD 全体で、この3つの日付の定義は L34-35（cell-2 の設定セル説明）に暗黙的に記載されているのみ。GCS セクション（L222-240）を単独で読む AI は「PREDICT_DATE=決算発表日」「ACTUAL_DATE=翌営業日」「SAVE_DATE=スクリプト実行日」の区別を知らない可能性がある

---

### 【MD 構成リスク】

1. **旧タイムスタンプ注意書きの跡地**: 修正前の L234-235 にあった「タイムスタンプは UTC」「created_at を JST 変換して最新を判断」が削除され、新しい内容に置換されている。これ自体は適切だが、旧記述を参照する memory やキャッシュを持つ AI セッションが混乱する可能性がある（一般論として、この MD の問題ではない）
2. **059 MD L126 の DL先パス表記**: `DL先: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json` はローカルファイル名としては `download_review_data.py` L92 と整合する（ローカル保存名は PREDICT_DATE ベースで命名）。しかし GCS ファイル名と異なる命名であることが説明なく並記されており、AI が GCS ファイル名と混同するリスクがある
3. **cell-11 のコードコメント**: `earnings_model_predict.ipynb` cell-11 L1193 に `# actual_YYYYMMDD_HHMMSS.json -> date = YYYYMMDD` という旧形式前提のコメントが残存。新命名 `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json` では `parts[1]` = SAVE_DATE となり、`_by_date` の dedup キーが PREDICT_DATE ではなく SAVE_DATE になる。同一 PREDICT_DATE に対して異なる SAVE_DATE で2回答え合わせを実行した場合、両方が集計に含まれる（意図的かどうか不明）

---

### 【指示優先順位・文脈境界】

- `data_catalog.md` L1077 と `059_earnings_model_eda.md` L227 は両方とも新命名規約を記載しており整合。二重管理ではあるが、data_catalog は全データストアの索引、059 はドメイン固有の詳細という役割分担で問題ない
- `batch_rerun_predict.py` の §書き込み規則（059 MD L109-113）にも新命名が記載されており3箇所整合

---

### 【パターン 2: 誤読・ミス原因分析】

#### 事象
メインエージェントが GCS actual ファイルを `actual_20260425`（ACTUAL_DATE）プレフィックスで検索し、0件と判定。実ファイルは `actual_20260427_205253_for_20260424.json`（SAVE_DATE=20260427）。ユーザーが「あるぞ」と指摘して発覚。

#### 読み手がどう解釈した可能性があるか
1. 059 MD 旧 L226: `actuals/actual_YYYYMMDD.json` を読み、YYYYMMDD = ACTUAL_DATE（翌営業日）と推論。GCS ファイル確認時の注意セクション旧 L234 に `actual_{PREDICT_DATE}_{HHMMSS}.json` と書かれていたが、AI は PREDICT_DATE を読み飛ばして「actual の後の日付 = その actual が対応する日付」と解釈した可能性がある
2. 旧注意書きが `PREDICT_DATE` ベースと書いていたにもかかわらず ACTUAL_DATE で検索したのは、AI が「答え合わせ = ACTUAL_DATE のデータ」という意味的連想を優先した可能性がある

#### 直接原因
059 MD の GCS パス表記（旧 L226）が `actual_YYYYMMDD.json` という汎用プレースホルダのままで、YYYYMMDD が何を指すのか不明瞭だった。加えて旧 L234 は `actual_{PREDICT_DATE}_{HHMMSS}.json` と書いていたが、2026-04-15 の命名規約変更（SAVE_DATE ベース + `_for_` サフィックス）が反映されていなかった。

#### 根本原因
ノートブックのファイル名生成コード変更（2026-04-15、cell-9）に対して知見 MD の更新が同期されなかった。059 MD 内に「このセクションの記述はコード実装準拠で維持する」旨の同期義務ルールはスコアリング因子テーブル（L46）にはあるが、GCS パス表記にはない。

#### 誤読を許した MD 上の原因
1. **059 MD 旧 L226**: `actual_YYYYMMDD.json` — YYYYMMDD の意味が未定義。AI は文脈から推論するしかなく、actual → ACTUAL_DATE と連想
2. **059 MD 旧 L234**: `actual_{PREDICT_DATE}_{HHMMSS}.json` — 2026-04-15 変更後は不正確。変更日のインライン更新が漏れた
3. **同期ルールの欠如**: スコアリング因子テーブルには「コード実装と常に一致させる」ルールがあるが、GCS パス表記には同様のルールがなかった

#### 再発防止の方向性
1. **059 MD の GCS セクションに同期義務ルールを追加**: 「このパス表記は `earnings_model_predict.ipynb` cell-9 の保存コードと常に一致させること」
2. **3つの日付概念の定義を GCS セクション冒頭に置く**: PREDICT_DATE / ACTUAL_DATE / SAVE_DATE の1行定義を GCS 保存先セクションの直前に配置
3. **cell-11 のコードコメント更新**: 旧形式 `actual_YYYYMMDD_HHMMSS.json` コメントを新形式に修正
4. **ノートブック命名規約変更時の知見 MD 更新チェックリスト**: 現状は暗黙的な義務。CLAUDE.md のノートブック修正時フックまたは 059 MD 冒頭に明示

---

### 【重大な指摘】（即修正）

#### #1 cell-11 精度集計の `_by_date` ロジックが新命名規約に未対応
- 箇所: `scripts/earnings_model/earnings_model_predict.ipynb` cell-11 (精度集計セル、L1193 付近)
- 問題: コメント `# actual_YYYYMMDD_HHMMSS.json -> date = YYYYMMDD` が旧形式。`parts = filename.split('_')` で `parts[1]` を取ると新命名では SAVE_DATE が取れる。旧命名では PREDICT_DATE が取れた。dedup キーの意味が変わっている
- AI の誤読パターン: AI がこのセルを修正・デバッグする際、コメントを信じて `parts[1]` = PREDICT_DATE だと思い込む
- トリガー: 精度集計のバグ調査・修正時
- 影響: 同一 PREDICT_DATE に対して異なる日に答え合わせを実行した場合、両ファイルが集計に含まれる。`_by_date` の意図が「PREDICT_DATE ごとに最新1件」であれば、SAVE_DATE キーでは重複を排除できない
- 根拠: cell-9 L1170 の `actual_{_save_date}_{_ts_a}_for_{PREDICT_DATE}.json` と cell-11 L1193 のコメントが不整合
- 推奨対応: コメントを新形式に修正。`_by_date` キーを `_for_YYYYMMDD` サフィックスから PREDICT_DATE を抽出するロジックに変更するかどうかは機能要件の確認が必要
- MD 修正だけで足りるか: 足りない。コード修正が必要

#### #2 059 MD の検索候補に「SAVE_DATE 不明時の主戦略」が欠落
- 箇所: `docs/knowledges/tools/059_earnings_model_eda.md:237-238`
- 問題: 検索候補が3つ列挙されているが、優先順位が不明。特に「SAVE_DATE がいつか分からない」ケース（別日に実行された可能性）で AI が最初に何を試すべきかが不明確
- AI の誤読パターン: AI が最初の候補 `actual_{今日の日付}` を試し、0件で「ファイルなし」と判断してしまう（今回の事故パターンの再発）
- トリガー: 答え合わせが前日以前に実行済みの場合
- 影響: 再度 actual ファイル見逃し
- 根拠: 今回の事故では SAVE_DATE=20260427 で検索すべきだったが、ACTUAL_DATE=20260425 で検索した。修正後も「今日の日付で検索」が最初の候補なので、翌日以降のセッションで同じミスが再発しうる
- 推奨対応: 検索候補の順序を変更。`_for_{PREDICT_DATE}` を最初の候補にする（PREDICT_DATE はユーザーから指示される or ノートブック設定で明確なので最も確実）
- MD 修正だけで足りるか: 足りる

---

### 【改善提案】（中優先度）

#### #1 GCS パス表記に同期義務ルールを追加
- 箇所: `docs/knowledges/tools/059_earnings_model_eda.md:222-229`
- 現状: スコアリング因子テーブル（L46）には「コード実装と常に一致させること」が明記されているが、GCS パス表記にはない
- 提案: GCS 保存先セクション冒頭に `> **同期ルール**: このパス表記は earnings_model_predict.ipynb cell-9（actual 保存）/ cell-7（prediction 保存）のコードと常に一致させること。ノートブック変更時はこのセクションも同時更新する。` を追加
- 期待効果: ノートブック変更時の知見 MD 更新漏れ防止

#### #2 3日付概念の定義をGCSセクション近傍に配置
- 箇所: `docs/knowledges/tools/059_earnings_model_eda.md:222` 付近
- 現状: PREDICT_DATE / ACTUAL_DATE / SAVE_DATE の定義が cell-2 の説明（L34-35）にしかない。GCS セクションを単独参照する AI は定義を把握できない
- 提案: GCS 保存先セクションの直前に「用語: PREDICT_DATE=決算発表日、ACTUAL_DATE=翌営業日、SAVE_DATE=スクリプト実行日（答え合わせを実行した日）」の1行定義を配置
- 期待効果: GCS セクション単独参照時の日付混同防止

#### #3 059 MD L126 のローカルパスがGCSファイル名と混同されるリスク
- 箇所: `docs/knowledges/tools/059_earnings_model_eda.md:126`
- 現状: `DL先: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json` — ローカル保存名は PREDICT_DATE ベースで正しいが、GCS ファイル名（SAVE_DATE ベース）と異なることが説明されていない
- 提案: `DL先: C:/tmp/earnings_review/prediction_YYYYMMDD.json, actual_YYYYMMDD.json（※ローカル保存名。GCSファイル名とは異なる）` と注記
- 期待効果: ローカルファイル名と GCS ファイル名の混同防止

---

### 【ソースコード・仕組み側への波及】

- 対象: `scripts/earnings_model/earnings_model_predict.ipynb` cell-11（精度集計セル）
- 理由: コメントが旧形式のまま。`_by_date` の dedup キーが SAVE_DATE ベースになっているが、意図が PREDICT_DATE ごとの最新1件なのか SAVE_DATE ごとの最新1件なのか不明。新命名規約ではファイル名に `_for_{PREDICT_DATE}` が含まれるため、PREDICT_DATE ベースの dedup が可能
- 推奨対応: (1) コメントを新形式に修正 (2) `_for_` サフィックスから PREDICT_DATE を抽出して dedup キーにするか、JSON 内の `predict_date` フィールドで dedup するかを検討
- 検証方法: 新旧両形式の actual ファイルが混在する GCS 上で cell-11 を実行し、PREDICT_DATE ごとに1件のみ集計されることを確認

---

### 【修正文案】

059 MD L236-239 の検索候補を優先順位付きに変更:

```markdown
# before
2. **actual ファイルの検索方法**: ファイル名は `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`。**SAVE_DATE は答え合わせを実行した日（≠ ACTUAL_DATE）**。検索プレフィックスの候補は:
   - `earnings_model/actuals/actual_{今日の日付}` — 当日実行分
   - `_for_{PREDICT_DATE}` が含まれるか目視確認 — 過去実行分
   - **❌ `actual_{ACTUAL_DATE}` で検索するのは誤り**（2026-04-27 事故: SAVE_DATE=20260427 なのに ACTUAL_DATE=20260425 で検索→0件→見逃し）

# after
2. **actual ファイルの検索方法**: ファイル名は `actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`。**SAVE_DATE は答え合わせを実行した日（≠ ACTUAL_DATE）**。検索は以下の優先順で試す:
   - **[最優先]** `_for_{PREDICT_DATE}` サフィックスで全 actual をフィルタ — SAVE_DATE に依存しない最も確実な方法
   - `earnings_model/actuals/actual_{今日の日付}` — 当日実行分を確認する場合のみ
   - **❌ `actual_{ACTUAL_DATE}` で検索するのは誤り**（2026-04-27 事故: SAVE_DATE=20260427 なのに ACTUAL_DATE=20260425 で検索→0件→見逃し）
```

---

### 【確認できなかった事項】

- cell-11 の `_by_date` ロジックの意図（SAVE_DATE dedup と PREDICT_DATE dedup のどちらが正しいか）は、ノートブックの実行結果を見ないと確定できない。旧命名時代は PREDICT_DATE = `parts[1]` だったため両者が一致していたが、新命名では乖離する
- GCS 上に旧命名（`actual_{PREDICT_DATE}_{HHMMSS}.json`）と新命名（`actual_{SAVE_DATE}_{HHMMSS}_for_{PREDICT_DATE}.json`）が混在しているかどうか。`download_review_data.py` は両方を探索するロジックを持っているため混在前提だが、cell-11 は未対応の可能性がある
