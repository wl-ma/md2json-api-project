from __future__ import annotations

from pathlib import Path
from typing import Any

from .annotation_schema import to_annotation_document
from .full_jobs import FullConversionService, FullJobNotFoundError, FullJobNotReadyError
from .jobs import JobNotFoundError, JobNotReadyError, JobService


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
        raise ValueError("Only .md and .pdf files are accepted by /v1/source-conversions in this service build.")

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

    def _decode_id(self, source_job_id: str) -> tuple[str, str]:
        if source_job_id.startswith(MARKDOWN_PREFIX):
            return "markdown", source_job_id[len(MARKDOWN_PREFIX) :]
        if source_job_id.startswith(PDF_PREFIX):
            return "pdf", source_job_id[len(PDF_PREFIX) :]
        if source_job_id.startswith(IMAGE_PREFIX):
            return "image", source_job_id[len(IMAGE_PREFIX) :]
        raise SourceJobNotFoundError(source_job_id)

    def _source_status(self, status: dict[str, Any], *, source_type: str, prefix: str) -> dict[str, Any]:
        payload = dict(status)
        payload["job_id"] = prefix + str(status["job_id"])
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
