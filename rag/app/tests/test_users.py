"""Users, roles, sessions, per-user conversation log, quotas, admin API and cache migration.
Offline (fake embeddings + fake LLM). Uses SQLite unless TEST_DATABASE_URL points to MySQL:

    set TEST_DATABASE_URL=mysql+pymysql://relant:pw@127.0.0.1/relant_test?charset=utf8mb4
    python -m pytest -q app/tests
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as base  # noqa: E402  (sets the environment before app imports)

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

TMP2 = Path(tempfile.mkdtemp(prefix="relant_users_test_"))


def _setup():
    from app.config import settings
    from app.db import reset_engine
    if not os.getenv("TEST_DATABASE_URL"):
        settings.database_url = f"sqlite:///{(TMP2 / 'app.db').as_posix()}"
    reset_engine()
    base.reset_database()
    data = TMP2 / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "Manual_VDV-25.txt").write_text(
        "VDV-25 metering valve. Working pressure 6 to 250 bar.", encoding="utf-8")
    (data / "stock.csv").write_text("Article;Description;Stock\n123456;O-ring VDV-25;14\n",
                                    encoding="utf-8")
    from app.retriever import HybridEvaluationRetriever
    r = HybridEvaluationRetriever(data_dir=str(data), chroma_dir=str(TMP2 / "idx" / "chroma"),
                                  manifest_file=str(TMP2 / "idx" / "manifest.json"),
                                  embeddings=DeterministicFakeEmbedding(size=32))
    r.update_index()
    return r


class BoomLLM(base.FakeLLM):
    def invoke(self, messages):
        if "explode" in messages[-1].content:
            raise RuntimeError("Error code: 402 insufficient credits")
        return super().invoke(messages)


def run_all() -> None:
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.jobs import IndexJobManager
    from app.main import create_app
    from app.router import QueryRouter

    r = _setup()
    llm = BoomLLM()
    router = QueryRouter(r, llm)
    comps = {"retriever": r, "router": router, "jobs": IndexJobManager(r, run_preflight=False)}
    base.make_user("root", "admin")
    base.make_user("sup", "supervisor")
    app = create_app(comps, auto_sync=False, web_warmup=False)

    with TestClient(app) as adm, TestClient(app) as ana, TestClient(app) as beto, \
            TestClient(app) as sup:
        base.login(adm, "root")
        roles = {x["name"]: x for x in adm.get("/admin/roles").json()["items"]}

        # ---- admin creates users (they must change the password at first login)
        for name in ("ana", "beto"):
            res = adm.post("/admin/users", json={"username": name, "full_name": name.title(),
                                                  "password": "inicial123",
                                                  "role_id": roles["usuario"]["id"]})
            assert res.status_code == 200, res.text
        assert adm.post("/admin/users", json={"username": "ana", "password": "inicial123",
                                              "role_id": roles["usuario"]["id"]}).status_code == 409
        assert adm.post("/admin/users", json={"username": "xyz", "password": "123",
                                              "role_id": 1}).status_code == 400
        base.login(ana, "ana", "inicial123")
        assert ana.post("/chat", json={"query": "hola"}).json()["detail"] == "must_change_password"
        assert ana.post("/auth/change-password", json={
            "current_password": "inicial123", "new_password": "inicial123"}).status_code == 400
        assert ana.post("/auth/change-password", json={
            "current_password": "inicial123", "new_password": "nueva4567"}).status_code == 200
        base.login(beto, "beto", "inicial123")
        beto.post("/auth/change-password", json={"current_password": "inicial123",
                                                 "new_password": "beto12345"})
        print("users + forced password change ok")

        # ---- conversations are per user
        a1 = ana.post("/chat", json={"query": "working pressure of the VDV-25", "debug": True}).json()
        cid = a1["conversation_id"]
        assert a1["conversation_created"] and a1["transcript"] is None   # no chat.debug
        a2 = ana.post("/chat", json={"query": "stock of 123456", "conversation_id": cid}).json()
        assert a2["conversation_id"] == cid and not a2["conversation_created"]
        conv = ana.get(f"/conversations/{cid}").json()
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] * 2, conv
        assert conv["messages"][1]["sources"] and conv["messages"][1]["usage"]["llm_calls"] == 1
        assert ana.get("/conversations").json()["total"] == 1
        assert beto.get("/conversations").json()["total"] == 0
        assert beto.get(f"/conversations/{cid}").status_code == 404
        assert beto.post("/chat", json={"query": "x", "conversation_id": cid}).status_code == 404
        print("per-user conversations ok")

        # ---- clarification answered in the same conversation -> original question resent
        q = "the valve"
        llm.fast_insufficient.add(q)
        llm.scripts[q] = [{"tool": "clarify", "args": {"question": "¿Qué modelo?"}}]
        c1 = beto.post("/chat", json={"query": q}).json()
        assert c1["clarify"], c1
        beto.post("/chat", json={"query": "VDV-25", "conversation_id": c1["conversation_id"]})
        from app.db import session_scope
        from app.models import Message
        with session_scope() as s:
            last_q = (s.query(Message).filter_by(conversation_id=c1["conversation_id"], role="user")
                      .order_by(Message.id.desc()).first())
            assert last_q.content == "VDV-25"
            assert last_q.effective_query == "the valve\nAclaración del usuario: VDV-25"
        print("clarify across turns ok")

        # ---- LLM error is logged as an error message
        assert beto.post("/chat", json={"query": "explode please"}).status_code == 503
        with session_scope() as s:
            err = s.query(Message).filter_by(status="error").one()
            assert "402" in err.error
        print("errors logged ok")

        # ---- permissions
        assert ana.get("/admin/users").status_code == 403
        assert ana.get("/admin/usage").status_code == 403
        assert sup.post("/auth/login", json={"username": "sup", "password": "clave1234"}).status_code == 200
        assert sup.get("/admin/users").status_code == 403
        usage = sup.get("/admin/usage").json()
        by = {i["username"]: i for i in usage["items"]}
        assert by["ana"]["questions"] == 2 and by["beto"]["errors"] == 1, usage
        assert usage["daily"] and usage["totals"]["questions"] == 5, usage
        ro = sup.get(f"/conversations/{cid}").json()
        assert ro["read_only"] and ro["conversation"]["user"]["username"] == "ana"
        allc = sup.get("/admin/conversations", params={"q": "123456"}).json()
        assert allc["total"] == 1 and allc["items"][0]["user"]["username"] == "ana", allc
        csv_text = sup.get("/admin/messages.csv", params={"user_id": by["ana"]["user_id"]}).text
        assert csv_text.startswith("﻿") and csv_text.count("\n") == 5, csv_text
        sup_debug = sup.post("/chat", json={"query": "working pressure of the VDV-25",
                                            "debug": True}).json()
        assert sup_debug["transcript"] is not None
        print("roles/permissions + usage + csv ok")

        # ---- preferences
        assert ana.put("/me/preferences", json={"deep_search": True, "theme": "dark"}).json()["theme"] == "dark"
        assert ana.get("/auth/me").json()["user"]["preferences"]["deep_search"] is True
        assert ana.put("/me/preferences", json={"theme": "neon"}).status_code == 422
        print("preferences ok")

        # ---- daily limit per role
        lim = adm.post("/admin/roles", json={"name": "limitado", "permissions": ["chat"],
                                             "daily_query_limit": 3}).json()
        uid_beto = next(u["id"] for u in adm.get("/admin/users").json()["items"] if u["username"] == "beto")
        adm.patch(f"/admin/users/{uid_beto}", json={"role_id": lim["id"]})
        # beto already asked 3 today (clarify, its answer, explode)
        assert beto.post("/chat", json={"query": "stock of 123456"}).status_code == 429
        assert beto.post("/chat/agent", json={"query": "stock"}).status_code == 403
        assert beto.get("/documents").status_code == 403
        print("daily limit + role change ok")

        # ---- soft delete
        assert ana.delete(f"/conversations/{cid}").status_code == 200
        assert ana.get("/conversations").json()["total"] == 0
        assert sup.get(f"/conversations/{cid}").status_code == 200
        print("soft delete ok")

        # ---- lock-out after wrong passwords, admin unlocks
        with TestClient(app) as anon:
            for _ in range(settings.login_max_attempts):
                assert anon.post("/auth/login", json={"username": "ana", "password": "bad"}).status_code == 401
            assert anon.post("/auth/login", json={"username": "ana", "password": "nueva4567"}).status_code == 423
            uid_ana = next(u["id"] for u in adm.get("/admin/users").json()["items"] if u["username"] == "ana")
            assert adm.patch(f"/admin/users/{uid_ana}", json={"unlock": True}).json()["locked"] is False
            assert anon.post("/auth/login", json={"username": "ana", "password": "nueva4567"}).status_code == 200
        print("lock-out ok")

        # ---- deactivation revokes sessions; the last admin is protected
        adm.patch(f"/admin/users/{uid_ana}", json={"is_active": False})
        assert ana.get("/auth/me").status_code == 401
        me_id = adm.get("/auth/me").json()["user"]["id"]
        assert adm.patch(f"/admin/users/{me_id}", json={"is_active": False}).status_code == 400
        assert adm.patch(f"/admin/roles/{roles['admin']['id']}",
                         json={"permissions": ["chat"]}).status_code == 400
        assert adm.patch(f"/admin/users/{me_id}", json={"role_id": roles["usuario"]["id"]}).status_code == 400
        assert adm.delete(f"/admin/roles/{lim['id']}").status_code == 400      # has users
        audit = adm.get("/admin/audit").json()
        assert {"login", "login_failed", "user_created", "password_changed",
                "conversation_deleted", "role_created"} <= set(audit["actions"]), audit["actions"]
        print("deactivation + last-admin guard + audit ok")

        # ---- logout
        adm.post("/auth/logout")
        assert adm.get("/auth/me").status_code == 401

    # ---- migrate the old SQLite embedding cache into the DB
    from app.cli import main as cli_main
    from app.embedding_cache import DbEmbeddingCache
    old = TMP2 / "old.cache.sqlite"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE emb (k TEXT PRIMARY KEY, v BLOB NOT NULL)")
    con.execute("CREATE TABLE cap (k TEXT PRIMARY KEY, t TEXT NOT NULL)")
    con.executemany("INSERT INTO emb VALUES (?, ?)",
                    [(f"{i:064x}", array("f", [i, 0.5, -1.0]).tobytes()) for i in range(2500)])
    con.execute("INSERT INTO cap VALUES (?, ?)", ("c" * 64, "Exploded view of VDV-25"))
    con.commit()
    con.close()
    before = DbEmbeddingCache().count()
    assert cli_main(["migrate-cache", "--sqlite", str(old)]) == 0
    assert cli_main(["migrate-cache", "--sqlite", str(old)]) == 0          # re-runnable
    cache = DbEmbeddingCache()
    assert cache.count() == before + 2500
    assert cache.get_many([f"{7:064x}"])[f"{7:064x}"] == [7.0, 0.5, -1.0]
    assert cache.get_caption("c" * 64) == "Exploded view of VDV-25"
    print("migrate-cache ok")


def test_users() -> None:
    try:
        run_all()
    finally:
        shutil.rmtree(TMP2, ignore_errors=True)


if __name__ == "__main__":
    test_users()
    print("\nALL USER TESTS PASSED")
