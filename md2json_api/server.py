from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from .jobs import JobNotFoundError, JobNotReadyError, JobNotRetryableError, JobService, WorkerSettings


def create_app(
    service: JobService | None = None,
    *,
    api_token: str | None = None,
    allow_unauthenticated: bool | None = None,
) -> FastAPI:
    owned_service = service is None
    jobs = service or JobService(WorkerSettings.from_environment())
    token = api_token if api_token is not None else os.environ.get("MD2JSON_API_TOKEN")
    allow_public = (
        allow_unauthenticated
        if allow_unauthenticated is not None
        else os.environ.get("MD2JSON_ALLOW_UNAUTHENTICATED", "").lower() == "true"
    )
    if not token and not allow_public:
        if owned_service:
            jobs.shutdown()
        raise RuntimeError(
            "MD2JSON_API_TOKEN is required for the API service. "
            "Set MD2JSON_ALLOW_UNAUTHENTICATED=true only for isolated local testing."
        )
    max_upload_bytes = int(os.environ.get("MD2JSON_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.recover_unfinished_jobs()
        yield
        if owned_service:
            jobs.shutdown()

    app = FastAPI(title="md2json-api", version="1.0.0", lifespan=lifespan)
    app.state.jobs = jobs

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

    return app


def _load_public_result(jobs: JobService, job_id: str, *, quality: bool):
    try:
        return jobs.quality_payload(job_id) if quality else jobs.result_payload(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
    except JobNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc
