"""Settings

TODAS las rutas vienen del .env (ninguna está escrita en el código):

* El .env que se lee es UNO solo: el de la variable de entorno RAG_ENV_FILE si existe
  (útil en IIS / web.config), si no el `.env` de la raíz del proyecto (junto a la carpeta
  app/). Ya no depende de la carpeta desde donde arranca uvicorn.
* Las rutas relativas del .env se resuelven contra RAG_BASE_DIR (si no se define, la
  carpeta donde está el .env), nunca contra el directorio de trabajo.
* Las carpetas de cada tipo de documento (productos, hojas técnicas, manuales, dibujos...)
  se declaran en el .env como RAG_DIR_* (relativas a RAG_DATA_DIR). Esa declaración es la
  que decide qué es cada archivo: un PDF dentro de RAG_DIR_MANUALES es un manual aunque su
  nombre diga "ficha técnica", y sólo RAG_DIR_HOJAS_TECNICAS cuenta como hoja técnica.
* Si falta una variable de ruta obligatoria, la app no arranca y dice cuál falta.

"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carpeta del código (no es una ruta de datos): sólo sirve para encontrar el .env por defecto.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("RAG_ENV_FILE") or (PROJECT_ROOT / ".env")).expanduser()

# ---- file types (fixed, not settings) -------------------------------------------------
PDF_EXTS = (".pdf",)
TEXT_EXTS = (".txt", ".md")
CSV_EXTS = (".csv",)
EXCEL_EXTS = (".xlsx", ".xlsm", ".xls")
WORD_EXTS = (".docx", ".doc")          # Word: texto + tablas (app/word_docs.py)
TABLE_EXTS = CSV_EXTS + EXCEL_EXTS     # tablas: CSV y Excel se leen igual (textutils.read_csv_table)
IMAGE_EXTS = (".png", ".jpg", ".jpeg")

# display name, keys searched in file path / first page (catalog)
MANUFACTURERS = (
    ("ABNOX", ("abnox",)),
    ("Schütze", ("schutze", "alfred schutze")),
    ("Soma", ("soma",)),
    ("Lubtec", ("lubtec",)),
    ("Walther Systemtechnik", ("walther",)),
)

# =========================================================================================
# Secciones de la carpeta de datos. La CARPETA de cada sección viene del .env (RAG_DIR_*);
# aquí sólo está qué significa cada sección.
# =========================================================================================
SECTION_ENV = {                       # sección -> variable del .env
    "productos": "RAG_DIR_PRODUCTOS",
    "precios": "RAG_DIR_PRECIOS",
    "fotos": "RAG_DIR_FOTOS",
    "explosionados": "RAG_DIR_EXPLOSIONADOS",
    "drawings": "RAG_DIR_DRAWINGS",
    "hojas_tecnicas": "RAG_DIR_HOJAS_TECNICAS",
    "manuales": "RAG_DIR_MANUALES",
    "catalogos": "RAG_DIR_CATALOGOS",
}
OTHER_SECTION = "otros"               # todo lo que no está en ninguna carpeta declarada
SECTION_DOC_TYPE = {                  # tipo de documento del catálogo, fijado por la carpeta
    "productos": "table", "precios": "table",
    "fotos": "drawing", "explosionados": "drawing", "drawings": "drawing",
    "hojas_tecnicas": "datasheet", "manuales": "manual", "catalogos": "catalogue",
}
SECTION_LABEL = {                     # etiqueta que ve el LLM junto a cada fragmento
    "productos": "PRODUCT DATA (product CSV)", "precios": "PRICE/STOCK LIST",
    "hojas_tecnicas": "DATASHEET", "manuales": "MANUAL", "catalogos": "CATALOGUE",
    "fotos": "PRODUCT PHOTO", "explosionados": "EXPLODED VIEW", "drawings": "DRAWING",
}
IMAGE_SECTIONS = ("fotos", "explosionados", "drawings")   # sólo aquí se indexan imágenes
# Fuentes que el agente / el router pueden pedir (nombre -> secciones)
SOURCE_GROUPS = {
    "datasheet": ("productos", "hojas_tecnicas"),
    "manual": ("manuales",),
    "drawing": ("explosionados", "drawings", "fotos"),
    "catalogue": ("catalogos",),
    "price": ("precios",),
    "other": (OTHER_SECTION,),
}
SOURCE_ALIASES = {
    "datasheet": "datasheet", "datasheets": "datasheet", "data sheet": "datasheet",
    "hoja tecnica": "datasheet", "hojas tecnicas": "datasheet", "ficha tecnica": "datasheet",
    "spec": "datasheet", "specs": "datasheet", "product": "datasheet", "producto": "datasheet",
    "manual": "manual", "manuals": "manual", "manuales": "manual", "instructivo": "manual",
    "instructions": "manual",
    "drawing": "drawing", "drawings": "drawing", "dibujo": "drawing", "dibujos": "drawing",
    "exploded": "drawing", "explosionado": "drawing", "despiece": "drawing", "plano": "drawing",
    "image": "drawing", "foto": "drawing",
    "catalogue": "catalogue", "catalog": "catalogue", "catalogo": "catalogue",
    "catalogos": "catalogue",
    "price": "price", "prices": "price", "precio": "price", "precios": "price",
    "stock": "price", "existencia": "price", "existencias": "price",
    "other": "other", "otros": "other",
}

WEB_SITES = {
    "abnox": {"name": "ABNOX", "start": "https://www.abnox.com/en",
              "sitemap": "https://www.abnox.com/sitemap.xml", "prefix": "/en"},
    "schuetze": {"name": "Alfred Schütze Apparatebau",
                 "start": "https://www.schuetze-gmbh.de/en/homepage",
                 "sitemap": "https://www.schuetze-gmbh.de/sitemap.xml", "prefix": "/en"},
}
WEB_SKIP = ("/legal-notice", "/data-privacy", "/privacy", "/imprint", "/jobs", "/career",
            "/return-of-goods", "/customer-login", "/login", "/search", "/cart", "/account")


class ConfigError(RuntimeError):
    """Falta o es inválida una variable del .env."""


def _dir_list(raw: str | None) -> tuple[str, ...]:
    """'drawings/fotos, Fotos' -> ('drawings/fotos', 'fotos'): relative, '/' separated,
    case-insensitive. Absolute values are left as they are (checked by the validator)."""
    out = []
    for part in str(raw or "").replace(";", ",").split(","):
        p = part.strip().strip('"').strip("'").replace("\\", "/")
        if not p:
            continue
        if not (p.startswith("/") or (len(p) > 1 and p[1] == ":")):
            p = p.strip("/")
            while p.startswith("./"):
                p = p[2:]
        out.append(p)
    return tuple(out)


@functools.lru_cache(maxsize=8)
def _section_prefixes(spec: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple:
    """((('drawings','fotos'), 'fotos'), (('drawings',), 'drawings'), ...) longest first,
    so 'drawings/fotos' wins over 'drawings'."""
    pref = []
    for section, dirs in spec:
        for d in dirs:
            segs = tuple(s.casefold() for s in d.split("/") if s and s != ".")
            if segs:
                pref.append((segs, section))
    pref.sort(key=lambda x: len(x[0]), reverse=True)
    return tuple(pref)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8-sig",
                                      extra="ignore", populate_by_name=True)

    # ---- OpenRouter ----
    openrouter_api_key: str = Field(
        "", validation_alias=AliasChoices("OPENROUTER_API_KEY_RELANT", "OPENROUTER_API_KEY"))
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-5.4-mini"        # fast path + final answers
    agent_model: str = ""                         # agent decisions ("" -> llm_model)
    vision_model: str = ""                        # drawing descriptions ("" -> llm_model)
    # Same model as 5.7 -> the vectors already in Chroma and in the cache stay valid.
    embed_model: str = "openai/text-embedding-3-small"
    temperature: float = 0.0
    llm_timeout: int = 90
    embed_timeout: int = 60
    max_retries: int = 3
    preflight: bool = True                        # check key + credits before indexing
    # USD per 1M tokens, only for the cost estimate returned with each answer.
    price_in_per_mtok: float = 0.25
    price_out_per_mtok: float = 2.00
    # ---- multilingual search: glossary (free) | llm (+1 small call if not English) | off
    query_translate: str = "glossary"

    # =====================================================================================
    # RUTAS (todas desde el .env). Relativas -> contra RAG_BASE_DIR.
    # =====================================================================================
    base_dir: str = Field("", validation_alias=AliasChoices("RAG_BASE_DIR"))
    # carpeta de documentos de la "Base general" (obligatoria)
    data_dir: str | None = Field(None, validation_alias=AliasChoices(
        "RAG_DATA_DIR", "RAG_PDF_DIR", "DATA_DIR"))
    # índice de la "Base general": chroma/, manifest.json, state.json (obligatoria)
    index_dir: str | None = Field(None, validation_alias=AliasChoices(
        "RAG_DB_DIR", "RAG_INDEX_DIR", "INDEX_DIR"))
    # multi-RAG: <RAG_KB_DIR>/shared|roles|users/<slug>/{files,index} (obligatoria)
    kb_dir: str | None = Field(None, validation_alias=AliasChoices("RAG_KB_DIR", "KB_DIR"))
    # páginas del chat / admin y logo (obligatorias)
    static_dir: str | None = Field(None, validation_alias=AliasChoices("RAG_STATIC_DIR"))
    datasheet_logo: str | None = Field(None, validation_alias=AliasChoices("RAG_DATASHEET_LOGO"))
    # opcionales: si se dejan vacías se derivan de RAG_DB_DIR
    chroma_dir_env: str = Field("", validation_alias=AliasChoices("RAG_CHROMA_DIR"))
    web_cache_dir_env: str = Field("", validation_alias=AliasChoices("RAG_WEB_CACHE_DIR"))
    datasheet_out_dir_env: str = Field("", validation_alias=AliasChoices("RAG_DATASHEET_OUT_DIR"))
    # opcional: config.json de app/pdfdatagen.py (plantilla HTML + colores por categoría
    # de las fichas técnicas PDF). Vacía = plantilla y colores por defecto.
    datasheet_pdf_config: str = Field("", validation_alias=AliasChoices("RAG_DATASHEET_PDF_CONFIG"))
    log_dir: str = Field("", validation_alias=AliasChoices("RAG_LOG_DIR"))   # "" = sólo consola
    # carpetas externas que un admin puede usar como base de conocimientos (/admin/kb).
    # Sólo rutas DENTRO de estas raíces (separadas por coma). Vacía = no se permiten.
    kb_external_roots: str = Field("", validation_alias=AliasChoices("RAG_KB_EXTERNAL_ROOTS"))

    # ---- secciones dentro de RAG_DATA_DIR (y dentro de files/ de cada base multi-RAG) ----
    # Relativas a la carpeta de datos; varias separadas por coma. Vacía = sección no usada.
    # Deben estar DECLARADAS en el .env (aunque sea vacías): si faltan, la app no arranca.
    dir_productos: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_PRODUCTOS"))
    dir_precios: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_PRECIOS"))
    dir_fotos: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_FOTOS"))
    dir_explosionados: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_EXPLOSIONADOS"))
    dir_drawings: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_DRAWINGS"))
    dir_hojas_tecnicas: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_HOJAS_TECNICAS"))
    dir_manuales: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_MANUALES"))
    dir_catalogos: str | None = Field(None, validation_alias=AliasChoices("RAG_DIR_CATALOGOS"))

    recursive: bool = True

    # ---- separación de fuentes (manuales vs hojas técnicas vs dibujos) ----
    # prefer = busca primero en la fuente que corresponde a la pregunta y sólo si ahí no hay
    #          nada usa el resto (etiquetado); strict = nunca sale de esa fuente;
    #          off = búsqueda mezclada como antes.
    source_routing: str = "prefer"
    # palabras (sin acentos, minúsculas) que indican de qué fuente debe salir la respuesta
    # palabras / frases completas (sin acentos) que indican de qué fuente sale la respuesta.
    # Se pueden reemplazar en el .env como lista JSON: INTENT_MANUAL_WORDS=["manual","falla"]
    intent_datasheet_words: tuple[str, ...] = (
        "especificacion", "especificaciones", "caracteristica", "caracteristicas",
        "datos tecnicos", "dato tecnico", "dimension", "dimensiones", "medida", "medidas",
        "peso", "presion", "presion maxima", "caudal", "conexion", "conexiones", "rosca",
        "material", "materiales", "temperatura", "voltaje", "tension", "potencia",
        "capacidad", "viscosidad", "rango", "rangos", "specification", "specifications",
        "specs", "pressure", "flow", "weight", "dimensions", "technical data")
    intent_manual_words: tuple[str, ...] = (
        "instalacion", "instalar", "mantenimiento", "operacion", "operar", "procedimiento",
        "paso a paso", "como se", "como hago", "como instalo", "falla", "fallas", "error",
        "errores", "alarma", "ajuste", "ajustar", "calibrar", "calibracion", "limpieza",
        "limpiar", "puesta en marcha", "arranque", "desmontar", "desmontaje", "montaje",
        "seguridad", "lubricacion", "troubleshooting", "maintenance", "installation",
        "operating", "how to")
    intent_drawing_words: tuple[str, ...] = (
        "posicion", "posiciones", "refaccion", "refacciones", "repuesto", "repuestos",
        "spare part", "spare parts", "pieza", "piezas", "foto", "fotos", "imagen",
        "imagenes", "ensamble")

    # ---- hybrid retriever ----
    top_k: int = 6
    # candidates taken from EACH list (vector, BM25) before fusion. 40 so the reranker
    # sees 30-40 fused candidates .
    candidate_pool: int = 40
    vector_weight: float = 0.4            # 5.7 min-max fusion only; unused since RRF (1.0)
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # ---- Production 1.0: Reciprocal Rank Fusion (replaces min-max + damping) ----
    rrf_k: int = 60                       # score = sum_i  w_i / (rrf_k + rank_i)
    rrf_vector_weight: float = 1.0
    rrf_bm25_weight: float = 1.0

    # ---- Production 1.0: cross-encoder reranker (multilingual ES/EN/DE) ----
    # local  = sentence-transformers CrossEncoder on this server (model downloaded once)
    # cohere = Cohere Rerank API (COHERE_API_KEY);  off = fused order only
    reranker_backend: str = "local"
    # small + fast on CPU; for best quality (GPU or patient CPU): BAAI/bge-reranker-v2-m3
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_candidates: int = 40         # fused candidates re-scored; top_k (8-10) kept
    reranker_max_chars: int = 1600        # characters of each chunk sent to the reranker
    reranker_batch: int = 16
    reranker_device: str = ""             # "" = auto (cuda if available), "cpu", "cuda"
    reranker_warmup: bool = True          # load the model in the background at API start
    cohere_api_key: str = Field("", validation_alias=AliasChoices("COHERE_API_KEY"))
    cohere_rerank_model: str = "rerank-v3.5"
    # chunks containing a part code that appears in the question are never pushed out of
    # the top-k by the reranker (cross-encoders are weak on codes like VDV-25)
    code_pinning: bool = True

    # ---- multilingual query (app/query_lang.py): QUERY_TRANSLATE is set above ----
    query_spell_fix: bool = True          # 'nozles' -> also search 'nozzles' (corpus words)

    # ---- Production 1.0: conversation context ----
    # auto = a follow-up ("¿y su caudal?", "what about the other one?") is rewritten into a
    #        standalone question from the last turns (1 small call to the agent model)
    # always = every question with history is rewritten;  off = each question alone (5.7)
    conversation_rewrite: str = "auto"
    conversation_history_turns: int = 3   # previous question/answer pairs used
    conversation_answer_chars: int = 700  # characters of each previous answer shown

    # ---- Production 1.0: BM25 analyzer ----
    # fold() accents/umlauts, Spanish + English stop words, Snowball stems (ES and EN),
    # plus the normalised part code (VDV-25 -> vdv25) as an extra token.
    bm25_stemming: bool = True

    # ---- Production 1.0: agent search tools ----
    max_queries_per_search: int = 2       # a search tool call may carry up to 2 queries
    max_query_chars: int = 300

    # ---- Production 1.0: free heuristic pre-route (no LLM call) ----
    preroute_enabled: bool = True

    # ---- Production 1.0: semantic answer cache (MySQL, table answer_cache) ----
    answer_cache_enabled: bool = True
    # text-embedding-3-small cosine. Conservative start; calibrate with
    #   python -m app.cli cache-pairs  ->  label  ->  python -m app.cli cache-calibrate
    answer_cache_threshold: float = 0.92
    answer_cache_log_floor: float = 0.75  # near-misses above this are logged for calibration
    answer_cache_max_per_scope: int = 500
    answer_cache_ttl_days: float = 30.0
    answer_cache_refresh_secs: float = 30.0   # other workers' new entries visible after this

    # ---- indexing ----
    auto_sync_on_start: bool = True   # background sync at API start (never blocks requests)
    embed_api_batch: int = 100
    save_every: int = 10
    embed_flush_docs: int = 500
    chroma_batch: int = 5000
    vector_max_table_rows: int = 2000
    csv_rows_per_chunk: int = 1
    csv_forward_fill_cols: int = 0
    describe_images: bool = True
    vision_workers: int = 4
    image_max_side: int = 1600
    reload_check_secs: float = 10.0   # other workers pick up a new index this often

    # ---- document-level tools ----
    list_docs_max: int = 25
    read_doc_max_chars: int = 8000
    read_table_max_rows: int = 60
    table_lookup_max_rows: int = 15

    # ---- router (fast path vs agent) ----
    fast_top_k: int = 8                    # chunks sent to the single fast-path LLM call
    fast_min_vector_score: float = 0.35    # below this AND low term coverage -> escalate
    fast_min_term_coverage: float = 0.5    # share of query words found in the top chunks
    fast_escalate_on_insufficient: bool = True   # LLM says INSUFFICIENT_CONTEXT -> agent
    agent_max_steps: int = 6
    run_baseline: bool = False             # True only for evaluation (doubles the cost)
    max_drawings: int = 3

    # ---- manufacturer websites ----
    web_enabled: bool = True
    web_cache_days: int = 14
    web_max_pages: int = 150
    web_timeout: int = 15
    web_workers: int = 4
    web_warmup: bool = True

    # ---- product master data + technical datasheets (app/products.py) ----
    part_col_aliases: tuple[str, ...] = (
        "numero de parte", "no de parte", "num de parte", "n de parte", "numero parte",
        "part number", "part no", "codigo", "clave", "sku", "articulo",
        "no parte", "num parte", "modelo", "model")
    name_col_aliases: tuple[str, ...] = (
        "nombre del producto", "nombre producto", "producto", "nombre", "product name",
        "name", "descripcion", "description")
    family_col_aliases: tuple[str, ...] = ("familia", "family", "linea", "categoria")
    datasheet_hide_cols: tuple[str, ...] = (
        "costo", "coste", "cost", "precio", "price", "pvp", "existencia", "existencias",
        "stock", "inventario", "almacen", "ubicacion", "proveedor", "margen", "notas internas",
        "imagen", "image", "foto", "archivo")
    # An image is an exploded view if its folder or file name contains one of these
    # (las de RAG_DIR_EXPLOSIONADOS siempre lo son)
    exploded_words: tuple[str, ...] = ("explosionad", "explosion", "exploded", "despiece",
                                       "explotad")
    datasheet_image_max_side: int = 1200          # embedded images are resized to this
    product_cache_secs: float = 30.0
    # Folders whose CSVs are product families (one row per product). RAG_DIR_PRODUCTOS is the
    # documented place, but Relant keeps the family CSVs (Valvulas_apertura_y_cierre.csv,
    # valvulas_volumetricas.csv ...) inside RAG_DIR_HOJAS_TECNICAS, so both are read.
    # .env: PRODUCT_TABLE_SECTIONS='["productos","hojas_tecnicas"]'
    product_table_sections: tuple[str, ...] = ("productos", "hojas_tecnicas")

    # ---- multi-agent quoting (app/multiagent.py) ----
    quote_min_margin: float = 0.25        # lines below this margin are flagged
    quote_max_revisions: int = 2          # supervisor may ask the quoting agent to redo
    quote_max_steps: int = 10
    quote_price_column: str = ""          # exact CSV header of the sale price (optional)
    quote_cost_column: str = ""           # exact CSV header of the purchase cost (optional)

    # ---- API ----
    admin_token: str = ""                  # header X-Admin-Token for /admin/index (CLI/scripts)
    cors_origins: str = ""                 # comma-separated, e.g. "http://localhost:5173"

    # ---- Marketplace (app/marketplace.py, app/marketplace_api.py) ----
    # Catálogo de productos del marketplace: el MISMO origen que lee la tienda (Vue).
    # CSV publicado de Google Sheets (…/pub?output=csv) o JSON (GET /api/products de
    # Server.js, o una lista de objetos). Vacío = asistente del marketplace desactivado.
    marketplace_catalog_url: str = ""
    # opcional: existencias "vivas" (Firebase Realtime DB, nodo inventario) que ganan sobre
    # el Stock de la hoja, p. ej. https://<proyecto>-default-rtdb.firebaseio.com/inventario.json
    marketplace_stock_url: str = ""
    marketplace_cache_secs: int = 120          # cada cuánto se vuelve a leer el catálogo
    marketplace_fetch_timeout: int = 20
    marketplace_currency: str = "USD"          # moneda en que la tienda muestra precios
    # URL pública de la tienda (para links en respuestas fuera del sitio); opcional
    marketplace_site_url: str = ""
    # columnas que JAMÁS salen al público (coinciden por subcadena, sin acentos)
    marketplace_hidden_columns: str = "costo,coste,cost,margen,margin,proveedor,supplier,utilidad"
    # cuenta de servicio con la que el marketplace consulta el RAG (se crea sola).
    # Sus límites de preguntas/tokens y sus bases de conocimiento se administran en /admin
    # como los de cualquier rol: dale SÓLO bases con información pública.
    marketplace_service_user: str = "marketplace"
    marketplace_role: str = "marketplace"
    marketplace_daily_query_limit: int = 2000  # para el rol cuando se crea (0 = sin límite)
    # firma de conversation_token (HMAC). Vacío = aleatorio por proceso (los tokens dejan
    # de valer al reiniciar: la conversación sigue, pero empieza un hilo nuevo)
    marketplace_secret: str = ""
    marketplace_rate_per_min: int = 12         # preguntas por minuto por IP (0 = sin límite)
    marketplace_max_products: int = 6          # tarjetas de producto por respuesta
    marketplace_candidates: int = 10           # candidatos del catálogo que ve el LLM
    marketplace_use_rag: bool = True           # False = sólo catálogo (sin documentos)
    # catálogo de hasta N productos: el asesor ve además una línea por cada producto
    # (así encuentra lo que la búsqueda por palabras no vio). 0 = sólo los candidatos
    marketplace_full_catalog_max: int = 250
    # bases de conocimiento que consulta el asistente público ("" = las del rol marketplace)
    marketplace_kbs: str = ""
    # secciones cuyos archivos (PDF, dibujos, fotos) se pueden abrir desde la tienda.
    # Tablas (CSV/Excel), precios, invoices y "otros" NUNCA se sirven al público.
    marketplace_public_sections: str = "hojas_tecnicas,manuales,catalogos,fotos,explosionados,drawings"

    # ---- application database (users, roles, conversations, audit, embedding cache) ----
    # DATABASE_URL completo, o vacío y las partes MYSQL_* (la clave queda fuera de la URL).
    # RAG_DATABASE_URL gana sobre DATABASE_URL: úsalo si el servidor también tiene un
    # DATABASE_URL de otra app (p. ej. el Postgres/Neon de Prisma de la tienda).
    database_url: str = Field("", validation_alias=AliasChoices("RAG_DATABASE_URL",
                                                                "DATABASE_URL"))
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "relant_rag"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800            # seconds; below MySQL wait_timeout
    db_echo: bool = False
    embed_cache_backend: str = "db"        # db (MySQL) | sqlite (old local file)

    # ---- authentication ----
    session_cookie_name: str = "relant_session"
    session_hours: float = 12.0
    session_cookie_secure: bool = False
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    password_min_length: int = 8
    trust_proxy_headers: bool = True
    initial_admin_username: str = ""
    initial_admin_password: str = ""

    # ---- multi-RAG: knowledge bases ----
    kb_max_upload_mb: float = 50.0          # per file
    kb_user_quota_mb: float = 200.0         # personal folder (0 = unlimited)
    kb_role_quota_mb: float = 2000.0        # role folder (0 = unlimited)
    kb_upload_exts: tuple[str, ...] = (".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx",
                                       ".xlsm", ".xls",
                                       ".png", ".jpg", ".jpeg")
    kb_auto_index_on_upload: bool = True

    # ---- token limits ----
    usage_cache_secs: float = 20.0
    limit_warn_pct: int = 80

    # =====================================================================================
    # validation: every path variable declared, relative paths -> RAG_BASE_DIR
    # =====================================================================================
    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        missing = []
        for env, attr in (("RAG_DATA_DIR", "data_dir"), ("RAG_DB_DIR", "index_dir"),
                          ("RAG_KB_DIR", "kb_dir"), ("RAG_STATIC_DIR", "static_dir"),
                          ("RAG_DATASHEET_LOGO", "datasheet_logo")):
            if not (getattr(self, attr) or "").strip():
                missing.append(env)
        for section, env in SECTION_ENV.items():
            if getattr(self, f"dir_{section}") is None:
                missing.append(env)
        if missing:
            raise ValueError(
                "Faltan rutas en el .env (" + str(ENV_FILE) + "): " + ", ".join(missing)
                + ". Todas las rutas se declaran en el .env; ver .env.example (raíz del proyecto).")

        base = Path(self.base_dir).expanduser() if self.base_dir.strip() else ENV_FILE.parent
        if not base.is_absolute():
            base = ENV_FILE.parent / base
        self.base_dir = str(base.resolve())

        def res(v: str) -> str:
            p = Path(str(v).strip().strip('"').strip("'")).expanduser()
            return str((p if p.is_absolute() else Path(self.base_dir) / p).resolve())

        for attr in ("data_dir", "index_dir", "kb_dir", "static_dir", "datasheet_logo"):
            setattr(self, attr, res(getattr(self, attr)))
        for attr in ("chroma_dir_env", "web_cache_dir_env", "datasheet_out_dir_env", "log_dir",
                     "datasheet_pdf_config"):
            if getattr(self, attr).strip():
                setattr(self, attr, res(getattr(self, attr)))

        self.kb_external_roots = ",".join(
            res(x) for x in str(self.kb_external_roots or "").replace(";", ",").split(",")
            if x.strip())

        data = Path(self.data_dir)
        for section in SECTION_ENV:
            dirs = []
            for d in _dir_list(getattr(self, f"dir_{section}")):
                p = Path(d)
                if p.is_absolute() or (len(d) > 1 and d[1] == ":"):
                    try:
                        d = p.resolve().relative_to(data).as_posix()
                    except ValueError:
                        raise ValueError(
                            f"{SECTION_ENV[section]}={d} está fuera de RAG_DATA_DIR ({data}). "
                            "Las carpetas de sección van relativas a RAG_DATA_DIR.") from None
                if ".." in d.split("/"):
                    raise ValueError(f"{SECTION_ENV[section]}: '..' no está permitido ({d})")
                dirs.append(d)
            setattr(self, f"dir_{section}", ",".join(dirs))
        self.source_routing = (self.source_routing or "prefer").strip().lower()
        if self.source_routing not in ("prefer", "strict", "off"):
            raise ValueError("SOURCE_ROUTING debe ser prefer, strict u off")
        if self.database_url.strip().lower().startswith(("postgres://", "postgresql://",
                                                          "prisma://")):
            # Es el DATABASE_URL de Prisma (tienda), no el del RAG: se ignora y se usa MYSQL_*
            import logging
            logging.getLogger("rag.config").warning(
                "DATABASE_URL apunta a Postgres (Prisma de la tienda); el RAG la ignora y usa "
                "MYSQL_*. Usa RAG_DATABASE_URL si quieres una URL completa para el RAG.")
            self.database_url = ""
        if not self.database_url.strip() and self.mysql_user.strip():
            self.database_url = (
                f"mysql+pymysql://{quote_plus(self.mysql_user)}:{quote_plus(self.mysql_password)}"
                f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4")
        return self

    # ---- sections ----
    def section_dirs(self, section: str) -> tuple[str, ...]:
        return _dir_list(getattr(self, f"dir_{section}", "") or "")

    def _section_spec(self) -> tuple:
        return tuple((s, self.section_dirs(s)) for s in SECTION_ENV)

    def section_of(self, rel: str) -> str:
        """Section of a file from its path relative to the data folder of its base
        ('@slug/' namespace allowed). Only the folders declared in the .env count."""
        r = str(rel or "").replace("\\", "/")
        if r.startswith("@") and "/" in r:
            r = r.split("/", 1)[1]
        folders = tuple(s.casefold() for s in r.split("/")[:-1] if s)
        for segs, section in _section_prefixes(self._section_spec()):
            if folders[:len(segs)] == segs:
                return section
        return OTHER_SECTION

    def section_root(self, rel: str) -> tuple[str, str]:
        """(section, path inside the section folder) — e.g. manuales/abnox/x.pdf ->
        ('manuales', 'abnox/x.pdf')."""
        r = str(rel or "").replace("\\", "/")
        if r.startswith("@") and "/" in r:
            r = r.split("/", 1)[1]
        parts = [s for s in r.split("/") if s]
        folders = tuple(s.casefold() for s in parts[:-1])
        for segs, section in _section_prefixes(self._section_spec()):
            if folders[:len(segs)] == segs:
                return section, "/".join(parts[len(segs):])
        return OTHER_SECTION, "/".join(parts)

    def external_root_ok(self, path: str) -> bool:
        """True if `path` (absolute) is inside one of RAG_KB_EXTERNAL_ROOTS."""
        try:
            target = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        for root in (r for r in self.kb_external_roots.split(",") if r):
            rp = Path(root)
            if target == rp or rp in target.parents:
                return True
        return False

    def first_dir(self, section: str) -> str:
        d = self.section_dirs(section)
        return d[0] if d else ""

    # ---- derived paths (layout the deployed index already uses) ----
    @property
    def chroma_dir(self) -> str:
        return self.chroma_dir_env or str(Path(self.index_dir) / "chroma")

    @property
    def manifest_file(self) -> str:
        return str(Path(self.index_dir) / "manifest.json")

    @property
    def state_file(self) -> str:
        return str(Path(self.index_dir) / "state.json")

    @property
    def sqlite_cache_file(self) -> str:
        """The old SQLite embedding cache (source for `python -m app.cli migrate-cache`)."""
        return self.manifest_file + ".cache.sqlite"

    @property
    def web_cache_dir(self) -> Path:
        return Path(self.web_cache_dir_env or Path(self.index_dir) / "web_cache")

    @property
    def datasheet_out_dir(self) -> Path:
        """Batch-generated HTML sheets. Outside data_dir on purpose: they must not be indexed
        (they would duplicate the CSV rows in every search)."""
        return Path(self.datasheet_out_dir_env or Path(self.index_dir) / "hojas_generadas")

    @property
    def effective_agent_model(self) -> str:
        return self.agent_model or self.llm_model

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.llm_model

    def paths_report(self) -> dict:
        """Todas las rutas efectivas (CLI `paths` y /health para administradores)."""
        out = {"env_file": str(ENV_FILE), "env_file_exists": ENV_FILE.is_file(),
               "RAG_BASE_DIR": self.base_dir, "RAG_DATA_DIR": self.data_dir,
               "RAG_DB_DIR": self.index_dir, "RAG_KB_DIR": self.kb_dir,
               "RAG_STATIC_DIR": self.static_dir, "RAG_DATASHEET_LOGO": self.datasheet_logo,
               "RAG_CHROMA_DIR": self.chroma_dir, "RAG_WEB_CACHE_DIR": str(self.web_cache_dir),
               "RAG_DATASHEET_OUT_DIR": str(self.datasheet_out_dir),
               "RAG_DATASHEET_PDF_CONFIG": self.datasheet_pdf_config or "(plantilla por defecto)",
               "RAG_LOG_DIR": self.log_dir or "(consola)",
               "RAG_KB_EXTERNAL_ROOTS": [r for r in self.kb_external_roots.split(",") if r]
               or "(no permitidas)"}
        for section, env in SECTION_ENV.items():
            out[env] = [str(Path(self.data_dir) / d) for d in self.section_dirs(section)] \
                or "(no usada)"
        return out


def load_settings() -> Settings:
    try:
        return Settings()
    except Exception as e:                        # pydantic ValidationError -> clear message
        msg = str(e)
        if "Faltan rutas" in msg or "RAG_" in msg or "SOURCE_ROUTING" in msg:
            first = next((ln.strip() for ln in msg.splitlines()
                          if "Faltan rutas" in ln or "RAG_" in ln or "SOURCE_ROUTING" in ln), msg)
            raise ConfigError(first.replace("Value error, ", "")) from None
        raise


settings = load_settings()
