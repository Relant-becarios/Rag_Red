"""End-to-end test of the product index + HTML datasheets with the REAL retriever on a
sample data/ folder (fake embeddings, no vision, no API key needed).

    python test_products.py          builds ./sample_data, indexes it, writes hoja_*.html
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as _base  # noqa: E402,F401  (sets the test environment before app imports)

ROOT = Path(tempfile.mkdtemp(prefix="relant_products_test_"))
DATA, INDEX = ROOT / "sample_data", ROOT / "sample_index"

from langchain_core.embeddings import Embeddings  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


class FakeEmb(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        v = [0.0] * 16
        for i, ch in enumerate(text[:400]):
            v[(ord(ch) + i) % 16] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]


def make_data():
    shutil.rmtree(DATA, ignore_errors=True)
    shutil.rmtree(INDEX, ignore_errors=True)
    for d in ("productos", "precios_existencias", "drawings/fotos", "drawings/explosionados",
              "hojas_tecnicas/bombas", "manuales/abnox"):
        (DATA / d).mkdir(parents=True, exist_ok=True)
    # Excel-style CSVs (';' and decimal comma, as exported in Mexico/Europe)
    (DATA / "productos/bombas.csv").write_text(
        "Número de parte;Nombre del producto;Presión máx.;Caudal;Conexión de salida;Peso;Notas internas\n"
        "BP-200;Bomba de pistón BP-200;20-220 bar;1,2 l/min;G 1/8\";3,5 kg;no mostrar\n"
        "BP-350;Bomba de pistón BP-350;350 bar;0,8 l/min;G 1/4\";4,1 kg;\n", encoding="utf-8")
    (DATA / "productos/valvulas_atomizado.csv").write_text(
        "No. de parte;Producto;Presión de aire;Boquilla;Material\n"
        "VA-10;Válvula de atomizado VA-10;2-6 bar;0,5 mm;Acero inoxidable\n", encoding="utf-8")
    (DATA / "precios_existencias/precios_existencias.csv").write_text(
        "Número de parte;Nombre del producto;Costo;Precio lista;Existencia\n"
        "BP-200;Bomba de pistón BP-200;9.000,00;12.500,00;4\n"
        "BP-350;Bomba de pistón BP-350;11.000,00;16.900,00;0\n"
        "VA-10;Válvula de atomizado VA-10;1.200,00;2.100,00;15\n"
        "XX-999;Producto descontinuado;10,00;20,00;1\n", encoding="utf-8")

    def img(path, label, color):
        im = Image.new("RGB", (900, 700), "white")
        d = ImageDraw.Draw(im)
        d.rectangle([150, 120, 750, 580], outline=color, width=12)
        d.ellipse([330, 250, 570, 450], fill=color)
        d.text((330, 610), label, fill="black")
        im.save(path)
    img(DATA / "drawings/fotos/BP-200.jpg", "BP-200", (40, 90, 140))
    img(DATA / "drawings/explosionados/BP-200_explosionado.png", "BP-200 exploded", (200, 30, 40))
    img(DATA / "drawings/fotos/VA-10.jpg", "VA-10", (30, 120, 90))
    img(DATA / "drawings/fotos/foto_sin_producto.jpg", "?", (120, 120, 120))

    import pymupdf as fitz
    for path, text in ((DATA / "hojas_tecnicas/bombas/BP-350_Bomba de piston.pdf",
                        "Hoja tecnica - Bomba de piston BP-350 - Presion max. 350 bar"),
                       (DATA / "manuales/abnox/manual_dosificador.pdf",
                        "ABNOX operating instructions dosing valve")):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), text)
        doc.save(str(path))
        doc.close()


def main():
    make_data()
    from app.products import build_datasheet_html, get_product_index, write_all_datasheets
    from app.retriever import HybridEvaluationRetriever

    r = HybridEvaluationRetriever(data_dir=str(DATA), chroma_dir=str(INDEX / "chroma"),
                                  manifest_file=str(INDEX / "manifest.json"),
                                  embeddings=FakeEmb())
    sync = r.update_index()
    print("indexed:", sync["found"], "files")
    for e in sorted(r.snapshot.catalog.values(), key=lambda x: x["rel"]):
        print(f"  {e['rel']:55s} {e['type']:10s} manufacturer={e['manufacturer']!r}")

    idx = get_product_index(r)
    rep = idx.report()
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    assert rep["products"] == 3
    assert rep["with_photo"] == 2 and rep["with_exploded"] == 1 and rep["with_pdf"] == 1
    assert rep["stock_rows_without_product"] == ["XX-999"]
    assert rep["images_without_product"] == ["drawings/fotos/foto_sin_producto.jpg"]

    bp = idx.find("bp200")[0]                       # spelling-insensitive part number
    assert bp.image["rel"].endswith("BP-200.jpg") and len(bp.exploded) == 1
    rows = dict(bp.spec_rows())
    assert "Notas internas" not in rows and "Número de parte" not in rows
    assert rows["Presión máx."] == "20-220 bar"
    assert idx.find("Válvula de atomizado VA-10")[0].part == "VA-10"     # by name
    assert idx.find("BP-350")[0].pdfs == ["hojas_tecnicas/bombas/BP-350_Bomba de piston.pdf"]
    assert dict(idx.find("BP-200")[0].stock)["Existencia"] == "4"

    page = build_datasheet_html(bp)
    for must in ("Bomba de pistón BP-200", "20-220 bar", "Vista explosionada", "data:image/jpeg"):
        assert must in page, must
    assert "9.000" not in page and "12.500" not in page, "price/cost leaked into the sheet"
    Path(ROOT / "hoja_BP-200.html").write_text(page, encoding="utf-8")
    Path(ROOT / "hoja_BP-350.html").write_text(build_datasheet_html(idx.find("BP-350")[0]),
                                               encoding="utf-8")

    # the tool the agent uses, and the batch export
    from app.tools import make_tools
    hits = []
    print(make_tools(r, hits)["product_datasheet"](part="BP-350"))
    assert hits[0]["kind"] == "datasheet" and hits[0]["source"] == "/datasheet/BP-350"
    print(write_all_datasheets(r, out_dir=ROOT / "hojas_generadas"))

    # PDF datasheet (app/pdfdatagen.py): same rows, no prices, photo + exploded view
    import pymupdf as fitz
    from app.products import build_datasheet_pdf, datasheet_pdf_filename
    from app.tools import RequestContext
    pdf = build_datasheet_pdf(bp)
    assert pdf[:5] == b"%PDF-", "not a PDF"
    (ROOT / datasheet_pdf_filename(bp)).write_bytes(pdf)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        text = "".join(pg.get_text() for pg in doc)
        n_images = sum(len(pg.get_images()) for pg in doc)
    for must in ("Bomba de pistón BP-200", "20-220 bar", "Vista explosionada"):
        assert must in text, must
    assert "9.000" not in text and "no mostrar" not in text, "price/internal note leaked into PDF"
    assert n_images >= 3, n_images                # logo + photo + exploded view
    va = build_datasheet_pdf(idx.find("BP-350")[0])   # no photo: still a valid PDF
    assert va[:5] == b"%PDF-"
    assert datasheet_pdf_filename(bp) == "Ficha_Tecnica_BOMBAS_BP-200.pdf"
    ctx = RequestContext(retriever=r)
    make_tools(r, [], ctx)["product_datasheet"](part="BP-200")
    assert ctx.downloads and ctx.downloads[0]["url"] == "/datasheet-pdf/BP-200", ctx.downloads
    from app.router import QueryRouter
    md = QueryRouter._with_downloads("La hoja técnica [datasheet:BP-200].", ctx, False)
    assert "](/datasheet-pdf/BP-200)" in md and QueryRouter._with_downloads("x", ctx, True) == "x"
    print("OK")


def test_products() -> None:
    try:
        main()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    test_products()
