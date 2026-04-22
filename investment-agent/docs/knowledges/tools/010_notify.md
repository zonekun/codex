# notify.py — メール通知・ログキャプチャ 共通ユーティリティ

**カテゴリ**: tools
**作成日**: 2026-02-28
**ステータス**: 有効
**関連ファイル**: `scripts/notify.py`

## 概要

Cloud Run Job・Colab スクリプトで使う Gmail SMTP メール送信とログキャプチャの共通ユーティリティ。
`scripts/notify.py` に実装。同一 `scripts/` ディレクトリ内のスクリプトから `sys.path.insert` でインポートする。

---

## インポート方法

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture
```

---

## メール設定

| 項目 | 値 |
|------|-----|
| SMTP ホスト | `smtp.googlemail.com:465`（SSL） |
| 送信アカウント | `springwater.jp@gmail.com` |
| 送信元（From） | `zone@ceres.dti.ne.jp` |
| 送信先（To） | `zonekun@gmail.com` |

---

## `send_mail()` — メール送信

```python
send_mail(
    subject: str,
    body: str,
    attachment_text: str | None = None,  # 添付テキスト（None なら添付なし）
    attachment_name: str = "log.txt",    # 添付ファイル名
) -> None
```

### 使用例

```python
# シンプル送信
send_mail("[JOB] 開始", "処理を開始しました。")

# ログをテキストファイルとして添付
send_mail(
    "[JOB] エラー",
    "エラーが発生しました。",
    attachment_text=log_text,
    attachment_name="edinet_log.txt",
)
```

> エラーが発生しても `send_mail()` は例外を飲み込んで `print` するだけ（ジョブ本体に影響なし）。

---

## `LogCapture` — ログキャプチャ

`print()` の出力を **stdout（コンソール）と内部バッファの両方**に記録する。
`stop()` でバッファ内容を文字列として返す。

```python
log_cap = LogCapture()
log_cap.start()
print("処理中...")          # stdout と内部バッファの両方に書き込まれる
log_text = log_cap.stop()  # キャプチャ終了 → ログ文字列を返す
```

---

## Cloud Run Job での標準的な使用パターン

> **ルール**: ジョブはエラー時のみメールを送る。正常完了時の通知は送らないこと（YF_STOCK_INFO で完了メールが飛んでいた件の教訓）。
>
> **ルール**: `send_mail` の挙動を変更する修正（正常完了メールの削除、件名変更など）をソースに入れたら、Cloud Run Job では**必ずイメージを再ビルドして `gcloud run jobs update --image ...:latest` で digest 再解決までやる**。ソースを直しただけでは旧イメージに焼き込まれた挙動が残り、定時実行で旧メールが送信され続ける。詳細手順は `005_cloudrun_job_deploy.md` ⑥ を参照。

```python
from datetime import datetime, timezone, timedelta
JST = timezone(timedelta(hours=+9), "JST")

def main():
    start_time = datetime.now(JST)   # ← 必ず JST を渡す（UTC禁止）
    log_cap    = LogCapture()
    log_cap.start()
    date_label = "不明"
    try:
        # ... セットアップ・日付解決・メイン処理 ...
        # 正常完了時は print のみ。send_mail は呼ばない
        log_cap.stop()
        print(f"[JOB] 完了 {date_label} 件数: {count}")

    except Exception as e:
        log_text = log_cap.stop()
        tb_str   = traceback.format_exc()
        send_mail(
            f"[JOB] エラー {date_label}",
            f"エラー: {e}\n\n{tb_str}",
            attachment_text=log_text or None,
        )
        raise
```

---

## Dockerfile への追加

`notify.py` を使うスクリプトをコンテナ化する場合、Dockerfile に COPY を追加すること:

```dockerfile
COPY scripts/<script>.py scripts/<script>.py
COPY scripts/notify.py scripts/notify.py
```

---

## 注意事項

- `LogCapture.stop()` 後に再度 `stop()` を呼んでも空文字を返す（safe）
- Cloud Run の stdout はそのまま Cloud Logging に流れるため、`_Tee` によって両方に書き込まれる動作は正しい
- Cloud Run コンテナでは `PYTHONUTF8=1` 環境変数を設定しないと日本語が文字化けする（Dockerfile の `ENV PYTHONUTF8=1` で対応済み）

---

## 落とし穴

### 正常完了時にメール送信してはいけない（2026-04-11 発覚）

ジョブ通知のルールは **「エラー時のみ送信」**。開始通知・完了通知は送らないこと。

**事故**: `scripts/yf_stock_info_load.py` が正常完了時に `send_mail("[YF_STOCK_INFO] 完了", ...)` を呼ぶ実装で作成され、毎回成功時にも通知メールが飛んでいた。

**原因**: 当時の本ファイル「標準的な使用パターン」セクションが、開始・完了・エラーの3通を送る例を示していたため、新規ジョブ作成時に「完了メールも送るのが標準」と誤読する構造だった。

**対応**: 標準パターンを「エラー時のみ」に修正し、本ルールを明文化した（本ファイル冒頭の注記）。既存ジョブ（edinet/tdnet/stock_price/dividend_date 等）は元々 `except` 節でのみ `send_mail` を呼んでおりルール遵守済み。
