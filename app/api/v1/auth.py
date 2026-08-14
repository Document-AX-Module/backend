import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    REFRESH_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import ProvidersResponse, TokenResponse, UserRead
from app.services.auth_providers import ProviderError, get_provider
from app.services.auth_service import AuthService, get_auth_service

settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
STATE_COOKIE = "oauth_state"
COOKIE_PATH = "/api/v1/auth"


def _callback_url(request: Request, provider: str) -> str:
    return str(request.url_for("auth_callback", provider=provider))


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=COOKIE_PATH,
        max_age=settings.refresh_token_expire_days * 86400,
    )


@router.get("/providers", response_model=ProvidersResponse)
def list_providers() -> ProvidersResponse:
    return ProvidersResponse(providers=settings.auth_providers)


@router.get("/login/{provider}")
def login(provider: str, request: Request) -> RedirectResponse:
    try:
        prov = get_provider(provider)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    state = secrets.token_urlsafe(24)
    redirect_uri = _callback_url(request, provider)
    response = RedirectResponse(prov.authorization_url(state, redirect_uri))
    response.set_cookie(
        STATE_COOKIE,
        state,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=COOKIE_PATH,
        max_age=600,
    )
    return response


@router.get("/callback/{provider}", name="auth_callback")
def callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    expected_state = request.cookies.get(STATE_COOKIE)
    if not expected_state or expected_state != state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid oauth state")

    try:
        prov = get_provider(provider)
        identity = prov.fetch_identity(code, _callback_url(request, provider))
    except ProviderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    user = auth_service.get_or_create_user(identity)
    refresh = create_refresh_token(user.id)

    response = RedirectResponse(f"{settings.frontend_url}/auth/callback")
    _set_refresh_cookie(response, refresh)
    response.delete_cookie(STATE_COOKIE, path=COOKIE_PATH)
    return response


@router.post("/token/refresh", response_model=TokenResponse)
def refresh_access_token(
    request: Request, db: Session = Depends(get_db)
) -> TokenResponse:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no refresh token")
    try:
        payload = decode_token(token, REFRESH_TOKEN_TYPE)
    except TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token") from None

    user = UserRepository(db).get(int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")

    access = create_access_token(user.id, user.role)
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
