from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AI Platform API"
    app_description: str = "AX Document Platform 백엔드 API"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_platform"

    cors_allow_origins: list[str] = ["http://localhost:3000"]

    # ── Auth (JWT) ──
    jwt_secret: str = "dev-secret-change-me"  # 운영에서는 반드시 .env 로 교체
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    # 로그인 콜백 후 프론트로 돌아갈 주소
    frontend_url: str = "http://localhost:3000"
    # 활성 로그인 provider. 크리덴셜 준비 전 dev 는 ["mock"], 준비되면 ["google"] 추가.
    auth_providers: list[str] = ["mock"]

    # refresh 토큰 쿠키
    cookie_secure: bool = False  # https 배포 시 True
    cookie_samesite: str = "lax"

    # ── Google OAuth (크리덴셜 준비되면 .env 로 주입) ──
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # 미지정 시 요청 기반 콜백 URL 을 사용한다.
    google_redirect_uri: str | None = None

    # ── Storage (로컬 파일) ──
    # 로컬 uvicorn 은 backend/ 기준 ../storage, 컨테이너는 compose 가 /data/storage 로 주입.
    storage_root: str = "../storage"
    max_upload_mb: int = 50
    allowed_upload_extensions: list[str] = [
        "pdf", "hwpx", "hwp", "docx", "xlsx", "pptx", "txt", "md",
    ]

    # ── Chunking ──
    chunk_max_chars: int = 1000
    chunk_overlap_chars: int = 150

    # ── LLM (GPT) ──
    # 키가 없으면 MockLLMProvider(규칙 기반)로 자동 폴백한다.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # "auto"(키 있으면 openai, 없으면 mock) · "openai" · "mock"
    llm_provider: str = "auto"
    # 교정에서 지적할 오류 유형. spelling(맞춤법) · spacing(띄어쓰기) ·
    # grammar(문법) · expression(표현·문체) 중 고른다.
    # 기본값이 좁은 이유: 문서가 길어질수록 GPT 가 "더 자연스럽게" 류의 표현·문맥
    # 지적을 쏟아내 정작 오탈자가 묻힌다. 프롬프트의 category enum 도 함께 좁혀지고,
    # 그래도 새어 나온 지적은 provider 가 응답에서 걸러낸다.
    proofread_categories: list[str] = ["spelling", "spacing"]
    # proofread 시 한 섹션에서 넘기는 최대 글자 수(과금/토큰 보호)
    proofread_max_chars_per_section: int = 4000
    # 배치 호출: 한 번의 GPT 호출에 묶는 상한(글자수/섹션수). 넘으면 다음 배치로.
    proofread_batch_max_chars: int = 6000
    proofread_batch_max_sections: int = 40


@lru_cache
def get_settings() -> Settings:
    return Settings()
