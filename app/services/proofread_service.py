from __future__ import annotations

import logging
from typing import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.models.ai_analysis_result import AiAnalysisResult
from app.repositories.analysis_repository import AiAnalysisResultRepository
from app.repositories.document_repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from app.schemas.proofread import ProofreadFindingRead, ProofreadResult
from app.services.document_service import DocumentNotFoundError, VersionNotFoundError
from app.services.llm import LLMError, LLMProvider, get_llm_provider

logger = logging.getLogger(__name__)

ANALYSIS_TYPE = "proofread"
# 파싱이 끝나 섹션이 존재하는 상태들(이 중 하나면 proofread 가능)
_READY_STATES = {"PARSED", "CHUNKING", "CHUNKED", "EMBEDDING", "READY"}


class DocumentNotReadyError(Exception):
    """아직 파싱되지 않아 검토할 섹션이 없는 문서."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"문서가 아직 파싱 전입니다 (현재 상태: {status})")


class AnalysisNotFoundError(Exception):
    def __init__(self, analysis_id: int) -> None:
        self.analysis_id = analysis_id
        super().__init__(f"Analysis {analysis_id} not found")


def build_result(
    record: AiAnalysisResult,
    findings: list[ProofreadFindingRead],
    scanned: int,
) -> ProofreadResult:
    return ProofreadResult(
        id=record.id,
        document_id=record.document_id,
        document_version_id=record.document_version_id,
        analysis_type=record.analysis_type,
        status=record.status,
        provider=record.provider,
        findings=findings,
        sections_scanned=scanned,
        error=record.error,
        created_at=record.created_at,
    )


class ProofreadService:
    def __init__(
        self,
        documents: DocumentRepository,
        versions: DocumentVersionRepository,
        analyses: AiAnalysisResultRepository,
        provider: LLMProvider,
        settings: Settings,
    ) -> None:
        self.documents = documents
        self.versions = versions
        self.analyses = analyses
        self.provider = provider
        self.settings = settings

    def create_pending(
        self, document_id: int, *, created_by: str | None
    ) -> AiAnalysisResult:
        """검증 후 RUNNING 레코드를 만들어 반환한다(실제 교정은 백그라운드에서)."""
        doc = self.documents.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)
        if doc.current_version_id is None:
            raise VersionNotFoundError(-1)
        version = self.versions.get(doc.current_version_id)
        if version is None:
            raise VersionNotFoundError(doc.current_version_id)
        if version.processing_status not in _READY_STATES:
            raise DocumentNotReadyError(version.processing_status)

        return self.analyses.add(
            AiAnalysisResult(
                document_id=doc.id,
                document_version_id=version.id,
                analysis_type=ANALYSIS_TYPE,
                status="RUNNING",
                provider=self.provider.name,
                result=None,
                error=None,
                created_by=created_by,
            )
        )

    def list_analyses(
        self, document_id: int, *, analysis_type: str | None = None
    ) -> list[AiAnalysisResult]:
        doc = self.documents.get(document_id)
        if doc is None:
            raise DocumentNotFoundError(document_id)
        return self.analyses.list_by_document(document_id, analysis_type=analysis_type)

    def get_analysis(self, document_id: int, analysis_id: int) -> ProofreadResult:
        record = self.analyses.get(analysis_id)
        if record is None or record.document_id != document_id:
            raise AnalysisNotFoundError(analysis_id)
        result = record.result or {}
        findings = [ProofreadFindingRead(**f) for f in result.get("findings", [])]
        scanned = (result.get("summary") or {}).get("sections_scanned", 0)
        return build_result(record, findings, scanned)


def _iter_batches(
    items: list[tuple], max_chars: int, max_sections: int
) -> Iterator[list[tuple]]:
    """(section, text) 목록을 글자수/개수 상한으로 배치 그룹핑."""
    batch: list[tuple] = []
    total = 0
    for section, text in items:
        if batch and (len(batch) >= max_sections or total + len(text) > max_chars):
            yield batch
            batch, total = [], 0
        batch.append((section, text))
        total += len(text)
    if batch:
        yield batch


def run_proofread_task(analysis_id: int) -> None:
    """BackgroundTasks 진입점 — 자체 세션으로 배치 교정을 수행하고 레코드를 갱신한다."""
    db = SessionLocal()
    try:
        settings = get_settings()
        analyses = AiAnalysisResultRepository(db)
        versions = DocumentVersionRepository(db)
        provider = get_llm_provider(settings)

        record = analyses.get(analysis_id)
        if record is None:
            logger.warning("run_proofread_task: analysis %s not found", analysis_id)
            return

        try:
            sections = versions.list_sections(record.document_version_id)
            items: list[tuple] = []
            for s in sections:
                text = (s.content or s.title or "").strip()
                if text:
                    items.append((s, text[: settings.proofread_max_chars_per_section]))

            findings: list[ProofreadFindingRead] = []
            for batch in _iter_batches(
                items,
                settings.proofread_batch_max_chars,
                settings.proofread_batch_max_sections,
            ):
                batch_out = provider.proofread_batch([t for _, t in batch])
                for (section, _), fs in zip(batch, batch_out):
                    for f in fs:
                        findings.append(
                            ProofreadFindingRead(
                                original=f.original,
                                suggestion=f.suggestion,
                                reason=f.reason,
                                category=f.category,
                                section_id=section.id,
                                page_start=section.page_start,
                                page_end=section.page_end,
                            )
                        )

            record.result = {
                "findings": [f.model_dump() for f in findings],
                "summary": {"total": len(findings), "sections_scanned": len(items)},
            }
            record.status = "COMPLETED"
            record.provider = provider.name
            db.commit()
            logger.info(
                "proofread %s done: %s findings over %s sections",
                analysis_id, len(findings), len(items),
            )
        except LLMError as exc:
            db.rollback()
            record = analyses.get(analysis_id)
            if record is not None:
                record.status = "FAILED"
                record.error = str(exc)
                db.commit()
            logger.warning("proofread %s failed: %s", analysis_id, exc)
    finally:
        db.close()


def get_proofread_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProofreadService:
    return ProofreadService(
        DocumentRepository(db),
        DocumentVersionRepository(db),
        AiAnalysisResultRepository(db),
        get_llm_provider(settings),
        settings,
    )
