from __future__ import annotations

from collections import Counter
from typing import Any


SCHEMA_VERSION = "md2json.annotation.v1"
TYPE_ALIASES = {
    "defn": "def",
    "definition": "def",
    "theorem": "thm",
    "proposition": "prop",
    "corollary": "cor",
    "remarks": "remark",
    "examples": "example",
    "exercises": "exercise",
}
KNOWN_TYPES = {
    "def",
    "thm",
    "prop",
    "lemma",
    "cor",
    "remark",
    "example",
    "exercise",
    "algorithm",
    "assumption",
    "claim",
    "conjecture",
    "problem",
    "question",
    "notation",
    "heading",
    "paragraph",
    "proof",
    "figure",
    "table",
    "unknown",
}


def to_annotation_document(
    *,
    result_payload: Any,
    quality_payload: Any | None,
    filename: str,
    source_type: str,
    original_mime_type: str | None = None,
) -> dict[str, Any]:
    raw_items = result_payload if isinstance(result_payload, list) else []
    label_counts = Counter(str(item.get("label") or "") for item in raw_items if isinstance(item, dict))
    items = [
        _annotation_item(item, index, label_counts=label_counts)
        for index, item in enumerate(raw_items, start=1)
        if isinstance(item, dict)
    ]
    quality = _quality_summary(items=items, quality_payload=quality_payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "filename": filename,
            "source_type": source_type,
            "original_mime_type": original_mime_type or _mime_type(filename, source_type),
            "content_hash": None,
        },
        "document": _document_summary(items),
        "items": items,
        "quality": quality,
    }


def _annotation_item(item: dict[str, Any], index: int, *, label_counts: Counter[str]) -> dict[str, Any]:
    item_id = _stable_item_id(item, index)
    label = str(item.get("label") or item.get("label_raw") or item_id)
    type_name = _normalize_type(item.get("type") or item.get("env"))
    dependencies = _dependencies(item)
    source_refs = _source_refs(item)
    assets = _assets(item)
    statement = _statement(item)
    proof = item.get("proof")
    proof_text = "" if proof is None else str(proof)
    issues = _item_issues(
        item=item,
        label=label,
        statement=statement,
        dependencies=dependencies,
        label_counts=label_counts,
    )
    return {
        "id": item_id,
        "order_index": int(item.get("order_index") or item.get("index") or index),
        "label": label,
        "label_raw": str(item.get("label_raw") or item.get("label") or ""),
        "type": type_name,
        "number_components": _string_list(item.get("number_components")),
        "statement": statement,
        "proof": proof_text,
        "context": _context(item.get("context")),
        "dependencies": dependencies,
        "source_refs": source_refs,
        "assets": assets,
        "audit": {
            "modified": _modified(item),
            "issues": issues,
        },
        "raw": {
            "env": str(item.get("env") or item.get("type") or ""),
        },
    }


def _stable_item_id(item: dict[str, Any], index: int) -> str:
    raw_id = item.get("id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id
    if isinstance(raw_id, int):
        return f"item_{raw_id:06d}"
    raw_index = item.get("index") or item.get("order_index") or index
    try:
        return f"item_{int(raw_index):06d}"
    except (TypeError, ValueError):
        return f"item_{index:06d}"


def _normalize_type(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    normalized = TYPE_ALIASES.get(raw, raw)
    return normalized if normalized in KNOWN_TYPES else "unknown"


def _statement(item: dict[str, Any]) -> str:
    if "statement" in item:
        return "" if item["statement"] is None else str(item["statement"])
    if "content" in item:
        return "" if item["content"] is None else str(item["content"])
    return ""


def _context(raw: Any) -> dict[str, str]:
    payload = raw if isinstance(raw, dict) else {}
    return {
        "chapter_title": str(payload.get("chapter_title") or payload.get("chapter") or ""),
        "chapter_number": str(payload.get("chapter_number") or ""),
        "section_title": str(payload.get("section_title") or payload.get("section") or ""),
        "section_number": str(payload.get("section_number") or ""),
        "subsection_title": str(payload.get("subsection_title") or ""),
        "subsection_number": str(payload.get("subsection_number") or ""),
    }


def _dependencies(item: dict[str, Any]) -> list[dict[str, Any]]:
    values = item.get("dependencies")
    if values is None:
        values = item.get("references")
    out = []
    if not isinstance(values, list):
        return out
    for value in values:
        if isinstance(value, dict):
            label = str(value.get("label") or value.get("target_label") or value.get("name") or "")
            target_id = value.get("target_id")
            resolved = bool(value.get("resolved", target_id is not None))
        else:
            label = str(value)
            target_id = None
            resolved = False
        if not label:
            continue
        out.append({"label": label, "target_id": target_id, "resolved": resolved})
    for label in _string_list(item.get("unresolved_references")):
        out.append({"label": label, "target_id": None, "resolved": False})
    return out


def _source_refs(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    pages = item.get("pages", source.get("pages"))
    if pages is None and item.get("page") is not None:
        pages = [item.get("page")]
    return {
        "pages": _int_list(pages),
        "block_ids": _string_list(source.get("block_ids") or item.get("block_ids")),
        "span_ids": _string_list(source.get("span_ids") or item.get("span_ids")),
        "bbox_refs": _bbox_refs(source.get("bbox_refs") or item.get("bbox_refs")),
    }


def _assets(item: dict[str, Any]) -> dict[str, str]:
    image = item.get("image") if isinstance(item.get("image"), dict) else {}
    return {
        "image_path": str(item.get("image_path") or image.get("path") or ""),
        "caption": str(item.get("caption") or ""),
        "table_markdown": str(item.get("table_markdown") or ""),
    }


def _modified(item: dict[str, Any]) -> bool:
    value = item.get("modified")
    if isinstance(value, dict):
        return bool(value.get("any"))
    return bool(value)


def _item_issues(
    *,
    item: dict[str, Any],
    label: str,
    statement: str,
    dependencies: list[dict[str, Any]],
    label_counts: Counter[str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not statement.strip():
        issues.append(_issue("error", "empty_statement", "该条目内容为空"))
    if not label.strip():
        issues.append(_issue("warning", "missing_label", "该条目缺少编号或名称"))
    elif label_counts[label] > 1:
        issues.append(_issue("warning", "duplicate_label", f"编号或名称重复：{label}"))
    for dependency in dependencies:
        if not dependency.get("resolved", False):
            issues.append(
                _issue(
                    "warning",
                    "unresolved_dependency",
                    f"引用 {dependency.get('label')}，但未找到对应条目",
                )
            )
    raw_audit = item.get("audit") if isinstance(item.get("audit"), dict) else {}
    for raw_issue in raw_audit.get("issues") or []:
        if isinstance(raw_issue, dict):
            issues.append(
                _issue(
                    str(raw_issue.get("severity") or "warning"),
                    str(raw_issue.get("code") or "item_issue"),
                    str(raw_issue.get("message") or raw_issue.get("detail") or "条目需要检查"),
                )
            )
    return _dedupe_issues(issues)


def _quality_summary(*, items: list[dict[str, Any]], quality_payload: Any | None) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for item in items:
        for issue in item["audit"]["issues"]:
            issues.append({**issue, "item_id": item["id"]})
    if isinstance(quality_payload, dict):
        for warning in quality_payload.get("global_warnings") or []:
            issues.append({**_issue("warning", "global_warning", str(warning)), "item_id": None})
        for section in quality_payload.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for warning in section.get("warnings") or []:
                issues.append({**_issue("warning", "section_warning", str(warning)), "item_id": None})
    issues = _dedupe_quality_issues(issues)
    return {
        "error_count": sum(1 for issue in issues if issue["severity"] == "error"),
        "warning_count": sum(1 for issue in issues if issue["severity"] == "warning"),
        "issues": issues,
    }


def _document_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    chapters: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    title = ""
    for item in items:
        context = item["context"]
        chapter_number = context["chapter_number"]
        chapter_title = context["chapter_title"]
        if not title and chapter_title:
            title = chapter_title
        key = (chapter_number, chapter_title)
        if key != ("", "") and key not in seen:
            seen.add(key)
            chapters.append({"number": chapter_number, "title": chapter_title})
    return {"title": title, "language": "", "chapters": chapters}


def _issue(severity: str, code: str, message: str) -> dict[str, str]:
    normalized = severity if severity in {"error", "warning"} else "warning"
    return {"severity": normalized, "code": code, "message": message}


def _dedupe_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    seen = set()
    for issue in issues:
        key = (issue["severity"], issue["code"], issue["message"])
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _dedupe_quality_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for issue in issues:
        key = (issue["severity"], issue["code"], issue["message"], issue.get("item_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _bbox_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        out.append(
            {
                "page": int(item.get("page") or 0),
                "block_id": str(item.get("block_id") or ""),
                "bbox": bbox,
            }
        )
    return out


def _mime_type(filename: str, source_type: str) -> str:
    lower = filename.lower()
    if source_type == "pdf" or lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".md"):
        return "text/markdown"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    return "application/octet-stream"
