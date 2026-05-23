"""ディスク容量クリーンアップツール.

対象:
- data/tmp/: 一時ファイル全削除（.jupyter_checkpoints 含む）
- data/logs/: 古いビルドログ (*.log > N日), コピー系 (*コピー*, * - Copy*), 空ファイル
- C:\\Users\\<user>\\.claude/: キャッシュ系 (debug/ telemetry/ file-history/ shell-snapshots/
  paste-cache/ cache/) の N 日以上前のファイル、全プロジェクトの N 日超セッションログ
- claude-mem (C:\\tmp\\claude-mem): logs/ trash/ backups/ の古いファイル削除、
  SQLite の古い observations/session_summaries/user_prompts 削除+VACUUM、
  vector-db 再構築用削除（オプション）
- Git GC: 指定ディレクトリ配下の Git リポジトリで git gc --aggressive --prune=now
- C:\\tmp/ 台帳: 103_tmp_folder_registry.md の期限(YYYY-MM-DD)列が当日以前のエントリを削除

使い方:
    python scripts/cleanup_disk.py                    # dry-run（削除せずに候補表示）
    python scripts/cleanup_disk.py --execute          # 実削除
    python scripts/cleanup_disk.py --logs-days 14     # data/logs保持日数（デフォルト30）
    python scripts/cleanup_disk.py --claude-days 14   # .claude/保持日数（デフォルト30）
    python scripts/cleanup_disk.py --mem-days 90      # claude-mem DB保持日数（デフォルト90）
    python scripts/cleanup_disk.py --skip-logs        # data/logsはスキップ
    python scripts/cleanup_disk.py --skip-claude      # .claudeはスキップ
    python scripts/cleanup_disk.py --skip-mem         # claude-memはスキップ
    python scripts/cleanup_disk.py --skip-tmp         # C:\\tmp台帳チェックをスキップ
    python scripts/cleanup_disk.py --mem-vacuum       # claude-mem DB VACUUM実行
    python scripts/cleanup_disk.py --git-gc           # Git GC dry-run（--execute で実行）
    python scripts/cleanup_disk.py --git-targets C:\\path1 C:\\path2  # Git GC対象変更
    python scripts/cleanup_disk.py --skip-git         # Git GCスキップ（--git-gc時）
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_LOGS = PROJECT_ROOT / "data" / "logs"
DATA_TMP = PROJECT_ROOT / "data" / "tmp"
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_MEM_DIR = Path(os.environ.get("CLAUDE_MEM_DATA_DIR", str(Path.home() / ".claude-mem")))
TMP_REGISTRY = PROJECT_ROOT / "docs" / "knowledges" / "tools" / "103_tmp_folder_registry.md"

DEFAULT_GIT_TARGETS = [
    Path(r"C:\gdrive\claude"),
    Path.home() / ".claude",
]

GITIGNORE_RECOMMENDED = ["node_modules/", ".venv/", "__pycache__/"]

# .claude/ 配下でサイズを食いやすいキャッシュ系ディレクトリ
CLAUDE_CACHE_DIRS = [
    "debug",
    "telemetry",
    "file-history",
    "shell-snapshots",
    "paste-cache",
    "cache",
]


import re as _re
from datetime import date as _date


def parse_tmp_registry(registry_path: Path) -> list[dict]:
    """103 台帳のエントリ表をパース.

    Returns:
        list of {"path": Path, "path_str": str, "expires": date|None, "condition": str, "raw_line": str}
    """
    entries: list[dict] = []
    if not registry_path.exists():
        return entries
    in_table = False
    header_skipped = False
    for line in registry_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        if "|---" in stripped or "|:---" in stripped:
            header_skipped = True
            continue
        if not header_skipped:
            continue  # header row
        parts = [p.strip() for p in stripped.split("|")]
        parts = [p for p in parts if p != ""]
        if len(parts) < 4:
            continue
        path_str = _re.sub(r"`", "", parts[0]).strip()
        condition = parts[3].strip()
        m = _re.match(r"(\d{4}-\d{2}-\d{2})", condition)
        expires = None
        if m:
            try:
                expires = _date.fromisoformat(m.group(1))
            except ValueError:
                pass
        entries.append({
            "path_str": path_str,
            "path": Path(path_str) if path_str else None,
            "expires": expires,
            "condition": condition,
            "raw_line": line,
        })
    return entries


def scan_tmp_registry() -> list[tuple[Path, str, int]]:
    """103 台帳の期限切れエントリを列挙. (path, reason, size) のリストを返す."""
    today = datetime.now(tz=JST).date()
    candidates: list[tuple[Path, str, int]] = []
    for entry in parse_tmp_registry(TMP_REGISTRY):
        if entry["expires"] is None:
            continue
        if entry["expires"] > today:
            continue
        p = entry["path"]
        if p and p.exists():
            size = dir_size(p) if p.is_dir() else p.stat().st_size
        else:
            size = 0
        reason = f"tmp expired ({entry['expires']})"
        candidates.append((p or Path(entry["path_str"]), reason, size))
    return candidates


def remove_tmp_registry_entries(expired_paths: list[Path]) -> int:
    """103 台帳から期限切れエントリ行を削除. 削除行数を返す."""
    if not TMP_REGISTRY.exists() or not expired_paths:
        return 0
    path_strs = {str(p) for p in expired_paths}
    lines = TMP_REGISTRY.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    removed = 0
    for line in lines:
        if any(ps.replace("\\", "\\\\") in line or ps in line for ps in path_strs):
            removed += 1
        else:
            new_lines.append(line)
    if removed:
        TMP_REGISTRY.write_text("".join(new_lines), encoding="utf-8")
    return removed


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


def scan_data_tmp() -> list[tuple[Path, str, int]]:
    """data/tmp/ の一時ファイルを列挙. 全ファイルが削除対象."""
    candidates: list[tuple[Path, str, int]] = []
    if not DATA_TMP.exists():
        return candidates
    for root, dirs, files in os.walk(DATA_TMP):
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.stat()
            except OSError:
                continue
            candidates.append((fp, "data/tmp", st.st_size))
    return candidates


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
        # N日以上更新なしのファイル
        if st.st_mtime < cutoff:
            candidates.append((p, f"old (>{days}d)", size))
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
    """.claude/projects/ 配下の全プロジェクトで N 日以上前のファイルを列挙.

    アクティブなプロジェクトでもセッションログは肥大化するため、
    個別ファイル単位で mtime が cutoff を超えたものを削除候補にする。
    """
    candidates: list[tuple[Path, str, int]] = []
    pdir = CLAUDE_HOME / "projects"
    if not pdir.exists():
        return candidates
    cutoff = time.time() - days * 86400
    for root, _, files in os.walk(pdir):
        for f in files:
            fp = Path(root) / f
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                proj = fp.relative_to(pdir).parts[0] if fp.relative_to(pdir).parts else "?"
                candidates.append(
                    (fp, f".claude/projects/{proj} (>{days}d)", st.st_size)
                )
    return candidates


def scan_claude_mem_files(days: int) -> list[tuple[Path, str, int]]:
    """claude-mem の logs/ trash/ backups/ で古いファイルを列挙."""
    candidates: list[tuple[Path, str, int]] = []
    if not CLAUDE_MEM_DIR.exists():
        return candidates
    cutoff = time.time() - days * 86400

    for subdir, reason_prefix in [
        ("logs", "claude-mem log"),
        ("trash", "claude-mem trash"),
        ("backups", "claude-mem backup"),
    ]:
        d = CLAUDE_MEM_DIR / subdir
        if not d.exists():
            continue
        for root, dirs, files in os.walk(d):
            for f in files:
                fp = Path(root) / f
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if subdir == "trash":
                    candidates.append((fp, f"{reason_prefix}", st.st_size))
                elif st.st_mtime < cutoff:
                    candidates.append((fp, f"{reason_prefix} (>{days}d)", st.st_size))
            for dd in dirs:
                dp = Path(root) / dd
                try:
                    st = dp.stat()
                except OSError:
                    continue
                if subdir == "trash":
                    candidates.append((dp, f"{reason_prefix} dir", dir_size(dp)))
    return candidates


def scan_claude_mem_db(days: int) -> list[tuple[str, int]]:
    """claude-mem.db の古いレコード数を集計. (table_name, row_count) のリスト."""
    db_path = CLAUDE_MEM_DIR / "claude-mem.db"
    if not db_path.exists():
        return []
    results: list[tuple[str, int]] = []
    cutoff_iso = (datetime.now(tz=JST) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn = sqlite3.connect(str(db_path))
        for table in ("observations", "session_summaries", "user_prompts"):
            try:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE created_at < ?",  # noqa: S608
                    (cutoff_iso,),
                )
                count = cur.fetchone()[0]
                if count > 0:
                    results.append((table, count))
            except sqlite3.OperationalError:
                pass
        conn.close()
    except sqlite3.Error:
        pass
    return results


def delete_claude_mem_old_rows(days: int) -> int:
    """claude-mem.db から古いレコードを削除. 削除行数を返す."""
    db_path = CLAUDE_MEM_DIR / "claude-mem.db"
    if not db_path.exists():
        return 0
    cutoff_iso = (datetime.now(tz=JST) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    total = 0
    try:
        conn = sqlite3.connect(str(db_path))
        for table in ("observations", "session_summaries", "user_prompts"):
            try:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE created_at < ?",  # noqa: S608
                    (cutoff_iso,),
                )
                total += cur.rowcount
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    return total


def vacuum_claude_mem_db() -> int:
    """claude-mem.db を VACUUM して WAL チェックポイント. 解放バイト数の概算を返す."""
    db_path = CLAUDE_MEM_DIR / "claude-mem.db"
    if not db_path.exists():
        return 0
    before = db_path.stat().st_size
    wal = db_path.with_suffix(".db-wal")
    if wal.exists():
        before += wal.stat().st_size
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.close()
    except sqlite3.Error:
        return 0
    after = db_path.stat().st_size
    if wal.exists():
        after += wal.stat().st_size
    return max(0, before - after)


def find_git_repos(targets: list[Path]) -> list[Path]:
    """対象ディレクトリ直下（1階層）の Git リポジトリを列挙."""
    repos: set[Path] = set()
    for target in targets:
        if not target.exists():
            continue
        resolved = target.resolve()
        if (resolved / ".git").exists():
            repos.add(resolved)
        if not resolved.is_dir():
            continue
        for entry in resolved.iterdir():
            if entry.is_dir() and (entry / ".git").exists():
                repos.add(entry.resolve())
    return sorted(repos)


def check_gitignore(repo: Path) -> list[str]:
    """リポジトリの .gitignore に推奨パターンが含まれるか確認. 不足パターンを返す."""
    gitignore = repo / ".gitignore"
    if not gitignore.exists():
        return GITIGNORE_RECOMMENDED[:]
    try:
        content = gitignore.read_text(encoding="utf-8")
    except OSError:
        return GITIGNORE_RECOMMENDED[:]
    lines = {line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")}
    missing: list[str] = []
    for pattern in GITIGNORE_RECOMMENDED:
        bare = pattern.rstrip("/")
        found = any(bare in line.replace("**/", "").rstrip("/") for line in lines)
        if not found:
            missing.append(pattern)
    return missing


def run_git_gc(repos: list[Path], *, execute: bool) -> None:
    """各リポジトリで git gc を実行. dry-run 時はサイズのみ表示."""
    if not repos:
        print("[git gc] no repos found")
        return

    print(f"[git gc] {len(repos)} repos found")
    total_before = 0
    total_after = 0

    for repo in repos:
        git_dir = repo / ".git"
        before = dir_size(git_dir)
        total_before += before
        print(f"\n  {repo}")
        print(f"    .git before: {human_size(before)}")

        missing = check_gitignore(repo)
        if missing:
            print(f"    [warn] .gitignore missing: {', '.join(missing)}")

        if execute:
            lock_file = git_dir / "index.lock"
            gc_pid = git_dir / "gc.pid"
            if lock_file.exists() or gc_pid.exists():
                blocker = "index.lock" if lock_file.exists() else "gc.pid"
                print(f"    [skip] {blocker} exists — another git process is running")
                total_after += before
                continue
            try:
                result = subprocess.run(
                    ["git", "gc", "--aggressive", "--prune=now"],
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    print(f"    [error] git gc failed: {result.stderr.strip()}")
                    total_after += dir_size(git_dir)
                    continue
            except subprocess.TimeoutExpired:
                print("    [error] git gc timed out (5min)")
                total_after += dir_size(git_dir)
                continue
            except FileNotFoundError:
                print("    [error] git not found in PATH")
                return
            after = dir_size(git_dir)
            total_after += after
            freed = before - after
            print(f"    .git after:  {human_size(after)} (freed {human_size(max(0, freed))})")
        else:
            total_after += before
            print("    (dry-run: --execute で実行)")

    print(f"\n  git gc total: {human_size(total_before)}", end="")
    if execute:
        freed = total_before - total_after
        print(f" -> {human_size(total_after)} (freed {human_size(max(0, freed))})")
    else:
        print()


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
    parser.add_argument("--mem-days", type=int, default=90, help="claude-mem DB保持日数")
    parser.add_argument("--skip-logs", action="store_true", help="data/logsはスキップ")
    parser.add_argument("--skip-claude", action="store_true", help=".claudeはスキップ")
    parser.add_argument("--skip-mem", action="store_true", help="claude-memはスキップ")
    parser.add_argument("--mem-vacuum", action="store_true", help="claude-mem DB VACUUM実行")
    parser.add_argument("--git-gc", action="store_true", help="Git GC実行")
    parser.add_argument("--git-targets", nargs="+", type=Path, default=None,
                        help="Git GC対象ディレクトリ（デフォルト: C:\\gdrive\\claude, ~/.claude）")
    parser.add_argument("--skip-git", action="store_true", help="Git GCスキップ")
    parser.add_argument("--skip-tmp", action="store_true", help="C:\\tmp台帳チェックをスキップ")
    args = parser.parse_args()

    now_jst = datetime.now(tz=JST).strftime("%Y-%m-%d %H:%M:%S JST")
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"=== cleanup_disk.py [{mode}] {now_jst} ===\n")

    sections: list[tuple[str, list[tuple[Path, str, int]]]] = []
    sections.append(("data/tmp/", scan_data_tmp()))
    if not args.skip_logs:
        sections.append(("data/logs/", scan_data_logs(args.logs_days)))
    if not args.skip_claude:
        sections.append((".claude/ caches", scan_claude_caches(args.claude_days)))
        sections.append((".claude/projects stale", scan_claude_stale_projects(args.claude_days)))
    if not args.skip_mem:
        sections.append(("claude-mem files", scan_claude_mem_files(args.mem_days)))
    if not args.skip_tmp:
        sections.append(("C:\\tmp registry expired", scan_tmp_registry()))

    mem_db_rows: list[tuple[str, int]] = []
    if not args.skip_mem:
        mem_db_rows = scan_claude_mem_db(args.mem_days)

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

    if mem_db_rows:
        print(f"[claude-mem DB] old rows (>{args.mem_days}d):")
        for table, count in mem_db_rows:
            print(f"  {table}: {count} rows")
        print()

    print(f"--- total: {total_count} files, {human_size(total_size)} ---")

    if args.execute and (total_count > 0 or mem_db_rows):
        print("\n[executing deletion...]")
        freed = 0
        tmp_expired_paths: list[Path] = []
        for title, items in sections:
            is_tmp = title == "C:\\tmp registry expired"
            for p, _reason, _size in items:
                freed += delete_path(p)
                if is_tmp:
                    tmp_expired_paths.append(p)
        if tmp_expired_paths:
            removed = remove_tmp_registry_entries(tmp_expired_paths)
            print(f"103 台帳エントリ削除: {removed} 行")
        if mem_db_rows:
            deleted_rows = delete_claude_mem_old_rows(args.mem_days)
            print(f"claude-mem DB: {deleted_rows} rows deleted")
        if args.mem_vacuum:
            vac = vacuum_claude_mem_db()
            print(f"claude-mem DB VACUUM: {human_size(vac)} freed")
        print(f"files freed: {human_size(freed)}")
    elif not args.execute:
        print("\ndry-run: 実削除するには --execute を付けて再実行してください。")

    if args.mem_vacuum and not args.execute:
        print("(--mem-vacuum は --execute と併用してください)")

    if args.git_gc and not args.skip_git:
        print()
        git_targets = args.git_targets or DEFAULT_GIT_TARGETS
        repos = find_git_repos(git_targets)
        run_git_gc(repos, execute=args.execute)

    return 0


if __name__ == "__main__":
    sys.exit(main())
