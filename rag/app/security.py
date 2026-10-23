"""Passwords, session tokens, permissions and the default roles."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# ---- permissions ----------------------------------------------------------------------
PERMISSIONS: dict[str, str] = {
    "chat": "Hacer preguntas al asistente",
    "chat.agent": "Usar búsqueda profunda (agente, más costosa)",
    "chat.debug": "Ver los pasos internos del agente",
    "documents.read": "Ver la lista de documentos y abrir archivos",
    "conversations.read_all": "Ver las conversaciones de todos los usuarios",
    "reports.read": "Ver uso y costos por usuario y la bitácora de auditoría",
    "users.manage": "Crear y editar usuarios y roles",
    "index.manage": "Reindexar documentos y refrescar sitios de fabricantes",
    "kb.upload_own": "Subir archivos a su carpeta personal (su propia base de conocimientos)",
    "kb.upload_role": "Subir archivos a la carpeta compartida de su rol",
    "kb.manage": "Crear bases de conocimientos y agregar archivos a cualquiera",
    "prompts.manage": "Crear y asignar prompts (instrucciones) por rol",
    "limits.manage": "Definir límites de tokens por usuario, rol y global",
    "agents.quote": "Usar el multiagente de cotización (importación / exportación)",
    "invoices.report": "Generar el reporte de invoices (costos y tarifas arancelarias) y bajarlo en CSV",
}

# name -> (description, permissions, daily question limit or None = unlimited)
DEFAULT_ROLES: dict[str, tuple[str, set[str], int | None]] = {
    "admin": ("Acceso total: usuarios, roles, índice, reportes", set(PERMISSIONS), None),
    "supervisor": ("Consulta y revisa conversaciones y uso de todos los usuarios",
                   {"chat", "chat.agent", "chat.debug", "documents.read",
                    "conversations.read_all", "reports.read", "kb.upload_own",
                    "kb.upload_role", "agents.quote", "invoices.report"}, None),
    "usuario": ("Hace preguntas y ve solo sus conversaciones",
                {"chat", "chat.agent", "documents.read", "kb.upload_own"}, None),
}

# Permissions added after the first release: existing databases get them once
# (init_db, marker `perm_version` in app_settings). Admins can remove them afterwards.
PERMISSION_UPGRADES: dict[int, dict[str, set[str]]] = {
    2: {"admin": {"kb.upload_own", "kb.upload_role", "kb.manage", "prompts.manage",
                  "limits.manage"},
        "supervisor": {"kb.upload_own", "kb.upload_role"},
        "usuario": {"kb.upload_own"}},
    3: {"admin": {"agents.quote"}, "supervisor": {"agents.quote"}},
    4: {"admin": {"invoices.report"}, "supervisor": {"invoices.report"},
        "importaciones": {"invoices.report"}},
}

# Roles for the import / export team, created once (init_db) when missing. They are normal
# roles: the admin can rename their description, change permissions or delete them.
TRADE_ROLES: dict[str, tuple[str, set[str]]] = {
    "importaciones": ("Compras e importaciones: proveedores, costos, existencias y cotización",
                      {"chat", "chat.agent", "documents.read", "kb.upload_own",
                       "kb.upload_role", "agents.quote", "invoices.report"}),
    "exportaciones": ("Ventas y exportaciones: cotizaciones a clientes y hojas técnicas",
                      {"chat", "chat.agent", "documents.read", "kb.upload_own",
                       "kb.upload_role", "agents.quote"}),
}
PERM_VERSION = max(PERMISSION_UPGRADES)

# ---- preferences ----------------------------------------------------------------------
DEFAULT_PREFERENCES: dict = {
    "deep_search": False,      # "Búsqueda profunda" checked by default
    "show_steps": False,       # "Mostrar pasos" (needs chat.debug)
    "theme": "auto",           # auto | light | dark
    "sidebar_open": True,
    "kbs": None,               # knowledge bases selected in the chat (None = defaults)
    "prompt_id": None,         # prompt profile selected in the chat (None = role default)
    "mode": "chat",            # chat | quote (multi-agent quote, needs agents.quote)
}


def merged_preferences(stored: dict | None) -> dict:
    out = dict(DEFAULT_PREFERENCES)
    for k, v in (stored or {}).items():
        if k in DEFAULT_PREFERENCES:
            out[k] = v
    return out


# ---- passwords: PBKDF2-HMAC-SHA256 (standard library, no compiled deps on Windows) ----
_ALGO, _ITER = "pbkdf2_sha256", 390_000


def hash_password(password: str, iterations: int = _ITER) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode("ascii").rstrip("=")  # noqa: E731
    return f"{_ALGO}${iterations}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        salt, expected = base64.b64decode(pad(salt_b64)), base64.b64decode(pad(dk_b64))
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


# A real hash to compare against when the user does not exist (same timing either way).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(12))


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def password_problem(password: str, min_length: int) -> str:
    if len(password or "") < min_length:
        return f"La contraseña debe tener al menos {min_length} caracteres."
    if password.isdigit() or password.isalpha():
        return "La contraseña debe combinar letras y números."
    return ""


# ---- session tokens: random, only the SHA-256 goes to the database ----------------------
def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
