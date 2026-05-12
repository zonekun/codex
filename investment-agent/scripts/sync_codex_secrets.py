"""One-way import of non-git secrets from GCS into the Codex workspace.

Claude Code already uses GCS as the sync point for non-git files. This tool
keeps all extra state on the Codex side and never writes to the Claude Code
workspace or to GCS.

No secret values are printed. The manifest stores local SHA-256 hashes and GCS
object generation metadata only.

Typical usage:
    python scripts/sync_codex_secrets.py --init-baseline
    python scripts/sync_codex_secrets.py
    python scripts/sync_codex_secrets.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))
GCLOUD = "gcloud.cmd" if platform.system() == "Windows" else "gcloud"
DEFAULT_BUCKET = "gs://stock_data_1930932/config/investment-agent"
DEFAULT_MANIFEST = Path("data/logs/codex_secrets_sync_manifest.json")
DEFAULT_FILES = (
    ".env",
    "keys/gcp-service-account.json",
)


@dataclass(frozen=True)
class LocalInfo:
    sha256: str
    size: int


@dataclass(frozen=True)
class SourceInfo:
    uri: str
    generation: str
    metageneration: str
    size: int
    md5_hash: str
    crc32c: str

    @property
    def signature(self) -> str:
        return f"{self.generation}:{self.metageneration}:{self.size}:{self.md5_hash}:{self.crc32c}"


@dataclass(frozen=True)
class Row:
    status: str
    path: str
    action: str


def sha256_file(path: Path) -> LocalInfo:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return LocalInfo(sha256=h.hexdigest(), size=size)


def run_gcloud(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([GCLOUD, *args], capture_output=True, text=True, check=False)


def describe_gcs(uri: str) -> SourceInfo | None:
    result = run_gcloud(["storage", "objects", "describe", uri, "--format=json"])
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return SourceInfo(
        uri=uri,
        generation=str(data.get("generation", "")),
        metageneration=str(data.get("metageneration", "")),
        size=int(data.get("size", 0) or 0),
        md5_hash=str(data.get("md5Hash", "")),
        crc32c=str(data.get("crc32c", "")),
    )


def copy_gcs_to_file(uri: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run_gcloud(["storage", "cp", uri, str(dest)])
    return result.returncode == 0


def hash_gcs_object(uri: str) -> LocalInfo | None:
    with tempfile.TemporaryDirectory(prefix="codex-gcs-secret-") as td:
        tmp = Path(td) / "object.bin"
        if not copy_gcs_to_file(uri, tmp):
            return None
        return sha256_file(tmp)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "files": {}}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_manifest(path: Path, bucket: str, entries: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "bucket": bucket,
        "updated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "files": entries,
    }
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def source_uri(bucket: str, rel_path: str) -> str:
    return f"{bucket.rstrip('/')}/{rel_path}"


def collect_sources(bucket: str, files: tuple[str, ...]) -> dict[str, SourceInfo]:
    sources: dict[str, SourceInfo] = {}
    for rel in files:
        info = describe_gcs(source_uri(bucket, rel))
        if info:
            sources[rel] = info
    return sources


def collect_local(root: Path, files: tuple[str, ...]) -> dict[str, LocalInfo]:
    local: dict[str, LocalInfo] = {}
    for rel in files:
        p = root / rel
        if p.exists() and p.is_file():
            local[rel] = sha256_file(p)
    return local


def classify(
    sources: dict[str, SourceInfo],
    local: dict[str, LocalInfo],
    base: dict[str, dict],
    files: tuple[str, ...],
) -> list[Row]:
    rows: list[Row] = []
    for path in files:
        src = sources.get(path)
        dst = local.get(path)
        b = base.get(path)
        if not src:
            rows.append(Row("SOURCE_MISSING", path, "not found in GCS"))
            continue
        if not b:
            rows.append(Row("BASELINE_MISSING", path, "run --init-baseline or review then --apply"))
            continue

        base_sig = str(b.get("source_signature", ""))
        base_local_sha = str(b.get("local_sha256", ""))
        src_changed = src.signature != base_sig
        dst_sha = dst.sha256 if dst else ""
        dst_changed = dst_sha != base_local_sha

        if not dst:
            rows.append(Row("SAFE_IMPORT", path, "restore local file from GCS"))
        elif not src_changed and not dst_changed:
            rows.append(Row("UNCHANGED", path, "none"))
        elif src_changed and not dst_changed:
            rows.append(Row("SAFE_IMPORT", path, "update from GCS"))
        elif not src_changed and dst_changed:
            rows.append(Row("CODEX_ONLY", path, "local changed"))
        else:
            rows.append(Row("CONFLICT", path, "GCS and local both changed"))
    return rows


def manifest_entry(src: SourceInfo, local: LocalInfo) -> dict:
    return {
        "uri": src.uri,
        "source_signature": src.signature,
        "generation": src.generation,
        "metageneration": src.metageneration,
        "size": src.size,
        "md5_hash": src.md5_hash,
        "crc32c": src.crc32c,
        "local_sha256": local.sha256,
        "local_size": local.size,
    }


def init_baseline(
    root: Path,
    bucket: str,
    manifest_path: Path,
    sources: dict[str, SourceInfo],
    local: dict[str, LocalInfo],
    files: tuple[str, ...],
    verify_source: bool,
) -> int:
    entries: dict[str, dict] = {}
    skipped: list[str] = []
    for rel in files:
        src = sources.get(rel)
        dst = local.get(rel)
        if not src or not dst:
            skipped.append(rel)
            continue
        if verify_source:
            src_hash = hash_gcs_object(src.uri)
            if not src_hash or src_hash.sha256 != dst.sha256:
                skipped.append(rel)
                continue
        entries[rel] = manifest_entry(src, dst)

    write_manifest(manifest_path, bucket, entries)
    print(f"Initialized secrets baseline: {len(entries)} files")
    if skipped:
        print("Skipped files:")
        for rel in skipped:
            print(f"  {rel}")
        return 2
    return 0


def print_rows(rows: list[Row], verbose: bool) -> None:
    visible = rows if verbose else [r for r in rows if r.status != "UNCHANGED"]
    if not visible:
        print("No secrets differences detected.")
        return
    width = max(len(r.status) for r in visible)
    for r in visible:
        print(f"{r.status:<{width}}  {r.path}  ({r.action})")


def apply_safe(
    rows: list[Row],
    root: Path,
    bucket: str,
    sources: dict[str, SourceInfo],
    entries: dict[str, dict],
) -> int:
    applied = 0
    for row in rows:
        if row.status != "SAFE_IMPORT":
            continue
        src = sources.get(row.path)
        if not src:
            continue
        dest = root / row.path
        if not copy_gcs_to_file(src.uri, dest):
            print(f"FAILED  {row.path}")
            continue
        entries[row.path] = manifest_entry(src, sha256_file(dest))
        applied += 1
    return applied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely import Codex non-git secrets from the project GCS bucket."
    )
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--file", action="append", dest="files")
    parser.add_argument("--init-baseline", action="store_true")
    parser.add_argument("--no-verify-source", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.dest.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    files = tuple(args.files) if args.files else DEFAULT_FILES

    sources = collect_sources(args.bucket, files)
    local = collect_local(root, files)

    if args.init_baseline:
        return init_baseline(
            root,
            args.bucket,
            manifest_path,
            sources,
            local,
            files,
            verify_source=not args.no_verify_source,
        )

    manifest = load_manifest(manifest_path)
    base_files = manifest.get("files", {})
    if not base_files:
        print("Secrets baseline manifest is missing or empty. Run --init-baseline first.")
        return 2

    rows = classify(sources, local, base_files, files)
    print_rows(rows, args.verbose)

    blockers = [r for r in rows if r.status == "CONFLICT"]
    if args.apply:
        entries = dict(base_files)
        applied = apply_safe(rows, root, args.bucket, sources, entries)
        write_manifest(manifest_path, args.bucket, entries)
        print(f"Applied safe secret imports: {applied}")

    if blockers:
        print(f"Conflicts require manual review: {len(blockers)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
