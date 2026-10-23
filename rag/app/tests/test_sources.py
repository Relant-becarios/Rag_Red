"""Paths from the .env + separation of sources (manuals / datasheets / drawings / invoices).

    python -m pytest -q app/tests/test_sources.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as base  # noqa: E402,F401  (sets the test environment before app imports)

from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
TMP = Path(tempfile.mkdtemp(prefix="relant_sources_test_"))
DATA, INDEX = TMP / "data", TMP / "index"


def _write(rel: str, text: str) -> None:
    p = DATA / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_data() -> None:
    shutil.rmtree(TMP, ignore_errors=True)
    # the SAME topic (pressure of the BP-200) in a datasheet, a manual and an invoice
    _write("hojas_tecnicas/bombas/BP-200_Bomba.txt",
           "Hoja técnica BP-200 bomba de pistón. Presión máxima 220 bar. Caudal 1,2 l/min.")
    _write("manuales/abnox/Manual_BP-200.txt",
           "Manual de operación BP-200. Antes del mantenimiento libere la presión del "
           "sistema. Presión de prueba recomendada 150 bar. Mantenimiento cada 500 horas.")
    # a file NAMED 'ficha tecnica' inside the manuals folder is still a manual
    _write("manuales/abnox/Ficha tecnica vieja BP-200.txt",
           "Ficha técnica antigua BP-200 presión máxima 180 bar (obsoleta).")
    _write("invoices/IMP 377/FICHA TECNICA BP-200.txt",
           "Commercial invoice no INV-9001. Item BP-200 piston pump presión 999 bar qty 2 "
           "unit cost 1500.00 total 3000.00 HS 8413.50.01 rate 5%")
    _write("productos/bombas.csv",
           "Número de parte;Nombre del producto;Presión máx.;Caudal\n"
           "BP-200;Bomba de pistón BP-200;220 bar;1,2 l/min\n")
    _write("precios_existencias/precios.csv",
           "Número de parte;Nombre del producto;Costo;Precio lista;Existencia\n"
           "BP-200;Bomba de pistón BP-200;9.000,00;12.500,00;4\n")
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
    for rel in ("drawings/explosionados/BP-200_explosionado.png", "drawings/fotos/BP-200.png",
                "imagenes_sueltas/BP-200.png"):             # the last one is NOT a drawing folder
        (DATA / rel).parent.mkdir(parents=True, exist_ok=True)
        (DATA / rel).write_bytes(png)


def new_retriever():
    from app.retriever import HybridEvaluationRetriever
    r = HybridEvaluationRetriever(data_dir=str(DATA), chroma_dir=str(INDEX / "chroma"),
                                  manifest_file=str(INDEX / "manifest.json"),
                                  embeddings=DeterministicFakeEmbedding(size=48))
    r.update_index()
    return r


class FakeLLM:
    """Fast path: echoes the ids it received. Invoice extraction: a fixed JSON."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def invoke(self, messages):
        system, user = messages[0].content, messages[-1].content
        self.calls.append(system[:30])
        usage = {"input_tokens": 10, "output_tokens": 5}
        if system.startswith("You read the text of a commercial invoice"):
            items = [{"part_number": "BP-200", "description": "piston pump", "qty": "2",
                      "unit_cost": "1500.00", "total_cost": "3000.00", "currency": "USD",
                      "tariff_code": "8413.50.01", "tariff_rate": "5%", "page": 1}]
            return SimpleNamespace(content=json.dumps({"items": items}), usage_metadata=usage)
        ids = [ln[1:].split("]")[0] for ln in user.splitlines() if ln.startswith("[")]
        return SimpleNamespace(content="Respuesta " + " ".join(f"[{i}]" for i in ids),
                               usage_metadata=usage)


# ---------------------------------------------------------------------------- tests
def test_paths_come_from_env() -> None:
    """Missing path variable -> clear error; relative paths -> RAG_BASE_DIR, not the cwd."""
    env = {k: v for k, v in os.environ.items()}
    env["RAG_ENV_FILE"] = str(TMP / "none.env")
    env["PYTHONPATH"] = str(ROOT)
    env.pop("RAG_DIR_MANUALES", None)
    code = "import app.config"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "RAG_DIR_MANUALES" in r.stderr, r.stderr[-500:]

    env["RAG_DIR_MANUALES"] = "manuales"
    env["RAG_BASE_DIR"] = str(TMP / "base")
    env["RAG_DATA_DIR"] = "datos/drive"
    env["RAG_DB_DIR"] = "db"
    code = ("from app.config import settings; import json; "
            "print(json.dumps([settings.data_dir, settings.index_dir, settings.chroma_dir]))")
    r = subprocess.run([sys.executable, "-c", code], cwd=tempfile.gettempdir(), env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    data_dir, index_dir, chroma = json.loads(r.stdout.strip().splitlines()[-1])
    base_dir = (TMP / "base").resolve()
    assert Path(data_dir) == base_dir / "datos" / "drive", data_dir
    assert Path(index_dir) == base_dir / "db" and Path(chroma) == base_dir / "db" / "chroma"

    # a section folder outside RAG_DATA_DIR is refused
    env["RAG_DIR_MANUALES"] = str(TMP / "otra_carpeta")
    r = subprocess.run([sys.executable, "-c", "import app.config"], cwd=str(ROOT), env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "fuera de RAG_DATA_DIR" in r.stderr, r.stderr[-500:]


def test_sources_are_separated() -> None:
    from app.config import settings
    from app.products import get_product_index
    from app.router import QueryRouter
    from app.tools import new_context
    base.reset_database()
    make_data()
    r = new_retriever()
    cat = r.snapshot.catalog

    # catalog type is decided by the folder declared in the .env, not by the file name
    assert cat["hojas_tecnicas/bombas/BP-200_Bomba.txt"]["type"] == "datasheet"
    assert cat["manuales/abnox/Ficha tecnica vieja BP-200.txt"]["type"] == "manual"
    assert cat["invoices/IMP 377/FICHA TECNICA BP-200.txt"]["type"] == "invoice"
    assert cat["manuales/abnox/Manual_BP-200.txt"]["manufacturer"] == "ABNOX"
    # images only inside the drawing folders
    assert "drawings/explosionados/BP-200_explosionado.png" in cat
    assert "imagenes_sueltas/BP-200.png" not in cat
    assert settings.section_of("drawings/fotos/x.jpg") == "fotos"
    assert settings.section_of("@base-x/Drawings/Explosionados/x.png") == "explosionados"

    # products: family CSV + photo + exploded + stock; the 'ficha técnica' of the invoice
    # folder and of the manuals folder are NOT the product's datasheet
    p = get_product_index(r).find("BP-200")[0]
    assert p.image and p.image["rel"] == "drawings/fotos/BP-200.png"
    assert [e["rel"] for e in p.exploded] == ["drawings/explosionados/BP-200_explosionado.png"]
    assert p.stock, "price row not joined"

    llm = FakeLLM()
    router = QueryRouter(r, llm)

    # technical data -> only datasheets / product CSV
    res = router.answer("¿Cuál es la presión máxima de la bomba?", debug=True)
    secs = {s["section"] for s in res.sources if s.get("section")}
    assert res.source_route == "datasheet", res.source_route
    assert secs and secs <= {"hojas_tecnicas", "productos"}, res.sources
    assert "{DATASHEET}" in res.transcript[-1] and "{MANUAL}" not in res.transcript[-1]

    # procedure -> only manuals
    res = router.answer("¿Cada cuánto se hace el mantenimiento de la bomba?", debug=True)
    secs = {s["section"] for s in res.sources if s.get("section")}
    assert res.source_route == "manual" and secs == {"manuales"}, (res.source_route, res.sources)

    # the user names the manual explicitly -> manual even for a 'technical' word
    res = router.answer("según el manual, ¿qué presión de prueba se usa?", debug=True)
    secs = {s["section"] for s in res.sources if s.get("section")}
    assert res.source_route == "manual" and secs == {"manuales"}, res.sources

    # the agent tools are separated too, and every note says its source
    ctx = new_context(r)
    out = ctx.tools["manual_search"](query="presión BP-200")
    assert "{MANUAL}" in out and "{DATASHEET}" not in out and "{INVOICE" not in out, out
    out = ctx.tools["datasheet_search"](query="presión BP-200")
    assert "{MANUAL}" not in out and ("{DATASHEET}" in out or "{PRODUCT DATA" in out), out
    out = ctx.tools["drawing_search"](query="BP-200")
    assert "{EXPLODED VIEW}" in out or "{PRODUCT PHOTO}" in out, out
    out = ctx.tools["semantic_search"](query="presión BP-200", source="invoice")
    assert out.count("{INVOICE / IMPORT DOCUMENT}") >= 1 and "{MANUAL}" not in out, out

    # prefer: nothing relevant in the preferred source -> widen, and say so
    res = router.answer("presión máxima de la ZQ-1 en el catálogo de pistolas")
    assert res.source_route in ("datasheet->all", "datasheet", ""), res.source_route

    # invoice report: extracted once, verified against the text, then cached
    from app import invoice_report as ir
    assert ir.is_invoice_report_question("genera el reporte de invoices con costos")
    rep = ir.build_report(r, llm, ctx.meter)
    assert rep["files"] == 1 and rep["extracted"] == 1 and rep["items"][0]["verified"], rep
    rep2 = ir.build_report(r, llm, ctx.meter)
    assert rep2["extracted"] == 0 and rep2["cached"] == 1
    assert ir.cached_items(r, part="BP-200") and "8413.50.01" in ir.to_csv(rep2["items"])
    assert "| BP-200 |" in ir.to_markdown(rep2, ir.csv_url(rep2["filters"], ["general"]))
    print("sources ok")


def test_external_kb_roots() -> None:
    from app.config import settings
    old = settings.kb_external_roots
    try:
        settings.kb_external_roots = str((TMP / "permitida").resolve())
        assert settings.external_root_ok(str(TMP / "permitida" / "sub"))
        assert not settings.external_root_ok(str(TMP / "otra"))
        assert not settings.external_root_ok(str(TMP / "permitida" / ".." / "otra"))
    finally:
        settings.kb_external_roots = old


if __name__ == "__main__":
    test_paths_come_from_env()
    test_sources_are_separated()
    test_external_kb_roots()
    print("ALL OK")
