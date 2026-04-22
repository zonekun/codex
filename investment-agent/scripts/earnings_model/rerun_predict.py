"""決算反応モデル predict ノートブックのバッチリラン.

Usage:
    PYTHONUTF8=1 C:/venvs/investment-agent/Scripts/python.exe scripts/earnings_model/rerun_predict.py \
        --dates 20260402:20260403 20260403:20260406 20260406:20260407

各日付ペアは PREDICT_DATE:ACTUAL_DATE の形式。
最後の日付ペアでのみ精度集計(Cell 11)を実行する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NOTEBOOK_PATH = Path(__file__).parent / "earnings_model_predict.ipynb"


def extract_code_cells(nb_path: Path) -> list[str]:
    """ノートブックからコードセルのソースを抽出する.

    Args:
        nb_path: ノートブックファイルのパス.

    Returns:
        コードセルのソース文字列リスト.
    """
    with open(nb_path, encoding="utf-8") as f:
        nb = json.load(f)
    cells: list[str] = []
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            cells.append("".join(cell["source"]))
    return cells


def clean_source(src: str) -> str:
    """マジックコマンド・display・末尾の変数表示を除去する.

    Args:
        src: セルのソースコード.

    Returns:
        クリーニング済みソースコード.
    """
    lines: list[str] = []
    for line in src.split("\n"):
        # マジックコマンド除去
        if line.strip().startswith("%"):
            continue
        # display() → print()
        line = line.replace("display(", "print(")
        lines.append(line)

    # 末尾の変数名だけの行を除去 (e.g. "df_features")
    while lines and re.match(r"^\s*\w+\s*$", lines[-1]):
        lines.pop()

    return "\n".join(lines)


def fix_escaped_backticks(src: str) -> str:
    """JSON由来のエスケープ済みバッククォートを正規化する.

    Args:
        src: ソースコード.

    Returns:
        修正済みソースコード.
    """
    return src.replace("\\`", "`")


def run_date_pair(
    code_cells: list[str],
    predict_date: str,
    actual_date: str,
    run_accuracy: bool,
) -> None:
    """1つの日付ペアに対してリランを実行する.

    Args:
        code_cells: ノートブックのコードセル一覧.
        predict_date: 予測対象日 (YYYYMMDD).
        actual_date: 答え合わせ日 (YYYYMMDD).
        run_accuracy: 精度集計(Cell 11相当)を実行するか.
    """
    print(f"\n{'=' * 60}")
    print(f"  PREDICT_DATE={predict_date}  ACTUAL_DATE={actual_date}")
    print(f"{'=' * 60}\n")

    # Cell index mapping: code_cells[0]=Cell1, [1]=Cell2(pip), [2]=Cell3, ...
    # Cell 1: 日付設定 → 差し替え
    predict_hyphen = f"{predict_date[:4]}-{predict_date[4:6]}-{predict_date[6:8]}"
    actual_hyphen = f"{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:8]}"
    date_src = f"""
PREDICT_DATE = '{predict_date}'
ACTUAL_DATE = '{actual_date}'
PREDICT_DATE_HYPHEN = '{predict_hyphen}'
ACTUAL_DATE_HYPHEN = '{actual_hyphen}'
print(f'予測対象日: {{PREDICT_DATE_HYPHEN}}  答え合わせ日: {{ACTUAL_DATE_HYPHEN}}')
"""

    # Cell 2: %pip install → スキップ
    # Cell 3: import + setup (code_cells[2])
    # Cell 5: データ取得 + 特徴量 (code_cells[3] = markdown なので code_cells[3] is actually...)
    # ノートブック構成: code cells are at indices 1,2,3,5,7,9,11
    # code_cells: [0]=Cell1, [1]=Cell2(pip), [2]=Cell3, [3]=Cell5, [4]=Cell7, [5]=Cell9, [6]=Cell11

    # 実行するセル: Cell1(日付), Cell3(setup), Cell5(データ), Cell7(スコア)
    # Cell9(答え合わせ), Cell11(精度集計: 最後のみ)
    setup_src = clean_source(code_cells[2])  # Cell 3
    data_src = clean_source(code_cells[3])    # Cell 5
    score_src = clean_source(code_cells[4])   # Cell 7

    # Cell 9: actual_return バグ修正済みのソースを使用
    answer_src = clean_source(code_cells[5])  # Cell 9

    # 結合して実行
    combined = "\n\n".join([date_src, setup_src, data_src, score_src, answer_src])

    if run_accuracy:
        accuracy_src = clean_source(code_cells[6])  # Cell 11
        combined += "\n\n" + accuracy_src

    combined = fix_escaped_backticks(combined)

    exec(compile(combined, f"<rerun_{predict_date}>", "exec"), {})


def main() -> None:
    """メインエントリポイント."""
    parser = argparse.ArgumentParser(description="earnings_model_predict バッチリラン")
    parser.add_argument(
        "--dates",
        nargs="+",
        required=True,
        help="PREDICT_DATE:ACTUAL_DATE のペア (例: 20260402:20260403)",
    )
    args = parser.parse_args()

    date_pairs: list[tuple[str, str]] = []
    for pair in args.dates:
        parts = pair.split(":")
        if len(parts) != 2:
            print(f"ERROR: Invalid date pair format: {pair}", file=sys.stderr)
            sys.exit(1)
        date_pairs.append((parts[0], parts[1]))

    code_cells = extract_code_cells(NOTEBOOK_PATH)
    print(f"Loaded {len(code_cells)} code cells from {NOTEBOOK_PATH}")

    for i, (pred, act) in enumerate(date_pairs):
        is_last = i == len(date_pairs) - 1
        run_date_pair(code_cells, pred, act, run_accuracy=is_last)

    print("\n=== All done ===")


if __name__ == "__main__":
    main()
