"""Full scan orchestration: collect() -> analyze() -> render(). analyze and render are pure; only collection talks to the platform."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .. import __version__
from ..connectors import effective_level, for_source, supports_full  # noqa: F401 : supports_full reste importable d'ici
from .model import COMPUTED, NOT_READ, READ, READ_ONLY, new_salt

LEVELS = {1: "inventory, volumes, consumption", 2: "+ queries (normalized forms), reads per table, policies", 3: "+ privileges and tools (pseudonymized)"}
# output files of a full scan (scan.json, scan.html, the CSV files of render.CSV_FILES, then these)
SHARE_FILE = "scan-share.json"
RAW_SQL_FILE = "scan-sql-raw.json"
SHARE_SCHEMA = "canot.share/1"


def _versions() -> dict:
    import duckdb
    import sqlglot

    return {"canot": __version__, "sqlglot": sqlglot.__version__, "duckdb": duckdb.__version__}


def collect(src, *, level: int, days: int | None = None, log: Callable[[str], None] = lambda s: None) -> tuple[dict, list[dict]]:
    """Reads the platform (read-only). Returns (snapshot, raw families); raw SQL text only lives in the second item."""
    if not supports_full(src):
        raise ValueError(f"full scan not available for {getattr(src, 'dialect', None)}")
    salt = new_salt()
    conn = for_source(src)
    level = effective_level(src, level)
    snap, raw = conn.scan_collect(src, level=level, salt=salt, days=days)
    snap["niveau_droits"] = level
    snap.setdefault("mode_sql", READ_ONLY)
    snap["versions"] = _versions()
    if not conn.rejeu_familles:  # replayability already computed by collection (dbt artifacts): no history families
        snap.setdefault("familles", [])
        return snap, raw
    # replayability: the raw text is normalized here, then discarded
    from . import replay

    # typed skeleton for EXPLAIN: tables and views whose columns are known
    columns = [t for t in snap.get("tables", []) if t.get("colonnes")]
    for o in snap.get("objets", []):
        if o.get("kind") in ("vue", "vue_materialisee") and o.get("colonnes"):
            parts = o["id"].split(".")
            columns.append({"base": parts[-3] if len(parts) > 2 else None, "schema": parts[-2] if len(parts) > 1 else o.get("schema"),
                            "nom": parts[-1], "colonnes": o["colonnes"]})
    known = {o["id"].split(".")[-1].upper() for o in snap.get("objets", []) if o.get("kind") in ("fonction", "procedure")}
    ext = [o["id"] for o in snap.get("objets", []) if o.get("kind") == "stage" and o.get("externe")]
    fenetre = snap.get("requetes")
    if not fenetre:  # actual window of the history reads (several regions: from the oldest to the most recent)
        wins = [l["fenetre"] for l in snap.get("lectures", []) if l["statut"] == READ and l.get("fenetre") and "familles" in l["vue"].lower()]
        fenetre = {"du": min(w["du"] for w in wins), "au": max(w["au"] for w in wins)} if wins else None
    lids = [l["id"] for l in snap.get("lectures", []) if l["statut"] == READ and any(k in l["vue"].upper() for k in ("QUERY_HISTORY", "JOBS"))]
    familles, rejo = replay.process_familles(raw, src.dialect, columns=columns or None, known_functions=known, salt=salt,
                                             fenetre={"du": fenetre.get("du"), "au": fenetre.get("au")} if fenetre else None,
                                             lectures=lids, external_stages=ext)
    snap["familles"] = familles
    snap["rejouabilite"] = rejo
    return snap, raw


def partage(result: dict) -> dict:
    """Summary that can be passed to q4o or an advisor: counts and ranges, never a table name, SQL or an identity."""
    from collections import Counter

    rj = (result.get("rejouabilite") or {}).get("taux", {}).get("familles") or {}
    lectures = result.get("lectures", [])
    return {
        "schema": SHARE_SCHEMA, "as_of": result.get("as_of"), "plateforme": result.get("plateforme"), "niveau_droits": result.get("niveau_droits"),
        "resume": result.get("resume"),
        "tables": len(result.get("tables", [])), "objets": dict(sorted(Counter(o["kind"] for o in result.get("objets", [])).items())),
        "constats": dict(sorted(Counter(f["code"] for f in result.get("drapeaux", [])).items())),
        "rejouabilite_familles_pct": rj.get("valeur") if rj.get("nature") == COMPUTED else None,
        "lectures": {"faites": sum(1 for l in lectures if l["statut"] == READ), "non_lues": sum(1 for l in lectures if l["statut"] == NOT_READ)},
        "versions": result.get("versions"),
        # optional additions (the schema stays canot.share/1): capability names and counts, nothing else
        **_partage_v2(result),
    }


def _partage_v2(result: dict) -> dict:
    if result.get("schema") != "canot.scan/2":
        return {}
    c = result.get("capacites") or {}
    return {"famille": result.get("famille"),
            "capacites": {"inventaire": c.get("inventaire"), "export": list(c.get("export") or []),
                          "preuve": [p.get("forme") for p in c.get("preuve") or [] if isinstance(p, dict)], "statut": c.get("statut")} if c else None,
            "portabilite": {k: sum(v.values()) for k, v in sorted((result.get("carte_sortie") or {}).items())}}


def scan_full(src, out: Path, *, level: int = 3, credit_price: float | None = None, include_sql_text: bool = False,
              lang: str | None = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """lang: the pane scan.html shows first (--lang; default: the locale, see brand.default_lang). scan.html always holds both
    languages; scan.json, the CSV files and scan-share.json stay English."""
    from .. import brand
    from . import replay
    from .analyze import analyze
    from .render import render_html, write_csv

    snap, raw = collect(src, level=level, log=log)
    result = analyze(snap, credit_price=credit_price)
    result_fr = analyze(snap, credit_price=credit_price, lang="fr")  # French texts of the same analysis, for the French pane only
    out.mkdir(parents=True, exist_ok=True)
    result["avec_sql_brut"] = result_fr["avec_sql_brut"] = bool(include_sql_text)
    (out / "scan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "scan.html").write_text(render_html(result, result_fr, lang=brand.default_lang(lang)), encoding="utf-8")
    write_csv(result, out)
    (out / SHARE_FILE).write_text(json.dumps(partage(result), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if include_sql_text:
        p = out / RAW_SQL_FILE
        p.write_text(json.dumps(replay.raw_text_export(raw, src.dialect), indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(p, 0o600)
    return result
