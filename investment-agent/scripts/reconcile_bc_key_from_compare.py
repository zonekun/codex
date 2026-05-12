#!/usr/bin/env python3
"""bc_key 逆引きマッピング候補生成スクリプト.

compare_monthly_buffett.py の結果 CSV から NG / BC_NODATA 行を抽出し、
BC 全フィールド × 誤差調整（yoy+100, ×N, ÷N, 符号反転, round/floor/ceil）
のマトリクスで値一致率を計算し、最高一致率の組合せを bc_key 候補として提案する。

【使い方】
  PYTHONUTF8=1 python scripts/reconcile_bc_key_from_compare.py \
    --compare-csv C:/tmp/buffett_compare_20260418_221847.csv \
    [--bc-csv data/csv/bc_monthly_kpi.csv] \
    [--adapter-index meta/_index/monthly_adapter_index.csv] \
    [--output data/logs/bc_key_reverse_mapping_<ts>.csv] \
    [--min-ratio 0.5]

【出力】
  SJIS (cp932) / CRLF / match_ratio 降順ソート
  15 カラム: ticker / our_key / status / current_bc_field / current_diff /
             suggested_bc_key / adjustment / suggested_yoy_offset / suggested_unit_scale /
             match_ratio / our_value_samples / bc_value_samples / implied_fix /
             bc_url / source_url
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))
TOLERANCE = 0.5

# 誤差調整の候補: (name, apply_fn)
ADJUSTMENTS: list[tuple[str, Any]] = [
    ("identity",  lambda v: v),
    ("yoy+100",   lambda v: v + 100),
    ("yoy-100",   lambda v: v - 100),
    ("×10",       lambda v: v * 10),
    ("×100",      lambda v: v * 100),
    ("×1000",     lambda v: v * 1000),
    ("×10000",    lambda v: v * 10000),
    ("×100000",   lambda v: v * 100000),
    ("×1000000",  lambda v: v * 1000000),
    ("÷10",       lambda v: v / 10),
    ("÷100",      lambda v: v / 100),
    ("÷1000",     lambda v: v / 1000),
    ("÷10000",    lambda v: v / 10000),
    ("÷100000",   lambda v: v / 100000),
    ("÷1000000",  lambda v: v / 1000000),
    ("negate",    lambda v: -v),
]


# 意味カテゴリー分離 (全店/既存店 と 売上/客数/客単価/店舗数 の交叉マッピングを防ぐ)
_STORE_TOKENS = {"全店", "既存店"}
_METRIC_TOKENS = {"売上", "客数", "客単価", "店舗数", "取扱高", "稼働率", "室数",
                  "発電量", "発電電力量", "来店者", "入居率", "搭乗率"}


def _extract_category_tokens(key: str) -> tuple[set[str], set[str]]:
    """key から (store_tokens, metric_tokens) を抽出."""
    store = {t for t in _STORE_TOKENS if t in key}
    metric = {t for t in _METRIC_TOKENS if t in key}
    return store, metric


def semantic_category_match(our_key: str, bc_field: str) -> str:
    """意味カテゴリの一致度を返す.

    Returns:
      'same'    : 両側のカテゴリトークンが完全一致（安全な提案）
      'unknown' : 片方にカテゴリトークンが存在しない（判定困難、保留）
      'cross'   : カテゴリトークンが不一致（全店↔既存店 or 売上↔客数等、明らかに意味違い）
    """
    our_store, our_metric = _extract_category_tokens(our_key)
    bc_store, bc_metric = _extract_category_tokens(bc_field)

    if not (our_store or our_metric) or not (bc_store or bc_metric):
        return "unknown"

    # 店舗セグメント (全店 vs 既存店) が両側にある場合は一致必須
    if our_store and bc_store and our_store != bc_store:
        return "cross"
    # 指標カテゴリ (売上/客数/客単価/店舗数等) が両側にある場合は一致必須
    if our_metric and bc_metric and our_metric != bc_metric:
        return "cross"

    if our_store == bc_store and our_metric == bc_metric:
        return "same"
    return "unknown"


def match_with_precision(our_val: float, bc_val: float, tol: float = TOLERANCE) -> bool:
    """BC 表示精度を自動検出し、round/floor/ceil の 3 候補で一致判定."""
    try:
        bc_str = f"{bc_val:.10g}"
        decimals = len(bc_str.split(".")[1]) if "." in bc_str else 0
        unit = 10 ** decimals
        candidates = {
            round(our_val * unit) / unit,
            math.floor(our_val * unit) / unit,
            math.ceil(our_val * unit) / unit,
        }
        if any(abs(bc_val - c) < 1e-9 for c in candidates):
            return True
        return min(abs(bc_val - c) for c in candidates) <= tol
    except (ValueError, TypeError, OverflowError):
        return False


def adjustment_to_params(adj_name: str) -> tuple[float, float]:
    """adjustment 名から (yoy_offset, unit_scale) タプルを返す."""
    if adj_name == "identity":
        return (0.0, 1.0)
    if adj_name == "yoy+100":
        return (100.0, 1.0)
    if adj_name == "yoy-100":
        return (-100.0, 1.0)
    if adj_name.startswith("×"):
        return (0.0, float(adj_name[1:]))
    if adj_name.startswith("÷"):
        return (0.0, 1.0 / float(adj_name[1:]))
    if adj_name == "negate":
        return (0.0, -1.0)
    return (0.0, 1.0)


def format_samples(vals: list[float], max_n: int = 3) -> str:
    """値のリストを `[v1, v2, v3]` 形式に整形."""
    trimmed = vals[:max_n]
    def _fmt(v: float) -> str:
        if v is None or math.isnan(v) or math.isinf(v):
            return "nan"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:g}"
    return "[" + ", ".join(_fmt(v) for v in trimmed) + "]"


def build_implied_fix(best_bc: str | None, best_adj: str | None,
                      yoy_offset: str, unit_scale: str, ratio: float) -> str:
    """CSV の implied_fix カラム用の推奨アクション文字列."""
    if best_bc is None or ratio <= 0:
        return "該当 BC なし → bc_ignore=true 検討"
    if ratio >= 0.9:
        parts = [f"bc_key={best_bc}"]
        if yoy_offset:
            parts.append(f"yoy_offset={yoy_offset}")
        if unit_scale:
            parts.append(f"unit_scale={unit_scale}")
        return ", ".join(parts) + " を設定"
    if ratio >= 0.5:
        return f"部分一致 {ratio:.0%} → 要確認"
    return f"一致率低 {ratio:.0%} → 要手動"


def gemini_semantic_match(
    low_conf_rows: list[dict[str, Any]],
    ticker_to_bc_fields: dict[str, set[str]],
) -> dict[tuple[str, str], str]:
    """低信頼行の (ticker, our_key) に対し Gemini で意味的に近い bc_field を推薦.

    Returns: {(ticker, our_key): suggested_bc_field} 辞書.
    """
    import os as _os
    import json as _json
    from dotenv import load_dotenv
    load_dotenv()
    api_key = _os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(f"[{datetime.now(JST):%H:%M:%S}] GEMINI_API_KEY 未設定 → semantic matching skip")
        return {}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print(f"[{datetime.now(JST):%H:%M:%S}] google-genai 未インストール → skip")
        return {}

    client = genai.Client(api_key=api_key)
    model = "gemini-3-flash-preview"

    # ticker ごとにグループ化
    grouped: dict[str, list[str]] = {}
    for r in low_conf_rows:
        t = r["ticker"]
        grouped.setdefault(t, []).append(r["our_key"])

    result: dict[tuple[str, str], str] = {}
    print(f"[{datetime.now(JST):%H:%M:%S}] Gemini semantic matching: {len(grouped)} ticker, 合計 {len(low_conf_rows)} 行")

    for i, (ticker, our_keys) in enumerate(grouped.items(), 1):
        bc_fields = sorted(ticker_to_bc_fields.get(ticker, set()))
        if not bc_fields:
            continue
        our_keys_unique = sorted(set(our_keys))
        prompt = f"""\
ticker {ticker} の月次開示データで、以下の our_key 各々に対して最も意味的に近い bc_field を選んでください。

候補 bc_fields:
{chr(10).join(f'  - {bc}' for bc in bc_fields)}

our_keys:
{chr(10).join(f'  - {k}' for k in our_keys_unique)}

ルール:
- 「全店 X」と「既存店 X」は別物。混同禁止
- 「売上/客数/客単価/店舗数」は別物。混同禁止
- ブランド名変更・統合は許容（例: 「ドン・キホーテ」→「国内小売」等）
- セグメント集約の変化は許容（例: 「ディスカウント事業」→「国内小売事業」）
- 意味的に該当する bc_field が無い場合は "NONE" を返す

出力形式: JSON object
  {{"<our_key1>": "<bc_field or NONE>", "<our_key2>": "<bc_field or NONE>", ...}}
"""
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
            raw = resp.text or ""
            try:
                mapping = _json.loads(raw)
                for ok, bc in mapping.items():
                    if bc and bc != "NONE" and bc in bc_fields:
                        result[(ticker, ok)] = bc
                if i % 10 == 0 or i == len(grouped):
                    print(f"  [{i}/{len(grouped)}] {ticker}: {len(mapping)} 件処理")
            except _json.JSONDecodeError:
                print(f"  [{ticker}] JSON parse fail: {raw[:200]}")
        except Exception as e:
            print(f"  [{ticker}] Gemini call error: {e}")
    return result


def load_ir_url_map(adapter_index_path: Path) -> dict[str, str]:
    """monthly_adapter_index.csv から ticker → IR page URL の辞書を返す."""
    urls: dict[str, str] = {}
    if not adapter_index_path.exists():
        return urls
    with open(adapter_index_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            t = (r.get("ticker") or "").strip()
            url = (r.get("monthly_page_url") or "").strip()
            if t and url:
                urls[t] = url
    return urls


def build_source_url(ticker: str, ir_url_map: dict[str, str]) -> str:
    """source URL: 非 TDnet（IR URL 登録あり）なら IR URL、なければ GCS console URL."""
    if ticker in ir_url_map:
        return ir_url_map[ticker]
    return f"https://console.cloud.google.com/storage/browser/stock_data_1930932/tdnet/{ticker}/"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="compare CSV から bc_key 候補を逆引き生成",
    )
    parser.add_argument("--compare-csv", required=True,
                        help="compare_monthly_buffett.py の出力 CSV")
    parser.add_argument("--bc-csv", default="data/csv/bc_monthly_kpi.csv",
                        help="BC 月次 KPI キャッシュ (デフォルト: data/csv/bc_monthly_kpi.csv)")
    parser.add_argument("--adapter-index", default="meta/_index/monthly_adapter_index.csv",
                        help="non-tdnet URL ルックアップ用")
    parser.add_argument("--output", default="",
                        help="出力 CSV パス（省略時 data/logs/bc_key_reverse_mapping_<ts>.csv）")
    parser.add_argument("--min-ratio", type=float, default=0.0,
                        help="出力対象の最低一致率（デフォルト全件）")
    parser.add_argument("--gemini-semantic", action="store_true",
                        help="低信頼行に対し Gemini semantic matching を実施（ブランド名変更等のズレ救済）")
    parser.add_argument("--exclude-applied", action="store_true", default=True,
                        help="adapter.json に既に bc_key を設定済の field は出力から除外（デフォルト有効）")
    args = parser.parse_args()

    # 1) compare CSV 読み込み: NG + BC_NODATA 行のみ
    target_rows: list[dict[str, str]] = []
    with open(args.compare_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            m = r.get("match", "")
            if m in ("NG", "BC_NODATA"):
                target_rows.append(r)
    print(f"[{datetime.now(JST):%H:%M:%S}] NG+BC_NODATA 行: {len(target_rows)}")

    # 2) BC CSV 読み込み: {ticker: {ym: {field: val}}}
    bc_data: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    with open(args.bc_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            t = r.get("ticker", "")
            ym = r.get("year_month", "")
            fld = r.get("field", "")
            try:
                v = float(r.get("value", ""))
            except (ValueError, TypeError):
                continue
            bc_data[t][ym][fld] = v
    print(f"[{datetime.now(JST):%H:%M:%S}] BC CSV: {len(bc_data)} 銘柄")

    # 3) IR URL マップ
    ir_url_map = load_ir_url_map(Path(args.adapter_index))
    print(f"[{datetime.now(JST):%H:%M:%S}] IR URL マップ: {len(ir_url_map)} 銘柄")

    # 4) (ticker, our_key) ごとにサンプル集約
    samples: dict[tuple[str, str], list[dict]] = defaultdict(list)
    current_bc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in target_rows:
        key = (r.get("ticker", ""), r.get("our_field", ""))
        try:
            ov = float(r.get("our_value", ""))
        except (ValueError, TypeError):
            continue
        samples[key].append({
            "ym": r.get("year_month", ""),
            "our_value": ov,
            "status": r.get("match", ""),
        })
        if r.get("match") == "NG" and r.get("bc_field"):
            current_bc[key] = {
                "bc_field": r["bc_field"],
                "diff": r.get("diff", ""),
            }

    # 5) 各 (ticker, our_key) について逆引き
    output_rows: list[dict[str, Any]] = []
    for (ticker, our_key), sample_list in samples.items():
        ticker_bc = bc_data.get(ticker, {})
        all_bc_fields: set[str] = set()
        for ym_fields in ticker_bc.values():
            all_bc_fields.update(ym_fields.keys())

        best = {"ratio": 0.0, "bc_field": None, "adj": None, "bc_vals": [],
                "total": 0, "sem": "unknown"}

        for bc_field in all_bc_fields:
            for adj_name, adj_fn in ADJUSTMENTS:
                match_count = 0
                total = 0
                bc_vals: list[float] = []
                for sample in sample_list:
                    ym = sample["ym"]
                    our_v = sample["our_value"]
                    bc_v = ticker_bc.get(ym, {}).get(bc_field)
                    if bc_v is None:
                        continue
                    total += 1
                    bc_vals.append(bc_v)
                    try:
                        adj_v = adj_fn(our_v)
                    except (ZeroDivisionError, OverflowError, ValueError):
                        continue
                    if match_with_precision(adj_v, bc_v):
                        match_count += 1
                if total == 0:
                    continue
                ratio = match_count / total

                # 意味カテゴリガード: cross (全店↔既存店 or 売上↔客数等) は
                # match_ratio=1.0 かつ total>=3 でない限り候補から除外
                sem = semantic_category_match(our_key, bc_field)
                if sem == "cross" and (ratio < 1.0 or total < 3):
                    continue

                # tie-break 優先順:
                #   1) semantic: same > unknown > cross
                #   2) match_ratio 高い方
                #   3) total 多い方
                _sem_rank = {"same": 2, "unknown": 1, "cross": 0}
                cur_sem_rank = _sem_rank[sem]
                best_sem_rank = _sem_rank.get(best["sem"], 1)
                better = (
                    (cur_sem_rank > best_sem_rank)
                    or (cur_sem_rank == best_sem_rank and ratio > best["ratio"])
                    or (cur_sem_rank == best_sem_rank and ratio == best["ratio"] and total > best["total"])
                )
                if better:
                    best = {
                        "ratio": ratio,
                        "bc_field": bc_field,
                        "adj": adj_name,
                        "bc_vals": bc_vals,
                        "total": total,
                        "sem": sem,
                    }

        if best["ratio"] < args.min_ratio and best["bc_field"] is not None:
            continue  # 最低一致率未達

        # 行データ組み立て
        status = "NG" if any(s["status"] == "NG" for s in sample_list) else "BC_NODATA"
        our_vals = [s["our_value"] for s in sample_list]
        cur = current_bc.get((ticker, our_key), {})

        yoy_offset_str = ""
        unit_scale_str = ""
        if best["adj"]:
            yo, us = adjustment_to_params(best["adj"])
            if yo != 0:
                yoy_offset_str = str(int(yo) if yo == int(yo) else yo)
            if us != 1.0:
                if us == int(us):
                    unit_scale_str = str(int(us))
                else:
                    unit_scale_str = f"{us:g}"

        implied = build_implied_fix(
            best["bc_field"], best["adj"], yoy_offset_str, unit_scale_str, best["ratio"],
        )

        output_rows.append({
            "ticker":               ticker,
            "our_key":              our_key,
            "status":               status,
            "current_bc_field":     cur.get("bc_field", ""),
            "current_diff":         cur.get("diff", ""),
            "suggested_bc_key":     best["bc_field"] or "",
            "adjustment":           best["adj"] or "no_match",
            "suggested_yoy_offset": yoy_offset_str,
            "suggested_unit_scale": unit_scale_str,
            "semantic":             best.get("sem", "unknown"),
            "match_ratio":          f"{best['ratio']:.2f}",
            "our_value_samples":    format_samples(our_vals),
            "bc_value_samples":     format_samples(best["bc_vals"]),
            "implied_fix":          implied,
            "bc_url":               f"https://www.buffett-code.com/company/{ticker}/kpi",
            "source_url":           build_source_url(ticker, ir_url_map),
        })

    # 5.4) 既に apply 済 (adapter.bc_key 設定済) の (ticker, our_key) を除外
    if args.exclude_applied:
        from pathlib import Path as _Path
        adapter_dir = _Path("meta/monthly")
        applied_set: set[tuple[str, str]] = set()
        if adapter_dir.exists():
            import json as _j
            for p in adapter_dir.glob("*_extract_adapter.json"):
                try:
                    adp = _j.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ticker_ = p.name.removesuffix("_extract_adapter.json")
                for fld in adp.get("fields", []):
                    key = fld.get("key") or fld.get("name", "")
                    bc_key = fld.get("bc_key")
                    if key and bc_key:  # bc_key 明示済 = apply 済
                        applied_set.add((ticker_, key))
                    if fld.get("bc_ignore"):  # bc_ignore=true も除外
                        applied_set.add((ticker_, key))
        before_cnt = len(output_rows)
        output_rows = [r for r in output_rows
                       if (r["ticker"], r["our_key"]) not in applied_set]
        print(f"[{datetime.now(JST):%H:%M:%S}] apply 済行を除外: {before_cnt} → {len(output_rows)} "
              f"(除外 {before_cnt - len(output_rows)} 件)")

    # 5.5) Gemini semantic matching (low-confidence 救済)
    if args.gemini_semantic:
        low_conf_rows = [r for r in output_rows if float(r["match_ratio"] or 0) < 0.5]
        print(f"[{datetime.now(JST):%H:%M:%S}] 低信頼 (<0.5): {len(low_conf_rows)} 行 → Gemini 対象")

        ticker_to_bc_fields: dict[str, set[str]] = {}
        for t, ym_fields in bc_data.items():
            all_fs: set[str] = set()
            for fs in ym_fields.values():
                all_fs.update(fs.keys())
            ticker_to_bc_fields[t] = all_fs

        gemini_map = gemini_semantic_match(low_conf_rows, ticker_to_bc_fields)
        print(f"[{datetime.now(JST):%H:%M:%S}] Gemini suggestion 取得: {len(gemini_map)} 件")

        # Gemini 提案を verify しながら行更新
        updated_count = 0
        for r in output_rows:
            key_t = (r["ticker"], r["our_key"])
            if key_t not in gemini_map:
                continue
            gemini_bc = gemini_map[key_t]

            # 数値一致率を再計算
            sample_list = samples.get(key_t, [])
            ticker_bc = bc_data.get(r["ticker"], {})
            best_ratio = 0.0
            best_adj_name = None
            best_bc_vals: list[float] = []
            for adj_name, adj_fn in ADJUSTMENTS:
                mc = 0
                tot = 0
                vals: list[float] = []
                for s in sample_list:
                    bv = ticker_bc.get(s["ym"], {}).get(gemini_bc)
                    if bv is None:
                        continue
                    tot += 1
                    vals.append(bv)
                    try:
                        if match_with_precision(adj_fn(s["our_value"]), bv):
                            mc += 1
                    except Exception:
                        pass
                if tot and (mc / tot) > best_ratio:
                    best_ratio = mc / tot
                    best_adj_name = adj_name
                    best_bc_vals = vals
            if best_ratio > float(r["match_ratio"] or 0):
                yo_s = ""
                us_s = ""
                if best_adj_name:
                    yo, us = adjustment_to_params(best_adj_name)
                    if yo != 0:
                        yo_s = str(int(yo) if yo == int(yo) else yo)
                    if us != 1.0:
                        us_s = str(int(us) if us == int(us) else f"{us:g}")
                r["suggested_bc_key"] = gemini_bc
                r["adjustment"] = best_adj_name or "no_match"
                r["suggested_yoy_offset"] = yo_s
                r["suggested_unit_scale"] = us_s
                r["semantic"] = "gemini"
                r["match_ratio"] = f"{best_ratio:.2f}"
                r["bc_value_samples"] = format_samples(best_bc_vals)
                r["implied_fix"] = build_implied_fix(gemini_bc, best_adj_name, yo_s, us_s, best_ratio)
                updated_count += 1
        print(f"[{datetime.now(JST):%H:%M:%S}] Gemini 適用後 数値一致率が向上した行: {updated_count}")

    # 6) ソート: semantic (same > gemini > unknown > cross) → match_ratio 降順 → ticker 昇順
    _sem_sort = {"same": 0, "gemini": 1, "unknown": 2, "cross": 3}
    output_rows.sort(key=lambda r: (
        _sem_sort.get(r.get("semantic", "unknown"), 2),
        -float(r["match_ratio"] or 0),
        r["ticker"],
        r["our_key"],
    ))

    # 7) 出力
    out_path = args.output
    if not out_path:
        ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
        out_path = f"data/logs/bc_key_reverse_mapping_{ts}.csv"

    fieldnames = [
        "ticker", "our_key", "status", "current_bc_field", "current_diff",
        "suggested_bc_key", "adjustment", "suggested_yoy_offset", "suggested_unit_scale",
        "semantic", "match_ratio", "our_value_samples", "bc_value_samples", "implied_fix",
        "bc_url", "source_url",
    ]
    with open(out_path, "w", encoding="cp932", newline="", errors="replace") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in output_rows:
            writer.writerow(row)

    # サマリ
    high_conf = sum(1 for r in output_rows if float(r["match_ratio"]) >= 0.9)
    mid_conf = sum(1 for r in output_rows if 0.5 <= float(r["match_ratio"]) < 0.9)
    low_conf = sum(1 for r in output_rows if float(r["match_ratio"]) < 0.5)
    sem_same = sum(1 for r in output_rows if r.get("semantic") == "same")
    sem_unknown = sum(1 for r in output_rows if r.get("semantic") == "unknown")
    sem_cross = sum(1 for r in output_rows if r.get("semantic") == "cross")
    print(f"[{datetime.now(JST):%H:%M:%S}] 出力: {out_path}")
    print(f"  合計:       {len(output_rows)} 行")
    print(f"  semantic:   same={sem_same}, unknown={sem_unknown}, cross={sem_cross}")
    print(f"  高信頼 ≥0.9: {high_conf} 件（即承認候補）")
    print(f"  中信頼 0.5〜: {mid_conf} 件（要確認）")
    print(f"  低信頼 <0.5: {low_conf} 件（要手動）")


if __name__ == "__main__":
    main()
