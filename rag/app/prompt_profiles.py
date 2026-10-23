"""Prompt profiles: instructions (persona, tone, format, focus) chosen per role.

A profile is appended to the system prompts of the fast path and of the agent
(app/prompts.with_instructions); it never replaces the grounding rules.

    role_ids []            -> every role may use it
    roles.default_prompt_id -> the profile used when the user has not picked one
    prompt_id 0 in /chat    -> no profile (plain assistant)

Routes
    GET    /prompts                         profiles I can use + my default
    GET    /admin/prompts                   [prompts.manage]
    POST   /admin/prompts                   [prompts.manage]
    PATCH  /admin/prompts/{id}              [prompts.manage]
    DELETE /admin/prompts/{id}              [prompts.manage]
    PUT    /admin/prompts/role-default      [prompts.manage] {role_id, prompt_id|null}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CurrentUser, audit, require
from app.db import session_scope
from app.models import PromptProfile, Role

SEED = [
    ("Técnico detallado", "Respuestas completas para soporte técnico",
     "Responde como ingeniero de soporte técnico. Da los datos exactos (presiones, medidas, "
     "códigos de artículo, posiciones del dibujo) en una lista o tabla, y al final indica los "
     "pasos o precauciones relevantes. Si hay varias opciones, compáralas."),
    ("Respuesta breve", "Una o dos frases, directo al dato",
     "Responde en máximo dos frases, directo al dato que se pregunta, sin introducciones. "
     "Mantén las citas entre corchetes."),
    ("Ventas y cotización", "Enfocado en artículos, existencias y precios",
     "Responde pensando en una cotización para un cliente: identifica el código de artículo, "
     "la descripción, la existencia y el precio cuando estén en las tablas; sugiere "
     "refacciones relacionadas si aparecen en los documentos. Tono cordial y profesional."),
]


def seed_profiles() -> None:
    with session_scope() as s:
        if s.query(PromptProfile).count():
            return
        for i, (name, desc, text) in enumerate(SEED):
            s.add(PromptProfile(name=name, description=desc, instructions=text, role_ids=[],
                                sort=i))


def profile_dict(p: PromptProfile, full: bool = True) -> dict:
    d = {"id": p.id, "name": p.name, "description": p.description,
         "role_ids": list(p.role_ids or []), "is_active": p.is_active, "sort": p.sort}
    if full:
        d["instructions"] = p.instructions
    return d


def _allowed(p: PromptProfile, user: CurrentUser) -> bool:
    return p.is_active and (not p.role_ids or user.role_id in (p.role_ids or []))


def available_profiles(user: CurrentUser) -> list[PromptProfile]:
    with session_scope() as s:
        rows = s.query(PromptProfile).order_by(PromptProfile.sort, PromptProfile.id).all()
        return [p for p in rows if _allowed(p, user)]


def resolve_prompt(user: CurrentUser, prompt_id: int | None) -> tuple[int | None, str]:
    """-> (profile id or None, instructions). Explicit id must be allowed for the role."""
    explicit = prompt_id is not None
    if prompt_id is None:
        prompt_id = user.preferences.get("prompt_id")
    if prompt_id is None:
        prompt_id = user.default_prompt_id
    if not prompt_id:
        return None, ""
    with session_scope() as s:
        p = s.get(PromptProfile, int(prompt_id))
        if p is not None and (_allowed(p, user) or (explicit and user.can("prompts.manage")
                                                    and p.is_active)):
            return p.id, p.instructions
    if explicit:
        raise HTTPException(status_code=403, detail="Ese prompt no está disponible para tu rol.")
    if user.default_prompt_id and user.default_prompt_id != prompt_id:
        return resolve_prompt(user, user.default_prompt_id)
    return None, ""


# ---------------------------------------------------------------------- routes
router = APIRouter()


@router.get("/prompts")
def my_prompts(user: CurrentUser = Depends(require("chat"))) -> dict:
    items = [profile_dict(p, full=False) for p in available_profiles(user)]
    selected = user.preferences.get("prompt_id")
    if selected is None:
        selected = user.default_prompt_id
    if selected and not any(i["id"] == selected for i in items):
        selected = user.default_prompt_id if any(
            i["id"] == user.default_prompt_id for i in items) else None
    return {"items": items, "role_default": user.default_prompt_id, "selected": selected or 0}


class PromptBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field("", max_length=300)
    instructions: str = Field(..., min_length=5, max_length=8000)
    role_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    sort: int = 0


class PromptPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=300)
    instructions: str | None = Field(None, min_length=5, max_length=8000)
    role_ids: list[int] | None = None
    is_active: bool | None = None
    sort: int | None = None


class RoleDefault(BaseModel):
    role_id: int
    prompt_id: int | None = None


def _roles_ok(s, ids: list[int]) -> list[int]:
    ids = sorted(set(int(i) for i in ids))
    if ids and {r for (r,) in s.query(Role.id).filter(Role.id.in_(ids))} != set(ids):
        raise HTTPException(status_code=400, detail="Algún rol no existe.")
    return ids


@router.get("/admin/prompts")
def admin_prompts(user: CurrentUser = Depends(require("prompts.manage"))) -> dict:
    with session_scope() as s:
        items = [profile_dict(p) for p in
                 s.query(PromptProfile).order_by(PromptProfile.sort, PromptProfile.id)]
        roles = [{"id": r.id, "name": r.name, "default_prompt_id": r.default_prompt_id}
                 for r in s.query(Role).order_by(Role.id)]
    return {"items": items, "roles": roles}


@router.post("/admin/prompts")
def admin_create_prompt(body: PromptBody,
                        user: CurrentUser = Depends(require("prompts.manage"))) -> dict:
    with session_scope() as s:
        p = PromptProfile(name=body.name.strip(), description=body.description.strip(),
                          instructions=body.instructions.strip(),
                          role_ids=_roles_ok(s, body.role_ids), is_active=body.is_active,
                          sort=body.sort)
        s.add(p)
        s.flush()
        audit("prompt_created", user=user, session=s, detail={"id": p.id, "name": p.name})
        return profile_dict(p)


@router.patch("/admin/prompts/{prompt_id}")
def admin_update_prompt(prompt_id: int, body: PromptPatch,
                        user: CurrentUser = Depends(require("prompts.manage"))) -> dict:
    with session_scope() as s:
        p = s.get(PromptProfile, prompt_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Prompt no encontrado.")
        ch = body.model_dump(exclude_none=True)
        for k in ("name", "description", "instructions"):
            if k in ch:
                setattr(p, k, ch[k].strip())
        if "role_ids" in ch:
            p.role_ids = _roles_ok(s, body.role_ids)
        if "is_active" in ch:
            p.is_active = body.is_active
        if "sort" in ch:
            p.sort = body.sort
        audit("prompt_updated", user=user, session=s,
              detail={"id": p.id, **{k: v for k, v in ch.items() if k != "instructions"},
                      **({"instructions": "(editado)"} if "instructions" in ch else {})})
        return profile_dict(p)


@router.delete("/admin/prompts/{prompt_id}")
def admin_delete_prompt(prompt_id: int,
                        user: CurrentUser = Depends(require("prompts.manage"))) -> dict:
    with session_scope() as s:
        p = s.get(PromptProfile, prompt_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Prompt no encontrado.")
        for r in s.query(Role).filter(Role.default_prompt_id == prompt_id):
            r.default_prompt_id = None
        s.delete(p)
        audit("prompt_deleted", user=user, session=s, detail={"id": prompt_id, "name": p.name})
    return {"ok": True}


@router.put("/admin/prompts/role-default")
def admin_role_default(body: RoleDefault,
                       user: CurrentUser = Depends(require("prompts.manage"))) -> dict:
    with session_scope() as s:
        r = s.get(Role, body.role_id)
        if r is None:
            raise HTTPException(status_code=404, detail="Rol no encontrado.")
        if body.prompt_id:
            p = s.get(PromptProfile, body.prompt_id)
            if p is None:
                raise HTTPException(status_code=404, detail="Prompt no encontrado.")
            if p.role_ids and r.id not in p.role_ids:
                p.role_ids = sorted(set(p.role_ids) | {r.id})   # make it usable by the role
        r.default_prompt_id = body.prompt_id or None
        audit("prompt_role_default", user=user, session=s,
              detail={"role": r.name, "prompt_id": r.default_prompt_id})
    return {"ok": True, "role_id": body.role_id, "prompt_id": body.prompt_id or None}
