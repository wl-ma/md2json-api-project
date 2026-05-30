#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import zipfile
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
    parser.add_argument(
        "--max-failures",
        type=int,
        default=int(os.environ.get("DOC2X_WATCH_MAX_FAILURES", "3")),
        help="Block automatic retries after this many consecutive failures for the same PDF content.",
    )
    parser.add_argument(
        "--retry-cooldown-seconds",
        type=int,
        default=int(os.environ.get("DOC2X_RETRY_COOLDOWN_SECONDS", "3600")),
        help="Wait this many seconds after a failed attempt before retrying the same PDF content.",
    )
    parser.add_argument(
        "--max-upload-bytes",
        type=int,
        default=int(os.environ.get("DOC2X_MAX_UPLOAD_BYTES", "300000000")),
        help="Maximum bytes per PDF sent to Doc2X. Larger PDFs are split before upload.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("DOC2X_MAX_PAGES", "2000")),
        help="Maximum pages per PDF sent to Doc2X. Larger PDFs are split before upload.",
    )
    parser.add_argument(
        "--split-max-pages",
        type=int,
        default=int(os.environ.get("DOC2X_SPLIT_MAX_PAGES", "500")),
        help="Target maximum pages per split chunk before byte-size refinement.",
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
    if args.max_failures < 1:
        raise SystemExit("--max-failures must be at least 1.")
    if args.retry_cooldown_seconds < 0:
        raise SystemExit("--retry-cooldown-seconds must be non-negative.")
    if args.max_upload_bytes < 1:
        raise SystemExit("--max-upload-bytes must be positive.")
    if args.max_pages < 1:
        raise SystemExit("--max-pages must be positive.")
    if args.split_max_pages < 1:
        raise SystemExit("--split-max-pages must be positive.")

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
    skipped_blocked = 0
    skipped_cooling = 0
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
        blocked_path = target_dir / "blocked.json"
        lock_path = target_dir / ".running"

        if _already_done(done_path, digest):
            skipped_done += 1
            print(f"skip_done={pdf}")
            continue
        if _is_blocked(blocked_path, digest):
            skipped_blocked += 1
            print(f"skip_blocked={pdf}")
            continue
        if _is_cooling_down(failed_path, digest, args.retry_cooldown_seconds):
            skipped_cooling += 1
            print(f"skip_cooling={pdf}")
            continue
        if lock_path.exists():
            print(f"skip_running={pdf}")
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(lock_path, f"{os.getpid()}\n")
        try:
            _clear_stale_failure_state(failed_path, blocked_path, digest)
            print(f"doc2x_start={pdf}")
            result = _convert_with_precheck_and_split(
                client=client,
                pdf=pdf,
                output_dir=target_dir,
                options=options,
                max_upload_bytes=args.max_upload_bytes,
                max_pages=args.max_pages,
                split_max_pages=args.split_max_pages,
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
            if failed_path.exists():
                failed_path.unlink()
            if blocked_path.exists():
                blocked_path.unlink()
            processed += 1
            print(f"doc2x_succeeded={pdf}")
        except Exception as exc:
            failed += 1
            previous_failures = _failure_count(failed_path, digest)
            failure_count = previous_failures + 1
            failure_payload = {
                "status": "failed",
                "source_file": str(pdf),
                "source_sha256": digest,
                "failure_count": failure_count,
                "max_failures": args.max_failures,
                "retry_cooldown_seconds": args.retry_cooldown_seconds,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
                "failed_at": _timestamp(),
                "failed_at_epoch": int(time.time()),
            }
            atomic_write_json(
                failed_path,
                failure_payload,
            )
            if failure_count >= args.max_failures:
                blocked_payload = dict(failure_payload)
                blocked_payload["status"] = "blocked"
                blocked_payload["blocked_at"] = _timestamp()
                blocked_payload["blocked_at_epoch"] = int(time.time())
                atomic_write_json(blocked_path, blocked_payload)
                print(f"doc2x_blocked={pdf} failures={failure_count}")
            print(f"doc2x_failed={pdf} error={type(exc).__name__}")
        finally:
            if lock_path.exists():
                lock_path.unlink()

    print(
        "watch_done "
        f"input_dir={input_dir} output_dir={output_dir} "
        f"processed={processed} skipped_done={skipped_done} "
        f"skipped_unstable={skipped_unstable} skipped_blocked={skipped_blocked} "
        f"skipped_cooling={skipped_cooling} failed={failed}"
    )
    return 0


class SplitChunk:
    def __init__(self, path: Path, start_page: int, end_page: int) -> None:
        self.path = path
        self.start_page = start_page
        self.end_page = end_page


class ConversionArtifacts:
    def __init__(self, markdown_path: Path, json_path: Path, manifest_path: Path) -> None:
        self.markdown_path = markdown_path
        self.json_path = json_path
        self.manifest_path = manifest_path


def _convert_with_precheck_and_split(
    *,
    client: Doc2XClient,
    pdf: Path,
    output_dir: Path,
    options: dict[str, Any],
    max_upload_bytes: int,
    max_pages: int,
    split_max_pages: int,
) -> ConversionArtifacts:
    page_count = _pdf_page_count(pdf)
    source_size = pdf.stat().st_size
    if source_size <= max_upload_bytes and page_count <= max_pages:
        return client.convert_file(
            source_file=pdf,
            output_dir=output_dir,
            options=options,
            progress_callback=lambda phase, progress, name=pdf.name: print(
                f"file={name} phase={phase}" + (f" progress={progress}" if progress is not None else "")
            ),
        )

    print(
        f"doc2x_split_required={pdf} pages={page_count} bytes={source_size} "
        f"max_pages={max_pages} max_upload_bytes={max_upload_bytes}"
    )
    with tempfile.TemporaryDirectory(prefix="doc2x-split-") as temp:
        temp_root = Path(temp)
        chunks = _split_pdf(
            pdf=pdf,
            temp_root=temp_root / "chunks",
            max_upload_bytes=max_upload_bytes,
            max_pages=min(max_pages, split_max_pages),
        )
        chunk_results = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_output = temp_root / "outputs" / f"chunk{index:04d}"
            print(
                f"doc2x_chunk_start={pdf.name} chunk={index}/{len(chunks)} "
                f"pages={chunk.start_page + 1}-{chunk.end_page}"
            )
            result = client.convert_file(
                source_file=chunk.path,
                output_dir=chunk_output,
                options=options,
                progress_callback=lambda phase, progress, name=pdf.name, chunk_index=index: print(
                    f"file={name} chunk={chunk_index} phase={phase}"
                    + (f" progress={progress}" if progress is not None else "")
                ),
            )
            chunk_results.append((chunk, result))
        return _merge_chunk_results(pdf=pdf, output_dir=output_dir, chunk_results=chunk_results)


def _pdf_page_count(pdf: Path) -> int:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError("pypdf is required for PDF precheck and splitting. Install project dependencies.") from exc
    with pdf.open("rb") as handle:
        reader = PdfReader(handle)
        return len(reader.pages)


def _split_pdf(*, pdf: Path, temp_root: Path, max_upload_bytes: int, max_pages: int) -> list[SplitChunk]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as exc:
        raise RuntimeError("pypdf is required for PDF precheck and splitting. Install project dependencies.") from exc
    temp_root.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf))
    chunks: list[SplitChunk] = []
    _write_chunk_recursive(
        reader=reader,
        temp_root=temp_root,
        chunks=chunks,
        start_page=0,
        end_page=len(reader.pages),
        max_pages=max_pages,
        max_upload_bytes=max_upload_bytes,
        PdfWriter=PdfWriter,
    )
    return chunks


def _write_chunk_recursive(
    *,
    reader: Any,
    temp_root: Path,
    chunks: list[SplitChunk],
    start_page: int,
    end_page: int,
    max_pages: int,
    max_upload_bytes: int,
    PdfWriter: Any,
) -> None:
    page_count = end_page - start_page
    if page_count > max_pages:
        midpoint = start_page + max_pages
        _write_chunk_recursive(
            reader=reader,
            temp_root=temp_root,
            chunks=chunks,
            start_page=start_page,
            end_page=midpoint,
            max_pages=max_pages,
            max_upload_bytes=max_upload_bytes,
            PdfWriter=PdfWriter,
        )
        _write_chunk_recursive(
            reader=reader,
            temp_root=temp_root,
            chunks=chunks,
            start_page=midpoint,
            end_page=end_page,
            max_pages=max_pages,
            max_upload_bytes=max_upload_bytes,
            PdfWriter=PdfWriter,
        )
        return

    path = temp_root / f"chunk_{start_page + 1:06d}_{end_page:06d}.pdf"
    _write_pdf_range(reader, PdfWriter, start_page, end_page, path)
    if path.stat().st_size <= max_upload_bytes:
        chunks.append(SplitChunk(path, start_page, end_page))
        return
    if page_count == 1:
        raise RuntimeError(
            f"Single PDF page exceeds Doc2X upload byte limit: page={start_page + 1} bytes={path.stat().st_size}"
        )
    path.unlink(missing_ok=True)
    midpoint = start_page + max(1, page_count // 2)
    _write_chunk_recursive(
        reader=reader,
        temp_root=temp_root,
        chunks=chunks,
        start_page=start_page,
        end_page=midpoint,
        max_pages=max_pages,
        max_upload_bytes=max_upload_bytes,
        PdfWriter=PdfWriter,
    )
    _write_chunk_recursive(
        reader=reader,
        temp_root=temp_root,
        chunks=chunks,
        start_page=midpoint,
        end_page=end_page,
        max_pages=max_pages,
        max_upload_bytes=max_upload_bytes,
        PdfWriter=PdfWriter,
    )


def _write_pdf_range(reader: Any, PdfWriter: Any, start_page: int, end_page: int, path: Path) -> None:
    writer = PdfWriter()
    for page_index in range(start_page, end_page):
        writer.add_page(reader.pages[page_index])
    with path.open("wb") as handle:
        writer.write(handle)


def _merge_chunk_results(
    *,
    pdf: Path,
    output_dir: Path,
    chunk_results: list[tuple[SplitChunk, Any]],
) -> ConversionArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_parts: list[str] = []
    merged_pages: list[Any] = []
    chunk_manifest = []
    for index, (chunk, result) in enumerate(chunk_results, start=1):
        markdown = result.markdown_path.read_text(encoding="utf-8").strip()
        if markdown:
            markdown_parts.append(markdown)
        payload = json.loads(result.json_path.read_text(encoding="utf-8"))
        pages = payload.get("pages") if isinstance(payload, dict) else None
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    adjusted = dict(page)
                    if isinstance(adjusted.get("page_idx"), int):
                        adjusted["page_idx"] = chunk.start_page + int(adjusted["page_idx"])
                    merged_pages.append(adjusted)
                else:
                    merged_pages.append(page)
        chunk_manifest.append(
            {
                "chunk_index": index,
                "start_page": chunk.start_page + 1,
                "end_page": chunk.end_page,
                "markdown_chars": len(markdown),
            }
        )
    if not markdown_parts:
        raise RuntimeError("Doc2X split conversion did not produce Markdown.")

    markdown_path = output_dir / "output.md"
    json_path = output_dir / "pages.json"
    manifest_path = output_dir / "export_manifest.json"
    archive_path = output_dir / "export.zip"
    markdown_text = "\n\n".join(markdown_parts).strip() + "\n"
    atomic_write_text(markdown_path, markdown_text)
    atomic_write_json(json_path, {"pages": merged_pages})
    atomic_write_json(
        manifest_path,
        {
            "source_file": pdf.name,
            "markdown_path": markdown_path.name,
            "json_path": json_path.name,
            "archive_path": archive_path.name,
            "markdown_source": "merged_split_exports",
            "split_chunks": chunk_manifest,
        },
    )
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(markdown_path, arcname="output.md")
        archive.write(json_path, arcname="pages.json")
        archive.write(manifest_path, arcname="export_manifest.json")
    return ConversionArtifacts(markdown_path=markdown_path, json_path=json_path, manifest_path=manifest_path)


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


def _is_blocked(blocked_path: Path, digest: str) -> bool:
    payload = _read_json_object(blocked_path)
    return (
        isinstance(payload, dict)
        and payload.get("status") == "blocked"
        and payload.get("source_sha256") == digest
    )


def _is_cooling_down(failed_path: Path, digest: str, cooldown_seconds: int) -> bool:
    if cooldown_seconds == 0:
        return False
    payload = _read_json_object(failed_path)
    if not isinstance(payload, dict) or payload.get("source_sha256") != digest:
        return False
    failed_at = payload.get("failed_at_epoch")
    if not isinstance(failed_at, int):
        return False
    return time.time() - failed_at < cooldown_seconds


def _failure_count(failed_path: Path, digest: str) -> int:
    payload = _read_json_object(failed_path)
    if not isinstance(payload, dict) or payload.get("source_sha256") != digest:
        return 0
    value = payload.get("failure_count")
    return int(value) if isinstance(value, int) else 0


def _clear_stale_failure_state(failed_path: Path, blocked_path: Path, digest: str) -> None:
    failed = _read_json_object(failed_path)
    if isinstance(failed, dict) and failed.get("source_sha256") != digest:
        failed_path.unlink(missing_ok=True)
    blocked = _read_json_object(blocked_path)
    if isinstance(blocked, dict) and blocked.get("source_sha256") != digest:
        blocked_path.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    raise SystemExit(main())
