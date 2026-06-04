from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from md2json_api.doc2x_client import Doc2XClient, Doc2XSettings


class FakePDFDoc2XClient(Doc2XClient):
    def __init__(self) -> None:
        super().__init__(
            Doc2XSettings(
                api_key="test-key",
                base_url="https://doc2x.example.test",
                timeout=1,
                poll_interval=0,
            )
        )
        self.post_json_calls: list[dict[str, Any]] = []
        self.put_files: list[Path] = []

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_json_calls.append({"path": path, "payload": payload})
        if path == "/api/v2/parse/preupload":
            return {
                "code": "success",
                "data": {
                    "uid": "pdf-task-1",
                    "url": "https://upload.example.test/pdf-task-1",
                },
            }
        if path == "/api/v2/convert/parse":
            return {
                "code": "success",
                "data": {
                    "status": "processing",
                    "url": "",
                },
            }
        raise AssertionError(f"Unexpected POST path: {path}")

    def _put_file(self, url: str, source_file: Path) -> None:
        self.put_files.append(source_file)

    def _poll_parse_status(self, uid: str, progress_callback: Any) -> dict[str, Any]:
        return {
            "code": "success",
            "data": {
                "status": "success",
                "result": {"pages": [{"page_idx": 0, "md": "Definition 1. Parsed."}]},
            },
        }

    def _poll_export_status(self, uid: str, progress_callback: Any) -> dict[str, Any]:
        return {
            "code": "success",
            "data": {
                "status": "success",
                "url": "https://download.example.test/export.zip",
            },
        }

    def _download(self, url: str) -> bytes:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("output.md", "Definition 1. Exported.")
        return archive.getvalue()


class Doc2XPDFClientTests(unittest.TestCase):
    def test_pdf_export_payload_matches_doc2x_v2_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            long_name = "a" * 80
            source_path = root / f"{long_name}.pdf"
            source_path.write_bytes(b"%PDF-1.4 fake")
            client = FakePDFDoc2XClient()

            result = client.convert_file(
                source_file=source_path,
                output_dir=root / "out",
                options={
                    "doc2x_model": "v3-2026",
                    "formula_mode": "normal",
                    "merge_cross_page_forms": False,
                    "formula_level": "0",
                },
            )

            export_payload = client.post_json_calls[1]["payload"]
            self.assertEqual(client.post_json_calls[1]["path"], "/api/v2/convert/parse")
            self.assertEqual(export_payload["uid"], "pdf-task-1")
            self.assertEqual(export_payload["to"], "md")
            self.assertEqual(export_payload["formula_mode"], "normal")
            self.assertEqual(export_payload["filename"], "a" * 50)
            self.assertIs(export_payload["merge_cross_page_forms"], False)
            self.assertEqual(export_payload["formula_level"], 0)
            self.assertIsInstance(export_payload["formula_level"], int)
            self.assertIn("Definition 1", result.markdown_path.read_text(encoding="utf-8"))

    def test_pdf_export_filename_is_safe_and_limited_by_utf8_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "章节:第一部分_非法?名称_很长很长很长很长很长.pdf"
            source_path.write_bytes(b"%PDF-1.4 fake")
            client = FakePDFDoc2XClient()

            client.convert_file(
                source_file=source_path,
                output_dir=root / "out",
                options={
                    "doc2x_model": "v3-2026",
                    "formula_mode": "normal",
                    "merge_cross_page_forms": False,
                    "formula_level": 0,
                },
            )

            export_filename = client.post_json_calls[1]["payload"]["filename"]
            self.assertLessEqual(len(export_filename.encode("utf-8")), 50)
            self.assertNotIn("?", export_filename)
            self.assertNotIn(":", export_filename)
            self.assertIn("_", export_filename)
            export_filename.encode("utf-8").decode("utf-8")


if __name__ == "__main__":
    unittest.main()
