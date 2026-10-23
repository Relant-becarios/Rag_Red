"""Draw the system as graphs (no LLM, no API calls).

Two different graphs live in this app, and both can be drawn:

1. CONTROL graphs (LangGraph): which function runs after which.
       QuoteCoordinator: supervisor -> quoting / margin ║ verifier -> finish
       HybridGraphAgent: decide -> act -> decide ... -> answer
   LangGraph draws them itself:  compiled.get_graph().draw_mermaid()
   (solid arrow = add_edge, dashed arrow = add_conditional_edges)

2. DATA graph (the relationships in data/): products, families, photos, exploded
   views, PDF datasheets, price rows, manufacturers and their documents.
   product_graph() builds it as {"nodes": [...], "links": [...]}, the format used by
   networkx, D3, force-graph (2D) and 3d-force-graph (3D).

    python -m app.cli graph --out grafo.html     -> one HTML page: 2D / 3D + Mermaid
    GET /graph                                   -> the same page from the API
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.catalog import DOC_TYPE_LABEL

# group -> label shown in the legend (colors come from CSS tokens in the page)
GROUPS = {
    "family": "Familia (CSV)", "product": "Producto", "photo": "Foto",
    "exploded": "Explosionada", "pdf": "Hoja técnica PDF", "price": "Precio / existencia",
    "manufacturer": "Fabricante", "document": "Documento (manual, catálogo…)",
    "table": "Otra tabla CSV",
}
RELATIONS = {
    "pertenece_a": "producto → familia", "foto": "producto → foto",
    "explosionada": "producto → vista explosionada", "hoja_pdf": "producto → PDF existente",
    "precio": "producto → fila de precios", "publica": "fabricante → documento",
}

# library versions pinned on purpose (UMD builds, global variables ForceGraph / ForceGraph3D)
CDN_2D = "https://cdn.jsdelivr.net/npm/force-graph@1.52.0/dist/force-graph.min.js"
CDN_3D = "https://cdn.jsdelivr.net/npm/3d-force-graph@1.80.1/dist/3d-force-graph.min.js"
CDN_MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js"


# =========================================================================================
# 1. LangGraph control graphs
# =========================================================================================
def langgraph_mermaid(compiled: Any) -> str:
    """Mermaid source of a compiled LangGraph, cleaned for any Mermaid renderer:
    no front-matter, no hard-coded purple colours, START / END instead of __start__."""
    src = compiled.get_graph().draw_mermaid()
    src = re.sub(r"^---.*?---\s*", "", src, flags=re.S)
    src = re.sub(r"\(\[<p>__start__</p>\]\)", "([START])", src)
    src = re.sub(r"\(\[<p>__end__</p>\]\)", "([END])", src)
    src = re.sub(r":::(first|last)", "", src)
    src = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("classDef"))
    return src.strip() + "\n"


def langgraph_edges(compiled: Any) -> dict:
    """The same graph as plain data (nodes + edges + conditional flag), e.g. for networkx."""
    g = compiled.get_graph()
    return {"nodes": [n for n in g.nodes],
            "edges": [{"source": e.source, "target": e.target, "conditional": e.conditional}
                      for e in g.edges]}


# =========================================================================================
# 2. Data graph
# =========================================================================================
def product_graph(retriever: Any, max_nodes: int = 4000) -> dict:
    """Nodes and links of everything in data/ that is related to something else."""
    from app.products import get_product_index
    idx = get_product_index(retriever).ensure()
    catalog = retriever.snapshot.catalog
    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def node(nid: str, label: str, group: str, **info: Any) -> str:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "group": group,
                          **{k: v for k, v in info.items() if v not in (None, "", [])}}
        return nid

    def link(a: str, b: str, rel: str) -> None:
        links.append({"source": a, "target": b, "rel": rel})

    for p in idx.products:
        fam = node(f"csv:{p.rel}", p.family, "family", file=p.rel)
        pid = node(f"prod:{p.key}", p.part or p.name, "product", name=p.name, part=p.part,
                   specs="; ".join(f"{h}: {v}" for h, v in p.spec_rows()[:12]),
                   url=f"/datasheet/{p.part or p.name}")
        link(pid, fam, "pertenece_a")
        if p.image:
            link(pid, node(f"file:{p.image['rel']}", Path(p.image["rel"]).name, "photo",
                           file=p.image["rel"], url=f"/files/{p.image['rel']}"), "foto")
        for x in p.exploded:
            link(pid, node(f"file:{x['rel']}", Path(x["rel"]).name, "exploded",
                           file=x["rel"], url=f"/files/{x['rel']}"), "explosionada")
        for pdf in p.pdfs:
            link(pid, node(f"file:{pdf}", Path(pdf).name, "pdf", file=pdf,
                           url=f"/files/{pdf}"), "hoja_pdf")
        if p.stock:
            st = dict(p.stock)
            show = {h: v for h, v in st.items()
                    if not re.search(r"cost|coste|margen|proveedor", h, re.I)}  # never cost
            link(pid, node(f"price:{p.key}", "precio/existencia", "price",
                           specs="; ".join(f"{h}: {v}" for h, v in show.items())), "precio")

    # manufacturers and their documents (manuals, catalogues, other tables)
    used_files = {n.get("file") for n in nodes.values()}
    for e in catalog.values():
        if e["rel"] in used_files:
            continue
        if e["type"] in ("drawing",) or not e.get("manufacturer"):
            continue
        m = node(f"mfr:{e['manufacturer'].lower()}", e["manufacturer"], "manufacturer")
        grp = "table" if e["type"] == "table" else "document"
        d = node(f"file:{e['rel']}", Path(e["rel"]).name, grp, file=e["rel"],
                 kind=DOC_TYPE_LABEL.get(e["type"], e["type"]), url=f"/files/{e['rel']}")
        link(m, d, "publica")

    deg = Counter()
    for l in links:
        deg[l["source"]] += 1
        deg[l["target"]] += 1
    out_nodes = list(nodes.values())
    if len(out_nodes) > max_nodes:                       # keep the best-connected nodes
        keep = {n["id"] for n in sorted(out_nodes, key=lambda n: -deg[n["id"]])[:max_nodes]}
        out_nodes = [n for n in out_nodes if n["id"] in keep]
        links = [l for l in links if l["source"] in keep and l["target"] in keep]
    for n in out_nodes:
        n["degree"] = deg[n["id"]]
    return {"nodes": out_nodes, "links": links,
            "stats": {"nodes": len(out_nodes), "links": len(links),
                      "by_group": dict(Counter(n["group"] for n in out_nodes)),
                      "by_relation": dict(Counter(l["rel"] for l in links)),
                      "truncated": len(nodes) > max_nodes}}


def to_networkx(graph: dict) -> Any:
    """For analysis in Python (centrality, components...):  pip install networkx"""
    import networkx as nx  # type: ignore
    g = nx.DiGraph()
    for n in graph["nodes"]:
        g.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for l in graph["links"]:
        g.add_edge(l["source"], l["target"], rel=l["rel"])
    return g


# =========================================================================================
# 3. The page (2D / 3D data graph + Mermaid control graphs)
# =========================================================================================
_CSS = """
:root { --bg:#f3f5f7; --surface:#fff; --fg:#17202b; --muted:#586474; --line:#c9d2dc; --accent:#1f6a86;
  --g-family:#1f6a86; --g-product:#c8102e; --g-photo:#2a7565; --g-exploded:#7a4fb0; --g-pdf:#a5600c;
  --g-price:#4f6b2a; --g-manufacturer:#17202b; --g-document:#8a94a3; --g-table:#5b8fb0;
  --f-display:"IBM Plex Sans Condensed","Arial Narrow",system-ui,sans-serif;
  --f-body:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --f-mono:"IBM Plex Mono",Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f141a; --surface:#161d25; --fg:#e2e8ee;
  --muted:#9aa6b4; --line:#2f3a47; --accent:#62b2d1; --g-family:#62b2d1; --g-product:#ff6b7f; --g-photo:#72c7b2;
  --g-exploded:#b69ae6; --g-pdf:#e6a752; --g-price:#a9cf6e; --g-manufacturer:#e2e8ee; --g-document:#7d8896; --g-table:#8fb9d6; color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#0f141a; --surface:#161d25; --fg:#e2e8ee; --muted:#9aa6b4; --line:#2f3a47; --accent:#62b2d1;
  --g-family:#62b2d1; --g-product:#ff6b7f; --g-photo:#72c7b2; --g-exploded:#b69ae6; --g-pdf:#e6a752; --g-price:#a9cf6e;
  --g-manufacturer:#e2e8ee; --g-document:#7d8896; --g-table:#8fb9d6; color-scheme:dark; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:15px/1.55 var(--f-body); margin:0; }
.wrap { max-width:1180px; margin:0 auto; padding-inline:20px; padding-block:26px 56px; display:grid; gap:34px; }
.eyebrow { font:500 12px/1 var(--f-mono); letter-spacing:.08em; text-transform:uppercase; color:var(--accent); }
h1 { font:600 clamp(26px,4vw,38px)/1.1 var(--f-display); margin:6px 0 8px; text-wrap:balance; }
h2 { font:600 22px/1.2 var(--f-display); margin:0; }
p { margin:0; max-width:76ch; } .muted { color:var(--muted); }
section { display:grid; gap:12px; min-width:0; }
code { font-family:var(--f-mono); font-size:.88em; background:var(--line); background:color-mix(in srgb, var(--line) 45%, transparent); padding:.05em .35em; border-radius:4px; }
.bar { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; }
.seg { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.seg button { font:500 14px var(--f-body); border:0; background:var(--surface); color:var(--fg); padding:7px 14px; cursor:pointer; }
.seg button[aria-pressed="true"] { background:var(--accent); color:var(--surface); }
.bar input { font:14px var(--f-body); border:1px solid var(--line); background:var(--surface); color:var(--fg); border-radius:8px; padding:7px 10px; min-width:0; flex:1 1 200px; max-width:320px; }
button:focus-visible, input:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.stage { display:grid; grid-template-columns:minmax(0,1fr) 270px; gap:14px; }
#graph { height:560px; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:var(--surface); position:relative; }
#graph .msg { position:absolute; inset:0; display:grid; place-items:center; color:var(--muted); padding:20px; text-align:center; }
aside { display:grid; gap:12px; align-content:start; min-width:0; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:grid; gap:6px; min-width:0; }
.card h3 { font:600 12px var(--f-display); letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:0; }
.legend { display:grid; gap:4px; font-size:13px; }
.legend label { display:flex; align-items:center; gap:8px; cursor:pointer; }
.dot { width:11px; height:11px; border-radius:50%; flex:none; }
.legend .n { margin-left:auto; color:var(--muted); font:12px var(--f-mono); font-variant-numeric:tabular-nums; }
#info { font-size:13.5px; overflow-wrap:anywhere; }
#info .k { color:var(--muted); font-size:12px; }
#info a { color:var(--accent); }
.mm { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }
.mm figure { margin:0; background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px; display:grid; gap:8px; min-width:0; overflow-x:auto; }
.mm figcaption { font-size:13px; color:var(--muted); }
pre.mermaid { margin:0; background:transparent; text-align:center; }
@media (max-width:820px) { .stage { grid-template-columns:minmax(0,1fr); } #graph { height:440px; } }
"""

_JS = r"""
(() => {
  const DATA = JSON.parse(document.getElementById("graph-data").textContent);
  const GROUPS = JSON.parse(document.getElementById("graph-groups").textContent);
  const REL = JSON.parse(document.getElementById("graph-rel").textContent);
  const box = document.getElementById("graph"), info = document.getElementById("info");
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const color = (g) => css("--g-" + g) || "#888";
  const hidden = new Set();
  let mode = "2d", fg = null, focus = null, query = "";

  // legend with counts + toggles
  const counts = {}; DATA.nodes.forEach((n) => counts[n.group] = (counts[n.group] || 0) + 1);
  const legend = document.getElementById("legend");
  Object.entries(GROUPS).filter(([g]) => counts[g]).forEach(([g, label]) => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="checkbox" checked id="grp-${g}"><span class="dot" data-g="${g}"></span>${label}<span class="n">${counts[g]}</span>`;
    l.querySelector("input").addEventListener("change", (e) => { e.target.checked ? hidden.delete(g) : hidden.add(g); refresh(); });
    legend.appendChild(l);
  });
  const paintLegend = () => legend.querySelectorAll(".dot").forEach((d) => d.style.background = color(d.dataset.g));

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const neighbours = (id) => { const s = new Set([id]); DATA.links.forEach((l) => { const a = l.source.id ?? l.source, b = l.target.id ?? l.target; if (a === id) s.add(b); if (b === id) s.add(a); }); return s; };
  let near = null;

  function visibleData() {
    const nodes = DATA.nodes.filter((n) => !hidden.has(n.group));
    const ids = new Set(nodes.map((n) => n.id));
    const links = DATA.links.filter((l) => ids.has(l.source.id ?? l.source) && ids.has(l.target.id ?? l.target));
    return { nodes, links };
  }
  function show(n) {
    focus = n; near = n ? neighbours(n.id) : null;
    if (!n) { info.innerHTML = '<span class="muted">Haz clic en un nodo para ver sus datos y sus relaciones.</span>'; return; }
    const rels = DATA.links.filter((l) => (l.source.id ?? l.source) === n.id || (l.target.id ?? l.target) === n.id)
      .map((l) => { const other = (l.source.id ?? l.source) === n.id ? (l.target.label ?? l.target) : (l.source.label ?? l.source); return `<li>${esc(l.rel)} · ${esc(other)}</li>`; }).join("");
    info.innerHTML = `<div class="k">${esc(GROUPS[n.group] || n.group)}</div><b>${esc(n.label)}</b>`
      + (n.name && n.name !== n.label ? `<div>${esc(n.name)}</div>` : "")
      + (n.kind ? `<div class="k">${esc(n.kind)}</div>` : "")
      + (n.file ? `<div class="k">${esc(n.file)}</div>` : "")
      + (n.specs ? `<div>${esc(n.specs)}</div>` : "")
      + (n.url && location.protocol !== "file:" ? `<div><a href="${esc(n.url)}" target="_blank" rel="noopener">Abrir</a></div>` : "")
      + `<div class="k">${n.degree} relación(es)</div><ul style="margin:0;padding-left:1.1em">${rels}</ul>`;
  }
  const matches = (n) => query && (n.label + " " + (n.name || "") + " " + (n.file || "")).toLowerCase().includes(query);
  const dim = (n) => (near && !near.has(n.id)) || (query && !matches(n));
  const nodeColor = (n) => dim(n) ? css("--line") : color(n.group);
  const linkColor = (l) => { const a = l.source.id ?? l.source, b = l.target.id ?? l.target; return near && !(near.has(a) && near.has(b)) ? css("--line") : css("--muted"); };
  const size = (n) => 2 + Math.sqrt(n.degree || 1) * 1.6 + (n.group === "family" || n.group === "manufacturer" ? 3 : 0);

  function build() {
    box.innerHTML = "";
    if (typeof ForceGraph === "undefined" || typeof ForceGraph3D === "undefined") {
      box.innerHTML = '<div class="msg">No se pudo cargar la librería de grafos (sin conexión a cdn.jsdelivr.net).</div>'; return;
    }
    const w = box.clientWidth, h = box.clientHeight;
    const common = (g) => g.width(w).height(h).graphData(visibleData()).nodeId("id")
      .nodeLabel((n) => `${esc(GROUPS[n.group] || n.group)}: ${esc(n.label)}`).nodeColor(nodeColor)
      .linkColor(linkColor).onNodeClick((n) => { show(focus === n ? null : n); refreshColors(); })
      .onBackgroundClick(() => { show(null); refreshColors(); });
    if (mode === "2d") {
      fg = common(ForceGraph()(box)).backgroundColor(css("--surface")).nodeVal(size)
        .linkDirectionalArrowLength(3.5).linkDirectionalArrowRelPos(1)
        .nodeCanvasObjectMode(() => "after")
        .nodeCanvasObject((n, ctx, scale) => {
          if (scale < 1.6 && n.group !== "family" && n.group !== "manufacturer" && !matches(n) && focus !== n) return;
          ctx.font = `${11 / scale}px "IBM Plex Sans", sans-serif`; ctx.textAlign = "center";
          ctx.fillStyle = dim(n) ? css("--line") : css("--fg");
          ctx.fillText(n.label, n.x, n.y + size(n) + 9 / scale);
        });
      fg.d3Force("charge").strength(-60);
    } else {
      fg = common(ForceGraph3D()(box)).backgroundColor(css("--surface")).nodeVal((n) => size(n) * 1.5)
        .nodeOpacity(0.95).linkOpacity(0.5).linkDirectionalArrowLength(2.5).linkDirectionalArrowRelPos(1)
        .linkDirectionalParticles((l) => focus && near && near.has(l.source.id) && near.has(l.target.id) ? 2 : 0)
        .linkDirectionalParticleWidth(1.4);
    }
    let fitted = false;
    fg.cooldownTicks(150).onEngineStop(() => { if (!fitted) { fitted = true; fg.zoomToFit(400, 30); } });
  }
  const refreshColors = () => { if (!fg) return; fg.nodeColor(nodeColor).linkColor(linkColor); if (mode === "3d") fg.linkDirectionalParticles(fg.linkDirectionalParticles()); };
  const refresh = () => { if (fg) { fg.graphData(visibleData()); } };

  document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mode = b.dataset.mode; document.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", x === b));
    if (fg && fg._destructor) fg._destructor(); build();
  }));
  document.getElementById("q").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); refreshColors(); });
  document.getElementById("fit").addEventListener("click", () => fg && fg.zoomToFit(500, 30));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { paintLegend(); build(); });
  new ResizeObserver(() => { if (fg) fg.width(box.clientWidth).height(box.clientHeight); }).observe(box);
  paintLegend(); show(null); build();
})();
"""


def graph_page(graph: dict, mermaids: dict[str, tuple[str, str]], title: str = "Grafo Relant RAG",
               standalone: bool = True) -> str:
    """HTML page. standalone=True -> complete document (loads Mermaid itself);
    False -> body fragment for a host that renders <pre class="mermaid"> on its own."""
    e = html.escape
    st = graph["stats"]
    figs = "".join(
        f'<figure><pre class="mermaid">{e(src)}</pre><figcaption><b>{e(name)}</b> · {e(cap)}</figcaption></figure>'
        for name, (src, cap) in mermaids.items())
    rels = " · ".join(f"<code>{e(k)}</code> {e(v)} ({st['by_relation'].get(k, 0)})"
                      for k, v in RELATIONS.items() if st["by_relation"].get(k))
    body = f"""
<div class="wrap">
<header><span class="eyebrow">Relant RAG · app/graph_export.py</span><h1>{e(title)}</h1>
<p class="muted">Dos grafos distintos. El de <b>datos</b> muestra cómo se relacionan los archivos de <code>data/</code>
por número de parte. Los de <b>control</b> (LangGraph) muestran qué función se ejecuta después de cuál.</p></header>

<section>
  <h2>Grafo de datos · {st['nodes']} nodos, {st['links']} relaciones</h2>
  <p class="muted">Arrastra para mover (en 3D, para girar); rueda para acercar. Clic en un nodo: resalta sus vecinos y muestra sus datos.
  Relaciones: {rels}.</p>
  <div class="bar">
    <div class="seg" role="group" aria-label="Vista"><button type="button" data-mode="2d" aria-pressed="true">2D</button><button type="button" data-mode="3d" aria-pressed="false">3D</button></div>
    <input id="q" type="search" placeholder="Buscar número de parte, nombre o archivo" aria-label="Buscar nodo">
    <div class="seg"><button type="button" id="fit">Encuadrar</button></div>
  </div>
  <div class="stage">
    <div id="graph"><div class="msg">Cargando grafo…</div></div>
    <aside>
      <div class="card"><h3>Tipos de nodo</h3><div class="legend" id="legend"></div></div>
      <div class="card"><h3>Nodo seleccionado</h3><div id="info"></div></div>
    </aside>
  </div>
</section>

<section>
  <h2>Grafos de control · LangGraph</h2>
  <p class="muted">Generados con <code>compiled.get_graph().draw_mermaid()</code>. Flecha sólida = <code>add_edge</code> (siempre);
  flecha punteada = <code>add_conditional_edges</code> (la función de ruteo elige).</p>
  <div class="mm">{figs}</div>
</section>
</div>
<script type="application/json" id="graph-data">{json.dumps({"nodes": graph["nodes"], "links": graph["links"]}, ensure_ascii=False).replace("</", "<\\/")}</script>
<script type="application/json" id="graph-groups">{json.dumps(GROUPS, ensure_ascii=False)}</script>
<script type="application/json" id="graph-rel">{json.dumps(RELATIONS, ensure_ascii=False)}</script>
<script src="{CDN_2D}"></script>
<script src="{CDN_3D}"></script>
<script>{_JS}</script>"""
    fonts = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@600'
             '&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">')
    if not standalone:
        return f"<title>{e(title)}</title>\n{fonts}\n<style>{_CSS}</style>\n{body}"
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>'
            f'{fonts}<style>{_CSS}</style></head><body>{body}'
            f'<script src="{CDN_MERMAID}"></script>'
            f'<script>mermaid.initialize({{startOnLoad:true, theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default"}});</script>'
            f'</body></html>')


def system_graph_page(retriever: Any, router: Any = None, quoter: Any = None,
                      standalone: bool = True) -> str:
    """Everything in one page: data graph + every LangGraph the app has."""
    mermaids: dict[str, tuple[str, str]] = {}
    if quoter is not None:
        mermaids["QuoteCoordinator"] = (langgraph_mermaid(quoter.app),
                                        "cotizador multiagente: supervisor y tres agentes")
    if router is not None:
        mermaids["HybridGraphAgent"] = (langgraph_mermaid(router.agent.app),
                                        "agente de búsqueda: decide → act → … → answer")
    return graph_page(product_graph(retriever), mermaids, standalone=standalone)
