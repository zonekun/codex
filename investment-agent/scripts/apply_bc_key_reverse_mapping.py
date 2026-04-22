#!/usr/bin/env python3
"""bc_key 逆引きマッピング CSV を adapter.json に反映するスクリプト.

reconcile_bc_key_from_compare.py の出力 CSV を（人間がレビューして承認した後）
読み込み、各 adapter.fields[*] に bc_key / yoy_offset / unit_scale を書き込む。
ローカル `data/monthly_adapters/<ticker>.json` と GCS `monthly/meta/<ticker>/extract_adapter.json`
の両方に反映する。

【使い方】
  # dry-run（適用前確認）
  PYTHONUTF8=1 python scripts/apply_bc_key_reverse_mapping.py \
    --input data/logs/bc_key_reverse_mapping_20260418_220000.csv \
    --dry-run

  # 実適用（GCS 同期含む）
  PYTHONUTF8=1 python scripts/apply_bc_key_reverse_mapping.py \
    --input data/logs/bc_key_reverse_mapping_20260418_220000.csv \
    --min-ratio 0.9

  # 実適用 + GCS スキップ（ローカルのみ更新）
  PYTHONUTF8=1 python scripts/apply_bc_key_reverse_mapping.py \
    --input ... --no-gcs

  # 特定 ticker のみ
  PYTHONUTF8=1 python scripts/apply_bc_key_reverse_mapping.py \
    --input ... --tickers 7918 3175

【入力 CSV の加工（承認フロー想定）】
  出力 CSV をコピーして「承認列」を追加する、または直接フィルタした CSV を渡す。
  min-ratio で自動承認ラインを設定可能。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
GCS_BUCKET = "stock_data_1930932"
GCS_META = "monthly/meta"
ADAPTER_DIR = Path("data/monthly_adapters")


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    """SJIS または UTF-8 の CSV を読み込む（どちらも試す）."""
    for enc in ("cp932", "utf-8-sig"):
        try:
            with open(path, encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"CSV の encoding 不明: {path}")


def apply_to_adapter(adapter: dict, our_key: str, suggested_bc_key: str,
                     yoy_offset: str, unit_scale: str) -> tuple[bool, list[str]]:
    """
    adapter.fields[*] の our_key に一致する field を探して bc_key/yoy_offset/unit_scale を設定。
    Returns: (変更あり, 変更ログリスト)
    """
    changes: list[str] = []
    matched = False
    for f in adapter.get("fields", []):
        if f.get("key") != our_key:
            continue
        matched = True
        # bc_key
        if suggested_bc_key and f.get("bc_key") != suggested_bc_key:
            old = f.get("bc_key")
            f["bc_key"] = suggested_bc_key
            changes.append(f"    bc_key: {old!r} → {suggested_bc_key!r}")
        # yoy_offset
        if yoy_offset:
            try:
                yo = float(yoy_offset)
                if f.get("yoy_offset") != yo:
                    old = f.get("yoy_offset")
                    f["yoy_offset"] = yo
                    changes.append(f"    yoy_offset: {old!r} → {yo}")
            except (ValueError, TypeError):
                pass
        # unit_scale
        if unit_scale:
            try:
                us = float(unit_scale)
                if f.get("unit_scale") != us:
                    old = f.get("unit_scale")
                    f["unit_scale"] = us
                    changes.append(f"    unit_scale: {old!r} → {us}")
            except (ValueError, TypeError):
                pass
    if not matched:
        changes.append(f"    ⚠️ field not found: key={our_key!r}")
    return (matched and bool(changes), changes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="逆引きマッピング CSV を adapter.json に反映",
    )
    parser.add_argument("--input", required=True, help="承認済み CSV パス")
    parser.add_argument("--min-ratio", type=float, default=0.9,
                        help="自動承認する最低一致率（デフォルト 0.9）")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="対象銘柄を限定（省略時は CSV 全銘柄）")
    parser.add_argument("--dry-run", action="store_true",
                        help="ファイル書き込み・GCS 同期を行わず、変更予定のみ表示")
    parser.add_argument("--no-gcs", action="store_true",
                        help="GCS 同期をスキップ（ローカルのみ更新）")
    args = parser.parse_args()

    csv_path = Path(args.input)
    rows = load_csv_rows(csv_path)
    log(f"CSV 読み込み: {len(rows)} 行 ({csv_path})")

    # フィルタ: ticker + min-ratio
    if args.tickers:
        tickers_set = set(args.tickers)
        rows = [r for r in rows if r.get("ticker", "") in tickers_set]
    rows = [r for r in rows if float(r.get("match_ratio", "0") or "0") >= args.min_ratio]
    log(f"  → 条件絞込後: {len(rows)} 行 (min-ratio={args.min_ratio})")

    # ticker ごとにグループ化
    by_ticker: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    log(f"  対象銘柄: {len(by_ticker)}")

    # GCS クライアント（必要時のみ）
    gcs_client = None
    if not args.no_gcs and not args.dry_run:
        try:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
            from google.cloud import storage
            gcs_client = storage.Client(project="stock-data-1930932")
        except Exception as e:
            log(f"  ⚠️ GCS クライアント初期化失敗: {e} → ローカルのみ更新に降格")
            gcs_client = None

    # 統計
    total_rows = 0
    total_changed = 0
    total_fields_modified = 0
    failed_tickers: list[str] = []

    for ticker, t_rows in sorted(by_ticker.items()):
        adapter_path = ADAPTER_DIR / f"{ticker}.json"
        if not adapter_path.exists():
            log(f"[{ticker}] adapter.json なし → スキップ")
            failed_tickers.append(ticker)
            continue

        try:
            with open(adapter_path, encoding="utf-8") as f:
                adapter = json.load(f)
        except Exception as e:
            log(f"[{ticker}] adapter.json 読み込み失敗: {e} → スキップ")
            failed_tickers.append(ticker)
            continue

        changed_in_ticker = False
        for r in t_rows:
            total_rows += 1
            our_key = r.get("our_key", "")
            bc_key = r.get("suggested_bc_key", "").strip()
            yoy = r.get("suggested_yoy_offset", "").strip()
            us = r.get("suggested_unit_scale", "").strip()

            if not bc_key:
                log(f"[{ticker}] {our_key}: suggested_bc_key 空 → スキップ")
                continue

            ok, changes = apply_to_adapter(adapter, our_key, bc_key, yoy, us)
            if changes:
                log(f"[{ticker}] {our_key}")
                for c in changes:
                    log(c)
                if ok:
                    changed_in_ticker = True
                    total_changed += 1
                    total_fields_modified += 1

        if changed_in_ticker and not args.dry_run:
            # タイムスタンプ記録
            adapter["_reverse_mapping_applied_at"] = datetime.now(JST).isoformat()

            # ローカル保存
            try:
                with open(adapter_path, "w", encoding="utf-8") as f:
                    json.dump(adapter, f, ensure_ascii=False, indent=2)
                log(f"[{ticker}] ローカル保存: {adapter_path}")
            except Exception as e:
                log(f"[{ticker}] ローカル保存失敗: {e}")
                failed_tickers.append(ticker)
                continue

            # GCS 同期
            if gcs_client:
                try:
                    blob = gcs_client.bucket(GCS_BUCKET).blob(
                        f"{GCS_META}/{ticker}/extract_adapter.json",
                    )
                    blob.upload_from_filename(str(adapter_path), content_type="application/json")
                    log(f"[{ticker}] GCS 同期: gs://{GCS_BUCKET}/{GCS_META}/{ticker}/extract_adapter.json")
                except Exception as e:
                    log(f"[{ticker}] GCS 同期失敗: {e}")
                    failed_tickers.append(ticker)

    # 最終サマリ
    log("")
    log("=== 適用サマリ ===")
    log(f"  対象行:         {total_rows}")
    log(f"  変更 field 数:  {total_fields_modified}")
    log(f"  変更 ticker 数: {len(by_ticker) - len(failed_tickers)}")
    if failed_tickers:
        log(f"  失敗 ticker:   {failed_tickers[:20]}")
    if args.dry_run:
        log("  ※ --dry-run のため実ファイル書き込みなし")


if __name__ == "__main__":
    main()
