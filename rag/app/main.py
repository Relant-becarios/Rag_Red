"""Relant App Production 1.0 — FastAPI app.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Every endpoint except /auth/login, /healthz and the static pages needs a logged-in user
(session cookie or `Authorization: Bearer`) with the permission shown in brackets.

Endpoints
    POST /auth/login | /auth/logout | /auth/change-password     GET /auth/me   (app/auth.py)
    GET/PUT /me/preferences
    POST /chat                 {query, conversation_id?, debug?}  [chat]
    POST /chat/agent           same, always the agent              [chat.agent]
    GET  /conversations[/{id}] PATCH/DELETE /conversations/{id}    [chat]  (app/conversations.py)
    GET  /documents            catalog listing, no LLM             [documents.read]
                               ?kb=slug1,slug2 (default: the bases selected in the chat)
    GET  /files/{rel}          a source file / drawing in the catalog [documents.read]
                               '@<base>/<path>' for files of other knowledge bases
    /kb, /kb/{slug}/files ...  multi-RAG bases, uploads, per-base reindex   (app/kb.py)
    /prompts, /admin/prompts   prompt profiles per role               (app/prompt_profiles.py)
    /me/usage, /admin/token-dashboard, /admin/limits  token limits     (app/usage.py)
    POST /quote                {query, conversation_id?, kb_ids?}  multi-agent draft quote
                               (supervisor -> quoting -> margin || verifier)  [agents.quote]
    GET  /datasheet/{part}     technical datasheet as HTML (?kb=...)          [documents.read]
    GET  /datasheet-pdf/{part} the same datasheet as a downloadable PDF        [documents.read]
                               (?kb=..., ?inline=true to open it in the browser; app/pdfdatagen.py)
    GET  /products?q=          product master data as JSON                    [documents.read]
    GET  /graph, /graph.json   data graph + LangGraph diagrams      [kb.manage|index.manage|reports.read]
    GET  /admin/data-report    data-quality report of the product data       [kb.manage|index.manage]
    GET  /health               index + router stats                [logged in]
    GET  /healthz              liveness for IIS / monitors (public, no details)
    /marketplace/*             PUBLIC store assistant: catalog search, chat, public files
                               (app/marketplace_api.py; service account, rate limit)
    POST /admin/index          {mode: update|rebuild}  [index.manage] or header X-Admin-Token
    GET  /admin/index          job status
    POST /admin/web/refresh    re-crawl manufacturer websites in the background
    /admin/users, /admin/roles, /admin/conversations, /admin/messages.csv,
    /admin/usage, /admin/audit                                       (app/admin_api.py)

Request handlers are plain `def`: FastAPI runs them in its thread pool, so slow LLM calls
never block the event loop, and the retriever is lock-free for reads.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import admin_api, auth, conversations, kb, marketplace_api, prompt_profiles, usage
from app.auth import CurrentUser, audit, current_user, optional_user, require
from app.kb import KBRegistry
from app.multiagent import QuoteCoordinator, quote_markdown
from app.textutils import norm
from app.usage import require_any
from app.catalog import catalog_entry_public
from app.config import settings
from app.db import init_db, session_scope
from app.errors import OpenRouterError, api_error_message, classify_api_error
from app.jobs import IndexJobManager
from app.retriever import HybridEvaluationRetriever
from app import APP_NAME, __version__
from app.production_router import ProductionRouter
from app.production_router import ProductionRouter as QueryRouter   # Production 1.0

_handlers: list[logging.Handler] = [logging.StreamHandler()]
if settings.log_dir:                      # RAG_LOG_DIR in the .env ("" = console only)
    from logging.handlers import RotatingFileHandler
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    _handlers.append(RotatingFileHandler(Path(settings.log_dir) / "relant_rag.log",
                                         maxBytes=10_000_000, backupCount=5, encoding="utf-8"))
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), handlers=_handlers,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag.api")
BUILD = "2026-10-19b excel+hojas · 2026-10-20 marketplace"      # visible en /healthz: confirma qué versión corre
STATIC_DIR = Path(settings.static_dir)    # RAG_STATIC_DIR in the .env


def _log_paths() -> None:
    """Startup: which .env was read and where every folder is (nothing hardcoded)."""
    rep = settings.paths_report()
    log.info(".env: %s (existe: %s)", rep["env_file"], rep["env_file_exists"])
    for k, v in rep.items():
        if k.startswith("RAG_"):
            log.info("  %s = %s", k, v)
    if not os.path.isdir(settings.data_dir):
        log.warning("RAG_DATA_DIR no existe: %s", settings.data_dir)
    from app.config import SECTION_ENV
    missing = [f"{env}={d}" for section, env in SECTION_ENV.items()
               for d in settings.section_dirs(section)
               if not (Path(settings.data_dir) / d).is_dir()]
    if missing and os.path.isdir(settings.data_dir):
        log.warning("carpetas declaradas en el .env que no existen dentro de RAG_DATA_DIR "
                    "(python -m app.cli init-data las crea): %s", ", ".join(missing))


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    conversation_id: int | None = None      # None -> a new conversation is created
    debug: bool = False                     # honoured only with permission chat.debug
    # multi-RAG: knowledge bases to search (None -> saved selection / role defaults)
    kb_ids: list[str] | None = Field(None, max_length=50)
    # prompt profile (None -> saved / role default, 0 -> none)
    prompt_id: int | None = None


class IndexRequest(BaseModel):
    mode: str = "update"


def build_components(retriever: HybridEvaluationRetriever | None = None, llm: Any = None,
                     agent_llm: Any = None, run_preflight: bool | None = None) -> dict:
    """Production wiring; tests inject fake retriever/LLM here."""
    from app.llm import make_llm
    if retriever is None:
        vision = make_llm(settings.effective_vision_model) if settings.describe_images else None
        retriever = HybridEvaluationRetriever(vision_llm=vision)
    llm = llm or make_llm(settings.llm_model)
    agent_llm = agent_llm or (make_llm(settings.effective_agent_model)
                              if settings.agent_model else llm)
    return {"retriever": retriever, "router": QueryRouter(retriever, llm, agent_llm),
            "quoter": QuoteCoordinator(retriever, llm, supervisor_llm=agent_llm),
            "jobs": IndexJobManager(retriever, run_preflight=run_preflight)}


def create_app(components: dict | None = None, auto_sync: bool | None = None,
               web_warmup: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()                                    # tables + default roles (idempotent)
        comps = components or build_components()
        comps["retriever"].load()                    # fast start from disk (seconds)
        app.state.retriever, app.state.router, app.state.jobs = (
            comps["retriever"], comps["router"], comps["jobs"])
        app.state.quoter = comps.get("quoter") or QuoteCoordinator(
            comps["retriever"], comps["router"].llm, supervisor_llm=comps["router"].agent.llm)
        app.state.kb = comps.get("registry") or KBRegistry(comps["retriever"], comps["jobs"])
        app.state.kb.refresh()
        if settings.auto_sync_on_start if auto_sync is None else auto_sync:
            app.state.jobs.start("update")           # background; requests are served now
        if settings.web_enabled and (settings.web_warmup if web_warmup is None else web_warmup):
            from app.manufacturer_web import get_web
            threading.Thread(target=lambda: get_web().ensure(allow_crawl=True),
                             name="web-warmup", daemon=True).start()
        if settings.reranker_backend != "off" and settings.reranker_warmup:
            from app.reranker import get_reranker   # load the cross-encoder off the hot path
            threading.Thread(target=get_reranker().warmup, name="reranker-warmup",
                             daemon=True).start()
        marketplace_api.startup(app)                 # cuenta de servicio + catálogo tienda
        _log_paths()
        log.info("%s %s ready", APP_NAME, __version__)
        yield

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware,
                           allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                           allow_methods=["*"], allow_headers=["*"])
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth.router)
    app.include_router(conversations.router)
    app.include_router(admin_api.router)
    app.include_router(kb.router)
    app.include_router(prompt_profiles.router)
    app.include_router(usage.router)
    app.include_router(marketplace_api.router)      # /marketplace/* (tienda en línea)

    def get_router(request: Request) -> QueryRouter:
        return request.app.state.router

    def get_retriever(request: Request) -> HybridEvaluationRetriever:
        return request.app.state.retriever

    def require_index_admin(request: Request, x_admin_token: str = Header(default="")) -> str:
        """X-Admin-Token (CLI / scheduled scripts) or a user with index.manage."""
        if settings.admin_token and x_admin_token == settings.admin_token:
            return "token"
        u = optional_user(request)
        if u is not None and u.can("index.manage") and not u.must_change_password:
            request.state.user = u
            return u.username
        raise HTTPException(status_code=403, detail="admin token or index.manage required")

    def _llm_error(e: Exception) -> HTTPException:
        c = classify_api_error(e)
        if isinstance(e, OpenRouterError) or (c and c[0] != "rate"):
            return HTTPException(status_code=503, detail=api_error_message(e))
        if c and c[0] == "rate":
            return HTTPException(status_code=429, detail=c[1])
        log.exception("query failed")
        return HTTPException(status_code=500, detail="Error interno al procesar la consulta.")

    @app.get("/")
    def index_page() -> Any:
        page = STATIC_DIR / "index.html"
        if page.exists():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return {"service": "Relant RAG", "docs": "/docs"}

    @app.get("/admin")
    def admin_page() -> Any:
        page = STATIC_DIR / "admin.html"
        if page.exists():
            return FileResponse(page, headers={"Cache-Control": "no-cache"})
        return RedirectResponse("/")

    def get_registry(request: Request) -> KBRegistry:
        return request.app.state.kb

    def _chat(req: ChatRequest, user: CurrentUser, router: QueryRouter, reg: KBRegistry,
              force_agent: bool) -> dict:
        debug = bool(req.debug and user.can("chat.debug"))
        # multi-RAG selection + prompt profile are validated BEFORE anything is stored
        slugs = reg.resolve_selection(user, req.kb_ids)
        prompt_id, instructions = prompt_profiles.resolve_prompt(user, req.prompt_id)
        retriever = reg.combined(slugs)
        turn = conversations.begin_turn(user, req.conversation_id, req.query, force_agent)
        t0 = time.perf_counter()
        history = [] if turn.created else conversations.recent_history(
            turn.conversation_id, turn.user_message_id, settings.conversation_history_turns)
        try:
            extra = {}
            if isinstance(router, ProductionRouter):     # an injected plain QueryRouter
                extra = dict(prompt_id=prompt_id, kb_slugs=slugs,   # (tests) lacks these
                             is_followup=turn.effective_query != req.query, history=history)
            res = router.answer(turn.effective_query, force_agent=force_agent, debug=debug,
                                retriever=retriever, instructions=instructions,**extra).to_dict()
        except Exception as e:
            err = _llm_error(e)
            conversations.fail_turn(user, turn, f"{err.status_code}: {err.detail}",
                                    force_agent, int((time.perf_counter() - t0) * 1000),
                                    kb_slugs=slugs, prompt_id=prompt_id)
            raise err
        msg_id = conversations.finish_turn(user, turn, res, force_agent, keep_transcript=debug,
                                           kb_slugs=slugs, prompt_id=prompt_id)
        res.update(conversation_id=turn.conversation_id, conversation_title=turn.title,
                   conversation_created=turn.created, message_id=msg_id,
                   question_id=turn.user_message_id, kb_slugs=slugs,
                   prompt_profile_id=prompt_id)
        try:
            res["quota"] = usage.quota_for(user)
        except Exception:                                  # never fail an answered question
            log.exception("quota read failed")
        return res

    @app.post("/chat")
    def chat(req: ChatRequest, router: QueryRouter = Depends(get_router),
             reg: KBRegistry = Depends(get_registry),
             user: CurrentUser = Depends(require("chat"))) -> dict:
        return _chat(req, user, router, reg, force_agent=False)

    @app.post("/chat/agent")
    def chat_agent(req: ChatRequest, router: QueryRouter = Depends(get_router),
                   reg: KBRegistry = Depends(get_registry),
                   user: CurrentUser = Depends(require("chat", "chat.agent"))) -> dict:
        return _chat(req, user, router, reg, force_agent=True)

    @app.post("/quote")
    def quote(req: ChatRequest, request: Request, reg: KBRegistry = Depends(get_registry),
              user: CurrentUser = Depends(require("chat", "agents.quote"))) -> dict:
        """Multi-agent DRAFT quote over the selected knowledge bases. Logged in the
        conversation like any answer (tokens count for the limits and the dashboard);
        never sent to a customer automatically: a person approves it."""
        debug = bool(req.debug and user.can("chat.debug"))
        slugs = reg.resolve_selection(user, req.kb_ids)
        retriever = reg.combined(slugs)
        turn = conversations.begin_turn(user, req.conversation_id, req.query, False)
        t0 = time.perf_counter()
        try:
            out = request.app.state.quoter.run(turn.effective_query, debug=True,
                                               retriever=retriever)
        except Exception as e:
            err = _llm_error(e)
            conversations.fail_turn(user, turn, f"{err.status_code}: {err.detail}", False,
                                    int((time.perf_counter() - t0) * 1000), kb_slugs=slugs)
            raise err
        catalog = retriever.snapshot.catalog
        sources, seen = [], set()
        for ln in (out.get("quote") or {}).get("lines", []):
            sid = ln.get("source_id")
            if sid and sid not in seen:
                seen.add(sid)
                rel = (ln.get("row_ref") or [""])[0]
                sources.append({"id": sid, "source": rel, "kind": "table", "score": None,
                                "cited": True, "url": f"/files/{rel}" if rel in catalog else ""})
        status = out.get("status", "")
        payload = {"type": "quote", **{k: v for k, v in out.items()
                                       if k not in ("usage", "timings_ms", "log")}}
        res = {"answer": quote_markdown(out), "route": "quote", "escalation": status,
               "clarify": status == "clarify", "sources": sources, "drawings": [],
               "agent_steps": len([x for x in (out.get("log") or []) if x.startswith("[supervisor]")]),
               "usage": out.get("usage") or {}, "timings_ms": out.get("timings_ms") or {},
               "transcript": out.get("log") if debug else None, "payload": payload}
        msg_id = conversations.finish_turn(user, turn, res, False, keep_transcript=debug,
                                           kb_slugs=slugs)
        res.update(conversation_id=turn.conversation_id, conversation_title=turn.title,
                   conversation_created=turn.created, message_id=msg_id,
                   question_id=turn.user_message_id, kb_slugs=slugs, prompt_profile_id=None)
        try:
            res["quota"] = usage.quota_for(user)
        except Exception:
            log.exception("quota read failed")
        audit("quote_created", user=user, detail={"status": status, "message_id": msg_id,
                                                  "kbs": slugs})
        return res

    def _selection(reg: KBRegistry, user: CurrentUser, kb: str):
        wanted = [x.strip() for x in kb.split(",") if x.strip()] or None
        return reg.combined(reg.resolve_selection(user, wanted))


    def _datasheet_selection(reg: KBRegistry, user: CurrentUser, kb: str):
        """Bases for a datasheet link. The link carries the bases of the answer that made
        it; a base this user cannot read (a conversation of another user, a base removed
        since) is skipped instead of failing the download. 403 only if none is readable."""
        wanted = [x.strip() for x in kb.split(",") if x.strip()]
        ok = []
        for slug in dict.fromkeys(wanted):
            try:
                ok += reg.resolve_selection(user, [slug])
            except HTTPException:
                log.info("datasheet: base '%s' skipped for %s (no access)", slug, user.username)
        if wanted and not ok:
            raise HTTPException(status_code=403, detail=(
                "No tienes acceso a la base de conocimientos de esta hoja técnica."))
        return reg.combined(ok or reg.resolve_selection(user, None))

    def _one_product(part: str, retriever: Any):
        """(product, variants) of one datasheet. The same part number in several rows (a
        repeated row, the same CSV / Excel in two knowledge bases) is NOT an error: equal
        rows collapse into one and different rows become variant columns. Only a query
        that matches different products is a 409 (the user must pick a part number)."""
        from app.products import get_product_index
        p, variants, candidates = get_product_index(retriever).resolve(part)
        if p is not None:
            return p, variants
        if not candidates:
            raise HTTPException(status_code=404, detail=(
                f"No hay producto '{part}' en las tablas de productos (CSV / Excel)."))
        raise HTTPException(status_code=409, detail={
            "message": (f"'{part}' coincide con {len(candidates)} productos; pide la hoja "
                        "técnica con el número de parte exacto: "
                        + ", ".join(c.part or c.name for c in candidates[:10])),
            "candidates": [{"part": c.part, "name": c.name, "family": c.family}
                           for c in candidates[:20]]})

    def _datasheet_error(part: str, status: int, message: str, as_pdf: bool) -> Response:
        """Error of a datasheet link. A browser download link saves WHATEVER comes back,
        so the error is a small PDF with the reason (it opens) instead of a JSON file
        named after the part number. The reason also goes in X-Error-Detail for the chat
        page and API clients."""
        from html import escape
        from urllib.parse import quote as _q
        log.warning("datasheet %s -> %d: %s", part, status, message)
        hdr = {"X-Error-Detail": _q(message), "Cache-Control": "no-store"}
        if as_pdf:
            try:
                import io
                from xhtml2pdf import pisa
                buf = io.BytesIO()
                pisa.CreatePDF(
                    "<html><head><meta charset='utf-8'><style>body{font-family:Helvetica;"
                    "font-size:11pt}h1{color:#C62828;font-size:15pt}</style></head><body>"
                    f"<h1>No se pudo generar la hoja técnica de {escape(part)}</h1>"
                    f"<p>{escape(message)}</p></body></html>", dest=buf, encoding="utf-8")
                body = buf.getvalue()
                if body.startswith(b"%PDF"):
                    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in part)
                    hdr["Content-Disposition"] = (f'inline; filename="ERROR_hoja_tecnica_'
                                                  f'{safe.encode("ascii", "ignore").decode()}.pdf"')
                    return Response(content=body, status_code=status,
                                    media_type="application/pdf", headers=hdr)
            except Exception:
                log.exception("datasheet error pdf failed")
        return HTMLResponse(f"<!doctype html><meta charset='utf-8'><title>Hoja técnica</title>"
                            f"<h1>No se pudo generar la hoja técnica de {escape(part)}</h1>"
                            f"<p>{escape(message)}</p>", status_code=status, headers=hdr)

    def _err_text(e: HTTPException) -> str:
        d = e.detail
        return d.get("message", str(d)) if isinstance(d, dict) else str(d)

    @app.get("/datasheet/{part:path}", response_class=HTMLResponse)
    def datasheet(part: str, kb: str = "", embed: bool = True,
                  reg: KBRegistry = Depends(get_registry),
                  user: CurrentUser = Depends(require("documents.read"))) -> Response:
        """HTML datasheet built from the product table row + photo + exploded view.
        embed=true: images inside the page (it can be saved or e-mailed as one file)."""
        from app.products import build_datasheet_html
        try:
            p, variants = _one_product(part, _datasheet_selection(reg, user, kb))
            return HTMLResponse(build_datasheet_html(p, embed=embed, variants=variants))
        except HTTPException as e:
            return _datasheet_error(part, e.status_code, _err_text(e), as_pdf=False)
        except Exception:
            log.exception("datasheet html failed for %s", part)
            return _datasheet_error(part, 500, "Error interno al generar la hoja técnica.",
                                    as_pdf=False)

    @app.get("/datasheet-pdf/{part:path}")
    def datasheet_pdf(part: str, kb: str = "", inline: bool = False,
                      reg: KBRegistry = Depends(get_registry),
                      user: CurrentUser = Depends(require("documents.read"))) -> Response:
        """Technical datasheet as a downloadable PDF (app/pdfdatagen.py, xhtml2pdf).
        Same data as /datasheet/{part}; inline=true opens it in the browser instead.
        Errors come back as a PDF with the reason (see _datasheet_error)."""
        from app.products import build_datasheet_pdf, datasheet_pdf_filename
        try:
            p, variants = _one_product(part, _datasheet_selection(reg, user, kb))
        except HTTPException as e:
            return _datasheet_error(part, e.status_code, _err_text(e), as_pdf=True)
        try:
            pdf = build_datasheet_pdf(p, variants)
            if not pdf.startswith(b"%PDF"):
                raise RuntimeError(f"not a PDF ({len(pdf)} bytes)")
        except Exception as ex:
            log.exception("datasheet pdf failed for %s", part)
            return _datasheet_error(part, 500, f"Error al generar el PDF: {ex}", as_pdf=True)
        audit("datasheet_pdf", user=user, detail={"part": p.part, "name": p.name,
                                                  "family": p.family, "bytes": len(pdf),
                                                  "variants": len(variants)})
        fname = datasheet_pdf_filename(p)
        from urllib.parse import quote as _q
        disp = "inline" if inline else "attachment"
        ascii_name = fname.encode("ascii", "ignore").decode() or "ficha_tecnica.pdf"
        return Response(content=pdf, media_type="application/pdf", headers={
            "Content-Disposition": f"{disp}; filename=\"{ascii_name}\"; "
                                   f"filename*=UTF-8''{_q(fname)}",
            "Cache-Control": "no-store"})

    @app.get("/products")
    def products(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50),
                 kb: str = "", reg: KBRegistry = Depends(get_registry),
                 user: CurrentUser = Depends(require("documents.read"))) -> dict:
        from app.products import get_product_index
        found = get_product_index(_selection(reg, user, kb)).find(q, limit=limit)
        # price + stock yes, purchase cost / margin never (they are for the margin agent only)
        hide = ("costo", "coste", "cost", "margen", "margin", "proveedor")
        return {"total": len(found), "items": [
            dict(p.summary(), stock={h: v for h, v in p.stock
                                     if not any(w in norm(h) for w in hide)}) for p in found]}

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page(request: Request, kb: str = "", reg: KBRegistry = Depends(get_registry),
                   user: CurrentUser = Depends(require_any("kb.manage", "index.manage",
                                                           "reports.read"))) -> HTMLResponse:
        from app.graph_export import system_graph_page
        st = request.app.state
        return HTMLResponse(system_graph_page(_selection(reg, user, kb), st.router, st.quoter))

    @app.get("/graph.json")
    def graph_json(max_nodes: int = Query(4000, ge=10, le=20000), kb: str = "",
                   reg: KBRegistry = Depends(get_registry),
                   user: CurrentUser = Depends(require_any("kb.manage", "index.manage",
                                                           "reports.read"))) -> dict:
        from app.graph_export import product_graph
        return product_graph(_selection(reg, user, kb), max_nodes=max_nodes)

    @app.get("/admin/data-report")
    def data_report(kb: str = "", reg: KBRegistry = Depends(get_registry),
                    user: CurrentUser = Depends(require_any("kb.manage", "index.manage"))) -> dict:
        from app.products import get_product_index
        return get_product_index(_selection(reg, user, kb)).report()

    @app.get("/documents")
    def documents(keyword: str = "", manufacturer: str = "", doc_type: str = "",
                  kb: str = "", limit: int = Query(50, ge=1, le=500),
                  reg: KBRegistry = Depends(get_registry),
                  user: CurrentUser = Depends(require("documents.read"))) -> dict:
        wanted = [x.strip() for x in kb.split(",") if x.strip()] or None
        slugs = reg.resolve_selection(user, wanted)
        retriever = reg.combined(slugs)
        items, total = retriever.list_documents(keyword, manufacturer, doc_type, limit)
        out = []
        for e in items:
            d = catalog_entry_public(e)
            d["kb"] = d["kb"] or reg.default_slug
            out.append(d)
        return {"total": total, "summary": retriever.catalog_summary(), "kbs": slugs,
                "items": out}

    @app.get("/files/{rel:path}")
    def get_file(rel: str, reg: KBRegistry = Depends(get_registry),
                 user: CurrentUser = Depends(require("documents.read"))) -> FileResponse:
        # Only files that are in the catalog of a base the user may read: no path
        # traversal, nothing outside the indexes.
        entry = reg.entry_for_rel(rel, user)
        if not entry or not os.path.isfile(entry["path"]):
            raise HTTPException(status_code=404, detail="file not found")
        # inline: PDFs and drawings open in the browser instead of downloading
        return FileResponse(entry["path"], filename=Path(entry["path"]).name,
                            content_disposition_type="inline")

    @app.get("/healthz")
    def healthz() -> dict:
        """Public liveness probe: no paths, no counts."""
        db_ok = True
        try:
            from sqlalchemy import text as _sql
            with session_scope() as s:
                s.execute(_sql("SELECT 1"))
        except Exception:
            db_ok = False
        return {"ok": db_ok, "db": db_ok, "build": BUILD}

    @app.get("/health")
    def health(request: Request, user: CurrentUser = Depends(current_user)) -> dict:
        st = request.app.state
        out = {"index": st.retriever.stats(), "router": st.router.stats(),
               "index_job": st.jobs.status}
        try:                       # what the chat will actually search for this user
            reg: KBRegistry = st.kb
            slugs = reg.resolve_selection(user, None)
            sel = reg.combined(slugs)
            sst = sel.stats()
            jobs = [reg.jobs(x).status.get("state") for x in slugs]
            out["selection"] = {"kbs": slugs, "files": sst.get("files", 0),
                                "chunks": sst.get("chunks", 0),
                                "indexing": any(j in ("running", "queued") for j in jobs)}
        except HTTPException:
            out["selection"] = {"kbs": [], "files": 0, "chunks": 0, "indexing": False}
        if user.can("index.manage"):
            # shown by the chat page when nothing is indexed (internal tool)
            out["config"] = {"data_dir": settings.data_dir,
                             "data_dir_exists": os.path.isdir(settings.data_dir),
                             "index_dir": settings.index_dir,
                             "source_routing": settings.source_routing,
                             "paths": settings.paths_report()}
        return out

    @app.post("/admin/index")
    def admin_index(req: IndexRequest, request: Request,
                    who: str = Depends(require_index_admin)) -> dict:
        try:
            out = request.app.state.jobs.start(req.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        u = getattr(request.state, "user", None)
        audit("index_started", user=u, username=who, detail={"mode": req.mode, **out})
        return out

    @app.get("/admin/index")
    def admin_index_status(request: Request, who: str = Depends(require_index_admin)) -> dict:
        return request.app.state.jobs.status

    @app.post("/admin/web/refresh")
    def admin_web_refresh(request: Request, who: str = Depends(require_index_admin)) -> dict:
        from app.manufacturer_web import get_web
        web = get_web()
        audit("web_refresh", user=getattr(request.state, "user", None), username=who)
        return {"started": web.refresh_in_background(refresh=True), "status": web.status()}

    return app


app = FastAPI()

# Configurar orígenes permitidos
origins = [
    "http://localhost:8082",         # Puerto estándar de tu IIS frontend
    "http://tu-servidor-ip",    # Dirección IP de producción de tu frontend
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
) 
app = create_app()

# Este bloque permite arrancar el servidor directamente con "python -m app.main"
if __name__ == "__main__":
    import uvicorn
    # Ejecuta la aplicación FastAPI en el puerto 8000
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)