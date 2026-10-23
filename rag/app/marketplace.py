"""Marketplace: el catálogo de la tienda en línea dentro del RAG.

El marketplace (Vue, Marketplace-main) es el sitio principal. Su catálogo vive en una hoja
de Google Sheets publicada como CSV (src/api/inventory.ts) y las existencias "vivas" en
Firebase (nodo `inventario`). Este módulo lee ESE MISMO origen, así el asistente sólo
recomienda lo que la tienda realmente vende, con el mismo SKU, precio y stock que ve el
cliente:

    MarketplaceCatalog        descarga (CSV o JSON) + caché con TTL + stock de Firebase
      .search(query)          búsqueda por requerimiento: códigos, medidas (1/4", 90°,
                              0.8 mm), atributos (tipo de rosca, manguera, "para válvulas",
                              equivalentes...), sinónimos es/en; sin LLM, sin tokens
    MarketplaceAdvisor        una pregunta del cliente ->
                              1. pregunta autónoma (seguimiento de la conversación)
                              2. candidatos del catálogo
                              3. respuesta técnica del RAG (router de siempre: hojas
                                 técnicas, manuales, dibujos) — opcional
                              4. UNA llamada al LLM "asesor" que cruza requerimiento +
                                 datos técnicos + catálogo y recomienda productos con
                                 la etiqueta [mp:SKU]
                              -> respuesta + tarjetas de producto + fuentes públicas

Los nombres de columna se reconocen igual que en inventory.ts ("no. De parte",
"Descripcion", "Precio", "Stock", "Categoria", "Imagen_Front_URL"...), y el precio se
convierte a USD con las mismas tasas, para que el asistente y la tienda nunca discrepen.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

log = logging.getLogger("rag.marketplace")

# ----------------------------------------------------------------------------- columnas
# mismas listas que src/api/inventory.ts (obtenerCampo) + las de Prisma (Server.js)
ID_COLS = ("no. de parte", "no de parte", "no_de_parte", "codigo/id", "id", "codigo",
           "código", "sku", "numero de parte", "número de parte", "part number")
NAME_COLS = ("descripcion", "descripción", "producto", "nombre", "name")
PRICE_COLS = ("precio", "costo", "price")       # "Costo" es fallback de inventory.ts
STOCK_COLS = ("stock", "existencia", "existencias")
CAT_COLS = ("categoria", "categoría", "category")
IMG_FRONT_COLS = ("imagen_front_url", "photo_front_url", "imagen_front")
IMG_BACK_COLS = ("imagen_back_url", "photo_back_url", "imagen_back")
IMG_EXPL_COLS = ("imagen_explosionada_url", "imagen_explosionada", "diagrama")
IMG_GEN_COLS = ("imagen_url", "imagen", "imagenurl", "image")
EQUIV_COLS = ("equivalentes", "equivalente", "equivalents")
# columnas que no son atributos del producto (fotos sueltas, columnas sin nombre)
_NOT_ATTR = re.compile(r"^(photo|foto|imagen|image|unnamed|_\d*$|column\d*|updatedat|"
                       r"createdat|vistoen)", re.I)

# misma conversión que convertirADolares() de inventory.ts
TASAS_USD = {"MXN": 0.05, "MXP": 0.05, "EUR": 1.1, "USD": 1.0}


def _key(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def to_usd(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    t = str(value).upper().strip()
    num = re.sub(r"[^0-9.]", "", t.replace(",", ""))
    try:
        amount = float(num) if num else 0.0
    except ValueError:
        amount = 0.0
    if "EUR" in t or "€" in t:
        return round(amount * TASAS_USD["EUR"], 2)
    if "MXN" in t or "MXP" in t:
        return round(amount * TASAS_USD["MXN"], 2)
    return round(amount, 2)


def market_key(sku: str) -> str:
    """Llave de Firebase / carrito (market.ts): minúsculas, sin . # $ [ ] /."""
    return re.sub(r"[.#$\[\]/]", "", str(sku or "").strip().lower())


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def code_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(s))


# ----------------------------------------------------------------------------- tokens
_TOKEN_RE = re.compile(r"\d+\s*/\s*\d+|\d+(?:\.\d+)?|[a-z0-9]+")
STOPWORDS = {
    "que", "cual", "cuales", "como", "para", "por", "con", "sin", "los", "las", "del", "una",
    "uno", "unos", "unas", "este", "esta", "ese", "esa", "son", "hay", "tiene", "tienen",
    "tienes", "dime", "sobre", "entre", "cuanto", "donde", "mas", "muy", "pero", "cuando",
    "puedo", "puede", "quiero", "necesito", "busco", "buscando", "ocupo", "requiero", "me",
    "mi", "mis", "de", "la", "el", "en", "un", "y", "o", "a", "al", "lo", "le", "se", "su",
    "sus", "es", "si", "no", "ya", "algo", "alguna", "alguno", "algun", "venden", "vendes",
    "manejan", "recomiendas", "recomienda", "recomiendan", "sirve", "sirva", "favor", "hola",
    "gracias", "producto", "productos", "tipo", "favor", "cotizar", "comprar", "precio",
    "the", "and", "for", "with", "what", "which", "how", "does", "are", "is", "can", "this",
    "that", "from", "about", "have", "has", "need", "tell", "give", "show", "much", "many",
    "do", "you", "i", "a", "an", "of", "to", "in", "my", "me", "any", "some", "buy",
}
# sinónimos (raíz sin acentos ni plural) -> términos equivalentes en el catálogo
SYNONYMS: dict[str, tuple[str, ...]] = {
    "manguera": ("hose", "tubo"), "hose": ("manguera",),
    "conexion": ("conector", "fitting", "racor", "conexione", "adaptador"),
    "conector": ("conexion", "fitting", "racor"), "fitting": ("conexion", "conector"),
    "racor": ("conexion", "conector"),
    "aguja": ("punta", "needle", "boquilla"), "punta": ("aguja", "boquilla"),
    "needle": ("aguja",), "boquilla": ("aguja", "punta", "nozzle"), "nozzle": ("boquilla",),
    "neumatica": ("neumatico", "aire", "pneumatic"), "neumatico": ("neumatica", "aire"),
    "aire": ("neumatica", "neumatico"),
    "hidraulica": ("hidraulico", "hydraulic"), "hidraulico": ("hidraulica",),
    "rosca": ("thread", "cuerda", "roscado"), "cuerda": ("rosca",),
    "valvula": ("valve",), "valve": ("valvula",),
    "codo": ("90", "angulo"), "extension": ("alargador", "extensione"),
    "atomizado": ("atomizador", "spray", "aspersion", "rociado"),
    "atomizador": ("atomizado",), "spray": ("atomizado",),
    "refaccion": ("repuesto", "spare", "pieza", "refaccione"),
    "repuesto": ("refaccion",), "pieza": ("refaccion",),
    "equipo": ("maquina", "bomba", "sistema"), "bomba": ("equipo", "pump"),
    "dosificar": ("dosificadora", "dosificador", "valvula"),
    "dosificadora": ("dosificar", "valvula"), "pulgada": ("in",),
    "grasa": ("lubricacion", "lubricante"), "aceite": ("lubricacion", "lubricante"),
}


def stem(t: str) -> str:
    """Plural español ligero: conexiones -> conexion, valvulas -> valvula, mm -> mm."""
    if t.isdigit() or "/" in t or "." in t or len(t) <= 3:
        return t
    if t.endswith("ones") and len(t) > 6:
        return t[:-2]
    if t.endswith("es") and len(t) > 5 and t[-3] in "lrnd":
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokens(text: str) -> list[str]:
    s = strip_accents(text)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)                    # 0,8 -> 0.8
    s = s.replace('"', " in ").replace("''", " in ").replace("°", " grados ")
    s = re.sub(r"\bpulg(?:adas?)?\b", " in ", s)
    out = []
    for m in _TOKEN_RE.findall(s):
        t = re.sub(r"\s+", "", m)
        if t.endswith(".0"):
            t = t[:-2]
        out.append(stem(t))
    return out


def query_terms(text: str) -> list[str]:
    seen, out = set(), []
    for t in tokens(text):
        if t in STOPWORDS or (len(t) < 2 and not t.isdigit()) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


# ----------------------------------------------------------------------------- producto
@dataclass
class MarketProduct:
    sku: str
    name: str
    category: str
    price: float
    stock: int
    image: str = ""
    image_back: str = ""
    image_exploded: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    equivalents: str = ""
    # índice de búsqueda (se arma una vez por carga)
    _fields: dict[str, set[str]] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return market_key(self.sku)

    @property
    def in_stock(self) -> bool:
        return self.stock > 0

    def public(self) -> dict:
        site = (settings.marketplace_site_url or "").rstrip("/")
        return {"sku": self.sku, "key": self.key, "name": self.name, "category": self.category,
                "price": self.price, "currency": settings.marketplace_currency,
                "stock": self.stock, "in_stock": self.in_stock, "image": self.image,
                "attributes": dict(self.attributes), "equivalents": self.equivalents,
                "url": f"{site}/catalogo?sku={self.sku}" if site else ""}

    def llm_line(self, detail: bool = True) -> str:
        stock = f"stock {self.stock}" if self.stock > 0 else "AGOTADO (stock 0)"
        head = (f"[mp:{self.sku}] {self.name} | {self.category} | "
                f"{self.price:.2f} {settings.marketplace_currency} | {stock}")
        if not detail:
            return head
        attrs = "; ".join(f"{k}: {v}" for k, v in self.attributes.items())
        if self.equivalents:
            attrs = (attrs + "; " if attrs else "") + f"equivalentes: {self.equivalents}"
        return head + (f"\n    {attrs}" if attrs else "")


def _hidden(col: str) -> bool:
    words = [w.strip() for w in strip_accents(settings.marketplace_hidden_columns).split(",")
             if w.strip()]
    k = strip_accents(col)
    return any(w in k for w in words)


def _pick(row: dict[str, Any], names: tuple[str, ...]) -> tuple[str, str]:
    """(columna, valor) de la primera columna no vacía de la lista (sin importar mayúsculas)."""
    index = {_key(k): k for k in row.keys() if k is not None}
    for n in names:
        k = index.get(_key(n))
        if k is not None:
            v = row.get(k)
            if v is not None and str(v).strip() != "":
                return k, str(v).strip()
    return "", ""


def _url(v: str) -> str:
    return v if v.startswith("http") else ""


def parse_rows(rows: list[dict[str, Any]]) -> list[MarketProduct]:
    out: list[MarketProduct] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        id_col, sku = _pick(row, ID_COLS)
        name_col, name = _pick(row, NAME_COLS)
        if not sku and not name:
            continue
        price_col, price_raw = _pick(row, PRICE_COLS)
        stock_col, stock_raw = _pick(row, STOCK_COLS)
        cat_col, cat = _pick(row, CAT_COLS)
        eq_col, equiv = _pick(row, EQUIV_COLS)
        used = {id_col, name_col, price_col, stock_col, cat_col, eq_col}
        imgs = {}
        for label, cols in (("front", IMG_FRONT_COLS), ("back", IMG_BACK_COLS),
                            ("expl", IMG_EXPL_COLS), ("gen", IMG_GEN_COLS)):
            c, v = _pick(row, cols)
            used.add(c)
            imgs[label] = _url(v)
        try:
            stock = int(float(str(stock_raw).replace(",", ""))) if stock_raw else 0
        except ValueError:
            stock = 0
        attrs: dict[str, str] = {}
        for k, v in row.items():
            if k is None or k in used or v is None or isinstance(v, (dict, list)):
                continue
            ks, vs = str(k).strip(), str(v).strip()
            if not ks or not vs or _NOT_ATTR.match(ks) or _hidden(ks) or vs.startswith("http"):
                continue
            if _key(ks) in {_key(x) for x in ID_COLS + NAME_COLS + PRICE_COLS}:
                continue
            attrs[ks] = vs
        p = MarketProduct(
            sku=sku or name, name=name or sku, category=cat or "General",
            price=to_usd(price_raw),        # el mismo precio que muestra la tienda
            stock=max(0, stock), image=imgs["front"] or imgs["gen"] or imgs["back"],
            image_back=imgs["back"], image_exploded=imgs["expl"], attributes=attrs,
            equivalents=equiv)
        out.append(p)
    return out


def parse_payload(text: str, content_type: str = "") -> list[dict[str, Any]]:
    """CSV (Google Sheets) o JSON (lista, {productos: [...]}, {items: [...]}, {data: [...]})."""
    body = text.lstrip("﻿").strip()
    if "json" in content_type or body[:1] in "[{":
        data = json.loads(body)
        if isinstance(data, dict):
            for k in ("productos", "products", "items", "data"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                data = list(data.values())     # Firebase: {id: {...}, ...}
        return [r for r in data if isinstance(r, dict)]
    return list(csv.DictReader(io.StringIO(body)))


# ----------------------------------------------------------------------------- catálogo
class MarketplaceCatalog:
    """Catálogo en memoria con TTL. Thread-safe; si una recarga falla se queda con la
    última copia buena (la tienda no se queda sin asistente por un error de red)."""

    FIELD_WEIGHTS = {"name": 3.0, "category": 2.0, "attrs": 1.5, "equiv": 2.0}

    def __init__(self, url: str | None = None, stock_url: str | None = None,
                 ttl: int | None = None, fetcher: Any = None) -> None:
        self.url = settings.marketplace_catalog_url if url is None else url
        self.stock_url = settings.marketplace_stock_url if stock_url is None else stock_url
        self.ttl = settings.marketplace_cache_secs if ttl is None else ttl
        self._fetch = fetcher or self._http_get
        self._lock = threading.Lock()
        self.products: list[MarketProduct] = []
        self.by_key: dict[str, MarketProduct] = {}
        self.by_code: dict[str, MarketProduct] = {}
        self.df: dict[str, int] = {}
        self.loaded_at = 0.0
        self.last_error = ""
        self.stock_overrides = 0

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    # -------------------------------------------------------------- carga
    @staticmethod
    def _http_get(url: str) -> tuple[str, str]:
        import urllib.request
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(f"{url}{sep}t={int(time.time())}",
                                     headers={"User-Agent": "RelantRAG-Marketplace/1.0"})
        with urllib.request.urlopen(req, timeout=settings.marketplace_fetch_timeout) as r:
            ct = r.headers.get("Content-Type", "")
            return r.read().decode("utf-8-sig", errors="replace"), ct

    def load_rows(self, rows: list[dict[str, Any]], stock: dict | None = None) -> None:
        products = parse_rows(rows)
        overrides = 0
        if stock:
            for p in products:
                v = stock.get(p.key)
                if v is not None:
                    try:
                        p.stock = max(0, int(float(v)))
                        overrides += 1
                    except (TypeError, ValueError):
                        pass
        self._index(products)
        self.stock_overrides = overrides

    def _index(self, products: list[MarketProduct]) -> None:
        df: dict[str, int] = {}
        by_key, by_code = {}, {}
        for p in products:
            attr_text = " ".join(f"{k} {v}" for k, v in p.attributes.items())
            p._fields = {"name": set(tokens(p.name)), "category": set(tokens(p.category)),
                         "attrs": set(tokens(attr_text)), "equiv": set(tokens(p.equivalents))}
            for t in set().union(*p._fields.values()):
                df[t] = df.get(t, 0) + 1
            by_key.setdefault(p.key, p)
            by_code.setdefault(code_norm(p.sku), p)
        with self._lock:
            self.products, self.by_key, self.by_code, self.df = products, by_key, by_code, df
            self.loaded_at = time.time()
            self.last_error = ""

    def refresh(self, force: bool = False) -> bool:
        if not self.enabled:
            return False
        if not force and self.products and time.time() - self.loaded_at < self.ttl:
            return True
        try:
            text, ct = self._fetch(self.url)
            rows = parse_payload(text, ct)
            stock = None
            if self.stock_url:
                try:
                    st_text, _ = self._fetch(self.stock_url)
                    stock = json.loads(st_text) or {}
                    if not isinstance(stock, dict):
                        stock = None
                except Exception as e:                 # stock vivo opcional
                    log.warning("marketplace: no se pudo leer el stock vivo: %s", e)
            self.load_rows(rows, stock)
            log.info("marketplace: %d productos cargados (%d con stock de Firebase)",
                     len(self.products), self.stock_overrides)
            return True
        except Exception as e:
            self.last_error = str(e)[:300]
            self.loaded_at = time.time() - max(0, self.ttl - 30)   # reintenta en ~30 s
            log.warning("marketplace: error al leer el catálogo (%s); se usa la copia "
                        "anterior (%d productos)", e, len(self.products))
            return bool(self.products)

    def ensure(self) -> None:
        self.refresh(force=False)

    # -------------------------------------------------------------- consulta
    def get(self, sku: str) -> MarketProduct | None:
        self.ensure()
        return self.by_key.get(market_key(sku)) or self.by_code.get(code_norm(sku))

    def categories(self) -> dict[str, int]:
        self.ensure()
        out: dict[str, int] = {}
        for p in self.products:
            out[p.category] = out.get(p.category, 0) + 1
        return out

    def _idf(self, t: str) -> float:
        n = max(1, len(self.products))
        return math.log(1 + n / (1 + self.df.get(t, 0)))

    def search(self, query: str, limit: int = 10, in_stock_only: bool = False,
               category: str = "") -> list[tuple[float, MarketProduct]]:
        """Puntaje por requerimiento: códigos exactos/parciales y equivalentes pesan más,
        luego términos (con sinónimos y medidas) ponderados por campo e IDF, más un bono
        por cobertura del requerimiento y otro pequeño por tener existencias."""
        self.ensure()
        terms = query_terms(query)
        q_codes = {code_norm(m) for m in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-./]{2,}", query or "")}
        q_codes = {c for c in q_codes if len(c) >= 3 and any(ch.isdigit() for ch in c)}
        expanded: list[tuple[str, float, str]] = [(t, 1.0, t) for t in terms]
        for t in terms:
            for s in SYNONYMS.get(t, ()):
                expanded.append((stem(s), 0.5, t))
        cat_f = strip_accents(category).strip()
        scored: list[tuple[float, MarketProduct]] = []
        for p in self.products:
            if in_stock_only and not p.in_stock:
                continue
            if cat_f and strip_accents(p.category) != cat_f:
                continue
            score, covered = 0.0, set()
            # códigos
            sku_n = code_norm(p.sku)
            eq_n = code_norm(p.equivalents)
            for c in q_codes:
                if c == sku_n:
                    score += 25
                    covered.add(c)
                elif len(c) >= 4 and (c in sku_n or (len(sku_n) >= 4 and sku_n in c)):
                    score += 8
                    covered.add(c)
                elif len(c) >= 4 and c in eq_n:
                    score += 12
                    covered.add(c)
            # términos
            for t, w, origin in expanded:
                best = 0.0
                for fname, ftoks in p._fields.items():
                    fw = self.FIELD_WEIGHTS[fname]
                    if t in ftoks:
                        best = max(best, fw)
                    elif len(t) >= 5 and any(x.startswith(t) or t.startswith(x)
                                             for x in ftoks if len(x) >= 5):
                        best = max(best, fw * 0.6)
                if best:
                    score += w * best * self._idf(t)
                    covered.add(origin)
            if score <= 0:
                continue
            wanted = len(terms) + len([c for c in q_codes if c not in terms])
            if wanted:
                score += 3.0 * len(covered) / wanted
            if p.in_stock:
                score *= 1.1
            scored.append((round(score, 3), p))
        scored.sort(key=lambda x: (-x[0], not x[1].in_stock, x[1].name))
        return scored[:limit]

    def status(self) -> dict:
        return {"enabled": self.enabled, "products": len(self.products),
                "categories": len({p.category for p in self.products}),
                "loaded_at": self.loaded_at, "age_secs": round(time.time() - self.loaded_at)
                if self.loaded_at else None, "stock_overrides": self.stock_overrides,
                "error": self.last_error}


_catalog: MarketplaceCatalog | None = None
_catalog_lock = threading.Lock()


def get_catalog() -> MarketplaceCatalog:
    global _catalog
    with _catalog_lock:
        if _catalog is None:
            _catalog = MarketplaceCatalog()
        return _catalog


def set_catalog(cat: MarketplaceCatalog | None) -> None:
    """Tests / recarga de configuración."""
    global _catalog
    with _catalog_lock:
        _catalog = cat


def format_candidates(found: list[tuple[float, MarketProduct]], header: str = "") -> str:
    if not found:
        return header + "(ningún producto del catálogo coincide con la búsqueda)"
    return header + "\n".join(p.llm_line() for _, p in found)


# ----------------------------------------------------------------------------- asesor
_TAG_RE = re.compile(r"\[mp:\s*([^\]\s][^\]]*?)\s*\]", re.I)


def mentioned_skus(text: str, catalog: MarketplaceCatalog) -> list[MarketProduct]:
    out, seen = [], set()
    for m in _TAG_RE.finditer(text or ""):
        p = catalog.get(m.group(1))
        if p is not None and p.key not in seen:
            seen.add(p.key)
            out.append(p)
    return out


def merge_usage(*usages: dict) -> dict:
    out = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "llm_seconds": 0.0,
           "est_cost_usd": 0.0, "by_stage": {}}
    for u in usages:
        if not u:
            continue
        for k in ("llm_calls", "input_tokens", "output_tokens"):
            out[k] += int(u.get(k) or 0)
        for k in ("llm_seconds", "est_cost_usd"):
            out[k] = round(out[k] + float(u.get(k) or 0), 6)
        for st, n in (u.get("by_stage") or {}).items():
            out["by_stage"][st] = out["by_stage"].get(st, 0) + int(n or 0)
    return out


@dataclass
class ShopperContext:
    viewing_sku: str = ""
    cart_skus: list[str] = field(default_factory=list)
    page: str = ""


class MarketplaceAdvisor:
    """Orquesta catálogo + RAG + LLM asesor para UNA pregunta del cliente."""

    def __init__(self, catalog: MarketplaceCatalog, llm: Any, router: Any = None) -> None:
        self.catalog, self.llm, self.router = catalog, llm, router

    def _context_block(self, ctx: ShopperContext) -> str:
        lines = []
        if ctx.viewing_sku:
            p = self.catalog.get(ctx.viewing_sku)
            if p is not None:
                lines.append("Producto que el cliente está viendo:\n  " + p.llm_line())
        cart = [p for p in (self.catalog.get(s) for s in ctx.cart_skus[:15]) if p is not None]
        if cart:
            lines.append("En su carrito:\n" + "\n".join("  " + p.llm_line(detail=False)
                                                        for p in cart))
        if ctx.page:
            lines.append(f"Página actual de la tienda: {ctx.page[:80]}")
        return "\n".join(lines) or "(sin contexto)"

    def candidates(self, query: str, ctx: ShopperContext) -> list[tuple[float, MarketProduct]]:
        n = max(3, settings.marketplace_candidates)
        found = self.catalog.search(query, limit=n)
        # lo que está viendo / tiene en el carrito también cuenta (compatibilidad:
        # "¿qué aguja le queda a esta válvula?")
        extra_q = " ".join(p.name for p in (self.catalog.get(s) for s in
                                            [ctx.viewing_sku] if s) if p is not None)
        if extra_q:
            have = {p.key for _, p in found}
            for sc, p in self.catalog.search(f"{query} {extra_q}", limit=n):
                if p.key not in have and len(found) < n + 4:
                    found.append((sc * 0.8, p))
        return found

    def catalog_overview(self, exclude: set[str]) -> str:
        """Catálogo chico (<= MARKETPLACE_FULL_CATALOG_MAX): lista compacta del resto para
        que el LLM encuentre coincidencias que la búsqueda léxica no vio."""
        limit = int(settings.marketplace_full_catalog_max or 0)
        rest = [p for p in self.catalog.products if p.key not in exclude]
        if not rest or len(self.catalog.products) > limit:
            cats = ", ".join(f"{c} ({n})" for c, n in self.catalog.categories().items())
            return f"Categorías del catálogo: {cats}" if cats else ""
        return "Resto del catálogo (resumen):\n" + "\n".join(p.llm_line(detail=False)
                                                            for p in rest)

    def advise(self, question: str, technical: str, ctx: ShopperContext,
               found: list[tuple[float, MarketProduct]], history: list[dict],
               meter: Any) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.prompts import MARKETPLACE_SYSTEM
        have = {p.key for _, p in found}
        conv = "\n".join(f"{'Cliente' if h['role'] == 'user' else 'Asesor'}: "
                         f"{h['content'][:settings.conversation_answer_chars]}"
                         for h in history[-6:])
        parts = [
            "STORE CATALOG — best matches for this question (full detail):\n"
            + format_candidates(found),
            self.catalog_overview(have),
            "TECHNICAL NOTES (from the technical documents):\n" + (technical or "(none)"),
            "CUSTOMER CONTEXT:\n" + self._context_block(ctx),
        ]
        if conv:
            parts.append("CONVERSATION SO FAR:\n" + conv)
        parts.append(f"CUSTOMER QUESTION: {question}")
        system = MARKETPLACE_SYSTEM.replace("{currency}", settings.marketplace_currency)
        return meter.invoke(self.llm, [SystemMessage(content=system),
                                       HumanMessage(content="\n\n".join(p for p in parts if p))],
                            stage="marketplace_advisor")

    @staticmethod
    def fallback_answer(found: list[tuple[float, MarketProduct]], technical: str) -> str:
        """Sin LLM disponible: lo que sí se sabe, sin inventar nada."""
        lines = []
        if technical:
            lines.append(technical.strip())
        if found:                     # sin LLM que filtre: sólo las coincidencias fuertes
            top = max(sc for sc, _ in found)
            found = [(sc, p) for sc, p in found if sc >= max(4.0, top * 0.35)] or found[:1]
        if found:
            lines.append("Estos productos de la tienda coinciden con tu búsqueda:")
            for _, p in found[:settings.marketplace_max_products]:
                st = "en existencia" if p.in_stock else "agotado"
                lines.append(f"- [mp:{p.sku}] {p.name} — {p.price:.2f} "
                             f"{settings.marketplace_currency} ({st})")
        else:
            lines.append("No encontré productos de la tienda que coincidan. ¿Puedes darme "
                         "más detalles (modelo de válvula, tipo y tamaño de rosca, fluido)?")
        return "\n".join(lines)
