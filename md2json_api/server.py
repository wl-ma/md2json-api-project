from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from .doc2x_jobs import (
    Doc2XJobService,
    Doc2XWorkerSettings,
)
from .full_jobs import (
    FullConversionService,
    FullWorkerSettings,
)
from .jobs import JobService, WorkerSettings
from .source_jobs import (
    SourceConversionService,
    SourceJobNotFoundError,
    SourceJobNotReadyError,
)


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
    source_jobs = SourceConversionService(markdown_jobs=jobs, full_jobs=full_jobs)
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
    app.state.source_jobs = source_jobs

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

    @app.post("/v1/source-conversions", status_code=status.HTTP_202_ACCEPTED, dependencies=[protected])
    async def create_source_conversion(
        file: Annotated[UploadFile, File(description="Markdown, PDF, or image source file.")],
        prompt_profile: Annotated[str, Form()] = "auto",
        structure_mode: Annotated[str, Form()] = "auto",
        audit_mode: Annotated[str, Form()] = "auto",
        doc2x_model: Annotated[str, Form()] = "v3-2026",
        formula_mode: Annotated[str, Form()] = "normal",
        formula_level: Annotated[str, Form()] = "0",
        merge_cross_page_forms: Annotated[bool, Form()] = False,
    ) -> dict:
        filename = file.filename or "input"
        lower = filename.lower()
        if lower.endswith(".md"):
            max_bytes = max_upload_bytes
        elif lower.endswith(".pdf"):
            max_bytes = doc2x_max_upload_bytes
        elif lower.endswith((".jpg", ".jpeg", ".png")):
            max_bytes = int(os.environ.get("DOC2X_IMAGE_MAX_UPLOAD_BYTES", str(7 * 1024 * 1024)))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .md, .pdf, .jpg, .jpeg, and .png files are accepted.",
            )
        content = await file.read(max_bytes + 1)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
        if len(content) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large.")
        try:
            return source_jobs.create_job(
                filename=filename,
                content=content,
                options={
                    "prompt_profile": prompt_profile,
                    "structure_mode": structure_mode,
                    "audit_mode": audit_mode,
                    "doc2x_model": doc2x_model,
                    "formula_mode": formula_mode,
                    "formula_level": formula_level,
                    "merge_cross_page_forms": merge_cross_page_forms,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/source-conversions/{job_id}", dependencies=[protected])
    def source_conversion_status(job_id: str) -> dict:
        try:
            return source_jobs.public_status(job_id)
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.get("/v1/source-conversions/{job_id}/result", dependencies=[protected])
    def source_conversion_result(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(source_jobs.result_payload(job_id))
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.put("/v1/source-conversions/{job_id}/annotation", dependencies=[protected])
    def save_source_annotation(
        job_id: str,
        payload: Annotated[dict, Body(description="Complete md2json.annotation.v1 document.")],
    ) -> JSONResponse:
        try:
            return JSONResponse(source_jobs.save_annotation_payload(job_id, payload))
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/source-conversions/{job_id}/annotation", dependencies=[protected])
    def source_annotation(job_id: str) -> JSONResponse:
        try:
            saved = source_jobs.saved_annotation_payload(job_id)
            if saved is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved annotation not found.")
            return JSONResponse(saved)
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.get("/v1/source-conversions/{job_id}/quality", dependencies=[protected])
    def source_conversion_quality(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(source_jobs.quality_payload(job_id))
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.get("/v1/source-conversions/{job_id}/usage", dependencies=[protected])
    def source_conversion_usage(job_id: str) -> JSONResponse:
        try:
            return JSONResponse(source_jobs.usage_payload(job_id))
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    return app
