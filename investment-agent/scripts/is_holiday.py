"""休日フラグファイル管理.

日本の祝日（平日に限る）および年末年始に holiday.txt を作成し、
それ以外の日は削除する。

【フラグの用途】
作成した holiday.txt は、Claude Code 管理外のローカルツール群が参照する。
（例: z_shina.py, z_貸株.py, k-db_yahoo.py 等）
本スクリプト自体はフラグを消費せず、管理のみ行う。

【実行方法】
- ローカル: Dropbox ローカルフォルダに直接書き込み/削除
- サーバ（Cloud Run / Colab）: Dropbox API 経由で書き込み/削除

祝日一覧出典: https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html
"""

import datetime
import os
import sys
import traceback

import jpholiday

# ==========================================
# 0. 実行環境判別
# ==========================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "local" | "colab_personal" | "colab_enterprise" | "cloudrun"
    """
    if os.environ.get("CLOUD_RUN_JOB") or os.environ.get("K_SERVICE"):
        return "cloudrun"
    try:
        import google.colab  # noqa: F401
        if os.environ.get("GOOGLE_CLOUD_PROJECT"):
            return "colab_enterprise"
        return "colab_personal"
    except ImportError:
        return "local"


RUNTIME: str = detect_runtime()

_DROPBOX_ERROR: str = ""

# 環境別ライブラリ読み込み
if RUNTIME == "local":
    _SLIB_PATH = r"C:\Users\zonekun\Dropbox\stock\py"
    if _SLIB_PATH not in sys.path:
        sys.path.insert(0, _SLIB_PATH)
    import Slib
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from notify import send_mail
    import dropbox
    from dropbox.exceptions import ApiError
    from dropbox.files import WriteMode


def _send_mail(subject: str, body: str) -> None:
    """環境別メール送信ラッパー."""
    if RUNTIME == "local":
        Slib.SendMail(subject, body)
    else:
        send_mail(subject, body)


# ==========================================
# 1. 設定
# ==========================================

# ローカル: 直接書き込むファイルパス
FLAG_PATH_LOCAL = r"C:\Users\zonekun\Dropbox\stock\script\holiday.txt"

# Dropbox 上のパス（サーバ環境用・ローカルと同じ場所）
FLAG_PATH_DBX = "/stock/script/holiday.txt"

# Dropbox 認証情報
DBX_APP_KEY       = "t8feblcw74hoeky"
DBX_APP_SECRET    = "fcjgc37d034pw1n"
DBX_REFRESH_TOKEN = "XwOxZlA8jPUAAAAAAAAAAZxnT4qRFtWLcShpKy3cNjTf3euIMqEZxCNieAQiLSDw"


# ==========================================
# 2. フラグ操作（環境別）
# ==========================================

def _get_dbx_client():
    """Dropbox クライアントを返す."""
    return dropbox.Dropbox(
        app_key=DBX_APP_KEY,
        app_secret=DBX_APP_SECRET,
        oauth2_refresh_token=DBX_REFRESH_TOKEN,
    )


def create_flag(today: datetime.date) -> None:
    """フラグファイルを作成する."""
    content = f"{today.strftime('%Y-%m-%d')}は祝日（または特別休暇）です。"
    if RUNTIME == "local":
        with open(FLAG_PATH_LOCAL, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"フラグ作成: {FLAG_PATH_LOCAL}")
    else:
        global _DROPBOX_ERROR
        dbx = _get_dbx_client()
        try:
            dbx.files_upload(content.encode("utf-8"), FLAG_PATH_DBX, mode=WriteMode("overwrite"))
            print(f"フラグ作成 (Dropbox): {FLAG_PATH_DBX}")
        except Exception as e:
            if "insufficient_space" in str(e):
                _DROPBOX_ERROR = f"Dropbox 容量不足のためフラグ作成をスキップ: {FLAG_PATH_DBX}"
                print(f"[警告] {_DROPBOX_ERROR}")
            else:
                raise


def delete_flag() -> None:
    """フラグファイルが存在すれば削除する."""
    deleted = False
    if RUNTIME == "local":
        if os.path.exists(FLAG_PATH_LOCAL):
            try:
                os.remove(FLAG_PATH_LOCAL)
                print(f"フラグ削除: {FLAG_PATH_LOCAL}")
                deleted = True
            except OSError as e:
                print(f"フラグ削除エラー: {e}")
                raise
    else:
        dbx = _get_dbx_client()
        try:
            dbx.files_delete_v2(FLAG_PATH_DBX)
            print(f"フラグ削除 (Dropbox): {FLAG_PATH_DBX}")
            deleted = True
        except ApiError as e:
            if e.error.is_path_lookup() and e.error.get_path_lookup().is_not_found():
                pass  # ファイルが存在しない場合は何もしない
            else:
                print(f"フラグ削除エラー: {e}")
                raise


# ==========================================
# 3. メイン
# ==========================================

def manage_holiday_flag() -> None:
    """平日の祝日・年末年始にフラグファイルを作成し、それ以外は削除する."""
    print(f"実行環境: {RUNTIME}")

    # 年末年始の特別ルール（jpholiday に含まれない日）
    special_dates = {
        datetime.date(2025, 12, 31),  # 大晦日
        datetime.date(2026,  1,  2),  # 年始休暇
        datetime.date(2026,  1,  3),  # 年始休暇
    }

    # -------------------------------------------------------
    # ▼ プログラム固定の祝日リスト（復活用にコメントアウト）
    # ▼ jpholiday に切り替えたため現在は未使用
    # -------------------------------------------------------
    # holidays_2026 = {
    #     # --- 特別ルール（年末年始） ---
    #     datetime.date(2025, 12, 31),  # 大晦日
    #     datetime.date(2026,  1,  1),  # 元日
    #     datetime.date(2026,  1,  2),  # 年始休暇
    #     datetime.date(2026,  1,  3),  # 年始休暇
    #     # ---------------------------
    #     datetime.date(2026,  1, 12),  # 成人の日
    #     datetime.date(2026,  2, 11),  # 建国記念の日
    #     datetime.date(2026,  2, 23),  # 天皇誕生日
    #     datetime.date(2026,  3, 20),  # 春分の日
    #     datetime.date(2026,  4, 29),  # 昭和の日
    #     datetime.date(2026,  5,  3),  # 憲法記念日（日曜日）
    #     datetime.date(2026,  5,  4),  # みどりの日
    #     datetime.date(2026,  5,  5),  # こどもの日
    #     datetime.date(2026,  5,  6),  # 憲法記念日の振替休日
    #     datetime.date(2026,  7, 20),  # 海の日
    #     datetime.date(2026,  8, 11),  # 山の日
    #     datetime.date(2026,  9, 21),  # 敬老の日
    #     datetime.date(2026,  9, 22),  # 国民の休日
    #     datetime.date(2026,  9, 23),  # 秋分の日
    #     datetime.date(2026, 10, 12),  # スポーツの日
    #     datetime.date(2026, 11,  3),  # 文化の日
    #     datetime.date(2026, 11, 23),  # 勤労感謝の日
    # }
    # -------------------------------------------------------

    today      = datetime.date.today()
    is_weekday = today.weekday() < 5  # 0=月 〜 4=金、5=土、6=日
    is_holiday = jpholiday.is_holiday(today) or (today in special_dates)

    if is_weekday and is_holiday:
        create_flag(today)
    else:
        delete_flag()


if __name__ == "__main__":
    try:
        manage_holiday_flag()
        if _DROPBOX_ERROR:
            _send_mail(
                "[is_holiday] 【異常終了】DROPBOX容量不足",
                f"Dropboxへのアップロードが容量不足で失敗しました。\n\n{_DROPBOX_ERROR}",
            )
            sys.exit(1)
    except Exception as e:
        _send_mail(
            "[is_holiday] エラー",
            f"エラーが発生しました: {e}\n\n{traceback.format_exc()}",
        )
        raise
