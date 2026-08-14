"""파서 공통 모델.

파일 포맷과 무관하게 파서는 UnifiedDocument(Section 목록)를 산출한다.
level 규약: heading = 1..9(작을수록 상위), 본문/표/기타 = 100(가장 가까운 heading 아래로 매달림).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

BODY_LEVEL = 100  # heading 이 아닌 콘텐츠


@dataclass
class UnifiedSection:
    content: str
    order: int
    title: str | None = None
    level: int = BODY_LEVEL
    section_type: str = "paragraph"  # heading | paragraph | table | list | image
    page_start: int | None = None
    page_end: int | None = None
    # bbox: [x0, y0, x1, y1] (페이지 좌표, PDF/OCR), ocr confidence 등
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnifiedDocument:
    sections: list[UnifiedSection]
    parser_name: str
    page_count: int | None = None
    ocr_used: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class ParseError(Exception):
    """파싱 실패."""


class DocumentParser(Protocol):
    name: str
    extensions: tuple[str, ...]

    def parse(self, path: Path) -> UnifiedDocument: ...
