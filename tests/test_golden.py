"""Regression safety net: scan outputs are compared byte for byte with the frozen files in tests/golden/.

Only non-deterministic values are neutralized, here and not in the code: pseudonym salt, versions
(canot, sqlglot, duckdb), temporary directory path. An intended difference is regenerated with:

    CANOT_REGEN_GOLDEN=1 .venv/bin/python -m pytest -q tests/test_golden.py

(CANOT_REGENERER_DORES=1 still works), then review the diff of tests/golden/ before committing it.
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
REGEN = "1" in (os.environ.get("CANOT_REGEN_GOLDEN"), os.environ.get("CANOT_REGENERER_DORES"))
FILES = ("scan.json", "scan.html", "tables.csv", "flags.csv", "families.csv", "reads.csv", "scan-share.json")
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
    for var in ("LC_ALL", "LC_MESSAGES"):  # scan.html shows English first whatever the machine's locale
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "C")


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
            diffs.append(f"{name}: reference missing")
            continue
        want = ref.read_bytes().decode("utf-8")
        if want != text:
            diff = "".join(list(difflib.unified_diff(want.splitlines(True), text.splitlines(True), "reference", "produced", n=1))[:40])
            diffs.append(f"{name} differs:\n{diff}")
    assert not diffs, (f"Output differs from tests/golden/{case}/. If the change is intended: "
                       "CANOT_REGEN_GOLDEN=1 .venv/bin/python -m pytest -q tests/test_golden.py, then review the diff.\n\n"
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
    """Untouched fixture (its families included): analyze -> render -> CSV -> share file, written like scan_full."""
    from canot.scanner.analyze import analyze
    from canot.scanner.render import render_html, write_csv

    snap = json.loads((FIX / f"scan_{fixture}.json").read_text(encoding="utf-8"))
    result, result_fr = analyze(snap), analyze(snap, lang="fr")
    result["avec_sql_brut"] = result_fr["avec_sql_brut"] = False
    (tmp_path / "scan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (tmp_path / "scan.html").write_text(render_html(result, result_fr, lang="en"), encoding="utf-8")
    write_csv(result, tmp_path)
    (tmp_path / run.SHARE_FILE).write_text(json.dumps(run.partage(result), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
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


def test_cli_scan_dbt_identique(figer, monkeypatch, tmp_path, capsys):
    """Test-bench dbt project (copy of the lab/dbt artifacts): canot scan dbt://… end to end, frozen outputs."""
    from canot.scanner import dbt as dbt_scan

    monkeypatch.setattr(dbt_scan, "now_iso", lambda: "2026-10-19T10:00:00Z")
    monkeypatch.setattr(dbt_scan, "_duree", lambda t0: 0.0)
    out = tmp_path / "out"
    stdout = _cli(["scan", f"dbt://{FIX / 'dbt' / 'banc'}", "--out", str(out)], capsys).replace(str(tmp_path), "<TMP>")
    _compare("cli_scan_dbt", {"stdout.txt": stdout, **_read(out)})
