"""Per-user conversation log.

Every question and every answer (or error) is a row in `messages`, grouped in
`conversations` owned by one user. The chat endpoint uses three short transactions:

    begin_turn()    check quota + ownership, store the question       (commit)
    -- RAG / LLM runs here, with NO database connection held --
    finish_turn()   store the answer with route, sources, tokens, cost (commit)
    fail_turn()     or store the error, so failed questions are in the log too

Routes (own conversations; `conversations.read_all` may open anybody's):
    GET    /conversations?q=&limit=&offset=
    GET    /conversations/{id}
    PATCH  /conversations/{id}   {title}
    DELETE /conversations/{id}   (soft delete: hidden for the user, kept for audit)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.auth import CurrentUser, audit, require
from app.db import session_scope, utcnow
from app.models import Conversation, Message

TITLE_MAX = 80
CLARIFY_JOIN = "\nAclaración del usuario: "


# ---------------------------------------------------------------------------- time helpers
def local_tz():
    return datetime.now().astimezone().tzinfo


def local_day_start_utc(day: datetime | None = None) -> datetime:
    """Midnight of the server's local day, as naive UTC (quota resets at local midnight)."""
    now_local = (day or datetime.now(timezone.utc)).astimezone(local_tz())
    start = datetime.combine(now_local.date(), dtime.min, tzinfo=local_tz())
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def parse_local_date(s: str | None, end: bool = False) -> datetime | None:
    """'2026-10-05' (local) -> naive UTC start of that day (or of the next day if end)."""
    if not s:
        return None
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Fecha inválida: {s} (usa AAAA-MM-DD)")
    if end:
        d = d + timedelta(days=1)
    return (datetime.combine(d, dtime.min, tzinfo=local_tz())
            .astimezone(timezone.utc).replace(tzinfo=None))


def iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


# ---------------------------------------------------------------------------- serializers
def conversation_dict(c: Conversation, with_user: bool = False) -> dict:
    d = {"id": c.id, "title": c.title, "message_count": c.message_count,
         "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
         "deleted": c.deleted_at is not None}
    if with_user:
        d["user"] = {"id": c.user.id, "username": c.user.username,
                     "full_name": c.user.full_name}
    return d


def message_dict(m: Message, include_transcript: bool = False) -> dict:
    if m.role == "user":
        return {"id": m.id, "role": "user", "content": m.content,
                "is_clarify_reply": bool(m.effective_query and m.effective_query != m.content),
                "created_at": iso(m.created_at)}
    return {"id": m.id, "role": "assistant", "status": m.status, "answer": m.content,
            "error": m.error, "route": m.route, "escalation": m.escalation,
            "clarify": m.clarify, "deep_search": m.deep_search,
            "sources": m.sources or [], "drawings": m.drawings or [], "usage": m.usage or {},
            "timings_ms": m.timings or {}, "agent_steps": m.agent_steps,
            "transcript": (m.transcript if include_transcript else None),
            "kb_slugs": m.kb_slugs or [], "prompt_profile_id": m.prompt_profile_id,
            "payload": m.payload,
            "created_at": iso(m.created_at)}


# ---------------------------------------------------------------------------- chat turn
@dataclass
class Turn:
    conversation_id: int
    user_message_id: int
    effective_query: str
    title: str
    created: bool


def _questions_today(s, user_id: int) -> int:
    return (s.query(func.count(Message.id))
            .filter(Message.user_id == user_id, Message.role == "user",
                    Message.created_at >= local_day_start_utc()).scalar() or 0)


def begin_turn(user: CurrentUser, conversation_id: int | None, text: str,
               deep: bool) -> Turn:
    from app.usage import check_limits          # questions/day + token limits (429)
    text = text.strip()
    now = utcnow()
    with session_scope() as s:
        check_limits(s, user)
        created = False
        if conversation_id:
            conv = s.get(Conversation, conversation_id, with_for_update=True)
            if conv is None or conv.user_id != user.id or conv.deleted_at is not None:
                raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        else:
            conv = Conversation(user_id=user.id, title=_title(text), created_at=now,
                                updated_at=now)
            s.add(conv)
            s.flush()
            created = True

        # answering a clarification? -> resend the original question with the answer
        effective = text
        last = (s.query(Message).filter(Message.conversation_id == conv.id)
                .order_by(Message.id.desc()).first())
        if last is not None and last.role == "assistant" and last.clarify:
            prev_q = (s.query(Message)
                      .filter(Message.conversation_id == conv.id, Message.role == "user",
                              Message.id < last.id)
                      .order_by(Message.id.desc()).first())
            if prev_q is not None:
                original = (prev_q.effective_query or prev_q.content).split(CLARIFY_JOIN)[0]
                effective = f"{original}{CLARIFY_JOIN}{text}"

        msg = Message(conversation_id=conv.id, user_id=user.id, role="user", content=text,
                      effective_query=effective, deep_search=deep, created_at=now)
        s.add(msg)
        conv.message_count += 1
        conv.updated_at = now
        s.flush()
        return Turn(conv.id, msg.id, effective, conv.title, created)


def finish_turn(user: CurrentUser, turn: Turn, result: dict, deep: bool,
                keep_transcript: bool, kb_slugs: list[str] | None = None,
                prompt_id: int | None = None) -> int:
    u = result.get("usage") or {}
    t = result.get("timings_ms") or {}
    now = utcnow()
    with session_scope() as s:
        m = Message(conversation_id=turn.conversation_id, user_id=user.id, role="assistant",
                    content=result.get("answer") or "", status="ok",
                    route=result.get("route") or "", escalation=result.get("escalation") or "",
                    clarify=bool(result.get("clarify")), deep_search=deep,
                    sources=result.get("sources") or [], drawings=result.get("drawings") or [],
                    usage=u, timings=t,
                    transcript=result.get("transcript") if keep_transcript else None,
                    agent_steps=int(result.get("agent_steps") or 0),
                    llm_calls=int(u.get("llm_calls") or 0),
                    input_tokens=int(u.get("input_tokens") or 0),
                    output_tokens=int(u.get("output_tokens") or 0),
                    cost_usd=Decimal(str(u.get("est_cost_usd") or 0)),
                    latency_ms=int(t.get("total") or 0), kb_slugs=kb_slugs or None,
                    prompt_profile_id=prompt_id, payload=result.get("payload"),
                    standalone_query=result.get("standalone_query"),
                    created_at=now)
        s.add(m)
        _bump(s, turn.conversation_id, now)
        s.flush()
        return m.id


def recent_history(conversation_id: int, before_message_id: int,
                   turns: int = 3) -> list[dict]:
    """Production 1.0: the last `turns` question/answer pairs before this question, oldest
    first, as [{"role": "user"|"assistant", "content": ...}]. A previous follow-up is shown
    in its rewritten standalone form, so chains of follow-ups keep their subject. Failed
    answers are skipped."""
    if turns <= 0:
        return []
    with session_scope() as s:
        rows = (s.query(Message.role, Message.content, Message.effective_query, Message.status,
                        Message.standalone_query)
                .filter(Message.conversation_id == conversation_id, Message.id < before_message_id)
                .order_by(Message.id.desc()).limit(turns * 2 + 2).all())
    out: list[dict] = []
    pending_standalone = None
    for role, content, effective, status, standalone in rows:      # newest first
        if role == "assistant":
            if status != "ok":
                pending_standalone = None
                continue
            pending_standalone = standalone
            out.append({"role": "assistant", "content": content or ""})
        else:
            text = pending_standalone or (effective or content or "")
            out.append({"role": "user", "content": text.replace(CLARIFY_JOIN, " — ")})
            pending_standalone = None
    out.reverse()
    while out and out[0]["role"] == "assistant":                  # start with a question
        out.pop(0)
    return out[-turns * 2:]


def fail_turn(user: CurrentUser, turn: Turn, error: str, deep: bool, latency_ms: int,
              kb_slugs: list[str] | None = None, prompt_id: int | None = None) -> int:
    now = utcnow()
    with session_scope() as s:
        m = Message(conversation_id=turn.conversation_id, user_id=user.id, role="assistant",
                    content="", status="error", error=error[:4000], deep_search=deep,
                    latency_ms=latency_ms, kb_slugs=kb_slugs or None,
                    prompt_profile_id=prompt_id, created_at=now)
        s.add(m)
        _bump(s, turn.conversation_id, now)
        s.flush()
        return m.id


def _bump(s, conversation_id: int, now: datetime) -> None:
    conv = s.get(Conversation, conversation_id)
    if conv is not None:
        conv.message_count += 1
        conv.updated_at = now


def _title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1].rstrip() + "…"


# ---------------------------------------------------------------------------- routes
router = APIRouter()


class ConversationPatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@router.get("/conversations")
def list_conversations(q: str = "", limit: int = Query(50, ge=1, le=200),
                       offset: int = Query(0, ge=0),
                       user: CurrentUser = Depends(require("chat"))) -> dict:
    with session_scope() as s:
        base = s.query(Conversation).filter(Conversation.user_id == user.id,
                                            Conversation.deleted_at.is_(None))
        if q.strip():
            base = base.filter(Conversation.title.like(f"%{q.strip()}%"))
        total = base.count()
        rows = (base.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .offset(offset).limit(limit).all())
        return {"total": total, "items": [conversation_dict(c) for c in rows]}


def load_conversation(conversation_id: int, user: CurrentUser) -> dict:
    with session_scope() as s:
        c = s.get(Conversation, conversation_id)
        own = c is not None and c.user_id == user.id and c.deleted_at is None
        if c is None or not (own or user.can("conversations.read_all")):
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        msgs = (s.query(Message).filter(Message.conversation_id == c.id)
                .order_by(Message.id).all())
        show_steps = user.can("chat.debug")
        return {"conversation": conversation_dict(c, with_user=not own),
                "read_only": not own,
                "messages": [message_dict(m, include_transcript=show_steps) for m in msgs]}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int,
                     user: CurrentUser = Depends(require("chat"))) -> dict:
    return load_conversation(conversation_id, user)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: int, body: ConversationPatch,
                        user: CurrentUser = Depends(require("chat"))) -> dict:
    with session_scope() as s:
        c = s.get(Conversation, conversation_id)
        if c is None or c.user_id != user.id or c.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        c.title = body.title.strip()[:200]
        return conversation_dict(c)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int,
                        user: CurrentUser = Depends(require("chat"))) -> dict:
    with session_scope() as s:
        c = s.get(Conversation, conversation_id)
        if c is None or c.user_id != user.id or c.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        c.deleted_at = utcnow()
        audit("conversation_deleted", user=user, detail={"conversation_id": c.id}, session=s)
    return {"ok": True}


def as_jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v
