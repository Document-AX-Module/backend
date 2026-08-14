from pydantic import BaseModel, Field


class SpellCheckRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class Correction(BaseModel):
    original: str
    suggestion: str
    start: int
    end: int
    reason: str | None = None


class SpellCheckResponse(BaseModel):
    original_text: str
    corrected_text: str
    corrections: list[Correction]
