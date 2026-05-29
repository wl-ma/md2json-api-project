#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from md2json_api.converter import ConverterConfig, MarkdownJsonConverter
from md2json_api.doc2x_client import Doc2XClient
from md2json_api.doc2x_jobs import validate_doc2x_options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a manual Doc2X integration smoke test with DOC2X_API_KEY from the local environment. "
            "The script never prints the key or Doc2X signed URLs."
        )
    )
    parser.add_argument("pdf", type=Path, help="PDF file to upload to Doc2X.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to /tmp/md2json-doc2x-smoke-<timestamp>.",
    )
    parser.add_argument("--doc2x-model", choices=["v2", "v3-2026"], default=os.environ.get("DOC2X_MODEL", "v3-2026"))
    parser.add_argument("--formula-mode", choices=["normal", "dollar"], default="normal")
    parser.add_argument("--formula-level", choices=["0", "1", "2"], default="0")
    parser.add_argument("--merge-cross-page-forms", action="store_true")
    parser.add_argument(
        "--md2json-local",
        action="store_true",
        help="After Doc2X succeeds, run the generated Markdown through the local md2json backend.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pdf = args.pdf.expanduser().resolve()
    if not pdf.exists() or not pdf.is_file():
        raise SystemExit(f"PDF does not exist: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise SystemExit("Only .pdf files are supported by this smoke test.")
    if not os.environ.get("DOC2X_API_KEY"):
        raise SystemExit("DOC2X_API_KEY is not configured in the local environment.")

    out_dir = (
        args.out_dir.expanduser().resolve()
        if args.out_dir is not None
        else Path("/tmp") / f"md2json-doc2x-smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    doc2x_dir = out_dir / "doc2x"
    options = validate_doc2x_options(
        {
            "doc2x_model": args.doc2x_model,
            "formula_mode": args.formula_mode,
            "formula_level": args.formula_level,
            "merge_cross_page_forms": args.merge_cross_page_forms,
        }
    )

    print(f"input_pdf={pdf}")
    print(f"output_dir={out_dir}")
    print("doc2x_status=starting")

    result = Doc2XClient().convert_file(
        source_file=pdf,
        output_dir=doc2x_dir,
        options=options,
        progress_callback=lambda phase, progress: print(
            f"doc2x_phase={phase}" + (f" progress={progress}" if progress is not None else "")
        ),
    )

    markdown = result.markdown_path.read_text(encoding="utf-8")
    doc2x_json = json.loads(result.json_path.read_text(encoding="utf-8"))
    pages = doc2x_json.get("pages") if isinstance(doc2x_json, dict) else None
    page_count = len(pages) if isinstance(pages, list) else "unknown"

    print("doc2x_status=succeeded")
    print(f"markdown_path={result.markdown_path}")
    print(f"markdown_chars={len(markdown)}")
    print(f"doc2x_json_path={result.json_path}")
    print(f"doc2x_pages={page_count}")
    print(f"manifest_path={result.manifest_path}")

    if args.md2json_local:
        md2json_out = out_dir / "md2json_local"
        print("md2json_status=starting")
        conversion = MarkdownJsonConverter(
            ConverterConfig(backend="local", model="unused", structure_mode="hard", audit_mode="off")
        ).convert(result.markdown_path, md2json_out)
        final_json = conversion.out_dir / f"{conversion.source_file.stem}.json"
        print("md2json_status=succeeded")
        print(f"md2json_result_path={final_json}")
        print(f"md2json_items={conversion.items_total}")
        print(f"md2json_sections={conversion.sections_written}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
