"""Proof levels (proof.py): stated per table and for the exercise in report.json, the evidence log, the share summary, both report
panes and the dbt replay, with the lowest-level rule; the old identity wording is gone."""
import json
import re

import duckdb
import pytest

from canot import proof
from canot.brand import drill_share
from canot.demo import DEMO_QUERIES, make_demo
from canot.evidence import EvidenceLog, verify_dir
from canot.report import write_report
from canot.sources.duckdb_src import DuckDBSource
from test_drill import drill
from test_report_i18n import EN_FORBIDDEN, FR_FORBIDDEN, panes, text


@pytest.fixture()
def demo(tmp_path):
    db = tmp_path / "demo.duckdb"
    make_demo(str(db), 2_000)
    q = tmp_path / "q.sql"
    q.write_text(DEMO_QUERIES)
    return db, q


class BrokenReadSource(DuckDBSource):
    """The read of one table fails: the table is not copied, hence not compared."""

    def read(self, table, batch_rows=100_000):
        if table.name == "orders":
            raise RuntimeError("read refused")
        yield from super().read(table, batch_rows)


def _events(out):
    return [json.loads(x) for x in (out / "evidence.jsonl").read_text().splitlines() if x.strip()]


def test_scale_order_and_rules():
    assert proof.ORDER == ("not_compared", "metadata_only", "row_sampled", "partition_sampled", "full_aggregates", "full_fingerprint")
    assert proof.lowest(["full_aggregates", "metadata_only", "full_aggregates"]) == "metadata_only"
    assert proof.lowest(["full_aggregates", "not_compared"]) == "not_compared"
    assert proof.lowest([]) == "not_compared"  # nothing compared is never a success
    assert proof.lowest(["full_aggregates", "something_unknown"]) == "something_unknown"  # unknown: never ranked above a known level
    assert proof.counts(["full_aggregates", "full_aggregates", "not_compared"]) == {
        "not_compared": 1, "metadata_only": 0, "row_sampled": 0, "partition_sampled": 0, "full_aggregates": 2, "full_fingerprint": 0}
    assert not proof.RESERVED & set(proof.PRODUCED)
    for lv in proof.ORDER:  # one label and one definition per level, in both languages
        assert proof.LABELS[lv]["en"] and proof.LABELS[lv]["fr"] and proof.DEFINITIONS[lv]["en"] and proof.DEFINITIONS[lv]["fr"]
    for lv in proof.RESERVED:
        assert "not implemented yet" in proof.DEFINITIONS[lv]["en"] and "pas encore disponible" in proof.DEFINITIONS[lv]["fr"]


def test_level_per_table_and_for_the_exercise(demo, tmp_path):
    res, out = drill(DuckDBSource(str(demo[0])), tmp_path, demo[1])
    assert res["checks"] and all(c["proof_level"] == "full_aggregates" for c in res["checks"])
    assert all("recomputed in the source engine" in c["proof_note"] for c in res["checks"])
    s = res["summary"]
    assert s["proof_level"] == "full_aggregates" and s["tables_by_proof_level"]["full_aggregates"] == 3
    assert sum(s["tables_by_proof_level"].values()) == s["tables_total"] and res["not_compared"] == []
    assert not {c["proof_level"] for c in res["checks"]} & proof.RESERVED
    share = drill_share(res)["summary"]
    assert share["proof_level"] == "full_aggregates" and share["tables_by_proof_level"] == s["tables_by_proof_level"]
    ev = _events(out)
    checks = [e for e in ev if e["event"] == "table_check"]
    assert len(checks) == 3 and all(e["data"]["proof_level"] == "full_aggregates" for e in checks)
    assert [e for e in ev if e["event"] == "summary"][0]["data"]["proof_level"] == "full_aggregates"
    assert verify_dir(out)[0]


def test_a_copy_error_makes_the_exercise_not_compared(demo, tmp_path):
    res, out = drill(BrokenReadSource(str(demo[0])), tmp_path, demo[1])
    s = res["summary"]
    assert s["proof_level"] == "not_compared"
    assert s["tables_by_proof_level"]["not_compared"] == 1 and s["tables_by_proof_level"]["full_aggregates"] == 2
    (nc,) = res["not_compared"]
    assert nc["proof_level"] == "not_compared" and nc["proof_note"].startswith("copy failed: RuntimeError: read refused")
    copy = [e["data"] for e in _events(out) if e["event"] == "copy" and e["data"]["error"]]
    assert copy and copy[0]["proof_level"] == "not_compared"
    assert verify_dir(out)[0]
    p = panes(write_report(res, out, "en").read_text())
    en, fr = text(p["en"]), text(p["fr"])
    assert re.search(r"not compared Not compared copy failed: RuntimeError: read refused", en)
    assert re.search(r"non comparée Non comparé échec de la copie : RuntimeError: read refused", fr)
    assert "Level reached by this exercise: Not compared ( not_compared )" in en
    assert "Niveau atteint par cet exercice : Non comparé ( not_compared )" in fr


def test_a_comparison_error_is_not_compared(demo, tmp_path, monkeypatch):
    import canot.drill

    real = canot.drill.check_table

    def flaky(src, t, *a, **k):
        if t.name == "orders":
            raise ValueError("stats query refused")
        return real(src, t, *a, **k)
    monkeypatch.setattr(canot.drill, "check_table", flaky)
    res, out = drill(DuckDBSource(str(demo[0])), tmp_path, demo[1])
    assert res["summary"]["proof_level"] == "not_compared"
    assert res["not_compared"][0]["proof_note"].startswith("comparison failed: ValueError")
    err = [e["data"] for e in _events(out) if e["event"] == "table_check_error"]
    assert err and err[0]["proof_level"] == "not_compared"


def test_verify_evidence_accepts_new_and_old_logs(demo, tmp_path):
    from canot.cli import run as cli_run

    _, out = drill(DuckDBSource(str(demo[0])), tmp_path, demo[1])
    assert cli_run(["verify-evidence", str(out)]) == 0
    old = EvidenceLog(tmp_path / "old")  # a log written before the levels: no proof key anywhere
    old.add("table_check", {"table": "S.T", "rows_source": 1, "rows_target": 1, "ok": True})
    old.add("summary", {"parity": "complete", "tables_ok": 1, "tables_total": 1})
    old.seal(tmp_path / "k.key")
    assert verify_dir(tmp_path / "old")[0] and cli_run(["verify-evidence", str(tmp_path / "old")]) == 0


def test_reports_state_the_level_in_both_panes_and_drop_the_identity_wording(tmp_path, monkeypatch, capsys):
    import canot.demo
    from canot.cli import main

    monkeypatch.setattr(canot.demo, "make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert main(["demo", "--out", str(tmp_path / "d"), "--rows", "2000", "--no-open"]) == 0
    assert "\nProof level: full_aggregates\n" in capsys.readouterr().out
    ev = tmp_path / "d" / "evidence"
    page = (ev / "report.html").read_text()
    p = panes(page)
    en, fr = text(p["en"]), text(p["fr"])
    assert "Proof level" in en and "Niveau de preuve" in fr
    assert "Table Source rows Target rows Result Proof level Details" in en
    assert "Table Lignes source Lignes cible Résultat Niveau de preuve Détails" in fr
    assert "matches Full aggregates" in en and "concordante Agrégats complets" in fr
    assert "Level reached by this exercise: Full aggregates ( full_aggregates ), the lowest level over its tables" in en
    assert "Niveau atteint par cet exercice : Agrégats complets ( full_aggregates ), le plus bas de ses tables" in fr
    assert "Counts and statistics match" in en and "Les comptes et statistiques concordent" in fr
    assert "What full aggregates do not prove" in en and "would not be detected" in en and "is not implemented yet" in en
    assert "Ce que les agrégats complets ne prouvent pas" in fr and "ne serait pas détectée" in fr and "n'est pas encore disponible" in fr
    for lv in proof.ORDER:  # the four lines define every level
        assert lv in en and lv in fr
    # the lossy nanosecond column: said to be compared only within precision
    assert "compared only within the precision of the target type" in en and "comparée seulement à la précision du type cible" in fr
    # the old wording is gone, in both panes
    for old in ("The content arrives identical", "identical content", "Identical", "identical"):
        assert old not in en, old
    for old in ("Le contenu arrive identique", "contenu identique", "Identique", "identique"):
        assert old not in fr, old
    for w in EN_FORBIDDEN:
        assert w not in en.lower(), w
    for w in FR_FORBIDDEN:
        assert w not in fr.lower(), w
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert p["en"].count("<h2") == p["fr"].count("<h2") and p["en"].count("<li") == p["fr"].count("<li")


def test_old_report_json_still_renders(demo, tmp_path):
    res, out = drill(DuckDBSource(str(demo[0])), tmp_path, demo[1])
    for c in res["checks"]:
        for k in ("proof_level", "proof_note", "within_precision_cols"):
            c.pop(k)
    for k in ("proof_level", "tables_by_proof_level"):
        res["summary"].pop(k)
    res.pop("not_compared")
    en = text(panes(write_report(res, out, "en").read_text())["en"])
    assert "Level reached by this exercise: Full aggregates" in en  # same computation as today: labelled as such


def test_dbt_replay_levels(tmp_path):
    from canot.dbt_rejeu import run_rejeu
    from test_dbt_rejeu import FIX, MARTS, STG, copie_duckdb

    db = copie_duckdb(tmp_path / "copie.duckdb")
    con = duckdb.connect(str(db))
    con.execute("alter table DBT_dbt_mart.dbt_daily_revenue add column extra integer")  # columns differ: only metadata compared
    con.close()
    out = tmp_path / "out"
    res = run_rejeu(f"dbt://{FIX}", out, tmp_path / "k.key", base=str(db), log=lambda *_: None)
    by = {m["modele"].split(".")[-1]: m for m in res["modeles"]}
    for m in MARTS:
        if m != "dbt_daily_revenue":
            assert by[m]["proof_level"] == "full_aggregates" and "recomputed" in by[m]["proof_note"], m
    assert by["dbt_daily_revenue"]["proof_level"] == "metadata_only"
    for m in STG:  # views: replayed, nothing to compare with
        assert by[m]["proof_level"] == "not_compared" and by[m]["proof_note"].startswith("not compared: no original table"), m
    s = res["summary"]
    assert s["proof_level"] == "not_compared"
    assert s["models_by_proof_level"] == {"not_compared": 4, "metadata_only": 1, "row_sampled": 0, "partition_sampled": 0,
                                          "full_aggregates": 3, "full_fingerprint": 0}
    saved = json.loads((out / "replay-dbt.json").read_text())
    assert saved["summary"]["proof_level"] == "not_compared"
    models = [e["data"] for e in _events(out) if e["event"] == "dbt_model"]
    assert models and all(e.get("proof_level") in proof.PRODUCED for e in models)
    assert verify_dir(out)[0]


def test_dbt_drill_report_states_the_replay_level(tmp_path):
    from test_drill_dbt import _drill, build_project

    proj = build_project(tmp_path, ecart=True)
    r, out = _drill(tmp_path, proj, lang="fr")
    assert r["dbt"]["rejeu"]["proof_level"] in proof.PRODUCED and "models_by_proof_level" in r["dbt"]["rejeu"]
    p = panes((out / "report.html").read_text())
    assert "Proof level of the replay:" in text(p["en"]) and "Niveau de preuve du rejeu :" in text(p["fr"])
    assert "identique" not in text(p["fr"]) and "identical" not in text(p["en"])


def test_nanosecond_timestamps_convert_without_pandas():
    """A clean install has no pandas: nanosecond timestamps must still convert (Windows crash on `uvx canot demo`)."""
    import datetime as dt

    import pyarrow as pa

    from canot.verify import _pylist

    col = pa.chunked_array([pa.array([1_000_000_000_000_001, 2_000_000_000_000_000, None], type=pa.timestamp("ns"))])
    got = _pylist(col)
    assert got[0] == "1970-01-12T13:46:40.000000001"   # sub-microsecond value kept exact, so it differs from a microsecond copy
    assert got[1] == dt.datetime(1970, 1, 24, 3, 33, 20)
    assert got[2] is None


def test_reported_version_is_the_packaged_version():
    """Reports and the evidence log carry the version: it must follow pyproject.toml (it once stayed at 0.5.0)."""
    import tomllib
    from pathlib import Path

    import canot

    meta = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert canot.__version__ == meta["project"]["version"]
