# TDnet 受注高・先行指標 抽出 オーケストレータ — 「受注高抽出コマンダー」（/orders-commander）

> **コードネーム**: 受注高抽出コマンダー（複数銘柄の進捗管理・動的並列起動の指揮官）
> 実働部隊は別スキル **受注高抽出ソルジャー**（`skills/orders_soldier.md` / `/orders-soldier`）。
> 本スキルはソルジャーを呼び出して結果を集約し、インデックス CSV を更新する役割のみを担う。
> ソルジャーの中身（PDF 読み・JSON 生成）には立ち入らない。

## モデル

| 役割 | モデル | 指定方法 |
|------|--------|---------|
| コマンダー（本スキル） | claude-sonnet-4-6 | 本スキルを実行するセッションのモデルで決まる（Sonnet で実行すること） |
| ソルジャー | claude-opus-4-7 | Agent ツール呼び出し時に `model="opus"` を明示指定 |

---

## 目的

TDnet で「受注高/受注残高」カテゴリに分類された全銘柄を対象に、
受注高抽出ソルジャーを動的並列で起動して順次処理し、
進捗・成否を 1 本のインデックス CSV に集約する。
クラッシュ後は `resume` モードで pending 銘柄から再開できる。

ハートビートファイル + status file 経由で完了検知・ハング検知を行うことで、
harness の Agent ツール完了通知配信が不確実な場合でも確実に動作する。

---

## 入力

| 項目 | 例 | 必須 | 説明 |
|------|-----|------|------|
| `mode` | `build` / `resume` | Yes | `build`: BQ から全件取り直してインデックス初期化（**既存ファイルを退避してから上書き**）。`resume`: 既存インデックスの `status=pending` のみ処理 |
| `parallel` | `3` | No | ソルジャー並列起動数（同時に動かす本数）。デフォルト `3`、上限なし（注意事項参照） |

### 引数解釈の手順（assistant が必ず辿る順序）

呼び出し prompt（スラッシュコマンド第二行以降 / Agent prompt 本文）に対し、以下を上から順に実行する:

1. **`key=value` 形式を優先採用**: prompt 内に `mode=X` / `parallel=Y` の表現があれば最優先で採用する
2. **位置引数フォールバック**: `key=value` が一つも見つからなければ、空白区切りの第 1 引数を `mode`、第 2 引数を `parallel` として解釈する
3. **`mode` 正規化と検証**: 取り出した値を小文字化 (`lower()`) + 前後 trim する。結果が `build` / `resume` のいずれでもなければ**エラー終了**（標準出力に `ERROR: invalid mode=...` を出して exit）
4. **`parallel` のデフォルト適用と検証**:
   - 未指定なら `3` を採用
   - 整数パースに失敗 or `< 1` ならエラー終了（`ERROR: invalid parallel=...`）
   - `>= 10` なら採用するが、Step 2 の `run_start` ログ直後に `parallel_high parallel={N}` を 1 行 append し、Claude Code harness の Agent ツール並列起動上限に達する可能性を画面にも 1 行警告する
5. **冒頭出力**: `🎯 [orders-commander] mode={mode} parallel={parallel}` を 1 行出力する（CLAUDE.md §8 規約）

### 呼び出し方の例

- スラッシュコマンド: `/orders-commander mode=build parallel=3`
- 位置引数: `/orders-commander build 3`
- Agent prompt 直書き: `skills/orders_commander.md の手順を mode=resume parallel=5 で実行せよ`

---

## 出力ファイル

すべて `C:/gdrive/claude/work/_index/` 配下（git 管理外）。
ハートビート / status file は別フォルダ `C:/gdrive/claude/work/_heartbeat/` `C:/gdrive/claude/work/_status/`（ソルジャー責任で作成）。

### 1. インデックス CSV — `orders_index.csv`

**1 銘柄 = 1 行**、`status` 列で 7 種類の状態を表現。

| 列 | 型 | 説明 |
|----|-----|------|
| `ticker` | string | 4 桁英数（先頭ゼロなし） |
| `status` | string | 下記 7 値のいずれか |
| `docs_found` | int | BQ で取得した DOC_ID 数（0-6） |
| `docs_read` | int | Read 成功した PDF 数 |
| `docs_with_data` | int | 数値抽出できた PDF 数 |
| `json_path` | string | 出力 JSON のフルパス。なければ空文字 |
| `json_bytes` | int | 出力 JSON のサイズ。なければ 0 |
| `reason` | string | `failed_*` 時のみ機械マップ可能な短い ID。下記「reason 列のドメイン」参照。それ以外は空文字 |
| `updated_at` | string | `YYYY-MM-DDTHH:MM:SS+09:00`（JST） |

**`status` の取りうる値**:

| status | 意味 |
|--------|------|
| `pending` | 未処理 / 処理中クラッシュ復帰待ち |
| `completed` | ソルジャー報告どおり完全成功 |
| `completed_partial` | 一部 PDF のみ抽出 |
| `failed_no_bq_records` | BQ に該当文書なし |
| `failed_no_gcs_files` | BQ にあるが GCS DL 全滅 |
| `failed_pdf_unreadable` | DL 成功するも全 PDF が Read 不可 |
| `failed_no_data` | Read 成功するも受注数値ゼロ（偽陽性銘柄） |

**`reason` 列のドメイン（status から `failed_` を除去した形に正規化）**:

| status | reason |
|--------|--------|
| `pending` / `completed` / `completed_partial` | `(空文字)` |
| `failed_no_bq_records` | `no_bq_records` |
| `failed_no_gcs_files` | `no_gcs_files` |
| `failed_pdf_unreadable` | `pdf_unreadable` |
| `failed_no_data` | `no_data` |

> **ソルジャー側 reason との整合**: ソルジャー §STATUS 一覧の reason 列は `no_data` ではなく `no_data_classified_as_false_positive` という長い文字列だが、コマンダーは集約時に **`failed_no_data` → `no_data` へ機械マップで吸収**する（Step 4 参照）。pandas で `lstrip('failed_')` 相当の単純フィルタが効くようにするため。ソルジャー側 reason 命名はコマンダーの責務外（変更しない）。

**ファイル仕様**: 文字コード = UTF-8（BOM なし）、改行 = `\n`、区切り = `,`、ヘッダ行あり、クォーティングは **`pandas.to_csv(quoting=csv.QUOTE_MINIMAL)` 相当**（カンマ・改行・ダブルクォートを含む値が将来発生しても安全側）。

### 2. ログ TSV — `orders_log.tsv`

append-only のイベントログ。**1 イベント = 1 行**。

| 列 | 例 |
|----|-----|
| `timestamp_jst` | `2026-05-18T19:42:01+09:00` |
| `ticker` | `7011` / `-`（全体イベントの場合） |
| `event` | `run_start` / `run_end` / `index_backup` / `parallel_high` / `soldier_invoke` / `soldier_finish` / `index_update` / `hb_alive_check` / `status_detected` / `inflight_persist` / `error` |
| `detail` | キーバリュー `k1=v1;k2=v2` 形式（タブ・改行を含まない短い文字列） |

`error` の `kind=` 内訳: `ticker_mismatch` / `soldier_response_invalid` / `soldier_hung_30min_timeout` / `taskstop_failed` / `invalid_number` / `docs_with_data_capped`

**新規イベントの detail 形式**:
- `soldier_finish`: `ticker=XXXX status=<6値> docs_found=A docs_read=B docs_with_data=C json_bytes=D tokens=Z duration_ms=W`（**tokens / duration_ms 必須**。task-notification の `<usage>` タグから抽出。task-notification 未到達で status file 経由完了の場合は `tokens=- duration_ms=-` を入れる。モデル間比較・性能分析に使用）
- `hb_alive_check`: `detail=checked_count=N hung_detected=M`（§B 各周回冒頭呼び出し時）
- `status_detected`: `detail=ticker=XXXX source=task_notification|status_file path=<file>` （§Step 4 §C で append）
- `inflight_persist`: `detail=event=resume_cleanup|resume_cleanup_files|... <key=value>...`（resume 起動時の zombie 検知や hb/status クリーンアップで使用）
- `parallel_high`: `detail=parallel=N`（`N >= 10` で append、境界値は `parallel >= 10`）

> **過去ログ互換性**: 過去ログ（初版バッチ並列方式時代）には `batch_start` / `batch_end` が残存する。新仕様ではこれらは生成しないが、ログ解析時は unknown event として無視可（過去 run の識別子として活用可）。

文字コード: UTF-8、区切り: `\t`、改行: `\n`、**ヘッダ行 1 行目**（書き出しタイミングは Step 1 参照）。

### 3. inflight 永続化 TSV — `orders_inflight.tsv`

動的並列で稼働中の task 一覧を**永続化**する（コマンダークラッシュ後の zombie 検知用）。

| 列 | 例 |
|----|-----|
| `task_id` | `a45df9a7eeae8ab2f` |
| `ticker` | `1444` |
| `invoked_at_jst` | `2026-05-18T15:30:01+09:00` |

ソルジャー起動時に append、完了 or タイムアウトで該当行を**全行書き直しで削除**（CSV と同じ原子置換方式）。

### 4. ブロックリスト CSV — `orders_blocklist.csv`

受注高/受注残高が**存在しないと確認済み**の ticker 一覧。ソルジャーを起動せずにスキップする。

| 列 | 例 |
|----|----|
| `ticker` | `2914` |
| `company_name` | `日本たばこ産業` |
| `industry_17` | `食品` |
| `industry_33` | `食料品` |
| `reason` | `食品・飲料は受注概念なし` |
| `added_at` | `2026-05-20T12:48:00+09:00` |

- `mode=build` 時: BQ 取得結果からブロックリスト ticker を除外し、インデックスに `status=failed_no_data / reason=blocklisted` で即書き込み（ソルジャー起動なし）
- `mode=resume` 時: `status=pending` の中にブロックリスト ticker があれば即 `failed_no_data / reason=blocklisted` に更新（ソルジャー起動なし）
- 追加・削除はユーザー手動。自動追加禁止（ソルジャーの判定はブロックリストに反映しない）

### 5. recently_stopped 永続化 TSV — `orders_recently_stopped.tsv`

TaskStop で強制終了した task の一覧を 30 分保持する。zombie 戻り値（停止後に遅れて返ってくる戻り値）を「期待通り破棄」として誤検知ログを出さないために使用。

| 列 | 例 |
|----|-----|
| `task_id` | `a45df9a7eeae8ab2f` |
| `ticker` | `1444` |
| `stopped_at_jst` | `2026-05-18T16:00:01+09:00` |

TaskStop 時に append、30 分経過した行は次回の参照タイミングで全行書き直し削除。
resume 時は前回 run の 30 分以内分を引き継いで読み込む（コマンダー再起動でも zombie 誤検知ログを出さない）。

---

## 実行手順

### Step 1: 事前ディレクトリ作成 + ログ初期化

```bash
mkdir -p /c/gdrive/claude/work/_index /c/gdrive/claude/work /c/tmp/tdnet_orders /c/gdrive/claude/work/_heartbeat /c/gdrive/claude/work/_status
```

PowerShell の場合:

```powershell
New-Item -ItemType Directory -Force -Path `
  "C:\gdrive\claude\work\_index","C:\gdrive\claude\work","C:\tmp\tdnet_orders","C:\gdrive\claude\work\_heartbeat","C:\gdrive\claude\work\_status" | Out-Null
```

**ログファイル初期化**:

- `C:/gdrive/claude/work/_index/orders_log.tsv` が**存在しない**、または**サイズ 0 バイト**の場合のみ、ヘッダ行 `timestamp_jst	ticker	event	detail\n` を書く
- 既存ファイル（サイズ > 0）があれば**ヘッダは追加しない**（resume での二重ヘッダ防止）

**inflight 永続ファイル初期化**:

- `C:/gdrive/claude/work/_index/orders_inflight.tsv` が**存在しない**、または**サイズ 0 バイト**の場合のみ、ヘッダ行 `task_id	ticker	invoked_at_jst\n` を書く
- 既存ファイルがあれば、resume 起動時にここから zombie 検知を行う（§Step 2 resume 参照）

**recently_stopped 永続ファイル初期化**:

- `C:/gdrive/claude/work/_index/orders_recently_stopped.tsv` が**存在しない**、または**サイズ 0 バイト**の場合のみ、ヘッダ行 `task_id	ticker	stopped_at_jst\n` を書く
- 既存ファイルがあれば、30 分以内のエントリのみメモリ上 `recently_stopped` セットに読み込み（resume 時 zombie 誤検知防止）

**ブロックリスト読み込み**:

- `C:/gdrive/claude/work/_index/orders_blocklist.csv` が存在すれば `ticker` 列を全件読み込み、メモリ上 `blocklist = set(ticker)` として保持
- 存在しない場合は空セット（ブロックリスト機能を無効化して動作継続）

### Step 2: モード別の初期化

#### `mode=build`

**A. 既存インデックス退避（CLAUDE.md §4.4 破壊的操作対策）**:

- `C:/gdrive/claude/work/_index/orders_index.csv` が既に存在する場合、上書き前にタイムスタンプ付きバックアップを取る:
  ```bash
  cp /c/gdrive/claude/work/_index/orders_index.csv \
     /c/gdrive/claude/work/_index/orders_index_YYYYMMDD_HHMMSS.csv.bak
  ```
- ログに `index_backup path=orders_index_YYYYMMDD_HHMMSS.csv.bak` を 1 行 append
- バックアップに失敗した場合は **build を中止**（既存インデックス保護優先）

**B. BQ で対象 ticker を全件取得**:

```sql
SELECT DISTINCT TICKER
FROM `gmailpj-357912.STOCK.TDNET_DOCUMENTS_ENHANCED`
WHERE EXISTS (SELECT 1 FROM UNNEST(SUB_CATEGORIES) AS sc WHERE sc = '受注高/受注残高')
  AND MAIN_CATEGORY IN ('決算短信', '決算説明資料', 'その他（未分類）', '受注高受注残高')
  AND AI_STATUS = 'completed'
  AND SUBMISSION_DATE BETWEEN '2022-01-01' AND CURRENT_DATE("Asia/Tokyo")
ORDER BY TICKER
```

**注意（ソルジャー側 §Step 2 と同期改修、include list 方式）**:
- **MAIN_CATEGORY include list**: 4 カテゴリのみホワイトリスト受け入れ（決算短信 / 決算説明資料 / その他（未分類） / 受注高受注残高）
- **SUB_CATEGORIES** に '受注高/受注残高' を含むことを必須
- 新カテゴリ出現時は自動除外、明示追加待ち（ユーザー指示）
- ソルジャー側 §Step 2 BQ クエリと**WHERE 条件を完全一致**させること。乖離すると「コマンダー pending に乗っていない ticker をソルジャー個別投入で処理」「コマンダー pending に乗っているがソルジャー側 BQ で 0 件 → failed_no_bq_records」等の副作用が発生する
- **CHUNK_INDEX IS NOT NULL は付けない**（2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する罠。ソルジャー側 §注意事項と同じ）

**B'. ブロックリスト適用**:

BQ 取得結果に対してブロックリストをフィルタリング:

1. BQ 取得 ticker のうち `blocklist` に含まれるものを分離
2. 分離した ticker はインデックスに `status=failed_no_data / reason=blocklisted` で即書き込み（ソルジャー起動なし）
3. ログに `blocklist_applied count=N tickers=A,B,...` を 1 行 append
4. 残りの ticker のみ `status=pending` としてインデックスに追加

**C. インデックス初期化**:

取得結果（ブロックリスト除外後）を `orders_index.csv` に書き出す（pending 分と blocklisted 分を混在）:

```
ticker,status,docs_found,docs_read,docs_with_data,json_path,json_bytes,reason,updated_at
1780,pending,0,0,0,,0,,2026-05-18T19:00:00+09:00
...
```

ログに `run_start mode=build target_count=N` を 1 行追記（§入力 §4 の通り `parallel >= 10` 時は `parallel_high parallel={N}` も追記）。

**D. ユーザー確認ゲート**:

退避ファイル名 + 退避前の `completed` / `failed_*` の合計件数（バックアップから集計） + 新規 pending 件数を**画面に 1 ブロック出力**してから Step 3 に進む。自動実行モード（無人実行）の場合もこの出力は必須（ログに残るため事後検証可能）。

**E. BQ 取得結果が 0 件のとき**:

`orders_index.csv` をヘッダ 1 行のみで書き出し、ログに `run_end target_count=0 reason=no_target` を append して**即終了**（Step 3 以降スキップ）。

#### `mode=resume`

`orders_index.csv` を読む。

- ファイルが無い → エラー終了（`ERROR: orders_index.csv not found, run mode=build first` を画面出力、exit）
- 存在 → `status=pending` の ticker を抽出し、**さらにブロックリストでフィルタリング**:
  - `status=pending` かつ `blocklist` に含まれる ticker → 即 `failed_no_data / reason=blocklisted` に更新（ソルジャー起動なし）
  - ログに `blocklist_applied count=N tickers=A,B,...` を 1 行 append
  - 残りの pending のみ処理対象

`failed_*` STATUS は**再投入しない**（仕様: failed 系スキップ）。再試行したい場合は
ユーザーが手動で `status=pending` に書き戻す前提。

**zombie 検知（前回 run の生存 task 確認）**:

- `orders_inflight.tsv` を読み、前回 run で稼働中だった task_id 一覧を取得
- 各 task_id を `TaskStop` 試行（既に消えていれば no-op）
- `orders_inflight.tsv` をヘッダのみに初期化
- ログに `inflight_persist event=resume_cleanup zombies=N detail=task_ids=A,B,C` を 1 行 append

**古い hb/status ファイルのクリーンアップ（必須）**:

前回 run で残った hb/status ファイルが残っていると、当 run で誤検知（status file を当 run の完了と誤認、古い hb を最新と誤認）する。resume 起動時に必ず削除:

```bash
rm -f /c/gdrive/claude/work/_heartbeat/*.hb /c/gdrive/claude/work/_status/*.status
```

ログに `inflight_persist event=resume_cleanup_files hb_removed=N status_removed=M` を append。

**recently_stopped 引継ぎ**:

`orders_recently_stopped.tsv` から、`stopped_at_jst` が「現在時刻 - 30 分」以降のエントリのみメモリ上 `recently_stopped` セットに読み込む。30 分超過分は同 TSV から削除（全行書き直し）。これにより前回 run の TaskStop 後 30 分以内に再 resume した場合の zombie 戻り値が誤検知されない。

ログに `run_start mode=resume target_count=N` を 1 行追記（§入力 §4 の通り `parallel >= 10` 時は `parallel_high parallel={N}` も追記）。

### Step 3: 動的並列ループ（ハートビート方式）

> **方針**: バッチ並列ではなく、常時 `parallel` 件稼働の**動的並列**。完了検知は **(a) task-notification + (b) status file 監視** の 2 経路。ハング検知は **ハートビート mtime 30 分超え** で判定。
>
> **重要前提**: harness の Agent ツール完了通知配信の確実性は未保証のため、status file 監視を fallback として採用する。

#### A. 初期投入（最初の `parallel` 件）

対象 ticker キューの先頭から `parallel` 件取り出し、Agent ツールで BG 起動:

```
Agent(subagent_type="general-purpose",
      model="opus",
      description="orders-soldier {ticker}",
      prompt="skills/orders_soldier.md の手順を ticker={ticker} で実行せよ。"
             "Step 8 の戻り値フォーマットに従って TICKER / STATUS / DOCS_FOUND / DOCS_READ / "
             "DOCS_WITH_DATA / JSON_PATH / JSON_BYTES / HIGHLIGHTS / ERRORS を必ず返せ。",
      run_in_background=true)
```

各起動について以下を実施:
1. ログ `soldier_invoke ticker=XXXX task_id=YYY` を append
2. `orders_inflight.tsv` に `YYY\tXXXX\tINVOKED_AT\n` を append（永続化）
3. メモリ上の `inflight = {task_id: (ticker, invoked_at)}` dict に追加
4. メモリ上の `recently_stopped = set()` を空で初期化（タイムアウト発火時に使用）

#### B. 完了検知ループ（task-notification + status file polling）

以下を pending キューが空 **かつ** `inflight` が空になるまで繰り返す:

**【各周回の最初に必ず実施】ハング検知トリガー（§C 呼び出し）**:

ループ各 iteration の**冒頭で必ず** §C の `find _heartbeat -mmin +30` を 1 回実行する。これにより「他 task が順調に完了通知を返している間も、hb mtime チェックが必ず走る」死角を排除する。`find` は数十ミリ秒で完了するため毎周回呼んでも overhead 無視可。

```bash
find /c/gdrive/claude/work/_heartbeat -name "*.hb" -mmin +30 -type f
```

(検出された ticker は §C のタイムアウト処理へ。検出ゼロなら次のステップへ。)

**【続けて】完了検知 + 処理 + 補充**:

1. **完了検知** — 以下のいずれかで完了を検出:
   - (a) **task-notification 受信**: 該当 task の output_file を Read → §Step 4 で解析
   - (b) **status file polling**: Bash で `ls /c/gdrive/claude/work/_status/*.status 2>/dev/null` を実行 → 新規ファイルがあれば**内容を Read で取得し KEY=VALUE をパース** → §Step 4 で解析
   - (c) **ハートビート切断**: 上記 §C 冒頭呼び出しで検出された task_id
2. **§Step 4 で 1 件処理** → orders_index.csv 1 件更新 + inflight からの削除 + ログ append
3. **inflight 件数 < `parallel`** かつ **pending キューに残あり**:
   - キュー先頭から `t` を取り出す
   - **inflight 重複チェック**: `inflight.values()` 内に ticker == `t` が存在すればスキップして次の pending を試す（同一ループ内で連続スキップ）
   - 重複でない `t` で §A の Agent BG 起動
   - 全 pending が inflight 重複でスキップになった場合、次の完了通知を待つ（補充せず）
4. **fallback polling 周期**: 完了通知が一切来ない場合に備え、Bash `sleep 60`（1 分待機、Bash 上限 10 分内）で待ってから次の周回に進む。`sleep` から戻ったら**周回冒頭の §C 呼び出しから再開**（hb チェックが必ず走る）

> ループ駆動の優先順位: **§C 冒頭呼び出し → task-notification > status file > 1 分 sleep**。`§C 冒頭呼び出し`はハング検知の死角を塞ぐための強制実行で、他 3 経路は完了通知のための fallback 階層。

#### C. ハング検知（ハートビート方式・30 分タイムアウト）

**§B 各周回冒頭で必ず呼び出される**（§B の「各周回の最初に必ず実施」参照）:

```bash
find /c/gdrive/claude/work/_heartbeat -name "*.hb" -mmin +30 -type f
```

**境界値**: `find -mmin +30` は **`mtime > 30 分前`**（=最終更新から 30 分超過、30:00.001 以上）を検出する。境界値の `mtime == 30 分前`（=30:00.000）は検出されない。実用上 1 分単位の精度で十分。

検出された各 `_heartbeat/{ticker}.hb` について、ticker は **ハング判定** → 以下を順次実行:

1. `TaskStop` ツールで当該 task を強制終了（inflight から task_id を取得）
2. **TaskStop 失敗時の fallback**: 例外 or no-op で失敗した場合、ログ `error ticker=XXXX kind=taskstop_failed task_id=YYY` を append。inflight からは強制削除し空きスロットを開ける（zombie Agent が後で戻り値を返す可能性は §Step 4 §A で吸収）
3. 当該 ticker を `pending` のまま据え置く（インデックス更新しない）
4. ログ `error ticker=XXXX kind=soldier_hung_30min_timeout task_id=YYY hb_mtime=ZZZ` を append
5. `_heartbeat/{ticker}.hb` を削除（mtime 古いまま残すと次回検知で再ループする）
6. `inflight` から削除 → `orders_inflight.tsv` も更新（全行書き直し原子置換）
7. **`recently_stopped` に追加**: メモリセット + `orders_recently_stopped.tsv` に `task_id\tticker\tNOW_JST\n` を append（zombie 戻り値の照合用、§Step 4 §A）
8. **JSONL サルベージはコマンダーの責務外**（spec ではコマンダー停止後に人手で `100_agent_stuck_recovery.md` 手順で実施）

> 30 分の根拠: 実走行データ（test_build 10 件 + build 30 件 = 計 37 ソルジャー）の所要時間中央値 6.6 分、最長 12.4 分。30 分はその 2.4 倍で十分マージン。これを超えるのはハング・context 上限到達の確度高い。30 分超で TaskStop すれば quota や context 浪費を防げる。実走ログ: `C:/gdrive/claude/work/_index/orders_log.tsv` の soldier_invoke→soldier_finish 差分集計。

#### D. 並列上限と注意

- ユーザー指定の `parallel` を尊重（上限なし）
- `parallel >= 10` 時の警告は §入力 §4 参照（重複記述なし）
- harness の Agent 並列起動上限（実用 1〜10、注意事項参照）

### Step 4: 完了通知 / status file ごとに 1 件単位でインデックス更新

#### A. TICKER 照合（最優先・誤マップ防止）

各完了通知の戻り値について、以下を**最初に**実行:

1. 戻り値テキストから `TICKER:` 行を抽出
2. 抽出値 `returned_ticker` が `inflight` 内の起動時 ticker と一致するかを検証（`task_id` 経由で期待 ticker を取得し照合）
3. **不一致 or `TICKER:` 行欠落**の場合:
   - 当該戻り値は**信頼せず破棄**
   - `inflight` の該当 task に対応する起動時 ticker は **CSV status は `pending` のまま放置**、**動的並列キューには再投入しない**（当 run では再投入せず、次回 `resume` で `status=pending` のまま拾われる。mismatch はソルジャー側のバグ可能性を含むためリトライで同じ結果になる確率高い）
   - ログに `error ticker=XXXX kind=ticker_mismatch returned=YYYY task_id=ZZZ` を append
   - `inflight` から削除 → `orders_inflight.tsv` も更新
4. **zombie 戻り値の許容**: `task_id` が `recently_stopped` に含まれていれば「期待通り破棄」として誤検知ログを出さず静かに無視
5. 一致した場合は §B 解析へ

#### B. ソルジャー戻り値の解析

**完了検知経路 (a) task-notification 受信時**:

ソルジャー戻り値の固定フォーマット（ソルジャー §Step 8）:

```
TICKER: 7011
STATUS: completed_partial
DOCS_FOUND: 6
DOCS_READ: 5
DOCS_WITH_DATA: 4
JSON_PATH: C:/gdrive/claude/work/7011.json
JSON_BYTES: 12345
HIGHLIGHTS:
- ...
ERRORS: ...
```

task-notification には `<usage><total_tokens>N</total_tokens><tool_uses>M</tool_uses><duration_ms>W</duration_ms></usage>` タグが含まれる。**`total_tokens` と `duration_ms` を必ず抽出して `soldier_finish` のログ detail に記録する**（§2 ログ TSV §`soldier_finish` 形式参照）。トークン量はモデル間比較・性能分析の primary key。

**完了検知経路 (b) status file polling 経由時**:

`_status/{ticker}.status` ファイルの内容を Read で取得（ソルジャー §Step 7 §2 で生成）:

```
TICKER=7011
STATUS=completed_partial
DOCS_FOUND=6
DOCS_READ=5
DOCS_WITH_DATA=4
JSON_PATH=C:/gdrive/claude/work/7011.json
JSON_BYTES=12345
```

経路 (a) と (b) でフォーマットが異なる点に注意:
- (a) は `KEY: VALUE`（コロン + スペース）、行頭インデント無し、`HIGHLIGHTS:` `ERRORS:` 含む
- (b) は `KEY=VALUE`（イコール）、7 フィールド固定、`HIGHLIGHTS` `ERRORS` なし

**統一パース手順**:
- (a) は `: ` を区切りに、(b) は `=` を区切りに分解
- どちらの経路でも以下の必須フィールドを取得: `TICKER` / `STATUS` / `DOCS_FOUND` / `DOCS_READ` / `DOCS_WITH_DATA` / `JSON_PATH` / `JSON_BYTES`
- `STATUS` 値は 6 種類（completed / completed_partial / failed_no_bq_records / failed_no_gcs_files / failed_pdf_unreadable / failed_no_data）のいずれかに一致しなければ「ソルジャー応答異常」として扱う（§D）
- `DOCS_FOUND` / `DOCS_READ` / `DOCS_WITH_DATA` / `JSON_BYTES` を int 化:
  - パース失敗（非数値）は `0` 扱い + ログ `error ticker=XXXX kind=invalid_number field=DOCS_FOUND raw="..."` を append
- `JSON_PATH` の値が `(none)` / 空文字 / `null` / 欠落のいずれか → `json_path` は空文字、`json_bytes` は `0`（`/` を含むかで構文的判定でも可）
- `reason` 列値は「§1 reason 列のドメイン」表のとおり `status` から機械マップ（コマンダー側で吸収）

**DOCS_WITH_DATA 二重キャップ防御**:

ソルジャー側 §Step 8 §DOCS_WITH_DATA 計算式（`len(set(d_elem['doc_id'] for d_elem in d))`）で `0 ≤ DOCS_WITH_DATA ≤ DOCS_READ ≤ DOCS_FOUND ≤ 6` を満たすはずだが、過去 incident（166A: DOCS_WITH_DATA=13）の再発防止のため、コマンダー側でも防御的にキャップする:

- `DOCS_WITH_DATA > DOCS_READ` を検出した場合: **`DOCS_WITH_DATA = DOCS_READ` にキャップ**して CSV 更新
- ログに `error ticker=XXXX kind=docs_with_data_capped raw_docs_with_data=N capped_to=M` を append（kind を `docs_with_data_capped` で識別）
- 当該 ticker の CSV `status` 列は変更せず、`docs_with_data` 列のみキャップ済値で上書き

**重複完了の回避**: 同一 ticker に対し (a) と (b) が両方届く可能性がある（task-notification と status file 出現の順序は不定）。`inflight` から削除済みの ticker に対する重複完了通知は単に**無視**（CSV 二重更新を防ぐ）。

#### C. インデックス更新（1 件単位 / 即時反映）

完了通知 1 件ごとに `orders_index.csv` を**全行読み込み → 該当 ticker 1 行を新 STATUS で上書き → 全行書き直し**する。

- 書き込みは `orders_index.csv.tmp` に書いて `mv` で原子置換（※詳細は §注意事項 §Windows mv の原子性 参照）
- 書き直し直前に `orders_index.csv.bak`（最新版）へコピー退避（二重バックアップ）
- 動的並列でも、同一 ticker を複数 task が触ることはない（spec §並行 agent 競合心配無用、ソルジャー §Step 1 参照）ため、衝突なし
- 書き込み頻度は増えるが（バッチN件 → 1件ずつ）、CSV 数百行の書き直しコストは小さい
- **復旧時の巻き戻し量**: クラッシュ時、最新 `.bak` か Step 2A 起動時 `_YYYYMMDD_HHMMSS.csv.bak` の二択。中間点バックアップは無い

ログに `index_update count=1 ticker=XXXX` を append。

完了検知後に `_status/{ticker}.status` ファイルが残っていれば削除（次回 resume の汚染防止）:

```bash
rm -f "/c/gdrive/claude/work/_status/{ticker}.status"
```

ログに `status_detected ticker=XXXX source=task_notification|status_file` を append（経路追跡用）。

#### D. ソルジャー応答異常時の処理

Agent 戻り値が次のいずれかの場合は **`status=pending` のまま据え置き** + 動的並列キューには再投入しない（§A の TICKER 不一致と同じ扱い）:
- `STATUS:` 行が無い／空／6 値以外
- 数値フィールド (`DOCS_FOUND` 等) がパース不可
- Agent ツール自体がエラーで戻った
- 30 分タイムアウトで `TaskStop` 強制終了（§C）

ログに `error ticker=XXXX kind=soldier_response_invalid raw_excerpt="..."`（最初 100 文字、ただし `gs://` / `key=` / `token=` / `Bearer ` を含む部分は `[REDACTED]` に置換）を append し、
§B 動的並列ループに戻って空きスロットに次の pending を投入する。**コマンダーは中断しない**。次回 `resume` 起動でこの ticker は自動的に再投入される。

### Step 5: 全件完了 → 最終レポート

全 ticker の処理が終わったら以下を出力:

```
=== orders-commander summary ===
MODE: {mode}
PARALLEL: {parallel}
TOTAL: {N}
COMPLETED: {a}
COMPLETED_PARTIAL: {b}
FAILED_NO_BQ_RECORDS: {c}
FAILED_NO_GCS_FILES: {d}
FAILED_PDF_UNREADABLE: {e}
FAILED_NO_DATA: {f}
TIMED_OUT: {h}    # 30 分タイムアウトで TaskStop された件数（error kind=soldier_hung_30min_timeout カウント）
PENDING_REMAINING: {g}    # TIMED_OUT 含む据え置き合計
INDEX_PATH: C:/gdrive/claude/work/_index/orders_index.csv
LOG_PATH:   C:/gdrive/claude/work/_index/orders_log.tsv
ELAPSED: {hh:mm:ss}
```

ログに `run_end total={N} completed={a} ... timed_out={h}` を append して終了。

### Step 6: 実装メモ（動的並列ループ）

> 参照頻度が高いヒントをここに集約。詳細は各 Step を参照。

**状態管理**:
- `inflight` dict は `{task_id: (ticker, invoked_at)}` 形式でメモリ保持 + `orders_inflight.tsv` で永続化（クラッシュ復旧時に再構築）
- `recently_stopped` は `set(task_id)` でメモリ保持 + `orders_recently_stopped.tsv` で永続化（TaskStop 後 30 分間、resume 跨ぎでも引継ぎ）
- `pending` キューは BQ 取得結果 or `orders_index.csv` の `status=pending` 行から構築（メモリのみ）

**完了検知の優先順位（§Step 3 §B）**:
1. §C 冒頭呼び出し（`find -mmin +30`、ハング検知の死角排除のため毎周回必ず実行）
2. task-notification 受信（harness 配信あれば最優先）
3. status file polling（`ls _status/*.status`、KEY=VALUE 全フィールドを Read で取得）
4. Bash `sleep 60` 後の再周回（能動 polling、`sleep` から戻ったら §C 冒頭から再開）

**ハング検知（§Step 3 §C）**:
- `find /c/gdrive/claude/work/_heartbeat -name "*.hb" -mmin +30 -type f` で 30 分超 hb を一括検出
- 30 分の根拠: 実走 37 件中央値 6.6 分・最長 12.4 分の 2.4 倍マージン
- 検出 → TaskStop → 失敗時 fallback（`taskstop_failed` ログ + inflight 強制削除）
- `recently_stopped` に追加（zombie 戻り値の照合用）

**フォーマット契約**:
- ソルジャー戻り値 (a) task-notification: `KEY: VALUE` 形式（コロン + スペース）
- ソルジャー戻り値 (b) status file: `KEY=VALUE` 形式（イコール）
- 必須フィールド: TICKER / STATUS / DOCS_FOUND / DOCS_READ / DOCS_WITH_DATA / JSON_PATH / JSON_BYTES

**harness 配信不確実性への対処**:
- task-notification 配信が確実に来る場合は (a) で即座に処理
- 来ない場合でも (b) status file polling と (d) sleep + 周回再開で fallback
- 改修目的（ハング検知の即時化・スループット改善）はどの経路でも保たれる

---

## STATUS 集計ルール

- `completed` と `completed_partial` は別カウント（合算しない）
- `TIMED_OUT` は `error kind=soldier_hung_30min_timeout` の件数（`PENDING_REMAINING` の内訳として表示）
- `PENDING_REMAINING > 0` は「ソルジャー応答異常で据え置き」を意味する。次回 `resume` で再試行可能

---

## 禁止事項

- **`failed_*` STATUS を勝手に再試行しない**（ユーザーが手動で `pending` に戻す前提）
- **`parallel` を勝手にキャップしない**（ユーザーが指定した数を尊重、ただし注意喚起は行う）
- **ソルジャー本体（PDF 読み・JSON 生成）を肩代わりしない**（必ず Agent ツール経由でソルジャー起動）
- **Python ワーカースクリプトで効率化しない**（並列実行は Claude Code の Agent ツール BG 起動で実現）
- **インデックス CSV を `append` で更新しない**（毎回全行書き直し + `mv` 原子置換）
- **既存 `orders_index.csv` を `mode=resume` で上書きしない**（読むだけ）
- **`mode=build` でバックアップを取らずに上書きしない**（Step 2A 必須）
- **`TICKER:` 照合をスキップして戻り値を信頼しない**（Step 4A 必須）
- **30 分タイムアウトを assistant 独自判断で延長したり省略したりしない**（spec §Step 3 §C 固定）。ただし harness 仕様の制約で 30 分判定が動かないことが判明した場合は、spec 改訂（禁止事項違反ではない）
- **同一 ticker を二重起動しない**（補充手順 §Step 3 §B-3 の inflight 重複チェックで担保）
- **`mode=build` と `mode=resume` を同時並行起動しない**（`orders_index.csv` への並行書き込みで破損する）
- **30 分判定の境界値は `elapsed >= 30min` で TaskStop**（明示）

---

## 注意事項

- BQ 接続: プロジェクト `gmailpj-357912`、テーブル `STOCK.TDNET_DOCUMENTS_ENHANCED`
- インデックス出力先: `C:/gdrive/claude/work/_index/`（git 管理外）
- ハートビート / status: `C:/gdrive/claude/work/_heartbeat/` `_status/`（ソルジャー責任で書き込み）
- ソルジャー出力先: `C:/gdrive/claude/work/{ticker}.json`（同じく git 管理外）
- 日時表記は **JST 固定** `YYYY-MM-DDTHH:MM:SS+09:00`（CLAUDE.md §日時ルール）
- Bash パス表記は Windows パス `C:\...` を直接使わず、フォワードスラッシュ `C:/...` または Unix 形式 `/c/...` を使う
- 実行時冒頭に `🎯 [orders-commander] mode={mode} parallel={parallel}` を 1 行出力
- ソルジャー戻り値の文言（HIGHLIGHTS 等）はインデックスには記録しない（ログにも記録しない。容量肥大防止）
- CSV クォーティング: `pandas.to_csv(quoting=csv.QUOTE_MINIMAL)` 相当を採用。将来 `reason` / 新規カラムに `,` / `\n` / `"` が混入しても破損しない
- **`parallel` の現実的上限**: Claude Code の Agent ツールは 1 メッセージ並列起動に harness 側上限（数十〜100 程度、未公開）がある。実用は **1〜10 を推奨**
- **動的補充の実装可能性は harness 仕様依存**: 「1 セッション中同時稼働 task 総数の上限」vs「1 メッセージで起動できる task 数の上限」のどちらかが効くかは未確認。前者なら動的補充で問題なし、後者だと最初の N 件以降の補充が拒否される可能性
- **Windows `mv` の原子性**: Git Bash の `mv` は NTFS 上で POSIX 原子置換を保証しない（CRT 実装依存）。書き直し時クラッシュで `orders_index.csv` が消失したら、最新 `.bak` または Step 2A の起動時 `_YYYYMMDD_HHMMSS.csv.bak` から復元する
- **ソルジャー側 reason 列との非対称**: コマンダーは §1 reason 列ドメイン表に従って `no_data` にマップする（pandas での機械フィルタを効かせるため）。ソルジャー側 reason 命名はコマンダーの責務外
- **JSONL サルベージ手順**: 30 分タイムアウト発火 + コマンダー停止後、`docs/knowledges/tools/100_agent_stuck_recovery.md` の手順を人手で適用すれば中間結果を復旧可能（実走で 1 件復旧実績あり）
- **CHUNK_INDEX IS NOT NULL の罠**: BQ クエリの WHERE 句に `CHUNK_INDEX IS NOT NULL` を**絶対に付けない**。2026-05-18 以前のロードは CHUNK_INDEX が全件 NULL のため、付けると過去データ全件を誤って除外する。本コマンダー §Step 2 §B BQ クエリも CHUNK_INDEX 条件は付けない設計（ソルジャー側 §Step 2 と同じ規約）
