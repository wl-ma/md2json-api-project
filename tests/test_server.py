from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient

    from md2json_api.jobs import JobService, WorkerSettings
    from md2json_api.server import create_app
except ModuleNotFoundError:
    TestClient = None


@unittest.skipIf(TestClient is None, "API optional dependencies are not installed.")
class ServerTests(unittest.TestCase):
    def test_authenticated_conversion_exposes_only_public_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(
                    jobs_root=Path(temp) / "jobs",
                    backend="local",
                    model="unused",
                )
            )
            app = create_app(service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    unauthenticated = client.post(
                        "/v1/conversions",
                        files={"file": ("notes.md", b"## 1 Test\n\nDefinition 1. Value.\n", "text/markdown")},
                    )
                    self.assertEqual(unauthenticated.status_code, 401)
                    response = client.post(
                        "/v1/conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("notes.md", b"## 1 Test\n\nDefinition 1. Value.\n", "text/markdown")},
                        data={"structure_mode": "hard", "audit_mode": "off"},
                    )
                    self.assertEqual(response.status_code, 202)
                    job_id = response.json()["job_id"]
                    terminal = _poll(client, job_id)
                    self.assertEqual(terminal["status"], "succeeded")
                    result = client.get(
                        f"/v1/conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()[0]["env"], "def")
                    quality = client.get(
                        f"/v1/conversions/{job_id}/quality",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(quality.json()["source_file"], "notes.md")
                    self.assertNotIn(temp, quality.text)
                    self.assertNotIn("output_dir", terminal)
                    service.store.update_status(job_id, status="failed", phase="failed")
                    resumed = client.post(
                        f"/v1/conversions/{job_id}/resume",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(resumed.status_code, 202)
                    self.assertEqual(_poll(client, job_id)["status"], "succeeded")
            finally:
                service.shutdown()

    def test_source_conversion_returns_annotation_schema_for_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(
                    jobs_root=Path(temp) / "jobs",
                    backend="local",
                    model="unused",
                )
            )
            app = create_app(service, api_token="test-token")
            try:
                with TestClient(app) as client:
                    response = client.post(
                        "/v1/source-conversions",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("notes.md", b"## 1 Test\n\nDefinition 1. Value.\n", "text/markdown")},
                        data={"structure_mode": "hard", "audit_mode": "off"},
                    )
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.json()["source_type"], "markdown")
                    job_id = response.json()["job_id"]
                    terminal = _poll_source(client, job_id)
                    self.assertEqual(terminal["status"], "succeeded")
                    result = client.get(
                        f"/v1/source-conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(result.status_code, 200)
                    payload = result.json()
                    self.assertEqual(payload["schema_version"], "md2json.annotation.v1")
                    self.assertEqual(payload["source"]["source_type"], "markdown")
                    self.assertEqual(payload["items"][0]["type"], "def")
                    self.assertIn("issues", payload["items"][0]["audit"])
                    quality = client.get(
                        f"/v1/source-conversions/{job_id}/quality",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(quality.status_code, 200)
                    self.assertIn("warning_count", quality.json())
                    usage = client.get(
                        f"/v1/source-conversions/{job_id}/usage",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(usage.status_code, 200)
            finally:
                service.shutdown()


def _poll(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(
            f"/v1/conversions/{job_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Job did not finish before timeout.")


def _poll_source(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(
            f"/v1/source-conversions/{job_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Source job did not finish before timeout.")


if __name__ == "__main__":
    unittest.main()
