"""Inventory of a dbt project from its artifacts: target/manifest.json, run_results.json, catalog.json.

No network access, no dbt command: JSON files are read. The models' SQL is customer code: it never leaves this
collection. Only fingerprints (sha256 of the normalized form, literals masked), line counts, function names and the
DuckDB translation category come out (scanner/replay.py, same rules as for queries).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

import duckdb
import sqlglot

from . import replay
from .model import Collector, fig, now_iso, pseudo

SCHEMA = "canot.scan/2"
# dbt adapter type -> sqlglot dialect; absent = sqlglot generic dialect
DIALECTES = {"snowflake": "snowflake", "bigquery": "bigquery", "databricks": "databricks", "spark": "spark", "glue": "spark",
             "postgres": "postgres", "redshift": "redshift", "duckdb": "duckdb", "trino": "trino", "starburst": "trino",
             "athena": "athena", "sqlserver": "tsql", "synapse": "tsql", "fabric": "tsql", "oracle": "oracle",
             "clickhouse": "clickhouse", "teradata": "teradata", "materialize": "postgres", "risingwave": "postgres", "doris": "doris"}
# materializations that store data (a "table" relation to copy)
STOCKEES = ("table", "incremental", "seed", "snapshot", "dynamic_table", "materialized_view")
ECHECS = ("error", "fail", "runtime error")
NON_EXECUTE = ("skipped", "no-op")
R_JINJA = "compiled SQL absent and Jinja not resolved (dbt compile produces it)"
R_PYTHON = "Python model: not analyzed (depends on the adapter API)"

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.S)
_BLOC = re.compile(r"\{%-?\s*(\w+)", re.S)
_APPEL = re.compile(r"\s*([A-Za-z_][\w.]*)")
_REF = re.compile(r"\{\{\s*ref\(\s*(['\"])([^'\"]+)\1(?:\s*,\s*(['\"])([^'\"]+)\3)?\s*\)\s*\}\}")
_SRC = re.compile(r"\{\{\s*source\(\s*(['\"])([^'\"]+)\1\s*,\s*(['\"])([^'\"]+)\3\s*\)\s*\}\}")
_SIMPLES = {"ref", "source", "config", "this"}  # template without logic: the relation is known at read time


class ProjetDbt:
    """A folder of dbt artifacts. `dbt://PATH`: PATH = the project, its target/ folder, or a manifest.json."""

    dialect = "dbt"
    kind = "dbt"

    def __init__(self, url: str):
        rest = url
        for p in ("dbt+file://", "dbt://"):
            if url.startswith(p):
                rest = url[len(p):]
                break
        path = Path(rest or ".").expanduser()
        if path.is_file() and path.name.endswith(".json"):
            cible = path.parent
        elif (path / "manifest.json").is_file():
            cible = path
        elif (path / "target" / "manifest.json").is_file():
            cible = path / "target"
        else:
            raise ValueError(f"dbt URL: no manifest.json in {rest!r} nor in its target/ folder. "
                             "`dbt parse` produces it by reading the project files, without connecting to the warehouse.")
        self.cible = cible.resolve()

    def describe(self) -> str:
        return "dbt:" + self.cible.parent.name

    def close(self) -> None:
        pass


def _json(p: Path) -> dict:
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError("unreadable dbt artifact")
    return d


@lru_cache(maxsize=1)
def fonctions_duckdb() -> frozenset[str]:
    con = duckdb.connect(config={"enable_external_access": False})
    try:
        return frozenset(str(r[0]).upper() for r in con.execute("select distinct function_name from duckdb_functions()").fetchall())
    finally:
        con.close()


def _duree(t0: float) -> float:
    return round(time.monotonic() - t0, 3)


def _relation(n: dict) -> str | None:
    r = n.get("relation_name")
    return r.replace('"', "").replace("`", "") if r else None


def _lignes(text: str | None) -> int | None:
    return len([x for x in text.splitlines() if x.strip()]) if text is not None else None


def jinja(raw: str) -> dict:
    """Template usage: blocks ({% if %}, {% for %}…), macro calls other than ref/source/config. Counts only."""
    blocs = [b for b in _BLOC.findall(raw or "") if not b.startswith("end")]
    appels = []
    for e in _EXPR.findall(raw or ""):
        m = _APPEL.match(e)
        if m and m.group(1).split(".")[0] not in _SIMPLES:
            appels.append(m.group(1))
    return {"blocs_n": len(blocs), "appels_n": len(appels), "dynamique": bool(blocs or appels)}


def _sans_gabarit(raw: str, noms: dict, sources: dict) -> str | None:
    """Raw SQL without template logic: ref() and source() replaced by the relation; None if Jinja remains."""
    def ref(m):
        return noms.get(m.group(4) or m.group(2)) or m.group(0)

    def src(m):
        return sources.get((m.group(2), m.group(4))) or m.group(0)

    sql = _SRC.sub(src, _REF.sub(ref, raw))
    sql = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", " ", sql, flags=re.S)
    sql = re.sub(r"\{#.*?#\}", " ", sql, flags=re.S)
    return None if ("{{" in sql or "{%" in sql) else sql


def analyse_sql(n: dict, dialect: str | None, skel, known: frozenset[str], noms: dict, sources: dict) -> dict:
    """What is kept of a node's code: fingerprint, lines, template, functions, translation category. Never the text."""
    raw = n.get("raw_code") if n.get("raw_code") is not None else n.get("raw_sql")
    compiled = n.get("compiled_code") if n.get("compiled_code") is not None else n.get("compiled_sql")
    lang = (n.get("language") or "sql").lower()
    out = {"langage": lang, "lignes": _lignes(raw), "jinja": jinja(raw or ""), "analyse": "not analyzed", "empreinte": None,
           "categorie": None, "raison": None, "fonctions": [], "absentes": [], "constructions": [], "secret": False}
    if lang != "sql":
        out["raison"] = R_PYTHON
        out["empreinte"] = replay._sha(" ".join((raw or "").split())) if raw else None
        return out
    sql = None
    if compiled and compiled.strip():
        sql, out["analyse"] = compiled, "compiled"
    elif raw and raw.strip():
        sql = _sans_gabarit(raw, noms, sources)
        if sql is None:
            out["raison"] = R_JINJA
            out["empreinte"] = replay._sha("C1\0" + replay._lexical(raw))
            return out
        out["analyse"] = "raw"
    if sql is None:
        out["raison"] = "code absent from the manifest"
        return out
    if replay.is_secret_query(sql):
        out["secret"] = True
        out["raison"] = "secret detected: counted then dropped"
        return out
    with replay._quiet():
        tree = replay._parse(sql, dialect)
        _, infos = replay._normalize(sql, tree, dialect or "")
        out["empreinte"] = infos["empreinte"]
        if infos.get("racine") is None:
            tree = None
        fx = replay._fonctions(tree, known)
        out["fonctions"] = fx
        out["constructions"] = [f for f in fx if ":" not in f]
        duck = fonctions_duckdb()
        out["absentes"] = sorted({f.split(":", 1)[1] for f in fx if f.startswith("moteur:") and f.split(":", 1)[1] not in duck})
        out["categorie"], out["raison"] = replay._categorize(tree, dialect or "", skel, known)
    return out


def portabilite(a: dict, materialisation: str | None, mat_propre: bool, adaptateur: str) -> tuple[str, str]:
    """(status, reason); "to rebuild" only on a read fact (function absent from DuckDB, translation impossible,
    adapter-specific materialization), never assumed."""
    if mat_propre:
        return "a_reconstruire", f"materialization {materialisation} specific to the {adaptateur} adapter"
    if a["langage"] != "sql":
        return "non_determine", a["raison"] or R_PYTHON
    if a["absentes"]:
        return "a_reconstruire", replay.R_FONCTION_ABSENTE + ": " + ", ".join(a["absentes"][:5])
    if a["categorie"] == "C3":
        return "a_reconstruire", replay.R_C3 + " (sqlglot)"
    if a["categorie"] == "C5" and a["raison"] in (replay.R_FONCTION_ABSENTE, replay.R_UDF):
        return "a_reconstruire", a["raison"]
    if a["categorie"] == "C0" or (a["categorie"] is None and a["raison"] in (replay.R_NON_TESTE, replay.R_HORS_PERIMETRE)):
        return "traduisible", "SQL translated to DuckDB by sqlglot" + (", accepted by EXPLAIN" if a["categorie"] == "C0" else ", not tested")
    return "non_determine", a["raison"] or "not analyzed"


def _run_results(rr: dict | None) -> dict[str, dict]:
    out = {}
    for r in (rr or {}).get("results") or []:
        uid = r.get("unique_id")
        if not uid:
            continue
        fin = max((t.get("completed_at") for t in r.get("timing") or [] if t.get("completed_at")), default=None)
        status = str(r.get("status") or "").lower() or None
        out[uid] = {"etat": status, "fin": fin[:19] + "Z" if fin else None,
                    "executions_n": 0 if status in NON_EXECUTE else 1, "echecs_n": 1 if status in ECHECS else 0,
                    "lignes_en_echec": r.get("failures") if isinstance(r.get("failures"), int) else None}
    return out


def _catalogue(cat: dict | None) -> dict[str, dict]:
    out = {}
    for sect in ("nodes", "sources"):
        for uid, e in ((cat or {}).get(sect) or {}).items():
            cols = sorted(((c or {}).get("index") or 0, (c or {}).get("name"), (c or {}).get("type")) for c in (e.get("columns") or {}).values())
            stats = e.get("stats") or {}

            def stat(*keys):
                for k in keys:
                    s = stats.get(k) or {}
                    if s.get("include") and isinstance(s.get("value"), (int, float)):
                        return int(s["value"])
                return None

            meta = e.get("metadata") or {}
            out[uid] = {"colonnes": [{"nom": n, "type": t} for _, n, t in cols if n], "lignes": stat("row_count", "num_rows"),
                        "octets": stat("bytes", "num_bytes"), "type": meta.get("type"), "proprietaire": meta.get("owner"),
                        "base": meta.get("database"), "schema": meta.get("schema"), "nom": meta.get("name")}
    return out


def _materialisations(man: dict) -> tuple[set[str], set[str]]:
    """(dbt-core materializations, materializations defined by the project or a third-party package)."""
    core, autres = {"ephemeral"}, set()  # ephemeral: no materialization macro, inlined as a CTE by dbt-core
    projet = (man.get("metadata") or {}).get("project_name")
    for m in (man.get("macros") or {}).values():
        name = m.get("name") or ""
        if not name.startswith("materialization_"):
            continue
        base = name[len("materialization_"):].rsplit("_", 1)[0]
        if m.get("package_name") == "dbt" and name.endswith("_default"):
            core.add(base)
        elif m.get("package_name") == projet or not str(m.get("package_name") or "").startswith("dbt"):
            autres.add(base)
    return core, autres


def _table(n: dict, cat: dict | None, run: dict | None, adaptateur: str, salt: bytes, flag: str) -> dict:
    ext = n.get("external") or {}
    loc = ext.get("location") if isinstance(ext, dict) else None
    c = cat or {}
    return {
        "id": _relation(n) or n["unique_id"], "base": n.get("database"), "schema": n.get("schema"),
        "nom": n.get("alias") or n.get("identifier") or n.get("name"), "type": "EXTERNE" if loc else "TABLE",
        "flags": [flag], "lignes": c.get("lignes"), "octets_actifs": c.get("octets"), "octets_historique": None, "clone_de": None,
        "colonnes": c.get("colonnes") or [], "retention_jours": None, "derniere_ddl": None,
        "derniere_ecriture": (run or {}).get("fin") if (run or {}).get("etat") == "success" else None,
        "derniere_lecture": None, "n_lectures": None, "n_ecritures": None,
        "proprietaire": pseudo(c.get("proprietaire"), salt, "r"), "politiques": [], "etiquettes": [], "supprimee_le": None,
        "externe_uri": loc, "partitionnement": None, "clustering": [],
        "stockage": {"connexion": f"dbt:{adaptateur}", "type": adaptateur},
    }


def _run_fields(run: dict | None, rr_lu: bool) -> dict:
    if run:
        return {"etat": run["etat"], "executions_n": run["executions_n"], "echecs_n": run["echecs_n"], "derniere_execution": run["fin"]}
    # run_results read but node absent from the last invocation: 0 measured executions; not read: None
    return {"etat": None, "executions_n": 0 if rr_lu else None, "echecs_n": 0 if rr_lu else None, "derniere_execution": None}


def _objet(kind: str, uid: str, **kw) -> dict:
    o = {"kind": kind, "id": uid, "schema": None, "langage": None, "proprietaire": None, "externe": False, "dependances_externes": [],
         "reference": [], "etat": None, "planification": None, "executions_n": None, "echecs_n": None, "derniere_execution": None,
         "sql_dynamique": None, "detail": {}}
    o.update(kw)
    return o


def _rejouabilite(analyses: list[dict], skel_lu: bool, n_secrets: int) -> dict:
    cats = {c: {"familles": sum(1 for a in analyses if a["categorie"] == c), "executions": None, "poids": None} for c in replay.CATEGORIES}
    test = [a for a in analyses if a["categorie"] in replay.BASE_TAUX]
    non_testees = [a for a in analyses if a["categorie"] is None]
    suffixe = f"SQL of the manifest models and snapshots, sqlglot {sqlglot.__version__}, duckdb {duckdb.__version__}"
    if skel_lu and test:
        taux_f = fig(round(100 * cats["C0"]["familles"] / len(test), 1), nature="computed", unite="%",
                     formule=f"{replay.LIBELLE_C0} (C0) / C0 to C6; one family = one model or snapshot", hypotheses=[replay.MENTION])
    else:
        taux_f = fig(nature="not_estimated", raison="catalog.json not read: DuckDB EXPLAIN not run" if not skel_lu else "no testable model")
    fonctions = Counter(f for a in analyses for f in a["fonctions"])
    return {
        "categories": cats, "base": f"{cats['C0']['familles']}/{len(test)} models tested, {len(non_testees)} not tested, {suffixe}",
        "mention": replay.MENTION,
        "libelle_c0": replay.LIBELLE_C0, "poids_unite": None,
        "taux": {"familles": taux_f,
                 "executions": fig(nature="not_estimated", raison="not applicable: dbt files carry no per-query history"),
                 "poids": fig(nature="not_read", raison="credits and bytes not read (dbt files)")},
        "bases": {"familles": suffixe}, "non_testees": {"familles": len(non_testees), "executions": None},
        "secrets_ecartes": {"familles": n_secrets, "executions": None}, "fonctions": dict(sorted(fonctions.items())),
    }


def collect(projet: ProjetDbt, *, salt: bytes) -> tuple[dict, list[dict]]:
    """(snapshot canot.scan/2, []): no raw family, the SQL never leaves here. Does not raise on a missing artifact."""
    t0 = time.monotonic()
    c = Collector(prefix="B")
    d = projet.cible
    vue = f"{d.name}/"

    def lire(nom: str, compte):
        res = c.read(vue + nom, lambda: _json(d / nom))
        if res is not None:
            c.lectures[-1]["lignes"] = compte(res)
        return res

    man = lire("manifest.json", lambda m: sum(len(m.get(k) or {}) for k in ("nodes", "sources", "macros", "exposures", "metrics",
                                                                            "semantic_models", "saved_queries", "unit_tests", "functions"))) or {}
    rr = lire("run_results.json", lambda r: len(r.get("results") or []))
    if rr is not None:
        c.lectures[-1]["fenetre"] = {"du": (rr.get("metadata") or {}).get("invocation_started_at"), "au": (rr.get("metadata") or {}).get("generated_at")}
    cat = lire("catalog.json", lambda x: len(x.get("nodes") or {}) + len(x.get("sources") or {}))

    meta = man.get("metadata") or {}
    adaptateur = str(meta.get("adapter_type") or "unknown")
    projet_nom = meta.get("project_name") or d.parent.name
    dialect = DIALECTES.get(adaptateur)
    nodes, sources = man.get("nodes") or {}, man.get("sources") or {}
    runs, catalogue = _run_results(rr), _catalogue(cat)
    core, autres = _materialisations(man)
    fonctions_dbt = man.get("functions") or {}
    known = frozenset(str(f.get("name") or "").upper() for f in fonctions_dbt.values())
    skel = None
    if cat is not None:
        cols = [{"base": e["base"], "schema": e["schema"], "nom": e["nom"], "colonnes": e["colonnes"]} for e in catalogue.values() if e["colonnes"]]
        skel = replay._Skeleton(cols, dialect or "")
    noms = {n.get("name"): _relation(n) for n in nodes.values() if _relation(n)}
    rels_src = {(s.get("source_name"), s.get("name")): _relation(s) for s in sources.values() if _relation(s)}
    rel = {uid: _relation(n) for uid, n in {**nodes, **sources}.items()}

    tables, objets, liens, analyses_rejeu = [], [], [], []
    tests_par_noeud = Counter(n.get("attached_node") for n in nodes.values() if n.get("resource_type") == "test")
    n_secrets = 0
    try:
        for uid, s in sorted(sources.items()):
            tables.append(_table(s, catalogue.get(uid), None, adaptateur, salt, "dbt:source"))
            fr = (s.get("freshness") or {})
            objets.append(_objet("source_dbt", uid, schema=s.get("schema"), famille_objet="donnees", portabilite="portable_tel_quel",
                                 sorties=[rel[uid]] if rel.get(uid) else [], moteur=adaptateur,
                                 detail={"relation": rel.get(uid), "source": s.get("source_name"), "chargeur": s.get("loader") or None,
                                         "fraicheur_declaree": bool((fr.get("warn_after") or {}).get("count") or (fr.get("error_after") or {}).get("count")),
                                         "colonnes_documentees_n": len(s.get("columns") or {}), "tests_n": tests_par_noeud.get(uid, 0)}))
        for uid, n in sorted(nodes.items()):
            rt = n.get("resource_type")
            if rt not in ("model", "test", "seed", "snapshot"):
                continue
            conf = n.get("config") or {}
            mat = conf.get("materialized") or rt
            parents = list((n.get("depends_on") or {}).get("nodes") or [])
            macros = list((n.get("depends_on") or {}).get("macros") or [])
            run = runs.get(uid)
            a = analyse_sql(n, dialect, skel, known, noms, rels_src) if rt != "seed" else None
            if a and a["secret"]:
                n_secrets += 1
            mat_propre = rt in ("model", "snapshot") and mat not in core and mat not in autres
            relation = rel.get(uid)
            common = dict(schema=n.get("schema"), reference=sorted({rel[p] for p in parents if rel.get(p)}), entrees=sorted(parents),
                          moteur=adaptateur, **_run_fields(run, rr is not None))
            if rt == "seed":
                tables.append(_table(n, catalogue.get(uid), run, adaptateur, salt, "dbt:seed"))
                objets.append(_objet("seed_dbt", uid, famille_objet="donnees", portabilite="portable_tel_quel",
                                     sorties=[relation] if relation else [], **common,
                                     detail={"relation": relation, "fichier": n.get("original_file_path"), "materialisation": "seed"}))
                continue
            port, raison_p = portabilite(a, mat, mat_propre, adaptateur)
            detail = {"materialisation": mat, "relation": relation, "paquet": n.get("package_name"), "fichier": n.get("original_file_path"),
                      "analyse": a["analyse"], "categorie": a["categorie"], "raison_rejeu": a["raison"], "raison_portabilite": raison_p,
                      "fonctions_propres": a["absentes"], "constructions": a["constructions"],
                      "macros_n": len(macros), "macros_adaptateur_n": sum(1 for m in macros if m.split(".")[1:2] == [f"dbt_{adaptateur}"]),
                      "jinja_blocs_n": a["jinja"]["blocs_n"], "jinja_appels_n": a["jinja"]["appels_n"],
                      "hooks_n": len(conf.get("pre-hook") or []) + len(conf.get("post-hook") or [])}
            obj = dict(langage=a["langage"], sql_dynamique=a["jinja"]["dynamique"] if a["lignes"] is not None else None,
                       famille_objet="traitement", portabilite=port, empreinte_code=a["empreinte"], lignes_code=a["lignes"], **common)
            if rt == "test":
                tm = n.get("test_metadata") or {}
                detail.update({"type": tm.get("name") or "singular", "colonne": n.get("column_name"), "attache_a": n.get("attached_node"),
                               "severite": conf.get("severity"), "lignes_en_echec": (run or {}).get("lignes_en_echec")})
                for k in ("materialisation", "relation", "hooks_n"):
                    detail.pop(k, None)
                if tm:  # generic test: SQL produced by a dbt-core or package macro, not dynamic SQL from the project
                    obj["sql_dynamique"] = False
                objets.append(_objet("test_dbt", uid, detail=detail, **obj))
                liens += [{"de": uid, "vers": p, "nature": "depend"} for p in parents]
                continue
            stocke = mat in STOCKEES
            if stocke:
                tables.append(_table(n, catalogue.get(uid), run, adaptateur, salt, f"dbt:{rt}"))
            detail.update({"acces": n.get("access"), "contrat": bool((n.get("contract") or {}).get("enforced")),
                           "tests_n": tests_par_noeud.get(uid, 0), "colonnes_documentees_n": len(n.get("columns") or {})})
            if rt == "snapshot":
                detail["strategie"] = conf.get("strategy")
            elif mat == "incremental":
                detail["strategie"] = conf.get("incremental_strategy")
            objets.append(_objet("snapshot_dbt" if rt == "snapshot" else "modele_dbt", uid, sorties=[relation] if relation and mat != "ephemeral" else [],
                                 detail=detail, **obj))
            liens += [{"de": uid, "vers": p, "nature": "lit"} for p in parents]
            if a["langage"] == "sql" and not a["secret"]:
                analyses_rejeu.append(a)
    finally:
        if skel is not None:
            skel.close()

    for uid, e in sorted((man.get("exposures") or {}).items()):
        parents = sorted((e.get("depends_on") or {}).get("nodes") or [])
        owner = e.get("owner") or {}
        objets.append(_objet("exposition_dbt", uid, famille_objet="rapport", portabilite="non_determine", entrees=parents,
                             proprietaire=pseudo(owner.get("email") or owner.get("name"), salt, "u"),
                             detail={"type": e.get("type"), "maturite": e.get("maturity"), "raison_portabilite": "downstream tool not read"}))
        liens += [{"de": uid, "vers": p, "nature": "depend"} for p in parents]
    for sect, kind in (("metrics", "metrique_dbt"), ("semantic_models", "modele_semantique_dbt"), ("saved_queries", "requete_sauvegardee_dbt"),
                       ("unit_tests", "test_unitaire_dbt"), ("functions", "fonction_dbt")):
        for uid, e in sorted((man.get(sect) or {}).items()):
            parents = sorted((e.get("depends_on") or {}).get("nodes") or [])
            fam = "traitement" if kind in ("test_unitaire_dbt", "fonction_dbt") else "rapport"
            objets.append(_objet(kind, uid, famille_objet=fam, portabilite="non_determine", entrees=parents,
                                 detail={"raison_portabilite": "not analyzed by this connector"}))
            liens += [{"de": uid, "vers": p, "nature": "depend"} for p in parents]

    macros = man.get("macros") or {}
    for uid, m in sorted(macros.items()):
        if m.get("package_name") != projet_nom:
            continue
        sql = m.get("macro_sql") or ""
        name = m.get("name") or ""
        objets.append(_objet("macro_dbt", uid, langage="jinja-sql", famille_objet="code", portabilite="traduisible",
                             empreinte_code=replay._sha(replay._lexical(sql)), lignes_code=_lignes(sql),
                             detail={"fichier": m.get("original_file_path"), "materialisation": name.startswith("materialization_"),
                                     "propre_adaptateur": name.startswith(f"{adaptateur}__"),
                                     "macros_n": len((m.get("depends_on") or {}).get("macros") or [])}))
    paquets = Counter(m.get("package_name") for m in macros.values() if m.get("package_name") != projet_nom)
    for p, nb in sorted(paquets.items(), key=lambda kv: str(kv[0])):
        role = "dbt-core" if p == "dbt" else "adapter" if p == f"dbt_{adaptateur}" else "package"
        objets.append(_objet("paquet_dbt", f"paquet:{p}", famille_objet="code", detail={"macros_n": nb, "role": role}))

    echecs = sum(r["echecs_n"] for r in runs.values())
    rmeta = (rr or {}).get("metadata") or {}
    objets.append(_objet("dag", f"dbt:{projet_nom}", famille_objet="planification", portabilite="traduisible", moteur="dbt",
                         planification="not read (orchestrator outside the dbt files)",
                         executions_n=1 if rr is not None else None, echecs_n=(1 if echecs else 0) if rr is not None else None,
                         derniere_execution=(rmeta.get("generated_at") or "")[:19] + "Z" if rmeta.get("generated_at") else None,
                         etat=f"last invocation: {((rr or {}).get('args') or {}).get('which')}" if rr is not None else None,
                         detail={"noeuds_executes_n": sum(r["executions_n"] for r in runs.values()) if rr is not None else None,
                                 "noeuds_en_echec_n": echecs if rr is not None else None,
                                 "noeuds_avertis_n": sum(1 for r in runs.values() if r["etat"] == "warn") if rr is not None else None,
                                 "duree_s": round(rr["elapsed_time"], 1) if isinstance((rr or {}).get("elapsed_time"), (int, float)) else None,
                                 "fenetre": "last invocation only (run_results.json)", "analyses_n": sum(
                                     1 for n in nodes.values() if n.get("resource_type") == "analysis")}))

    gen = str(meta.get("generated_at") or "")
    snap = {
        "schema": SCHEMA, "plateforme": "dbt", "famille": "fichiers", "as_of": now_iso(),
        "perimetre": f"dbt project {projet_nom}: adapter {adaptateur}, dbt {meta.get('dbt_version') or 'version not read'}"
                     + (f", manifest of {gen[0:10]}" if len(gen) >= 10 else ""),
        "edition": f"dbt {meta.get('dbt_version')}" if meta.get("dbt_version") else None,
        "mode_sql": "no SQL issued (files read)", "lectures": c.lectures, "tables": tables, "objets": objets, "familles": [],
        "liens": sorted(liens, key=lambda x: (x["de"], x["vers"])), "metering": None,
        "rejouabilite": _rejouabilite(analyses_rejeu, skel is not None, n_secrets),
        "cout_du_scan": {"requetes": 0, "duree_s": _duree(t0)},
    }
    return snap, []
