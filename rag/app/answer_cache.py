"""Semantic answer cache (MySQL tables answer_cache / answer_cache_log).

Called from ProductionRouter.answer() (app/production_router.py), which wraps
QueryRouter.answer():  lookup -> (miss) router -> store.

CACHE KEY = question + SCOPE. The scope is a sha256 of everything that can change the
answer besides the question:
    * the sorted knowledge-base slugs searched (a user only hits answers built from the
      bases he selected, which the registry already permission-checked);
    * the prompt profile id + a hash of its instructions (edited profile -> new scope);
    * the INDEX VERSION: manifest mtime of every base. A new upload or a re-index changes
      it, so every old entry of that selection stops matching automatically;
    * the chat / agent / embedding models, the reranker, the prompt texts, the app version;
    * whether the user may build invoice reports.

LOOKUP
    1. exact: same normalised question in the same scope -> hit (no vector needed);
    2. semantic: cosine(question vector, cached vectors of the scope) >= threshold AND the
       same part codes (extract_codes) AND the same numbers -> hit. "VDV-25" vs "VDV-32" or
       "20:1" vs "30:1" never match, however similar the sentences are.
    The question vector is the one the retriever computes anyway (shared query LRU in
    CachedEmbeddings), so the lookup costs no extra API call.

NEVER CACHED: clarify replies, INSUFFICIENT_CONTEXT / "not found" answers, answers to a
clarification (follow-ups), invoice reports (veto from the invoice_report tool), quotes,
answers without sources, answers citing the invoices folder, errors.

CALIBRATION: every lookup whose best candidate has similarity >= ANSWER_CACHE_LOG_FLOOR is
written to answer_cache_log. Label a sample (same / different) and compute the threshold:
    python -m app.cli cache-pairs --out pairs.csv     (fill the `label` column)
    python -m app.cli cache-calibrate pairs.csv
"""
from __future__ import annotations

import contextvars
import csv
import hashlib
import json
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterator

import numpy as np

from app.config import settings
from app.textutils import extract_codes, fold, query_numbers

log = logging.getLogger("rag.answer_cache")

CLARIFY_JOIN = "\nAclaración del usuario: "       # same marker as app/conversations.py

# ======================================================================= veto (per request)
_VETO: contextvars.ContextVar[set | None] = contextvars.ContextVar("answer_cache_veto",
                                                                   default=None)


def veto_cache(reason: str) -> None:
    """Called by tools whose output must never be cached (invoice_report). The set is
    shared by reference, so it also works from LangGraph worker threads (copied context)."""
    v = _VETO.get()
    if v is not None:
        v.add(reason)


@contextmanager
def veto_scope() -> Iterator[set]:
    v: set = set()
    token = _VETO.set(v)
    try:
        yield v
    finally:
        _VETO.reset(token)


# ======================================================================= keys
def normalize_question(q: str) -> str:
    return " ".join(fold(q).split())


def _sha(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def index_version(retriever: Any) -> list:
    parts = getattr(retriever, "parts", None) or [retriever]
    out = []
    for p in parts:
        snap = getattr(p, "snapshot", None)
        out.append([getattr(p, "namespace", ""), round(float(getattr(snap, "manifest_mtime", 0.0)), 3),
                    len(getattr(snap, "chunks", {}) or {})])
    return out


def make_scope(retriever: Any, kb_slugs: list[str] | None, prompt_id: Any,
               instructions: str, allow_invoices: bool) -> str:
    from app import __version__
    from app.prompts import ANSWER_SYSTEM, FAST_SYSTEM, TOOL_SYSTEM
    slugs = kb_slugs or getattr(retriever, "kb_slugs", None) or \
        [getattr(retriever, "kb_slug", "") or getattr(retriever, "namespace", "")]
    return _sha({
        "kbs": sorted(str(s) for s in slugs),
        "prompt_id": prompt_id, "instructions": _sha(instructions or "")[:16],
        "index": index_version(retriever),
        "models": [settings.llm_model, settings.effective_agent_model, settings.embed_model],
        "retrieval": [settings.top_k, settings.fast_top_k, settings.source_routing,
                      settings.rrf_k, settings.reranker_backend, settings.reranker_model],
        "prompts": _sha(FAST_SYSTEM, TOOL_SYSTEM, ANSWER_SYSTEM)[:16],
        "invoices": bool(allow_invoices), "app": __version__})


def query_vector(retriever: Any, question: str) -> np.ndarray | None:
    """The vector the retriever computes for this question anyway (shared LRU)."""
    emb = getattr(retriever, "embeddings", None)
    if emb is None and getattr(retriever, "parts", None):
        emb = retriever.parts[0].embeddings
    if emb is None:
        return None
    sq = getattr(retriever, "search_query", None)
    try:
        text = sq(question) if callable(sq) else question     # same string as the search
        v = np.asarray(emb.embed_query(text), dtype=np.float32)
    except Exception as e:
        log.info("answer cache: no query vector (%s) -> exact match only", str(e)[:120])
        return None
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


# ======================================================================= request kind
def request_kind(question: str) -> tuple:
    """What the user ASKS FOR besides the topic: a datasheet / PDF request and the source he
    names (manual, datasheet, drawing). Two questions with the same codes and a high cosine
    ('specs of AX-4192400' vs 'PDF datasheet of AX-4192400') are NOT the same request: the
    second one must return the PDF download link, so a semantic hit needs the same kind."""
    try:
        from app.router import QueryRouter            # lazy: router imports this module's users
        sheet = QueryRouter.is_datasheet_question(question)
        intent = tuple(sorted(QueryRouter.source_intent(question)))
    except Exception:                                 # never break a lookup over this
        f = fold(question)
        sheet = bool(re.search(r"\b(hojas? (tecnicas?|de datos)|fichas? tecnicas?|data ?sheets?|pdf)\b", f))
        intent = ()
    return (bool(sheet), intent)


def has_datasheet_link(answer: str) -> bool:
    return "/datasheet-pdf/" in (answer or "")


# ======================================================================= what is cacheable
_NO_ANSWER_RE = re.compile(
    # English
    r"(do(es)?n'?t|do(es)? not) (contain|mention|discuss|say|provide|include|cover|specify)|"
    r"could ?n'?t find|could not find|can'?t (answer|determine|find)|cannot (answer|determine|find)|"
    r"unable to (answer|determine|find)|no (information|data|passage|mention) (about|on|of|for)|"
    # Spanish (folded: no accents)
    r"no (encontre|encontramos|se encontro|se encontraron|halle|hallamos)|"
    r"no (contiene|contienen|incluye|incluyen|menciona|mencionan|especifica|especifican|"
    r"indica|indican|proporciona|proporcionan|aparece|aparecen)|"
    r"no (hay|tengo|dispongo de|cuento con) (informacion|datos)|sin informacion|"
    r"no (es|fue) posible (determinar|encontrar|responder)|no puedo (responder|determinar)",
    re.IGNORECASE)


def is_no_answer(answer: str) -> bool:
    head = fold(answer[:400].replace("’", "'")) + " " + answer[:400].lower()
    return bool(_NO_ANSWER_RE.search(head))


def uncacheable_reason(result: dict, question: str, vetoes: set | None = None,
                       followup: bool = False) -> str | None:
    """None = may be cached; otherwise a short reason (returned to the client in debug)."""
    from app.prompts import INSUFFICIENT
    if vetoes:
        return "veto: " + ",".join(sorted(vetoes))
    if followup or CLARIFY_JOIN in (question or ""):
        return "clarify follow-up"
    if result.get("clarify"):
        return "clarify reply"
    route = str(result.get("route") or "")
    if route in ("quote", "invoice", "invoice_report", "error", "cache", "clarify"):
        return f"route {route}"
    esc = str(result.get("escalation") or "")
    if esc in ("invoice_report", "no_permission"):       # router.py's own invoice path
        return f"escalation {esc}"
    if result.get("payload"):
        return "structured payload"
    ans = str(result.get("answer") or "")
    if not ans.strip():
        return "empty answer"
    if INSUFFICIENT in ans:
        return "insufficient context"
    if is_no_answer(ans):
        return "not-found answer"
    if request_kind(question)[0] and not has_datasheet_link(ans):
        # a datasheet / PDF request answered without the download link is a failure
        # (product not in the product CSVs yet): caching it would keep serving it after
        # the data is fixed
        return "datasheet without PDF link"
    sources = result.get("sources") or []
    if not sources:
        return "no sources"
    # an answer that CITES an invoice / import document is not cached (when the router marks
    # citations; otherwise every source counts). Merely retrieved, uncited chunks don't count.
    marked = any(isinstance(s, dict) and "cited" in s for s in sources)
    for s in sources:
        if marked and not (isinstance(s, dict) and s.get("cited")):
            continue
        src = (s.get("source") or s.get("id")) if isinstance(s, dict) else s
        if src and settings.section_of(str(src)) == "invoices":
            return "invoice source"
    return None


# ======================================================================= the cache
@dataclass
class _ScopeIndex:
    loaded_at: float = 0.0
    ids: list[int] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    codes: list[frozenset] = field(default_factory=list)
    numbers: list[frozenset] = field(default_factory=list)
    mat: np.ndarray | None = None


class AnswerCache:
    PURGE_EVERY_SECS = 3600.0

    def __init__(self, threshold: float | None = None) -> None:
        from app.db import Base, get_engine
        from app.models import AnswerCacheEntry, AnswerCacheLog
        self._E, self._L = AnswerCacheEntry, AnswerCacheLog
        Base.metadata.create_all(get_engine(), tables=[AnswerCacheEntry.__table__,
                                                       AnswerCacheLog.__table__])
        self.threshold = float(threshold if threshold is not None else settings.answer_cache_threshold)
        self._mem: dict[str, _ScopeIndex] = {}
        self._lock = threading.Lock()
        self._last_purge = 0.0
        self.stats_counter = {"lookups": 0, "exact_hits": 0, "semantic_hits": 0, "misses": 0,
                              "rejected_codes": 0, "stored": 0, "not_stored": 0}

    # ------------------------------------------------------------------ helpers
    def _cutoff(self):
        from app.db import utcnow
        return utcnow() - timedelta(days=settings.answer_cache_ttl_days)

    def _scope_index(self, scope: str) -> _ScopeIndex:
        now = time.time()
        with self._lock:
            idx = self._mem.get(scope)
            if idx is not None and now - idx.loaded_at < settings.answer_cache_refresh_secs:
                return idx
        from app.db import session_scope
        E = self._E
        with session_scope() as s:
            rows = (s.query(E.id, E.question, E.codes, E.numbers, E.vec)
                    .filter(E.scope == scope, E.created_at >= self._cutoff(), E.vec.isnot(None))
                    .order_by(E.id.desc()).limit(settings.answer_cache_max_per_scope).all())
        idx = _ScopeIndex(loaded_at=now)
        vecs = []
        for rid, q, codes, nums, vec in rows:
            v = np.frombuffer(vec, dtype=np.float32)
            if vecs and v.shape != vecs[0].shape:
                continue                                  # embedding model changed
            idx.ids.append(int(rid))
            idx.questions.append(q)
            idx.codes.append(frozenset(codes or []))
            idx.numbers.append(frozenset(nums or []))
            vecs.append(v)
        idx.mat = np.vstack(vecs) if vecs else None
        with self._lock:
            self._mem[scope] = idx
            if len(self._mem) > 256:                      # old scopes (previous index versions)
                for k in sorted(self._mem, key=lambda k: self._mem[k].loaded_at)[:64]:
                    self._mem.pop(k, None)
        return idx

    def _load_result(self, entry_id: int) -> tuple[dict, str, Any] | None:
        from app.db import session_scope, utcnow
        with session_scope() as s:
            e = s.get(self._E, entry_id)
            if e is None:
                return None
            e.hits += 1
            e.last_hit_at = utcnow()
            return dict(e.result or {}), e.question, e.created_at

    def _log_pair(self, scope: str, q: str, cached_q: str, sim: float, codes_match: bool,
                  accepted: bool, reason: str) -> None:
        try:
            from app.db import session_scope
            with session_scope() as s:
                s.add(self._L(scope=scope, question=q, cached_question=cached_q,
                              similarity=float(sim), codes_match=codes_match,
                              accepted=accepted, reason=reason[:64]))
        except Exception as e:                              # logging must never break a chat
            log.debug("answer cache log failed: %s", e)

    # ------------------------------------------------------------------ lookup
    def lookup(self, question: str, qvec: np.ndarray | None, scope: str) -> dict | None:
        """{"result", "exact", "similarity", "cached_question", "cached_at"} or None."""
        from app.db import session_scope
        self.stats_counter["lookups"] += 1
        qh = _sha(normalize_question(question))
        E = self._E
        with session_scope() as s:
            row = (s.query(E.id).filter(E.scope == scope, E.qhash == qh,
                                        E.created_at >= self._cutoff())
                   .order_by(E.id.desc()).first())
        q_kind = request_kind(question)
        if row is not None:
            got = self._load_result(int(row[0]))
            if got and q_kind[0] and not has_datasheet_link(str(got[0].get("answer") or "")):
                got = None             # stored before the PDF fix: rebuild it, never serve it
            if got:
                self.stats_counter["exact_hits"] += 1
                return {"result": got[0], "exact": True, "similarity": 1.0,
                        "cached_question": got[1], "cached_at": str(got[2])}
        if qvec is None:
            self.stats_counter["misses"] += 1
            return None
        idx = self._scope_index(scope)
        if idx.mat is None or idx.mat.shape[1] != qvec.shape[0]:
            self.stats_counter["misses"] += 1
            return None
        sims = idx.mat @ qvec
        order = np.argsort(-sims)
        q_codes, q_nums = frozenset(extract_codes(question)), query_numbers(question)
        best_logged = False
        for i in order[:20]:
            sim = float(sims[i])
            if sim < settings.answer_cache_log_floor:
                break
            codes_ok = idx.codes[i] == q_codes and idx.numbers[i] == q_nums
            kind_ok = request_kind(idx.questions[i]) == q_kind
            if not kind_ok:
                codes_ok = False      # same skip path: a lower candidate may be the same kind
            accepted = codes_ok and sim >= self.threshold
            if not best_logged:
                reason = "hit" if accepted else (
                    "request kind differs" if not kind_ok else
                    "codes/numbers differ" if not codes_ok else "below threshold")
                self._log_pair(scope, question, idx.questions[i], sim, codes_ok, accepted, reason)
                best_logged = True
            if not codes_ok:
                if sim >= self.threshold:
                    self.stats_counter["rejected_codes"] += 1
                continue                          # a lower candidate may have the same codes
            if not accepted:
                break                             # sorted: the rest are lower still
            got = self._load_result(idx.ids[i])
            if got and q_kind[0] and not has_datasheet_link(str(got[0].get("answer") or "")):
                continue
            if got:
                self.stats_counter["semantic_hits"] += 1
                return {"result": got[0], "exact": False, "similarity": round(sim, 4),
                        "cached_question": got[1], "cached_at": str(got[2])}
        self.stats_counter["misses"] += 1
        return None

    # ------------------------------------------------------------------ store
    def store(self, question: str, qvec: np.ndarray | None, scope: str, result: dict) -> None:
        from app.db import session_scope
        clean = {k: v for k, v in result.items()
                 if k not in ("transcript", "quota", "cache", "conversation_id", "message_id",
                              "question_id", "conversation_title", "conversation_created")}
        clean = json.loads(json.dumps(clean, default=str))
        with session_scope() as s:
            s.add(self._E(scope=scope, qhash=_sha(normalize_question(question)),
                          question=question, codes=sorted(extract_codes(question)),
                          numbers=sorted(query_numbers(question)),
                          vec=qvec.astype(np.float32).tobytes() if qvec is not None else None,
                          result=clean))
        self.stats_counter["stored"] += 1
        with self._lock:
            idx = self._mem.get(scope)
            if idx is not None:
                idx.loaded_at = 0.0                       # reload on next lookup
        self._maybe_purge(scope)

    def _maybe_purge(self, scope: str) -> None:
        """Bound each scope and drop expired entries (old index versions expire by TTL)."""
        from app.db import session_scope
        try:
            E = self._E
            with session_scope() as s:
                n = s.query(E.id).filter(E.scope == scope).count()
                extra = n - settings.answer_cache_max_per_scope
                if extra > 0:
                    old = [r[0] for r in s.query(E.id).filter(E.scope == scope)
                           .order_by(E.id.asc()).limit(extra)]
                    s.query(E).filter(E.id.in_(old)).delete(synchronize_session=False)
            if time.time() - self._last_purge < self.PURGE_EVERY_SECS:
                return
            self._last_purge = time.time()
            from app.db import utcnow
            with session_scope() as s:
                d = s.query(E).filter(E.created_at < self._cutoff()).delete(synchronize_session=False)
                s.query(self._L).filter(self._L.created_at < utcnow() - timedelta(days=180)) \
                    .delete(synchronize_session=False)
            if d:
                log.info("answer cache: %d expired entries removed", d)
        except Exception as e:
            log.warning("answer cache purge failed: %s", e)

    # ------------------------------------------------------------------ admin
    def clear(self) -> int:
        from app.db import session_scope
        with session_scope() as s:
            n = s.query(self._E).delete(synchronize_session=False)
        with self._lock:
            self._mem.clear()
        return int(n or 0)

    def stats(self) -> dict:
        out: dict[str, Any] = dict(self.stats_counter, threshold=self.threshold,
                                   scopes_in_memory=len(self._mem))
        try:
            from app.db import session_scope
            with session_scope() as s:
                out["entries"] = s.query(self._E.id).count()
        except Exception:
            out["entries"] = None
        lk = out["lookups"] or 1
        out["hit_rate"] = round((out["exact_hits"] + out["semantic_hits"]) / lk, 3)
        return out


# ======================================================================= calibration (CLI)
def export_pairs(path: str, limit: int = 2000) -> int:
    """Write the logged pairs to CSV with an empty `label` column (same / different)."""
    from app.db import Base, get_engine, session_scope
    from app.models import AnswerCacheLog as L
    Base.metadata.create_all(get_engine(), tables=[L.__table__])
    with session_scope() as s:
        rows = s.query(L).order_by(L.similarity.desc()).limit(limit).all()
        data = [(r.id, round(r.similarity, 4), int(r.codes_match), int(r.accepted), r.reason,
                 r.question, r.cached_question, r.label) for r in rows]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "similarity", "codes_match", "accepted", "reason", "question",
                    "cached_question", "label"])
        w.writerows(data)
    return len(data)


def calibrate(path: str, target_precision: float = 0.98) -> dict:
    """Read a labelled CSV (label = same | different) -> lowest threshold whose precision on
    the labelled pairs is >= target, and the recall it keeps."""
    same, rows = {"same", "s", "si", "sí", "yes", "y", "1", "igual"}, []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            lab = str(r.get("label") or "").strip().lower()
            if not lab:
                continue
            rows.append((float(r["similarity"]), r.get("codes_match", "1") in ("1", "True", "true"),
                         lab in same))
    if not rows:
        return {"error": "no labelled rows (fill the `label` column with same / different)"}
    n_same = sum(1 for _, _, y in rows if y)
    table, best = [], None
    t = 0.80
    while t <= 0.995:
        acc = [(y) for sim, ok, y in rows if ok and sim >= t]
        tp = sum(acc)
        prec = tp / len(acc) if acc else 1.0
        rec = tp / n_same if n_same else 0.0
        table.append({"threshold": round(t, 3), "accepted": len(acc), "precision": round(prec, 3),
                      "recall": round(rec, 3)})
        if best is None and acc and prec >= target_precision:
            best = table[-1]
        t += 0.005
    return {"labelled": len(rows), "same": n_same, "target_precision": target_precision,
            "recommended": best, "table": table}
