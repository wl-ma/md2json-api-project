from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from md2json_api.jobs import JobService, JobStore, WorkerSettings


class JobServiceTests(unittest.TestCase):
    def test_local_job_persists_status_and_public_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(
                    jobs_root=Path(temp) / "jobs",
                    backend="local",
                    model="unused",
                )
            )
            try:
                created = service.create_job(
                    filename="notes.md",
                    markdown=b"## 1 Test section\n\nDefinition 1. A finite set.\n",
                    options={"prompt_profile": "auto", "structure_mode": "hard", "audit_mode": "off"},
                )
                status = _wait_for_terminal_status(service, created["job_id"])
                self.assertEqual(status["status"], "succeeded")
                self.assertEqual(status["sections_completed"], status["sections_total"])
                items = service.result_payload(created["job_id"])
                self.assertIsInstance(items, list)
                self.assertEqual(items[0]["env"], "def")
                report = service.quality_payload(created["job_id"])
                self.assertEqual(report["source_file"], "notes.md")
                self.assertNotIn(temp, str(report))
                self.assertNotIn("output_dir", status)
            finally:
                service.shutdown()

    def test_local_job_rejects_llm_structure_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(
                    jobs_root=Path(temp) / "jobs",
                    backend="local",
                    model="unused",
                )
            )
            try:
                with self.assertRaisesRegex(ValueError, "unavailable with the configured local backend"):
                    service.create_job(
                        filename="notes.md",
                        markdown=b"## 1 Test\n\nDefinition 1. Value.\n",
                        options={"prompt_profile": "auto", "structure_mode": "llm", "audit_mode": "off"},
                    )
            finally:
                service.shutdown()

    def test_queued_job_is_recovered_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jobs_root = Path(temp) / "jobs"
            job_dir = jobs_root / "recoverable"
            input_path = job_dir / "input.md"
            input_path.parent.mkdir(parents=True)
            input_path.write_text("## 1 Test\n\nDefinition 1. Recovered.\n", encoding="utf-8")
            store = JobStore(jobs_root / "jobs.sqlite3")
            store.create(
                job_id="recoverable",
                input_name="recovered.md",
                input_path=input_path,
                output_dir=job_dir / "output",
                options={"prompt_profile": "auto", "structure_mode": "hard", "audit_mode": "off"},
            )
            store.close()

            service = JobService(WorkerSettings(jobs_root=jobs_root, backend="local", model="unused"))
            try:
                service.recover_unfinished_jobs()
                status = _wait_for_terminal_status(service, "recoverable")
                self.assertEqual(status["status"], "succeeded")
                self.assertEqual(service.result_payload("recoverable")[0]["env"], "def")
            finally:
                service.shutdown()

    def test_failed_job_can_be_resubmitted_with_safe_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = JobService(
                WorkerSettings(jobs_root=Path(temp) / "jobs", backend="local", model="unused")
            )
            try:
                created = service.create_job(
                    filename="notes.md",
                    markdown=b"## 1 Test\n\nDefinition 1. Retried.\n",
                    options={"prompt_profile": "auto", "structure_mode": "hard", "audit_mode": "off"},
                )
                self.assertEqual(_wait_for_terminal_status(service, created["job_id"])["status"], "succeeded")
                service.store.update_status(created["job_id"], status="failed", phase="failed")
                resumed = service.resume_failed_job(created["job_id"])
                self.assertIn(resumed["status"], {"queued", "running", "succeeded"})
                self.assertEqual(_wait_for_terminal_status(service, created["job_id"])["status"], "succeeded")
            finally:
                service.shutdown()


def _wait_for_terminal_status(service: JobService, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        payload = service.public_status(job_id)
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Job did not finish before timeout.")


if __name__ == "__main__":
    unittest.main()
