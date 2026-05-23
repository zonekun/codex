# 204_cr_tdnet_orders_extract_skill

**提出日**: 2026-05-18
**提出者**: Claude (メインエージェント)
**レビュースキル**: code-reviewer
**レビューパターン**: 1（新規実装の妥当性レビュー）

---

## レビュー対象ファイル

| パス | 役割 |
|------|------|
| `skills/tdnet_orders_extract.md` | スキル正本（手順定義） |
| `.claude/commands/tdnet-orders-extract.md` | スラッシュコマンドラッパー（Agent 起動指示） |

参考情報（編集対象外、コンテキスト用）:
- `docs/plans/ad-hoc_tdnet_orders_extract_20260517_214618.md` — 元プランMD（SQL・JSONフォーマット・運用ルール）
- `docs/reviews/202_cr_tdnet_orders_extract.md` — 前回プランレビュー（プランMD向け、本スキルとは別）

---

## 事象・背景

### なぜ作ったか

`docs/plans/ad-hoc_tdnet_orders_extract_20260517_214618.md` の Phase 2（残り全銘柄抽出）を、
**1 銘柄 = 1 Agent 起動**の形に分解できるようにするためのスキル化。

Phase 1（30社サンプル）と Phase 2 序盤（60社処理済）は単一セッション内で
連続処理していたが、コンテキスト圧縮の影響が大きいため、呼び出し元が pending リストを
管理し、銘柄ごとに本スキルを Agent 起動する設計に変更した。

### 設計上の重要決定

1. **本スキルはインデックスを触らない** — pending.txt / `_failed.csv` の更新は呼び出し元の責任
2. **既存 JSON があっても上書き** — Phase2 で 1 文書だけ読んだ `partial; 1_of_6_docs_read` を 6 文書版で置き換えるのが目的の 1 つ
3. **BQ → GCS PDF 直読** — CHUNK_TEXT は受注高/受注残高分類で NULL になる設計のため
4. **Python スクリプトで効率化禁止** — PDF 読みは Agent 内の Read ツール経由（ユーザー指示）
5. **失敗パターンを 6 種類定義** — `failed_no_bq_records` / `failed_no_gcs_files` / `failed_pdf_unreadable` / `failed_no_data` / `completed_partial` / `completed`

---

## レビュー観点

### 1. 設計の妥当性

- BQ SQL の条件（`MAIN_CATEGORY='受注高/受注残高' OR SUB_CATEGORIES に含む` + `AI_STATUS='completed'` + 日付範囲）が **プランMDの設計判断と整合**しているか
- 抽出対象（受注ベース指標）と対象外（売上/利益/BS/CF/配当/業績予想/構成比/入居率/役務売上）の境界が明確か
- 「期待する数値がなかった」場合の判定基準（キーワード無し/定性のみ/構成比のみ/OCR無し）が運用可能か

### 2. 戻り値（STATUS）の網羅性

- 6 STATUS（completed / completed_partial / failed_no_bq_records / failed_no_gcs_files / failed_pdf_unreadable / failed_no_data）で実運用の失敗パターンを **網羅できているか**
- 呼び出し元が `_failed.csv` に追記する際に **STATUS から `reason` が一意に決まるか**

### 3. JSON フォーマットの一貫性

- 短縮キー（t/n/u/d/doc_id/src/title/kind/cum/axis/h/r）の定義に **抜け漏れがないか**
- `cum` × `axis` の 4 通り組合せ説明と、セグメント別の `<期>-<セグメント名>` 埋込みルールが整合しているか
- 三菱重工 7011 の例が **複数 d 要素・複数 cum/axis 組合せ・複数年度** を網羅できているか

### 4. 禁止事項・例外処理

- 禁止事項 4 項目（Python スクリプト効率化禁止 / インデックスファイル編集禁止 / 複数銘柄同時処理禁止 / 既存 JSON 読みに行かない）が **過不足ないか**
- スキャン PDF / 10p 超 PDF / FILE_NAME 不一致 の各失敗ケースで **STATUS と JSON 出力有無の対応関係**が一貫しているか

### 5. ラッパー（command）との整合

- `.claude/commands/tdnet-orders-extract.md` が **Agent ツール起動指示**として最小限・明確か
- スキル正本に書かれている呼び出し元責任が **ラッパー側にも明記されているか**

---

## スコープ外（レビュー対象外）

- 呼び出し元側の実装（pending リスト管理・並列起動・進捗報告 LINE 通知 等）
- 元プランMD（`ad-hoc_tdnet_orders_extract_20260517_214618.md`）の内容そのもの
- 089/042 知見MDの内容（ユーザー指示で参照禁止）
- 既存 60 社分の `partial; 1_of_6_docs_read` JSON の品質

---

# コードレビュー: TDnet 受注高・先行指標 抽出スキル（/tdnet-orders-extract）

- 日時: 2026-05-18 (JST)
- 対象:
  - `skills/tdnet_orders_extract.md`
  - `.claude/commands/tdnet-orders-extract.md`
- パターン: 1（新規実装の妥当性レビュー）
- レビュアー: Claude (code-reviewer runbook, sub-agent)

---

## 【サマリー】

- 変更の要約: TDnet 開示 PDF から受注高・受注残高等の先行指標数値を抽出する「1 銘柄 = 1 Agent 起動」型スキルを新規作成。BQ で対象 DOC_ID/FILE_NAME を取得 → GCS から PDF を `gsutil cp` で DL → Read ツールで読み取り → 短縮キー JSON を `C:/gdrive/claude/work/{ticker}.json` にアトミック保存 → 結果報告。インデックスは触らない。
- 品質評価: **B** — 設計判断（GCS PDF 直読・1 銘柄 1 起動・6 STATUS）は妥当で前回レビュー#1〜#4 の指摘を概ね消し込めている。ただし (1) `t` / `n` / `u` を「必須」と言い切れない状況（`failed_*` で JSON 出力なしのケース）の契約が曖昧、(2) `note` フィールドがフォーマット定義から漏れている、(3) Step 5 と Step 8 の判定基準にズレ（`failed_pdf_unreadable` の発火条件）、(4) ラッパーが他スキルと比べても情報量が薄く呼び出し元責任の明示なし、の 4 点で運用前に詰めるべき穴がある。
- 主要リスク:
  1. `note` フィールドが「フォーマット例」と「キー定義」に出てくるが「キー定義」リストには載っていない → JSON 再パース側で扱いがブレる
  2. `STATUS=failed_pdf_unreadable` の判定が「全 PDF を Read 失敗」だが、Step 5 で「10p 超は pages 指定で再読み」とあるため**何回トライしても発火しない**ロジックホールの可能性
  3. ラッパー `.claude/commands/tdnet-orders-extract.md` が 2 行しかなく、引数（ticker）の受け渡し方法が `$ARGUMENTS` 等の規約に従っているか不明

## 【重大な指摘】（即修正）

### #1 `note` フィールドがフォーマット定義に漏れている（JSON スキーマ不整合）

- 箇所: `skills/tdnet_orders_extract.md:124-145` (JSON フォーマット例) / `skills/tdnet_orders_extract.md:147-160` (キー定義)
- 事象: JSON フォーマット例の L128 に `"note": "<業種_状況; 重要イベント; partial; N_of_6_docs_read など>"` というキーがあるが、直下の「キー定義」リスト（L147-160）には `note` が一切現れない。例（三菱重工 7011, L164-205）でも `note` が抜けている。
- トリガー: 呼び出し元（または将来この JSON を集計する側）が「note は必ず入る optional 文字列か / `completed_partial` 以外では省略していいのか / 三菱重工例にあるように完全に書かない場合の意味は何か」を判定する根拠が無い。
- 影響: スキーマ駆動の再パース（pydantic 等）を組む際に、フィールドが optional か required か / 値ドメインが何か（自由文字列 vs 構造化トークン `partial; N_of_6_docs_read`）が判定不能。STATUS = `completed_partial` のときに `note` に `partial; N_of_6_docs_read` を明記しろと L252 にあるが、フォーマット規約側の契約として閉じていない。
- 根拠: フォーマット例には登場、キー定義リストには未記載、三菱重工サンプルにも未記載という 3 点の不整合。
- 推奨対応 (**[方向性]**):
  - L147 のキー定義リストに `note` を追加し、(a) optional か required か、(b) `STATUS=completed_partial` のときの必須文字列（`partial; N_of_6_docs_read`）と (c) その他自由記述（`業種_状況`, `重要イベント` 等）の区切りルール（セミコロン区切り？）を明文化する
  - 例 JSON（三菱重工）にも `note` フィールドの実例（`"note": null` でもよい）を 1 つ入れて、フォーマット例と契約を一致させる

### #2 `failed_pdf_unreadable` の発火条件にロジックホール

- 箇所: `skills/tdnet_orders_extract.md:80` (Step 5) / `skills/tdnet_orders_extract.md:255` (STATUS 表)
- 事象: Step 5 では「各 PDF を Read ツールで読む（pages 指定なし）。10p 超で読めない場合は `pages` 指定で再読み」とある。STATUS 表では `failed_pdf_unreadable` = 「PDF を**全て** Read 失敗（スキャン PDF 等）」と定義。だが「全て」のスコープが (a) 全 PDF (`DOCS_FOUND` 件) が 1 件も読めない、なのか (b) 1 つの PDF で初回 Read 失敗→pages 再試行も失敗、なのか不明。さらに「pages 指定で再読み」を**何ページ目から何ページ目まで**、**何回試すか**の打ち切り条件が無い。
- トリガー: 文書 6 件のうち 1 件がスキャン PDF 100p、残り 5 件が読める通常 PDF だった場合、(a)解釈なら `completed_partial`、(b)解釈なら `failed_pdf_unreadable` にもなり得る。pages 再試行ループの打ち切り条件が無いと、巨大スキャン PDF で Agent が何十回も Read を試行して context 枯渇する。
- 影響: 6 STATUS の意味が分岐実装で発散する。失敗銘柄が `_failed.csv` に登録される条件が Agent 起動ごとに揺れ、「pending → completed 移動」する呼び出し元の判定が一貫しなくなる。
- 根拠: Step 5 のリトライ規約が「再読み」の 3 文字のみ、STATUS 表の `failed_pdf_unreadable` の条件文「全て Read 失敗」と「`completed_partial` の条件文「6 文書未満しか PDF が無い、もしくは一部しか数値が取れなかった」」が交差する境界が定義されていない。
- 推奨対応 (**[方向性]**):
  - Step 5 に「リトライポリシー: 初回 Read 失敗 → pages=1-5 / 6-10 で再試行 → それでも空なら当該 PDF を skip し次の PDF へ。1 PDF あたり最大 3 回まで」のような**打ち切り条件**を明示
  - STATUS 表で以下のように発火条件を排他的に再定義（案）:
    - `failed_pdf_unreadable` = 「`DOCS_READ=0`、つまり 1 つも PDF を Read できなかった」
    - `completed_partial` = 「`DOCS_READ>=1` かつ `DOCS_WITH_DATA>=1` かつ（読めなかった PDF が 1 件以上 or `DOCS_FOUND<6`）」
    - `failed_no_data` = 「`DOCS_READ>=1` かつ `DOCS_WITH_DATA=0`」
  - 戻り値テンプレート (Step 8) の `DOCS_READ` / `DOCS_WITH_DATA` を上記判定の入力として明示的にリンクさせる

### #3 ラッパー `.claude/commands/tdnet-orders-extract.md` の情報量が薄い

- 箇所: `.claude/commands/tdnet-orders-extract.md:1-5`
- 事象: ラッパー本文は実質「skills/tdnet_orders_extract.md を Read し、Agent ツールで独立実行せよ。引数として渡された ticker（4桁英数）1 銘柄分の JSON を生成し、結果報告のみ呼び出し元に返す。インデックス（pending/completed 等）の更新は呼び出し元の責任。」のみ。
  - 他スキルでは `.claude/commands/batch-owner-judge.md` / `.claude/commands/classify-tob.md` のように「会話内で従え」型 と、`.claude/commands/judge-owner-character.md` のように「Agent ツールで独立実行せよ。結果のみ報告」型 の 2 パターンを使い分けている。本スキルは後者寄り（Agent 起動）だが、引数の受け渡し方法（slash command の `$ARGUMENTS` 規約や、呼び出し元が会話文に書いた ticker をどう拾うか）が**明示されていない**。
- トリガー: 呼び出し元が `/tdnet-orders-extract 7011` のように打った場合に `7011` が Agent に渡る経路が、スキル正本にも commands ラッパーにも書かれていない。`.claude/commands/` の他ファイルにも `$ARGUMENTS` の使用例が無いので、現状の運用がどうなっているかが本レビューでは確認できない。
- 影響: 呼び出し元（メインエージェントが新セッションでスキル起動する場合 / 別 AI / 別運用者が触る場合）に「ticker をどう渡せばいいか」が伝わらない。Phase 2 のメインエージェントがこのスキルを呼び出して 1000+ 銘柄を回す運用の根幹なので、入力受け渡し規約は明文化が必須。
- 根拠: ラッパー本文 2 行、スキル正本「入力」表（L13-19）に「Agent 呼び出し時にどう渡すか」の記述なし。
- 推奨対応 (**[方向性]**):
  - ラッパー `.claude/commands/tdnet-orders-extract.md` に呼び出し例（例: `/tdnet-orders-extract 7011` で第一引数が ticker）と、Agent 起動時の prompt テンプレート（「ticker=7011 として skills/tdnet_orders_extract.md の手順を実行せよ」のような最小指示）を 1 ブロック追記する
  - またはスキル正本 §入力 直下に「呼び出し元責任: メインエージェントは Agent ツール起動時に prompt 内で `ticker={ticker}` を明示すること」と 1 行追加
  - **記載先**: ラッパー側（commands ファイル）優先。スキル正本は「処理手順の正本」であり、入力受け渡し規約は「呼び出し方」なので commands 側に置く方が自然

## 【改善提案】（可読性・保守性）

### #1 抽出対象外リスト（Step 5）と「失敗銘柄リスト化対象（偽陽性）」の対応が読みづらい

- 箇所: `skills/tdnet_orders_extract.md:92-106` (抽出対象外表) / `skills/tdnet_orders_extract.md:256` (`failed_no_data` 備考)
- 現状: 抽出対象外カテゴリ（売上高・利益・BS/CF・配当・構成比・入居率等）が 9 行の表で示され、それとは別に `failed_no_data` 備考に「偽陽性銘柄（Gemini 分類エラーで紛れ込んでいる業種、例: 水産・バイオ・サービス業）」が登場する。「抽出対象外」と「偽陽性で `failed_no_data` に落ちる」の関係（前者を全部排除した結果として後者になるのか / 別判定なのか）が読み手にすぐ伝わらない。
- 提案: 「Step 5 抽出対象外を適用して、それでも数値抽出できる場合 = `completed/completed_partial`、対象外項目しか無い場合 = `failed_no_data`」と明示的な接続文を 1 行入れる。提出 MD 観点 1 の「期待する数値がなかった場合の判定」とも整合する。

### #2 `cum` × `axis` 4 通り組合せの実例提示が 2 通りまで

- 箇所: `skills/tdnet_orders_extract.md:162-205` (三菱重工 7011 例)
- 現状: 三菱重工例には `cum=true, axis=null`（受注高）と `cum=false, axis="セグメント"`（受注残高）の 2 通りしか出ていない。残り 2 通り（`cum=false, axis=null` / `cum=true, axis="セグメント"`）の最小例が無い。
- 提案: 4 通り全部の最小例を 1 つずつ並べるか、もしくは「他の 2 通りは `cum`/`axis` 値を変えるだけで `h`/`r` の構造は同じ」と明記する。前回レビュー#3 の `h`/`r` 規約統一の指摘を踏襲するなら、4 通り全てで同じ `h=["期","1Q","2Q","3Q","4Q"]` パターンを使えるはずなので、それを明示する 1 行を入れた方が再パース側が安心して書ける。

### #3 短縮キー定義の `kind` 値カタログ化

- 箇所: `skills/tdnet_orders_extract.md:88-90` (抽出対象ラベル列挙) / `skills/tdnet_orders_extract.md:153` (キー定義 `kind`)
- 現状: 抽出対象ラベル（受注高 / 受注金額 / 受注工事高 / 新規受注高 / 受注残高 / 繰越高 / 次期繰越高 / 繰越工事高 / 手持工事高 / 受注棟数 / 受注戸数 / 受注件数 + セグメント別/部門別/地域別/製品別の受注内訳）が列挙されているが、`kind` 値として「文書記載どおり」と書かれているため、表記揺れが JSON 全体に持ち込まれる。
- 提案: 一過性ジョブなので無理に正規化する必要は無いが、Phase 2 完了後に集計する側が困らないよう「`kind` 候補値は上記列挙の表記を優先し、文書独自表現の場合は note にメモする」のようなゆるい規約を 1 行入れる。または「`kind` は文書記載どおりで OK、後処理で正規化する前提」と非スコープを明示する。

### #4 Step 4 の出力ディレクトリ事前作成手順なし

- 箇所: `skills/tdnet_orders_extract.md:70-72` (gsutil cp の DL 先) / `skills/tdnet_orders_extract.md:212-215` (アトミック保存)
- 現状: `/c/tmp/tdnet_orders/` / `C:/gdrive/claude/work/` への書き込みコマンドがあるが、これらディレクトリの事前作成手順が無い。Phase 1 で既に作成済みのため現在は問題ないが、別端末・別環境で本スキルが初回起動された場合 `gsutil cp: No such file or directory` で `failed_no_gcs_files` 扱いになる可能性。
- 提案: Step 4 冒頭に「初回起動時のみ `mkdir -p /c/tmp/tdnet_orders/` `mkdir -p /c/gdrive/claude/work/` を実行」（CLAUDE.md §6 で `mkdir -p` は Windows 環境では `New-Item -ItemType Directory -Force` に置換、Bash なら `-p` 不可とあるので、ガイダンス文を Bash/PowerShell の両方で書く）。または「ディレクトリ事前存在を呼び出し元責任とする」と明記。

### #5 ラッパー側に冒頭バナー出力規約の記載なし

- 箇所: `.claude/commands/tdnet-orders-extract.md:1-5`
- 現状: CLAUDE.md §8「実行時冒頭に `🎯 [<スキル名>] <タスク概要>` を出力。」とあるが、本ラッパーには記載なし。他のラッパー（classify-tob.md / batch-owner-judge.md 等）にも明示されていないため横展開不足ではあるが、新規作成のタイミングで入れるべき。
- 提案: ラッパーに「冒頭で `🎯 [tdnet-orders-extract] ticker={ticker} を処理開始` を出力すること」を 1 行追加。**記載先**: CLAUDE.md §8 が正本なので原則は CLAUDE.md 既存ルールに従えば足りる。本スキル固有の追加は不要だが、ラッパー側に「（CLAUDE.md §8 のバナー規約を遵守）」と 1 行入れると忘れにくくなる。

## 【確認できなかった事項】

- `.claude/commands/*.md` で引数（slash command の `$ARGUMENTS`）がどう Agent に渡るかの harness 実装は本レビューでは確認できない。他のラッパー（judge-owner-character.md 等）も引数規約を書いていないので、harness の慣習として「ラッパー本文 + ユーザー追記文がそのまま Agent prompt になる」可能性が高いが、未確認。Phase 2 の本格運用前に呼び出し元側の 1 件で実 dry-run して、ticker が正しく Agent に渡るかを実測する必要がある。
- `C:/gdrive/claude/work/_failed.csv` のフォーマット（列順・エンコーディング・改行コード）は本スキルが直接書かないが、呼び出し元が STATUS を見て追記する際の規約がスキル正本にも MD にも無い。`_failed.csv` の契約は呼び出し元責任とはいえ、本スキルの STATUS と reason マッピング表（例: `failed_no_bq_records → reason="no_bq_records"`）を 1 ブロック書いておくと呼び出し元実装が楽になる。提出 MD 観点 2 (STATUS から reason が一意に決まるか) に対する回答として、現状の STATUS 名がそのまま reason として使える設計（命名で一意）にはなっているので、その明示が望ましい。
- Phase 1 / Phase 2 序盤の 90 社が「単一セッション連続実行」で処理済とのことだが、その実績で発火した STATUS の分布（`completed` / `completed_partial` / `failed_*` の比率）が本スキル正本には反映されていない。実績ベースで「`failed_no_data` は 全体の N% 程度発生する想定」と注意事項に書いておくと、Phase 2 で Agent 起動コストの見積もりがしやすい（指摘ではなく観察）。

---

## 返却 2026-05-18

- #1: [採用] note フィールドをキー定義表に追加、ドメイン規約 (セミコロン区切り) 定義、三菱重工例に note=null 追加、completed_partial 時の必須文字列規約追加
- #2: [採用] Step 5 にリトライポリシー (3 回打ち切り) 追加、STATUS 表を DOCS_FOUND/READ/WITH_DATA で排他的再定義、判定優先順位明記
- #3: [採用] ラッパーに引数受渡し規約・呼び出し例・コマンダー責任・戻り値リンクを追記
- 改善#1: [採用] failed_no_data と偽陽性業種の対応関係を Step 5 に明記
- 改善#2: [採用] cum/axis 4 通り組合せを表で明記 (h/r 構造共通も明示)
- 改善#3: [採用] kind 値の表記揺れ規約 (後処理正規化前提) を追加
- 改善#4: [採用] Step 1 に mkdir -p / New-Item の事前作成手順追加
- 改善#5: [採用] CLAUDE.md §8 バナー出力規約 (🎯 [tdnet-orders-extract] ticker={ticker}) を注意事項とラッパーに追記

命名: 本スキル = 「受注高抽出ソルジャー」 (ユーザー指示 2026-05-18)
後続: 「受注高抽出コマンダー」を別途開発予定

---

**2026-05-18 ファイル名変更（候補C 採用 / ユーザー指示）**:
- `skills/tdnet_orders_extract.md` → `skills/orders_soldier.md`
- `.claude/commands/tdnet-orders-extract.md` → `.claude/commands/orders-soldier.md`
- スラッシュコマンド: `/tdnet-orders-extract` → `/orders-soldier`
