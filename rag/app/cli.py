"""command line: indexing outside the API process, health
check, quick questions, answer-cache calibration, reranker check.

    python -m app.cli paths                 qué .env se leyó y todas las rutas efectivas
    python -m app.cli check                 key, credits, embeddings, chat model
    python -m app.cli update                index new / changed / deleted files
    python -m app.cli rebuild               wipe and index everything (cache keeps paid vectors)
    python -m app.cli ask "¿precio del 123456?" [--agent] [--debug]

Base de datos (MySQL, DATABASE_URL en el .env):
    python -m app.cli db-init                         crea tablas y roles base
    python -m app.cli create-user admin --role admin  pide la contraseña
    python -m app.cli set-password juan               pide la nueva contraseña
    python -m app.cli list-users
    python -m app.cli migrate-cache [--sqlite RUTA]   copia la caché SQLite de embeddings
                                                      y descripciones de dibujos a MySQL

Multi-RAG (bases de conocimientos):
    python -m app.cli kb-list                          bases, carpetas y estado del índice
    python -m app.cli kb-update <slug> [--rebuild]     indexa una base (u-juan, rol-ventas...)

Productos, hojas técnicas y grafo (base general):
    python -m app.cli init-data             crea las carpetas RAG_DIR_* del .env (vacías)
    python -m app.cli data-report           productos sin foto, duplicados, huérfanos...
    python -m app.cli datasheet 123456 [--out hoja.html]   una hoja técnica HTML
                                            (sin --out -> RAG_DATASHEET_OUT_DIR)
    python -m app.cli datasheet --all       todos -> RAG_DATASHEET_OUT_DIR
    python -m app.cli datasheet 123456 --pdf [--out hoja.pdf]   la hoja técnica en PDF
    python -m app.cli graph [--out grafo.html] [--json grafo.json]   grafo 2D/3D
                                            (sin --out -> RAG_DB_DIR/grafo.html)

    python -m app.cli reranker-check        descarga/carga el cross-encoder y lo prueba
    python -m app.cli cache-pairs [--out pairs.csv]   pares registrados para calibrar
    python -m app.cli cache-calibrate pairs.csv [--precision 0.98]   umbral recomendado
    python -m app.cli cache-clear           vacía la caché de respuestas
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.config import settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("paths")
    sub.add_parser("update")
    sub.add_parser("rebuild")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--agent", action="store_true")
    ask.add_argument("--debug", action="store_true")
    sub.add_parser("db-init")
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--role", default="usuario")
    cu.add_argument("--full-name", default="")
    cu.add_argument("--email", default="")
    cu.add_argument("--password", default="", help="omit to type it (not echoed)")
    cu.add_argument("--no-force-change", action="store_true",
                    help="do not ask the user to change the password at first login")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    sp.add_argument("--password", default="")
    sub.add_parser("list-users")
    mc = sub.add_parser("migrate-cache")
    mc.add_argument("--sqlite", default="", help="default: <RAG_DB_DIR>/manifest.json.cache.sqlite")
    sub.add_parser("kb-list")
    ku = sub.add_parser("kb-update")
    ku.add_argument("slug")
    ku.add_argument("--rebuild", action="store_true")
    sub.add_parser("init-data")
    sub.add_parser("data-report")
    ds = sub.add_parser("datasheet")
    ds.add_argument("part", nargs="?", default="")
    ds.add_argument("--all", action="store_true")
    ds.add_argument("--out", default="")
    ds.add_argument("--no-embed", action="store_true", help="link images instead of embedding")
    ds.add_argument("--pdf", action="store_true", help="PDF (app/pdfdatagen.py) instead of HTML")
    gr = sub.add_parser("graph")
    gr.add_argument("--out", default="")
    gr.add_argument("--json", default="", help="also write nodes/links as JSON")
    sub.add_parser("reranker-check")
    cp = sub.add_parser("cache-pairs")
    cp.add_argument("--out", default="answer_cache_pairs.csv")
    cp.add_argument("--limit", type=int, default=2000)
    cc = sub.add_parser("cache-calibrate")
    cc.add_argument("csv")
    cc.add_argument("--precision", type=float, default=0.98)
    sub.add_parser("cache-clear")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "init-data":                      # no API key needed
        return init_data()
    if args.cmd == "paths":
        print(json.dumps(settings.paths_report(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "kb-list":
        try:
            return _kb_list()
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.cmd in ("reranker-check", "cache-pairs", "cache-calibrate", "cache-clear"):
        try:
            return _production_command(args)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.cmd in ("db-init", "create-user", "set-password", "list-users", "migrate-cache"):
        try:
            return _db_command(args)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    from app.errors import IndexingAborted, OpenRouterError
    from app.llm import check_api_key, make_llm, preflight
    try:
        key = check_api_key()
        if args.cmd == "check":
            print("preflight:", preflight(key))
            from app.llm import make_embeddings
            print("embedding dims:", len(make_embeddings().embed_query("ping")))
            print("chat:", make_llm().invoke("Reply with OK").content)
            return 0

        from app.jobs import index_lock_for
        from app.retriever import HybridEvaluationRetriever
        vision = make_llm(settings.effective_vision_model) if settings.describe_images else None
        if args.cmd == "kb-update":
            return _kb_update(args.slug, args.rebuild, vision, key)
        retriever = HybridEvaluationRetriever(vision_llm=vision)
        if args.cmd in ("update", "rebuild"):
            if settings.preflight:
                preflight(key)
            with index_lock_for(retriever):
                sync = retriever.rebuild_index() if args.cmd == "rebuild" else retriever.update_index()
            print(json.dumps(sync, indent=2, ensure_ascii=False))
            return 0

        from app.production_router import ProductionRouter as QueryRouter
        retriever.load()
        if args.cmd == "data-report":
            from app.products import get_product_index
            print(json.dumps(get_product_index(retriever).report(), indent=2, ensure_ascii=False))
            return 0
        if args.cmd == "datasheet":
            from app.products import build_datasheet_html, get_product_index, write_all_datasheets
            if args.all:
                print(json.dumps(write_all_datasheets(retriever, args.out or None,
                                                      embed=not args.no_embed), indent=2))
                return 0
            found = get_product_index(retriever).find(args.part)
            if not found:
                print(f"No hay producto '{args.part}' en productos/*.csv", file=sys.stderr)
                return 1
            if len(found) > 1:
                print("Varias coincidencias, se usa la primera: "
                      + ", ".join(f"{p.part} ({p.name})" for p in found), file=sys.stderr)
            out = args.out
            if args.pdf:
                from app.products import build_datasheet_pdf, datasheet_pdf_filename
                if not out:
                    settings.datasheet_out_dir.mkdir(parents=True, exist_ok=True)
                    out = str(settings.datasheet_out_dir / datasheet_pdf_filename(found[0]))
                with open(out, "wb") as f:
                    f.write(build_datasheet_pdf(found[0]))
                print(out)
                return 0
            if not out:
                settings.datasheet_out_dir.mkdir(parents=True, exist_ok=True)
                out = str(settings.datasheet_out_dir / f"hoja_{found[0].part or 'producto'}.html")
            with open(out, "w", encoding="utf-8") as f:
                f.write(build_datasheet_html(found[0], embed=not args.no_embed))
            print(out)
            return 0
        if args.cmd == "graph":
            from app.graph_export import product_graph, system_graph_page
            from app.multiagent import QuoteCoordinator
            router = QueryRouter(retriever, make_llm())
            if not args.out:
                from pathlib import Path
                args.out = str(Path(settings.index_dir) / "grafo.html")
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(system_graph_page(retriever, router, QuoteCoordinator(retriever, router.llm)))
            if args.json:
                with open(args.json, "w", encoding="utf-8") as f:
                    json.dump(product_graph(retriever), f, ensure_ascii=False, indent=1)
            print(args.out)
            return 0
        # CLI: pre-route + RRF + reranker as in the API; no answer cache (no user scope)
        res = QueryRouter(retriever, make_llm(), use_cache=False).answer(
            args.question, force_agent=args.agent, debug=args.debug)
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
        return 0
    except (OpenRouterError, IndexingAborted, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _production_command(args) -> int:
    if args.cmd == "reranker-check":
        import time
        from app.reranker import get_reranker
        rr = get_reranker()
        t0 = time.time()
        q = "presión máxima de la bomba"
        docs = ["Maximum operating pressure: 250 bar (Max. Betriebsdruck 250 bar).",
                "Instrucciones de limpieza del filtro de aire.",
                "La presión máxima de trabajo de la bomba es de 250 bar."]
        scores = rr.score(q, docs)
        print(json.dumps({"status": rr.status(), "seconds": round(time.time() - t0, 2),
                          "query": q, "scores": dict(zip(docs, scores or []))},
                         indent=2, ensure_ascii=False))
        return 0 if scores else 1
    from app.answer_cache import AnswerCache, calibrate, export_pairs
    if args.cmd == "cache-pairs":
        n = export_pairs(args.out, args.limit)
        print(f"{n} pares -> {args.out}. Llena la columna `label` con same / different y "
              f"ejecuta: python -m app.cli cache-calibrate {args.out}")
        return 0
    if args.cmd == "cache-calibrate":
        rep = calibrate(args.csv, args.precision)
        if "error" in rep:
            print(rep["error"], file=sys.stderr)
            return 1
        for row in rep["table"]:
            print(f"  t={row['threshold']:.3f}  aceptados={row['accepted']:4d}  "
                  f"precision={row['precision']:.3f}  recall={row['recall']:.3f}")
        best = rep["recommended"]
        print(f"\n{rep['labelled']} pares etiquetados ({rep['same']} iguales). " + (
            f"Umbral recomendado: ANSWER_CACHE_THRESHOLD={best['threshold']} "
            f"(precision {best['precision']}, recall {best['recall']})" if best else
            "Ningún umbral alcanza la precisión pedida: deja el valor actual."))
        return 0
    if args.cmd == "cache-clear":
        print(f"{AnswerCache().clear()} respuestas borradas de la caché")
        return 0
    return 1


def _ask_password(given: str) -> str:
    if given:
        return given
    import getpass
    p1 = getpass.getpass("Contraseña: ")
    p2 = getpass.getpass("Repite la contraseña: ")
    if p1 != p2:
        raise RuntimeError("las contraseñas no coinciden")
    return p1


def _db_command(args) -> int:
    from app.db import init_db, session_scope
    from app.models import Role, User
    from app.security import hash_password, password_problem

    init_db()
    if args.cmd == "db-init":
        with session_scope() as s:
            roles = [r.name for r in s.query(Role).order_by(Role.id)]
            n = s.query(User).count()
        print(f"OK: tablas listas. Roles: {', '.join(roles)}. Usuarios: {n}.")
        return 0

    if args.cmd == "list-users":
        with session_scope() as s:
            for u in s.query(User).order_by(User.username):
                print(f"{u.id:>5}  {u.username:<25} {u.role.name:<12} "
                      f"{'activo' if u.is_active else 'INACTIVO'}  {u.full_name}")
        return 0

    if args.cmd in ("create-user", "set-password"):
        username = args.username.strip().lower()
        pwd = _ask_password(args.password)
        problem = password_problem(pwd, settings.password_min_length)
        if problem:
            raise RuntimeError(problem)
        with session_scope() as s:
            u = s.query(User).filter(User.username == username).first()
            if args.cmd == "set-password":
                if u is None:
                    raise RuntimeError(f"no existe el usuario {username}")
                u.password_hash, u.failed_logins, u.locked_until = hash_password(pwd), 0, None
                print(f"OK: contraseña de {username} actualizada (cuenta desbloqueada).")
                return 0
            if u is not None:
                raise RuntimeError(f"el usuario {username} ya existe")
            role = s.query(Role).filter(Role.name == args.role.lower()).first()
            if role is None:
                raise RuntimeError(f"no existe el rol {args.role}")
            s.add(User(username=username, full_name=args.full_name, email=args.email or None,
                       password_hash=hash_password(pwd), role_id=role.id, preferences={},
                       must_change_password=not args.no_force_change))
        print(f"OK: usuario {username} creado con rol {role.name}.")
        return 0

    if args.cmd == "migrate-cache":
        return _migrate_cache(args.sqlite or settings.sqlite_cache_file)
    return 1


def _migrate_cache(path: str) -> int:
    """SQLite (tables emb, cap) -> MySQL (embedding_cache, caption_cache). Re-runnable:
    rows are upserted, so running it twice changes nothing."""
    import os
    import sqlite3
    import time
    from app.embedding_cache import DbEmbeddingCache

    if not os.path.isfile(path):
        raise RuntimeError(f"no encuentro la caché SQLite: {path}")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    dst = DbEmbeddingCache()
    t0 = time.time()
    total = src.execute("SELECT COUNT(*) FROM emb").fetchone()[0]
    print(f"vectores en SQLite: {total:,}  ->  {dst.path}")
    done, cur = 0, src.execute("SELECT k, v FROM emb")
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        dst.put_raw([(k, bytes(v)) for k, v in rows])
        done += len(rows)
        print(f"  {done:,}/{total:,}", end="\r", flush=True)
    caps = src.execute("SELECT k, t FROM cap").fetchall()
    dst.put_captions([(k, t) for k, t in caps if t])
    src.close()
    print(f"\nOK: {done:,} vectores y {len(caps):,} descripciones copiados en "
          f"{time.time() - t0:.1f}s. En MySQL ahora: {dst.count():,} vectores, "
          f"{dst.count_captions():,} descripciones.")
    print("El archivo SQLite no se modificó; puedes conservarlo como respaldo.")
    return 0


def _kb_list() -> int:
    from app.db import init_db
    from app.kb import KBRegistry  # noqa: F401
    from app.kb import _info
    from app.db import session_scope
    from app.models import KnowledgeBase, Role, User
    init_db()
    with session_scope() as s:
        owners = dict(s.query(User.id, User.username).all())
        roles = dict(s.query(Role.id, Role.name).all())
        infos = [_info(k, owners, roles) for k in s.query(KnowledgeBase).order_by(KnowledgeBase.id)]
    for kb in infos:
        who = kb.owner_username or kb.role_name or ("todos" if not kb.read_roles else "roles " + ",".join(map(str, kb.read_roles)))
        print(f"{kb.slug:<28} {kb.scope:<7} {'activa' if kb.is_active else 'INACTIVA':<9} {who:<20} {kb.files_dir}")
    return 0


def _kb_update(slug: str, rebuild: bool, vision, key: str) -> int:
    """Index one knowledge base from the console (same lock file as the API workers)."""
    from app.db import init_db, session_scope
    from app.jobs import index_lock_for
    from app.kb import DEFAULT_SLUG, _info
    from app.llm import preflight
    from app.models import KnowledgeBase, Role, User
    from app.retriever import HybridEvaluationRetriever
    init_db()
    with session_scope() as s:
        row = s.query(KnowledgeBase).filter_by(slug=slug).first()
        if row is None:
            raise RuntimeError(f"no existe la base {slug} (python -m app.cli kb-list)")
        kb = _info(row, dict(s.query(User.id, User.username).all()), dict(s.query(Role.id, Role.name).all()))
    kb.files_dir.mkdir(parents=True, exist_ok=True)
    if slug == DEFAULT_SLUG:
        r = HybridEvaluationRetriever(vision_llm=vision)
    else:
        kb.idx_dir.mkdir(parents=True, exist_ok=True)
        r = HybridEvaluationRetriever(data_dir=str(kb.files_dir), chroma_dir=str(kb.idx_dir / "chroma"),
                                      manifest_file=str(kb.idx_dir / "manifest.json"),
                                      namespace=kb.namespace, vision_llm=vision)
    if settings.preflight:
        preflight(key)
    with index_lock_for(r):
        sync = r.rebuild_index() if rebuild else r.update_index()
    print(json.dumps(sync, indent=2, ensure_ascii=False))
    return 0


DATA_LAYOUT = {      # sección -> qué va ahí (las CARPETAS vienen del .env, RAG_DIR_*)
    "productos": "un CSV por familia: bombas.csv, valvulas_atomizado.csv ...",
    "precios": "precios_existencias.csv (precio, costo, existencia)",
    "fotos": "foto del producto: <NumParte>.jpg",
    "explosionados": "vista explosionada: <NumParte>_explosionado.png",
    "drawings": "otros dibujos / planos",
    "hojas_tecnicas": "PDF existentes: <familia>/<NumParte>_<Nombre>.pdf",
    "manuales": "<fabricante>/<archivo>.pdf",
    "catalogos": "<fabricante>/<archivo>.pdf",
}


def init_data() -> int:
    """Create the folders declared in the .env (safe: never touches existing files)."""
    from pathlib import Path
    from app.config import SECTION_ENV
    root = Path(settings.data_dir)
    for section, what in DATA_LAYOUT.items():
        dirs = settings.section_dirs(section)
        if not dirs:
            print(f"({SECTION_ENV[section]} vacía: sección no usada)")
        for d in dirs:
            (root / d).mkdir(parents=True, exist_ok=True)
            print(f"{root / d}  <- {what}  [{SECTION_ENV[section]}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
