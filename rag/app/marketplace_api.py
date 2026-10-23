"""API pública del asistente del marketplace (la tienda Vue la llama directo desde el navegador).

    GET  /marketplace/health                 catálogo + bases que consulta (sin rutas)
    GET  /marketplace/products?q=&limit=&in_stock=&category=
                                             búsqueda en el catálogo de la tienda (sin LLM)
    GET  /marketplace/products/{sku}         un producto
    POST /marketplace/chat                   {message, conversation_id?, conversation_token?,
                                              context?: {viewing_sku, cart_skus, page}}
                                             -> {answer, products, sources, drawings,
                                                 downloads, conversation_id,
                                                 conversation_token, ...}
    GET  /marketplace/files/{rel}            PDF / dibujo / foto citado en una respuesta
                                             (sólo secciones públicas, nunca tablas)
    GET  /marketplace/datasheet-pdf/{part}   hoja técnica PDF generada (sin precios/costos)
    POST /marketplace/catalog/refresh        recarga el catálogo [X-Admin-Token o index.manage]

Seguridad
* Los clientes de la tienda no tienen usuario en el RAG: todo corre con una CUENTA DE
  SERVICIO (MARKETPLACE_SERVICE_USER, rol MARKETPLACE_ROLE) que se crea sola con una
  contraseña aleatoria que nadie conoce. Sus límites de preguntas / tokens y las bases de
  conocimiento que puede leer se administran en /admin como las de cualquier rol.
* Cada conversación se devuelve con un conversation_token (HMAC): sin él no se puede
  continuar ni leer la conversación de otro visitante aunque se adivine el id.
* Límite de preguntas por minuto por IP (MARKETPLACE_RATE_PER_MIN).
* Al público sólo salen archivos de MARKETPLACE_PUBLIC_SECTIONS (hojas técnicas, manuales,
  catálogos, dibujos, fotos); tablas, listas de precios, invoices y "otros" nunca.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.auth import CurrentUser, _to_current, audit, client_ip, optional_user
from app.config import TABLE_EXTS, settings
from app.db import session_scope
from app.llm import UsageMeter
from app.marketplace import (MarketplaceAdvisor, ShopperContext, get_catalog, mentioned_skus,
                             merge_usage)

log = logging.getLogger("rag.marketplace.api")
router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# ============================================================================ service user
_svc_lock = threading.Lock()
_svc_user_id: int | None = None


def ensure_service_account(registry: Any = None) -> int:
    """Rol + usuario de servicio (idempotente). Devuelve el id del usuario."""
    global _svc_user_id
    if _svc_user_id is not None:
        return _svc_user_id
    from app.kb import ensure_role_kb
    from app.models import Role, User
    from app.security import hash_password
    with _svc_lock:
        if _svc_user_id is not None:
            return _svc_user_id
        uname = settings.marketplace_service_user.strip().lower() or "marketplace"
        rname = settings.marketplace_role.strip() or "marketplace"
        created = False
        with session_scope() as s:
            role = s.query(Role).filter_by(name=rname).first()
            if role is None:
                role = Role(name=rname, description="Asistente público del marketplace "
                            "(clientes de la tienda en línea). Dale sólo bases públicas.",
                            permissions=["chat", "documents.read"],
                            daily_query_limit=settings.marketplace_daily_query_limit or None,
                            is_system=False)
                s.add(role)
                s.flush()
                ensure_role_kb(s, role)
                created = True
            user = s.query(User).filter_by(username=uname).first()
            if user is None:
                user = User(username=uname, full_name="Marketplace (clientes de la tienda)",
                            password_hash=hash_password(secrets.token_urlsafe(32)),
                            role_id=role.id, preferences={}, must_change_password=False)
                s.add(user)
                s.flush()
                created = True
            _svc_user_id = user.id
        if created:
            log.warning("marketplace: cuenta de servicio '%s' (rol '%s') creada", uname, rname)
            if registry is not None:
                try:
                    registry.refresh()
                except Exception:
                    log.exception("kb registry refresh failed")
        return _svc_user_id


def service_user(request: Request) -> CurrentUser:
    from app.models import User
    global _svc_user_id
    uname = settings.marketplace_service_user.strip().lower() or "marketplace"
    uid = ensure_service_account(getattr(request.app.state, "kb", None))
    with session_scope() as s:
        u = s.get(User, uid)
        if u is None or u.username != uname:        # BD recreada / usuario borrado
            _svc_user_id = None
            u = s.get(User, ensure_service_account(getattr(request.app.state, "kb", None)))
        if u is None or not u.is_active:
            raise HTTPException(status_code=503, detail="El asistente de la tienda está "
                                "deshabilitado por el administrador.")
        cu = _to_current(u, None, client_ip(request))
    if not cu.can("chat"):
        raise HTTPException(status_code=503, detail="El rol del marketplace no tiene permiso "
                            "de chat.")
    return cu


# ============================================================================ helpers
_secret = (settings.marketplace_secret
           or (hashlib.sha256(f"mp:{settings.admin_token}".encode()).hexdigest()
               if settings.admin_token else secrets.token_hex(32)))


def conversation_token(conversation_id: int) -> str:
    return hmac.new(_secret.encode(), f"mp-conv:{conversation_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def _valid_conversation(cid: int | None, token: str | None) -> int | None:
    if not cid or not token:
        return None
    return cid if hmac.compare_digest(conversation_token(cid), token) else None


_rate: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def check_rate(ip: str) -> None:
    limit = settings.marketplace_rate_per_min
    if limit <= 0:
        return
    now = time.time()
    with _rate_lock:
        q = _rate[ip or "?"]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail="Demasiadas preguntas seguidas. "
                                "Espera un momento e inténtalo de nuevo.")
        q.append(now)
        if len(_rate) > 5000:                       # limpia IPs viejas
            for k in [k for k, v in _rate.items() if not v or now - v[-1] > 60]:
                _rate.pop(k, None)


def _public_sections() -> set[str]:
    return {x.strip() for x in settings.marketplace_public_sections.split(",") if x.strip()}


def _is_public_entry(entry: dict | None) -> bool:
    if not entry:
        return False
    if str(entry.get("ext") or Path(entry.get("path", "")).suffix).lower() in TABLE_EXTS:
        return False
    return entry.get("section") in _public_sections()


def _selection(request: Request, user: CurrentUser) -> tuple[list[str], Any]:
    reg = request.app.state.kb
    wanted = [x.strip() for x in settings.marketplace_kbs.split(",") if x.strip()] or None
    slugs = reg.resolve_selection(user, wanted)
    return slugs, reg.combined(slugs)


def _ds_url(part: str, kb: str = "") -> str:
    return (f"/marketplace/datasheet-pdf/{quote(part, safe='')}?inline=true"
            + (f"&kb={quote(kb, safe=',')}" if kb else ""))


_DS_RE = re.compile(r"^/datasheet(?:-pdf)?/([^?]+)(?:\?(.*))?$")


def _remap_url(url: str, catalog: dict) -> str:
    """URL interna del RAG -> URL pública de la tienda ('' = no se publica)."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url                                       # páginas web de fabricantes
    m = _DS_RE.match(url)
    if m:
        kb = ""
        for kv in (m.group(2) or "").split("&"):
            if kv.startswith("kb="):
                kb = unquote(kv[3:])
        return _ds_url(unquote(m.group(1)), kb)
    if url.startswith("/files/"):
        rel = unquote(url[len("/files/"):])
        if _is_public_entry(catalog.get(rel)):
            return "/marketplace/files/" + quote(rel)
    return ""


_LINK_RE = re.compile(r"\[([^\]]+)\]\((/datasheet-pdf/[^)\s]+)\)")


def _public_rag_parts(rag: dict, retriever: Any) -> tuple[list, list, list]:
    catalog = retriever.snapshot.catalog
    sources = []
    for s in rag.get("sources") or []:
        if s.get("kind") == "marketplace":
            continue
        url = _remap_url(s.get("url") or "", catalog)
        if not url:
            continue
        sources.append({"id": s.get("id"), "title": Path(str(s.get("source") or "")).name
                        or s.get("source"), "kind": s.get("kind"), "url": url,
                        "cited": bool(s.get("cited"))})
    drawings = []
    for d in rag.get("drawings") or []:
        url = _remap_url(d.get("url") or "", catalog)
        if url:
            drawings.append({"id": d.get("id"), "title": Path(d.get("source", "")).stem,
                             "url": url})
    downloads, seen = [], set()
    for label, url in _LINK_RE.findall(rag.get("answer") or ""):
        pub = _remap_url(url, catalog)
        if pub and pub not in seen:
            seen.add(pub)
            downloads.append({"label": label.replace("📄", "").strip(), "url": pub})
    return sources[:8], drawings[:4], downloads[:3]


def _technical_notes(rag: dict) -> str:
    ans = (rag.get("answer") or "").strip()
    ans = re.sub(r"\n\*\*Descargas\*\*\n(- .*\n?)+", "", ans).strip()   # links: aparte
    if not ans or re.search(r"IN\w{0,3}SUFFICIENT[_ ]CONTEXT", ans, re.I):
        return ""
    if rag.get("clarify"):
        return f"(The document assistant needs more detail and asked: {ans})"
    return ans


# ============================================================================ schemas
class ShopperContextIn(BaseModel):
    viewing_sku: str = Field("", max_length=80)
    cart_skus: list[str] = Field(default_factory=list, max_length=30)
    page: str = Field("", max_length=120)


class MarketChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1500)
    conversation_id: int | None = None
    conversation_token: str | None = Field(None, max_length=100)
    context: ShopperContextIn | None = None


# ============================================================================ routes
@router.get("/health")
def health(request: Request) -> dict:
    cat = get_catalog()
    if cat.enabled:
        cat.ensure()
    out = {"ok": cat.enabled and bool(cat.products), "catalog": cat.status(),
           "rag": {"enabled": settings.marketplace_use_rag}}
    if settings.marketplace_use_rag:
        try:
            user = service_user(request)
            slugs, rtr = _selection(request, user)
            st = rtr.stats()
            out["rag"].update(kbs=slugs, files=st.get("files", 0), chunks=st.get("chunks", 0))
        except HTTPException as e:
            out["rag"].update(error=str(e.detail))
        except Exception as e:                       # BD caída, etc.
            out["rag"].update(error=type(e).__name__)
    return out


@router.get("/products")
def products(q: str = Query("", max_length=300), limit: int = Query(12, ge=1, le=50),
             in_stock: bool = False, category: str = Query("", max_length=80)) -> dict:
    cat = get_catalog()
    if not cat.enabled:
        raise HTTPException(status_code=503, detail="Catálogo del marketplace no configurado.")
    if q.strip():
        found = cat.search(q, limit=limit, in_stock_only=in_stock, category=category)
        items = [dict(p.public(), score=sc) for sc, p in found]
    else:
        cat.ensure()
        items = [p.public() for p in cat.products
                 if (not in_stock or p.in_stock)
                 and (not category or p.category.lower() == category.lower())][:limit]
    return {"total": len(items), "items": items, "catalog": cat.status()}


@router.get("/products/{sku:path}")
def product(sku: str) -> dict:
    p = get_catalog().get(sku)
    if p is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado en la tienda.")
    return p.public()


@router.post("/chat")
def chat(req: MarketChatRequest, request: Request) -> dict:
    from app import conversations
    from app.conversation_context import contextualize
    from app.errors import api_error_message, is_fatal_api_error
    from app.prompt_profiles import resolve_prompt
    from app.prompts import MARKETPLACE_RAG_INSTRUCTIONS

    cat = get_catalog()
    if not cat.enabled and not settings.marketplace_use_rag:
        raise HTTPException(status_code=503, detail="El asistente de la tienda no está configurado.")
    check_rate(client_ip(request))
    user = service_user(request)
    st = request.app.state
    t0 = time.perf_counter()

    conv_id = _valid_conversation(req.conversation_id, req.conversation_token)
    turn = conversations.begin_turn(user, conv_id, req.message, False)   # límites (429)
    history = [] if turn.created else conversations.recent_history(
        turn.conversation_id, turn.user_message_id, settings.conversation_history_turns)
    c = req.context or ShopperContextIn()
    shopper = ShopperContext(viewing_sku=c.viewing_sku.strip(),
                             cart_skus=[x.strip() for x in c.cart_skus if x.strip()],
                             page=c.page.strip())
    meter = UsageMeter()
    router_obj = st.router
    agent_llm = getattr(router_obj.agent, "llm", None)

    # 1) pregunta autónoma (un seguimiento "¿y en 1/4?" lleva el producto anterior)
    standalone, method = contextualize(turn.effective_query, history, agent_llm, meter)

    # 2) candidatos del catálogo de la tienda (local)
    advisor = MarketplaceAdvisor(cat, router_obj.llm, router_obj)
    found = advisor.candidates(standalone, shopper) if cat.enabled else []

    # 3) respuesta técnica del RAG (hojas técnicas, manuales, dibujos)
    rag: dict = {}
    slugs: list[str] = []
    sources: list = []
    drawings: list = []
    downloads: list = []
    if settings.marketplace_use_rag:
        try:
            slugs, rtr = _selection(request, user)
            if rtr.stats().get("chunks", 0) > 0:
                prompt_id, instr = resolve_prompt(user, None)
                instr = "\n\n".join(x for x in (instr, MARKETPLACE_RAG_INSTRUCTIONS) if x)
                q = standalone
                if shopper.viewing_sku and shopper.viewing_sku.lower() not in q.lower():
                    p = cat.get(shopper.viewing_sku) if cat.enabled else None
                    if p is not None:
                        q = f"{q} (producto que está viendo: {p.name} {p.sku})"
                rag = router_obj.answer(q, retriever=rtr, instructions=instr,
                                        prompt_id=prompt_id, kb_slugs=slugs).to_dict()
                sources, drawings, downloads = _public_rag_parts(rag, rtr)
        except HTTPException as e:
            log.info("marketplace: sin RAG para esta pregunta (%s)", e.detail)
        except Exception as e:
            if is_fatal_api_error(e):
                log.error("marketplace: RAG sin LLM: %s", e)
            else:
                log.exception("marketplace: RAG failed")
    technical = _technical_notes(rag)

    # 4) asesor: requerimiento + datos técnicos + catálogo -> recomendación
    route = "marketplace"
    try:
        answer = advisor.advise(standalone, technical, shopper, found, history, meter)
    except Exception as e:
        log.warning("marketplace advisor failed: %s", e)
        route = "marketplace-fallback"
        answer = advisor.fallback_answer(found, technical)
        if not found and not technical:
            msg = api_error_message(e) if is_fatal_api_error(e) else str(e)
            conversations.fail_turn(user, turn, f"503: {msg}"[:4000], False,
                                    int((time.perf_counter() - t0) * 1000), kb_slugs=slugs)
            raise HTTPException(status_code=503, detail="El asistente no está disponible en "
                                "este momento. Intenta de nuevo en unos minutos.")

    recommended = mentioned_skus(answer, cat) if cat.enabled else []
    match = "recommended"
    if not recommended and found and found[0][0] >= 6:
        recommended, match = [p for _, p in found[:3]], "related"
    cards = [dict(p.public(), match=match)
             for p in recommended[:settings.marketplace_max_products]]

    usage = merge_usage(rag.get("usage") or {}, meter.as_dict())
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {"answer": answer, "route": route, "escalation": rag.get("route", ""),
           "clarify": False, "sources": sources, "drawings": drawings,
           "agent_steps": int(rag.get("agent_steps") or 0), "usage": usage,
           "timings_ms": {"total": total_ms}, "standalone_query": standalone
           if method else None,
           "payload": {"type": "marketplace", "products": [p["sku"] for p in cards],
                       "match": match, "context": shopper.__dict__,
                       "candidates": [p.sku for _, p in found[:10]]}}
    msg_id = conversations.finish_turn(user, turn, res, False, keep_transcript=False,
                                       kb_slugs=slugs or None)
    return {"answer": answer, "products": cards, "sources": sources, "drawings": drawings,
            "downloads": downloads, "conversation_id": turn.conversation_id,
            "conversation_token": conversation_token(turn.conversation_id),
            "message_id": msg_id, "route": route, "rag_route": rag.get("route", ""),
            "seconds": round(total_ms / 1000, 1)}


@router.get("/files/{rel:path}")
def public_file(rel: str, request: Request) -> FileResponse:
    user = service_user(request)
    entry = request.app.state.kb.entry_for_rel(rel, user)
    if not _is_public_entry(entry) or not os.path.isfile(entry["path"]):
        raise HTTPException(status_code=404, detail="Archivo no disponible.")
    return FileResponse(entry["path"], filename=Path(entry["path"]).name,
                        content_disposition_type="inline",
                        headers={"Cache-Control": "public, max-age=3600"})


@router.get("/datasheet-pdf/{part:path}")
def public_datasheet(part: str, request: Request, kb: str = "", inline: bool = True) -> Response:
    from app.products import build_datasheet_pdf, datasheet_pdf_filename, get_product_index
    user = service_user(request)
    reg = request.app.state.kb
    wanted = [x for x in kb.split(",") if x.strip()]
    ok: list[str] = []
    for slug in wanted:
        try:
            ok += reg.resolve_selection(user, [slug])
        except HTTPException:
            pass
    rtr = reg.combined(ok) if ok else _selection(request, user)[1]
    p, variants, candidates = get_product_index(rtr).resolve(part)
    if p is None:
        raise HTTPException(status_code=404 if not candidates else 409,
                            detail=f"No hay una hoja técnica única para '{part}'.")
    try:
        pdf = build_datasheet_pdf(p, variants)
    except Exception:
        log.exception("marketplace datasheet pdf failed for %s", part)
        raise HTTPException(status_code=500, detail="No se pudo generar la hoja técnica.")
    fname = datasheet_pdf_filename(p)
    ascii_name = fname.encode("ascii", "ignore").decode() or "ficha_tecnica.pdf"
    audit("marketplace_datasheet", user=user, detail={"part": p.part})
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; "
                               f"filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}",
        "Cache-Control": "no-store"})


@router.post("/catalog/refresh")
def refresh_catalog(request: Request, x_admin_token: str = Header(default="")) -> dict:
    ok_token = bool(settings.admin_token) and hmac.compare_digest(x_admin_token,
                                                                  settings.admin_token)
    u = None if ok_token else optional_user(request)
    if not ok_token and not (u and u.can("index.manage")):
        raise HTTPException(status_code=403, detail="admin token or index.manage required")
    cat = get_catalog()
    cat.refresh(force=True)
    return cat.status()


def startup(app: Any) -> None:
    """Llamado desde el lifespan de main.py: cuenta de servicio + catálogo en segundo plano."""
    def _warm() -> None:
        try:
            ensure_service_account(getattr(app.state, "kb", None))
        except Exception:
            log.exception("marketplace: no se pudo crear la cuenta de servicio")
        cat = get_catalog()
        if cat.enabled:
            cat.refresh(force=True)
        else:
            log.info("marketplace: MARKETPLACE_CATALOG_URL vacío (asistente de tienda sin catálogo)")
    threading.Thread(target=_warm, name="marketplace-warmup", daemon=True).start()
