"""Filet de non-régression : les sorties du scan sont comparées à l'octet aux fichiers figés de tests/golden/.

Seules les valeurs non déterministes sont neutralisées, ici et pas dans le code : sel des pseudonymes, versions
(canot, sqlglot, duckdb), chemin du dossier temporaire. Une différence volontaire se régénère avec :

    CANOT_REGENERER_DORES=1 .venv/bin/python -m pytest -q tests/test_golden.py

puis relire le diff de tests/golden/ avant de le committer.
"""
import difflib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot

import canot
from canot.scanner import run

FIX = Path(__file__).parent / "fixtures"
GOLD = Path(__file__).parent / "golden"
REGEN = os.environ.get("CANOT_REGENERER_DORES") == "1"
FILES = ("scan.json", "scan.html", "tables.csv", "drapeaux.csv", "familles.csv", "lectures.csv", "scan-partage.json")
SALT = b"canot-dore-sel-0"

RAW = {
    "snowflake": [
        ("select o.id, o.amount from CANOT_LAB.RAW.ORDERS o where o.id > 10", 40, 2.5, None, "dbt"),
        ("select count(*) from CANOT_LAB.RAW.ORDERS where id = 7", 12, 0.4, None, None),
        ("copy into @CANOT_LAB.EXT.S3_PUBLIC/out/ from (select * from CANOT_LAB.RAW.ORDERS)", 3, 0.2, None, None),
        ("select my_udf(id) from CANOT_LAB.RAW.ARCHIVE_1", 2, 0.1, None, "tableau"),
        ("execute immediate $$ select 1 $$", 1, 0.0, None, None),
        ("create user bob password='hunter2'", 1, 0.0, None, None),
        ("select * from table(result_scan(last_query_id()))", 5, None, None, None),
    ],
    "bigquery": [
        ("select * from `lab-a.raw.orders` where d >= '2026-01-01'", 900, None, 2_000_000_000_000, None),
        ("select count(*) from `lab-a.raw.customers`", 10, None, 1_000_000, "looker"),
        ("export data options(uri='gs://bucket-x/out/*.csv', format='CSV') as select * from `lab-a.raw.orders`", 2, None, 50_000, None),
        ("select ML.PREDICT(MODEL `lab-a.raw.m`, (select 1))", 1, None, None, None),
        ("create user bob password='hunter2'", 1, None, 0, None),
    ],
}


def _raw(dialect: str) -> list[dict]:
    return [{"_sql": sql, "hash_plateforme": f"h{i}", "n_exec": n, "temps_ms": 10.0 * n, "credits": cr, "octets": oc, "app": app,
             "utilisateur": f"user{i % 3}@exemple.test", "role": "R", "entrepot": "W" if dialect == "snowflake" else None,
             "query_type": "CREATE_USER" if "create user" in sql else "SELECT"}
            for i, (sql, n, cr, oc, app) in enumerate(RAW[dialect])]


@pytest.fixture
def figer(monkeypatch):
    monkeypatch.setattr(run, "new_salt", lambda: SALT)
    monkeypatch.setattr(run, "_versions", lambda: {"canot": "X", "sqlglot": "X", "duckdb": "X"})
    monkeypatch.setattr(sqlglot, "__version__", "X")
    monkeypatch.setattr(duckdb, "__version__", "X")
    monkeypatch.setattr(canot, "__version__", "X")


def _fake_collect(monkeypatch, fixture: str):
    snap = json.loads((FIX / f"scan_{fixture}.json").read_text(encoding="utf-8"))
    dialect = snap["plateforme"]
    raw = _raw(dialect)
    mod = SimpleNamespace(collect=lambda *a, **k: (json.loads(json.dumps(snap)), raw))
    import canot.scanner as pkg

    monkeypatch.setitem(sys.modules, f"canot.scanner.{dialect}", mod)
    monkeypatch.setattr(pkg, dialect, mod, raising=False)
    return SimpleNamespace(dialect=dialect, database="B", schemas=[], run_readonly=lambda sql: [], project="p", data_project="p",
                           datasets=[], describe=lambda: f"{dialect}:dore", close=lambda: None)


def _compare(case: str, produced: dict[str, str]):
    d = GOLD / case
    if REGEN:
        d.mkdir(parents=True, exist_ok=True)
        for name, text in produced.items():
            (d / name).write_text(text, encoding="utf-8", newline="")
        return
    diffs = []
    for name, text in produced.items():
        ref = d / name
        if not ref.exists():
            diffs.append(f"{name} : référence absente")
            continue
        want = ref.read_bytes().decode("utf-8")
        if want != text:
            diff = "".join(list(difflib.unified_diff(want.splitlines(True), text.splitlines(True), "référence", "produit", n=1))[:40])
            diffs.append(f"{name} diffère :\n{diff}")
    assert not diffs, (f"Sortie différente de tests/golden/{case}/. Si le changement est voulu : "
                       "CANOT_REGENERER_DORES=1 .venv/bin/python -m pytest -q tests/test_golden.py, puis relire le diff.\n\n"
                       + "\n".join(diffs))


def _read(out: Path, names=FILES) -> dict[str, str]:
    return {n: (out / n).read_bytes().decode("utf-8") for n in names}


@pytest.mark.parametrize("fixture", ["snowflake", "bigquery", "degrade"])
def test_scan_full_identique(figer, monkeypatch, tmp_path, fixture):
    src = _fake_collect(monkeypatch, fixture)
    run.scan_full(src, tmp_path, level=3)
    _compare(f"scan_full_{fixture}", _read(tmp_path))


@pytest.mark.parametrize("fixture", ["snowflake", "bigquery", "degrade"])
def test_analyse_seule_identique(tmp_path, fixture):
    """Fixture intacte (ses familles comprises) : analyze -> render -> CSV -> partage, écrits comme scan_full."""
    from canot.scanner.analyze import analyze
    from canot.scanner.render import render_html, write_csv

    result = analyze(json.loads((FIX / f"scan_{fixture}.json").read_text(encoding="utf-8")))
    result["avec_sql_brut"] = False
    (tmp_path / "scan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (tmp_path / "scan.html").write_text(render_html(result), encoding="utf-8")
    write_csv(result, tmp_path)
    (tmp_path / "scan-partage.json").write_text(json.dumps(run.partage(result), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    _compare(f"analyse_{fixture}", _read(tmp_path))


def _cli(argv, capsys) -> str:
    from canot.cli import run as cli_run

    assert cli_run(argv) == 0
    return capsys.readouterr().out


def test_cli_scan_complet_identique(figer, monkeypatch, tmp_path, capsys):
    import canot.sources

    src = _fake_collect(monkeypatch, "snowflake")
    monkeypatch.setattr(canot.sources, "open_source", lambda url: src)
    out = tmp_path / "out"
    stdout = _cli(["scan", "snowflake://COMPTE/B", "--out", str(out)], capsys).replace(str(tmp_path), "<TMP>")
    _compare("cli_scan_complet", {"stdout.txt": stdout, **_read(out)})


def test_cli_scan_leger_identique(tmp_path, capsys):
    from canot.demo import make_demo

    db = tmp_path / "demo.duckdb"
    make_demo(str(db), 500)
    out = tmp_path / "out"
    stdout = _cli(["scan", str(db), "--out", str(out)], capsys).replace(str(tmp_path), "<TMP>")
    _compare("cli_scan_leger", {"stdout.txt": stdout, "scan.json": (out / "scan.json").read_text(encoding="utf-8").replace(str(tmp_path), "<TMP>")})
