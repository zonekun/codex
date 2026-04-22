"""ディスク容量クリーンアップツール.

対象:
- data/logs/: 古いビルドログ (*.log > N日), コピー系 (*コピー*, * - Copy*), 空ファイル
- C:\\Users\\<user>\\.claude/: キャッシュ系 (debug/ telemetry/ file-history/ shell-snapshots/
  paste-cache/ cache/) の N 日以上前のファイル、存在しないプロジェクトパスの
  セッションログ

使い方:
    python scripts/cleanup_disk.py                    # dry-run（削除せずに候補表示）
    python scripts/cleanup_disk.py --execute          # 実削除
    python scripts/cleanup_disk.py --logs-days 14     # data/logs保持日数（デフォルト30）
    python scripts/cleanup_disk.py --claude-days 14   # .claude/保持日数（デフォルト30）
    python scripts/cleanup_disk.py --skip-claude      # .claude/はスキップ
    python scripts/cleanup_disk.py --skip-logs        # data/logs/はスキップ
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_LOGS = PROJECT_ROOT / "data" / "logs"
CLAUDE_HOME = Path.home() / ".claude"

# .claude/ 配下でサイズを食いやすいキャッシュ系ディレクトリ
CLAUDE_CACHE_DIRS = [
    "debug",
    "telemetry",
    "file-history",
    "shell-snapshots",
    "paste-cache",
    "cache",
]


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def is_copy_file(name: str) -> bool:
    low = name.lower()
    return (
        "コピー" in name
        or " - copy" in low
        or ".tmp" == Path(name).suffix.lower()
    )


def scan_data_logs(days: int) -> list[tuple[Path, str, int]]:
    """data/logs/ のクリーン候補を列挙. (path, reason, size) のリストを返す."""
    candidates: list[tuple[Path, str, int]] = []
    if not DATA_LOGS.exists():
        return candidates
    cutoff = time.time() - days * 86400
    for p in DATA_LOGS.iterdir():
        if not p.is_file():
            continue
        # active_jobs.md は除外
        if p.name == "active_jobs.md":
            continue
        st = p.stat()
        size = st.st_size
        # 空ファイル
        if size == 0:
            candidates.append((p, "empty", 0))
            continue
        # コピー系
        if is_copy_file(p.name):
            candidates.append((p, "copy/tmp", size))
            continue
        # 古いビルドログ
        if p.suffix == ".log" and p.name.startswith("build_") and st.st_mtime < cutoff:
            candidates.append((p, f"old build log (>{days}d)", size))
    return candidates


def dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def scan_claude_caches(days: int) -> list[tuple[Path, str, int]]:
    """.claude/ のキャッシュ系で N 日以上更新なしのファイルを列挙."""
    candidates: list[tuple[Path, str, int]] = []
    if not CLAUDE_HOME.exists():
        return candidates
    cutoff = time.time() - days * 86400
    for sub in CLAUDE_CACHE_DIRS:
        d = CLAUDE_HOME / sub
        if not d.exists():
            continue
        for root, _, files in os.walk(d):
            for f in files:
                fp = Path(root) / f
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    candidates.append((fp, f".claude/{sub} (>{days}d)", st.st_size))
    return candidates


def scan_claude_stale_projects(days: int) -> list[tuple[Path, str, int]]:
    """.claude/projects/ で、直近 N 日更新がないプロジェクトを列挙.

    ディレクトリ名→実パスの復元は Windows 日本語パス（`マイドライブ`→`---------`）で
    不可逆になるため、パス復元ではなく mtime で判定する。
    プロジェクト内の最も新しい .jsonl の mtime が cutoff 未満なら「使われていない」とみなす。
    """
    candidates: list[tuple[Path, str, int]] = []
    pdir = CLAUDE_HOME / "projects"
    if not pdir.exists():
        return candidates
    cutoff = time.time() - days * 86400
    for entry in pdir.iterdir():
        if not entry.is_dir():
            continue
        # プロジェクト内の最新mtimeを取得
        latest = 0.0
        for root, _, files in os.walk(entry):
            for f in files:
                try:
                    m = (Path(root) / f).stat().st_mtime
                    if m > latest:
                        latest = m
                except OSError:
                    pass
        if latest > 0 and latest < cutoff:
            last_jst = datetime.fromtimestamp(latest, tz=JST).strftime("%Y-%m-%d")
            candidates.append(
                (entry, f".claude/projects stale (last:{last_jst})", dir_size(entry))
            )
    return candidates


def delete_path(p: Path) -> int:
    """ファイル/ディレクトリを削除. 削除できたバイト数を返す."""
    try:
        if p.is_dir():
            size = dir_size(p)
            shutil.rmtree(p)
            return size
        else:
            size = p.stat().st_size
            p.unlink()
            return size
    except OSError as e:
        print(f"  [warn] delete failed: {p} ({e})", file=sys.stderr)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Disk cleanup for project and .claude caches")
    parser.add_argument("--execute", action="store_true", help="実削除する（デフォルトdry-run）")
    parser.add_argument("--logs-days", type=int, default=30, help="data/logs 保持日数")
    parser.add_argument("--claude-days", type=int, default=30, help=".claude キャッシュ保持日数")
    parser.add_argument("--skip-logs", action="store_true", help="data/logsはスキップ")
    parser.add_argument("--skip-claude", action="store_true", help=".claudeはスキップ")
    args = parser.parse_args()

    now_jst = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S JST")
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"=== cleanup_disk.py [{mode}] {now_jst} ===\n")

    sections: list[tuple[str, list[tuple[Path, str, int]]]] = []
    if not args.skip_logs:
        sections.append(("data/logs/", scan_data_logs(args.logs_days)))
    if not args.skip_claude:
        sections.append((".claude/ caches", scan_claude_caches(args.claude_days)))
        sections.append((".claude/projects stale", scan_claude_stale_projects(args.claude_days)))

    total_size = 0
    total_count = 0
    for title, items in sections:
        if not items:
            print(f"[{title}] no candidates")
            continue
        sec_size = sum(s for _, _, s in items)
        print(f"[{title}] {len(items)} files, {human_size(sec_size)}")
        # 上位10件プレビュー
        items_sorted = sorted(items, key=lambda x: -x[2])
        for p, reason, size in items_sorted[:10]:
            print(f"  {human_size(size):>10}  {reason:<30}  {p}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")
        total_size += sec_size
        total_count += len(items)
        print()

    print(f"--- total: {total_count} files, {human_size(total_size)} ---")

    if args.execute and total_count > 0:
        print("\n[executing deletion...]")
        freed = 0
        for _title, items in sections:
            for p, _reason, _size in items:
                freed += delete_path(p)
        print(f"freed: {human_size(freed)}")
    elif not args.execute:
        print("\ndry-run: 実削除するには --execute を付けて再実行してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
