"""Bigger sample data/ (4 families, ~40 products) -> /graph page via the real API."""
import os, random, shutil
from pathlib import Path
import test_products as tp
from PIL import Image
from reportlab.pdfgen import canvas

random.seed(7)
D = tp.DATA
tp.make_data()
FAM = {"bombas": ("BP", "Bomba de pistón", ["Presión máx.", "Caudal", "Conexión de salida", "Peso"]),
       "valvulas_atomizado": ("VA", "Válvula de atomizado", ["Presión de aire", "Boquilla", "Material"]),
       "valvulas_volumetricas": ("VV", "Válvula volumétrica", ["Volumen por ciclo", "Presión máx.", "Sellos"]),
       "dosificadores": ("DS", "Dosificador", ["Relación de mezcla", "Caudal", "Viscosidad máx."])}
price = ["Número de parte;Nombre del producto;Costo;Precio lista;Existencia"]
for fam, (pre, nm, cols) in FAM.items():
    rows = ["Número de parte;Nombre del producto;" + ";".join(cols)]
    for i in range(random.randint(8, 12)):
        part = f"{pre}-{100 + i * 25}"
        rows.append(f"{part};{nm} {part};" + ";".join(f"{random.randint(2, 300)}" for _ in cols))
        price.append(f"{part};{nm} {part};{random.randint(500, 9000)},00;{random.randint(9000, 20000)},00;{random.randint(0, 20)}")
        if random.random() < .8:
            Image.new("RGB", (60, 40), (random.randint(0, 255), 90, 140)).save(D / f"drawings/fotos/{part}.jpg")
        if random.random() < .35:
            Image.new("RGB", (60, 40), (200, 30, 40)).save(D / f"drawings/explosionados/{part}_explosionado.png")
        if random.random() < .3:
            (D / f"hojas_tecnicas/{fam}").mkdir(parents=True, exist_ok=True)
            c = canvas.Canvas(str(D / f"hojas_tecnicas/{fam}/{part}_{nm}.pdf")); c.drawString(72, 750, f"Hoja tecnica {part}"); c.save()
    (D / f"productos/{fam}.csv").write_text("\n".join(rows), encoding="utf-8")
(D / "precios_existencias/precios_existencias.csv").write_text("\n".join(price), encoding="utf-8")
for mfr, docs in {"abnox": ["manual_valvula_dosificadora", "catalogo_2026", "lista_refacciones"],
                  "schuetze": ["betriebsanleitung_pumpe", "catalogo_bombas"],
                  "walther": ["manual_atomizado", "datasheet_pistolas"]}.items():
    (D / f"manuales/{mfr}").mkdir(parents=True, exist_ok=True)
    for d in docs:
        c = canvas.Canvas(str(D / f"manuales/{mfr}/{d}.pdf")); c.drawString(72, 750, f"{mfr} {d}"); c.save()
