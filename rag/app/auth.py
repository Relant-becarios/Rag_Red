"""Login, sessions, permission checks and user preferences.

Sessions: an HttpOnly cookie holds a random token; MySQL stores only its SHA-256, so a
leaked database cannot be used to log in. Every login is a row in `user_sessions`
(revocable: logout, password change, deactivating the user). API clients may send the
same token as `Authorization: Bearer <token>`.

    POST /auth/login            {username, password}     -> cookie + user
    POST /auth/logout
    GET  /auth/me                                         -> user, role, permissions, prefs
    POST /auth/change-password  {current_password, new_password}
    GET  /me/preferences    PUT /me/preferences
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.config import settings
from app.db import session_scope, utcnow
from app.models import AuditLog, User, UserSession
from app.security import (merged_preferences, new_session_token, password_problem,
                          token_hash, verify_dummy, verify_password, hash_password)

log = logging.getLogger("rag.auth")
TOUCH_EVERY = timedelta(minutes=5)          # write last_seen at most this often


@dataclass
class CurrentUser:
    id: int
    username: str
    full_name: str
    role: str
    permissions: set[str]
    preferences: dict
    must_change_password: bool
    daily_query_limit: int | None
    session_id: int | None = None
    ip: str = ""
    extra: dict = field(default_factory=dict)
    role_id: int = 0
    daily_token_limit: int | None = None        # effective (user override or role)
    monthly_token_limit: int | None = None
    default_prompt_id: int | None = None        # role default prompt profile

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "full_name": self.full_name,
                "role": self.role, "role_id": self.role_id,
                "permissions": sorted(self.permissions),
                "preferences": self.preferences,
                "must_change_password": self.must_change_password,
                "daily_query_limit": self.daily_query_limit,
                "daily_token_limit": self.daily_token_limit,
                "monthly_token_limit": self.monthly_token_limit}


# ---------------------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def audit(action: str, *, user: CurrentUser | None = None, user_id: int | None = None,
          username: str = "", ip: str = "", detail: Any = None, session=None) -> None:
    """Write one audit row. Never lets an audit failure break the request."""
    row = AuditLog(action=action, user_id=user.id if user else user_id,
                   username=(user.username if user else username)[:80],
                   ip=ip or (user.ip if user else ""), detail=detail)
    try:
        if session is not None:
            session.add(row)
        else:
            with session_scope() as s:
                s.add(row)
    except Exception:  # pragma: no cover
        log.exception("audit write failed (%s)", action)


def _token_from(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(settings.session_cookie_name, "")


def effective_limit(user_value: int | None, role_value: int | None) -> int | None:
    """User NULL -> role value; user 0 -> unlimited; role NULL/0 -> unlimited."""
    if user_value is None:
        return role_value or None
    return user_value or None


def _to_current(u: User, sess_id: int | None, ip: str) -> CurrentUser:
    r = u.role
    return CurrentUser(id=u.id, username=u.username, full_name=u.full_name,
                       role=r.name, permissions=set(r.permissions or []),
                       preferences=merged_preferences(u.preferences),
                       must_change_password=u.must_change_password,
                       daily_query_limit=effective_limit(u.daily_query_limit,
                                                         r.daily_query_limit),
                       session_id=sess_id, ip=ip, role_id=r.id,
                       daily_token_limit=effective_limit(u.daily_token_limit,
                                                         r.daily_token_limit),
                       monthly_token_limit=effective_limit(u.monthly_token_limit,
                                                           r.monthly_token_limit),
                       default_prompt_id=r.default_prompt_id)


def optional_user(request: Request) -> CurrentUser | None:
    token = _token_from(request)
    if not token:
        return None
    now = utcnow()
    with session_scope() as s:
        row = (s.query(UserSession, User).join(User, User.id == UserSession.user_id)
               .filter(UserSession.token_hash == token_hash(token),
                       UserSession.revoked_at.is_(None),
                       UserSession.expires_at > now, User.is_active.is_(True))
               .first())
        if row is None:
            return None
        sess, user = row
        if now - sess.last_seen_at > TOUCH_EVERY:          # sliding expiry, few writes
            sess.last_seen_at = now
            sess.expires_at = now + timedelta(hours=settings.session_hours)
        return _to_current(user, sess.id, client_ip(request))


def current_user(request: Request) -> CurrentUser:
    u = optional_user(request)
    if u is None:
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")
    request.state.user = u
    return u


def require(*perms: str) -> Callable[..., CurrentUser]:
    """Dependency: logged in, password already changed, and ALL the given permissions."""
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="must_change_password")
        missing = [p for p in perms if not user.can(p)]
        if missing:
            raise HTTPException(status_code=403,
                                detail="Tu rol no tiene permiso para esta acción.")
        return user
    return dep


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(settings.session_cookie_name, token, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/",
                        max_age=int(settings.session_hours * 3600))


def revoke_user_sessions(session, user_id: int, except_id: int | None = None) -> None:
    q = update(UserSession).where(UserSession.user_id == user_id,
                                  UserSession.revoked_at.is_(None))
    if except_id:
        q = q.where(UserSession.id != except_id)
    session.execute(q.values(revoked_at=utcnow()))


# ---------------------------------------------------------------------------- schemas
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


class Preferences(BaseModel):
    deep_search: bool | None = None
    show_steps: bool | None = None
    theme: Literal["auto", "light", "dark"] | None = None
    sidebar_open: bool | None = None
    kbs: list[str] | None = Field(None, max_length=50)
    prompt_id: int | None = None
    mode: Literal["chat", "quote"] | None = None


# ---------------------------------------------------------------------------- routes
router = APIRouter()


@router.post("/auth/login")
def login(req: LoginRequest, request: Request, response: Response) -> dict:
    ip, now = client_ip(request), utcnow()
    username = req.username.strip().lower()
    generic = HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    with session_scope() as s:
        user = s.query(User).filter(User.username == username).with_for_update().first()
        if user is None:
            verify_dummy(req.password)                     # same timing as a real user
            audit("login_failed", username=username, ip=ip, detail={"reason": "unknown_user"},
                  session=s)
            raise_after = generic
        elif not user.is_active:
            verify_dummy(req.password)
            audit("login_failed", user_id=user.id, username=username, ip=ip,
                  detail={"reason": "inactive"}, session=s)
            raise_after = generic
        elif user.locked_until and user.locked_until > now:
            mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
            audit("login_failed", user_id=user.id, username=username, ip=ip,
                  detail={"reason": "locked"}, session=s)
            raise_after = HTTPException(status_code=423, detail=(
                f"Cuenta bloqueada por intentos fallidos. Intenta de nuevo en {mins} min "
                "o pide a un administrador que la desbloquee."))
        elif not verify_password(req.password, user.password_hash):
            user.failed_logins += 1
            detail = {"reason": "bad_password", "attempt": user.failed_logins}
            if user.failed_logins >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_logins = 0
                detail["locked"] = True
            audit("login_failed", user_id=user.id, username=username, ip=ip, detail=detail,
                  session=s)
            raise_after = generic
        else:
            raise_after = None
            user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
            token, th = new_session_token()
            sess = UserSession(token_hash=th, user_id=user.id, created_at=now, last_seen_at=now,
                               expires_at=now + timedelta(hours=settings.session_hours), ip=ip,
                               user_agent=request.headers.get("user-agent", "")[:255])
            s.add(sess)
            s.flush()
            audit("login", user_id=user.id, username=username, ip=ip, session=s)
            cu = _to_current(user, sess.id, ip)
    if raise_after is not None:
        raise raise_after
    _set_cookie(response, token)
    return {"user": cu.public(), "token": token}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    u = optional_user(request)
    if u and u.session_id:
        with session_scope() as s:
            s.execute(update(UserSession).where(UserSession.id == u.session_id)
                      .values(revoked_at=utcnow()))
            audit("logout", user=u, session=s)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    from app.security import PERMISSIONS
    return {"user": user.public(), "permission_labels": PERMISSIONS,
            "password_min_length": settings.password_min_length}


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest,
                    user: CurrentUser = Depends(current_user)) -> dict:
    problem = password_problem(req.new_password, settings.password_min_length)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    with session_scope() as s:
        u = s.get(User, user.id)
        if u is None or not verify_password(req.current_password, u.password_hash):
            raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
        if verify_password(req.new_password, u.password_hash):
            raise HTTPException(status_code=400,
                                detail="La nueva contraseña debe ser distinta de la actual.")
        u.password_hash = hash_password(req.new_password)
        u.must_change_password = False
        revoke_user_sessions(s, u.id, except_id=user.session_id)   # log out other devices
        audit("password_changed", user=user, session=s)
    return {"ok": True}


@router.get("/me/preferences")
def get_preferences(user: CurrentUser = Depends(current_user)) -> dict:
    return user.preferences


@router.put("/me/preferences")
def put_preferences(prefs: Preferences, user: CurrentUser = Depends(current_user)) -> dict:
    with session_scope() as s:
        u = s.get(User, user.id)
        merged = merged_preferences(u.preferences)
        merged.update({k: v for k, v in prefs.model_dump().items() if v is not None})
        # explicit null resets the chat selection to the defaults
        for k in ("kbs", "prompt_id"):
            if k in prefs.model_fields_set and getattr(prefs, k) is None:
                merged[k] = None
        u.preferences = merged
    return merged
