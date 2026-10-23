"""Offline test of app/multiagent.py: fake retriever (one CSV) + scripted fake LLM.
Run:  python -m pytest -q app/tests/test_multiagent.py"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline as _base  # noqa: E402,F401  (sets the test environment before app imports)

from app.multiagent import QuoteCoordinator, parse_money  # noqa: E402
from app.textutils import norm, read_csv_table  # noqa: E402

CSV = """Artículo;Descripción;Costo;Precio lista;Stock
123456;O-ring 10x2 NBR;40,00;100,00;12
VDV-25;Válvula dosificadora VDV-25;2.000,00;2.400,00;3
MMKD-40;Kit de juntas MMKD-40;300,00;500,00;8
"""


class FakeRetriever:
    def __init__(self):
        self.path = os.path.join(tempfile.mkdtemp(), "precios.csv")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(CSV)
        self.snapshot = SimpleNamespace(catalog={"precios.csv": {
            "rel": "precios.csv", "path": self.path, "type": "table", "title": "precios"}})

    def _table_rows(self, path):
        h, rows, _ = read_csv_table(path)
        return h, rows, [[norm(c) for c in r] for r in rows]

    def retrieve(self, query, top_k=None, kinds=None, hits=None):
        return []

    def maybe_reload(self):
        pass


class FakeLLM:
    """Answers by looking at the system prompt. Records which thread ran each call."""
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def invoke(self, messages):
        system, user = messages[0].content, messages[1].content
        with self.lock:
            self.calls.append((system[:20], threading.current_thread().name, time.time()))
        if system.startswith("Extract"):
            # 1st try: the model misreads the O-ring code; after feedback it gets it right
            code = "123456" if "feedback" in user.lower() else "123465"
            out = {"items": [{"code": code, "description": "O-ring", "qty": 10},
                             {"code": "VDV-25", "description": "válvula", "qty": 1}]}
        elif system.startswith("You check"):
            time.sleep(0.3)                     # make the verifier slow to see the overlap
            out = {"missing": [], "extra": [], "qty_mismatch": [], "ok": True}
        else:                                   # supervisor
            if "Allowed now: quote, clarify" in user:
                out = {"next": "quote", "reason": "codes and quantities are given"}
            elif "no aparece" in user and "Allowed now: quote" in user:
                out = {"next": "quote", "reason": "wrong code",
                       "feedback": "123465 does not exist; the customer wrote 123456"}
            else:
                out = {"next": "finish", "reason": "ready"}
        return SimpleNamespace(content=json.dumps(out), usage_metadata={
            "input_tokens": 100, "output_tokens": 20})


def test_parse_money():
    assert str(parse_money("1.234,50 €")) == "1234.50"
    assert str(parse_money("$1,234.50")) == "1234.50"
    assert str(parse_money("12,5")) == "12.5"
    assert parse_money("n/a") is None


def test_full_run():
    llm = FakeLLM()
    res = QuoteCoordinator(FakeRetriever(), llm).run(
        "Cotizar 10 o-rings 123456 y una válvula VDV-25", debug=True)
    print("\n".join(res["log"]))
    print(json.dumps({k: res[k] for k in ("status", "margin", "verification")},
                     indent=1, ensure_ascii=False))
    q = res["quote"]
    assert [l["status"] for l in q["lines"]] == ["ok", "ok"]
    assert q["total"] == "3400.00"                 # 10 x 100 + 1 x 2400
    assert q["revision"] == 1                      # rebuilt once after the verifier's error
    assert res["margin"]["below_min"] == ["VDV-25"]   # (2400-2000)/2400 = 16.7 % < 25 %
    assert res["verification"]["ok"]
    assert res["status"] == "needs_review"         # margin problem -> a human decides
    print("usage:", res["usage"])


if __name__ == "__main__":
    test_parse_money()
    test_full_run()
    print("OK")
