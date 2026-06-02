#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR="$SCRIPT_DIR"
PARSERBENCH_DIR=$(cd -- "$REPO_DIR/../parserbench" && pwd)
RESULTS_ROOT="$PARSERBENCH_DIR/results/md2json-api-project"
PYTHON_BIN=${PYTHON_BIN:-"$REPO_DIR/.venv/bin/python"}
DOC2X_MODEL=${DOC2X_MODEL:-v3-2026}
FORMULA_MODE=${FORMULA_MODE:-normal}
FORMULA_LEVEL=${FORMULA_LEVEL:-0}
MERGE_CROSS_PAGE_FORMS=${MERGE_CROSS_PAGE_FORMS:-false}
PROMPT_PROFILE=${PROMPT_PROFILE:-auto}
STRUCTURE_MODE=${STRUCTURE_MODE:-auto}
AUDIT_MODE=${AUDIT_MODE:-off}
RESUME_AUDIT_MODE=${RESUME_AUDIT_MODE:-off}
MD2JSON_BACKEND=${MD2JSON_BACKEND:-openai}
MD2JSON_MODEL=${MD2JSON_MODEL:-gpt-5.2}
ONLY_BOOK=${ONLY_BOOK:-}
SKIP_DOC2X=${SKIP_DOC2X:-false}
ENV_FILE=${MD2JSON_ENV_FILE:-/etc/md2json/md2json.env}

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

MD2JSON_BACKEND=${MD2JSON_BACKEND:-openai}
MD2JSON_MODEL=${MD2JSON_MODEL:-gpt-5.2}

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ -z "${DOC2X_API_KEY:-}" ]]; then
  echo "DOC2X_API_KEY is not set." >&2
  exit 1
fi

if [[ "$MD2JSON_BACKEND" == "openai" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is not set for MD2JSON_BACKEND=openai." >&2
  exit 1
fi

if [[ "$MD2JSON_BACKEND" == "azure" ]]; then
  if [[ -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
    echo "AZURE_OPENAI_API_KEY is not set for MD2JSON_BACKEND=azure." >&2
    exit 1
  fi
  if [[ -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
    echo "AZURE_OPENAI_ENDPOINT is not set for MD2JSON_BACKEND=azure." >&2
    exit 1
  fi
fi

mkdir -p "$RESULTS_ROOT"

shopt -s nullglob
pdfs=("$PARSERBENCH_DIR"/*.pdf)
shopt -u nullglob

if [[ ${#pdfs[@]} -ne 10 ]]; then
  echo "Expected 10 parserbench PDFs, found ${#pdfs[@]} under $PARSERBENCH_DIR" >&2
  exit 1
fi

succeeded=0
failed=0
skipped=0

for pdf in "${pdfs[@]}"; do
  name=$(basename "$pdf" .pdf)
  if [[ -n "$ONLY_BOOK" && "$name" != "$ONLY_BOOK" ]]; then
    continue
  fi
  out_dir="$RESULTS_ROOT/$name"
  doc2x_dir="$out_dir"
  md2json_dir="$out_dir/md2json"
  log_path="$out_dir/resume_run.log"

  echo "=== [$name] checking ==="
  mkdir -p "$out_dir"

  if [[ -f "$md2json_dir/output.json" ]]; then
    echo "[$name] skip: final md2json output already exists"
    skipped=$((skipped + 1))
    continue
  fi

  doc2x_ok=false
  if [[ -f "$doc2x_dir/output.md" && -f "$doc2x_dir/pages.json" ]]; then
    doc2x_ok=true
  fi

  if [[ "$SKIP_DOC2X" == "true" ]]; then
    if [[ "$doc2x_ok" == true ]]; then
      echo "[$name] SKIP_DOC2X=true, reusing existing Doc2X outputs" | tee -a "$log_path"
    else
      echo "[$name] SKIP_DOC2X=true but existing Doc2X outputs are missing; continuing" | tee -a "$log_path"
      failed=$((failed + 1))
      continue
    fi
  elif [[ "$doc2x_ok" != true ]]; then
    echo "[$name] running Doc2X" | tee -a "$log_path"
    tmp_single_dir=$(mktemp -d)
    cp -f "$pdf" "$tmp_single_dir/"
    doc2x_cmd=(
      "$PYTHON_BIN" "$REPO_DIR/scripts/doc2x_watch_folder.py"
      --input-dir "$tmp_single_dir"
      --output-dir "$RESULTS_ROOT"
      --doc2x-model "$DOC2X_MODEL"
      --formula-mode "$FORMULA_MODE"
      --formula-level "$FORMULA_LEVEL"
      --stable-seconds 0
    )
    if [[ "$MERGE_CROSS_PAGE_FORMS" == "true" ]]; then
      doc2x_cmd+=(--merge-cross-page-forms)
    fi
    if ! "${doc2x_cmd[@]}" >>"$log_path" 2>&1; then
      echo "[$name] Doc2X failed; continuing" | tee -a "$log_path"
      rm -rf "$tmp_single_dir"
      failed=$((failed + 1))
      continue
    fi
    rm -rf "$tmp_single_dir"
  else
    echo "[$name] reuse existing Doc2X outputs" | tee -a "$log_path"
  fi

  if [[ ! -f "$doc2x_dir/output.md" ]]; then
    echo "[$name] missing output.md after Doc2X; continuing" | tee -a "$log_path"
    failed=$((failed + 1))
    continue
  fi

  mkdir -p "$md2json_dir"
  audit_mode_to_use="$AUDIT_MODE"
  structure_mode_to_use="$STRUCTURE_MODE"
  prompt_profile_to_use="$PROMPT_PROFILE"
  if [[ -d "$md2json_dir/api_calls" || -d "$md2json_dir/audit_api_calls" || -f "$md2json_dir/.conversion_manifest.json" ]]; then
    audit_mode_to_use="$RESUME_AUDIT_MODE"
    structure_mode_to_use="hard"
    prompt_profile_to_use="auto"
    echo "[$name] detected partial md2json outputs; retrying with audit_mode=$audit_mode_to_use structure_mode=$structure_mode_to_use prompt_profile=$prompt_profile_to_use" | tee -a "$log_path"
  else
    echo "[$name] running md2json with audit_mode=$audit_mode_to_use structure_mode=$structure_mode_to_use prompt_profile=$prompt_profile_to_use" | tee -a "$log_path"
  fi

  convert_args=(
    -m md2json_api.cli convert
    "$doc2x_dir/output.md"
    --out-dir "$md2json_dir"
    --backend "$MD2JSON_BACKEND"
    --model "$MD2JSON_MODEL"
    --prompt-profile "$prompt_profile_to_use"
    --structure-mode "$structure_mode_to_use"
    --audit-mode "$audit_mode_to_use"
  )
  if [[ -n "${OPENAI_BASE_URL:-}" ]]; then
    convert_args+=(--base-url "$OPENAI_BASE_URL")
  fi
  if [[ -n "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
    convert_args+=(--azure-endpoint "$AZURE_OPENAI_ENDPOINT")
  fi
  if [[ -n "${AZURE_OPENAI_API_VERSION:-}" ]]; then
    convert_args+=(--azure-api-version "$AZURE_OPENAI_API_VERSION")
  fi

  if ! "$PYTHON_BIN" "${convert_args[@]}" >>"$log_path" 2>&1; then
    echo "[$name] md2json failed; continuing" | tee -a "$log_path"
    failed=$((failed + 1))
    continue
  fi

  if [[ ! -f "$md2json_dir/output.json" ]]; then
    echo "[$name] md2json finished without output.json; continuing" | tee -a "$log_path"
    failed=$((failed + 1))
    continue
  fi

  cp -f "$pdf" "$out_dir/"
  echo "=== [$name] done -> $out_dir ==="
  succeeded=$((succeeded + 1))
done

echo "Resume run finished. succeeded=$succeeded failed=$failed skipped=$skipped results=$RESULTS_ROOT"
