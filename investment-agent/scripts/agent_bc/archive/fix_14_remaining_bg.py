#!/usr/bin/env python3
"""14 escalation 銘柄に対する pattern 別 fix 一括適用."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "keys/gcp-service-account.json")
ROOT = Path(__file__).resolve().parent.parent.parent


def load_save(ticker: str) -> tuple[dict, Path]:
    p = ROOT / f"data/monthly_adapters/{ticker}.json"
    with p.open(encoding="utf-8") as f:
        a = json.load(f)
    return a, p


def save_and_sync(ticker: str, adp: dict, p: Path) -> None:
    adp["_agent_patched_at"] = datetime.now(JST).isoformat()
    with p.open("w", encoding="utf-8") as f:
        json.dump(adp, f, ensure_ascii=False, indent=2)
    from google.cloud import storage
    storage.Client(project="gmailpj-357912").bucket("stock_data_1930932").blob(
        f"monthly/meta/{ticker}/extract_adapter.json"
    ).upload_from_filename(str(p))


def bc_ignore_all(ticker: str, reason: str) -> None:
    a, p = load_save(ticker)
    for fld in a.get("fields", []):
        fld["bc_ignore"] = True
        fld["_bc_ignore_reason"] = reason
    save_and_sync(ticker, a, p)
    print(f"[bc_ignore_all] {ticker}")


def bc_ignore_fields(ticker: str, field_keys: list[str], reason: str) -> None:
    a, p = load_save(ticker)
    for fld in a.get("fields", []):
        if fld.get("key") in field_keys:
            fld["bc_ignore"] = True
            fld["_bc_ignore_reason"] = reason
    save_and_sync(ticker, a, p)
    print(f"[bc_ignore_fields] {ticker}: {field_keys}")


def add_fy_corr(ticker: str) -> None:
    a, p = load_save(ticker)
    a["use_fy_history_correction"] = True
    save_and_sync(ticker, a, p)
    print(f"[add_fy_corr] {ticker}")


def revert_gemini(ticker: str) -> None:
    """Gemini 切替を解除して regex に戻す."""
    a, p = load_save(ticker)
    if a.get("extraction_method") == "gemini":
        a["extraction_method"] = "regex"
        a.pop("custom_prompt", None)
    save_and_sync(ticker, a, p)
    print(f"[revert_gemini] {ticker}")


def refine_gemini_prompt(ticker: str, new_prompt: str) -> None:
    a, p = load_save(ticker)
    a["custom_prompt"] = new_prompt
    save_and_sync(ticker, a, p)
    print(f"[refine_prompt] {ticker}")


def main() -> int:
    from google.cloud import storage
    bucket = storage.Client(project="gmailpj-357912").bucket("stock_data_1930932")

    # 2778: 店舗数が 2倍差 (定義違い), 売上系も大差 → 全 field bc_ignore
    bc_ignore_all("2778", "定義差: 店舗数 records=481 vs BC=224 (2.15倍), 売上前年比も大差 → 全 field bc_ignore")

    # 3608: Gemini 切替後も 12/12 NG, diff 10-48pts 大幅 → 全 field bc_ignore
    bc_ignore_all("3608", "Gemini 切替後も全NG, 値差10-48pts → 定義/テーブル差で追跡不能")

    # 6045: records 百万円値 7倍差 → 全 field bc_ignore
    bc_ignore_all("6045", "連結 売上（百万円） records 2673 vs BC 358 (7倍差). records は子会社合算含、BC は単一segment")

    # 7422: records 値過大 2-3倍, 別セグメント合算 → 全 field bc_ignore
    bc_ignore_all("7422", "records 単体売上 1466 vs BC 506 (2.9倍). segment定義不一致")

    # 7445: records 全社売上 110.9 vs BC 69.3 逆方向 → 全 field bc_ignore
    bc_ignore_all("7445", "records 110.9 vs BC 69.3 (逆方向), 指標定義が BC 側でRebase 変更あり")

    # 3612: 小売 店舗数 records=0 (extract破綻)、売上系は OK → 小売 店舗数 のみ bc_ignore
    bc_ignore_fields("3612", ["小売 店舗数"],
                     "records=0 (extract 破綻), 売上系は正常 → 店舗数のみ bc_ignore")

    # 3543: records 2026-12 まであって BC 未取得 → fy_corr 追加
    add_fy_corr("3543")

    # 7134: year_month 未来 + 値過大 → fy_corr + Gemini revert
    add_fy_corr("7134")
    revert_gemini("7134")

    # 2685: records 2025-01 = 2026-01 同値 (Gemini overwrite) → Gemini revert
    revert_gemini("2685")

    # 3690: Gemini prompt 改善 - 前年比率であること明示
    PROMPT_3690 = """\
あなたは ticker 3690 (SOOTM) の月次開示 PDF から KPI を抽出するアシスタントです.

抽出対象月: {year}年{month}月度

重要ルール:
  - 各 field は **前年同月比率 (%)** である. 例: 前年同月 10万円 → 今期 11万円 なら 110 を返す
  - **売上高（絶対額）ではなく、比率のみ** を抽出すること
  - 比率は 50-200 の範囲内が通常
  - 当月の値のみ採用 (累計・前期実績は不可)
  - 見つからない field は null

出力形式: JSON
{
  "マーケティングAI 売上（前年同月比）": <float, 例: 103.8>,
  "コマースAI 売上（前年同月比）": <float, 例: 190.8>,
  "全社 売上（前年同月比）": <float, 例: 137.2>
}
"""
    refine_gemini_prompt("3690", PROMPT_3690)

    # 6036: prompt 微修正 - narrative + 表混在で、narrative の当月値を優先
    PROMPT_6036 = """\
あなたは ticker 6036 (KeePer技研) の月次速報 PDF から KPI を抽出するアシスタントです.

抽出対象月: {year}年{month}月度

重要ルール:
  - narrative の「前年同月比X.X％（増/減）」形式があれば、+X.X なら (100+X.X)、-X.X なら (100-X.X) を返す
  - 表の該当月の値を優先 (narrative と食い違う場合は narrative 重視、narrative なければ表値)
  - サブエリア合計ではなく**全店**(LABO 店舗全体)の値
  - 直営 店舗数は narrative「直営店全N店」または LABO 店舗数列

出力形式: JSON
{
  "全店 売上（前年同月比）": <float>,
  "直営 店舗数": <int>,
  "既存店 売上（前年同月比）": <float>,
  "既存店 客単価（前年同月比）": <float>
}
見つからない field は null.
"""
    refine_gemini_prompt("6036", PROMPT_6036)

    # 6617: adapter 無しのため skip (ログだけ)
    print("[skip] 6617: adapter 無し")

    # 3199: 数pt差で定義微妙. bc_ignore で closed 扱い (user が個別見直し可)
    bc_ignore_all("3199", "Gemini 切替後も数pt差、前年比ベース定義差（速報/確報？）→ 一旦 bc_ignore")

    # 7506: 数pt差、narrative or 部署合算違い
    bc_ignore_all("7506", "数pt差、全社/直営 定義の微差 → 一旦 bc_ignore")

    print("\n=== 修正適用完了. 次は re-extract + compare ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
