"""Soporte de Word (.docx / .doc) en el RAG: lectura, chunks y descubrimiento."""
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.word_docs import read_word_text

docx = pytest.importorskip("docx")       # python-docx sólo para CREAR el archivo de prueba


def _make_docx(path: Path) -> Path:
    d = docx.Document()
    d.add_heading("Válvula VDV-25", 1)
    d.add_paragraph("Presión máxima de 400 bar.")
    t = d.add_table(rows=2, cols=2)
    for i, r in enumerate([["Modelo", "Caudal"], ["VDV-25", "12 l/min"]]):
        for j, v in enumerate(r):
            t.cell(i, j).text = v
    d.save(path)
    return path


def test_docx_text_and_tables(tmp_path):
    txt = read_word_text(_make_docx(tmp_path / "a.docx"))
    assert "# Válvula VDV-25" in txt
    assert "400 bar" in txt
    assert "Modelo: VDV-25 | Caudal: 12 l/min" in txt


def test_doc_renamed_docx(tmp_path):
    p = _make_docx(tmp_path / "a.docx")
    q = p.with_suffix(".doc")
    p.rename(q)
    assert "400 bar" in read_word_text(q)


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice para generar el .doc")
def test_binary_doc(tmp_path):
    p = _make_docx(tmp_path / "b.docx")
    subprocess.run(["soffice", "--headless", "--convert-to", "doc", "--outdir", str(tmp_path),
                    str(p)], check=True, capture_output=True, timeout=120)
    from app.word_docs import _doc_text
    txt = _doc_text(tmp_path / "b.doc")             # parser propio, sin LibreOffice
    assert "400 bar" in txt and "Modelo: VDV-25 | Caudal: 12 l/min" in txt


def test_retriever_indexes_word(tmp_path):
    from app.retriever import HybridEvaluationRetriever as R
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    p = _make_docx(tmp_path / "manual.docx")
    (tmp_path / "~$manual.docx").write_bytes(b"lock")          # archivo de bloqueo de Word
    fake = SimpleNamespace(_data_dir=tmp_path, _recursive=True, namespace="",
                           _splitter=RecursiveCharacterTextSplitter(chunk_size=500))
    fake._has_sibling_image = R._has_sibling_image
    fake._extract_text = R._extract_text
    fake._image_allowed = lambda f: False
    found = [f.name for f in R._iter_files(fake)]
    assert found == ["manual.docx"]
    chunks = R._text_chunks(fake, str(p), "manual.docx")
    assert chunks and any("400 bar" in c["text"] for c in chunks)
    assert ".docx" in settings.kb_upload_exts and ".doc" in settings.kb_upload_exts
