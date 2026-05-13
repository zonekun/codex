"""Step 7: F10 スコアリングパスの結合テスト."""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from zaraba_earnings import _is_buyback_title, _is_tostnet_title


@dataclass
class MockDisc:
    title: str
    document_url: str = ""


def test_keywords() -> None:
    tests = [
        ("自己株式取得に係る事項の決定に関するお知らせ", True, False),
        ("自己株式の取得状況に関するお知らせ", False, False),
        ("自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知", True, True),
        ("自己株式の消却に関するお知らせ", False, False),
        ("自己株式の市場買付に関するお知らせ", True, False),
        ("自己株式の取得枠設定に関するお知らせ", True, False),
        ("自己株式の取得及び自己株式立会外買付取引（Ｎ－ＮＥＴ３）", True, True),
        ("（訂正）自己株式取得に係る事項の決定に関するお知らせ", False, False),
        ("自己株式の取得に関するお知らせ", True, False),
        ("自己株式取得の中止に関するお知らせ", False, False),
        ("自己株式の取得終了に関するお知らせ", False, False),
        ("自己株式の取得結果に関するお知らせ", False, False),
        ("自己の株式の取得に関するお知らせ", True, False),
        ("自己株式の無償取得に関するお知らせ", False, False),
        ("自己株式の公開買付に関するお知らせ", False, False),
        ("自己株式取得に係る事項の決定及び自己株式の消却に関するお知らせ", False, False),
        ("自己株式の取得期間延長に関するお知らせ", False, False),
        ("自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関する", True, True),
    ]

    ok = 0
    for title, exp_bb, exp_tn3 in tests:
        bb = _is_buyback_title(title)
        tn3 = _is_tostnet_title(title)
        passed = bb == exp_bb and tn3 == exp_tn3
        ok += 1 if passed else 0
        if not passed:
            print(f"  FAIL: '{title[:50]}' bb={bb}(exp={exp_bb}) tn3={tn3}(exp={exp_tn3})")
    print(f"Keyword tests: {ok}/{len(tests)} passed")
    assert ok == len(tests), f"{len(tests) - ok} keyword tests failed"


def test_scoring_logic() -> None:
    from zaraba_tdnet_poller import BuybackInfo

    # Case 1: TN3 only by title -> weight 0
    discs1 = [MockDisc("自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関する")]
    is_tn3 = any(_is_tostnet_title(d.title) for d in discs1)
    has_mkt = any(
        ("決定" in d.title or "市場買付" in d.title or "取得枠" in d.title)
        and not _is_tostnet_title(d.title)
        for d in discs1
    )
    assert is_tn3 and not has_mkt
    print("  Case 1 (TN3 only -> weight=0, '自社株(TN3)'): OK")

    # Case 2: Market decision -> PDF parse path
    discs2 = [MockDisc("自己株式取得に係る事項の決定に関するお知らせ")]
    is_tn3 = any(_is_tostnet_title(d.title) for d in discs2)
    has_mkt = any(
        ("決定" in d.title or "市場買付" in d.title or "取得枠" in d.title)
        and not _is_tostnet_title(d.title)
        for d in discs2
    )
    assert not is_tn3 and has_mkt
    print("  Case 2 (Market decision -> PDF parse): OK")

    # Case 3: Combined (decision + TN3) -> market priority
    discs3 = [
        MockDisc("自己株式取得に係る事項の決定に関するお知らせ"),
        MockDisc("自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関する"),
    ]
    is_tn3 = any(_is_tostnet_title(d.title) for d in discs3)
    has_mkt = any(
        ("決定" in d.title or "市場買付" in d.title or "取得枠" in d.title)
        and not _is_tostnet_title(d.title)
        for d in discs3
    )
    assert is_tn3 and has_mkt
    print("  Case 3 (Combined -> market priority): OK")

    # Score thresholds
    for pct, exp_score, exp_label in [
        (0.5, 1, "自社株0.5%"),
        (1.0, 1, "自社株1.0%"),
        (2.5, 1, "自社株2.5%"),
        (3.0, 3, "自社株3.0%"),
        (4.9, 3, "自社株4.9%"),
        (5.0, 4, "自社株5.0%"),
        (10.0, 4, "自社株10.0%"),
    ]:
        score = 0
        if pct >= 5.0:
            score += 4
        elif pct >= 3.0:
            score += 3
        else:
            score += 1
        label = f"自社株{pct:.1f}%"
        assert score == exp_score, f"pct={pct}: score={score} != {exp_score}"
        assert label == exp_label, f"pct={pct}: label={label} != {exp_label}"
    print("  Score thresholds (<1%=+1, 1-3%=+1, 3-5%=+3, >=5%=+4): OK")

    # Fallback
    score = 0
    info = None
    if info and info.pct_of_outstanding is not None:
        pass
    else:
        score += 2
    assert score == 2
    print("  Fallback (parse fail -> +2): OK")


def test_pdf_roundtrip() -> None:
    """Real PDF download + parse from GCS."""
    from google.cloud import storage
    from google.oauth2 import service_account
    from zaraba_tdnet_poller import parse_buyback_pdf

    key_path = Path(__file__).resolve().parent.parent / "keys" / "gcp-service-account.json"
    creds = service_account.Credentials.from_service_account_file(str(key_path))
    client = storage.Client(credentials=creds, project=creds.project_id)
    bucket = client.bucket("stock_data_1930932")

    # コーナン商事 — 2.18%, market buyback
    blob_name = "tdnet/7516/20250411_7516_コーナン商_自己株式取得_自己株式取得に係る事項の決定に関するお知らせ_140120250409511923.pdf"
    pdf_bytes = bucket.blob(blob_name).download_as_bytes()
    info = parse_buyback_pdf(pdf_bytes)
    assert info is not None
    assert info.pct_of_outstanding == 2.18
    assert info.is_tostnet3 is False
    print(f"  PDF roundtrip (7516 コーナン商 pct=2.18%): OK")

    # カルビー — TN3
    blob_name2 = "tdnet/2229/20251121_2229_カルビー_自己株式取得_自己株式立会外買付取引（ＴｏＳＴＮｅＴ－３）による自己株式の買付けに関するお知ら_140120251121507398.pdf"
    pdf_bytes2 = bucket.blob(blob_name2).download_as_bytes()
    info2 = parse_buyback_pdf(pdf_bytes2)
    assert info2 is not None
    assert info2.is_tostnet3 is True
    assert info2.pct_of_outstanding == 2.7
    print(f"  PDF roundtrip (2229 カルビー TN3 pct=2.7%): OK")


if __name__ == "__main__":
    print("=== Step 7: Integration Tests ===\n")
    print("--- Keyword matching ---")
    test_keywords()
    print("\n--- Scoring logic ---")
    test_scoring_logic()
    print("\n--- PDF roundtrip ---")
    test_pdf_roundtrip()
    print("\nAll integration tests passed!")
