from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from md2json_api.doc2x_client import Doc2XClient, Doc2XSettings


class FakeImageDoc2XClient(Doc2XClient):
    def __init__(self) -> None:
        super().__init__(
            Doc2XSettings(
                api_key="test-key",
                base_url="https://doc2x.example.test",
                image_endpoint="/api/v2/async/parse/img/layout",
                image_status_endpoint="/api/v2/parse/img/layout/status",
            )
        )
        self.calls: list[dict[str, Any]] = []

    def _post_binary(
        self,
        path: str,
        body: bytes,
        *,
        content_type: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"path": path, "body": body, "content_type": content_type, "params": params})
        return {
            "code": "success",
            "data": {
                "uid": "image-task-1",
            },
        }

    def _get_json(self, path: str) -> dict[str, Any]:
        self.calls.append({"path": path, "method": "GET"})
        return {
            "code": "success",
            "data": {
                "status": "success",
                "result": {
                    "pages": [
                        {
                            "page_idx": 0,
                            "md": "Definition 1. From image.",
                        }
                    ]
                },
            },
        }


class Doc2XImageClientTests(unittest.TestCase):
    def test_image_file_posts_binary_and_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_path = root / "source.png"
            image_path.write_bytes(b"\x89PNG fake")
            client = FakeImageDoc2XClient()

            result = client.convert_image_file(
                source_file=image_path,
                output_dir=root / "out",
                options={"doc2x_model": "v3-2026"},
            )

            self.assertEqual(client.calls[0]["content_type"], "image/png")
            self.assertEqual(client.calls[0]["path"], "/api/v2/async/parse/img/layout")
            self.assertEqual(client.calls[1]["path"], "/api/v2/parse/img/layout/status?uid=image-task-1")
            self.assertIn("Definition 1", result.markdown_path.read_text(encoding="utf-8"))
            self.assertTrue(result.json_path.exists())
            self.assertTrue(result.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
