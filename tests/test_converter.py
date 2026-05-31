from __future__ import annotations

import unittest

from md2json_api.converter import ConverterConfig, MarkdownJsonConverter, normalize_items
from md2json_api.models import MarkdownSection, SectionContext


class ConverterLabelTests(unittest.TestCase):
    def test_source_numbered_labels_are_general_and_not_shifted_by_synthetic_items(self) -> None:
        section = MarkdownSection(
            index=6,
            context=SectionContext(
                chapter="Complete book",
                chapter_number="",
                section="6. Relative Interiors of Convex Sets",
                section_number="6",
            ),
            text="",
            start_line=1,
            end_line=10,
            heading_level=2,
            source_heading="Relative Interiors of Convex Sets",
        )
        items = normalize_items(
            [
                {
                    "label": "Notation -6-1",
                    "env": "notation",
                    "number_components": [],
                    "content": "Opening notation block used in the section.",
                    "dependencies": [],
                    "proof": None,
                },
                {
                    "label": "Definition -6-2",
                    "env": "def",
                    "number_components": [],
                    "content": "Opening conceptual block defining the notation used in the section.",
                    "dependencies": [],
                    "proof": None,
                },
                {
                    "label": "Theorem -6-3",
                    "env": "thm",
                    "number_components": [],
                    "content": "Theorem 6.1. Let C be a convex set.",
                    "dependencies": [],
                    "proof": "Proof body.",
                },
                {
                    "label": "Corollary -6-4",
                    "env": "cor",
                    "number_components": ["6", "1", "1"],
                    "content": "Corollary 6.1.1. A consequence.",
                    "dependencies": ["Theorem -6-3"],
                    "proof": None,
                },
                {
                    "label": "Algorithm -6-5",
                    "env": "algorithm",
                    "number_components": [],
                    "content": "Algorithm 1: Compute the closure.",
                    "dependencies": ["Theorem 6.1"],
                    "proof": None,
                },
            ],
            section,
            global_start=1,
        )

        self.assertEqual(items[0]["label"], "Notation 6-extra-1")
        self.assertEqual(items[1]["label"], "Definition 6-extra-2")
        self.assertEqual(items[2]["label"], "Theorem 6.1")
        self.assertEqual(items[3]["label"], "Corollary 6.1.1")
        self.assertEqual(items[4]["label"], "Algorithm 1")
        self.assertEqual(items[3]["dependencies"], ["Theorem 6.1"])
        self.assertEqual(items[4]["dependencies"], ["Theorem 6.1"])


def test_converter_hands_bad_span_trace_to_audit(tmp_path) -> None:
    input_md = tmp_path / "book.md"
    input_md.write_text("## 1 Test\n\nTheorem 1. A statement.\n", encoding="utf-8")

    converter = MarkdownJsonConverter(
        ConverterConfig(
            backend="mock",
            audit_mode="llm",
            structure_mode="hard",
        )
    )
    auditor = _RecordingAuditRepairer()
    converter.extractor = _BadSpanExtractor()
    converter.auditor = auditor

    result = converter.convert(input_md, tmp_path / "out")

    assert result.items_total == 1
    assert auditor.current_items == []
    assert auditor.extraction_trace is not None
    assert auditor.extraction_trace["span_builder_status"] == "failed"
    assert "missing content_span" in auditor.extraction_trace["span_builder_error"]
    assert auditor.extraction_trace["span_builder"][0]["content"]["ok"] is False


class _BadSpanExtractor:
    def extract_section(self, section: MarkdownSection) -> list[dict]:
        return [
            {
                "label": "Theorem 1",
                "env": "thm",
                "number_components": ["1"],
                "dependencies": [],
                "source_order_anchor": "Theorem 1.",
                "proof_span": None,
            }
        ]


class _RecordingAuditRepairer:
    def __init__(self) -> None:
        self.current_items: list[dict] | None = None
        self.extraction_trace: dict | None = None

    def audit_repair_section(
        self,
        section: MarkdownSection,
        current_items: list[dict],
        *,
        extraction_trace: dict | None = None,
    ) -> dict:
        self.current_items = current_items
        self.extraction_trace = extraction_trace
        return {
            "audit_markdown": "Recovered from bad initial span.",
            "patch_candidate": {
                "section_id": f"section{section.index:02d}",
                "overall_assessment": "major repair",
                "actions": [],
                "open_questions": [],
            },
            "repaired_items": [
                {
                    "index": 1,
                    "label": "Theorem 1",
                    "env": "thm",
                    "number_components": ["1"],
                    "context": section.context.as_json(),
                    "content": "Theorem 1. A statement.",
                    "dependencies": [],
                    "proof": None,
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
