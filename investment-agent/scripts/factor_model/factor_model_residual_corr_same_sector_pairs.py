"""factor_model_residual_corr ノートブックの子ツール: 同業種×低残差相関ペア抽出.

親ツール `scripts/factor_model/factor_model_residual_corr.ipynb` が出力した
`residual_corr_YYYY.csv` / `clusters_YYYY.csv` を読み込み、同じINDUSTRY_33
に属するペアのうち残差相関が低いもの（市場が差別化しているペア候補）を
CSVに書き出す.

使い方:
    # 最新年（2025）だけで抽出
    PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py \\
        --years 2025 --threshold 0.2

    # 2024/2025両年で安定して低連動なペアのみ抽出（推奨）
    PYTHONUTF8=1 python scripts/factor_model/factor_model_residual_corr_same_sector_pairs.py \\
        --years 2024 2025 --threshold 0.2 --stable

入力:
    親ツール出力先 (Colab): G:\\マイドライブ\\analysis\\factor_model\\
    親ツール出力先 (ローカル): C:\\tmp\\factor_model_output\\
    既定は Google Drive. --input-dir で上書き可.

出力:
    C:\\tmp\\factor_model_output\\same_sector_low_corr_<years>.csv
"""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import pandas as pd
import structlog

log = structlog.get_logger()

INPUT_DIR_DEFAULT = Path(r"G:\マイドライブ\analysis\factor_model")
OUTPUT_DIR = Path(r"C:\tmp\factor_model_output")


def load_year(year: int, input_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """指定年の残差相関行列と銘柄メタを読み込む.

    Args:
        year: 対象年.
        input_dir: 親ツール出力が置かれているディレクトリ.

    Returns:
        (corr, meta)
        corr: 残差相関行列 (index/columns = TICKER).
        meta: TICKER / CLUSTER / STOCK_NAME / INDUSTRY_33 を持つメタDataFrame.
    """
    corr_path = input_dir / f"residual_corr_{year}.csv"
    meta_path = input_dir / f"clusters_{year}.csv"
    if not corr_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"親ツール出力が見つかりません: {corr_path} または {meta_path}. "
            f"先に factor_model_residual_corr.ipynb を実行してください."
        )

    corr = pd.read_csv(corr_path, index_col=0, encoding="utf-8")
    meta = pd.read_csv(meta_path, encoding="utf-8")

    corr.index = corr.index.astype(str)
    corr.columns = corr.columns.astype(str)
    meta["TICKER"] = meta["TICKER"].astype(str)
    return corr, meta


def build_pairs(corr: pd.DataFrame, meta: pd.DataFrame, year: int) -> pd.DataFrame:
    """同INDUSTRY_33ペアに残差相関を付与したDataFrameを返す.

    Args:
        corr: 残差相関行列.
        meta: TICKER / INDUSTRY_33 / STOCK_NAME を持つメタ.
        year: 付与する列名に使う年 (r_YYYY).

    Returns:
        columns = [industry_33, a, name_a, b, name_b, r_YYYY]
    """
    name_map = meta.set_index("TICKER")["STOCK_NAME"].to_dict()
    rows = []
    for industry, grp in meta.groupby("INDUSTRY_33"):
        tickers = [t for t in grp["TICKER"].tolist() if t in corr.index]
        for a, b in combinations(tickers, 2):
            rows.append(
                {
                    "industry_33": industry,
                    "a": a,
                    "name_a": name_map.get(a, ""),
                    "b": b,
                    "name_b": name_map.get(b, ""),
                    f"r_{year}": corr.loc[a, b],
                }
            )
    return pd.DataFrame(rows)


def extract_low_corr_pairs(
    years: list[int], threshold: float, stable: bool, input_dir: Path
) -> pd.DataFrame:
    """指定年の残差相関をマージして低連動ペアを抽出する.

    Args:
        years: 対象年リスト. 昇順で指定.
        threshold: 残差相関の閾値 (未満を採用).
        stable: True の場合、全年で threshold 未満のペアのみ残す.
                False の場合、最新年 (years[-1]) のみで判定.
        input_dir: 親ツール出力が置かれているディレクトリ.

    Returns:
        抽出結果DataFrame. r_mean 昇順.
    """
    merged: pd.DataFrame | None = None
    for y in years:
        corr, meta = load_year(y, input_dir)
        df = build_pairs(corr, meta, y)
        log.info("loaded_year", year=y, pairs=len(df))
        if merged is None:
            merged = df
        else:
            merged = merged.merge(
                df.drop(columns=["name_a", "name_b"]),
                on=["industry_33", "a", "b"],
                how="inner",
            )

    assert merged is not None
    corr_cols = [f"r_{y}" for y in years]

    if stable:
        mask = (merged[corr_cols] < threshold).all(axis=1)
    else:
        mask = merged[corr_cols[-1]] < threshold

    result = merged.loc[mask].copy()
    result["r_mean"] = result[corr_cols].mean(axis=1)
    result = result.sort_values(["industry_33", "r_mean"]).reset_index(drop=True)
    return result


def main() -> None:
    """CLIエントリポイント."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=[2024, 2025],
        help="対象年 (例: --years 2024 2025). 複数指定時は昇順推奨",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.2,
        help="残差相関の閾値 (未満を採用). default=0.2",
    )
    ap.add_argument(
        "--stable",
        action="store_true",
        help="全年で閾値未満のペアのみ抽出 (複数年指定時に推奨)",
    )
    ap.add_argument(
        "--input-dir",
        type=Path,
        default=INPUT_DIR_DEFAULT,
        help=f"親ツール出力ディレクトリ. default={INPUT_DIR_DEFAULT}",
    )
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    years_sorted = sorted(args.years)
    result = extract_low_corr_pairs(
        years=years_sorted,
        threshold=args.threshold,
        stable=args.stable,
        input_dir=args.input_dir,
    )

    suffix = "_".join(str(y) for y in years_sorted)
    if args.stable and len(years_sorted) > 1:
        suffix += "_stable"
    out_path = OUTPUT_DIR / f"same_sector_low_corr_{suffix}.csv"
    result.to_csv(out_path, index=False, encoding="utf-8-sig")

    log.info(
        "saved",
        path=str(out_path),
        rows=len(result),
        threshold=args.threshold,
        stable=args.stable,
    )
    if len(result) > 0:
        print(f"\n=== 抽出結果サマリー ===")
        print(f"  総ペア数: {len(result)}")
        print(f"  業種数  : {result['industry_33'].nunique()}")
        print(f"\n--- TOP10 (低連動) ---")
        print(result.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
