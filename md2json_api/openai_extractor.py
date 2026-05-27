from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from .models import MarkdownSection
from .prompts import build_section_prompt, build_system_prompt
from .runtime import atomic_write_json
from .schema import responses_json_schema_format


class OpenAISectionExtractor:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int | None = None,
        timeout: float = 600,
        trace_dir: Path | None = None,
        prompt_profile: str = "auto",
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self._client = None

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "The openai package is not installed. Run: python3 -m pip install -r requirements.txt"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            kwargs["timeout"] = self.timeout
            self._client = OpenAI(**kwargs)
        return self._client

    def extract_section(self, section: MarkdownSection) -> list[dict[str, Any]]:
        cached = self._read_cached_response(section)
        if cached is not None:
            return cached
        request: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": build_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_section_prompt(section, self.prompt_profile)},
            ],
            "text": {"format": responses_json_schema_format()},
        }
        if self.max_output_tokens:
            request["max_output_tokens"] = self.max_output_tokens

        response = _with_retries(lambda: self.client.responses.create(**request))
        output_text = getattr(response, "output_text", None)
        if not output_text:
            output_text = _collect_response_text(response)
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid JSON: {output_text[:500]}") from exc
        self._write_trace(section, request, output_text, payload)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError(f"OpenAI response did not contain an items array: {payload!r}")
        return payload["items"]

    def _read_cached_response(self, section: MarkdownSection) -> list[dict[str, Any]] | None:
        if self.trace_dir is None:
            return None
        path = self.trace_dir / f"section{section.index:02d}_response.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        return None

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
    ) -> None:
        if self.trace_dir is None:
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        stem = f"section{section.index:02d}"
        combined = {
            "section_index": section.index,
            "context": section.context.as_json(),
            "provider_shape": "openai_responses",
            "request": request_payload,
            "response_text": response_text,
            "response_json": response_payload,
        }
        atomic_write_json(self.trace_dir / f"{stem}.json", combined)
        atomic_write_json(self.trace_dir / f"{stem}_request.json", request_payload)
        atomic_write_json(self.trace_dir / f"{stem}_response.json", response_payload)


def _collect_response_text(response: Any) -> str:
    pieces: list[str] = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                pieces.append(text)
    return "".join(pieces)


def _with_retries(fn, *, attempts: int = 3, delay: float = 5.0):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                raise
            time.sleep(delay * attempt)
    raise last_exc
