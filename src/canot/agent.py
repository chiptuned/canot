"""What an agent can ask Canot: the documentation and explanations of your reports. No connection to your platforms:
the tools read files you point to. `canot mcp` exposes them over MCP (stdio)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

TOPICS = {"commands": "commands.md", "scan": "scan.md", "on-prem": "on-prem.md", "targets": "targets.md", "discrepancies": "discrepancies.md"}
ALIASES = {"command": "commands", "drill": "commands", "mcp": "commands", "onprem": "on-prem", "target": "targets",
           "discrepancy": "discrepancies", "mismatches": "discrepancies"}
COMMANDS_SECTION, NEXT_SECTION = "## The commands", "## The sources"
MAX_JSON_BYTES = 50_000_000
SCAN_SCHEMAS = ("canot.scan/1", "canot.scan/2")  # /2 = /1 + family, capabilities, links, exit map
REPLAY_SCHEMAS = ("canot.replay-dbt/1", "canot.rejeu-dbt/1")  # the second one: files written before the rename


def _docs_dir() -> Path:
    """CANOT_DOCS_DIR if set, then the installed package, then the development repository."""
    here = Path(__file__).parent
    env = os.environ.get("CANOT_DOCS_DIR")
    for p in ((Path(env),) if env else ()) + (here / "docs", here.parents[1] / "docs"):
        if (p / TOPICS["commands"]).exists():
            return p
    raise FileNotFoundError("documentation not found")


def read_doc(topic: str) -> str:
    """A full documentation page: commands, scan, on-prem, targets, discrepancies."""
    key = topic.strip().lower().removesuffix(".md")
    key = ALIASES.get(key, key)
    if key not in TOPICS:
        return f"Unknown topic: {topic!r}. Topics: {', '.join(TOPICS)}."
    path = _docs_dir() / TOPICS[key]
    if not path.is_file():
        return f"Page {TOPICS[key]} is missing from the documentation folder."
    return path.read_text(encoding="utf-8")


def _sections() -> list[tuple[str, str, str]]:
    out = []
    docs = _docs_dir()
    for topic, fname in TOPICS.items():
        if not (docs / fname).is_file():
            continue
        title, buf = topic, []
        for line in (docs / fname).read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                if buf:
                    out.append((topic, title, "\n".join(buf).strip()))
                title, buf = line.lstrip("# ").strip(), []
            else:
                buf.append(line)
        if buf:
            out.append((topic, title, "\n".join(buf).strip()))
    return [s for s in out if s[2]]


def search_docs(query: str, limit: int = 5) -> list[dict]:
    """Documentation passages that answer the query (keywords), most relevant first."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    scored = []
    for topic, title, text in _sections():
        hay_t, hay = title.lower(), text.lower()
        score = sum(3 * hay_t.count(w) + hay.count(w) for w in words)
        if score:
            scored.append((score, {"page": topic, "section": title, "text": text[:1500]}))
    return [s for _, s in sorted(scored, key=lambda x: -x[0])[:limit]]


def commands() -> str:
    """The table of commands and their options."""
    text = read_doc("commands")
    start = text.find(COMMANDS_SECTION)
    if start < 0:
        return f"Section {COMMANDS_SECTION!r} not found in {TOPICS['commands']}; full page follows.\n\n{text}"
    end = text.find(NEXT_SECTION, start)
    if end < 0:  # otherwise up to the next level-2 heading
        nxt = re.search(r"^## ", text[start + len(COMMANDS_SECTION):], re.M)
        end = start + len(COMMANDS_SECTION) + nxt.start() if nxt else len(text)
    return text[start:end].strip()


def _load(path: str) -> dict:
    p = Path(path).expanduser()
    if p.suffix != ".json" or not p.is_file():
        raise ValueError("give the path of a .json file produced by Canot (scan.json, report.json or replay-dbt.json)")
    if p.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("file too large")
    return json.loads(p.read_text(encoding="utf-8"))


def _fig(f) -> str:
    if not isinstance(f, dict):
        return str(f)
    n = f.get("nature")
    if n in ("not_read", "not_estimated"):
        return f"{n} ({f.get('raison')})"
    if n == "estimated":
        return f"estimated: {f.get('bas')} to {f.get('haut')} {f.get('unite') or ''}".strip()
    return f"{n}: {f.get('valeur')} {f.get('unite') or ''}".strip()


def explain_scan(path: str, max_findings: int = 40) -> dict:
    """Summary of a scan.json: scope, "to review" findings, reads not done, what is not quantified."""
    r = _load(path)
    if r.get("schema") not in SCAN_SCHEMAS:
        raise ValueError("this file is not a Canot scan.json")
    flags = [{"code": f["code"], "target": f["cible"], "observed": f["texte"]} for f in r.get("drapeaux", [])[:max_findings]]
    nl = [{"read": f"{l['id']} {l['vue']}", "reason": l.get("raison")} for l in r.get("lectures", []) if l.get("statut") == "not_read"]
    return {
        "platform": r.get("plateforme"), "date": r.get("as_of"), "scope": r.get("perimetre"), "grant_level": r.get("niveau_droits"),
        "summary": r.get("resume"), "findings": flags, "findings_total": len(r.get("drapeaux", [])),
        "reads_not_done": nl, "not_estimated": r.get("non_estime"), "freshness": r.get("fraicheur"),
        "replayability": (r.get("rejouabilite") or {}).get("base"),
        # /2 only; None on a /1 scan.json
        "family": r.get("famille"), "capabilities": r.get("capacites"), "exit_map": r.get("carte_sortie") or None,
        "not_provable": _not_provable(r),
        "good_to_know": ["Each finding is \"To review\", with no severity level: it states what is observed and what is not.",
                         "The absence of a finding only holds for the reads that were done.",
                         "Accepted by DuckDB does not mean an identical result: only canot drill compares results.",
                         "A scan takes inventory: it exports nothing and proves nothing. Export and evidence are canot drill "
                         "capabilities (canot replay-dbt for the P3 evidence of a dbt project)."],
    }


def _not_provable(r: dict) -> dict | None:
    """What is counted but cannot be proven off the platform: objects "to rebuild" and findings F13 to F16."""
    if r.get("schema") != "canot.scan/2":
        return None
    to_rebuild = sum((r.get("carte_sortie") or {}).get("a_reconstruire", {}).values())
    codes = {}
    for f in r.get("drapeaux", []):
        if f["code"] in ("F13", "F14", "F15", "F16"):
            codes[f["code"]] = codes.get(f["code"], 0) + 1
    evidence = [p.get("forme") for p in (r.get("capacites") or {}).get("preuve") or []]
    return {"objects_to_rebuild": to_rebuild, "findings": codes, "declared_evidence_forms": evidence}


def capabilities(platform: str | None = None) -> dict:
    """What Canot supports per platform (inventory, export, evidence P1-P5), its validation status and the recorded test date.
    Read from the connector registry, never guessed."""
    from . import connectors

    if platform:
        c = connectors.get(platform.strip().lower())
        if c is None:
            return {"error": f"Unknown platform: {platform!r}. Platforms: {', '.join(connectors.CONNECTORS)}."}
        items = [c.capacites()]
    else:
        items = [c.capacites() for c in connectors.CONNECTORS.values()]
    return {"connectors": items, "evidence_forms": connectors.PREUVES,
            "good_to_know": [f"status \"{connectors.TESTE}\": seen on a real platform; \"{connectors.NON_TESTE}\": code present, never run on a real account.",
                             "teste_sur: date and subject of the test recorded in the repository documentation; empty if no test is recorded there.",
                             "Inventory = canot scan; export and evidence = canot drill (P3 for a dbt project: canot replay-dbt). "
                             "A missing evidence form is not replaced by any score."]}


def explain_report(path: str, max_rows: int = 30) -> dict:
    """Summary of an exercise report.json (parity, tables and queries with a mismatch, leads from the discrepancy catalog)
    or of a replay-dbt.json (P3 evidence: identical, different, not compared and not replayable models, with their reasons)."""
    r = _load(path)
    if r.get("schema") in REPLAY_SCHEMAS:
        mods = r.get("modeles") or []
        return {"summary": r.get("summary"), "form": "P3 pipeline replay",
                "different": [{"model": m["modele"], "reason": m.get("raison")} for m in mods if m.get("statut") == "different"][:max_rows],
                "not_replayable": [{"model": m["modele"], "reason": m.get("raison")} for m in mods if m.get("statut") == "non_rejouable"][:max_rows],
                "not_compared": [m["modele"] for m in mods if m.get("statut") == "non_compare"][:max_rows],
                "rules": r.get("regles"),
                "good_to_know": ["\"identique\" (identical): same compiled SQL, copied inputs, DuckDB output compared with the original table.",
                                 "A model that is not replayable or not compared is not proven: it is counted with its reason.",
                                 "The evidence signature attests its integrity, not the author nor the date."]}
    if "summary" not in r or "checks" not in r:
        raise ValueError("this file is not a Canot report.json")
    bad_t = [{"table": c["table"], "source_rows": c.get("rows_source"), "copy_rows": c.get("rows_target"), "schema_ok": c.get("schema_ok"),
              "stats_ok": c.get("stats_ok"), "fingerprint_ok": c.get("fingerprint_ok"), "stats_diffs": c.get("stats_diffs", [])[:5],
              "precision_only": bool(c.get("precision_cols")) and c.get("stats_ok") is not False}
             for c in r["checks"] if not c.get("ok")][:max_rows]
    bad_q = [{"status": q.get("status"), "detail": str(q.get("detail"))[:300], "query": str(q.get("sql"))[:200]}
             for q in r.get("queries", []) if q.get("status") != "match"][:max_rows]
    try:
        catalog = [l.lstrip("# ").strip() for l in read_doc("discrepancies").splitlines() if l.startswith("## ")]
    except OSError:
        catalog = []
    return {"summary": r["summary"], "tables_with_mismatch": bad_t, "queries_with_mismatch": bad_q, "discrepancy_catalog": catalog,
            "good_to_know": ["The quoted queries come from your history: review them before sharing.",
                             "A precision-only mismatch (nanoseconds with Iceberg v2) needs a decision; it is not corruption.",
                             "The evidence signature attests its integrity, not the author nor the date."]}


TOOLS = (commands, search_docs, read_doc, capabilities, explain_scan, explain_report)


def serve() -> None:
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise ModuleNotFoundError("The MCP connector is missing. Reinstall Canot: pip install -U canot", name="mcp") from e
    server = Server("canot")
    for fn in TOOLS:
        server.tool()(fn)
    server.run()
