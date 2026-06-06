from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .converter import MarkdownJsonConverter
from .doc2x_client import Doc2XClient
from .doc2x_jobs import Doc2XConverter, validate_doc2x_options
from .jobs import WorkerSettings, validate_options
from .runtime import atomic_write_bytes, atomic_write_json


FULL_STATUSES = {"queued", "running", "succeeded", "failed"}


@dataclass(frozen=True)
class FullWorkerSettings:
    jobs_root: Path
    md2json_settings: WorkerSettings
    max_workers: int = 1

    @classmethod
    def from_environment(cls) -> "FullWorkerSettings":
        jobs_root = Path(os.environ.get("MD2JSON_JOBS_ROOT", "var/jobs")).expanduser().resolve()
        return cls(
            jobs_root=jobs_root,
            md2json_settings=WorkerSettings.from_environment(),
            max_workers=max(1, int(os.environ.get("FULL_CONVERSION_WORKERS", "1"))),
        )


class FullJobStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS full_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    input_name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    doc2x_dir TEXT NOT NULL,
                    md2json_output_dir TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    doc2x_progress INTEGER,
                    sections_total INTEGER,
                    sections_completed INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL,
                    markdown_path TEXT,
                    doc2x_json_path TEXT,
                    result_path TEXT,
                    quality_path TEXT,
                    usage_path TEXT,
                    error_internal TEXT
                )
                """
            )
            self._ensure_column("full_jobs", "last_accessed_at", "TEXT")

    def create(
        self,
        *,
        job_id: str,
        input_name: str,
        input_path: Path,
        doc2x_dir: Path,
        md2json_output_dir: Path,
        options: dict[str, Any],
    ) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO full_jobs (
                    id, status, created_at, updated_at, input_name, input_path,
                    doc2x_dir, md2json_output_dir, options_json, sections_completed, phase
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, 0, 'queued')
                """,
                (
                    job_id,
                    now,
                    now,
                    input_name,
                    str(input_path),
                    str(doc2x_dir),
                    str(md2json_output_dir),
                    json.dumps(options),
                ),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM full_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_payload(row) if row is not None else None

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM full_jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM full_jobs ORDER BY updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def update_status(self, job_id: str, *, status: str, phase: str, error_internal: str | None = None) -> None:
        if status not in FULL_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE full_jobs SET status = ?, phase = ?, updated_at = ?, error_internal = ? WHERE id = ?",
                (status, phase, _now(), error_internal, job_id),
            )

    def update_doc2x_progress(self, job_id: str, *, phase: str, progress: int | None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE full_jobs SET phase = ?, doc2x_progress = ?, updated_at = ? WHERE id = ?",
                (f"doc2x_{phase}", progress, _now(), job_id),
            )

    def update_markdown_paths(self, job_id: str, *, markdown_path: Path, doc2x_json_path: Path) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE full_jobs SET markdown_path = ?, doc2x_json_path = ?, updated_at = ? WHERE id = ?",
                (str(markdown_path), str(doc2x_json_path), _now(), job_id),
            )

    def update_md2json_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE full_jobs
                SET phase = ?, sections_total = ?, sections_completed = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    f"md2json_{progress['phase']}",
                    int(progress["sections_total"]),
                    int(progress["sections_completed"]),
                    _now(),
                    job_id,
                ),
            )

    def complete(self, job_id: str, *, result_path: Path, quality_path: Path, usage_path: Path) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE full_jobs
                SET status = 'succeeded', phase = 'completed', updated_at = ?,
                    result_path = ?, quality_path = ?, usage_path = ?, error_internal = NULL
                WHERE id = ?
                """,
                (_now(), str(result_path), str(quality_path), str(usage_path), job_id),
            )

    def touch_access(self, job_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE full_jobs SET last_accessed_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), job_id),
            )

    def delete(self, job_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM full_jobs WHERE id = ?", (job_id,))

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        existing = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


class FullConversionService:
    def __init__(self, settings: FullWorkerSettings, doc2x_client: Doc2XConverter | None = None) -> None:
        self.settings = settings
        self.root = self.settings.jobs_root / "full"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = FullJobStore(self.settings.jobs_root / "full_jobs.sqlite3")
        self.doc2x_client = doc2x_client or Doc2XClient()
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="full-worker")

    def create_job(self, *, filename: str, content: bytes, options: dict[str, Any]) -> dict[str, Any]:
        validated = validate_full_options(options)
        if (
            self.settings.md2json_settings.backend == "local"
            and validated["md2json"]["structure_mode"] == "llm"
        ):
            raise ValueError("structure_mode=llm is unavailable with the configured local backend.")
        job_id = uuid.uuid4().hex
        job_dir = self.root / job_id
        input_path = job_dir / "input" / Path(filename or "input.pdf").name
        doc2x_dir = job_dir / "doc2x"
        md2json_output_dir = job_dir / "md2json" / "output"
        atomic_write_bytes(input_path, content)
        self.store.create(
            job_id=job_id,
            input_name=input_path.name,
            input_path=input_path,
            doc2x_dir=doc2x_dir,
            md2json_output_dir=md2json_output_dir,
            options=validated,
        )
        self._executor.submit(self._run_job, job_id)
        return self.public_status(job_id)

    def recover_unfinished_jobs(self) -> None:
        for job in self.store.unfinished():
            self.store.update_status(job["id"], status="queued", phase="recovering")
            self._executor.submit(self._run_job, job["id"])

    def public_status(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        payload = {
            "job_id": job["id"],
            "status": job["status"],
            "phase": job["phase"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "input_name": job["input_name"],
            "doc2x_progress": job["doc2x_progress"],
            "sections_total": job["sections_total"],
            "sections_completed": job["sections_completed"],
        }
        if job["status"] == "failed":
            payload["error"] = "Full conversion failed. Contact the service operator with the job_id."
        return payload

    def result_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        self.store.touch_access(job_id)
        return json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))

    def quality_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        self.store.touch_access(job_id)
        payload = json.loads(Path(job["quality_path"]).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["source_file"] = job["input_name"]
        return payload

    def usage_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        self.store.touch_access(job_id)
        return json.loads(Path(job["usage_path"]).read_text(encoding="utf-8"))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
        self.store.close()

    def _run_job(self, job_id: str) -> None:
        job = self._required(job_id)
        started_at = time.monotonic()
        self.store.update_status(job_id, status="running", phase="doc2x_starting")
        try:
            doc2x_result = self.doc2x_client.convert_file(
                source_file=Path(job["input_path"]),
                output_dir=Path(job["doc2x_dir"]),
                options=job["options"]["doc2x"],
                progress_callback=lambda phase, progress: self.store.update_doc2x_progress(
                    job_id, phase=phase, progress=progress
                ),
            )
            self.store.update_markdown_paths(
                job_id,
                markdown_path=doc2x_result.markdown_path,
                doc2x_json_path=doc2x_result.json_path,
            )
            self.store.update_status(job_id, status="running", phase="md2json_starting")
            config = self.settings.md2json_settings.converter_config(job["options"]["md2json"], resume=False)
            result = MarkdownJsonConverter(config).convert(
                doc2x_result.markdown_path,
                Path(job["md2json_output_dir"]),
                progress_callback=lambda progress: self.store.update_md2json_progress(job_id, progress),
            )
            result_path = result.out_dir / f"{result.source_file.stem}.json"
            quality_path = result.out_dir / "quality_report.json"
            md2json_usage_path = result.out_dir / "usage_summary.json"
            md2json_usage = json.loads(md2json_usage_path.read_text(encoding="utf-8")) if md2json_usage_path.exists() else {}
            total_elapsed = round(max(0.0, time.monotonic() - started_at), 6)
            doc2x_elapsed = round(max(0.0, total_elapsed - float(md2json_usage.get("wall_clock_elapsed_seconds", 0.0))), 6)
            usage_path = Path(job["md2json_output_dir"]).parent / "usage_summary.json"
            atomic_write_json(
                usage_path,
                {
                    "requests": int(md2json_usage.get("requests", 0)) + 1,
                    "input_tokens": int(md2json_usage.get("input_tokens", 0)),
                    "output_tokens": int(md2json_usage.get("output_tokens", 0)),
                    "total_tokens": int(md2json_usage.get("total_tokens", 0)),
                    "llm_elapsed_seconds": round(float(md2json_usage.get("llm_elapsed_seconds", 0.0)), 6),
                    "wall_clock_elapsed_seconds": total_elapsed,
                    "phases": {
                        "doc2x": {
                            "requests": 1,
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                            "elapsed_seconds": doc2x_elapsed,
                        },
                        "md2json": md2json_usage,
                    },
                },
            )
            self.store.complete(job_id, result_path=result_path, quality_path=quality_path, usage_path=usage_path)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:2000]
            self.store.update_status(job_id, status="failed", phase="failed", error_internal=message)

    def _successful(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        if job["status"] != "succeeded":
            raise FullJobNotReadyError(job["status"])
        return job

    def _required(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise FullJobNotFoundError(job_id)
        return job


class FullJobNotFoundError(KeyError):
    pass


class FullJobNotReadyError(RuntimeError):
    pass


def validate_full_options(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc2x": validate_doc2x_options(options),
        "md2json": validate_options(
            {
                "prompt_profile": str(options.get("prompt_profile", "auto")),
                "structure_mode": str(options.get("structure_mode", "auto")),
                "audit_mode": str(options.get("audit_mode", "auto")),
            }
        ),
    }


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["options"] = json.loads(payload.pop("options_json"))
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
