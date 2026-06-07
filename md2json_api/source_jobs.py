from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .annotation_schema import to_annotation_document
from .full_jobs import FullConversionService, FullJobNotFoundError, FullJobNotReadyError
from .jobs import JobNotFoundError, JobNotReadyError, JobService
from .runtime import atomic_write_json


MARKDOWN_PREFIX = "markdown_"
PDF_PREFIX = "pdf_"
IMAGE_PREFIX = "image_"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class SourceConversionService:
    def __init__(self, *, markdown_jobs: JobService, full_jobs: FullConversionService) -> None:
        self.markdown_jobs = markdown_jobs
        self.full_jobs = full_jobs

    def create_job(self, *, filename: str, content: bytes, options: dict[str, Any]) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        if suffix == ".md":
            status = self.markdown_jobs.create_job(
                filename=filename,
                markdown=content,
                options={
                    "prompt_profile": str(options.get("prompt_profile", "auto")),
                    "structure_mode": str(options.get("structure_mode", "auto")),
                    "audit_mode": str(options.get("audit_mode", "auto")),
                },
            )
            return self._source_status(status, source_type="markdown", prefix=MARKDOWN_PREFIX)
        if suffix == ".pdf":
            status = self.full_jobs.create_job(filename=filename, content=content, options=options)
            return self._source_status(status, source_type="pdf", prefix=PDF_PREFIX)
        if suffix in IMAGE_EXTENSIONS:
            status = self.full_jobs.create_job(filename=filename, content=content, options=options)
            return self._source_status(status, source_type="image", prefix=IMAGE_PREFIX)
        raise ValueError(
            "Only .md, .pdf, .jpg, .jpeg, and .png files are accepted by /v1/source-conversions."
        )

    def public_status(self, source_job_id: str) -> dict[str, Any]:
        source_type, backend_id = self._decode_id(source_job_id)
        try:
            if source_type == "markdown":
                return self._source_status(
                    self.markdown_jobs.public_status(backend_id),
                    source_type=source_type,
                    prefix=MARKDOWN_PREFIX,
                )
            return self._source_status(
                self.full_jobs.public_status(backend_id),
                source_type=source_type,
                prefix=IMAGE_PREFIX if source_type == "image" else PDF_PREFIX,
            )
        except JobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except FullJobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc

    def result_payload(self, source_job_id: str) -> dict[str, Any]:
        saved = self.saved_annotation_payload(source_job_id)
        if saved is not None:
            return saved
        return self.generated_result_payload(source_job_id)

    def generated_result_payload(self, source_job_id: str) -> dict[str, Any]:
        source_type, backend_id = self._decode_id(source_job_id)
        try:
            if source_type == "markdown":
                result = self.markdown_jobs.result_payload(backend_id)
                quality = self.markdown_jobs.quality_payload(backend_id)
                status = self.markdown_jobs.public_status(backend_id)
            else:
                result = self.full_jobs.result_payload(backend_id)
                quality = self.full_jobs.quality_payload(backend_id)
                status = self.full_jobs.public_status(backend_id)
        except JobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except FullJobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except JobNotReadyError as exc:
            raise SourceJobNotReadyError(str(exc)) from exc
        except FullJobNotReadyError as exc:
            raise SourceJobNotReadyError(str(exc)) from exc
        return to_annotation_document(
            result_payload=result,
            quality_payload=quality,
            filename=str(status.get("input_name") or "input"),
            source_type=source_type,
        )

    def save_annotation_payload(self, source_job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source_type, backend_id = self._decode_id(source_job_id)
        self._validate_annotation_payload(payload, source_type=source_type)
        path = self._annotation_path(source_type, backend_id, require_success=True)
        atomic_write_json(path, payload)
        return {
            "job_id": source_job_id,
            "schema_version": payload["schema_version"],
            "item_count": len(payload.get("items", [])),
            "saved": True,
        }

    def saved_annotation_payload(self, source_job_id: str) -> dict[str, Any] | None:
        source_type, backend_id = self._decode_id(source_job_id)
        path = self._annotation_path(source_type, backend_id, require_success=True)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def quality_payload(self, source_job_id: str) -> dict[str, Any]:
        return self.result_payload(source_job_id)["quality"]

    def usage_payload(self, source_job_id: str) -> Any:
        source_type, backend_id = self._decode_id(source_job_id)
        try:
            if source_type == "markdown":
                return self.markdown_jobs.usage_payload(backend_id)
            return self.full_jobs.usage_payload(backend_id)
        except JobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except FullJobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except JobNotReadyError as exc:
            raise SourceJobNotReadyError(str(exc)) from exc
        except FullJobNotReadyError as exc:
            raise SourceJobNotReadyError(str(exc)) from exc

    def markdown_payload(self, source_job_id: str) -> str:
        source_type, backend_id = self._decode_id(source_job_id)
        try:
            if source_type == "markdown":
                job = self.markdown_jobs.store.get(backend_id)
                if job is None:
                    raise SourceJobNotFoundError(source_job_id)
                if job["status"] != "succeeded":
                    raise SourceJobNotReadyError(str(job["status"]))
                self.markdown_jobs.store.touch_access(backend_id)
                return Path(job["input_path"]).read_text(encoding="utf-8")
            job = self.full_jobs.store.get(backend_id)
            if job is None:
                raise SourceJobNotFoundError(source_job_id)
            if job["status"] != "succeeded":
                raise SourceJobNotReadyError(str(job["status"]))
            self.full_jobs.store.touch_access(backend_id)
            markdown_path = job.get("markdown_path")
            if not markdown_path:
                raise SourceJobNotReadyError("markdown_unavailable")
            return Path(markdown_path).read_text(encoding="utf-8")
        except JobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc
        except FullJobNotFoundError as exc:
            raise SourceJobNotFoundError(source_job_id) from exc

    def list_jobs(
        self,
        *,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        results: list[dict[str, Any]] = []

        if source_type in {None, "markdown"}:
            for job in self.markdown_jobs.store.list(limit=safe_limit):
                payload = self._source_status(job, source_type="markdown", prefix=MARKDOWN_PREFIX)
                if status is None or payload["status"] == status:
                    results.append(payload)

        if source_type in {None, "pdf", "image"}:
            for job in self.full_jobs.store.list(limit=safe_limit):
                inferred_source_type = _infer_full_source_type(job)
                if source_type is not None and inferred_source_type != source_type:
                    continue
                payload = self._source_status(
                    job,
                    source_type=inferred_source_type,
                    prefix=IMAGE_PREFIX if inferred_source_type == "image" else PDF_PREFIX,
                )
                if status is None or payload["status"] == status:
                    results.append(payload)

        results.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return results[:safe_limit]

    def _decode_id(self, source_job_id: str) -> tuple[str, str]:
        if source_job_id.startswith(MARKDOWN_PREFIX):
            return "markdown", source_job_id[len(MARKDOWN_PREFIX) :]
        if source_job_id.startswith(PDF_PREFIX):
            return "pdf", source_job_id[len(PDF_PREFIX) :]
        if source_job_id.startswith(IMAGE_PREFIX):
            return "image", source_job_id[len(IMAGE_PREFIX) :]
        raise SourceJobNotFoundError(source_job_id)

    def _annotation_path(self, source_type: str, backend_id: str, *, require_success: bool) -> Path:
        try:
            if source_type == "markdown":
                job = self.markdown_jobs.store.get(backend_id)
                if job is None:
                    raise SourceJobNotFoundError(backend_id)
                if require_success and job["status"] != "succeeded":
                    raise SourceJobNotReadyError(str(job["status"]))
                return Path(job["output_dir"]).parent / "annotation.json"
            job = self.full_jobs.store.get(backend_id)
            if job is None:
                raise SourceJobNotFoundError(backend_id)
            if require_success and job["status"] != "succeeded":
                raise SourceJobNotReadyError(str(job["status"]))
            return Path(job["md2json_output_dir"]).parent / "annotation.json"
        except JobNotFoundError as exc:
            raise SourceJobNotFoundError(backend_id) from exc
        except FullJobNotFoundError as exc:
            raise SourceJobNotFoundError(backend_id) from exc

    def _validate_annotation_payload(self, payload: dict[str, Any], *, source_type: str) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Annotation payload must be a JSON object.")
        if payload.get("schema_version") != "md2json.annotation.v1":
            raise ValueError("Only schema_version=md2json.annotation.v1 is accepted.")
        source = payload.get("source")
        if not isinstance(source, dict):
            raise ValueError("Annotation payload must include source object.")
        submitted_source_type = source.get("source_type")
        if submitted_source_type is not None and submitted_source_type != source_type:
            raise ValueError("Annotation source.source_type does not match the conversion job.")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("Annotation payload must include items array.")
        seen_ids: set[str] = set()
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Annotation item at index {index} must be an object.")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise ValueError(f"Annotation item at index {index} must include a non-empty id.")
            if item_id in seen_ids:
                raise ValueError(f"Duplicate annotation item id: {item_id}")
            seen_ids.add(item_id)
        quality = payload.get("quality")
        if quality is not None and not isinstance(quality, dict):
            raise ValueError("Annotation quality must be an object when provided.")

    def _source_status(self, status: dict[str, Any], *, source_type: str, prefix: str) -> dict[str, Any]:
        payload = dict(status)
        job_id = payload.get("job_id") or payload.get("id")
        if job_id is None:
            raise KeyError("job_id")
        payload["job_id"] = prefix + str(job_id)
        payload["source_type"] = source_type
        if "doc2x_progress" in payload:
            payload["preprocess_progress"] = payload.pop("doc2x_progress")
        else:
            payload["preprocess_progress"] = None
        return payload


class SourceJobNotFoundError(KeyError):
    pass


class SourceJobNotReadyError(RuntimeError):
    pass


def _infer_full_source_type(job: dict[str, Any]) -> str:
    suffix = Path(str(job.get("input_name") or "")).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return "pdf"
