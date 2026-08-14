from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.document import (
    ChunkRead,
    DocumentRead,
    DocumentUploadResponse,
    DocumentVersionRead,
    SectionRead,
    VersionStatusResponse,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentService,
    DocumentValidationError,
    VersionNotFoundError,
    get_document_service,
    status_progress,
)
from app.services.parsing_service import run_parse_task
from app.services.project_service import ProjectNotFoundError

router = APIRouter(tags=["documents"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.get("/projects/{project_id}/documents", response_model=list[DocumentRead])
def list_documents(
    project_id: int,
    folder_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    type: str | None = Query(default=None),
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[DocumentRead]:
    try:
        return service.list_document_reads(
            project_id, folder_id=folder_id, status=status_filter, document_type=type
        )
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    project_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder_id: int | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    service: DocumentService = Depends(get_document_service),
    current_user: User = Depends(get_current_user),
) -> DocumentUploadResponse:
    data = await file.read()
    try:
        document, version = service.upload_document(
            project_id,
            folder_id=folder_id,
            name=name,
            description=description,
            filename=file.filename or "unnamed",
            content_type=file.content_type,
            data=data,
            created_by=str(current_user.id),
        )
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None
    except DocumentValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    # 업로드 응답(202) 후 백그라운드로 파싱: UPLOADED→PARSING→PARSED
    background_tasks.add_task(run_parse_task, version.id)
    return DocumentUploadResponse(document=document, version=version)


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> DocumentRead:
    try:
        return service.get_document_read(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersionRead])
def list_versions(
    document_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[DocumentVersionRead]:
    try:
        return service.list_versions(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> FileResponse:
    try:
        version, path = service.resolve_download(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None
    except VersionNotFoundError:
        raise _not_found("No stored file for this document") from None
    return FileResponse(
        path,
        filename=version.original_file_name,
        media_type=version.mime_type or "application/octet-stream",
    )


@router.get("/versions/{version_id}", response_model=DocumentVersionRead)
def get_version(
    version_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> DocumentVersionRead:
    try:
        return service.get_version(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.get("/versions/{version_id}/status", response_model=VersionStatusResponse)
def get_version_status(
    version_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> VersionStatusResponse:
    try:
        version = service.get_version(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None
    return VersionStatusResponse(
        version_id=version.id,
        status=version.processing_status,
        progress=status_progress(version.processing_status),
    )


@router.get("/versions/{version_id}/sections", response_model=list[SectionRead])
def list_sections(
    version_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[SectionRead]:
    try:
        return service.list_sections(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.get("/versions/{version_id}/chunks", response_model=list[ChunkRead])
def list_chunks(
    version_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> list[ChunkRead]:
    try:
        return service.list_chunks(version_id)
    except VersionNotFoundError:
        raise _not_found(f"Version {version_id} not found") from None


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    service: DocumentService = Depends(get_document_service),
    _: User = Depends(get_current_user),
) -> None:
    try:
        service.delete_document(document_id)
    except DocumentNotFoundError:
        raise _not_found(f"Document {document_id} not found") from None
