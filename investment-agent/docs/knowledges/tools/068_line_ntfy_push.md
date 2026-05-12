# LINE通知（ntfy プッシュ通知）

**カテゴリ**: tools
**作成日**: 2026-04-07
**ステータス**: 有効
**関連ファイル**: `scripts/notify.py`
**通称**: LINE通知 / ライン / LINE / ライン会話（双方向ループ対話モード）

## 概要

ntfy.sh を利用したスマホプッシュ通知。処理完了・ジョブ結果などを自分のスマホに即時通知する。
Cloud Run Job のメール通知（`send_mail`）とは独立した、**ローカル Windows 専用**の自己通知手段。

## ⚠️ 最重要: 単方向通知 と 双方向通知 の使い分け

**用途によって呼ぶ関数・CLI オプションが完全に別**。間違えるとユーザー返信を永遠に待つ／返信が拾えない等の事故になる。

| 用途 | Python 関数 | CLI | 挙動 |
|------|-------------|-----|------|
| **①単に連絡するだけ**（完了通知・失敗通知など、返信不要） | `send_ntfy(message)` | `notify.py ntfy "..."` | 送信して即終了。IDなし |
| **②ユーザー判断を仰ぐ**（yes/no・続行/停止などの返信がほしい） | `send_ntfy_and_wait(message, timeout=...)` | `notify.py ntfy "..." --wait --timeout 1800` | 3桁IDを付与して送信 → 同じIDで始まるリプライを待つ（ブロッキング） |

**判定基準**: 「送ったあと何か返信を受け取る必要があるか？」
- No → `send_ntfy`（①）。完了通知・エラー通知・進捗通知は全部こっち
- Yes → `send_ntfy_and_wait`（②）。必ず `timeout` を指定する

**ありがちな間違い**:
- ❌ 連絡だけしたいのに `send_ntfy_and_wait` を呼んでしまい、スクリプトが `timeout` 秒ずっと止まる
- ❌ ユーザー判断を仰ぎたいのに `send_ntfy` を呼んでしまい、返信が来ても誰も拾わない
- ❌ `send_ntfy_and_wait` を使うのにユーザーに「IDを1行目にコピペ」ルールを伝え忘れ、返信が永遠に一致しない

## セットアップ

1. スマホに ntfy アプリをインストール（iOS / Android）
2. トピック名を購読（`.env` の `NTFY_TOPIC` と一致させる）

## 使い方

## ① 単方向通知（連絡するだけ / 返信不要）

完了・失敗・進捗などの一方向の連絡。**リプライを待たないので、呼び出し元はすぐ次の処理に進む**。

### CLI から

```bash
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "月次データロード完了" --sender ATP --task "月次DL"

# タイトル明示指定（--title はsender/taskより優先）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "緊急: ジョブ失敗" --title "エラー" --priority urgent
```

> `--wait` を**付けない**こと。付けるとリプライ待ちモード（②）に入り、コマンドが `--timeout` 秒止まる。
> `--sender` は**毎回必須**（§送信元コンテキスト参照）。`--title` 明示時のみ省略可。

### Python スクリプトから

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_ntfy

# 基本
send_ntfy("edinet-load 完了: 125件処理")

# タイトル・優先度を指定
send_ntfy("ジョブ失敗", title="エラー", priority="urgent", tags="x")
```

> `send_ntfy_and_wait` を**使わない**こと。連絡のみなら必ず `send_ntfy`。

## ② 双方向通知（ユーザー判断を仰ぐ / リプライを待つ）

エラー発生時など、ユーザーの判断をスクリプトに反映したい場合に使う。
送信時に **数字3桁のメッセージID** が先頭に自動付与される。
ユーザーは ntfy アプリから **ID をコピペして自由文で返信** する。

> **呼び出し元への注意**: `send_ntfy_and_wait` は返信が来るか `timeout` 秒経過するまで**ブロックする**。必ず `timeout` を明示的に指定し、タイムアウト時のフォールバック動作を書くこと。

### CLI から

> **`--timeout` 指定時は `--wait` が自動有効化される**（MR-136対策）。`--timeout` を指定する意図は「待ちたい」ことなので、`--wait` 漏れによる単方向送信を構造的に防止する。`--wait` の明示指定も引き続き有効。

```bash
# --wait でリプライ待ちモードに入る（デフォルト3600秒待機）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy \
    "銘柄 7203 を再試行しますか？ (yes/skip/abort)" --wait --timeout 1800 --sender ATP --task "NG調査"

# 標準出力にリプライ本文が出る。タイムアウト時は exit code 2
```

送信メッセージ例（1行目ID / 2行目以降本文）:
```
472
銘柄 7203 を再試行しますか？ (yes/skip/abort)
```
ユーザー返信例（1行目にIDをコピペ、2行目以降に指示）:
```
472
yes
```
→ スクリプトは `yes` を受信。

### Python スクリプトから

```python
from notify import send_ntfy_and_wait

msg_id, reply = send_ntfy_and_wait(
    "NG銘柄 7203 検出。continue / stop どちら？",
    timeout=1800,  # 30分待つ
)
if reply is None:
    # タイムアウト → デフォルト挙動で続行
    logger.warning("user reply timeout, defaulting to stop")
    sys.exit(1)
elif reply.lower().startswith("c"):
    ...  # 続行
else:
    ...  # 停止
```

### 仕様

- `send_ntfy(with_id=True)` は ID を返しつつメッセージ先頭を `NNN\n<本文>` 形式にして送信
- `wait_ntfy_reply(msg_id, timeout, own_body_rest=...)` は `GET /<topic>/json?since=<t>` のストリームを購読し、**先頭が `NNN` で始まる**メッセージを受信
- 送信エコーは `own_body_rest`（送信した本文）との**完全一致比較**で除外（再接続時も安全）
- ユーザーは ntfy アプリから返信する際、**1行目に `NNN` だけ書き、2行目以降に指示を書く**（`NNN 指示` のスペース区切り1行も可）
- ID は数字3桁（0-999 の範囲でランダム）
- **衝突回避**: 送信直前に ntfy トピックの**直近30分の履歴を poll** し、既に使用中の3桁IDを避けて生成する（`_gen_unique_msg_id`）
- **有効期限**: 30分より前のIDは再利用可（= 実質の TTL）。衝突チェックは best-effort で、ネットワーク失敗時は単純ランダム生成にフォールバック

## ③ ライン会話モード（双方向通知ループでチャット的に継続対話）

> **正本**: ライン会話モードのルール正本は **CLAUDE.md §ライン会話モード**。本セクションはリファレンス実装・API詳細・コード例の提供が役割。ルールの矛盾がある場合は CLAUDE.md を優先する。

**発動条件**: ユーザーが「ライン会話発動」「ライン会話開始」「ライン会話始めて」等の宣言をした場合のみ。暗黙には発動しない。

**目的**: ユーザーが PC の画面を見られない状況（外出中・移動中・他作業中）でも、スマホの ntfy リプライ経由で Claude と対話を継続する。

**サイクル**:
```
[発動宣言]
  → AI: send_ntfy_and_wait(...) で「現状報告 + 次の質問/選択肢」を送信してブロック
  → ユーザー: ntfy アプリで ID + 返信本文をリプライ
  → AI: 受信した指示に従って作業 or 回答を生成
  → AI: 再び send_ntfy_and_wait(...) で「結果報告 + 次の質問」を送信してブロック
  → 以下、ユーザーが「ライン会話終了」等を返信するまで繰り返し
```

**使い方（Claude 側の動作ルール）**:

1. **発動宣言を受けたら最初のメッセージを送る前にユーザーに確認しない**。即座に `send_ntfy_and_wait` で初回通知を出す。通常の対話 UI で「ライン会話を開始しますか？」と聞き返すと二度手間
2. **各ターンで必ず `send_ntfy_and_wait` を使う**（単方向 `send_ntfy` は使わない）。片方向になった瞬間にループが切れる
3. **`timeout` はデフォルト 10800 秒（3時間）**。memory の `line_conversation_mode.md` に別の値が指定されている場合はそちらを優先（詳細ルールは CLAUDE.md §ライン会話モード参照）
4. **タイムアウト時は安全側デフォルト動作**（破壊的操作は行わない。詳細は CLAUDE.md §ライン会話モード参照）
5. **モード終了判定は CLAUDE.md §ライン会話モードのルールに従う**。「ライン会話終了」「会話モード終了」等の明示的宣言のみ。「別仕事」「コンテキストクリア」等はモード終了ではない
6. **1メッセージの長さはスマホで読める範囲に収める**。長文の分析結果等は要約して送り、詳細は「詳細ほしければ reply で “詳細” と返信」と誘導する
7. **ID運用ルールをユーザーに最初の1回だけ明示**（1行目にIDだけコピペ、2行目以降に指示）

**実装**: 既存の `send_ntfy_and_wait` をループで呼ぶだけで実現できる。**コード改修は不要**。Claude 側のエージェントループで以下相当の流れを実行する:

```python
from notify import send_ntfy_and_wait, send_ntfy

send_ntfy("ライン会話モード開始。1行目にID、2行目以降に指示を書いて返信してください。", priority="default")

while True:
    body = "<今の作業状況の要約>\n\n次にどうしますか？(続行 / 別タスク / 終了)"
    msg_id, reply = send_ntfy_and_wait(body, timeout=10800)
    if reply is None:
        # タイムアウト → 会話終了
        send_ntfy("返信タイムアウト。ライン会話を終了します。")
        break
    if reply.strip().lower() in ("ライン会話終了", "会話モード終了", "end conversation"):
        send_ntfy("ライン会話を終了しました。")
        break
    # 注意: 「終了」単体・「別仕事」「コンテキストクリア」等はモード終了ではない
    # reply を指示として解釈し、作業 or 回答を生成（次のターンに反映）
    ...
```

**注意**:
- ライン会話中は**通常の対話 UI に返答を書かない**（ユーザーが画面を見ていない前提なので無意味）。全応答は ntfy 経由で送る（CLAUDE.md §ライン会話モード L94 参照）
- ntfy トピックは公開のため、機密情報（APIキー・個人情報・口座残高等）はライン会話に流さない
- **モード終了・コンテキストクリア・BG完了通知のルールは CLAUDE.md §ライン会話モードが正本**。本セクションでは繰り返さない

## 送信元コンテキスト（タイトル自動生成）

通知タイトルを `<送信元略字>：<作業内容>` 形式で自動生成する仕組み。

| 送信元 | 略字 |
|--------|------|
| Claude Code | `ATP` |
| Codex | `GPT` |

### ⚠️ 必須ルール: `--sender` と `--task` は毎回付ける

**CLI 呼び出しは毎回別プロセス**のため、`set_ntfy_context()` のグローバル変数は保持されない。`--sender` を省略すると「投資エージェント」に戻り、ATP/GPT が入らない。

- **Claude Code**: `--sender ATP` を**全ての** `notify.py ntfy` 呼び出しに付ける（省略禁止）
- **Codex**: `--sender GPT` を同様に付ける
- `--task` はセッション中の作業内容を簡潔に記載（**省略禁止** — CLAUDE.md §用語・解釈ルール。省略すると通知タイトルがsenderのみになり作業判別不能）
- `--title` を明示的に指定した場合はそちらが優先（後方互換）。`--title` 明示時でも `--task` は付けておく（コマンドテンプレートの統一性）

### Python から（長時間スクリプト内で複数回送信する場合）

```python
from notify import set_ntfy_context, send_ntfy

set_ntfy_context("ATP", "LINE通知改造")  # プロセス冒頭で1回だけ
send_ntfy("処理完了")  # タイトル → "ATP：LINE通知改造"
```

> `set_ntfy_context` は同一プロセス内でのみ有効。CLI呼び出しでは使えない。

### CLI から（Claude Code / Codex が Bash で呼ぶ標準形）

```bash
# 単方向通知
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "処理完了" --sender ATP --task "LINE通知改造"
# タイトル → "ATP：LINE通知改造"

# 双方向通知
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "どうしますか？" --wait --timeout 1800 --sender ATP --task "月次パイプライン"
# タイトル → "ATP：月次パイプライン"
```

## パラメータ

| パラメータ | 型 | 説明 | デフォルト値 |
|-----------|-----|------|------------|
| message | str | 通知本文（作業内容） | 必須 |
| title | str \| None | 通知タイトル（None時はset_ntfy_contextの値を使用） | `None`（→コンテキスト未設定時は`投資エージェント`） |
| tags | str | ntfy タグ（絵文字ショートコード） | `white_check_mark` |
| priority | str | low / default / high / urgent | `high`（Androidポップアップ表示にはhigh以上が必要） |

## 設定

| 項目 | 値 |
|------|-----|
| 環境変数 | `NTFY_TOPIC`（`.env` に記載） |
| サーバー | `https://ntfy.sh`（公式パブリックサーバー） |
| 送信方式 | JSON API（UTF-8 日本語対応） |

## 注意事項

- トピック名は推測されにくいランダム文字列にすること（セキュリティ）
- 標準ライブラリのみ使用（追加パッケージ不要）
- Cloud Run Job からは従来通り `send_mail` を使う（ntfy はローカル専用）
- 双方向通知のリプライ検出は**同じトピック上の全メッセージ**を見るため、複数の `send_ntfy_and_wait` を並行実行する場合は互いに ID が混信しない（ID でフィルタしているため安全）
- ただし ntfy トピックは**公開**のため、トピック名さえ知られるとリプライ偽装が可能。用途は本人判断用途に限定する

### 落とし穴: 単方向と双方向の混同

冒頭の「使い分け早見表」の再掲。実装時に毎回確認すること。

- **返信不要の連絡 → `send_ntfy` / `notify.py ntfy "..."`**（`--wait` なし）
- **返信ほしい判断仰ぎ → `send_ntfy_and_wait(..., timeout=...)` / `notify.py ntfy "..." --wait --timeout N`**

過去の事故パターン:
- 連絡するだけのつもりで `--wait` を付けてしまい、呼び出し元スクリプトが `timeout` 秒（デフォルト3600秒=1時間）ブロック
- 判断を仰ぐつもりで `send_ntfy` を使ってしまい、ユーザーが返信してもスクリプトに届かず放置
- 双方向通知で ID 運用ルール（1行目ID／2行目以降本文）をユーザーに伝え忘れ、`reply` がずっと `None` のまま
- **コンテキスト圧縮後に記憶ベースでCLIコマンドを再構成し、`--timeout` のみ指定して `--wait` を落とした（MR-136）**。現在は `--timeout` 指定時に `--wait` が自動有効化されるよう修正済み（構造的防止）。ただし圧縮後は**必ず本セクションのCLIテンプレートを再読してからコマンドを構成すること**（他のフラグ漏れ防止）

**呼び出し側の書き方の原則**: 「この通知のあとスクリプトは止まるべきか？」を自問する。止まるなら②、止まらないなら①。迷ったら①（単方向）にして、本当に返信が必要になってから②に切り替える。

### 落とし穴: `send_ntfy_and_wait` の実行方法 — LINE会話モード中はBGデフォルト

**LINE会話モード中（CLAUDE.md §ライン会話モード、`line_conversation_mode.md` active: true）**:
- `run_in_background=true` で実行する（BGデフォルト）
- task-notification 受信時に出力ファイルを読み、返信を処理 → 次の `send_ntfy_and_wait` をBGで発行してループ継続
- ntfy timeout は memory の `line_conversation_mode.md` の値を優先。デフォルト 10800秒。**絶対に短縮しない**
- 4/29事故の教訓: BGが問題だったのではなく、**task-notification を処理しなかった**ことが問題。BGで送信しても task-notification を確実に処理すればループは維持される
- **FGフォールバック**: BG詰まり・task-notification未着等の緊急時のみFGで実行。FG時は Bash / PowerShell ツールの `timeout` に `600000`（10分、ミリ秒）を指定して auto-BG 化を防ぐ

```python
# LINE会話モード中:
# OK: BGデフォルト → task-notification で返信を受信
Bash(command="... notify.py ntfy '...' --wait --timeout 10800 --sender ATP --task '作業名' ...", run_in_background=true)

# OK: FGフォールバック（緊急時のみ）+ timeout=600000ms で auto-BG 回避
Bash(command="... notify.py ntfy '...' --wait --timeout 10800 --sender ATP --task '作業名' ...", timeout=600000)
```

**LINE会話モード外（通常モード）**:
- `run_in_background=true` で実行可。返信到着時に task-notification が届く
- フォアグラウンド実行するとセッションが timeout 秒ブロックされるため非推奨

```python
# LINE会話モード外:
# OK: バックグラウンド実行 → 返信到着時に通知される
Bash(command="... notify.py ntfy '...' --wait --sender ATP --task '作業名' ...", run_in_background=true)
```

**完了通知受信後の処理義務**: task-notification 受信時は**必ず出力ファイルを読み**、`send_ntfy_and_wait()` の戻り値（msg_id, reply）を確認する。ユーザーの返信が含まれている。読まずに放置するとユーザーのLINE返信を見落とす（2026-04-29 事故教訓）。コンテキストクリア待機中等の「処理停止状態」であっても、バックグラウンドタスクの完了通知処理は例外として必ず実行する。

### 落とし穴: Bash パス表記 — フォワードスラッシュ必須

Bash ツールで notify.py を呼ぶ場合、**Windows パス `C:\...` はそのまま使えない**。`\` は Bash のエスケープ文字のため、パス区切りとして解釈されない。

```bash
# OK: フォワードスラッシュ
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "メッセージ"

# OK: Unix 形式
PYTHONUTF8=1 /c/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "メッセージ"

# NG: バックスラッシュ → command not found
PYTHONUTF8=1 C:\venvs\investment-agent\Scripts\python.exe scripts/notify.py ntfy "メッセージ"
```

**日本語 Windows 固有の罠**: `\`（U+005C）が `¥`（円マーク）として表示される。エラーメッセージのパスが `C:¥venvs¥...` と表示され、パス区切り問題に気付きにくい（2026-05-02 事故）。

**注意**: Python コード内のパス（`open('C:/tmp/...')`等）はフォワードスラッシュで動作する。ただし Git Bash 固有の `/c/tmp/` 形式は Python から認識されないため使わないこと。

### 画面+LINE両方出力モード（拡張モード）

ユーザーが「画面にも出せ」「画面とLINE両方に送れ」等を指示した場合、通常のLINE会話モード（LINEのみ）から**拡張モード**に移行する。

**出力テンプレート（毎ターン機械的に適用）**:
1. **画面テキスト出力を先に書く** — マークダウンでアウトプット内容をテキスト出力
2. **LINE送信** — `send_ntfy_and_wait()` で同じ内容を送信

**必ず2ステップとも実行する。片方だけで終わらない。** 順序は画面→LINE固定（画面出力が確実に生成されてからLINE送信に進む）。

**注意の振り子現象**: 「LINE忘れた→次はLINE送った→今度は画面忘れた」と交互に欠落する事故パターン（レビュー038で4回連続発生）。チェックリスト（注意力依存）ではなくテンプレート（構造固定）で防止する。

**発動条件**: ユーザーが「両方に出せ」等を指示した時点。

---

## LINE会話モード運用ルール

> CLAUDE.md §5 から詳細を移動。CLAUDE.md側にはトリガー行動のみ残す。

### 動作フロー（同期ループ）

`send_ntfy_and_wait()` で返信受信 → 処理 → 結果を `send_ntfy_and_wait()` で送信 → 返信受信 → ... 「了解」等の短い応答でもループを途切れさせない。

### 動作フロー（非同期サブタスク）

(a) サブタスク起動 → (b) 中間報告を `send_ntfy_and_wait()` → (c) 完了待ち → (d) 最終報告を `send_ntfy_and_wait()`。「〜を待て」は「自分で起動して結果を待て」が第一解釈。

### 実行方式

- `send_ntfy_and_wait()` は `run_in_background=true` で実行する。task-notification 受信時に必ず出力ファイルを読み、返信内容を処理する。処理後、次の `send_ntfy_and_wait()` を再び BG で発行してループを維持する。send_ntfy へのダウングレードは理由を問わず禁止
- **FG実行はフォールバック**: BG詰まり・task-notification未着等の緊急時のみ `run_in_background` なし（FG）で実行してよい。FG時はツールの `timeout` パラメータに `600000`（10分、ミリ秒）を指定して auto-BG 化を防ぐ
- **timeout**: memory の `line_conversation_mode.md` の値を優先。デフォルト 10800秒
- タイムアウト時は安全な方向にデフォルト動作（破壊的操作は行わない）

### モード終了

- **明示的宣言のみ**: 「解除」「通常モードに戻す」等のみ。タスク切替指示を終了と推測しない。memory の `active: false` への外部変更もモード解除シグナルではない — 別セッションが共有memoryを書き換えても、本セッション内でユーザーからの解除指示がない限りモードを維持する
- 終了時は memory の `line_conversation_mode.md` を `active: false` に更新。`activated_by_session` / `deactivated_by_session` フィールドに「セッションID — タスク概要」を記録する

### コンテキストクリア指示

(1) memoryに保存 (2) `/clear を実行してください` と案内 (3) クリア完了前の新タスクはリマインド (4) ただしBG完了通知(task-notification)は処理する
**終了条件**: ユーザーが「LINEだけでいい」等を指示するか、LINE会話モード自体が解除されるまで。
