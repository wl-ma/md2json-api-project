from __future__ import annotations

import copy
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .audit_tools import AuditSourceToolExecutor, audit_source_tool_schemas
from .models import MarkdownSection
from .prompts import build_audit_repair_prompt, build_audit_repair_system_prompt
from .runtime import atomic_write_json
from .schema import (
    chat_audit_repair_json_schema_response_format,
)


class NoopSectionAuditRepairer:
    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
        *,
        extraction_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _noop_payload(section, current_items)


class MockSectionAuditRepairer(NoopSectionAuditRepairer):
    def __init__(self, *, model: str = "mock-api-worker", trace_dir: Path | None = None, prompt_profile: str = "auto") -> None:
        self.model = model
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile

    def set_trace_dir(self, trace_dir: Path) -> None:
        self.trace_dir = trace_dir

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
        *,
        extraction_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_audit_repair_system_prompt(self.prompt_profile, section)},
                {
                    "role": "user",
                    "content": build_audit_repair_prompt(
                        section,
                        current_items,
                        self.prompt_profile,
                        extraction_trace=extraction_trace,
                    ),
                },
            ],
            "response_format": chat_audit_repair_json_schema_response_format(),
        }
        response_payload = _noop_payload(section, current_items)
        self._write_trace(section, request_payload, json.dumps(response_payload, ensure_ascii=False), response_payload)
        return response_payload

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="mock_azure_chat_completions",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
        )


class OpenAISectionAuditRepairer:
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
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self.reasoning_effort = reasoning_effort
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

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
        *,
        extraction_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cached = _read_cached_audit_response(self.trace_dir, section)
        if cached is not None:
            return cached
        request, output_text, payload, usage = _run_chat_tool_audit(
            create_completion=lambda request_payload: self.client.chat.completions.create(**request_payload),
            model=self.model,
            section=section,
            current_items=current_items,
            extraction_trace=extraction_trace,
            prompt_profile=self.prompt_profile,
            max_output_tokens=self.max_output_tokens,
            max_tokens_key="max_completion_tokens",
            reasoning_effort=self.reasoning_effort,
        )
        self._write_trace(section, request, output_text, payload, usage=usage)
        return payload

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="openai_chat_completions_tools",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
            usage=usage,
        )


class AzureChatSectionAuditRepairer:
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
        prompt_profile: str = "auto",
        reasoning_effort: str | None = None,
    ) -> None:
        self.model = model
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self.api_key = api_key
        self.max_output_tokens = max_output_tokens
        self.timeout = timeout
        self.trace_dir = trace_dir
        self.prompt_profile = prompt_profile
        self.reasoning_effort = reasoning_effort
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

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict[str, Any]],
        *,
        extraction_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cached = _read_cached_audit_response(self.trace_dir, section)
        if cached is not None:
            return cached
        if self.client is False:
            create_completion = self._chat_completion_via_rest
        else:
            create_completion = lambda request_payload: self.client.chat.completions.create(**request_payload)
        request, output_text, payload, usage = _run_chat_tool_audit(
            create_completion=create_completion,
            model=self.model,
            section=section,
            current_items=current_items,
            extraction_trace=extraction_trace,
            prompt_profile=self.prompt_profile,
            max_output_tokens=self.max_output_tokens,
            max_tokens_key="max_completion_tokens" if self.reasoning_effort else "max_tokens",
            reasoning_effort=self.reasoning_effort,
        )
        self._write_trace(section, request, output_text, payload, usage=usage)
        return payload

    def _chat_completion_via_rest(self, request_payload: dict[str, Any]) -> dict[str, Any]:
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
            raise RuntimeError(f"Azure OpenAI audit HTTP {exc.code}: {error_body[:1000]}") from exc
        return payload

    def _write_trace(
        self,
        section: MarkdownSection,
        request_payload: dict[str, Any],
        response_text: str,
        response_payload: dict[str, Any],
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_dir is None:
            return
        _write_trace(
            self.trace_dir,
            section,
            provider_shape="azure_chat_completions_tools",
            request_payload=request_payload,
            response_text=response_text,
            response_payload=response_payload,
            usage=usage,
        )


def _run_chat_tool_audit(
    *,
    create_completion,
    model: str,
    section: MarkdownSection,
    current_items: list[dict[str, Any]],
    extraction_trace: dict[str, Any] | None,
    prompt_profile: str,
    max_output_tokens: int | None,
    max_tokens_key: str,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any] | None]:
    tools = audit_source_tool_schemas()
    executor = AuditSourceToolExecutor(section, current_items)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_audit_repair_system_prompt(prompt_profile, section)
            + "\n\n"
            + _TOOL_AUDIT_SYSTEM_INSTRUCTIONS,
        },
        {
            "role": "user",
            "content": build_audit_repair_prompt(
                section,
                current_items,
                prompt_profile,
                extraction_trace=extraction_trace,
            )
            + "\n\n"
            + _TOOL_AUDIT_USER_INSTRUCTIONS,
        },
    ]
    requests: list[dict[str, Any]] = []
    response_text_parts: list[str] = []
    usage: dict[str, Any] | None = None

    def send(tool_choice: dict[str, Any] | str = "auto") -> Any:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        if max_output_tokens:
            request[max_tokens_key] = max_output_tokens
        if reasoning_effort:
            request["reasoning_effort"] = reasoning_effort
        requests.append(copy.deepcopy(request))
        return _with_retries(lambda: create_completion(request))

    def handle(response: Any) -> bool:
        nonlocal usage
        usage = _merge_usage(usage, _response_usage(response))
        message = _choice_message(response)
        content = _message_content(message)
        if content:
            response_text_parts.append(content)
        tool_calls = _message_tool_calls(message)
        messages.append(_assistant_message_dict(message, tool_calls))
        if not tool_calls:
            return executor.final_payload is not None
        for tool_call in tool_calls:
            result_text = executor.execute_json(tool_call["name"], tool_call["arguments"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result_text,
                }
            )
            if tool_call["name"] == "build_repaired_items":
                response_text_parts.append(result_text)
        return executor.final_payload is not None

    if handle(send(_force_tool_choice("list_source_item_labels"))):
        pass
    else:
        for _ in range(6):
            if handle(send("auto")):
                break
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continue the audit with source tools. If you have enough evidence, call "
                        "build_repaired_items with the complete final item list."
                    ),
                }
            )

    if executor.final_payload is None:
        messages.append(
            {
                "role": "user",
                "content": (
                    "You must now call build_repaired_items. Include every final item that should remain in this "
                    "section; use preserve_current_label for unchanged source-backed items and source spans for "
                    "new or corrected content/proof."
                ),
            }
        )
        handle(send(_force_tool_choice("build_repaired_items")))

    if executor.final_payload is None:
        raise RuntimeError("Audit tool workflow ended without build_repaired_items.")

    payload = _parse_audit_payload(json.dumps(executor.final_payload, ensure_ascii=False), provider="audit source tools")
    payload["tool_trace"] = executor.trace
    request_record = {
        "model": model,
        "tool_workflow": "llm_declared_labels_source_span_tools",
        "tools": tools,
        "requests": requests,
        "final_messages": messages,
    }
    response_text = "\n".join(part for part in response_text_parts if part).strip()
    if not response_text:
        response_text = json.dumps(payload, ensure_ascii=False)
    return request_record, response_text, payload, usage


_TOOL_AUDIT_SYSTEM_INSTRUCTIONS = """Audit source tool workflow:
- You, not the tools, decide which mathematical items exist in the Markdown section.
- First call list_source_item_labels with the labels/items you identify by reading the Markdown. The tool only records your list and checks literal anchors; it does not mine labels with hard-coded theorem-name rules.
- Use search_source and extract_source_span to locate exact text spans for any content/proof that needs repair.
- Choose anchors that are long enough to be unique in the section: prefer the full visible item heading plus the opening words/formula for content spans. For proof spans, remember that include_start=false excludes the entire start_anchor; use it only when start_anchor is just the proof marker to remove, not proof-body text. If you include the first proof-body words in start_anchor, set include_start=true. Avoid generic anchors such as "In general," unless search_source shows the intended occurrence is unambiguous.
- Make source_order_anchor the exact visible heading or opening sentence of the item itself, not a nearby note, preliminary remark, or proof marker.
- Set source_order_occurrence to the 1-based occurrence of source_order_anchor from the beginning of the supplied Markdown section.
- Occurrence counts exact case-sensitive occurrences of the exact anchor string, not semantic/math-equivalent occurrences.
- In build_repaired_items, proof_span anchors are resolved after the item's content_span, and content/proof spans must not cross the next item's source_order_anchor.
- Dependencies are source-internal mathematical item labels only; do not put bibliography citations, bracketed references, author-year references, book/paper titles, page references, or theorem numbers from other works into dependencies.
- Do not handwrite repaired content/proof text. In the final build_repaired_items call, provide source spans for new or changed text so the tool copies from Markdown.
- build_repaired_items must include the complete final item array for the section, including unchanged items that should remain."""


_TOOL_AUDIT_USER_INSTRUCTIONS = """Use the available source tools rather than directly returning JSON.

Recommended sequence:
1. Call list_source_item_labels with your own source-item inventory.
2. Search/extract spans for missing, truncated, mislabeled, or proof-boundary issues.
3. Call build_repaired_items once, with all final items in source order."""


def _force_tool_choice(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


def _choice_message(response: Any) -> Any:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        return (choices[0] if choices else {}).get("message") or {}
    return response.choices[0].message


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", None) or "")


def _message_tool_calls(message: Any) -> list[dict[str, str]]:
    raw_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    output: list[dict[str, str]] = []
    for index, raw in enumerate(raw_calls or [], start=1):
        if isinstance(raw, dict):
            function = raw.get("function") or {}
            call_id = str(raw.get("id") or f"tool_call_{index}")
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "{}")
        else:
            function = getattr(raw, "function", None)
            call_id = str(getattr(raw, "id", None) or f"tool_call_{index}")
            name = str(getattr(function, "name", "") or "")
            arguments = str(getattr(function, "arguments", None) or "{}")
        if name:
            output.append({"id": call_id, "name": name, "arguments": arguments})
    return output


def _assistant_message_dict(message: Any, tool_calls: list[dict[str, str]]) -> dict[str, Any]:
    output: dict[str, Any] = {"role": "assistant", "content": _message_content(message) or None}
    if tool_calls:
        output["tool_calls"] = [
            {
                "id": tool_call["id"],
                "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": tool_call["arguments"],
                },
            }
            for tool_call in tool_calls
        ]
    return output


def _noop_payload(section: MarkdownSection, current_items: list[dict[str, Any]]) -> dict[str, Any]:
    section_id = f"section{section.index:02d}_{section.context.section_number}"
    return {
        "audit_markdown": (
            f"Section {section_id}: {section.context.section}\n\n"
            "Short verdict: no change\n\n"
            f"Current JSON summary: {len(current_items)} item(s).\n\n"
            "Findings: mock audit did not perform semantic review.\n\n"
            "Compact action summary: no actions."
        ),
        "patch_candidate": {
            "section_id": section_id,
            "overall_assessment": "no change",
            "actions": [],
            "open_questions": [],
        },
        "repaired_items": current_items,
    }


def _parse_audit_payload(output_text: str, *, provider: str) -> dict[str, Any]:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} audit response was not valid JSON: {output_text[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} audit response was not an object: {payload!r}")
    if not isinstance(payload.get("audit_markdown"), str):
        raise RuntimeError(f"{provider} audit response missing audit_markdown.")
    patch = payload.get("patch_candidate")
    if not isinstance(patch, dict) or not isinstance(patch.get("actions"), list):
        raise RuntimeError(f"{provider} audit response missing patch_candidate actions.")
    if not isinstance(payload.get("repaired_items"), list):
        raise RuntimeError(f"{provider} audit response missing repaired_items.")
    return payload


def _read_cached_audit_response(trace_dir: Path | None, section: MarkdownSection) -> dict[str, Any] | None:
    if trace_dir is None:
        return None
    path = trace_dir / f"section{section.index:02d}_response.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        return _parse_audit_payload(json.dumps(payload, ensure_ascii=False), provider="cached audit")
    except RuntimeError:
        return None


def _write_trace(
    trace_dir: Path,
    section: MarkdownSection,
    *,
    provider_shape: str,
    request_payload: dict[str, Any],
    response_text: str,
    response_payload: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    stem = f"section{section.index:02d}"
    combined = {
        "section_index": section.index,
        "context": section.context.as_json(),
        "provider_shape": provider_shape,
        "request": request_payload,
        "response_text": response_text,
        "response_json": response_payload,
        "usage": usage,
    }
    atomic_write_json(trace_dir / f"{stem}.json", combined)
    atomic_write_json(trace_dir / f"{stem}_request.json", request_payload)
    atomic_write_json(trace_dir / f"{stem}_response.json", response_payload)


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
    if isinstance(response, dict):
        usage = response.get("usage")
        return usage if isinstance(usage, dict) else None
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


def _merge_usage(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any] | None:
    if right is None:
        return left
    if left is None:
        return dict(right)
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] = merged[key] + value
        else:
            merged[key] = value
    return merged
