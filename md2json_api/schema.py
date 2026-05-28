from __future__ import annotations

from .models import ALLOWED_ENVS


ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "index": {"type": "integer"},
        "label": {"type": "string"},
        "env": {"type": "string", "enum": list(ALLOWED_ENVS)},
        "number_components": {
            "type": "array",
            "items": {"type": "string"},
        },
        "context": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "chapter": {"type": "string"},
                "section": {"type": "string"},
                "chapter_number": {"type": "string"},
                "section_number": {"type": "string"},
            },
            "required": ["chapter", "section", "chapter_number", "section_number"],
        },
        "content": {"type": "string"},
        "dependencies": {
            "type": "array",
            "items": {"type": "string"},
        },
        "proof": {"type": ["string", "null"]},
    },
    "required": [
        "index",
        "label",
        "env",
        "number_components",
        "context",
        "content",
        "dependencies",
        "proof",
    ],
}


SECTION_ITEMS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": ITEM_SCHEMA,
        }
    },
    "required": ["items"],
}


PATCH_ACTION_SCHEMA: dict = {
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
        "candidate_item": {"anyOf": [ITEM_SCHEMA, {"type": "null"}]},
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
        "candidate_item",
    ],
}


PATCH_CANDIDATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "section_id": {"type": "string"},
        "overall_assessment": {
            "type": "string",
            "enum": ["no change", "minor repair", "moderate repair", "major repair"],
        },
        "actions": {
            "type": "array",
            "items": PATCH_ACTION_SCHEMA,
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["section_id", "overall_assessment", "actions", "open_questions"],
}


LINE_RANGE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["start_line", "end_line", "reason"],
}


STRUCTURE_SECTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "section_number": {"type": "string"},
        "section_title": {"type": "string"},
        "chapter": {"type": "string"},
        "chapter_number": {"type": "string"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "heading_source": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": [
        "section_number",
        "section_title",
        "chapter",
        "chapter_number",
        "start_line",
        "end_line",
        "heading_source",
        "confidence",
        "reason",
    ],
}


STRUCTURE_PLAN_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document_title": {"type": "string"},
        "chapter": {"type": "string"},
        "chapter_number": {"type": "string"},
        "front_matter_ranges": {"type": "array", "items": LINE_RANGE_SCHEMA},
        "sections": {"type": "array", "items": STRUCTURE_SECTION_SCHEMA},
        "back_matter_ranges": {"type": "array", "items": LINE_RANGE_SCHEMA},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "document_title",
        "chapter",
        "chapter_number",
        "front_matter_ranges",
        "sections",
        "back_matter_ranges",
        "warnings",
    ],
}


AUDIT_REPAIR_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "audit_markdown": {"type": "string"},
        "patch_candidate": PATCH_CANDIDATE_SCHEMA,
        "repaired_items": {
            "type": "array",
            "items": ITEM_SCHEMA,
        },
    },
    "required": ["audit_markdown", "patch_candidate", "repaired_items"],
}


def responses_json_schema_format(name: str = "math_section_items") -> dict:
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": SECTION_ITEMS_SCHEMA,
    }


def chat_json_schema_response_format(name: str = "math_section_items") -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": SECTION_ITEMS_SCHEMA,
        },
    }


def responses_audit_repair_json_schema_format(name: str = "math_section_audit_repair") -> dict:
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": AUDIT_REPAIR_SCHEMA,
    }


def chat_audit_repair_json_schema_response_format(name: str = "math_section_audit_repair") -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": AUDIT_REPAIR_SCHEMA,
        },
    }


def responses_structure_plan_json_schema_format(name: str = "math_structure_plan") -> dict:
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": STRUCTURE_PLAN_SCHEMA,
    }


def chat_structure_plan_json_schema_response_format(name: str = "math_structure_plan") -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": STRUCTURE_PLAN_SCHEMA,
        },
    }
