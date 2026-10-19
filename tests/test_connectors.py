"""Connector registry: declared capabilities, scan routing, no driver imported up front."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from canot import connectors
from canot.connectors import Capabilities, Connector
from canot.scanner import run

FIX = Path(__file__).parent / "fixtures"


def test_cinq_connecteurs_et_leur_statut():
    c = connectors.CONNECTORS
    assert list(c) == ["snowflake", "bigquery", "databricks", "duckdb", "dbt"]
    assert {n: x.status for n, x in c.items()} == {"snowflake": "tested", "bigquery": "tested", "databricks": "written, untested", "duckdb": "tested",
                                                   "dbt": "tested"}
    assert [n for n, x in c.items() if x.capabilities.inventaire == "complet"] == ["snowflake", "bigquery", "dbt"]
    assert all(set(x.capabilities.preuve) <= set(connectors.PREUVES) for x in c.values())


def test_le_registre_n_importe_aucun_pilote():
    code = ("import sys, canot.connectors, canot.scanner.run, canot.cli; "
            "print([m for m in sys.modules if m.split('.')[0] in ('snowflake', 'databricks') or m.startswith('google.cloud')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def test_schema_inconnu_meme_message():
    """A scheme with no connector says so, the same way through the registry and through sources.open_source."""
    from canot.connectors import NoConnector
    from canot.sources import open_source

    with pytest.raises(NoConnector, match="no connector for Oracle yet"):
        connectors.get("duckdb").open("oracle://x")
    with pytest.raises(NoConnector, match="no connector for Oracle yet"):
        open_source("oracle://x")


def test_connectors_command(capsys):
    from canot.cli import run as cli_run

    assert cli_run(["connectors"]) == 0
    out = capsys.readouterr().out
    for label, status in (("Snowflake", "tested"), ("BigQuery", "tested"), ("Databricks", "written, untested"), ("DuckDB", "tested")):
        line = next(l for l in out.splitlines() if l.startswith(label))
        assert line.rstrip().endswith("included") and f"  {status}  " in line, line
    assert "P1 data parity" in out and "full (levels 1-3)" in out


def test_scan_complet_refuse_un_connecteur_leger(tmp_path):
    src = SimpleNamespace(dialect="duckdb")
    assert not run.supports_full(src)
    with pytest.raises(ValueError, match="full scan not available for duckdb"):
        run.collect(src, level=1)


def test_faux_connecteur_produit_un_scan_minimal(monkeypatch, tmp_path):
    class Fake(Connector):
        name, label, schemes, dialect, url = "fake", "Fake", ("fake",), "fake", "fake://X"
        capabilities = Capabilities("complet", (1,))

        def scan_collect(self, src, *, level, salt, days=None):
            assert isinstance(salt, bytes) and level == 1
            return {"schema": "canot.scan/1", "plateforme": "fake", "as_of": "2026-10-17T10:00:00Z", "perimetre": "fake://X",
                    "lectures": [{"id": "X1", "vue": "INVENTAIRE", "statut": "read", "raison": None, "lignes": 1, "fenetre": None, "latence_max_h": None}],
                    "tables": [], "objets": [], "metering": None}, []

    monkeypatch.setitem(connectors.CONNECTORS, "fake", Fake())
    src = SimpleNamespace(dialect="fake")
    assert run.supports_full(src)
    res = run.scan_full(src, tmp_path, level=1)
    assert res["plateforme"] == "fake" and res["lectures"][0]["statut"] == "read"
    assert json.loads((tmp_path / "scan-share.json").read_text(encoding="utf-8"))["schema"] == "canot.share/1"
    assert (tmp_path / "scan.html").exists()


def test_fiche_bigquery_non_vu_couvert():
    from canot.scanner.analyze import analyze

    snap = json.loads((FIX / "scan_bigquery.json").read_text(encoding="utf-8"))
    nv = "queries run from other projects"
    f05 = [f for f in analyze(snap)["drapeaux"] if f["code"] == "F05"]
    assert f05 and all(nv in f["preuve"]["non_vus"] for f in f05)
    for lec in snap["lectures"]:
        if "JOBS_BY_ORGANIZATION" in lec["vue"]:
            lec["statut"] = "read"
    f05 = [f for f in analyze(snap)["drapeaux"] if f["code"] == "F05"]
    assert f05 and all(nv not in f["preuve"]["non_vus"] for f in f05)


def test_plateforme_sans_connecteur_explique_comment_le_demander(capsys):
    from canot.cli import main

    assert main(["scan", "redshift://cluster/db"]) == 1
    err = capsys.readouterr().err
    assert "no connector for Amazon Redshift yet" in err and "canot.io/en/review.html?connector=redshift" in err
    assert "Q4O" in err and "Available today" in err and "Traceback" not in err


def test_la_liste_des_connecteurs_cite_la_feuille_de_route(capsys):
    from canot.cli import run as cli_run

    cli_run(["connectors"])
    out = capsys.readouterr().out
    assert "On the roadmap" in out and "Teradata" in out and "Ask for one" in out
