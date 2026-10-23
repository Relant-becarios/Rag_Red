"""Application database (MySQL ) through SQLAlchemy 2.

    DATABASE_URL=mysql+pymysql://relant:***@127.0.0.1:3306/Rag_red?charset=utf8mb4

* One engine per process with a connection pool (pool_pre_ping + pool_recycle, so
  connections that MySQL closed after `wait_timeout` are replaced transparently).
* `session_scope()` = short transactions. Never keep a session open while the LLM is
  answering: a 20-second LLM call would otherwise hold a pooled connection.
* `init_db()` creates missing tables and the default roles (idempotent, safe with several
  uvicorn workers starting at the same time).
* Any SQLAlchemy URL works (tests use SQLite), but production is meant for MySQL 8.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

log = logging.getLogger("rag.db")


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC (DATETIME columns carry no time zone; the UI converts to local time)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_engine: Engine | None = None
_factory: sessionmaker | None = None
_lock = threading.Lock()


class DatabaseNotConfigured(RuntimeError):
    pass


def database_url() -> str:
    url = (settings.database_url or "").strip()
    if not url:
        raise DatabaseNotConfigured(
            "DATABASE_URL no está definida. Ejemplo en el .env:\n"
            "DATABASE_URL=mysql+pymysql://relant:CLAVE@127.0.0.1:3306/rag_red?charset=utf8mb4")
    return url


def get_engine() -> Engine:
    global _engine, _factory
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            url = database_url()
            kw: dict[str, Any] = {"echo": settings.db_echo, "future": True}
            if url.startswith("sqlite"):
                kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
            else:
                kw.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                          pool_recycle=settings.db_pool_recycle, pool_pre_ping=True,
                          isolation_level="READ COMMITTED")
            eng = create_engine(url, **kw)
            if url.startswith("sqlite"):
                @event.listens_for(eng, "connect")
                def _sqlite_pragmas(conn, _rec):  # pragma: no cover - test backend only
                    cur = conn.cursor()
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA foreign_keys=ON")
                    cur.close()
            _factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
            _engine = eng
            log.info("database: %s", eng.url.render_as_string(hide_password=True))
    return _engine


def reset_engine() -> None:
    """Tests only: forget the engine so a new DATABASE_URL is picked up."""
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _factory = None, None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error, always return the connection to the pool."""
    get_engine()
    assert _factory is not None
    s: Session = _factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def dialect_name() -> str:
    return get_engine().dialect.name


def upsert(session: Session, table: Any, rows: list[dict], update_cols: list[str]) -> None:
    """INSERT ... ON DUPLICATE KEY UPDATE (MySQL) / ON CONFLICT DO UPDATE (SQLite)."""
    if not rows:
        return
    name = session.get_bind().dialect.name
    if name == "mysql":
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(table)
        stmt = stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in update_cols})
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(table)
        pk = [c.name for c in table.primary_key.columns]
        stmt = stmt.on_conflict_do_update(index_elements=pk,
                                          set_={c: stmt.excluded[c] for c in update_cols})
    else:  # postgresql and others
        from sqlalchemy.dialects.postgresql import insert
        stmt = insert(table)
        pk = [c.name for c in table.primary_key.columns]
        stmt = stmt.on_conflict_do_update(index_elements=pk,
                                          set_={c: stmt.excluded[c] for c in update_cols})
    session.execute(stmt, rows)


def init_db() -> None:
    """Create missing tables + default roles + optional first admin. Idempotent."""
    from app import models  # noqa: F401  (registers the tables)
    from app.security import DEFAULT_ROLES, hash_password

    eng = get_engine()
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)
    for name, (desc, perms, limit) in DEFAULT_ROLES.items():
        try:
            with session_scope() as s:
                if s.query(models.Role).filter_by(name=name).first() is None:
                    s.add(models.Role(name=name, description=desc, permissions=sorted(perms),
                                      daily_query_limit=limit, is_system=True))
        except IntegrityError:          # another worker created it at the same moment
            pass

    user, pwd = settings.initial_admin_username.strip(), settings.initial_admin_password
    if user and pwd:
        try:
            with session_scope() as s:
                if s.query(models.User).count() == 0:
                    role = s.query(models.Role).filter_by(name="admin").one()
                    s.add(models.User(username=user.lower(), full_name="Administrador",
                                      password_hash=hash_password(pwd), role_id=role.id,
                                      must_change_password=True))
                    log.warning("first admin user '%s' created from INITIAL_ADMIN_USERNAME; "
                                "it must change its password at first login", user)
        except IntegrityError:
            pass

    _upgrade_permissions()
    try:
        from app.kb import ensure_system_kbs
        ensure_system_kbs()
    except IntegrityError:              # another worker provisioned them at the same moment
        pass
    try:
        from app.prompt_profiles import seed_profiles
        seed_profiles()
    except IntegrityError:
        pass




# Columns added after the first release. create_all() never alters an existing table, so
# they are added here (MySQL 8 adds nullable columns with ALGORITHM=INSTANT: no rebuild).
_NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "roles": [("daily_token_limit", "BIGINT NULL"), ("monthly_token_limit", "BIGINT NULL"),
              ("default_prompt_id", "INTEGER NULL")],
    "users": [("daily_query_limit", "INTEGER NULL"), ("daily_token_limit", "BIGINT NULL"),
              ("monthly_token_limit", "BIGINT NULL")],
    "messages": [("kb_slugs", "JSON NULL"), ("prompt_profile_id", "INTEGER NULL"),
                 ("payload", "JSON NULL"), ("standalone_query", "TEXT NULL")],
}


def _add_missing_columns(eng: Engine) -> None:
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    for table, cols in _NEW_COLUMNS.items():
        if table not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols:
            if name in have:
                continue
            try:
                with eng.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                log.warning("database upgraded: %s.%s added", table, name)
            except Exception as e:      # another worker added it first
                log.info("column %s.%s not added (%s)", table, name, str(e)[:120])


def get_setting(key: str, default: Any = None) -> Any:
    from app.models import AppSetting
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return default if row is None or row.v is None else row.v


def set_setting(key: str, value: Any) -> None:
    from app.models import AppSetting
    with session_scope() as s:
        row = s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(k=key, v=value))
        else:
            row.v = value


def _upgrade_permissions() -> None:
    """Give the permissions introduced by new versions to the base roles, once."""
    from app import models
    from app.security import PERMISSION_UPGRADES, PERM_VERSION
    try:
        current = int(get_setting("perm_version", 1) or 1)
    except (TypeError, ValueError):
        current = 1
    if current >= PERM_VERSION:
        return
    with session_scope() as s:
        for version in sorted(v for v in PERMISSION_UPGRADES if v > current):
            for role_name, perms in PERMISSION_UPGRADES[version].items():
                r = s.query(models.Role).filter_by(name=role_name).first()
                if r is not None:
                    r.permissions = sorted(set(r.permissions or []) | perms)
    set_setting("perm_version", PERM_VERSION)
    log.warning("permissions upgraded to version %d (knowledge bases, prompts, limits)",
                PERM_VERSION)
