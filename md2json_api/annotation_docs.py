from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import atomic_write_json

ANNOTATION_STATUSES = {"available"}


class AnnotationDocumentStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotation_documents (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    input_name TEXT NOT NULL,
                    annotation_path TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._ensure_column("annotation_documents", "last_accessed_at", "TEXT")

    def create(self, *, doc_id: str, input_name: str, annotation_path: Path, item_count: int) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO annotation_documents (
                    id, status, created_at, updated_at, input_name, annotation_path, item_count
                ) VALUES (?, 'available', ?, ?, ?, ?, ?)
                """,
                (doc_id, now, now, input_name, str(annotation_path), item_count),
            )

    def get(self, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM annotation_documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return _row_payload(row) if row is not None else None

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM annotation_documents ORDER BY updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [_row_payload(row) for row in rows]

    def update(self, *, doc_id: str, item_count: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE annotation_documents SET updated_at = ?, item_count = ? WHERE id = ?",
                (_now(), item_count, doc_id),
            )

    def touch_access(self, doc_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE annotation_documents SET last_accessed_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), doc_id),
            )

    def delete(self, doc_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM annotation_documents WHERE id = ?", (doc_id,))

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        existing = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


class AnnotationDocumentService:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = AnnotationDocumentStore(self.root / "annotation_documents.sqlite3")

    def create(self, *, filename: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_payload(payload)
        doc_id = uuid.uuid4().hex
        doc_dir = self.root / doc_id
        annotation_path = doc_dir / "annotation.json"
        atomic_write_json(annotation_path, payload)
        self.store.create(
            doc_id=doc_id,
            input_name=Path(filename or "annotation.json").name,
            annotation_path=annotation_path,
            item_count=len(payload.get("items", [])),
        )
        return self.public_status(doc_id)

    def public_status(self, doc_id: str) -> dict[str, Any]:
        doc = self._required(doc_id)
        return {
            "annotation_id": doc["id"],
            "status": doc["status"],
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
            "input_name": doc["input_name"],
            "item_count": doc["item_count"],
        }

    def list_documents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self.public_status(doc["id"]) for doc in self.store.list(limit=limit)]

    def get_payload(self, doc_id: str) -> dict[str, Any]:
        doc = self._required(doc_id)
        self.store.touch_access(doc_id)
        return json.loads(Path(doc["annotation_path"]).read_text(encoding="utf-8"))

    def update_payload(self, doc_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_payload(payload)
        doc = self._required(doc_id)
        atomic_write_json(Path(doc["annotation_path"]), payload)
        self.store.update(doc_id=doc_id, item_count=len(payload.get("items", [])))
        return {
            "annotation_id": doc_id,
            "schema_version": payload["schema_version"],
            "item_count": len(payload.get("items", [])),
            "saved": True,
        }

    def shutdown(self) -> None:
        self.store.close()

    def _required(self, doc_id: str) -> dict[str, Any]:
        doc = self.store.get(doc_id)
        if doc is None:
            raise AnnotationDocumentNotFoundError(doc_id)
        return doc

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Annotation payload must be a JSON object.")
        if payload.get("schema_version") != "md2json.annotation.v1":
            raise ValueError("Only schema_version=md2json.annotation.v1 is accepted.")
        source = payload.get("source")
        if not isinstance(source, dict):
            raise ValueError("Annotation payload must include source object.")
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


class AnnotationDocumentNotFoundError(KeyError):
    pass


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    if payload.get("status") not in ANNOTATION_STATUSES:
        raise RuntimeError(f"Unknown annotation document status in store: {payload.get('status')}")
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
