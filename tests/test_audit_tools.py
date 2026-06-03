from __future__ import annotations

import unittest

from md2json_api.audit_tools import AuditSourceToolExecutor
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
        start_line=10,
        end_line=16,
        heading_level=3,
        source_heading="1. Test section",
    )


class AuditSourceToolTests(unittest.TestCase):
    def test_list_source_item_labels_records_llm_labels_without_hard_mining(self) -> None:
        executor = AuditSourceToolExecutor(_section(), [])

        result = executor.execute("list_source_item_labels", {"items": [], "notes": "no labels declared"})

        self.assertEqual(result["declared_labels"], [])
        self.assertEqual(result["current_labels"], [])
        self.assertIn("model-declared", result["tool_note"])

    def test_build_repaired_items_copies_llm_selected_source_spans(self) -> None:
        executor = AuditSourceToolExecutor(_section(), [])
        executor.execute(
            "list_source_item_labels",
            {
                "items": [
                    {
                        "label": "Theorem 1",
                        "env": "thm",
                        "number_components": ["1"],
                        "anchor_text": "Theorem 1.",
                        "reason": "explicit theorem label in source",
                    },
                    {
                        "label": "Lemma 2",
                        "env": "lemma",
                        "number_components": ["2"],
                        "anchor_text": "Lemma 2.",
                        "reason": "explicit lemma label in source",
                    },
                ],
                "notes": "model-declared labels",
            },
        )

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Two explicit source items repaired from spans.",
                "overall_assessment": "major repair",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Theorem 1",
                        "env": "thm",
                        "number_components": ["1"],
                        "dependencies": [],
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
                        "preserve_current_label": None,
                        "source_order_anchor": "Theorem 1.",
                        "reason": "copy theorem and proof from source spans",
                    },
                    {
                        "label": "Lemma 2",
                        "env": "lemma",
                        "number_components": ["2"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Lemma 2.",
                            "end_anchor": None,
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": None,
                        "preserve_current_label": None,
                        "source_order_anchor": "Lemma 2.",
                        "reason": "copy lemma from source span",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Theorem 1", "Lemma 2"])
        self.assertEqual(items[0]["content"], "Theorem 1. Every finite subgroup of $K^\\times$ is cyclic.")
        self.assertEqual(
            items[0]["proof"],
            "Let $G$ be a finite subgroup and choose an element of maximal order.",
        )
        self.assertEqual(
            items[1]["content"],
            "Lemma 2. If $x \\in U$, then $x+y \\in U$ for all $y \\in U$.",
        )
        self.assertEqual(result["tool_validation"]["declared_labels_count"], 2)

    def test_source_order_anchor_occurrence_disambiguates_repeated_anchors(self) -> None:
        section = MarkdownSection(
            index=2,
            context=SectionContext(
                chapter="Test notes",
                chapter_number="",
                section="2. Repeated anchors",
                section_number="2",
            ),
            text=(
                "(3) Let \\( F \\) be a subfield of \\( k \\) . Earlier exercise.\n\n"
                "Definition 2.1. A middle definition.\n\n"
                "(3) Let \\( F \\) be a subfield of \\( k \\) . Later exercise.\n\n"
                "(4) Final exercise.\n"
            ),
            start_line=1,
            end_line=7,
            heading_level=3,
            source_heading="2. Repeated anchors",
        )
        current_items = [
            {
                "index": 1,
                "label": "Exercise 2.0.3",
                "env": "exercise",
                "number_components": ["2", "0", "3"],
                "context": section.context.as_json(),
                "content": "(3) Let \\( F \\) be a subfield of \\( k \\) . Earlier exercise.",
                "dependencies": [],
                "proof": None,
            },
            {
                "index": 2,
                "label": "Definition 2.1",
                "env": "def",
                "number_components": ["2", "1"],
                "context": section.context.as_json(),
                "content": "Definition 2.1. A middle definition.",
                "dependencies": [],
                "proof": None,
            },
            {
                "index": 3,
                "label": "Exercise 2.2.3",
                "env": "exercise",
                "number_components": ["2", "2", "3"],
                "context": section.context.as_json(),
                "content": "(3) Let \\( F \\) be a subfield of \\( k \\) . Later exercise.",
                "dependencies": [],
                "proof": None,
            },
        ]
        executor = AuditSourceToolExecutor(section, current_items)

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Preserve repeated-anchor items.",
                "overall_assessment": "no change",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Exercise 2.0.3",
                        "env": "exercise",
                        "number_components": ["2", "0", "3"],
                        "dependencies": [],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Exercise 2.0.3",
                        "source_order_anchor": "(3) Let \\( F \\) be a subfield of \\( k \\) .",
                        "source_order_occurrence": 1,
                        "reason": "preserve the first repeated exercise",
                    },
                    {
                        "label": "Definition 2.1",
                        "env": "def",
                        "number_components": ["2", "1"],
                        "dependencies": [],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Definition 2.1",
                        "source_order_anchor": "Definition 2.1.",
                        "source_order_occurrence": 1,
                        "reason": "preserve the middle definition",
                    },
                    {
                        "label": "Exercise 2.2.3",
                        "env": "exercise",
                        "number_components": ["2", "2", "3"],
                        "dependencies": [],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Exercise 2.2.3",
                        "source_order_anchor": "(3) Let \\( F \\) be a subfield of \\( k \\) .",
                        "source_order_occurrence": 2,
                        "reason": "preserve the second repeated exercise",
                    },
                ],
            },
        )

        self.assertEqual(
            [item["label"] for item in result["repaired_items"]],
            ["Exercise 2.0.3", "Definition 2.1", "Exercise 2.2.3"],
        )

    def test_proof_span_searches_after_content_and_stops_at_next_item_anchor(self) -> None:
        section = MarkdownSection(
            index=3,
            context=SectionContext(
                chapter="Test notes",
                chapter_number="",
                section="3. Boundary section",
                section_number="3",
            ),
            text=(
                "Intro paragraph. In general, this sentence is not a proof.\n\n"
                "Corollary 3.1. A statement with a proof.\n\n"
                "Proof. In general,\n"
                "the proof begins here and should stop before the next item.\n\n"
                "Corollary 3.2. The next statement."
            ),
            start_line=30,
            end_line=38,
            heading_level=3,
            source_heading="3. Boundary section",
        )
        executor = AuditSourceToolExecutor(section, [])

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Repair spans with ambiguous proof anchor.",
                "overall_assessment": "moderate repair",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Corollary 3.1",
                        "env": "cor",
                        "number_components": ["3", "1"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Corollary 3.1. A statement with a proof.",
                            "end_anchor": "Proof. In general,",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": {
                            "start_anchor": "In general,",
                            "end_anchor": None,
                            "start_occurrence": 2,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "preserve_current_label": None,
                        "source_order_anchor": "Corollary 3.1.",
                        "reason": "copy first corollary from source",
                    },
                    {
                        "label": "Corollary 3.2",
                        "env": "cor",
                        "number_components": ["3", "2"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Corollary 3.2. The next statement.",
                            "end_anchor": None,
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": None,
                        "preserve_current_label": None,
                        "source_order_anchor": "Corollary 3.2.",
                        "reason": "copy next corollary from source",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Corollary 3.1", "Corollary 3.2"])
        self.assertEqual(
            items[0]["proof"],
            "In general,\nthe proof begins here and should stop before the next item.",
        )
        self.assertNotIn("Intro paragraph", items[0]["proof"])
        self.assertNotIn("Corollary 3.2", items[0]["proof"])
        self.assertIn("clipped to preserve item boundaries", result["tool_validation"]["warnings"][0])

    def test_end_occurrence_is_resolved_from_section_start(self) -> None:
        section = MarkdownSection(
            index=4,
            context=SectionContext(
                chapter="Test notes",
                chapter_number="",
                section="4. Repeated boundary section",
                section_number="4",
            ),
            text=(
                "Example 1. Earlier content.\n\n"
                "Repeated transition sentence.\n\n"
                "Example 2. Later content that should be extracted.\n\n"
                "Repeated transition sentence.\n\n"
                "Theorem 3. The next item."
            ),
            start_line=40,
            end_line=48,
            heading_level=3,
            source_heading="4. Repeated boundary section",
        )
        executor = AuditSourceToolExecutor(section, [])

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Repair a span with a repeated end anchor.",
                "overall_assessment": "minor repair",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Example 2",
                        "env": "example",
                        "number_components": ["2"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Example 2.",
                            "end_anchor": "Repeated transition sentence.",
                            "start_occurrence": 1,
                            "end_occurrence": 2,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": None,
                        "preserve_current_label": None,
                        "source_order_anchor": "Example 2.",
                        "reason": "copy the later example using the global second end anchor",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Example 2"])
        self.assertEqual(items[0]["content"], "Example 2. Later content that should be extracted.")
        self.assertEqual(result["tool_validation"]["warnings"], [])

    def test_preserved_item_with_non_source_backed_text_is_repaired_from_declared_anchors(self) -> None:
        section = MarkdownSection(
            index=9,
            context=SectionContext(
                chapter="Complete book",
                chapter_number="",
                section="9. Some Closedness Criteria",
                section_number="9",
            ),
            text=(
                "Corollary 9.2.1. Let \\( {f}_{1} \\) satisfy \\( {z}_{1}\\right) \\leq 0.\n\n"
                "Proof. Apply Theorem 9.2. ||\n\n"
                "COROLLARY 9.2.2. Let \\( {f}_{2} \\) satisfy another condition."
            ),
            start_line=100,
            end_line=105,
            heading_level=2,
            source_heading="Some Closedness Criteria",
        )
        current_items = [
            {
                "index": 1,
                "label": "Corollary 9.2.1",
                "env": "cor",
                "number_components": ["9", "2", "1"],
                "context": section.context.as_json(),
                "content": "Corollary 9.2.1. Let \\( {f}_{1} \\) satisfy \\( {{z}_{1}}\\right) \\leq 0.",
                "dependencies": ["Theorem 9.2"],
                "proof": "Apply Theorem 9.2. ||",
            },
            {
                "index": 2,
                "label": "Corollary 9.2.2",
                "env": "cor",
                "number_components": ["9", "2", "2"],
                "context": section.context.as_json(),
                "content": "COROLLARY 9.2.2. Let \\( {f}_{2} \\) satisfy another condition.",
                "dependencies": [],
                "proof": None,
            },
        ]
        executor = AuditSourceToolExecutor(section, current_items)

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Preserve declared items.",
                "overall_assessment": "no change",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Corollary 9.2.1",
                        "env": "cor",
                        "number_components": ["9", "2", "1"],
                        "dependencies": ["Theorem 9.2"],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Corollary 9.2.1",
                        "source_order_anchor": "Corollary 9.2.1.",
                        "reason": "preserve item using source anchors if current text is not source-backed",
                    },
                    {
                        "label": "Corollary 9.2.2",
                        "env": "cor",
                        "number_components": ["9", "2", "2"],
                        "dependencies": [],
                        "content_span": None,
                        "proof_span": None,
                        "preserve_current_label": "Corollary 9.2.2",
                        "source_order_anchor": "COROLLARY 9.2.2.",
                        "reason": "unchanged next item",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Corollary 9.2.1", "Corollary 9.2.2"])
        self.assertIn("\\( {z}_{1}\\right)", items[0]["content"])
        self.assertNotIn("{{z}_{1}}", items[0]["content"])
        self.assertEqual(items[0]["proof"], "Apply Theorem 9.2. ||")
        self.assertEqual(result["tool_validation"]["warnings"], [])

    def test_delayed_proof_span_after_next_anchor_is_kept_with_warning(self) -> None:
        section = MarkdownSection(
            index=2,
            context=SectionContext(
                chapter="Test notes",
                chapter_number="",
                section="2. Inserted remarks",
                section_number="2",
            ),
            text=(
                "Proposition 7.1. A statement whose proof follows an inserted remark.\n\n"
                "Preliminary remark. This is a separate source item before the proof.\n\n"
                "Proof of proposition 7.1. The proof starts after the preliminary remark.\n\n"
                "Note. A note after the proof."
            ),
            start_line=20,
            end_line=26,
            heading_level=2,
            source_heading="Inserted remarks",
        )
        executor = AuditSourceToolExecutor(section, [])

        result = executor.execute(
            "build_repaired_items",
            {
                "audit_markdown": "Proof span crosses an inserted source item.",
                "overall_assessment": "moderate repair",
                "actions": [],
                "open_questions": [],
                "items": [
                    {
                        "label": "Proposition 7.1",
                        "env": "prop",
                        "number_components": ["7", "1"],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Proposition 7.1. A statement",
                            "end_anchor": "Preliminary remark.",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": {
                            "start_anchor": "Proof of proposition 7.1.",
                            "end_anchor": "Note.",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": False,
                            "include_end": False,
                        },
                        "preserve_current_label": None,
                        "source_order_anchor": "Proposition 7.1. A statement",
                        "reason": "keep proposition even if proof span is invalid",
                    },
                    {
                        "label": "Remark 7.1-extra-1",
                        "env": "remark",
                        "number_components": [],
                        "dependencies": [],
                        "content_span": {
                            "start_anchor": "Preliminary remark.",
                            "end_anchor": "Proof of proposition 7.1.",
                            "start_occurrence": 1,
                            "end_occurrence": 1,
                            "include_start": True,
                            "include_end": False,
                        },
                        "proof_span": None,
                        "preserve_current_label": None,
                        "source_order_anchor": "Preliminary remark.",
                        "reason": "separate inserted remark",
                    },
                ],
            },
        )

        items = result["repaired_items"]
        self.assertEqual([item["label"] for item in items], ["Proposition 7.1", "Remark 7.1-extra-1"])
        self.assertEqual(items[0]["proof"], "The proof starts after the preliminary remark.")
        self.assertIn("may be an explicit delayed proof", result["tool_validation"]["warnings"][-1])


if __name__ == "__main__":
    unittest.main()
