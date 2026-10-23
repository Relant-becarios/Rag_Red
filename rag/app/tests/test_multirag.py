"""Multi-RAG + roles: knowledge bases per user / role / global, uploads, access control,
prompt profiles per role, token limits, the token dashboard, the multi-agent quote and the
product datasheets. Offline (fake embeddings + scripted fake LLM), SQLite unless
TEST_DATABASE_URL points to MySQL.

    python -m pytest -q app/tests/test_multirag.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as base  # noqa: E402  (sets the environment before app imports)

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

TMP3 = Path(tempfile.mkdtemp(prefix="relant_multirag_test_"))


class TeamLLM(base.FakeLLM):
    """FakeLLM that also understands prompt profiles and the quote multi-agent."""

    def __init__(self) -> None:
        super().__init__()
        self.systems: list[str] = []

    def invoke(self, messages):
        from app.multiagent import COVERAGE_SYSTEM, EXTRACT_SYSTEM, SUPERVISOR_SYSTEM
        from app.prompts import ANSWER_SYSTEM, FAST_SYSTEM, TOOL_SYSTEM
        system, user = messages[0].content, messages[-1].content
        self.systems.append(system)
        usage = {"input_tokens": 100, "output_tokens": 20}
        if system == SUPERVISOR_SYSTEM:
            nxt = "quote" if "Allowed now: quote, clarify" in user else "finish"
            return SimpleNamespace(content=json.dumps({"next": nxt, "reason": "test"}),
                                   usage_metadata=usage)
        if system == EXTRACT_SYSTEM:
            items = [{"code": "123456", "description": "O-ring", "qty": 10},
                     {"code": "VDV-25", "description": "válvula", "qty": 1}]
            return SimpleNamespace(content=json.dumps({"items": items}), usage_metadata=usage)
        if system == COVERAGE_SYSTEM:
            return SimpleNamespace(content=json.dumps({"ok": True}), usage_metadata=usage)
        for b in (FAST_SYSTEM, TOOL_SYSTEM, ANSWER_SYSTEM):
            if system.startswith(b) and system != b:          # a prompt profile is applied
                messages = [SimpleNamespace(content=b), *messages[1:]]
                break
        return super().invoke(messages)


def _setup():
    from app.config import settings
    from app.db import reset_engine
    if not os.getenv("TEST_DATABASE_URL"):
        settings.database_url = f"sqlite:///{(TMP3 / 'app.db').as_posix()}"
    settings.kb_dir = str(TMP3 / "kb")
    reset_engine()
    base.reset_database()
    data = TMP3 / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "Manual_VDV-25.txt").write_text(
        "VDV-25 metering valve. Working pressure 6 to 250 bar.", encoding="utf-8")
    (data / "stock.csv").write_text("Article;Description;Stock\n123456;O-ring VDV-25;14\n",
                                    encoding="utf-8")
    from app.retriever import HybridEvaluationRetriever
    r = HybridEvaluationRetriever(data_dir=str(data), chroma_dir=str(TMP3 / "idx" / "chroma"),
                                  manifest_file=str(TMP3 / "idx" / "manifest.json"),
                                  embeddings=DeterministicFakeEmbedding(size=32))
    r.update_index()
    return r


def wait_job(client, slug: str, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/kb/{slug}/status").json()
        if st["job"].get("state") not in ("running", "queued"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"indexing of {slug} did not finish")


def upload(client, slug: str, name: str, content: bytes, subdir: str = "") -> dict:
    r = client.post(f"/kb/{slug}/files", files=[("files", (name, content, "text/plain"))],
                    data={"subdir": subdir})
    assert r.status_code == 200, r.text
    return r.json()


def run_all() -> None:
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.db import session_scope
    from app.jobs import IndexJobManager
    from app.main import create_app
    from app.models import KnowledgeBase, Message, Role
    from app.multiagent import QuoteCoordinator
    from app.router import QueryRouter

    r = _setup()
    llm = TeamLLM()
    router = QueryRouter(r, llm)
    comps = {"retriever": r, "router": router, "quoter": QuoteCoordinator(r, llm),
             "jobs": IndexJobManager(r, run_preflight=False)}

    # ---- base roles + trade roles + one folder per role, created by init_db
    with session_scope() as s:
        names = {x.name for x in s.query(Role)}
        assert {"admin", "supervisor", "usuario", "importaciones", "exportaciones"} <= names
        kbs = {k.slug: k for k in s.query(KnowledgeBase)}
        assert {"general", "rol-admin", "rol-usuario", "rol-importaciones"} <= set(kbs)
        imp_role = s.query(Role).filter_by(name="importaciones").one()
        assert "agents.quote" in imp_role.permissions and imp_role.default_prompt_id
        assert "agents.quote" not in s.query(Role).filter_by(name="usuario").one().permissions
    for name, role in (("root", "admin"), ("sup", "supervisor"), ("ana", "usuario"),
                       ("beto", "usuario"), ("imp", "importaciones")):
        base.make_user(name, role)
    app = create_app(comps, auto_sync=False, web_warmup=False)

    with TestClient(app) as adm, TestClient(app) as ana, TestClient(app) as beto, \
            TestClient(app) as sup, TestClient(app) as imp:
        for c, n in ((adm, "root"), (ana, "ana"), (beto, "beto"), (sup, "sup"), (imp, "imp")):
            base.login(c, n)

        # ---- the chat picker: general + my role folder + my personal folder
        mine = ana.get("/kb").json()
        slugs = {k["slug"]: k for k in mine["items"]}
        assert set(slugs) == {"general", "rol-usuario", "u-ana"}, slugs
        assert slugs["u-ana"]["can_write"] and not slugs["rol-usuario"]["can_write"]
        assert not slugs["general"]["can_write"]
        assert set(mine["selected"]) == {"general", "rol-usuario", "u-ana"}
        print("kb picker ok:", sorted(slugs))

        # ---- personal upload -> indexed in the background -> searchable only by the owner
        out = upload(ana, "u-ana", "notas_ana.txt",
                     b"ZEPHYR-77 coupling: tightening torque 42 Nm, thread M8.")
        assert out["saved"][0]["path"] == "notas_ana.txt" and out["job"], out
        st = wait_job(ana, "u-ana")
        assert st["job"]["state"] == "done" and st["files"] == 1, st
        files = ana.get("/kb/u-ana/files").json()
        assert files["items"][0]["status"] == "indexed", files
        assert files["items"][0]["rel"] == "@u-ana/notas_ana.txt"

        res = ana.post("/chat", json={"query": "tightening torque of the ZEPHYR-77 coupling",
                                      "kb_ids": ["u-ana"]}).json()
        assert res["kb_slugs"] == ["u-ana"], res
        assert res["sources"] and all(s["source"].startswith("@u-ana/") for s in res["sources"])
        assert res["sources"][0]["url"].startswith("/files/@u-ana/")
        assert res["quota"]["tokens_today"] > 0, res["quota"]
        assert ana.get("/files/@u-ana/notas_ana.txt").status_code == 200
        print("personal base ok:", res["route"], res["sources"][0]["id"])

        # ---- another user of the same role cannot see it
        assert "u-ana" not in {k["slug"] for k in beto.get("/kb").json()["items"]}
        assert beto.post("/chat", json={"query": "ZEPHYR-77", "kb_ids": ["u-ana"]}).status_code == 403
        assert beto.get("/files/@u-ana/notas_ana.txt").status_code == 404
        assert beto.get("/kb/u-ana/files").status_code == 404
        assert beto.post("/kb/u-ana/files", files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 404
        print("isolation between users ok")

        # ---- default selection searches several bases at once (MultiRetriever)
        res = ana.post("/chat", json={"query": "stock of 123456 and ZEPHYR-77 torque"}).json()
        assert set(res["kb_slugs"]) == {"general", "rol-usuario", "u-ana"}, res["kb_slugs"]
        srcs = {s["source"] for s in res["sources"]}
        assert "stock.csv" in srcs and "@u-ana/notas_ana.txt" in srcs, srcs
        docs = ana.get("/documents").json()
        assert {d["kb"] for d in docs["items"]} == {"general", "u-ana"}, docs
        print("multi-base search ok:", sorted(srcs))

        # ---- saved selection (preferences) is honoured; forbidden slugs are ignored
        ana.put("/me/preferences", json={"kbs": ["u-ana", "u-beto"]})
        assert ana.post("/chat", json={"query": "torque ZEPHYR-77"}).json()["kb_slugs"] == ["u-ana"]
        ana.put("/me/preferences", json={"kbs": None})

        # ---- upload rules: type, sanitised names, sub-folders, quota, drawings folder
        bad = upload(ana, "u-ana", "virus.exe", b"MZ")
        assert bad["rejected"] and not bad["saved"]
        trav = upload(ana, "u-ana", "../../evil.txt", b"evil")
        assert trav["saved"][0]["path"] == "evil.txt"
        assert not (TMP3 / "evil.txt").exists() and not (TMP3 / "kb" / "evil.txt").exists()
        sub = upload(ana, "u-ana", "a.txt", b"x", subdir="../fuera")
        assert sub["rejected"] and not sub["saved"], sub
        png = upload(ana, "u-ana", "plano.png", b"\x89PNG fake")
        assert png["saved"][0]["path"] == "drawings/plano.png", png
        old_quota = settings.kb_user_quota_mb
        settings.kb_user_quota_mb = 0.0001                # ~100 bytes
        q = upload(ana, "u-ana", "grande.txt", b"x" * 500)
        assert q["rejected"] and "límite" in q["rejected"][0]["reason"], q
        settings.kb_user_quota_mb = old_quota
        wait_job(ana, "u-ana")
        assert ana.delete("/kb/u-ana/files/evil.txt").json()["ok"]
        assert ana.delete("/kb/u-ana/files/../../app.db").status_code in (400, 404)
        assert (TMP3 / "app.db").exists() or os.getenv("TEST_DATABASE_URL")
        st = wait_job(ana, "u-ana")
        names = {i["path"] for i in ana.get("/kb/u-ana/files").json()["items"]}
        assert "evil.txt" not in names and "notas_ana.txt" in names, names
        print("upload rules ok")

        # ---- role folder: supervisors write theirs; users cannot write the role folder
        upload(sup, "rol-supervisor", "procedimiento.txt", b"Procedimiento de garantia RMA-5")
        wait_job(sup, "rol-supervisor")
        assert "rol-supervisor" not in {k["slug"] for k in ana.get("/kb").json()["items"]}
        assert ana.post("/kb/rol-usuario/files",
                        files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 403
        # the admin adds to any base, including the general one
        upload(adm, "rol-usuario", "politica.txt", b"Politica de devoluciones: 30 dias.")
        wait_job(adm, "rol-usuario")
        assert ana.get("/kb/rol-usuario/files").json()["items"][0]["status"] == "indexed"
        print("role folders ok")

        # ---- admin: a global base only for the import team, with prices for quotes
        roles = {x["name"]: x["id"] for x in adm.get("/admin/kb").json()["roles"]}
        kb = adm.post("/admin/kb", json={"name": "Listas de precios importación",
                                         "slug": "precios-imp", "read_roles": [roles["importaciones"]],
                                         "write_roles": [roles["importaciones"]]}).json()
        assert kb["slug"] == "precios-imp", kb
        assert adm.post("/admin/kb", json={"name": "Hack", "slug": "u-hack"}).status_code == 400
        upload(imp, "precios-imp", "precios.csv",
               "Artículo;Descripción;Costo;Precio lista;Stock\n123456;O-ring 10x2 NBR;40,00;100,00;12\n"
               "VDV-25;Válvula dosificadora VDV-25;2.000,00;2.400,00;3\n".encode("utf-8"))
        upload(imp, "precios-imp", "bombas.csv",
               "Número de parte;Nombre del producto;Presión máx.;Peso\nBP-200;Bomba BP-200;220 bar;3,5 kg\n"
               .encode("utf-8"), subdir="productos")
        st = wait_job(imp, "precios-imp")
        assert st["files"] == 2, st
        assert "precios-imp" in {k["slug"] for k in imp.get("/kb").json()["items"]}
        assert "precios-imp" not in {k["slug"] for k in ana.get("/kb").json()["items"]}
        assert ana.get("/kb/precios-imp/files").status_code == 404
        print("global base per role ok")

        # ---- multi-agent quote: only roles with agents.quote, over the selected bases
        assert ana.post("/quote", json={"query": "cotizar 10 123456"}).status_code == 403
        qres = imp.post("/quote", json={"query": "Cotizar 10 o-rings 123456 y una VDV-25",
                                        "kb_ids": ["precios-imp"], "debug": True}).json()
        assert qres["route"] == "quote", qres
        quote = qres["payload"]["quote"]
        assert [ln["status"] for ln in quote["lines"]] == ["ok", "ok"], quote
        assert quote["total"] == "3400.00", quote
        assert qres["payload"]["margin"]["below_min"] == ["VDV-25"]
        assert qres["escalation"] == "needs_review"
        assert "| 123456 |" in qres["answer"] and qres["usage"]["llm_calls"] >= 3
        assert qres["sources"][0]["url"].startswith("/files/@precios-imp/")
        conv = imp.get(f"/conversations/{qres['conversation_id']}").json()
        assert conv["messages"][1]["payload"]["type"] == "quote"
        print("quote multi-agent ok:", quote["total"], qres["escalation"])

        # ---- product datasheet from a role base
        page = imp.get("/datasheet/BP-200", params={"kb": "precios-imp"})
        assert page.status_code == 200 and "Bomba BP-200" in page.text and "220 bar" in page.text
        assert ana.get("/datasheet/BP-200", params={"kb": "precios-imp"}).status_code == 403
        pdf = imp.get("/datasheet-pdf/BP-200", params={"kb": "precios-imp"})
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-", pdf.text[:200]
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["content-disposition"].startswith("attachment;")
        assert ana.get("/datasheet-pdf/BP-200", params={"kb": "precios-imp"}).status_code == 403
        assert imp.get("/datasheet-pdf/NO-EXISTE", params={"kb": "precios-imp"}).status_code == 404
        prods = imp.get("/products", params={"q": "BP-200", "kb": "precios-imp"}).json()
        assert prods["total"] == 1
        assert adm.get("/admin/data-report", params={"kb": "precios-imp"}).json()["products"] == 1
        assert ana.get("/admin/data-report").status_code == 403
        assert adm.get("/graph.json", params={"kb": "precios-imp"}).status_code == 200
        print("datasheets ok")

        # ---- prompt profiles per role
        pr = adm.post("/admin/prompts", json={
            "name": "Pirata", "instructions": "Responde como un pirata amable (MARCA-PIRATA).",
            "role_ids": [roles["usuario"]]}).json()
        assert any(p["id"] == pr["id"] for p in ana.get("/prompts").json()["items"])
        assert not any(p["id"] == pr["id"] for p in sup.get("/prompts").json()["items"])
        llm.systems.clear()
        res = ana.post("/chat", json={"query": "working pressure of the VDV-25",
                                      "prompt_id": pr["id"]}).json()
        assert res["prompt_profile_id"] == pr["id"]
        assert any("MARCA-PIRATA" in s for s in llm.systems), llm.systems
        assert sup.post("/chat", json={"query": "x", "prompt_id": pr["id"]}).status_code == 403
        adm.put("/admin/prompts/role-default", json={"role_id": roles["usuario"], "prompt_id": pr["id"]})
        llm.systems.clear()
        res = beto.post("/chat", json={"query": "working pressure of the VDV-25"}).json()
        assert res["prompt_profile_id"] == pr["id"] and any("MARCA-PIRATA" in s for s in llm.systems)
        res = beto.post("/chat", json={"query": "working pressure of the VDV-25", "prompt_id": 0}).json()
        assert res["prompt_profile_id"] is None
        imp_prompts = imp.get("/prompts").json()
        assert imp_prompts["role_default"] and imp_prompts["selected"] == imp_prompts["role_default"]
        print("prompt profiles per role ok")

        # ---- token limits: user override, role limit, global limit
        ids = {u["username"]: u["id"] for u in adm.get("/admin/limits").json()["users"]}
        assert adm.patch(f"/admin/limits/users/{ids['beto']}",
                         json={"monthly_token_limit": 1}).status_code == 200
        r429 = beto.post("/chat", json={"query": "stock of 123456"})
        assert r429.status_code == 429 and "mensual" in r429.json()["detail"], r429.text
        me = beto.get("/me/usage").json()
        assert me["monthly_token_limit"] == 1 and me["pct_month"] >= 100
        adm.patch(f"/admin/limits/users/{ids['beto']}", json={"monthly_token_limit": None})
        assert beto.post("/chat", json={"query": "stock of 123456"}).status_code == 200
        adm.patch(f"/admin/limits/roles/{roles['usuario']}", json={"daily_token_limit": 1})
        assert ana.post("/chat", json={"query": "stock of 123456"}).status_code == 429
        adm.patch(f"/admin/limits/users/{ids['ana']}", json={"daily_token_limit": 0})  # unlimited
        assert ana.post("/chat", json={"query": "stock of 123456"}).status_code == 200
        adm.patch(f"/admin/limits/roles/{roles['usuario']}", json={"daily_token_limit": 0})
        assert ana.put("/admin/limits", json={"monthly_token_limit": 1}).status_code == 403
        adm.put("/admin/limits", json={"monthly_token_limit": 1})
        rg = sup.post("/chat", json={"query": "stock of 123456"})
        assert rg.status_code == 429 and "organización" in rg.json()["detail"], rg.text
        adm.put("/admin/limits", json={"monthly_token_limit": None})
        assert sup.post("/chat", json={"query": "stock of 123456"}).status_code == 200
        print("token limits ok")

        # ---- dashboard
        assert ana.get("/admin/token-dashboard").status_code == 403
        d = sup.get("/admin/token-dashboard").json()
        by = {u["username"]: u for u in d["by_user"]}
        assert by["ana"]["tokens"] > 0 and by["ana"]["tokens_today"] > 0
        assert d["totals"]["tokens"] == sum(u["tokens"] for u in d["by_user"])
        assert {"u-ana", "precios-imp"} <= {k["slug"] for k in d["by_kb"]}, d["by_kb"]
        assert any(p["name"] == "Pirata" for p in d["by_prompt"])
        assert any(x["route"] == "quote" for x in d["by_route"])
        assert {"usuario", "importaciones"} <= {x["role"] for x in d["by_role"]}
        assert d["daily"] and d["global"]["tokens_month"] >= d["totals"]["tokens"] - 1
        with session_scope() as s:
            m = (s.query(Message).filter(Message.role == "assistant", Message.kb_slugs.isnot(None))
                 .first())
            assert m.kb_slugs
        print("dashboard ok:", d["totals"])

        # ---- admin: deactivate a base -> users lose it; audit trail
        kid = next(k["id"] for k in adm.get("/admin/kb").json()["items"] if k["slug"] == "precios-imp")
        adm.patch(f"/admin/kb/{kid}", json={"is_active": False})
        assert "precios-imp" not in {k["slug"] for k in imp.get("/kb").json()["items"]}
        assert imp.post("/chat", json={"query": "x", "kb_ids": ["precios-imp"]}).status_code == 403
        assert adm.delete(f"/admin/kb/{kid}", params={"delete_files": True}).json()["ok"]
        gid = next(k["id"] for k in adm.get("/admin/kb").json()["items"] if k["slug"] == "general")
        assert adm.delete(f"/admin/kb/{gid}").status_code == 400
        acts = set(adm.get("/admin/audit").json()["actions"])
        assert {"kb_file_uploaded", "kb_file_deleted", "kb_created", "kb_deleted",
                "prompt_created", "limits_updated", "quote_created"} <= acts, acts
        print("admin kb lifecycle + audit ok")

        # ---- a new role gets its folder automatically
        nr = adm.post("/admin/roles", json={"name": "aduanas", "permissions": ["chat", "kb.upload_role"]}).json()
        assert any(k["slug"] == "rol-aduanas" for k in adm.get("/admin/kb").json()["items"]), nr


def test_upgrade_old_database() -> None:
    """An existing MySQL/SQLite database from the previous release gets the new columns."""
    from sqlalchemy import create_engine, inspect, text
    from app.db import _add_missing_columns
    TMP3.mkdir(parents=True, exist_ok=True)
    old = TMP3 / "old.db"
    old.unlink(missing_ok=True)
    eng = create_engine(f"sqlite:///{old.as_posix()}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE roles (id INTEGER PRIMARY KEY, name VARCHAR(50))"))
        c.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(80))"))
        c.execute(text("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)"))
    _add_missing_columns(eng)
    _add_missing_columns(eng)                       # idempotent
    cols = {t: {c["name"] for c in inspect(eng).get_columns(t)} for t in ("roles", "users", "messages")}
    assert {"daily_token_limit", "monthly_token_limit", "default_prompt_id"} <= cols["roles"]
    assert {"daily_query_limit", "daily_token_limit", "monthly_token_limit"} <= cols["users"]
    assert {"kb_slugs", "prompt_profile_id", "payload"} <= cols["messages"]
    eng.dispose()


def test_multirag() -> None:
    try:
        run_all()
        test_upgrade_old_database()
    finally:
        shutil.rmtree(TMP3, ignore_errors=True)


if __name__ == "__main__":
    test_multirag()
    print("\nALL MULTI-RAG TESTS PASSED")
