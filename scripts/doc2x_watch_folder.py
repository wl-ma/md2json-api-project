#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from md2json_api.doc2x_client import Doc2XClient
from md2json_api.doc2x_jobs import validate_doc2x_options
from md2json_api.runtime import atomic_write_json, atomic_write_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a folder for PDF files and convert missing or changed files through Doc2X. "
            "DOC2X_API_KEY must be configured in the local environment."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=_env_path("DOC2X_WATCH_INPUT_DIR"),
        help="Directory containing source PDF files. Defaults to DOC2X_WATCH_INPUT_DIR.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_env_path("DOC2X_WATCH_OUTPUT_DIR"),
        help="Directory where Doc2X outputs are written. Defaults to DOC2X_WATCH_OUTPUT_DIR.",
    )
    parser.add_argument("--doc2x-model", choices=["v2", "v3-2026"], default=os.environ.get("DOC2X_MODEL", "v3-2026"))
    parser.add_argument("--formula-mode", choices=["normal", "dollar"], default=os.environ.get("DOC2X_FORMULA_MODE", "normal"))
    parser.add_argument("--formula-level", choices=["0", "1", "2"], default=os.environ.get("DOC2X_FORMULA_LEVEL", "0"))
    parser.add_argument(
        "--merge-cross-page-forms",
        action="store_true",
        default=_env_bool("DOC2X_MERGE_CROSS_PAGE_FORMS", False),
        help="Ask Doc2X to merge cross-page tables.",
    )
    parser.add_argument(
        "--stable-seconds",
        type=int,
        default=int(os.environ.get("DOC2X_WATCH_STABLE_SECONDS", "30")),
        help="Skip files modified more recently than this many seconds.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=int(os.environ.get("DOC2X_WATCH_MAX_FILES", "0")),
        help="Maximum PDFs to process in this run. 0 means no limit.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=_env_bool("DOC2X_WATCH_RECURSIVE", False),
        help="Scan input directory recursively.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input_dir is None:
        raise SystemExit("--input-dir or DOC2X_WATCH_INPUT_DIR is required.")
    if args.output_dir is None:
        raise SystemExit("--output-dir or DOC2X_WATCH_OUTPUT_DIR is required.")
    if not os.environ.get("DOC2X_API_KEY"):
        raise SystemExit("DOC2X_API_KEY is not configured in the local environment.")
    if args.stable_seconds < 0:
        raise SystemExit("--stable-seconds must be non-negative.")

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    options = validate_doc2x_options(
        {
            "doc2x_model": args.doc2x_model,
            "formula_mode": args.formula_mode,
            "formula_level": args.formula_level,
            "merge_cross_page_forms": args.merge_cross_page_forms,
        }
    )
    client = Doc2XClient()

    processed = 0
    skipped_done = 0
    skipped_unstable = 0
    failed = 0

    for pdf in _iter_pdfs(input_dir, recursive=args.recursive):
        if args.max_files and processed >= args.max_files:
            break
        if not _is_stable(pdf, args.stable_seconds):
            skipped_unstable += 1
            print(f"skip_unstable={pdf}")
            continue

        digest = _sha256(pdf)
        relative_key = _relative_output_key(input_dir, pdf)
        target_dir = output_dir / relative_key
        done_path = target_dir / "done.json"
        failed_path = target_dir / "failed.json"
        lock_path = target_dir / ".running"

        if _already_done(done_path, digest):
            skipped_done += 1
            print(f"skip_done={pdf}")
            continue
        if lock_path.exists():
            print(f"skip_running={pdf}")
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(lock_path, f"{os.getpid()}\n")
        try:
            if failed_path.exists():
                failed_path.unlink()
            print(f"doc2x_start={pdf}")
            result = client.convert_file(
                source_file=pdf,
                output_dir=target_dir,
                options=options,
                progress_callback=lambda phase, progress, name=pdf.name: print(
                    f"file={name} phase={phase}" + (f" progress={progress}" if progress is not None else "")
                ),
            )
            atomic_write_text(target_dir / "source.sha256", digest + "\n")
            atomic_write_json(
                done_path,
                {
                    "status": "succeeded",
                    "source_file": str(pdf),
                    "source_sha256": digest,
                    "doc2x_model": options["doc2x_model"],
                    "formula_mode": options["formula_mode"],
                    "formula_level": options["formula_level"],
                    "markdown_path": str(result.markdown_path),
                    "json_path": str(result.json_path),
                    "manifest_path": str(result.manifest_path),
                    "completed_at": _timestamp(),
                },
            )
            processed += 1
            print(f"doc2x_succeeded={pdf}")
        except Exception as exc:
            failed += 1
            atomic_write_json(
                failed_path,
                {
                    "status": "failed",
                    "source_file": str(pdf),
                    "source_sha256": digest,
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "failed_at": _timestamp(),
                },
            )
            print(f"doc2x_failed={pdf} error={type(exc).__name__}")
        finally:
            if lock_path.exists():
                lock_path.unlink()

    print(
        "watch_done "
        f"input_dir={input_dir} output_dir={output_dir} "
        f"processed={processed} skipped_done={skipped_done} "
        f"skipped_unstable={skipped_unstable} failed={failed}"
    )
    return 1 if failed else 0


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _iter_pdfs(input_dir: Path, *, recursive: bool) -> list[Path]:
    candidates = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted(path for path in candidates if path.is_file() and path.suffix.lower() == ".pdf")


def _is_stable(path: Path, stable_seconds: int) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    if stable_seconds == 0:
        return True
    return time.time() - stat.st_mtime >= stable_seconds


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_output_key(input_dir: Path, pdf: Path) -> Path:
    relative = pdf.relative_to(input_dir)
    return relative.with_suffix("")


def _already_done(done_path: Path, digest: str) -> bool:
    if not done_path.exists():
        return False
    try:
        payload: Any = json.loads(done_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "succeeded"
        and payload.get("source_sha256") == digest
    )


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
