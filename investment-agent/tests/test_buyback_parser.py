"""自社株買い PDF パーサーの訓練/テスト検証.

Step 4 (訓練): GCS から PDF をダウンロードし parse_buyback_pdf() の正規表現精度を確認。
Step 5 (テスト): 訓練と別の銘柄・日付で同一正規表現を検証（凍結状態）。
"""

import io
import sys
from dataclasses import dataclass
from pathlib import Path

# プロジェクトルートの scripts/ を PYTHONPATH に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from google.cloud import storage
from google.oauth2 import service_account

from zaraba_tdnet_poller import parse_buyback_pdf, BuybackInfo


# ── GCS クライアント ──────────────────────────────────
def _gcs_client() -> storage.Client:
    key_path = Path(__file__).resolve().parent.parent / "keys" / "gcp-service-account.json"
    creds = service_account.Credentials.from_service_account_file(str(key_path))
    return storage.Client(credentials=creds, project=creds.project_id)


BUCKET_NAME = "stock_data_1930932"


def download_pdf(client: storage.Client, blob_name: str) -> bytes:
    """GCS から PDF バイト列をダウンロード."""
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


# ── テストケース定義 ──────────────────────────────────
@dataclass
class TrainCase:
    pattern: str       # A-I
    ticker: str
    blob_name: str
    title: str
    expect_pct: bool        # pct_of_outstanding が取れるべきか
    expect_tn3: bool        # is_tostnet3 が True であるべきか
    note: str = ""


# === 訓練セット (Step 4) ===
TRAIN_CASES: list[TrainCase] = [
    # Pattern A: 市場買付決定 (pct=True, tn3=False) — 2023-2025
    TrainCase("A", "7516",
              "tdnet/7516/20250411_7516_コーナン商_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120250409511923.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "6778",
              "tdnet/6778/20230309_6778_アルチザ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120230309527739.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "8737",
              "tdnet/8737/20240814_8737_あかつき本社_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120240813571387.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "265A",
              "tdnet/265A/20251114_265A_Ｇ－エイチエムコム_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120251114501930.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "5851",
              "tdnet/5851/20251014_5851_リョービ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120251014572576.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "7762",
              "tdnet/7762/20230213_7762_シチズン時計_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120230213507678.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "8511",
              "tdnet/8511/20230509_8511_日証金_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120230509560912.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),

    # Pattern B: ToSTNeT-3 のみ (pct=True, tn3=True) — 2023-2025
    TrainCase("B", "2229",
              "tdnet/2229/20251121_2229_カルビー_自己株式取得_自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら_140120251121507398.pdf",
              "自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True, note="全角TN3"),
    TrainCase("B", "6362",
              "tdnet/6362/20230530_6362_石井鉄_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120230530588219.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "2109",
              "tdnet/2109/20250515_2109_ＤＭ三井製糖_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120250515552730.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "3407",
              "tdnet/3407/20241223_3407_旭化成_自己株式取得_自己株式立会外買付取引（ToSTNeT-３）による自己株式の買付けに関するお知ら_140120241223542621.pdf",
              "自己株式立会外買付取引（ToSTNeT-３）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True, note="ToSTNeT-３ 混合"),
    TrainCase("B", "6262",
              "tdnet/6262/20251106_6262_ＰＥＧＡＳＵＳ_自己株式取得_自己株式立会外買付取引（ToSTNeT-３）による自己株式の買付けに関するお知ら_140120251106590035.pdf",
              "自己株式立会外買付取引（ToSTNeT-３）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "7182",
              "tdnet/7182/20250228_7182_ゆうちょ銀行_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120250228585365.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True, note="ゆうちょ大型"),

    # Pattern C: N-NET3（名証立会外）(tn3=True) — 2020-2025
    TrainCase("C", "3032",
              "tdnet/3032/20251127_3032_ゴルフ・ドゥ_自己株式取得_自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに_140120251127510533.pdf",
              "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに",
              expect_pct=True, expect_tn3=True, note="全角N-NET3 複合"),
    TrainCase("C", "2185",
              "tdnet/2185/20250212_2185_シイエム・シイ_自己株式取得_自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに関するお知らせ_140120250207566225.pdf",
              "自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに関するお知らせ",
              expect_pct=True, expect_tn3=True, note="全角N-NET3"),
    TrainCase("C", "2902",
              "tdnet/2902/20220926_2902_太陽化_自己株式取得_自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに_140120220926536166.pdf",
              "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに",
              expect_pct=True, expect_tn3=True),
    TrainCase("C", "7950",
              "tdnet/7950/20250213_7950_デコラックス_自己株式取得_自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに_140120250213573922.pdf",
              "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに",
              expect_pct=True, expect_tn3=True),
    TrainCase("C", "8076",
              "tdnet/8076/20200220_8076_カノークス_自己株式取得_自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに_140120200220468066.pdf",
              "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに",
              expect_pct=True, expect_tn3=True),

    # Pattern F: 複合（決定+TN3）(pct=True, tn3=True) — 2023-2025
    TrainCase("F", "9885",
              "tdnet/9885/20240828_9885_シャルレ_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120240826576489.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "3916",
              "tdnet/3916/20230313_3916_ＤＩＴ_自己株式取得_自己株式の取得および自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の_140120230310528499.pdf",
              "自己株式の取得および自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の",
              expect_pct=True, expect_tn3=True, note="全角TN3 複合"),
    TrainCase("F", "4234",
              "tdnet/4234/20241122_4234_サンエー化研_自己株式取得_自己株式取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付_140120241122528184.pdf",
              "自己株式取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "2332",
              "tdnet/2332/20250929_2332_クエスト_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-３）による自己株式の買_140120250929564475.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-３）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "9856",
              "tdnet/9856/20251110_9856_ケーユーＨＤ_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120251107592810.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),

    # Pattern G: 市場買付（決定以外）取得終了報告 — pct不確定だが try
    TrainCase("G", "1976",
              "tdnet/1976/20240206_1976_明星工業_自己株式取得_自己株式の市場買付及び取得終了に関するお知らせ_140120240206527437.pdf",
              "自己株式の市場買付及び取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了報告"),
    TrainCase("G", "8098",
              "tdnet/8098/20221017_8098_稲畑産_自己株式取得_自己株式の市場買付及び取得終了並びに自己株式の消却に関するお知らせ_140120221014544509.pdf",
              "自己株式の市場買付及び取得終了並びに自己株式の消却に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了+消却"),
    TrainCase("G", "6093",
              "tdnet/6093/20200605_6093_エスクローＡＪ_自己株式取得_自己株式の市場買付けおよび取得終了に関するお知らせ_140120200605437645.pdf",
              "自己株式の市場買付けおよび取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了"),
    TrainCase("G", "7381",
              "tdnet/7381/20220307_7381_北國ＦＨＤ_自己株式取得_自己株式の市場買付および取得終了に関するお知らせ_140120220307501190.pdf",
              "自己株式の市場買付および取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了"),
    TrainCase("G", "3371",
              "tdnet/3371/20200324_3371_ソフトクリエＨＤ_自己株式取得_自己株式の市場買付及び取得終了に関するお知らせ_140120200324483214.pdf",
              "自己株式の市場買付及び取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了"),

    # Pattern H: 取得枠設定 (pct=True, tn3=False) — 2020-2025
    TrainCase("H", "1518",
              "tdnet/1518/20250513_1518_三井松島ＨＤ_自己株式取得_自己株式の取得枠設定に関するお知らせ_140120250513546687.pdf",
              "自己株式の取得枠設定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("H", "333A",
              "tdnet/333A/20250613_333A_Ｐ－テクノスマイル_自己株式取得_自己株式の取得枠設定に関するお知らせ_140120250612588791.pdf",
              "自己株式の取得枠設定に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="画像PDF、テキスト抽出不可"),
    TrainCase("H", "6758",
              "tdnet/6758/20210428_6758_ソニーグループ_自己株式取得_自己株式の取得枠設定に関するお知らせ（会社法第459条第１項の規定による定款の定_140120210428402179.pdf",
              "自己株式の取得枠設定に関するお知らせ（会社法第459条第１項の規定による定款の定",
              expect_pct=True, expect_tn3=False, note="ソニー大型"),
    TrainCase("H", "6645",
              "tdnet/6645/20211028_6645_オムロン_自己株式取得_自己株式取得枠の設定に関するお知らせ_140120211027417661.pdf",
              "自己株式取得枠の設定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("H", "7309",
              "tdnet/7309/20250212_7309_シマノ_自己株式取得_株主還元方針、自己株式の取得中止並びに新規の自己株式取得枠の設定に関するお知らせ_140120250207567364.pdf",
              "株主還元方針、自己株式の取得中止並びに新規の自己株式取得枠の設定に関するお知らせ",
              expect_pct=True, expect_tn3=True, note="取得方法にTN3記載あり"),

    # Pattern I: 古い開示（2016-2017年）- フォーマット差異確認
    TrainCase("I", "8186",
              "tdnet/8186/20160212_8186_Ｊ－大塚家具_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160212413411.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016 大塚家具"),
    TrainCase("I", "9072",
              "tdnet/9072/20160805_9072_ニッコンＨＤ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160805468809.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016"),
    TrainCase("I", "5471",
              "tdnet/5471/20160531_5471_大同特鋼_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160531406733.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016"),
    TrainCase("I", "6419",
              "tdnet/6419/20160215_6419_マースエンジ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160215414933.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016"),
    TrainCase("I", "8840",
              "tdnet/8840/20171026_8840_大京_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120171026499652.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2017"),
    TrainCase("I", "9075",
              "tdnet/9075/20170206_9075_福山運_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120170206491455.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2017"),
]


def run_training(verbose: bool = True) -> dict:
    """訓練セットを実行し結果を返す."""
    client = _gcs_client()
    results = {
        "total": 0,
        "pct_ok": 0,
        "pct_fail": 0,
        "tn3_ok": 0,
        "tn3_fail": 0,
        "parse_fail": 0,
        "failures": [],
    }

    for case in TRAIN_CASES:
        results["total"] += 1
        label = f"[{case.pattern}] {case.ticker}"
        try:
            pdf_bytes = download_pdf(client, case.blob_name)
        except Exception as e:
            print(f"  {label}: GCS download FAIL - {e}")
            results["parse_fail"] += 1
            results["failures"].append((case, "GCS_DOWNLOAD_FAIL", str(e)))
            continue

        info = parse_buyback_pdf(pdf_bytes)
        if info is None:
            print(f"  {label}: PARSE FAIL (None returned)")
            results["parse_fail"] += 1
            results["failures"].append((case, "PARSE_NONE", ""))
            continue

        # pct check
        pct_ok = True
        if case.expect_pct:
            if info.pct_of_outstanding is None:
                pct_ok = False
                results["pct_fail"] += 1
                results["failures"].append((case, "PCT_MISS", f"got None"))
            else:
                results["pct_ok"] += 1
        else:
            results["pct_ok"] += 1

        # tn3 check
        tn3_ok = True
        if info.is_tostnet3 != case.expect_tn3:
            tn3_ok = False
            results["tn3_fail"] += 1
            results["failures"].append((case, "TN3_MISMATCH",
                                        f"expected={case.expect_tn3}, got={info.is_tostnet3}"))
        else:
            results["tn3_ok"] += 1

        status = "OK" if (pct_ok and tn3_ok) else "FAIL"
        if verbose:
            print(f"  {label}: {status}  pct={info.pct_of_outstanding}  tn3={info.is_tostnet3}"
                  f"  shares={info.total_shares}  amt={info.total_amount_oku}"
                  f"  {'[' + case.note + ']' if case.note else ''}")

    return results


def print_summary(results: dict) -> None:
    """結果サマリを出力."""
    total = results["total"]
    print(f"\n{'='*60}")
    print(f"Total: {total}")
    print(f"PCT extraction: {results['pct_ok']}/{total} OK, {results['pct_fail']} FAIL")
    print(f"TN3 detection:  {results['tn3_ok']}/{total} OK, {results['tn3_fail']} FAIL")
    print(f"Parse failures: {results['parse_fail']}")

    if results["failures"]:
        print(f"\n--- Failures ---")
        for case, fail_type, detail in results["failures"]:
            print(f"  [{case.pattern}] {case.ticker}: {fail_type} - {detail}")
            print(f"    blob: {case.blob_name}")

    pct_rate = results['pct_ok'] / total * 100 if total else 0
    tn3_rate = results['tn3_ok'] / total * 100 if total else 0
    print(f"\nPCT rate: {pct_rate:.1f}%  TN3 rate: {tn3_rate:.1f}%")

    passed = (results['pct_fail'] == 0 and results['tn3_fail'] == 0 and results['parse_fail'] == 0)
    print(f"Overall: {'PASS' if passed else 'FAIL'}")


def debug_pdf(blob_name: str) -> None:
    """単一 PDF のテキストと正規表現マッチを詳細表示."""
    import re
    from zaraba_tdnet_poller import _RE_PCT, _RE_TN3, _RE_SHARES, _RE_AMOUNT

    client = _gcs_client()
    pdf_bytes = download_pdf(client, blob_name)
    print(f"PDF size: {len(pdf_bytes)} bytes")

    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    print(f"Text length: {len(text)} chars")
    print(f"\n--- Text (first 2000 chars) ---\n{text[:2000]}")
    print(f"\n--- Regex matches ---")
    m = _RE_PCT.search(text)
    print(f"PCT: {m.group(0) if m else 'NO MATCH'}")
    m = _RE_TN3.search(text)
    print(f"TN3: {m.group(0) if m else 'NO MATCH'}")
    m = _RE_SHARES.search(text)
    print(f"SHARES: {m.group(0) if m else 'NO MATCH'}")
    m = _RE_AMOUNT.search(text)
    print(f"AMOUNT: {m.group(0) if m else 'NO MATCH'}")

    info = parse_buyback_pdf(pdf_bytes)
    print(f"\n--- Parsed ---\n{info}")


# === テストセット (Step 5) ===
TEST_CASES: list[TrainCase] = [
    # Pattern A: 市場買付決定 (pct=True, tn3=False)
    TrainCase("A", "3963",
              "tdnet/3963/20250214_3963_シンクロ・フード_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120250213573691.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "4613",
              "tdnet/4613/20240530_4613_関ペイント_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120240527507840.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=True, note="PDF内にTN3記載あり"),
    TrainCase("A", "2802",
              "tdnet/2802/20231113_2802_味の素_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120231110585007.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="味の素 大型"),
    TrainCase("A", "4063",
              "tdnet/4063/20210817_4063_信越化_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120210816486556.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="信越化学 大型"),
    TrainCase("A", "8704",
              "tdnet/8704/20250204_8704_トレイダーズＨＤ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ （会社法第165条第２項の規定によ_140120250204562325.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "3371",
              "tdnet/3371/20220107_3371_ソフトクリエＨＤ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120220107564988.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "8604",
              "tdnet/8604/20240131_8604_野村_自己株式取得_自己株式の取得に係る事項の決定に関するお知らせ_140120240131523052.pdf",
              "自己株式の取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="野村 大型"),
    TrainCase("A", "4426",
              "tdnet/4426/20240515_4426_Ｐ－パスロジ_自己株式取得_自己株式の取得に係る事項の決定に関するお知らせ_140120240515597871.pdf",
              "自己株式の取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("A", "1801",
              "tdnet/1801/20240426_1801_大成建_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120240415570931.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="大成建設 大型"),

    # Pattern B: ToSTNeT-3 のみ (pct=True, tn3=True)
    TrainCase("B", "1982",
              "tdnet/1982/20240808_1982_日比谷設_自己株式取得_自己株式立会外買付取引(ToSTNeT-3)による自己株式の買付けに関するお知ら_140120240808567846.pdf",
              "自己株式立会外買付取引(ToSTNeT-3)による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "2796",
              "tdnet/2796/20240115_2796_ファーマライズＨＤ_自己株式取得_自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら_140120240115515392.pdf",
              "自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True, note="全角TN3"),
    TrainCase("B", "1980",
              "tdnet/1980/20200225_1980_ダイダン_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120200221468935.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "4204",
              "tdnet/4204/20201111_4204_積水化_自己株式取得_自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら_140120201111421142.pdf",
              "自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True, note="全角TN3"),
    TrainCase("B", "4662",
              "tdnet/4662/20251215_4662_フォーカスシステムズ_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120251215520197.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),
    TrainCase("B", "8242",
              "tdnet/8242/20250205_8242_Ｈ２Ｏリテイル_自己株式取得_自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら_140120250130558824.pdf",
              "自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知ら",
              expect_pct=True, expect_tn3=True),

    # Pattern C: N-NET3 (tn3=True)
    TrainCase("C", "6111",
              "tdnet/6111/20180510_6111_旭精機_自己株式取得_自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付けに関するお知らせ_140120180510432058.pdf",
              "自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付けに関するお知らせ",
              expect_pct=True, expect_tn3=True, note="半角カッコ+全角N-NET3"),
    TrainCase("C", "7648",
              "tdnet/7648/20161111_7648_トーカン_自己株式取得_自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに関するお知らせ_140120161102429758.pdf",
              "自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに関するお知らせ",
              expect_pct=False, expect_tn3=True, note="PDFにpct記載なし"),
    TrainCase("C", "9310a",
              "tdnet/9310/20160830_9310_トランスシティ_自己株式取得_自己株式の取得および自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付け_140120160830485108.pdf",
              "自己株式の取得および自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付け",
              expect_pct=False, expect_tn3=True, note="2016 pctなし"),
    TrainCase("C", "9310b",
              "tdnet/9310/20190827_9310_トランスシティ_自己株式取得_自己株式の取得および自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付け_140120190827491989.pdf",
              "自己株式の取得および自己株式立会外買付取引(Ｎ－ＮＥＴ３)による自己株式の買付け",
              expect_pct=False, expect_tn3=True, note="2019 pctなし"),
    TrainCase("C", "9471",
              "tdnet/9471/20160818_9471_文渓堂_自己株式取得_自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに_140120160818478008.pdf",
              "自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）による自己株式の買付けに",
              expect_pct=False, expect_tn3=True, note="2016 pctなし"),

    # Pattern F: 複合 (決定+TN3) (pct=True, tn3=True)
    TrainCase("F", "6265",
              "tdnet/6265/20250515_6265_コンバム_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による 自己株式の_140120250515555399.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による 自己株式の",
              expect_pct=True, expect_tn3=True, note="全角TN3"),
    TrainCase("F", "1945",
              "tdnet/1945/20250214_1945_東京エネシス_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120250214575714.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "6626",
              "tdnet/6626/20231114_6626_ＳＥＭＩＴＥＣ_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120231114589532.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "7570",
              "tdnet/7570/20250130_7570_橋本総業ＨＤ_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120250130557977.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "7609",
              "tdnet/7609/20250206_7609_ダイトロン_自己株式取得_自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買_140120250206565206.pdf",
              "自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買",
              expect_pct=True, expect_tn3=True),
    TrainCase("F", "3075",
              "tdnet/3075/20250127_3075_銚子丸_自己株式取得_自己株式の取得及び自己株式立会外買い付け取引（ToSTNeT-3）による自己株式_140120250103546170.pdf",
              "自己株式の取得及び自己株式立会外買い付け取引（ToSTNeT-3）による自己株式",
              expect_pct=True, expect_tn3=True, note="買い付け(送り仮名あり)"),
    TrainCase("F", "9740",
              "tdnet/9740/20251125_9740_ＣＳＰ_自己株式取得_自己株式の取得および自己株式立会外買付取引(ToSTNeT-3)による自己株式の_140120251125508692.pdf",
              "自己株式の取得および自己株式立会外買付取引(ToSTNeT-3)による自己株式の",
              expect_pct=True, expect_tn3=True),

    # Pattern G: 市場買付 取得終了 (pct不確定, tn3=False)
    TrainCase("G", "6804",
              "tdnet/6804/20210118_6804_ホシデン_自己株式取得_自己株式の市場買付および取得終了に関するお知らせ_140120210118445743.pdf",
              "自己株式の市場買付および取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了"),
    TrainCase("G", "6923",
              "tdnet/6923/20211116_6923_スタンレー電_自己株式取得_自己株式の市場買付及び取得終了に関するお知らせ_140120211115436280.pdf",
              "自己株式の市場買付及び取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="取得終了"),
    TrainCase("G", "7751",
              "tdnet/7751/20230810_7751_キヤノン_自己株式取得_自己株式の市場買付けおよび取得終了に関するお知らせ_140120230810539748.pdf",
              "自己株式の市場買付けおよび取得終了に関するお知らせ",
              expect_pct=False, expect_tn3=False, note="キヤノン大型 取得終了"),

    # Pattern H: 取得枠 (pct=True, tn3=False)
    TrainCase("H", "2282",
              "tdnet/2282/20250509_2282_日ハム_自己株式取得_自己株式の取得枠設定に関するお知らせ_140120250508535580.pdf",
              "自己株式の取得枠設定に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("H", "4334",
              "tdnet/4334/20220921_4334_ユークス_自己株式取得_自己株式の取得枠拡大に関するお知らせ_140120220921534664.pdf",
              "自己株式の取得枠拡大に関するお知らせ",
              expect_pct=True, expect_tn3=False),
    TrainCase("H", "6332",
              "tdnet/6332/20250509_6332_月島ＨＤ_自己株式取得_自己株式の取得枠設定に関するお知らせ_140120250508534708.pdf",
              "自己株式の取得枠設定に関するお知らせ",
              expect_pct=True, expect_tn3=False),

    # Pattern I: 古い開示 2016-2019 (pct=True, tn3=False)
    TrainCase("I", "2181",
              "tdnet/2181/20170217_2181_テンプＨＤ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120170217401843.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=True, note="2017 PDF内TN3記載"),
    TrainCase("I", "2335",
              "tdnet/2335/20160531_2335_キューブシステム_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160531407527.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016"),
    TrainCase("I", "3105",
              "tdnet/3105/20190214_3105_日清紡ＨＤ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120190212474559.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2019"),
    TrainCase("I", "4979",
              "tdnet/4979/20160610_4979_ＯＡＴアグリオ_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120160610420336.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=True, note="2016 PDF内TN3記載"),
    TrainCase("I", "8398",
              "tdnet/8398/20161110_8398_筑邦銀_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120161109435040.pdf",
              "自己株式取得に係る事項の決定に関するお知らせ",
              expect_pct=True, expect_tn3=False, note="2016 地銀"),
]


def run_test_set(verbose: bool = True) -> dict:
    """テストセットを実行（正規表現は凍結状態で検証）."""
    client = _gcs_client()
    results = {
        "total": 0, "pct_ok": 0, "pct_fail": 0,
        "tn3_ok": 0, "tn3_fail": 0, "parse_fail": 0, "failures": [],
    }
    for case in TEST_CASES:
        results["total"] += 1
        label = f"[{case.pattern}] {case.ticker}"
        try:
            pdf_bytes = download_pdf(client, case.blob_name)
        except Exception as e:
            print(f"  {label}: GCS download FAIL - {e}")
            results["parse_fail"] += 1
            results["failures"].append((case, "GCS_DOWNLOAD_FAIL", str(e)))
            continue
        info = parse_buyback_pdf(pdf_bytes)
        if info is None:
            print(f"  {label}: PARSE FAIL (None returned)")
            results["parse_fail"] += 1
            results["failures"].append((case, "PARSE_NONE", ""))
            continue
        pct_ok = True
        if case.expect_pct:
            if info.pct_of_outstanding is None:
                pct_ok = False
                results["pct_fail"] += 1
                results["failures"].append((case, "PCT_MISS", "got None"))
            else:
                results["pct_ok"] += 1
        else:
            results["pct_ok"] += 1
        tn3_ok = True
        if info.is_tostnet3 != case.expect_tn3:
            tn3_ok = False
            results["tn3_fail"] += 1
            results["failures"].append((case, "TN3_MISMATCH",
                                        f"expected={case.expect_tn3}, got={info.is_tostnet3}"))
        else:
            results["tn3_ok"] += 1
        status = "OK" if (pct_ok and tn3_ok) else "FAIL"
        if verbose:
            print(f"  {label}: {status}  pct={info.pct_of_outstanding}  tn3={info.is_tostnet3}"
                  f"  shares={info.total_shares}  amt={info.total_amount_oku}"
                  f"  {'[' + case.note + ']' if case.note else ''}")
    return results


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        blob = sys.argv[2] if len(sys.argv) > 2 else TRAIN_CASES[0].blob_name
        debug_pdf(blob)
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        print("=== Test Set (Step 5) ===")
        results = run_test_set()
        print_summary(results)
    elif len(sys.argv) > 1 and sys.argv[1] == "all":
        print("=== Training Set (Step 4) ===")
        train_results = run_training()
        print_summary(train_results)
        print("\n\n=== Test Set (Step 5) ===")
        test_results = run_test_set()
        print_summary(test_results)
    else:
        print("=== Training Set (Step 4) ===")
        results = run_training()
        print_summary(results)
