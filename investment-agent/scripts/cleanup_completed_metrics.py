"""受注アダプタ structure.json から非先行指標メトリクス(type:completed)を除去する."""

import argparse
import csv
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime


def cleanup_structure(data: dict) -> tuple[dict, dict]:
    """structure.jsonからcompletedメトリクスを除去.

    Returns:
        (modified_data, change_info) — change_info has keys:
        action: "delete_adapter" | "remove_completed" | "no_change"
        removed_metrics: list of removed metric names
        remaining_metrics: count of remaining metrics
    """
    current_ver = data.get("current_version")
    change_info = {
        "ticker": data.get("ticker", ""),
        "company_name": data.get("company_name", ""),
        "action": "no_change",
        "removed_metrics": [],
        "remaining_metrics": 0,
    }

    for version in data.get("versions", []):
        if version.get("valid_from") != current_ver:
            continue

        if not version.get("data_available", True):
            return data, change_info

        metrics = version.get("metrics", [])
        if not metrics:
            return data, change_info

        leading = [m for m in metrics if m.get("type") in ("order_intake", "backlog")]
        completed = [m for m in metrics if m.get("type") == "completed"]

        if not completed:
            change_info["remaining_metrics"] = len(leading)
            return data, change_info

        completed_names = {m["name"] for m in completed}
        change_info["removed_metrics"] = sorted(completed_names)

        if not leading:
            version["data_available"] = False
            version["metrics"] = []
            version["breakdown_dimensions"] = []
            now_str = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
            existing_notes = version.get("notes", "")
            version["notes"] = (
                existing_notes
                + f" [{now_str}] completedメトリクスのみのため収集対象外(false化)。"
            )
            change_info["action"] = "delete_adapter"
            change_info["remaining_metrics"] = 0
        else:
            orphan_children = [
                m
                for m in metrics
                if m.get("parent_metric") in completed_names
                and m.get("type") == "completed"
            ]
            remove_names = completed_names | {m["name"] for m in orphan_children}
            change_info["removed_metrics"] = sorted(remove_names)

            new_metrics = [m for m in metrics if m["name"] not in remove_names]
            version["metrics"] = new_metrics

            remaining_metric_names = {m["name"] for m in new_metrics}
            bd = version.get("breakdown_dimensions", [])
            new_bd = []
            for dim in bd:
                items = dim.get("items", [])
                has_reference = any(
                    item in name
                    for item in items
                    for name in remaining_metric_names
                )
                if has_reference:
                    new_bd.append(dim)
            version["breakdown_dimensions"] = new_bd

            change_info["action"] = "remove_completed"
            change_info["remaining_metrics"] = len(new_metrics)

        break

    return data, change_info


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove completed metrics from structure.json")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without modifying files")
    parser.add_argument("--meta-dir", default="meta/quarterly", help="Directory containing structure.json files")
    parser.add_argument("--output-csv", default="C:/tmp/cleanup_completed_summary.csv", help="Summary CSV path")
    args = parser.parse_args()

    meta_dir = Path(args.meta_dir)
    structures = sorted(meta_dir.glob("*_structure.json"))

    results = []
    delete_adapter_tickers = []

    for f in structures:
        data = json.loads(f.read_text(encoding="utf-8"))
        modified, info = cleanup_structure(data)

        if info["action"] == "no_change":
            continue

        results.append(info)

        if info["action"] == "delete_adapter":
            delete_adapter_tickers.append(info["ticker"])

        if not args.dry_run:
            f.write_text(json.dumps(modified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_path = Path(args.output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=["ticker", "company_name", "action", "removed_metrics", "remaining_metrics"])
        writer.writeheader()
        for r in results:
            r_copy = dict(r)
            r_copy["removed_metrics"] = "|".join(r["removed_metrics"])
            writer.writerow(r_copy)

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}処理結果:")
    print(f"  変更対象: {len(results)}社")
    delete_count = sum(1 for r in results if r["action"] == "delete_adapter")
    remove_count = sum(1 for r in results if r["action"] == "remove_completed")
    print(f"  アダプタ削除(false化): {delete_count}社")
    print(f"  completedメトリクス削除: {remove_count}社")
    print(f"  サマリCSV: {csv_path}")

    if delete_adapter_tickers:
        print(f"\n  adapter削除対象ticker ({len(delete_adapter_tickers)}社):")
        for t in delete_adapter_tickers:
            print(f"    {t}")


if __name__ == "__main__":
    main()
