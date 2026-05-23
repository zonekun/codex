# 207_cr_orders_heartbeat_revision

**提出日**: 2026-05-18
**提出者**: Claude (メインエージェント)
**レビュースキル**: code-reviewer
**レビューパターン**: 2（既存改修）

---

## レビュー対象ファイル

| パス | 役割 |
|------|------|
| `skills/orders_soldier.md` | ソルジャー spec（ハートビート + status file 書き出しを追加） |
| `skills/orders_commander.md` | コマンダー spec（206 レビュー重大5+改善8 全採用＋ハートビート方式取り込み） |

参考情報（編集対象外、コンテキスト用）:
- `.claude/commands/orders-soldier.md` — ソルジャーラッパー（変更なし）
- `.claude/commands/orders-commander.md` — コマンダーラッパー（変更なし）
- `docs/reviews/204_cr_tdnet_orders_extract_skill.md` — ソルジャー初版レビュー（全採用済み）
- `docs/reviews/205_cr_tdnet_orders_commander_skill.md` — コマンダー初版レビュー（全採用済み）
- `docs/reviews/206_cr_orders_commander_dynamic_parallel.md` — コマンダー動的並列改修レビュー（重大5+改善8、本改修で全採用済み）
- `docs/knowledges/tools/100_agent_stuck_recovery.md` — Agent ハング救済ノウハウ

---

## 事象・背景

### なぜ改修するか

206 レビューで提出された改修案（バッチ並列 → 動的並列、30 分タイムアウト追加）に対し、レビュアーから「**完了通知駆動ループの実現可能性が未検証**」「**30 分タイムアウトの定期チェック機構が未定義**」「**TaskStop が Agent task に有効か未検証**」の重大 3 件が指摘された。これらは harness 仕様への依存が大きく、最悪 spec 通り実装しても改修目的（ハング検知の即時化）が達成されないリスクがあった。

ユーザー判断は「ハートビート方式で代替実現する」。assistant の内蔵タイマーや task-notification 配信に依存せず、ファイル mtime 監視で能動的にハング検知できる構造に転換。

### 改修方針

a. **ソルジャーが定期ハートビートを書く**
   - Step 1: `_heartbeat/{ticker}.hb` を初回書き出し
   - Step 5: 各 PDF Read 直前に hb を上書き（30 秒〜数分毎、30 分タイムアウトに十分余裕）
   - Step 7: hb 最終更新後に削除（完了シグナル）

b. **ソルジャーが完了 status file を書く**
   - Step 7: `_status/{ticker}.status` に STATUS 値を書き出し
   - task-notification 配信が来なくても、コマンダーは status file polling で完了検知可能

c. **コマンダーが完了検知を 3 段階 fallback**
   - (a) task-notification 受信（harness 配信あれば）
   - (b) status file polling（`ls _status/*.status`）
   - (c) Bash `sleep 60` で 1 分待機後の再 poll（能動 polling）

d. **コマンダーがハートビート mtime を能動チェック**
   - 5 分毎（fallback poll 周回内）に `find _heartbeat -mmin +30` で 30 分超を一括検出
   - 検出 → `TaskStop` 試行 → 失敗時 fallback（zombie として記録、inflight 削除）

e. **inflight 永続化（206 改善#5 採用）**
   - `orders_inflight.tsv` に `task_id\tticker\tinvoked_at_jst` を append/削除
   - resume 起動時に zombie task の TaskStop 試行

f. **206 重大#4#5 + 改善#1-#8 全採用**

### 設計上の重要決定

1. **ハートビート mtime チェックは Bash `find -mmin +30` で能動実行**（assistant 内蔵タイマーに依存しない）
2. **完了検知の 3 段階 fallback**で harness 配信不確実性を吸収
3. **TaskStop 失敗時 fallback** + `recently_stopped` セットで zombie 戻り値の誤検知防止
4. **`_heartbeat/` `_status/` ディレクトリはソルジャー側で作成**（spec §Step 1 で mkdir 指示）
5. **inflight 永続化はクラッシュ復旧用**（resume 時 zombie TaskStop + ヘッダ初期化）

---

## レビュー観点

### 1. ハートビート方式の堅牢性

- ソルジャーが hb 更新を忘れた場合（spec 違反）、30 分後に誤ハング判定される。spec §禁止事項に十分な強調があるか
- hb 更新タイミング（PDF Read 直前）と PDF 処理時間（最長 12 分）のバランス。各 PDF 処理中に hb 更新が走らないと「処理中なのにハング判定」の誤検知が起こり得る
- hb ファイル名は `{ticker}.hb` でアルファ ticker（`142A` 等）も問題なく動くか
- hb 削除タイミング（Step 7 末）と status file 書き出しの順序が race を起こさないか

### 2. 完了検知 3 段階 fallback の race / 漏れ

- task-notification と status file が**同時に**届いた場合の二重処理回避
- (b) status file polling の頻度（`ls _status/` の実行タイミング）が spec で明示されているか
- (c) Bash sleep 60 中に完了通知が来た場合、sleep 完了を待つことになるが、これによる遅延（最大 60 秒）が受容可能か
- 同一 task の完了通知と status file 出現の順序が不定の場合の整合性

### 3. ハング検知のタイミング

- `find _heartbeat -mmin +30` を「5 分毎」と spec に書かれているが、実装上は fallback poll の周期で呼ぶことになる。完了通知が連続して来る場合、fallback poll が呼ばれず hb mtime チェックも走らない可能性
- 30 分タイムアウト発火の境界値（spec で `elapsed >= 30min` と明示済み）

### 4. inflight 永続化と zombie 検知

- `orders_inflight.tsv` の書き込み頻度（起動・完了・タイムアウト毎）と Windows mv 原子性
- resume 起動時の zombie TaskStop 試行が、harness 側で「task が既に消えている」場合に失敗ログを大量に吐かないか
- セッション跨ぎで Agent task が生存するか（harness 仕様依存）に対する spec の安全側設計

### 5. TaskStop 失敗時 fallback の妥当性

- §Step 3 §C-2 で「TaskStop 失敗 → ログ + inflight 強制削除 + zombie 戻り値は recently_stopped で吸収」が成立するか
- `recently_stopped` set の保持期間 30 分が短すぎないか（zombie が 30 分超で戻ってきたら誤検知）

### 6. 既存運用との互換性

- ソルジャー側の他フィールド（JSON 構造、HIGHLIGHTS 上限、note ドメイン、failed_* 削除規約等、過去レビューで採用済み）が破壊されていないか
- コマンダー側で旧バッチ並列方式から動的並列への移行は spec 内で完結しているか
- ログイベントドメイン（`hb_alive_check` / `status_detected` / `inflight_persist` の追加）が過去ログとの互換性宣言と整合しているか

### 7. ソルジャー側の禁止事項追加

- 「ハートビート更新を忘れない」が spec §禁止事項 / §Step 5 で十分強調されているか
- status file 書き出しを忘れた場合の影響（コマンダーは task-notification で完了検知可能、ただし fallback を奪う）

---

## スコープ外（レビュー対象外）

- 既存の orders_index.csv / orders_log.tsv の実走行データ
- BQ テーブルスキーマ・GCS バケット配置
- 089/042 知見 MD の内容
- LINE 会話モード関連の運用ルール
- ハートビート / status file 用 別 Agent ウォッチドッグの構築（spec ではコマンダー本体が能動チェックする方針で完結）

---

## レビュー結果記入欄

（code-reviewer から返却された指摘・提案を以下に追記）

---

# コードレビュー: 受注高抽出ソルジャー＋コマンダー ハートビート方式改修

- 日時: 2026-05-18 (JST)
- 対象: `skills/orders_soldier.md`（基準: HEAD 時点。ハートビート + status file 追加版） / `skills/orders_commander.md`（基準: HEAD 時点。動的並列+30分タイムアウト+ハートビート方式+206 重大5&改善8 全採用版）
- パターン: 2（既存改修）
- レビュアー: Claude (code-reviewer runbook, sub-agent)

> **出力先の特記**: パターン 2 の標準は新規 `docs/reviews/NNN_cr_*.md` 作成だが、提出元のユーザー指示により**既存 207_cr_orders_heartbeat_revision.md の「レビュー結果記入欄」末尾に追記**（CLAUDE.md §4.2 ユーザー指示優先）。

---

## 【サマリー】

- 変更の要約: 206 レビューの重大3件（完了通知駆動ループ未検証 / 30 分タイマー機構不在 / TaskStop 有効性未検証）に対するユーザー判断「ハートビート方式」を spec に取り込んだ。ソルジャー側に `_heartbeat/{ticker}.hb` の Step 1/5/7 での書き出し + `_status/{ticker}.status` 書き出しを必須化。コマンダー側は完了検知を **(a) task-notification → (b) status file polling → (c) Bash sleep 60 + ls 再 poll** の 3 段階 fallback、ハング検知を `find -mmin +30` の能動チェックに転換。inflight 永続化（`orders_inflight.tsv`）、TaskStop 失敗時 fallback、`recently_stopped` セット 30 分保持、Step 6 実装メモ追加、最終レポート `TIMED_OUT` 行追加、その他 206 改善 8 件を採用。
- 品質評価: **B** — 改修の方向性（assistant 内蔵タイマー依存を排除し、ファイル mtime + 能動 Bash 駆動に転換）は 206 重大 3 件への適切な回答で、設計は概ね妥当。ただし**ハング検知の発火経路（§Step 3 §B のループ駆動構造）に「ハング状態では fallback poll が回らない可能性」という残存リスク**があり、改修動機の達成度が前回より改善されたものの完全ではない（後述 #1）。加えて、ソルジャー側 §Step 5 のリトライ中に hb 更新が無いため、Read 自体が長時間返らない場合のハング誤判定リスクが残る（#2）。これらは 206 重大 3 件のような「実現不能」レベルではなく、**運用上ほぼ大丈夫だがエッジケースで穴がある**水準なので B 評価。
- 主要リスク:
  1. **完了通知が一切来ない＆hb mtime チェックも回らない死角**: §Step 3 §B のループ進行は「完了検知（a/b/c）→ §Step 4 で 1 件処理 → 補充 → fallback poll」の順だが、§B-4 の `sleep 60` 周期に達するまで §C の `find -mmin +30` は明示的に呼ばれない。fallback poll 周期内で呼ぶと書かれているが、「いつ」「どの分岐で」が曖昧で、ハングだけ発生して他 task は順調に完了通知を返している場合に、完了通知駆動ループだけが回り続け、hb mtime チェックが永遠にスキップされる経路が残る
  2. **ソルジャー §Step 5 リトライ中の hb 更新欠如**: 1 PDF あたり最大 3 回リトライするが、hb 更新は「各 PDF の Read 直前」（=リトライ前 1 回のみ）。Read tool 自体が応答返さず 30 分以上ハングするケース（実際 100 知見 MD の 2026-05-12 事例で Google Drive 上の Grep が 48 分ハング）では、hb は最新化されないまま 30 分タイムアウト誤発火する可能性
  3. **`recently_stopped` セットのメモリ揮発性**: 30 分保持は妥当だが、コマンダー本体がクラッシュ → resume すると `recently_stopped` セットが空で再開され、zombie 戻り値が来ても `ticker_mismatch` として誤検知ログ化する。inflight TSV と同じく永続化が必要

## 【改修プラン評価】

### 妥当性

- **方向性**: 206 重大 3 件への回答として極めて妥当。「ハートビート方式」は assistant 内蔵タイマーへの依存を排除し、ファイル mtime という外部観測可能な状態に置き換えることで、harness 仕様の不確実性を吸収する設計に転換した。これは 206 で指摘した `content:architecture-platform-mismatch` の根本対処（プラットフォーム機能制約と整合する設計）に該当する。
- **根本原因 vs 対症療法**: 206 重大 #1（完了通知駆動ループ）→ status file polling + Bash sleep poll の 3 段階 fallback で吸収。重大 #2（30 分定期チェック）→ `find -mmin +30` の能動 Bash 実行で代替。重大 #3（TaskStop 有効性）→ 失敗時 fallback + zombie 戻り値の `recently_stopped` 吸収で 2 段防衛。いずれも対症療法でなく**真因（assistant が時計を能動的に見られないこと、harness の通知配信不確実性）への構造的対処**であり、設計品質は高い。
- **独立仮説の照合**: 改修前症状（2 時間放置事故）の独立原因分析は「Agent ツール完了通知の不確実性 + assistant の能動時計駆動の欠如」だった。本改修はこの 2 点をそれぞれ「status file polling」「Bash sleep + find -mmin」で個別対処しており、方向性に問題なし。

### 副作用・デグレードチェック

- [ ] **ソルジャー §Step 5 リトライ中の hb 更新欠如**: 「各 PDF の Read 直前」(L148) でしか hb 更新せず、リトライ (1)→(2)→(3) の間は更新されない。Read tool 自体がハングした場合、`pages: "1-5"` リトライまで到達しないまま 30 分経過する可能性。100 知見 MD の 2026-05-12 事例（Read 系ツールが Google Drive 上で 48 分ハング）が再現すれば、hb は古いまま → ハング判定 → TaskStop 強制終了。これは「PDF 巨大スキャン PDF で context 枯渇を防ぐ」という Step 5 リトライポリシーの本来目的とは別の経路で誤発火する。
- [ ] **`recently_stopped` セットの揮発性**: §Step 3 §C-7 で 30 分メモリ保持と書かれているが、`orders_inflight.tsv` のような永続化はない。コマンダーがクラッシュ → resume すると set が空 → zombie 戻り値が来ても §Step 4 §A-4 の照合が失敗 → §A-3 の TICKER 照合に進み「inflight 内 ticker と照合失敗」で `ticker_mismatch` 誤検知ログ。206 改善 #5 で inflight を永続化したのと同じ理由で `recently_stopped` も永続化が必要（または起動時に「過去 30 分以内の `kind=taskstop_failed` `kind=soldier_hung_30min_timeout` ログから task_id を抽出して set 再構築」する手順を spec 化）。
- [ ] **ソルジャー hb 削除（Step 7）と status 書き出しの順序逆**: §Step 7 spec L378-400 を見ると、順序は「PDF 削除 → status file 書き出し → hb 最終更新 → hb 削除」。コマンダー側は「hb 消失 + status 出現 = 完了」と解釈する設計だが、ソルジャー上で「status 書き出し済 + hb 削除前」の状態の瞬間がある。コマンダーの §Step 3 §B-1 (b) は「status file polling で新規ファイル発見 → §Step 4 で解析」なので、その瞬間に status 検知して処理を始めると hb がまだ残っている状態。ただし §C のハング検知は `mmin +30` 基準で、status 直書き直後の hb は mtime 新しい（L396 で `date >` を打った直後）ため誤検知はしない。**race は生じないが、コマンダー解釈モデル「hb 消失 + status 出現」が L400 のコメントで誤誘導**（実際の実装に必要なのは status の出現のみ）。
- [ ] **`_status/{ticker}.status` 削除タイミング**: コマンダーは §Step 4 §C 末尾で「処理後に `rm -f _status/{ticker}.status`」と書かれているが、ハング判定 → TaskStop の場合は status file は存在しない（ソルジャーが §Step 7 まで到達しないため）。`_heartbeat/{ticker}.hb` は §C-5 で削除指示があるが、**ソルジャー側でハング後に何らかのフォールバックで status が後から書かれた zombie ケース**（TaskStop 失敗 + ソルジャーが復活して Step 7 まで到達）では、status file が残存。次回 resume 時に status を拾うと「既に完了したと誤認」する。**resume 起動時に `_status/*.status` を残存件として読み込む手順は明示されていない**（§Step 2 mode=resume L204-220 に「status file クリーンアップ」がない）。
- [ ] **`_status/{ticker}.status` 内容のフォーマット**: ソルジャー側 L386 で `echo "STATUS={status_value}" > "..."`。1 行のみ。コマンダー側 §Step 3 §B-1 (b) で「内容 Read で STATUS 取得」と書かれているが、**status file からは TICKER / DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA / JSON_PATH / JSON_BYTES が取得できない**。これらは task-notification の戻り値で取得する前提だが、status file 経由完了検知（(a) が来ない場合）では、§Step 4 §B のソルジャー戻り値解析に必要な数値フィールドが全て欠落する。**status file 検知だけで完了処理を完結させる経路が成立しない**（CSV 更新時に `docs_found=0, docs_read=0, ...` で書かざるを得ない）。
- [ ] **既存ログ TSV の `hb_alive_check` / `status_detected` / `inflight_persist` イベント追加**: §出力ファイル §2 L103 で event ドメインに追加されているが、**event=hb_alive_check の発生タイミング（毎周期 1 件か全 task まとめてか）と detail フォーマットが未定義**。L161-178 で `error kind=...` バリエーションは網羅されているが、新規イベント 3 種の detail 仕様が抜けている。
- [ ] **過去ログ互換性宣言の一貫性**: §出力ファイル §2 L107 で `batch_start`/`batch_end` の互換性宣言は記載済みだが、**ハートビート方式以前の動的並列 v1（206 改修前）のログとの互換性は不明**。例えば test_build 10 件の実走ログにはおそらく `batch_start` 等が含まれるが、その間の event 名称変更は本 spec 内で言及なし。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **ハング検知の発火経路の曖昧性**: §Step 3 §B-1 (c) で「ハートビート切断」が完了検知の選択肢の 1 つとして並列に書かれているが、**「いつそれを判定するか」の trigger が §B-4 の `sleep 60` 周期内に紛れ込んでいる**（L261-263、§Step 6 L394 「`find _heartbeat -mmin +30` で一括検出」）。完了通知 (a) が次々と来ているシナリオ（健全な task 3 件 + ハング 1 件 = 4 task が parallel=4 で稼働）では、健全 3 件の完了通知ループが回り続け、§B-4 の sleep 周期に到達しない可能性。結果ハング task が補充後 inflight に残り続け、hb mtime チェックが走らないまま放置される。**§B-1 と §B-4 の 2 段階で「§C 呼び出し」が暗黙化されており、いつ呼ぶかの明示が必要**。
- [ ] **同一 ticker の連続再投入時の hb ファイル干渉**: spec §禁止事項 L420 で「同一 ticker を二重起動しない」が `inflight` 排他で担保されているが、**TaskStop 後の次回 resume で同 ticker 再投入時、`_heartbeat/{ticker}.hb` ファイル名が同じ**。前回 run で削除されていれば問題ないが、§Step 3 §C-5 で hb 削除があり、TaskStop fallback 経路（§C-2 で削除指示なし）では削除されない可能性がある。L276 「inflight からは強制削除し空きスロットを開ける」と L279 「`_heartbeat/{ticker}.hb` を削除」の関係が分かりにくい（C-2 は fallback 経路、C-5 は正常 TaskStop 成功経路？）。次回 resume で古い hb 残存 → resume 起動直後に「mmin +30 該当」と即誤判定する経路。**§Step 2 resume の頭で「`_heartbeat/*.hb` を全削除」する手順を入れた方が安全**（既存稼働中 task が無いことが前提なので破壊しない）。
- [ ] **`_status/{ticker}.status` の世代管理欠如**: 同一 ticker の連続再投入で、前回 run の status が `_status/` 配下に残っている可能性。コマンダーは §Step 4 §C 末尾で削除するが、ハング → TaskStop パスでは status は無いので削除コードが走らない。次回 resume 起動直後に古い status を拾うと「既に completed と誤認」して即 CSV 更新する経路。**§Step 2 resume の頭で `_status/*.status` を全削除する手順が必要**。
- [ ] **`_heartbeat/` `_status/` のディスク使用量**: 各 ticker ごとに 1 ファイル × 200 〜 500 銘柄。Step 7 で hb 削除されるが、status は §Step 4 §C で削除される。万が一コマンダーが削除前にクラッシュすると、status が永続的に残る。長期運用で `_status/` が肥大化する可能性は薄いが、`_status/*.status` の累積 cleanup 手順が無い（resume 起動時 cleanup を入れれば自動解消）。
- [ ] **30 分タイムアウト境界値の Bash `find -mmin` 解釈**: spec §禁止事項 L422 で「30 分判定の境界値は `elapsed >= 30min` で TaskStop」と明示。しかし `find -mmin +30` は「mtime が 30 分より古い」=「`elapsed > 30 分`」（GNU find 仕様）。**`elapsed >= 30min` は `find -mmin +29` または `-mmin +30 -o -mmin 30` 相当で実現する必要**。1 分の境界ズレで誤検知防止できているように見えるが、spec の宣言と実装コマンドが整合していない。
- [ ] **`recently_stopped` 30 分保持の根拠**: §Step 6 L394 で「TaskStop 後 30 分間」と書かれているが、**30 分の根拠は spec で説明されていない**（30 分タイムアウトと同じ値だが、意味が違う）。30 分超で zombie 戻り値が来た場合、TICKER 照合で `ticker_mismatch` 誤検知ログ化する。Agent task は理論上 context 枯渇しない限り無期限稼働可能なので、保持期間を `parallel` 件中の最大 invoked_at + 30 分（=最後の起動から 30 分）のように動的にした方が安全。
- [ ] **inflight 永続化の更新頻度**: §Step 3 §A-2 で「append」、§Step 3 §B-2 (`inflight` 削除時)、§C-6 (タイムアウト時) で「全行書き直し」と書かれている。30 件並列で 30 回 / 件の完了 → 900 回の全行書き直し。`orders_inflight.tsv` が小さい（数 KB）なので I/O コストは小さいが、Windows mv 原子性問題（§注意事項）は inflight TSV にも同じく適用される。**inflight TSV のクラッシュ復旧手順（消失時の対応）が spec に無い**（消失すれば次回 resume で zombie 検知が全く効かない）。
- [ ] **fallback poll の最大 30 回繰り返し条件**: §Step 3 §B-4 「最大 30 回（=30 分）繰り返す。30 分間 status file も増えず task-notification も来なければ §C ハング判定に進む」と書かれているが、**「30 回経過したらどう §C に飛ぶか」の制御フローが暗黙**。`for i in 1..30 do sleep 60; ls; done` の擬似コードがなく、30 分 = 30 回というマジックナンバーが §禁止事項 L422 の「30 分タイムアウト」と概念的に被るが意味は別（前者は polling 周期上限、後者は task ごとの hb mtime 上限）。**§Step 3 §B-4 と §C の関係を「§B-4 は status/task-notification の待機ループ、§C は inflight 個別 task の hb mtime チェック」と明示分離する記述が必要**。
- [ ] **Step 7 でソルジャーが書き出す status が `failed_*` の場合の hb 扱い**: ソルジャー §Step 7 L396 「ハートビート最終更新 + 削除」は STATUS が `completed*` / `failed_*` を問わず実行される設計。コマンダーは status file の内容を読んで STATUS を取得するが、**hb がまだ削除中の race window で、コマンダーが §C の hb mtime チェックを走らせると「mtime 新しい→OK」になり問題なし**。ただし、ソルジャーが Step 7 途中でクラッシュ（status 書き出し済 + hb 削除前）した場合、コマンダーが status 検知 → §Step 4 で正常完了処理 → inflight 削除。次回 hb mtime チェックで「inflight に居ない ticker の hb が残存」する。L267 の `find -name "{ticker}.hb"` は inflight 内 ticker についてのみチェックなので無視されるが、**`_heartbeat/` 配下の孤児 hb の cleanup が無い**。
- [ ] **ソルジャー側「ハートビート更新忘れ」の禁止事項強調**: ソルジャー §禁止事項 L450-460 を見ると、L455-456 などには ハートビート関連の禁止事項が無い（§Step 5 §「ハートビート（必須）」L159 で「忘れるとコマンダーから『ハング』と誤判定」と書かれているのみ）。**§禁止事項に「Step 5 の各 PDF 開始時の hb 更新を省略しない」「Step 7 の status file 書き出しを省略しない」を 1 項目ずつ追加すべき**。提出元の重点観点 §7「ソルジャー側禁止事項の強調十分性」への直接的回答。

### 新規リスク

- **ファイル I/O 競合（特に Google Drive File Stream）**: `_heartbeat/` `_status/` は `C:/gdrive/claude/work/_heartbeat/` `_status/` 配下。Google Drive 同期対象ディレクトリで、ファイル mtime が同期遅延で実際の書き込みより遅れる可能性。コマンダーが `find -mmin +30` を走らせる時の mtime は OS が見るローカルキャッシュなので問題は薄いが、**ソルジャー（別 Agent task）が hb 書き込みしてから OS の mtime が更新されるまでのラグ**が長いと、健全タスクが「ハング判定」される。`C:/tmp/...` のような ローカルディスク配置の方が安全（既に PDF 一時保存先は `C:/tmp/tdnet_orders/` を使っている）。
- **30 分タイムアウト後の inflight 削除と再起動のレース**: §Step 3 §C で「inflight から削除 + 空きスロット開ける」と書かれているが、TaskStop 失敗時の zombie Agent がまだバックグラウンドで動いている状態で、**同 ticker を pending から再投入する経路は §禁止事項 L420 で禁止**。ただし `recently_stopped` セットに task_id を保持するだけで「ticker」は記録されていない。L302「mismatch はリトライで同じ結果になる確率高い」前提で当 run 再投入禁止という設計は ticker_mismatch だけで、`soldier_hung_30min_timeout` で TaskStop された ticker は次回 resume で再投入される。**resume 時、zombie Agent が同 ticker でまだ動いていれば二重起動（`{ticker}.json` 上書きレース）**。これは 206 改善 #5 で `orders_inflight.tsv` に zombie 検知を入れた目的そのものだが、`recently_stopped` セットが揮発するため、TaskStop 失敗 → resume パスで防げない。
- **PDF 処理時間 12 分 + hb 更新 1 回/PDF = 30 分タイムアウト発火リスク**: 実測中央値 6.6 分・最長 12.4 分とのことだが、最長 12 分の PDF が連続 3 件あると 36 分。**hb 更新は PDF 直前のみなので、連続 3 PDF を 36 分かけて処理する場合、hb mtime が 12 分前 → 24 分前 → 30 分超**で誤発火。実測最長 12.4 分の根拠データ（206 提出ファイル参照）が「1 銘柄の合計時間」か「1 PDF の時間」かで意味が変わる。後者なら新 hb 方式で「1 銘柄合計 30 分超 = 単一 PDF 長時間処理の累積」が起こり得る。
- **`_status/` polling の `ls` レート増**: §Step 3 §B-1 (b) で「`ls /c/gdrive/claude/work/_status/*.status` を実行」と書かれているが、**polling 頻度の上限が無い**（完了通知ループ各周回で呼ばれる可能性）。`parallel=10` で 10 件並列稼働中、各完了通知ごとに ls poll が走ると、Google Drive File Stream への `ls` 系 I/O が増大。ls 自体は軽量だが、Google Drive 経由は数秒オーダーの応答遅延が出ることがある。

## 【重大な指摘】（即修正）

### #1 ハング検知（§C）の発火経路が「完了通知が散発的に来るシナリオ」で死角化する

- 箇所: `skills/orders_commander.md:247-263`（§Step 3 §B-1 と §B-4） / `skills/orders_commander.md:265-284`（§Step 3 §C）
- 事象: §Step 3 §B-1 (c) は完了検知の選択肢の 1 つとして「ハートビート切断」が並んでいる（L254）。一方、§B-4 は「完了通知が一切来ない場合に備え、Bash sleep 60 で 1 分待機後の再 poll、最大 30 回」と書かれている（L261）。**§C の `find -mmin +30` を「いつ」「どの分岐で」呼ぶかは明示されておらず**、§Step 6 実装メモ L394 で「fallback poll 中の周期内で呼ぶ」とあるが、これは「全 task ハング状態でしか効かない」と読める。
- トリガー: `parallel=4` で 4 件稼働中、3 件は健全（30 秒〜10 分で完了通知を返す）、1 件はハング（無応答）のシナリオ。健全 3 件の完了通知が次々と (a) で検知 → §Step 4 で処理 → §B-3 で補充 → ループ先頭に戻る。この間 §B-4 の sleep 60 周期に到達せず、§C の `find -mmin +30` が呼ばれない。新規補充された 3 件もまた順次完了 → 補充 → ループ継続。**最初のハング 1 件は inflight に残ったまま 30 分超えても TaskStop されない**。
- 影響:
  - 改修動機 2（ハング検知の即時化）が、健全 task と混在シナリオで達成されない。206 重大 #2 の「30 分判定が永遠に走らない」という根本問題が形を変えて残存。
  - 実走ログ（バッチC 8 件中 5 件完了後 3 件 2 時間ハング）と同じ構造のシナリオで、新 spec でも同様の放置が発生する可能性。
  - ハング 1 件が inflight 1 枠を占有 → 実効並列度が `parallel - 1` に低下。`parallel=2` で 1 件ハングすれば実効並列度 1 で深刻。
- 根拠:
  - L247-263 §B のループ構造は「完了検知 (a/b/c) → §Step 4 → 補充 → 次の完了検知」の優先順位（L263「task-notification > status file > 1 分 sleep + ls poll」）。完了通知が来続ける限り、sleep 60 ステップに到達しない。
  - L267 §C の `find -mmin +30` を呼ぶ条件が「5 分毎（または fallback poll 中の周期内）」と書かれているが、**5 分毎を駆動するのは assistant 自身**で、これこそ 206 重大 #2 で指摘した「assistant に内蔵タイマー無し」問題そのもの。完了通知駆動ループで 5 分計測する手段が無い。
- 推奨対応 (**[方向性]**):
  - **§Step 3 §B のループ各周回（完了通知 1 件処理直後 = §B-3 補充後）に「`find _heartbeat -mmin +30` を必ず実行する」を明示**。各周回 1 回呼ぶことで、健全 task の完了通知ごとにハング検知が走る。`find` 自体は数百 ms で完了するので I/O コストは無視可能。
  - §B-1 の選択肢順を整理: 「(0) 周回先頭で必ず `find -mmin +30` 実行 → 該当 ticker があれば §C へ。(a) task-notification 受信 → §Step 4。(b) status file polling → §Step 4。(c) どちらも無ければ sleep 60 → 周回先頭に戻る」とする。「ハング検知 = 完了検知の選択肢」ではなく「ハング検知 = ループ周回開始時の必須チェック」に位置付け直す。
  - §Step 6 実装メモにも「`find` は各周回必須、周期は完了通知到着頻度に同期」と明示。
  - 加えて、`parallel=1` で 1 件ハング時は完了通知が 0 件 = ループが §B-4 sleep に入り、30 分後にようやく検知。最悪ケースとして許容するか、§B-4 の sleep 周期短縮（10 分 or 5 分）を検討する余地あり。
  - **記載先**: spec §Step 3 §B（フロー記述）と §Step 6 §実装メモ（補足）の両方。

### #2 ソルジャー §Step 5 リトライ中の hb 更新欠如（Read tool ハング時に誤発火）

- 箇所: `skills/orders_soldier.md:131-159`（§Step 5 リトライポリシー + ハートビート）
- 事象: §Step 5 のリトライ手順 (1) (2) (3) の中で hb 更新は「各 PDF の Read 直前」（L148）の 1 回のみ。Read tool 自体が応答返さず長時間ハングする（実例: 100 知見 MD §2026-05-12 事例で Google Drive 上の Read/Grep が 48 分ハング）と、hb は最新化されないまま 30 分タイムアウト誤発火する。
- トリガー: 巨大 PDF や Google Drive File Stream の I/O 遅延で Read 1 回が 30 分以上応答返さないケース。あるいは PDF 6 件中 3 件が各 12 分の最長ケース → 36 分連続処理 → hb 古い。
- 影響:
  - 健全な long-running ソルジャーが「ハング」誤判定 → TaskStop 強制終了。
  - 次回 resume で同 ticker 再投入 → 再度 Read ハング → 再 TaskStop の無限ループ（実害は強い）。
  - 「PDF 6 件全部を 12 分ずつ処理する銘柄」が永遠に completed にならない。
- 根拠:
  - L148 hb 更新は「各 PDF の Read 直前」のみ。リトライ (1)(2)(3) 各回の前に再更新する記述は無い。
  - L157「PDF 6 件処理する場合、6 回 + α のハートビート更新が想定される（PDF Read 自体は数十秒〜数分なので、PDF 毎に 1 回更新で 30 分タイムアウトには余裕で間に合う）」は **「PDF Read 自体は数十秒〜数分」という仮定**に基づき、12 分の最長ケース 3 連続（=36 分）を考慮していない。
  - 100 知見 MD §「2026-05-12 事例」で Read/Grep 系が Google Drive 上で 48 分ハングした実例あり。Read tool 単独でも 30 分超ハングは現実的シナリオ。
- 推奨対応 (**[方向性]**):
  - **§Step 5 リトライ手順 (1) (2) (3) 各回の直前に hb 更新を入れる**。1 回あたり数十 ms の Bash コストで誤検知を防げる。
  - 加えて、§Step 5 §ハートビート L148 の文言を「各 PDF の Read 直前 **+ 各リトライ直前**」に変更し、L157 の「6 回 + α」の見積を「最大 18 回（PDF 6 件 × 3 リトライ）」と更新。
  - 同時にコマンダー側 §Step 3 §C の 30 分タイムアウトを「PDF 最長 12 分 × 3 リトライ = 36 分」を許容する 40 分などに延長する選択肢もあるが、これは 206 改修動機（ハング検知の即時化）に逆行するので推奨しない。**ソルジャー側 hb 更新頻度を上げる方が筋が良い**。
  - **記載先**: `skills/orders_soldier.md` §Step 5 リトライポリシー + ハートビート節（修正のみで完結）。

### #3 `recently_stopped` セットの揮発性で resume 後 zombie 戻り値が誤検知

- 箇所: `skills/orders_commander.md:281`（§Step 3 §C-7 「`recently_stopped` に task_id を 30 分保持」）/ `skills/orders_commander.md:305`（§Step 4 §A-4 zombie 戻り値の許容） / `skills/orders_commander.md:391-393`（§Step 6 実装メモ）
- 事象: `recently_stopped` セットは「メモリのみ保持」（L392）。コマンダー本体がクラッシュ → resume 起動時に空セットで再開。前回 run で TaskStop 失敗した zombie Agent が resume 中に戻り値を返してきた場合、`recently_stopped` 照合が失敗 → §Step 4 §A-3 「TICKER 不一致」フローに進み、`ticker_mismatch` 誤検知ログ。
- トリガー: コマンダー run 中に SIGINT クラッシュ or context 枯渇でセッション切断 → resume 起動 → 前回 zombie Agent（harness 仕様次第で生存可能性あり）が戻り値配信。
- 影響:
  - ログに無意味な `error kind=ticker_mismatch` が大量発生（zombie が複数あれば数 10 件規模）。
  - 提出元の重点観点 §6「既存運用との互換性」で「ログイベントドメイン整合」が崩れる（過去ログとの統計差異）。
  - 実害は薄いが、運用者が「mismatch 多発 = ソルジャー側バグ」と誤診する可能性。
- 根拠:
  - L392 「`recently_stopped` は `set(task_id)` でメモリのみ保持」明示。永続化なし。
  - L114 `orders_inflight.tsv` の zombie 検知（resume 時）は task_id を TaskStop 試行するが、`recently_stopped` への登録は触れられていない。
  - L302「mismatch はソルジャー側のバグ可能性を含むためリトライで同じ結果になる確率高い」前提は、本当にバグの場合は正しいが、**zombie 戻り値による mismatch はバグではなく resume 復帰特有の現象**なので別扱いが必要。
- 推奨対応 (**[方向性]**):
  - **`recently_stopped` を `orders_inflight.tsv` と同じく永続化する**。例: `orders_recently_stopped.tsv` 新設、各 TaskStop 時に `task_id\tticker\tstopped_at_jst\n` を append。`stopped_at_jst` が現在時刻 - 30 分超なら自然失効。
  - または、**resume 起動時に `orders_log.tsv` から過去 30 分以内の `kind=taskstop_failed` `kind=soldier_hung_30min_timeout` イベントを scan して set 再構築**する手順を §Step 2 mode=resume に追加。永続化ファイル追加せず既存ログ流用可能。
  - 加えて §Step 6 実装メモ L392 の「メモリのみ保持」を「メモリ + ログから再構築」に修正。
  - **記載先**: `skills/orders_commander.md` §Step 2 mode=resume と §Step 6 実装メモ。

### #4 `_heartbeat/` `_status/` の resume 起動時クリーンアップが欠如（古いファイル拾い誤判定）

- 箇所: `skills/orders_commander.md:204-220`（§Step 2 mode=resume）/ `skills/orders_soldier.md:46-58`（§Step 1 事前ディレクトリ作成）
- 事象: §Step 2 mode=resume は `orders_inflight.tsv` の zombie 検知は行うが、`_heartbeat/*.hb` と `_status/*.status` のクリーンアップは無い。前回 run で Step 7 まで到達せずクラッシュしたソルジャーの hb / status が残存している場合、resume 起動直後に
  - 古い hb (mtime 30 分超) → §Step 3 §C の `find -mmin +30` で即誤検知（ただし inflight に居ない ticker は §C-1 の対象外なので影響薄）
  - 古い status → §Step 3 §B-1 (b) の `ls _status/*.status` で「新規ファイル」として誤拾い → §Step 4 で誤処理 → CSV 誤更新
- トリガー: コマンダー本体クラッシュ後の resume。前回 run でソルジャーが Step 7 直前まで実行（status 書き出し済）→ コマンダーが status 検知前にクラッシュ → resume で status を「新規」として拾う。
- 影響:
  - CSV の status が古い完了結果で上書きされる（実害: 古い JSON が今回 run で利用される）。
  - 提出元の重点観点 §6「既存運用との互換性」が崩れる。
- 根拠:
  - L204-220 §Step 2 mode=resume に `_status/` `_heartbeat/` cleanup の記述なし。
  - §Step 3 §B-1 (b) は「新規ファイル」と書かれているが、「新規」の定義（resume 起動時刻以降に作られたか）が無い。`ls` だけでは判別不能。
- 推奨対応 (**[方向性]**):
  - §Step 2 mode=resume 冒頭（zombie 検知の前）に **`_heartbeat/*.hb` と `_status/*.status` 全削除**を追加:
    - `rm -f /c/gdrive/claude/work/_heartbeat/*.hb /c/gdrive/claude/work/_status/*.status`
    - ログに `inflight_persist event=resume_cleanup_files heartbeat=N status=M` を append
  - 既存稼働中ソルジャーが居ない前提（resume の前提）なので、削除しても影響なし。これにより古い hb の偽検知 + 古い status の誤拾いを防ぐ。
  - **記載先**: `skills/orders_commander.md` §Step 2 mode=resume。

### #5 status file 経由完了検知時の数値フィールド欠落（CSV 更新不能）

- 箇所: `skills/orders_commander.md:251-253`（§Step 3 §B-1 (b) status file polling） / `skills/orders_commander.md:292-330`（§Step 4 §B 解析）/ `skills/orders_soldier.md:383-389`（§Step 7 status 書き出し）
- 事象: ソルジャー側 status file の内容は `STATUS={status_value}` の 1 行のみ（L386）。コマンダー側 §Step 3 §B-1 (b) は「内容 Read で STATUS 取得」と書かれているが、**status file には TICKER / DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA / JSON_PATH / JSON_BYTES / HIGHLIGHTS / ERRORS が無い**。§Step 4 §B はこれらをパースする前提だが、status 経由検知では取得できない。
- トリガー: task-notification (a) が来ず status file polling (b) のみで完了検知されたケース。harness の Agent 完了通知配信が不確実な状況（=本改修の主目的）で発生。
- 影響:
  - CSV 更新時、`docs_found=0, docs_read=0, docs_with_data=0, json_path="", json_bytes=0` のまま書かれる（実際には JSON は生成済みなのに）。
  - 集計レポート（§Step 5）で `failed_no_bq_records` カウントに誤って計上される可能性。
  - **status file polling 経路が「完了は検知できるが、CSV 更新の中身が空」**という致命的な穴。
- 根拠:
  - L386 `echo "STATUS={status_value}" > "..."` → 1 行のみ。
  - L292 §Step 4 §B 「§Step 8 戻り値フォーマット」の全フィールドを前提にパースしているが、status file のフォーマットはそれを満たさない。
  - §Step 3 §B-1 (b) と (a) の出力フォーマットの非対称性が spec で吸収されていない。
- 推奨対応 (**[方向性]**):
  - **a) ソルジャー側 status file の内容を §Step 8 戻り値フォーマット全体に拡張**: `echo` の代わりに `cat <<EOF` で TICKER / STATUS / DOCS_FOUND / ... の全フィールドを書く。HIGHLIGHTS / ERRORS は短縮可。これにより status 経由検知でも §Step 4 §B 解析が成立。
  - **b) status file 経由完了検知時は `JSON_PATH` 等を JSON ファイル存在確認 + ls で復元**: `[ -f /c/gdrive/claude/work/{ticker}.json ] && wc -c {ticker}.json` で `json_bytes` を取得、`docs_*` 系は JSON 内 `d[]` 要素数から推定。ただし `docs_found` は復元不能（BQ 再クエリが必要）。
  - **(a) の方が spec として簡潔**。ソルジャー §Step 7 の echo 1 行を 10 行程度に拡張するだけで済む。
  - **記載先**: `skills/orders_soldier.md` §Step 7 status file 書き出し節 + `skills/orders_commander.md` §Step 3 §B-1 (b) / §Step 4 §B（参照を整合）。

## 【改善提案】（可読性・保守性）

### #1 ソルジャー §禁止事項に hb / status 関連の項目が無い

- 箇所: `skills/orders_soldier.md:449-460`（§禁止事項）
- 現状: ハートビートと status file の重要性は §Step 5 §「ハートビート（必須）」L159 と §Step 7 で言及されているが、§禁止事項に明示的な項目が無い。
- 提案: §禁止事項に 2 項目追加:
  - 「**Step 5 の各 PDF 開始時の hb 更新を省略しない**（コマンダーから「ハング」誤判定で TaskStop される）」
  - 「**Step 7 の status file 書き出しを省略しない**（コマンダーが task-notification 配信に依存することになり、改修目的が達成できない）」
- 提出元の重点観点 §7「ソルジャー側禁止事項の強調十分性」への直接的回答。

### #2 新規イベント `hb_alive_check` / `status_detected` / `inflight_persist` の detail フォーマット未定義

- 箇所: `skills/orders_commander.md:103`（§出力ファイル §2 ログ TSV event 列定義）
- 現状: event 名は追加されているが、detail のキーバリュー仕様が無い（`run_start mode=build target_count=N` のような例示が他 event にはあるが、新規 3 種類は無い）。
- 提案: event 列定義表 or §Step 3/Step 4 で各イベントの detail フォーマットを明示:
  - `hb_alive_check inflight=N timeout_candidates=M`
  - `status_detected ticker=XXXX source=task_notification|status_file`（§Step 4 §C L350 で言及済みだが event 定義表に反映）
  - `inflight_persist event=resume_cleanup zombies=N` / `event=append task_id=YYY` / `event=remove task_id=YYY`

### #3 `find -mmin +30` の境界値と spec 宣言の不整合

- 箇所: `skills/orders_commander.md:269-272`（§Step 3 §C `find -mmin +30`）/ `skills/orders_commander.md:422`（§禁止事項 「30 分判定の境界値は `elapsed >= 30min` で TaskStop」）
- 現状: spec は `elapsed >= 30min` で TaskStop と宣言するが、`find -mmin +30` は GNU find 仕様で「mtime が 30 分より厳密に古い」（=`elapsed > 30min`）。1 分の境界ズレ。
- 提案: §Step 3 §C のコマンドを `find -mmin +29` に変更（=`elapsed >= 30min` 相当）するか、§禁止事項 L422 を `elapsed > 30min` に変更して一致させる。後者の方が `find` 仕様と素直に整合。

### #4 §Step 3 §B-4 の sleep 60 周期と §C ハング検知の関係が暗黙

- 箇所: `skills/orders_commander.md:261-263`（§Step 3 §B-4） / `skills/orders_commander.md:265-284`（§Step 3 §C）
- 現状: §B-4 は「最大 30 回（=30 分）繰り返す」、§C は「5 分毎（または fallback poll 中の周期内）」。両者の数値が混在し、「いつ §C に飛ぶか」が曖昧。
- 提案: §Step 3 §B のループ構造を擬似コードで明示:
  ```
  while pending or inflight:
    run_find_mmin_check()     # §C 必須実行（重大指摘 #1）
    if hung_detected: handle_hang() ; continue
    notify = poll_task_notification(timeout=10s)
    if notify: handle_completion(notify) ; refill() ; continue
    status_files = ls _status/*.status
    if status_files: handle_completion(status_files) ; refill() ; continue
    sleep 60
  ```
- これにより重大指摘 #1 と §B-4 の不明確性が解消する。

### #5 `parallel >= 10` 警告が §入力 §4 から参照のみで詳細追えない

- 箇所: `skills/orders_commander.md:36-37`（§入力 §4） / 各所の参照
- 現状: 206 改善 #8 で重複削減したが、参照先 §入力 §4 を読まないと「parallel_high 警告ログがどのフォーマットで何処に追記されるか」分からない。「§Step 2 の `run_start` ログ直後に `parallel_high parallel={N}` を 1 行 append」は記述ありだが、event=parallel_high の detail 仕様が §出力ファイル §2 の event 列定義に反映されていない。
- 提案: §出力ファイル §2 の event 列定義表に `parallel_high` を追加（detail = `parallel=N`）。

### #6 §Step 6 実装メモが「動的並列ループ」と「ハートビート方式」のミックスで構造化されていない

- 箇所: `skills/orders_commander.md:387-397`（§Step 6 実装メモ）
- 現状: ハートビート mtime チェック（L394）、30 分タイムアウト根拠（L395）、harness 配信不確実性（L397）が箇条書きで混在。
- 提案: サブセクション化:
  - **動的並列ループ管理**: inflight dict, recently_stopped, 補充ロジック
  - **完了検知 3 段階 fallback**: 優先順位、各経路の trigger
  - **ハートビート方式（ハング検知）**: `find -mmin +30`, 周期、TaskStop 失敗時 fallback
  - **harness 仕様依存事項**: task-notification 配信、Agent task 寿命、TaskStop 有効性

### #7 ソルジャー §Step 1 のディレクトリ作成 + hb 初回書き出しが完了する前に Step 2 BQ クエリが始まる場合のフロー

- 箇所: `skills/orders_soldier.md:46-67`（§Step 1）
- 現状: §Step 1 で hb 初回書き出し（L63-64）した後、§Step 2 BQ クエリへ進む。BQ クエリは数秒〜30 秒程度で終わるが、極端な遅延時に hb mtime チェックが先に走ると「mtime 30 分超じゃないので OK」になるはずだが、**Step 1 hb 書き出し前にコマンダー側で `find` が走ると hb ファイル自体が無い**。
- 提案: ソルジャー §Step 1 の hb 初回書き出しはコマンダー BG 起動直後の最初の 1 秒以内に実行される設計を明示。または、コマンダー側 §Step 3 §C の `find` 結果で「hb ファイル自体が存在しない ticker」を「起動直後 = 健全」として除外する条件を明示（既存 `find -name "{ticker}.hb"` は無いファイルを返さないので動作は OK だが、spec で明示すれば誤読防止）。

### #8 30 分タイムアウト根拠の数値（中央値 6.6 分・最長 12.4 分）の出典が曖昧

- 箇所: `skills/orders_commander.md:284`（§Step 3 §C 末尾） / `skills/orders_commander.md:395`（§Step 6 末尾）
- 現状: 「全 37 件実測（test_build + build 30 件）の所要時間中央値 6.6 分、最長 12.4 分」と書かれているが、出典が無い。実走ログのどのファイル・どの run か追跡不能。
- 提案: 出典を 1 行追加: 「（実測ログ: `C:/gdrive/claude/work/_index/orders_log.tsv` の 2026-05-18 build run 30 件 + test_build 10 件、soldier_invoke と soldier_finish の timestamp 差から算出）」。

### #9 ソルジャー §Step 7 のフロー順序が「PDF 削除 → status → hb 更新 → hb 削除」と複雑

- 箇所: `skills/orders_soldier.md:375-401`（§Step 7）
- 現状: 4 ステップを 3 つの ``` ``` ブロックで分割しており、フロー全体が一目で見えない。
- 提案: §Step 7 冒頭にフロー summary を追加: 「PDF 削除 → status 書き出し → hb 最終更新 → hb 削除（順序固定）」。

### #10 ログ TSV と CSV の文字コード・改行コードの記述位置がバラバラ

- 箇所: `skills/orders_commander.md:93-110`（CSV / log TSV / inflight TSV それぞれ）
- 現状: CSV は L93 で「UTF-8 / `\n` / `,`」、log TSV は L110 で「UTF-8 / `\t` / `\n`」、inflight TSV は記述が無い。
- 提案: inflight TSV にも同等の記述を追加（UTF-8 / `\t` / `\n` / ヘッダ 1 行）。3 ファイル統一フォーマットなら 1 か所にまとめる選択肢も。

## 【確認できなかった事項】

- **harness の Agent 完了通知配信の挙動**: 本改修は status file fallback で吸収する設計だが、(a) task-notification 経路が実際にどの程度の頻度で動くかは実走するまで不明。fallback 経路 (b) (c) だけで運用が破綻しないかは Phase 2 全銘柄実走テストで確認が必要。
- **`recently_stopped` 30 分の妥当性**: 30 分という値の根拠が spec に無い（30 分タイムアウトと同じ値だが、意味が違う）。Agent task が TaskStop 後にどれくらいの時間で実際に消えるかが harness 仕様依存で未確認。
- **`_status/{ticker}.status` ファイル削除の race**: コマンダー §Step 4 §C 末尾削除と、別 task の §Step 3 §B-1 (b) `ls _status/*.status` 実行が同時並列で起きる確率は低いが、Windows ファイルロック挙動次第で `ls` が古いリストを返す可能性。
- **Google Drive File Stream 上の mtime 同期遅延**: `_heartbeat/` `_status/` を `C:/gdrive/...` 配下に置く設計の影響。同期遅延は通常 1 秒未満だが、I/O 競合時に数秒〜数十秒の遅延が出る場合の影響は未検証。
- **Agent task のセッション跨ぎ生存**: 206 で残った未確認事項。本改修は inflight 永続化で zombie 検知を入れたが、harness が resume 時に Agent task を全部殺すかどうかは未確認。殺すなら zombie 検知は不要、生かすなら本改修の 永続化が活きる。

---

## 不備蓄積ログ追記内容（004-1）

本レビューの指摘は `docs/knowledges/tools/004-1_code_review_findings_log.md` に [CR-207] タグで追記済み（13 件）。

---

## 2 次対応記録（207 レビュー受領後）

**対応日**: 2026-05-18
**対応者**: Claude (メインエージェント)
**ユーザー判断**: 「納得感ある内容は対応」→ 全 15 件採用

### 重大指摘 5 件

- **#1 ハング検知 §C の発火経路死角化** → [採用] §Step 3 §B 各周回冒頭で必ず `find _heartbeat -mmin +30` を呼ぶよう仕様変更。死角を構造的に排除
- **#2 ソルジャー §Step 5 リトライ中 hb 更新欠如** → [採用] 「各 PDF Read 直前」→「各 PDF Read 試行直前（リトライ含む）」に明文化、リトライ前 hb 更新を必須化
- **#3 recently_stopped セットのメモリ揮発性** → [採用] `orders_recently_stopped.tsv` を新設、TaskStop 時 append、resume 時 30 分以内分のみ読み込み引継ぎ
- **#4 _heartbeat/ _status/ resume クリーンアップ欠如** → [採用] resume Step 2 で `rm -f _heartbeat/*.hb _status/*.status` 必須化、ログ `inflight_persist event=resume_cleanup_files` で記録
- **#5 status file 経由完了時の数値フィールド欠落** → [採用 大幅修正] ソルジャー Step 7 §2 で `STATUS=...` 1 行から **KEY=VALUE 全 7 フィールド**（TICKER/STATUS/DOCS_FOUND/DOCS_READ/DOCS_WITH_DATA/JSON_PATH/JSON_BYTES）に拡張。コマンダー §Step 4 §B で経路 (a) `KEY: VALUE` と経路 (b) `KEY=VALUE` の統一パース手順を明示

### 改善提案 10 件

- **#1 ソルジャー禁止事項強化** → [採用] hb/status 関連 3 項目追加（更新欠かさない、書き出し欠かさない、Step 7 処理順序を入れ替えない）
- **#2 新規イベント detail 仕様** → [採用] `hb_alive_check` / `status_detected` / `inflight_persist` / `parallel_high` の detail 形式を §ログ TSV 直下に明示
- **#3 find -mmin 境界値** → [採用] `-mmin +30` = 「mtime > 30 分前（30:00.001 以上）」を明示
- **#4 §B-4/§C 関係明示** → [採用] §B 各周回冒頭で §C を呼ぶ位置を明文化、ループ駆動の優先順位を整理
- **#5 parallel_high 定義** → [採用] §ログ TSV detail 形式で「`parallel >= 10` で append、境界値は `parallel >= 10`」を明示
- **#6 §Step 6 実装メモ構造化** → [採用] 状態管理 / 完了検知優先順位 / ハング検知 / フォーマット契約 / harness 配信不確実性 の 5 セクションに整理
- **#7 ソルジャー §Step 1 hb 初回タイミング** → [採用] ディレクトリ作成と同一 Bash 呼び出しの末尾で実行する形に変更（タイミング確定）
- **#8 30 分根拠出典** → [採用] §Step 3 §C 末尾 + §Step 6 に「実走 37 件中央値 6.6 分・最長 12.4 分・実走ログパス」を明記
- **#9 ソルジャー §Step 7 フロー順序** → [採用] PDF 削除 → status → hb 更新 → hb 削除 の順序を必須化、入れ替え禁止を §禁止事項に追加
- **#10 文字コード統一** → [採用] hb file: UTF-8 ASCII、status file: UTF-8 KEY=VALUE、改行 `
` を spec に明示

### 変更ファイル

- `skills/orders_soldier.md` — Step 1/5/7 + §禁止事項に追記
- `skills/orders_commander.md` — §Step 3 §B-C 大幅改訂、§Step 4 §B 拡張、§Step 6 整理、§出力ファイル §4 新設
- `.claude/commands/orders-{soldier,commander}.md` — 変更なし
- Dropbox 同期予定: `C:/Users/zonekun/Dropbox/stock/temp/orders_{soldier,commander}/`
