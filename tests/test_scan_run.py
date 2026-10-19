"""collect -> analyze -> render orchestration, with a fake collection: regression on how the pieces are wired."""
import os
import json
from pathlib import Path
from types import SimpleNamespace

from canot.scanner import run

FIX = Path(__file__).parent / "fixtures"
CANARI = "canari-7f3a@exemple.test"


def _src(monkeypatch, dialect: str):
    snap = json.loads((FIX / f"scan_{'snowflake' if dialect == 'snowflake' else 'bigquery'}.json").read_text(encoding="utf-8"))
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
        assert res["schema"] == "canot.scan/2" and res["famille"] == "entrepot" and res["capacites"]["plateforme"] == dialect
        for name in ("scan.json", "scan.html", "tables.csv", "flags.csv", "families.csv", "reads.csv", "scan-share.json"):
            assert (out / name).exists()
        everything = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
        assert CANARI not in everything and "hunter2" not in everything
        assert not (out / "scan-sql-raw.json").exists()  # raw text is never written without an explicit option
        assert not {"scan-partage.json", "scan-sql-brut.json", "drapeaux.csv", "familles.csv", "lectures.csv"} & {p.name for p in out.iterdir()}


def test_texte_brut_seulement_sur_demande(monkeypatch, tmp_path):
    src, _ = _src(monkeypatch, "snowflake")
    out = tmp_path / "brut"
    run.scan_full(src, out, level=2, include_sql_text=True)
    p = out / "scan-sql-raw.json"
    assert p.exists() and (os.name == "nt" or oct(p.stat().st_mode & 0o777) == "0o600")
    brut = p.read_text(encoding="utf-8")
    assert CANARI in brut and "hunter2" not in brut  # the requested text is there, never the text of a query holding a secret
    assert "avec_sql_brut" in (out / "scan.json").read_text(encoding="utf-8")


def test_fichier_de_partage_sans_nom_ni_sql(monkeypatch, tmp_path):
    src, _ = _src(monkeypatch, "bigquery")
    res = run.scan_full(src, tmp_path, level=3)
    p = json.loads((tmp_path / "scan-share.json").read_text(encoding="utf-8"))
    assert p["schema"] == "canot.share/1" and p["tables"] == len(res["tables"])
    brut = (tmp_path / "scan-share.json").read_text(encoding="utf-8")
    assert CANARI not in brut
    for t in res["tables"]:
        assert t["nom"] not in brut or len(t["nom"]) < 4  # no table name (very short names may match a word)


def test_langue_du_rapport_seul_le_html_change(monkeypatch, tmp_path):
    """--lang (or the locale) only picks the pane scan.html shows first: both languages are always there, the data files stay English."""
    for var in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(var, raising=False)
    src, _ = _src(monkeypatch, "snowflake")
    files = {}
    for case, lang, env in (("en", "en", "fr_FR.UTF-8"), ("fr", "fr", "C"), ("locale_fr", None, "fr_FR.UTF-8"), ("repli", None, "C")):
        monkeypatch.setenv("LANG", env)
        out = tmp_path / case
        run.scan_full(src, out, level=3, lang=lang)
        files[case] = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    for case in ("fr", "locale_fr", "repli"):
        assert {n: t for n, t in files[case].items() if n != "scan.html"} == {n: t for n, t in files["en"].items() if n != "scan.html"}
    assert '<html lang="en">' in files["en"]["scan.html"] and '<html lang="en">' in files["repli"]["scan.html"]
    assert '<html lang="fr">' in files["fr"]["scan.html"] and files["locale_fr"]["scan.html"] == files["fr"]["scan.html"]
    for case in files:
        h = files[case]["scan.html"]
        assert 'id="lang-en"' in h and 'id="lang-fr"' in h and "À examiner" in h and "To review" in h
    assert "À examiner" not in files["fr"]["scan.json"] and "À examiner" not in files["fr"]["flags.csv"]


def test_analyse_pure_dans_les_deux_langues():
    import copy

    from canot.scanner.analyze import analyze

    snap = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    before = copy.deepcopy(snap)
    en, fr = analyze(snap), analyze(snap, lang="fr")
    assert snap == before and analyze(snap) == en and analyze(snap, lang="fr") == fr

    def shape(x):
        if isinstance(x, dict):
            return {k: shape(v) for k, v in x.items()}
        if isinstance(x, list):
            return [shape(v) for v in x]
        return type(x).__name__

    assert shape(en) == shape(fr)  # same keys, same list lengths: only the texts differ
    assert [d["code"] for d in en["drapeaux"]] == [d["code"] for d in fr["drapeaux"]]
    assert {d["etiquette"] for d in fr["drapeaux"]} == {"À examiner"} and {d["etiquette"] for d in en["drapeaux"]} == {"To review"}
