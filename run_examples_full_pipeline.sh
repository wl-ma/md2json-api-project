#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

INPUT_ROOT="${PROJECT_ROOT}/examples"
OUTPUT_ROOT="${PROJECT_ROOT}/example_full_pipeline_outputs"
RESULTS_DIR="${OUTPUT_ROOT}/results"
LOGS_DIR="${OUTPUT_ROOT}/logs"
PYTHON_BIN="${PYTHON_BIN:-$VENV_PYTHON}"
ENV_FILE="${MD2JSON_ENV_FILE:-/etc/md2json/md2json.env}"
MD2JSON_BACKEND="${MD2JSON_BACKEND:-openai}"
MD2JSON_MODEL="${MD2JSON_MODEL:-gpt-5.2}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}"
AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2024-10-21}"
MD2JSON_LLM_TIMEOUT="${MD2JSON_LLM_TIMEOUT:-600}"
DOC2X_MODEL="${DOC2X_MODEL:-v3-2026}"
FORMULA_MODE="${FORMULA_MODE:-normal}"
FORMULA_LEVEL="${FORMULA_LEVEL:-0}"
MERGE_CROSS_PAGE_FORMS="${MERGE_CROSS_PAGE_FORMS:-false}"
PROMPT_PROFILE="${PROMPT_PROFILE:-auto}"
STRUCTURE_MODE="${STRUCTURE_MODE:-auto}"
AUDIT_MODE="${AUDIT_MODE:-auto}"
FORCE_RERUN="${FORCE_RERUN:-false}"

BOOK_DIRS=(
  "Numerical Optimization"
  "Optimization Theory and Methods"
)

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

MD2JSON_BACKEND="${MD2JSON_BACKEND:-openai}"
MD2JSON_MODEL="${MD2JSON_MODEL:-gpt-5.2}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-}"
AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2024-10-21}"
MD2JSON_LLM_TIMEOUT="${MD2JSON_LLM_TIMEOUT:-600}"
DOC2X_MODEL="${DOC2X_MODEL:-v3-2026}"
FORMULA_MODE="${FORMULA_MODE:-normal}"
FORMULA_LEVEL="${FORMULA_LEVEL:-0}"
MERGE_CROSS_PAGE_FORMS="${MERGE_CROSS_PAGE_FORMS:-false}"
PROMPT_PROFILE="${PROMPT_PROFILE:-auto}"
STRUCTURE_MODE="${STRUCTURE_MODE:-auto}"
AUDIT_MODE="${AUDIT_MODE:-auto}"
FORCE_RERUN="${FORCE_RERUN:-false}"

usage() {
  cat <<EOF
Usage: ./run_examples_full_pipeline.sh

Reads PDFs from:
  examples/Numerical Optimization
  examples/Optimization Theory and Methods

Runs the full local pipeline directly:
  PDF -> Doc2X -> Markdown -> md2json

Outputs are stored per PDF under:
  ${RESULTS_DIR}/<book_slug>/

Resume/skip behavior:
  - If a PDF already has a completed result.json, it is skipped.
  - If Doc2X outputs already exist, they are reused.
  - If md2json_output contains resumable artifacts, md2json runs with resume=true.
  - Set FORCE_RERUN=true to ignore existing outputs and rerun from scratch.

Environment loading:
  - This script automatically sources: ${ENV_FILE}
  - Override with MD2JSON_ENV_FILE=/path/to/file

Required environment after loading:
  DOC2X_API_KEY
  For MD2JSON_BACKEND=openai: OPENAI_API_KEY
  For MD2JSON_BACKEND=azure:  AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT

Optional environment:
  PYTHON_BIN
  MD2JSON_BACKEND
  MD2JSON_MODEL
  OPENAI_BASE_URL
  AZURE_OPENAI_ENDPOINT
  AZURE_OPENAI_API_VERSION
  MD2JSON_LLM_TIMEOUT
  DOC2X_MODEL
  FORMULA_MODE
  FORMULA_LEVEL
  MERGE_CROSS_PAGE_FORMS
  PROMPT_PROFILE
  STRUCTURE_MODE
  AUDIT_MODE
  FORCE_RERUN
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ -z "${DOC2X_API_KEY:-}" ]]; then
  echo "DOC2X_API_KEY is required." >&2
  exit 1
fi

case "$MD2JSON_BACKEND" in
  openai)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      echo "OPENAI_API_KEY is required when MD2JSON_BACKEND=openai." >&2
      exit 1
    fi
    ;;
  azure)
    if [[ -z "${AZURE_OPENAI_API_KEY:-}" || -z "$AZURE_OPENAI_ENDPOINT" ]]; then
      echo "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are required when MD2JSON_BACKEND=azure." >&2
      exit 1
    fi
    ;;
  local|mock)
    ;;
  *)
    echo "Unsupported MD2JSON_BACKEND: $MD2JSON_BACKEND" >&2
    exit 1
    ;;
esac

mkdir -p "$RESULTS_DIR" "$LOGS_DIR"

slugify_relpath() {
  local path="$1"
  "$PYTHON_BIN" - "$path" <<'PY'
import re
import sys
from pathlib import Path

raw = Path(sys.argv[1]).as_posix()
raw = re.sub(r"\.pdf$", "", raw, flags=re.IGNORECASE)
raw = raw.replace("/", "__")
raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw)
raw = re.sub(r"_+", "_", raw).strip("_.")
print(raw or "document")
PY
}

run_one_pdf() {
  local pdf_path="$1"
  local artifact_dir="$2"
  local rel_path="$3"

  PYTHONPATH="$PROJECT_ROOT" \
  MD2JSON_BACKEND="$MD2JSON_BACKEND" \
  MD2JSON_MODEL="$MD2JSON_MODEL" \
  MD2JSON_LLM_TIMEOUT="$MD2JSON_LLM_TIMEOUT" \
  DOC2X_API_KEY="$DOC2X_API_KEY" \
  DOC2X_BASE_URL="${DOC2X_BASE_URL:-https://v2.doc2x.noedgeai.com}" \
  DOC2X_TIMEOUT="${DOC2X_TIMEOUT:-600}" \
  DOC2X_POLL_INTERVAL="${DOC2X_POLL_INTERVAL:-2}" \
  DOC2X_MODEL="$DOC2X_MODEL" \
  FORMULA_MODE="$FORMULA_MODE" \
  FORMULA_LEVEL="$FORMULA_LEVEL" \
  MERGE_CROSS_PAGE_FORMS="$MERGE_CROSS_PAGE_FORMS" \
  PROMPT_PROFILE="$PROMPT_PROFILE" \
  STRUCTURE_MODE="$STRUCTURE_MODE" \
  AUDIT_MODE="$AUDIT_MODE" \
  OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  OPENAI_BASE_URL="$OPENAI_BASE_URL" \
  AZURE_OPENAI_ENDPOINT="$AZURE_OPENAI_ENDPOINT" \
  AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY:-}" \
  AZURE_OPENAI_API_VERSION="$AZURE_OPENAI_API_VERSION" \
  FORCE_RERUN="$FORCE_RERUN" \
  "$PYTHON_BIN" - "$PROJECT_ROOT" "$pdf_path" "$artifact_dir" "$rel_path" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

project_root = Path(sys.argv[1])
pdf_path = Path(sys.argv[2]).resolve()
artifact_dir = Path(sys.argv[3]).resolve()
rel_path = sys.argv[4]

sys.path.insert(0, str(project_root))

from md2json_api.converter import ConverterConfig, MarkdownJsonConverter
from md2json_api.doc2x_client import Doc2XClient
from md2json_api.runtime import atomic_write_json

artifact_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(pdf_path, artifact_dir / pdf_path.name)

doc2x_dir = artifact_dir / "doc2x"
md2json_out_dir = artifact_dir / "md2json_output"
status_path = artifact_dir / "status.json"
meta_path = artifact_dir / "meta.json"
quality_export_path = artifact_dir / "quality.json"
usage_export_path = artifact_dir / "usage.json"
result_export_path = artifact_dir / "result.json"

backend = os.environ.get("MD2JSON_BACKEND", "openai")
model = os.environ.get("MD2JSON_MODEL", "gpt-5.2")
openai_api_key = os.environ.get("OPENAI_API_KEY") or None
openai_base_url = os.environ.get("OPENAI_BASE_URL") or None
azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or None
azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY") or None
azure_api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
llm_timeout = float(os.environ.get("MD2JSON_LLM_TIMEOUT", "600"))
force_rerun = os.environ.get("FORCE_RERUN", "false").lower() == "true"

doc2x_options = {
    "doc2x_model": os.environ.get("DOC2X_MODEL", "v3-2026"),
    "formula_mode": os.environ.get("FORMULA_MODE", "normal"),
    "formula_level": os.environ.get("FORMULA_LEVEL", "0"),
    "merge_cross_page_forms": os.environ.get("MERGE_CROSS_PAGE_FORMS", "false").lower() == "true",
}
md2json_options = {
    "prompt_profile": os.environ.get("PROMPT_PROFILE", "auto"),
    "structure_mode": os.environ.get("STRUCTURE_MODE", "auto"),
    "audit_mode": os.environ.get("AUDIT_MODE", "auto"),
}

def write_status(payload: dict) -> None:
    atomic_write_json(status_path, payload)

def write_meta(payload: dict) -> None:
    atomic_write_json(meta_path, payload)

def final_result_exists() -> bool:
    return result_export_path.exists() and quality_export_path.exists() and meta_path.exists()

def doc2x_ready() -> bool:
    return (
        (doc2x_dir / "output.md").exists()
        and (doc2x_dir / "pages.json").exists()
        and (doc2x_dir / "export_manifest.json").exists()
    )

def md2json_resume_requested() -> bool:
    if not md2json_out_dir.exists():
        return False
    if (md2json_out_dir / ".conversion_manifest.json").exists():
        return True
    return any(
        path.exists()
        for path in [
            md2json_out_dir / "api_calls",
            md2json_out_dir / "audit_api_calls",
            md2json_out_dir / "mock_api_calls",
            md2json_out_dir / "mock_audit_api_calls",
            md2json_out_dir / "structure_api_call" / "response.json",
            md2json_out_dir / "mock_structure_api_call" / "response.json",
        ]
    )

if final_result_exists() and not force_rerun:
    write_status(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "succeeded",
            "phase": "completed",
            "doc2x_progress": 100,
            "sections_total": None,
            "sections_completed": None,
            "skipped": True,
        }
    )
    write_meta(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "succeeded",
            "phase": "completed",
            "skipped": True,
            "reason": "final result already exists",
            "doc2x_options": doc2x_options,
            "md2json_options": md2json_options,
            "backend": backend,
            "model": model,
        }
    )
    raise SystemExit(10)

write_status(
    {
        "source_relative_path": rel_path,
        "input_name": pdf_path.name,
        "status": "running",
        "phase": "starting",
        "doc2x_progress": None,
        "sections_total": None,
        "sections_completed": 0,
    }
)

started_at = time.monotonic()
doc2x_progress_holder = {"value": None}

try:
    if doc2x_ready() and not force_rerun:
        doc2x_markdown_path = doc2x_dir / "output.md"
        doc2x_json_path = doc2x_dir / "pages.json"
        doc2x_manifest_path = doc2x_dir / "export_manifest.json"
        doc2x_reused = True
        doc2x_progress_holder["value"] = 100
        write_status(
            {
                "source_relative_path": rel_path,
                "input_name": pdf_path.name,
                "status": "running",
                "phase": "doc2x_reused",
                "doc2x_progress": 100,
                "sections_total": None,
                "sections_completed": 0,
            }
        )
    else:
        client = Doc2XClient()

        def on_doc2x_progress(phase: str, progress: int | None) -> None:
            doc2x_progress_holder["value"] = progress
            write_status(
                {
                    "source_relative_path": rel_path,
                    "input_name": pdf_path.name,
                    "status": "running",
                    "phase": f"doc2x_{phase}",
                    "doc2x_progress": progress,
                    "sections_total": None,
                    "sections_completed": 0,
                }
            )

        doc2x_result = client.convert_file(
            source_file=pdf_path,
            output_dir=doc2x_dir,
            options=doc2x_options,
            progress_callback=on_doc2x_progress,
        )
        doc2x_markdown_path = doc2x_result.markdown_path
        doc2x_json_path = doc2x_result.json_path
        doc2x_manifest_path = doc2x_result.manifest_path
        doc2x_reused = False
        doc2x_progress_holder["value"] = 100

    resume_md2json = md2json_resume_requested() and not force_rerun
    write_status(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "running",
            "phase": "md2json_starting",
            "doc2x_progress": doc2x_progress_holder["value"],
            "sections_total": None,
            "sections_completed": 0,
            "resume": resume_md2json,
        }
    )

    converter = MarkdownJsonConverter(
        ConverterConfig(
            backend=backend,
            model=model,
            api_key=azure_api_key if backend == "azure" else openai_api_key,
            base_url=openai_base_url,
            azure_endpoint=azure_endpoint,
            azure_api_version=azure_api_version,
            llm_timeout=llm_timeout,
            prompt_profile=md2json_options["prompt_profile"],
            structure_mode=md2json_options["structure_mode"],
            audit_mode=md2json_options["audit_mode"],
            resume=resume_md2json,
        )
    )

    def on_md2json_progress(progress: dict) -> None:
        write_status(
            {
                "source_relative_path": rel_path,
                "input_name": pdf_path.name,
                "status": "running",
                "phase": f"md2json_{progress['phase']}",
                "doc2x_progress": doc2x_progress_holder["value"],
                "sections_total": int(progress["sections_total"]),
                "sections_completed": int(progress["sections_completed"]),
                "resume": resume_md2json,
            }
        )

    result = converter.convert(
        doc2x_markdown_path,
        md2json_out_dir,
        progress_callback=on_md2json_progress,
    )

    result_path = result.out_dir / f"{result.source_file.stem}.json"
    quality_path = result.out_dir / "quality_report.json"
    usage_path = result.out_dir / "usage_summary.json"

    shutil.copy2(result_path, result_export_path)
    shutil.copy2(quality_path, quality_export_path)
    if usage_path.exists():
        shutil.copy2(usage_path, usage_export_path)
    else:
        atomic_write_json(usage_export_path, {})

    elapsed = round(max(0.0, time.monotonic() - started_at), 6)
    write_status(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "succeeded",
            "phase": "completed",
            "doc2x_progress": 100,
            "sections_total": result.sections_written,
            "sections_completed": result.sections_written,
            "resume": resume_md2json,
        }
    )
    write_meta(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "succeeded",
            "phase": "completed",
            "doc2x_reused": doc2x_reused,
            "doc2x_options": doc2x_options,
            "md2json_options": md2json_options,
            "md2json_resume": resume_md2json,
            "backend": backend,
            "model": model,
            "elapsed_seconds": elapsed,
            "doc2x_markdown_path": str(doc2x_markdown_path),
            "doc2x_json_path": str(doc2x_json_path),
            "doc2x_manifest_path": str(doc2x_manifest_path),
            "md2json_output_dir": str(result.out_dir),
            "result_path": str(result_export_path),
            "quality_path": str(quality_export_path),
            "usage_path": str(usage_export_path),
            "items_total": result.items_total,
            "sections_written": result.sections_written,
        }
    )
except Exception as exc:
    write_status(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "failed",
            "phase": "failed",
            "doc2x_progress": doc2x_progress_holder["value"],
            "sections_total": None,
            "sections_completed": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    )
    write_meta(
        {
            "source_relative_path": rel_path,
            "input_name": pdf_path.name,
            "status": "failed",
            "phase": "failed",
            "doc2x_options": doc2x_options,
            "md2json_options": md2json_options,
            "backend": backend,
            "model": model,
            "error": f"{type(exc).__name__}: {exc}",
        }
    )
    raise
PY
}

main() {
  local total=0
  local succeeded=0
  local failed=0
  local skipped=0
  local run_id
  run_id="$(date -u +%Y%m%dT%H%M%SZ)"

  local book_dir
  for book_dir in "${BOOK_DIRS[@]}"; do
    local source_dir="$INPUT_ROOT/$book_dir"
    if [[ ! -d "$source_dir" ]]; then
      echo "Input directory not found: $source_dir" >&2
      exit 1
    fi

    while IFS= read -r -d '' pdf_path; do
      total=$((total + 1))

      local rel_path
      rel_path="${pdf_path#${INPUT_ROOT}/}"
      local slug
      slug="$(slugify_relpath "$rel_path")"
      local artifact_dir="$RESULTS_DIR/$slug"
      local log_path="$LOGS_DIR/${slug}.log"
      mkdir -p "$artifact_dir"

      echo "[$total] run $rel_path" | tee "$log_path"
      set +e
      run_one_pdf "$pdf_path" "$artifact_dir" "$rel_path" >>"$log_path" 2>&1
      rc=$?
      set -e

      if [[ $rc -eq 0 ]]; then
        succeeded=$((succeeded + 1))
        echo "done $rel_path -> $artifact_dir" | tee -a "$log_path"
      elif [[ $rc -eq 10 ]]; then
        skipped=$((skipped + 1))
        echo "skip $rel_path -> $artifact_dir" | tee -a "$log_path"
      else
        failed=$((failed + 1))
        echo "failed $rel_path -> $artifact_dir" | tee -a "$log_path"
      fi
    done < <(find "$source_dir" -maxdepth 1 -type f -name '*.pdf' -print0 | sort -z)
  done

  local summary_path="$OUTPUT_ROOT/summary.json"
  "$PYTHON_BIN" - "$summary_path" "$run_id" "$total" "$succeeded" "$failed" "$skipped" <<'PY'
import json
import sys
from pathlib import Path

summary_path = Path(sys.argv[1])
payload = {
    "run_id": sys.argv[2],
    "total": int(sys.argv[3]),
    "succeeded": int(sys.argv[4]),
    "failed": int(sys.argv[5]),
    "skipped": int(sys.argv[6]),
}
summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

  echo "run finished: total=$total succeeded=$succeeded failed=$failed skipped=$skipped results=$RESULTS_DIR"
}

main "$@"
