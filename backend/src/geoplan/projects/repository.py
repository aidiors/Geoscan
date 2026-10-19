from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError
from geoplan.projects.codec import document_digest
from geoplan.projects.models import (
    ProjectDocument,
    ProjectRevision,
    ProjectSaveRequest,
    ProjectSummary,
)

MAXIMUM_DOCUMENT_BYTES = 32 * 1024 * 1024


def encode_document(document: ProjectDocument) -> tuple[str, str]:
    archived = document.archive()
    digest = document_digest(archived.model_dump(mode="json"))
    payload = archived.model_dump_json()
    if len(payload.encode()) > MAXIMUM_DOCUMENT_BYTES:
        raise PlanningError(ErrorCode.PROJECT_INVALID, "Проект превышает допустимый размер 32 МиБ.")
    return payload, digest


class ProjectRepository(Protocol):
    def save(
        self, query: ProjectSaveRequest, project_id: UUID | None = None
    ) -> ProjectRevision: ...
    def list_projects(self) -> list[ProjectSummary]: ...
    def revisions(self, project_id: UUID) -> list[ProjectSummary]: ...
    def get(self, project_id: UUID, revision: int | None = None) -> ProjectRevision: ...


@dataclass(frozen=True)
class SqliteProjectRepository:
    path: Path

    @classmethod
    def from_environment(cls) -> SqliteProjectRepository:
        default = Path(__file__).resolve().parents[4] / "work" / "server-data"
        return cls(Path(os.environ.get("GEOPLAN_DATA_DIR", str(default))) / "projects.sqlite3")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise PlanningError(
                    ErrorCode.PROJECT_INVALID, "Версия хранилища не поддерживается."
                )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, head INTEGER NOT NULL, "
                "updated TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS revisions ("
                "project_id TEXT NOT NULL REFERENCES projects(id), number INTEGER NOT NULL,"
                "name TEXT NOT NULL, created TEXT NOT NULL, digest TEXT NOT NULL, "
                "payload TEXT NOT NULL,"
                "PRIMARY KEY(project_id, number));"
                "PRAGMA user_version = 1;"
            )
            yield connection
        except (sqlite3.Error, OSError) as error:
            raise PlanningError(
                ErrorCode.PROJECT_STORAGE_UNAVAILABLE,
                "Хранилище проектов недоступно. Скачайте файл проекта для резервного сохранения.",
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def save(self, query: ProjectSaveRequest, project_id: UUID | None = None) -> ProjectRevision:
        payload, digest = encode_document(query.document)
        identifier = str(project_id or uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT head FROM projects WHERE id = ?", (identifier,)
            ).fetchone()
            if project_id is not None and previous is None:
                self._not_found()
            head = previous["head"] if previous else None
            if query.parent_revision != head:
                raise PlanningError(
                    ErrorCode.PROJECT_CONFLICT,
                    "Проект уже изменён в другом окне. Откройте актуальную ревизию "
                    "или сохраните текущую работу отдельным проектом.",
                )
            number = (head or 0) + 1
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, head=excluded.head, updated=excluded.updated",
                (identifier, query.name, number, now),
            )
            connection.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                (identifier, number, query.name, now, digest, payload),
            )
        return ProjectRevision(
            project_id=UUID(identifier),
            name=query.name,
            revision=number,
            created_at=now,
            document_sha256=digest,
            document=ProjectDocument.model_validate_json(payload),
        )

    def list_projects(self) -> list[ProjectSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, head, updated FROM projects ORDER BY updated DESC"
            ).fetchall()
        return [
            ProjectSummary(
                project_id=r["id"],
                name=r["name"],
                latest_revision=r["head"],
                updated_at=r["updated"],
            )
            for r in rows
        ]

    def revisions(self, project_id: UUID) -> list[ProjectSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, number, created FROM revisions "
                "WHERE project_id=? ORDER BY number DESC",
                (str(project_id),),
            ).fetchall()
        if not rows:
            self._not_found()
        return [
            ProjectSummary(
                project_id=project_id,
                name=r["name"],
                latest_revision=r["number"],
                updated_at=r["created"],
            )
            for r in rows
        ]

    def get(self, project_id: UUID, revision: int | None = None) -> ProjectRevision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT name, number, created, digest, payload FROM revisions "
                "WHERE project_id=? AND (? IS NULL OR number=?) ORDER BY number DESC LIMIT 1",
                (str(project_id), revision, revision),
            ).fetchone()
        if row is None:
            self._not_found()
        if document_digest(json.loads(row["payload"])) != row["digest"]:
            raise PlanningError(
                ErrorCode.PROJECT_INVALID, "Нарушена целостность сохранённой ревизии."
            )
        return ProjectRevision(
            project_id=project_id,
            name=row["name"],
            revision=row["number"],
            created_at=row["created"],
            document_sha256=row["digest"],
            document=ProjectDocument.model_validate_json(row["payload"]).archive(),
        )

    @staticmethod
    def _not_found() -> None:
        raise PlanningError(ErrorCode.PROJECT_NOT_FOUND, "Проект или ревизия не найдены.")
