#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import time
from pathlib import Path


JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean old md2json job artifact directories.")
    parser.add_argument(
        "--jobs-root",
        type=Path,
        default=Path(os.environ.get("MD2JSON_JOBS_ROOT", "var/jobs")),
        help="Job root directory. Defaults to MD2JSON_JOBS_ROOT or var/jobs.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.environ.get("MD2JSON_RETENTION_DAYS", "7")),
        help="Delete job directories older than this many days.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print directories that would be removed without deleting them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    jobs_root = args.jobs_root.expanduser().resolve()
    if args.retention_days < 1:
        raise SystemExit("--retention-days must be at least 1.")
    if not jobs_root.exists():
        print(f"jobs_root_missing={jobs_root}")
        return 0
    if not jobs_root.is_dir():
        raise SystemExit(f"jobs root is not a directory: {jobs_root}")

    cutoff = time.time() - args.retention_days * 24 * 60 * 60
    candidates = list(_iter_job_dirs(jobs_root))
    removed = 0
    for path in sorted(candidates):
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime > cutoff:
            continue
        if args.dry_run:
            print(f"would_remove={path}")
        else:
            shutil.rmtree(path)
            print(f"removed={path}")
        removed += 1
    print(f"cleanup_done jobs_root={jobs_root} retention_days={args.retention_days} matched={removed}")
    return 0


def _iter_job_dirs(jobs_root: Path):
    for namespace in ("doc2x", "full"):
        root = jobs_root / namespace
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir():
                    yield child
    for child in jobs_root.iterdir():
        if child.is_dir() and JOB_ID_RE.fullmatch(child.name):
            yield child


if __name__ == "__main__":
    raise SystemExit(main())
