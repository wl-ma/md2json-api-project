from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response

from .annotation_docs import AnnotationDocumentNotFoundError, AnnotationDocumentService
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
    annotation_docs = AnnotationDocumentService(jobs.settings.jobs_root / "annotation_documents")
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
        annotation_docs.shutdown()
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
    app.state.annotation_docs = annotation_docs

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
                options={},
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/source-conversions", dependencies=[protected])
    def list_source_conversions(
        source_type: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        if source_type is not None and source_type not in {"markdown", "pdf", "image"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported source_type.")
        if status_filter is not None and status_filter not in {"queued", "running", "succeeded", "failed"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported status.")
        return source_jobs.list_jobs(source_type=source_type, status=status_filter, limit=limit)

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

    @app.get("/v1/source-conversions/{job_id}/markdown", dependencies=[protected])
    def source_conversion_markdown(job_id: str) -> Response:
        try:
            markdown = source_jobs.markdown_payload(job_id)
            return Response(content=markdown, media_type="text/markdown; charset=utf-8")
        except SourceJobNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except SourceJobNotReadyError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Job is not complete: {exc}.") from exc

    @app.post("/v1/annotation-documents", dependencies=[protected])
    async def create_annotation_document(
        file: Annotated[UploadFile, File(description="md2json.annotation.v1 JSON file.")],
    ) -> dict:
        filename = file.filename or "annotation.json"
        if not filename.lower().endswith(".json"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .json files are accepted.")
        content = await file.read(max_upload_bytes + 1)
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
        if len(content) > max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large.")
        try:
            payload = __import__("json").loads(content.decode("utf-8"))
            return annotation_docs.create(filename=filename, payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON document.") from exc

    @app.get("/v1/annotation-documents", dependencies=[protected])
    def list_annotation_documents(limit: int = 100) -> list[dict]:
        return annotation_docs.list_documents(limit=limit)

    @app.get("/v1/annotation-documents/{annotation_id}", dependencies=[protected])
    def get_annotation_document(annotation_id: str) -> JSONResponse:
        try:
            return JSONResponse(annotation_docs.get_payload(annotation_id))
        except AnnotationDocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Annotation document not found.") from exc

    @app.put("/v1/annotation-documents/{annotation_id}", dependencies=[protected])
    def update_annotation_document(
        annotation_id: str,
        payload: Annotated[dict, Body(description="Complete md2json.annotation.v1 document.")],
    ) -> JSONResponse:
        try:
            return JSONResponse(annotation_docs.update_payload(annotation_id, payload))
        except AnnotationDocumentNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Annotation document not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return app
