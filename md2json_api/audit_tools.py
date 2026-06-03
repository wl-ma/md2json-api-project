from __future__ import annotations

import copy
import json
import re
from typing import Any

from .models import ALLOWED_ENVS, MarkdownSection


PROOF_BOUNDARY_RE = re.compile(r"(?im)^\s*(?:Proof|PROOF|证明)\s*[.:：]?\s*")


def audit_source_tool_schemas() -> list[dict[str, Any]]:
    span = _span_schema()
    return [
        {
            "type": "function",
            "function": {
                "name": "list_source_item_labels",
                "description": (
                    "Record the source item labels that you, the model, identified by reading the Markdown section. "
                    "This tool does not discover labels with hard-coded rules; it only checks whether your anchors "
                    "appear in the source and returns the current JSON labels for comparison."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "label": {"type": "string"},
                                    "env": {"type": "string", "enum": list(ALLOWED_ENVS)},
                                    "number_components": {"type": "array", "items": {"type": "string"}},
                                    "anchor_text": {
                                        "type": "string",
                                        "description": "A short exact source substring near the item start.",
                                    },
                                    "reason": {"type": "string"},
                                },
                                "required": ["label", "env", "number_components", "anchor_text", "reason"],
                            },
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["items", "notes"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_source",
                "description": (
                    "Search the Markdown section for a literal string. Use this when you need to locate a label, "
                    "proof marker, next-item boundary, or formula before extracting a span."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "max_matches": {"type": "integer"},
                    },
                    "required": ["query", "max_matches"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "extract_source_span",
                "description": (
                    "Copy an exact text span from the Markdown section using literal start/end anchors chosen by you. "
                    "Use this to inspect the exact content/proof text before final repair."
                ),
                "parameters": span,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "build_repaired_items",
                "description": (
                    "Build the final repaired_items array. For every item that should remain in the section, either "
                    "preserve an existing current item or provide source spans so this tool copies content/proof exactly "
                    "from the Markdown. The model decides the labels, envs, ordering, and which items exist."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "audit_markdown": {"type": "string"},
                        "overall_assessment": {
                            "type": "string",
                            "enum": ["no change", "minor repair", "moderate repair", "major repair"],
                        },
                        "actions": {"type": "array", "items": _tool_action_schema()},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "items": {
                            "type": "array",
                            "description": (
                                "The complete final item list for this section, in source order. Include unchanged "
                                "items too, using preserve_current_label when no source-span change is needed."
                            ),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "label": {"type": "string"},
                                    "env": {"type": "string", "enum": list(ALLOWED_ENVS)},
                                    "number_components": {"type": "array", "items": {"type": "string"}},
                                    "dependencies": {"type": "array", "items": {"type": "string"}},
                                    "content_span": {
                                        "type": ["object", "null"],
                                        "additionalProperties": False,
                                        "properties": span["properties"],
                                        "required": span["required"],
                                    },
                                    "proof_span": {
                                        "type": ["object", "null"],
                                        "additionalProperties": False,
                                        "properties": span["properties"],
                                        "required": span["required"],
                                    },
                                    "preserve_current_label": {
                                        "type": ["string", "null"],
                                        "description": "Set to an existing current JSON label to keep its source-backed text.",
                                    },
                                    "source_order_anchor": {
                                        "type": ["string", "null"],
                                        "description": "Short exact source substring used only for ordering/validation.",
                                    },
                                    "source_order_occurrence": {
                                        "type": "integer",
                                        "description": "1-based occurrence of source_order_anchor from the beginning of the section.",
                                    },
                                    "reason": {"type": "string"},
                                },
                                "required": [
                                    "label",
                                    "env",
                                    "number_components",
                                    "dependencies",
                                    "content_span",
                                    "proof_span",
                                    "preserve_current_label",
                                    "source_order_anchor",
                                    "source_order_occurrence",
                                    "reason",
                                ],
                            },
                        },
                    },
                    "required": ["audit_markdown", "overall_assessment", "actions", "open_questions", "items"],
                },
            },
        },
    ]


class AuditSourceToolExecutor:
    def __init__(self, section: MarkdownSection, current_items: list[dict[str, Any]]) -> None:
        self.section = section
        self.current_items = copy.deepcopy(current_items)
        self.current_by_label = {str(item.get("label") or ""): item for item in self.current_items}
        self.declared_labels: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []
        self.final_payload: dict[str, Any] | None = None

    def execute_json(self, name: str, raw_arguments: str) -> str:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        result = self.execute(name, arguments)
        self.trace.append({"tool": name, "arguments": arguments, "result_summary": summarize_tool_result(result)})
        return json.dumps(result, ensure_ascii=False)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_source_item_labels":
            return self._record_llm_labels(arguments)
        if name == "search_source":
            return self._search_source(str(arguments.get("query") or ""), int(arguments.get("max_matches") or 5))
        if name == "extract_source_span":
            return self._extract_span(arguments)
        if name == "build_repaired_items":
            self.final_payload = self._build_repaired_items(arguments)
            return self.final_payload
        return {"error": f"Unknown audit source tool: {name}"}

    def _record_llm_labels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        labels: list[dict[str, Any]] = []
        for raw in arguments.get("items") or []:
            if not isinstance(raw, dict):
                continue
            anchor = str(raw.get("anchor_text") or "")
            offsets = _find_all(self.section.text, anchor, limit=5) if anchor else []
            labels.append(
                {
                    "label": str(raw.get("label") or ""),
                    "env": raw.get("env") if raw.get("env") in ALLOWED_ENVS else None,
                    "number_components": [str(part) for part in raw.get("number_components") or []],
                    "anchor_text": anchor,
                    "anchor_found": bool(offsets),
                    "anchor_offsets": offsets,
                    "reason": str(raw.get("reason") or ""),
                }
            )
        self.declared_labels = labels
        return {
            "declared_labels": labels,
            "current_labels": [str(item.get("label") or "") for item in self.current_items],
            "notes": str(arguments.get("notes") or ""),
            "tool_note": "Labels are model-declared; the tool only checked literal anchors.",
        }

    def _search_source(self, query: str, max_matches: int) -> dict[str, Any]:
        matches: list[dict[str, Any]] = []
        if not query:
            return {"matches": matches}
        for offset in _find_all(self.section.text, query, limit=max(1, max_matches)):
            snippet_start = max(0, offset - 240)
            snippet_end = min(len(self.section.text), offset + len(query) + 240)
            matches.append(
                {
                    "offset": offset,
                    "line": _absolute_line_at(self.section, offset),
                    "snippet": self.section.text[snippet_start:snippet_end],
                }
            )
        return {"matches": matches}

    def _extract_span(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return _extract_span_from_source(self.section, arguments)

    def _build_repaired_items(self, arguments: dict[str, Any]) -> dict[str, Any]:
        final_items: list[dict[str, Any]] = []
        validations: list[dict[str, Any]] = []
        warnings: list[str] = []
        order_positions: list[int] = []

        raw_items = [raw for raw in arguments.get("items") or [] if isinstance(raw, dict)]
        for index, raw in enumerate(raw_items):
            built, validation, order_position = self._build_one_item(raw, raw_items[index + 1 :])
            validations.append(validation)
            warnings.extend(str(warning) for warning in validation.get("warnings", []) if str(warning))
            if built is None:
                warnings.append(validation["message"])
                continue
            final_items.append(built)
            order_positions.append(order_position)

        if len(order_positions) == len(final_items):
            final_items = [
                item for _, item in sorted(zip(order_positions, final_items), key=lambda pair: (pair[0], pair[1].get("index", 0)))
            ]
        for index, item in enumerate(final_items, start=1):
            item["index"] = index

        section_id = f"section{self.section.index:02d}"
        patch_candidate = {
            "section_id": section_id,
            "overall_assessment": arguments.get("overall_assessment") or "no change",
            "actions": normalize_tool_actions(arguments.get("actions") or []),
            "open_questions": [str(question) for question in arguments.get("open_questions") or []],
        }
        audit_markdown = str(arguments.get("audit_markdown") or "").strip()
        if warnings:
            audit_markdown = (
                audit_markdown
                + "\n\nSource-span validation warnings:\n"
                + "\n".join(f"- {warning}" for warning in warnings)
            ).strip()

        return {
            "audit_markdown": audit_markdown or f"{section_id}: audit repaired with source span tools.",
            "patch_candidate": patch_candidate,
            "repaired_items": final_items,
            "tool_validation": {
                "declared_labels_count": len(self.declared_labels),
                "item_validations": validations,
                "warnings": warnings,
            },
        }

    def _build_one_item(
        self,
        raw: dict[str, Any],
        following_raws: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
        label = str(raw.get("label") or "").strip()
        preserve_label = _nullable_string(raw.get("preserve_current_label")) or label
        current = self.current_by_label.get(preserve_label)
        content_span = raw.get("content_span")
        proof_span = raw.get("proof_span")
        anchor_position = _source_order_anchor_position(self.section.text, raw)
        order_position = anchor_position if anchor_position is not None else 10**12

        content_result = None
        proof_result = None
        fallback = None
        if isinstance(content_span, dict):
            content_result = _extract_span_from_source(self.section, content_span)
            if content_result.get("found"):
                order_position = min(order_position, int(content_result["char_start"]))
        if isinstance(proof_span, dict):
            proof_search_start = int(content_result["char_end"]) if content_result and content_result.get("found") else 0
            proof_result = _extract_span_from_source(self.section, proof_span, search_start=proof_search_start)
        if content_result is None and current is not None and not item_text_is_source_backed(current, self.section.text):
            fallback = self._fallback_item_from_declared_anchors(raw, following_raws)
            if fallback is not None:
                order_position = min(order_position, int(fallback["char_start"]))

        item_start = _span_start(content_result)
        if item_start is None:
            item_start = anchor_position
        if item_start is None and fallback is not None:
            item_start = int(fallback["char_start"])
        if item_start is None:
            item_start = 0
        next_anchor_pos = _next_following_anchor_position(self.section.text, following_raws, item_start + 1)
        span_warnings: list[str] = []
        if content_result is not None and content_result.get("found"):
            span_warnings.extend(_clip_span_to_next_anchor(self.section, content_result, next_anchor_pos, label, "content"))
        if proof_result is not None and proof_result.get("found"):
            span_warnings.extend(_clip_span_to_next_anchor(self.section, proof_result, next_anchor_pos, label, "proof"))

        validation = {
            "label": label,
            "content_source": "span" if content_result else "current",
            "proof_source": "span" if proof_result else "current_or_null",
            "ok": True,
            "message": "",
            "warnings": span_warnings,
        }

        if content_result is not None and not content_result.get("found"):
            validation["ok"] = False
            validation["message"] = f"Could not extract content span for {label}: {content_result.get('error')}"
            return None, validation, order_position
        if proof_result is not None and not proof_result.get("found"):
            message = f"Could not extract proof span for {label}: {proof_result.get('error')}"
            validation["proof_source"] = "current_or_null_after_failed_span"
            validation["warnings"].append(message)
            proof_result = None

        if content_result is not None:
            content = str(content_result.get("text") or "").strip()
        elif current is not None and source_text_contains(self.section.text, current.get("content")):
            content = str(current.get("content") or "").strip()
            located = locate_text(self.section.text, content)
            if located is not None:
                order_position = min(order_position, located)
        elif fallback is not None:
            content = str(fallback.get("content") or "").strip()
            validation["content_source"] = "declared_anchor_span"
        else:
            validation["ok"] = False
            validation["message"] = f"No source-backed content available for {label}."
            return None, validation, order_position

        if proof_result is not None:
            proof_text = str(proof_result.get("text") or "").strip() or None
        elif fallback is not None:
            proof_text = fallback.get("proof")
            validation["proof_source"] = "declared_anchor_span_or_null"
        elif current is not None and source_text_contains(self.section.text, current.get("proof")):
            proof_text = current.get("proof")
        else:
            proof_text = None

        item = {
            "index": int(current.get("index") or 0) if current else 0,
            "label": label,
            "env": raw.get("env") if raw.get("env") in ALLOWED_ENVS else (current or {}).get("env", "remark"),
            "number_components": [str(part) for part in raw.get("number_components") or []],
            "context": self.section.context.as_json(),
            "content": content,
            "dependencies": [str(dep).strip() for dep in raw.get("dependencies") or [] if str(dep).strip()],
            "proof": proof_text,
        }
        return item, validation, order_position

    def _fallback_item_from_declared_anchors(
        self,
        raw: dict[str, Any],
        following_raws: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        start = _source_order_anchor_position(self.section.text, raw)
        if start is None:
            return None
        anchor = str(raw.get("source_order_anchor") or "").strip()
        end = len(self.section.text)
        search_from = start + max(1, len(anchor))
        for following in following_raws:
            next_pos = _source_order_anchor_position(self.section.text, following)
            if next_pos is not None and next_pos >= search_from:
                end = next_pos
                break
        if end <= start:
            return None
        content, proof = split_statement_and_proof(self.section.text[start:end].strip())
        if not content:
            return None
        return {"content": content, "proof": proof, "char_start": start, "char_end": end}


def _span_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "start_anchor": {"type": "string"},
            "end_anchor": {
                "type": ["string", "null"],
                "description": "Literal text where the span ends. Null means the span runs to the section end.",
            },
            "start_occurrence": {
                "type": "integer",
                "description": "1-based occurrence of start_anchor from the beginning of the section.",
            },
            "end_occurrence": {
                "type": "integer",
                "description": "1-based occurrence of end_anchor from the beginning of the section, ignored when null.",
            },
            "include_start": {
                "type": "boolean",
                "description": "True copies the full start_anchor into the field; false starts after the full start_anchor.",
            },
            "include_end": {
                "type": "boolean",
                "description": "True copies the full end_anchor into the field; false stops before the full end_anchor.",
            },
        },
        "required": ["start_anchor", "end_anchor", "start_occurrence", "end_occurrence", "include_start", "include_end"],
    }


def _tool_action_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "delete"]},
            "target_label": {"type": ["string", "null"]},
            "anchor_position": {"type": ["string", "null"], "enum": ["before", "after", "replace", None]},
            "anchor_target_label": {"type": ["string", "null"]},
            "provisional_label": {"type": ["string", "null"]},
            "env": {"type": ["string", "null"], "enum": list(ALLOWED_ENVS) + [None]},
            "reason": {"type": "string"},
            "content_excerpt": {"type": ["string", "null"]},
            "field_updates_note": {"type": ["string", "null"]},
        },
        "required": [
            "action",
            "target_label",
            "anchor_position",
            "anchor_target_label",
            "provisional_label",
            "env",
            "reason",
            "content_excerpt",
            "field_updates_note",
        ],
    }


def _extract_span_from_source(
    section: MarkdownSection,
    spec: dict[str, Any],
    *,
    search_start: int = 0,
) -> dict[str, Any]:
    start_anchor = str(spec.get("start_anchor") or "")
    end_anchor = spec.get("end_anchor")
    start_occurrence = max(1, int(spec.get("start_occurrence") or 1))
    end_occurrence = max(1, int(spec.get("end_occurrence") or 1))
    include_start = bool(spec.get("include_start"))
    include_end = bool(spec.get("include_end"))

    if not start_anchor:
        return {"found": False, "error": "empty start_anchor"}
    search_start = max(0, min(len(section.text), int(search_start)))
    start_anchor_pos = _find_nth(section.text, start_anchor, start_occurrence, start=0)
    if start_anchor_pos is None:
        return {"found": False, "error": f"start_anchor not found: {start_anchor[:80]!r}"}
    if start_anchor_pos < search_start:
        return {"found": False, "error": "start_anchor occurrence resolved before search_start"}
    start = start_anchor_pos if include_start else start_anchor_pos + len(start_anchor)

    if end_anchor is None:
        end_anchor_pos = len(section.text)
        end = len(section.text)
    else:
        end_anchor_text = str(end_anchor)
        if not end_anchor_text:
            return {"found": False, "error": "empty end_anchor"}
        end_anchor_pos = _find_nth(section.text, end_anchor_text, end_occurrence, start=0)
        if end_anchor_pos is None:
            return {"found": False, "error": f"end_anchor occurrence not found: {end_anchor_text[:80]!r}"}
        end = end_anchor_pos + len(end_anchor_text) if include_end else end_anchor_pos

    if end < start:
        return {"found": False, "error": "end anchor resolved before start anchor"}
    text = section.text[start:end]
    return {
        "found": True,
        "text": text,
        "char_start": start,
        "char_end": end,
        "line_start": _absolute_line_at(section, start),
        "line_end": _absolute_line_at(section, max(start, end - 1)),
        "start_anchor_offset": start_anchor_pos,
        "end_anchor_offset": end_anchor_pos,
        "search_start": search_start,
    }


def _span_start(result: dict[str, Any] | None) -> int | None:
    if not result or not result.get("found"):
        return None
    return int(result["char_start"])


def _next_following_anchor_position(
    source_text: str,
    following_raws: list[dict[str, Any]],
    start: int,
) -> int | None:
    candidates: list[int] = []
    for following in following_raws:
        pos = _source_order_anchor_position(source_text, following)
        if pos is not None:
            if pos >= start:
                candidates.append(pos)
    return min(candidates) if candidates else None


def _clip_span_to_next_anchor(
    section: MarkdownSection,
    result: dict[str, Any],
    next_anchor_pos: int | None,
    label: str,
    field_name: str,
) -> list[str]:
    if next_anchor_pos is None:
        return []
    char_start = int(result["char_start"])
    char_end = int(result["char_end"])
    if char_start >= next_anchor_pos:
        message = (
            f"{field_name} span for {label} starts at or after the next item anchor; "
            "the model should choose an anchor inside the current item."
        )
        if field_name == "proof":
            return [
                message
                + " Keeping the resolved proof span because it may be an explicit delayed proof."
            ]
        result["found"] = False
        result["error"] = message
        return [
            message
        ]
    if char_end <= next_anchor_pos:
        return []

    result["text"] = section.text[char_start:next_anchor_pos]
    result["char_end"] = next_anchor_pos
    result["line_end"] = _absolute_line_at(section, max(char_start, next_anchor_pos - 1))
    result["clipped_to_next_item_anchor"] = True
    return [
        f"{field_name} span for {label} crossed the next item anchor and was clipped to preserve item boundaries."
    ]


def normalize_tool_actions(actions: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        normalized.append(
            {
                "action": raw.get("action") if raw.get("action") in {"add", "update", "delete"} else "update",
                "target_label": _nullable_string(raw.get("target_label")),
                "anchor_position": raw.get("anchor_position")
                if raw.get("anchor_position") in {"before", "after", "replace"}
                else None,
                "anchor_target_label": _nullable_string(raw.get("anchor_target_label")),
                "provisional_label": _nullable_string(raw.get("provisional_label")),
                "env": raw.get("env") if raw.get("env") in ALLOWED_ENVS else None,
                "reason": str(raw.get("reason") or ""),
                "content_excerpt": _nullable_string(raw.get("content_excerpt")),
                "field_updates_note": _nullable_string(raw.get("field_updates_note")),
                "candidate_item": None,
            }
        )
    return normalized


def summarize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"keys": sorted(result.keys())}
    if "declared_labels" in result:
        summary["declared_labels_count"] = len(result.get("declared_labels") or [])
    if "matches" in result:
        summary["matches_count"] = len(result.get("matches") or [])
    if "text" in result:
        summary["found"] = result.get("found")
        summary["chars"] = len(str(result.get("text") or ""))
    if "repaired_items" in result:
        summary["repaired_items_count"] = len(result.get("repaired_items") or [])
    if "error" in result:
        summary["error"] = result.get("error")
    return summary


def source_text_contains(source_text: str, value: str | None) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return _normalize_ws(text) in _normalize_ws(source_text)


def item_text_is_source_backed(item: dict[str, Any], source_text: str) -> bool:
    return source_text_contains(source_text, item.get("content")) and source_text_contains(source_text, item.get("proof"))


def split_statement_and_proof(block: str) -> tuple[str, str | None]:
    match = PROOF_BOUNDARY_RE.search(block)
    if match is None:
        return block.strip(), None
    content = block[: match.start()].strip()
    proof = block[match.end() :].strip()
    return content, proof or None


def locate_text(source_text: str, value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    return _find_first(source_text, first_line)


def _find_all(text: str, query: str, *, limit: int) -> list[int]:
    if not query:
        return []
    offsets: list[int] = []
    start = 0
    while len(offsets) < limit:
        pos = text.find(query, start)
        if pos < 0:
            break
        offsets.append(pos)
        start = pos + max(1, len(query))
    return offsets


def _find_first(text: str, query: str) -> int | None:
    if not query:
        return None
    pos = text.find(query)
    return pos if pos >= 0 else None


def _find_first_after(text: str, query: str, start: int) -> int | None:
    if not query:
        return None
    pos = text.find(query, start)
    return pos if pos >= 0 else None


def _source_order_anchor_position(source_text: str, raw: dict[str, Any]) -> int | None:
    anchor = str(raw.get("source_order_anchor") or "").strip()
    if not anchor:
        return None
    occurrence = max(1, int(raw.get("source_order_occurrence") or 1))
    return _find_nth(source_text, anchor, occurrence, start=0)


def _find_nth(text: str, query: str, occurrence: int, *, start: int = 0) -> int | None:
    pos = start
    for _ in range(occurrence):
        found = text.find(query, pos)
        if found < 0:
            return None
        pos = found + max(1, len(query))
    return found


def _absolute_line_at(section: MarkdownSection, offset: int) -> int:
    local_line = section.text.count("\n", 0, max(0, offset)) + 1
    return section.start_line + local_line - 1


def _nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
