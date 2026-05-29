from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from md2json_api.doc2x_client import Doc2XResult
from md2json_api.doc2x_jobs import Doc2XJobService, Doc2XWorkerSettings
from md2json_api.full_jobs import FullConversionService, FullWorkerSettings
from md2json_api.jobs import WorkerSettings
from md2json_api.runtime import atomic_write_json, atomic_write_text


class FakeDoc2XClient:
    def convert_file(
        self,
        *,
        source_file: Path,
        output_dir: Path,
        options: dict[str, Any],
        progress_callback: Any = None,
    ) -> Doc2XResult:
        if progress_callback is not None:
            progress_callback("waiting_parse", 75)
        markdown_path = output_dir / "output.md"
        json_path = output_dir / "pages.json"
        manifest_path = output_dir / "export_manifest.json"
        atomic_write_text(markdown_path, "## 1 Test\n\nDefinition 1. Service layer.\n")
        atomic_write_json(json_path, {"pages": [{"page_idx": 0, "md": "Definition 1. Service layer."}]})
        atomic_write_json(manifest_path, {"source_file": source_file.name})
        if progress_callback is not None:
            progress_callback("finalizing", 100)
        return Doc2XResult(markdown_path=markdown_path, json_path=json_path, manifest_path=manifest_path)


class Doc2XJobServiceTests(unittest.TestCase):
    def test_doc2x_job_persists_public_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = Doc2XJobService(Doc2XWorkerSettings(jobs_root=Path(temp) / "jobs"), client=FakeDoc2XClient())
            try:
                created = service.create_job(
                    filename="source.pdf",
                    content=b"%PDF-1.4 fake",
                    options={"doc2x_model": "v3-2026", "formula_mode": "normal", "formula_level": "0"},
                )
                status = _wait_doc2x(service, created["job_id"])
                self.assertEqual(status["status"], "succeeded")
                self.assertEqual(status["progress"], 100)
                self.assertIn("Definition 1", service.markdown_text(created["job_id"]))
                self.assertEqual(service.json_payload(created["job_id"])["pages"][0]["page_idx"], 0)
                self.assertNotIn(temp, str(status))
            finally:
                service.shutdown()

    def test_full_job_uses_doc2x_markdown_as_md2json_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            service = FullConversionService(
                FullWorkerSettings(
                    jobs_root=jobs_root,
                    md2json_settings=WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"),
                ),
                doc2x_client=FakeDoc2XClient(),
            )
            try:
                created = service.create_job(
                    filename="source.pdf",
                    content=b"%PDF-1.4 fake",
                    options={
                        "doc2x_model": "v3-2026",
                        "formula_mode": "normal",
                        "formula_level": "0",
                        "prompt_profile": "auto",
                        "structure_mode": "hard",
                        "audit_mode": "off",
                    },
                )
                status = _wait_full(service, created["job_id"])
                self.assertEqual(status["status"], "succeeded")
                self.assertEqual(service.result_payload(created["job_id"])[0]["env"], "def")
                self.assertEqual(service.quality_payload(created["job_id"])["source_file"], "source.pdf")
            finally:
                service.shutdown()


def _wait_doc2x(service: Doc2XJobService, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        payload = service.public_status(job_id)
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Job did not finish before timeout.")


def _wait_full(service: FullConversionService, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        payload = service.public_status(job_id)
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Job did not finish before timeout.")


if __name__ == "__main__":
    unittest.main()
