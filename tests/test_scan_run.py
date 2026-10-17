"""Orchestration collect -> analyze -> render, avec une collecte factice : régression sur le branchement des briques."""
import json
from pathlib import Path
from types import SimpleNamespace

from canot.scanner import run

FIX = Path(__file__).parent / "fixtures"
CANARI = "canari-7f3a@exemple.test"


def _src(monkeypatch, dialect: str):
    snap = json.loads((FIX / f"scan_{'snowflake' if dialect == 'snowflake' else 'bigquery'}.json").read_text())
    snap["familles"], snap["rejouabilite"] = [], None
    raw = [
        {"_sql": f"select id from t where email = '{CANARI}'", "hash_plateforme": "h1", "n_exec": 3, "temps_ms": 10, "credits": 0.1,
         "octets": 100, "app": None, "utilisateur": "alice", "role": "R", "entrepot": "W", "query_type": "SELECT"},
        {"_sql": "create user bob password='hunter2'", "hash_plateforme": "h2", "n_exec": 1, "temps_ms": 1, "credits": 0, "octets": 0,
         "utilisateur": "alice", "role": "R", "entrepot": "W", "query_type": "CREATE_USER"},
    ]
    mod = SimpleNamespace(collect=lambda *a, **k: (snap, raw))
    monkeypatch.setitem(__import__("sys").modules, "canot.scanner.snowflake" if dialect == "snowflake" else "canot.scanner.bigquery", mod)
    import canot.scanner as pkg

    monkeypatch.setattr(pkg, "snowflake" if dialect == "snowflake" else "bigquery", mod, raising=False)
    return SimpleNamespace(dialect=dialect, database="B", schemas=[], run_readonly=lambda sql: [], project="p", data_project="p", datasets=[]), raw


def test_scan_full_de_bout_en_bout_sans_fuite(monkeypatch, tmp_path):
    for dialect in ("snowflake", "bigquery"):
        src, raw = _src(monkeypatch, dialect)
        out = tmp_path / dialect
        res = run.scan_full(src, out, level=3)
        assert res["schema"] == "canot.scan/1"
        for name in ("scan.json", "scan.html", "tables.csv", "drapeaux.csv", "familles.csv", "lectures.csv"):
            assert (out / name).exists()
        everything = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
        assert CANARI not in everything and "hunter2" not in everything
        assert not (out / "scan-sql-brut.json").exists()  # le texte brut n'est jamais écrit sans option explicite


def test_texte_brut_seulement_sur_demande(monkeypatch, tmp_path):
    src, _ = _src(monkeypatch, "snowflake")
    out = tmp_path / "brut"
    run.scan_full(src, out, level=2, include_sql_text=True)
    p = out / "scan-sql-brut.json"
    assert p.exists() and oct(p.stat().st_mode & 0o777) == "0o600"
    brut = p.read_text(encoding="utf-8")
    assert CANARI in brut and "hunter2" not in brut  # le texte demandé est là, jamais celui d'une requête à secret
    assert "avec_sql_brut" in (out / "scan.json").read_text(encoding="utf-8")


def test_fichier_de_partage_sans_nom_ni_sql(monkeypatch, tmp_path):
    src, _ = _src(monkeypatch, "bigquery")
    res = run.scan_full(src, tmp_path, level=3)
    p = json.loads((tmp_path / "scan-partage.json").read_text(encoding="utf-8"))
    assert p["schema"] == "canot.partage/1" and p["tables"] == len(res["tables"])
    brut = (tmp_path / "scan-partage.json").read_text(encoding="utf-8")
    assert CANARI not in brut
    for t in res["tables"]:
        assert t["nom"] not in brut or len(t["nom"]) < 4  # aucun nom de table (les noms très courts peuvent coïncider avec un mot)
