from __future__ import annotations

import argparse
import os
from pathlib import Path

from .converter import ConverterConfig, MarkdownJsonConverter, REASONING_EFFORT_CHOICES
from .prompts import PROMPT_PROFILES
from .splitter import split_markdown_document


def _normalized_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert math textbook Markdown to JSON.")
    subparsers = parser.add_subparsers(dest="command")

    convert = subparsers.add_parser("convert", help="Convert one Markdown file to JSON outputs.")
    convert.add_argument("input_md", type=Path, help="Input Markdown file.")
    convert.add_argument("--out-dir", type=Path, default=None, help="Output directory. Defaults to <input>_json.")
    convert.add_argument("--backend", choices=["openai", "azure", "mock", "local"], default="openai")
    convert.add_argument("--model", default=os.environ.get("MD2JSON_MODEL", "gpt-5.2"))
    convert.add_argument("--api-key", default=None, help="OpenAI API key. Prefer OPENAI_API_KEY instead.")
    convert.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"), help="Optional OpenAI-compatible base URL.")
    convert.add_argument("--azure-endpoint", default=os.environ.get("AZURE_OPENAI_ENDPOINT"))
    convert.add_argument("--azure-api-version", default=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    convert.add_argument("--max-output-tokens", type=int, default=None)
    convert.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORT_CHOICES,
        default=_normalized_env("MD2JSON_REASONING_EFFORT"),
        help="Optional reasoning effort for reasoning models: none, minimal, low, medium, high, or xhigh.",
    )
    convert.add_argument(
        "--llm-timeout",
        type=float,
        default=float(os.environ.get("MD2JSON_LLM_TIMEOUT", "600")),
        help="LLM request timeout in seconds.",
    )
    convert.add_argument(
        "--prompt-profile",
        choices=list(PROMPT_PROFILES),
        default=os.environ.get("MD2JSON_PROMPT_PROFILE", "auto"),
        help="Prompt profile: auto, textbook, paper, or chinese_math.",
    )
    convert.add_argument(
        "--audit-mode",
        choices=["auto", "llm", "off"],
        default=os.environ.get("MD2JSON_AUDIT_MODE", "auto"),
        help="Run LLM audit/repair after initial extraction. auto enables it for openai/azure/mock.",
    )
    convert.add_argument(
        "--structure-mode",
        choices=["auto", "llm", "hard"],
        default=os.environ.get("MD2JSON_STRUCTURE_MODE", "auto"),
        help="Plan chapter/section structure before extraction. auto calls LLM on suspicious structure.",
    )
    convert.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing structure/api/audit response JSON files when rerunning after an interrupted conversion.",
    )

    inspect = subparsers.add_parser("inspect", help="Show detected chapter/section boundaries only.")
    inspect.add_argument("input_md", type=Path)

    serve = subparsers.add_parser("serve", help="Run the authenticated asynchronous HTTP API service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        split_plan = split_markdown_document(args.input_md.read_text(encoding="utf-8"), source_name=args.input_md.name)
        for section in split_plan.sections:
            print(
                f"{section.index:02d} "
                f"{section.context.chapter} | {section.context.section} "
                f"(lines {section.start_line}-{section.end_line})"
            )
        if split_plan.front_matter:
            print(f"front_matter: {len(split_plan.front_matter)} chars")
        if split_plan.back_matter:
            print(f"back_matter_excluded: {len(split_plan.back_matter)} chars")
        for warning in split_plan.warnings:
            print(f"warning: {warning}")
        return 0

    if args.command == "convert":
        if args.backend == "openai" and not (args.api_key or os.environ.get("OPENAI_API_KEY")):
            parser.error("OpenAI backend requires OPENAI_API_KEY or --api-key. Use --backend local for offline smoke tests.")
        if args.backend == "azure":
            if not (args.api_key or os.environ.get("AZURE_OPENAI_API_KEY")):
                parser.error("Azure backend requires AZURE_OPENAI_API_KEY or --api-key.")
            if not args.azure_endpoint:
                parser.error("Azure backend requires AZURE_OPENAI_ENDPOINT or --azure-endpoint.")
        if args.structure_mode == "llm" and args.backend == "local":
            parser.error("--structure-mode llm requires --backend openai, azure, or mock.")
        config = ConverterConfig(
            backend=args.backend,
            model=args.model,
            api_key=args.api_key or os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=args.base_url,
            azure_endpoint=args.azure_endpoint,
            azure_api_version=args.azure_api_version,
            max_output_tokens=args.max_output_tokens,
            llm_timeout=args.llm_timeout,
            prompt_profile=args.prompt_profile,
            audit_mode=args.audit_mode,
            structure_mode=args.structure_mode,
            resume=args.resume,
            reasoning_effort=args.reasoning_effort,
        )
        result = MarkdownJsonConverter(config).convert(args.input_md, args.out_dir)
        print(f"wrote {result.items_total} items across {result.sections_written} sections")
        print(result.out_dir)
        return 0

    if args.command == "serve":
        try:
            import uvicorn
        except ModuleNotFoundError as exc:
            parser.error("The API dependencies are not installed. Run: python3 -m pip install -r requirements.txt")
            raise exc
        from .server import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
