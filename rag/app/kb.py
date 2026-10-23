"""Multi-RAG: several knowledge bases, each with its own folder of files and its own index.

    general                 the existing index (RAG_DATA_DIR / RAG_DB_DIR), namespace ''
    <slug>                  bases created by an admin   <RAG_KB_DIR>/shared/<slug>/{files,index}
    rol-<rol>               one per role                <RAG_KB_DIR>/roles/<slug>/{files,index}
    u-<usuario>             one per user                <RAG_KB_DIR>/users/<slug>/{files,index}

Every non-default base names its files '@<slug>/<path>', so chunk ids, citations and
/files links are unique across bases and the existing router/agent code works unchanged
on the union of several bases (MultiRetriever).

Access (see can_read / can_write):
    global  read: read_roles (empty = all roles)      write: write_roles
    role    read: members of the role                 write: members with kb.upload_role
    user    read: the owner                           write: the owner with kb.upload_own
    kb.manage reads and writes every base (admin screens; the chat picker stays personal).

Routes
    GET    /kb                              bases I can use in the chat (+ which I can write)
    GET    /kb/{slug}/files                 files of a base (disk + index status)
    POST   /kb/{slug}/files                 upload (multipart: files[], subdir)
    DELETE /kb/{slug}/files/{path}          delete one file
    POST   /kb/{slug}/reindex               {mode: update|rebuild}
    GET    /kb/{slug}/status                index stats + job
    GET    /admin/kb  POST /admin/kb  PATCH|DELETE /admin/kb/{id}      [kb.manage]
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from collections import ChainMap, Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.auth import CurrentUser, audit, require
from app.config import IMAGE_EXTS, IMAGE_SECTIONS, settings
from app.db import session_scope
from app.jobs import IndexJobManager
from app.models import KnowledgeBase, Role, User
from app.fusion import rerank_and_select, rrf_fuse
from app.query_lang import expand_query, spell_fix
from app.retriever import HybridEvaluationRetriever, SearchSignals

log = logging.getLogger("rag.kb")
DEFAULT_SLUG = "general"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
SCOPES = ("global", "role", "user")
_SCOPE_FOLDER = {"global": "shared", "role": "roles", "user": "users"}
INFO_TTL = 5.0                      # seconds the KB rows are cached per process


# ====================================================================== naming + paths
def slugify(text: str, prefix: str = "") -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:70] or "base"
    return f"{prefix}{t}"[:80]


def role_slug(role_name: str) -> str:
    return slugify(role_name, "rol-")


def user_slug(username: str) -> str:
    return slugify(username, "u-")


@dataclass(frozen=True)
class KBInfo:
    """Plain copy of a knowledge_bases row (safe to use outside a DB session)."""
    id: int
    slug: str
    name: str
    description: str
    scope: str
    owner_user_id: int | None
    role_id: int | None
    read_roles: tuple[int, ...]
    write_roles: tuple[int, ...]
    data_dir: str
    index_dir: str
    is_default: bool
    is_active: bool
    selected_by_default: bool
    owner_username: str = ""
    role_name: str = ""

    @property
    def namespace(self) -> str:
        return "" if self.is_default else self.slug

    @property
    def files_dir(self) -> Path:
        if self.data_dir and not self.is_default:
            # external folder stored in the DB: only inside RAG_KB_EXTERNAL_ROOTS (.env)
            if not settings.external_root_ok(self.data_dir):
                raise PermissionError(
                    f"La carpeta de la base «{self.slug}» ({self.data_dir}) no está dentro de "
                    "RAG_KB_EXTERNAL_ROOTS del .env.")
            return Path(self.data_dir)
        if self.data_dir:
            return Path(self.data_dir)
        if self.is_default:
            return Path(settings.data_dir)
        return Path(settings.kb_dir) / _SCOPE_FOLDER.get(self.scope, "shared") / self.slug / "files"

    @property
    def idx_dir(self) -> Path:
        if self.index_dir:
            return Path(self.index_dir)
        if self.is_default:
            return Path(settings.index_dir)
        return Path(settings.kb_dir) / _SCOPE_FOLDER.get(self.scope, "shared") / self.slug / "index"

    @property
    def quota_bytes(self) -> int:
        mb = {"user": settings.kb_user_quota_mb, "role": settings.kb_role_quota_mb}.get(self.scope, 0)
        return int(mb * 1024 * 1024) if mb else 0

    def public(self) -> dict:
        return {"id": self.id, "slug": self.slug, "name": self.name,
                "description": self.description, "scope": self.scope,
                "owner_user_id": self.owner_user_id, "owner_username": self.owner_username,
                "role_id": self.role_id, "role_name": self.role_name,
                "read_roles": list(self.read_roles), "write_roles": list(self.write_roles),
                "is_default": self.is_default, "is_active": self.is_active,
                "selected_by_default": self.selected_by_default,
                "custom_dir": bool(self.data_dir) and not self.is_default}


def _info(kb: KnowledgeBase, owners: dict[int, str], roles: dict[int, str]) -> KBInfo:
    return KBInfo(id=kb.id, slug=kb.slug, name=kb.name, description=kb.description or "",
                  scope=kb.scope, owner_user_id=kb.owner_user_id, role_id=kb.role_id,
                  read_roles=tuple(int(x) for x in (kb.read_roles or [])),
                  write_roles=tuple(int(x) for x in (kb.write_roles or [])),
                  data_dir=kb.data_dir or "", index_dir=kb.index_dir or "",
                  is_default=kb.is_default, is_active=kb.is_active,
                  selected_by_default=kb.selected_by_default,
                  owner_username=owners.get(kb.owner_user_id or 0, ""),
                  role_name=roles.get(kb.role_id or 0, ""))


# ====================================================================== provisioning
def ensure_role_kb(s, role: Role) -> KnowledgeBase:
    kb = s.query(KnowledgeBase).filter_by(scope="role", role_id=role.id).first()
    if kb is None:
        kb = KnowledgeBase(slug=role_slug(role.name), name=f"Carpeta del rol {role.name}",
                           description=f"Archivos compartidos por el rol {role.name}",
                           scope="role", role_id=role.id, read_roles=[], write_roles=[],
                           created_by="sistema")
        s.add(kb)
        s.flush()
    return kb


def ensure_user_kb(s, user_id: int) -> KnowledgeBase:
    kb = s.query(KnowledgeBase).filter_by(scope="user", owner_user_id=user_id).first()
    if kb is None:
        u = s.get(User, user_id)
        kb = KnowledgeBase(slug=user_slug(u.username), name="Mis archivos",
                           description=f"Carpeta personal de {u.full_name or u.username}",
                           scope="user", owner_user_id=u.id, read_roles=[], write_roles=[],
                           created_by="sistema")
        s.add(kb)
        s.flush()
    return kb


def ensure_system_kbs() -> None:
    """Default base + one base per role (idempotent; called by init_db)."""
    with session_scope() as s:
        if s.query(KnowledgeBase).filter_by(is_default=True).first() is None:
            s.add(KnowledgeBase(slug=DEFAULT_SLUG, name="Base general",
                                description="Documentos técnicos de la empresa (índice principal)",
                                scope="global", read_roles=[], write_roles=[],
                                is_default=True, created_by="sistema"))
            s.flush()
        for role in s.query(Role).all():
            ensure_role_kb(s, role)


# ====================================================================== access rules
def can_read(kb: KBInfo, user: CurrentUser, manage: bool = True) -> bool:
    if manage and user.can("kb.manage"):
        return True
    if not kb.is_active:
        return False
    if kb.scope == "global":
        return not kb.read_roles or user.role_id in kb.read_roles
    if kb.scope == "role":
        return kb.role_id == user.role_id
    return kb.owner_user_id == user.id


def can_write(kb: KBInfo, user: CurrentUser) -> bool:
    if user.can("kb.manage"):
        return True
    if not kb.is_active:
        return False
    if kb.scope == "global":
        return user.role_id in kb.write_roles
    if kb.scope == "role":
        return kb.role_id == user.role_id and user.can("kb.upload_role")
    return kb.owner_user_id == user.id and user.can("kb.upload_own")


# ====================================================================== multi retriever
@dataclass
class _MultiSnapshot:
    catalog: Any
    chunks: Any
    empty: bool
    built_at: float = 0.0


class MultiRetriever:
    """Same read API as HybridEvaluationRetriever over the union of several bases.
    File names are unique thanks to the namespaces, so catalogs and chunk tables are just
    chained (no copies). Writes (indexing) always go to one base, never through here.
    Instances are cached per selection by the registry, so per-retriever caches (product
    index of app/products.py) survive between requests."""

    def __init__(self, retrievers: list[HybridEvaluationRetriever],
                 slugs: list[str] | None = None) -> None:
        self.parts = retrievers
        self.kb_slugs = list(slugs or [getattr(r, "kb_slug", "") for r in retrievers])
        self._top_k = max(r._top_k for r in retrievers)
        self._snap_key: tuple = ()
        self._snap: _MultiSnapshot | None = None

    # ---- snapshot-like view (rebuilt only when one of the bases swaps its snapshot)
    @property
    def snapshot(self) -> _MultiSnapshot:
        snaps = [r.snapshot for r in self.parts]
        key = tuple(id(s) for s in snaps)
        cur = self._snap
        if cur is None or key != self._snap_key:
            cur = _MultiSnapshot(catalog=ChainMap(*[s.catalog for s in snaps]),
                                 chunks=ChainMap(*[s.chunks for s in snaps]),
                                 empty=all(s.empty for s in snaps),
                                 built_at=max(s.built_at for s in snaps))
            self._snap, self._snap_key = cur, key
        return cur

    def _table_rows(self, path: str):
        """CSV rows by absolute path (used by products / multiagent); cached by its base."""
        for r in self.parts:
            try:
                Path(path).resolve().relative_to(r.data_dir.resolve())
                return r._table_rows(path)
            except ValueError:
                continue
        return self.parts[0]._table_rows(path)

    def maybe_reload(self) -> None:
        for r in self.parts:
            r.maybe_reload()

    def stats(self) -> dict:
        out: dict = {"files": 0, "chunks": 0, "catalog": 0, "vectors": 0}
        for r in self.parts:
            st = r.stats()
            for k in out:
                out[k] += max(0, int(st.get(k) or 0))
        return out

    # ---- search
    def retrieve(self, query: str, top_k: int | None = None, kinds: set[str] | None = None,
                 hits: list[dict] | None = None, sections: set[str] | None = None) -> list[dict]:
        return self.retrieve_with_signals(query, top_k, kinds, hits, sections)[0]

    def sections_present(self) -> set[str]:
        return {s for r in self.parts for s in r.sections_present()}

    def retrieve_with_signals(self, query: str, top_k: int | None = None,
                              kinds: set[str] | None = None,
                              hits: list[dict] | None = None,
                              sections: set[str] | None = None,
                              ) -> tuple[list[dict], SearchSignals]:
        """Production 1.0: ONE Reciprocal Rank Fusion over every base (replaces the 5.7
        per-base min-max + damping heuristic).

        * vector list: candidates of all bases merged by raw cosine relevance — comparable,
          because every base uses the same embedding model;
        * BM25 lists: one per base (BM25 scores depend on each corpus' idf, so they are NOT
          comparable across bases: only their ranks are used);
        * then the same cross-encoder + code pinning + table expansion as a single base."""
        k = top_k or self._top_k
        sig = SearchSignals(vector_ok=True)
        if not (query or "").strip():
            return [], sig
        # glossary + typo fix ONCE, against the vocabulary of all selected bases, so every
        # base searches (and embeds) the same string: one embedding call, shared LRU
        search_q = self._search_query(query)
        try:
            self.parts[0].embeddings.embed_query(search_q)
        except Exception:
            pass                         # each base falls back to BM25 on its own
        vec_all: list[tuple[str, float]] = []
        bm_lists: list[list[str]] = []
        for r in self.parts:
            vec, bm, sg = r._candidates(search_q, kinds, sections, expanded=True)
            vec_all.extend(vec)
            if bm:
                bm_lists.append([c for c, _ in bm])
            sig.vector_ok = sig.vector_ok and sg.vector_ok
            sig.max_vector = max(sig.max_vector, sg.max_vector)
            sig.max_bm25 = max(sig.max_bm25, sg.max_bm25)
        vec_all.sort(key=lambda x: x[1], reverse=True)
        vec_list = [c for c, _ in vec_all[:settings.candidate_pool * len(self.parts)]]
        if not vec_list and not bm_lists:
            return [], sig
        # the BM25 weight is shared between the bases' lists so the keyword side keeps the
        # same total weight as with one base
        wb = settings.rrf_bm25_weight / max(1, len(bm_lists))
        fused = rrf_fuse([vec_list] + bm_lists, [settings.rrf_vector_weight] + [wb] * len(bm_lists))
        results, sig.max_rerank = rerank_and_select(query, fused, self.snapshot.chunks, k,
                                                    max_extra=8, focus_query=search_q)
        sig.n_results = len(results)
        if hits is not None:
            hits.extend(results)
        return results, sig

    def drawings(self) -> list[dict]:
        return [d for r in self.parts for d in r.drawings()]

    def _search_query(self, query: str) -> str:
        """Glossary + typo correction against the vocabulary of ALL selected bases."""
        if getattr(settings, "query_translate", "glossary") == "off":
            return query
        q = expand_query(query)
        if not settings.query_spell_fix:
            return q
        words: set = set()
        idx: dict = {}
        for r in self.parts:
            w, i = r.vocab()
            words |= w
            for key, v in i.items():
                idx.setdefault(key, []).extend(v)
        return spell_fix(q, words, idx) if words else q

    def search_query(self, query: str, snap: Any = None) -> str:
        return self._search_query(query)

    # ---- documents
    def _owner(self, doc: str) -> HybridEvaluationRetriever | None:
        raw = str(doc or "").strip().strip("[]\"'` ")
        if raw.startswith("@"):
            ns = raw[1:].split("/", 1)[0]
            for r in self.parts:
                if r.namespace == ns:
                    return r
        best, best_rank = None, 9
        for r in self.parts:
            rel, alts = r.resolve_doc(raw)
            if not rel:
                continue
            rank = 0 if rel == raw else 1 if not alts else 2
            if rank < best_rank:
                best, best_rank = r, rank
        return best

    def resolve_doc(self, raw: str, snap: Any = None) -> tuple[str | None, list[str]]:
        r = self._owner(raw)
        return r.resolve_doc(raw) if r else (None, [])

    def read_document(self, doc: str, part: int = 1, query: str = "",
                      max_chars: int | None = None, hits: list[dict] | None = None) -> str:
        r = self._owner(doc) or self.parts[0]
        return r.read_document(doc, part=part, query=query, max_chars=max_chars, hits=hits)

    def list_documents(self, keyword: str = "", manufacturer: str = "", doc_type: str = "",
                       limit: int | None = None) -> tuple[list[dict], int]:
        limit = limit or settings.list_docs_max
        lists, total = [], 0
        for r in self.parts:
            items, n = r.list_documents(keyword, manufacturer, doc_type, limit)
            lists.append(items)
            total += n
        if keyword:                      # interleave: each list is already ranked
            out: list[dict] = []
            for i in range(max((len(x) for x in lists), default=0)):
                out.extend(x[i] for x in lists if i < len(x))
        else:
            out = sorted((e for x in lists for e in x),
                         key=lambda e: (e["manufacturer"].lower(), e["type"], e["rel"].lower()))
        return out[:max(1, int(limit))], total

    def catalog_summary(self) -> str:
        from app.catalog import DOC_TYPE_LABEL
        cat = self.snapshot.catalog
        by_type = Counter(DOC_TYPE_LABEL.get(e["type"], e["type"]) for e in cat.values())
        return (f"{len(cat)} files in {len(self.parts)} knowledge bases: "
                + ", ".join(f"{v} {k}" for k, v in by_type.most_common()))

    def table_lookup(self, code: str, columns: str = "", table: str = "",
                     limit: int | None = None, hits: list[dict] | None = None) -> str:
        found, misses = [], []
        for r in self.parts:
            if not any(e["type"] == "table" for e in r.snapshot.catalog.values()):
                continue
            before = len(hits) if hits is not None else 0
            out = r.table_lookup(code, columns=columns, table=table, limit=limit, hits=hits)
            got = (len(hits) - before) if hits is not None else int(" 0 matching" not in out)
            (found if got else misses).append(out)
        if found:
            return "\n\n".join(found)
        return misses[0] if misses else ("No CSV table in the selected knowledge bases. "
                                         "Use semantic_search instead.")


# ====================================================================== registry
class KBRegistry:
    """Per process: KB rows (cached a few seconds), one retriever + one job manager per
    base, created on first use. The default base reuses the injected retriever."""

    def __init__(self, default_retriever: HybridEvaluationRetriever,
                 default_jobs: IndexJobManager) -> None:
        self._lock = threading.RLock()
        self._serial = threading.Lock()
        default_jobs.serial_lock = self._serial
        self._default = default_retriever
        self._vision = getattr(default_retriever, "_vision_llm", None)
        self._run_preflight = default_jobs.run_preflight
        self._retrievers: dict[str, HybridEvaluationRetriever] = {DEFAULT_SLUG: default_retriever}
        self._jobs: dict[str, IndexJobManager] = {DEFAULT_SLUG: default_jobs}
        self._infos: dict[str, KBInfo] = {}
        self._infos_at = 0.0
        self._multi: dict[tuple[str, ...], MultiRetriever] = {}
        default_retriever.kb_slug = DEFAULT_SLUG

    # ---- rows
    def refresh(self) -> None:
        with session_scope() as s:
            owners = dict(s.query(User.id, User.username).join(
                KnowledgeBase, KnowledgeBase.owner_user_id == User.id).all())
            roles = dict(s.query(Role.id, Role.name).all())
            infos = {kb.slug: _info(kb, owners, roles)
                     for kb in s.query(KnowledgeBase).order_by(KnowledgeBase.id).all()}
        from dataclasses import replace
        for slug, kb in infos.items():
            if kb.is_default:     # the folders of the retriever actually serving the base
                infos[slug] = replace(
                    kb, data_dir=str(self._default.data_dir),
                    index_dir=str(Path(self._default._manifest_file).resolve().parent))
        with self._lock:
            self._infos, self._infos_at = infos, time.time()

    def infos(self) -> dict[str, KBInfo]:
        if time.time() - self._infos_at > INFO_TTL:
            self.refresh()
        return self._infos

    def info(self, slug: str) -> KBInfo | None:
        got = self.infos().get(slug)
        if got is None:                 # created moments ago by another worker?
            self.refresh()
            got = self._infos.get(slug)
        return got

    @property
    def default_slug(self) -> str:
        return DEFAULT_SLUG

    # ---- retrievers + jobs
    def retriever(self, slug: str) -> HybridEvaluationRetriever:
        with self._lock:
            r = self._retrievers.get(slug)
            if r is not None:
                return r
            kb = self.info(slug)
            if kb is None:
                raise KeyError(slug)
            try:
                files_dir = kb.files_dir
            except PermissionError as e:
                log.error("%s", e)
                raise HTTPException(status_code=403, detail=str(e)) from None
            files_dir.mkdir(parents=True, exist_ok=True)
            kb.idx_dir.mkdir(parents=True, exist_ok=True)
            r = HybridEvaluationRetriever(
                data_dir=str(files_dir), chroma_dir=str(kb.idx_dir / "chroma"),
                manifest_file=str(kb.idx_dir / "manifest.json"), namespace=kb.namespace,
                vision_llm=self._vision, cached_embeddings=self._default.embeddings)
            r.load()
            r.kb_slug = slug
            self._retrievers[slug] = r
            self._jobs[slug] = IndexJobManager(r, run_preflight=self._run_preflight,
                                               serial_lock=self._serial)
            return r

    def jobs(self, slug: str) -> IndexJobManager:
        self.retriever(slug)
        return self._jobs[slug]

    def loaded(self, slug: str) -> bool:
        return slug in self._retrievers

    def forget(self, slug: str) -> None:
        with self._lock:
            if slug == DEFAULT_SLUG:
                return
            self._retrievers.pop(slug, None)
            self._jobs.pop(slug, None)
            self._multi = {k: v for k, v in self._multi.items() if slug not in k}
        self.refresh()

    def combined(self, slugs: list[str]) -> Any:
        """One retriever for the selection (the base itself, or a cached MultiRetriever)."""
        if len(slugs) == 1:
            return self.retriever(slugs[0])
        key = tuple(slugs)
        with self._lock:
            m = self._multi.get(key)
            if m is None:
                m = MultiRetriever([self.retriever(s) for s in slugs], list(slugs))
                if len(self._multi) > 64:
                    self._multi.clear()
                self._multi[key] = m
            return m

    # ---- per user
    def readable(self, user: CurrentUser, for_chat: bool = True) -> list[KBInfo]:
        """for_chat: the chat picker shows the bases the user is entitled to by role,
        not every personal folder an admin could open."""
        return [kb for kb in self.infos().values()
                if kb.is_active and can_read(kb, user, manage=not for_chat)]

    def default_selection(self, user: CurrentUser) -> list[str]:
        mine = self.readable(user)
        sel = [kb.slug for kb in mine if kb.selected_by_default]
        return sel or [kb.slug for kb in mine[:1]]

    def resolve_selection(self, user: CurrentUser, wanted: list[str] | None) -> list[str]:
        """Bases searched by one question. Explicit list -> validated; None -> the
        user's saved selection, else the defaults. Unknown/forbidden saved slugs are
        dropped silently; an explicit forbidden slug is an error."""
        infos = self.infos()
        if wanted:
            out = []
            for slug in dict.fromkeys(wanted):
                kb = infos.get(slug) or self.info(slug)
                if kb is None or not kb.is_active or not can_read(kb, user):
                    raise HTTPException(status_code=403, detail=(
                        f"No tienes acceso a la base de conocimientos «{slug}»."))
                out.append(slug)
            return out
        saved = user.preferences.get("kbs")
        if saved:
            ok = [s for s in saved if s in infos and infos[s].is_active
                  and can_read(infos[s], user, manage=False)]
            if ok:
                return ok
        sel = self.default_selection(user)
        if not sel:
            raise HTTPException(status_code=403, detail=(
                "Tu rol no tiene ninguna base de conocimientos asignada."))
        return sel

    def entry_for_rel(self, rel: str, user: CurrentUser) -> dict | None:
        """Catalog entry of a file the user may open (for /files/{rel})."""
        slug = rel[1:].split("/", 1)[0] if rel.startswith("@") else DEFAULT_SLUG
        kb = self.info(slug)
        if kb is None or not can_read(kb, user):
            return None
        return self.retriever(slug).snapshot.catalog.get(rel)


def get_registry(request: Request) -> KBRegistry:
    return request.app.state.kb


# ====================================================================== files on disk
def _clean_segment(seg: str) -> str:
    seg = unicodedata.normalize("NFC", seg).strip().strip(".").strip()
    seg = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", seg)
    return seg[:180]


def safe_relpath(name: str, subdir: str = "") -> str:
    parts = [p for p in re.split(r"[\\/]+", subdir or "") if p.strip()]
    if len(parts) > 4:
        raise HTTPException(status_code=400, detail="Subcarpeta demasiado profunda (máx. 4 niveles).")
    clean = []
    for p in parts:
        c = _clean_segment(p)
        if not c or c in (".", ".."):
            raise HTTPException(status_code=400, detail="Nombre de subcarpeta inválido.")
        clean.append(c)
    fname = _clean_segment(re.split(r"[\\/]+", name or "")[-1])
    if not fname or fname in (".", "..") or fname.startswith("~$"):
        raise HTTPException(status_code=400, detail=f"Nombre de archivo inválido: {name!r}")
    ext = Path(fname).suffix.lower()
    rel = "/".join(clean + [fname])
    if ext in IMAGE_EXTS and settings.section_of(rel) not in IMAGE_SECTIONS:
        # images are only indexed inside the drawing folders declared in the .env
        target = settings.first_dir("drawings") or settings.first_dir("fotos") \
            or settings.first_dir("explosionados")
        if not target:
            raise HTTPException(status_code=400, detail=(
                "No hay carpeta de dibujos declarada en el .env (RAG_DIR_DRAWINGS)."))
        rel = "/".join([target] + clean + [fname])
    return rel


def _inside(base: Path, rel: str) -> Path:
    root = base.resolve()
    target = (root / rel).resolve()
    if target == root or root not in target.parents:
        raise HTTPException(status_code=400, detail="Ruta fuera de la carpeta de la base.")
    return target


def _folder_size(p: Path) -> int:
    total = 0
    if p.is_dir():
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    return total


def list_files(kb: KBInfo, r: HybridEvaluationRetriever | None, limit: int = 5000) -> dict:
    root = kb.files_dir
    cat = r.snapshot.catalog if r is not None else {}
    items, total_bytes = [], 0
    exts = {e.lower() for e in settings.kb_upload_exts}
    if root.is_dir():
        for f in sorted(root.rglob("*")):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
            except OSError:
                continue
            rel = f.relative_to(root).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            total_bytes += st.st_size
            if len(items) >= limit:
                continue
            nrel = f"@{kb.namespace}/{rel}" if kb.namespace else rel
            ext = f.suffix.lower()
            supported = ext in exts
            if nrel in cat:
                status = "indexed"
            elif not supported:
                status = "unsupported"
            elif ext in IMAGE_EXTS and settings.section_of(rel) not in IMAGE_SECTIONS:
                status = "ignored"
            else:
                status = "pending"
            items.append({"path": rel, "rel": nrel, "name": f.name, "size": st.st_size,
                          "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                          "ext": ext, "status": status, "section": settings.section_of(rel),
                          "title": (cat.get(nrel) or {}).get("title", "") if nrel in cat else ""})
    return {"items": items, "total_bytes": total_bytes, "quota_bytes": kb.quota_bytes,
            "truncated": len(items) >= limit}


def kb_status(reg: KBRegistry, kb: KBInfo) -> dict:
    r = reg.retriever(kb.slug)
    # job first: if it says "done", the stats read next already include its result
    job = dict(reg.jobs(kb.slug).status)
    st = r.stats()
    state = r._load_state()
    return {"files": st.get("files", 0), "chunks": st.get("chunks", 0),
            "vectors": st.get("vectors", 0), "indexed_at": st.get("indexed_at", ""),
            "pending_drawings": st.get("pending_drawings", 0),
            "warning": st.get("startup_warning", ""), "job": job,
            "embedding_totals": state.get("totals") or {}}


# ====================================================================== routes
router = APIRouter()


def _kb_or_404(reg: KBRegistry, slug: str) -> KBInfo:
    kb = reg.info(slug)
    if kb is None:
        raise HTTPException(status_code=404, detail="Base de conocimientos no encontrada.")
    return kb


def _ensure_personal(reg: KBRegistry, user: CurrentUser) -> None:
    """The personal base is created on first use (listing, upload, status...)."""
    if not user.can("kb.upload_own"):
        return
    if any(kb.scope == "user" and kb.owner_user_id == user.id for kb in reg.infos().values()):
        return
    with session_scope() as s:
        ensure_user_kb(s, user.id)
    reg.refresh()


def _readable(reg: KBRegistry, slug: str, user: CurrentUser) -> KBInfo:
    if reg.info(slug) is None and slug == user_slug(user.username):
        _ensure_personal(reg, user)
    kb = _kb_or_404(reg, slug)
    if not can_read(kb, user):
        raise HTTPException(status_code=404, detail="Base de conocimientos no encontrada.")
    return kb


def _writable(reg: KBRegistry, slug: str, user: CurrentUser) -> KBInfo:
    kb = _readable(reg, slug, user)
    if not can_write(kb, user):
        raise HTTPException(status_code=403, detail="No puedes agregar archivos a esta base.")
    return kb


@router.get("/kb")
def my_kbs(request: Request, user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    _ensure_personal(reg, user)
    items = []
    for kb in reg.readable(user):
        d = kb.public()
        d["can_write"] = can_write(kb, user)
        try:
            st = reg.retriever(kb.slug).stats()
            d["files"], d["chunks"] = st.get("files", 0), st.get("chunks", 0)
            d["indexing"] = reg.jobs(kb.slug).status.get("state") in ("running", "queued")
        except Exception as e:               # a broken folder must not hide the others
            log.warning("kb %s: %s", kb.slug, e)
            d["files"], d["chunks"], d["error"] = 0, 0, str(e)[:200]
        items.append(d)
    order = {"user": 0, "role": 1, "global": 2}
    items.sort(key=lambda d: (not d["is_default"], order.get(d["scope"], 3), d["name"].lower()))
    return {"items": items, "selected": reg.resolve_selection(user, None) if items else [],
            "default_selection": reg.default_selection(user),
            "upload_exts": list(settings.kb_upload_exts),
            "max_upload_mb": settings.kb_max_upload_mb}


@router.get("/kb/{slug}/files")
def kb_files(slug: str, request: Request,
             user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    kb = _readable(reg, slug, user)
    out = list_files(kb, reg.retriever(slug))
    out.update(kb=kb.public(), can_write=can_write(kb, user), status=kb_status(reg, kb))
    if kb.is_default and not user.can("kb.manage"):
        out["items"] = [i for i in out["items"] if i["status"] == "indexed"]
    return out


@router.get("/kb/{slug}/status")
def kb_status_route(slug: str, request: Request,
                    user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    kb = _readable(reg, slug, user)
    return kb_status(reg, kb)


@router.post("/kb/{slug}/files")
def kb_upload(slug: str, request: Request, files: list[UploadFile] = File(...),
              subdir: str = Form(""), user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    kb = _writable(reg, slug, user)
    if kb.is_default and not user.can("kb.manage"):
        raise HTTPException(status_code=403, detail="Solo un administrador agrega a la base general.")
    allowed = {e.lower() for e in settings.kb_upload_exts}
    max_bytes = int(settings.kb_max_upload_mb * 1024 * 1024)
    root = kb.files_dir
    root.mkdir(parents=True, exist_ok=True)
    used = _folder_size(root) if kb.quota_bytes else 0
    saved, rejected = [], []
    for up in files:
        name = up.filename or ""
        try:
            rel = safe_relpath(name, subdir)
        except HTTPException as e:
            rejected.append({"name": name, "reason": e.detail})
            continue
        if Path(rel).suffix.lower() not in allowed:
            rejected.append({"name": name, "reason": "Tipo no permitido. Usa: "
                             + ", ".join(sorted(allowed))})
            continue
        target = _inside(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.uploading")
        size = 0
        too_big = over_quota = False
        old_size = target.stat().st_size if target.exists() else 0
        with open(tmp, "wb") as out:
            while True:
                chunk = up.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    too_big = True
                    break
                if kb.quota_bytes and used - old_size + size > kb.quota_bytes:
                    over_quota = True
                    break
                out.write(chunk)
        if too_big or over_quota or size == 0:
            tmp.unlink(missing_ok=True)
            reason = (f"Supera {settings.kb_max_upload_mb:g} MB por archivo." if too_big else
                      f"Sin espacio: la carpeta tiene un límite de {kb.quota_bytes / 1048576:,.0f} MB."
                      if over_quota else "Archivo vacío.")
            rejected.append({"name": name, "reason": reason})
            continue
        os.replace(tmp, target)
        used += size - old_size
        saved.append({"path": rel, "size": size, "replaced": bool(old_size)})
    job = None
    if saved:
        audit("kb_file_uploaded", user=user,
              detail={"kb": slug, "files": [x["path"] for x in saved][:50]})
        if settings.kb_auto_index_on_upload:
            job = reg.jobs(slug).start_or_queue()
    return {"saved": saved, "rejected": rejected, "job": job}


@router.delete("/kb/{slug}/files/{path:path}")
def kb_delete_file(slug: str, path: str, request: Request,
                   user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    kb = _writable(reg, slug, user)
    if kb.is_default and not user.can("kb.manage"):
        raise HTTPException(status_code=403, detail="Solo un administrador modifica la base general.")
    if path.startswith("@"):
        path = path.split("/", 1)[1] if "/" in path else ""
    target = _inside(kb.files_dir, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    target.unlink()
    # remove empty folders left behind (never the base folder itself)
    parent, root = target.parent, kb.files_dir.resolve()
    while parent != root and root in parent.parents and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    audit("kb_file_deleted", user=user, detail={"kb": slug, "file": path})
    job = reg.jobs(slug).start_or_queue() if settings.kb_auto_index_on_upload else None
    return {"ok": True, "job": job}


class ReindexBody(BaseModel):
    mode: str = "update"


@router.post("/kb/{slug}/reindex")
def kb_reindex(slug: str, body: ReindexBody, request: Request,
               user: CurrentUser = Depends(require("chat"))) -> dict:
    reg = get_registry(request)
    kb = _writable(reg, slug, user)
    if body.mode not in ("update", "rebuild"):
        raise HTTPException(status_code=400, detail="mode debe ser update o rebuild")
    if (body.mode == "rebuild" or kb.is_default) and not (
            user.can("kb.manage") or user.can("index.manage")):
        raise HTTPException(status_code=403, detail="Solo un administrador puede reconstruir.")
    out = (reg.jobs(slug).start(body.mode) if body.mode == "rebuild"
           else reg.jobs(slug).start_or_queue())
    audit("kb_reindex", user=user, detail={"kb": slug, "mode": body.mode})
    return out


# ---------------------------------------------------------------------- admin
class KBCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    slug: str = Field("", max_length=80)
    description: str = Field("", max_length=500)
    read_roles: list[int] = Field(default_factory=list)
    write_roles: list[int] = Field(default_factory=list)
    data_dir: str = Field("", max_length=500)
    selected_by_default: bool = True


class KBPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=150)
    description: str | None = Field(None, max_length=500)
    read_roles: list[int] | None = None
    write_roles: list[int] | None = None
    is_active: bool | None = None
    selected_by_default: bool | None = None


def _check_roles(s, ids: list[int]) -> list[int]:
    ids = sorted(set(int(i) for i in ids))
    if ids:
        found = {r for (r,) in s.query(Role.id).filter(Role.id.in_(ids))}
        if found != set(ids):
            raise HTTPException(status_code=400, detail="Algún rol no existe.")
    return ids


@router.get("/admin/kb")
def admin_list_kb(request: Request, user: CurrentUser = Depends(require("kb.manage"))) -> dict:
    reg = get_registry(request)
    reg.refresh()
    items = []
    for kb in reg.infos().values():
        d = kb.public()
        try:
            d.update(status=kb_status(reg, kb), disk_bytes=_folder_size(kb.files_dir),
                     quota_bytes=kb.quota_bytes)
        except Exception as e:
            d.update(status={"error": str(e)[:200]}, disk_bytes=0, quota_bytes=kb.quota_bytes)
        items.append(d)
    order = {"global": 0, "role": 1, "user": 2}
    items.sort(key=lambda d: (not d["is_default"], order.get(d["scope"], 3), d["name"].lower()))
    with session_scope() as s:
        roles = [{"id": r.id, "name": r.name} for r in s.query(Role).order_by(Role.id)]
    return {"items": items, "roles": roles, "kb_dir": str(Path(settings.kb_dir).resolve()),
            "upload_exts": list(settings.kb_upload_exts),
            "max_upload_mb": settings.kb_max_upload_mb}


@router.post("/admin/kb")
def admin_create_kb(body: KBCreate, request: Request,
                    user: CurrentUser = Depends(require("kb.manage"))) -> dict:
    reg = get_registry(request)
    slug = (body.slug.strip().lower() or slugify(body.name))
    if not SLUG_RE.match(slug) or slug.startswith(("rol-", "u-")) or slug == DEFAULT_SLUG:
        raise HTTPException(status_code=400, detail=(
            "Identificador inválido: 2-80 caracteres a-z 0-9 - _, sin los prefijos rol- / u-."))
    data_dir = body.data_dir.strip()
    if data_dir and not os.path.isabs(data_dir):
        raise HTTPException(status_code=400, detail="La carpeta externa debe ser una ruta absoluta.")
    if data_dir and not settings.external_root_ok(data_dir):
        raise HTTPException(status_code=400, detail=(
            "La carpeta externa debe estar dentro de una de las raíces permitidas en el .env "
            "(RAG_KB_EXTERNAL_ROOTS)."))
    if data_dir:
        data_dir = str(Path(data_dir).resolve())
    with session_scope() as s:
        if s.query(KnowledgeBase).filter_by(slug=slug).first():
            raise HTTPException(status_code=409, detail="Ya existe una base con ese identificador.")
        kb = KnowledgeBase(slug=slug, name=body.name.strip(), description=body.description.strip(),
                           scope="global", read_roles=_check_roles(s, body.read_roles),
                           write_roles=_check_roles(s, body.write_roles), data_dir=data_dir,
                           selected_by_default=body.selected_by_default,
                           created_by=user.username)
        s.add(kb)
        s.flush()
        kid = kb.id
        audit("kb_created", user=user, session=s, detail={"slug": slug, "name": kb.name})
    reg.refresh()
    info = reg.info(slug)
    info.files_dir.mkdir(parents=True, exist_ok=True)
    if data_dir:                      # an existing folder: index what is already there
        reg.jobs(slug).start_or_queue()
    return {"id": kid, **info.public()}


@router.patch("/admin/kb/{kb_id}")
def admin_update_kb(kb_id: int, body: KBPatch, request: Request,
                    user: CurrentUser = Depends(require("kb.manage"))) -> dict:
    reg = get_registry(request)
    with session_scope() as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="Base no encontrada.")
        ch = body.model_dump(exclude_none=True)
        if "name" in ch:
            kb.name = body.name.strip()
        if "description" in ch:
            kb.description = body.description.strip()
        if "read_roles" in ch:
            if kb.scope != "global":
                raise HTTPException(status_code=400, detail="Solo las bases globales tienen roles de lectura.")
            kb.read_roles = _check_roles(s, body.read_roles)
        if "write_roles" in ch:
            if kb.scope != "global":
                raise HTTPException(status_code=400, detail="Solo las bases globales tienen roles de escritura.")
            kb.write_roles = _check_roles(s, body.write_roles)
        if "is_active" in ch:
            if kb.is_default and not body.is_active:
                raise HTTPException(status_code=400, detail="La base general no se puede desactivar.")
            kb.is_active = body.is_active
        if "selected_by_default" in ch:
            kb.selected_by_default = body.selected_by_default
        slug = kb.slug
        audit("kb_updated", user=user, session=s, detail={"slug": slug, **ch})
    reg.refresh()
    return reg.info(slug).public()


@router.delete("/admin/kb/{kb_id}")
def admin_delete_kb(kb_id: int, request: Request, delete_files: bool = False,
                    user: CurrentUser = Depends(require("kb.manage"))) -> dict:
    reg = get_registry(request)
    with session_scope() as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="Base no encontrada.")
        if kb.is_default or kb.scope != "global":
            raise HTTPException(status_code=400, detail=(
                "Solo se eliminan bases globales creadas por un administrador; las de rol y "
                "usuario se pueden desactivar."))
        slug = kb.slug
    info = reg.info(slug)
    if reg.loaded(slug) and reg.jobs(slug).running:
        raise HTTPException(status_code=409, detail="La base se está indexando; espera a que termine.")
    reg.forget(slug)
    with session_scope() as s:
        kb = s.get(KnowledgeBase, kb_id)
        if kb is not None:
            s.delete(kb)
        audit("kb_deleted", user=user, session=s, detail={"slug": slug, "files": delete_files})
    # the index is always removed; the files only on request and never an external folder
    base_folder = Path(settings.kb_dir) / "shared" / slug
    if info is not None:
        shutil.rmtree(info.idx_dir, ignore_errors=True)
        if delete_files and not info.data_dir:
            shutil.rmtree(base_folder, ignore_errors=True)
    reg.refresh()
    return {"ok": True}
