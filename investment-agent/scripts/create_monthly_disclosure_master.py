"""月次開示収集マスタ CSV を作成して GCS にアップロードする."""

import io
import csv
from google.cloud import storage
from google.oauth2 import service_account

KEY_PATH = "keys/gcp-service-account.json"
GCS_PATH = "config/monthly_disclosure_master.csv"
BUCKET = "stock_data_1930932"

# マスタデータ（DISCLOSURE_TYPE=c: TDnetにデータなし → 会社独自IR開示）
MASTER_DATA = [
    # TICKER, COMPANY_NAME, DISCLOSURE_TYPE, IR_URL, NOTES, UPDATED_AT
    ("2678", "アスクル", "c", "https://www.askul.co.jp/corp/investor/", "月次販売速報（BtoB・BtoC）を毎月開示", "2026-03-11"),
    ("2702", "日本マクドナルドホールディングス", "c", "https://www.nikkei.com/nkd/company/kigyo/?scode=2702", "月次既存店売上高・全店売上高を毎月開示", "2026-03-11"),
    ("3649", "ファインデックス", "c", "https://findex.co.jp/ir/", "医療システム会社。月次開示有無要確認", "2026-03-11"),
    ("3691", "デジタルプラス", "c", "https://digital-plus.co.jp/ir", "フィンテック・デジタルマーケティング。月次開示有無要確認", "2026-03-11"),
    ("4177", "ｉ－ｐｌｕｇ", "c", "https://i-plug.co.jp/ir/", "新卒ダイレクトリクルーティング。月次開示有無要確認", "2026-03-11"),
    ("4523", "エーザイ", "c", "https://www.eisai.co.jp/ir/", "製薬会社。月次売上速報有無要確認", "2026-03-11"),
    ("4666", "パーク２４", "c", "https://www.park24.co.jp/ir/financial/monthly.html", "月次業績状況（稼働台数・売上）を毎月開示", "2026-03-11"),
    ("4680", "ラウンドワン", "c", "https://www.round1.co.jp/company/ir/sales.html", "月次売上げ状況を毎月開示", "2026-03-11"),
    ("4732", "ユー・エス・エス", "c", "https://www.ussnet.co.jp/ir/library/monthly/index.html", "月次データ（中古車オークション台数・成約台数）を毎月開示", "2026-03-11"),
    ("4889", "レナサイエンス", "c", "https://www.renascience.co.jp/ir/", "創薬ベンチャー（東北大発）。月次開示有無要確認", "2026-03-11"),
    ("4890", "坪田ラボ", "c", "https://tsubota-lab.com/ir/", "バイオベンチャー（慶應大発）。月次開示有無要確認", "2026-03-11"),
    ("5589", "オートサーバー", "c", "https://www.autoserver.co.jp/ir/", "中古車販売。月次開示有無要確認", "2026-03-11"),
    ("5888", "DAIWA CYCLE", "c", "https://www.daiwa-cycle.co.jp/ir/", "自転車専門チェーン。月次開示有無要確認", "2026-03-11"),
    ("6071", "ＩＢＪ", "c", "https://www.ibjapan.jp/ir", "婚活サービス。月次成婚数等開示有無要確認", "2026-03-11"),
    ("6073", "アサンテ", "c", "https://www.asante.co.jp/ir/", "シロアリ駆除・住環境サービス。月次開示有無要確認", "2026-03-11"),
    ("6146", "ディスコ", "c", "https://www.disco.co.jp/jp/ir/", "半導体製造装置メーカー。月次売上（Will Call）を毎月開示", "2026-03-11"),
    ("6425", "ユニバーサルエンターテインメント", "c", "https://www.universal-777.co.jp/ir/library/", "パチスロ機製造。月次販売台数開示有無要確認", "2026-03-11"),
    ("6535", "アイモバイル", "c", "https://www.i-mobile.co.jp/ir/index.html", "ネット広告配信。月次開示有無要確認", "2026-03-11"),
    ("6627", "テラプローブ", "c", "https://www.teraprobe.com/ir/", "半導体テスト。月次開示有無要確認", "2026-03-11"),
    ("7157", "ライフネット生命保険", "c", "https://ir.lifenet-seimei.co.jp/ja/ir/financial/monthly.html", "月次業績速報（新契約件数・保有契約件数）を毎月開示", "2026-03-11"),
    ("7162", "アストマックス", "c", "https://www.astmax.co.jp/ir/", "エネルギー・資産運用。月次開示有無要確認", "2026-03-11"),
    ("7175", "今村証券", "c", "", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("7177", "GMOフィナンシャルホールディングス", "c", "https://www.gmofh.com/ir/monthly_report.html", "月次開示情報（信用取引残高・FX預り証拠金残高・預り資産）を毎月開示", "2026-03-11"),
    ("7198", "SBIアルヒ", "c", "https://www.sbiaruhi-group.jp/english/ir", "住宅ローン専門金融機関。月次実行件数開示有無要確認", "2026-03-11"),
    ("7326", "SBIインシュアランスグループ", "c", "https://www.sbiig.co.jp/ir/", "保険持株会社。月次保有契約件数等開示有無要確認", "2026-03-11"),
    ("7455", "パリミキホールディングス", "c", "https://www.paris-miki.com/hd/investor/", "眼鏡小売。月次開示有無要確認", "2026-03-11"),
    ("7545", "西松屋チェーン", "c", "https://www.24028.jp/ir/", "子供服専門チェーン。月次売上速報を毎月開示", "2026-03-11"),
    ("8255", "アクシアル リテイリング", "c", "https://www.axial-r.com/ir", "食品スーパー（新潟）。月次業績推移を開示", "2026-03-11"),
    ("8613", "丸三証券", "c", "https://www.marusan-sec.co.jp/ir/", "証券会社。月次売買代金・口座数等開示有無要確認", "2026-03-11"),
    ("8614", "東洋証券", "c", "https://www.toyo-sec.co.jp/ir/", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8622", "水戸証券", "c", "https://www.mito.co.jp/inquiry/ir.html", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8624", "いちよし証券", "c", "https://www.ichiyoshi.co.jp/stockholder", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8628", "松井証券", "c", "https://www.matsui.co.jp/company/ir/disclosure/business/", "月次業績開示（売買代金・口座数・信用残高）を毎月開示", "2026-03-11"),
    ("8700", "丸八証券", "c", "", "証券会社（名古屋）。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8706", "極東証券", "c", "https://www.kyokuto-sec.co.jp/ir/", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8707", "岩井コスモホールディングス", "c", "https://www.iwaicosmo-hd.jp/ir/", "証券持株会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8708", "アイザワ証券グループ", "c", "https://www.aizawa.co.jp/ir/", "証券会社。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8739", "スパークス・グループ", "c", "https://www.sparx.jp/ir/", "資産運用会社。月次運用残高等開示有無要確認", "2026-03-11"),
    ("8742", "小林洋行", "c", "https://www.kobayashiyoko.com/ir/", "商品先物取引・フジトミ証券が中核。月次売買代金等開示有無要確認", "2026-03-11"),
    ("8975", "いちごオフィスリート投資法人", "c", "https://www.ichigo-office.co.jp/ir/", "J-REIT（オフィス）。月次稼働率・ポートフォリオ情報を開示", "2026-03-11"),
    ("9904", "ベリテ", "c", "https://www.verite.jp/aboutus/irinfo.html", "宝飾品小売。月次販売速報開示有無要確認", "2026-03-11"),
    ("9979", "大庄", "c", "https://www.daisyo.co.jp/company/ir/index.html", "居酒屋チェーン（庄や等）。月次既存店売上高を毎月開示", "2026-03-11"),
]

FIELDNAMES = ["TICKER", "COMPANY_NAME", "DISCLOSURE_TYPE", "IR_URL", "NOTES", "UPDATED_AT"]


def create_csv_content() -> str:
    """CSVコンテンツを生成する."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    for row in MASTER_DATA:
        writer.writerow({
            "TICKER": row[0],
            "COMPANY_NAME": row[1],
            "DISCLOSURE_TYPE": row[2],
            "IR_URL": row[3],
            "NOTES": row[4],
            "UPDATED_AT": row[5],
        })
    return output.getvalue()


def upload_to_gcs(csv_content: str) -> None:
    """CSVをGCSにアップロードする."""
    creds = service_account.Credentials.from_service_account_file(KEY_PATH)
    client = storage.Client(project="gmailpj-357912", credentials=creds)
    bucket = client.bucket(BUCKET)
    blob = bucket.blob(GCS_PATH)
    blob.upload_from_string(csv_content.encode("utf-8"), content_type="text/csv; charset=utf-8")
    print(f"アップロード完了: gs://{BUCKET}/{GCS_PATH}")


if __name__ == "__main__":
    csv_content = create_csv_content()
    row_count = csv_content.count("\n") - 1  # ヘッダ行を除く
    print(f"CSV生成完了: {row_count}行 (ヘッダ除く)")
    upload_to_gcs(csv_content)
