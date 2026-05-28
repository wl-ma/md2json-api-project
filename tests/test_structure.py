from __future__ import annotations

import unittest

from md2json_api.models import MarkdownSection, SectionContext
from md2json_api.splitter import SplitPlan
from md2json_api.structure import looks_like_bare_section_candidate, split_plan_from_structure_plan


class StructurePlannerTests(unittest.TestCase):
    def test_bare_section_candidate_rejects_body_enumerators(self) -> None:
        self.assertTrue(looks_like_bare_section_candidate("1.1 滤子", chapter_number="1"))
        self.assertFalse(
            looks_like_bare_section_candidate(
                "1. 首先,对任意 \\( A \\) 上的滤子基 \\( \\mathfrak{B} \\) 定义",
                chapter_number="1",
            )
        )
        self.assertFalse(
            looks_like_bare_section_candidate(
                "F. 1 若 \\( A, B \\in \\mathfrak{F} \\) ,则 \\( A \\cap B \\in \\mathfrak{F} \\)",
                chapter_number="1",
            )
        )
        self.assertFalse(
            looks_like_bare_section_candidate(
                "6 给定了素数 \\( p \\) ,对域 \\( \\mathbb{Q} \\) 的任意元素可以考虑赋值",
                chapter_number="1",
            )
        )

    def test_explicit_empty_chapter_number_does_not_fallback(self) -> None:
        fallback = SplitPlan(
            sections=[
                MarkdownSection(
                    index=1,
                    context=SectionContext(
                        chapter="Chapter VIII. Convex Algebra",
                        chapter_number="VIII",
                        section="1. Inferred pre-section material",
                        section_number="1",
                    ),
                    text="fallback",
                    start_line=1,
                    end_line=3,
                    heading_level=None,
                    source_heading="fallback",
                )
            ]
        )
        plan = {
            "chapter": "Complete book",
            "chapter_number": "",
            "sections": [
                {
                    "section_number": "1",
                    "section_title": "Affine Sets",
                    "start_line": 1,
                    "end_line": 3,
                    "heading_source": "## SECTION 1 Affine Sets",
                    "confidence": "high",
                    "reason": "test",
                }
            ],
            "warnings": [],
        }

        split_plan = split_plan_from_structure_plan(
            source_text="## SECTION 1 Affine Sets\n\nBody",
            source_name="book.md",
            plan=plan,
            fallback_plan=fallback,
        )

        self.assertEqual(split_plan.sections[0].context.chapter, "Complete book")
        self.assertEqual(split_plan.sections[0].context.chapter_number, "")

    def test_structure_plan_can_set_section_level_chapters(self) -> None:
        fallback = SplitPlan(
            sections=[
                MarkdownSection(
                    index=1,
                    context=SectionContext(
                        chapter="Complete book",
                        chapter_number="",
                        section="I.1. Formal Power Series",
                        section_number="I.1",
                    ),
                    text="fallback",
                    start_line=1,
                    end_line=5,
                    heading_level=None,
                    source_heading="fallback",
                )
            ]
        )
        plan = {
            "chapter": "Complete book",
            "chapter_number": "",
            "sections": [
                {
                    "section_number": "I.1",
                    "section_title": "Formal Power Series",
                    "chapter": "Chapter I. Power Series in One Variable",
                    "chapter_number": "I",
                    "start_line": 2,
                    "end_line": 3,
                    "heading_source": "## 1. Formal Power Series",
                    "confidence": "high",
                    "reason": "test",
                },
                {
                    "section_number": "II.1",
                    "section_title": "Curvilinear Integrals",
                    "chapter": "Chapter II. Holomorphic Functions, Cauchy's Integral",
                    "chapter_number": "II",
                    "start_line": 4,
                    "end_line": 5,
                    "heading_source": "## 1. Curvilinear Integrals",
                    "confidence": "high",
                    "reason": "test",
                },
            ],
            "warnings": [],
        }

        split_plan = split_plan_from_structure_plan(
            source_text=(
                "# Book\n"
                "## 1. Formal Power Series\n"
                "Body\n"
                "## 1. Curvilinear Integrals\n"
                "Body\n"
            ),
            source_name="book.md",
            plan=plan,
            fallback_plan=fallback,
        )

        self.assertEqual(split_plan.sections[0].context.chapter, "Chapter I. Power Series in One Variable")
        self.assertEqual(split_plan.sections[0].context.chapter_number, "I")
        self.assertEqual(split_plan.sections[0].context.section_number, "I.1")
        self.assertEqual(split_plan.sections[1].context.chapter, "Chapter II. Holomorphic Functions, Cauchy's Integral")
        self.assertEqual(split_plan.sections[1].context.chapter_number, "II")
        self.assertEqual(split_plan.sections[1].context.section_number, "II.1")

    def test_structure_plan_qualifies_local_section_numbers_with_chapter(self) -> None:
        fallback = SplitPlan(sections=[])
        plan = {
            "chapter": "Complete book",
            "chapter_number": "",
            "sections": [
                {
                    "section_number": "1",
                    "section_title": "Formal Power Series",
                    "chapter": "Power Series in One Variable",
                    "chapter_number": "I",
                    "start_line": 1,
                    "end_line": 2,
                    "heading_source": "## 1. Formal Power Series",
                    "confidence": "high",
                    "reason": "test",
                }
            ],
            "warnings": [],
        }

        split_plan = split_plan_from_structure_plan(
            source_text="## 1. Formal Power Series\nBody\n",
            source_name="book.md",
            plan=plan,
            fallback_plan=fallback,
        )

        self.assertEqual(split_plan.sections[0].context.section_number, "I.1")


if __name__ == "__main__":
    unittest.main()
