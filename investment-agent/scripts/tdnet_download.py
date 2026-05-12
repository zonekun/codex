"""
TDnet 適時開示ファイル一括ダウンロード

Usage:
    PYTHONUTF8=1 python scripts/tdnet_download.py
    PYTHONUTF8=1 python scripts/tdnet_download.py --from 20260217 --to 20260224
    PYTHONUTF8=1 python scripts/tdnet_download.py --from 20260217 --to 20260224 --save-dir /c/Users/zonekun/Dropbox/stock/script/tdnet

Data source:
    TDnet 公式サイト（東京証券取引所）
    https://www.release.tdnet.info/inbs/I_list_NNN_YYYYMMDD.html

File destination:
    [local]  <save_dir>/<証券コード4桁>/<filename>.pdf
    [colab]  gs://stock_data_1930932/tdnet/<証券コード4桁>/<filename>.pdf

    ※ ローカル保存先は CLAUDE.md 規約に従い C:/tmp/ 配下（Dropbox / Google Drive 禁止）。
      ローカル実行はテスト用途のみ。本番は Cloud Run Job で GCS に保存。
    ※ 決算短信 XBRL ZIP はザラ場ツール（zaraba_tdnet_poller.py）がザラバ中に
      ローカルDLして iXBRL パーサーに回す。日次バッチでの永続化は行わない。

Runtime:
    実行環境を自動判別（local / colab_personal / colab_enterprise）

Filename format:
    {日付}_{証券コード4桁}_{会社名}_{カテゴリ}_{ファイル概要}_{オリジナルファイル名}.pdf
    例: 20260224_4438_Welby_業績予想_業績予想の開示に関するお知らせ_140120260224567725.pdf

Backup:
    旧実装（yanoshin非公式API）の仕様は以下を参照
    docs/knowledges/api/003_tdnet_official_scraping.md
"""

import argparse
import csv
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=+9), "JST")
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

# notify.py（同ディレクトリ）から共通メール・ログ機能をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import send_mail, LogCapture

# ╔══════════════════════════════════════════════════════════════════╗
# ║  ★ 実行設定（ここを直接書き換えて使う）                                ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  DATE_MODE を選択:                                                ║
# ║    "t"  今日の日付でダウンロード ← デフォルト                         ║
# ║    "1"  DATE_SINGLE の1日のみダウンロード                            ║
# ║    "r"  DATE_FROM ～ DATE_TO の期間でダウンロード                     ║
# ╚══════════════════════════════════════════════════════════════════╝

DATE_MODE   = "t"          # "t"=今日 / "1"=特定の1日 / "r"=期間

DATE_SINGLE = "20260224"   # MODE="1" のときの日付 (YYYYMMDD)

DATE_FROM   = "20260217"   # MODE="r" のときの開始日 (YYYYMMDD)
DATE_TO     = "20260224"   # MODE="r" のときの終了日 (YYYYMMDD)

SAVE_DIR    = Path("C:/tmp/tdnet")  # local のみ使用（テスト用途、本番は GCS）

GCS_BUCKET  = "stock_data_1930932"   # Colab のみ使用
GCS_PREFIX  = "tdnet"                # Colab のみ使用

# ════════════════════════════════════════════════════════════════════

# ============================================================
# 実行環境の自動判別
# ============================================================

def detect_runtime() -> str:
    """実行環境を自動判別する.

    Returns:
        "local" | "colab_personal" | "colab_enterprise" | "cloudrun"

    Cloud Run 環境変数:
        Jobs:     CLOUD_RUN_JOB（K_JOB は誤り）
        Services: K_SERVICE
    """
    import os
    # Cloud Run Jobs: CLOUD_RUN_JOB、Cloud Run Services: K_SERVICE が自動セットされる
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

# ============================================================

TDNET_BASE_URL      = "https://www.release.tdnet.info/inbs/"
YANOSHIN_BASE_URL   = "https://webapi.yanoshin.jp/webapi/tdnet/list/"
PAGE_FETCH_INTERVAL = 0.5   # ページ取得間隔（秒）。TDnetサーバへの礼儀
DOWNLOAD_INTERVAL   = 0.3   # PDFダウンロード間隔（秒）

# 環境変数 TDNET_USE_YANOSHIN=true で yanoshin API を使用（過去データ取得用）
USE_YANOSHIN: bool = os.environ.get("TDNET_USE_YANOSHIN", "").lower() == "true"
REQUEST_TIMEOUT     = 30    # HTTPタイムアウト（秒）
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": _UA}

# ============================================================
# カテゴリ分類テーブル（要否の根拠）
#
#   #  P  カテゴリ                      件数/週  要否    理由
# ------------------------------------------------------------
#   1  S  決算短信                         125  ✅ 要  財務数値の一次ソース。業績判断の最重要資料
#   2  S  TOB・MBO                         30  ✅ 要  対象株に即座かつ大幅な価格インパクト
#   3  S  業績修正                          19  ✅ 要  ガイダンス変更は翌日の株価に直撃
#   4  S  買収防衛策                          2  ✅ 要  TOBと連動。株価・需給に即影響
#   5  S  上場廃止                           2  ✅ 要  保有株の出口に直結
#   6  S  継続企業疑義(GC)                    1  ✅ 要  倒産リスクの最大シグナル
#   7  A  決算説明資料                        74  ✅ 要  決算短信の補足。経営陣の見通しが読める
#   8  A  自己株式取得                        70  ✅ 要  需給改善・株価下支えシグナル
#   9  A  役員異動（代表クラス）                  45  ✅ 要  CEO/代表交代は株価トレンド転換点
#  10  A  配当                             37  ✅ 要  配当利回り変化は機関投資家の売買トリガー
#  11  A  第三者割当・公募増資                   34  ✅ 要  希薄化による需給悪化。株価下落の典型
#  12  A  分配金                            29  ✅ 要  J-REITの収益指標。配当と同等の重要性
#  13  A  合併・組織再編                       25  ✅ 要  株価に大きな構造変化をもたらす
#  14  A  子会社化・買収                       22  ✅ 要  事業拡大・のれん増加。プレミアム付与あり
#  15  A  主要株主異動                         13  ✅ 要  大株主の売却/取得は需給に直撃
#  16  A  新株予約権発行                        12  ✅ 要  将来希薄化リスク。MSワラントは要警戒
#  17  A  株式売出し                          12  ✅ 要  大株主の出口。需給悪化→株価下落圧力
#  18  A  配当変更（増減配）                     12  ✅ 要  増配はポジサプ、減配は売り圧力
#  19  A  中期経営計画                         11  ✅ 要  目標数値・戦略転換が株価織り込みの起点
#  20  A  株式分割・併合                        11  ✅ 要  分割は流動性向上→個人投資家流入
#  21  A  特別利益                              -  ✅ 要  一時的収益改善。特別損益計上から分離
#  21b A  特別損失                              -  ✅ 要  業績修正の前兆になることが多い（減損損失含む）
#  22  A  業績予想                             7  ✅ 要  初回ガイダンス開示は期待値形成に直結
#  23  A  事業計画（グロース）                     7  ✅ 要  グロース銘柄の成長ストーリー確認
#  24  A  立会外分売                            6  ✅ 要  大量売却の告知。需給悪化パターン
#  25  A  監査人異動                            6  ✅ 要  不正・粉飾の予兆になることがある
#  26  A  訴訟・法的                            5  ✅ 要  巨額賠償・仮差押は業績・キャッシュに直撃
#  27  A  インシデント（災害・事故）                  5  ✅ 要  生産停止・設備損失は業績への影響大
#  28  A  自己株式消却                           4  ✅ 要  EPS改善シグナル。株価ポジティブ
#  29  A  インシデント（セキュリティ）                 4  ✅ 要  情報漏洩・サイバー攻撃は信用失墜
#  30  A  転換社債(CB)発行                       3  ✅ 要  希薄化リスク+HFの空売り戦略に利用される
#  31  A  行政処分                              2  ✅ 要  業務停止は売上直撃。最悪上場廃止に発展
#  32  A  DES（債権株式化）                      1  ✅ 要  財務改善シグナルだが希薄化を伴う
#  33  A  リストラ・希望退職                       1  ✅ 要  コスト削減→収益改善の先行指標
#  34  A  不祥事・社内調査                        1  ✅ 要  決算延期・上場廃止リスクの入口
#  35  B  役員・人事                           175  ❌ 不要  一般的な異動は投資判断に影響しない
#  36  B  その他（未分類）                       126  ✅ 要  稀に重要情報が含まれる可能性あり
#  37  B  資金調達（社債・借入）                    38  ❌ 不要  即時の株価影響は限定的
#  38  B  株主優待                             17  ✅ 要  優待変更は需給に影響
#  39  B  株主総会                             13  ❌ 不要  通常は形式的。重要決議は他カテゴリで捕捉
#  40  B  資産取得（不動産）                       12  ❌ 不要  対象が不動産株・REITでない限り無関係
#  41  B  提携・協業                            10  ✅ 要  進展次第では株価インパクト
#  42  B  月次開示                             10  ✅ 要  小売・不動産等では業績トレンドの早期把握
#  43  B  子会社設立                             8  ✅ 要  新事業・海外展開の把握
#  44  B  暗号資産                              8  ❌ 不要  多くは話題性止まり。投資戦略の対象外
#  45  B  訂正                                8  ❌ 不要  軽微な誤記訂正が大半
#  46  B  大型受注・契約                             7  ✅ 要  大型案件は業績予想の上振れ根拠になりうる
#  47  B  資産売却（不動産）                        7  ✅ 要  特別益の源泉。業績修正の先行指標に
#   -  B  業績の重要な先行指標                      -  ✅ 要  SaaS解約率・ARPU、稼働率、出荷台数等、将来業績に直結するKPI（月次開示とは別枠）
#   -  B  受注高/受注残高                          -  ✅ 要  製造・建設業の重要シグナル。大型受注・契約とは独立したカテゴリ
#  48  B  開示事項の経過・変更                       6  ❌ 不要  過去の開示の補足。単独では判断困難
#  49  B  子会社からの配当受領                       6  ❌ 不要  グループ内取引。連結PLへの影響は軽微
#  50  B  事業・子会社売却                         4  ✅ 要  選択と集中。特別益も発生
#  51  B  格付                                2  ❌ 不要  社債投資家向け。株式投資への影響は間接的
#  52  C  ETF/ETN日々開示                      45  ❌ 不要  個別株投資と無関係。自動開示の定型文書
#  53  C  株式報酬制度（ESOP等）                   24  ❌ 不要  直接の株価インパクトは軽微
#  54  C  定款変更                             24  ❌ 不要  定時総会の附議事項が大半
#  55  C  ETF関連                             14  ❌ 不要  個別株投資と無関係
#  56  C  ETF分配金                            11  ❌ 不要  個別株投資と無関係
#  57  C  資本構成変更（税務整理）                    10  ❌ 不要  資本金・準備金の付け替え。P/L影響なし
#  58  C  支配株主関連                            4  ❌ 不要  有価証券報告書の記載義務開示。実態変化なし
#  59  C  ガバナンス変更                           4  ❌ 不要  機関設計変更等。即時の投資判断には不要
#  60  C  本店移転                              3  ❌ 不要  事業実態への影響ゼロ
#  61  C  J-REIT（資金調達）                     1  ❌ 不要  J-REIT特有の定型開示
#  62  C  SO行使価額確定                          1  ❌ 不要  発行時に既に織り込み済み
#   -  C  有価証券報告書                          -  ❌ 不要  有報・四半期報告書。DLフェーズのみで使用
# ============================================================

# ダウンロードをスキップするカテゴリ（B不要 + C全て）
# ※ 優先度Cはis_needed()内でまとめてスキップ。ここはB不要のみ列挙。
_SKIP_B_CATEGORIES: set[str] = {
    "役員・人事",               # #35
    "資金調達（社債・借入）",      # #37
    "株主総会",                 # #39
    "資産取得（不動産）",          # #40
    "暗号資産",                 # #44
    "訂正",                    # #45
    "開示事項の経過・変更",         # #48
    "子会社からの配当受領",         # #49
    "格付",                    # #51
}


# ============================================================
# カテゴリ分類
# ============================================================

def _is_reit(item: dict) -> bool:
    """J-REIT・ETF等の特殊証券を判定する."""
    name = item.get("company_name", "")
    return bool(re.search(r'リート|投資法人|REIT', name))


def classify(title: str, item: dict) -> tuple[str, str]:
    """タイトルとアイテム情報からカテゴリと優先度を返す.

    Returns:
        (category, priority)  priority: S=最重要 / A=重要 / B=参考 / C=不要
    """
    t = title

    # ===== S: 最重要 =====
    if re.search(r'決算短信', t):                                                     return ("決算短信", "S")
    if re.search(r'上場廃止', t):                                                     return ("上場廃止", "S")
    if re.search(r'事業.*継続性|継続企業|ゴーイング', t):                                return ("継続企業疑義(GC)", "S")
    if re.search(r'公開買付|TOB|MBO', t):                                             return ("TOB・MBO", "S")
    if re.search(r'大規模買付.*対応|買収防衛|ポイズンピル', t):                           return ("買収防衛策", "S")
    if re.search(r'業績.*修正|修正.*業績|業績予想.*修正|業績.*下方|業績.*上方', t):       return ("業績修正", "S")
    if re.search(r'特別損失.*計上.*業績|特別利益.*計上.*業績|減損.*業績', t):            return ("業績修正", "S")

    # ===== A: 重要 =====
    if re.search(r'決算補足|決算説明|決算ハイライト|IR説明|投資家.*説明|決算.*説明会', t): return ("決算説明資料", "A")
    if re.search(r'業績予想|利益予想|売上予想|業績.*予想.*開示|業績.*予想.*変更|前年比速報', t): return ("業績予想", "A")
    if re.search(r'第三者割当|公募増資|新株式.*発行', t):                               return ("第三者割当・公募増資", "A")
    if re.search(r'新株予約権.*発行|ワラント|MSワラント|ライツ', t):                     return ("新株予約権発行", "A")
    if re.search(r'転換社債|CB.*発行|ユーロ円建.*転換社債', t):                         return ("転換社債(CB)発行", "A")
    if re.search(r'株式分割|株式併合', t):                                             return ("株式分割・併合", "A")
    if re.search(r'自己株式.*取得|自社株.*取得|自己株式立会外|ToSTNeT|ＴｏＳＴＮｅＴ', t): return ("自己株式取得", "A")
    if re.search(r'自己株式.*消却|自社株.*消却', t):                                   return ("自己株式消却", "A")
    if re.search(r'配当.*増額|増配|特別配当|記念配当|配当.*復活|配当.*廃止|配当.*減額|減配|配当.*変更|配当支払.*変更', t): return ("配当変更（増減配）", "A")
    if re.search(r'剰余金の配当|剰余金配当.*確定|配当に関するお知らせ|配当.*確定|資本剰余金.*配当|純資産減少割合.*確定', t): return ("配当", "A")
    if re.search(r'分配金', t):                                                       return ("分配金", "A")
    if re.search(r'合併|株式交換|株式移転|吸収分割|新設分割|会社分割', t):               return ("合併・組織再編", "A")
    if re.search(r'特定子会社.*異動', t):                                              return ("子会社化・買収", "A")
    if re.search(r'買収|完全子会社化|子会社.*株式.*取得|株式.*取得.*子会社', t):         return ("子会社化・買収", "A")
    if re.search(r'特別転進|早期退職|希望退職|人員削減|リストラ', t):                    return ("リストラ・希望退職", "A")
    if re.search(r'特別利益', t):                                                      return ("特別利益", "A")
    if re.search(r'特別損失|減損損失', t):                                              return ("特別損失", "A")
    if re.search(r'火災|爆発|事故.*発生|災害', t):                                    return ("インシデント（災害・事故）", "A")
    if re.search(r'サイバー|不正アクセス|情報漏洩|システム.*障害', t):                   return ("インシデント（セキュリティ）", "A")
    if re.search(r'不正|横領|粉飾|調査委員会|第三者委員会', t):                         return ("不祥事・社内調査", "A")
    if re.search(r'行政処分|営業停止|課徴金|業務改善命令', t):                          return ("行政処分", "A")
    if re.search(r'立会外分売', t):                                                   return ("立会外分売", "A")
    if re.search(r'売出し|発行価格.*決定|売出価格.*決定', t):                           return ("株式売出し", "A")
    if re.search(r'デット.*エクイティ|債権.*株式化', t):                                return ("DES（債権株式化）", "A")
    if re.search(r'中期経営計画|経営計画|経営方針', t):                                 return ("中期経営計画", "A")
    if re.search(r'事業計画.*成長可能性', t):                                          return ("事業計画（グロース）", "A")
    if re.search(r'仮差押|差押|訴訟|判決|和解|調停', t):                               return ("訴訟・法的", "A")
    if re.search(r'主要株主.*異動|大株主.*変更|筆頭株主', t):                           return ("主要株主異動", "A")
    if re.search(r'公認会計士.*異動|監査人.*異動|監査法人.*変更', t):                    return ("監査人異動", "A")
    if re.search(r'最高経営責任者|CEO.*異動|代表取締役.*異動|代表取締役.*就任|代表取締役.*退任'
                 r'|社長.*交代|社長.*就任|代表執行役.*異動|代表執行役.*就任', t):        return ("役員異動（代表クラス）", "A")

    # ===== B: 参考 =====
    if re.search(r'役員|取締役|監査役|人事異動|経営体制|執行体制|経営執行', t):          return ("役員・人事", "B")
    if re.search(r'月次|(?=.*月度)(?=.*[KＫ][PＰ][IＩ])', t):                          return ("月次開示", "B")
    if re.search(r'稼働率|入居率|出荷台数|販売台数|販売数量|解約率|チャーン|ARPU|ARR|MRR', t):
                                                                                      return ("業績の重要な先行指標", "B")
    if re.search(r'業務提携|資本業務提携|資本提携|合弁.*設立', t):                       return ("提携・協業", "B")
    if re.search(r'受注高|受注残高|受注残', t):                                         return ("受注高/受注残高", "B")
    if re.search(r'受注|契約締結|基本合意|覚書締結', t):                                return ("大型受注・契約", "B")
    if re.search(r'子会社.*設立|孫会社|関係会社.*設立|海外.*設立', t):                  return ("子会社設立", "B")
    if re.search(r'子会社.*持分.*譲渡|子会社.*売却|事業.*譲渡|撤退', t):               return ("事業・子会社売却", "B")
    if re.search(r'販売用不動産.*購入|不動産.*取得|固定資産.*取得|物件.*取得|土地.*取得', t): return ("資産取得（不動産）", "B")
    if re.search(r'不動産.*譲渡|固定資産.*譲渡|物件.*売却|不動産.*売却|信託受益権.*譲渡', t): return ("資産売却（不動産）", "B")
    if re.search(r'株主総会|定時総会|臨時総会', t):                                    return ("株主総会", "B")
    if re.search(r'株主優待|優待制度', t):                                             return ("株主優待", "B")
    if re.search(r'暗号資産|ビットコイン|イーサリアム', t):                              return ("暗号資産", "B")
    if re.search(r'格付', t):                                                         return ("格付", "B")
    if re.search(r'開示事項.*経過|開示事項.*変更|開示事項.*追加|開示.*延期', t):         return ("開示事項の経過・変更", "B")
    if re.search(r'訂正', t):                                                         return ("訂正", "B")
    if re.search(r'連結子会社.*配当金受領|子会社.*配当金受領', t):                       return ("子会社からの配当受領", "B")
    if re.search(r'社債.*発行|普通社債|無担保社債|シンジケートローン|借入.*締結'
                 r'|資金.*借入|当座貸越|コミットメントライン', t):                       return ("資金調達（社債・借入）", "B")

    # ===== C: 不要 =====
    if re.search(r'日々の開示事項', t):                                               return ("ETF/ETN日々開示", "C")
    if re.search(r'ETFの収益分配|ETFの分配金', t):                                    return ("ETF分配金", "C")
    if re.search(r'ETF|ＥＴＦ', t):                                                  return ("ETF関連", "C")
    if _is_reit(item) and re.search(r'資金.*借入|投資法人債|借換|期限前弁済|利率決定|金利決定', t):
                                                                                      return ("J-REIT（資金調達）", "C")
    if re.search(r'株式給付信託|J-ESOP|BBT.*追加拠出|持株会.*譲渡制限|譲渡制限付株式.*報酬'
                 r'|自己株式.*処分.*従業員|従業員.*自己株式.*処分|譲渡制限付株式.*自己株式.*処分', t):
                                                                                      return ("株式報酬制度（ESOP等）", "C")
    if re.search(r'ストックオプション.*内容確定|ストック.*オプション.*確定|新株予約権.*確定'
                 r'|ストック.*オプションに関するお知らせ', t):                           return ("SO行使価額確定", "C")
    if re.search(r'本店.*移転|本社.*移転', t):                                        return ("本店移転", "C")
    if re.search(r'定款.*変更|定款の一部変更', t):                                     return ("定款変更", "C")
    if re.search(r'監査等委員会.*移行|指名委員会.*移行|ガバナンス.*改定|内部統制.*改定', t): return ("ガバナンス変更", "C")
    if re.search(r'資本金.*減少|資本準備金.*減少|資本剰余金.*振替|別途積立金.*取崩', t):  return ("資本構成変更（税務整理）", "C")
    if re.search(r'支配株主', t):                                                     return ("支配株主関連", "C")
    if re.search(r'有価証券報告|四半期報告', t):                                       return ("有価証券報告書", "C")

    return ("その他（未分類）", "B")


def is_needed(category: str, priority: str) -> bool:
    """この開示をダウンロードすべきか判定する."""
    if priority == "C":
        return False
    if category in _SKIP_B_CATEGORIES:
        return False
    return True


# ============================================================
# ファイル名生成
# ============================================================

_INVALID_CHARS   = re.compile(r'[<>:"/\\|?*\r\n\t]')
_LEGAL_ENTITY    = re.compile(
    r'株式会社|（株）|㈱|有限会社|（有）|合同会社|合資会社'
    r'|一般社団法人|公益社団法人|一般財団法人|公益財団法人'
)


def _sanitize(s: str, max_len: int = 50) -> str:
    """ファイル名パーツから無効文字を除去して長さを制限する."""
    s = _INVALID_CHARS.sub('', s).strip('. ')
    return s[:max_len]


def _clean_company_name(name: str) -> str:
    """会社名から法人格表記（株式会社等）を除去する."""
    return _LEGAL_ENTITY.sub('', name).strip()


def make_filename(tdnet: dict, category: str) -> str:
    """開示レコードから保存ファイル名を生成する.

    Format:
        {日付}_{証券コード4桁}_{会社名}_{カテゴリ}_{ファイル概要}_{オリジナルファイル名}.pdf
    """
    # 日付 (YYYYMMDD)
    pubdate  = tdnet.get("pubdate", "")
    date_str = pubdate[:10].replace("-", "")          # "2026-02-24" → "20260224"

    # 証券コード4桁（TDnetの5桁コードの先頭4文字）
    code4 = (tdnet.get("company_code") or "")[:4]

    # 会社名（法人格除去・サニタイズ・最大20文字）
    name = _sanitize(_clean_company_name(tdnet.get("company_name") or ""), max_len=20)

    # カテゴリ（サニタイズ・最大20文字）
    cat = _sanitize(category, max_len=20)

    # ファイル概要 = TDNetのページタイトル（サニタイズ・最大40文字）
    title = _sanitize(tdnet.get("title") or "", max_len=40)

    # オリジナルファイル名（拡張子なし）
    doc_url   = tdnet.get("document_url") or ""
    orig_stem = Path(doc_url.rstrip("/").split("/")[-1]).stem   # "140120260224567725"

    parts    = [p for p in [date_str, code4, name, cat, title, orig_stem] if p]
    return "_".join(parts) + ".pdf"


# ============================================================
# TDnet スクレイピング
# ============================================================

def fetch_one_page(date_str: str, page: int) -> list[dict]:
    """公式TDnetから1ページ分の開示一覧を取得する.

    Args:
        date_str: 日付文字列 (YYYYMMDD)
        page:     ページ番号 (1〜)

    Returns:
        開示レコードのリスト。ページが存在しない場合は空リスト。
    """
    url = f"{TDNET_BASE_URL}I_list_{page:03d}_{date_str}.html"
    try:
        resp = curl_requests.get(
            url, timeout=REQUEST_TIMEOUT, headers=HEADERS, impersonate="chrome124"
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] ページ取得失敗 ({url}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # kjTime クラスのセルを含む table を探す（テーブル順序に依存しない）
    target_table = None
    for tbl in soup.find_all("table"):
        if tbl.find(class_=re.compile(r"kjTime")):
            target_table = tbl
            break
    if target_table is None:
        return []

    disclosures = []
    for tr in target_table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        rec: dict = {}
        for td in tds:
            cls  = " ".join(td.get("class", []))
            text = td.get_text(strip=True)

            if "kjTime" in cls:
                rec["time"] = text
            elif "kjCode" in cls:
                rec["company_code"] = text
            elif "kjName" in cls:
                rec["company_name"] = text
            elif "kjTitle" in cls:
                rec["title"] = text
                a_tag = td.find("a")
                if a_tag:
                    href = a_tag.get("href", "")
                    rec["document_url"] = urljoin(TDNET_BASE_URL, href)
            elif "kjXbrl" in cls:
                a_tag = td.find("a")
                if a_tag:
                    href = a_tag.get("href", "")
                    rec["url_xbrl"] = urljoin(TDNET_BASE_URL, href)

        if rec.get("company_code") and rec.get("title") and rec.get("document_url"):
            time_val      = rec.get("time", "00:00")
            rec["pubdate"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_val}:00"
            fn             = rec["document_url"].rstrip("/").split("/")[-1]
            rec["id"]      = fn.replace(".pdf", "")
            disclosures.append(rec)

    return disclosures


def fetch_disclosures(date_from: str, date_to: str) -> list[dict]:
    """公式TDnetサイトから指定期間の適時開示一覧を取得する.

    1日ずつループし、各日について複数ページを取得する。
    休日・祝日はページが存在しないため自動的にスキップされる。

    Args:
        date_from: 開始日 (YYYYMMDD)
        date_to:   終了日 (YYYYMMDD)

    Returns:
        開示レコードのリスト
    """
    d_from  = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to    = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))
    current = d_from

    all_items: list[dict] = []

    while current <= d_to:
        date_str  = current.strftime("%Y%m%d")
        day_count = 0
        page      = 1

        while True:
            items = fetch_one_page(date_str, page)
            if not items:
                break
            all_items.extend(items)
            day_count += len(items)
            page += 1
            time.sleep(PAGE_FETCH_INTERVAL)

        if day_count:
            print(f"  [{date_str}] {day_count} 件取得")

        current += timedelta(days=1)

    print(f"[TDnet] 取得件数合計: {len(all_items)}")
    return all_items


def fetch_disclosures_yanoshin(date_from: str, date_to: str) -> list[dict]:
    """yanoshin APIから1日ずつ開示一覧を取得する（公式サイトにない過去データ用）.

    Args:
        date_from: 開始日 (YYYYMMDD)
        date_to:   終了日 (YYYYMMDD)

    Returns:
        開示レコードのリスト（公式スクレイピングと同形式）
    """
    d_from  = date(int(date_from[:4]), int(date_from[4:6]), int(date_from[6:8]))
    d_to    = date(int(date_to[:4]),   int(date_to[4:6]),   int(date_to[6:8]))
    current = d_from
    all_items: list[dict] = []

    while current <= d_to:
        date_str = current.strftime("%Y%m%d")
        url = f"{YANOSHIN_BASE_URL}{date_str}-{date_str}.json?limit=9999"
        try:
            resp = curl_requests.get(
                url, timeout=60, headers=HEADERS, impersonate="chrome124"
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [WARN] yanoshin API 取得失敗 ({date_str}): {e}")
            current += timedelta(days=1)
            time.sleep(PAGE_FETCH_INTERVAL)
            continue

        day_items = []
        for item in data.get("items", []):
            t = item.get("Tdnet", {})
            if not t:
                continue
            # rd.php? リダイレクトを解除して実際の PDF URL を取得
            doc = t.get("document_url", "")
            if "rd.php?" in doc:
                t["document_url"] = doc.split("rd.php?", 1)[1]
            # company_code を 4 桁に正規化（yanoshin は 5 桁で返す場合あり）
            code = (t.get("company_code") or "")
            t["company_code"] = code[:4] if len(code) > 4 else code
            # id をファイル名から生成（拡張子なし）
            fn = (t.get("document_url") or "").rstrip("/").split("/")[-1]
            t["id"] = fn.replace(".pdf", "")
            day_items.append(t)

        if day_items:
            print(f"  [{date_str}] {len(day_items)} 件取得 (yanoshin)")
            all_items.extend(day_items)

        current += timedelta(days=1)
        time.sleep(PAGE_FETCH_INTERVAL)

    print(f"[yanoshin] 取得件数合計: {len(all_items)}")
    return all_items


def _fetch_bytes(url: str) -> bytes | None:
    """URL からバイト列を取得する."""
    try:
        resp = curl_requests.get(
            url, timeout=REQUEST_TIMEOUT, headers=HEADERS, impersonate="chrome124"
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"  [ERROR] 取得失敗: {url} → {e}")
        return None


def download_file(url: str, save_path: Path) -> bool:
    """URL のファイルをダウンロードしてローカルに保存する（local 専用）."""
    content = _fetch_bytes(url)
    if content is None:
        return False
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(content)
    return True


# ---- GCS (Colab 専用) ----

_gcs_client = None


def _get_gcs_client():
    """GCS クライアントを取得する（遅延初期化）."""
    global _gcs_client
    if _gcs_client is not None:
        return _gcs_client
    from google.cloud import storage
    if RUNTIME == "colab_personal":
        import json
        from google.colab import userdata
        from google.oauth2 import service_account
        key_info = json.loads(userdata.get("GCP_SA_KEY"))
        creds = service_account.Credentials.from_service_account_info(key_info)
        _gcs_client = storage.Client(credentials=creds)
    else:  # colab_enterprise / cloudrun: ADC を使用
        _gcs_client = storage.Client()
    return _gcs_client


def _gcs_blob_exists(blob_path: str) -> bool:
    """GCS 上に blob が存在するか確認する."""
    return _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).exists()


def _upload_to_gcs(content: bytes, blob_path: str) -> bool:
    """バイト列を GCS にアップロードする."""
    try:
        _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).upload_from_string(
            content, content_type="application/pdf"
        )
        return True
    except Exception as e:
        print(f"  [ERROR] GCS アップロード失敗: {blob_path} → {e}")
        return False


def _upload_csv_to_gcs(csv_text: str, blob_path: str) -> None:
    """CSV 文字列を GCS にアップロードする."""
    _get_gcs_client().bucket(GCS_BUCKET).blob(blob_path).upload_from_string(
        csv_text.encode("utf-8-sig"), content_type="text/csv; charset=utf-8"
    )


# ============================================================
# 再開（レジューム）ログ
# ============================================================

def _resume_log_ref(save_dir: Path, date_from: str, date_to: str) -> "Path | str":
    """再開ログの参照先を返す（local=Path、GCS=blob パス文字列）."""
    name = f"_resume_{date_from}_{date_to}.txt"
    if RUNTIME == "local":
        return save_dir / name
    return f"{GCS_PREFIX}/{name}"


def _load_done_dates(log_ref: "Path | str") -> set[str]:
    """完了済み日付セット (YYYYMMDD) を読み込む."""
    try:
        if isinstance(log_ref, Path):
            if not log_ref.exists():
                return set()
            lines = log_ref.read_text(encoding="utf-8").splitlines()
        else:
            blob = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            if not blob.exists():
                return set()
            lines = blob.download_as_text().splitlines()
        return {line[5:].strip() for line in lines if line.startswith("DONE:")}
    except Exception:
        return set()


def _mark_date_done(log_ref: "Path | str", date_str: str) -> None:
    """再開ログに完了日付 (YYYYMMDD) を追記する."""
    try:
        if isinstance(log_ref, Path):
            log_ref.parent.mkdir(parents=True, exist_ok=True)
            with open(log_ref, "a", encoding="utf-8") as f:
                f.write(f"DONE:{date_str}\n")
        else:
            blob     = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            existing = blob.download_as_text() if blob.exists() else ""
            blob.upload_from_string(existing + f"DONE:{date_str}\n")
    except Exception as e:
        print(f"  [WARN] 再開ログ書き込みエラー: {e}")


def _delete_resume_log(log_ref: "Path | str") -> None:
    """正常完了後に再開ログを削除する."""
    try:
        if isinstance(log_ref, Path):
            if log_ref.exists():
                log_ref.unlink()
                print(f"[再開ログ] 削除完了: {log_ref}")
        else:
            blob = _get_gcs_client().bucket(GCS_BUCKET).blob(log_ref)
            if blob.exists():
                blob.delete()
                print(f"[再開ログ] 削除完了: gs://{GCS_BUCKET}/{log_ref}")
    except Exception as e:
        print(f"  [WARN] 再開ログ削除エラー: {e}")


# ============================================================
# メイン
# ============================================================

def _resolve_dates() -> tuple[str, str]:
    """DATE_MODE に従って (date_from, date_to) を解決する."""
    today = date.today().strftime("%Y%m%d")
    if DATE_MODE == "t":
        return today, today
    elif DATE_MODE == "1":
        return DATE_SINGLE, DATE_SINGLE
    elif DATE_MODE == "r":
        return DATE_FROM, DATE_TO
    else:
        raise ValueError(f"DATE_MODE が不正: {DATE_MODE!r}  ('t' / '1' / 'r')")


def parse_args() -> argparse.Namespace:
    # Colab環境では sys.argv にJupyterカーネルの引数（-f /path/kernel-xxx.json 等）が
    # 混入するため argparse が unrecognized arguments でクラッシュする。
    # Colab では DATE_MODE / SAVE_DIR の設定ブロックを直接編集すること。
    if RUNTIME in ("colab_personal", "colab_enterprise"):
        return argparse.Namespace(date_from=None, date_to=None, save_dir=SAVE_DIR)

    parser = argparse.ArgumentParser(
        description="TDnet 適時開示ファイル一括ダウンロード",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "日付指定例:\n"
            "  --from 20260224              1日のみ\n"
            "  --from 20260217 --to 20260224  期間指定\n"
            "  (省略時はファイル冒頭の DATE_MODE に従う)"
        ),
    )
    parser.add_argument("--from", dest="date_from", default=None,
                        help="開始日 YYYYMMDD (省略時は DATE_MODE に従う)")
    parser.add_argument("--to",   dest="date_to",   default=None,
                        help="終了日 YYYYMMDD (省略時は --from と同じ日)")
    parser.add_argument("--save-dir", type=Path, default=SAVE_DIR,
                        help="保存先ディレクトリ (default: %(default)s)")
    parser.add_argument("--yanoshin", action="store_true", default=False,
                        help="yanoshin APIを使用（公式サイトにない過去データ用）")
    parser.add_argument("--force", action="store_true", default=False,
                        help="既存GCSファイルを上書きする")
    return parser.parse_args()


def main() -> None:
    start_time = datetime.now(JST)
    log_cap    = LogCapture()
    log_cap.start()
    date_label = "不明"

    try:
        args = parse_args()

        # 引数指定 > DATE_MODE の優先順位
        if args.date_from:
            date_from = args.date_from
            date_to   = args.date_to or args.date_from   # --to 省略時は --from と同日
        else:
            date_from, date_to = _resolve_dates()

        save_dir: Path = args.save_dir
        date_label = f"{date_from} ～ {date_to}" if date_from != date_to else date_from

        print("=== TDnet 適時開示ダウンロード ===")
        print(f"実行環境 : {RUNTIME}")
        print(f"期間     : {date_from} ～ {date_to}")
        if RUNTIME == "local":
            print(f"保存先   : {save_dir}")
        else:
            print(f"保存先   : gs://{GCS_BUCKET}/{GCS_PREFIX}/")
        print()

        # 1. 開示一覧を取得
        use_yanoshin = getattr(args, "yanoshin", False) or USE_YANOSHIN
        if use_yanoshin:
            print("[データソース] yanoshin API（過去データ用）")
            disclosures = fetch_disclosures_yanoshin(date_from, date_to)
        else:
            disclosures = fetch_disclosures(date_from, date_to)
        if not disclosures:
            print("対象データがありませんでした。")
            log_cap.stop()
            return

        # 2. ダウンロード実行（日付別にグループ化して処理・再開をサポート）
        success     = 0
        skip_dup    = 0   # 既存ファイルスキップ
        skip_cat    = 0   # カテゴリフィルタスキップ
        skip_resume = 0   # 再開スキップ
        errors      = 0
        index_rows: list[dict] = []

        # --- 再開ログ ---
        resume_ref  = _resume_log_ref(save_dir, date_from, date_to)
        done_dates  = set() if args.force else _load_done_dates(resume_ref)
        resume_mode = bool(done_dates)
        if resume_mode:
            print(f"\n{'='*50}")
            print(f"[再開モード] 再開ログを検出しました（完了済み {len(done_dates)} 日）")
            print(f"{'='*50}\n")

        # 日付ごとにグループ化（pubdate の先頭 8 文字 = YYYYMMDD）
        disclosures_by_date: dict[str, list[dict]] = {}
        for item in disclosures:
            d = (item.get("pubdate") or "")[:10].replace("-", "")
            disclosures_by_date.setdefault(d, []).append(item)

        total    = len(disclosures)
        item_num = 0

        for date_str in sorted(disclosures_by_date.keys()):
            day_items = disclosures_by_date[date_str]

            # 完了済み日付はスキップ
            if date_str in done_dates:
                skip_resume += sum(
                    1 for it in day_items
                    if is_needed(*classify(it.get("title", ""), it))
                )
                item_num += len(day_items)
                print(f"[再開] {date_str}: スキップ（完了済み）")
                continue

            for tdnet in day_items:
                item_num += 1
                title   = tdnet.get("title") or ""
                doc_url = tdnet.get("document_url") or ""
                if not doc_url:
                    continue

                # カテゴリ判定
                category, priority = classify(title, tdnet)

                # フィルタリング
                if not is_needed(category, priority):
                    skip_cat += 1
                    print(f"[{item_num}/{total}] スキップ ({priority}/{category}): {title[:50]}")
                    continue

                # ファイル名（証券コード4桁のサブディレクトリ）
                code4    = (tdnet.get("company_code") or "")[:4]
                filename = make_filename(tdnet, category)

                # インデックス記録
                index_rows.append({
                    "id":           tdnet.get("id", ""),
                    "pubdate":      tdnet.get("pubdate", ""),
                    "company_code": code4,
                    "company_name": tdnet.get("company_name", ""),
                    "category":     category,
                    "priority":     priority,
                    "title":        title,
                    "filename":     filename,
                    "url":          doc_url,
                })

                if RUNTIME == "local":
                    save_path = save_dir / code4 / filename
                    if save_path.exists():
                        print(f"[{item_num}/{total}] スキップ (既存): {filename}")
                        skip_dup += 1
                        continue
                    print(f"[{item_num}/{total}] ダウンロード: {filename}")
                    if download_file(doc_url, save_path):
                        success += 1
                    else:
                        errors += 1

                else:  # Colab / Cloud Run → GCS
                    blob_path = f"{GCS_PREFIX}/{code4}/{filename}"
                    if not args.force and _gcs_blob_exists(blob_path):
                        print(f"[{item_num}/{total}] スキップ (既存): {filename}")
                        skip_dup += 1
                        continue
                    content = _fetch_bytes(doc_url)
                    print(f"[{item_num}/{total}] アップロード: gs://{GCS_BUCKET}/{blob_path}")
                    if content and _upload_to_gcs(content, blob_path):
                        success += 1
                    else:
                        errors += 1

                time.sleep(DOWNLOAD_INTERVAL)

            # 1日分完了 → 再開ログに記録
            _mark_date_done(resume_ref, date_str)
            time.sleep(1.0)  # 日付間スリープ

        # 全日付処理完了 → 再開ログを削除
        _delete_resume_log(resume_ref)

        # 3. インデックス CSV を保存
        import io
        fieldnames = ["id", "pubdate", "company_code", "company_name",
                      "category", "priority", "title", "filename", "url"]
        index_name = f"index_{date_from}_{date_to}.csv"

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(index_rows)
        csv_text = buf.getvalue()

        if RUNTIME == "local":
            save_dir.mkdir(parents=True, exist_ok=True)
            index_path = save_dir / index_name
            with open(index_path, "w", newline="", encoding="utf-8-sig") as f:
                f.write(csv_text)
            index_label = str(index_path)
        else:  # Colab → GCS
            blob_path = f"{GCS_PREFIX}/{index_name}"
            _upload_csv_to_gcs(csv_text, blob_path)
            index_label = f"gs://{GCS_BUCKET}/{blob_path}"

        elapsed = datetime.now(JST) - start_time
        print()
        print("=== 完了 ===")
        print(f"  DL成功             : {success}")
        print(f"  スキップ（既存）     : {skip_dup}")
        print(f"  スキップ（カテゴリ）  : {skip_cat}")
        if skip_resume:
            print(f"  スキップ（再開）    : {skip_resume}")
        print(f"  エラー             : {errors}")
        print(f"  インデックス        : {index_label}")

        log_cap.stop()

    except Exception as e:
        log_text = log_cap.stop()
        tb_str   = traceback.format_exc()
        print(f"[FATAL] {e}\n{tb_str}", file=sys.stderr)
        send_mail(
            f"[TDNET] エラー {date_label}",
            f"TDnet ダウンロードでエラーが発生しました。\n\n"
            f"対象期間  : {date_label}\n"
            f"エラー    : {e}\n\n"
            f"トレースバック:\n{tb_str}",
            attachment_text=log_text or None,
            attachment_name="tdnet_log.txt",
        )
        raise


if __name__ == "__main__":
    main()
