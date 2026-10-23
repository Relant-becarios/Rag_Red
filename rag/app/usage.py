"""Token limits and the token-usage dashboard.

Limits (checked before every question, in conversations.begin_turn):
    user   daily_query_limit, daily_token_limit, monthly_token_limit
           NULL = inherit from the role, 0 = no limit for this user
    role   the same three fields; NULL / 0 = no limit
    global monthly_token_limit / daily_token_limit for the whole organisation
           (app_settings key "token_limits")
A question that starts below the limit is answered even if it ends a little above it;
the next one is refused (HTTP 429) until the day / month resets (server local time).

Routes
    GET   /me/usage                          my tokens today / this month and my limits
    GET   /admin/token-dashboard             [reports.read]  totals, per day / user / role /
                                             knowledge base / route / prompt
    GET   /admin/limits                      [reports.read | limits.manage]
    PUT   /admin/limits                      [limits.manage] global limits
    PATCH /admin/limits/users/{id}           [limits.manage] per-user overrides
    PATCH /admin/limits/roles/{id}           [limits.manage] per-role limits
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func

from app.auth import CurrentUser, audit, current_user, effective_limit, require
from app.config import settings
from app.conversations import iso, local_day_start_utc, local_tz, parse_local_date
from app.db import get_setting, session_scope, set_setting
from app.models import KnowledgeBase, Message, PromptProfile, Role, User

GLOBAL_KEY = "token_limits"
TOKENS = Message.input_tokens + Message.output_tokens


# ====================================================================== time helpers
def local_month_start_utc(now: datetime | None = None) -> datetime:
    loc = (now or datetime.now(timezone.utc)).astimezone(local_tz())
    start = loc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def next_month_label() -> str:
    loc = datetime.now(local_tz())
    nxt = (loc.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt.strftime("%d/%m/%Y")


# ====================================================================== reads
def global_limits() -> dict:
    v = get_setting(GLOBAL_KEY, {}) or {}
    return {"monthly_token_limit": v.get("monthly_token_limit") or None,
            "daily_token_limit": v.get("daily_token_limit") or None}


_g_lock = threading.Lock()
_g_cache: dict[str, Any] = {"at": 0.0, "day": 0, "month": 0}


def global_tokens(fresh: bool = False) -> tuple[int, int]:
    """(tokens today, tokens this month) of every user; cached a few seconds."""
    with _g_lock:
        if not fresh and time.time() - _g_cache["at"] < settings.usage_cache_secs:
            return _g_cache["day"], _g_cache["month"]
    d0, m0 = local_day_start_utc(), local_month_start_utc()
    with session_scope() as s:
        day, month = s.query(
            func.coalesce(func.sum(case((Message.created_at >= d0, TOKENS), else_=0)), 0),
            func.coalesce(func.sum(TOKENS), 0),
        ).filter(Message.role == "assistant", Message.created_at >= min(d0, m0)).one()
    with _g_lock:
        _g_cache.update(at=time.time(), day=int(day or 0), month=int(month or 0))
    return int(day or 0), int(month or 0)


def user_usage(s, user_id: int) -> dict:
    d0, m0 = local_day_start_utc(), local_month_start_utc()
    tok_day, tok_month = s.query(
        func.coalesce(func.sum(case((Message.created_at >= d0, TOKENS), else_=0)), 0),
        func.coalesce(func.sum(TOKENS), 0),
    ).filter(Message.user_id == user_id, Message.role == "assistant",
             Message.created_at >= min(d0, m0)).one()
    q_day = (s.query(func.count(Message.id))
             .filter(Message.user_id == user_id, Message.role == "user",
                     Message.created_at >= d0).scalar() or 0)
    return {"tokens_today": int(tok_day or 0), "tokens_month": int(tok_month or 0),
            "questions_today": int(q_day)}


def _pct(used: int, limit: int | None) -> float | None:
    return round(100.0 * used / limit, 1) if limit else None


def quota_for(user: CurrentUser, s=None) -> dict:
    if s is None:
        with session_scope() as s2:
            u = user_usage(s2, user.id)
    else:
        u = user_usage(s, user.id)
    out = {**u, "daily_query_limit": user.daily_query_limit,
           "daily_token_limit": user.daily_token_limit,
           "monthly_token_limit": user.monthly_token_limit,
           "pct_day": _pct(u["tokens_today"], user.daily_token_limit),
           "pct_month": _pct(u["tokens_month"], user.monthly_token_limit),
           "warn_pct": settings.limit_warn_pct}
    pcts = [p for p in (out["pct_day"], out["pct_month"],
                        _pct(u["questions_today"], user.daily_query_limit)) if p is not None]
    out["max_pct"] = max(pcts) if pcts else None
    return out


def check_limits(s, user: CurrentUser) -> None:
    """Raise 429 when the user (or the organisation) has no budget left."""
    u = user_usage(s, user.id)
    if user.daily_query_limit and u["questions_today"] >= user.daily_query_limit:
        raise HTTPException(status_code=429, detail=(
            f"Alcanzaste el límite de {user.daily_query_limit} preguntas por día de tu "
            "rol. Se restablece a medianoche."))
    if user.daily_token_limit and u["tokens_today"] >= user.daily_token_limit:
        raise HTTPException(status_code=429, detail=(
            f"Alcanzaste tu límite diario de {user.daily_token_limit:,} tokens. "
            "Se restablece a medianoche."))
    if user.monthly_token_limit and u["tokens_month"] >= user.monthly_token_limit:
        raise HTTPException(status_code=429, detail=(
            f"Alcanzaste tu límite mensual de {user.monthly_token_limit:,} tokens. "
            f"Se restablece el {next_month_label()}. Pide al administrador ampliarlo."))
    g = global_limits()
    if g["monthly_token_limit"] or g["daily_token_limit"]:
        day, month = global_tokens()
        if g["daily_token_limit"] and day >= g["daily_token_limit"]:
            raise HTTPException(status_code=429, detail=(
                "La organización alcanzó su límite diario de tokens. Se restablece a medianoche."))
        if g["monthly_token_limit"] and month >= g["monthly_token_limit"]:
            raise HTTPException(status_code=429, detail=(
                "La organización alcanzó su límite mensual de tokens. Avisa al administrador."))


# ====================================================================== routes
router = APIRouter()


@router.get("/me/usage")
def my_usage(user: CurrentUser = Depends(current_user)) -> dict:
    return quota_for(user)


def require_any(*perms: str):
    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="must_change_password")
        if not any(user.can(p) for p in perms):
            raise HTTPException(status_code=403, detail="Tu rol no tiene permiso para esta acción.")
        return user
    return dep


def _local_day(s):
    off_min = int(datetime.now(local_tz()).utcoffset().total_seconds() // 60)
    name = s.get_bind().dialect.name
    if name == "mysql":
        from sqlalchemy import text
        return func.date(func.timestampadd(text("MINUTE"), off_min, Message.created_at))
    if name == "sqlite":
        return func.date(Message.created_at, f"{off_min:+d} minutes")
    from sqlalchemy import text
    return func.date(Message.created_at + text(f"interval '{off_min} minutes'"))


def _status(pct: float | None) -> str:
    if pct is None:
        return "unlimited"
    if pct >= 100:
        return "over"
    if pct >= settings.limit_warn_pct:
        return "warn"
    return "ok"


@router.get("/admin/token-dashboard")
def token_dashboard(date_from: str | None = None, date_to: str | None = None,
                    user: CurrentUser = Depends(require("reports.read"))) -> dict:
    d0 = parse_local_date(date_from) or (local_day_start_utc() - timedelta(days=29))
    d1 = parse_local_date(date_to, end=True)
    flt = [Message.role == "assistant", Message.created_at >= d0]
    if d1:
        flt.append(Message.created_at < d1)
    day0, month0 = local_day_start_utc(), local_month_start_utc()
    with session_scope() as s:
        # ---- per user (period + today + month)
        rows = (s.query(Message.user_id, func.count(Message.id),
                        func.coalesce(func.sum(Message.input_tokens), 0),
                        func.coalesce(func.sum(Message.output_tokens), 0),
                        func.coalesce(func.sum(Message.cost_usd), 0),
                        func.sum(case((Message.route == "agent", 1), else_=0)),
                        func.max(Message.created_at))
                .filter(*flt).group_by(Message.user_id).all())
        period = {r[0]: r for r in rows}
        now_rows = (s.query(Message.user_id,
                            func.coalesce(func.sum(case((Message.created_at >= day0, TOKENS), else_=0)), 0),
                            func.coalesce(func.sum(case((Message.created_at >= month0, TOKENS), else_=0)), 0))
                    .filter(Message.role == "assistant", Message.created_at >= min(day0, month0))
                    .group_by(Message.user_id).all())
        current = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in now_rows}
        users = s.query(User).all()
        roles = {r.id: r for r in s.query(Role).all()}
        by_user = []
        for u in users:
            if not u.is_active and u.id not in period:
                continue
            r = roles.get(u.role_id)
            p = period.get(u.id)
            tin, tout = (int(p[2]), int(p[3])) if p else (0, 0)
            today, month = current.get(u.id, (0, 0))
            dl = effective_limit(u.daily_token_limit, r.daily_token_limit if r else None)
            ml = effective_limit(u.monthly_token_limit, r.monthly_token_limit if r else None)
            pd, pm = _pct(today, dl), _pct(month, ml)
            worst = max([x for x in (pd, pm) if x is not None], default=None)
            by_user.append({
                "user_id": u.id, "username": u.username, "full_name": u.full_name,
                "role": r.name if r else "", "role_id": u.role_id, "is_active": u.is_active,
                "questions": int(p[1]) if p else 0, "input_tokens": tin, "output_tokens": tout,
                "tokens": tin + tout, "cost_usd": round(float(p[4]), 6) if p else 0.0,
                "agent_answers": int(p[5] or 0) if p else 0, "last_at": iso(p[6]) if p else None,
                "tokens_today": today, "tokens_month": month,
                "daily_token_limit": dl, "monthly_token_limit": ml,
                "limit_source": {"daily": "user" if u.daily_token_limit is not None else "role",
                                 "monthly": "user" if u.monthly_token_limit is not None else "role"},
                "pct_day": pd, "pct_month": pm, "status": _status(worst)})
        by_user.sort(key=lambda x: (x["tokens"], x["tokens_month"]), reverse=True)

        # ---- per day
        day = _local_day(s)
        daily = [{"date": str(d), "questions": int(n), "input_tokens": int(i or 0),
                  "output_tokens": int(o or 0), "cost_usd": round(float(c or 0), 6)}
                 for d, n, i, o, c in
                 s.query(day, func.count(Message.id), func.sum(Message.input_tokens),
                         func.sum(Message.output_tokens), func.sum(Message.cost_usd))
                 .filter(*flt).group_by(day).order_by(day).all()]

        # ---- per route
        by_route = [{"route": rt or "error", "questions": int(n), "tokens": int(t or 0),
                     "cost_usd": round(float(c or 0), 6)}
                    for rt, n, t, c in
                    s.query(Message.route, func.count(Message.id), func.sum(TOKENS),
                            func.sum(Message.cost_usd))
                    .filter(*flt).group_by(Message.route).all()]
        by_route.sort(key=lambda x: x["tokens"], reverse=True)

        # ---- per prompt profile
        names = {p.id: p.name for p in s.query(PromptProfile).all()}
        by_prompt = [{"prompt_id": pid, "name": names.get(pid, "Sin perfil") if pid else "Sin perfil",
                      "questions": int(n), "tokens": int(t or 0)}
                     for pid, n, t in
                     s.query(Message.prompt_profile_id, func.count(Message.id), func.sum(TOKENS))
                     .filter(*flt).group_by(Message.prompt_profile_id).all()]
        by_prompt.sort(key=lambda x: x["tokens"], reverse=True)

        # ---- per knowledge base (a question that searched 2 bases counts in both)
        kb_names = {k.slug: k.name for k in s.query(KnowledgeBase).all()}
        kb_acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        last_id = 0
        while True:
            batch = (s.query(Message.id, Message.kb_slugs, TOKENS)
                     .filter(*flt, Message.id > last_id).order_by(Message.id).limit(5000).all())
            if not batch:
                break
            for mid, slugs, tok in batch:
                for slug in (slugs or ["general"]):
                    kb_acc[slug][0] += 1
                    kb_acc[slug][1] += int(tok or 0)
            last_id = batch[-1][0]
        by_kb = [{"slug": k, "name": kb_names.get(k, k), "questions": v[0], "tokens": v[1]}
                 for k, v in kb_acc.items()]
        by_kb.sort(key=lambda x: x["tokens"], reverse=True)

    # ---- per role (current role of each user)
    acc: dict[str, dict] = {}
    for u in by_user:
        a = acc.setdefault(u["role"], {"role": u["role"], "role_id": u["role_id"], "users": 0,
                                       "active_users": 0, "questions": 0, "tokens": 0,
                                       "cost_usd": 0.0, "tokens_month": 0})
        a["users"] += 1
        a["active_users"] += 1 if u["questions"] else 0
        a["questions"] += u["questions"]
        a["tokens"] += u["tokens"]
        a["tokens_month"] += u["tokens_month"]
        a["cost_usd"] = round(a["cost_usd"] + u["cost_usd"], 6)
    for a in acc.values():
        r = roles.get(a["role_id"])
        a["daily_token_limit"] = r.daily_token_limit if r else None
        a["monthly_token_limit"] = r.monthly_token_limit if r else None
    by_role = sorted(acc.values(), key=lambda x: x["tokens"], reverse=True)

    g = global_limits()
    g_day, g_month = global_tokens(fresh=True)
    tin = sum(u["input_tokens"] for u in by_user)
    tout = sum(u["output_tokens"] for u in by_user)
    totals = {"questions": sum(u["questions"] for u in by_user), "input_tokens": tin,
              "output_tokens": tout, "tokens": tin + tout,
              "cost_usd": round(sum(u["cost_usd"] for u in by_user), 6),
              "active_users": sum(1 for u in by_user if u["questions"]),
              "agent_answers": sum(u["agent_answers"] for u in by_user),
              "users_warn": sum(1 for u in by_user if u["status"] == "warn"),
              "users_over": sum(1 for u in by_user if u["status"] == "over")}
    totals["avg_tokens_per_question"] = round(totals["tokens"] / totals["questions"]) \
        if totals["questions"] else 0
    return {"period": {"from": iso(d0), "to": iso(d1)}, "totals": totals,
            "global": {**g, "tokens_today": g_day, "tokens_month": g_month,
                       "pct_day": _pct(g_day, g["daily_token_limit"]),
                       "pct_month": _pct(g_month, g["monthly_token_limit"]),
                       "warn_pct": settings.limit_warn_pct, "resets_on": next_month_label()},
            "pricing": {"in_per_mtok": settings.price_in_per_mtok,
                        "out_per_mtok": settings.price_out_per_mtok,
                        "model": settings.llm_model},
            "daily": daily, "by_user": by_user, "by_role": by_role, "by_kb": by_kb,
            "by_route": by_route, "by_prompt": by_prompt}


# ---------------------------------------------------------------------- limits
class GlobalLimits(BaseModel):
    monthly_token_limit: int | None = Field(None, ge=0, le=10**12)
    daily_token_limit: int | None = Field(None, ge=0, le=10**12)


class LimitPatch(BaseModel):
    """Field absent = unchanged. On users: null = inherit from role, 0 = unlimited.
    On roles: null or 0 = unlimited."""
    daily_query_limit: int | None = Field(None, ge=0, le=100000)
    daily_token_limit: int | None = Field(None, ge=0, le=10**12)
    monthly_token_limit: int | None = Field(None, ge=0, le=10**12)


@router.get("/admin/limits")
def get_limits(user: CurrentUser = Depends(require_any("limits.manage", "reports.read"))) -> dict:
    with session_scope() as s:
        roles = [{"id": r.id, "name": r.name, "daily_query_limit": r.daily_query_limit,
                  "daily_token_limit": r.daily_token_limit,
                  "monthly_token_limit": r.monthly_token_limit}
                 for r in s.query(Role).order_by(Role.id)]
        users = [{"id": u.id, "username": u.username, "full_name": u.full_name,
                  "role_id": u.role_id, "role": u.role.name, "is_active": u.is_active,
                  "daily_query_limit": u.daily_query_limit,
                  "daily_token_limit": u.daily_token_limit,
                  "monthly_token_limit": u.monthly_token_limit}
                 for u in s.query(User).order_by(User.username)]
    return {"global": global_limits(), "roles": roles, "users": users,
            "warn_pct": settings.limit_warn_pct, "can_edit": user.can("limits.manage")}


@router.put("/admin/limits")
def put_limits(body: GlobalLimits, user: CurrentUser = Depends(require("limits.manage"))) -> dict:
    v = {"monthly_token_limit": body.monthly_token_limit or None,
         "daily_token_limit": body.daily_token_limit or None}
    set_setting(GLOBAL_KEY, v)
    with _g_lock:
        _g_cache["at"] = 0.0
    audit("limits_updated", user=user, detail={"scope": "global", **v})
    return global_limits()


def _apply(obj: Any, body: LimitPatch, inherit_null: bool) -> dict:
    changes = {}
    for k in body.model_fields_set:
        v = getattr(body, k)
        if not inherit_null and v == 0:
            v = None                  # role: 0 = unlimited = NULL
        setattr(obj, k, v)
        changes[k] = v
    return changes


@router.patch("/admin/limits/users/{user_id}")
def patch_user_limits(user_id: int, body: LimitPatch,
                      user: CurrentUser = Depends(require("limits.manage"))) -> dict:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        ch = _apply(u, body, inherit_null=True)
        audit("limits_updated", user=user, session=s,
              detail={"scope": "user", "target": u.username, **ch})
        r = u.role
        return {"id": u.id, "daily_query_limit": u.daily_query_limit,
                "daily_token_limit": u.daily_token_limit,
                "monthly_token_limit": u.monthly_token_limit,
                "effective": {"daily_query_limit": effective_limit(u.daily_query_limit, r.daily_query_limit),
                              "daily_token_limit": effective_limit(u.daily_token_limit, r.daily_token_limit),
                              "monthly_token_limit": effective_limit(u.monthly_token_limit, r.monthly_token_limit)}}


@router.patch("/admin/limits/roles/{role_id}")
def patch_role_limits(role_id: int, body: LimitPatch,
                      user: CurrentUser = Depends(require("limits.manage"))) -> dict:
    with session_scope() as s:
        r = s.get(Role, role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        ch = _apply(r, body, inherit_null=False)
        audit("limits_updated", user=user, session=s, detail={"scope": "role", "target": r.name, **ch})
        return {"id": r.id, "daily_query_limit": r.daily_query_limit,
                "daily_token_limit": r.daily_token_limit,
                "monthly_token_limit": r.monthly_token_limit}
