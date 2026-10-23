"""Offline tests: fake embeddings + scripted fake LLM. No API key, no credits spent.

    python -m pytest -q tests/test_offline.py        (or:  python tests/test_offline.py)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="relant_rag_test_"))
os.environ.update({
    # tests never read the project's .env: every path is given here
    "RAG_ENV_FILE": str(TMP / "tests.env"), "RAG_BASE_DIR": str(TMP),
    "RAG_DATA_DIR": str(TMP / "data"), "RAG_DB_DIR": str(TMP / "index"), "RAG_KB_DIR": str(TMP / "kb"),
    "RAG_STATIC_DIR": str(ROOT / "app" / "static"),
    "RAG_DATASHEET_LOGO": str(ROOT / "app" / "static" / "logo_relant.png"),
    "RAG_DIR_PRODUCTOS": "productos", "RAG_DIR_PRECIOS": "precios_existencias",
    "RAG_DIR_FOTOS": "drawings/fotos", "RAG_DIR_EXPLOSIONADOS": "drawings/explosionados",
    "RAG_DIR_DRAWINGS": "drawings", "RAG_DIR_HOJAS_TECNICAS": "hojas_tecnicas",
    "RAG_DIR_MANUALES": "manuales", "RAG_DIR_CATALOGOS": "catalogos",
    "RAG_DIR_INVOICES": "invoices",
    "DESCRIBE_IMAGES": "false", "WEB_ENABLED": "false", "AUTO_SYNC_ON_START": "false",
    "PREFLIGHT": "false", "ADMIN_TOKEN": "secret", "RELOAD_CHECK_SECS": "0",
    "OPENROUTER_API_KEY_RELANT": "sk-or-test-not-used",
    # application DB: SQLite file by default; run against MySQL with e.g.
    # TEST_DATABASE_URL=mysql+pymysql://relant:pw@127.0.0.1/relant_test?charset=utf8mb4
    "DATABASE_URL": os.getenv("TEST_DATABASE_URL", f"sqlite:///{(TMP / 'app.db').as_posix()}"),
})
for _k in ("RAG_PDF_DIR", "RAG_INDEX_DIR"):        # legacy names must not leak in
    os.environ.pop(_k, None)


def reset_database() -> None:
    """Empty application DB (tests may run against a real MySQL test schema)."""
    from app import models  # noqa: F401
    from app.db import Base, get_engine, init_db
    Base.metadata.drop_all(get_engine())
    init_db()


def make_user(username: str, role: str = "usuario", password: str = "clave1234") -> None:
    from app.db import session_scope
    from app.models import Role, User
    from app.security import hash_password
    with session_scope() as s:
        r = s.query(Role).filter_by(name=role).one()
        s.add(User(username=username, full_name=username.title(), password_hash=hash_password(password),
                   role_id=r.id, preferences={}))


def login(client, username: str, password: str = "clave1234") -> None:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

from app.prompts import ANSWER_SYSTEM, FAST_SYSTEM, INSUFFICIENT, TOOL_SYSTEM  # noqa: E402


# ------------------------------------------------------------------ fixtures
def make_data() -> None:
    d = TMP / "data"
    (d / "abnox").mkdir(parents=True, exist_ok=True)
    (d / "drawings" / "abnox").mkdir(parents=True, exist_ok=True)   # RAG_DIR_DRAWINGS
    (d / "abnox" / "Manual_ABNOX_VDV-25.txt").write_text(
        "ABNOX VDV-25 operating instructions\n\nThe VDV-25 metering valve works with a "
        "working pressure of 6 to 250 bar. Connection thread G 1/4. Seals: NBR.\n\n"
        "Maintenance: replace the O-ring every 2000 hours.", encoding="utf-8")
    (d / "abnox" / "Datasheet_MMKD-40.txt").write_text(
        "ABNOX MMKD-40 datasheet\n\nMaximum flow 40 l/min, weight 3.2 kg, viscosity up to "
        "NLGI 2 grease.", encoding="utf-8")
    (d / "stock.csv").write_text(
        "Article;Description;Stock;Price\n123456;O-ring 10x2 NBR VDV-25;14;3.50\n"
        "654321;Piston MMKD-40;2;120.00\n777888;Spring kit;0;8.90\n", encoding="utf-8")
    # tiny valid PNG + sidecar notes (drawings indexed by name/notes; vision disabled)
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
    (d / "drawings" / "abnox" / "VDV-25_exploded_view.png").write_bytes(png)


class FakeLLM:
    """Answers by system prompt. Agent decisions come from a per-question script."""

    def __init__(self) -> None:
        self.fast_insufficient: set[str] = set()
        self.scripts: dict[str, list[dict]] = {}
        self.calls = 0
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            self.calls += 1
        system, user = messages[0].content, messages[-1].content
        q = user.rsplit("Question: ", 1)[-1].split("\n")[0].strip()
        usage = {"input_tokens": len(user) // 4, "output_tokens": 20}
        if system == FAST_SYSTEM:
            if q in self.fast_insufficient:
                return SimpleNamespace(content=INSUFFICIENT, usage_metadata=usage)
            ids = [ln[1:].split("]")[0] for ln in user.splitlines() if ln.startswith("[")]
            return SimpleNamespace(content=f"Fast answer for {q} [{ids[0] if ids else '-'}]",
                                   usage_metadata=usage)
        if system == TOOL_SYSTEM:
            n_steps = user.count("[decide]")
            script = self.scripts.get(q, [{"tool": "answer", "args": {"text": f"Agent: {q}"}}])
            step = script[min(n_steps, len(script) - 1)]
            return SimpleNamespace(content=json.dumps(step), usage_metadata=usage)
        if system == ANSWER_SYSTEM:
            return SimpleNamespace(content=f"Synthesized answer for {q}", usage_metadata=usage)
        raise AssertionError("unexpected system prompt")


def new_retriever():
    from app.retriever import HybridEvaluationRetriever
    return HybridEvaluationRetriever(embeddings=DeterministicFakeEmbedding(size=64))


# ------------------------------------------------------------------ tests
def run_all() -> None:
    from app.router import QueryRouter
    reset_database()
    make_data()
    r = new_retriever()
    sync = r.update_index()
    assert sync["new"] == 4 and sync["complete"], sync
    st = r.stats()
    assert st["files"] == 4 and st["chunks_by_kind"]["table"] == 4, st
    print("index ok:", st["files"], "files,", st["chunks"], "chunks")

    # second sync: nothing new, nothing paid
    sync2 = r.update_index()
    assert sync2["new"] == 0 and sync2["api_texts"] == 0, sync2

    llm = FakeLLM()
    router = QueryRouter(r, llm)

    # 1) code -> table_lookup first, one LLM call
    res = router.answer("¿Cuál es el stock del artículo 123456?")
    assert res.route == "fast+table", res
    assert res.usage["llm_calls"] == 1
    assert any(s["id"] == "stock.csv_row1" for s in res.sources), res.sources
    print("fast+table ok:", res.answer[:60], res.usage)

    # 2) technical question -> fast path (VDV-25 also appears in a stock row -> +table)
    res = router.answer("working pressure of the VDV-25 valve")
    assert res.route in ("fast", "fast+table") and res.usage["llm_calls"] == 1, res
    res = router.answer("maximum flow and weight of the pump datasheet")
    assert res.route == "fast" and res.usage["llm_calls"] == 1, res
    res = router.answer("working pressure of the VDV-25 valve")
    assert res.drawings and res.drawings[0]["source"].endswith("VDV-25_exploded_view.png")
    print("fast ok, drawing:", res.drawings[0]["source"])

    # 3) catalog question -> agent with list_documents
    q = "¿Qué manuales tenemos de Abnox?"
    llm.scripts[q] = [{"tool": "list_documents", "args": {"manufacturer": "abnox", "doc_type": "manual"}},
                      {"tool": "answer", "args": {"text": "Manual_ABNOX_VDV-25.txt"}}]
    res = router.answer(q, debug=True)
    assert res.route == "agent" and res.escalation == "catalog", res
    assert res.agent_steps == 1 and "list_documents" in res.transcript[1], res.transcript
    print("catalog -> agent ok:", res.usage["llm_calls"], "calls")

    # 4) fast LLM says INSUFFICIENT -> agent, seeded with the fast-path search
    q = "maintenance interval of the VDV-25"
    llm.fast_insufficient.add(q)
    llm.scripts[q] = [{"tool": "semantic_search", "args": {"query": q}},
                      {"tool": "read_document", "args": {"doc": "Manual_ABNOX_VDV-25"}},
                      {"tool": "answer", "args": {"text": "Every 2000 hours [abnox/Manual_ABNOX_VDV-25.txt]"}}]
    res = router.answer(q, debug=True)
    assert res.route == "agent" and res.escalation == "insufficient_context", res
    assert any("repeated call skipped" in t for t in res.transcript), "seed not used"
    assert any(s["cited"] for s in res.sources), res.sources
    print("insufficient -> agent ok (repeat skipped thanks to seed)")

    # 5) unknown code -> escalate
    res = router.answer("price of ZZX-9999")
    assert res.route == "agent" and res.escalation == "code_not_found", res
    print("code_not_found -> agent ok")

    # 6) clarify is terminal and returns a question
    q = "the valve"
    llm.fast_insufficient.add(q)
    llm.scripts[q] = [{"tool": "clarify", "args": {"question": "¿Qué modelo de válvula?"}}]
    res = router.answer(q)
    assert res.clarify and res.route == "clarify" and res.answer.startswith("¿"), res
    print("clarify ok")

    # 7) concurrency: queries never mix sources, and run while indexing
    errors: list[str] = []
    queries = {"stock of 654321": "stock.csv_row2", "stock of 777888": "stock.csv_row3",
               "stock of 123456": "stock.csv_row1"}

    def worker(q: str, expect: str) -> None:
        for _ in range(10):
            out = router.answer(q)
            ids = {s["id"] for s in out.sources if s["kind"] == "table" and "_row" in s["id"]}
            if expect not in ids:
                errors.append(f"{q}: missing {expect} in {ids}")
            others = {v for v in queries.values() if v != expect} & {s["id"] for s in out.sources if s.get("cited")}
            if others:
                errors.append(f"{q}: cited other request's rows {others}")

    (TMP / "data" / "new_note.txt").write_text("Lubtec pump LP-7 note.", encoding="utf-8")
    threads = [threading.Thread(target=worker, args=(q, e)) for q, e in queries.items() for _ in range(3)]
    threads.append(threading.Thread(target=r.update_index))
    t0 = time.time()
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors[:5]
    assert r.stats()["files"] == 5
    print(f"concurrency ok: {len(threads) - 1} threads x 10 queries + 1 indexing in "
          f"{time.time() - t0:.1f}s")

    # 8) another process/worker notices the new index through maybe_reload()
    r2 = new_retriever()
    r2.load()
    (TMP / "data" / "another.txt").write_text("Walther coupling WK-3.", encoding="utf-8")
    time.sleep(0.05)
    r.update_index()
    r2.maybe_reload()
    for _ in range(50):
        if r2.stats()["files"] == 6:
            break
        time.sleep(0.05)
    assert r2.stats()["files"] == 6, r2.stats()
    print("multi-worker reload ok")

    # 9) API
    from fastapi.testclient import TestClient
    from app.jobs import IndexJobManager
    from app.main import create_app
    comps = {"retriever": r, "router": router, "jobs": IndexJobManager(r, run_preflight=False)}
    make_user("admin1", "admin")
    with TestClient(create_app(comps, auto_sync=False, web_warmup=False)) as c:
        assert c.post("/chat", json={"query": "stock of 123456"}).status_code == 401
        assert c.get("/files/stock.csv").status_code == 401
        login(c, "admin1")
        assert c.get("/health").json()["index"]["files"] == 6
        body = c.post("/chat", json={"query": "stock of 123456"}).json()
        assert body["route"] == "fast+table", body
        docs = c.get("/documents", params={"manufacturer": "abnox"}).json()
        assert docs["total"] >= 3, docs
        assert c.get("/files/stock.csv").status_code == 200
        assert c.get("/files/../../etc/passwd").status_code == 404
        c.post("/auth/logout")
        assert c.post("/admin/index", json={"mode": "update"}).status_code == 403
        started = c.post("/admin/index", json={"mode": "update"},
                         headers={"X-Admin-Token": "secret"}).json()
        assert started["started"], started
        for _ in range(100):
            s = c.get("/admin/index", headers={"X-Admin-Token": "secret"}).json()
            if s["state"] != "running":
                break
            time.sleep(0.05)
        assert s["state"] == "done", s
        login(c, "admin1")
        print("API ok; router stats:", c.get("/health").json()["router"])


def test_offline() -> None:
    try:
        run_all()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    test_offline()
    print("\nALL OFFLINE TESTS PASSED")
