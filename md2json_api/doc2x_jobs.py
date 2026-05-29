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
from typing import Any, Protocol

from .doc2x_client import Doc2XClient, Doc2XResult
from .runtime import atomic_write_bytes


DOC2X_STATUSES = {"queued", "running", "succeeded", "failed"}
DOC2X_MODELS = {"v2", "v3-2026"}
DOC2X_FORMULA_MODES = {"normal", "dollar"}
DOC2X_FORMULA_LEVELS = {"0", "1", "2"}


class Doc2XConverter(Protocol):
    def convert_file(
        self,
        *,
        source_file: Path,
        output_dir: Path,
        options: dict[str, Any],
        progress_callback: Any = None,
    ) -> Doc2XResult:
        ...


@dataclass(frozen=True)
class Doc2XWorkerSettings:
    jobs_root: Path
    max_workers: int = 1

    @classmethod
    def from_environment(cls) -> "Doc2XWorkerSettings":
        jobs_root = Path(os.environ.get("MD2JSON_JOBS_ROOT", "var/jobs")).expanduser().resolve()
        return cls(
            jobs_root=jobs_root,
            max_workers=max(1, int(os.environ.get("DOC2X_WORKERS", "1"))),
        )


class Doc2XJobStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS doc2x_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    progress INTEGER,
                    phase TEXT NOT NULL,
                    markdown_path TEXT,
                    json_path TEXT,
                    manifest_path TEXT,
                    error_internal TEXT
                )
                """
            )

    def create(self, *, job_id: str, input_name: str, input_path: Path, output_dir: Path, options: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO doc2x_jobs (
                    id, status, created_at, updated_at, input_name, input_path, output_dir,
                    options_json, phase
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, 'queued')
                """,
                (job_id, now, now, input_name, str(input_path), str(output_dir), json.dumps(options)),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM doc2x_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_payload(row) if row is not None else None

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM doc2x_jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def update_status(self, job_id: str, *, status: str, phase: str, error_internal: str | None = None) -> None:
        if status not in DOC2X_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE doc2x_jobs SET status = ?, phase = ?, updated_at = ?, error_internal = ? WHERE id = ?",
                (status, phase, _now(), error_internal, job_id),
            )

    def update_progress(self, job_id: str, *, phase: str, progress: int | None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE doc2x_jobs SET phase = ?, progress = ?, updated_at = ? WHERE id = ?",
                (phase, progress, _now(), job_id),
            )

    def complete(self, job_id: str, *, markdown_path: Path, json_path: Path, manifest_path: Path) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE doc2x_jobs
                SET status = 'succeeded', phase = 'completed', updated_at = ?,
                    markdown_path = ?, json_path = ?, manifest_path = ?, error_internal = NULL
                WHERE id = ?
                """,
                (_now(), str(markdown_path), str(json_path), str(manifest_path), job_id),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class Doc2XJobService:
    def __init__(self, settings: Doc2XWorkerSettings, client: Doc2XConverter | None = None) -> None:
        self.settings = settings
        self.root = self.settings.jobs_root / "doc2x"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = Doc2XJobStore(self.settings.jobs_root / "doc2x_jobs.sqlite3")
        self.client = client or Doc2XClient()
        self._executor = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="doc2x-worker")

    def create_job(self, *, filename: str, content: bytes, options: dict[str, Any]) -> dict[str, Any]:
        validated = validate_doc2x_options(options)
        job_id = uuid.uuid4().hex
        job_dir = self.root / job_id
        input_path = job_dir / "input" / Path(filename or "input.pdf").name
        output_dir = job_dir / "doc2x"
        atomic_write_bytes(input_path, content)
        self.store.create(
            job_id=job_id,
            input_name=input_path.name,
            input_path=input_path,
            output_dir=output_dir,
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
            "progress": job["progress"],
        }
        if job["status"] == "failed":
            payload["error"] = "Doc2X conversion failed. Contact the service operator with the job_id."
        return payload

    def markdown_text(self, job_id: str) -> str:
        job = self._successful(job_id)
        return Path(job["markdown_path"]).read_text(encoding="utf-8")

    def json_payload(self, job_id: str) -> Any:
        job = self._successful(job_id)
        return json.loads(Path(job["json_path"]).read_text(encoding="utf-8"))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
        self.store.close()

    def _run_job(self, job_id: str) -> None:
        job = self._required(job_id)
        self.store.update_status(job_id, status="running", phase="saving_input")
        try:
            result = self.client.convert_file(
                source_file=Path(job["input_path"]),
                output_dir=Path(job["output_dir"]),
                options=job["options"],
                progress_callback=lambda phase, progress: self.store.update_progress(
                    job_id, phase=phase, progress=progress
                ),
            )
            self.store.complete(
                job_id,
                markdown_path=result.markdown_path,
                json_path=result.json_path,
                manifest_path=result.manifest_path,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:2000]
            self.store.update_status(job_id, status="failed", phase="failed", error_internal=message)

    def _successful(self, job_id: str) -> dict[str, Any]:
        job = self._required(job_id)
        if job["status"] != "succeeded":
            raise Doc2XJobNotReadyError(job["status"])
        return job

    def _required(self, job_id: str) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise Doc2XJobNotFoundError(job_id)
        return job


class Doc2XJobNotFoundError(KeyError):
    pass


class Doc2XJobNotReadyError(RuntimeError):
    pass


def validate_doc2x_options(options: dict[str, Any]) -> dict[str, Any]:
    model = str(options.get("doc2x_model", os.environ.get("DOC2X_MODEL", "v3-2026")))
    formula_mode = str(options.get("formula_mode", "normal"))
    formula_level = str(options.get("formula_level", "0"))
    if model not in DOC2X_MODELS:
        raise ValueError("Unsupported doc2x_model.")
    if formula_mode not in DOC2X_FORMULA_MODES:
        raise ValueError("Unsupported formula_mode.")
    if formula_level not in DOC2X_FORMULA_LEVELS:
        raise ValueError("Unsupported formula_level.")
    return {
        "doc2x_model": model,
        "formula_mode": formula_mode,
        "formula_level": int(formula_level),
        "merge_cross_page_forms": _as_bool(options.get("merge_cross_page_forms", False)),
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["options"] = json.loads(payload.pop("options_json"))
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
