# LINE通知（ntfy プッシュ通知）

**カテゴリ**: tools
**作成日**: 2026-04-07
**ステータス**: 有効
**関連ファイル**: `scripts/notify.py`
**通称**: LINE通知 / ライン / LINE

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
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "月次データロード完了"

# タイトル・優先度を指定
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy "緊急: ジョブ失敗" --title "エラー" --priority urgent
```

> `--wait` を**付けない**こと。付けるとリプライ待ちモード（②）に入り、コマンドが `--timeout` 秒止まる。

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

```bash
# --wait でリプライ待ちモードに入る（デフォルト3600秒待機）
PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/notify.py ntfy \
    "銘柄 7203 を再試行しますか？ (yes/skip/abort)" --wait --timeout 1800

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

## パラメータ

| パラメータ | 型 | 説明 | デフォルト値 |
|-----------|-----|------|------------|
| message | str | 通知本文（作業内容） | 必須 |
| title | str | 通知タイトル | `投資エージェント` |
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

**呼び出し側の書き方の原則**: 「この通知のあとスクリプトは止まるべきか？」を自問する。止まるなら②、止まらないなら①。迷ったら①（単方向）にして、本当に返信が必要になってから②に切り替える。
