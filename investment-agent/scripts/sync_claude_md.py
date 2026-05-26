"""Sync Markdown updates from the Claude Code workspace into Codex.

This tool supports two modes:
  - safe: three-way sync using a manifest baseline
  - mirror: file-level mirror from Claude into Codex, then refresh manifest

Typical usage:
    python scripts/sync_claude_md.py --init-baseline
    python scripts/sync_claude_md.py --mode safe
    python scripts/sync_claude_md.py --mode safe --apply --delete
    python scripts/sync_claude_md.py --mode mirror
    python scripts/sync_claude_md.py --mode mirror --apply
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
DEFAULT_SOURCE = Path(r"C:\gdrive\claude\investment-agent")
DEFAULT_TARGETS = ("CLAUDE.md", "data_catalog.md", "docs", "skills")
DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "codex_state"
    / "investment-agent"
    / "claude_md_sync_manifest.json"
)
DEFAULT_EXCLUDES = (
    "docs/claude-code-intake-checklist.md",
    "docs/claude-md-sync.md",
    "docs/codex-to-claude-handoff.md",
    "docs/codex/**",
    "docs/git-bootstrap-notes.md",
    "docs/plans/*codex*.md",
)
CODEX_ARTIFACT_PATTERNS = ("*codex*", "*_codex_*")
CODEX_PROTECTED_PATHS = (
    "docs/claude-code-handoff-template.md",
    "docs/reviews/001_sync_claude_md_mirror_gap.md",
)
MANIFEST_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class FileInfo:
    sha256: str
    size: int


@dataclass(frozen=True)
class Row:
    status: str
    path: str
    action: str


@dataclass(frozen=True)
class MirrorApplyResult:
    applied: int
    blocked_deletes: int


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


def is_codex_artifact(path: str) -> bool:
    if path in CODEX_PROTECTED_PATHS:
        return True
    if fnmatch.fnmatchcase(path, "docs/codex/**"):
        return True
    filename = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(filename, pattern) for pattern in CODEX_ARTIFACT_PATTERNS)


def git_tracking_state(dest_root: Path, rel_path: str) -> str:
    git_exe = shutil.which("git")
    if not git_exe:
        return "unknown"
    try:
        result = subprocess.run(
            [
                git_exe,
                "-C",
                str(dest_root),
                "ls-files",
                "--error-unmatch",
                "--",
                rel_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if result.returncode == 0:
        return "tracked"
    if result.returncode == 1:
        return "untracked"
    return "unknown"


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


def parse_manifest_files(base_files: dict[str, dict]) -> dict[str, FileInfo]:
    return {
        k: FileInfo(sha256=v["sha256"], size=int(v.get("size", 0)))
        for k, v in base_files.items()
        if isinstance(v, dict) and "sha256" in v
    }


def validate_manifest(
    manifest: dict,
    source_root: Path,
    allow_stale: bool,
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    source_root_text = manifest.get("source_root")
    if source_root_text and Path(source_root_text) != source_root:
        issues.append(
            f"Manifest source_root mismatch: manifest={source_root_text} current={source_root}"
        )

    updated_at_text = manifest.get("updated_at_jst")
    if updated_at_text:
        try:
            updated_at = datetime.fromisoformat(updated_at_text)
            age = datetime.now(JST) - updated_at.astimezone(JST)
            if age > timedelta(days=MANIFEST_MAX_AGE_DAYS):
                issues.append(
                    f"Manifest is stale: age={age.days}d > {MANIFEST_MAX_AGE_DAYS}d "
                    f"(updated_at_jst={updated_at_text})"
                )
        except ValueError:
            issues.append(f"Manifest updated_at_jst is invalid: {updated_at_text}")

    if issues and not allow_stale:
        return False, issues
    return True, issues


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


def plan_mirror(
    source: dict[str, FileInfo],
    dest: dict[str, FileInfo],
    dest_root: Path | None = None,
) -> list[Row]:
    rows: list[Row] = []
    paths = sorted(set(source) | set(dest))
    for path in paths:
        src = source.get(path)
        dst = dest.get(path)
        if src and not dst:
            rows.append(Row("ADD", path, "new from Claude"))
        elif dst and not src:
            if is_codex_artifact(path):
                rows.append(Row("CODEX_PROTECTED", path, "Codex artifact, skipping delete"))
            else:
                action = "remove destination-only file"
                if dest_root is not None:
                    action = f"{action}; git={git_tracking_state(dest_root, path)}"
                rows.append(Row("DELETE", path, action))
        elif src and dst and src.sha256 != dst.sha256:
            rows.append(Row("MODIFY", path, "overwrite from Claude"))
    return rows


def summarize_rows(rows: list[Row], ordered_statuses: tuple[str, ...]) -> None:
    counts = {status: 0 for status in ordered_statuses}
    for row in rows:
        if row.status in counts:
            counts[row.status] += 1
    print("Summary:")
    for status in ordered_statuses:
        print(f"  {status:<20} {counts[status]}")


def print_rows(rows: list[Row], verbose: bool, hide_statuses: set[str] | None = None) -> None:
    hide_statuses = hide_statuses or set()
    visible = rows if verbose else [r for r in rows if r.status not in hide_statuses]
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


def apply_mirror_rows(
    rows: list[Row],
    source_root: Path,
    dest_root: Path,
    force_delete_untracked: bool = False,
) -> MirrorApplyResult:
    blocked = 0
    if not force_delete_untracked:
        for row in rows:
            if row.status != "DELETE":
                continue
            state = git_tracking_state(dest_root, row.path)
            if state != "tracked":
                print(
                    f"Blocked DELETE for {row.path}: git={state}. "
                    "Use --force-delete-untracked to allow this destructive delete."
                )
                blocked += 1
        if blocked:
            return MirrorApplyResult(applied=0, blocked_deletes=blocked)

    applied = 0
    dest_root_resolved = dest_root.resolve()
    for row in rows:
        src = source_root / Path(row.path)
        dst = dest_root / Path(row.path)
        if row.status in {"ADD", "MODIFY"}:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            applied += 1
        elif row.status == "DELETE":
            if dst.exists():
                resolved = dst.resolve()
                if dest_root_resolved not in resolved.parents and resolved != dest_root_resolved:
                    raise RuntimeError(f"Refusing to delete outside destination: {resolved}")
                dst.unlink()
                applied += 1
    return MirrorApplyResult(applied=applied, blocked_deletes=0)


def init_baseline(
    source_root: Path,
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
        description="Sync Claude Code Markdown updates into Codex."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--exclude", action="append", dest="excludes")
    parser.add_argument("--mode", choices=("safe", "mirror"), default="safe")
    parser.add_argument("--init-baseline", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete", action="store_true", help="safe mode only: apply Claude-side deletions")
    parser.add_argument(
        "--force-delete-untracked",
        action="store_true",
        help="mirror mode only: allow deleting untracked or git-unknown destination-only files",
    )
    parser.add_argument("--allow-stale-manifest", action="store_true")
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

    print(f"Mode: {args.mode}")
    print(f"Source: {source_root}")
    print(f"Dest:   {dest_root}")
    print(f"Targets: {', '.join(targets)}")
    print(f"Excludes: {', '.join(excludes)}")
    print()

    if args.init_baseline:
        return init_baseline(source_root, manifest_path, source_files, dest_files)

    if args.mode == "mirror":
        rows = plan_mirror(source_files, dest_files, dest_root=dest_root)
        summarize_rows(rows, ("ADD", "MODIFY", "DELETE", "CODEX_PROTECTED"))
        print_rows(rows, args.verbose)
        print("\nMirror mode note: on --apply, files are mirrored and the manifest is reset to the post-sync shared state.")
        if args.delete:
            print("Warning: --delete is ignored in mirror mode because delete is part of mirror semantics.")

        if args.apply:
            result = apply_mirror_rows(
                rows,
                source_root,
                dest_root,
                force_delete_untracked=args.force_delete_untracked,
            )
            if result.blocked_deletes:
                print(
                    f"\nBlocked mirror changes: {result.blocked_deletes} destructive deletes. "
                    "Manifest was not refreshed."
                )
                return 1
            write_manifest(manifest_path, source_root, source_files)
            print(f"\nApplied mirror changes: {result.applied}")
            print(f"Manifest refreshed: {manifest_path}")
        return 0

    manifest = load_manifest(manifest_path)
    base_files = manifest.get("files", {})
    if not base_files:
        print("Baseline manifest is missing or empty. Run with --init-baseline first.")
        return 2

    is_valid, manifest_issues = validate_manifest(
        manifest,
        source_root,
        allow_stale=args.allow_stale_manifest,
    )
    for issue in manifest_issues:
        print(f"Manifest issue: {issue}")
    if not is_valid:
        print("Refusing safe sync with stale or mismatched manifest. Re-run with --init-baseline or --allow-stale-manifest.")
        return 2

    rows = classify(source_files, dest_files, base_files)
    summarize_rows(
        rows,
        (
            "SAFE_IMPORT",
            "CLAUDE_DELETED",
            "BASELINE_MISSING_SAME",
            "BASELINE_STALE",
            "CODEX_ONLY",
            "CONFLICT",
        ),
    )
    print_rows(rows, args.verbose, hide_statuses={"UNCHANGED"})

    blockers = [r for r in rows if r.status == "CONFLICT"]
    delete_candidates = [r for r in rows if r.status == "CLAUDE_DELETED"]
    if delete_candidates and not args.delete:
        print(f"\nDelete note: {len(delete_candidates)} Claude-side deletions are pending. Re-run with --delete to apply them.")
    if blockers:
        print(f"Conflict note: {len(blockers)} paths still require manual review.")
    print("Safe mode note: this is not a full mirror. Only safe rows are applied automatically.")

    if args.apply:
        manifest_files = parse_manifest_files(base_files)
        applied, next_manifest = apply_safe_rows(
            rows,
            source_root,
            dest_root,
            source_files,
            manifest_files,
            include_deletes=args.delete,
        )
        write_manifest(manifest_path, source_root, next_manifest)
        print(f"\nApplied safe changes: {applied}")
        print(f"Manifest refreshed: {manifest_path}")

    if blockers:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
