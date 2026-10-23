"""Marketplace: catálogo de la tienda + asistente público (sin API key, sin red).

    python -m pytest -q app/tests/test_marketplace.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as base  # noqa: E402  (sets the test environment before app imports)

from app.prompts import ANSWER_SYSTEM, FAST_SYSTEM, MARKETPLACE_SYSTEM, TOOL_SYSTEM  # noqa: E402

# columnas reales de la hoja de Google Sheets de la tienda (+ una interna que no debe salir)
SHEET_CSV = (
    "Categoria,no. De parte,Descripcion,Stock,Precio,tipo de conexión,tamaño de rosca,"
    "tipo de rosca,angulo,tamaño de manguera,Diametro,Longitud,Clase,Para valvulas,"
    "Tiempo Espera,Photo_front,Photo_back,Imagen_Front_URL,Imagen_Back_URL,,,,,"
    "Costo interno,equivalentes\n"
    "REFACCIONES,111077,Aguja para válvula de 1.0 mm,19,1,,,,,,1.0 mm,,,VDV-25,,a.jpg,,"
    "https://img.example/111077.jpg,,,,,,0.40,\n"
    "REFACCIONES,110025,Aguja de carburo de tungsteno LV de 0.8 mm,19,1,,,,,,0.8 mm,,,"
    "VDV-25,,,,,,,,,,0.55,AX-110025\n"
    "REFACCIONES,6797,\"Aguja cal. 18, L-1/2\"\", a 90°\",0,1,,,,90°,,,1/2\",,,,,,,,,,,,0.10,\n"
    "CONEXIONES NEUMATICAS,PN-14,Conector recto neumático 1/4 NPT a manguera 6 mm,25,3.5,"
    "Recto,1/4,NPT,0°,6 mm,,,,,,,,,,,,,,1.2,\n"
    "CONEXIONES NEUMATICAS,PN-18,Codo neumático 1/8 BSPP a manguera 4 mm,4,4,Codo,1/8,"
    "BSPP,90°,4 mm,,,,,,,,,,,,,,1.5,\n"
    "CONEXIONES HIDRAULICAS,HC-38,Conexión hidráulica 3/8 NPT alta presión,2,180 MXN,"
    "Recta,3/8,NPT,0°,,,,Alta presión,,,,,,,,,,,60,\n"
)
FIREBASE_STOCK = {"110025": 7, "pn-14": 0}        # stock "vivo" de Firebase (nodo inventario)


def fake_fetch(url: str):
    if "stock" in url:
        return json.dumps(FIREBASE_STOCK), "application/json"
    return SHEET_CSV, "text/csv"


class MarketFakeLLM(base.FakeLLM):
    """FakeLLM de test_offline + el asesor de la tienda. Los prompts del router llegan con
    las instrucciones del marketplace anexadas (with_instructions): se recortan."""

    def __init__(self) -> None:
        super().__init__()
        self.advisor_inputs: list[str] = []

    def invoke(self, messages):
        system, user = messages[0].content, messages[-1].content
        usage = {"input_tokens": len(user) // 4, "output_tokens": 30}
        if system.startswith(MARKETPLACE_SYSTEM.split("{currency}")[0]):
            self.advisor_inputs.append(user)
            catalog = user.split("TECHNICAL NOTES")[0]
            tags = [ln.split("]")[0] + "]" for ln in catalog.splitlines() if ln.startswith("[mp:")]
            pick = " y ".join(tags[:2]) if tags else "nada del catálogo"
            return SimpleNamespace(content=f"Te recomiendo {pick}.", usage_metadata=usage)
        for base_prompt in (FAST_SYSTEM, TOOL_SYSTEM, ANSWER_SYSTEM):
            if system.startswith(base_prompt) and system != base_prompt:
                messages = [SimpleNamespace(content=base_prompt)] + list(messages[1:])
                break
        return super().invoke(messages)


def make_catalog():
    from app.marketplace import MarketplaceCatalog
    return MarketplaceCatalog(url="test://sheet.csv", stock_url="test://stock.json", ttl=600,
                              fetcher=fake_fetch)


def test_catalog_parse_and_search() -> None:
    cat = make_catalog()
    assert cat.refresh(force=True)
    assert len(cat.products) == 6, [p.sku for p in cat.products]
    a = cat.get("111077")
    assert a.name.startswith("Aguja para válvula") and a.stock == 19 and a.price == 1.0
    assert a.image == "https://img.example/111077.jpg"
    assert a.attributes.get("Para valvulas") == "VDV-25"
    # columnas internas y de fotos jamás llegan como atributo
    assert not any("costo" in k.lower() or k.lower().startswith("photo") for p in cat.products
                   for k in p.attributes), [p.attributes for p in cat.products]
    assert cat.get("110025").stock == 7 and cat.get("PN-14").stock == 0      # Firebase gana
    assert cat.get("HC-38").price == 9.0                                     # 180 MXN -> USD
    assert cat.get("6797").name == 'Aguja cal. 18, L-1/2", a 90°'

    top = lambda q, **kw: [p.sku for _, p in cat.search(q, **kw)]          # noqa: E731
    assert top("aguja de 0.8 mm")[0] == "110025", top("aguja de 0.8 mm")
    assert top("necesito un conector neumático de 1/4 NPT para manguera de 6mm")[0] == "PN-14"
    assert top("codo 1/8 bspp")[0] == "PN-18"
    assert top("6797")[0] == "6797"
    assert top("equivalente AX-110025")[0] == "110025"
    assert top("conexion hidraulica 3/8")[0] == "HC-38"
    assert "PN-14" not in top("conector neumatico", in_stock_only=True)
    assert top("refacciones para la VDV-25")[:2] == ["111077", "110025"] or \
        set(top("refacciones para la VDV-25")[:2]) == {"111077", "110025"}
    assert top("cotización de un barco") == []
    print("catalog ok:", cat.status())


def test_agent_tool() -> None:
    from app.marketplace import set_catalog
    from app.tools import make_tools
    set_catalog(make_catalog())
    hits: list = []
    tools = make_tools(SimpleNamespace(), hits)
    out = tools["marketplace_search"](query="aguja 0.8 mm", in_stock="true")
    assert out.splitlines()[1].startswith("[mp:110025]"), out
    assert hits and hits[0]["kind"] == "marketplace"
    assert "0.55" not in out                                      # costo interno
    print("agent tool ok")


def test_marketplace_api() -> None:
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.jobs import IndexJobManager
    from app.main import create_app
    from app.marketplace import set_catalog
    from app.production_router import ProductionRouter

    import tempfile
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from app.retriever import HybridEvaluationRetriever

    base.reset_database()
    tmp = Path(tempfile.mkdtemp(prefix="relant_market_test_"))   # datos propios: no toca
    data = tmp / "data"                                            # el índice de los demás
    (data / "manuales").mkdir(parents=True)
    (data / "drawings" / "abnox").mkdir(parents=True)
    (data / "manuales" / "Manual_VDV-25_agujas.txt").write_text(
        "Manual VDV-25. Para cordones finos use la aguja de 0.8 mm; para silicón espeso la "
        "de 1.0 mm. Cambie la aguja cada 500 horas.", encoding="utf-8")
    (data / "stock.csv").write_text("Article;Description;Stock;Cost\n110025;Aguja VDV-25;7;0.55\n",
                                    encoding="utf-8")
    (data / "drawings" / "abnox" / "VDV-25_exploded_view.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    r = HybridEvaluationRetriever(data_dir=str(data), chroma_dir=str(tmp / "index" / "chroma"),
                                  manifest_file=str(tmp / "index" / "manifest.json"),
                                  embeddings=DeterministicFakeEmbedding(size=48))
    r.update_index()
    llm = MarketFakeLLM()
    router = ProductionRouter(r, llm, use_cache=False)
    set_catalog(make_catalog())
    comps = {"retriever": r, "router": router, "jobs": IndexJobManager(r, run_preflight=False)}
    old_rate = settings.marketplace_rate_per_min
    settings.marketplace_rate_per_min = 100
    try:
        with TestClient(create_app(comps, auto_sync=False, web_warmup=False)) as c:
            h = c.get("/marketplace/health").json()
            assert h["ok"] and h["catalog"]["products"] == 6, h
            assert h["rag"].get("chunks", 0) > 0, h

            body = c.get("/marketplace/products", params={"q": "aguja 0.8 mm"}).json()
            assert body["items"][0]["sku"] == "110025", body
            assert "Costo interno" not in json.dumps(body)
            assert c.get("/marketplace/products/PN-18").json()["stock"] == 4

            # 1) pregunta de cliente -> RAG (manual) + catálogo -> tarjetas de producto
            q = "¿Qué aguja necesito para la VDV-25 para un cordón fino de 0.8 mm?"
            res = c.post("/marketplace/chat", json={
                "message": q, "context": {"viewing_sku": "111077", "cart_skus": ["PN-14"]}})
            assert res.status_code == 200, res.text
            res = res.json()
            assert res["products"] and res["products"][0]["sku"] == "110025", res
            assert res["products"][0]["match"] == "recommended"
            assert res["conversation_id"] and res["conversation_token"]
            sent = llm.advisor_inputs[-1]
            notes = sent.split("TECHNICAL NOTES")[1].split("CUSTOMER CONTEXT")[0]
            assert "Fast answer for" in notes, notes                     # respuesta del RAG
            assert "está viendo" in sent and "[mp:111077]" in sent       # contexto del cliente
            assert "0.55" not in sent and "Costo" not in sent              # nada interno
            for s in res["sources"]:                                      # sólo públicas
                assert s["url"].startswith(("/marketplace/", "http")), s
                assert not s["url"].endswith(".csv"), s
            print("chat ok:", res["answer"], [p["sku"] for p in res["products"]],
                  [s["url"] for s in res["sources"]])

            # 2) seguimiento con token -> misma conversación; token falso -> conversación nueva
            res2 = c.post("/marketplace/chat", json={
                "message": "¿y tienen conectores neumáticos de 1/4?",
                "conversation_id": res["conversation_id"],
                "conversation_token": res["conversation_token"]}).json()
            assert res2["conversation_id"] == res["conversation_id"], res2
            assert res2["products"][0]["sku"] in ("PN-14", "PN-18"), res2
            res3 = c.post("/marketplace/chat", json={
                "message": "hola", "conversation_id": res["conversation_id"],
                "conversation_token": "0" * 32}).json()
            assert res3["conversation_id"] != res["conversation_id"]

            # 3) archivos: tablas nunca, manuales sí
            assert c.get("/marketplace/files/stock.csv").status_code == 404
            assert c.get("/marketplace/files/manuales/Manual_VDV-25_agujas.txt").status_code == 200
            assert c.get("/marketplace/files/../../etc/passwd").status_code == 404

            # 4) la cuenta de servicio no sirve para entrar al RAG interno
            assert c.post("/auth/login", json={"username": "marketplace",
                                               "password": ""}).status_code in (401, 422)
            assert c.post("/chat", json={"query": "stock"}).status_code == 401

            # 5) límite por IP
            settings.marketplace_rate_per_min = 1
            assert c.post("/marketplace/chat", json={"message": "x"}).status_code == 429

            # 6) recargar catálogo: sólo con token de admin
            assert c.post("/marketplace/catalog/refresh").status_code == 403
            assert c.post("/marketplace/catalog/refresh",
                          headers={"X-Admin-Token": "secret"}).json()["products"] == 6
    finally:
        settings.marketplace_rate_per_min = old_rate
        set_catalog(None)
    print("marketplace api ok")


if __name__ == "__main__":
    test_catalog_parse_and_search()
    test_agent_tool()
    test_marketplace_api()
