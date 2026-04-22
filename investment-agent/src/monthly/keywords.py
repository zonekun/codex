"""月次開示パイプライン共通キーワード定数 (L-1 責務分割: 初段)。

複数スクリプトで独立に定義されていた正規表現・集合を一箇所に集約。
将来的には download_monthly.py / extract_monthly_data.py も import 移行予定
(M-5: 現在は update_monthly_adapters.py のみ連携、挙動差が大きいため段階移行)。

使用箇所:
- scripts/update_monthly_adapters.py (全て import 済)
- scripts/download_monthly.py (M-5 未着手、独自定義 EIR_MONTHLY_RE を使用中)
- scripts/extract_monthly_data.py (M-5 未着手、独自定義 _MONTHLY_KW を使用中)
"""
from __future__ import annotations

import re


# eIR パターン（eol/eir-parts 経由の月次開示 API）
EIR_PATTERN = re.compile(r"eir-parts\.net|ssl4\.eir-parts|eolparts", re.I)
EIR_CODE_RE = re.compile(r"eir-parts\.net/(?:V4Public/eir/|[^/]+/)(\d+)/", re.I)
EIR_JS_RE = re.compile(r"(https://ssl\d+\.eir-parts\.net/V4Public/eir/(\d+)/[^\"']+\.js)", re.I)

# ダウンロード拡張子
DOWNLOAD_EXT = re.compile(r"\.(pdf|xlsx|xls|csv)(\?.*)?$", re.IGNORECASE)

# href_pattern 既定値（scrape_links 型で link_href_pattern が推定できなかった場合のデフォルト）
DEFAULT_HREF_PATTERN = r"\.(pdf|xlsx|xls|csv)$"

# 月次キーワード（ページスクレイプ用）
MONTHLY_TEXT = re.compile(
    r"月次|monthly|売上速報|月別|受注速報|受注実績|販売台数|輸送実績|旅客数|搭乗実績|"
    r"稼働実績|出荷量|売上高速報|月次速報|月報|月次販売|月次データ|月次実績|"
    r"sokuhou|m-report|monthly\.pdf|月次報告|月次売上",
    re.IGNORECASE,
)

# 月次開示ではないと判断するキーワード（これが含まれるリンクテキストは除外）
# L-5: `配当|役員|人事` は広すぎて `月次配当速報` 等の正当な月次リンクを誤除外する。
# narrow 化して corp event 単独表記のみ弾くように変更 (`配当金`/`役員人事`/`人事異動`)。
NON_MONTHLY_EXCLUDE = re.compile(
    r"決算説明|説明会資料|スクリプト|自己株式|自社株|社債|グリーンボンド|"
    r"電子公告|有価証券報告|四半期報告|コーポレートガバナンス|"
    r"株主優待|株主総会|定時株主|招集通知|ニュースリリース|プレスリリース|"
    r"中期経営|経営戦略|IR資料|統合報告|アニュアルレポート|"
    r"配当金|配当予想|配当方針|役員人事|人事異動|組織変更|M&A|買収|合併|子会社|関係会社|"
    r"サステナビリティ|ESG|CSR",
    re.IGNORECASE,
)

# HTML テーブル月次キーワード
HTML_TABLE_TEXT = re.compile(
    r"月次|売上速報|月別売上|月次実績|月次データ",
    re.IGNORECASE,
)

# HTML テーブル月ヘッダーパターン（4月〜12月、1月〜3月 等）
HTML_TABLE_MONTH_HEADER = re.compile(r"(?:[1-9]|1[0-2])月")

# IR サブページ探索キーワード（メインページにリンクなし時のフォールバック）
IR_SUBPAGE_KEYWORDS = re.compile(
    r"/ir|IR[^\w]|アーカイブ|年別|一覧|ライブラリ|月次|バックナンバー|過去.*開示|irlist",
    re.IGNORECASE,
)

# 情報サイト除外リスト（Google 検索結果フィルタ用）
SKIP_DOMAINS = frozenset({
    "nikkei.com", "kabutan.jp", "minkabu.jp", "yahoo.co.jp", "yahoo.com",
    "google.com", "google.co.jp", "support.google.com", "bloomberg.co.jp", "reuters.com",
    "irbank.net", "stockweather.co.jp", "buffett-code.com",
    "twitter.com", "x.com", "facebook.com", "wikipedia.org",
    "invest.co.jp", "traders.co.jp", "ullet.com", "edinet.fsa.go.jp",
})

# Google リダイレクト汚染チェック用（知見 049 レートリミット検出と同一集合）
GOOGLE_CONTAMINATION_DOMAINS = frozenset({
    "support.google.com",
    "accounts.google.com",
    "policies.google.com",
    "google.com",
})

# Playwright / curl_cffi 共通 HTTP ヘッダ
SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}
