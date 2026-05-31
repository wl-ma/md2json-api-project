from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .audit_tools import _extract_span_from_source
from .models import MarkdownSection


@dataclass(frozen=True)
class SpanBuildResult:
    items: list[dict[str, Any]]
    diagnostics: dict[str, Any]


class SpanBuildError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


def build_items_from_source_spans(raw_items: list[dict[str, Any]], section: MarkdownSection) -> SpanBuildResult:
    items: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "section_index": section.index,
        "section_number": section.context.section_number,
        "raw_items": [],
        "span_builder": [],
    }
    for index, raw in enumerate(raw_items, start=1):
        label = str(raw.get("label") or "").strip()
        diagnostics["raw_items"].append(_raw_item_trace(raw))
        item_diagnostic: dict[str, Any] = {
            "label": label or str(index),
            "content": {"ok": None},
            "proof": {"ok": None},
        }
        diagnostics["span_builder"].append(item_diagnostic)

        content_span = raw.get("content_span")
        if not isinstance(content_span, dict):
            item_diagnostic["content"] = {"ok": False, "error": "missing content_span"}
            raise SpanBuildError(f"Extractor item {label or index} missing content_span.", diagnostics)
        content_result = _extract_span_from_source(section, content_span)
        item_diagnostic["content"] = _span_result_trace(content_result)
        if not content_result.get("found"):
            raise SpanBuildError(
                f"Could not extract content span for {label or index}: {content_result.get('error')}",
                diagnostics,
            )

        proof_text = None
        proof_span = raw.get("proof_span")
        if proof_span is not None:
            if not isinstance(proof_span, dict):
                item_diagnostic["proof"] = {"ok": False, "error": "invalid proof_span"}
                raise SpanBuildError(f"Extractor item {label or index} has invalid proof_span.", diagnostics)
            proof_result = _extract_span_from_source(
                section,
                proof_span,
                search_start=int(content_result["char_end"]),
            )
            item_diagnostic["proof"] = _span_result_trace(proof_result)
            if not proof_result.get("found"):
                raise SpanBuildError(
                    f"Could not extract proof span for {label or index}: {proof_result.get('error')}",
                    diagnostics,
                )
            proof_text = str(proof_result.get("text") or "").strip() or None
        else:
            item_diagnostic["proof"] = {"ok": True, "span": None}

        content = str(content_result.get("text") or "").strip()
        if not content:
            item_diagnostic["content"]["ok"] = False
            item_diagnostic["content"]["error"] = "empty content span"
            raise SpanBuildError(f"Extractor item {label or index} produced an empty content span.", diagnostics)

        items.append(
            {
                "index": index,
                "label": label,
                "env": raw.get("env"),
                "number_components": [str(part) for part in raw.get("number_components") or []],
                "context": section.context.as_json(),
                "content": content,
                "dependencies": [str(dep) for dep in raw.get("dependencies") or []],
                "proof": proof_text,
            }
        )
    return SpanBuildResult(items=items, diagnostics=diagnostics)


def _raw_item_trace(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": str(raw.get("label") or ""),
        "env": raw.get("env"),
        "number_components": [str(part) for part in raw.get("number_components") or []],
        "dependencies": [str(dep) for dep in raw.get("dependencies") or []],
        "source_order_anchor": str(raw.get("source_order_anchor") or ""),
        "content_span": raw.get("content_span"),
        "proof_span": raw.get("proof_span"),
    }


def _span_result_trace(result: dict[str, Any]) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "ok": bool(result.get("found")),
    }
    if result.get("found"):
        trace.update(
            {
                "line_start": result.get("line_start"),
                "line_end": result.get("line_end"),
                "char_start": result.get("char_start"),
                "char_end": result.get("char_end"),
                "start_anchor_offset": result.get("start_anchor_offset"),
                "end_anchor_offset": result.get("end_anchor_offset"),
                "text_chars": len(str(result.get("text") or "")),
            }
        )
    else:
        trace["error"] = result.get("error")
    return trace
