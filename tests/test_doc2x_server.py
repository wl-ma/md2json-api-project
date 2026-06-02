from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

try:
    from fastapi.testclient import TestClient

    from md2json_api.doc2x_client import Doc2XResult
    from md2json_api.doc2x_jobs import Doc2XJobService, Doc2XWorkerSettings
    from md2json_api.full_jobs import FullConversionService, FullWorkerSettings
    from md2json_api.jobs import JobService, WorkerSettings
    from md2json_api.runtime import atomic_write_json, atomic_write_text
    from md2json_api.server import create_app
except ModuleNotFoundError:
    TestClient = None


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
            progress_callback("waiting_parse", 50)
        markdown_path = output_dir / "output.md"
        json_path = output_dir / "pages.json"
        manifest_path = output_dir / "export_manifest.json"
        atomic_write_text(markdown_path, "## 1 Test\n\nDefinition 1. From PDF.\n")
        atomic_write_json(
            json_path,
            {"pages": [{"page_idx": 0, "md": "## 1 Test\n\nDefinition 1. From PDF.\n"}]},
        )
        atomic_write_json(
            manifest_path,
            {"source_file": source_file.name, "markdown_source": "fake", "model": options["doc2x_model"]},
        )
        if progress_callback is not None:
            progress_callback("finalizing", 100)
        return Doc2XResult(markdown_path=markdown_path, json_path=json_path, manifest_path=manifest_path)


@unittest.skipIf(TestClient is None, "API optional dependencies are not installed.")
class Doc2XServerTests(unittest.TestCase):
    def test_doc2x_conversion_exposes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            markdown_service = JobService(WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"))
            doc2x_service = Doc2XJobService(Doc2XWorkerSettings(jobs_root=jobs_root), client=FakeDoc2XClient())
            full_service = FullConversionService(
                FullWorkerSettings(
                    jobs_root=jobs_root,
                    md2json_settings=WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"),
                ),
                doc2x_client=FakeDoc2XClient(),
            )
            app = create_app(markdown_service, doc2x_service, full_service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/doc2x-conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("source.pdf", b"%PDF-1.4 fake", "application/pdf")},
                    )
                    self.assertEqual(response.status_code, 202)
                    job_id = response.json()["job_id"]
                    terminal = _poll(client, f"/v1/doc2x-conversions/{job_id}")
                    self.assertEqual(terminal["status"], "succeeded")

                    markdown = client.get(
                        f"/v1/doc2x-conversions/{job_id}/markdown",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(markdown.status_code, 200)
                    self.assertIn("Definition 1", markdown.text)

                    doc2x_json = client.get(
                        f"/v1/doc2x-conversions/{job_id}/json",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(doc2x_json.status_code, 200)
                    self.assertEqual(doc2x_json.json()["pages"][0]["page_idx"], 0)
                    usage = client.get(
                        f"/v1/doc2x-conversions/{job_id}/usage",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(usage.status_code, 200)
                    self.assertEqual(usage.json()["requests"], 1)
                    self.assertNotIn(temp, json.dumps(terminal))
            finally:
                markdown_service.shutdown()
                doc2x_service.shutdown()
                full_service.shutdown()

    def test_full_conversion_runs_doc2x_then_md2json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            markdown_service = JobService(WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"))
            doc2x_service = Doc2XJobService(Doc2XWorkerSettings(jobs_root=jobs_root), client=FakeDoc2XClient())
            full_service = FullConversionService(
                FullWorkerSettings(
                    jobs_root=jobs_root,
                    md2json_settings=WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"),
                ),
                doc2x_client=FakeDoc2XClient(),
            )
            app = create_app(markdown_service, doc2x_service, full_service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/full-conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("source.pdf", b"%PDF-1.4 fake", "application/pdf")},
                        data={"structure_mode": "hard", "audit_mode": "off"},
                    )
                    self.assertEqual(response.status_code, 202)
                    job_id = response.json()["job_id"]
                    terminal = _poll(client, f"/v1/full-conversions/{job_id}")
                    self.assertEqual(terminal["status"], "succeeded")
                    self.assertEqual(terminal["sections_completed"], terminal["sections_total"])

                    result = client.get(
                        f"/v1/full-conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()[0]["env"], "def")

                    quality = client.get(
                        f"/v1/full-conversions/{job_id}/quality",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(quality.status_code, 200)
                    self.assertEqual(quality.json()["source_file"], "source.pdf")
                    usage = client.get(
                        f"/v1/full-conversions/{job_id}/usage",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(usage.status_code, 200)
                    self.assertIn("md2json", usage.json()["phases"])
            finally:
                markdown_service.shutdown()
                doc2x_service.shutdown()
                full_service.shutdown()

    def test_source_conversion_returns_annotation_schema_for_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            markdown_service = JobService(WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"))
            doc2x_service = Doc2XJobService(Doc2XWorkerSettings(jobs_root=jobs_root), client=FakeDoc2XClient())
            full_service = FullConversionService(
                FullWorkerSettings(
                    jobs_root=jobs_root,
                    md2json_settings=WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"),
                ),
                doc2x_client=FakeDoc2XClient(),
            )
            app = create_app(markdown_service, doc2x_service, full_service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/source-conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("source.pdf", b"%PDF-1.4 fake", "application/pdf")},
                        data={"structure_mode": "hard", "audit_mode": "off"},
                    )
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.json()["source_type"], "pdf")
                    job_id = response.json()["job_id"]
                    terminal = _poll(client, f"/v1/source-conversions/{job_id}")
                    self.assertEqual(terminal["status"], "succeeded")
                    self.assertEqual(terminal["source_type"], "pdf")

                    result = client.get(
                        f"/v1/source-conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(result.status_code, 200)
                    payload = result.json()
                    self.assertEqual(payload["schema_version"], "md2json.annotation.v1")
                    self.assertEqual(payload["source"]["filename"], "source.pdf")
                    self.assertEqual(payload["source"]["source_type"], "pdf")
                    self.assertEqual(payload["items"][0]["type"], "def")
                    self.assertIn("error_count", payload["quality"])
            finally:
                markdown_service.shutdown()
                doc2x_service.shutdown()
                full_service.shutdown()

    def test_source_conversion_returns_annotation_schema_for_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            markdown_service = JobService(WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"))
            doc2x_service = Doc2XJobService(Doc2XWorkerSettings(jobs_root=jobs_root), client=FakeDoc2XClient())
            full_service = FullConversionService(
                FullWorkerSettings(
                    jobs_root=jobs_root,
                    md2json_settings=WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"),
                ),
                doc2x_client=FakeDoc2XClient(),
            )
            app = create_app(markdown_service, doc2x_service, full_service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/source-conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("source.png", b"\x89PNG fake", "image/png")},
                        data={"structure_mode": "hard", "audit_mode": "off"},
                    )
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.json()["source_type"], "image")
                    job_id = response.json()["job_id"]
                    self.assertTrue(job_id.startswith("image_"))
                    terminal = _poll(client, f"/v1/source-conversions/{job_id}")
                    self.assertEqual(terminal["status"], "succeeded")
                    self.assertEqual(terminal["source_type"], "image")

                    result = client.get(
                        f"/v1/source-conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(result.status_code, 200)
                    payload = result.json()
                    self.assertEqual(payload["schema_version"], "md2json.annotation.v1")
                    self.assertEqual(payload["source"]["filename"], "source.png")
                    self.assertEqual(payload["source"]["source_type"], "image")
                    self.assertEqual(payload["items"][0]["type"], "def")
            finally:
                markdown_service.shutdown()
                doc2x_service.shutdown()
                full_service.shutdown()


def _poll(client: TestClient, path: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(path, headers={"Authorization": "Bearer test-token"})
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Job did not finish before timeout.")


if __name__ == "__main__":
    unittest.main()
