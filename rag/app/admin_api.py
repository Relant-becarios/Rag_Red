"""Administration API (users, roles, conversation log, usage, audit).
    users.manage            GET/POST /admin/users        PATCH /admin/users/{id}
                            GET/POST /admin/roles        PATCH/DELETE /admin/roles/{id}
    conversations.read_all  GET /admin/conversations     GET /admin/messages.csv
    reports.read            GET /admin/usage             GET /admin/audit
"""
from __future__ import annotations

import csv
import io
import re
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, text

from app.auth import CurrentUser, audit, require, revoke_user_sessions
from app.config import settings
from app.conversations import conversation_dict, iso, parse_local_date
from app.db import session_scope
from app.models import AuditLog, Conversation, Message, Role, User
from app.security import PERMISSIONS, hash_password, merged_preferences, password_problem

router = APIRouter(prefix="/admin")
USERNAME_RE = re.compile(r"^[a-z0-9._@-]{3,80}$")


# ============================================================================ helpers
def role_dict(r: Role, n_users: int | None = None) -> dict:
    d = {"id": r.id, "name": r.name, "description": r.description,
         "permissions": sorted(r.permissions or []), "daily_query_limit": r.daily_query_limit,
         "daily_token_limit": r.daily_token_limit, "monthly_token_limit": r.monthly_token_limit,
         "default_prompt_id": r.default_prompt_id, "is_system": r.is_system}
    if n_users is not None:
        d["users"] = n_users
    return d


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email,
            "role_id": u.role_id, "role": u.role.name, "is_active": u.is_active,
            "must_change_password": u.must_change_password,
            "locked": bool(u.locked_until and u.locked_until > _now()),
            "last_login_at": iso(u.last_login_at), "created_at": iso(u.created_at),
            "daily_query_limit": u.daily_query_limit, "daily_token_limit": u.daily_token_limit,
            "monthly_token_limit": u.monthly_token_limit,
            "preferences": merged_preferences(u.preferences)}

def _now():
    from app.db import utcnow
    return utcnow()


def _check_admins_left(s) -> None:
    """At least one active user must keep `users.manage`, or nobody could fix things."""
    s.flush()
    manage_roles = [r.id for r in s.query(Role).all() if "users.manage" in (r.permissions or [])]
    n = (s.query(func.count(User.id))
         .filter(User.is_active.is_(True), User.role_id.in_(manage_roles or [-1])).scalar())
    if not n:
        raise HTTPException(status_code=400, detail=(
            "Debe quedar al menos un usuario activo con permiso de administrar usuarios."))


def _clean_perms(perms: list[str]) -> list[str]:
    bad = [p for p in perms if p not in PERMISSIONS]
    if bad:
        raise HTTPException(status_code=400, detail=f"Permisos desconocidos: {', '.join(bad)}")
    return sorted(set(perms))


def require_any(*perms: str):
    """Logged in with at least ONE of the permissions."""
    from app.auth import current_user

    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="must_change_password")
        if not any(user.can(p) for p in perms):
            raise HTTPException(status_code=403, detail="Tu rol no tiene permiso para esta acción.")
        return user
    return dep


@router.get("/user-options")
def user_options(user: CurrentUser = Depends(require_any(
        "users.manage", "conversations.read_all", "reports.read"))) -> dict:
    """Id + name of every user, for the filters of the log / usage / audit screens."""
    with session_scope() as s:
        return {"items": [{"id": u.id, "username": u.username, "full_name": u.full_name,
                           "is_active": u.is_active}
                          for u in s.query(User).order_by(User.username)]}


# ============================================================================ users
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    full_name: str = Field("", max_length=150)
    email: str | None = Field(None, max_length=190)
    password: str = Field(..., max_length=200)
    role_id: int
    must_change_password: bool = True


class UserPatch(BaseModel):
    full_name: str | None = Field(None, max_length=150)
    email: str | None = Field(None, max_length=190)
    role_id: int | None = None
    is_active: bool | None = None
    password: str | None = Field(None, max_length=200)      # reset by the admin
    must_change_password: bool | None = None
    unlock: bool | None = None


@router.get("/users")
def list_users(q: str = "", user: CurrentUser = Depends(require("users.manage"))) -> dict:
    with session_scope() as s:
        base = s.query(User)
        if q.strip():
            like = f"%{q.strip()}%"
            base = base.filter(or_(User.username.like(like), User.full_name.like(like),
                                   User.email.like(like)))
        rows = base.order_by(User.username).all()
        return {"items": [user_dict(u) for u in rows]}


@router.post("/users")
def create_user(body: UserCreate, user: CurrentUser = Depends(require("users.manage"))) -> dict:
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail=(
            "Usuario inválido: 3-80 caracteres, solo letras minúsculas, números y . _ @ -"))
    problem = password_problem(body.password, settings.password_min_length)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    with session_scope() as s:
        if s.get(Role, body.role_id) is None:
            raise HTTPException(status_code=400, detail="Rol inexistente.")
        if s.query(User).filter(User.username == username).first():
            raise HTTPException(status_code=409, detail="Ese nombre de usuario ya existe.")
        u = User(username=username, full_name=body.full_name.strip(),
                 email=(body.email or "").strip() or None,
                 password_hash=hash_password(body.password), role_id=body.role_id,
                 must_change_password=body.must_change_password, preferences={})
        s.add(u)
        s.flush()
        s.refresh(u)
        audit("user_created", user=user, session=s,
              detail={"target": username, "role_id": body.role_id})
        return user_dict(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch,
                user: CurrentUser = Depends(require("users.manage"))) -> dict:
    changes = body.model_dump(exclude_none=True)
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        if "full_name" in changes:
            u.full_name = body.full_name.strip()
        if "email" in changes:
            u.email = body.email.strip() or None
        if "role_id" in changes and body.role_id != u.role_id:
            if s.get(Role, body.role_id) is None:
                raise HTTPException(status_code=400, detail="Rol inexistente.")
            u.role_id = body.role_id
            s.flush()
            s.expire(u, ["role"])
        if "is_active" in changes:
            if not body.is_active and u.id == user.id:
                raise HTTPException(status_code=400, detail="No puedes desactivar tu propio usuario.")
            u.is_active = body.is_active
            if not body.is_active:
                revoke_user_sessions(s, u.id)
        if body.password:
            problem = password_problem(body.password, settings.password_min_length)
            if problem:
                raise HTTPException(status_code=400, detail=problem)
            u.password_hash = hash_password(body.password)
            u.must_change_password = True if body.must_change_password is None \
                else body.must_change_password
            revoke_user_sessions(s, u.id)
        elif "must_change_password" in changes:
            u.must_change_password = body.must_change_password
        if body.unlock:
            u.locked_until, u.failed_logins = None, 0
        _check_admins_left(s)
        detail = {k: v for k, v in changes.items() if k != "password"}
        if body.password:
            detail["password_reset"] = True
        audit("user_updated", user=user, session=s, detail={"target": u.username, **detail})
        s.flush()
        s.refresh(u)
        return user_dict(u)


# ============================================================================ roles
class RoleBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: str = Field("", max_length=255)
    permissions: list[str] = Field(default_factory=list)
    daily_query_limit: int | None = Field(None, ge=1, le=100000)


class RolePatch(BaseModel):
    description: str | None = Field(None, max_length=255)
    permissions: list[str] | None = None
    daily_query_limit: int | None = Field(None, ge=0, le=100000)   # 0 = unlimited


@router.get("/roles")
def list_roles(user: CurrentUser = Depends(require("users.manage"))) -> dict:
    with session_scope() as s:
        counts = dict(s.query(User.role_id, func.count(User.id)).group_by(User.role_id).all())
        return {"items": [role_dict(r, counts.get(r.id, 0))
                          for r in s.query(Role).order_by(Role.id).all()],
                "permissions": PERMISSIONS}


@router.post("/roles")
def create_role(body: RoleBody, user: CurrentUser = Depends(require("users.manage"))) -> dict:
    name = body.name.strip().lower()
    with session_scope() as s:
        if s.query(Role).filter(Role.name == name).first():
            raise HTTPException(status_code=409, detail="Ya existe un rol con ese nombre.")
        r = Role(name=name, description=body.description.strip(),
                 permissions=_clean_perms(body.permissions),
                 daily_query_limit=body.daily_query_limit, is_system=False)
        s.add(r)
        s.flush()
        from app.kb import ensure_role_kb
        ensure_role_kb(s, r)                   # every role gets its shared folder
        audit("role_created", user=user, session=s, detail=role_dict(r))
        return role_dict(r, 0)


@router.patch("/roles/{role_id}")
def update_role(role_id: int, body: RolePatch,
                user: CurrentUser = Depends(require("users.manage"))) -> dict:
    with session_scope() as s:
        r = s.get(Role, role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        if body.description is not None:
            r.description = body.description.strip()
        if body.permissions is not None:
            r.permissions = _clean_perms(body.permissions)
        if "daily_query_limit" in body.model_fields_set:
            r.daily_query_limit = body.daily_query_limit or None
        _check_admins_left(s)
        audit("role_updated", user=user, session=s, detail=role_dict(r))
        n = s.query(func.count(User.id)).filter(User.role_id == r.id).scalar()
        return role_dict(r, n)


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, user: CurrentUser = Depends(require("users.manage"))) -> dict:
    with session_scope() as s:
        r = s.get(Role, role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        if r.is_system:
            raise HTTPException(status_code=400, detail="Los roles base no se pueden borrar.")
        if s.query(User).filter(User.role_id == r.id).count():
            raise HTTPException(status_code=400,
                                detail="El rol tiene usuarios asignados; cámbialos primero.")
        s.delete(r)
        audit("role_deleted", user=user, session=s, detail={"name": r.name})
    return {"ok": True}


# ============================================================================ conversation log
def _msg_filters(user_id: int | None, date_from: str | None, date_to: str | None) -> list:
    f = []
    if user_id:
        f.append(Message.user_id == user_id)
    d0, d1 = parse_local_date(date_from), parse_local_date(date_to, end=True)
    if d0:
        f.append(Message.created_at >= d0)
    if d1:
        f.append(Message.created_at < d1)
    return f


@router.get("/conversations")
def all_conversations(user_id: int | None = None, q: str = "",
                      date_from: str | None = None, date_to: str | None = None,
                      include_deleted: bool = True,
                      limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                      user: CurrentUser = Depends(require("conversations.read_all"))) -> dict:
    with session_scope() as s:
        base = s.query(Conversation)
        if user_id:
            base = base.filter(Conversation.user_id == user_id)
        if not include_deleted:
            base = base.filter(Conversation.deleted_at.is_(None))
        d0, d1 = parse_local_date(date_from), parse_local_date(date_to, end=True)
        if d0:
            base = base.filter(Conversation.updated_at >= d0)
        if d1:
            base = base.filter(Conversation.created_at < d1)
        if q.strip():
            like = f"%{q.strip()}%"
            sub = s.query(Message.id).filter(Message.conversation_id == Conversation.id,
                                             Message.content.like(like))
            base = base.filter(or_(Conversation.title.like(like), sub.exists()))
        total = base.count()
        rows = (base.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .offset(offset).limit(limit).all())
        return {"total": total, "items": [conversation_dict(c, with_user=True) for c in rows]}


@router.get("/messages.csv")
def export_messages(user_id: int | None = None, date_from: str | None = None,
                    date_to: str | None = None,
                    user: CurrentUser = Depends(require("conversations.read_all"))):
    """Question/answer pairs as CSV (Excel opens it: UTF-8 with BOM, ';' separator)."""
    filters = _msg_filters(user_id, date_from, date_to)
    audit("messages_exported", user=user,
          detail={"user_id": user_id, "from": date_from, "to": date_to})

    def rows() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["fecha_utc", "usuario", "conversacion_id", "conversacion", "mensaje_id",
                    "tipo", "texto", "ruta", "escalamiento", "estado", "llamadas_llm",
                    "tokens_entrada", "tokens_salida", "costo_usd", "ms"])
        yield "﻿" + buf.getvalue()
        last_id = 0
        while True:                                   # keyset pagination: constant memory
            with session_scope() as s:
                batch = (s.query(Message, User.username, Conversation.title)
                         .join(User, User.id == Message.user_id)
                         .join(Conversation, Conversation.id == Message.conversation_id)
                         .filter(and_(*filters, Message.id > last_id))
                         .order_by(Message.id).limit(2000).all())
            if not batch:
                break
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            for m, uname, title in batch:
                w.writerow([iso(m.created_at), uname, m.conversation_id, title, m.id,
                            "pregunta" if m.role == "user" else "respuesta",
                            m.content if m.status == "ok" else f"ERROR: {m.error}",
                            m.route, m.escalation, m.status, m.llm_calls, m.input_tokens,
                            m.output_tokens, f"{float(m.cost_usd or 0):.6f}", m.latency_ms])
            last_id = batch[-1][0].id
            yield buf.getvalue()

    return StreamingResponse(rows(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="conversaciones.csv"'})


# ============================================================================ usage + audit
def _local_date_expr(s):
    """DATE(created_at shifted to the server's local time) for daily grouping."""
    from app.conversations import local_tz
    from datetime import datetime
    off_min = int(datetime.now(local_tz()).utcoffset().total_seconds() // 60)
    if s.get_bind().dialect.name == "mysql":
        return func.date(func.timestampadd(text("MINUTE"), off_min, Message.created_at))
    if s.get_bind().dialect.name == "sqlite":
        return func.date(Message.created_at, f"{off_min:+d} minutes")
    return func.date(Message.created_at + text(f"interval '{off_min} minutes'"))


@router.get("/usage")
def usage(date_from: str | None = None, date_to: str | None = None,
          user: CurrentUser = Depends(require("reports.read"))) -> dict:
    filters = [Message.role == "assistant", *_msg_filters(None, date_from, date_to)]
    with session_scope() as s:
        agg = (s.query(Message.user_id, func.count(Message.id),
                       func.sum(case((Message.status == "error", 1), else_=0)),
                       func.sum(case((Message.route == "agent", 1), else_=0)),
                       func.sum(Message.llm_calls), func.sum(Message.input_tokens),
                       func.sum(Message.output_tokens), func.sum(Message.cost_usd),
                       func.avg(Message.latency_ms), func.max(Message.created_at))
               .filter(*filters).group_by(Message.user_id).all())
        users = {u.id: u for u in s.query(User).filter(User.id.in_([a[0] for a in agg] or [-1]))}
        convs = dict(s.query(Message.user_id, func.count(func.distinct(Message.conversation_id)))
                     .filter(*filters).group_by(Message.user_id).all())
        items = []
        for uid, n, errs, agent, calls, tin, tout, cost, avg_ms, last in agg:
            u = users.get(uid)
            items.append({"user_id": uid, "username": u.username if u else f"#{uid}",
                          "full_name": u.full_name if u else "",
                          "role": u.role.name if u else "", "questions": int(n or 0),
                          "conversations": int(convs.get(uid, 0)), "errors": int(errs or 0),
                          "agent_answers": int(agent or 0), "llm_calls": int(calls or 0),
                          "input_tokens": int(tin or 0), "output_tokens": int(tout or 0),
                          "cost_usd": round(float(cost or 0), 6),
                          "avg_ms": int(avg_ms or 0), "last_at": iso(last)})
        items.sort(key=lambda x: x["questions"], reverse=True)
        day = _local_date_expr(s)
        daily = (s.query(day, func.count(Message.id), func.sum(Message.cost_usd))
                 .filter(*filters).group_by(day).order_by(day).all())
        totals = {k: sum(i[k] for i in items) for k in
                  ("questions", "errors", "agent_answers", "llm_calls", "input_tokens",
                   "output_tokens")}
        totals["cost_usd"] = round(sum(i["cost_usd"] for i in items), 6)
        totals["active_users"] = len(items)
        return {"items": items, "totals": totals,
                "daily": [{"date": str(d), "questions": int(n), "cost_usd": round(float(c or 0), 6)}
                          for d, n, c in daily]}


@router.get("/audit")
def audit_log(user_id: int | None = None, action: str = "",
              date_from: str | None = None, date_to: str | None = None,
              limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
              user: CurrentUser = Depends(require("reports.read"))) -> dict:
    with session_scope() as s:
        base = s.query(AuditLog)
        if user_id:
            base = base.filter(AuditLog.user_id == user_id)
        if action:
            base = base.filter(AuditLog.action == action)
        d0, d1 = parse_local_date(date_from), parse_local_date(date_to, end=True)
        if d0:
            base = base.filter(AuditLog.created_at >= d0)
        if d1:
            base = base.filter(AuditLog.created_at < d1)
        total = base.count()
        rows = base.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
        actions = [a for (a,) in s.query(AuditLog.action).distinct().order_by(AuditLog.action)]
        return {"total": total, "actions": actions,
                "items": [{"id": a.id, "created_at": iso(a.created_at), "user_id": a.user_id,
                           "username": a.username, "action": a.action, "ip": a.ip,
                           "detail": a.detail} for a in rows]}
