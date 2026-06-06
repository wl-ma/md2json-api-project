#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from md2json_api.cache_gc import CacheGcService, CacheGcSettings
from md2json_api.jobs import WorkerSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean cached md2json job artifacts.")
    parser.add_argument("--jobs-root", type=Path, default=None, help="Override jobs root directory.")
    parser.add_argument("--dry-run", action="store_true", help="Only print deletions without removing files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    worker_settings = WorkerSettings.from_environment()
    settings = CacheGcSettings.from_environment()
    if args.dry_run:
        settings.dry_run = True
    jobs_root = args.jobs_root.resolve() if args.jobs_root else worker_settings.jobs_root.resolve()
    report = CacheGcService(jobs_root=jobs_root, settings=settings).run()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
