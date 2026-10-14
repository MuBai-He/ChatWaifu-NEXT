"""Single-owner password setup and password-to-Bearer exchange."""

import hmac
from typing import NoReturn, cast

from fastapi import APIRouter, HTTPException, Request, Response

from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.persistence.atomic_secret_store import SecretStoreError
from chatwaifu_runtime.security.password import (
    PasswordAuthError,
    PasswordLogin,
    PasswordSession,
    PasswordStatus,
    PasswordUpdate,
)

router = APIRouter(prefix="/v1/runtime/password", tags=["runtime-auth"])


def _container(request: Request) -> RuntimeContainer:
    return cast(RuntimeContainer, request.app.state.container)


def _require_credential(request: Request, container: RuntimeContainer) -> None:
    # Never permit anonymous setup, even in auth-disabled development mode.
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    candidate = token.strip().encode("utf-8") if scheme.lower() == "bearer" else b""
    admin = container.settings.security.admin_token
    if not candidate or not (
        hmac.compare_digest(candidate, container.capability_token.encode())
        or (admin and hmac.compare_digest(candidate, admin.get_secret_value().encode()))
        or container.password_auth.is_token_valid(token.strip())
    ):
        raise HTTPException(401, "请先使用访问令牌或密码登录。")


def _raise_auth_error(error: PasswordAuthError) -> NoReturn:
    headers = {"Cache-Control": "no-store"}
    if error.retry_after:
        headers["Retry-After"] = str(error.retry_after)
    raise HTTPException(error.status, str(error), headers=headers) from error


@router.get("", response_model=PasswordStatus)
def password_status(request: Request, response: Response) -> PasswordStatus:
    response.headers["Cache-Control"] = "no-store"
    return _container(request).password_auth.status()


@router.post("/login", response_model=PasswordSession)
def password_login(body: PasswordLogin, request: Request, response: Response) -> PasswordSession:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _container(request).password_auth.login(body.password.get_secret_value())
    except PasswordAuthError as error:
        _raise_auth_error(error)


@router.put("", response_model=PasswordSession)
def update_password(body: PasswordUpdate, request: Request, response: Response) -> PasswordSession:
    response.headers["Cache-Control"] = "no-store"
    container = _container(request)
    _require_credential(request, container)
    try:
        session = container.password_auth.update(body)
    except PasswordAuthError as error:
        _raise_auth_error(error)
    except (SecretStoreError, OSError) as error:
        raise HTTPException(
            503, "无法安全保存密码，请检查服务器配置目录。", headers={"Cache-Control": "no-store"}
        ) from error
    container.ws_ticket_store.clear()
    return session
