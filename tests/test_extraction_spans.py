from __future__ import annotations

import pytest

from md2json_api.extraction_spans import SpanBuildError, build_items_from_source_spans
from md2json_api.prompts import build_audit_repair_prompt
from md2json_api.models import MarkdownSection, SectionContext


def _section() -> MarkdownSection:
    return MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Test notes",
            chapter_number="",
            section="1. Test section",
            section_number="1",
        ),
        text=(
            "### 1. Test section\n\n"
            "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.\n\n"
            "Proof. Let $G$ be a finite subgroup and choose an element of maximal order.\n\n"
            "Lemma 2. If $x \\in U$, then $x+y \\in U$ for all $y \\in U$.\n"
        ),
        start_line=1,
        end_line=7,
        heading_level=3,
        source_heading="1. Test section",
    )


def test_build_items_from_source_spans_copies_content_and_proof() -> None:
    result = build_items_from_source_spans(
        [
            {
                "label": "Theorem 1",
                "env": "thm",
                "number_components": ["1"],
                "dependencies": [],
                "source_order_anchor": "Theorem 1.",
                "content_span": {
                    "start_anchor": "Theorem 1.",
                    "end_anchor": "Proof.",
                    "start_occurrence": 1,
                    "end_occurrence": 1,
                    "include_start": True,
                    "include_end": False,
                },
                "proof_span": {
                    "start_anchor": "Proof.",
                    "end_anchor": "Lemma 2.",
                    "start_occurrence": 1,
                    "end_occurrence": 1,
                    "include_start": False,
                    "include_end": False,
                },
            }
        ],
        _section(),
    )

    items = result.items
    assert items[0]["content"] == "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic."
    assert items[0]["proof"] == "Let $G$ be a finite subgroup and choose an element of maximal order."
    assert result.diagnostics["raw_items"][0]["label"] == "Theorem 1"
    assert result.diagnostics["span_builder"][0]["content"]["ok"] is True
    assert result.diagnostics["span_builder"][0]["proof"]["ok"] is True


def test_build_items_from_source_spans_raises_on_missing_content_span() -> None:
    with pytest.raises(SpanBuildError, match="missing content_span") as raised:
        build_items_from_source_spans(
            [
                {
                    "label": "Theorem 1",
                    "env": "thm",
                    "number_components": ["1"],
                    "dependencies": [],
                    "source_order_anchor": "Theorem 1.",
                    "proof_span": None,
                }
            ],
            _section(),
        )
    assert raised.value.diagnostics["span_builder"][0]["content"]["ok"] is False


def test_proof_start_occurrence_is_resolved_from_section_start() -> None:
    section = MarkdownSection(
        index=1,
        context=SectionContext(
            chapter="Test notes",
            chapter_number="",
            section="1. Test section",
            section_number="1",
        ),
        text=(
            "Theorem 1. First statement.\n\n"
            "Proof. First proof.\n\n"
            "Lemma 2. Second statement.\n\n"
            "Proof. Second proof.\n\n"
            "Corollary 3. Next item.\n"
        ),
        start_line=1,
        end_line=9,
        heading_level=3,
        source_heading="1. Test section",
    )

    result = build_items_from_source_spans(
        [
            {
                "label": "Lemma 2",
                "env": "lemma",
                "number_components": ["2"],
                "dependencies": [],
                "source_order_anchor": "Lemma 2.",
                "content_span": {
                    "start_anchor": "Lemma 2.",
                    "end_anchor": "Proof.",
                    "start_occurrence": 1,
                    "end_occurrence": 2,
                    "include_start": True,
                    "include_end": False,
                },
                "proof_span": {
                    "start_anchor": "Proof.",
                    "end_anchor": "Corollary 3.",
                    "start_occurrence": 2,
                    "end_occurrence": 1,
                    "include_start": False,
                    "include_end": False,
                },
            }
        ],
        section,
    )

    assert result.items[0]["content"] == "Lemma 2. Second statement."
    assert result.items[0]["proof"] == "Second proof."


def test_audit_prompt_includes_initial_extraction_span_trace() -> None:
    trace = {
        "raw_items": [
            {
                "label": "Theorem 1",
                "source_order_anchor": "Theorem 1.",
                "content_span": {"start_anchor": "Theorem 1."},
                "proof_span": {"start_anchor": "Proof."},
            }
        ],
        "span_builder": [{"label": "Theorem 1", "content": {"ok": True}, "proof": {"ok": True}}],
    }

    prompt = build_audit_repair_prompt(_section(), [], "textbook", extraction_trace=trace)

    assert "Initial Extraction Span Trace" in prompt
    assert '"source_order_anchor": "Theorem 1."' in prompt
