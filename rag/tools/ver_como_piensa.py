"""See how Python reads and runs product_graph(), step by step.

    python ver_como_piensa.py            prints every stage
    python ver_como_piensa.py --json x   also saves the evidence as JSON

Stages (the same ones CPython goes through for every .py file):
  1. tokenize  : characters  -> tokens (words, symbols)
  2. ast       : tokens      -> syntax tree (the grammar of the program)
  3. compile   : tree        -> bytecode (instructions for Python's virtual machine)
  4. execute   : the virtual machine runs the bytecode, line by line, creating objects
"""
from __future__ import annotations

import ast
import dis
import inspect
import io
import json
import os
import shutil
import sys
import textwrap
import time
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA, INDEX = ROOT / "mini_data", ROOT / "mini_index"
os.environ.update(OPENROUTER_API_KEY_RELANT="sk-or-test", RAG_DATA_DIR=str(DATA),
                  RAG_DB_DIR=str(INDEX), DESCRIBE_IMAGES="false", WEB_ENABLED="false")
EVIDENCE: dict = {"python": sys.version.split()[0]}


def title(t: str) -> None:
    print("\n" + "=" * 90 + f"\n{t}\n" + "=" * 90)


# ---------------------------------------------------------------- a 1-product collection
def make_mini_data() -> None:
    from PIL import Image
    from reportlab.pdfgen import canvas
    shutil.rmtree(DATA, ignore_errors=True)
    shutil.rmtree(INDEX, ignore_errors=True)
    for d in ("productos", "precios_existencias", "drawings/fotos", "manuales/abnox"):
        (DATA / d).mkdir(parents=True)
    (DATA / "productos/bombas.csv").write_text(
        "Número de parte;Nombre del producto;Presión máx.\nBP-200;Bomba de pistón BP-200;220 bar\n",
        encoding="utf-8")
    (DATA / "precios_existencias/precios.csv").write_text(
        "Número de parte;Nombre del producto;Costo;Precio lista;Existencia\n"
        "BP-200;Bomba de pistón BP-200;9000;12500;4\n", encoding="utf-8")
    Image.new("RGB", (40, 30), (40, 90, 140)).save(DATA / "drawings/fotos/BP-200.jpg")
    c = canvas.Canvas(str(DATA / "manuales/abnox/manual.pdf"))
    c.drawString(72, 750, "ABNOX operating instructions")
    c.save()


def main() -> None:
    from app import graph_export
    from app.graph_export import product_graph

    src_lines, first = inspect.getsourcelines(product_graph)
    source = textwrap.dedent("".join(src_lines))
    EVIDENCE["source"] = source
    EVIDENCE["first_line"] = first

    # ------------------------------------------------------------ 1. tokenize
    title("1. TOKENIZE: the text of `link` becomes a list of tokens")
    link_src = textwrap.dedent("".join(src_lines[14:16]))      # def link(...) + body
    toks = []
    for t in tokenize.generate_tokens(io.StringIO(link_src).readline):
        if t.type in (tokenize.NL, tokenize.ENDMARKER):
            continue
        toks.append([tokenize.tok_name[t.type], t.string, f"{t.start[0]}:{t.start[1]}"])
        print(f"  {tokenize.tok_name[t.type]:10s} {t.string!r:28s} line:col {t.start[0]}:{t.start[1]}")
    EVIDENCE["link_source"] = link_src
    EVIDENCE["tokens"] = toks

    # ------------------------------------------------------------ 2. AST
    title("2. PARSE: tokens become a syntax tree (AST)")
    tree = ast.parse(link_src)
    dumped = ast.dump(tree.body[0], indent=2, include_attributes=False)
    print(dumped)
    EVIDENCE["ast_link"] = dumped
    full = ast.parse(source).body[0]
    outline = []
    for stmt in full.body:
        kind = type(stmt).__name__
        line = stmt.lineno - 1 + first
        txt = ast.unparse(stmt).splitlines()[0][:80]
        outline.append([line, kind, txt])
        print(f"  line {line:4d}  {kind:12s} {txt}")
    EVIDENCE["ast_outline"] = outline

    # ------------------------------------------------------------ 3. bytecode
    title("3. COMPILE: the tree becomes bytecode (one code object per function)")
    code = product_graph.__code__
    inner = {c.co_name: c for c in code.co_consts if hasattr(c, "co_code")}
    info = {"product_graph": {
        "co_varnames": list(code.co_varnames), "co_cellvars": list(code.co_cellvars),
        "inner_code_objects": list(inner), "n_bytes": len(code.co_code)}}
    for name in ("node", "link"):
        c = inner[name]
        info[name] = {"co_varnames": list(c.co_varnames), "co_freevars": list(c.co_freevars),
                      "n_bytes": len(c.co_code)}
    print(json.dumps(info, indent=1))
    EVIDENCE["code_info"] = info
    buf = io.StringIO()
    dis.dis(inner["link"], file=buf)
    EVIDENCE["dis_link"] = buf.getvalue()
    print(buf.getvalue())
    buf = io.StringIO()
    dis.dis(inner["node"], file=buf)
    EVIDENCE["dis_node"] = buf.getvalue()
    # the for-loop over products, from the outer function
    buf = io.StringIO()
    dis.dis(code, file=buf)
    loop = [ln for ln in buf.getvalue().splitlines()]
    loop_line = str(first + 17)              # the line "for p in idx.products:"
    start = next(i for i, ln in enumerate(loop) if ln.split()[:1] == [loop_line])
    EVIDENCE["dis_loop"] = "\n".join(loop[start:start + 24])
    print(EVIDENCE["dis_loop"])

    # ------------------------------------------------------------ 4. execute + trace
    title("4. EXECUTE: the virtual machine runs it (traced line by line)")
    make_mini_data()
    from test_products import FakeEmb
    from app.retriever import HybridEvaluationRetriever
    r = HybridEvaluationRetriever(embeddings=FakeEmb())
    r.update_index()
    from app.products import get_product_index
    get_product_index(r).ensure()          # build the product index first (not traced)

    targets = {code, inner["node"], inner["link"]}
    events: list[dict] = []
    t0 = time.perf_counter()

    def short(v):
        if isinstance(v, dict):
            return f"dict({len(v)})"
        if isinstance(v, list):
            return f"list({len(v)})"
        s = repr(v)
        return s if len(s) < 60 else s[:57] + "..."

    def tracer(frame, event, arg):
        if frame.f_code not in targets:
            return None                      # don't trace library code
        def local(frame, event, arg):
            if event in ("line", "return"):
                fn = frame.f_code.co_name
                loc = frame.f_locals
                rec = {"t_us": round((time.perf_counter() - t0) * 1e6), "fn": fn,
                       "event": event, "line": frame.f_lineno,
                       "depth": 0 if fn == "product_graph" else 1}
                if fn == "product_graph":
                    rec["nodes"] = len(loc["nodes"]) if "nodes" in loc else None
                    rec["links"] = len(loc["links"]) if "links" in loc else None
                else:
                    rec["args"] = {k: short(loc[k]) for k in list(frame.f_code.co_varnames)[:3]
                                   if k in loc}
                if event == "return":
                    rec["returned"] = short(arg)
                events.append(rec)
            return local
        events.append({"t_us": round((time.perf_counter() - t0) * 1e6),
                       "fn": frame.f_code.co_name, "event": "call", "line": frame.f_lineno,
                       "depth": 0 if frame.f_code.co_name == "product_graph" else 1})
        return local

    sys.settrace(tracer)
    g = product_graph(r)
    sys.settrace(None)
    for ev in events:
        pad = "    " * ev["depth"]
        extra = ev.get("args") or (f"nodes={ev.get('nodes')} links={ev.get('links')}"
                                   if ev["fn"] == "product_graph" else "")
        print(f"{ev['t_us']:7d}µs {pad}{ev['event']:6s} {ev['fn']}:{ev['line']}  {extra}"
              + (f"  -> {ev['returned']}" if "returned" in ev else ""))
    EVIDENCE["trace"] = events
    EVIDENCE["result"] = g
    lines = {i + first: ln.rstrip("\n") for i, ln in enumerate(src_lines)}
    EVIDENCE["source_by_line"] = lines

    # ------------------------------------------------------------ 5. objects in memory
    title("5. MEMORY: what the result is made of")
    n0 = g["nodes"][0]
    mem = {"result_type": type(g).__name__, "nodes": len(g["nodes"]), "links": len(g["links"]),
           "first_node": n0, "first_link": g["links"][0],
           "link_source_is_same_str_object_as_node_id":
               g["links"][0]["source"] is next(n["id"] for n in g["nodes"]
                                               if n["id"] == g["links"][0]["source"]),
           "sizeof_first_node_dict_bytes": sys.getsizeof(n0)}
    print(json.dumps(mem, indent=1, ensure_ascii=False))
    EVIDENCE["memory"] = mem


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        Path(out).write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=1, default=str),
                             encoding="utf-8")
        print("\nsaved", out)
