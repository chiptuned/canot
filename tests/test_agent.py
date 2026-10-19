import json

import pytest

from canot import agent

COMMANDS_MD = """# Canot: the commands

Intro paragraph.

## The commands

| Command | What it does |
|---|---|
| `canot drill SOURCE DESTINATION` | Full exit exercise. |
| `canot scan SOURCE` | Read-only X-ray. |

## The sources

| Source | Form |
|---|---|
| DuckDB | `home.duckdb` |

## Local or on-prem exit

Write the copy to a local folder or an on-prem S3-compatible store.
"""


@pytest.fixture()
def docs(tmp_path, monkeypatch):
    """A small documentation folder, so agent.py is tested independently of the real docs/."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "commands.md").write_text(COMMANDS_MD, encoding="utf-8")
    (d / "scan.md").write_text("# Scan\n\n## Read-only\n\nEach query goes through a read-only guard.\n", encoding="utf-8")
    (d / "discrepancies.md").write_text("# Discrepancy catalog\n\n## Nanoseconds do not survive Iceberg v2\n\nText.\n\n"
                                        "## BigQuery: DATE_TRUNC\n\nText.\n", encoding="utf-8")
    monkeypatch.setenv("CANOT_DOCS_DIR", str(d))
    return d


def test_read_doc_and_unknown_topic(docs):
    assert "canot drill SOURCE DESTINATION" in agent.read_doc("commands")
    assert agent.read_doc("drill") == agent.read_doc("commands.md")  # aliases and suffix
    assert "Topics" in agent.read_doc("whatever")
    assert "missing" in agent.read_doc("targets")  # a page absent from the folder gives a readable message


def test_search_docs_finds_local_exit(docs):
    hits = agent.search_docs("on-prem exit local folder")
    assert hits and any("local" in h["text"].lower() for h in hits)
    assert agent.search_docs("zzzzqqq") == []


def test_commands_extracts_the_commands_section(docs):
    t = agent.commands()
    assert t.startswith("## The commands") and "canot scan SOURCE" in t and "The sources" not in t


def test_commands_without_next_section_or_title(docs):
    (docs / "commands.md").write_text(COMMANDS_MD.split("## The sources")[0] + "## Other\n\nmore\n", encoding="utf-8")
    t = agent.commands()
    assert "canot drill" in t and "## Other" not in t
    (docs / "commands.md").write_text("# Commands\n\nno table here\n", encoding="utf-8")
    assert "not found" in agent.commands() and "no table here" in agent.commands()


def test_mcp_tool_names():
    assert [f.__name__ for f in agent.TOOLS] == ["commands", "search_docs", "read_doc", "capabilities", "explain_scan", "explain_report"]
    assert set(agent.TOPICS) == {"commands", "scan", "on-prem", "targets", "discrepancies"}


def test_real_docs_list_every_cli_subcommand():
    """The real docs/commands.md (maintained alongside the CLI) lists every subcommand."""
    import argparse

    from canot.cli import parser

    t = agent.commands()
    sub = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert "connectors" in sub.choices and "replay-dbt" in sub.choices
    assert "connecteurs" not in sub.choices and "rejeu-dbt" not in sub.choices
    for cmd in sub.choices:
        assert f"canot {cmd}" in t, cmd


def test_capabilities():
    r = agent.capabilities()
    assert {c["plateforme"] for c in r["connectors"]} >= {"snowflake", "bigquery", "dbt"} and r["evidence_forms"]["P3"] == "pipeline replay"
    assert "error" in agent.capabilities("oracle")


def test_explain_scan_on_the_fixture(tmp_path):
    from pathlib import Path

    from canot.scanner.analyze import analyze

    snap = json.loads((Path(__file__).parent / "fixtures" / "scan_bigquery.json").read_text())
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(analyze(snap)))
    r = agent.explain_scan(str(p))
    assert r["findings_total"] > 0 and r["summary"] and "To review" in r["good_to_know"][0]


def test_rejects_a_file_that_is_not_canot(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        agent.explain_scan(str(p))
    with pytest.raises(ValueError):
        agent.explain_report(str(p))
    with pytest.raises(ValueError):
        agent.explain_scan(str(tmp_path / "absent.txt"))


def test_explain_report(tmp_path, docs):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"summary": {"parity": "mismatch"}, "checks": [
        {"table": "a.b", "ok": False, "rows_source": 3, "rows_target": 2, "schema_ok": True, "stats_ok": False, "fingerprint_ok": False, "stats_diffs": ["x"]},
        {"table": "a.c", "ok": True}], "queries": [{"status": "mismatch", "detail": "d", "sql": "select 1"}, {"status": "match", "sql": "select 2"}]}))
    r = agent.explain_report(str(p))
    assert [t["table"] for t in r["tables_with_mismatch"]] == ["a.b"] and len(r["queries_with_mismatch"]) == 1
    assert r["discrepancy_catalog"] == ["Nanoseconds do not survive Iceberg v2", "BigQuery: DATE_TRUNC"]
