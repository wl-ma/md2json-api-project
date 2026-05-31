from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse

from .doc2x_jobs import (
    Doc2XJobNotFoundError,
    Doc2XJobNotReadyError,
    Doc2XJobService,
    Doc2XWorkerSettings,
)
from .full_jobs import (
    FullConversionService,
    FullJobNotFoundError,
    FullJobNotReadyError,
    FullWorkerSettings,
)
from .jobs import JobNotFoundError, JobNotReadyError, JobNotRetryableError, JobService, WorkerSettings


def create_app(
    service: JobService | None = None,
    doc2x_service: Doc2XJobService | None = None,
    full_service: FullConversionService | None = None,
    *,
    api_token: str | None = None,
    allow_unauthenticated: bool | None = None,
) -> FastAPI:
    owned_service = service is None
    owned_doc2x_service = doc2x_service is None
    owned_full_service = full_service is None
    jobs = service or JobService(WorkerSettings.from_environment())
    doc2x_jobs = doc2x_service or Doc2XJobService(
        Doc2XWorkerSettings(
            jobs_root=jobs.settings.jobs_root,
            max_workers=max(1, int(os.environ.get("DOC2X_WORKERS", "1"))),
        )
    )
    full_jobs = full_service or FullConversionService(
        FullWorkerSettings(
            jobs_root=jobs.settings.jobs_root,
            md2json_settings=jobs.settings,
            max_workers=max(1, int(os.environ.get("FULL_CONVERSION_WORKERS", "1"))),
        )
    )
    token = api_token if api_token is not None else os.environ.get("MD2JSON_API_TOKEN")
    allow_public = (
        allow_unauthenticated
        if allow_unauthenticated is not None
        else os.environ.get("MD2JSON_ALLOW_UNAUTHENTICATED", "").lower() == "true"
    )
    if not token and not allow_public:
        if owned_service:
            jobs.shutdown()
        if owned_doc2x_service:
            doc2x_jobs.shutdown()
        if owned_full_service:
            full_jobs.shutdown()
        raise RuntimeError(
            "MD2JSON_API_TOKEN is required for the API service. "
            "Set MD2JSON_ALLOW_UNAUTHENTICATED=true only for isolated local testing."
        )
    max_upload_bytes = int(os.environ.get("MD2JSON_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    doc2x_max_upload_bytes = int(os.environ.get("DOC2X_MAX_UPLOAD_BYTES", str(300 * 1024 * 1024)))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.recover_unfinished_jobs()
        doc2x_jobs.recover_unfinished_jobs()
        full_jobs.recover_unfinished_jobs()
        yield
        if owned_service:
            jobs.shutdown()
        if owned_doc2x_service:
            doc2x_jobs.shutdown()
        if owned_full_service:
            full_jobs.shutdown()

    app = FastAPI(title="md2json-api", version="1.0.0", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.doc2x_jobs = doc2x_jobs
    app.state.full_jobs = full_jobs

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        if allow_public:
            return
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
        supplied = authorization[len(prefix) :]
        if token is None or not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token.")

    protected = Depends(authorize)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/conversions", status_code=status.HTTP_202_ACCEPTED, dependencies=[protected])
    async def create_conversion(
        file: Annotated[UploadFile, File(description="Markdown source file.")],
        prompt_profile: Annotated[str, Form()] = "auto",
        structure_mode: Annotated[str, Form()] = "auto",
        audit_mode: Annotated[str, Form()] = "auto",
    ) -> dict:
        if not (file.filename or "").lower().endswith(".md"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .md files are accepted.")
        content = await file.read(max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded Markdown is empty.")
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large.")
        try:
            return jobs.create_job(
                filename=file.filename or "input.md",
                markdown=content,
                options={
                    "prompt_profile": prompt_profile,
                    "structure_mode": structure_mode,
                    "audit_mode": audit_mode,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/doc2x-conversions", status_code=status.HTTP_202_ACCEPTED, dependencies=[protected])
    async def create_doc2x_conversion(
        file: Annotated[UploadFile, File(description="Original PDF source file.")],
        doc2x_model: Annotated[str, Form()] = "v3-2026",
        formula_mode: Annotated[str, Form()] = "normal",
        formula_level: Annotated[str, Form()] = "0",
        merge_cross_page_forms: Annotated[bool, Form()] = False,
    ) -> dict:
        filename = file.filename or "input.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .pdf files are accepted.")
        content = await file.read(doc2x_max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
        if len(content) > doc2x_max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large.")
        try:
            return doc2x_jobs.create_job(
                filename=filename,
                content=content,
                options={
                    "doc2x_model": doc2x_model,
                    "formula_mode": formula_mode,
                    "formula_level": formula_level,
                    "merge_cross_page_forms": merge_cross_page_forms,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/doc2x-conversions/{job_id}", dependencies=[protected])
    def doc2x_conversion_status(job_id: str) -> dict:
        try:
            return doc2x_jobs.public_status(job_id)
        except Doc2XJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.get("/v1/doc2x-conversions/{job_id}/markdown", dependencies=[protected])
    def doc2x_conversion_markdown(job_id: str) -> PlainTextResponse:
        try:
            return PlainTextResponse(doc2x_jobs.markdown_text(job_id), media_type="text/markdown")
        except Doc2XJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except Doc2XJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.get("/v1/doc2x-conversions/{job_id}/json", dependencies=[protected])
    def doc2x_conversion_json(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(doc2x_jobs.json_payload(job_id))
        except Doc2XJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except Doc2XJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.post("/v1/full-conversions", status_code=status.HTTP_202_ACCEPTED, dependencies=[protected])
    async def create_full_conversion(
        file: Annotated[UploadFile, File(description="Original PDF source file.")],
        doc2x_model: Annotated[str, Form()] = "v3-2026",
        formula_mode: Annotated[str, Form()] = "normal",
        formula_level: Annotated[str, Form()] = "0",
        merge_cross_page_forms: Annotated[bool, Form()] = False,
        prompt_profile: Annotated[str, Form()] = "auto",
        structure_mode: Annotated[str, Form()] = "auto",
        audit_mode: Annotated[str, Form()] = "auto",
    ) -> dict:
        filename = file.filename or "input.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .pdf files are accepted.")
        content = await file.read(doc2x_max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
        if len(content) > doc2x_max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large.")
        try:
            return full_jobs.create_job(
                filename=filename,
                content=content,
                options={
                    "doc2x_model": doc2x_model,
                    "formula_mode": formula_mode,
                    "formula_level": formula_level,
                    "merge_cross_page_forms": merge_cross_page_forms,
                    "prompt_profile": prompt_profile,
                    "structure_mode": structure_mode,
                    "audit_mode": audit_mode,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/full-conversions/{job_id}", dependencies=[protected])
    def full_conversion_status(job_id: str) -> dict:
        try:
            return full_jobs.public_status(job_id)
        except FullJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.get("/v1/full-conversions/{job_id}/result", dependencies=[protected])
    def full_conversion_result(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(full_jobs.result_payload(job_id))
        except FullJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except FullJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.get("/v1/full-conversions/{job_id}/quality", dependencies=[protected])
    def full_conversion_quality(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(full_jobs.quality_payload(job_id))
        except FullJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except FullJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.get("/v1/conversions/{job_id}", dependencies=[protected])
    def conversion_status(job_id: str) -> dict:
        try:
            return jobs.public_status(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.post("/v1/conversions/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED, dependencies=[protected])
    def resume_conversion(job_id: str) -> dict:
        try:
            return jobs.resume_failed_job(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except JobNotRetryableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Only failed jobs can be resumed; current status is {exc}.",
            ) from exc

    @app.get("/v1/conversions/{job_id}/result", dependencies=[protected])
    def conversion_result(job_id: str) -> JSONResponse:
        return JSONResponse(_load_public_result(jobs, job_id, quality=False))

    @app.get("/v1/conversions/{job_id}/quality", dependencies=[protected])
    def conversion_quality(job_id: str) -> JSONResponse:
        return JSONResponse(_load_public_result(jobs, job_id, quality=True))

    @app.get("/v1/conversions/{job_id}/usage", dependencies=[protected])
    def conversion_usage(job_id: str) -> JSONResponse:
        return JSONResponse(_load_public_usage(jobs.usage_payload, job_id))

    @app.get("/v1/doc2x-conversions/{job_id}/usage", dependencies=[protected])
    def doc2x_usage(job_id: str) -> JSONResponse:
        return JSONResponse(_load_public_usage(doc2x_jobs.usage_payload, job_id))

    @app.get("/v1/full-conversions/{job_id}/usage", dependencies=[protected])
    def full_conversion_usage(job_id: str) -> JSONResponse:
        return JSONResponse(_load_public_usage(full_jobs.usage_payload, job_id))

    return app


def _load_public_result(jobs: JobService, job_id: str, *, quality: bool):
    try:
        return jobs.quality_payload(job_id) if quality else jobs.result_payload(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except JobNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc


def _load_public_usage(loader, job_id: str):
    try:
        return loader(job_id)
    except (JobNotFoundError, Doc2XJobNotFoundError, FullJobNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except (JobNotReadyError, Doc2XJobNotReadyError, FullJobNotReadyError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc
