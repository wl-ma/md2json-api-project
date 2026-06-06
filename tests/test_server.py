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
    def test_legacy_public_conversion_routes_are_not_registered(self) -> None:
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
                    self.assertEqual(client.post("/v1/conversions").status_code, 404)
                    self.assertEqual(client.get("/v1/conversions/unknown").status_code, 404)
                    self.assertEqual(client.post("/v1/conversions/unknown/resume").status_code, 404)
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
                    rejected = client.put(
                        f"/v1/source-conversions/{job_id}/annotation",
                        headers={"Authorization": "Bearer test-token"},
                        json={**payload, "schema_version": "legacy"},
                    )
                    self.assertEqual(rejected.status_code, 400)
                    payload["items"][0]["statement"] = "Edited Definition 1. Value."
                    saved = client.put(
                        f"/v1/source-conversions/{job_id}/annotation",
                        headers={"Authorization": "Bearer test-token"},
                        json=payload,
                    )
                    self.assertEqual(saved.status_code, 200)
                    self.assertTrue(saved.json()["saved"])
                    self.assertEqual(saved.json()["item_count"], 1)
                    annotation = client.get(
                        f"/v1/source-conversions/{job_id}/annotation",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(annotation.status_code, 200)
                    self.assertEqual(annotation.json()["items"][0]["statement"], "Edited Definition 1. Value.")
                    updated_result = client.get(
                        f"/v1/source-conversions/{job_id}/result",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(updated_result.json()["items"][0]["statement"], "Edited Definition 1. Value.")
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
                    listed = client.get(
                        "/v1/source-conversions?source_type=markdown&status=succeeded&limit=10",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(listed.status_code, 200)
                    self.assertTrue(any(item["job_id"] == job_id for item in listed.json()))
            finally:
                service.shutdown()

    def test_annotation_documents_support_direct_json_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(
                    jobs_root=Path(temp) / "jobs",
                    backend="local",
                    model="unused",
                )
            )
            app = create_app(service, api_token="test-token")
            payload = {
                "schema_version": "md2json.annotation.v1",
                "source": {"filename": "chapter.json", "source_type": "markdown"},
                "document": {"title": "", "language": "", "chapters": []},
                "items": [
                    {
                        "id": "item_000001",
                        "order_index": 1,
                        "type": "def",
                        "label": "Definition 1",
                        "statement": "Definition 1. Value.",
                        "proof": "",
                        "dependencies": [],
                        "source_refs": {"pages": [], "block_ids": [], "span_ids": [], "bbox_refs": []},
                        "assets": {"image_path": "", "caption": "", "table_markdown": ""},
                        "audit": {"modified": False, "issues": []},
                    }
                ],
                "quality": {"error_count": 0, "warning_count": 0, "issues": []},
            }
            try:
                with TestClient(app) as client:
                    created = client.post(
                        "/v1/annotation-documents",
                        headers={"Authorization": "Bearer test-token"},
                        files={"file": ("chapter.json", __import__("json").dumps(payload).encode("utf-8"), "application/json")},
                    )
                    self.assertEqual(created.status_code, 200)
                    annotation_id = created.json()["annotation_id"]
                    listed = client.get(
                        "/v1/annotation-documents?limit=10",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(listed.status_code, 200)
                    self.assertTrue(any(item["annotation_id"] == annotation_id for item in listed.json()))
                    fetched = client.get(
                        f"/v1/annotation-documents/{annotation_id}",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(fetched.status_code, 200)
                    self.assertEqual(fetched.json()["items"][0]["statement"], "Definition 1. Value.")
                    payload["items"][0]["statement"] = "Edited Definition 1. Value."
                    updated = client.put(
                        f"/v1/annotation-documents/{annotation_id}",
                        headers={"Authorization": "Bearer test-token"},
                        json=payload,
                    )
                    self.assertEqual(updated.status_code, 200)
                    self.assertTrue(updated.json()["saved"])
                    refetched = client.get(
                        f"/v1/annotation-documents/{annotation_id}",
                        headers={"Authorization": "Bearer test-token"},
                    )
                    self.assertEqual(refetched.json()["items"][0]["statement"], "Edited Definition 1. Value.")
            finally:
                service.shutdown()


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
