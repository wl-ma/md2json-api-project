from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from md2json_api.converter import ConverterConfig, MarkdownJsonConverter
from md2json_api.runtime import output_directory_lock


class RuntimeSafetyTests(unittest.TestCase):
    def test_resume_rejects_changed_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.md"
            output = root / "output"
            source.write_text("## 1 Test\n\nDefinition 1. First definition.\n", encoding="utf-8")
            config = ConverterConfig(backend="local", structure_mode="hard", audit_mode="off")
            MarkdownJsonConverter(config).convert(source, output)

            source.write_text("## 1 Test\n\nTheorem 1. Changed statement.\n", encoding="utf-8")
            resumed = ConverterConfig(backend="local", structure_mode="hard", audit_mode="off", resume=True)
            with self.assertRaisesRegex(RuntimeError, "input document or conversion settings differ"):
                MarkdownJsonConverter(resumed).convert(source, output)

    def test_output_directory_lock_prevents_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            with output_directory_lock(output):
                with self.assertRaisesRegex(RuntimeError, "Another conversion"):
                    with output_directory_lock(output):
                        pass

    def test_resume_rejects_legacy_cache_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.md"
            output = root / "output"
            source.write_text("## 1 Test\n\nDefinition 1. Value.\n", encoding="utf-8")
            response = output / "api_calls" / "section01_response.json"
            response.parent.mkdir(parents=True)
            response.write_text(json.dumps({"items": []}), encoding="utf-8")
            config = ConverterConfig(backend="local", structure_mode="hard", audit_mode="off", resume=True)
            with self.assertRaisesRegex(RuntimeError, "Cannot safely resume legacy output"):
                MarkdownJsonConverter(config).convert(source, output)


if __name__ == "__main__":
    unittest.main()
