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

from md2json_api.converter import ConverterConfig, MarkdownJsonConverter
from md2json_api.runtime import atomic_write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan completed Doc2X folder results and run md2json on output.md files."
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=_env_path("MD2JSON_DOC2X_INPUT_ROOT"),
        help="Root directory containing completed Doc2X results.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=_env_path("MD2JSON_MDONLY_WORK_ROOT"),
        help="Directory for md2json intermediate outputs.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_env_path("MD2JSON_MDONLY_OUTPUT_ROOT"),
        help="Directory for exported md2json results and status files.",
    )
    parser.add_argument(
        "--stable-seconds",
        type=int,
        default=int(os.environ.get("MD2JSON_MDONLY_STABLE_SECONDS", "30")),
        help="Skip source folders modified more recently than this many seconds.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=int(os.environ.get("MD2JSON_MDONLY_MAX_FILES", "0")),
        help="Maximum completed Doc2X folders to process in one run. 0 means no limit.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=int(os.environ.get("MD2JSON_MDONLY_MAX_FAILURES", "3")),
        help="Maximum failure count before blocking an item.",
    )
    parser.add_argument(
        "--retry-cooldown-seconds",
        type=int,
        default=int(os.environ.get("MD2JSON_MDONLY_RETRY_COOLDOWN_SECONDS", "3600")),
        help="Cooldown period after a failure before retrying the same item.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be processed; do not write outputs or run md2json.",
    )
    parser.add_argument(
        "--backend",
        choices=["openai", "azure", "mock", "local"],
        default=os.environ.get("MD2JSON_SERVER_BACKEND", "openai"),
    )
    parser.add_argument("--model", default=os.environ.get("MD2JSON_MODEL", "gpt-5.5"))
    parser.add_argument("--prompt-profile", default=os.environ.get("MD2JSON_PROMPT_PROFILE", "auto"))
    parser.add_argument("--structure-mode", default=os.environ.get("MD2JSON_STRUCTURE_MODE", "auto"))
    parser.add_argument("--audit-mode", default=os.environ.get("MD2JSON_AUDIT_MODE", "auto"))
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=float(os.environ.get("MD2JSON_LLM_TIMEOUT", "600")),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=os.environ.get("MD2JSON_REASONING_EFFORT"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input_root is None or args.work_root is None or args.output_root is None:
        raise SystemExit("MD2JSON_DOC2X_INPUT_ROOT, MD2JSON_MDONLY_WORK_ROOT, and MD2JSON_MDONLY_OUTPUT_ROOT are required.")

    input_root = args.input_root.expanduser().resolve()
    work_root = args.work_root.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    if not input_root.exists():
        raise SystemExit(f"Input root does not exist: {input_root}")

    processed = 0
    skipped_done = 0
    skipped_unstable = 0
    skipped_blocked = 0
    skipped_cooling = 0
    skipped_running = 0
    failed = 0

    config = ConverterConfig(
        backend=args.backend,
        model=args.model,
        api_key=os.environ.get("AZURE_OPENAI_API_KEY") if args.backend == "azure" else os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL") if args.backend != "azure" else None,
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT") if args.backend == "azure" else None,
        azure_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        llm_timeout=args.llm_timeout,
        prompt_profile=args.prompt_profile,
        structure_mode=args.structure_mode,
        audit_mode=args.audit_mode,
        reasoning_effort=(args.reasoning_effort or None),
        resume=True,
    )

    for source_dir in sorted(_iter_source_dirs(input_root)):
        rel = source_dir.relative_to(input_root)
        output_dir = output_root / rel
        work_dir = work_root / rel
        done_path = output_dir / "done.json"
        failed_path = output_dir / "failed.json"
        blocked_path = output_dir / "blocked.json"
        lock_path = output_dir / ".running"
        status_path = output_dir / "status.json"
        meta_path = output_dir / "meta.json"
        markdown_path = source_dir / "output.md"
        pages_path = source_dir / "pages.json"
        source_done = source_dir / "done.json"

        if not markdown_path.exists() or not pages_path.exists() or not source_done.exists():
            continue
        if _already_done(done_path, markdown_path):
            print(f"skip_done={source_dir}")
            skipped_done += 1
            continue
        if _is_blocked(blocked_path, markdown_path):
            print(f"skip_blocked={source_dir}")
            skipped_blocked += 1
            continue
        if _is_cooling_down(failed_path, markdown_path, args.retry_cooldown_seconds):
            print(f"skip_cooling={source_dir}")
            skipped_cooling += 1
            continue
        if lock_path.exists():
            print(f"skip_running={source_dir}")
            skipped_running += 1
            continue
        if not _is_stable(source_dir, args.stable_seconds):
            print(f"skip_unstable={source_dir}")
            skipped_unstable += 1
            continue

        if args.dry_run:
            print(f"would_process={source_dir} -> output={output_dir} work={work_dir}")
            processed += 1
            if args.max_files and processed >= args.max_files:
                break
            continue

        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(time.time()), encoding="utf-8")
        try:
            atomic_write_json(
                status_path,
                {
                    "status": "running",
                    "phase": "md2json_starting",
                    "source_dir": str(source_dir),
                    "markdown_path": str(markdown_path),
                    "updated_at": _now(),
                },
            )
            result = MarkdownJsonConverter(config).convert(
                markdown_path,
                work_dir / "md2json_output",
                progress_callback=lambda progress: atomic_write_json(
                    status_path,
                    {
                        "status": "running",
                        "phase": f"md2json_{progress['phase']}",
                        "sections_total": int(progress["sections_total"]),
                        "sections_completed": int(progress["sections_completed"]),
                        "updated_at": _now(),
                    },
                ),
            )
            result_path = result.out_dir / f"{result.source_file.stem}.json"
            quality_path = result.out_dir / "quality_report.json"
            usage_path = result.out_dir / "usage_summary.json"
            atomic_write_json(
                meta_path,
                {
                    "status": "succeeded",
                    "source_dir": str(source_dir),
                    "markdown_path": str(markdown_path),
                    "pages_path": str(pages_path),
                    "work_dir": str(work_dir),
                    "md2json_output_dir": str(result.out_dir),
                    "result_path": str(result_path),
                    "quality_path": str(quality_path),
                    "usage_path": str(usage_path),
                    "backend": args.backend,
                    "model": args.model,
                    "updated_at": _now(),
                },
            )
            _copy_if_exists(result_path, output_dir / "result.json")
            _copy_if_exists(quality_path, output_dir / "quality.json")
            _copy_if_exists(usage_path, output_dir / "usage.json")
            atomic_write_json(
                done_path,
                {
                    "status": "done",
                    "digest": _digest(markdown_path),
                    "source_dir": str(source_dir),
                    "updated_at": _now(),
                },
            )
            if failed_path.exists():
                failed_path.unlink()
            if blocked_path.exists():
                blocked_path.unlink()
            atomic_write_json(
                status_path,
                {
                    "status": "succeeded",
                    "phase": "completed",
                    "updated_at": _now(),
                },
            )
            processed += 1
        except Exception as exc:
            failed += 1
            previous_failures = _failure_count(failed_path, markdown_path)
            failure_count = previous_failures + 1
            failure_payload = {
                "status": "failed",
                "digest": _digest(markdown_path),
                "source_dir": str(source_dir),
                "error": f"{type(exc).__name__}: {exc}",
                "failure_count": failure_count,
                "max_failures": args.max_failures,
                "updated_at": _now(),
            }
            atomic_write_json(failed_path, failure_payload)
            atomic_write_json(
                status_path,
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": failure_payload["error"],
                    "failure_count": failure_count,
                    "updated_at": _now(),
                },
            )
            if failure_count >= args.max_failures:
                blocked_payload = dict(failure_payload)
                blocked_payload["status"] = "blocked"
                atomic_write_json(blocked_path, blocked_payload)
                print(f"md2json_blocked={source_dir} failures={failure_count}")
            else:
                print(f"md2json_failed={source_dir} failures={failure_count} error={failure_payload['error']}")
        finally:
            if lock_path.exists():
                lock_path.unlink()

        if args.max_files and processed >= args.max_files:
            break

    print(
        "watch_done "
        f"input_root={input_root} output_root={output_root} work_root={work_root} "
        f"processed={processed} skipped_done={skipped_done} skipped_unstable={skipped_unstable} "
        f"skipped_blocked={skipped_blocked} skipped_cooling={skipped_cooling} skipped_running={skipped_running} failed={failed}"
    )
    return 0


def _iter_source_dirs(input_root: Path) -> list[Path]:
    return [path for path in input_root.rglob("output.md") if path.is_file() and path.parent.is_dir()]


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _already_done(done_path: Path, markdown_path: Path) -> bool:
    if not done_path.exists():
        return False
    try:
        payload = json.loads(done_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("digest") == _digest(markdown_path)


def _is_blocked(blocked_path: Path, markdown_path: Path) -> bool:
    if not blocked_path.exists():
        return False
    try:
        payload = json.loads(blocked_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("digest") == _digest(markdown_path)


def _is_cooling_down(failed_path: Path, markdown_path: Path, retry_cooldown_seconds: int) -> bool:
    if retry_cooldown_seconds <= 0 or not failed_path.exists():
        return False
    try:
        payload = json.loads(failed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if payload.get("digest") != _digest(markdown_path):
        return False
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str):
        return False
    try:
        updated_ts = time.mktime(time.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return False
    return (time.time() - updated_ts) < retry_cooldown_seconds


def _failure_count(failed_path: Path, markdown_path: Path) -> int:
    if not failed_path.exists():
        return 0
    try:
        payload = json.loads(failed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    if payload.get("digest") != _digest(markdown_path):
        return 0
    value = payload.get("failure_count")
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _is_stable(path: Path, stable_seconds: int) -> bool:
    if stable_seconds <= 0:
        return True
    newest = path.stat().st_mtime
    for candidate in path.rglob("*"):
        try:
            newest = max(newest, candidate.stat().st_mtime)
        except FileNotFoundError:
            continue
    return (time.time() - newest) >= stable_seconds


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.write_bytes(src.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
