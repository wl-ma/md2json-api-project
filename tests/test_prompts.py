from __future__ import annotations

from md2json_api.models import MarkdownSection, SectionContext
from md2json_api.prompts import AUDIT_RULES, COMMON_RULES, build_audit_repair_system_prompt, build_system_prompt


def test_prompts_require_numbered_exercises_to_be_split() -> None:
    assert "split explicitly numbered exercises/problems into separate items" in COMMON_RULES
    assert "merged exercises/problems" in AUDIT_RULES


def test_extraction_and_audit_prompts_explain_span_inclusion() -> None:
    section = MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Test notes",
            chapter_number="",
            section="1. Test",
            section_number="1",
        ),
        text="Theorem 1. A statement.\n\nProof. Body.",
        start_line=1,
        end_line=3,
        heading_level=2,
        source_heading="1. Test",
    )

    extraction_prompt = build_system_prompt("textbook", section)
    audit_prompt = build_audit_repair_system_prompt("textbook", section)

    assert "include_start/include_end control whether the full anchor string itself is copied" in extraction_prompt
    assert "do not put proof-body words or formulas inside a start_anchor that will be excluded" in audit_prompt
