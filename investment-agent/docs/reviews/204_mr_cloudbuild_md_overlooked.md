# レビュー依頼: 既存MDの記載を読まず同じエラーを5回繰り返した事故

- 日時: 2026-05-18 JST
- 提出者: メインエージェント（Claude Opus）
- 対象スキル: md-reviewer
- レビューパターン: **2（事故/教訓ログ系）** または **4（不備指摘＋再発防止策）**
- 種別: メインエージェント自己事故報告

## 対象MD

- `docs/knowledges/tools/005_cloudrun_job_deploy.md` §⑥（スクリプト変更時の更新）— **記載は適切。読まなかったメインエージェントの過失**
- `CLAUDE.md` §高頻度参照テーブル — Cloud Run Job deploy 行が `005_cloudrun_job_deploy.md` を指している
- `CLAUDE.md` §6 「Cloud Build: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止」— 抽象ルールはあるが「事故再発防止のための必読フロー」とまで明文化されていない

## 事象（時系列）

セッション内タスク: TDnet ENHANCED テーブルに `CHUNK_INDEX` 列追加 + 3 Cloud Run Job への deploy。

1. プラン `tools-013_tdnet_chunk_index_column_20260517_223000.md` を策定（CR-203 反映済み）
2. BQ ALTER TABLE ADD COLUMN を実行・成功
3. **Cloud Build deploy 実行**: Google Drive 配下（`G:\マイドライブ\...` 経由 → 内部的に同一 NTFS）の作業ディレクトリから
   ```bash
   cd "G:/マイドライブ/claude/investment-agent" && \
   gcloud builds submit --config cloudbuild/cloudbuild.tdnet-load-daily.yaml \
     --gcs-source-staging-dir gs://gmailpj-357912-cloudbuild/source .
   ```
   → `[WinError 32] プロセスはファイルにアクセスできません: file.tgz` でエラー
4. **同じエラーを 5 回連続でリトライ**（試行1〜5）。途中で `--project` フラグ追加・ジャンクションパス `C:\gdrive\...` 経由切替も試したが、Temp 配下の `file.tgz` ロックは全試行で再現
5. ユーザー指摘: 「これは一時的に C:\tmp でビルドってどこかのMDに書いてなかったか」
6. `docs/knowledges/tools/005_cloudrun_job_deploy.md` を grep → **§⑥ に「一時ビルドディレクトリ方式（`/c/tmp/cloudbuild-*`）」が明記されていた**ことを発見
   ```bash
   BUILD_DIR=$(mktemp -d /c/tmp/cloudbuild-XXXXXX)
   ... 必要ファイルのみコピー ...
   gcloud builds submit "$BUILD_DIR" --config ...
   ```
   過去事故 MR-171（3216ファイル/20.5MiB が Google Drive からアップロードされ遅延・失敗）への明示的な対策として既に文書化済み

## 根本原因

メインエージェントの **「索引ファースト → 該当知見MDを Read」フローを Cloud Build deploy 作業の前に踏まなかった** ことが直接原因。

- CLAUDE.md §高頻度参照テーブル: 「Cloud Run Jobにデプロイ → `docs/knowledges/tools/005_cloudrun_job_deploy.md`」と明記
- CLAUDE.md 冒頭ターン hook で毎ターン「[索引ファースト] ファイル探索・データ確認・回答検討する際は最初にCLAUDE.md §高頻度参照テーブルを照合せよ」と通知されている
- それでも `cd <project> && gcloud builds submit .` を CLAUDE.md §6 の抽象ルール（「ドキュメントからコマンドをコピー」）だけで実行し、§⑥ の「Windows + Google Drive 上で `gcloud builds submit` するとファイルロック」具体パターンを参照しなかった

なお、同セッション内で発生していた `.venv` 関連 hook エラー（毎ツール呼び出しで blocking error が表示される）が**ノイズ**になり、本物のエラーメッセージへの注意配分を奪っていた可能性も併記しておく（言い訳ではなく観測事実として）。

## 観点（md-reviewer に評価してほしい点）

1. **記載 MD 側の改善余地はあるか**（記載済みだが目立たない・誘導が弱い等）
   - §⑥ の小見出しが「スクリプト変更時の更新」で、ビルドコンテキスト最適化が主題ではない。Windows ファイルロック対策としては `gcloud crashed (PermissionError) WinError 32 file.tgz` という具体エラー文字列が記載されていない → grep ヒットしない構造
   - §⑥ §④ §② のどこに最初に飛ぶべきかが MD 内で順序付けされていない
   - 過去事故 MR-171（Google Drive 経由の遅延）と本件（ファイルロック）が **症状違いだが対策同一**であることを明示する 1 行があれば、症状ファーストでも到達できる
2. **CLAUDE.md §10 索引テーブルから 005 への誘導の十分性**
   - 「Cloud Run Jobにデプロイ」エントリは既存。ただ「**スクリプト変更後の再 deploy**」「**Windows / Google Drive 環境でのビルド**」というシナリオ別の誘導は無く、新規 deploy と同じ MD に丸投げ
3. **事故再発防止策の提案**
   - 005_cloudrun_job_deploy.md §⑥ の冒頭に **「Windows + Google Drive 環境では `gcloud builds submit .` 直接実行禁止。`/c/tmp/` 上の一時ディレクトリから実行（理由: MR-171 + ファイルロック事故）」** を 1 行で明示するのが最小コスト改善
   - もしくは CLAUDE.md §6 の Cloud Build 行に「**Windows では一時ビルドディレクトリ必須**（詳細: `005_cloudrun_job_deploy.md §⑥`）」を追記
4. **メインエージェント側の再発防止（feedback または CLAUDE.md 反映）**
   - 「`gcloud builds submit` を打つ前に 005 §⑥ を必ず Read」というメインエージェント行動則を追加すべきか
   - すでに「索引ファースト」フィードバックは memory に存在するが、本件のように「索引テーブルにエントリがあるのに踏まない」失敗を防ぐより強い仕組み（例: 特定コマンド実行前の自動 Read を hook 化）

## 補足

- Cloud Build 自体はこのレビュー依頼と並行で `/c/tmp/cloudbuild-u3GD4j/` 経由で再投入中（BG task `blyjgq38w`）。本レビューは事故記録と再発防止が目的で、deploy 自体の進行は別途継続
- 同種パターン（CLAUDE.md 索引→該当MD Read を飛ばす）が他の作業でも発生していないか、過去レビュー履歴 / `004-1_code_review_findings_log.md` を含めて傾向確認を希望

## 期待するアウトプット

1. 上記観点に沿った評価（記載 MD 改善 / 索引誘導 / 再発防止）
2. 005_cloudrun_job_deploy.md §⑥ への追記提案（あれば具体文）
3. CLAUDE.md §6 への追記提案（あれば具体文）
4. メインエージェント向け feedback memory 追加の要否判定

---

# MD AI可読性レビュー: Cloud Build `[WinError 32] file.tgz` 既存MD見落とし事故

- 日時: 2026-05-18 JST
- 対象MD:
  - `G:\マイドライブ\claude\investment-agent\docs\knowledges\tools\005_cloudrun_job_deploy.md`（特に §⑥ L190-L222 / §⑩ L347）
  - `G:\マイドライブ\claude\investment-agent\CLAUDE.md`（§6 L110-L127 / §10 L164-L201）
- パターン: **4（運用事故 — 自己の行動不備記録）**
- レビュアー: Claude (md-reviewer サブエージェント)
- 出力先: 本ファイル（提出MDへの追記。レビュー正本もここ）
- モード: **MD非原因モード**（Step 3 全軸 A 以上 → §⑥ 本体の記述品質は問題なく、構造的導線・症状到達性の改善が論点）

---

## 【サマリー】

- AI可読性評価: **A**（005 §⑥ は手順・コード例・MR-171 根拠まで揃っており明確）
- 誤読リスク評価: **B**（記述は明確だが「Windows + Google Drive で発生するファイルロック」という症状起点からの到達性が弱い。grep で `WinError 32` / `file.tgz` がヒットしない）
- 重大な指摘: **2件**（症状フレーズ未収録 + §⑩ 罠表に Windows ファイルロック行が無い）
- 根本原因: メインエージェント側の **索引ファースト原則違反**（CLAUDE.md §10 に明示エントリありながら未参照）。MR-153 / MR-160 に続く **3度目の同種事故**

## 【Markdown 品質評価】

| 軸 | ランク | 1行根拠 |
|----|-------|--------|
| Accuracy | A | §⑥ コード例・GCS staging dir・MR-171 根拠は実態と整合 |
| Completeness | A | 「なぜ一時ディレクトリが必要か」（MR-171: Drive 全体アップロード）+ 手順 + フォールバックが揃っている |
| Relevance | A | Job 修正再 deploy 主題と一時ディレクトリ手順の関連は直接的 |
| Actionability | A | BUILD_DIR 作成 → コピー → submit → rm の 1-2-3 ステップが順序明示 |

→ 全軸 A 以上のため **MD非原因モード**。MD表現の改善ではなく **MD群の構造・導線・症状到達性** に絞って分析する。

---

## 【パターン 4: 原因分析】

### 事象

- TDnet ENHANCED の `CHUNK_INDEX` 列追加に伴い 3 Cloud Run Job (`tdnet-load-daily` / `tdnet-ai-prepare` / `tdnet-ai-finalize`) の再 deploy が必要
- メインエージェントは `cd "G:/マイドライブ/.../investment-agent" && gcloud builds submit --config ... .` をプロジェクトルート（Google Drive 配下）から直接実行
- `[WinError 32] プロセスはファイルにアクセスできません: '...\\file.tgz'` で失敗 → **同一エラーで 5 回連続リトライ**（`--project` flag 追加・ジャンクションパス `C:/gdrive/...` 経由切替も無効）
- ユーザー指摘「これは一時的に C:\tmp でビルドってどこかのMDに書いてなかったか」で初めて 005 §⑥ L198-L222 の「一時ビルドディレクトリ方式（`/c/tmp/cloudbuild-*`）」が **既に文書化済み** と発覚

### AIの思考回路（推定）

1. **deploy 着手時の参照**: CLAUDE.md §6 L120「Cloud Build: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止」を参照 → 「コマンドをコピーすればよい」と解釈
2. **コピー元の特定**: §10 L174「Cloud Run Jobにデプロイ → `005_cloudrun_job_deploy.md`」エントリを **読み飛ばし**、過去セッションの記憶から `gcloud builds submit --config ... .` という一般形のみ再構成
3. **エラー発生時の判断**: `[WinError 32] file.tgz` を「一時的なファイルロック」と解釈 → リトライで解消する種類の症状と誤認
4. **5 回連続リトライの正当化**: `--project` フラグ追加・ジャンクションパス切替で「環境差異」を試行 → **既存 MD の確認には戻らなかった**（症状起点で 005 を grep する発想が起動しなかった）
5. **誤った帰着**: 「Windows + Google Drive 環境固有の制約」と漠然と認識しつつ、**それが MD に書かれているかを確認するアクションを取らなかった**

直接の意思決定ポイントは Step 2 の「§10 索引未参照」と Step 4 の「症状起点での MD 再検索を発火させなかった」の 2 箇所。

### 直接原因

- **CLAUDE.md §10 索引テーブル（L164-L201）の Cloud Run Job エントリを deploy 着手前に踏まなかった**（索引ファースト原則違反）
- CLAUDE.md §6 L120 の「該当ドキュメントからコマンドをコピー」抽象ルールのみで、**具体的な参照先 MD を Read せず一般形コマンドを再構成**した

### 根本原因

1. **索引ファースト原則の発火信頼性が低い**: CLAUDE.md §10 L164 冒頭の「タスクを受けたら**まず下記タスクテーブルを確認**」は記述として明確だが、**「セッション中盤の作業切替（BQ ALTER 完了 → deploy 移行）」のような暗黙の局面遷移で発火しない**。MR-153（PSメニュー）/MR-160（BQ TVF）/今回（Cloud Build）は全てセッション内の作業切替直後に索引未参照が発生
2. **症状起点で MD に到達する経路が弱い**: 005 §⑥ には「WinError 32」「file.tgz」「PermissionError」等のエラー文字列が一切無く、**grep でヒットしない**。AI がエラー本文をクエリにして 005 に辿り着けない
3. **§⑩ 罠テーブル（L347）の最後の行に MR-171 由来の「ビルドコンテキスト肥大化」は登録されているが、症状が「速度低下」記述のみで「Windows ファイルロック (WinError 32)」が併記されていない**。同根対策（一時ディレクトリ方式）だが症状違いの 2 つを 1 行に統合できていない
4. **ノイズ要因**: 同セッション内で `.venv` 関連 hook エラーが毎ツール呼び出しで blocking 表示され、注意配分を奪った可能性（観測事実として記録。本レビューのスコープ外で別途要対処）

### MD上の原因

**MDの記述は明確。問題は行動側にある。** ただし以下の **構造的・導線上の改善余地** が残る:

- 005 §⑥ 冒頭に「Windows + Google Drive 直接 deploy 禁止」の **症状ファースト警告** が無い
- 005 §⑩ 罠表 L347 が MR-171（速度遅延）のみで、本件（WinError 32 ファイルロック）の **症状フレーズ** を含まない
- CLAUDE.md §6 L120「Cloud Build」行が抽象ルールのみで、Windows 特有制約への注記が欠如

これらは「MD非原因」の範疇に収まる **構造補強提案** として【エスカレーション判定】に記載する。

---

## 【MD群の構造・導線分析】（Step 6 部分実行）

### CLAUDE.md からの到達可能性

- **§10 L174「Cloud Run Jobにデプロイ → 005_cloudrun_job_deploy.md」は存在**。索引ファーストフローを踏めば 1 ホップで到達可能。
- ただし **「スクリプト変更後の再 deploy」「Windows / Google Drive 環境でのビルド」というシナリオ別エントリは無い**。「初回 deploy」と「再 deploy（既存 Job 更新）」が同じ MD に丸投げされている → AI が「初回 deploy 手順は §④ → 自分は再 deploy だから §⑥」と二段推論する必要がある
- §6 L120「Cloud Build: ドキュメントからコマンドをコピー」と §10 L174 が **相互参照していない**。§6 から「具体は §10 から 005 へ」という導線が無く、§6 単独で読むと一般的な指示にしか見えない

### コンテキスト圧縮後の可用性

- CLAUDE.md は冒頭に再ロードされる構造のため §10 索引は常時可用
- ただし索引テーブルは 30 行超で **「索引を引く」という能動的アクションがトリガーされないと素通りする**。本事故は圧縮起因ではなくセッション中盤の作業切替時に発生

### MD間導線の健全性

- 005 → CLAUDE.md への逆参照（「本MDは CLAUDE.md §10 から到達」等）は無し。MD間導線は **CLAUDE.md → 005 の一方向のみ**
- 過去事故ログ（004-1 L190 / L210）から `behavior:index-first-violation` は MR-153 / MR-160 / 今回で **3度目**。同根反復 → **MD 改善だけでは収束しない構造的問題**

---

## 【重大な指摘】

### 指摘1: 005 §⑥ に「Windows + Google Drive 環境での直接 deploy 禁止」の症状ファースト警告が無い

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md` §⑥ 冒頭 L190-L197（現状はルール＋テンプレート更新通知のみ）
- 問題: §⑥ の主題が「スクリプト変更時の更新」であり、「Windows ファイルロック対策としての一時ディレクトリ方式」が **副次的な手段** として L199-L201 のコメント行に埋もれている
- 誤読パターン: AI が「Windows 環境制約による必須手順」ではなく「速度最適化の任意手順」と解釈し、`gcloud builds submit .` 直接実行を選んでしまう
- トリガー条件: Windows 端末で初めて再 deploy する AI が §⑥ を流し読む場面 / `[WinError 32] file.tgz` で詰まった後に §⑥ を grep 検索する場面
- 影響: 本事故そのもの（5 回連続リトライ）/ 将来同様の事故再発（特に新セッション AI）
- 根拠: 本事故タイムライン（試行1〜5）+ MR-171（同根の Drive 経由遅延事故が既に発生）
- 推奨対応（**修正文案あり**）: §⑥ 冒頭に **症状フレーズ込みの 1 行注記** を追加。エラーメッセージで grep ヒットさせる
- MD修正で足りるか: **足りる**（コードガードは不要。Cloud Build 自体は外部ツールで hook 不可能）

### 指摘2: 005 §⑩ 罠テーブル L347 が「ファイルロック (WinError 32)」症状を含まない

- 箇所: `docs/knowledges/tools/005_cloudrun_job_deploy.md` §⑩ L347（最終行「プロジェクトルートから gcloud builds submit するとビルドコンテキスト肥大化」）
- 問題: 現行記述は MR-171（速度遅延）の症状のみで、本件の WinError 32 / file.tgz / PermissionError がカバーされていない
- 誤読パターン: AI がエラーメッセージで §⑩ を grep しても **ヒットしない** → 「罠表に未登録の新規パターン」と誤認し、別原因（プロジェクト設定・gcloud バージョン）を疑い始める
- トリガー条件: `gcloud builds submit` 失敗時に AI が `WinError 32` / `file.tgz` を Grep 検索する場面
- 影響: 本事故と全く同じ「症状起点で MD に辿り着けず堂々巡り」を再生産
- 根拠: 本事故で実際に grep 起動が遅れた + 004-1 L158-L159（MR-171 時にも §⑩ よくある罠への登録漏れが指摘済み）
- 推奨対応（**修正文案あり**）: §⑩ L347 を「速度遅延 + Windows ファイルロック」の **2症状併記** に拡張
- MD修正で足りるか: **足りる**

---

## 【改善提案】（中優先度）

### 提案1: CLAUDE.md §10 索引テーブルに「Cloud Build 失敗 / Windows ファイルロック」シナリオ別行を追加するかは保留

- 現状: §10 L174「Cloud Run Jobにデプロイ → 005」のみ
- 案: 「Cloud Build 失敗（WinError 32 / file.tgz / PermissionError）→ 005 §⑥ + §⑩ 末尾行」を追加
- 期待効果: 症状起点でも索引から 005 §⑥ に到達できる
- **保留理由**: §10 はタスク起点（「〜したい」）で索引化されており、症状起点エントリは設計思想と不整合。指摘1・指摘2 で 005 内に症状フレーズを埋め込めば AI 内蔵 grep で十分到達可能。**§10 への症状エントリ追加は本ケースでは不要**（タグ爆発防止）

### 提案2: CLAUDE.md §6 L120「Cloud Build」行への注記追加

- 現状: 「Cloud Build: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止」
- 案: 末尾に「（Windows + Google Drive 環境では一時ビルドディレクトリ必須 → `005_cloudrun_job_deploy.md §⑥`）」を追記
- 期待効果: §6 の抽象ルールから 005 §⑥ への直接ポインタが張られ、再 deploy 場面で **§10 索引を経由せずとも** 適切な MD に到達できる
- **CLAUDE.md §1 編集ポリシー整合**: 1行追記・既存原則の補強・ポインタ化のため適合（手順・事故番号・適用例の列挙には該当しない）
- 採用判定: **採用推奨**（指摘1・2 の MD 側改善に加え、CLAUDE.md からの導線強化も併用）

### 提案3: メインエージェント向け feedback memory 追加 → **否**

- 既存 memory `feedback_claudemd_index_first.md`（18日前 / Why に MR-153 系事故が記録済み）が **本件にそのまま該当**。新規 memory 追加は重複
- 既存 memory + CLAUDE.md §10 L164 冒頭文（「タスクを受けたら**まず下記タスクテーブルを確認**」）の組み合わせで規範は十分。**規範不足ではなく発火信頼性問題**
- 発火信頼性は memory 追加では解消しない（同種事故が memory 存在下で 3 回発生している）→ 新規 memory は **対症療法**
- 採用判定: **追加せず**。代替策として既存 memory の「Why」に本件（2026-05-17 Cloud Build deploy）を 1 行追記する案もあるが、memory は CLAUDE.md 反映後に削除する設計（CLAUDE.md §4.3）のため、本来は CLAUDE.md / 知見MDに永続化すべき。**指摘1・2・提案2 が真の対策**

### 提案4: 過去 3 度の `behavior:index-first-violation` 同根反復に対する構造的対策

- MR-153（PSメニュー）/ MR-160（BQ TVF）/ 今回（Cloud Build）で **作業切替時の索引未参照** が共通パターン
- MD 改善だけでは収束していない（既に memory・CLAUDE.md §10 冒頭文・索引ファースト原則は存在）
- 構造的対策候補（**[方向性]** — 具体実装は本レビュースコープ外）:
  - hook 化: 特定コマンド（`gcloud builds submit` / `gcloud run jobs` / 既存ツール起動）実行前に該当知見MDの存在チェック → 自動 Read 強制
  - PreToolUse hook で Bash コマンドに `gcloud builds submit` を検出したら警告
- 採用判定: **本レビューでは方向性のみ提示**。具体実装は別タスクで `/code-reviewer` または ad-hoc 改修計画に委ねる

---

## 【ソースコード・仕組み側への波及】

- **MD修正で本件再発は概ね防げる**（指摘1・2 で症状フレーズが埋め込まれれば grep 到達可能）
- ただし MR-153 / MR-160 / 今回の **同根 3 回反復** は MD 改善で完全収束していない → 構造的強制（PreToolUse hook 等）が本格対策
- 本件単独では hook 不要。**3 回目という事実を 004-1 に記録し傾向監視を継続**（hook 化判断は 4 回目発生時の累積判断に委ねる）

---

## 【修正文案】

### 文案1: 005_cloudrun_job_deploy.md §⑥ 冒頭への注記追加

**before** (L190-L197 周辺):
```markdown
## ⑥ スクリプト変更時の更新

> **ルール**: スクリプト修正 → 再ビルド → `jobs update --image` の3ステップが1セット。`jobs update` を忘れると古いイメージが定時実行され続ける（`:latest` タグはdigestで固定されるため自動更新されない）。
```

**after**:
```markdown
## ⑥ スクリプト変更時の更新

> **⚠️ Windows + Google Drive 必須ルール**: プロジェクトルート（`G:\マイドライブ\...` / `C:\gdrive\...`）から `gcloud builds submit .` を直接実行禁止。**`[WinError 32] file.tgz`（PermissionError / プロセスはファイルにアクセスできません）でリトライ無限ループに陥る**。下記「一時ビルドディレクトリ方式」（`/c/tmp/cloudbuild-*`）で必ず実行すること。理由: Google Drive 同期プロセスが staging tar 作成中の `file.tgz` をロックする + MR-171（Drive 全体 3216ファイル/20.5MiB がアップロードされ遅延）。
>
> **ルール**: スクリプト修正 → 再ビルド → `jobs update --image` の3ステップが1セット。`jobs update` を忘れると古いイメージが定時実行され続ける（`:latest` タグはdigestで固定されるため自動更新されない）。
```

**[検証済み]**: 症状フレーズ（`WinError 32` / `file.tgz` / `PermissionError`）を 1 行に集約 → AI が次回エラー本文で grep して即座にヒットする。MR-171 への参照も保持。

### 文案2: 005_cloudrun_job_deploy.md §⑩ よくある罠 L347 の拡張

**before** (L347):
```markdown
| **プロジェクトルートから `gcloud builds submit` するとビルドコンテキスト肥大化** | Dockerfile は1ファイルしか COPY しないのに Google Drive 上のプロジェクト全体 3216ファイル/20.5MiB がアップロードされた（MR-171） | §⑥ の一時ビルドディレクトリ方式を使う。ローカルSSD上に必要ファイルだけコピーしてビルド。Google Drive の遅延も回避できる |
```

**after**:
```markdown
| **プロジェクトルートから `gcloud builds submit` するとビルドコンテキスト肥大化 + Windows ファイルロック (`WinError 32` / `file.tgz`)** | (1) Dockerfile は1ファイルしか COPY しないのに Google Drive 上のプロジェクト全体 3216ファイル/20.5MiB がアップロードされ遅延（MR-171）。(2) Google Drive 同期プロセスが staging tar（`file.tgz`）をロックし `PermissionError` でリトライ無限ループ（MR-204: 2026-05-17 / 5回連続失敗事故） | §⑥ の一時ビルドディレクトリ方式（`/c/tmp/cloudbuild-*`）を使う。ローカルSSD上に必要ファイルだけコピーしてビルド。Google Drive の遅延もロックも回避できる |
```

**[検証済み]**: 1行に 2 症状（速度遅延 / ファイルロック）+ 2 事故番号（MR-171 / MR-204）を併記。grep ヒット性確保。

### 文案3: CLAUDE.md §6 L120 への注記追加

**before** (L120):
```markdown
- **Cloud Build**: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止
```

**after**:
```markdown
- **Cloud Build**: `docs/knowledges/` 内の該当ドキュメントからコマンドをコピー。手打ち禁止。Windows + Google Drive 環境では一時ビルドディレクトリ必須（`005_cloudrun_job_deploy.md §⑥` 参照）
```

**[検証済み]**: CLAUDE.md §1 編集ポリシー（原則1-3行 + ポインタのみ）に整合。1行内で完結し、手順・事故番号・適用例の列挙には該当しない。詳細は 005 §⑥ に委譲。

---

## 【推奨検証（Step 8）】

### 8a. 正本帰属チェック

- 文案1・2: 正本は 005_cloudrun_job_deploy.md（Cloud Run Job deploy 手順の Single Source of Truth）→ 正本に書く形で整合
- 文案3: 正本は CLAUDE.md §6 だが詳細は 005 に委譲（ポインタ化）→ 正本帰属違反なし
- **memory への詳細書き込みは推奨に含まれていない**（提案3で memory 追加を否定済み）→ CLAUDE.md §4.3 違反なし

### 8b. 上位ルール整合性チェック

- **CLAUDE.md §1 編集ポリシー**: 文案3 は 1 行追記・既存原則の補強・ポインタ化のみ → 「追加してよいもの」(原則 1-3 行、ポインタ 1 行、高頻度参照 1 行) に該当。「追加禁止」(手順・フロー、事故番号、適用例列挙) には該当しない → **整合**
- **記載先判定 Q1-Q3**:
  - Q1（知見MD追記で対応可能か）: **Yes**。文案1・2 で 005 §⑥・§⑩ 改善 → 主対策は完了
  - Q2（CLAUDE.md 既存原則の表現改訂が必要か）: **限定的 Yes**。文案3 は §6 の既存「Cloud Build」原則の補強（新規ルール追加ではない）
  - Q3（CLAUDE.md §1 編集ポリシー適合）: **Yes**（上記参照）
- **既存 memory との衝突**: `feedback_claudemd_index_first.md` と推奨は **同方向**（索引ファースト原則を強化する形）→ 衝突なし

### 8c. 副作用シミュレーション

**(i) 単体副作用**:
- 文案1: §⑥ 冒頭に警告ブロックが入る → 既存の「ルール:」ブロックと並列配置 → 視覚的に「Windows 必須 → 通常ルール」の優先順が明示される。新たな誤読パターン無し
- 文案2: §⑩ テーブルの 1 セルが 2 症状併記で長くなる → セル内で `(1)` `(2)` で番号付け → 可読性確保。新たな誤読パターン無し
- 文案3: §6 L120 が 1 行から 1 行（少し長く）に → 行数増加なし。CLAUDE.md 200 行上限への影響なし

**(ii) クロスルール競合**:
- 文案1 の「Windows 必須ルール」 vs 既存「ルール（3ステップ）」: 前者は環境制約、後者は手順 → 競合せず並列
- 文案3 の「Windows + Google Drive 一時ディレクトリ必須」 vs §6 L125「ダウンロード先: 検証用DLは `C:\tmp\` を使う」: 同じ `C:\tmp\` 系の指示で **同方向**（衝突なし）

**(iii) 状態依存シナリオ**:
- 非 Windows 端末（Cloud Shell / Linux）からの実行: 文案1 の「Windows + Google Drive 必須ルール」は非該当 → 既存「ルール」（3ステップ）が適用される → 動作保持
- 初回 deploy（§④）: 文案1 は §⑥ 配下なので §④ には影響しない → ただし §④ L128 でも `gcloud builds submit ... .` を Drive 配下から実行する可能性あり → **追加検討事項**（§④ にも同様の注記が必要か）→ 【確認できなかった事項】に記載

**(iv) 再発防止策の実効性**:
- 文案1・2: **症状フレーズ埋め込み型** → AI が次回エラーで grep した際に物理的にヒットする = 意志依存ではなく構造的発火 → **強実効性**
- 文案3: **ポインタ補強型** → 索引ファースト原則の発火信頼性に依存（意志依存型の残存）→ **中実効性**。本件は §6 L120 と §10 L174 の二重導線で発火率を上げる狙い
- 構造的強制（PreToolUse hook）: **方向性のみ提示**。本レビューで具体実装は提案しない（過去 3 回累積でも MD 改善先行が妥当）

### 8d. 事後確認事項の定義

推奨実施後の確認観点:
- 文案1 適用後、次回 Cloud Build 失敗時に AI が `WinError 32` / `file.tgz` で grep して 005 §⑥ にヒットするか
- 文案2 適用後、§⑩ よくある罠の visual scan で症状フレーズが目に入るか
- 文案3 適用後、CLAUDE.md §6 を読んだ AI が Windows 環境フラグを認識して 005 §⑥ へ飛ぶか
- **長期監視**: `behavior:index-first-violation` タグの累積（次回発生で 4 回目 → hook 化判断のトリガー）

---

## 【エスカレーション判定】

### 7a. ソースコード・仕組み側

- **本件単独**: MD 修正で十分（推奨対応で症状到達性が確保される）
- **3 回累積（MR-153/160/204）**: 構造的強制（PreToolUse hook で `gcloud builds submit` 検出時に 005 §⑥ Read 強制）が候補。**ただし本レビューでは方向性のみ提示し具体実装は別タスクへ委譲**

### 7b. 制度的エスカレーション

- **記載先**: 知見MD（005_cloudrun_job_deploy.md §⑥・§⑩）+ CLAUDE.md §6（1行ポインタのみ補強）→ §1 編集ポリシー適合
- 新規スキルMD追加・新規知見MD作成は **不要**

---

## 【確認できなかった事項】

1. **§④ 初回 deploy 手順への注記要否**: §④ L128 の `gcloud builds submit ... .` も同じ Windows ファイルロックリスクを持つ可能性。本レビューでは「§⑥ は再 deploy の主動線」と判断し §④ への注記は保留。実態として初回 deploy が Windows + Google Drive で実施される頻度を確認できれば §④ にも同様注記を追加すべき
2. **`.venv` 関連 hook エラー**: 観測事実として注意配分を奪った可能性が提出MDに記載されているが、本レビューのスコープ外（提出MD自身の「スコープ外」宣言通り）。別途要対処
3. **「セッション中盤の作業切替時に索引ファーストが発火しない」現象の汎用化**: MR-153/160/204 の共通パターンを「作業切替トリガー」として明示化する余地。本レビューでは推奨に含めず、累積監視を継続
4. **PreToolUse hook の具体実装**: 既存 hook 構成（`.venv` blocking error 等）への上書きリスク・実装コストは未調査

---

## 【004-1 追記予定】

下記タグで `docs/knowledges/tools/004-1_code_review_findings_log.md` に追記:

```
- [2026-05-18] behavior:index-first-violation | docs/reviews/204_mr_cloudbuild_md_overlooked.md / 005_cloudrun_job_deploy.md §⑥ | [MR-204] Cloud Build deploy で CLAUDE.md §10「Cloud Run Jobにデプロイ」エントリ未参照、5回連続 WinError 32 リトライ。MR-153/160 に続く3度目の同種事故
- [2026-05-18] md:discoverability | docs/knowledges/tools/005_cloudrun_job_deploy.md §⑥ L190-L201 | [MR-204] Windows ファイルロック対策(一時ビルドディレクトリ方式)が§⑥小見出し「スクリプト変更時の更新」配下に埋没。冒頭症状フレーズ警告なし
- [2026-05-18] md:missing-source-verification | docs/knowledges/tools/005_cloudrun_job_deploy.md §⑩ L347 | [MR-204] よくある罠表のビルドコンテキスト肥大化行に「WinError 32 / file.tgz / PermissionError」症状フレーズ未収録。grep で 005 にヒットしない構造
```

---

## 完了時報告

- レビューMDパス: `docs/reviews/204_mr_cloudbuild_md_overlooked.md`（提出MDに追記の形）
- AI可読性評価: **A**（005 §⑥ 本体は明確）
- 誤読リスク評価: **B**（症状起点での到達性に改善余地）
- 重大指摘件数: **2件**（症状フレーズ未収録 + §⑩ 罠表拡張）
- 最重要指摘: ①005 §⑥ 冒頭に「Windows + Google Drive 必須」症状フレーズ警告ブロック追加、②005 §⑩ 罠表 L347 を 2 症状併記に拡張、③CLAUDE.md §6 L120 にポインタ補強（1行）
- 004-1 追記件数: 3件（`behavior:index-first-violation` / `md:discoverability` / `md:missing-source-verification`）
- メインエージェント向け feedback memory 追加: **不要**（既存 `feedback_claudemd_index_first.md` が該当。新規追加は重複）

---

## 返却 2026-05-18

### 重大な指摘
- #1 005 §⑥ 冒頭に Windows + Google Drive 必須警告: [採用] 症状フレーズ込みで適用済み
- #2 005 §⑩ 罠表 L347 を 2 症状併記に拡張: [採用] MR-171/MR-204 併記で適用済み

### 改善提案
- #1 CLAUDE.md §10 索引に症状エントリ追加: [見送り] レビュアー判定「設計思想と不整合、症状フレーズ埋め込みで十分」を尊重
- #2 CLAUDE.md §6 L120 Cloud Build 行への注記補強: [採用] 適用済み（§1 編集ポリシー適合確認済み）
- #3 メインエージェント向け feedback memory 追加: [見送り] レビュアー判定「既存 feedback_claudemd_index_first.md が該当、重複かつ対症療法」を尊重
- #4 PreToolUse hook 構造的対策: [次回対応] 本セッションでは方向性のみ。MR-153/160/204 の 3 度目反復として記録、4 回目発生時に hook 化判断のトリガー

### 確認できなかった事項
- §④ 初回 deploy 手順への注記要否: [次回対応] 初回 deploy 実施頻度を踏まえて判断、本セッションでは保留
- .venv 関連 hook エラー: [見送り] スコープ外、別タスク
- 「セッション中盤の作業切替時に索引ファースト未発火」の汎用化: [次回対応] 累積監視継続
- PreToolUse hook 具体実装コスト: [次回対応] 上記 #4 と同じ
