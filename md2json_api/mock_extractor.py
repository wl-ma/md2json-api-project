from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .local_extractor import LocalSectionExtractor
from .models import MarkdownSection
from .prompts import build_section_prompt, build_system_prompt
from .schema import chat_json_schema_response_format


class MockApiSectionExtractor:
    """Simulate the API boundary while keeping the full converter pipeline intact."""

    def __init__(self, *, model: str = "mock-api-worker", trace_dir: Path | None = None, prompt_profile: str = "auto") -> None:
        self.model = model
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self.local = LocalSectionExtractor()

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    def extract_section(self, section: MarkdownSection) -> list[dict[str, Any]]:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_section_prompt(section, self.prompt_profile)},
            ],
            "response_format": chat_json_schema_response_format(),
        }
        local_items = self.local.extract_section(section)
        api_items = [_as_api_item(item, section) for item in local_items]
        response_payload = {"items": api_items}
        self._write_trace(section, request_payload, response_payload)
        return response_payload["items"]

    def _write_trace(self, section: MarkdownSection, request_payload: dict[str, Any], response_payload: dict[str, Any]) -> None:
        if self.trace_dir is None:
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        stem = f"section{section.index:02d}"
        combined_payload = {
            "section_index": section.index,
            "context": section.context.as_json(),
            "provider_shape": "azure_chat_completions",
            "request": request_payload,
            "response_text": json.dumps(response_payload, ensure_ascii=False),
            "response_json": response_payload,
        }
        (self.trace_dir / f"{stem}.json").write_text(
            json.dumps(combined_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.trace_dir / f"{stem}_request.json").write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.trace_dir / f"{stem}_response.json").write_text(
            json.dumps(response_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _as_api_item(item: dict[str, Any], section: MarkdownSection) -> dict[str, Any]:
    api_item = copy.deepcopy(item)
    content = str(api_item.get("content") or "")
    proof = api_item.get("proof")
    return {
        "label": str(api_item.get("label") or ""),
        "env": api_item.get("env"),
        "number_components": [str(part) for part in api_item.get("number_components", [])],
        "dependencies": [str(dep) for dep in api_item.get("dependencies", [])],
        "source_order_anchor": content,
        "content_span": _span_for_text(section.text, content),
        "proof_span": _span_for_text(section.text, str(proof), occurrence=1) if proof is not None else None,
    }


def _span_for_text(source: str, text: str, *, occurrence: int | None = None) -> dict[str, Any]:
    if not text:
        raise RuntimeError("Mock API item cannot build an empty source span.")
    start = source.find(text)
    if start < 0:
        raise RuntimeError(f"Mock API item text is not present in the source: {text[:80]!r}")
    if occurrence is None:
        occurrence = source[:start].count(text) + 1
    return {
        "start_anchor": text,
        "end_anchor": text,
        "start_occurrence": occurrence,
        "end_occurrence": occurrence,
        "include_start": True,
        "include_end": True,
    }
