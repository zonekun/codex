# MD AI可読性レビュー: monthly-error-autofix スキル正本（Step 3A 自律修復 重点評価）

- 日時: 2026-05-07 22:57 JST
- 対象: `skills/monthly-error-autofix.md`
- パターン: 1（まっさらレビュー）
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/105_mr_monthly_error_autofix_skill.md`
- 重点観点: Step 3A（extract adapter自律修復）がAIエージェントに自律的（ユーザー指示なし）で実行させるのに十分な記述か
- 前回レビュー: `docs/reviews/081_mr_monthly_error_autofix_skill_plan.md`（プランMDレビュー、5件の重大指摘）

---

## 【サマリー】

月次開示パイプラインのエラーを自律修正するAgentスキルの正本。前回レビュー（081）はプランMDを対象としたが、今回はスキル正本（実装後）をレビュー。081の5件の重大指摘のうち、#4（Gemini API許可明示）と#5（時間目標と品質の優先順位）は対処済み。#1（Layer遷移判定基準）と#3（分類-戦略キーマッピング）は3層アーキテクチャ自体を廃止してシンプル化したため解消。#2（パターンDB蓄積キー設計）は042-1で構造化済み。

全体として前回プランから大幅にシンプル化され、AI可読性は改善されている。しかし Step 3A の「自律修復」を自律的に実行させる上で、PDF/HTML実物取得の具体的手段と、pdfplumber によるテキスト抽出の実行方法に欠落がある。

- **AI可読性評価: A** -- 全体構造は明瞭。社訓が冒頭にあり、フローは一意に辿れる。Step 3A のシンプルさは好判断
- **誤読リスク評価: B** -- Step 3A の PDF取得・テキスト抽出手段と、Step 5 のローカルパス保存ルールに曖昧さがあり、自律実行時にAIが手段選択で迷う

**主要リスク3件**:
1. Step 3A-2「PDF/HTML実物取得」のGCSダウンロード手段が `gsutil cp` の具体例なし。AIが `gcs_read` MCP / `gsutil cat` / `gsutil cp` のどれを使うか迷う
2. Step 3A-3「pdfplumber テキスト抽出」の実行コードが未記載。AIがpdfplumberの使い方を知っていても、venv のパスやインポート方法でBash組み立てに迷い時間を浪費する
3. Step 5-1「ローカルadapterを正式パスに保存」と Step 3A-4「Edit: adapter JSONを修正」の間で、修正対象ファイルがローカル正式パス（`meta/monthly/{ticker}_extract_adapter.json`）なのか一時ファイルなのか不明

---

## 【Markdown 品質評価】

### Accuracy / 正確性: A

- GCSパス構造（`monthly/meta/{ticker}/extract_adapter.json` 等）は042 §ファイルマッピング絶対表と整合
- Python venvパス `<python>` はCLAUDE.md §実行環境と整合
- `PYTHONUTF8=1` 環境変数はCLAUDE.md §スクリプト実行と整合
- `--no-batch` オプション、`--region us-west1` はCLAUDE.md §Cloud Run Job locationと整合
- Gemini API使用許可セクション（L31-36）がCLAUDE.md §Gemini APIの制約に対する明示的例外として正しく記載：081 #4指摘の対処済み
- 「社訓」で「時間制限なし。品質最優先」と明記：081 #5指摘の対処済み（時間目標自体を削除し品質を絶対優先に変更）
- Step 4のローカル検証コマンド `--tickers <ticker> --no-batch` は実在する引数

### Completeness / 完全性: B

- Step 3A-2のPDF/HTML取得手段が抽象的（「GCS `monthly/docs/{ticker}/` から最新1件を `C:/tmp/` にDL」のみ。具体コマンドなし）
- Step 3A-3の「pdfplumber テキスト抽出 or HTML Read」が具体的実行手段を欠く
- Step 3A-4「Edit: adapter JSONを修正（1社1Edit、コンテキスト蓄積禁止）」で、修正するファイルの場所（ローカルパス）が不明
- Step 5「ローカルadapterを正式パスに保存」で初めてローカル正式パス `meta/monthly/{ticker}_extract_adapter.json` が登場するが、Step 3A-4の時点でこのパスを使うのか別の場所を使うのかが不明
- エラー分類テーブル（Step 2）のE1-E6が個別定義されていない。「E1-E6: extract adapter系 → Step 3A」とだけあり、E1-E6の各症状が不明。042-1パターンDBを見ればE1-E6の定義はあるが、Step 0でE系はパターンDB事前ロード不要と明記されているため、AIがE系エラーの分類基準を知らないまま作業に入る

### Relevance / 関連性: A

- 3層アーキテクチャを廃止し、extract系は「現物を見て直す」に一本化。情報のノイズが大幅に減少
- 「やらないこと」セクション（L113-118）が明確で、AIが不要な探索に入ることを防止
- DL系（Step 3B）とExtract系（Step 3A）の分離が明瞭

### Actionability / 実行可能性: B+

- メインフロー（Step 0-7）は順序が一意で辿りやすい
- 修復サイクル（Step 3A）の6ステップは概念的には明確
- しかし、Step 3A-2, 3A-3 の実行手段の欠落により、AIは「何をすればよいか」は分かるが「どう実行するか」で迷う場面がある
- Step 7-1「LINE通知: send_ntfy で送信」は `--sender ATP --task` 指定の記載なし（CLAUDE.md §ntfy送信ルール参照）

---

## 【AI 誤読リスク】

### Risk-1: Step 3A-2 PDF取得手段の未指定

**箇所**: `skills/monthly-error-autofix.md:99`
> GCS `monthly/docs/{ticker}/` から最新1件を `C:/tmp/` にDL

**問題**: 「DL」とのみ記載。具体的手段（`gsutil cp`）のコマンド例がない。特にGCS上のファイル一覧から「最新1件」を特定するにはまず `gsutil ls` が必要だが、その手順もない。

**AIの誤読パターン**: GCP MCPサーバーの `gcs_list` + `gcs_read` を使おうとする（PDFバイナリには不適切）、またはPDFファイル名のソート方法が不明で誤ったファイルを取得する

**トリガー**: Step 3A修復サイクルの毎回

**影響**: 時間浪費（手段の試行錯誤）、誤ったPDFの取得による誤判断

### Risk-2: Step 3A-3 pdfplumberテキスト抽出の実行方法欠落

**箇所**: `skills/monthly-error-autofix.md:100`
> pdfplumber テキスト抽出 or HTML Read で構造を把握

**問題**: pdfplumberによるテキスト抽出をどのように実行するかが未記載。Agent型サブエージェントはスクリプトファイル作成を嫌うため、ワンライナーPythonで実行するのが自然だが、そのコード例がない。

**AIの誤読パターン**: (a) pdfplumberのインポートでエラー（venvの活性化忘れ）、(b) 抽出したテキストの確認方法（print全文？先頭N行？）で迷う、(c) HTMLの場合に「HTML Read」が `Read` ツールでの直接閲読を意味するのか、BeautifulSoup等でのパースを意味するのか不明

**トリガー**: PDF/HTML実物確認が必要な全ての修復（ほぼ毎回）

**影響**: 抽出方法の試行錯誤で時間浪費

### Risk-3: 修正対象ファイルのパスが曖昧

**箇所**: `skills/monthly-error-autofix.md:101` (Step 3A-4) vs `skills/monthly-error-autofix.md:156` (Step 5-1)

**問題**: Step 3A-4は「Edit: adapter JSONを修正」とだけあり、どのパスのファイルを修正するか明記されていない。Step 5-1で「ローカルadapterを正式パスに保存: `meta/monthly/{ticker}_extract_adapter.json`」とあるが、これは Step 3A-4 の修正先とは別の場所からコピーするのか、同一ファイルなのかが不明。

**AIの誤読パターン**: (a) GCSから`gsutil cat`で取得した内容を`C:/tmp/`に保存→編集→`meta/monthly/`にコピー→GCSアップロードの3段階パターン、(b) 直接`meta/monthly/{ticker}_extract_adapter.json`を編集→GCSアップロードの2段階パターン。どちらが意図されているか判断不能

**トリガー**: 毎回の修復サイクル

**影響**: パス間違いによるadapter破損、または正式パスへの反映漏れ

---

## 【MD 構成リスク】

### Struct-1: E1-E6の個別定義の欠落

**箇所**: `skills/monthly-error-autofix.md:85-87`

**問題**: DL系エラー（D1-D4）は各行に症状と対応方針が記載されているが、Extract系（E1-E6）は「extract adapter系 → Step 3A」の1行のみ。Step 0で042-1パターンDBは「DL系エラー（D1-D4）のみ」ロードと明記されており、Extract系のエラーコードの意味がスキルMD内に存在しない。

AIはStep 2でエラーをE1-E6に分類しようとするが、E1-E6の定義がないため分類できない。実質的にはE系は全て「Step 3Aへ」という意味だが、それならば分類テーブルは「E1-E6」ではなく「Extract系全般」と書くべき。

### Struct-2: Step 3A「判断の指針」の位置と拘束力

**箇所**: `skills/monthly-error-autofix.md:105-111`

**問題**: 「判断の指針（頭の中で使う。探索しない）」が修復サイクルの**後**に配置されている。修復サイクル内のStep 3で「042のスキーマ定義に照らしてadapterを修正」と指示しているが、スキーマ定義以外の判断基準（regex vs gemini の選択基準、`bc_ignore` の使用条件等）はこの「判断の指針」セクションに書かれている。AIが修復サイクルの手順を逐次実行していると、「判断の指針」に到達する前にStep 3の判断を行ってしまう可能性がある。

---

## 【指示優先順位・文脈境界】

### Priority-1: 042スキーマ定義との責務分担

Step 3A-3で「042のスキーマ定義（fields, extraction_method 等）に照らしてadapterを修正」とあるが、本スキルMD内の「判断の指針」セクションにもadapter修正の判断基準が記載されている。042のスキーマ定義が正本（Single Source of Truth）であることは042に明記されているが、本スキルMDの「判断の指針」がスキーマ定義を**補完**するものなのか**一部抜粋**なのかが不明。整合性は現時点では問題ないが、042側が更新された場合に乖離するリスクがある。

### Priority-2: CLAUDE.md §ntfy送信ルールとの整合

Step 7-1「LINE通知: 結果サマリーを `send_ntfy` で送信」は `--sender ATP --task "<作業名>"` の指定がない。CLAUDE.md §用語・解釈ルールで「`--sender ATP --task` は全てのnotify.py ntfy呼び出しに付ける」と明記されており、本スキルMDでの記載漏れはAIが引数なしで `send_ntfy` を実行するリスクを生む。

### Priority-3: CLAUDE.md §AI手動反復処理の逐次永続化義務との関係

Step 3A-4に「1社1Edit、コンテキスト蓄積禁止」とあり、CLAUDE.md §逐次永続化義務（2026-05-07追加）の「1件1永続化」と整合する。ただし、10件処理ごとのgit commitの記載がスキルMDに無い。修正上限10社/回（ガードレール#2）に照らすと最大10社を処理する可能性があり、中間コミットが必要になる場面がある。

---

## 【重大な指摘】（即修正）

### #1 Step 3A修復サイクルのPDF取得・テキスト抽出に具体的な実行コマンドがない

**箇所**: `skills/monthly-error-autofix.md:99-100`
**問題**: Step 3A-2「PDF/HTML実物取得」とStep 3A-3「pdfplumber テキスト抽出 or HTML Read」に具体的な実行コマンド・コード例がない。Step 4のローカル検証にはコマンド例があるのに、Step 3A-2/3A-3にはない
**AIの誤読パターン**: 手段の試行錯誤（gsutil cat vs gsutil cp vs gcs_read MCP、pdfplumber ワンライナーの組み立て）で時間浪費。特にAgent型サブエージェントは試行錯誤のコスト（コンテキスト消費）が高い
**トリガー**: 毎回のextract adapter修復
**影響**: 修復1社あたり5-10分の無駄。10社なら50-100分の浪費
**根拠**: Step 4（ローカル検証）にはコマンド例が明記されており、そこではAIは迷わない。Step 3A-2/3A-3だけコマンド例がないのは非対称
**推奨対応**: Step 3A-2にGCSファイル一覧取得+DLのコマンド例、Step 3A-3にpdfplumberワンライナーのコード例を追加:
```bash
# Step 3A-2: 最新PDF取得
gsutil ls gs://stock_data_1930932/monthly/docs/{ticker}/ | tail -1
gsutil cp gs://stock_data_1930932/monthly/docs/{ticker}/{filename} C:/tmp/

# Step 3A-3: テキスト抽出
PYTHONUTF8=1 <python> -c "
import pdfplumber
with pdfplumber.open('C:/tmp/{filename}') as pdf:
    for page in pdf.pages:
        print(page.extract_text())
"
```
**MD修正だけで足りるか**: 足りる

### #2 修正対象ファイルのパスフロー（Step 3A-4 → Step 5-1）が不明

**箇所**: `skills/monthly-error-autofix.md:101` と `skills/monthly-error-autofix.md:156`
**問題**: Step 3A-1で `gsutil cat` or ローカル Read で現行adapterを取得するが、取得先（ファイルとして保存するか、画面表示のみか）が不明。Step 3A-4でEditするファイルのパスが不明。Step 5-1で「ローカルadapterを正式パスに保存」とあるが、「保存」の意味がStep 3A-4の編集先から正式パスへの移動なのか、Step 3A-4時点で既に正式パスにあるのかが判別不能
**AIの誤読パターン**: 2つの解釈が可能: (a) GCSからC:/tmp/にDL→C:/tmp/で編集→meta/monthly/にコピー→GCSアップロード、(b) 既存ローカルファイル(meta/monthly/)を直接編集→GCSアップロード。(a)の場合Step 5-1は「C:/tmp/ → meta/monthly/」のコピー、(b)の場合Step 5-1は不要な手順
**トリガー**: 毎回の修復サイクル
**影響**: (a)を選択した場合、C:/tmp/のファイル残置リスク。(b)を選択した場合、ローカルにファイルが存在しない初回修正で失敗
**根拠**: 042 §不変ルール4「正本はGCSとローカルを同時に反映。片方だけ更新して『後で同期』は禁止」
**推奨対応**: 修復サイクルのパスフローを明示化:
  1. 現行adapter取得: `gsutil cat ... > C:/tmp/{ticker}_extract_adapter.json`（一時保存）
  2. C:/tmp/ のファイルをEdit
  3. ローカル検証（Step 4）
  4. OK後: `cp C:/tmp/... meta/monthly/{ticker}_extract_adapter.json` + `gsutil cp meta/monthly/... gs://...`
**MD修正だけで足りるか**: 足りる

### #3 E1-E6の分類定義がスキルMD内に不在

**箇所**: `skills/monthly-error-autofix.md:85-87`
**問題**: Step 2でエラーをD1-D4/E1-E6に分類するが、E1-E6の個別定義（症状・対応方針）がスキルMD内にない。E系は全て「Step 3A」に流れるため、分類自体は機能するが、AIはStep 2で「これはE何番？」の判断に042-1パターンDBを参照する必要がある。しかしStep 0で「042-1はDL系エラー（D1-D4）のみ事前ロード」と明記されており矛盾する
**AIの誤読パターン**: (a) E系のエラーコードを付けるためにわざわざ042-1をロード（Step 0の指示に反する）、(b) E系の分類を諦めて全て「E?」として処理（分類テーブルの意図が不明に）
**トリガー**: extract系エラーがある場合（頻出）
**影響**: 042-1の不要ロードによるトークン浪費、またはStep 7報告時にエラーコードが曖昧
**根拠**: DL系はD1-D4各行に症状と対応方針が記載されているのに対し、E系は1行にまとめられている
**推奨対応**: 2つの選択肢のいずれかを取る:
  - (A) E系も各行に1行要約を記載（例: E1: regex不一致、E2: 年月検出失敗、E3: regex限界→Gemini、E4: Gemini応答異常、E5: 空文字キー、E6: format不一致）。ただしStep 3Aへの流れは変わらない旨を明記
  - (B) 分類テーブルのE系を1行に統合し「Extract系全般 → Step 3A（個別分類不要）」と明記。E1-E6のコードは報告時の分類用であり、修復フローの分岐には使わないことを明示
**MD修正だけで足りるか**: 足りる

### #4 Step 7 LINE通知の --sender --task 引数が未記載

**箇所**: `skills/monthly-error-autofix.md:176`
**問題**: 「LINE通知: 結果サマリーを `send_ntfy` で送信」とあるが、CLAUDE.md §用語・解釈ルールで必須とされている `--sender ATP --task "<作業名>"` の記載がない
**AIの誤読パターン**: `send_ntfy` を引数なしで実行し、通知タイトルが「投資エージェント」に戻る
**トリガー**: 修復完了時の毎回の報告
**影響**: ユーザーが通知元を識別できない（軽微だが、レビュー095で既に事故として記録済み）
**根拠**: CLAUDE.md L14-15: 「`--sender ATP --task` は全てのnotify.py ntfy呼び出しに付ける」、レビュー096
**推奨対応**: Step 7-1を以下に修正:
```
1. **LINE通知**: 結果サマリーを `send_ntfy --sender ATP --task "月次エラー修復"` で送信
```
**MD修正だけで足りるか**: 足りる

---

## 【改善提案】（中優先度）

### #1 「判断の指針」セクションの配置改善

**箇所**: `skills/monthly-error-autofix.md:105-118`
**現状**: 「判断の指針」と「やらないこと」が修復サイクル（L97-103）の**後**に配置されている
**提案**: 修復サイクルの**前**（L96とL97の間）に「判断の指針」を移動するか、修復サイクルのStep 3内に「判断の指針を参照」のポインタを入れる。AIは手順を上から順に実行するため、Step 3の判断時に下のセクションを参照する保証がない
**期待効果**: 判断基準が修復判断の時点で確実にロードされる

### #2 Step 3A-1 の adapter 取得方法を統一

**箇所**: `skills/monthly-error-autofix.md:98`
**現状**: `gsutil cat or ローカル Read で現行 extract_adapter.json を取得`
**提案**: 「GCSからgsutil catで取得し、C:/tmp/{ticker}_extract_adapter.json に保存」に統一。「or ローカル Read」はローカルにファイルが存在する前提だが、Agent型サブエージェントの初回起動時にローカルにadapterが存在するとは限らない。GCS一本に統一する方が確実
**期待効果**: 手段選択の迷いがなくなる

### #3 ガードレール#5 adapter backupの具体手順

**箇所**: `skills/monthly-error-autofix.md:188`
**現状**: `adapter backup: 修正前に旧adapter内容をログに記録（gsutil catで表示）`
**提案**: 「ログに記録」が画面表示のみを意味するのか、ファイルに保存するのかを明記。Agent型サブエージェントの場合、画面表示はコンテキストに残るがセッション終了後は消失する。`gsutil cat ... > C:/tmp/{ticker}_extract_adapter_backup.json` のように一時ファイルとして保存するか、git管理下のファイルを修正前にcommitすることでバックアップとするかを明示すべき
**期待効果**: バックアップの確実性向上

### #4 Step 5 の複数ticker一括実行に関するガード

**箇所**: `skills/monthly-error-autofix.md:158`
**現状**: `Cloud Run Job再実行: gcloud run jobs execute ... --args="--tickers,<全修正ticker>" --async`
**提案**: 全修正tickerを一括実行する前に、ローカル検証が全社分完了していることの確認チェックを明記。現状はStep 4→Step 5が1社分の流れに見えるが、Step 5のコマンドは「全修正ticker」を一括投入している。修復サイクルは「1社分」と明記されているため、全社の修復完了後にまとめてStep 5を実行するのか、1社ごとにStep 5を実行するのかが曖昧
**期待効果**: ローカル検証未了の社が本番投入されるリスクの排除

### #5 中間git commitのトリガー明記

**箇所**: `skills/monthly-error-autofix.md:178` (Step 7-3)
**現状**: Step 7でまとめてgit commitする構成
**提案**: CLAUDE.md §逐次永続化義務（2026-05-07追加）に準拠し、「修正上限10社の場合、5社処理ごとに中間commitを実施」等のトリガーを明記。MR-103の事故教訓として追加されたCLAUDE.mdルールとの整合を取る
**期待効果**: クラッシュ時の作業消失防止

---

## 【ソースコード・仕組み側への波及】

本レビューはスキルMD（指示文書）のAI可読性評価であり、ソースコード修正は直接提案しない。ただし以下の点を記録する:

- Step 3A-2でGCSからPDFを取得するヘルパースクリプト（`scripts/fetch_monthly_pdf.py --ticker <t> --latest`等）が存在すれば、スキルMD内のコマンド例を簡潔化でき、AIの手段選択の迷いを根本的に排除できる。現時点ではスクリプト化は不要（頻度が低い）だが、修復対象が20社以上に常態化した場合は検討の価値がある

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 全推奨はスキルMD（`skills/monthly-error-autofix.md`）の修正に留まる: OK
- adapter スキーマ定義の正本は042であり、本レビューの推奨はスキルMD側の参照手段を改善するものであって042の内容を複製しない: OK
- 推奨#4（--sender --task）はCLAUDE.md既存ルールの遵守であり、CLAUDE.md側の変更は不要: OK

### 8b. 上位ルール整合性チェック

- 推奨#1のコマンド例追加はCLAUDE.md §実行環境（Bashフォワードスラッシュ、PYTHONUTF8=1）と整合
- 推奨#2のパスフロー明示は042 §不変ルール4「正本はGCSとローカルを同時に反映」と整合
- 推奨#4はCLAUDE.md §ntfy送信ルールそのもの
- Q1: Step 3Aの「PDFテキスト抽出コマンド例」は月次エラー修復固有のため、CLAUDE.md汎用化は不要
- Q2: 修正対象ファイルのパスフロー（一時ファイル→正式パス→GCS）パターンは他のadapter修正タスク（手動修正、Codex委譲結果反映等）でも再利用されるが、042に既に不変ルールとして記載済みのため追加不要
- Q3: 中間commitトリガー（改善提案#5）はCLAUDE.md §逐次永続化義務で既にプロジェクト汎用ルール化済み。スキルMDはそのルールへのポインタを追加すればよい

### 8c. 副作用シミュレーション

**(i) 単体副作用**:
- 推奨#1のコマンド例追加により、AIがコマンドを盲目的にコピペして`{ticker}`や`{filename}`を置換し忘れるリスク → プレースホルダが `<>` ではなく `{}` で書かれている場合、JSONのキーと混同する可能性がある。推奨文案では `{ticker}` を使っているが、Step 4のローカル検証コマンドは `<ticker>` を使っている。**表記統一が必要**（`<ticker>` に統一推奨）

**(ii) クロスルール競合**:
- ガードレール#5「adapter backup: gsutil catで表示」と推奨#2のパスフロー「gsutil cat > C:/tmp/」は矛盾しない（表示も保存も可能）
- ガードレール#7「コード変更禁止」と推奨のソースコード波及セクションは矛盾しない（ヘルパースクリプトの提案は将来検討であり即時実行ではない）

**(iii) 状態依存シナリオ**:
- Agent型サブエージェントのためLINE会話モード等のメイン状態に依存しない: OK

**(iv) 再発防止策の実効性**:
- 推奨#1-#4は全て構造的対処（コマンド例追加、パスフロー明示、定義追加、引数明記）であり、意志依存型ではない: OK

### 8d. 事後確認事項

- 推奨#1のコマンド例追加後、Agent型で初回実行した際にPDF取得・テキスト抽出がスムーズに進むか確認
- 推奨#2のパスフロー実施後、042 §不変ルール4（GCSとローカル同時反映）が遵守されているか確認
- E系分類（推奨#3）の対応後、Step 7報告のエラーコードが適切に付与されているか確認

---

## 【確認できなかった事項】

1. `extract_monthly_data.py` の `--no-batch` オプションがGemini APIを実際に呼び出すかどうか（`extraction_method: "gemini"` のadapterの場合）。コード閲読なしのため未確認
2. GCS `monthly/docs/{ticker}/` 内のPDFファイル命名規則。`gsutil ls | tail -1` で「最新」が正しく取れるかはファイル名のソート順に依存する
3. `pdfplumber` がvenv `C:/venvs/investment-agent/` にインストール済みかどうか
4. スキルの設計内容の妥当性（3層→フラット化の判断等）は code-reviewer パターン4の責務。本レビューはAI可読性のみ評価
