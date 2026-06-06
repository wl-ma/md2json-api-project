from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .annotation_docs import AnnotationDocumentService
from .full_jobs import FullJobStore
from .jobs import JobStore


@dataclass(slots=True)
class CacheGcSettings:
    enabled: bool = True
    delete_failed_after_days: int = 7
    delete_succeeded_after_days: int = 14
    delete_annotated_after_days: int = 180
    delete_annotation_documents_after_days: int = 180
    keep_annotation_documents: bool = True
    delete_debug_after_days: int = 1
    batch_limit: int = 500
    dry_run: bool = False

    @classmethod
    def from_environment(cls) -> "CacheGcSettings":
        return cls(
            enabled=_env_bool("MD2JSON_CACHE_GC_ENABLED", True),
            delete_failed_after_days=_env_int("MD2JSON_CACHE_DELETE_FAILED_AFTER_DAYS", 7),
            delete_succeeded_after_days=_env_int("MD2JSON_CACHE_DELETE_SUCCEEDED_AFTER_DAYS", 14),
            delete_annotated_after_days=_env_int("MD2JSON_CACHE_DELETE_ANNOTATED_AFTER_DAYS", 180),
            delete_annotation_documents_after_days=_env_int("MD2JSON_CACHE_DELETE_ANNOTATION_DOCUMENTS_AFTER_DAYS", 180),
            keep_annotation_documents=_env_bool("MD2JSON_CACHE_KEEP_ANNOTATION_DOCUMENTS", True),
            delete_debug_after_days=_env_int("MD2JSON_CACHE_DELETE_DEBUG_AFTER_DAYS", 1),
            batch_limit=_env_int("MD2JSON_CACHE_GC_BATCH_LIMIT", 500),
            dry_run=_env_bool("MD2JSON_CACHE_GC_DRY_RUN", False),
        )


@dataclass(slots=True)
class CacheGcReport:
    markdown_jobs_deleted: int = 0
    full_jobs_deleted: int = 0
    annotation_documents_deleted: int = 0
    debug_directories_deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "markdown_jobs_deleted": self.markdown_jobs_deleted,
            "full_jobs_deleted": self.full_jobs_deleted,
            "annotation_documents_deleted": self.annotation_documents_deleted,
            "debug_directories_deleted": self.debug_directories_deleted,
        }


class CacheGcService:
    def __init__(self, *, jobs_root: Path, settings: CacheGcSettings | None = None) -> None:
        self.jobs_root = jobs_root.expanduser().resolve()
        self.settings = settings or CacheGcSettings.from_environment()

    def run(self) -> CacheGcReport:
        report = CacheGcReport()
        if not self.settings.enabled:
            return report

        now = datetime.now(timezone.utc)
        report.markdown_jobs_deleted = self._cleanup_markdown_jobs(now)
        report.full_jobs_deleted = self._cleanup_full_jobs(now)
        report.annotation_documents_deleted = self._cleanup_annotation_documents(now)
        report.debug_directories_deleted = self._cleanup_debug_artifacts(now)
        return report

    def _cleanup_markdown_jobs(self, now: datetime) -> int:
        store = JobStore(self.jobs_root / "jobs.sqlite3")
        deleted = 0
        try:
            for job in store.list(limit=self.settings.batch_limit):
                if job["status"] in {"queued", "running"}:
                    continue
                if not _is_expired(job, now, self.settings):
                    continue
                if _has_saved_annotation(Path(job["output_dir"])) and not _annotated_expired(job, now, self.settings):
                    continue
                if self._delete_path(Path(job["output_dir"]).parent):
                    store.delete(job["id"])
                    deleted += 1
        finally:
            store.close()
        return deleted

    def _cleanup_full_jobs(self, now: datetime) -> int:
        store = FullJobStore(self.jobs_root / "full_jobs.sqlite3")
        deleted = 0
        try:
            for job in store.list(limit=self.settings.batch_limit):
                if job["status"] in {"queued", "running"}:
                    continue
                if not _is_expired(job, now, self.settings):
                    continue
                output_parent = Path(job["md2json_output_dir"]).parent.parent
                if _has_saved_annotation(output_parent) and not _annotated_expired(job, now, self.settings):
                    continue
                if self._delete_path(output_parent):
                    store.delete(job["id"])
                    deleted += 1
        finally:
            store.close()
        return deleted

    def _cleanup_annotation_documents(self, now: datetime) -> int:
        if self.settings.keep_annotation_documents:
            return 0
        root = self.jobs_root / "annotation_documents"
        if not root.exists():
            return 0
        service = AnnotationDocumentService(root)
        deleted = 0
        try:
            for doc in service.store.list(limit=self.settings.batch_limit):
                updated_at = _parse_dt(str(doc["updated_at"]))
                if now - updated_at < timedelta(days=self.settings.delete_annotation_documents_after_days):
                    continue
                if self._delete_path(Path(doc["annotation_path"]).parent):
                    service.store.delete(doc["id"])
                    deleted += 1
        finally:
            service.shutdown()
        return deleted

    def _cleanup_debug_artifacts(self, now: datetime) -> int:
        deleted = 0
        threshold = timedelta(days=self.settings.delete_debug_after_days)
        for base in [self.jobs_root, self.jobs_root / "full"]:
            if not base.exists():
                continue
            for pattern in ("**/api_calls", "**/audit_api_calls", "**/mock_api_calls", "**/structure_api_call"):
                for path in base.glob(pattern):
                    if not path.is_dir():
                        continue
                    age = now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    if age < threshold:
                        continue
                    if self._delete_path(path):
                        deleted += 1
        return deleted

    def _delete_path(self, path: Path) -> bool:
        if not path.exists():
            return True
        if self.settings.dry_run:
            print(f"DRY-RUN delete {path}")
            return True
        shutil.rmtree(path)
        return True


def _is_expired(job: dict[str, Any], now: datetime, settings: CacheGcSettings) -> bool:
    updated_at = _parse_dt(str(job["updated_at"]))
    if job["status"] == "failed":
        return now - updated_at >= timedelta(days=settings.delete_failed_after_days)
    if _has_saved_annotation(Path(_job_root_path(job))):
        return now - updated_at >= timedelta(days=settings.delete_annotated_after_days)
    return now - updated_at >= timedelta(days=settings.delete_succeeded_after_days)


def _annotated_expired(job: dict[str, Any], now: datetime, settings: CacheGcSettings) -> bool:
    updated_at = _parse_dt(str(job["updated_at"]))
    return now - updated_at >= timedelta(days=settings.delete_annotated_after_days)


def _job_root_path(job: dict[str, Any]) -> str:
    if "output_dir" in job:
        return str(Path(job["output_dir"]).parent)
    return str(Path(job["md2json_output_dir"]).parent.parent)


def _has_saved_annotation(job_root: Path) -> bool:
    return (job_root / "annotation.json").exists() or (job_root / "saved_annotation.json").exists()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    return int(value)
