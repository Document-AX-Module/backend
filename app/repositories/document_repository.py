from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_section import DocumentSection
from app.models.document_version import DocumentVersion


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_project(
        self,
        project_id: int,
        *,
        folder_id: int | None = None,
        status: str | None = None,
        document_type: str | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(Document.project_id == project_id)
        if folder_id is not None:
            stmt = stmt.where(Document.folder_id == folder_id)
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if document_type is not None:
            stmt = stmt.where(Document.document_type == document_type)
        return list(self.db.scalars(stmt.order_by(Document.id.desc())))

    def get(self, document_id: int) -> Document | None:
        return self.db.get(Document, document_id)

    def add(self, document: Document) -> Document:
        self.db.add(document)
        self.db.flush()  # id 확보 (commit 은 서비스가 한 번에)
        return document

    def commit_refresh(self, *objs) -> None:
        self.db.commit()
        for obj in objs:
            self.db.refresh(obj)

    def delete(self, document: Document) -> None:
        self.db.delete(document)
        self.db.commit()


class DocumentVersionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, version_id: int) -> DocumentVersion | None:
        return self.db.get(DocumentVersion, version_id)

    def get_many(self, version_ids: set[int]) -> list[DocumentVersion]:
        if not version_ids:
            return []
        return list(
            self.db.scalars(
                select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
            )
        )

    def list_by_document(self, document_id: int) -> list[DocumentVersion]:
        return list(
            self.db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no.desc())
            )
        )

    def add(self, version: DocumentVersion) -> DocumentVersion:
        self.db.add(version)
        self.db.flush()
        return version

    def list_sections(self, version_id: int) -> list[DocumentSection]:
        return list(
            self.db.scalars(
                select(DocumentSection)
                .where(DocumentSection.document_version_id == version_id)
                .order_by(DocumentSection.order_no)
            )
        )

    def list_chunks(self, version_id: int) -> list[DocumentChunk]:
        return list(
            self.db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_version_id == version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
