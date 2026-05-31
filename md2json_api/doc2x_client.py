from __future__ import annotations

import json
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

from .runtime import atomic_write_bytes, atomic_write_json, atomic_write_text


@dataclass(frozen=True)
class Doc2XSettings:
    api_key: str | None
    base_url: str = "https://v2.doc2x.noedgeai.com"
    timeout: float = 600
    poll_interval: float = 2

    @classmethod
    def from_environment(cls) -> "Doc2XSettings":
        return cls(
            api_key=os.environ.get("DOC2X_API_KEY"),
            base_url=os.environ.get("DOC2X_BASE_URL", "https://v2.doc2x.noedgeai.com"),
            timeout=float(os.environ.get("DOC2X_TIMEOUT", "600")),
            poll_interval=float(os.environ.get("DOC2X_POLL_INTERVAL", "2")),
        )


@dataclass(frozen=True)
class Doc2XResult:
    markdown_path: Path
    json_path: Path
    manifest_path: Path


class Doc2XClient:
    def __init__(self, settings: Doc2XSettings | None = None) -> None:
        self.settings = settings or Doc2XSettings.from_environment()

    def convert_file(
        self,
        *,
        source_file: Path,
        output_dir: Path,
        options: dict[str, Any],
        progress_callback: Callable[[str, int | None], None] | None = None,
    ) -> Doc2XResult:
        if not self.settings.api_key:
            raise RuntimeError("Doc2X backend is not configured.")

        output_dir.mkdir(parents=True, exist_ok=True)
        progress_callback = progress_callback or (lambda _phase, _progress: None)

        progress_callback("requesting_preupload", None)
        preupload = self._post_json("/api/v2/parse/preupload", {"model": options["doc2x_model"]})
        uid = _require_nested(preupload, "data", "uid")
        upload_url = _require_nested(preupload, "data", "url")

        progress_callback("uploading_to_doc2x", None)
        self._put_file(str(upload_url), source_file)

        progress_callback("waiting_parse", None)
        parse_status = self._poll_parse_status(str(uid), progress_callback)
        result_payload = parse_status.get("data", {}).get("result", {})
        pages_path = output_dir / "pages.json"
        atomic_write_json(pages_path, result_payload)

        progress_callback("exporting_markdown", None)
        export_request = {
            "uid": str(uid),
            "to": "md",
            "formula_mode": options["formula_mode"],
            "filename": source_file.stem,
            "merge_cross_page_forms": options["merge_cross_page_forms"],
            "formula_level": options["formula_level"],
        }
        self._post_json("/api/v2/convert/parse", export_request)
        export_status = self._poll_export_status(str(uid), progress_callback)
        download_url = _require_nested(export_status, "data", "url")

        progress_callback("downloading_export", None)
        archive_path = output_dir / "export.zip"
        archive_bytes = self._download(str(download_url).replace("\\u0026", "&"))
        atomic_write_bytes(archive_path, archive_bytes)

        markdown_path = output_dir / "output.md"
        markdown_text = _markdown_from_zip(archive_path)
        if markdown_text is None:
            markdown_text = _markdown_from_pages(result_payload)
        if not markdown_text.strip():
            raise RuntimeError("Doc2X conversion did not produce Markdown.")
        atomic_write_text(markdown_path, markdown_text)

        manifest_path = output_dir / "export_manifest.json"
        atomic_write_json(
            manifest_path,
            {
                "uid": str(uid),
                "source_file": source_file.name,
                "markdown_path": markdown_path.name,
                "json_path": pages_path.name,
                "archive_path": archive_path.name,
                "markdown_source": "export_zip" if _zip_has_markdown(archive_path) else "pages_md_fallback",
            },
        )
        progress_callback("finalizing", 100)
        return Doc2XResult(markdown_path=markdown_path, json_path=pages_path, manifest_path=manifest_path)

    def _poll_parse_status(
        self,
        uid: str,
        progress_callback: Callable[[str, int | None], None],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.timeout
        last_payload: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            payload = self._get_json(f"/api/v2/parse/status?uid={parse.quote(uid)}")
            last_payload = payload
            if payload.get("code") != "success":
                raise RuntimeError(f"Doc2X parse failed: {payload.get('code', 'unknown_error')}")
            data = payload.get("data", {})
            progress = data.get("progress")
            progress_callback("waiting_parse", int(progress) if isinstance(progress, int) else None)
            status = data.get("status")
            if status == "success":
                return payload
            if status in {"failed", "fail", "error"}:
                raise RuntimeError("Doc2X parse failed.")
            time.sleep(self.settings.poll_interval)
        raise TimeoutError(f"Doc2X parse timed out: {last_payload.get('code') if last_payload else 'no_status'}")

    def _poll_export_status(
        self,
        uid: str,
        progress_callback: Callable[[str, int | None], None],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.timeout
        while time.monotonic() < deadline:
            payload = self._get_json(f"/api/v2/convert/parse/result?uid={parse.quote(uid)}")
            if payload.get("code") != "success":
                raise RuntimeError(f"Doc2X export failed: {payload.get('code', 'unknown_error')}")
            data = payload.get("data", {})
            status = data.get("status")
            if status == "success" and data.get("url"):
                return payload
            if status in {"failed", "fail", "error"}:
                raise RuntimeError("Doc2X export failed.")
            progress_callback("exporting_markdown", None)
            time.sleep(self.settings.poll_interval)
        raise TimeoutError("Doc2X export timed out.")

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = self._headers({"Content-Type": "application/json"})
        return self._json_request(request.Request(self._url(path), data=body, headers=headers, method="POST"))

    def _get_json(self, path: str) -> dict[str, Any]:
        return self._json_request(request.Request(self._url(path), headers=self._headers(), method="GET"))

    def _put_file(self, url: str, source_file: Path) -> None:
        req = request.Request(url, data=source_file.read_bytes(), method="PUT")
        try:
            with request.urlopen(req, timeout=self.settings.timeout) as response:
                if response.status >= 400:
                    raise RuntimeError("Doc2X preupload PUT failed.")
        except error.URLError as exc:
            raise RuntimeError("Doc2X preupload PUT failed.") from exc

    def _download(self, url: str) -> bytes:
        try:
            with request.urlopen(url, timeout=self.settings.timeout) as response:
                if response.status >= 400:
                    raise RuntimeError("Doc2X export download failed.")
                return response.read()
        except error.URLError as exc:
            raise RuntimeError("Doc2X export download failed.") from exc

    def _json_request(self, req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=self.settings.timeout) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                body = ""
            if exc.code == 429:
                raise RuntimeError(f"Doc2X rate limit exceeded: {body}") from exc
            raise RuntimeError(f"Doc2X request failed with HTTP {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError("Doc2X request failed.") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Doc2X returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Doc2X returned unexpected JSON.")
        return payload

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        return self.settings.base_url.rstrip("/") + path


def _require_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise RuntimeError("Doc2X response is missing required fields.")
        current = current[key]
    if current in (None, ""):
        raise RuntimeError("Doc2X response contains an empty required field.")
    return current


def _markdown_from_zip(archive_path: Path) -> str | None:
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".md") and not name.endswith("/")]
        if not names:
            return None
        names.sort(key=lambda name: (len(Path(name).parts), name))
        with archive.open(names[0]) as handle:
            return handle.read().decode("utf-8")


def _zip_has_markdown(archive_path: Path) -> bool:
    with zipfile.ZipFile(archive_path) as archive:
        return any(name.lower().endswith(".md") and not name.endswith("/") for name in archive.namelist())


def _markdown_from_pages(result_payload: Any) -> str:
    if not isinstance(result_payload, dict):
        return ""
    pages = result_payload.get("pages")
    if not isinstance(pages, list):
        return ""
    chunks: list[str] = []
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("md"), str):
            chunks.append(page["md"].rstrip())
    return "\n\n".join(chunk for chunk in chunks if chunk)
