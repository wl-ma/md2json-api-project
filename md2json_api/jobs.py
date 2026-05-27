from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .converter import ConverterConfig, MarkdownJsonConverter
from .runtime import atomic_write_bytes


JOB_STATUSES = {"queued", "running", "succeeded", "failed"}
PROMPT_PROFILES = {"auto", "textbook", "paper", "chinese_math"}
STRUCTURE_MODES = {"auto", "llm", "hard"}
AUDIT_MODES = {"auto", "llm", "off"}


@dataclass(frozen=True)
class WorkerSettings:
    jobs_root: Path
    backend: str
    model: str
    base_url: str | None = None
    azure_endpoint: str | None = None
    azure_api_version: str = "2024-10-21"
    max_output_tokens: int | None = None
    llm_timeout: float = 600
    max_workers: int = 1

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        jobs_root = Path(os.environ.get("MD2JSON_JOBS_ROOT", "var/jobs")).expanduser().resolve()
        max_output = os.environ.get("MD2JSON_MAX_OUTPUT_TOKENS")
        return cls(
            jobs_root=jobs_root,
            backend=os.environ.get("MD2JSON_SERVER_BACKEND", "openai"),
            model=os.environ.get("MD2JSON_MODEL", "gpt-5.2"),
            base_url=os.environ.get("OPENAI_BASE_URL"),
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),
            azure_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            max_output_tokens=int(max_output) if max_output else None,
            llm_timeout=float(os.environ.get("MD2JSON_LLM_TIMEOUT", "600")),
            max_workers=max(1, int(os.environ.get("MD2JSON_WORKERS", "1"))),
        )

    def converter_config(self, options: dict[str, str], *, resume: bool) -> ConverterConfig:
        if self.backend == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("Server OpenAI backend is not configured.")
        elif self.backend == "azure":
            api_key = os.environ.get("AZURE_OPENAI_API_KEY")
            if not api_key or not self.azure_endpoint:
                raise RuntimeError("Server Azure backend is not configured.")
        elif self.backend in {"local", "mock"}:
            api_key = None
        else:
            raise RuntimeError("Unsupported server backend configuration.")
        return ConverterConfig(
            backend=self.backend,
            model=self.model,
            api_key=api_key,
            base_url=self.base_url,
            azure_endpoint=self.azure_endpoint,
            azure_api_version=self.azure_api_version,
            max_output_tokens=self.max_output_tokens,
            llm_timeout=self.llm_timeout,
            prompt_profile=options["prompt_profile"],
            structure_mode=options["structure_mode"],
            audit_mode=options["audit_mode"],
            resume=resume,
        )


class JobStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    sections_total INTEGER,
                    sections_completed INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL,
                    result_path TEXT,
                    quality_path TEXT,
                    error_internal TEXT
                )
                """
            )

    def create(self, *, job_id: str, input_name: str, input_path: Path, output_dir: Path, options: dict[str, str]) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs (
                    id, status, created_at, updated_at, input_name, input_path, output_dir,
                    options_json, sections_completed, phase
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, 0, 'queued')
                """,
                (job_id, now, now, input_name, str(input_path), str(output_dir), json.dumps(options)),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_payload(row) if row is not None else None

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def update_status(self, job_id: str, *, status: str, phase: str, error_internal: str | None = None) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE jobs SET status = ?, phase = ?, updated_at = ?, error_internal = ? WHERE id = ?",
                (status, phase, _now(), error_internal, job_id),
            )

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET phase = ?, sections_total = ?, sections_completed = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(progress["phase"]),
                    int(progress["sections_total"]),
                    int(progress["sections_completed"]),
                    _now(),
                    job_id,
                ),
            )

    def queue_failed_for_resume(self, job_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', phase = 'resuming', updated_at = ?, error_internal = NULL
                WHERE id = ? AND status = 'failed'
                """,
                (_now(), job_id),
            )
        return cursor.rowcount == 1

    def complete(self, job_id: str, *, result_path: Path, quality_path: Path) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', phase = 'completed', updated_at = ?,
                    result_path = ?, quality_path = ?, error_internal = NULL
                WHERE id = ?
                """,
                (_now(), str(result_path), str(quality_path), job_id),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class JobService:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.settings.jobs_root.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.settings.jobs_root / "jobs.sqlite3")
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="md2json-worker")

    def create_job(self, *, filename: str, markdown: bytes, options: dict[str, str]) -> dict[str, Any]:
        validated = validate_options(options)
        if self.settings.backend == "local" and validated["structure_mode"] == "llm":
            raise ValueError("structure_mode=llm is unavailable with the configured local backend.")
        job_id = uuid.uuid4().hex
        job_dir = self.settings.jobs_root / job_id
        input_path = job_dir / "input.md"
        output_dir = job_dir / "output"
        atomic_write_bytes(input_path, markdown)
        self.store.create(
            job_id=job_id,
            input_name=Path(filename or "input.md").name,
            input_path=input_path,
            output_dir=output_dir,
            options=validated,
        )
        self._executor.submit(self._run_job, job_id, False)
        return self.public_status(job_id)

    def recover_unfinished_jobs(self) -> None:
        for job in self.store.unfinished():
            self.store.update_status(job["id"], status="queued", phase="recovering")
            self._executor.submit(self._run_job, job["id"], True)

    def resume_failed_job(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        if job["status"] != "failed" or not self.store.queue_failed_for_resume(job_id):
            raise JobNotRetryableError(self._required(job_id)["status"])
        self._executor.submit(self._run_job, job_id, True)
        return self.public_status(job_id)

    def public_status(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        payload = {
            "job_id": job["id"],
            "status": job["status"],
            "phase": job["phase"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "input_name": job["input_name"],
            "sections_total": job["sections_total"],
            "sections_completed": job["sections_completed"],
        }
        if job["status"] == "failed":
            payload["error"] = "Conversion failed. Contact the service operator with the job_id."
        return payload

    def result_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        return json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))

    def quality_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        payload = json.loads(Path(job["quality_path"]).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["source_file"] = job["input_name"]
        return payload

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
        self.store.close()

    def _run_job(self, job_id: str, resume: bool) -> None:
        job = self._required(job_id)
        self.store.update_status(job_id, status="running", phase="starting")
        try:
            config = self.settings.converter_config(job["options"], resume=resume)
            result = MarkdownJsonConverter(config).convert(
                Path(job["input_path"]),
                Path(job["output_dir"]),
                progress_callback=lambda progress: self.store.update_progress(job_id, progress),
            )
            result_path = result.out_dir / f"{result.source_file.stem}.json"
            quality_path = result.out_dir / "quality_report.json"
            self.store.complete(job_id, result_path=result_path, quality_path=quality_path)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:2000]
            self.store.update_status(job_id, status="failed", phase="failed", error_internal=message)

    def _successful(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        if job["status"] != "succeeded":
            raise JobNotReadyError(job["status"])
        return job

    def _required(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job


class JobNotFoundError(KeyError):
    pass


class JobNotReadyError(RuntimeError):
    pass


class JobNotRetryableError(RuntimeError):
    pass


def validate_options(options: dict[str, str]) -> dict[str, str]:
    values = {
        "prompt_profile": options.get("prompt_profile", "auto"),
        "structure_mode": options.get("structure_mode", "auto"),
        "audit_mode": options.get("audit_mode", "auto"),
    }
    if values["prompt_profile"] not in PROMPT_PROFILES:
        raise ValueError("Unsupported prompt_profile.")
    if values["structure_mode"] not in STRUCTURE_MODES:
        raise ValueError("Unsupported structure_mode.")
    if values["audit_mode"] not in AUDIT_MODES:
        raise ValueError("Unsupported audit_mode.")
    return values


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["options"] = json.loads(payload.pop("options_json"))
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
