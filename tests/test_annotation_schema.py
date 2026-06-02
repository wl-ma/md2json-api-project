from __future__ import annotations

import unittest

from md2json_api.annotation_schema import to_annotation_document


class AnnotationSchemaTests(unittest.TestCase):
    def test_converts_legacy_items_to_annotation_v1(self) -> None:
        payload = to_annotation_document(
            result_payload=[
                {
                    "index": 1,
                    "label": "Lemma 1.1",
                    "env": "lemma",
                    "content": "Lemma 1.1. Value.",
                    "dependencies": [{"label": "Definition 1.1", "target_id": None, "resolved": False}],
                    "proof": None,
                    "context": {"chapter": "Intro", "chapter_number": "1", "section": "Basics"},
                }
            ],
            quality_payload={"global_warnings": ["check document"]},
            filename="notes.md",
            source_type="markdown",
        )

        self.assertEqual(payload["schema_version"], "md2json.annotation.v1")
        self.assertEqual(payload["source"]["source_type"], "markdown")
        item = payload["items"][0]
        self.assertEqual(item["id"], "item_000001")
        self.assertEqual(item["type"], "lemma")
        self.assertEqual(item["statement"], "Lemma 1.1. Value.")
        self.assertEqual(item["context"]["chapter_title"], "Intro")
        self.assertEqual(item["dependencies"][0]["label"], "Definition 1.1")
        self.assertEqual(item["assets"]["caption"], "")
        self.assertEqual(item["audit"]["issues"][0]["code"], "unresolved_dependency")
        self.assertGreaterEqual(payload["quality"]["warning_count"], 1)

    def test_converts_rich_items_with_assets_and_source_refs(self) -> None:
        payload = to_annotation_document(
            result_payload=[
                {
                    "id": 7,
                    "label": "figure_0007",
                    "env": "figure",
                    "statement": "",
                    "image": {"path": "figures/f1.png"},
                    "caption": "A figure",
                    "source": {"pages": [3], "bbox_refs": [{"page": 3, "block_id": "b1", "bbox": [1, 2, 3, 4]}]},
                }
            ],
            quality_payload=None,
            filename="book.pdf",
            source_type="pdf",
        )

        item = payload["items"][0]
        self.assertEqual(item["id"], "item_000007")
        self.assertEqual(item["assets"]["image_path"], "figures/f1.png")
        self.assertEqual(item["assets"]["caption"], "A figure")
        self.assertEqual(item["source_refs"]["pages"], [3])
        self.assertEqual(item["source_refs"]["bbox_refs"][0]["bbox"], [1, 2, 3, 4])
        self.assertEqual(item["audit"]["issues"][0]["severity"], "error")


if __name__ == "__main__":
    unittest.main()
