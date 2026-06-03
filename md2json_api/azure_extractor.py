from __future__ import annotations

import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import MarkdownSection
from .prompts import build_section_prompt, build_system_prompt
from .runtime import atomic_write_json
from .schema import chat_json_schema_response_format


class AzureChatSectionExtractor:
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
            except ModuleNotFoundError as exc:
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

    def extract_section(self, section: MarkdownSection) -> list[dict[str, Any]]:
        cached = self._read_cached_response(section)
        if cached is not None:
            return cached
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": build_system_prompt(self.prompt_profile, section)},
                {"role": "user", "content": build_section_prompt(section, self.prompt_profile)},
            ],
            "response_format": chat_json_schema_response_format(),
        }
        if self.max_output_tokens:
            token_budget_key = "max_completion_tokens" if self.reasoning_effort else "max_tokens"
            request[token_budget_key] = self.max_output_tokens
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort

        usage: dict[str, Any] | None = None
        if self.client is False:
            output_text = _with_retries(lambda: self._extract_section_via_rest(request))
        else:
            response = _with_retries(lambda: self.client.chat.completions.create(**request))
            output_text = response.choices[0].message.content or ""
            usage = _response_usage(response)
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Azure OpenAI response was not valid JSON: {output_text[:500]}") from exc
        self._write_trace(section, request, output_text, payload, usage=usage)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError(f"Azure OpenAI response did not contain an items array: {payload!r}")
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

    def _extract_section_via_rest(self, request_payload: dict[str, Any]) -> str:
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
            raise RuntimeError(f"Azure OpenAI HTTP {exc.code}: {error_body[:1000]}") from exc
        return payload["choices"][0]["message"].get("content") or ""

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
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        stem = f"section{section.index:02d}"
        combined = {
            "section_index": section.index,
            "context": section.context.as_json(),
            "provider_shape": "azure_chat_completions",
            "request": request_payload,
            "response_text": response_text,
            "response_json": response_payload,
            "usage": usage,
        }
        atomic_write_json(self.trace_dir / f"{stem}.json", combined)
        atomic_write_json(self.trace_dir / f"{stem}_request.json", request_payload)
        atomic_write_json(self.trace_dir / f"{stem}_response.json", response_payload)


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
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            output[key] = value
    return output or None
