"""Tables of the application database (see db/schema_mysql.sql for the same DDL in SQL).

roles            name, permissions (JSON list), optional daily question limit
users            login, password hash, role, preferences (JSON), lock-out counters
user_sessions    one row per login (token stored only as SHA-256), revocable
conversations    per user; title, counters
messages         every question and every answer, with route, sources, tokens, cost, time
audit_log        logins, failed logins, user/role changes, indexing...
embedding_cache  text -> vector  (was the SQLite file manifest.json.cache.sqlite)
caption_cache    drawing -> description (same SQLite file, table `cap`)
answer_cache     Production 1.0: semantic answer cache (question vector + full result),
                 scoped by knowledge bases + prompt profile + index version
answer_cache_logmiss pairs logged to calibrate the threshold
knowledge_bases  multi-RAG: one folder + one index per base (general, per role, per user)
prompt_profiles  assistant instructions selectable per role
app_settings     key -> JSON value (global token limit, migration markers)

Token limits: roles and users carry daily/monthly token limits. On a user, NULL means
"inherit from the role" and 0 means "no limit" (see app/usage.py).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        LargeBinary, Numeric, String, Text)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

# BIGINT ids in MySQL; SQLite needs plain INTEGER for autoincrement (tests).
BigId = BigInteger().with_variant(Integer, "sqlite")
LongText = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
Blob = LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")
DT = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    daily_query_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    default_prompt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    email: Mapped[str | None] = mapped_column(String(190), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # per-user overrides: NULL = inherit from the role, 0 = unlimited
    daily_query_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DT, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DT, default=utcnow, onupdate=utcnow,
                                                 nullable=False)

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id", "revoked_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DT, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DT, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(255), default="", nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conv_user_updated", "user_id", "deleted_at", "updated_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    # soft delete: the user no longer sees it, the audit trail keeps it
    deleted_at: Mapped[datetime | None] = mapped_column(DT, nullable=True)

    user: Mapped[User] = relationship(lazy="joined")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_msg_conv", "conversation_id", "id"),
                      Index("ix_msg_user_created", "user_id", "created_at"),
                      Index("ix_msg_created", "created_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)        # user | assistant
    content: Mapped[str] = mapped_column(LongText, nullable=False)
    # user rows: question actually sent to the RAG (includes the original question when
    # the user is answering a clarification)
    effective_query: Mapped[str | None] = mapped_column(LongText, nullable=True)
    # assistant rows
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)  # ok | error
    route: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    escalation: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    clarify: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deep_search: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sources: Mapped[Any] = mapped_column(JSON, nullable=True)
    drawings: Mapped[Any] = mapped_column(JSON, nullable=True)
    usage: Mapped[Any] = mapped_column(JSON, nullable=True)
    timings: Mapped[Any] = mapped_column(JSON, nullable=True)
    transcript: Mapped[Any] = mapped_column(JSON, nullable=True)
    agent_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # multi-RAG: which knowledge bases were searched and which prompt profile was used
    kb_slugs: Mapped[Any] = mapped_column(JSON, nullable=True)
    prompt_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # structured result of a specialised agent (multi-agent quote), re-rendered by the UI
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    # Production 1.0, assistant rows: the follow-up rewritten as a standalone question
    standalone_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_created", "created_at"),
                      Index("ix_audit_user", "user_id", "created_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigId, nullable=True)
    username: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    detail: Mapped[Any] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)


class EmbeddingVector(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    k: Mapped[str] = mapped_column(String(64), primary_key=True)     # sha256(model + text)
    v: Mapped[bytes] = mapped_column(Blob, nullable=False)           # float32 array


class CaptionCache(Base):
    __tablename__ = "caption_cache"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    k: Mapped[str] = mapped_column(String(64), primary_key=True)
    t: Mapped[str] = mapped_column(LongText, nullable=False)


class AnswerCacheEntry(Base):
    """Production 1.0 semantic answer cache. `scope` = sha256 of (sorted knowledge-base
    slugs, prompt profile, index version of every base, models, prompts, invoice
    permission): a re-index or a prompt change starts a fresh scope automatically."""
    __tablename__ = "answer_cache"
    __table_args__ = (Index("ix_ac_scope_q", "scope", "qhash"),
                      Index("ix_ac_scope_created", "scope", "created_at"),
                      Index("ix_ac_created", "created_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    qhash: Mapped[str] = mapped_column(String(64), nullable=False)   # sha256(normalised q)
    question: Mapped[str] = mapped_column(LongText, nullable=False)
    codes: Mapped[Any] = mapped_column(JSON, nullable=True)         # sorted part codes
    numbers: Mapped[Any] = mapped_column(JSON, nullable=True)       # sorted numbers
    vec: Mapped[bytes | None] = mapped_column(Blob, nullable=True)  # float32, unit length
    result: Mapped[Any] = mapped_column(JSON, nullable=False)       # router result dict
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    last_hit_at: Mapped[datetime | None] = mapped_column(DT, nullable=True)


class AnswerCacheLog(Base):
    """Near-miss and hit pairs (similarity >= ANSWER_CACHE_LOG_FLOOR) for calibrating
    ANSWER_CACHE_THRESHOLD:  python -m app.cli cache-pairs / cache-calibrate."""
    __tablename__ = "answer_cache_log"
    __table_args__ = (Index("ix_acl_created", "created_at"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(LongText, nullable=False)
    cached_question: Mapped[str] = mapped_column(LongText, nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    codes_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    label: Mapped[str] = mapped_column(String(16), default="", nullable=False)  # same|different
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)


class KnowledgeBase(Base):
    """One searchable collection = one folder of files + one index (Chroma + manifest).

    scope   global  created by an admin; readable by `read_roles` (empty = every role),
                    writable by `write_roles` (+ anyone with kb.manage)
            role    one per role; members read it, members with kb.upload_role write it
            user    one per user; only the owner (and kb.manage) reads and writes it
    """
    __tablename__ = "knowledge_bases"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    scope: Mapped[str] = mapped_column(String(16), default="global", nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    role_id: Mapped[int | None] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=True, index=True)
    read_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    write_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # optional absolute folders (admin only); empty -> <RAG_KB_DIR>/<scope>/<key>/...
    data_dir: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    index_dir: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # pre-selected in the chat when the user has not chosen sources yet
    selected_by_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DT, default=utcnow, onupdate=utcnow,
                                                 nullable=False)


class PromptProfile(Base):
    """Extra instructions appended to the system prompts (persona, tone, format, rules).
    `role_ids` empty = every role may use it."""
    __tablename__ = "prompt_profiles"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    instructions: Mapped[str] = mapped_column(LongText, nullable=False)
    role_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DT, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DT, default=utcnow, onupdate=utcnow,
                                                 nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    k: Mapped[str] = mapped_column(String(64), primary_key=True)
    v: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DT, default=utcnow, onupdate=utcnow,
                                                 nullable=False)
