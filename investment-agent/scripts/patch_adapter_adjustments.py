"""
NG突合CSVからunit_scale / yoy_offsetを自動検出してアダプターに書き込む。

- diff > 1000 かつ比率が標準スケール（1000 or 1000000 or 0.001 or 0.000001）に近い場合
  → unit_scale を設定
- bc ≈ our + 100 の場合 → yoy_offset = 100 を設定

Usage:
    PYTHONUTF8=1 python scripts/patch_adapter_adjustments.py \
        --csv C:/tmp/buffett_compare_20260330_013221.csv
    PYTHONUTF8=1 python scripts/patch_adapter_adjustments.py \
        --csv C:/tmp/buffett_compare_20260330_013221.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from typing import Any

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

KEY_FILE = "keys/gcp-service-account.json"
BUCKET = "stock_data_1930932"
PREFIX = "monthlydata"
PROJECT = "gmailpj-357912"

# 標準スケール候補（our * scale ≈ bc）
STANDARD_SCALES = [1_000_000, 1_000, 0.001, 0.000_001]
SCALE_TOLERANCE = 0.25  # ±25% の誤差を許容


def detect_scale(our: float, bc: float) -> float | None:
    """our * scale ≈ bc となる標準スケールを返す。見つからなければ None。"""
    if bc == 0:
        return None
    ratio = bc / our
    for s in STANDARD_SCALES:
        if abs(ratio / s - 1) <= SCALE_TOLERANCE:
            return s
    return None


def detect_yoy_offset(our: float, bc: float) -> float | None:
    """bc ≈ our + offset となるオフセットを返す（100 のみ対応）。"""
    if abs(bc - (our + 100)) <= 5:
        return 100.0
    return None


def load_ng_rows(csv_path: str) -> list[dict]:
    with open(csv_path, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r["match"] == "NG"]


def build_patch_plan(ng_rows: list[dict]) -> dict[str, dict[str, dict]]:
    """
    {ticker: {field_key: {"unit_scale": x} or {"yoy_offset": x}}} を返す。
    複数月の中央値で判定してブレを吸収する。
    """
    # (ticker, field) → [(our, bc), ...]
    samples: dict[tuple, list] = defaultdict(list)
    for r in ng_rows:
        try:
            our = float(r["our_value"])
            bc = float(r["bc_value"])
            if our == 0 or bc == 0:
                continue
            samples[(r["ticker"], r["our_field"])].append((our, bc))
        except (ValueError, KeyError):
            continue

    plan: dict[str, dict[str, dict]] = defaultdict(dict)

    for (ticker, field), pairs in samples.items():
        # scale 検出: 全ペアで一致するスケールが過半数なら採用
        scales = [detect_scale(o, b) for o, b in pairs]
        scales = [s for s in scales if s is not None]
        if scales and len(scales) >= len(pairs) * 0.6:
            # 最頻値を採用
            from collections import Counter
            scale = Counter(scales).most_common(1)[0][0]
            plan[ticker][field] = {"unit_scale": scale}
            continue

        # yoy_offset 検出
        offsets = [detect_yoy_offset(o, b) for o, b in pairs]
        offsets = [x for x in offsets if x is not None]
        if offsets and len(offsets) >= len(pairs) * 0.6:
            plan[ticker][field] = {"yoy_offset": 100.0}

    return plan


def apply_patch(
    gcs: storage.Client,
    plan: dict[str, dict[str, dict]],
    dry_run: bool,
) -> None:
    bucket = gcs.bucket(BUCKET)
    total_fields = sum(len(v) for v in plan.values())
    print(f"\n適用対象: {len(plan)}社 / {total_fields}フィールド")

    patched_tickers = 0
    patched_fields = 0

    for ticker, field_patches in sorted(plan.items()):
        blob_path = f"{PREFIX}/{ticker}/extract_adapter.json"
        blob = bucket.blob(blob_path)
        try:
            adapter = json.loads(blob.download_as_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [{ticker}] アダプター読み込みエラー: {e}")
            continue

        changed = False
        for field in adapter.get("fields", []):
            key = field.get("key", "")
            if key not in field_patches:
                continue
            patch = field_patches[key]
            for attr, val in patch.items():
                old = field.get(attr)
                if old != val:
                    field[attr] = val
                    changed = True
                    patched_fields += 1
                    print(f"  [{ticker}] {key[:35]:<35} {attr}={val}"
                          + (f" (was {old})" if old is not None else ""))

        if changed:
            patched_tickers += 1
            if not dry_run:
                new_content = json.dumps(adapter, ensure_ascii=False, indent=2)
                blob.upload_from_string(new_content, content_type="application/json")

    status = "[DRY RUN]" if dry_run else "完了"
    print(f"\n{status}: {patched_tickers}社 / {patched_fields}フィールド更新")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="NG突合CSVパス")
    parser.add_argument("--dry-run", action="store_true", help="書き込みを行わない")
    args = parser.parse_args()

    print(f"=== アダプター調整パッチ ({'DRY RUN' if args.dry_run else '書き込みあり'}) ===")

    ng_rows = load_ng_rows(args.csv)
    print(f"NGレコード: {len(ng_rows)}件")

    plan = build_patch_plan(ng_rows)
    print(f"\n検出結果:")
    scale_count = sum(1 for d in plan.values() for v in d.values() if "unit_scale" in v)
    yoy_count = sum(1 for d in plan.values() for v in d.values() if "yoy_offset" in v)
    print(f"  unit_scale: {scale_count}フィールド")
    print(f"  yoy_offset: {yoy_count}フィールド")

    creds = service_account.Credentials.from_service_account_file(KEY_FILE)
    gcs = storage.Client(project=PROJECT, credentials=creds)

    apply_patch(gcs, plan, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
