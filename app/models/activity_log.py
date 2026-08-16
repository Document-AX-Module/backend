from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import CreatedAtMixin


class ActivityLog(CreatedAtMixin, Base):
    """누가·언제·무엇을 했는지 남기는 감사 로그.

    **의도적으로 FK 를 하나도 걸지 않는다.** 대상(문서·프로젝트·폴더)이나 사용자가
    삭제되면 CASCADE 로 로그까지 함께 사라져 버려, 정작 가장 알고 싶은
    "누가 그걸 지웠는지"를 잃게 되기 때문이다. 대신 삭제된 뒤에도 읽을 수 있도록
    그 시점의 이름을 `actor_label` / `target_label` 에 복사해 둔다.

    소급 기록이 불가능하므로 행은 절대 수정하지 않고 append 만 한다.
    """

    __tablename__ = "activity_logs"
    __table_args__ = (
        # 피드는 항상 최신순 — 전체 조회용
        Index("ix_activity_logs_created_at", "created_at"),
        # 프로젝트 스코프 조회용
        Index("ix_activity_logs_project_created_at", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # 누가 — users.id 값이지만 FK 는 걸지 않는다(위 주석 참고).
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    # 기록 시점의 사용자 이름(없으면 이메일). 개명·탈퇴 후에도 피드가 읽히게 한다.
    actor_label: Mapped[str | None] = mapped_column(String(255))

    # 무엇을 — "document.upload", "project.delete" 같은 점 구분 액션 코드.
    # 표시 문구가 아니라 코드를 저장한다(문구가 바뀌어도 과거 로그가 안 깨지게).
    action: Mapped[str] = mapped_column(String(50), nullable=False)

    # 어디서 — 프로젝트 스코프 필터용. 프로젝트 밖의 활동(로그인)은 NULL.
    project_id: Mapped[int | None] = mapped_column(BigInteger)

    # 대상 — "document" | "project" | "folder" | "analysis"
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    # 기록 시점의 대상 이름(파일명·문서명·폴더명). 삭제 로그에서 특히 중요하다.
    target_label: Mapped[str | None] = mapped_column(String(500))

    # 액션별 부가 정보. 예: {"analysis_type": "proofread", "findings": 3}
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
