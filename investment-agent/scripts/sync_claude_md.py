"""Import Markdown updates from the Claude Code workspace into Codex.

This tool is intentionally one-sided:
  - Claude Code workspace is read-only input.
  - Codex workspace owns the sync manifest.
  - Only safe three-way Markdown imports are applied automatically.

Typical usage:
    python scripts/sync_claude_md.py --init-baseline
    python scripts/sync_claude_md.py
    python scripts/sync_claude_md.py --apply
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
DEFAULT_SOURCE = Path(r"C:\gdrive\claude\investment-agent")
DEFAULT_TARGETS = ("CLAUDE.md", "data_catalog.md", "docs", "skills")
DEFAULT_MANIFEST = Path("data/logs/claude_md_sync_manifest.json")
DEFAULT_EXCLUDES = (
    "docs/claude-code-intake-checklist.md",
    "docs/claude-md-sync.md",
    "docs/codex-*.md",
    "docs/git-bootstrap-notes.md",
    "docs/plans/*codex*.md",
)


@dataclass(frozen=True)
class FileInfo:
    sha256: str
    size: int


@dataclass(frozen=True)
class Row:
    status: str
    path: str
    action: str


def sha256_file(path: Path) -> FileInfo:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return FileInfo(sha256=h.hexdigest(), size=size)


def rel_key(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_excluded(path: str, excludes: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes)


def collect_markdown(
    root: Path, targets: tuple[str, ...], excludes: tuple[str, ...]
) -> dict[str, FileInfo]:
    files: dict[str, FileInfo] = {}
    for target in targets:
        p = root / target
        if not p.exists():
            continue
        if p.is_file():
            key = rel_key(p, root)
            if p.suffix.lower() == ".md" and not is_excluded(key, excludes):
                files[key] = sha256_file(p)
            continue
        for md in p.rglob("*.md"):
            if md.is_file():
                key = rel_key(md, root)
                if not is_excluded(key, excludes):
                    files[key] = sha256_file(md)
    return files


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "files": {}}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_manifest(path: Path, source_root: Path, files: dict[str, FileInfo]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "source_root": str(source_root),
        "updated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "files": {
            k: {"sha256": v.sha256, "size": v.size}
            for k, v in sorted(files.items())
        },
    }
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def classify(
    source: dict[str, FileInfo],
    dest: dict[str, FileInfo],
    base: dict[str, dict],
) -> list[Row]:
    rows: list[Row] = []
    paths = sorted(set(source) | set(dest) | set(base))
    for path in paths:
        src = source.get(path)
        dst = dest.get(path)
        base_entry = base.get(path)
        base_sha = base_entry.get("sha256") if isinstance(base_entry, dict) else None
        src_sha = src.sha256 if src else None
        dst_sha = dst.sha256 if dst else None

        if base_sha is None:
            if src_sha and not dst_sha:
                rows.append(Row("SAFE_IMPORT", path, "new from Claude"))
            elif src_sha and dst_sha and src_sha == dst_sha:
                rows.append(Row("BASELINE_MISSING_SAME", path, "record baseline"))
            elif src_sha and dst_sha:
                rows.append(Row("CONFLICT", path, "exists on both without baseline"))
            elif dst_sha and not src_sha:
                rows.append(Row("CODEX_ONLY", path, "not present in Claude"))
            continue

        if src_sha is None:
            if dst_sha == base_sha:
                rows.append(Row("CLAUDE_DELETED", path, "delete only with --delete"))
            elif dst_sha:
                rows.append(Row("CONFLICT", path, "Claude deleted, Codex changed"))
            else:
                rows.append(Row("BASELINE_STALE", path, "remove from baseline"))
            continue

        if dst_sha is None:
            if src_sha == base_sha:
                rows.append(Row("CODEX_DELETED", path, "Codex deleted"))
            else:
                rows.append(Row("CONFLICT", path, "Claude changed, Codex deleted"))
            continue

        if src_sha == base_sha and dst_sha == base_sha:
            rows.append(Row("UNCHANGED", path, "none"))
        elif src_sha != base_sha and dst_sha == base_sha:
            rows.append(Row("SAFE_IMPORT", path, "update from Claude"))
        elif src_sha == base_sha and dst_sha != base_sha:
            rows.append(Row("CODEX_ONLY", path, "Codex changed"))
        elif src_sha == dst_sha:
            rows.append(Row("BASELINE_MISSING_SAME", path, "record current shared content"))
        else:
            rows.append(Row("CONFLICT", path, "both changed"))
    return rows


def print_rows(rows: list[Row], verbose: bool) -> None:
    visible = rows if verbose else [r for r in rows if r.status != "UNCHANGED"]
    if not visible:
        print("No Markdown differences detected.")
        return

    width = max(len(r.status) for r in visible)
    for r in visible:
        print(f"{r.status:<{width}}  {r.path}  ({r.action})")


def apply_safe_rows(
    rows: list[Row],
    source_root: Path,
    dest_root: Path,
    source_files: dict[str, FileInfo],
    manifest_files: dict[str, FileInfo],
    include_deletes: bool,
) -> tuple[int, dict[str, FileInfo]]:
    applied = 0
    next_manifest = dict(manifest_files)

    for row in rows:
        src = source_root / Path(row.path)
        dst = dest_root / Path(row.path)

        if row.status == "SAFE_IMPORT":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            next_manifest[row.path] = source_files[row.path]
            applied += 1
        elif row.status == "BASELINE_MISSING_SAME":
            if row.path in source_files:
                next_manifest[row.path] = source_files[row.path]
                applied += 1
        elif row.status == "BASELINE_STALE":
            next_manifest.pop(row.path, None)
            applied += 1
        elif row.status == "CLAUDE_DELETED" and include_deletes:
            if dst.exists():
                dst.unlink()
            next_manifest.pop(row.path, None)
            applied += 1

    return applied, next_manifest


def init_baseline(
    source_root: Path,
    dest_root: Path,
    manifest_path: Path,
    source_files: dict[str, FileInfo],
    dest_files: dict[str, FileInfo],
) -> int:
    baseline: dict[str, FileInfo] = {}
    skipped: list[str] = []
    for path, src in sorted(source_files.items()):
        dst = dest_files.get(path)
        if dst and dst.sha256 == src.sha256:
            baseline[path] = src
        else:
            skipped.append(path)

    write_manifest(manifest_path, source_root, baseline)
    print(f"Initialized baseline: {len(baseline)} files")
    if skipped:
        print(f"Skipped {len(skipped)} files because Codex does not match Claude:")
        for path in skipped[:50]:
            print(f"  {path}")
        if len(skipped) > 50:
            print(f"  ... {len(skipped) - 50} more")
        return 2
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and safely import Claude Code Markdown updates into Codex."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--exclude", action="append", dest="excludes")
    parser.add_argument("--init-baseline", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete", action="store_true", help="Apply Claude-side deletions too.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source.resolve()
    dest_root = args.dest.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = dest_root / manifest_path

    if not source_root.exists():
        print(f"Source workspace not found: {source_root}")
        return 2
    if not dest_root.exists():
        print(f"Destination workspace not found: {dest_root}")
        return 2

    targets = tuple(args.targets) if args.targets else DEFAULT_TARGETS
    excludes = tuple(args.excludes) if args.excludes else DEFAULT_EXCLUDES
    source_files = collect_markdown(source_root, targets, excludes)
    dest_files = collect_markdown(dest_root, targets, excludes)

    if args.init_baseline:
        return init_baseline(source_root, dest_root, manifest_path, source_files, dest_files)

    manifest = load_manifest(manifest_path)
    base_files = manifest.get("files", {})
    if not base_files:
        print("Baseline manifest is missing or empty. Run with --init-baseline first.")
        return 2

    rows = classify(source_files, dest_files, base_files)
    print_rows(rows, args.verbose)

    blockers = [r for r in rows if r.status == "CONFLICT"]
    if args.apply:
        manifest_files = {
            k: FileInfo(sha256=v["sha256"], size=int(v.get("size", 0)))
            for k, v in base_files.items()
            if isinstance(v, dict) and "sha256" in v
        }
        applied, next_manifest = apply_safe_rows(
            rows,
            source_root,
            dest_root,
            source_files,
            manifest_files,
            include_deletes=args.delete,
        )
        write_manifest(manifest_path, source_root, next_manifest)
        print(f"Applied safe changes: {applied}")

    if blockers:
        print(f"Conflicts require manual review: {len(blockers)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
