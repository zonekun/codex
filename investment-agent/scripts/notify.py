"""メール通知・ログキャプチャ 共通ユーティリティ.

使用方法:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send_mail, LogCapture

メール送信:
    send_mail("[JOB] 開始", "処理を開始しました。")
    send_mail("[JOB] エラー", "エラーが発生しました。", attachment_text=log_text)

ログキャプチャ:
    log_cap = LogCapture()
    log_cap.start()
    print("処理中...")          # stdout と内部バッファの両方に書き込まれる
    log_text = log_cap.stop()  # キャプチャ終了 → ログ文字列を返す
"""

import argparse
import io
import json
import os
import random
import socket
import ssl
import smtplib
import string
import sys
import time
import urllib.request
import urllib.error
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ============================================================
# Gmail SMTP 設定（Slib.py 準拠）
# ============================================================

_SMTP_ACCOUNT  = "springwater.jp@gmail.com"
_SMTP_PASSWORD = "vopt uwcc vcte ubex"
_SMTP_HOST     = "smtp.googlemail.com"
_SMTP_PORT     = 465
NOTIFY_FROM    = "zone@ceres.dti.ne.jp"
NOTIFY_TO      = "zonekun@gmail.com"


# ============================================================
# メール送信
# ============================================================

def send_mail(
    subject: str,
    body: str,
    attachment_text: str | None = None,
    attachment_name: str = "log.txt",
) -> None:
    """Gmail SMTP でプレーンテキストメールを送信する.

    Args:
        subject        : 件名
        body           : 本文（プレーンテキスト）
        attachment_text: 添付するテキスト（None なら添付なし）
        attachment_name: 添付ファイル名（デフォルト: "log.txt"）
    """
    try:
        if attachment_text:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body, "plain", "utf-8"))
            att = MIMEBase("text", "plain")
            att.set_payload(attachment_text.encode("utf-8"))
            encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment",
                           filename=attachment_name)
            msg.attach(att)
        else:
            msg = MIMEText(body, "plain", "utf-8")

        msg["Subject"] = subject
        msg["From"]    = NOTIFY_FROM
        msg["To"]      = NOTIFY_TO

        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT,
                              context=ssl.create_default_context()) as server:
            server.login(_SMTP_ACCOUNT, _SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[メール送信] {subject}")
    except Exception as e:
        print(f"[メール送信失敗] {e}")


# ============================================================
# ログキャプチャ
# ============================================================

# ============================================================
# ntfy プッシュ通知
# ============================================================

_NTFY_BASE_URL = "https://ntfy.sh"


def _load_ntfy_topic() -> str:
    """環境変数 or .env から NTFY_TOPIC を取得する."""
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        return topic
    # .env からフォールバック読み込み
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NTFY_TOPIC="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("NTFY_TOPIC が未設定です（.env または環境変数に設定してください）")


_MSG_ID_TTL_SEC = 1800  # 30分以内に使用済みのIDは避ける（実質の有効期限）


def _gen_msg_id(length: int = 3) -> str:
    """数字3桁のメッセージIDを生成する（コピペ短縮）."""
    return "".join(random.choices("0123456789", k=length))


def _fetch_recent_msg_ids(topic: str, lookback_sec: int = _MSG_ID_TTL_SEC) -> set[str]:
    """直近 lookback_sec に送受信された本文先頭3桁数字を使用済みIDとして収集する."""
    since = int(time.time()) - lookback_sec
    url = f"{_NTFY_BASE_URL}/{topic}/json?poll=1&since={since}"
    used: set[str] = set()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") != "message":
                    continue
                body = (ev.get("message") or "").lstrip()
                if not body:
                    continue
                # 本文先頭の1単語（空白/改行区切り）を取り出す
                head = body.split(maxsplit=1)[0]
                if head.isdigit() and len(head) == 3:
                    used.add(head)
    except (socket.timeout, urllib.error.URLError, TimeoutError):
        # 衝突チェックは best-effort。失敗時は空集合で続行
        pass
    return used


def _gen_unique_msg_id(topic: str, max_tries: int = 50) -> str:
    """直近TTL内に使われていないIDを生成する（衝突回避）."""
    used = _fetch_recent_msg_ids(topic)
    for _ in range(max_tries):
        mid = _gen_msg_id()
        if mid not in used:
            return mid
    # 1000通り全て衝突は事実上発生しないが念のためのフォールバック
    return _gen_msg_id()


def send_ntfy(
    message: str,
    title: str = "投資エージェント",
    *,
    tags: str = "white_check_mark",
    priority: str = "high",
    with_id: bool = False,
) -> str | None:
    """ntfy.sh にプッシュ通知を送信する.

    Args:
        message : 通知本文（作業内容など）
        title   : 通知タイトル
        tags    : ntfy タグ（絵文字ショートコード）
        priority: low / default / high / urgent
                  デフォルト high(4) でAndroidヘッドアップ通知を表示
        with_id : True のとき先頭に [XXXXXX] 形式の6文字IDを付与して送信し、IDを返す

    Returns:
        with_id=True のとき生成したメッセージID、それ以外は None
    """
    topic = _load_ntfy_topic()
    # ID付き送信時: 直近TTL内に使用済みのIDを避けて生成
    msg_id = _gen_unique_msg_id(topic) if with_id else None
    # ID付き送信時: 1行目にID、2行目以降に本文（リプライ時にIDだけコピペしやすい）
    body = f"{msg_id}\n{message}" if msg_id else message
    url = f"{_NTFY_BASE_URL}"
    payload = json.dumps({
        "topic": topic,
        "title": title,
        "message": body,
        "tags": [tags],
        "priority": 3 if priority == "default" else
                   {"low": 2, "high": 4, "urgent": 5}.get(priority, 3),
        "click": "",
        "actions": [],
    }).encode("utf-8")
    # Sticky: 通知領域に固定（スワイプで消えない）
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Sticky", "yes")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            suffix = f" id={msg_id}" if msg_id else ""
            print(f"[ntfy] 通知送信OK ({resp.status}){suffix}")
    except urllib.error.URLError as e:
        print(f"[ntfy] 通知送信失敗: {e}")
    return msg_id


def wait_ntfy_reply(
    msg_id: str,
    timeout: float = 3600.0,
    *,
    since_ts: int | None = None,
    own_body_rest: str | None = None,
) -> str | None:
    """指定メッセージIDで始まるリプライ（自分の送信以外）を待つ.

    ユーザーは ntfy アプリから以下いずれかの形式で返信する想定:
        XXXXXX
        自由文の指示

    または `XXXXXX 指示` のようにスペース区切り1行でも可。
    受信メッセージ本文の先頭（lstrip後）が msg_id と一致すれば、
    ID と直後の空白・改行を除いた残り文字列をリプライとして返す。

    Args:
        msg_id        : send_ntfy(with_id=True) で取得した6文字ID
        timeout       : 待機上限（秒）。0以下で即時終了
        since_ts      : ntfy購読開始タイムスタンプ（unix秒）。未指定なら現在時刻
        own_body_rest : 自分が送信した本文（ID除去後）。エコー判定に使う。
                        未指定時は「最初にID一致した1件」をエコーとみなすフォールバック。

    Returns:
        リプライ本文（IDプレフィクス除去済み・前後空白trim）。
        タイムアウト時は None
    """
    topic = _load_ntfy_topic()
    since = since_ts if since_ts is not None else int(time.time())
    url = f"{_NTFY_BASE_URL}/{topic}/json?since={since}"
    deadline = time.time() + timeout
    own_echo_seen = False

    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        read_timeout = min(60.0, remaining)
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=read_timeout) as resp:
                for raw in resp:
                    if time.time() > deadline:
                        return None
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") != "message":
                        continue
                    body = ev.get("message", "") or ""
                    stripped = body.lstrip()
                    if not stripped.startswith(msg_id):
                        continue
                    # ID と直後の空白・改行を除去
                    rest = stripped[len(msg_id):].lstrip("\r\n \t")
                    # エコー判定
                    if own_body_rest is not None:
                        # 厳密判定: 残り本文が送信時本文と完全一致ならエコー
                        if rest == own_body_rest:
                            continue
                    else:
                        # フォールバック: 最初にヒットした1件をエコーとみなす
                        if not own_echo_seen:
                            own_echo_seen = True
                            continue
                    return rest
        except (socket.timeout, urllib.error.URLError, TimeoutError):
            # 接続断・タイムアウトは再接続して継続
            continue
    return None


def send_ntfy_and_wait(
    message: str,
    timeout: float = 3600.0,
    title: str = "投資エージェント",
    *,
    tags: str = "question",
    priority: str = "urgent",
) -> tuple[str, str | None]:
    """通知送信 → 同じトピックへのリプライを待つ（双方向通知）.

    使用例:
        msg_id, reply = send_ntfy_and_wait("A/B どちらで続行？", timeout=1800)
        if reply is None:
            # タイムアウト → デフォルト挙動
            ...
        elif reply.lower().startswith("a"):
            ...

    Args:
        message : 通知本文（ユーザー判断を仰ぐ内容）
        timeout : 返信待機上限（秒）
        title   : 通知タイトル
        tags    : ntfy タグ（デフォルト question=❓）
        priority: 優先度（デフォルト urgent）

    Returns:
        (msg_id, reply) 形式のタプル。タイムアウト時は reply=None
    """
    since_ts = int(time.time())
    msg_id = send_ntfy(
        message, title=title, tags=tags, priority=priority, with_id=True,
    )
    assert msg_id is not None
    reply = wait_ntfy_reply(
        msg_id, timeout=timeout, since_ts=since_ts, own_body_rest=message,
    )
    return msg_id, reply


# ============================================================
# ログキャプチャ
# ============================================================

class _Tee:
    """stdout を標準出力とバッファの両方に書き込む（内部クラス）."""

    def __init__(self, original: object, buf: io.StringIO) -> None:
        self._orig = original
        self._buf  = buf

    def write(self, data: str) -> None:
        self._orig.write(data)
        self._buf.write(data)

    def flush(self) -> None:
        self._orig.flush()
        self._buf.flush()


class LogCapture:
    """print() の出力を stdout とバッファの両方に記録する.

    Example:
        log_cap = LogCapture()
        log_cap.start()
        print("処理中...")
        log_text = log_cap.stop()  # → "処理中...\\n"
    """

    def __init__(self) -> None:
        self._buf  = io.StringIO()
        self._orig = None

    def start(self) -> None:
        """キャプチャ開始（sys.stdout を Tee に差し替える）."""
        self._orig = sys.stdout
        sys.stdout = _Tee(self._orig, self._buf)

    def stop(self) -> str:
        """キャプチャ終了 → 蓄積されたログ文字列を返す."""
        if self._orig is not None:
            sys.stdout = self._orig
            self._orig = None
        return self._buf.getvalue()


# ============================================================
# CLI エントリポイント
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="通知ユーティリティ")
    sub = parser.add_subparsers(dest="cmd")

    # ntfy サブコマンド
    p_ntfy = sub.add_parser("ntfy", help="ntfy プッシュ通知を送信")
    p_ntfy.add_argument("message", help="通知メッセージ")
    p_ntfy.add_argument("--title", default="投資エージェント", help="通知タイトル")
    p_ntfy.add_argument("--priority", default="high",
                        choices=["low", "default", "high", "urgent"])
    p_ntfy.add_argument("--wait", action="store_true",
                        help="メッセージIDを付与して送信し、リプライを待つ")
    p_ntfy.add_argument("--timeout", type=float, default=3600.0,
                        help="--wait 時の待機秒数（デフォルト3600）")

    # mail サブコマンド
    p_mail = sub.add_parser("mail", help="メール通知を送信")
    p_mail.add_argument("subject", help="件名")
    p_mail.add_argument("body", help="本文")

    args = parser.parse_args()
    if args.cmd == "ntfy":
        if args.wait:
            msg_id, reply = send_ntfy_and_wait(
                args.message, timeout=args.timeout, title=args.title,
                priority=args.priority,
            )
            if reply is None:
                print(f"[ntfy] タイムアウト id={msg_id}")
                sys.exit(2)
            print(f"[ntfy] リプライ受信 id={msg_id}")
            print(reply)
        else:
            send_ntfy(args.message, title=args.title, priority=args.priority)
    elif args.cmd == "mail":
        send_mail(args.subject, args.body)
    else:
        parser.print_help()
