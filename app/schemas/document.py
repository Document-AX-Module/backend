from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    version_no: int
    original_file_name: str
    mime_type: str | None
    file_size: int | None
    checksum: str | None
    processing_status: str
    parser_version: str | None
    created_at: datetime


class AuthorInfo(BaseModel):
    id: int | None
    name: str | None
    email: str | None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    folder_id: int | None
    name: str
    description: str | None
    document_type: str | None
    status: str
    current_version_id: int | None
    created_by: str | None
    # 작성자 이름/이메일(created_by 사용자 id 를 해석) + 현재 버전 정보
    author: AuthorInfo | None = None
    current_version: DocumentVersionRead | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document: DocumentRead
    version: DocumentVersionRead


class VersionStatusResponse(BaseModel):
    version_id: int
    status: str
    progress: int  # 0~100


class SectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_version_id: int
    parent_section_id: int | None
    title: str | None
    section_type: str | None
    level: int | None
    page_start: int | None
    page_end: int | None
    order_no: int
    content: str | None
    meta: dict | None


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    document_id: int
    document_version_id: int
    section_id: int | None
    chunk_index: int
    content: str
    page_start: int | None
    page_end: int | None
    # ORM 속성명은 chunk_metadata(컬럼 metadata) → 응답 키는 metadata
    metadata: dict | None = Field(default=None, validation_alias="chunk_metadata")
