from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import atomic_write_json


def build_usage_summary(*, out_dir: Path, started_at: float | None = None, ended_at: float | None = None) -> dict[str, Any]:
    phases = {
        "structure": _summarize_trace_group(out_dir / "structure_api_call"),
        "extract": _summarize_trace_group(out_dir / "api_calls"),
        "audit": _summarize_trace_group(out_dir / "audit_api_calls"),
        "mock_structure": _summarize_trace_group(out_dir / "mock_structure_api_call"),
        "mock_extract": _summarize_trace_group(out_dir / "mock_api_calls"),
        "mock_audit": _summarize_trace_group(out_dir / "mock_audit_api_calls"),
    }
    total_requests = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_llm_elapsed_seconds = 0.0
    for summary in phases.values():
        total_requests += int(summary["requests"])
        total_input_tokens += int(summary["input_tokens"])
        total_output_tokens += int(summary["output_tokens"])
        total_tokens += int(summary["total_tokens"])
        total_llm_elapsed_seconds += float(summary["elapsed_seconds"])
    payload: dict[str, Any] = {
        "requests": total_requests,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "llm_elapsed_seconds": round(total_llm_elapsed_seconds, 6),
        "phases": phases,
    }
    if started_at is not None and ended_at is not None:
        payload["wall_clock_elapsed_seconds"] = round(max(0.0, ended_at - started_at), 6)
    return payload


def write_usage_summary(*, out_dir: Path, started_at: float | None = None, ended_at: float | None = None) -> dict[str, Any]:
    payload = build_usage_summary(out_dir=out_dir, started_at=started_at, ended_at=ended_at)
    atomic_write_json(out_dir / "usage_summary.json", payload)
    return payload


def _summarize_trace_group(path: Path) -> dict[str, Any]:
    files = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(candidate for candidate in path.rglob("*.json") if candidate.is_file())
    requests = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    elapsed_seconds = 0.0
    for file_path in files:
        payload = _read_json(file_path)
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        requests += 1
        input_tokens += _as_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        output_tokens += _as_int(usage.get("output_tokens") or usage.get("completion_tokens"))
        total_tokens += _as_int(usage.get("total_tokens"))
        elapsed_seconds += _elapsed_seconds(payload)
    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _elapsed_seconds(payload: dict[str, Any]) -> float:
    if isinstance(payload.get("elapsed_seconds"), (int, float)):
        return float(payload["elapsed_seconds"])
    timing = payload.get("timing")
    if isinstance(timing, dict) and isinstance(timing.get("elapsed_seconds"), (int, float)):
        return float(timing["elapsed_seconds"])
    return 0.0


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
