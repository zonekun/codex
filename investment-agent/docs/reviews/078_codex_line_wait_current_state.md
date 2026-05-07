# 078: Codex LINE wait current-state code review

## Reviewed State

- Date: 2026-05-07 JST
- Branch: `codex/integration`
- Latest relevant commits:
  - `434883b codex: add dedicated line wait wrapper`
  - `a992712 codex: force utf8 line wait logs`
  - `5521d51 codex: block sends until line replies are processed`
- Runtime evidence:
  - `C:\tmp\codex_line_wait\active_gpt_wait.json`
  - `status: replied`
  - `processed: false`
  - `msg_id: 594`
  - `reply_text: 回答せよ`

## Findings

### P0-1. Background wait records replies but cannot wake Codex

`scripts/codex_line_wait.py` correctly writes `status: replied` and `reply_text_path` when a LINE reply arrives. However, nothing in Codex is triggered by that state transition. The Python process exits, but Codex does not receive a callback, task notification, or scheduled wake-up.

This means the implementation can receive the reply and still leave it unhandled until the user sends another normal chat message. That is exactly the observed failure pattern: the reply exists in state, but Codex has not acted on it.

Affected code:

- `scripts/codex_line_wait.py:199-222` waits and writes reply state, then exits.
- `docs/codex/notification-wait.md:70-77` still describes a manual "inspect later" workflow, not an automatic wake-up mechanism.

Expected behavior for LINE conversation mode is that a LINE reply becomes the next active conversation turn without requiring the user to complain in chat. Current Codex implementation does not satisfy that contract.

### P0-2. The design copied a Claude Code background-notification assumption that Codex does not have

The documentation says not to rely on background process completion automatically notifying Codex, but the overall loop still depends on Codex checking the state later. Claude Code has a task-notification flow; this Codex session does not.

Affected code/docs:

- `docs/codex/notification-wait.md:73-77` requires later inspection, but no process enforces that inspection.
- `scripts/codex_line_wait.py:142-160` blocks only the next send. It does not force handling of an already received reply.

The `processed=false` guard is useful as a safety rail, but it only fires when another `send-wait` is attempted. It does not make Codex answer the user.

### P1-1. State file is only one-slot and can obscure historical reply processing

`active_gpt_wait.json` stores only the current/latest wait. Reply text is written to a per-log reply file, but the active state is overwritten on the next send. If an operator bypasses the guard with `--replace` or clears state, the active view no longer shows pending context.

Affected code:

- `scripts/codex_line_wait.py:31`
- `scripts/codex_line_wait.py:178-196`
- `scripts/codex_line_wait.py:212-219`

A durable append-only event log would make review and recovery easier.

### P1-2. The wrapper imports private functions from shared `notify.py`

The Codex wrapper imports `_NTFY_BASE_URL`, `_gen_unique_msg_id`, `_load_ntfy_topic`, and `wait_ntfy_reply` from `notify.py`. That avoids modifying the shared module, but it couples Codex behavior to private internals.

Affected code:

- `scripts/codex_line_wait.py:28`

If `notify.py` changes internals for Claude Code, Codex can regress without a public contract changing.

### P2-1. Documentation still has stale wording from the old direct notify flow

The Codex doc now mandates `codex_line_wait.py`, but the background handoff section still starts with "If `notify.py ntfy --wait` is launched...". That wording is stale and can send future agents back to the wrong primitive.

Affected docs:

- `docs/codex/notification-wait.md:58-60`

## Recommended Fix Direction

Do not keep adding checks around the same background-wait model. The core model is wrong for Codex.

Use one of these designs:

1. Foreground blocking wait for LINE conversation turns in Codex, so the tool call returns the reply directly to the active assistant turn.
2. External supervisor that can actually wake/invoke Codex when `active_gpt_wait.json` transitions to `replied`.
3. Declare Codex LINE mode as screen-assisted only: replies are stored and must be processed on the next chat turn, with no claim of automatic bidirectional conversation.

Option 1 is the smallest honest fix inside the current Codex environment. Option 2 is stronger but requires an integration outside this repository. Option 3 is operationally honest but does not meet the original LINE conversation requirement.

---

# Claude code-reviewer 独立レビュー

- 日時: 2026-05-07 14:01 JST
- 対象: `scripts/codex_line_wait.py`, `docs/codex/notification-wait.md`
- パターン: 1 (新規コードレビュー) — Codex(GPT)実装を Claude code-reviewer が独立検証
- レビュアー: Claude (code-reviewer runbook)
- 参照: Codex セルフレビュー（本ファイル上半分の Findings）

---

## 【サマリー】

- 変更の要約: Codex 環境向けに ntfy 双方向通知の送信・返信待機・状態管理を CLI ラッパーとして実装。`notify.py` の共有関数を利用しつつ、Codex 固有のプロセス状態ファイル管理を追加
- 品質評価: **C** — コードの実装品質自体は堅実だが、アーキテクチャ上の根本問題（返信到着後にCodexを自動起動する手段がない）によりLINE会話モード契約を履行不能。加えてセキュリティ・堅牢性に複数の実バグあり
- 主要リスク:
  1. 返信受信後の自動復帰機構が存在しない（CLAUDE.md §ライン会話モード契約の根本的未達）
  2. 共有 `notify.py` のプライベート関数への直接依存によるカップリング崩壊リスク
  3. 状態ファイルの原子性保証が不完全（Windows 環境での `Path.replace()` の挙動）

---

## 【Codex セルフレビューの妥当性検証】

Codex の自己分析（P0-1, P0-2, P1-1, P1-2, P2-1）を独立検証した結果:

### P0-1 (BGプロセス終了後にCodexを起動する手段がない) — **妥当・同意**

核心を正確に捉えている。Claude Code では `task-notification` + `ScheduleWakeup` が双方向ループの心臓部であり、返信到着がエージェントの次ターンをトリガーする。Codex にはこの等価物が存在しない。

### P0-2 (Claude Code のBG通知前提を移植した設計不整合) — **妥当・同意**

ただし補足が必要: 問題はさらに深い。`notification-wait.md` の §Background Wait Handoff は「Codex が次にチャットメッセージを受けた時に状態を検査せよ」と書いているが、LINE会話モードの契約は「ユーザーの画面入力を待たない」（CLAUDE.md §ライン会話モード: 「ユーザーがスマホにいる前提」）。つまりユーザーは PC の chat 画面に来ない前提であり、この「次のchat時に検査」モデルは契約違反のワークアラウンドに過ぎない。

### P1-1 (シングルスロット状態ファイル) — **妥当だが優先度は適切**

P1 は妥当。append-only ログは理想だが、現状のシングルスロットでも `--replace` ガードが機能しており、致命度は P0 以下。

### P1-2 (プライベート関数インポート) — **妥当・下記 #3 で深掘り**

### P2-1 (notify.py 旧表現の残存) — **妥当**

---

## 【重大な指摘】（即修正）

### #1 LINE会話モード契約の構造的未達（アーキテクチャ問題）

- 箇所: `scripts/codex_line_wait.py:199-222` + `docs/codex/notification-wait.md:60-77`
- 事象: LINE 返信が `active_gpt_wait.json` に `status: replied` として書き込まれるが、Codex セッションに制御が戻らない。ユーザーが PC chat に戻るまでデッドロック
- トリガー: ユーザーがスマホからLINE返信するが、PCのCodex chatには何も入力しない（LINE会話モードの正常系）
- 影響: LINE会話モードが実質的に機能しない。CLAUDE.md §ライン会話モード「send_ntfy_and_wait() で返信受信 → 処理 → 結果を send_ntfy_and_wait() で送信」の同期ループ契約を満たせない
- 根拠: Python プロセスは返信受信後に exit 0 で終了し、stdout に reply を書く（L221）。しかし Codex はファイル変更やプロセス終了を契機に起動する仕組みがない
- 推奨対応: **Option 1 (FG ブロッキング) を採用し、Codex のツール呼び出しとして foreground で実行する**。ツール実行が返信テキストを直接返すため、Codex の次ターンに自然に接続される。BG + 状態ファイルモデルは破棄する

### #2 `_send_ntfy_with_id` が `notify.py` の `send_ntfy` と微妙に異なる挙動を持つ

- 箇所: `scripts/codex_line_wait.py:83-102`
- 事象: Codex ラッパーは独自に `_send_ntfy_with_id` を実装しているが、`notify.py` の `send_ntfy(with_id=True)` と以下が異なる:
  - `notify.py` は `_build_title()` で sender/task コンテキストを使う。Codex 版は `args.title` を直接使う
  - `notify.py` は URL を `_NTFY_BASE_URL` (= `"https://ntfy.sh"`) に POST する。Codex 版も同じだがトピック指定をJSON payload内の `topic` フィールドで行う（これ自体は正しいが、将来 `_NTFY_BASE_URL` がフルURLに変わった場合に壊れる）
- トリガー: `notify.py` の `_NTFY_BASE_URL` や送信ロジックが変更された場合
- 影響: 通知の表示形式の不整合、または送信失敗
- 根拠: L28 で `_NTFY_BASE_URL` をインポートしているが、L98 で直接 `_NTFY_BASE_URL` に POST しており、`notify.py` の `send_ntfy` 関数を呼ばずにロジックを複製している
- 推奨対応: `send_ntfy(message, title=title, with_id=True, ...)` を呼び、返り値の `msg_id` を取得する形に統一する。独自の HTTP 送信ロジックは削除する

### #3 プライベート関数依存による暗黙カップリング

- 箇所: `scripts/codex_line_wait.py:28`
- 事象: `_NTFY_BASE_URL`, `_gen_unique_msg_id`, `_load_ntfy_topic`, `wait_ntfy_reply` の4つをインポート。このうち `wait_ntfy_reply` のみが実質的に公開 API（CLI からも使われる）。残り3つはアンダースコア付きのプライベート実装詳細
- トリガー: `notify.py` のリファクタリング（ID生成戦略の変更、トピック解決方式の変更、URL構造の変更）
- 影響: Codex ラッパーが暗黙に壊れ、LINE送信が失敗する。Codex は Claude Code とは別リポジトリのため CI/テスト連動がなく、破損の検知が遅れる
- 根拠: `_` プレフィックス関数は Python の命名慣習上「内部実装・変更の予告なし」を意味する
- 推奨対応: `notify.py` に公開 API (`send_ntfy_with_id_and_wait` または既存 `send_ntfy_and_wait`) を使うか、必要な機能を `notify.py` に公開インターフェースとして追加する。#2 の修正と併せて、Codex ラッパーは `send_ntfy_and_wait()` のみをインポートすれば足りる

### #4 Windows での `Path.replace()` の非原子性

- 箇所: `scripts/codex_line_wait.py:64` (`tmp.replace(path)`)
- 事象: Windows 上で `Path.replace()` は宛先ファイルが存在する場合に `OSError` (Permission denied) を投げることがある。特に他プロセス（Codex の `status` サブコマンド）が同時にファイルを読んでいる場合
- トリガー: `cmd_status` (L106-117) と `cmd_send_wait` (L142-222) が同一の `active_gpt_wait.json` に対して同時実行される場合
- 影響: 状態ファイルの書き込み失敗 → 未処理例外 → プロセス crash → 返信が記録されない
- 根拠: Windows のファイルロック機構。`Path.replace()` は POSIX では atomic だが Windows では atomic でない
- 推奨対応: `os.replace()` を使い、try/except で `OSError` をキャッチし短い sleep + retry を入れる。または `msvcrt.locking` でファイルロックを取る

### #5 `os.environ[args.message_env]` の未チェック KeyError

- 箇所: `scripts/codex_line_wait.py:70`
- 事象: `--message-env` で環境変数名を指定した場合、その変数が未定義だと `KeyError` が送出される。`SystemExit` と異なり tracebackが出力され、状態ファイルには `send_failed` すら書かれない
- トリガー: 環境変数のタイポ or 未設定で `--message-env` を使用
- 影響: 状態ファイルが前回のまま残り、次回 `send-wait` が「既存の active wait あり」と誤判定して拒否する可能性
- 根拠: L168 の try/except は `_send_ntfy_with_id` の呼び出しのみをラップしており、L162 の `_read_message` は try の外で呼ばれる
- 推奨対応: `os.environ.get(args.message_env)` を使い、None の場合は `SystemExit` で統一的に終了する

### #6 `_is_pid_alive` の偽陽性

- 箇所: `scripts/codex_line_wait.py:39-45`
- 事象: `tasklist /FI "PID eq {pid}"` の出力に `f'"{pid}"'` が含まれるかで判定するが、PID が別プロセスに再利用された場合に偽陽性になる。また `tasklist` 自体が PID 不在時に `"情報: 指定された条件に一致するタスクは実行されていません。"` をエラー出力に書く（stdout ではなく stderr の場合もある — ロケール依存）
- トリガー: 長時間タイムアウト後に PID が別プロセスに再割り当てされた状態で `status` を確認
- 影響: 既に終了した wait プロセスが「生存中」と判定され、`--replace` なしでは新しい send-wait を開始できないデッドロック
- 根拠: Windows は PID を比較的早く再利用する（65536 個のプール）。3時間のタイムアウト中にプロセスが終了し PID が再利用される確率は無視できない
- 推奨対応: PID に加えてプロセス開始時刻（`creation_time`）を記録し、`wmic process where ProcessId={pid} get CreationDate` で照合する。あるいは #1 の修正（FG化）により PID 追跡自体が不要になる

---

## 【改善提案】（可読性・保守性）

### #1 `--sender` / `--task` 引数の欠落

- 箇所: `scripts/codex_line_wait.py:234-235`
- 現状: `--title` のデフォルトが `"GPT"` でハードコード。CLAUDE.md §用語ルール「`--sender GPT --task "<作業名>"` 必須」に従う場合、`set_ntfy_context` が呼ばれていないため `notify.py` 内部の `_build_title()` は使われない。`_send_ntfy_with_id` が独自に title を直接使っているため整合するが、将来 `send_ntfy` 呼び出しに統一した場合に title が「投資エージェント」にフォールバックする
- 提案: `--sender` / `--task` を argparse に追加し、`set_ntfy_context(sender, task)` を呼んでから送信する。これにより `notify.py` の公開 API に移行した際にも title が正しく生成される

### #2 ログファイルパスのハードコード

- 箇所: `scripts/codex_line_wait.py:31`
- 現状: `DEFAULT_STATE_PATH = Path(r"C:\tmp\codex_line_wait\active_gpt_wait.json")` がハードコード
- 提案: 環境変数 `CODEX_LINE_STATE_DIR` でオーバーライド可能にする。テスト時や別環境での実行が容易になる

### #3 docstring の欠落

- 箇所: `scripts/codex_line_wait.py` 全関数
- 現状: モジュール docstring はあるが、各関数・サブコマンドに docstring がない
- 提案: 少なくとも公開サブコマンド（`cmd_send_wait`, `cmd_status`, `cmd_clear_state`, `cmd_mark_processed`）に Google style docstring を追加する

### #4 structlog 未使用

- 箇所: `scripts/codex_line_wait.py` 全体
- 現状: `print(f"[codex-line] ...")` でロギング。プロジェクトのコーディング規約（CLAUDE.md §コーディング規約: print 禁止、structlog 使用）に違反
- 提案: `structlog` に移行する。ただし Codex リポジトリ側で `structlog` が依存に入っているか要確認

---

## 【Codex Recommended Fix Direction の評価】

### Option 1: FG ブロッキング — **推奨（最善案）**

Codex のツール呼び出しとして foreground で実行し、返信テキストをそのまま stdout に返す。Codex の次ターンにシームレスに接続される。

**利点**: 状態ファイル管理・PID 追跡・BG 検査ループがすべて不要になる。`send_ntfy_and_wait()` 1関数呼び出しで完結する。
**欠点**: Codex のシェルタイムアウトが返信待機時間以上であることが必要（`notification-wait.md` §Timeout Alignment で既に認識されている）。

**Claude code-reviewer の追加見解**: FG 化した場合、現在の `codex_line_wait.py` の大部分（状態ファイル管理・PID チェック・BG handoff ロジック）は不要になる。残すべきは:
- メッセージ送信 + 返信待機（`notify.py` の `send_ntfy_and_wait()` を直接呼ぶだけで足りる）
- タイムアウト時の exit code 制御
- stdout への返信テキスト出力

実質的に **20行程度のシンプルラッパー** に縮退できる。

### Option 2: 外部 Supervisor — **技術的に正しいが過剰**

ファイル変更監視 + Codex CLI 呼び出しの supervisor を別途構築する。

**利点**: BG で待てるためシェルタイムアウト制約がない。
**欠点**: Codex CLI の呼び出し方法が不明確。Codex はチャットインターフェースであり API 経由で任意のターンを注入する公開手段があるかが前提。実装コストが高い。

### Option 3: Screen-assisted モード宣言 — **正直だが契約不履行を公式化するだけ**

LINE 会話モードの「双方向自動ループ」を放棄し、「次のチャット入力時に返信を処理する」モデルとして再定義する。

**利点**: 現状の実装でそのまま動作する。
**欠点**: CLAUDE.md §ライン会話モードの契約（「ユーザーの画面入力を待たない」）を明示的に満たさない。ユーザーが Codex にも LINE 会話モードを求めている前提と矛盾。

### Claude code-reviewer の追加案: Option 4 — Hybrid (FG + timeout fallback)

FG 実行を基本とし、シェルタイムアウト上限を超える長時間待機が必要な場合のみ:
1. FG で `send_ntfy_and_wait(timeout=shell_max)` を実行
2. タイムアウトした場合、自動的にリトライメッセージ（「まだお待ちしています」）を送信して再度 FG wait
3. これを N 回まで繰り返す（`--max-retries` で制御）

これにより BG 監視の複雑さなしに長時間待機を実現できる。ただし Codex のシェル環境が「1ツール呼び出し = 1ターン」で FG ブロックを長時間許容するかは環境依存。

---

## 【確認できなかった事項】

- Codex のツール実行環境でシェルタイムアウトの上限値がいくつか（PowerShell `Start-Process` 経由ではなく直接実行の場合のタイムアウト制約）
- Codex が1つのツール呼び出し内で10800秒（3時間）ブロックすることを許容するか（セッション切断のリスク）
- `notify.py` の `wait_ntfy_reply` が ntfy.sh のストリーミング接続を60秒ごとに再接続するが、Windows のネットワークスタック上で3時間以上安定動作するかの実績
- Codex リポジトリ内の `pyproject.toml` / `requirements.txt` に `structlog` が含まれているか
- Codex の `codex/integration` ブランチの他のコミットが本ラッパーに影響を与えるか

