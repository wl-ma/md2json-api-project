from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import MarkdownSection, SectionContext
from .splitter import SplitPlan, is_back_matter_heading, parse_chapter_heading, parse_section_heading


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
BARE_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*|[A-Z])(?:[\.)])?\s+(.+?)\s*$")
MATHY_TEXT_RE = re.compile(r"(?:\\\(|\\\[|\$|\\[A-Za-z]+|[{}_^=<>])")
ITEM_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:"
    r"Definition|Theorem|Corollary|Proposition|Lemma|Example|Exercise|Remark|"
    r"Algorithm|Assumption|Claim|Conjecture|Problem|Question|Notation|Proof|Try it|Investigate|"
    r"定义|定理|命题|引理|推论|例|练习|注记|算法|假设|断言|猜想|问题|记号|证明"
    r")\b",
    re.I,
)


def build_structure_candidates(source_text: str, *, max_candidates: int = 5000) -> list[dict[str, Any]]:
    lines = source_text.splitlines()
    candidates: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        kind = _candidate_kind(stripped)
        if kind is None:
            continue
        candidates.append(
            {
                "line": index,
                "kind": kind,
                "text": stripped[:500],
                "prev": _neighbor(lines, index, direction=-1),
                "next": _neighbor(lines, index, direction=1),
            }
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def needs_structure_planner(source_text: str, hard_plan: SplitPlan) -> bool:
    if hard_plan.warnings:
        return True
    lines = source_text.splitlines()
    section_start_lines = {section.start_line for section in hard_plan.sections}
    numbered_section_prefixes = {section.context.section_number for section in hard_plan.sections}
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not looks_like_bare_section_candidate(stripped, chapter_number=_chapter_number_from_plan(hard_plan)):
            continue
        parsed = parse_section_heading(stripped, chapter_number=_chapter_number_from_plan(hard_plan))
        if parsed is None:
            continue
        number, _title = parsed
        if index not in section_start_lines and number not in numbered_section_prefixes:
            return True
    heading_levels = []
    item_heading_levels = []
    for line in lines:
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        parsed = parse_section_heading(title, chapter_number=_chapter_number_from_plan(hard_plan))
        if parsed is not None:
            heading_levels.append(level)
        elif ITEM_HEADING_RE.match(title):
            item_heading_levels.append(level)
    return bool(heading_levels and item_heading_levels and min(item_heading_levels) < max(heading_levels))


def looks_like_bare_section_candidate(stripped_line: str, *, chapter_number: str = "") -> bool:
    match = BARE_NUMBERED_HEADING_RE.match(stripped_line)
    if not match or ITEM_HEADING_RE.match(stripped_line):
        return False
    marker = match.group(1).strip()
    rest = match.group(2).strip()
    parsed = parse_section_heading(stripped_line, chapter_number=chapter_number)
    if parsed is None:
        return False
    if "." in marker:
        return _heading_like_text(rest, allow_math=True)
    return _heading_like_text(rest, allow_math=False)


def split_plan_from_structure_plan(
    *,
    source_text: str,
    source_name: str,
    plan: dict[str, Any],
    fallback_plan: SplitPlan,
) -> SplitPlan:
    lines = source_text.splitlines()
    total_lines = len(lines)
    warnings = [str(warning).strip() for warning in plan.get("warnings") or [] if str(warning).strip()]
    raw_sections = plan.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        return SplitPlan(
            sections=fallback_plan.sections,
            front_matter=fallback_plan.front_matter,
            back_matter=fallback_plan.back_matter,
            warnings=fallback_plan.warnings + ["LLM structure plan had no sections; used hard splitter fallback"],
        )

    sorted_sections = sorted(raw_sections, key=lambda item: int(item.get("start_line") or 0))
    if raw_sections != sorted_sections:
        warnings.append("LLM structure sections were not sorted; sorted by start_line")

    chapter = str(plan.get("chapter") or "").strip() or _fallback_chapter(fallback_plan, source_name)
    if "chapter_number" in plan:
        chapter_number = str(plan.get("chapter_number") or "").strip()
    else:
        chapter_number = _fallback_chapter_number(fallback_plan)

    sections: list[MarkdownSection] = []
    previous_end = 0
    for local_index, raw in enumerate(sorted_sections, start=1):
        start_line = _clamp_int(raw.get("start_line"), 1, total_lines)
        end_line = _clamp_int(raw.get("end_line"), start_line, total_lines)
        if start_line <= previous_end:
            start_line = min(previous_end + 1, total_lines)
            warnings.append(f"Adjusted overlapping section start for section {local_index}.")
        if end_line < start_line:
            end_line = start_line
            warnings.append(f"Adjusted invalid end_line for section {local_index}.")
        previous_end = end_line
        section_chapter = str(raw.get("chapter") or "").strip() or chapter
        section_chapter_number = str(raw.get("chapter_number") or "").strip()
        if "chapter_number" not in raw:
            section_chapter_number = chapter_number
        section_number = _qualified_section_number(
            str(raw.get("section_number") or "").strip() or str(local_index),
            section_chapter_number,
        )
        section_title = str(raw.get("section_title") or "").strip() or section_number
        source_heading = _source_heading(lines, start_line, section_title)
        heading_level = _heading_level(lines[start_line - 1]) if 1 <= start_line <= total_lines else None
        text = _join_lines(lines, start_line, end_line + 1).strip()
        sections.append(
            MarkdownSection(
                index=local_index,
                context=SectionContext(
                    chapter=section_chapter,
                    chapter_number=section_chapter_number,
                    section=f"{section_number}. {section_title}".strip(),
                    section_number=section_number,
                ),
                text=text,
                start_line=start_line,
                end_line=end_line,
                heading_level=heading_level,
                source_heading=source_heading,
            )
        )

    front_matter = _ranges_text(lines, plan.get("front_matter_ranges"))
    if not front_matter and sections and sections[0].start_line > 1:
        front_matter = _join_lines(lines, 1, sections[0].start_line).strip()
    back_matter = _ranges_text(lines, plan.get("back_matter_ranges"))
    return SplitPlan(sections=sections, front_matter=front_matter, back_matter=back_matter, warnings=warnings)


def _qualified_section_number(section_number: str, chapter_number: str) -> str:
    if not section_number or not chapter_number:
        return section_number
    if section_number == chapter_number or section_number.startswith(f"{chapter_number}."):
        return section_number
    if re.fullmatch(r"\d+(?:\.\d+)*", section_number):
        return f"{chapter_number}.{section_number}"
    return section_number


def write_structure_planner_artifacts(
    out_dir: Path,
    *,
    candidates: list[dict[str, Any]],
    plan: dict[str, Any] | None,
    mode: str,
    used: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "used": used,
        "candidates": candidates,
    }
    (out_dir / "structure_candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if plan is not None:
        (out_dir / "structure_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _candidate_kind(stripped_line: str) -> str | None:
    heading = HEADING_RE.match(stripped_line)
    if heading:
        title = heading.group(2).strip()
        if parse_chapter_heading(title) is not None:
            return f"markdown_h{len(heading.group(1))}_chapter"
        if is_back_matter_heading(title):
            return f"markdown_h{len(heading.group(1))}_back_matter"
        if ITEM_HEADING_RE.match(title):
            return f"markdown_h{len(heading.group(1))}_item_or_activity"
        parsed = parse_section_heading(title, chapter_number="")
        if parsed is not None:
            return f"markdown_h{len(heading.group(1))}_section_candidate"
        return f"markdown_h{len(heading.group(1))}_heading"
    if parse_chapter_heading(stripped_line) is not None:
        return "bare_chapter_candidate"
    if is_back_matter_heading(stripped_line):
        return "bare_back_matter_candidate"
    if ITEM_HEADING_RE.match(stripped_line):
        return "item_or_activity_candidate"
    if looks_like_bare_section_candidate(stripped_line):
        return "bare_section_candidate"
    return None


def _heading_like_text(text: str, *, allow_math: bool) -> bool:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return False
    if len(clean) > 110:
        return False
    if clean.endswith((".", "。", ";", "；", ",", "，")):
        return False
    if not allow_math and MATHY_TEXT_RE.search(clean):
        return False
    # Bare integer/letter lines are frequently proof/list enumerators.  Treat
    # sentence-like prose as body text and let the LLM see only plausible titles.
    if len(clean) > 55 and re.search(r"[，,;；:：。.!?？]", clean):
        return False
    return True


def _neighbor(lines: list[str], line_number: int, *, direction: int) -> str:
    index = line_number - 1 + direction
    while 0 <= index < len(lines):
        text = lines[index].strip()
        if text:
            return text[:300]
        index += direction
    return ""


def _chapter_number_from_plan(plan: SplitPlan) -> str:
    return plan.sections[0].context.chapter_number if plan.sections else ""


def _fallback_chapter(plan: SplitPlan, source_name: str) -> str:
    return plan.sections[0].context.chapter if plan.sections else Path(source_name).stem


def _fallback_chapter_number(plan: SplitPlan) -> str:
    return plan.sections[0].context.chapter_number if plan.sections else ""


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def _source_heading(lines: list[str], start_line: int, fallback: str) -> str:
    if not (1 <= start_line <= len(lines)):
        return fallback
    text = lines[start_line - 1].strip()
    heading = HEADING_RE.match(text)
    if heading:
        return heading.group(2).strip()
    return text or fallback


def _heading_level(line: str) -> int | None:
    match = HEADING_RE.match(line)
    return len(match.group(1)) if match else None


def _join_lines(lines: list[str], start_line: int, end_line_exclusive: int) -> str:
    start = max(start_line - 1, 0)
    end = max(end_line_exclusive - 1, start)
    return "\n".join(lines[start:end])


def _ranges_text(lines: list[str], ranges: Any) -> str:
    if not isinstance(ranges, list):
        return ""
    pieces = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        start_line = _clamp_int(item.get("start_line"), 1, len(lines))
        end_line = _clamp_int(item.get("end_line"), start_line, len(lines))
        if end_line >= start_line:
            pieces.append(_join_lines(lines, start_line, end_line + 1).strip())
    return "\n\n".join(piece for piece in pieces if piece)
