"""Texto de documentos de Word para el RAG: .docx (Word 2007+) y .doc (Word 97-2003).

.docx  -> sólo librería estándar (zipfile + XML). Se respeta el orden del documento:
          títulos (estilos Heading/Título -> '#'), párrafos, listas y TABLAS (cada fila
          como 'Encabezado: valor | ...', igual que los CSV), más encabezados / pies de
          página, notas al pie y comentarios.
.doc   -> parser binario en Python puro con `olefile` (pip install olefile); funciona en
          Windows y Linux sin Word ni LibreOffice instalados. Si falla (archivo cifrado,
          formato Word 6/95 ...) se intenta, si existen, LibreOffice (soffice) o antiword.

Uso: read_word_text(path) -> str   (lanza RuntimeError con un mensaje claro si no se puede)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger("rag.word")

WORD_EXTS = (".docx", ".doc")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_RE = re.compile(r"(?i)^(heading|t[ií]tulo|titre|berschrift|überschrift)\s*(\d)")


def read_word_text(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return _clean(_docx_text(p))
    if ext == ".doc":
        # muchos ".doc" en realidad son .docx / RTF / HTML renombrados
        with open(p, "rb") as f:
            magic = f.read(8)
        if magic.startswith(b"PK"):
            return _clean(_docx_text(p))
        if magic.startswith(b"{\\rtf"):
            return _clean(_rtf_text(p.read_bytes().decode("latin-1", "ignore")))
        if magic.lstrip().lower().startswith((b"<html", b"<!doc", b"<?xml", b"mime")):
            raw = p.read_bytes().decode("utf-8", "ignore")
            return _clean(re.sub(r"<[^>]+>", " ", raw))
        errors = []
        try:
            return _clean(_doc_text(p))
        except Exception as e:                      # noqa: BLE001
            errors.append(f"olefile: {e}")
        for fn in (_doc_via_soffice, _doc_via_antiword):
            try:
                txt = fn(p)
                if txt and txt.strip():
                    return _clean(txt)
            except Exception as e:                  # noqa: BLE001
                errors.append(f"{fn.__name__}: {e}")
        raise RuntimeError(f"No se pudo leer el Word .doc '{p.name}' ({'; '.join(errors)}). "
                           "Ábrelo en Word y guárdalo como .docx.")
    raise RuntimeError(f"No es un documento de Word: {p.name}")


# =========================================================================================
# .docx
# =========================================================================================
def _docx_text(p: Path) -> str:
    try:
        z = zipfile.ZipFile(p)
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"'{p.name}' no es un .docx válido (¿protegido con contraseña?)") from e
    with z:
        names = set(z.namelist())
        styles = _docx_styles(z) if "word/styles.xml" in names else {}
        parts = []
        main = "word/document.xml"
        if main not in names:
            raise RuntimeError(f"'{p.name}' no tiene word/document.xml")
        parts.append(_docx_part(z.read(main), styles))
        extra = sorted(n for n in names if re.match(r"word/(header|footer)\d*\.xml$", n))
        hf = [t for t in (_docx_part(z.read(n), styles) for n in extra) if t.strip()]
        seen, uniq = set(), []
        for t in hf:                                # el mismo encabezado en cada sección
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        if uniq:
            parts.append("Encabezados / pies de página:\n" + "\n".join(uniq))
        for n, label in (("word/footnotes.xml", "Notas al pie"),
                         ("word/endnotes.xml", "Notas finales"),
                         ("word/comments.xml", "Comentarios")):
            if n in names:
                t = _docx_part(z.read(n), styles)
                if t.strip():
                    parts.append(f"{label}:\n{t}")
    return "\n\n".join(parts)


def _docx_styles(z: zipfile.ZipFile) -> dict[str, int]:
    """styleId -> nivel de título (1..9) para los estilos de título."""
    out: dict[str, int] = {}
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except ET.ParseError:
        return out
    for st in root.iter(f"{_W}style"):
        sid = st.get(f"{_W}styleId") or ""
        name_el = st.find(f"{_W}name")
        name = name_el.get(f"{_W}val") if name_el is not None else ""
        for cand in (name or "", sid):
            m = _HEADING_RE.match(cand.strip())
            if m:
                out[sid] = int(m.group(2))
                break
        else:
            low = (name or "").lower()
            if low in ("title", "título", "titulo"):
                out[sid] = 1
            elif low.startswith(("list bullet", "list number", "list paragraph", "lista",
                                 "párrafo de lista", "parrafo de lista")):
                out[sid] = 0                        # 0 = viñeta / lista
    return out


def _docx_part(xml: bytes, styles: dict[str, int]) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    lines: list[str] = []
    for container in _body_containers(root):
        _walk_block(container, styles, lines)
    return "\n".join(lines)


def _body_containers(root: ET.Element) -> list[ET.Element]:
    body = root.find(f"{_W}body")
    if body is not None:
        return [body]
    # header/footer (w:hdr/w:ftr) o notas/comentarios (varios hijos)
    tag = root.tag.replace(_W, "")
    if tag in ("hdr", "ftr"):
        return [root]
    return [c for c in root if c.tag in (f"{_W}footnote", f"{_W}endnote", f"{_W}comment")
            and c.get(f"{_W}type") not in ("separator", "continuationSeparator",
                                           "continuationNotice")]


def _walk_block(el: ET.Element, styles: dict[str, int], lines: list[str]) -> None:
    for child in el:
        tag = child.tag
        if tag == f"{_W}p":
            t = _para_text(child, styles)
            if t:
                lines.append(t)
        elif tag == f"{_W}tbl":
            lines.extend(_table_lines(child, styles))
            lines.append("")
        elif tag in (f"{_W}sdt", f"{_W}sdtContent", f"{_W}customXml", f"{_W}ins",
                     f"{_W}smartTag"):
            _walk_block(child, styles, lines)


def _para_text(p: ET.Element, styles: dict[str, int]) -> str:
    buf: list[str] = []
    for node in p.iter():
        tag = node.tag
        if tag == f"{_W}t":
            buf.append(node.text or "")
        elif tag == f"{_W}tab":
            buf.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            buf.append("\n")
        elif tag == f"{_W}noBreakHyphen":
            buf.append("-")
        elif tag == f"{_W}sym":
            buf.append(" ")
        elif tag == f"{_W}delText":                # texto borrado con control de cambios
            continue
    text = "".join(buf).strip()
    if not text:
        return ""
    ppr = p.find(f"{_W}pPr")
    if ppr is not None:
        ps = ppr.find(f"{_W}pStyle")
        sid = ps.get(f"{_W}val") if ps is not None else ""
        lvl = styles.get(sid or "")
        if lvl == 0:
            return f"- {text}"
        ol = ppr.find(f"{_W}outlineLvl")
        if lvl is None and ol is not None:
            try:
                v = int(ol.get(f"{_W}val") or "9")
                lvl = v + 1 if v < 9 else None
            except ValueError:
                lvl = None
        if lvl:
            return f"{'#' * min(lvl, 6)} {text}"
        if ppr.find(f"{_W}numPr") is not None:
            return f"- {text}"
    return text


def _table_lines(tbl: ET.Element, styles: dict[str, int]) -> list[str]:
    rows: list[list[str]] = []
    for tr in tbl.findall(f"{_W}tr"):
        cells = []
        for tc in tr.findall(f"{_W}tc"):
            inner: list[str] = []
            _walk_block(tc, styles, inner)
            cells.append(" ".join(" ".join(x.split()) for x in inner if x.strip()))
        if any(c for c in cells):
            rows.append(cells)
    return _format_table(rows)


def _format_table(rows: list[list[str]]) -> list[str]:
    """Primera fila = encabezados (si parece encabezado) -> 'Col: valor | Col: valor'."""
    if not rows:
        return []
    head = rows[0]
    use_head = (len(rows) > 1 and len(head) > 1 and all(head)
                and len(set(head)) == len(head) and all(len(h) <= 60 for h in head))
    out = ["[TABLA]"]
    if use_head:
        out.append("Columnas: " + " | ".join(head))
        for n, r in enumerate(rows[1:], start=1):
            pairs = [f"{h}: {v}" if h else v for h, v in zip(head, r) if v]
            pairs += [v for v in r[len(head):] if v]
            out.append(f"Fila {n}: " + " | ".join(pairs))
    else:
        out.extend(" | ".join(c for c in r if c) for r in rows)
    return out


# =========================================================================================
# .doc (Word 97-2003, formato binario) — texto vía la tabla de piezas (CLX/PlcPcd)
# =========================================================================================
def _doc_text(p: Path) -> str:
    try:
        import olefile  # type: ignore
    except ImportError as e:
        raise RuntimeError("falta olefile (pip install olefile)") from e
    if not olefile.isOleFile(str(p)):
        raise RuntimeError("no es un archivo OLE de Word 97-2003")
    with olefile.OleFileIO(str(p)) as ole:
        if not ole.exists("WordDocument"):
            raise RuntimeError("no contiene el flujo WordDocument")
        wd = ole.openstream("WordDocument").read()
        if len(wd) < 0x01AA:
            raise RuntimeError("FIB demasiado corto (¿Word 6/95?)")
        ident, nfib = struct.unpack_from("<HH", wd, 0)
        if ident != 0xA5EC:
            raise RuntimeError("firma de Word inválida")
        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        if flags & 0x0100:                          # fEncrypted
            raise RuntimeError("el documento está protegido con contraseña")
        if nfib < 0x00C1:                           # < Word 97
            raise RuntimeError("formato Word 6/95 no soportado")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise RuntimeError(f"falta el flujo {table_name}")
        table = ole.openstream(table_name).read()
        # FibRgLw97: ccpText y compañía (texto principal, notas, encabezados, ...)
        ccp = struct.unpack_from("<iiiiiii", wd, 0x4C)
        ccp_total = sum(max(0, c) for c in ccp)
        fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
        if not lcb_clx or fc_clx + lcb_clx > len(table):
            raise RuntimeError("no se encontró la tabla de piezas (CLX)")
        clx = table[fc_clx:fc_clx + lcb_clx]
    text = _pieces_text(wd, clx)
    if ccp_total and len(text) > ccp_total:
        text = text[:ccp_total]
    main_len = max(0, ccp[0])
    main, rest = text[:main_len] if main_len else text, text[main_len:] if main_len else ""
    out = _doc_cleanup(main)
    rest = _doc_cleanup(rest).strip()
    if rest:
        out += "\n\nNotas / encabezados / pies de página:\n" + rest
    return out


def _pieces_text(wd: bytes, clx: bytes) -> str:
    i = 0
    while i < len(clx) and clx[i] == 0x01:          # Prc (propiedades): se saltan
        cb = struct.unpack_from("<h", clx, i + 1)[0]
        i += 3 + cb
    if i >= len(clx) or clx[i] != 0x02:
        raise RuntimeError("CLX sin Pcdt")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (len(plc) - 4) // 12
    if n <= 0:
        return ""
    cps = struct.unpack_from(f"<{n + 1}i", plc, 0)
    out = []
    for k in range(n):
        off = 4 * (n + 1) + 8 * k
        fc = struct.unpack_from("<I", plc, off + 2)[0]
        length = cps[k + 1] - cps[k]
        if length <= 0:
            continue
        if fc & 0x40000000:                         # comprimido: 1 byte por carácter (cp1252)
            start = (fc & ~0x40000000) // 2
            out.append(wd[start:start + length].decode("cp1252", "replace"))
        else:                                       # UTF-16LE
            out.append(wd[fc:fc + 2 * length].decode("utf-16-le", "replace"))
    return "".join(out)


def _doc_cleanup(t: str) -> str:
    # campos: \x13 instrucción \x14 resultado \x15 -> sólo el resultado
    res, stack = [], []                             # stack: True = dentro de la instrucción
    for ch in t:
        if ch == "\x13":
            stack.append(True)
            continue
        if ch == "\x14":
            if stack:
                stack[-1] = False
            continue
        if ch == "\x15":
            if stack:
                stack.pop()
            continue
        if stack and stack[-1]:
            continue
        res.append(ch)
    t = "".join(res)
    t = t.replace("\x0b", "\n").replace("\x0c", "\n")
    t = t.replace("\x1e", "-").replace("\x1f", "").replace("\xa0", " ")
    # tablas: cada celda termina en \x07 y la fila en otra marca \x07
    t = t.replace("\x07\x07", "\x07\ue000\r").replace("\r", "\n")
    t = re.sub(r"[\x00-\x06\x08\x0e-\x1d]", "", t)
    out: list[str] = []
    rows: list[list[str]] = []
    for line in t.split("\n"):
        if line.endswith("\ue000"):
            cells = [" ".join(c.split()) for c in line[:-1].split("\x07")]
            if cells and cells[-1] == "":
                cells.pop()
            if any(cells):
                rows.append(cells)
            continue
        if rows:
            out.extend(_format_table(rows) + [""])
            rows = []
        out.append(line.replace("\x07", " | "))
    if rows:
        out.extend(_format_table(rows))
    return "\n".join(out)


# ----- alternativas externas para .doc (sólo si están instaladas) -----------------------
def _doc_via_soffice(p: Path) -> str:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe and os.name == "nt":
        for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
            if Path(c).is_file():
                exe = c
                break
    if not exe:
        raise RuntimeError("LibreOffice no instalado")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([exe, "--headless", "--convert-to", "docx", "--outdir", td, str(p)],
                       check=True, capture_output=True, timeout=120)
        out = Path(td) / (p.stem + ".docx")
        if not out.is_file():
            raise RuntimeError("LibreOffice no generó el .docx")
        return _docx_text(out)


def _doc_via_antiword(p: Path) -> str:
    exe = shutil.which("antiword")
    if not exe:
        raise RuntimeError("antiword no instalado")
    r = subprocess.run([exe, "-w", "0", str(p)], check=True, capture_output=True, timeout=60)
    return r.stdout.decode("utf-8", "ignore")


# ----- RTF disfrazado de .doc -------------------------------------------------------------
def _rtf_text(rtf: str) -> str:
    rtf = re.sub(r"\{\\\*[^{}]*\}", "", rtf)
    rtf = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), rtf)
    rtf = re.sub(r"\\(par|line|row)\b ?", "\n", rtf)
    rtf = re.sub(r"\\cell\b ?", " | ", rtf)
    rtf = re.sub(r"\\tab\b ?", "\t", rtf)
    rtf = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), rtf)
    rtf = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", rtf)
    return rtf.replace("{", "").replace("}", "").replace("\\\\", "\\")


def _clean(t: str) -> str:
    t = t.replace("\r\n", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
