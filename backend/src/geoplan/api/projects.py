import hmac
from uuid import UUID

from fastapi import APIRouter, Query

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError
from geoplan.projects.codec import document_digest
from geoplan.projects.models import (
    PortableProject,
    ProjectRevision,
    ProjectSaveRequest,
    ProjectSummary,
)
from geoplan.projects.repository import ProjectRepository, encode_document


def create_project_router(repository: ProjectRepository) -> APIRouter:
    router = APIRouter(prefix="/v1/projects", tags=["projects"])

    @router.get("")
    def list_projects() -> list[ProjectSummary]:
        return repository.list_projects()

    @router.post("")
    def create_project(query: ProjectSaveRequest) -> ProjectRevision:
        return repository.save(query)

    @router.post("/portable")
    def portable(query: ProjectSaveRequest) -> PortableProject:
        _, digest = encode_document(query.document)
        return PortableProject(
            name=query.name, document_sha256=digest, document=query.document.archive()
        )

    @router.post("/import")
    def import_project(query: PortableProject) -> ProjectRevision:
        # Verify before stripping any authorization from an untrusted imported archive.
        digest = document_digest(query.document.model_dump(mode="json"))
        if not hmac.compare_digest(query.document_sha256, digest):
            raise PlanningError(
                ErrorCode.PROJECT_INVALID, "Контрольная сумма проекта не совпадает."
            )
        return repository.save(
            ProjectSaveRequest(name=query.name, document=query.document.archive())
        )

    @router.get("/{project_id}")
    def get_project(
        project_id: UUID, revision: int | None = Query(default=None, ge=1)
    ) -> ProjectRevision:
        return repository.get(project_id, revision)

    @router.post("/{project_id}/revisions")
    def save_revision(project_id: UUID, query: ProjectSaveRequest) -> ProjectRevision:
        return repository.save(query, project_id)

    @router.get("/{project_id}/revisions")
    def list_revisions(project_id: UUID) -> list[ProjectSummary]:
        return repository.revisions(project_id)

    return router
