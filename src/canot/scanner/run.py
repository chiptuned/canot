"""Orchestration du scan complet : collect() -> analyze() -> render(). analyze et render sont purs ; seule la collecte parle à la plateforme."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .. import __version__
from ..connectors import for_source, supports_full  # noqa: F401 : supports_full reste importable d'ici
from .model import new_salt

LEVELS = {1: "inventaire, volumes, consommation", 2: "+ requêtes (formes normalisées), lectures par table, politiques", 3: "+ droits et outils (pseudonymisés)"}


def _versions() -> dict:
    import duckdb
    import sqlglot

    return {"canot": __version__, "sqlglot": sqlglot.__version__, "duckdb": duckdb.__version__}


def collect(src, *, level: int, days: int | None = None, log: Callable[[str], None] = lambda s: None) -> tuple[dict, list[dict]]:
    """Lit la plateforme (lecture seule). Renvoie (snapshot, familles brutes) ; le texte SQL brut ne vit que dans le second retour."""
    if not supports_full(src):
        raise ValueError(f"scan complet non disponible pour {getattr(src, 'dialect', None)}")
    salt = new_salt()
    snap, raw = for_source(src).scan_collect(src, level=level, salt=salt, days=days)
    snap["niveau_droits"] = level
    snap.setdefault("mode_sql", "lecture seule")
    snap["versions"] = _versions()
    # rejouabilité : le texte brut est normalisé ici, puis jeté
    from . import replay

    # squelette typé pour EXPLAIN : tables et vues dont on connaît les colonnes
    columns = [t for t in snap.get("tables", []) if t.get("colonnes")]
    for o in snap.get("objets", []):
        if o.get("kind") in ("vue", "vue_materialisee") and o.get("colonnes"):
            parts = o["id"].split(".")
            columns.append({"base": parts[-3] if len(parts) > 2 else None, "schema": parts[-2] if len(parts) > 1 else o.get("schema"),
                            "nom": parts[-1], "colonnes": o["colonnes"]})
    known = {o["id"].split(".")[-1].upper() for o in snap.get("objets", []) if o.get("kind") in ("fonction", "procedure")}
    ext = [o["id"] for o in snap.get("objets", []) if o.get("kind") == "stage" and o.get("externe")]
    fenetre = snap.get("requetes")
    if not fenetre:  # fenêtre réelle des lectures d'historique (plusieurs régions : de la plus ancienne à la plus récente)
        wins = [l["fenetre"] for l in snap.get("lectures", []) if l["statut"] == "lu" and l.get("fenetre") and "familles" in l["vue"].lower()]
        fenetre = {"du": min(w["du"] for w in wins), "au": max(w["au"] for w in wins)} if wins else None
    lids = [l["id"] for l in snap.get("lectures", []) if l["statut"] == "lu" and any(k in l["vue"].upper() for k in ("QUERY_HISTORY", "JOBS"))]
    familles, rejo = replay.process_familles(raw, src.dialect, columns=columns or None, known_functions=known, salt=salt,
                                             fenetre={"du": fenetre.get("du"), "au": fenetre.get("au")} if fenetre else None,
                                             lectures=lids, external_stages=ext)
    snap["familles"] = familles
    snap["rejouabilite"] = rejo
    return snap, raw


def partage(result: dict) -> dict:
    """Synthèse transmissible à q4o ou à un conseil : des comptes et des fourchettes, jamais un nom de table, un SQL ni une identité."""
    from collections import Counter

    rj = (result.get("rejouabilite") or {}).get("taux", {}).get("familles") or {}
    lectures = result.get("lectures", [])
    return {
        "schema": "canot.partage/1", "as_of": result.get("as_of"), "plateforme": result.get("plateforme"), "niveau_droits": result.get("niveau_droits"),
        "resume": result.get("resume"),
        "tables": len(result.get("tables", [])), "objets": dict(sorted(Counter(o["kind"] for o in result.get("objets", [])).items())),
        "constats": dict(sorted(Counter(f["code"] for f in result.get("drapeaux", [])).items())),
        "rejouabilite_familles_pct": rj.get("valeur") if rj.get("nature") == "calculé" else None,
        "lectures": {"faites": sum(1 for l in lectures if l["statut"] == "lu"), "non_lues": sum(1 for l in lectures if l["statut"] == "non_lu")},
        "versions": result.get("versions"),
    }


def scan_full(src, out: Path, *, level: int = 3, credit_price: float | None = None, include_sql_text: bool = False,
              log: Callable[[str], None] = lambda s: None) -> dict:
    from . import replay
    from .analyze import analyze
    from .render import render_html, write_csv

    snap, raw = collect(src, level=level, log=log)
    result = analyze(snap, credit_price=credit_price)
    out.mkdir(parents=True, exist_ok=True)
    result["avec_sql_brut"] = bool(include_sql_text)
    (out / "scan.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "scan.html").write_text(render_html(result), encoding="utf-8")
    write_csv(result, out)
    (out / "scan-partage.json").write_text(json.dumps(partage(result), indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if include_sql_text:
        p = out / "scan-sql-brut.json"
        p.write_text(json.dumps(replay.raw_text_export(raw, src.dialect), indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(p, 0o600)
    return result
