"""Shows which PDF extractor built your index, using the KA-2 HD manual (Doc-ID 0893).

Run from the project root (the folder that contains app/):
    python check_index.py
"""
import glob
import os
import pickle

import app.pdf_tables as pt
from app.config import settings

print("pdf_tables loaded from:", pt.__file__)
print("pdf_tables version:", "NEW (visual rows)" if hasattr(pt, "_visual_rows") else "OLD -> replace app/pdf_tables.py")

manifests = [settings.manifest_file] + glob.glob(os.path.join(settings.kb_dir, "**", "manifest.json"),
                                                  recursive=True)
found = False
for mf in manifests:
    if not os.path.exists(mf):
        continue
    with open(mf, "rb") as f:
        manifest = pickle.load(f)
    for path, entry in manifest.items():
        if "0893" not in os.path.basename(path):
            continue
        found = True
        chunks = entry["chunks"]
        new = any("Sección:" in c["text"] for c in chunks) and any(c.get("page_label") for c in chunks)
        print(f"\nindex: {mf}\nfile:  {path}\nchunks: {len(chunks)}  ->  built with the",
              "NEW extractor" if new else "OLD extractor: run a REBUILD (not update)")
        for c in chunks:
            if c.get("page") == 27:
                print("-----", c["id"].rsplit("/", 1)[-1])
                print(c["text"][:420])
if not found:
    print("No indexed file with '0893' in its name.")
