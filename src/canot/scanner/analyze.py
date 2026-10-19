"""Pure scan analysis: snapshot (contract in model.py) -> scan.json (spec §2).

No I/O, no clock: the reference date is snapshot["as_of"]. Every derived figure goes through `fig`;
a value that was not read stays None. Flags say what is observed and what is not, never what to do.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .. import prices
from ..connectors import FAMILLES, capacites, get as connecteur, profile
from ..scan import NON_PORTABLE, THROUGHPUT_MBPS
from .model import (COMPUTED, ESTIMATED, LABEL_TO_REVIEW, MEASURED, NATURES, NOT_ESTIMATED, NOT_READ, PARTIAL, READ, READ_ONLY,
                    bucket_only, fig, legacy)

SCHEMA = "canot.scan/2"
ETIQUETTE = LABEL_TO_REVIEW
LIBELLES = {
    "F01": "External dependency",
    "F02": "Hidden external dependency",
    "F03": "Write outside the platform",
    "F04": "Owner role with no holder",
    "F05": "Storage with no observed read",
    "F06": "Billed history that does not transfer",
    "F07": "Dropped table still billed",
    "F08": "Dynamic SQL",
    "F09": "External consumers",
    "F10": "Security to rebuild",
    "F11": "Scheduled chain and its failures",
    "F12": "Incomplete read",
    # outside warehouses: raised only on objects that collection read with the /2 fields they require
    "F13": "Processing with no executable equivalent off the platform",
    "F14": "Proprietary extension",
    "F15": "Model with no readable open export",
    "F16": "Business logic in the presentation layer",
}
CODES_HORS_ENTREPOT = ("F13", "F14", "F15", "F16")
KIND_LABELS = {
    "vue": "view", "vue_materialisee": "materialized view", "fonction": "function", "procedure": "procedure", "tache": "task",
    "flux": "stream", "pipe": "pipe", "stage": "stage", "base_importee": "imported database", "partage": "share",
    "table_dynamique": "dynamic table", "modele_ml": "ML model", "politique_masquage": "masking policy",
    "politique_lignes": "row access policy", "requete_planifiee": "scheduled query", "connexion": "connection",
    "integration": "integration", "reservation": "reservation", "etiquette": "tag",
    # canot.scan/2, non-warehouse platforms
    "recette_sql": "SQL recipe", "recette_python": "Python recipe", "recette_visuelle": "visual recipe",
    "recette_plugin": "plugin recipe", "scenario": "scenario", "modele_entraine": "trained model", "notebook": "notebook",
    "dossier_gere": "managed folder", "service_api": "API service", "env_code": "code environment", "plugin": "plugin",
    "mesure": "measure", "calcul_rapport": "report calculation", "rapport": "report", "flux_etl": "ETL flow", "dag": "DAG",
    "programme_sas": "SAS program",
    # dbt project ("fichiers" family): artifacts read offline
    "modele_dbt": "dbt model", "test_dbt": "dbt test", "source_dbt": "dbt source", "seed_dbt": "dbt seed", "snapshot_dbt": "dbt snapshot",
    "macro_dbt": "project dbt macro", "paquet_dbt": "dbt package", "exposition_dbt": "dbt exposure", "metrique_dbt": "dbt metric",
    "modele_semantique_dbt": "dbt semantic model", "requete_sauvegardee_dbt": "dbt saved query",
    "test_unitaire_dbt": "dbt unit test", "fonction_dbt": "dbt function",
}
# plural labels, when the plural is not the label + "s"
KIND_PLURALS = {"macro_dbt": "project dbt macros", "programme_sas": "SAS programs", "flux": "streams", "politique_lignes": "row access policies",
                "requete_sauvegardee_dbt": "dbt saved queries", "modele_ml": "ML models"}
DBT_KINDS = ("modele_dbt", "test_dbt", "source_dbt", "seed_dbt", "snapshot_dbt", "macro_dbt", "paquet_dbt", "exposition_dbt", "metrique_dbt",
             "modele_semantique_dbt", "requete_sauvegardee_dbt", "test_unitaire_dbt", "fonction_dbt")
# objects to rebuild on the destination (counted in the rework)
REBUILD_KINDS = ("vue", "vue_materialisee", "fonction", "procedure", "tache", "flux", "pipe", "stage", "table_dynamique",
                 "modele_ml", "politique_masquage", "politique_lignes", "requete_planifiee", "connexion", "integration")
SCHEDULED_KINDS = ("tache", "requete_planifiee", "pipe", "table_dynamique", "flux", "scenario", "dag")
POLICY_KINDS = ("politique_masquage", "politique_lignes", "etiquette")
# F13: processing whose engine stays on the platform (object type read, never assumed)
SANS_MOTEUR_KINDS = ("recette_visuelle", "recette_plugin")
EXTENSION_KINDS = ("plugin",)
MODELE_KINDS = ("modele_entraine",)
PRESENTATION_KINDS = ("mesure", "calcul_rapport")
FORMATS_OUVERTS = ("mlflow", "onnx", "pmml", "python")
F16_LIGNES = 5  # without a criterion declared by the connector, a measure of 5 lines or more counts as non-trivial

VIVANTE_JOURS = 30   # write observed in the last 30 days
RECREEE_JOURS = 7    # DDL in the last 7 days, with no later write
CREDITS_PAR_HEURE = 1.0  # XS warehouse
TIB = 2 ** 40
PRICE_SOURCES = (prices.SNOWFLAKE_SOURCE, prices.BIGQUERY_SOURCE)
CONTRAT_NON_LU = "your contract price not read"
MENTION_REJOUABILITE = "accepted ≠ identical result"
FROZEN, LIVE, RECREATED = "frozen", "live", "recreated"

KIND_SOURCES = {
    "vue": ("VIEWS", "OBJECT_DEPENDENCIES"), "vue_materialisee": ("VIEWS", "MATERIALIZED_VIEWS"),
    "fonction": ("FUNCTIONS", "ROUTINES"), "procedure": ("PROCEDURES", "ROUTINES"),
    "tache": ("TASKS", "TASK_HISTORY", "SERVERLESS_TASK_HISTORY"), "flux": ("STREAMS", "SHOW STREAMS"),
    "pipe": ("PIPES", "COPY_HISTORY"), "stage": ("STAGES",), "base_importee": ("DATABASES", "datasets.get"),
    "partage": ("SHARES", "SCHEMATA_LINKS", "SHARED_DATASET_USAGE"),
    "table_dynamique": ("DYNAMIC_TABLE_REFRESH_HISTORY", "SHOW DYNAMIC TABLES"), "modele_ml": ("models.list",),
    "politique_masquage": ("POLICY_REFERENCES",), "politique_lignes": ("POLICY_REFERENCES", "rowAccessPolicies.list"),
    "requete_planifiee": ("transferConfigs.list",), "connexion": ("connections.list",),
    "integration": ("INTEGRATIONS", "SHOW INTEGRATIONS"), "reservation": ("RESERVATIONS",), "etiquette": ("TAG_REFERENCES",),
    "dag": ("run_results.json", "manifest.json"), **{k: ("manifest.json",) for k in DBT_KINDS},
}
TABLE_SOURCES = ("TABLES", "TABLE_OPTIONS", "tables.get")
STORAGE_SOURCES = ("TABLE_STORAGE_METRICS", "TABLE_STORAGE", "tables.get")
GRANT_SOURCES = ("GRANTS_TO_USERS", "GRANTS_TO_ROLES")
POLICY_SOURCES = ("POLICY_REFERENCES", "TAG_REFERENCES", "rowAccessPolicies.list", "COLUMN_FIELD_PATHS")
WRITE_SOURCES = ("TABLES", "TABLE_DML_HISTORY", "ACCESS_HISTORY", "tables.get", "JOBS")
CODE_SOURCES = ("COLUMNS", "VIEWS", "FUNCTIONS", "PROCEDURES", "ROUTINES", "TASKS", "PIPES", "transferConfigs.list")
# what a read that was not done limits (F12)
IMPACTS = {
    "ACCESS_HISTORY": "reads and writes per object (F05)",
    "TABLE_QUERY_PRUNING_HISTORY": "fallback for reads per table (F05)",
    "TABLES": "table inventory", "COLUMNS": "non-portable column types",
    "TABLE_STORAGE_METRICS": "active bytes and history (F05, F06, F07, ranges)",
    "TABLE_STORAGE": "volumes per table (F05, F06, ranges)", "tables.get": "volumes per table (F05, F06, ranges)",
    "STORAGE_USAGE": "average billed storage", "METERING_DAILY_HISTORY": "credits per service",
    "WAREHOUSE_METERING_HISTORY": "credits per warehouse", "QUERY_ATTRIBUTION_HISTORY": "credits per query family",
    "TABLE_DML_HISTORY": "latest writes (movement)", "OBJECT_DEPENDENCIES": "dependencies between objects (F02)",
    "VIEWS": "view dependencies (F02)", "FUNCTIONS": "external functions and dynamic SQL (F01, F08)",
    "PROCEDURES": "dynamic SQL (F08)", "ROUTINES": "dynamic SQL (F08)", "TASKS": "scheduled chains (F11)",
    "TASK_HISTORY": "task runs and failures (F11)", "SERVERLESS_TASK_HISTORY": "serverless task runs (F11)",
    "TASK_VERSIONS": "task versions (F11)", "STREAMS": "streams (F11)", "PIPES": "continuous ingestion (F11)",
    "COPY_HISTORY": "observed loads", "STAGES": "external stages (F01)", "DATABASES": "imported databases (F01)",
    "DATA_TRANSFER_HISTORY": "outbound transfers",
    "DYNAMIC_TABLE_REFRESH_HISTORY": "dynamic table refreshes (F11)",
    "AUTOMATIC_CLUSTERING_HISTORY": "automatic clustering credits",
    "MATERIALIZED_VIEW_REFRESH_HISTORY": "materialized view credits",
    "SEARCH_OPTIMIZATION_HISTORY": "search optimization credits",
    "QUERY_ACCELERATION_HISTORY": "query acceleration credits",
    "QUERY_HISTORY": "query families, replayability, observed writes (F03)",
    "POLICY_REFERENCES": "applied policies (F10)", "TAG_REFERENCES": "tags (F10)",
    "GRANTS_TO_ROLES": "owner roles with no holder (F04)", "GRANTS_TO_USERS": "owner roles with no holder (F04)",
    "USERS": "user types (person or service)", "SESSIONS": "client tools", "SHARES": "external consumers (F09)",
    "JOBS": "reads per table (F05), query families, on-demand and reserved costs",
    "JOBS_BY_ORGANIZATION": "queries run from other projects", "JOBS_TIMELINE": "load profile",
    "TABLE_OPTIONS": "external table locations (F01)", "COLUMN_FIELD_PATHS": "policy tags (F10)",
    "SCHEMATA_OPTIONS": "dataset options", "SCHEMATA_LINKS": "linked datasets, Analytics Hub (F09)",
    "SHARED_DATASET_USAGE": "Analytics Hub consumers (F09)", "PARTITIONS": "partitions",
    "MATERIALIZED_VIEWS": "materialized views", "RESERVATIONS": "reserved capacity",
    "CAPACITY_COMMITMENTS": "capacity commitments", "RESERVATIONS_TIMELINE": "reservation usage",
    "datasets.list": "dataset inventory", "datasets.get": "linked and external datasets, dataset permissions (F01, F09)",
    "transferConfigs.list": "scheduled queries (F11)", "rowAccessPolicies.list": "row access policies (F10)",
    "connections.list": "external connections (F01)", "models.list": "ML models",
    "SHOW WAREHOUSES": "warehouse size and suspension", "SHOW DYNAMIC TABLES": "dynamic tables (F11)",
    "SHOW ICEBERG TABLES": "Iceberg tables (F01)", "SHOW EXTERNAL TABLES": "external table locations (F01)",
    "SHOW STREAMS": "streams (F11)", "QUERY_HISTORY × SESSIONS": "tool per query family",
    "transferConfigs.runs.list": "scheduled query runs and failures (F11)", "tables.list": "table inventory",
    # dbt artifacts
    "manifest.json": "the whole dbt project inventory", "run_results.json": "latest runs and failures (F11, movement)",
    "catalog.json": "columns, types and volumes of relations; replayability not tested by DuckDB EXPLAIN",
}
# a read that was not done limits nothing if its replacement was read
FALLBACKS = {"TABLE_QUERY_PRUNING_HISTORY": ("ACCESS_HISTORY",), "TABLE_STORAGE": ("tables.get",), "tables.get": ("TABLE_STORAGE",),
             # regional views refused: the tables.list/get API gives inventory, types, options, definitions and policy tags
             **{v: ("tables.get",) for v in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS")}}

# --- formats (shared with render.py) --------------------------------------------------------------------


def fmt_num(x, dec: int = 0) -> str:
    return f"{x:,.{dec}f}"


def _short_num(v: float) -> str:
    s = fmt_num(v, 0 if abs(v) >= 100 else 1)
    return s[:-2] if s.endswith(".0") else s


def fmt_bytes(n) -> str:
    if n is None:
        return "not read"
    for unit, f in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if abs(n) >= f:
            return f"{_short_num(n / f)} {unit}"
    return f"{int(n)} B"


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fmt_date(v) -> str:
    d = parse_dt(v)
    return d.strftime("%Y-%m-%d") if d else "unknown date"


def pl(n, singular: str, plural: str | None = None) -> str:
    return f"{fmt_num(n)} {singular if n == 1 else (plural or singular + 's')}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# --- hygiene: nothing sensitive goes through the analysis ---------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>)]+", re.I)
_DENY_KEYS = {"sql", "texte", "text", "definition", "body", "corps", "query", "erreur", "error", "message", "email",
              "login", "ip", "bind", "binds", "password", "secret", "token", "environment", "code", "script"}


def _bucket(uri: str) -> str:
    """Bucket only; for http(s), the host without the scheme: a quoted location is not a link."""
    b = bucket_only(uri) or ""
    return re.sub(r"(?i)^https?://", "", b)


def scrub(v):
    """URIs reduced to the bucket, e-mail addresses masked."""
    if not isinstance(v, str):
        return v
    v = _URI.sub(lambda m: _bucket(m.group(0)), v)
    return _EMAIL.sub("[masked]", v)


def _reduce_uri(u) -> str | None:
    if not u:
        return None
    u = str(u)
    return (_bucket(u) or None) if "://" in u else scrub(u.split("/")[0])


def _safe_detail(d) -> dict:
    out = {}
    for k, v in sorted((d or {}).items()):
        if set(str(k).lower().split("_")) & _DENY_KEYS:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = [scrub(x) for x in v if x is None or isinstance(x, (str, int, float, bool))]
        elif v is None or isinstance(v, (str, int, float, bool)):
            out[k] = scrub(v)
    return out


# --- reads --------------------------------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9_]+")


def _tokens(vue: str) -> set[str]:
    vue = re.sub(r"\([^)]*\)", " ", vue)  # "GRANTS_TO_ROLES (role hierarchy)": the label does not count
    out = set()
    for m in _WORD.finditer(vue):
        w, nxt = m.group(0), vue[m.end():m.end() + 2]
        if not w.isupper() and nxt[:1] == "." and nxt[1:2].islower():
            continue  # "tables.get": API call, not the TABLES view
        out.add(w.upper())
    return out


def _match(vue: str, name: str) -> bool:
    """A name with a dot or a space is searched as is; otherwise as a view identifier (neither SHOW nor a "×" join)."""
    if "." in name or " " in name:
        return name.lower() in vue.lower()
    if vue.lstrip().upper().startswith(("SHOW ", "DESCRIBE ")) or "×" in vue:
        return False
    return name.upper() in _tokens(vue)


def short_vue(vue: str) -> str:
    return re.sub(r"(?i)^snowflake\.account_usage\.", "", vue or "")


class _Reads:
    def __init__(self, lectures: list[dict]):
        self.all = lectures

    def find(self, *names) -> list[dict]:
        return [lec for lec in self.all if any(_match(lec["vue"], n) for n in names)]

    def status(self, *names) -> str | None:
        found = self.find(*names)
        if not found:
            return None
        n = sum(lec["statut"] == READ for lec in found)
        return READ if n == len(found) else (PARTIAL if n else NOT_READ)

    def ok(self, *names) -> bool:
        return self.status(*names) in (READ, PARTIAL)

    def ids(self, *names) -> list[str]:
        return sorted(lec["id"] for lec in self.find(*names) if lec["statut"] == READ)

    def why(self, *names) -> str:
        failed = [lec for lec in self.find(*names) if lec["statut"] != READ]
        if not failed:
            return f"{names[0]}: read not attempted"
        lec = failed[0]
        return f"{short_vue(lec['vue'])} not read: {lec['raison'] or 'reason not provided'}"

    def partial(self, *names) -> bool:
        """A read can be only partial (failed windows or calls): collection says so in its reason."""
        return self.status(*names) == PARTIAL or any((lec["raison"] or "").startswith("partial")
                                                     for lec in self.find(*names) if lec["statut"] == READ)

    def window(self, *names) -> tuple[datetime | None, datetime | None]:
        du = [parse_dt((lec["fenetre"] or {}).get("du")) for lec in self.find(*names) if lec["statut"] == READ]
        au = [parse_dt((lec["fenetre"] or {}).get("au")) for lec in self.find(*names) if lec["statut"] == READ]
        du, au = [d for d in du if d], [d for d in au if d]
        return (min(du) if du else None, max(au) if au else None)


def _lecture(lec: dict) -> dict:
    statut = READ if legacy("statut", lec.get("statut")) == READ else NOT_READ
    fen = lec.get("fenetre") or None
    return {"id": str(lec.get("id")), "vue": scrub(str(lec.get("vue") or "")), "statut": statut,
            "raison": scrub(lec.get("raison")),
            "lignes": lec.get("lignes"), "latence_max_h": lec.get("latence_max_h"),
            "fenetre": {"du": fen.get("du"), "au": fen.get("au")} if isinstance(fen, dict) else None}


def _impact_key(vue: str) -> str | None:
    return next((k for k in IMPACTS if _match(vue, k)), None)


# --- tables, objects, families ------------------------------------------------------------------------------


def _non_portables(cols) -> list[dict]:
    return [{"nom": c.get("nom"), "type": c.get("type")} for c in cols or []
            if any(k in str(c.get("type") or "").upper() for k in NON_PORTABLE)]


def _mouvement(t: dict, as_of: datetime) -> tuple[str | None, str]:
    if t.get("supprimee_le"):
        return None, f"dropped on {fmt_date(t['supprimee_le'])}"
    ddl, w, nw = parse_dt(t.get("derniere_ddl")), parse_dt(t.get("derniere_ecriture")), t.get("n_ecritures")
    if ddl and (as_of - ddl).days < RECREEE_JOURS and (w is None or (w - ddl).total_seconds() <= 3600):
        unknown = w is None and nw is None
        return RECREATED, f"last DDL on {fmt_date(ddl)}, " + ("writes not read" if unknown else "no later write")
    if w and (as_of - w).days < VIVANTE_JOURS:
        return LIVE, f"last write on {fmt_date(w)}"
    if w:
        return FROZEN, f"last write on {fmt_date(w)}"
    if nw == 0 and ddl and (as_of - ddl).days >= VIVANTE_JOURS:
        return FROZEN, f"no write observed, last DDL on {fmt_date(ddl)}"
    return None, "writes not read"


def _table(t: dict, as_of: datetime) -> dict:
    mv, preuve = _mouvement(t, as_of)
    cols = t.get("colonnes") or []
    detail = _safe_detail(t.get("detail"))
    return {
        "id": scrub(str(t.get("id"))), "base": t.get("base"), "schema": t.get("schema"), "nom": t.get("nom"),
        "type": t.get("type") or "TABLE", "flags": sorted(str(f) for f in t.get("flags") or []),
        "lignes": t.get("lignes"), "octets_actifs": t.get("octets_actifs"), "octets_historique": t.get("octets_historique"),
        "clone_de": t.get("clone_de"), "retention_jours": t.get("retention_jours"), "proprietaire": t.get("proprietaire"),
        "supprimee_le": t.get("supprimee_le"), "externe_uri": _reduce_uri(t.get("externe_uri")),
        "partitionnement": scrub(t.get("partitionnement")), "clustering": [scrub(c) for c in t.get("clustering") or []],
        "colonnes_n": len(cols), "colonnes_non_portables": _non_portables(cols),
        "politiques": sorted(scrub(p) for p in t.get("politiques") or []), "etiquettes_n": len(t.get("etiquettes") or []),
        "derniere_ddl": t.get("derniere_ddl"),
        # n_pruning: Snowflake fallback without ACCESS_HISTORY; partage: subscriber reads (BigQuery SHARED_DATASET_USAGE)
        "lectures": {"n": t.get("n_lectures"), "derniere": t.get("derniere_lecture"),
                     "n_pruning": detail.get("requetes_pruning_n"), "partage": detail.get("lectures_partage_n")},
        "ecritures": {"n": t.get("n_ecritures"), "derniere": t.get("derniere_ecriture")},
        "mouvement": mv, "mouvement_preuve": preuve, "detail": detail, "drapeaux": [],
        **({"stockage": _stockage(t["stockage"])} if "stockage" in t else {}),
    }


def _stockage(s) -> dict | None:
    if not isinstance(s, dict):
        return None
    return {"connexion": scrub(s.get("connexion")), "type": scrub(s.get("type"))}


def _objet_v2(o: dict) -> dict:
    """Optional canot.scan/2 fields: copied only when present, never raw code (fingerprint and line count)."""
    out = {}
    for k in ("famille_objet", "portabilite", "moteur"):
        if k in o:
            out[k] = scrub(o[k])
    for k in ("entrees", "sorties", "extensions"):
        if k in o:
            out[k] = sorted({scrub(str(x)) for x in o[k] or []})
    if "formats_export" in o:
        out["formats_export"] = None if o["formats_export"] is None else sorted({str(x).lower() for x in o["formats_export"]})
    if "empreinte_code" in o:
        e = o["empreinte_code"]
        out["empreinte_code"] = e if isinstance(e, str) and re.fullmatch(r"[0-9a-f]{64}", e) else None
    if "lignes_code" in o:
        out["lignes_code"] = o["lignes_code"] if isinstance(o["lignes_code"], int) else None
    if "non_triviale" in o:
        out["non_triviale"] = o["non_triviale"] if isinstance(o["non_triviale"], bool) else None
    return out


def _objet(o: dict) -> dict:
    return {
        "kind": o.get("kind"), "id": scrub(str(o.get("id"))), "schema": o.get("schema"), "langage": o.get("langage"),
        "proprietaire": o.get("proprietaire"), "externe": bool(o.get("externe")),
        "dependances_externes": sorted({d for d in (_reduce_uri(x) for x in o.get("dependances_externes") or []) if d}),
        "reference": sorted({scrub(str(r)) for r in o.get("reference") or []}), "etat": scrub(o.get("etat")),
        "planification": scrub(o.get("planification")), "executions_n": o.get("executions_n"), "echecs_n": o.get("echecs_n"),
        "derniere_execution": o.get("derniere_execution"), "sql_dynamique": o.get("sql_dynamique"),
        "detail": _safe_detail(o.get("detail")), "drapeaux": [], **_objet_v2(o),
    }


FAMILLE_KEYS = ("empreinte", "hash_plateforme", "n_exec", "temps_ms", "credits", "octets", "app", "outil", "entrepot",
                "categorie", "ecriture_externe")


def _famille(f: dict) -> dict:
    out = {k: scrub(f.get(k)) for k in FAMILLE_KEYS}
    out["ecriture_externe"] = bool(f.get("ecriture_externe"))
    out["tables"] = sorted(scrub(str(x)) for x in f.get("tables") or [])
    out["fonctions"] = sorted(scrub(str(x)) for x in f.get("fonctions") or [])
    out["sql_normalise"] = scrub(f.get("sql_normalise"))
    uris = f.get("compartiments") or f.get("uris_export") or (f.get("detail") or {}).get("export") or []  # replay: detail.export
    out["compartiments"] = sorted({d for d in (_reduce_uri(u) for u in uris) if d})
    return out


# --- flags --------------------------------------------------------------------------------------------------


def _flag(code, cible_type, cible, texte, base, preuve=None, confiance=None) -> dict:
    return {"code": code, "libelle": LIBELLES[code], "etiquette": ETIQUETTE, "cible_type": cible_type, "cible": cible,
            "texte": texte, "preuve": preuve or {}, "base": sorted(set(base)), "confiance": confiance}


class _Ctx:
    def __init__(self, snap, as_of, reads, tables, objets, familles):
        self.snap, self.plat, self.as_of, self.reads = snap, snap.get("plateforme"), as_of, reads
        self.tables, self.objets, self.familles = tables, objets, familles
        self.present = [t for t in tables if not t["supprimee_le"]]
        c = connecteur(self.plat)
        self.famille = c.famille if c else (snap.get("famille") if snap.get("famille") in FAMILLES else None)

    def kind_ids(self, kind) -> list[str]:
        return self.reads.ids(*KIND_SOURCES.get(kind, ()))


def _external(ctx: _Ctx) -> dict[str, tuple[str, str, list[str]]]:
    """id -> (target type, description, locations) for everything that depends directly on the outside (F01)."""
    out = {}
    for t in ctx.tables:
        if t["supprimee_le"]:
            continue
        if t["type"] in ("EXTERNE", "ICEBERG") or t["externe_uri"]:
            loc = [t["externe_uri"]] if t["externe_uri"] else []
            what = "Iceberg table: data on an external volume" if t["type"] == "ICEBERG" else "External table: data outside the platform"
            out[t["id"]] = ("table", what, loc)
    for o in ctx.objets:
        k, deps = o["kind"], o["dependances_externes"]
        if k == "base_importee" and o["id"].upper() in profile(ctx.plat).get("imports_systeme", ()):
            continue
        exports = bool(o["detail"].get("ecriture_externe") or o["detail"].get("exporte"))
        if k == "base_importee":
            out[o["id"]] = ("objet", "Database imported from an inbound share", deps)
        elif k in ("connexion", "integration") or o["externe"] or (deps and not exports):  # an export destination belongs to F03
            what = {"stage": "External stage", "fonction": "External function: calls a service outside the platform",
                    "connexion": "Connection to an external service", "integration": "Integration with an external service"}
            out[o["id"]] = ("objet", what.get(k, f"{cap(KIND_LABELS.get(k, k))}: declared external dependency"), deps)
    return out


def _f01_f02(ctx: _Ctx, ext: dict) -> list[dict]:
    flags = []
    for i, (ctype, what, loc) in sorted(ext.items()):
        kind = next((o["kind"] for o in ctx.objets if o["id"] == i), None)
        base = ctx.reads.ids(*TABLE_SOURCES) if ctype == "table" else ctx.kind_ids(kind)
        where = f" ({', '.join(loc)})" if loc else ("" if kind == "base_importee" else " (location not read)")
        texte = f"{what}{where}."
        flags.append(_flag("F01", ctype, i, texte, base, {"emplacements": loc, "type": kind or "table"}))
    graph = {o["id"]: o["reference"] for o in ctx.objets}
    for o in ctx.objets:
        if o["id"] in ext or not o["reference"]:
            continue
        path = _shortest(o["id"], graph, ext)
        if not path:
            continue
        loc = ext[path[-1]][2]
        chemin = " → ".join(path)
        texte = (f"Depends on {path[-1]}, outside the platform, through {pl(len(path) - 1, 'level')}: {chemin}"
                 f" ({', '.join(loc) if loc else 'location not read'}).")
        base = ctx.kind_ids(o["kind"]) + ctx.reads.ids("OBJECT_DEPENDENCIES")
        flags.append(_flag("F02", "objet", o["id"], texte, base, {"chemin": path, "emplacements": loc}))
    return flags


def _shortest(start: str, graph: dict, targets: dict) -> list[str] | None:
    seen, frontier = {start}, [[start]]
    while frontier:
        nxt = []
        for path in frontier:
            for r in graph.get(path[-1], []):
                if r in seen:
                    continue
                if r in targets:
                    return path + [r]
                seen.add(r)
                nxt.append(path + [r])
        frontier = nxt
    return None


def _f03(ctx: _Ctx) -> list[dict]:
    flags = []
    for o in ctx.objets:
        if o["kind"] in SCHEDULED_KINDS + ("procedure",) and (o["detail"].get("ecriture_externe") or o["detail"].get("exporte")):
            loc = o["dependances_externes"]
            texte = (f"{cap(KIND_LABELS.get(o['kind'], o['kind']))} that writes outside the platform (declared in its definition); "
                     f"destination: {', '.join(loc) if loc else 'not read'}.")
            flags.append(_flag("F03", "objet", o["id"], texte, ctx.kind_ids(o["kind"]), {"mode": "declared", "emplacements": loc}))
    stage_locs = {o["id"].upper(): o["dependances_externes"] for o in ctx.objets if o["kind"] == "stage"}
    for f in ctx.familles:
        if not f["ecriture_externe"]:
            continue
        # replay may name the stage rather than the bucket: go back to the stage location when it is known
        loc = sorted({x for c in f["compartiments"] for x in (stage_locs.get(c.lstrip("@").upper()) or [c])})
        qui = f["outil"] or f["app"]
        texte = (f"Write outside the platform observed: {pl(f['n_exec'] or 0, 'execution')}"
                 + (f" ({qui})" if qui else "") + f"; destination: {', '.join(loc) if loc else 'not read'}.")
        flags.append(_flag("F03", "famille", (f["empreinte"] or "")[:12], texte, ctx.reads.ids("QUERY_HISTORY", "JOBS"),
                           {"mode": "observed", "emplacements": loc, "n_exec": f["n_exec"], "tables": f["tables"][:5]}))
    for o in ctx.objets:  # unloads to an external stage seen in ACCESS_HISTORY
        n = o["detail"].get("ecritures_observees_n")
        if o["kind"] == "stage" and o["externe"] and isinstance(n, int) and n > 0:
            loc = o["dependances_externes"]
            texte = f"Write outside the platform observed: {pl(n, 'write')} to this external stage ({', '.join(loc) if loc else 'location not read'})."
            flags.append(_flag("F03", "objet", o["id"], texte, ctx.kind_ids("stage") + ctx.reads.ids("ACCESS_HISTORY"),
                               {"mode": "observed", "emplacements": loc, "n_exec": n}))
    return flags


def _has_holder(roles: dict):
    """Holder = role granted to a user, directly or through a parent role. None = not determined."""
    memo: dict[str, bool | None] = {}

    def has(name: str, seen: frozenset = frozenset()) -> bool | None:
        if name in memo:
            return memo[name]
        r = roles.get(name)
        if r is None or name in seen:
            return None if r is None else False
        users = r.get("utilisateurs", r.get("utilisateurs_n"))
        if users is None and isinstance(r.get("titulaire"), bool):
            return r["titulaire"]
        if users:
            memo[name] = True
            return True
        res = False if users == 0 else None
        for parent in r.get("accorde_a", r.get("parents")) or []:
            h = has(parent, seen | {name})
            if h:
                res = True
                break
            if h is None:
                res = None
        memo[name] = res
        return res

    return has


def _owners(ctx: _Ctx) -> dict[str, list[tuple[str, dict]]]:
    out = defaultdict(list)
    for x in ctx.present + ctx.objets:
        if x.get("proprietaire"):
            out[x["proprietaire"]].append((x["id"], x))
    return out


def _f04(ctx: _Ctx) -> tuple[list[dict], list[str]]:
    """(flags, unresolved roles). Does not guess: without GRANTS_TO_USERS and GRANTS_TO_ROLES read, nothing is concluded."""
    roles_list = (ctx.snap.get("droits") or {}).get("roles")
    if not (roles_list is not None and all(ctx.reads.status(n) == READ for n in GRANT_SOURCES)):
        return [], []
    roles = {r.get("nom"): r for r in roles_list if r.get("nom")}
    has = _has_holder(roles)
    flags, unresolved = [], []
    for role, owned in sorted(_owners(ctx).items()):
        h = has(role)
        if h is None:
            unresolved.append(role)
            continue
        if h:
            continue
        ids = sorted(i for i, _ in owned)
        for _, x in owned:
            x["drapeaux"].append("F04")
        texte = (f"Role {role} owns {pl(len(ids), 'object')} and is granted to no user, "
                 f"neither directly nor through another role.")
        flags.append(_flag("F04", "role", role, texte, ctx.reads.ids(*GRANT_SOURCES), {"objets_n": len(ids), "exemples": ids[:5]}))
    return flags, unresolved


def _read_sources(ctx: _Ctx) -> list[dict]:
    """Sources of reads per table (connector profile), from the most to the least reliable; only those that were read."""
    r = ctx.reads
    fiche = profile(ctx.plat)
    out = []
    for name, label, conf, keys in fiche.get("sources_lectures", ()):
        if not r.ok(name):
            continue
        non_vus = [nv for nv in fiche["non_vus"][name] if r.status(fiche.get("non_vus_couverts", {}).get(nv, "")) != READ]
        du, au = r.window(name)
        partiel = r.partial(name)
        lat = [lec["latence_max_h"] for lec in r.find(name) if lec["statut"] == READ and lec["latence_max_h"] is not None]
        out.append({"vue": name, "label": label, "confiance": "low" if partiel or not du else legacy("confiance", conf), "non_vus": non_vus,
                    "ids": r.ids(name), "du": du, "au": au, "partiel": partiel, "keys": keys, "latence_h": max(lat, default=None)})
    return out


def _source_for(t: dict, sources: list[dict]) -> tuple[dict | None, int | None]:
    for s in sources:
        n = next((t["lectures"][k] for k in s["keys"] if t["lectures"][k] is not None), None)
        if n is not None:
            return s, n
    return None, None


def _too_recent(t: dict, src: dict, as_of: datetime) -> bool:
    """Table seen only in real time, or created more recently than the latency of the read source: its absence of
    reads is not observable yet."""
    if "recente" in t["flags"]:
        return True
    created = parse_dt(t["detail"].get("creee_le"))
    lat = src.get("latence_h")
    return bool(created and lat is not None and created > (src["au"] or as_of) - timedelta(hours=lat))


def _f05(ctx: _Ctx, sources: list[dict], shared: dict[str, int]) -> list[dict]:
    """No source read, or unknown value for the table: F05 stays silent (F12 says so). Table too recent: F05 stays silent too."""
    horizon = profile(ctx.plat).get("horizon_jours", 365)
    flags = []
    for t in ctx.present:
        src, n = _source_for(t, sources)
        octets = t["octets_actifs"]
        if src is None or t["type"] in ("EXTERNE", "TEMPORAIRE") or n != 0 or not octets or octets <= 0 or (t["lectures"]["partage"] or 0) > 0:
            continue
        if _too_recent(t, src, ctx.as_of):
            continue
        if src["du"] and src["au"]:
            jours = (src["au"] - src["du"]).days
            fen = f"from {fmt_date(src['du'])} to {fmt_date(src['au'])}"
        else:
            jours, fen = None, "(window not provided)"
        propre = f"{fmt_bytes(octets)} stored" if not t.get("clone_de") else f"Clone or snapshot of {t['clone_de']} (only its own bytes are billed)"
        texte = (f"{propre}. No read observed in {src['label']} {fen}; "
                 f"blind spots: {', '.join(src['non_vus'])}.")
        if jours is not None and jours < horizon - 1:
            texte += f" {jours}-day window, shorter than {horizon}: a quarterly or yearly job may not appear in it."
        if src["partiel"]:
            texte += " Some read windows were not read."
        ddl = parse_dt(t["derniere_ddl"])
        if ddl and src["du"] and ddl > src["du"]:
            texte += f" Last DDL on {fmt_date(ddl)}."
        if shared.get(t["id"]):
            texte += f" Shared with {pl(shared[t['id']], 'external consumer')}: their reads are not seen."
        preuve = {"octets_actifs": octets, "source": src["label"], "du": src["du"].strftime("%Y-%m-%d") if src["du"] else None,
                  "au": src["au"].strftime("%Y-%m-%d") if src["au"] else None, "jours": jours, "non_vus": src["non_vus"]}
        flags.append(_flag("F05", "table", t["id"], texte, src["ids"] + ctx.reads.ids(*STORAGE_SOURCES), preuve, src["confiance"]))
    return flags


def _f06_f07(ctx: _Ctx) -> list[dict]:
    base = ctx.reads.ids(*STORAGE_SOURCES)
    flags = []
    for t in ctx.tables:
        h, a = t["octets_historique"], t["octets_actifs"]
        if "recente" in t["flags"]:  # billed history not yet in ACCOUNT_USAGE: no conclusion
            continue
        if t["supprimee_le"]:
            if h is None and a is None:
                continue
            total = (h or 0) + (a or 0)
            if total > 0:
                texte = f"Dropped on {fmt_date(t['supprimee_le'])}; {fmt_bytes(total)} still billed (Time Travel and Fail-safe)."
                flags.append(_flag("F07", "table", t["id"], texte, base + ctx.reads.ids("TABLES"),
                                   {"supprimee_le": t["supprimee_le"], "octets": total}))
        elif h and h > 0:
            texte = (f"{fmt_bytes(h)} of billed history (Time Travel and Fail-safe)"
                     + (f", retention {fmt_num(t['retention_jours'])} days" if t["retention_jours"] is not None else "")
                     + (f", for {fmt_bytes(a)} active" if a is not None else "") + "; this history does not leave with the copy.")
            flags.append(_flag("F06", "table", t["id"], texte, base,
                               {"octets_historique": h, "octets_actifs": a, "retention_jours": t["retention_jours"]}))
    return flags


F08_DBT = (": SQL produced by Jinja and macros at compile time; the replayed SQL depends on the target and the variables, "
           "only the compiled SQL that was read is analyzed.")


def _f08(ctx: _Ctx) -> list[dict]:
    return [_flag("F08", "objet", o["id"],
                  f"{cap(KIND_LABELS.get(o['kind'], o['kind']))}"
                  + (F08_DBT if o["kind"] in DBT_KINDS else (f" ({o['langage']})" if o["langage"] else "")
                     + ": builds SQL at run time; the objects it touches cannot be read from the code."),
                  ctx.kind_ids(o["kind"]), {"langage": o["langage"]})
            for o in ctx.objets if o["sql_dynamique"] is True]


CONSUMER_KEYS = (("comptes_cibles", "target account", "target accounts"), ("comptes_cibles_n", "target account", "target accounts"),
                 ("projets_consommateurs_n", "consumer project", "consumer projects"), ("abonnes", "subscriber", "subscribers"),
                 ("consommateurs", "consumer", "consumers"))


def _consumers(o: dict) -> tuple[int | None, str]:
    d = o["detail"]
    for k, one, many in CONSUMER_KEYS:
        if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool):
            return int(d[k]), pl(int(d[k]), one, many)
    return None, "external consumers (number not read)"


def _f09(ctx: _Ctx) -> tuple[list[dict], dict[str, int]]:
    flags, shared = [], {}
    for o in ctx.objets:
        if o["kind"] != "partage":
            continue
        n, qui = _consumers(o)
        if not (n and n > 0) and not (n is None and o["externe"]):
            continue
        for r in o["reference"]:
            shared[r] = max(shared.get(r, 0), n or 1)
        quoi = (pl(len(o["reference"]), "shared object") if o["reference"]
                else f"database {o['detail']['base']}" if o["detail"].get("base") else "shared objects not read")
        texte = f"Share to {qui}; {quoi}. Consumer reads are not seen by this scan."
        flags.append(_flag("F09", "objet", o["id"], texte, ctx.kind_ids("partage"), {"consommateurs": n, "objets": o["reference"][:10]}))
    return flags, shared


def _f10(ctx: _Ctx) -> list[dict]:
    base = ctx.reads.ids(*POLICY_SOURCES)
    flags = []
    for t in ctx.present:
        if not (t["politiques"] or t["etiquettes_n"]):
            continue
        parts = []
        if t["politiques"]:
            parts.append(f"policies: {', '.join(t['politiques'])}")
        if t["etiquettes_n"]:
            parts.append(pl(t["etiquettes_n"], "tag"))
        texte = (f"{cap('; '.join(parts))}. They do not leave with the copy; "
                 f"the copy requires a principal not filtered by these policies.")
        flags.append(_flag("F10", "table", t["id"], texte, base, {"politiques": t["politiques"], "etiquettes_n": t["etiquettes_n"]}))
    for o in ctx.objets:
        if o["kind"] not in POLICY_KINDS:
            continue
        what = cap(KIND_LABELS[o["kind"]])
        texte = f"{what} applied to {pl(len(o['reference']), 'object')}; it does not leave with the copy"
        texte += "." if o["kind"] == "etiquette" else "; the copy requires an unfiltered principal."
        flags.append(_flag("F10", "objet", o["id"], texte, ctx.kind_ids(o["kind"]), {"objets": o["reference"][:10]}))
    return flags


def _f11(ctx: _Ctx) -> list[dict]:
    sched = {o["id"]: o for o in ctx.objets if o["kind"] in SCHEDULED_KINDS}
    by_upper = {i.replace('"', "").upper(): i for i in sched}

    def preds(o) -> list[str]:
        p = set(r for r in o["reference"] if r in sched and r != o["id"])
        apres = o["detail"].get("apres") or o["detail"].get("predecesseurs")
        for x in (apres.split(",") if isinstance(apres, str) else apres or []):
            i = by_upper.get(str(x).strip().replace('"', "").upper())
            if i and i != o["id"]:
                p.add(i)
        return sorted(p)

    def chain(o) -> list[str]:
        path = [o["id"]]
        while len(path) < 20:
            p = [x for x in preds(sched[path[0]]) if x not in path]
            if not p:
                break
            path.insert(0, p[0])
        return path

    flags = []
    for i, o in sorted(sched.items()):
        head = [cap(KIND_LABELS.get(o["kind"], o["kind"]))]
        if o["planification"]:
            head.append(f"schedule {o['planification']}")
        if o["etat"]:
            head.append(f"state {o['etat']}")
        if o["kind"] == "flux" and o["executions_n"] is None:
            runs = f"on {', '.join(o['reference'])}" if o["reference"] else "tracked object not read"
        elif o["executions_n"] is None:
            runs = "runs not read"
        else:
            runs = pl(o["executions_n"], "run") + ", " + (pl(o["echecs_n"], "failure") if o["echecs_n"] is not None else "failures not read")
        if o["derniere_execution"]:
            runs += f", last on {fmt_date(o['derniere_execution'])}"
        ch = chain(o)
        texte = ", ".join(head) + f"; {runs}" + (f"; chain: {' → '.join(ch)}" if len(ch) > 1 else "") + "."
        flags.append(_flag("F11", "objet", i, texte, ctx.kind_ids(o["kind"]),
                           {"chaine": ch, "executions_n": o["executions_n"], "echecs_n": o["echecs_n"], "etat": o["etat"]}))
    return flags


def _label(o: dict) -> str:
    return cap(KIND_LABELS.get(o["kind"], str(o["kind"])))


def _f13(ctx: _Ctx) -> list[dict]:
    """Processing whose engine does not leave the platform: visual or plugin recipe read, or processing declared as to rebuild."""
    flags = []
    for o in ctx.objets:
        port = o.get("portabilite", "absente")
        if not ((o["kind"] in SANS_MOTEUR_KINDS and port in ("absente", "a_reconstruire"))
                or (o.get("famille_objet") == "traitement" and port == "a_reconstruire")):
            continue
        io = []
        if "entrees" in o:
            io.append(pl(len(o["entrees"]), "input"))
        if "sorties" in o:
            io.append(pl(len(o["sorties"]), "output"))
        raison = o["detail"].get("raison_portabilite")
        texte = (f"{_label(o)} with no executable equivalent off the platform (type read: {o['kind']}"
                 + (f", engine {o['moteur']}" if o.get("moteur") else "") + ")"
                 + (f"; {raison}" if raison else "")
                 + (f"; {', '.join(io)}" if io else "") + "; to rebuild: counted, not replayed.")
        flags.append(_flag("F13", "objet", o["id"], texte, ctx.kind_ids(o["kind"]),
                           {"kind": o["kind"], "portabilite": o.get("portabilite"), "moteur": o.get("moteur"),
                            "entrees": o.get("entrees", [])[:10], "sorties": o.get("sorties", [])[:10]}))
    return flags


def _f14(ctx: _Ctx) -> list[dict]:
    """Proprietary extension: inventoried plugin whose code is not read or is licensed; plugin recipe with no inventoried plugin."""
    plugins = {o["id"]: o for o in ctx.objets if o["kind"] in EXTENSION_KINDS or o.get("famille_objet") == "extension"}
    usage_lu = any("extensions" in o for o in ctx.objets)
    flags = []
    for i, o in sorted(plugins.items()):
        licence = o["detail"].get("licence")
        code_non_lu = o.get("empreinte_code") is None
        if not (code_non_lu or licence):
            continue
        users = sorted(x["id"] for x in ctx.objets if i in x.get("extensions", []))
        etat = []
        if o["detail"].get("version"):
            etat.append(f"version {o['detail']['version']}")
        if code_non_lu:
            etat.append("code not read")
        if licence:
            etat.append(f"license {licence}")
        usage = ((f"used by {pl(len(users), 'object')}: {', '.join(users[:5])}" + (" …" if len(users) > 5 else "")) if users
                 else "no use observed in the objects read") if usage_lu else "use per object not read"
        texte = f"{_label(o)} ({', '.join(etat)}) ; {usage}."
        flags.append(_flag("F14", "objet", i, texte, ctx.kind_ids(o["kind"]),
                           {"version": o["detail"].get("version"), "code_lu": not code_non_lu, "licence": licence, "utilise_par": users[:10]}))
    for o in ctx.objets:
        if o["kind"] == "recette_plugin" and not any(e in plugins for e in o.get("extensions", [])):
            ext = o.get("extensions", [])
            texte = (f"{_label(o)}: extension used but not inventoried"
                     + (f" ({', '.join(ext[:5])})" if ext else " (plugin not named by collection)") + "; extension code not read.")
            flags.append(_flag("F14", "objet", o["id"], texte, ctx.kind_ids(o["kind"]), {"extensions": ext[:10], "code_lu": False}))
    return flags


def _f15(ctx: _Ctx) -> list[dict]:
    """Trained model with no declared open format; formats not read (license, privileges) = "not determined", never "not exportable"."""
    flags = []
    for o in ctx.objets:
        if not (o["kind"] in MODELE_KINDS or o.get("famille_objet") == "modele"):
            continue
        fx = o.get("formats_export")
        algo = o["detail"].get("algorithme")
        head = _label(o) + (f" ({algo})" if algo else "")
        if fx is None:
            texte = f"{head}: export formats not read (license or privileges); open export not determined."
        elif any(f in FORMATS_OUVERTS for f in fx):
            continue
        else:
            texte = (f"{head}: no open format declared among MLflow, ONNX, PMML, Python"
                     + (f" (formats read: {', '.join(fx)})" if fx else " (no format read)") + ".")
        flags.append(_flag("F15", "objet", o["id"], texte, ctx.kind_ids(o["kind"]),
                           {"formats_export": fx, "determine": fx is not None}))
    return flags


def _f16(ctx: _Ctx) -> list[dict]:
    """Non-trivial measure or calculation in the presentation layer: criterion declared by the connector, otherwise line count."""
    flags = []
    for o in ctx.objets:
        if not (o["kind"] in PRESENTATION_KINDS or o.get("famille_objet") == "rapport"):
            continue
        nt, lignes = o.get("non_triviale"), o.get("lignes_code")
        if nt is True:
            pourquoi = "non-trivial according to the connector"
        elif nt is None and isinstance(lignes, int) and lignes >= F16_LIGNES:
            pourquoi = f"{pl(lignes, 'line')} of calculation"
        else:
            continue
        texte = (f"{_label(o)} in the presentation layer, {pourquoi}" + (f"; language {o['langage']}" if o.get("langage") else "")
                 + "; recalculation outside the tool not compared.")
        flags.append(_flag("F16", "objet", o["id"], texte, ctx.kind_ids(o["kind"]),
                           {"non_triviale": nt, "lignes_code": lignes, "langage": o.get("langage")}))
    return flags


def _f12(ctx: _Ctx, edition_missing: bool, unresolved_owners: list[str], sources: list[dict]) -> list[dict]:
    r, flags = ctx.reads, []
    for lec in r.all:
        key = _impact_key(lec["vue"])
        if lec["statut"] == READ:
            if (lec["raison"] or "").startswith("partial"):
                impact = IMPACTS.get(key, "the conclusions that depend on it")
                lec["limite"] = impact
                flags.append(_flag("F12", "lecture", lec["id"], f"{short_vue(lec['vue'])} partly read ({lec['raison']}); limits: {impact}.",
                                   [lec["id"]], {"vue": lec["vue"], "raison": lec["raison"]}))
            continue
        if key in FALLBACKS and r.ok(*FALLBACKS[key]):
            lec["limite"] = f"none: {FALLBACKS[key][0]} read instead"
            continue
        if key == "ACCESS_HISTORY":
            impact = ("reads per object: F05 computed with TABLE_QUERY_PRUNING_HISTORY, medium confidence"
                      if r.ok("TABLE_QUERY_PRUNING_HISTORY") else "reads per object: F05 not computed")
            if edition_missing:
                impact += "; edition not determined (Standard or missing privilege)"
        else:
            impact = IMPACTS.get(key, "the conclusions that depend on it")
        lec["limite"] = impact
        texte = f"{short_vue(lec['vue'])} not read ({lec['raison'] or 'reason not provided'}); limits: {impact}."
        flags.append(_flag("F12", "lecture", lec["id"], texte, [], {"vue": lec["vue"], "raison": lec["raison"]}))
    if ctx.plat == "snowflake" and not r.find(*GRANT_SOURCES):
        flags.append(_flag("F12", "lecture", "droits",
                           f"Privileges not read (level {ctx.snap.get('niveau_droits')}); limits: owner roles with no holder (F04).", []))
    elif r.find(*GRANT_SOURCES) and all(r.status(n) == READ for n in GRANT_SOURCES) and (ctx.snap.get("droits") or {}).get("roles") is None:
        flags.append(_flag("F12", "lecture", "droits", "Privileges read but not passed to the analysis; limits: owner roles with no holder (F04).", []))
    if unresolved_owners:
        flags.append(_flag("F12", "lecture", "roles",
                           f"{pl(len(unresolved_owners), 'owner role')} with no grant read; "
                           f"limits: owner roles with no holder (F04).", r.ids(*GRANT_SOURCES), {"roles": unresolved_owners[:10]}))
    if sources:
        unknown = [t["id"] for t in ctx.present if _source_for(t, sources)[0] is None and t["type"] not in ("EXTERNE", "TEMPORAIRE")]
        if unknown:
            flags.append(_flag("F12", "lecture", "lectures_par_table",
                               f"Reads not determined for {pl(len(unknown), 'table')}; limits: storage with no observed read (F05).",
                               [i for s in sources for i in s["ids"]], {"tables": unknown[:10]}))
    # an external function has no SQL body: its empty sql_dynamique is not unread code
    hidden = [o["id"] for o in ctx.objets if o["kind"] in ("procedure", "fonction") and o["sql_dynamique"] is None
              and not (o["kind"] == "fonction" and o["externe"])]
    if hidden:
        flags.append(_flag("F12", "lecture", "code_des_routines",
                           f"Code not read for {pl(len(hidden), 'routine')} (filtered by privileges or not provided); limits: dynamic SQL (F08).",
                           ctx.reads.ids("PROCEDURES", "FUNCTIONS", "ROUTINES"), {"routines": hidden[:10]}))
    return flags


# --- figures ------------------------------------------------------------------------------------------------


class _NonEstime:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, sujet: str, raison: str) -> None:
        item = {"sujet": sujet, "raison": raison}
        if item not in self.items:
            self.items.append(item)

    def track(self, sujet: str, f: dict) -> dict:
        if f["nature"] in (NOT_READ, NOT_ESTIMATED):
            self.add(sujet, f["raison"])
        return f


def _measured(value, reads: _Reads, names: tuple, unite: str, conf=None) -> dict:
    if value is None or not reads.ok(*names):
        return fig(nature=NOT_READ, unite=unite, raison=reads.why(*names) if not reads.ok(*names) else "not provided by collection")
    return fig(value, nature=MEASURED, unite=unite, base=reads.ids(*names),
               confiance=conf or ("medium" if reads.status(*names) == PARTIAL else None))


def _usd_from_credits(credits: dict, credit_price: float | None, base: list[str]) -> dict:
    c = credits["valeur"] if credits["nature"] != ESTIMATED else None
    lo, hi = (credits["bas"], credits["haut"]) if credits["nature"] == ESTIMATED else (c, c)
    if lo is None:
        return fig(nature=NOT_ESTIMATED, unite="USD", raison="credits not read")
    if credit_price:
        if credits["nature"] == ESTIMATED:
            return fig(nature=ESTIMATED, unite="USD", bas=round(lo * credit_price, 2), haut=round(hi * credit_price, 2), base=base,
                       formule="credits × credit price provided", hypotheses=[f"credit price provided: ${credit_price}"])
        return fig(round(c * credit_price, 2), nature=COMPUTED, unite="USD", base=base, formule="credits × credit price provided",
                   hypotheses=[f"credit price provided: ${credit_price}"])
    pmin, pmax = min(prices.SNOWFLAKE_CREDIT_USD.values()), max(prices.SNOWFLAKE_CREDIT_USD.values())
    return fig(nature=ESTIMATED, unite="USD", bas=round(lo * pmin, 2), haut=round(hi * pmax, 2), base=base, confiance="low",
               formule="credits × list price of the credit, from lowest to highest",
               hypotheses=[f"Snowflake list price as of {prices.AS_OF}, AWS Paris/Frankfurt: ${fmt_num(pmin, 2)} (Standard edition) to ${fmt_num(pmax, 2)} "
                           f"(highest edition), {prices.SNOWFLAKE_SOURCE}", f"{CONTRAT_NON_LU}; your region may differ"])


def _months_touched(du: datetime, au: datetime) -> int:
    return (au.year - du.year) * 12 + au.month - du.month + 1


COUTS_FICHIERS = "project files: no credits and no billed bytes; the scan of the target platform measures them"


def _couts(ctx: _Ctx, credit_price: float | None, ne: _NonEstime, row_policies: bool) -> dict:
    r, m = ctx.reads, ctx.snap.get("metering") or {}
    if ctx.famille == "fichiers":
        ne.add("Platform consumption", f"not read: {COUTS_FICHIERS}")
        return {"fenetre_jours": None, "credits": fig(nature=NOT_READ, unite="credits", raison=COUTS_FICHIERS),
                "credits_par_service": [], "credits_par_entrepot": [], "transferts": [],
                "monnaie": fig(nature=NOT_ESTIMATED, unite="USD", raison="consumption not read"),
                "stockage_octets_moyens": fig(nature=NOT_READ, unite="bytes", raison=COUTS_FICHIERS),
                "stockage_monnaie": fig(nature=NOT_ESTIMATED, unite="USD", raison=CONTRAT_NON_LU),
                "note": "No consumption in project files."}
    out: dict = {"fenetre_jours": m.get("fenetre_jours")}
    stock = _measured(m.get("stockage_octets_moyens"), r, ("STORAGE_USAGE", "TABLE_STORAGE", "tables.get"), "bytes")
    out["stockage_octets_moyens"] = ne.track("Average billed storage", stock)
    out["stockage_monnaie"] = ne.track("Storage cost", fig(nature=NOT_ESTIMATED, unite="USD", raison=CONTRAT_NON_LU))
    tr_names = ("DATA_TRANSFER_HISTORY",)
    out["transferts"] = [{"cible": scrub(x.get("cible")), "octets": _measured(x.get("octets"), r, tr_names, "bytes")}
                         for x in sorted(m.get("transferts") or [], key=lambda x: str(x.get("cible")))] if r.ok(*tr_names) else []
    if ctx.plat == "bigquery":
        out.update(_couts_bigquery(ctx, m, ne, row_policies))
        return out
    names = ("METERING_DAILY_HISTORY",)
    services = m.get("par_service")
    if r.ok(*names) and services is not None:
        par = [{"service": scrub(s.get("service")), "credits": _measured(s.get("credits"), r, names, "credits")}
               for s in sorted(services, key=lambda s: (-(s.get("credits") or 0), str(s.get("service"))))]
        known = [p["credits"]["valeur"] for p in par if p["credits"]["valeur"] is not None]
        total = fig(round(sum(known), 4), nature=COMPUTED, unite="credits", base=r.ids(*names),
                    formule=f"sum of credits per service over {m.get('fenetre_jours') or '?'} days",
                    confiance="medium" if r.status(*names) == PARTIAL or len(known) < len(par) else None)
    else:
        par = []
        total = fig(nature=NOT_READ, unite="credits", raison=r.why(*names) if not r.ok(*names) else "not provided by collection")
    out["credits"] = ne.track("Credits consumed", total)
    out["credits_par_service"] = par
    wnames = ("WAREHOUSE_METERING_HISTORY",)
    out["credits_par_entrepot"] = [{"entrepot": scrub(w.get("entrepot")), "credits": _measured(w.get("credits"), r, wnames, "credits")}
                                   for w in sorted(m.get("par_entrepot") or [], key=lambda w: (-(w.get("credits") or 0), str(w.get("entrepot"))))
                                   ] if r.ok(*wnames) else []
    out["monnaie"] = ne.track("Credits in dollars", _usd_from_credits(total, credit_price, r.ids(*names)))
    out["note"] = "Credits measured; in dollars, only with the price provided or a dated list-price range."
    return out


def _couts_bigquery(ctx: _Ctx, m: dict, ne: _NonEstime, row_policies: bool) -> dict:
    r, names = ctx.reads, ("JOBS",)
    od, rs = m.get("a_la_demande"), m.get("reserve")
    base = r.ids(*names)
    octets = _measured((od or {}).get("octets_factures"), r, names, "bytes")
    if octets["valeur"] is not None:
        du, au = r.window(*names)
        jours = m.get("fenetre_jours")
        if not (du and au) and jours:
            au = ctx.as_of
            du = datetime.fromtimestamp(au.timestamp() - jours * 86400, tz=timezone.utc)
        months = _months_touched(du, au) if du and au else None
        tib, price = octets["valeur"] / TIB, prices.BIGQUERY_ON_DEMAND_USD_PER_TIB
        hyp = [f"on-demand list price as of {prices.AS_OF}: ${fmt_num(price, 2)} per TiB (US rate; region to confirm), {prices.BIGQUERY_SOURCE}",
               "high: no free tier deducted; low: 1 free TiB per month touched by the window; "
               "the free tier is shared by the billing account, it cannot be attributed per project", CONTRAT_NON_LU]
        if row_policies:
            hyp.append("billed bytes NULL for queries on tables with a row access policy: not counted")
        hi = round(tib * price, 2)
        lo = round(max(0.0, tib - months) * price, 2) if months else hi
        usd_od = fig(nature=ESTIMATED, unite="USD", bas=lo, haut=hi, base=base, confiance="low",
                     formule="billed TiB × list price, free tier deducted or not", hypotheses=hyp)
    else:
        usd_od = fig(nature=NOT_ESTIMATED, unite="USD", raison="billed bytes not read")
    slots = _measured((rs or {}).get("slot_heures"), r, names, "slot-hours")
    return {
        "a_la_demande": {"octets_factures": ne.track("On-demand billed bytes", octets),
                         "requetes": _measured((od or {}).get("requetes"), r, names, "queries"),
                         "monnaie": ne.track("On-demand, in dollars", usd_od)},
        "reserve": {"slot_heures": ne.track("Reserved slot-hours", slots),
                    "requetes": _measured((rs or {}).get("requetes"), r, names, "queries"),
                    "monnaie": ne.track("Reserved, in dollars", fig(nature=NOT_ESTIMATED, unite="USD", raison=CONTRAT_NON_LU))},
        "note": "On-demand and reserved: two billing models, never added together.",
    }


def _fourchettes(ctx: _Ctx, credit_price: float | None, ne: _NonEstime) -> dict:
    r = ctx.reads
    base = r.ids(*STORAGE_SOURCES)
    movable = [t for t in ctx.present if t["type"] not in ("EXTERNE", "TEMPORAIRE")]
    known = [t["octets_actifs"] for t in movable if t["octets_actifs"] is not None]
    missing = len(movable) - len(known)
    if movable and not known:
        if ctx.famille == "fichiers":
            raison = "volumes missing from catalog.json" if r.ok("catalog.json") else "catalog.json not read"
        else:
            raison = r.why(*STORAGE_SOURCES) if not r.ok(*STORAGE_SOURCES) else "volumes not provided"
        volume = fig(nature=NOT_READ, unite="bytes", raison=raison)
    else:
        hyp = ["active bytes of present tables, excluding external and temporary tables"]
        if ctx.plat == "snowflake":
            hyp.append("compressed stored volume; the volume read by the copy may be higher")
        if any(t["type"] == "CLONE" for t in movable):
            hyp.append("clones: only their own bytes are counted")
        if missing:
            hyp.append(f"{pl(missing, 'table')} with no volume read, not counted: minimum value")
        volume = fig(sum(known), nature=COMPUTED, unite="bytes", base=base, formule="sum of active bytes", hypotheses=hyp,
                     confiance="medium" if missing else None)
    out = {"volume": ne.track("Volume to copy", volume)}
    v = volume["valeur"]
    debit = f"assumed throughput of {fmt_num(THROUGHPUT_MBPS[0])} to {fmt_num(THROUGHPUT_MBPS[1])} MB/s, replaced by the measurement of a canot drill"
    if v is None:
        out["duree_lecture"] = ne.track("Copy read time", fig(nature=NOT_ESTIMATED, unite="hours", raison="volume not read"))
        hours = None
    else:
        hours = (v / 1e6 / THROUGHPUT_MBPS[1] / 3600, v / 1e6 / THROUGHPUT_MBPS[0] / 3600)
        out["duree_lecture"] = fig(nature=ESTIMATED, unite="hours", bas=round(hours[0], 2), haut=round(hours[1], 2), base=base,
                                   formule="volume ÷ assumed throughput", hypotheses=[debit], confiance="low")
    if ctx.plat == "snowflake":
        if hours is None:
            cr = fig(nature=NOT_ESTIMATED, unite="credits", raison="volume not read")
        else:
            cr = fig(nature=ESTIMATED, unite="credits", bas=round(hours[0] * CREDITS_PAR_HEURE, 2), haut=round(hours[1] * CREDITS_PAR_HEURE, 2),
                     base=base, formule="time × credits per hour", hypotheses=[debit, "XS warehouse, 1 credit per hour"], confiance="low")
        out["credits_lecture"] = ne.track("Copy read credits", cr)
        out["cout_lecture"] = ne.track("Copy read cost", _usd_from_credits(cr, credit_price, base))
    elif ctx.plat != "bigquery":
        out["cout_lecture"] = ne.track("Copy read cost", fig(nature=NOT_ESTIMATED, unite="USD",
                                                           raison="depends on the platform that hosts the tables, not read here"))
    else:
        out["cout_lecture"] = ne.track("Copy read cost (Storage Read API, network egress)",
                                       fig(nature=NOT_ESTIMATED, unite="USD", raison=CONTRAT_NON_LU,
                                           hypotheses=["read through the Storage Read API, billed per TiB read, and network egress out of Google Cloud"]))
    n_obj = sum(o["kind"] in REBUILD_KINDS or o.get("portabilite") == "a_reconstruire" for o in ctx.objets)
    n_cols = sum(len(t["colonnes_non_portables"]) for t in ctx.present)
    lo, hi = prices.rework_days(n_obj, n_cols)
    hyp = [f"{pl(n_obj, 'object')} to rebuild and {pl(n_cols, 'column')} with a platform-specific type",
           f"q4o assumptions: {fmt_num(prices.REWORK_DAYS_PER_OBJECT[0], 2)} to {fmt_num(prices.REWORK_DAYS_PER_OBJECT[1], 1)} day per object, "
           f"{fmt_num(prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[0], 1)} to {fmt_num(prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[1], 1)} per column, open to discussion"]
    unread = [short_vue(lec["vue"]) for lec in r.find(*CODE_SOURCES) if lec["statut"] != READ]
    if unread:
        hyp.append(f"objects from reads not done not counted: {', '.join(sorted(set(unread)))}")
    out["reprise"] = fig(nature=ESTIMATED, unite="days", bas=lo, haut=hi, base=r.ids(*CODE_SOURCES), formule="objects × days per object + columns × days per column",
                         hypotheses=hyp, confiance="low")
    return out


def _rejouabilite(ctx: _Ctx, ne: _NonEstime) -> dict:
    names = ("QUERY_HISTORY", "JOBS")
    rj = ctx.snap.get("rejouabilite")
    if not rj:
        raison = ctx.reads.why(*names) if not ctx.reads.ok(*names) else "not provided by collection"
        ne.add("Query replayability", raison)
        return {"statut": NOT_READ, "raison": raison, "categories": {}, "base": None, "mention": MENTION_REJOUABILITE, "taux": {}}
    cats = {k: {"familles": v.get("familles"), "executions": v.get("executions"), "poids": v.get("poids")}
            for k, v in sorted((rj.get("categories") or {}).items())}
    given = rj.get("taux") or {}
    given = {k: ({**v, "nature": legacy("nature", v.get("nature"))} if isinstance(v, dict) else v) for k, v in given.items()}
    if given and all(isinstance(v, dict) and v.get("nature") in NATURES for v in given.values()):
        taux = {k: dict(v) for k, v in sorted(given.items())}  # computed by replay on its own bases: taken as is
    else:
        tested = [k for k in cats if k != "C7"]
        taux = {}
        for key, unite in (("familles", "families"), ("executions", "executions"), ("poids", "credits" if ctx.plat == "snowflake" else "bytes")):
            vals = [cats[k][key] for k in tested]
            den = sum(v for v in vals if v is not None)
            num = (cats.get("C0") or {}).get(key)
            if None in vals or num is None or not den:
                taux[key] = fig(nature=NOT_READ, unite="%", raison=f"{unite} not provided per category")
            else:
                taux[key] = fig(round(100 * num / den, 1), nature=COMPUTED, unite="%", base=ctx.reads.ids(*names),
                                formule=f"C0 ÷ C0 to C6, in {unite}; C7 counted separately")
    return {"statut": READ, "raison": None, "categories": cats, "base": scrub(rj.get("base")), "mention": MENTION_REJOUABILITE,
            "taux": taux, "bases": {k: scrub(v) for k, v in sorted((rj.get("bases") or {}).items())},
            "non_testees": rj.get("non_testees"), "secrets_ecartes": rj.get("secrets_ecartes"),
            "fonctions": {scrub(str(k)): v for k, v in sorted((rj.get("fonctions") or {}).items())}}


def _cout_du_scan(ctx: _Ctx) -> dict:
    c = ctx.snap.get("cout_du_scan") or {}
    lecs = ctx.reads.all
    tried = [lec for lec in lecs if lec["statut"] == READ or _attempted(lec["raison"])]
    if c.get("requetes") is not None:
        req = fig(c["requetes"], nature=MEASURED, unite="queries")
    else:
        req = fig(len(tried), nature=COMPUTED, unite="queries", base=sorted(lec["id"] for lec in tried),
                  formule="reads attempted, read or refused by the platform", hypotheses=["one read = one query"])
    duree = (fig(c["duree_s"], nature=MEASURED, unite="seconds") if c.get("duree_s") is not None
             else fig(nature=NOT_READ, unite="seconds", raison="duration not provided by collection"))
    out = {"requetes": req, "duree": duree,
           "lectures": {"total": len(lecs), "lu": sum(lec["statut"] == READ for lec in lecs), "non_lu": sum(lec["statut"] != READ for lec in lecs)}}
    if c.get("appels_api") is not None:
        out["appels_api"] = fig(c["appels_api"], nature=MEASURED, unite="calls")
    if c.get("octets_factures") is not None:
        out["octets_factures"] = fig(c["octets_factures"], nature=MEASURED, unite="bytes")
    if c.get("credits") is not None:
        out["credits"] = fig(c["credits"], nature=MEASURED, unite="credits")
    return out


# reasons of a read the platform refused (model.classify_failure, bigquery guard): the query was sent
_TRIED = ("missing privilege", "edition required", "absent or not exposed", "service disabled", "timed out", "failed (", "estimated volume")


def _attempted(raison: str | None) -> bool:
    return bool(raison) and raison.startswith(_TRIED)


# --- summary ------------------------------------------------------------------------------------------------


def _fraicheur(ctx: _Ctx) -> dict | None:
    """Real-time cross-check by collection (Snowflake): objects more recent than the ACCOUNT_USAGE latency. None if not provided."""
    f = ctx.snap.get("fraicheur")
    if not isinstance(f, dict):
        return None
    rec = f.get("recents_seulement_temps_reel") or {}
    n_t = sum("recente" in t["flags"] for t in ctx.present)
    lat = [lec["latence_max_h"] for lec in ctx.reads.find("TABLES", *STORAGE_SOURCES, "ACCESS_HISTORY")
           if lec["statut"] == READ and lec["latence_max_h"]]
    return {"recents_seulement_temps_reel": {"tables": rec.get("tables") if isinstance(rec.get("tables"), int) else n_t,
                                             "objets": rec.get("objets") if isinstance(rec.get("objets"), int) else None},
            "volumes_en_retard": f.get("volumes_en_retard") if isinstance(f.get("volumes_en_retard"), int) else None,
            "latence_max_h": f.get("latence_max_h"), "texte": scrub(f.get("texte")),
            "tables_sans_historique": n_t, "latence_historique_h": max(lat, default=None)}


def _fraicheur_phrase(fr: dict | None) -> str | None:
    """Sentences of the CIO summary: tables seen only in real time, volumes not yet computed by TABLE_STORAGE_METRICS."""
    if not fr:
        return None
    out = []
    n, h, v = fr["tables_sans_historique"], fr["latence_historique_h"], fr.get("volumes_en_retard") or 0
    lat = f", whose latency can reach {fmt_num(h, 1).removesuffix('.0')} h" if h else ""
    if n == 1:
        out.append(f"1 table seen only in real time: missing from ACCOUNT_USAGE{lat}; "
                   "its history (billed volumes, reads) is not available yet.")
    elif n > 1:
        out.append(f"{fmt_num(n)} tables seen only in real time: missing from ACCOUNT_USAGE{lat}; "
                   "their history (billed volumes, reads) is not available yet.")
    if v:
        out.append(f"Volume underestimated: storage metrics not up to date yet for {pl(v, 'table')}.")
    return " ".join(out) or None


def _resume_fichiers(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    """Summary of a project read as files (dbt): processing, portability, what is not read here."""
    kinds = Counter(o["kind"] for o in ctx.objets if o.get("famille_objet") in ("traitement", "donnees"))
    def lab(k: str) -> tuple[str, str]:
        x = KIND_LABELS.get(k, k)
        return x, KIND_PLURALS.get(k, x + "s")

    quoi = ", ".join(pl(n, *lab(k)) for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
    port = Counter(o["portabilite"] or "non_determine" for o in ctx.objets if "portabilite" in o)
    mats = Counter(o["detail"].get("materialisation") for o in ctx.objets if o["kind"] == "modele_dbt")
    dsi = [f"{quoi or 'no object'}; {pl(len(ctx.present), 'stored relation')}; {pl(len(res['liens']), 'dependency link')}."
           + (f" Materializations: {', '.join(f'{fmt_num(n)} {m}' for m, n in sorted(mats.items(), key=lambda kv: str(kv[0])))}." if mats else ""),
           f"Portability: {fmt_num(port['traduisible'])} translatable, {fmt_num(port['a_reconstruire'])} to rebuild (F13), "
           f"{fmt_num(port['non_determine'])} not determined, {fmt_num(port['portable_tel_quel'])} portable as is."]
    rj = res["rejouabilite"]
    cats = rj.get("categories") or {}
    tested = sum((v["familles"] or 0) for k, v in cats.items() if k not in ("C7",))
    if rj["statut"] == READ and tested:
        dsi.append(f"Replayability: {fmt_num((cats.get('C0') or {}).get('familles') or 0)}/{fmt_num(tested)} models accepted by DuckDB; "
                   f"{MENTION_REJOUABILITE}.")
    elif rj["statut"] == READ:
        nt = (rj.get("non_testees") or {}).get("familles") or 0
        dsi.append(f"Replayability: {pl(nt, 'model')} translated, not tested by DuckDB EXPLAIN ({rj['taux']['familles'].get('raison')}).")
    daf = [f"Consumption not read: {COUTS_FICHIERS}."]
    rssi = ["No connection to a platform: files read locally; code is reduced to fingerprints and counts.",
            f"{pl(by_code['F01'], 'declared external dependency', 'declared external dependencies')} (F01); writes outside the platform, "
            "privileges and policies not read here."]
    return {"DSI": dsi, "DAF": daf, "RSSI": rssi}


def _resume(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    if ctx.famille == "fichiers":
        return _resume_fichiers(ctx, res, by_code)
    r, couts, fr = ctx.reads, res["couts"], res["fourchettes"]
    vol = fr["volume"]["valeur"]
    n_obj = sum(o["kind"] in REBUILD_KINDS or o.get("portabilite") == "a_reconstruire" for o in ctx.objets)
    mv = res["mouvements"]
    fraiche = _fraicheur_phrase(res.get("fraicheur"))  # in the first sentence: the box keeps three entries
    dsi = [f"{pl(len(ctx.present), 'present table')} ({fmt_bytes(vol) if vol is not None else 'volume not read'}) "
           f"and {pl(n_obj, 'object')} to rebuild besides tables." + (f" {fraiche}" if fraiche else ""),
           f"Movement: {fmt_num(mv['figée'])} frozen, {fmt_num(mv['vivante'])} live, {fmt_num(mv['recréée'])} recreated, "
           f"{fmt_num(mv['non_determine'])} not determined."]
    rj = res["rejouabilite"]
    if rj["statut"] == READ and rj["categories"]:
        tested = sum((v["familles"] or 0) for k, v in rj["categories"].items() if k != "C7")
        dsi.append(f"Replayability: {fmt_num((rj['categories'].get('C0') or {}).get('familles') or 0)}/{fmt_num(tested)} families "
                   f"accepted by DuckDB; {MENTION_REJOUABILITE}.")
    else:
        dsi.append(f"Replayability not read: {rj['raison']}.")

    daf = []
    jours = couts.get("fenetre_jours")
    sur = f"over {jours} days" if jours else "over the window read"
    if ctx.plat == "bigquery":
        od, rs = couts["a_la_demande"], couts["reserve"]
        a = fmt_bytes(od["octets_factures"]["valeur"]) + " billed on demand" if od["octets_factures"]["valeur"] is not None else "on-demand not read"
        b = f"{fmt_num(rs['slot_heures']['valeur'], 1)} reserved slot-hours" if rs["slot_heures"]["valeur"] is not None else "reserved not read"
        daf.append(f"Measured {sur}: {a}, {b}; the two do not add up.")
        m = od["monnaie"]
        daf.append((f"On-demand, list-price range: ${fmt_num(m['bas'], 2)} to ${fmt_num(m['haut'], 2)}" if m["nature"] == ESTIMATED
                    else f"On-demand, not estimated: {m['raison']}") + f"; reserved, not estimated: {CONTRAT_NON_LU}.")
    else:
        c = couts["credits"]
        daf.append(f"Consumption measured {sur}: {fmt_num(c['valeur'], 1)} credits." if c["valeur"] is not None
                   else f"Consumption not read: {c['raison']}.")
        m = couts["monnaie"]
        if m["nature"] == COMPUTED:
            daf.append(f"At the credit price provided: ${fmt_num(m['valeur'], 2)}.")
        elif m["nature"] == ESTIMATED:
            daf.append(f"In dollars, list-price range as of {prices.AS_OF}: ${fmt_num(m['bas'], 2)} to ${fmt_num(m['haut'], 2)}; {CONTRAT_NON_LU}.")
        else:
            daf.append(f"In dollars, not estimated: {m['raison']}.")
    h = res["ne_part_pas"]["historique"]
    if h["octets"]["valeur"] is not None:
        daf.append(f"Billed history that does not leave with the copy: {fmt_bytes(h['octets']['valeur'])} across {pl(h['tables'], 'table')} (F06, F07).")
    else:
        daf.append(f"Billed history not read: {h['octets']['raison']}.")

    rssi = []
    if r.ok("POLICY_REFERENCES", "TAG_REFERENCES", "rowAccessPolicies.list", "COLUMN_FIELD_PATHS") or by_code["F10"]:
        rssi.append(f"{pl(by_code['F10'], 'table or policy', 'tables or policies')} to rebuild on the security side (F10); "
                    "the copy requires an unfiltered principal.")
    else:
        rssi.append(f"Policies not read: {r.why(*POLICY_SOURCES)}.")
    rssi.append(f"{pl(by_code['F01'] + by_code['F02'], 'external dependency', 'external dependencies')} (F01, F02), "
                f"{pl(by_code['F03'], 'write')} outside the platform (F03), "
                f"{pl(by_code['F09'], 'share')} with external consumers (F09).")
    if res["f04_lu"]:
        rssi.append(f"{pl(by_code['F04'], 'owner role')} with no holder (F04).")
    elif ctx.plat == "bigquery":
        rssi.append("Owner roles (F04): not applicable on BigQuery, where privileges are carried by datasets and tables.")
    else:
        rssi.append("Owner roles with no holder not determined: privileges not read.")
    return {"DSI": dsi, "DAF": daf, "RSSI": rssi}


def _capacites_snapshot(c) -> dict | None:
    """Capabilities declared by a connector outside the registry: copied (known keys only), guessing nothing."""
    if not isinstance(c, dict):
        return None
    keys = ("plateforme", "libelle", "famille", "inventaire", "niveaux", "export", "preuve", "statut", "teste_sur")
    return {k: c.get(k) for k in keys}


def _liens(liens) -> list[dict]:
    out = [{"de": scrub(str(x.get("de"))), "vers": scrub(str(x.get("vers"))), "nature": x.get("nature")}
           for x in liens or [] if isinstance(x, dict) and x.get("de") and x.get("vers")]
    return sorted(out, key=lambda x: (x["de"], x["vers"], str(x["nature"])))


def _carte_sortie(objets: list[dict]) -> dict:
    """Objects per portability × object family; empty as long as no object carries a portability (snapshot /1)."""
    carte: dict = {}
    for o in objets:
        if "portabilite" not in o:
            continue
        p, f = o["portabilite"] or "non_determine", o.get("famille_objet") or "non_precisee"
        carte.setdefault(p, {})
        carte[p][f] = carte[p].get(f, 0) + 1
    return {p: dict(sorted(v.items())) for p, v in sorted(carte.items())}


# --- entry point --------------------------------------------------------------------------------------------


def analyze(snapshot: dict, *, credit_price: float | None = None) -> dict:
    """/1 or /2 snapshot (model.py) -> scan.json (schema canot.scan/2: /1 + added fields). Pure function."""
    as_of = parse_dt(snapshot.get("as_of"))
    if as_of is None:
        raise ValueError("snapshot without as_of: the analysis has no clock")
    reads = _Reads(sorted((_lecture(lec) for lec in snapshot.get("lectures") or []), key=lambda lec: lec["id"]))
    tables = sorted((_table(t, as_of) for t in snapshot.get("tables") or []), key=lambda t: t["id"])
    objets = sorted((_objet(o) for o in snapshot.get("objets") or []), key=lambda o: (str(o["kind"]), o["id"]))
    familles = sorted((_famille(f) for f in snapshot.get("familles") or []), key=lambda f: (-(f["n_exec"] or 0), str(f["empreinte"])))
    ctx = _Ctx(snapshot, as_of, reads, tables, objets, familles)
    plat = ctx.plat

    edition = snapshot.get("edition")
    edition_missing = not edition and plat == "snowflake" and reads.status("ACCESS_HISTORY") == NOT_READ
    if not edition:
        edition = "not determined (Standard or missing privilege)" if edition_missing else "not determined"

    ext = _external(ctx)
    sources = _read_sources(ctx)
    f09, shared = _f09(ctx)
    f04, unresolved = _f04(ctx)
    drapeaux = (_f01_f02(ctx, ext) + _f03(ctx) + f04 + _f05(ctx, sources, shared) + _f06_f07(ctx) + _f08(ctx) + f09
                + _f10(ctx) + _f11(ctx) + _f12(ctx, edition_missing, unresolved, sources)
                + _f13(ctx) + _f14(ctx) + _f15(ctx) + _f16(ctx))
    drapeaux.sort(key=lambda d: (d["code"], d["cible_type"], str(d["cible"])))
    by_id = {("table", t["id"]): t for t in tables} | {("objet", o["id"]): o for o in objets}
    for d in drapeaux:
        x = by_id.get((d["cible_type"], d["cible"]))
        if x is not None:
            x["drapeaux"].append(d["code"])
    for x in by_id.values():
        x["drapeaux"] = sorted(set(x["drapeaux"]))
    by_code = Counter(d["code"] for d in drapeaux)

    ne = _NonEstime()
    row_policies = plat == "bigquery" and (any(o["kind"] == "politique_lignes" for o in objets) or any(t["politiques"] for t in tables))
    couts = _couts(ctx, credit_price, ne, row_policies)
    fourchettes = _fourchettes(ctx, credit_price, ne)
    rejouabilite = _rejouabilite(ctx, ne)

    mv = Counter(t["mouvement"] for t in ctx.present)
    # the keys stay French (scan.json contract); the values counted are the English movements
    mouvements = {"figée": mv[FROZEN], "vivante": mv[LIVE], "recréée": mv[RECREATED], "non_determine": mv[None],
                  "base": reads.ids(*WRITE_SOURCES) + (reads.ids("run_results.json") if ctx.famille == "fichiers" else []),
                  "regle": (f"recreated: DDL less than {RECREEE_JOURS} days old with no later write; live: write less than "
                            f"{VIVANTE_JOURS} days old; frozen: older last write; not determined: writes not read")}

    hist_tables = [t for t in tables if t["octets_historique"] and t["octets_historique"] > 0
                   or (t["supprimee_le"] and (t["octets_actifs"] or 0) > 0)]
    hist_total = sum((t["octets_historique"] or 0) + ((t["octets_actifs"] or 0) if t["supprimee_le"] else 0) for t in hist_tables)
    if reads.ok(*STORAGE_SOURCES) and any(t["octets_historique"] is not None for t in tables):
        hist = fig(hist_total, nature=COMPUTED, unite="bytes", base=reads.ids(*STORAGE_SOURCES),
                   formule="Time Travel + Fail-safe of present tables, + bytes still billed for dropped tables")
    elif ctx.famille == "fichiers":
        hist = fig(nature=NOT_READ, unite="bytes", raison=COUTS_FICHIERS)
    else:
        hist = fig(nature=NOT_READ, unite="bytes", raison=reads.why(*STORAGE_SOURCES) if not reads.ok(*STORAGE_SOURCES) else "history not provided")
    ne.track("Billed history", hist)
    kinds = Counter(o["kind"] for o in objets)
    ne_part_pas = {
        "objets_par_type": dict(sorted(kinds.items())),
        "exemples": {k: [o["id"] for o in objets if o["kind"] == k][:5] for k in sorted(kinds)},
        "routines_par_langage": dict(sorted(Counter((o["langage"] or "not read").upper() for o in objets
                                                    if o["kind"] in ("procedure", "fonction")).items())),
        "colonnes_non_portables": sum(len(t["colonnes_non_portables"]) for t in ctx.present),
        "historique": {"octets": hist, "tables": len(hist_tables)},
    }

    outils = defaultdict(lambda: [0, 0])
    for f in familles:
        k = f["outil"] or f["app"] or "unidentified"
        outils[k][0] += 1
        outils[k][1] += f["n_exec"] or 0
    flagged = defaultdict(list)
    for d in drapeaux:
        flagged[d["code"]].append(d)
    dependances = {
        "externes": [{"id": d["cible"], "type": d["preuve"].get("type"), "emplacements": d["preuve"].get("emplacements", [])} for d in flagged["F01"]],
        "cachees": [{"id": d["cible"], "chemin": d["preuve"]["chemin"], "emplacements": d["preuve"]["emplacements"]} for d in flagged["F02"]],
        "ecritures_externes": [{"cible_type": d["cible_type"], "cible": d["cible"], "mode": d["preuve"]["mode"],
                                "emplacements": d["preuve"]["emplacements"]} for d in flagged["F03"]],
        "consommateurs": [{"id": d["cible"], "comptes": d["preuve"]["consommateurs"]} for d in flagged["F09"]],
        "outils": [{"outil": scrub(k), "familles": v[0], "executions": v[1]} for k, v in sorted(outils.items(), key=lambda kv: (-kv[1][1], kv[0]))],
        "clients": [{"outil": scrub(c.get("outil")), "sessions": c.get("sessions")}
                    for c in sorted((snapshot.get("droits") or {}).get("outils") or [], key=lambda c: str(c.get("outil")))],
        "aretes": sorted([o["id"], ref] for o in objets for ref in o["reference"]),
        "base": reads.ids("OBJECT_DEPENDENCIES", "VIEWS", "QUERY_HISTORY", "JOBS"),
    }

    ne.add("Your contract rate", CONTRAT_NON_LU if not (credit_price and plat == "snowflake") else "credit price provided, other items not read")
    ne.add("Destination storage, running both platforms in parallel, operations after the exit",
           "depend on your contract and your architecture")
    ne.add("Identical results of translated queries", f"{MENTION_REJOUABILITE}: translated to DuckDB, not compared; a canot drill compares the results")
    ne.add("Cost projections provided by the platform", "not used: they cannot be verified from the scan")
    if plat == "bigquery":
        ne.add("BigQuery free tier", "shared by the billing account, cannot be attributed per project")
        ne.add("Reads through the Storage Read API", "missing from INFORMATION_SCHEMA.JOBS")
        if row_policies:
            ne.add("Billed bytes of queries under a row access policy", "NULL value in INFORMATION_SCHEMA.JOBS")

    lectures = reads.all
    for lec in lectures:
        lec.setdefault("limite", None)
    res = {
        "schema": SCHEMA, "as_of": snapshot.get("as_of"), "versions": {k: str(v) for k, v in sorted((snapshot.get("versions") or {}).items())},
        "plateforme": plat, "perimetre": scrub(snapshot.get("perimetre")), "edition": edition,
        "schemas_caches": sorted({str(t["schema"]) for t in tables if str(t["schema"] or "").startswith("_")}
                                 | {str(d.get("nom")) for d in snapshot.get("datasets") or [] if d.get("cache") and d.get("nom")}),
        "niveau_droits": snapshot.get("niveau_droits"), "mode_sql": legacy("mode_sql", snapshot.get("mode_sql")) or READ_ONLY,
        "lectures": lectures, "tables": tables, "objets": objets, "familles": familles, "drapeaux": drapeaux,
        "couts": couts, "fourchettes": fourchettes, "dependances": dependances, "rejouabilite": rejouabilite,
        "mouvements": mouvements, "ne_part_pas": ne_part_pas, "non_estime": ne.items, "cout_du_scan": _cout_du_scan(ctx),
        "latence_max_h": max((lec["latence_max_h"] for lec in lectures if lec["statut"] == READ and lec["latence_max_h"] is not None), default=None),
        "fraicheur": _fraicheur(ctx),
        "f04_lu": bool(f04) or (all(reads.status(n) == READ for n in GRANT_SOURCES) and (snapshot.get("droits") or {}).get("roles") is not None),
        "regles": {"vivante_jours": VIVANTE_JOURS, "recreee_jours": RECREEE_JOURS, "horizon_lectures_jours": profile(plat).get("horizon_jours", 365),
                   "debit_mo_s": list(THROUGHPUT_MBPS)},
        "sources_prix": [profile(plat)["source_prix"]] if "source_prix" in profile(plat) else [],
        # canot.scan/2: connector declarations (registry first, otherwise the snapshot's), lineage and exit map
        "famille": ctx.famille,
        "capacites": capacites(plat) or _capacites_snapshot(snapshot.get("capacites")),
        "liens": _liens(snapshot.get("liens")),
        "carte_sortie": _carte_sortie(objets),
    }
    res["resume"] = _resume(ctx, res, by_code)
    return res
