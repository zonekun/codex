# MD AI可読性レビュー: monitor_backfill 二重投入事故

- 日時: 2026-04-26 13:30 JST
- 対象: `docs/knowledges/tools/013-2_monitor_backfill.md`, `CLAUDE.md` §監視する／見張る
- パターン: 2 (誤読・ミス原因レビュー)
- レビュアー: Claude (md-reviewer skill)
- 出力先: `docs/reviews/001_monitor_backfill_duplicate_workflow.md`

---

## 【サマリー】

- レビュー対象の要約: `monitor_backfill.py` の BG 起動時に `Bash(run_in_background=true)` + コマンド内 `&` を併用し、プロセス生存を誤判断して resume プロセスを追加起動。結果 workflow が 4 本起動（正規 2 + 重複 2）。
- AI可読性評価: **C** — BG 起動パターンの具体例が MD に欠落しており、`run_in_background=true` と `&` の併用禁止が明示されていない。
- 誤読リスク評価: **C** — プロセス生存確認手順が `ps -ef | grep` の 1 行コマンドのみで、Windows Git Bash での PID 検出限界に対する注意がない。
- 主要リスク:
  1. BG 起動パターンの記述不足により、同じ二重起動が再発する
  2. プロセス生存確認が `ps | grep` だけでは Windows 環境で不十分
  3. monitor_backfill.py にプロセス排他制御（ロック機構）がない

---

## 【Markdown 品質評価】

### Accuracy / 正確性
- `013-2_monitor_backfill.md:30-31` の BG 実行説明は `# run_in_background=true で起動、完了時に task-notification が飛ぶ` の 1 行コメントのみ。**具体的な Bash tool 呼び出しパターンが書かれていない**ため、オペレーターが自分で組み立てる余地がある

### Completeness / 完全性
- **BG 起動の正しいコマンドテンプレートが欠落**: `run_in_background=true` をツールパラメータとして使う場合、コマンド内に `&` を付けてはいけないことが書かれていない
- **プロセス排他制御の記述なし**: 同一 YAML config で複数プロセスが同時起動するケースへの防御がない
- **resume 起動前の重複チェック手順がない**: resume する前に「元プロセスが本当に死んでいるか」を確認する手順が書かれていない

### Relevance / 関連性
- インシデント事例（§🚨）は充実しているが、**プロセス管理の誤りに起因するインシデント**が未収録

### Actionability / 実行可能性
- `使い方` セクションで前景実行と BG 実行が区別されているが、BG 実行の**具体的な呼び出し方**が示されていないため、AI が自分で構成する必要がある

---

## 【AI 誤読リスク】

- `013-2:30-31` の `# run_in_background=true で起動` はコメント行であり、**Bash tool のパラメータ指定なのか、bash シェルの `&` を指すのか**が曖昧。AI は両方を組み合わせる可能性がある
- `013-2:170-172` の `ps -ef | grep recover_batch  # or monitor_backfill` は UNIX 前提。Windows Git Bash では `ps aux` でも Python プロセスの完全なコマンドライン引数が表示されないケースがあり、grep が空振りする

---

## 【MD 構成リスク】

- BG 実行パターンが `使い方` セクションの末尾コメント 1 行に押し込められており、本番で最も使う起動パターンにもかかわらず目立たない
- `CLAUDE.md:41` の `Bash(run_in_background=true) でポーリングスクリプトを起動` は手段を列挙しているが、**各手段の使い分け・注意点**が書かれていない

---

## 【指示優先順位・文脈境界】

- `CLAUDE.md` §監視する／見張る と `013-2_monitor_backfill.md` の責務分担は明確（CLAUDE.md = 原則、013-2 = 具体手順）
- ただし、`CLAUDE.md` が列挙する監視手段に `run_in_background=true` の使用上の注意がないため、AI は Bash tool の一般的な使い方（`&` でバックグラウンド化）と混同する

---

## 【パターン 2: 誤読・ミス原因分析】

### 事象
`monitor_backfill.py` を `Bash(run_in_background=true)` で起動する際、コマンド内にも `&` を付けたことで bash が即 return → task-notification が exit 0 で完了報告。実際の Python プロセスはバックグラウンドで生存していたが、`ps aux | grep monitor_backfill` が空を返したため「プロセスなし」と誤判断。resume 用 YAML を作成して 2 つ目のプロセスを起動し、結果として load 完了時に 4 workflow が投入された。

### 読み手がどう解釈した可能性があるか
1. `Bash(run_in_background=true)` はコマンドをバックグラウンドで実行するツールオプション。`&` はシェルのバックグラウンド演算子。**両方使えばより確実にバックグラウンド化される**と解釈した可能性
2. task が exit 0 で完了 → **プロセスが終了した**と解釈（実際は bash が終了しただけで Python は生存）
3. `ps aux | grep monitor_backfill` が空 → **プロセスは確実に死んでいる**と解釈（Windows Git Bash の ps 制限を考慮していない）

### 直接原因
- `013-2_monitor_backfill.md:30-31` に BG 起動の具体的なコマンドテンプレートがない
- `run_in_background=true` と `&` の併用禁止が明示されていない
- プロセス生存確認の Windows 対応手順がない

### 根本原因
1. **MD の記述不足**: BG 起動パターンが 1 行コメントで済まされており、正しい使い方が示されていない
2. **排他制御の仕組み不在**: monitor_backfill.py にロックファイル等の排他制御がないため、複数起動を検知・防止できない
3. **プロセス確認手順の環境依存**: `ps -ef | grep` は UNIX 前提であり、Windows Git Bash では不十分

### 誤読を許した MD 上の原因
- `013-2_monitor_backfill.md:30-31`: BG 実行がコメント 1 行で、テンプレートも注意書きもない
- `013-2_monitor_backfill.md:170-172`: `ps -ef | grep` が Windows 環境で信頼できないことへの注意なし
- `CLAUDE.md:41`: `Bash(run_in_background=true)` を列挙するが、`&` との併用禁止を書いていない

### 再発防止の方向性

#### A. MD 修正（即効性あり・確実性低）
1. `013-2` の BG 起動セクションにテンプレートコマンドと `&` 併用禁止を明記
2. resume 前の重複チェック手順を追加
3. インシデント事例として本件を追記

#### B. コードガード（即効性やや劣る・確実性高）
1. **monitor_backfill.py にロックファイル機構を追加**: 起動時に `config YAML パス` をキーとするロックファイルを取得、既に取得済みなら abort。これで同一 config の多重起動を構造的に防止
2. **workflow 投入前に既存 ACTIVE workflow の重複チェック**: 同じ date 範囲の ACTIVE workflow が既にあれば投入をスキップ

---

## 【重大な指摘】（即修正）

### #1 BG 起動パターンのテンプレート・注意書き欠落
- 箇所: `docs/knowledges/tools/013-2_monitor_backfill.md:28-31`
- 問題: BG 実行の具体的なコマンドが示されず、1 行コメントのみ
- AI の誤読パターン: `run_in_background=true` と `&` を両方使うことで「より確実にバックグラウンド化される」と解釈
- トリガー: ユーザーから「見張り要」指示を受け、monitor_backfill を BG 起動する場面
- 影響: 二重プロセス起動 → 二重 workflow 投入 → TPU コスト浪費
- 根拠: 本事故で実際に発生
- 推奨対応: BG 起動テンプレートを明記し、`&` 併用禁止と理由を注意書きで追加
- MD 修正だけで足りるか: **足りない**。ロックファイル機構がなければ、テンプレートを無視して起動されたら防げない

### #2 プロセス生存確認手順が Windows Git Bash で不十分
- 箇所: `docs/knowledges/tools/013-2_monitor_backfill.md:170-172`
- 問題: `ps -ef | grep monitor_backfill` は Windows Git Bash でコマンドライン引数を完全表示しないケースがあり、空振りする
- AI の誤読パターン: grep 結果が空 → 「プロセスは確実に死んでいる」と断定
- トリガー: BG プロセスの生存確認が必要な場面
- 影響: 生存プロセスを見落として resume プロセスを追加起動
- 根拠: 本事故で `ps aux | grep monitor_backfill` が空を返したが実際は PID 268409 が生存
- 推奨対応: `wmic process where "name='python.exe'" get commandline,processid` または `tasklist /FI "PID eq <PID>"` を Windows 用の代替手段として追記。`run_in_background=true` の場合は task output ファイルの更新時刻も確認手段として使える
- MD 修正だけで足りるか: 足りる（手順の環境対応）

### #3 monitor_backfill.py にプロセス排他制御がない
- 箇所: `scripts/monitor_backfill.py` 全体
- 問題: 同一 config YAML で複数プロセスが同時起動可能
- AI の誤読パターン: MD の問題ではなくコード側の防御不在
- トリガー: 操作ミスや通信断で resume プロセスを起動する場面
- 影響: 二重 workflow 投入
- 根拠: 本事故
- 推奨対応: 起動時にロックファイル（PID ファイル）を作成し、既存ロックがあり PID が生存していれば abort する仕組みを追加
- MD 修正だけで足りるか: **足りない**。ロックファイル機構はコード側の改修が必要

---

## 【改善提案】（中優先度）

### #1 workflow 投入前の重複チェック
- 箇所: `scripts/monitor_backfill.py` の workflow 投入ロジック
- 現状: 無条件で workflow を投入する
- 提案: `gcloud workflows executions list` で同一ワークフロー・同一日付範囲の ACTIVE execution がないか確認し、あれば投入をスキップまたは警告
- 期待効果: 仮にプロセスが二重起動しても、workflow の二重投入を防止

### #2 CLAUDE.md §監視する／見張る に `run_in_background=true` 使用時の注意を追記
- 箇所: `CLAUDE.md:41`
- 現状: 手段の列挙のみ
- 提案: `run_in_background=true` 使用時は「コマンド内に `&` を付けない」注意書きを追加
- 期待効果: 本件と同パターンの事故防止

---

## 【ソースコード・仕組み側への波及】

- 対象: `scripts/monitor_backfill.py`
- 理由: MD の注意書き追加だけでは、操作ミスや通信断時の resume 起動で再発する。プロセス排他制御はコード側でしか確実に実装できない
- 推奨対応: ロックファイル機構（PID ファイル）を `monitor_backfill.py` に追加。具体的には:
  1. 起動時に `/tmp/monitor_backfill_<config_hash>.lock` を作成、中に PID を書く
  2. 既にロックファイルがあり、記載 PID のプロセスが生存していれば `sys.exit("ERROR: 既に実行中 PID=xxx")`
  3. 正常終了・異常終了時にロックファイルを削除（atexit で登録）
- 検証方法: 同一 YAML で 2 プロセス起動を試み、2 つ目が abort されることを確認

---

## 【確認できなかった事項】

- Windows Git Bash の `ps` がどの条件で Python プロセスのコマンドライン引数を表示しないのかの正確な仕様
- `Bash(run_in_background=true)` が内部でどのようにプロセスを管理しているか（fork? subprocess?）の Claude Code 側実装詳細
- 元プロセス（bywxergvv）の task output ファイルにリアルタイムでログが書き込まれていたかどうか（確認手段として使えたか）
