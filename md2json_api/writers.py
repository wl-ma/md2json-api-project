from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ConversionResult, MarkdownSection
from .quality import write_quality_report
from .usage_summary import write_usage_summary


def write_outputs(
    *,
    source_file: Path,
    out_dir: Path,
    sections: list[MarkdownSection],
    section_items: list[list[dict[str, Any]]],
    all_items: list[dict[str, Any]],
    front_matter: str = "",
    back_matter: str = "",
    split_warnings: list[str] | None = None,
    initial_section_items: list[list[dict[str, Any]]] | None = None,
    audit_results: list[dict[str, Any] | None] | None = None,
    quality_report: dict[str, Any] | None = None,
    started_at: float | None = None,
    ended_at: float | None = None,
) -> ConversionResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    sections_dir = out_dir / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    source_sections_dir = out_dir / "source_md_sections"
    source_sections_dir.mkdir(parents=True, exist_ok=True)
    initial_sections_dir = out_dir / "initial_sections"
    audit_reports_dir = out_dir / "audit_reports"
    patch_candidates_dir = out_dir / "patch_candidates"
    if initial_section_items is not None:
        initial_sections_dir.mkdir(parents=True, exist_ok=True)
    if audit_results is not None:
        audit_reports_dir.mkdir(parents=True, exist_ok=True)
        patch_candidates_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(
        out_dir,
        sections_dir,
        source_sections_dir,
        initial_sections_dir,
        audit_reports_dir,
        patch_candidates_dir,
    )

    main_json = out_dir / f"{source_file.stem}.json"
    main_json.write_text(json.dumps(all_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files: list[dict[str, Any]] = []
    for section, items in zip(sections, section_items):
        file_name = f"section{section.index:02d}.json"
        source_section_name = f"section{section.index:02d}.md"
        (sections_dir / file_name).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (source_sections_dir / source_section_name).write_text(section.text.rstrip() + "\n", encoding="utf-8")
        if initial_section_items is not None:
            initial_items = initial_section_items[section.index - 1] if section.index - 1 < len(initial_section_items) else []
            (initial_sections_dir / file_name).write_text(
                json.dumps(initial_items, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if audit_results is not None:
            audit_result = audit_results[section.index - 1] if section.index - 1 < len(audit_results) else None
            if audit_result is not None:
                (audit_reports_dir / f"section{section.index:02d}_audit.md").write_text(
                    str(audit_result.get("audit_markdown") or "").rstrip() + "\n",
                    encoding="utf-8",
                )
                (patch_candidates_dir / f"section{section.index:02d}_patch_candidate.json").write_text(
                    json.dumps(audit_result.get("patch_candidate") or {}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        ctx = section.context
        files.append(
            {
                "file": file_name,
                "source_md": source_section_name,
                "initial_json": file_name if initial_section_items is not None else None,
                "audit_report": f"section{section.index:02d}_audit.md" if audit_results is not None else None,
                "patch_candidate": f"section{section.index:02d}_patch_candidate.json" if audit_results is not None else None,
                "items": len(items),
                "initial_items": (
                    len(initial_section_items[section.index - 1])
                    if initial_section_items is not None and section.index - 1 < len(initial_section_items)
                    else None
                ),
                "chapter_number": ctx.chapter_number,
                "section_number": ctx.section_number,
                "chapter": ctx.chapter,
                "section": ctx.section,
                "line_range": [section.start_line, section.end_line],
                "heading_level": section.heading_level,
                "source_heading": section.source_heading,
            }
        )

    usage_summary = write_usage_summary(out_dir=out_dir, started_at=started_at, ended_at=ended_at)
    summary = {
        "sections_written": len(sections),
        "items_total": len(all_items),
        "out_dir": str(sections_dir),
        "source_file": str(source_file),
        "front_matter_chars": len(front_matter),
        "back_matter_chars": len(back_matter),
        "split_warnings": split_warnings or [],
        "audit_enabled": audit_results is not None,
        "usage_summary": usage_summary,
        "files": files,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_structure(
        out_dir / "structure.json",
        source_file=source_file,
        files=files,
        front_matter=front_matter,
        back_matter=back_matter,
        split_warnings=split_warnings or [],
    )
    if front_matter:
        (out_dir / "front_matter.md").write_text(front_matter.rstrip() + "\n", encoding="utf-8")
    if back_matter:
        (out_dir / "back_matter.md").write_text(back_matter.rstrip() + "\n", encoding="utf-8")
    if quality_report is not None:
        write_quality_report(out_dir, quality_report)
    if audit_results is not None:
        write_consolidated_patch_candidate(out_dir / "patch_candidate_all.json", files, audit_results)
        write_consolidated_audit(out_dir / "audit_report_all.md", files, audit_results)
    write_index(out_dir / "INDEX.md", source_file, files, len(all_items))

    return ConversionResult(
        source_file=source_file,
        out_dir=out_dir,
        sections_written=len(sections),
        items_total=len(all_items),
        files=files,
    )


def write_index(path: Path, source_file: Path, files: list[dict[str, Any]], items_total: int) -> None:
    lines = [
        f"# {source_file.stem} JSON extraction index",
        "",
        f"Source: `{source_file}`",
        "",
        "| file | source md | initial | audit | patch | lines | section | items |",
        "|---|---|---|---|---|---:|---|---:|",
    ]
    for row in files:
        section = str(row["section"]).replace("|", "\\|")
        line_range = row.get("line_range") or ["", ""]
        lines.append(
            f"| sections/{row['file']} | source_md_sections/{row['source_md']} | "
            f"{_optional_path('initial_sections', row.get('initial_json'))} | "
            f"{_optional_path('audit_reports', row.get('audit_report'))} | "
            f"{_optional_path('patch_candidates', row.get('patch_candidate'))} | "
            f"{line_range[0]}-{line_range[1]} | {section} | {row['items']} |"
        )
    lines.extend(["", f"Total items: {items_total}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_structure(
    path: Path,
    *,
    source_file: Path,
    files: list[dict[str, Any]],
    front_matter: str,
    back_matter: str,
    split_warnings: list[str],
) -> None:
    payload = {
        "source_file": str(source_file),
        "front_matter_chars": len(front_matter),
        "back_matter_chars": len(back_matter),
        "split_warnings": split_warnings,
        "sections": [
            {
                "file": row["file"],
                "source_md": row["source_md"],
                "chapter": row["chapter"],
                "chapter_number": row["chapter_number"],
                "section": row["section"],
                "section_number": row["section_number"],
                "line_start": row["line_range"][0],
                "line_end": row["line_range"][1],
                "heading_level": row.get("heading_level"),
                "source_heading": row.get("source_heading"),
                "items": row["items"],
            }
            for row in files
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_consolidated_patch_candidate(
    path: Path,
    files: list[dict[str, Any]],
    audit_results: list[dict[str, Any] | None],
) -> None:
    no_change = []
    sections_payload = []
    defer = []
    for row, audit_result in zip(files, audit_results):
        section_id = f"section{int(row['file'].removeprefix('section').removesuffix('.json')):02d}_{row['section_number']}"
        if audit_result is None:
            defer.append({"section_id": section_id, "reason": "audit result unavailable"})
            continue
        patch = audit_result.get("patch_candidate") or {}
        if not patch.get("actions"):
            no_change.append(section_id)
        else:
            sections_payload.append(patch)
        for question in patch.get("open_questions") or []:
            if question:
                defer.append({"section_id": section_id, "reason": question})
    payload = {
        "scope": "all_sections",
        "review_method": "llm section audit and repair",
        "overall_assessment": "localized repair" if sections_payload else "no change",
        "append_only_provisional_labels": True,
        "no_change": no_change,
        "defer": defer,
        "sections": sections_payload,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_consolidated_audit(
    path: Path,
    files: list[dict[str, Any]],
    audit_results: list[dict[str, Any] | None],
) -> None:
    lines = ["# Consolidated LLM Audit Report", ""]
    for row, audit_result in zip(files, audit_results):
        lines.append(f"## {row['file']} {row['section']}")
        lines.append("")
        if audit_result is None:
            lines.append("Audit unavailable.")
        else:
            lines.append(str(audit_result.get("audit_markdown") or "").rstrip())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def cleanup_previous_outputs(
    out_dir: Path,
    sections_dir: Path,
    source_sections_dir: Path,
    initial_sections_dir: Path,
    audit_reports_dir: Path,
    patch_candidates_dir: Path,
) -> None:
    for pattern_dir, pattern in [
        (sections_dir, "section*.json"),
        (source_sections_dir, "section*.md"),
        (initial_sections_dir, "section*.json"),
        (audit_reports_dir, "section*_audit.md"),
        (patch_candidates_dir, "section*_patch_candidate.json"),
    ]:
        for stale in pattern_dir.glob(pattern):
            stale.unlink()
    for name in [
        "INDEX.md",
        "summary.json",
        "structure.json",
        "quality_report.json",
        "quality_report.md",
        "audit_report_all.md",
        "patch_candidate_all.json",
        "front_matter.md",
        "back_matter.md",
        "structure_candidates.json",
        "structure_plan.json",
    ]:
        target = out_dir / name
        if target.exists():
            target.unlink()


def _optional_path(folder: str, name: Any) -> str:
    return f"{folder}/{name}" if name else ""
