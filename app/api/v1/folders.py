from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.folder import FolderCreate, FolderRead, FolderUpdate
from app.services.folder_service import (
    FolderNotFoundError,
    FolderService,
    FolderValidationError,
    get_folder_service,
)
from app.services.project_service import ProjectNotFoundError

router = APIRouter(tags=["folders"])


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.get("/projects/{project_id}/folders", response_model=list[FolderRead])
def list_folders(
    project_id: int,
    service: FolderService = Depends(get_folder_service),
    _: User = Depends(get_current_user),
) -> list[FolderRead]:
    try:
        return service.list_folders(project_id)
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None


@router.post(
    "/projects/{project_id}/folders",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_folder(
    project_id: int,
    payload: FolderCreate,
    service: FolderService = Depends(get_folder_service),
    _: User = Depends(get_current_user),
) -> FolderRead:
    try:
        return service.create_folder(project_id, payload)
    except ProjectNotFoundError:
        raise _not_found(f"Project {project_id} not found") from None
    except FolderValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.patch("/folders/{folder_id}", response_model=FolderRead)
def update_folder(
    folder_id: int,
    payload: FolderUpdate,
    service: FolderService = Depends(get_folder_service),
    _: User = Depends(get_current_user),
) -> FolderRead:
    try:
        return service.update_folder(folder_id, payload)
    except FolderNotFoundError:
        raise _not_found(f"Folder {folder_id} not found") from None
    except FolderValidationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(
    folder_id: int,
    service: FolderService = Depends(get_folder_service),
    _: User = Depends(get_current_user),
) -> None:
    try:
        service.delete_folder(folder_id)
    except FolderNotFoundError:
        raise _not_found(f"Folder {folder_id} not found") from None
