"""playwright調査結果に基づき、eIR企業の adapter.json を一括更新する。

46社を type=eir_api / status=active に更新し GCS にアップロードする。
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# GCS SSL 回避（Windows ローカル環境用）
import urllib3
import requests as _req
from requests.adapters import HTTPAdapter as _HA
urllib3.disable_warnings()

class _NoVerify(_HA):
    def send(self, req, **kw):
        kw["verify"] = False
        return super().send(req, **kw)

_orig = _req.Session.__init__
def _p(self, *a, **kw):
    _orig(self, *a, **kw)
    self.mount("https://", _NoVerify())
    self.verify = False
_req.Session.__init__ = _p

from google.cloud import storage
from google.oauth2 import service_account

GCS_BUCKET = "stock_data_1930932"
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "keys", "gcp-service-account.json")
INVESTIGATION_JSON = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "playwright_investigation.json"
JST = timezone(timedelta(hours=9))


def _get_gcs_client():
    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    return storage.Client(project="gmailpj-357912", credentials=creds)


def _load_or_create_adapter(bucket, ticker, investigation_result):
    """GCS から adapter.json を読み込むか新規作成する。"""
    gcs_key = f"monthlydata/{ticker}/adapter.json"
    blob = bucket.blob(gcs_key)

    adapter = None
    if blob.exists():
        try:
            adapter = json.loads(blob.download_as_text())
        except Exception:
            pass

    if adapter is None:
        # ir_url.json から移行
        ir_key = f"monthlydata/{ticker}/ir_url.json"
        ir_blob = bucket.blob(ir_key)
        ir_data = {}
        if ir_blob.exists():
            try:
                ir_data = json.loads(ir_blob.download_as_text())
            except Exception:
                pass
        adapter = {
            "ticker": ticker,
            "company_name": investigation_result.get("company_name", ""),
            "ir_page_url": ir_data.get("monthly_page_url", investigation_result.get("url", "")),
            "company_hp_url": ir_data.get("company_hp_url", ""),
            "status": "active",
            "type": "eir_api",
            "eir_code": "",
            "note": "",
            "url_source": "investigation",
            "updated_at": "",
            "last_checked": "",
            "last_success": "",
        }

    return adapter, gcs_key


def main():
    with open(INVESTIGATION_JSON, encoding="utf-8") as f:
        results = json.load(f)

    eir_results = [r for r in results if r["verdict"] == "eir_api"]
    print(f"eIR API 対応企業: {len(eir_results)}社")

    gcs = _get_gcs_client()
    bucket = gcs.bucket(GCS_BUCKET)

    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    updated = 0

    for r in eir_results:
        ticker = r["ticker"]
        company = r["company_name"]
        eir_codes = r["eir_codes"]

        if not eir_codes:
            print(f"  {ticker} {company} → eir_code なし、スキップ")
            continue

        eir_code = eir_codes[0]
        adapter, gcs_key = _load_or_create_adapter(bucket, ticker, r)

        # 更新
        adapter["type"] = "eir_api"
        adapter["status"] = "active"
        adapter["eir_code"] = eir_code
        adapter["updated_at"] = now
        adapter["last_checked"] = now

        # company_name が空なら補完
        if not adapter.get("company_name"):
            adapter["company_name"] = company

        # ir_page_url が空なら補完
        if not adapter.get("ir_page_url"):
            adapter["ir_page_url"] = r.get("url", "")

        blob = bucket.blob(gcs_key)
        blob.upload_from_string(
            json.dumps(adapter, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        print(f"  ✓ {ticker} {company} → eir_code={eir_code}")
        updated += 1

    print(f"\n更新完了: {updated}社")

    # 非eIR企業の確認
    non_eir = [r for r in results if r["verdict"] != "eir_api"]
    if non_eir:
        print(f"\n=== 非eIR企業 ({len(non_eir)}社) ===")
        for r in non_eir:
            print(f"  {r['ticker']} {r['company_name']} → {r['verdict']}")
            print(f"    URL: {r.get('url', '')}")
            if r.get("error"):
                print(f"    ERR: {r['error']}")


if __name__ == "__main__":
    main()
