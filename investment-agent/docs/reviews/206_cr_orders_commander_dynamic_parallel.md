# 206_cr_orders_commander_dynamic_parallel

**提出日**: 2026-05-18
**提出者**: Claude (メインエージェント)
**レビュースキル**: code-reviewer
**レビューパターン**: 2（既存改修）

---

## レビュー対象ファイル

| パス | 役割 |
|------|------|
| `skills/orders_commander.md` | コマンダー spec（動的並列 + 30分タイムアウト対応版） |

参考情報（編集対象外、コンテキスト用）:
- `skills/orders_soldier.md` — ソルジャー本体（並行競合心配無用 + 棟/戸/件数等価カウントを追加済み、本レビュー対象外）
- `.claude/commands/orders-commander.md` — ラッパー（変更なし）
- `docs/reviews/205_cr_tdnet_orders_commander_skill.md` — 初版 spec のコードレビュー（重大5+改善7 全採用済み）
- `C:/gdrive/claude/work/_index/orders_index.csv` — test_build 10件 + build 30件 の実測結果
- `C:/gdrive/claude/work/_index/orders_log.tsv` — 実行ログ（バッチ A/B/C のハング事象記録）

---

## 事象・背景

### なぜ改修するか

初版コマンダー spec（バッチ並列方式）で test_build 10件 + build 30件を実走行した結果、以下 2 つの実害が明確化:

1. **バッチ最遅律速によるスループット劣化**
   - 短いソルジャー（2-3 分）と長いソルジャー（10-12 分）が混在
   - バッチ並列は「全 N 件完了待ち → 次 N 件」のため、毎バッチで最遅 ticker（12 分等）に律速
   - 30 件 8 並列で約 4 バッチ × 12 分 ≒ 50 分が理論最短だが、動的補充があれば 30 分前後に短縮できる

2. **ハング検知の遅延**
   - バッチC で 3 件（1444 / 1450 / 1452）が 2 時間以上ハング
   - メインコマンダーがバッチ完了を待つだけで、個別タスクのタイムアウト判定機構がなかった
   - 結果、ユーザーが LINE で「2 時間もかかっている」と気付くまで放置 → 手動 TaskStop で復旧
   - 30 分タイムアウト + 自動 TaskStop があれば即時復旧 + quota/context 浪費防止

### 改修方針

a. **バッチ並列 → 動的並列**: 常時 `parallel` 件稼働、1 件完了したら即 pending から 1 件投入
b. **30 分タイムアウト監視**: 各 inflight タスクの起動時刻を保持、30 分超で `TaskStop` + ticker は `pending` 据え置き
c. **インデックス更新を 1 件単位に変更**: バッチ完了時 N 件まとめて → 完了通知ごと即時 1 件更新
d. **ログイベント整理**: `batch_start` / `batch_end` 廃止、`error` の `kind=` を増やす（`soldier_hung_30min_timeout` 追加）

### 設計上の重要決定

1. **30 分タイムアウトは固定値**（実測中央値 6.6 分 / 最長 12.4 分の 2.4 倍。延長禁止を spec §禁止事項に明記）
2. **動的補充は同一 ticker の二重起動を禁止**（spec §禁止事項追記、ticker 単位で `inflight` 排他）
3. **JSONL サルベージはコマンダーの責務外**（30 分タイムアウト発火時は単に TaskStop + pending 据え置き。サルベージは別途 `100_agent_stuck_recovery.md` の手順を人手で実施）
4. **バッチ並列のメリット（書き込み回数低減）は放棄**（CSV 数百行の書き直しコストは小さい想定）

---

## レビュー観点

### 1. 動的並列の正しさ（race / 抜け漏れ）

- `inflight` dict（`{task_id: (ticker, invoked_at)}`）の管理が、完了通知到着順序に対して race を起こさないか
- pending キュー先頭から取り出すだけで、同一 ticker を二重起動するパスがないか
- pending キューが空になったあと、`inflight` 件数が 0 になるまで待つループの終了条件が正しいか
- 動的補充のループが、完了通知 1 件あたり「インデックス更新 + 次の起動」の順序で原子的に進むか

### 2. 30 分タイムアウトの確実な発火

- 完了通知駆動のループの中で、「30 分経過チェック」が確実に走るか（完了通知が来ないままハングし続けるケースで、タイマー監視が成立するか）
- spec §Step 3 §B の「定期チェック（5 分毎程度）」が assistant の実行モデルで実現可能か（自律的に時計を見る仕組み）
- TaskStop 失敗時のフォールバック（task-id 不正等）が記述されているか

### 3. インデックス CSV 書き込み頻度の影響

- バッチ並列時は 1 バッチ＝1 書き込みだったのが、動的並列では完了通知ごと（30 件で 30 書き込み）に増える
- ファイル I/O 増による Windows mv 原子性問題の悪化（spec §注意事項参照）はないか
- バックアップ `.bak` の上書き頻度増による「直前バックアップ」の意味が薄まらないか（誤更新時のロールバック粒度）

### 4. 既存運用との互換性

- `resume` モードで読む既存 `orders_index.csv` のフォーマット（列・status 値・reason 値）は変更なしか
- ログ TSV の event ドメイン変更（`batch_start`/`batch_end` 廃止）が、過去ログを解析する別ツールに影響しないか（今のところ別ツールは無いはずだが念のため）
- `mode=build` の初期化フロー（バックアップ・ユーザー確認ゲート・BQ 取得・全件 pending 初期化）は変更なしか

### 5. 禁止事項の網羅性

- 新規追加 2 項目（30 分タイムアウト不変・同一 ticker 二重起動禁止）が過不足ないか
- 削除した 1 項目（「複数バッチを同時に走らせない」）が、動的並列方式の前提と矛盾しないか
- ソルジャー側で追加された禁止事項（並行 agent 競合心配無用・棟/戸/件数等価カウント）と整合しているか

### 6. 引数解釈・冒頭出力の継続性

- 引数解釈手順（§入力 §「引数解釈の手順」）は変更なしか
- 冒頭出力 `🎯 [orders-commander] mode={mode} parallel={parallel}` は維持されているか
- ラッパー (`.claude/commands/orders-commander.md`) との委譲関係は変更なしか

---

## スコープ外（レビュー対象外）

- ソルジャー本体の今回追加改修（並行 agent 競合心配無用・棟/戸/件数等価カウント）— 別途検討
- 既存 30 件の build 結果データ品質
- BQ テーブルスキーマ・GCS バケット配置
- 089/042 知見 MD の内容
- LINE 会話モード関連の運用ルール

---

## レビュー結果記入欄

（code-reviewer から返却された指摘・提案を以下に追記）

---

# コードレビュー: 受注高抽出コマンダー 動的並列+30分タイムアウト改修（/orders-commander）

- 日時: 2026-05-18 (JST)
- 対象: `skills/orders_commander.md`（基準: HEAD 時点。初版 spec 205 採用後の動的並列+30分タイムアウト改訂版）
- パターン: 2（既存改修）
- レビュアー: Claude (code-reviewer runbook, sub-agent)

> **出力先の特記**: パターン 2 の標準は新規 `docs/reviews/NNN_cr_*.md` 作成だが、本件は提出元のユーザー指示により **既存 206_cr_orders_commander_dynamic_parallel.md の末尾追記**に変更（CLAUDE.md §4.2 ユーザー指示優先）。

---

## 【サマリー】

- 変更の要約: 初版 spec（バッチ並列：N 件同時起動 → 全完了待ち → 次 N 件）を **動的並列**（常時 `parallel` 件稼働、1 件完了で即 1 件補充）に置き換え、**30 分タイムアウト + 自動 TaskStop** を追加。インデックス CSV 書き込みは「バッチ完了時まとめて」から「完了通知ごと即時 1 件」に変更。ログイベントは `batch_start`/`batch_end` を廃止し `error kind=soldier_hung_30min_timeout` を追加。禁止事項に 2 件追加（30分タイムアウト不変・同一 ticker 二重起動禁止）、1 件削除（複数バッチ同時禁止）。
- 品質評価: **B−** — 改修の動機（バッチ最遅律速の解消・ハング検知の即時化）と方向性は妥当で、初版から大規模に書き換えたわりに整合は概ね保たれている。ただし **改修の中核機構である「完了通知駆動ループ」と「30 分定期チェック」が Claude Code harness で実現可能かが spec 内に根拠なく前提化されており**、ここが実は実現不能だと改修目的が達成できない（むしろ 2 時間放置事故より悪化し得る）。L373 で「task-notification は harness が自動配信、poll 不要」と断定しているが、Agent ツール完了が task-notification として配信される保証は文書化されていない（`068_line_ntfy_push.md` は Bash run_in_background 系の例のみ）。**ここが #1 として最重要**。
- 主要リスク:
  1. **完了通知駆動ループの実現可能性が未検証** — Agent ツールで起動した task の完了が `task-notification` として配信されるのか、それとも assistant が自前で polling する必要があるのかが spec で前提化されているが根拠なし。配信されない場合、ループは初期投入後の最初の N 件で停止する（最遅完了を待ったあと、補充が走らない）
  2. **30 分タイムアウトの「定期チェック（5 分毎程度）」を回す機構が無い** — assistant に内蔵タイマーはなく、定期的に時計を見るイベントソースは「完了通知到着 → 経過チェック」しかない。完了通知が一切来ないハング状態（=タイムアウト発火が一番必要な状況）では、何もトリガーされず無期限待機する。**改修の動機（2 時間放置の解消）がこの欠陥で逆に達成できない**
  3. **TaskStop ツールが Agent ツール起動 task に有効か未検証** — 既存知見 `100_agent_stuck_recovery.md` §「再発防止」で「Agent/Task ツールにタイムアウト機構なし（GitHub #49150 — closed as not planned）」と既に確認済み。TaskStop で Agent task を本当に止められるかの根拠が spec に無い

## 【改修プラン評価】

### 妥当性

- **方向性**: 改修の方向性（バッチ並列 → 動的並列、ハング検知の自動化）は正しい。バッチ最遅律速の問題は実走ログで実証されており（バッチC が 8 件中 5 件完了後も 3 件のハングで全体を 2h13m 引き止めた）、動的並列化は理論上理にかなう。
- **根本原因 vs 対症療法**: 30 分タイムアウト + 自動 TaskStop は「ハングを検知して止める」点では症状抑制的だが、ハング原因（PDF Read 無限ループ・context 上限到達・gsutil 詰まり）の本体はソルジャー側 spec で個別に対処済み（ソルジャー §Step 5 のリトライポリシー）。コマンダーが追加で打つ手は「打ち切り」しかないので、対症療法ではなくむしろ**観測限界に対する適切な fallback** と評価できる。
- **独立仮説の照合**: 改修前症状（バッチC で 2h13m 放置）を独立に分析すると、根本原因は「Agent ツールにタイムアウトが無いこと」(GitHub #49150)。これに対する正攻法はコマンダー側のループで時計を見て TaskStop すること。改修方針はこれに合致するが、**「ループが回るか」「TaskStop が効くか」の前提検証が抜けている**ため、方針は正しいが実装契約に穴がある。

### 副作用・デグレードチェック

- [ ] **インデックス CSV 書き込み頻度 8〜30 倍化**: バッチ並列時は最大 4 回 / 30 件（バッチ完了ごと）だったのが、動的並列では 30 回 / 30 件（完了通知ごと）。Windows mv 原子性は POSIX 保証なし（注意事項に既記載）、書き込み数が増えた分、書き込み中クラッシュで CSV 消失する確率も増える。**`.bak` も毎回上書きされる**ため、「直前 1 件分の状態」しか復元できなくなり、複数件の戻り値が同時期に来たケースで「2 件前の状態」に戻すには Step 2A の起動時 `_YYYYMMDD_HHMMSS.csv.bak` が必要。spec §Step 4C は「衝突なし」と断定するが、ファイル I/O 失敗時の復旧経路までは追えていない。
- [ ] **ログイベント `batch_start`/`batch_end` 廃止による過去ログ非互換**: 実走ログ `orders_log.tsv` には既に `batch_start batch_no=1 tickers=...` `batch_end batch_no=1` が約 8 件記録されている（11:36 までの初版バッチ並列実行ぶん）。spec はこれら旧イベントの**過去ログ互換性**について一切言及しておらず、将来 pandas で `event in {'batch_start','batch_end'}` を読む解析コードが書かれた場合、新旧ログ混在で behavior が変わる。**互換性の宣言（旧イベントは過去ログにのみ存在・新規生成しない）を spec §注意事項に明記すべき**。
- [ ] **動的補充時の Agent 起動 prompt が初期投入と同一かが暗黙**: §Step 3 §A は完全な prompt を明示するが §B-4 は「§A と同じ prompt」とだけ書き、`description` フィールド（`orders-soldier {ticker}`）の置換テンプレも暗黙。assistant が補充時にテンプレを変えてしまう余地がある。
- [ ] **削除した禁止事項「複数バッチを同時に走らせない」の喪失**: 動的並列方式と矛盾するので削除は正しいが、**「複数 mode（build と resume）の同時起動禁止」相当の禁止事項**は新 spec にも書かれていない。`mode=build` 中に別端末から `mode=resume` を撃つと、`orders_index.csv` への並行書き込みで破損する。CLAUDE.md §4.4 の dry-run 原則とも整合させるべき。
- [ ] **`inflight` dict 永続化の欠落（クラッシュ復旧不能）**: 旧バッチ並列は「バッチ完了時にインデックス全更新」のため、クラッシュ時の復旧は「inflight 完了分のみ部分反映 + 残りは next resume で pending」だった。新動的並列の `inflight` dict はメモリ上のみで、コマンダー本体がクラッシュすると「30 分タイマー監視中だった task_id 群」が全消滅する。次回 `resume` は inflight 内の ticker（`status=pending` のまま）を再度ソルジャー起動するが、**前回起動した zombie ソルジャー Agent はまだ生きているかもしれない**（Claude Code セッションを跨いで Agent が生存するかは未確認）。生きていた場合、同一 ticker の二重起動が発生し、`{ticker}.json` の上書きレースになる。

### 抜け漏れ（類似観点での横展開含む）

- [ ] **harness の Agent 並列起動上限と動的補充の整合性**: 注意事項 L370 で「実用は 1〜10 を推奨」と書かれているが、動的補充では「1 メッセージで N 件起動 + 1 件完了ごとに 1 件追加起動」となる。Claude Code の Agent ツールが「1 セッション中に同時稼働できる task の総数」と「1 メッセージで起動できる task の数」のどちらに上限があるかが不明。前者なら動的補充で問題ないが、後者だと最初の N 件以降は補充時に毎回拒否される可能性。**§注意事項に「動的補充の実装可能性は harness 仕様依存」を 1 行追加すべき**。
- [ ] **`inflight` 件数の上限管理が parallel と一致する保証なし**: §B-4 「`inflight` 件数 < `parallel` かつ pending キューに残あり」で補充するが、ticker_mismatch (§4A) で inflight から削除しても pending には戻さない（spec §4A 「`pending` のまま放置」と書いてあるが、`pending` キューに改めて投入する記述は無い）。結果、`inflight` から消えた ticker は二度と起動されず、次回 `resume` までスキップされる。**意図通りなら spec に「ticker_mismatch ケースは当 run では再投入せず、次回 resume で拾う」と明示すべき**。
- [ ] **30 分判定の境界値**: 「`invoked_at` から **30 分経過**」(L234) の比較演算子（`>= 30min` か `> 30min` か）が未定義。境界値（29:59 vs 30:00 vs 30:01）の挙動が assistant 実装依存。実害は薄いが、spec として `elapsed >= 30min → TaskStop` のように明示する方が安全。
- [ ] **TaskStop 失敗時の fallback**: §Step 3 §C で「`TaskStop` ツールで当該 task を強制終了」(L237) と書かれているが、TaskStop 自体が失敗した場合（task_id 不正・既に終了・harness エラー）の処理が無い。冒頭「重点的に見て欲しい観点」§2 で提出元自身が指摘している通り、**TaskStop 失敗ケースの fallback（inflight から削除して空きスロットを開ける・ログ `error kind=taskstop_failed` 等）を Step 3 §C に追加すべき**。
- [ ] **`mode=build` + 動的並列の重複ガード**: §禁止事項に「同一 ticker を二重起動しない（ticker 単位で `inflight` 排他）」(L356) が追加されたが、**実装方法（補充時に pending 取り出し後 inflight 内重複チェック）の手順記述が §Step 3 §B にない**。禁止事項として宣言するだけでなく、§B-4 の「キュー先頭から 1 件取り出し」直前に「取り出した ticker が inflight に存在しないことを確認、存在すればスキップして次の pending」相当の手順を入れるべき。現状の pending キューが build 時 distinct なら理論上重複は無いが、防御コードが無いので将来の re-injection 改修で破綻する。
- [ ] **動的補充で起動した Agent のログイベント `soldier_invoke` が「初期投入か補充か」を識別不能**: §A 初期投入と §B-4 補充の両方で `soldier_invoke ticker=XXXX` を append するため、旧 spec の `batch_no=1` のような「何回目の起動か」相当の識別子が消えた。実害は弱いが、後工程で「補充頻度分析」が必要になったときに detail から推定できない。`soldier_invoke ticker=XXXX wave=initial|refill` のような軽い識別子を入れる手もある（改善提案レベル）。
- [ ] **30 分タイムアウト発火後の JSONL サルベージ責務分離が緩い**: §Step 3 §C §4「JSONL サルベージは試みない（コマンダーの責務外）」(L239) と「サルベージは別途 `100_agent_stuck_recovery.md` の手順を人手で実施」が書かれているが、**100 知見 MD への導線が「手順を人手で実施」しか書かれておらず、誰がいつ気付くかの仕掛けが無い**。実走ログでサルベージ済み 1 件（13:54:44 `source=salvaged_from_jsonl`）の前例があるので、§Step 3 §C にせめて「LINE 等でユーザーに知らせる」「最終レポート §Step 5 に `timed_out_tickers=...` を出力する」相当のサルベージ依頼トリガーを入れた方がよい。

### 新規リスク

- **「ハング検知が遅延 2 時間 → 30 分」の改善が、本当に達成されない可能性**: 改修動機 2（ハング検知の即時化）は spec L41-42 で「30 分タイムアウト + 自動 TaskStop があれば即時復旧」と書かれているが、上記 #2 で指摘した通り **「定期チェック（5 分毎程度）」を回す機構が assistant にない**。最悪、新 spec で 30 分超えでも放置され、初版より状況が悪化する（旧版はバッチ完了時に必ず時計を見ていたが、新版は完了通知に依存）。**この点の harness 動作確認（task-notification が Agent 完了で来るか、来ないとき何が代替になるか）が改修取り込みの前に必要**。
- **CSV 書き込み 30 倍化による Windows mv 失敗時のロールバック範囲拡大**: 「直前 .bak」が完了通知 1 件ごとに上書きされるため、書き込み中クラッシュで CSV 消失したとき、最も近い復旧点は「30 件中 N−1 件目完了時点」（=ほぼ最新）か「Step 2A 起動時点」（=全 pending）の二択になる。旧 spec のバッチ完了時 .bak のような「中間点」は無い。**復旧時のデータ巻き戻し量が「1 件分」か「全件分」かの二極になる**点を spec §Step 4C か §注意事項に明示すべき。
- **`parallel` 上限による動的補充の頭打ち**: `parallel=8` で 30 件処理した実走ログを見ると、バッチC 完了が 11:37 → 13:50 と 2h13m。動的並列なら理論上 30 件 × 6.6 分 / 8 並列 = 約 25 分で済むはずだが、harness 側の「1 メッセージ並列上限」が 8 だった場合、動的補充は 1 件ごとに新規メッセージで起動 → assistant の往復回数が 30 回近く必要 → 各 1 件起動に assistant ターンが必要 → 結局 assistant の「待ち」が律速になる可能性。**改修効果（バッチ最遅律速の解消）が、別の律速（assistant ターン律速）に置き換わるだけのリスク**。

## 【重大な指摘】（即修正）

### #1 完了通知駆動ループの実現可能性が未検証（改修目的が達成できないリスク）

- 箇所: `skills/orders_commander.md:218`（Step 3 §B 「完了通知待ち（task-notification）」）/ `skills/orders_commander.md:373`（注意事項 §「動的並列の実装メモ」末尾「完了通知は harness が自動配信するので poll 不要」）
- 事象: spec は Agent ツールで `run_in_background=true` 起動した task の完了が **`task-notification` として assistant に自動配信される** ことを前提に動的並列ループを設計しているが、この前提に根拠が無い。`068_line_ntfy_push.md` で言及される task-notification は Bash の `send_ntfy_and_wait`（run_in_background）の例で、Agent ツールでも同じ機構が動くかは未確認。`100_agent_stuck_recovery.md` §「再発防止」では「Agent/Task ツールにタイムアウト機構なし（GitHub #49150）」「オーケストレータが無期限待機」と既に観測されており、Agent ツールの完了通知に何かしらの非対称性がある可能性が示唆されている。
- トリガー: コマンダー実行時、初期投入の `parallel` 件を Agent BG 起動した直後。assistant が「§B 完了通知待ち」に入ったまま、Agent 完了 task-notification が来ないと、assistant は次のアクションを取らない（poll 不要と spec に書かれているため）。
- 影響: 
  - **改修目的「バッチ最遅律速の解消」が達成できない**: 完了通知が来ないなら、assistant は最初の `parallel` 件が「自分で気付くまで」待つ。これは旧バッチ並列の「全完了待ち」と同等の挙動になり、改修効果ゼロ。
  - **30 分タイムアウトも発火しない**: §C のタイムアウト判定は §B のループ内（L222「30 分経過した起動中タスクがある場合」）で行われるため、ループが回らなければタイマー判定も走らない。**改修動機 2（2 時間放置事故の解消）が、新仕様で達成されないどころか「assistant が無期限待機」で悪化する可能性**。
  - 動的並列ループの全機能が成立しない。コマンダーは事実上「初期投入だけして hang」になる。
- 根拠: 
  - L218「完了通知待ち（task-notification）」「30 分経過チェック（定期）」を OR で並べているが、どちらも assistant の能動行動として実現する手段が明示されていない。
  - L373「完了通知は harness が自動配信するので poll 不要」が断定形だが、出典・検証ログ・参照知見 MD のいずれも提示されていない。
  - `100_agent_stuck_recovery.md` §「再発防止」(L92-94) で「Agent/Task ツールにタイムアウト機構なし」「オーケストレータが無期限待機」が既知。「無期限待機」は完了通知が来ないか/来ても処理されないことを示唆。
- 推奨対応 (**[方向性]**): 
  - **本改修を実走前に、harness の Agent ツール完了通知配信の挙動を最小再現テストで確認する**（test_build 2 件 1 並列で起動 → 完了通知が assistant に届くか）。届かなければ動的並列 spec 全体の再設計が必要（poll 方式に切り替えるか、Monitor ツールで擬似的にイベント化するか）。
  - 仮に動的並列方式を維持する場合、§Step 3 §B の冒頭に「**完了通知が一定時間（例: 5 分）来なかった場合、Bash で `ls C:/gdrive/claude/work/*.json` 等の出力ファイル存在確認で完了検知**」相当の **能動 poll fallback** を入れる。spec §禁止事項「Python ワーカーで効率化しない」(L350) と矛盾しないよう、poll は Bash の `ls` レベルに限定する。
  - **記載先**: harness 仕様確認結果は `docs/knowledges/tools/100_agent_stuck_recovery.md` か新規 `docs/knowledges/tools/NNN_agent_task_notification.md` に永続化する。spec L373 の注意事項にも参照リンクを張る。

### #2 30 分タイムアウト「定期チェック（5 分毎程度）」を回す機構が未定義

- 箇所: `skills/orders_commander.md:218`（Step 3 §B 「30 分経過チェック（定期）」）/ `skills/orders_commander.md:233-240`（Step 3 §C タイムアウト処理）/ `skills/orders_commander.md:373`（注意事項 §「動的並列の実装メモ」「30 分タイムアウト判定は定期チェック（5 分毎程度）で良い」）
- 事象: spec は「定期チェック（5 分毎程度）」と書かれているが、**assistant に内蔵タイマー・cron・schedule 機能はない**。assistant が「5 分経ったから時計を見る」と能動行動を取るトリガーは、外部からのメッセージ受信（task-notification か user message）しかない。指摘 #1 の通り task-notification が Agent 完了で来ない場合、§B のループは完了通知駆動でしか進まないため、**完了通知が一切来ないハング状態（=タイムアウト発火が最も必要な状況）では、30 分判定が永遠に走らない**。
- トリガー: 全 `parallel` 件のソルジャーが全部ハング（実走ログのバッチC 8 件中 3 件ハングのような状況の悪化版）。完了通知が一切来ない → ループが進まない → タイマー判定が走らない → 何時間でも放置される。
- 影響: 
  - 改修動機 2 で挙げた「ハング検知の遅延（2 時間放置事故）」が、新 spec で**完全に未解決**になる（むしろ無期限放置リスクで悪化）。
  - 「30 分タイムアウト + 自動 TaskStop」(L42) という spec の宣伝文句が実装契約として成立しない。
  - 提出元の「重点的に見て欲しい観点」§2「完了通知駆動ループ内でタイマーが回るか」がまさにこの点を疑っており、spec を読む限り**回らない**と読める。
- 根拠: 
  - L373 注意事項「30 分タイムアウト判定は定期チェック（5 分毎程度）で良い」は **assistant が能動的に時計を見る方法**を提示していない。
  - L218 §B の選択肢「完了通知待ち（task-notification）または 30 分経過チェック（定期）」も並列で書かれているが、後者を駆動する仕組みは明示されていない。
  - CLAUDE.md §5「監視」§「CronCreate」(L130-131 相当) などのスケジューラ系は使われていない。
- 推奨対応 (**[方向性]**): 
  - **a) Bash `sleep` + 起動時刻記録方式**: assistant が §B のループ各周回で `inflight` 内の `invoked_at` をチェックし、最古の invoked_at が「現在時刻 - 25 分」を超えていれば「次の完了通知を待たず 5 分の Bash `sleep 300` を入れて再チェック」を行う。`sleep` は Bash ツールで実装可能（run_in_background=false で 5 分待機 → 戻ったら時計再確認）。Bash の `timeout` 上限（10 分）内に収まるため実現可能。**ただし「完了通知が来ているのに 5 分待つ」非効率は受容する前提**。
  - **b) Monitor ツール活用**: Monitor は背景プロセスの stdout を行イベント化する。コマンダーが Bash で `while true; do date; sleep 300; done` のような定期 echo プロセスを別途起動し、Monitor 経由でイベント受信して assistant ループを駆動する。実装は複雑になるが「時計駆動」を実現できる。
  - **c) 妥協案: 30 分タイムアウト保証を「完了通知到着時のみチェック」に degrade**: spec L373 を「30 分タイマーは『他 task の完了通知が来た時に併せてチェック』する。全 task ハング時はタイマーが発火しないことを許容する。代わりに `parallel` が 1 のときの全 task ハングを禁止事項に追加」と書き換える。改修効果は減るが実装可能性は担保される。
  - **記載先**: a/b/c の選択結果と根拠を spec §Step 3 §B（実装ロジック）と §注意事項（実装メモ）両方に記載。harness 仕様の制約は #1 と統合して 100 知見 MD に永続化。

### #3 TaskStop ツールが Agent ツール起動 task に有効か未検証

- 箇所: `skills/orders_commander.md:236`（Step 3 §C-1 「`TaskStop` ツールで当該 task を強制終了」）
- 事象: spec §Step 3 §C-1 で「`TaskStop` ツールで当該 task を強制終了」と書かれているが、**Agent ツール起動 task（subagent_type=general-purpose）に対して TaskStop が有効に作用するかが未検証**。`100_agent_stuck_recovery.md` §「再発防止」(L92) で「Agent/Task ツールにタイムアウト機構なし（GitHub #49150 — closed as not planned）」と既知の通り、Agent ツールの寿命管理は harness が標準では提供していない。TaskStop は Bash `run_in_background` 起動の Bash task を止める用途で使われている。
- トリガー: §Step 3 §C 発火時（inflight task が 30 分経過 → TaskStop 呼び出し）。
- 影響: 
  - TaskStop が無効化（失敗 or no-op）の場合、zombie Agent task が背景で動き続け、`{ticker}.json` への上書き or 別 ticker の処理に context を食う可能性。
  - コマンダーは inflight から該当 task を削除して空きスロットに次の pending を投入するが、zombie が後から完了通知を返してくる可能性（時間差攻撃）。spec §4A の TICKER 照合で破棄されるが、ログには `ticker_mismatch` が出る。
  - 30 分タイムアウト機構が宣伝通りに動かない（#1 と #2 と合わせて、改修の中核 3 機構が全部前提未検証になる）。
- 根拠: 
  - L236「`TaskStop` ツールで当該 task を強制終了」が断定形だが、Agent ツール task に対する TaskStop の効果について検証ログ・知見 MD への参照なし。
  - 既存知見 `100_agent_stuck_recovery.md` で「Agent/Task ツール」を一括りにして「タイムアウト機構なし」と書かれており、TaskStop で本当に止まるなら矛盾する。
- 推奨対応 (**[方向性]**): 
  - **a) 最小再現テスト**: test_build 1 件 1 並列で Agent 起動 → 30 秒後に TaskStop → 該当 Agent が止まるか確認（output_file の更新停止 or jsonl の最終行で判定）。
  - **b) TaskStop 失敗時 fallback の spec 化**: §Step 3 §C-1a 相当のサブステップを追加し「TaskStop 失敗（例外 or no-op）時はログ `error ticker=XXXX kind=taskstop_failed task_id=YYY` を append、inflight からは削除（空きスロット開ける）、zombie Agent が後で戻り値を返す可能性を覚悟する。次の完了通知ループで zombie 戻り値が来たら TICKER 照合で破棄」。
  - **c) zombie 検出時の TICKER 照合強化**: §4A の TICKER 照合は「inflight 内の起動時 ticker と一致するか」を検証するが、zombie の場合「TaskStop 後に inflight から削除済みなので照合先がない」状況になる。**inflight から削除した task_id を一定時間（例: 30 分）保持する `recently_stopped` セット**を作り、zombie 戻り値の task_id が recently_stopped にあれば「期待通り破棄」と判定（誤検知ログを出さない）。
  - **記載先**: a の結果に応じて spec §Step 3 §C を改訂、知見は #1/#2 と同じ 100 知見 MD に統合。

### #4 動的補充時の「同一 ticker 二重起動禁止」が宣言のみで手順化されていない

- 箇所: `skills/orders_commander.md:356`（禁止事項「同一 ticker を二重起動しない」）/ `skills/orders_commander.md:225-228`（Step 3 §B-4 補充手順）
- 事象: 禁止事項 L356 で「同一 ticker を二重起動しない（ticker 単位で `inflight` 排他。動的補充時は pending キューから 1 件だけ取り出す）」と宣言されているが、**§Step 3 §B-4 の補充手順に「取り出した ticker が inflight に既に居ないことを確認する」ステップが書かれていない**。pending キューが build 時 distinct なら理論上同 ticker 重複は無いが、防御コードが無いと将来の re-injection 改修（ticker_mismatch リトライ・soldier_response_invalid リトライ等）で破綻する。
- トリガー: 将来 `ticker_mismatch` 等で「inflight から削除 + pending に戻す」改修を入れた瞬間、同 ticker が `inflight` と `pending` 両方に存在する状態が生まれ、補充時に重複起動が発生。
- 影響: 
  - 同一 ticker の 2 ソルジャーが並行で `{ticker}.json` を書き合うレース（ソルジャー側 §Step 1 で「ticker 単位で完全分離・競合心配無用」と書かれている前提が破綻）。
  - インデックス CSV の同 ticker 行が 2 回 update される → 最後勝ち（race）でどちらの結果が残るか非決定的。
  - 禁止事項の宣言が実装で守られない構造的欠陥。
- 根拠: 
  - L225-228 §B-4 は「キュー先頭から 1 件取り出し、Agent BG 起動 → `inflight` に追加 → ログ追記」だけで、ticker 重複チェックが無い。
  - L356 の禁止事項は「禁止する」と宣言するだけで、実装上どこで弾くかが指示されていない。
- 推奨対応 (**[方向性]**): 
  - §B-4 の冒頭に **`inflight` 重複チェックステップ**を追加: 「pending キュー先頭から `t` を取り出す。`inflight.values()` 内に ticker == `t` が存在すれば、当該 `t` はスキップして次の pending を試す（同一ループ内で連続スキップ）。全 pending が inflight 重複でスキップになった場合、次の完了通知を待つ（補充せず）」。
  - 禁止事項 L356 末尾に「（実装は §Step 3 §B-4 の `inflight` 重複チェックステップで担保）」と参照を入れる。
  - **記載先**: spec 内で完結（外部知見 MD への波及なし）。

### #5 ticker_mismatch ケースが「inflight から削除」のみで「pending に戻さない」ため、当 run で再投入されない（spec の動線不明）

- 箇所: `skills/orders_commander.md:258-263`（Step 4 §A-3 TICKER 不一致処理）
- 事象: §Step 4 §A-3 で「不一致 or `TICKER:` 行欠落の場合 → 戻り値を信頼せず破棄 → 起動時 ticker は `pending` のまま放置 → ログ append → `inflight` から削除」と書かれている。「`pending` のまま放置」が**インデックス CSV の status 値の話**（=pending のまま）なのか、**動的並列の pending キューの話**（=キューに戻すかどうか）なのか曖昧。`inflight` から削除しただけだと、pending キューには戻されないので**当 run では二度と起動されず**、次回 `resume` まで待つことになる。実害は薄いが、改修動機（動的補充で常時 N 件稼働）と整合しない（1 件 mismatch が出るたびに `inflight` の有効稼働数が 1 減る）。
- トリガー: ソルジャーが TICKER 不一致戻り値を返した場合（spec L139-141 で挙げられている 3 シナリオ）。
- 影響: 
  - `inflight` の実効並列度が徐々に下がる（mismatch のたびに 1 ずつ）。pending キューが空になっても inflight に「実態は終わってる空きスロット」が残り、ループ終了条件「pending キュー空 AND inflight 空」(L216) を満たすのが遅れる可能性。
  - 改修動機「常時 `parallel` 件稼働」が mismatch の度に空きスロット 1 増となるが、補充は §B-4「`inflight` 件数 < `parallel`」で発動するので、補充ロジックは動く。ただし「補充対象が空きスロット数 vs pending 残数」のどちらが少ないかで挙動が変わる。
  - spec の意図（次回 resume で拾う = ticker_mismatch は単発障害として打ち切り）か、当 run で再投入したいのかが曖昧で、後続改修時の判断材料が足りない。
- 根拠: 
  - L261「`inflight` の該当 task に対応する起動時 ticker は `pending` のまま放置」の「`pending` のまま」が CSV の status 列の話と読める文脈だが、明示はない。
  - 補充ロジック §B-4 は「キュー先頭から 1 件取り出し」のみで、mismatch で空いたスロットへの「mismatch だった ticker の再投入」は記述なし。
- 推奨対応 (**[方向性]**): 
  - §Step 4 §A-3 末尾に **意図の明示**を追加: 「mismatch 銘柄は当 run では再投入しない（次回 `resume` で `status=pending` のまま拾われる）。これは mismatch がソルジャー側のバグ可能性を含むため、リトライで同じ結果になる確率が高い前提」。
  - もし「当 run で再投入したい」設計に変える場合、pending キューに re-push する手順 + 連続 mismatch 上限（無限ループ防止）を仕様化する必要がある。**現状の spec は「再投入しない」前提に見えるので、それを明示すれば足りる**。
  - 同様の論点が「soldier_response_invalid」(§Step 4 §D) と「30 分タイムアウト」(§Step 3 §C) にも当てはまる。§Step 4 §D L311「次回 `resume` 起動でこの ticker は自動的に再投入される」は明示ありで OK だが、§A-3 と §C はこの注記が無く非対称。**3 つの「inflight 削除 → pending 据え置き」分岐の挙動を統一表現する**べき。
  - **記載先**: spec 内で完結。

## 【改善提案】（可読性・保守性）

### #1 旧ログイベント `batch_start` / `batch_end` との過去ログ互換性宣言が無い

- 箇所: `skills/orders_commander.md:99`（ログ TSV の event 列定義）
- 現状: event 列定義は「`run_start` / `run_end` / `index_backup` / `parallel_high` / `soldier_invoke` / `soldier_finish` / `index_update` / `error`」の 8 種類のみ。旧仕様の `batch_start` / `batch_end` が削除されているが、実走ログには既に 8 件以上記録されている（11:36 までの初版バッチ並列実行ぶん）。
- 提案: §注意事項に 1 行追加「過去ログには旧バッチ並列方式の `batch_start` / `batch_end` イベントが残存する。新仕様ではこれらは生成されないが、ログ解析時に許容すること（unknown event として無視 or 過去 run の識別に活用）」。または event 列定義表に「（廃止）`batch_start` / `batch_end` — 過去ログにのみ存在」の行を追加。

### #2 動的並列実装メモが §注意事項に紛れて見つけにくい

- 箇所: `skills/orders_commander.md:373`（注意事項末尾の「動的並列の実装メモ」「30 分タイムアウトの根拠」）
- 現状: 動的並列ループの実装ヒントと 30 分タイムアウトの根拠が §注意事項の末尾 2 行に詰め込まれている。実装で参照する頻度が高いセクションなので、§Step 3 直下の **「実装メモ」サブセクション**として分離した方が見つけやすい。
- 提案: §Step 3 の冒頭 or 末尾に「**実装メモ**」サブセクションを設け、L373-374 をそこに移動。§注意事項は「環境制約・既知の罠」だけに絞る。

### #3 §禁止事項 L355「30 分タイムアウトを延長したり省略したりしない」の例外規定が無い

- 箇所: `skills/orders_commander.md:355`
- 現状: 「30 分タイムアウトを延長したり省略したりしない」は厳格な禁止だが、harness 仕様で実現不能だった場合（重大指摘 #2 で挙げた状況）の **fallback パスが無い**ため、spec を遵守しようとして「永遠に待つ」結果になる。
- 提案: L355 を 2 文に分割し「30 分タイムアウトを **assistant 独自判断で延長したり省略したりしない**。ただし harness 仕様の制約で 30 分判定が動かないことが判明した場合は、§Step 3 §B の実装方式を改訂する（禁止事項違反ではなく spec 改訂を要する）」。

### #4 §Step 4C「全行書き直し + mv 原子置換」の Windows 環境注釈の重複

- 箇所: `skills/orders_commander.md:295`（§Step 4C 「Windows 環境では mv の原子性が POSIX 保証ではない」）/ `skills/orders_commander.md:371`（注意事項「Windows `mv` の原子性」）
- 現状: 同じ内容が §Step 4C と §注意事項の 2 か所に重複記載されている。
- 提案: §Step 4C は §注意事項を参照する形に集約（「※詳細は §注意事項 §Windows mv の原子性 参照」）。重複削減で spec 全体の読みやすさが向上。

### #5 「`inflight = { task_id: (ticker, invoked_at_jst), ... }`」の dict 構造が暗黙に「assistant の内部状態」として書かれている

- 箇所: `skills/orders_commander.md:212`（Step 3 §A 末尾「**起動時刻マップ**を内部保持」）/ `skills/orders_commander.md:373`（注意事項「`inflight` は `{task_id: (ticker, invoked_at)}` の dict で管理」）
- 現状: `inflight` dict はメモリ上の assistant 内部状態として扱われており、コマンダー本体が圧縮 or クラッシュした場合の **再構築手段が無い**。次回 `resume` で `inflight` を再構築するヒントが書かれていない（CSV の `status=pending` から「進行中だった ticker か未着手 ticker か」を区別できない）。
- 提案: §Step 3 §A に「`inflight` 起動時に **`orders_inflight.tsv`** に `task_id\tticker\tinvoked_at_jst\n` を append し、完了 or タイムアウト時に該当行を削除（または `removed_at` 列を追加）。これによりコマンダークラッシュ後の `resume` で zombie 検知・タイムアウト判定の継続が可能になる」相当の永続化を追加。コスト小・効果中。

### #6 `JSON_PATH:` の値解釈で「`(none)`」の文字列リテラルが暗黙

- 箇所: `skills/orders_commander.md:286`（Step 4B「`JSON_PATH: (none)` または欠落 → `json_path` は空文字、`json_bytes` は `0`」）
- 現状: ソルジャー側 spec を読むと §Step 8 戻り値フォーマット L369 で「`JSON_PATH: C:/gdrive/claude/work/{ticker}.json | (none)`」と書かれており、`(none)` 文字列リテラルが期待される。コマンダー側 L286 もこれを前提にしているが、**ソルジャーが将来 `JSON_PATH:` を空文字 or `null` 等の別表現で返した場合の挙動が不明**。
- 提案: L286 を「`JSON_PATH:` の値が `(none)` / 空文字 / `null` / 欠落のいずれか → `json_path` は空文字、`json_bytes` は `0`」と拡張。または「`/` を含むか否かで判定」のような構文的判定に変える。

### #7 §Step 5 最終レポートに `TIMED_OUT_TICKERS` 行が無い

- 箇所: `skills/orders_commander.md:318-332`（Step 5 最終レポート）
- 現状: 最終レポートは 8 カテゴリ + ELAPSED で集計されるが、30 分タイムアウトで TaskStop された ticker（`pending` 据え置き）は **`PENDING_REMAINING` に含まれる** だけで、「ハングが何件発生したか」が画面 1 ブロックでは見えない。実走ログ確認時に grep が必要。
- 提案: 最終レポートに `TIMED_OUT: {h}` 行を追加（`error kind=soldier_hung_30min_timeout` の count）。`PENDING_REMAINING` の内訳として表示すると、改修動機 2（ハング検知の即時化）の効果測定が画面 1 行で可能になる。

### #8 `parallel >= 10` の警告が引数解釈時とラン開始時の 2 か所で重複

- 箇所: `skills/orders_commander.md:34`（§入力 §4「`>= 10` なら採用するが、Step 2 の `run_start` ログ直後に `parallel_high parallel={N}` を 1 行 append し、Claude Code harness の Agent ツール並列起動上限に達する可能性を画面にも 1 行警告する」）/ `skills/orders_commander.md:171`（Step 2C 末尾「`parallel >= 10` の場合は直後に `parallel_high parallel={N}` も追記」）/ `skills/orders_commander.md:191`（Step 2 mode=resume 末尾「`parallel >= 10` の場合は直後に `parallel_high parallel={N}` も追記」）/ `skills/orders_commander.md:247`（Step 3 §D「`parallel >= 10` の場合は §引数解釈 4. の通り `parallel_high` 警告ログ」）
- 現状: 同じ「`parallel >= 10` で `parallel_high` ログ」が 4 か所で書かれている。1 か所で定義 + 残り 3 か所は参照、にできる。
- 提案: §入力 §4 を正本として残し、§Step 2C / §Step 2 mode=resume / §Step 3 §D は「※詳細は §入力 §4 参照」に置換。

## 【確認できなかった事項】

- **Claude Code harness の Agent ツール完了通知配信の有無**: 重大指摘 #1 の前提「Agent ツール起動 task の完了が task-notification として配信されるか」は harness 仕様に依存。本レビューでは実行禁止のため検証不能。実走テストが必要。
- **TaskStop ツールの Agent ツール task に対する有効性**: 重大指摘 #3 の前提。`100_agent_stuck_recovery.md` の記述からは「Agent/Task ツールにタイムアウト機構なし」と読めるが、TaskStop が能動的に止められるかは別問題。最小再現テストが必要。
- **Claude Code Agent ツールの「同時稼働 task 数上限」と「1 メッセージ並列起動上限」の区別**: 動的補充が harness 制約と整合するかが未確認（抜け漏れチェック §「harness の Agent 並列起動上限と動的補充の整合性」参照）。
- **Claude Code セッション跨ぎでの Agent task 生存**: 副作用チェック §「`inflight` dict 永続化の欠落」で挙げた zombie 問題の前提（コマンダー本体クラッシュ後の Agent task の生存）が未確認。harness が Agent を即殺すなら問題ないが、メインセッション復帰後も生きていれば二重起動リスクが顕在化する。
- **CSV 書き込み 30 倍化の I/O 失敗確率**: 副作用チェック §「インデックス CSV 書き込み頻度 8〜30 倍化」で挙げた問題の定量見積もりは未確認。Google Drive File Stream 経由の書き込みは I/O 失敗確率が高いが、`C:/gdrive/...` 配下の書き込み失敗統計は手元にない。

---


---

## レビュー対応記録（提出元）

**対応日**: 2026-05-18
**対応者**: Claude (メインエージェント)
**ユーザー判断**: ハートビート方式採用 + 他指摘は納得感あれば取り込み（assistant 判断）

### 重大指摘 5 件

- **#1 完了通知駆動ループの実現可能性未検証** → [採用: ハートビート方式] ソルジャー側に Step 1/5/7 で `_heartbeat/{ticker}.hb` 更新 + Step 7 で `_status/{ticker}.status` 書き出しを必須化。コマンダーは完了検知を **(a) task-notification → (b) status file polling → (c) Bash sleep 60 + ls 再 poll** の 3 段階 fallback で実装。harness 配信仕様に依存しない設計に転換
- **#2 30 分タイムアウト「定期チェック」機構未定義** → [採用] Bash `find _heartbeat -mmin +30` で能動チェック。fallback poll の各周回（sleep 60）で呼び出すことで「完了通知が来ないハング状態」でも能動的に時計判定が走る
- **#3 TaskStop が Agent task に有効か未検証** → [採用: 失敗時 fallback 明記] Step 3 §C-2 に TaskStop 失敗時 fallback（ログ `error kind=taskstop_failed`、inflight 強制削除、zombie 戻り値は §Step 4 §A の `recently_stopped` セットで吸収）を spec 化
- **#4 同一 ticker 二重起動禁止が手順化されていない** → [採用] Step 3 §B-3 に inflight 重複チェックステップ追加（補充時に `inflight.values()` 内重複を確認、重複なら次の pending を試す）
- **#5 ticker_mismatch 動線曖昧** → [採用] Step 4 §A-3 で「CSV status は pending、動的並列キューには再投入しない、次回 resume で拾う」と意図明示。soldier_response_invalid (§D) と整合

### 改善提案 8 件

- **#1 旧ログイベント互換性宣言不在** → [採用] ログ TSV §event 列定義の下に「過去ログには `batch_start`/`batch_end` が残存、unknown event として無視可」を明記
- **#2 動的並列実装メモが §注意事項に紛れて見つけにくい** → [採用] Step 6「実装メモ（動的並列ループ）」サブセクションを Step 直下に新設、関連ヒントを集約
- **#3 30 分禁止の fallback 規定不在** → [採用] §禁止事項を「assistant 独自判断で延長/省略しない。ただし harness 仕様で 30 分判定が動かないと判明すれば spec 改訂を要する（禁止事項違反ではない）」と 2 文に分割
- **#4 Windows mv 注釈の重複** → [採用] Step 4C は §注意事項を参照する形に集約（重複削減）
- **#5 inflight 永続化不在** → [採用] `orders_inflight.tsv` を新設（`task_id	ticker	invoked_at_jst`）。起動時 append、完了/タイムアウトで削除。resume 起動時に zombie 検知（TaskStop 試行 + ヘッダ初期化）
- **#6 JSON_PATH 値解釈の硬さ** → [採用] Step 4B で「`(none)` / 空文字 / `null` / 欠落のいずれか → 空文字扱い」と拡張
- **#7 最終レポートに TIMED_OUT 行不在** → [採用] Step 5 最終レポートに `TIMED_OUT: {h}` 行追加（`PENDING_REMAINING` の内訳）
- **#8 parallel>=10 警告 4 箇所重複** → [採用] §入力 §4 を正本とし、Step 2C / Step 2 mode=resume / Step 3 §D は参照に置換

### 確認できなかった事項 5 件

spec 内対処済み（harness 実走験が必要なものは fallback で安全側に倒した）:
- A. Agent ツール完了通知配信の有無 → status file polling + Bash sleep poll の 3 段階 fallback で吸収（§Step 3 §B）
- B. TaskStop の Agent task 有効性 → 失敗時 fallback 明記（§Step 3 §C-2）+ zombie 戻り値の `recently_stopped` セット吸収（§Step 4 §A-4）
- C. 同時稼働 task 上限 vs 1 メッセージ並列起動上限 → §注意事項に「動的補充の実装可能性は harness 仕様依存」記載
- D. セッション跨ぎでの Agent task 生存 → `orders_inflight.tsv` 永続化 + resume 時 zombie 検知（§Step 2 resume）
- E. CSV 書き込み 30 倍化の I/O 失敗確率 → 中間点バックアップは無い旨を §Step 4C に明記（最新 .bak or 起動時 .bak の二択）

### 変更ファイル

- `skills/orders_commander.md` — 全面改訂（441行）
- `skills/orders_soldier.md` — Step 1/5/7 にハートビート + status file 追加（471行）
- `.claude/commands/orders-commander.md` — 変更なし
- Dropbox 同期: `C:/Users/zonekun/Dropbox/stock/temp/orders_{soldier,commander}/` に最新版転送予定
