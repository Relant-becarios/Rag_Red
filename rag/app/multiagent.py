"""Multi-agent quoting: a SUPERVISOR coordinates three specialist agents.

                     ┌──────────────────────────────────────────────┐
                     │                 supervisor                   │
                     │  (decides who works next; LLM only where a   │
                     │   judgment is needed, rules everywhere else) │
                     └───┬──────────────┬──────────────────┬────────┘
              "quote"    │   "review"   │  (parallel)      │ "finish" / "clarify"
                         ▼              ▼                  ▼
                 quoting_agent   margin_agent ║ verifier_agent      finish -> END
                         │              │                  │
                         └──────────────┴──── back to supervisor

* quoting_agent   LLM reads the customer request -> list of (code, qty); prices come from the
                  CSV tables in Python (never from the LLM).
* margin_agent    pure Python: cost vs price per line, flags lines below the minimum margin.
* verifier_agent  re-reads every cited table row, recomputes the arithmetic (Python), and
                  asks the LLM one question: "does this quote cover what the customer asked?"
* margin_agent and verifier_agent run IN PARALLEL (same LangGraph super-step).

Design rules worth remembering:
1. Numbers are computed with Decimal in Python. LLMs extract and judge; they don't do math.
2. Every agent writes ONLY its own key of the shared state, so parallel branches never clash.
   The one key both write ("log") has a reducer (operator.add) that merges their lists.
3. The supervisor's LLM choice is validated against the allowed transitions; a bad or
   unparsable answer falls back to a rule. Hard limits (max revisions, max steps) always win.
4. The result is a DRAFT. A person approves a quote before it reaches a customer.
"""
from __future__ import annotations

import json
import logging
import operator
import re
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.config import settings
from app.textutils import mkey, norm, parse_json
from app.tools import RequestContext, new_context

log = logging.getLogger("rag.multiagent")

# ---- settings (QUOTE_* in the .env) ------------------------------------------------------
MIN_MARGIN = Decimal(str(getattr(settings, "quote_min_margin", 0.25)))   # 25 %
MAX_REVISIONS = int(getattr(settings, "quote_max_revisions", 2))
MAX_STEPS = int(getattr(settings, "quote_max_steps", 10))
PRICE_COLUMN = str(getattr(settings, "quote_price_column", ""))   # exact header, optional
COST_COLUMN = str(getattr(settings, "quote_cost_column", ""))
PRICE_TOLERANCE = Decimal("0.01")
QUOTE_SECTIONS = ("precios", "productos")      # folders RAG_DIR_PRECIOS, RAG_DIR_PRODUCTOS

_PRICE_WORDS = ("precio venta", "precio de venta", "precio lista", "precio unitario", "precio",
                "list price", "unit price", "sales price", "price", "pvp", "verkaufspreis")
_COST_WORDS = ("costo", "coste", "cost", "precio compra", "precio de compra",
               "purchase price", "einkaufspreis", "ek preis")
CENT = Decimal("0.01")


# =========================================================================================
# Prompts (kept here so the file reads top to bottom; move them to prompts.py if you like)
# =========================================================================================
SUPERVISOR_SYSTEM = (
    "You coordinate a team that prepares spare-parts quotes for a distributor of Abnox, "
    "Schütze, Soma, Lubtec, Walther Systemtechnik and relant . Agents you can call:\n"
    "- quote: builds or rebuilds the quote from the customer request (give it feedback).\n"
    "- review: runs the margin checker and the source/correctness verifier in parallel.\n"
    "- clarify: the request cannot be quoted without asking the customer something.\n"
    "- finish: the quote is ready for a human to approve (problems may remain; they will be "
    "shown to the human).\n"
    "Only the options listed under 'Allowed now' are valid. Rebuild the quote only when the "
    "review found something the quoting agent can fix (wrong code, missing item, wrong "
    "quantity). Margin problems are NOT fixed by rebuilding: finish and let a human decide.\n"
    'Respond with ONLY JSON: {"next": "<option>", "reason": "...", "feedback": "...", '
    '"question": "..."}  (feedback for quote, question for clarify).'
)

EXTRACT_SYSTEM = (
    "Extract the items a customer wants quoted. For every item return the article/part "
    "code exactly as written (empty string if none), a short description and the quantity "
    "(1 if not stated). Do not invent codes. If reviewer feedback is given, apply it.\n"
    'Respond with ONLY JSON: {"items": [{"code": "...", "description": "...", "qty": 1}]}'
)

COVERAGE_SYSTEM = (
    "You check a draft quote against the customer request. List requested items that are "
    "missing from the quote, quote lines the customer did not ask for, and quantities that "
    "differ from the request. Be strict and literal; do not comment on prices.\n"
    'Respond with ONLY JSON: {"missing": ["..."], "extra": ["..."], '
    '"qty_mismatch": ["..."], "ok": true|false}'
)


# =========================================================================================
# Helpers: money and table rows (deterministic, no LLM)
# =========================================================================================
def parse_money(raw: Any) -> Decimal | None:
    """'$1,234.50' / '1.234,50 €' / '980' -> Decimal. None if it is not a number."""
    s = re.sub(r"[^\d,.\-]", "", str(raw or ""))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:                       # the last separator is the decimal one
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:                                  # '1,234' thousands vs '12,5' decimal
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _find_col(headers: list[str], explicit: str, words: tuple[str, ...],
              exclude: int | None = None) -> int | None:
    if explicit and explicit in headers:
        return headers.index(explicit)
    keys = [mkey(h) for h in headers]
    for w in words:                                 # word order = priority
        wk = mkey(w)
        for i, k in enumerate(keys):
            if i != exclude and wk and wk in k:
                return i
    return None


def price_and_cost_cols(headers: list[str]) -> tuple[int | None, int | None]:
    cost = _find_col(headers, COST_COLUMN, _COST_WORDS)
    price = _find_col(headers, PRICE_COLUMN, _PRICE_WORDS, exclude=cost)
    return price, cost


def find_rows(retriever: Any, code: str) -> list[dict]:
    """EXACT matches of a code in any cell of any CSV table. A quote must not be built on
    a partial match, so 'code in cell' / 'partial' matches from table_lookup are ignored."""
    nc = norm(code)
    if len(nc) < 2:
        return []
    out = []
    tables = [e for e in retriever.snapshot.catalog.values()
              if e["type"] == "table" and e.get("section") != ""]
    # Prices come first from the folders declared in the .env (RAG_DIR_PRECIOS, then
    # RAG_DIR_PRODUCTOS); other CSVs are used only for a code found in neither. The
    groups = [[e for e in tables if e.get("section") == s] for s in QUOTE_SECTIONS]
    groups.append([e for e in tables if e.get("section") not in QUOTE_SECTIONS])
    for group in groups:
        for e in sorted(group, key=lambda x: x["rel"]):
            try:
                headers, rows, normed = retriever._table_rows(e["path"])
            except Exception as ex:
                log.warning("cannot read %s: %s", e["rel"], ex)
                continue
            pcol, ccol = price_and_cost_cols(headers)
            for i, cells in enumerate(normed):
                if nc in cells:
                    step = settings.csv_rows_per_chunk
                    out.append({"rel": e["rel"], "row": i, "headers": headers,
                                "values": rows[i], "price_col": pcol, "cost_col": ccol,
                                "source_id": f"{e['rel']}_row{(i // step) * step + 1}"})
        if any(r["price_col"] is not None for r in out):
            break                       # found with a price in a preferred folder
    out.sort(key=lambda r: r["price_col"] is None)  # rows with a price first
    return out


def read_row(retriever: Any, rel: str, row: int) -> tuple[list[str], list[str]] | None:
    e = retriever.snapshot.catalog.get(rel)
    if not e:
        return None
    headers, rows, _ = retriever._table_rows(e["path"])
    return (headers, rows[row]) if 0 <= row < len(rows) else None


def _q(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _ask_json(ctx: RequestContext, llm: Any, system: str, user: str, stage: str,
              default: dict) -> dict:
    """One LLM call that must return a JSON object; any parse problem -> default."""
    raw = ctx.meter.invoke(llm, [SystemMessage(content=system), HumanMessage(content=user)],
                           stage=stage)
    try:
        d = parse_json(raw)
        return d if isinstance(d, dict) else default
    except (json.JSONDecodeError, ValueError):
        log.warning("%s: unparsable JSON, using default", stage)
        return default


# =========================================================================================
# Shared state
# =========================================================================================
class QuoteState(TypedDict):
    request: str
    quote: dict | None            # written only by quoting_agent
    margin: dict | None           # written only by margin_agent
    verification: dict | None     # written only by verifier_agent
    decision: dict                # written only by supervisor
    revisions: int
    steps: int
    log: Annotated[list[str], operator.add]   # reducer: parallel writes are concatenated
    final: dict


def _ctx(config: RunnableConfig) -> RequestContext:
    return config["configurable"]["ctx"]


def _rtr(config: RunnableConfig, default: Any) -> Any:
    """The knowledge bases selected for THIS request (multi-RAG), else the default index."""
    return _ctx(config).retriever or default


# =========================================================================================
# The coordinator
# =========================================================================================
class QuoteCoordinator:
    def __init__(self, retriever: Any, llm: Any, supervisor_llm: Any | None = None) -> None:
        self.retriever = retriever
        self.llm = llm                          # workers (cheap model is fine)
        self.supervisor_llm = supervisor_llm or llm
        self.app = self._build_graph()          # compiled once, shared by all requests

    # ------------------------------------------------------------------ supervisor
    @staticmethod
    def _allowed(state: QuoteState) -> list[str]:
        if state["steps"] >= MAX_STEPS:
            return ["finish"]
        if state["quote"] is None:
            return ["quote", "clarify"]
        if state["margin"] is None or state["verification"] is None:
            return ["review"]                   # every new quote is reviewed: no LLM needed
        opts = ["finish", "clarify"]
        if state["revisions"] < MAX_REVISIONS and state["verification"]["issues"]:
            opts.insert(0, "quote")
        return opts

    def _summary(self, state: QuoteState) -> str:
        parts = [f"Customer request:\n{state['request']}"]
        q = state["quote"]
        if q:
            lines = "\n".join(f"- {l['code'] or '?'} x{l['qty']} {l['status']} "
                              f"unit={l['unit_price']} [{l['source_id']}]" for l in q["lines"])
            parts.append(f"Draft quote (revision {state['revisions']}):\n{lines}\n"
                         f"Total: {q['total']}")
        if state["verification"]:
            parts.append("Verifier issues:\n" + ("\n".join(
                f"- [{i['severity']}] {i['msg']}" for i in state["verification"]["issues"])
                or "(none)"))
        if state["margin"]:
            parts.append(f"Margin: {state['margin']['summary']}")
        return "\n\n".join(parts)

    def _supervisor(self, state: QuoteState, config: RunnableConfig) -> dict:
        allowed = self._allowed(state)
        if len(allowed) == 1:                   # nothing to decide -> no tokens spent
            d = {"next": allowed[0], "reason": "rule"}
        else:
            d = _ask_json(_ctx(config), self.supervisor_llm, SUPERVISOR_SYSTEM,
                          f"{self._summary(state)}\n\nAllowed now: {', '.join(allowed)}",
                          "supervisor", {"next": "", "reason": "parse-fail"})
            if d.get("next") not in allowed:    # guardrail: never trust the choice blindly
                fallback = "quote" if state["quote"] is None else "finish"
                d = {"next": fallback, "reason": f"invalid choice {d.get('next')!r} -> rule"}
        return {"decision": d, "steps": state["steps"] + 1,
                "log": [f"[supervisor] -> {d['next']} ({str(d.get('reason', ''))[:80]})"]}

    def _route(self, state: QuoteState) -> str | list[str]:
        nxt = state["decision"]["next"]
        if nxt == "review":
            return ["margin_agent", "verifier_agent"]     # a list = run in parallel
        return {"quote": "quoting_agent"}.get(nxt, "finish")

    # ------------------------------------------------------------------ worker 1: quoting
    def _quoting_agent(self, state: QuoteState, config: RunnableConfig) -> dict:
        ctx = _ctx(config)
        retriever = _rtr(config, self.retriever)
        feedback = str(state["decision"].get("feedback") or "")
        user = f"Customer request:\n{state['request']}"
        if state["quote"] is not None:
            user += ("\n\nPrevious draft lines: "
                     + json.dumps([{k: l[k] for k in ("code", "description", "qty")}
                                   for l in state["quote"]["lines"]], ensure_ascii=False))
        if feedback:
            user += f"\n\nReviewer feedback to apply: {feedback}"
        items = _ask_json(ctx, self.llm, EXTRACT_SYSTEM, user, "quote_extract",
                          {"items": []}).get("items") or []

        lines, total = [], Decimal(0)
        for it in items if isinstance(items, list) else []:
            code = str(it.get("code") or "").strip()
            try:
                qty = max(1, int(Decimal(str(it.get("qty") or 1))))
            except (InvalidOperation, ValueError):
                qty = 1
            line = {"code": code, "description": str(it.get("description") or ""), "qty": qty,
                    "unit_price": None, "line_total": None, "source_id": "", "row_ref": None,
                    "status": "not_found", "alternatives": []}
            rows = find_rows(retriever, code) if code else []
            if rows:
                r = rows[0]
                price = parse_money(r["values"][r["price_col"]]) if r["price_col"] is not None else None
                line.update(source_id=r["source_id"], row_ref=[r["rel"], r["row"]],
                            alternatives=[x["source_id"] for x in rows[1:4]])
                if price is None:
                    line["status"] = "no_price"
                else:
                    lt = _q(price * qty)
                    line.update(unit_price=str(_q(price)), line_total=str(lt), status="ok")
                    total += lt
                if r["source_id"]:
                    ctx.hits.append({"id": r["source_id"], "source": r["rel"], "kind": "table",
                                     "text": " | ".join(f"{h}: {v}" for h, v in
                                                        zip(r["headers"], r["values"]) if v),
                                     "path": ""})
            elif line["description"]:           # no code: show candidates to the human
                docs = retriever.retrieve(line["description"], top_k=3, kinds={"table"},
                                               hits=ctx.hits)
                line["alternatives"] = [d["id"] for d in docs]
            lines.append(line)

        rev = state["revisions"] + (state["quote"] is not None)   # 0 = first draft
        quote = {"lines": lines, "total": str(_q(total)), "revision": rev}
        ok = sum(l["status"] == "ok" for l in lines)
        # A new quote invalidates the old reviews -> the supervisor will ask for a new one.
        return {"quote": quote, "margin": None, "verification": None, "revisions": rev,
                "log": [f"[quoting] {len(lines)} line(s), {ok} priced, total {quote['total']}"]}

    # ------------------------------------------------------------------ worker 2: margin
    def _margin_agent(self, state: QuoteState, config: RunnableConfig) -> dict:
        t0 = time.perf_counter()
        retriever = _rtr(config, self.retriever)
        report, revenue, cost_sum, unknown = [], Decimal(0), Decimal(0), 0
        for l in state["quote"]["lines"]:
            if l["status"] != "ok":
                continue
            got = read_row(retriever, *l["row_ref"])
            cost = None
            if got:
                headers, values = got
                _, ccol = price_and_cost_cols(headers)
                cost = parse_money(values[ccol]) if ccol is not None else None
            price = Decimal(l["unit_price"])
            if cost is None:
                unknown += 1
                report.append({"code": l["code"], "margin": None, "flag": "no_cost_data"})
                continue
            m = (price - cost) / price if price else Decimal(0)
            revenue += price * l["qty"]
            cost_sum += cost * l["qty"]
            report.append({"code": l["code"], "unit_cost": str(_q(cost)),
                           "margin": f"{m:.1%}",
                           "flag": "loss" if m < 0 else ("below_min" if m < MIN_MARGIN else "")})
        overall = (revenue - cost_sum) / revenue if revenue else None
        below = [r["code"] for r in report if r["flag"] in ("below_min", "loss")]
        summary = (f"overall {overall:.1%}" if overall is not None else "overall n/a") + \
                  f"; minimum {MIN_MARGIN:.0%}; below minimum: {', '.join(below) or 'none'}" + \
                  (f"; {unknown} line(s) without cost data" if unknown else "")
        dt = (time.perf_counter() - t0) * 1000
        return {"margin": {"lines": report, "overall": f"{overall:.1%}" if overall is not None
                           else None, "below_min": below, "summary": summary},
                "log": [f"[margin] {summary} ({dt:.0f} ms)"]}

    # ------------------------------------------------------------------ worker 3: verifier
    def _verifier_agent(self, state: QuoteState, config: RunnableConfig) -> dict:
        q, issues = state["quote"], []
        retriever = _rtr(config, self.retriever)

        def add(sev: str, msg: str) -> None:
            issues.append({"severity": sev, "msg": msg})

        # (a) deterministic: every number traced back to its row and recomputed
        total = Decimal(0)
        for l in q["lines"]:
            tag = l["code"] or l["description"][:40]
            if l["status"] == "not_found":
                add("error", f"{tag}: el código no aparece en ninguna tabla"
                    + (f" (candidatos: {', '.join(l['alternatives'])})" if l["alternatives"] else ""))
                continue
            if l["status"] == "no_price":
                add("error", f"{tag}: la fila {l['source_id']} no tiene columna o valor de precio")
                continue
            got = read_row(retriever, *l["row_ref"])
            if not got:
                add("error", f"{tag}: la fila citada {l['source_id']} ya no existe")
                continue
            headers, values = got
            pcol, _ = price_and_cost_cols(headers)
            src_price = parse_money(values[pcol]) if pcol is not None else None
            if src_price is None or abs(_q(src_price) - Decimal(l["unit_price"])) > PRICE_TOLERANCE:
                add("error", f"{tag}: el precio {l['unit_price']} no coincide con la fuente ({src_price})")
            if norm(l["code"]) not in {norm(v) for v in values}:
                add("error", f"{tag}: el código no está en la fila citada {l['source_id']}")
            if _q(Decimal(l["unit_price"]) * l["qty"]) != Decimal(l["line_total"]):
                add("error", f"{tag}: el importe de la partida es incorrecto")
            if l["alternatives"]:
                add("warning", f"{tag}: también aparece en {', '.join(l['alternatives'])}; "
                               "confirma qué lista de precios aplica")
            total += Decimal(l["line_total"])
        if _q(total) != Decimal(q["total"]):
            add("error", f"el total {q['total']} no coincide con la suma de partidas ({_q(total)})")

        # (b) LLM judgment: does the quote match what was asked?
        cov = _ask_json(_ctx(config), self.llm, COVERAGE_SYSTEM,
                        f"Customer request:\n{state['request']}\n\nQuote lines:\n" + "\n".join(
                            f"- {l['code'] or '(no code)'} | {l['description']} | qty {l['qty']}"
                            for l in q["lines"]),
                        "verify_coverage", {"ok": True})
        for key, label in (("missing", "falta en la cotización"), ("extra", "no fue solicitado"),
                           ("qty_mismatch", "la cantidad no coincide")):
            for x in cov.get(key) or []:
                add("error" if key != "extra" else "warning", f"{label}: {x}")
        n_err = sum(i["severity"] == "error" for i in issues)
        return {"verification": {"issues": issues, "ok": n_err == 0},
                "log": [f"[verifier] {n_err} error(s), {len(issues) - n_err} warning(s)"]}

    # ------------------------------------------------------------------ end
    def _finish(self, state: QuoteState, config: RunnableConfig) -> dict:
        d = state["decision"]
        if d.get("next") == "clarify":
            return {"final": {"status": "clarify",
                              "question": d.get("question") or "¿Puede indicar los códigos "
                              "de artículo y las cantidades que necesita?"}}
        q, v, m = state["quote"], state["verification"], state["margin"]
        problems = bool(v and not v["ok"]) or bool(m and m["below_min"]) or v is None
        return {"final": {"status": "needs_review" if problems else "ready_for_approval",
                          "quote": q, "verification": v, "margin": m}}

    # ------------------------------------------------------------------ graph
    def _build_graph(self) -> Any:
        g = StateGraph(QuoteState)
        g.add_node("supervisor", self._supervisor)
        g.add_node("quoting_agent", self._quoting_agent)
        g.add_node("margin_agent", self._margin_agent)
        g.add_node("verifier_agent", self._verifier_agent)
        g.add_node("finish", self._finish)
        g.set_entry_point("supervisor")
        g.add_conditional_edges("supervisor", self._route,
                                ["quoting_agent", "margin_agent", "verifier_agent", "finish"])
        g.add_edge("quoting_agent", "supervisor")
        # Both parallel branches point at the supervisor: LangGraph waits until BOTH have
        # finished their super-step and then runs the supervisor once (fan-in).
        g.add_edge("margin_agent", "supervisor")
        g.add_edge("verifier_agent", "supervisor")
        g.add_edge("finish", END)
        return g.compile()

    def run(self, request: str, debug: bool = False, retriever: Any = None) -> dict:
        """retriever: per-request override (the knowledge bases the user selected)."""
        t0 = time.perf_counter()
        rtr = retriever or self.retriever
        rtr.maybe_reload()
        ctx = new_context(rtr)
        out = self.app.invoke(
            {"request": request.strip(), "quote": None, "margin": None, "verification": None,
             "decision": {}, "revisions": 0, "steps": 0, "log": [], "final": {}},
            config={"recursion_limit": 4 * MAX_STEPS, "configurable": {"ctx": ctx}})
        res = dict(out["final"])
        res.update(usage=ctx.meter.as_dict(),
                   timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
                   log=out["log"] if debug else None)
        log.info("quote status=%s calls=%d steps=%d", res["status"],
                 res["usage"]["llm_calls"], out["steps"])
        return res


# =========================================================================================
# Plain-text summary (stored as the answer in the conversation log / CSV export)
# =========================================================================================
_STATUS_ES = {"ready_for_approval": "Lista para aprobación",
              "needs_review": "Requiere revisión", "clarify": "Falta información"}
_LINE_ES = {"ok": "ok", "not_found": "no encontrado", "no_price": "sin precio"}


def quote_markdown(res: dict) -> str:
    st = res.get("status", "")
    if st == "clarify":
        return res.get("question") or "¿Puede indicar los códigos y cantidades?"
    q = res.get("quote") or {"lines": [], "total": "0.00"}
    out = [f"**Cotización borrador — {_STATUS_ES.get(st, st)}** (debe aprobarla una persona)",
           "", "| Código | Descripción | Cant. | Precio unit. | Importe | Estado | Fuente |",
           "|---|---|---:|---:|---:|---|---|"]
    for ln in q["lines"]:
        out.append(f"| {ln['code'] or '—'} | {ln['description'] or ''} | {ln['qty']} | "
                   f"{ln['unit_price'] or '—'} | {ln['line_total'] or '—'} | "
                   f"{_LINE_ES.get(ln['status'], ln['status'])} | "
                   f"{('[' + ln['source_id'] + ']') if ln['source_id'] else ''} |")
    out += ["", f"**Total:** {q['total']}"]
    m = res.get("margin") or {}
    if m.get("summary"):
        out.append(f"\n**Margen:** {m['summary']}")
    issues = (res.get("verification") or {}).get("issues") or []
    if issues:
        out.append("\n**Observaciones del verificador:**")
        out += [f"- {'Advertencia' if i['severity'] == 'warning' else 'Error'}: {i['msg']}"
                for i in issues]
    return "\n".join(out)
