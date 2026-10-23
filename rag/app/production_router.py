"""ProductionRouter: QueryRouter + semantic answer cache + heuristic
pre-route. The fast-path / signal routing of QueryRouter is NOT changed.

    ProductionRouter.answer(question, history)
      0. follow-up -> standalone question from the conversation (app/conversation_context.py)
      1. answer cache lookup (exact, then semantic with the code/number rule)  -> hit: done
         (skipped for /chat/agent, debug and answers to a clarification)
      2. heuristic pre-route (app/preroute.py): obviously multi-step -> force the agent
      3. QueryRouter.answer()  (fast path -> signals / INSUFFICIENT_CONTEXT -> agent)
      4. store the result in the cache unless a never-cache rule applies

It subclasses QueryRouter (app/router.py) and only wraps answer(), so router.py itself is
unchanged. The result object keeps the same .to_dict() contract that main.py, cli.py and
conversations.finish_turn() use; three keys are added: "cache", "preroute" (when it fired)
and route="cache" / escalation="exact" | "semantic 0.953" on a hit.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any

from app.answer_cache import (CLARIFY_JOIN, AnswerCache, make_scope, query_vector,
                              uncacheable_reason, veto_scope)
from app.config import settings
from app.conversation_context import contextualize
from app.llm import UsageMeter
from app.preroute import heuristic_preroute
from app.router import QueryRouter

log = logging.getLogger("rag.router.production")


class RouterResult:
    """Dict-backed result with the same .to_dict() as the QueryRouter result."""

    def __init__(self, data: dict, inner: Any = None) -> None:
        self._data, self._inner = data, inner

    def to_dict(self) -> dict:
        return dict(self._data)

    def __getattr__(self, name: str) -> Any:
        data = self.__dict__.get("_data") or {}
        if name in data:
            return data[name]
        inner = self.__dict__.get("_inner")
        if inner is not None:
            return getattr(inner, name)
        raise AttributeError(name)


_ZERO_USAGE = {"llm_calls": 0, "by_stage": {}, "input_tokens": 0, "output_tokens": 0,
               "llm_seconds": 0.0, "est_cost_usd": 0.0}


class ProductionRouter(QueryRouter):
    def __init__(self, retriever: Any, llm: Any, agent_llm: Any = None, *args: Any,
                 use_cache: bool | None = None, answer_cache: AnswerCache | None = None,
                 **kwargs: Any) -> None:
        if agent_llm is None:
            super().__init__(retriever, llm, *args, **kwargs)
        else:
            super().__init__(retriever, llm, agent_llm, *args, **kwargs)
        self._prod_retriever = retriever
        self.prod_counter: Counter = Counter()
        self.answer_cache: AnswerCache | None = answer_cache
        enabled = settings.answer_cache_enabled if use_cache is None else use_cache
        if self.answer_cache is None and enabled:
            try:
                self.answer_cache = AnswerCache()
            except Exception as e:                    # no database: the app still answers
                log.warning("answer cache disabled: %s", e)
                self.answer_cache = None

    # ------------------------------------------------------------------ main entry
    def answer(self, query: str, force_agent: bool = False, debug: bool = False,
               retriever: Any = None, instructions: str = "",
               prompt_id: Any = None, kb_slugs: list[str] | None = None,
               is_followup: bool = False, history: list[dict] | None = None,
               **kwargs: Any) -> RouterResult:
        t0 = time.perf_counter()
        rtr = retriever or self._prod_retriever
        followup = bool(is_followup) or CLARIFY_JOIN in (query or "")

        # 0) conversation context: a follow-up becomes a standalone question
        typed, rewrite_meter = query, UsageMeter()
        query, method = contextualize(query, history or [], getattr(self.agent, "llm", None),
                                      rewrite_meter)
        if method:
            self.prod_counter[f"rewrite:{method}"] += 1
        cache = self.answer_cache
        scope, qvec = None, None
        if cache is not None:
            try:
                rtr.maybe_reload()                  # index version must be current
                scope = make_scope(rtr, kb_slugs, prompt_id, instructions)
                qvec = query_vector(rtr, query)
            except Exception as e:
                log.warning("answer cache skipped for this question: %s", e)
                scope = None

        # 1) cache lookup
        if scope is not None and not (force_agent or debug or followup):
            try:
                hit = cache.lookup(query, qvec, scope)
            except Exception as e:
                log.warning("answer cache lookup failed: %s", e)
                hit = None
            if hit:
                self.prod_counter["cache_hits"] += 1
                d = self._hit_result(hit, t0)
                self._add_rewrite(d, typed, query, method, rewrite_meter)
                return RouterResult(d)

        # 2) free heuristic pre-route (never overrides an explicit /chat/agent, and never
        #    takes a question away from router.py's dedicated paths)
        reason = None if force_agent or self._has_own_path(query) else heuristic_preroute(query)
        if reason:
            self.prod_counter["preroute_agent"] += 1
        call = dict(force_agent=force_agent or bool(reason), debug=debug,
                    instructions=instructions, **kwargs)
        if retriever is not None:
            call["retriever"] = retriever

        # 3) the unchanged router (fast path -> signals -> agent)
        with veto_scope() as vetoes:
            res = super().answer(query, **call)
        d = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        if reason:
            d["preroute"] = reason
            d["escalation"] = reason[:64]

        self._add_rewrite(d, typed, query, method, rewrite_meter)

        # 4) store
        if scope is not None:
            why = uncacheable_reason(d, query, vetoes, followup)
            if why is None:
                try:
                    cache.store(query, qvec, scope, d)
                    d["cache"] = {"hit": False, "stored": True}
                except Exception as e:
                    log.warning("answer cache store failed: %s", e)
                    d["cache"] = {"hit": False, "stored": False, "reason": "store error"}
            else:
                cache.stats_counter["not_stored"] += 1
                d["cache"] = {"hit": False, "stored": False, "reason": why}
        return RouterResult(d, res)

    # ------------------------------------------------------------------ helpers
    def _has_own_path(self, q: str) -> bool:
        """router.py answers these better than the agent: i(deterministic
        table), catalog listing, a named document / page, part codes (exact table lookup)."""

        for name in ("is_catalog_question", "detect_codes"):
            fn = getattr(self, name, None)
            if fn is not None and fn(q):
                return True
        detect = getattr(self, "detect_doc_page", None)
        return bool(detect and detect(q)[0])

    @staticmethod
    def _add_rewrite(d: dict, typed: str, standalone: str, method: str,
                     meter: UsageMeter) -> None:
        """Report the rewrite and add its tokens to the answer's usage (quotas, dashboard)."""
        if not method:
            return
        d["standalone_query"] = standalone
        d["rewrite"] = {"method": method, "typed": typed}
        if meter.calls:
            u = dict(d.get("usage") or {})
            extra = meter.as_dict()
            for k in ("llm_calls", "input_tokens", "output_tokens"):
                u[k] = int(u.get(k) or 0) + int(extra.get(k) or 0)
            for k in ("llm_seconds", "est_cost_usd"):
                u[k] = round(float(u.get(k) or 0) + float(extra.get(k) or 0), 6)
            stages = dict(u.get("by_stage") or {})
            stages["rewrite"] = stages.get("rewrite", 0) + meter.calls
            u["by_stage"] = stages
            d["usage"] = u

    def _hit_result(self, hit: dict, t0: float) -> dict:
        d = dict(hit["result"])
        d.update(route="cache",
                 escalation="exact" if hit["exact"] else f"semantic {hit['similarity']:.3f}",
                 clarify=False, transcript=None, agent_steps=0, usage=dict(_ZERO_USAGE),
                 timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
                 cache={"hit": True, "exact": hit["exact"], "similarity": hit["similarity"],
                        "cached_question": hit["cached_question"],
                        "cached_at": hit["cached_at"]})
        d.pop("preroute", None)
        return d

    def stats(self) -> dict:
        try:
            base = super().stats()
        except Exception:
            base = {}
        base = dict(base or {})
        from app.reranker import get_reranker
        base["production"] = {
            "version": "Relant App Production 1.0",
            "preroute_agent": self.prod_counter["preroute_agent"],
            "cache_hits": self.prod_counter["cache_hits"],
            "answer_cache": self.answer_cache.stats() if self.answer_cache else "disabled",
            "reranker": get_reranker().status(),
        }
        return base
