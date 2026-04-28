"""Keep the local Codex workspace under a target size.

This script is Codex-side only. It lives outside the Claude Code project clone so
Claude-side syncs do not overwrite it.

Usage:
    python cleanup_codex_workspace.py
    python cleanup_codex_workspace.py --execute
    python cleanup_codex_workspace.py --target-gb 1.0 --execute
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
WORKSPACE_ROOT = Path(__file__).resolve().parent
DEFAULT_TARGET_GB = 1.0


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    size: int
    priority: int


GENERATED_DIRS = (
    "investment-agent/.uv-cache",
    "investment-agent/.ruff_cache",
    "investment-agent/.mypy_cache",
    "investment-agent/.pytest_cache",
    "investment-agent/htmlcov",
    "investment-agent/build",
    "investment-agent/dist",
)

PROTECTED_PATHS = (
    "investment-agent/.env",
    "investment-agent/.claude",
    "investment-agent/.mcp.json",
    "investment-agent/.venv",
    "investment-agent/.venv-codex",
    "investment-agent/venv",
    "investment-agent/env",
    "investment-agent/keys",
    "investment-agent/data/logs",
    "investment-agent/data/master",
)

PROTECTED_NAMES = (
    ".env",
    ".claude",
    ".mcp.json",
    ".venv",
    ".venv-codex",
    "venv",
    "env",
    "keys",
)

TMP_PREFIXES = ("tmp_", "tmp-")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def dir_size(path: Path) -> int:
    total = 0
    if path.is_file():
        return path.stat().st_size
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_active_python_path(path: Path) -> bool:
    active_paths = (Path(sys.prefix), Path(sys.base_prefix), Path(sys.executable).parent)
    return any(is_relative_to(active, path) or active == path for active in active_paths)


def protected_paths() -> tuple[Path, ...]:
    return tuple((WORKSPACE_ROOT / rel).resolve() for rel in PROTECTED_PATHS)


def is_protected_path(path: Path) -> bool:
    resolved = path.resolve()
    if path.name in PROTECTED_NAMES:
        return True
    for protected in protected_paths():
        if resolved == protected or is_relative_to(resolved, protected):
            return True
    return False


def is_inside_workspace(path: Path) -> bool:
    resolved = path.resolve()
    root = WORKSPACE_ROOT.resolve()
    return resolved == root or is_relative_to(resolved, root)


def is_git_tracked_or_unignored(path: Path) -> bool:
    rel = path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel],
        cwd=WORKSPACE_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if tracked.returncode == 0:
        return True

    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", rel],
        cwd=WORKSPACE_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return ignored.returncode != 0


def add_candidate(items: list[Candidate], rel: str, reason: str, priority: int) -> None:
    path = WORKSPACE_ROOT / rel
    if (
        not path.exists()
        or is_active_python_path(path)
        or is_protected_path(path)
        or not is_inside_workspace(path)
    ):
        return
    size = dir_size(path)
    if size > 0:
        items.append(Candidate(path=path, reason=reason, size=size, priority=priority))


def find_pycache_dirs() -> list[Path]:
    project = WORKSPACE_ROOT / "investment-agent"
    if not project.exists():
        return []
    results: list[Path] = []
    ignored_parts = {".git", ".uv-cache", ".venv-codex", ".venv", "venv", "env"}
    for root, dirs, _files in os.walk(project):
        root_path = Path(root)
        if any(part in ignored_parts for part in root_path.parts):
            dirs[:] = []
            continue
        for dirname in list(dirs):
            if dirname == "__pycache__":
                results.append(root_path / dirname)
    return results


def scan_candidates(target_bytes: int) -> list[Candidate]:
    current = dir_size(WORKSPACE_ROOT)
    if current <= target_bytes:
        return []

    candidates: list[Candidate] = []
    for rel in GENERATED_DIRS:
        add_candidate(candidates, rel, f"generated dir: {rel}", 10)

    project = WORKSPACE_ROOT / "investment-agent"
    if project.exists():
        for path in project.iterdir():
            if path.name.startswith(TMP_PREFIXES):
                rel = path.relative_to(WORKSPACE_ROOT).as_posix()
                if is_git_tracked_or_unignored(path):
                    continue
                add_candidate(candidates, rel, "temporary project root item", 20)

    for path in find_pycache_dirs():
        rel = path.relative_to(WORKSPACE_ROOT).as_posix()
        add_candidate(candidates, rel, "python __pycache__", 30)

    selected: list[Candidate] = []
    projected = current
    for item in sorted(candidates, key=lambda c: (c.priority, -c.size, str(c.path))):
        if projected <= target_bytes:
            break
        selected.append(item)
        projected -= item.size
    return selected


def make_writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def is_reparse_point(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attrs = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def assert_safe_tree(path: Path) -> None:
    if not is_inside_workspace(path):
        raise RuntimeError(f"Refusing to delete outside workspace: {path}")
    if is_protected_path(path):
        raise RuntimeError(f"Refusing to delete protected path: {path}")
    if path.is_symlink() or is_reparse_point(path):
        raise RuntimeError(f"Refusing to delete symlink/reparse candidate: {path}")

    if not path.is_dir():
        return

    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        if root_path.is_symlink() or is_reparse_point(root_path) or not is_inside_workspace(root_path):
            raise RuntimeError(f"Refusing unsafe cleanup tree root: {root_path}")
        if is_protected_path(root_path):
            raise RuntimeError(f"Refusing cleanup tree containing protected path: {root_path}")

        for name in dirs + files:
            child = root_path / name
            if child.is_symlink() or is_reparse_point(child):
                raise RuntimeError(f"Refusing cleanup tree containing symlink/reparse path: {child}")
            if not is_inside_workspace(child):
                raise RuntimeError(f"Refusing cleanup tree containing outside path: {child}")
            if is_protected_path(child):
                raise RuntimeError(f"Refusing cleanup tree containing protected path: {child}")


def remove_path(path: Path) -> int:
    assert_safe_tree(path)
    size = dir_size(path)
    if path.is_dir():
        shutil.rmtree(path, onerror=lambda _func, p, _exc: make_writable(Path(p)))
    else:
        make_writable(path)
        path.unlink()
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean local Codex workspace generated files.")
    parser.add_argument("--target-gb", type=float, default=DEFAULT_TARGET_GB)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    target_bytes = int(args.target_gb * 1024 * 1024 * 1024)
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    before = dir_size(WORKSPACE_ROOT)

    print(f"=== cleanup_codex_workspace.py [{mode}] {now} ===")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print(f"Current:   {human_size(before)}")
    print(f"Target:    {args.target_gb:.2f}GB")
    print()

    candidates = scan_candidates(target_bytes)
    if not candidates:
        print("No candidates. Workspace is already within target.")
        return 0

    total = sum(item.size for item in candidates)
    print(f"Candidates: {len(candidates)} items, {human_size(total)}")
    for item in candidates:
        print(f"  {human_size(item.size):>10}  {item.reason:<45}  {item.path}")

    if not args.execute:
        print("\ndry-run: add --execute to delete these paths.")
        return 0

    print("\n[executing deletion...]")
    freed = 0
    for item in candidates:
        try:
            freed += remove_path(item.path)
        except OSError as exc:
            print(f"  [warn] delete failed: {item.path} ({exc})", file=sys.stderr)

    time.sleep(0.1)
    after = dir_size(WORKSPACE_ROOT)
    print(f"Freed:     {human_size(freed)}")
    print(f"After:     {human_size(after)}")
    if after > target_bytes:
        print("Warning: workspace is still over target. A running process may be locking files.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
