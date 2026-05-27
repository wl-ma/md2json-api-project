from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .prompts import STRUCTURE_SYSTEM_PROMPT, build_structure_prompt
from .runtime import atomic_write_json
from .schema import (
    chat_structure_plan_json_schema_response_format,
    responses_structure_plan_json_schema_format,
)
from .splitter import SplitPlan, parse_chapter_heading, parse_section_heading
from .structure import HEADING_RE, build_structure_candidates, looks_like_bare_section_candidate


class NoopStructurePlanner:
    def plan_document(
        self,
        *,
        source_name: str,
        source_text: str,
        hard_plan: SplitPlan,
        prompt_profile: str,
    ) -> dict[str, Any] | None:
        return None


class MockStructurePlanner:
    def __init__(self, *, model: str = "mock-api-worker", trace_dir: Path | None = None) -> None:
        self.model = model
        self.trace_dir = trace_dir

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    def plan_document(
        self,
        *,
        source_name: str,
        source_text: str,
        hard_plan: SplitPlan,
        prompt_profile: str,
    ) -> dict[str, Any]:
        candidates = build_structure_candidates(source_text)
        request = _request_payload(
            model=self.model,
            source_name=source_name,
            source_text=source_text,
            hard_plan=hard_plan,
            candidates=candidates,
            prompt_profile=prompt_profile,
            provider="azure",
        )
        payload = _heuristic_plan(source_name=source_name, source_text=source_text, hard_plan=hard_plan)
        self._write_trace(request, json.dumps(payload, ensure_ascii=False), payload, usage=None)
        return payload

    def _write_trace(
        self,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(self.trace_dir, request_payload, response_text, response_payload, usage=usage, provider_shape="mock")


class OpenAIStructurePlanner:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int | None = None,
        timeout: float = 600,
        trace_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.trace_dir = trace_dir
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
            kwargs: dict[str, Any] = {"timeout": self.timeout}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def plan_document(
        self,
        *,
        source_name: str,
        source_text: str,
        hard_plan: SplitPlan,
        prompt_profile: str,
    ) -> dict[str, Any]:
        cached = _read_cached_response(self.trace_dir)
        if cached is not None:
            return cached
        candidates = build_structure_candidates(source_text)
        request = _request_payload(
            model=self.model,
            source_name=source_name,
            source_text=source_text,
            hard_plan=hard_plan,
            candidates=candidates,
            prompt_profile=prompt_profile,
            provider="openai",
        )
        if self.max_output_tokens:
            request["max_output_tokens"] = self.max_output_tokens
        response = _with_retries(lambda: self.client.responses.create(**request))
        output_text = getattr(response, "output_text", None) or _collect_response_text(response)
        payload = _parse_structure_payload(output_text)
        self._write_trace(request, output_text, payload, usage=_response_usage(response))
        return payload

    def _write_trace(
        self,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(self.trace_dir, request_payload, response_text, response_payload, usage=usage, provider_shape="openai_responses")


class AzureChatStructurePlanner:
    def __init__(
        self,
        *,
        model: str,
        azure_endpoint: str,
        api_version: str,
        api_key: str | None = None,
        max_output_tokens: int | None = None,
        timeout: float = 600,
        trace_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.trace_dir = trace_dir
        self._client = None

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import AzureOpenAI
            except ModuleNotFoundError:
                self._client = False
                return self._client
            kwargs: dict[str, Any] = {
                "azure_endpoint": self.azure_endpoint,
                "api_version": self.api_version,
                "timeout": self.timeout,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            self._client = AzureOpenAI(**kwargs)
        return self._client

    def plan_document(
        self,
        *,
        source_name: str,
        source_text: str,
        hard_plan: SplitPlan,
        prompt_profile: str,
    ) -> dict[str, Any]:
        cached = _read_cached_response(self.trace_dir)
        if cached is not None:
            return cached
        candidates = build_structure_candidates(source_text)
        request = _request_payload(
            model=self.model,
            source_name=source_name,
            source_text=source_text,
            hard_plan=hard_plan,
            candidates=candidates,
            prompt_profile=prompt_profile,
            provider="azure",
        )
        if self.max_output_tokens:
            request["max_tokens"] = self.max_output_tokens
        usage = None
        if self.client is False:
            output_text = _with_retries(lambda: self._plan_via_rest(request))
        else:
            response = _with_retries(lambda: self.client.chat.completions.create(**request))
            output_text = response.choices[0].message.content or ""
            usage = _response_usage(response)
        payload = _parse_structure_payload(output_text)
        self._write_trace(request, output_text, payload, usage=usage)
        return payload

    def _plan_via_rest(self, request_payload: dict[str, Any]) -> str:
        if not self.api_key:
            raise RuntimeError("Azure REST fallback requires an API key.")
        endpoint = self.azure_endpoint.rstrip("/")
        deployment = urllib.parse.quote(self.model, safe="")
        api_version = urllib.parse.quote(self.api_version, safe="")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        body = json.dumps(request_payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Azure OpenAI structure HTTP {exc.code}: {error_body[:1000]}") from exc
        return payload["choices"][0]["message"].get("content") or ""

    def _write_trace(
        self,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(self.trace_dir, request_payload, response_text, response_payload, usage=usage, provider_shape="azure_chat_completions")


def _request_payload(
    *,
    model: str,
    source_name: str,
    source_text: str,
    hard_plan: SplitPlan,
    candidates: list[dict[str, Any]],
    prompt_profile: str,
    provider: str,
) -> dict[str, Any]:
    source_line_count = len(source_text.splitlines())
    prompt = build_structure_prompt(
        source_name=source_name,
        source_line_count=source_line_count,
        hard_sections=hard_plan.sections,
        hard_warnings=hard_plan.warnings,
        candidates=candidates,
        prompt_profile=prompt_profile,
    )
    if provider == "openai":
        return {
            "model": model,
            "input": [
                {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "text": {"format": responses_structure_plan_json_schema_format()},
        }
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": chat_structure_plan_json_schema_response_format(),
    }


def _heuristic_plan(*, source_name: str, source_text: str, hard_plan: SplitPlan) -> dict[str, Any]:
    lines = source_text.splitlines()
    chapter = hard_plan.sections[0].context.chapter if hard_plan.sections else Path(source_name).stem
    chapter_number = hard_plan.sections[0].context.chapter_number if hard_plan.sections else ""
    starts: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines, start=1):
        raw = line.strip()
        heading = HEADING_RE.match(raw)
        text = heading.group(2).strip() if heading else raw
        parsed_chapter = parse_chapter_heading(text)
        if parsed_chapter is not None:
            chapter_number, chapter = parsed_chapter
            continue
        if not heading and not looks_like_bare_section_candidate(text, chapter_number=chapter_number):
            continue
        parsed = parse_section_heading(text, chapter_number=chapter_number)
        if parsed is None:
            continue
        starts.append((index, parsed[0], parsed[1], line.strip()))
    starts = _collapse_single_section_excerpt(starts)
    sections = []
    for position, (start_line, number, title, heading) in enumerate(starts):
        end_line = (starts[position + 1][0] - 1) if position + 1 < len(starts) else len(lines)
        sections.append(
            {
                "section_number": number,
                "section_title": title,
                "start_line": start_line,
                "end_line": end_line,
                "heading_source": heading,
                "confidence": "medium",
                "reason": "mock heuristic structure plan",
            }
        )
    if not sections:
        for section in hard_plan.sections:
            sections.append(
                {
                    "section_number": section.context.section_number,
                    "section_title": section.context.section,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "heading_source": section.source_heading,
                    "confidence": "low",
                    "reason": "mock fallback to hard splitter",
                }
            )
    first_start = sections[0]["start_line"] if sections else 1
    front_matter_ranges = []
    if first_start > 1:
        front_matter_ranges.append({"start_line": 1, "end_line": first_start - 1, "reason": "material before first section"})
    return {
        "document_title": chapter,
        "chapter": chapter,
        "chapter_number": chapter_number,
        "front_matter_ranges": front_matter_ranges,
        "sections": sections,
        "back_matter_ranges": [],
        "warnings": ["mock structure planner used"],
    }


def _collapse_single_section_excerpt(starts: list[tuple[int, str, str, str]]) -> list[tuple[int, str, str, str]]:
    if len(starts) <= 1:
        return starts
    parent_number = starts[0][1]
    if all(_is_descendant_section(parent_number, section_number) for _line, section_number, _title, _heading in starts[1:]):
        return starts[:1]
    return starts


def _is_descendant_section(parent_number: str, child_number: str) -> bool:
    if not parent_number or not child_number:
        return False
    return child_number.startswith(parent_number + ".")


def _parse_structure_payload(output_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Structure planner response was not valid JSON: {output_text[:500]}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), list):
        raise RuntimeError(f"Structure planner response missing sections: {payload!r}")
    return payload


def _read_cached_response(trace_dir: Path | None) -> dict[str, Any] | None:
    if trace_dir is None:
        return None
    path = trace_dir / "response.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) and isinstance(payload.get("sections"), list) else None


def _write_trace(
    trace_dir: Path,
    request_payload: dict[str, Any],
    response_text: str,
    response_payload: dict[str, Any],
    *,
    usage: dict[str, Any] | None,
    provider_shape: str,
) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    combined = {
        "provider_shape": provider_shape,
        "request": request_payload,
        "response_text": response_text,
        "response_json": response_payload,
        "usage": usage,
    }
    atomic_write_json(trace_dir / "call.json", combined)
    atomic_write_json(trace_dir / "request.json", request_payload)
    atomic_write_json(trace_dir / "response.json", response_payload)


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


def _response_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    output: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            output[key] = value
    return output or None
