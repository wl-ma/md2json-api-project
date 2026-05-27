from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterator

MANIFEST_VERSION = 1


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def output_directory_lock(out_dir: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("Output-directory locking requires a POSIX deployment host.") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir / ".conversion.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another conversion is already writing output directory: {out_dir}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prepare_conversion_manifest(*, input_md: Path, out_dir: Path, config: Any) -> None:
    manifest_path = out_dir / ".conversion_manifest.json"
    expected = _manifest_payload(input_md, config)
    existing = _read_json(manifest_path)
    if not config.resume:
        if manifest_path.exists():
            manifest_path.unlink()
        _clear_resume_artifacts(out_dir)
    else:
        if existing is None and _has_resume_artifacts(out_dir):
            raise RuntimeError(
                "Cannot safely resume legacy output without .conversion_manifest.json; "
                "use a new output directory or run without --resume."
            )
        if existing is not None and existing != expected:
            raise RuntimeError(
                "Cannot resume because the input document or conversion settings differ from the cached run."
            )
    atomic_write_json(manifest_path, expected)


def _manifest_payload(input_md: Path, config: Any) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "input_sha256": hashlib.sha256(input_md.read_bytes()).hexdigest(),
        "settings": {
            "backend": config.backend,
            "model": config.model,
            "base_url": config.base_url,
            "azure_endpoint": config.azure_endpoint,
            "azure_api_version": config.azure_api_version,
            "max_output_tokens": config.max_output_tokens,
            "llm_timeout": config.llm_timeout,
            "prompt_profile": config.prompt_profile,
            "audit_mode": config.audit_mode,
            "structure_mode": config.structure_mode,
        },
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid conversion manifest: {path}") from exc
    return payload if isinstance(payload, dict) else None


def _has_resume_artifacts(out_dir: Path) -> bool:
    return any(
        path.exists()
        for path in [
            out_dir / "structure_api_call" / "response.json",
            out_dir / "api_calls",
            out_dir / "audit_api_calls",
            out_dir / "mock_structure_api_call" / "response.json",
            out_dir / "mock_api_calls",
            out_dir / "mock_audit_api_calls",
        ]
    )


def _clear_resume_artifacts(out_dir: Path) -> None:
    for directory_name in ["api_calls", "audit_api_calls", "mock_api_calls", "mock_audit_api_calls"]:
        directory = out_dir / directory_name
        if directory.exists():
            for path in directory.glob("section*.json"):
                path.unlink()
    for directory_name in ["structure_api_call", "mock_structure_api_call"]:
        directory = out_dir / directory_name
        if directory.exists():
            for filename in ["call.json", "request.json", "response.json"]:
                path = directory / filename
                if path.exists():
                    path.unlink()
