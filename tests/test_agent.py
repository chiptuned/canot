import json

import pytest

from canot import agent


def test_lire_doc_et_sujet_inconnu():
    assert "canot drill SOURCE DESTINATION" in agent.lire_doc("commandes")
    assert "Sujets" in agent.lire_doc("nimporte-quoi")


def test_chercher_doc_trouve_la_sortie_locale():
    hits = agent.chercher_doc("sortie on-prem dossier local")
    assert hits and any("local" in h["texte"].lower() for h in hits)
    assert agent.chercher_doc("zzzzqqq") == []


def test_commandes_liste_les_sous_commandes_du_cli():
    import argparse

    from canot.cli import parser

    t = agent.commandes()
    sub = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert "connecteurs" in sub.choices
    for cmd in sub.choices:
        assert f"canot {cmd}" in t, cmd


def test_expliquer_scan_sur_la_fixture(tmp_path):
    from pathlib import Path

    from canot.scanner.analyze import analyze

    snap = json.loads((Path(__file__).parent / "fixtures" / "scan_bigquery.json").read_text())
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(analyze(snap)))
    r = agent.expliquer_scan(str(p))
    assert r["constats_total"] > 0 and r["resume"] and "À examiner" in r["a_savoir"][0]


def test_refuse_un_fichier_qui_n_est_pas_canot(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        agent.expliquer_scan(str(p))
    with pytest.raises(ValueError):
        agent.expliquer_rapport(str(p))
    with pytest.raises(ValueError):
        agent.expliquer_scan(str(tmp_path / "absent.txt"))


def test_expliquer_rapport(tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"summary": {"parity": "partielle"}, "checks": [
        {"table": "a.b", "ok": False, "rows_source": 3, "rows_target": 2, "schema_ok": True, "stats_ok": False, "fingerprint_ok": False, "stats_diffs": ["x"]},
        {"table": "a.c", "ok": True}], "queries": [{"status": "mismatch", "detail": "d", "sql": "select 1"}, {"status": "match", "sql": "select 2"}]}))
    r = agent.expliquer_rapport(str(p))
    assert [t["table"] for t in r["tables_en_ecart"]] == ["a.b"] and len(r["requetes_en_ecart"]) == 1
