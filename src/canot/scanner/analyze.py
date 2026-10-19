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
from . import i18n
from .i18n import t as _t, tr
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
LIBELLES_FR = {
    "F01": "Dépendance externe",
    "F02": "Dépendance externe cachée",
    "F03": "Écriture hors de la plateforme",
    "F04": "Rôle propriétaire sans titulaire",
    "F05": "Stockage sans lecture observée",
    "F06": "Historique facturé qui ne se transfère pas",
    "F07": "Table supprimée encore facturée",
    "F08": "SQL dynamique",
    "F09": "Consommateurs externes",
    "F10": "Sécurité à reconstruire",
    "F11": "Chaîne planifiée et ses échecs",
    "F12": "Lecture incomplète",
    "F13": "Traitement sans équivalent exécutable hors de la plateforme",
    "F14": "Extension propriétaire",
    "F15": "Modèle sans export ouvert lisible",
    "F16": "Logique métier dans la couche de présentation",
}
ETIQUETTE_FR = "À examiner"
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
KIND_LABELS_FR = {
    "vue": "vue", "vue_materialisee": "vue matérialisée", "fonction": "fonction", "procedure": "procédure", "tache": "tâche",
    "flux": "flux (stream)", "pipe": "pipe", "stage": "stage", "base_importee": "base importée", "partage": "partage",
    "table_dynamique": "table dynamique", "modele_ml": "modèle ML", "politique_masquage": "politique de masquage",
    "politique_lignes": "politique d'accès aux lignes", "requete_planifiee": "requête planifiée", "connexion": "connexion",
    "integration": "intégration", "reservation": "réservation", "etiquette": "étiquette (tag)",
    "recette_sql": "recette SQL", "recette_python": "recette Python", "recette_visuelle": "recette visuelle",
    "recette_plugin": "recette de plugin", "scenario": "scénario", "modele_entraine": "modèle entraîné", "notebook": "notebook",
    "dossier_gere": "dossier géré", "service_api": "service d'API", "env_code": "environnement de code", "plugin": "plugin",
    "mesure": "mesure", "calcul_rapport": "calcul de rapport", "rapport": "rapport", "flux_etl": "flux ETL", "dag": "DAG",
    "programme_sas": "programme SAS",
    "modele_dbt": "modèle dbt", "test_dbt": "test dbt", "source_dbt": "source dbt", "seed_dbt": "seed dbt", "snapshot_dbt": "snapshot dbt",
    "macro_dbt": "macro dbt du projet", "paquet_dbt": "paquet dbt", "exposition_dbt": "exposition dbt", "metrique_dbt": "métrique dbt",
    "modele_semantique_dbt": "modèle sémantique dbt", "requete_sauvegardee_dbt": "requête sauvegardée dbt",
    "test_unitaire_dbt": "test unitaire dbt", "fonction_dbt": "fonction dbt",
}


def kind_label(k) -> str:
    """Displayed label of an object type, in the current language (the type itself is data: unchanged)."""
    return (KIND_LABELS_FR if i18n.is_fr() else KIND_LABELS).get(k, str(k))


def kind_plural(k) -> str:
    if i18n.is_fr():
        return i18n.plural_fr(kind_label(k))
    return KIND_PLURALS.get(k, KIND_LABELS.get(k, str(k)) + "s")


def flag_label(code: str) -> str:
    return (LIBELLES_FR if i18n.is_fr() else LIBELLES)[code]


def etiquette() -> str:
    return _t(ETIQUETTE, ETIQUETTE_FR)


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
CONTRAT_NON_LU_FR = "prix de votre contrat non lu"
MENTION_REJOUABILITE = "accepted ≠ identical result"
MENTION_REJOUABILITE_FR = "accepté ≠ résultat identique"


def contrat_non_lu() -> str:
    return _t(CONTRAT_NON_LU, CONTRAT_NON_LU_FR)


def mention() -> str:
    return _t(MENTION_REJOUABILITE, MENTION_REJOUABILITE_FR)
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
IMPACTS_FR = {
    "ACCESS_HISTORY": "lectures et écritures par objet (F05)",
    "TABLE_QUERY_PRUNING_HISTORY": "solution de repli pour les lectures par table (F05)",
    "TABLES": "inventaire des tables", "COLUMNS": "types de colonnes non portables",
    "TABLE_STORAGE_METRICS": "octets actifs et historique (F05, F06, F07, fourchettes)",
    "TABLE_STORAGE": "volumes par table (F05, F06, fourchettes)", "tables.get": "volumes par table (F05, F06, fourchettes)",
    "STORAGE_USAGE": "stockage facturé moyen", "METERING_DAILY_HISTORY": "crédits par service",
    "WAREHOUSE_METERING_HISTORY": "crédits par entrepôt", "QUERY_ATTRIBUTION_HISTORY": "crédits par famille de requêtes",
    "TABLE_DML_HISTORY": "dernières écritures (mouvement)", "OBJECT_DEPENDENCIES": "dépendances entre objets (F02)",
    "VIEWS": "dépendances des vues (F02)", "FUNCTIONS": "fonctions externes et SQL dynamique (F01, F08)",
    "PROCEDURES": "SQL dynamique (F08)", "ROUTINES": "SQL dynamique (F08)", "TASKS": "chaînes planifiées (F11)",
    "TASK_HISTORY": "exécutions et échecs des tâches (F11)", "SERVERLESS_TASK_HISTORY": "exécutions des tâches serverless (F11)",
    "TASK_VERSIONS": "versions des tâches (F11)", "STREAMS": "flux (F11)", "PIPES": "ingestion continue (F11)",
    "COPY_HISTORY": "chargements observés", "STAGES": "stages externes (F01)", "DATABASES": "bases importées (F01)",
    "DATA_TRANSFER_HISTORY": "transferts sortants",
    "DYNAMIC_TABLE_REFRESH_HISTORY": "rafraîchissements des tables dynamiques (F11)",
    "AUTOMATIC_CLUSTERING_HISTORY": "crédits du clustering automatique",
    "MATERIALIZED_VIEW_REFRESH_HISTORY": "crédits des vues matérialisées",
    "SEARCH_OPTIMIZATION_HISTORY": "crédits de l'optimisation de recherche",
    "QUERY_ACCELERATION_HISTORY": "crédits de l'accélération de requêtes",
    "QUERY_HISTORY": "familles de requêtes, rejouabilité, écritures observées (F03)",
    "POLICY_REFERENCES": "politiques appliquées (F10)", "TAG_REFERENCES": "étiquettes (F10)",
    "GRANTS_TO_ROLES": "rôles propriétaires sans titulaire (F04)", "GRANTS_TO_USERS": "rôles propriétaires sans titulaire (F04)",
    "USERS": "types d'utilisateurs (personne ou service)", "SESSIONS": "outils clients", "SHARES": "consommateurs externes (F09)",
    "JOBS": "lectures par table (F05), familles de requêtes, coûts à la demande et réservés",
    "JOBS_BY_ORGANIZATION": "requêtes lancées depuis d'autres projets", "JOBS_TIMELINE": "profil de charge",
    "TABLE_OPTIONS": "emplacements des tables externes (F01)", "COLUMN_FIELD_PATHS": "tags de politique (F10)",
    "SCHEMATA_OPTIONS": "options des datasets", "SCHEMATA_LINKS": "datasets liés, Analytics Hub (F09)",
    "SHARED_DATASET_USAGE": "consommateurs Analytics Hub (F09)", "PARTITIONS": "partitions",
    "MATERIALIZED_VIEWS": "vues matérialisées", "RESERVATIONS": "capacité réservée",
    "CAPACITY_COMMITMENTS": "engagements de capacité", "RESERVATIONS_TIMELINE": "utilisation des réservations",
    "datasets.list": "inventaire des datasets", "datasets.get": "datasets liés et externes, permissions des datasets (F01, F09)",
    "transferConfigs.list": "requêtes planifiées (F11)", "rowAccessPolicies.list": "politiques d'accès aux lignes (F10)",
    "connections.list": "connexions externes (F01)", "models.list": "modèles ML",
    "SHOW WAREHOUSES": "taille et suspension des entrepôts", "SHOW DYNAMIC TABLES": "tables dynamiques (F11)",
    "SHOW ICEBERG TABLES": "tables Iceberg (F01)", "SHOW EXTERNAL TABLES": "emplacements des tables externes (F01)",
    "SHOW STREAMS": "flux (F11)", "QUERY_HISTORY × SESSIONS": "outil par famille de requêtes",
    "transferConfigs.runs.list": "exécutions et échecs des requêtes planifiées (F11)", "tables.list": "inventaire des tables",
    "manifest.json": "tout l'inventaire du projet dbt", "run_results.json": "dernières exécutions et échecs (F11, mouvement)",
    "catalog.json": "colonnes, types et volumes des relations ; rejouabilité non testée par EXPLAIN DuckDB",
}


def _impact(key) -> str:
    return (IMPACTS_FR if i18n.is_fr() else IMPACTS).get(key, _t("the conclusions that depend on it", "les conclusions qui en dépendent"))


# a read that was not done limits nothing if its replacement was read
FALLBACKS = {"TABLE_QUERY_PRUNING_HISTORY": ("ACCESS_HISTORY",), "TABLE_STORAGE": ("tables.get",), "tables.get": ("TABLE_STORAGE",),
             # regional views refused: the tables.list/get API gives inventory, types, options, definitions and policy tags
             **{v: ("tables.get",) for v in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS")}}

# --- formats (shared with render.py) --------------------------------------------------------------------


def fmt_num(x, dec: int = 0) -> str:
    """1,234.5 (English) or 1 234,5 (French, current language of i18n)."""
    return i18n.fmt_num(x, dec)


def _short_num(v: float) -> str:
    return i18n.trim0(fmt_num(v, 0 if abs(v) >= 100 else 1))


def fmt_bytes(n) -> str:
    if n is None:
        return _t("not read", "non lu")
    *units, last = i18n.BYTE_UNITS[i18n.lang()]
    for unit, f in units:
        if abs(n) >= f:
            return f"{_short_num(n / f)} {unit}"
    return f"{int(n)} {last}"


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
    return d.strftime("%Y-%m-%d") if d else _t("unknown date", "date inconnue")


def pl(n, singular: str, plural: str | None = None) -> str:
    """Count and noun; the plural rule follows the current language (French: singular for 0 and 1)."""
    return i18n.plural(n, singular, plural)


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
            return _t(f"{names[0]}: read not attempted", f"{names[0]} : lecture non tentée")
        lec = failed[0]
        return _t(f"{short_vue(lec['vue'])} not read: {lec['raison'] or 'reason not provided'}",
                 f"{short_vue(lec['vue'])} non lu : {tr(lec['raison']) or 'motif non fourni'}")

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
        return None, _t(f"dropped on {fmt_date(t['supprimee_le'])}", f"supprimée le {fmt_date(t['supprimee_le'])}")
    ddl, w, nw = parse_dt(t.get("derniere_ddl")), parse_dt(t.get("derniere_ecriture")), t.get("n_ecritures")
    if ddl and (as_of - ddl).days < RECREEE_JOURS and (w is None or (w - ddl).total_seconds() <= 3600):
        unknown = w is None and nw is None
        return RECREATED, _t(f"last DDL on {fmt_date(ddl)}, " + ("writes not read" if unknown else "no later write"),
                             f"dernier DDL le {fmt_date(ddl)}, " + ("écritures non lues" if unknown else "aucune écriture ultérieure"))
    if w and (as_of - w).days < VIVANTE_JOURS:
        return LIVE, _t(f"last write on {fmt_date(w)}", f"dernière écriture le {fmt_date(w)}")
    if w:
        return FROZEN, _t(f"last write on {fmt_date(w)}", f"dernière écriture le {fmt_date(w)}")
    if nw == 0 and ddl and (as_of - ddl).days >= VIVANTE_JOURS:
        return FROZEN, _t(f"no write observed, last DDL on {fmt_date(ddl)}", f"aucune écriture observée, dernier DDL le {fmt_date(ddl)}")
    return None, _t("writes not read", "écritures non lues")


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
    return {"code": code, "libelle": flag_label(code), "etiquette": etiquette(), "cible_type": cible_type, "cible": cible,
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
            what = (_t("Iceberg table: data on an external volume", "Table Iceberg : données sur un volume externe") if t["type"] == "ICEBERG"
                    else _t("External table: data outside the platform", "Table externe : données hors de la plateforme"))
            out[t["id"]] = ("table", what, loc)
    for o in ctx.objets:
        k, deps = o["kind"], o["dependances_externes"]
        if k == "base_importee" and o["id"].upper() in profile(ctx.plat).get("imports_systeme", ()):
            continue
        exports = bool(o["detail"].get("ecriture_externe") or o["detail"].get("exporte"))
        if k == "base_importee":
            out[o["id"]] = ("objet", _t("Database imported from an inbound share", "Base importée depuis un partage entrant"), deps)
        elif k in ("connexion", "integration") or o["externe"] or (deps and not exports):  # an export destination belongs to F03
            what = {"stage": _t("External stage", "Stage externe"),
                    "fonction": _t("External function: calls a service outside the platform",
                                   "Fonction externe : appelle un service hors de la plateforme"),
                    "connexion": _t("Connection to an external service", "Connexion à un service externe"),
                    "integration": _t("Integration with an external service", "Intégration avec un service externe")}
            out[o["id"]] = ("objet", what.get(k, _t(f"{cap(kind_label(k))}: declared external dependency",
                                                    f"{cap(kind_label(k))} : dépendance externe déclarée")), deps)
    return out


def _f01_f02(ctx: _Ctx, ext: dict) -> list[dict]:
    flags = []
    for i, (ctype, what, loc) in sorted(ext.items()):
        kind = next((o["kind"] for o in ctx.objets if o["id"] == i), None)
        base = ctx.reads.ids(*TABLE_SOURCES) if ctype == "table" else ctx.kind_ids(kind)
        where = f" ({', '.join(loc)})" if loc else ("" if kind == "base_importee" else _t(" (location not read)", " (emplacement non lu)"))
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
        texte = _t(f"Depends on {path[-1]}, outside the platform, through {pl(len(path) - 1, 'level')}: {chemin}"
                   f" ({', '.join(loc) if loc else 'location not read'}).",
                   f"Dépend de {path[-1]}, hors de la plateforme, à travers {pl(len(path) - 1, 'niveau', 'niveaux')} : {chemin}"
                   f" ({', '.join(loc) if loc else 'emplacement non lu'}).")
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
            texte = _t(f"{cap(kind_label(o['kind']))} that writes outside the platform (declared in its definition); "
                       f"destination: {', '.join(loc) if loc else 'not read'}.",
                       f"{cap(kind_label(o['kind']))} qui écrit hors de la plateforme (écriture déclarée dans sa définition) ; "
                       f"destination : {', '.join(loc) if loc else 'non lue'}.")
            flags.append(_flag("F03", "objet", o["id"], texte, ctx.kind_ids(o["kind"]), {"mode": "declared", "emplacements": loc}))
    stage_locs = {o["id"].upper(): o["dependances_externes"] for o in ctx.objets if o["kind"] == "stage"}
    for f in ctx.familles:
        if not f["ecriture_externe"]:
            continue
        # replay may name the stage rather than the bucket: go back to the stage location when it is known
        loc = sorted({x for c in f["compartiments"] for x in (stage_locs.get(c.lstrip("@").upper()) or [c])})
        qui = f["outil"] or f["app"]
        texte = _t(f"Write outside the platform observed: {pl(f['n_exec'] or 0, 'execution')}"
                   + (f" ({qui})" if qui else "") + f"; destination: {', '.join(loc) if loc else 'not read'}.",
                   f"Écriture hors de la plateforme observée : {pl(f['n_exec'] or 0, 'exécution')}"
                   + (f" ({qui})" if qui else "") + f" ; destination : {', '.join(loc) if loc else 'non lue'}.")
        flags.append(_flag("F03", "famille", (f["empreinte"] or "")[:12], texte, ctx.reads.ids("QUERY_HISTORY", "JOBS"),
                           {"mode": "observed", "emplacements": loc, "n_exec": f["n_exec"], "tables": f["tables"][:5]}))
    for o in ctx.objets:  # unloads to an external stage seen in ACCESS_HISTORY
        n = o["detail"].get("ecritures_observees_n")
        if o["kind"] == "stage" and o["externe"] and isinstance(n, int) and n > 0:
            loc = o["dependances_externes"]
            texte = _t(f"Write outside the platform observed: {pl(n, 'write')} to this external stage "
                       f"({', '.join(loc) if loc else 'location not read'}).",
                       f"Écriture hors de la plateforme observée : {pl(n, 'écriture')} vers ce stage externe "
                       f"({', '.join(loc) if loc else 'emplacement non lu'}).")
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
        texte = _t(f"Role {role} owns {pl(len(ids), 'object')} and is granted to no user, "
                   f"neither directly nor through another role.",
                   f"Le rôle {role} possède {pl(len(ids), 'objet')} et n'est attribué à aucun utilisateur, "
                   f"ni directement ni par un autre rôle.")
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


def non_vu(x: str, plat: str | None) -> str:
    """A blind spot of a read source, in the current language (French variant in the connector profile)."""
    return profile(plat).get("non_vus_fr", {}).get(x, x) if i18n.is_fr() else x


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
            fen = _t(f"from {fmt_date(src['du'])} to {fmt_date(src['au'])}", f"du {fmt_date(src['du'])} au {fmt_date(src['au'])}")
        else:
            jours, fen = None, _t("(window not provided)", "(fenêtre non fournie)")
        propre = (_t(f"{fmt_bytes(octets)} stored", f"{fmt_bytes(octets)} stockés") if not t.get("clone_de")
                  else _t(f"Clone or snapshot of {t['clone_de']} (only its own bytes are billed)",
                          f"Clone ou snapshot de {t['clone_de']} (seuls ses propres octets sont facturés)"))
        angles = ", ".join(non_vu(x, ctx.plat) for x in src["non_vus"])
        texte = _t(f"{propre}. No read observed in {src['label']} {fen}; blind spots: {angles}.",
                   f"{propre}. Aucune lecture observée dans {src['label']} {fen} ; angles morts : {angles}.")
        if jours is not None and jours < horizon - 1:
            texte += _t(f" {jours}-day window, shorter than {horizon}: a quarterly or yearly job may not appear in it.",
                        f" Fenêtre de {jours} jours, plus courte que {horizon} : un traitement trimestriel ou annuel peut ne pas y figurer.")
        if src["partiel"]:
            texte += _t(" Some read windows were not read.", " Certaines fenêtres de lecture n'ont pas été lues.")
        ddl = parse_dt(t["derniere_ddl"])
        if ddl and src["du"] and ddl > src["du"]:
            texte += _t(f" Last DDL on {fmt_date(ddl)}.", f" Dernier DDL le {fmt_date(ddl)}.")
        if shared.get(t["id"]):
            texte += _t(f" Shared with {pl(shared[t['id']], 'external consumer')}: their reads are not seen.",
                        f" Partagée avec {pl(shared[t['id']], 'consommateur externe', 'consommateurs externes')} : leurs lectures ne sont pas vues.")
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
                texte = _t(f"Dropped on {fmt_date(t['supprimee_le'])}; {fmt_bytes(total)} still billed (Time Travel and Fail-safe).",
                           f"Supprimée le {fmt_date(t['supprimee_le'])} ; {fmt_bytes(total)} encore facturés (Time Travel et Fail-safe).")
                flags.append(_flag("F07", "table", t["id"], texte, base + ctx.reads.ids("TABLES"),
                                   {"supprimee_le": t["supprimee_le"], "octets": total}))
        elif h and h > 0:
            texte = _t(f"{fmt_bytes(h)} of billed history (Time Travel and Fail-safe)"
                       + (f", retention {fmt_num(t['retention_jours'])} days" if t["retention_jours"] is not None else "")
                       + (f", for {fmt_bytes(a)} active" if a is not None else "") + "; this history does not leave with the copy.",
                       f"{fmt_bytes(h)} d'historique facturé (Time Travel et Fail-safe)"
                       + (f", rétention {pl(t['retention_jours'], 'jour')}" if t["retention_jours"] is not None else "")
                       + (f", pour {fmt_bytes(a)} actifs" if a is not None else "") + " ; cet historique ne part pas avec la copie.")
            flags.append(_flag("F06", "table", t["id"], texte, base,
                               {"octets_historique": h, "octets_actifs": a, "retention_jours": t["retention_jours"]}))
    return flags


F08_DBT = (": SQL produced by Jinja and macros at compile time; the replayed SQL depends on the target and the variables, "
           "only the compiled SQL that was read is analyzed.")
F08_DBT_FR = (" : SQL produit par Jinja et les macros à la compilation ; le SQL rejoué dépend de la cible et des variables, "
              "seul le SQL compilé qui a été lu est analysé.")


def _f08(ctx: _Ctx) -> list[dict]:
    return [_flag("F08", "objet", o["id"],
                  f"{cap(kind_label(o['kind']))}"
                  + (_t(F08_DBT, F08_DBT_FR) if o["kind"] in DBT_KINDS else (f" ({o['langage']})" if o["langage"] else "")
                     + _t(": builds SQL at run time; the objects it touches cannot be read from the code.",
                          " : construit du SQL à l'exécution ; les objets touchés ne peuvent pas être lus dans le code.")),
                  ctx.kind_ids(o["kind"]), {"langage": o["langage"]})
            for o in ctx.objets if o["sql_dynamique"] is True]


CONSUMER_KEYS = (("comptes_cibles", "target account", "target accounts"), ("comptes_cibles_n", "target account", "target accounts"),
                 ("projets_consommateurs_n", "consumer project", "consumer projects"), ("abonnes", "subscriber", "subscribers"),
                 ("consommateurs", "consumer", "consumers"))
CONSUMER_WORDS_FR = {"target account": ("compte cible", "comptes cibles"), "consumer project": ("projet consommateur", "projets consommateurs"),
                     "subscriber": ("abonné", "abonnés"), "consumer": ("consommateur", "consommateurs")}


def _consumers(o: dict) -> tuple[int | None, str]:
    d = o["detail"]
    for k, one, many in CONSUMER_KEYS:
        if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool):
            if i18n.is_fr():
                one, many = CONSUMER_WORDS_FR[one]
            return int(d[k]), pl(int(d[k]), one, many)
    return None, _t("external consumers (number not read)", "des consommateurs externes (nombre non lu)")


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
        if i18n.is_fr():
            quoi = (pl(len(o["reference"]), "objet partagé", "objets partagés") if o["reference"]
                    else f"base {o['detail']['base']}" if o["detail"].get("base") else "objets partagés non lus")
        else:
            quoi = (pl(len(o["reference"]), "shared object") if o["reference"]
                    else f"database {o['detail']['base']}" if o["detail"].get("base") else "shared objects not read")
        texte = _t(f"Share to {qui}; {quoi}. Consumer reads are not seen by this scan.",
                   f"Partage vers {qui} ; {quoi}. Les lectures des consommateurs ne sont pas vues par ce scan.")
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
            parts.append(_t(f"policies: {', '.join(t['politiques'])}", f"politiques : {', '.join(t['politiques'])}"))
        if t["etiquettes_n"]:
            parts.append(pl(t["etiquettes_n"], _t("tag", "étiquette")))
        texte = _t(f"{cap('; '.join(parts))}. They do not leave with the copy; "
                   f"the copy requires a principal not filtered by these policies.",
                   f"{cap(' ; '.join(parts))}. Elles ne partent pas avec la copie ; "
                   f"la copie exige un principal non filtré par ces politiques.")
        flags.append(_flag("F10", "table", t["id"], texte, base, {"politiques": t["politiques"], "etiquettes_n": t["etiquettes_n"]}))
    for o in ctx.objets:
        if o["kind"] not in POLICY_KINDS:
            continue
        what = cap(kind_label(o["kind"]))
        texte = _t(f"{what} applied to {pl(len(o['reference']), 'object')}; it does not leave with the copy",
                   f"{what} appliquée à {pl(len(o['reference']), 'objet')} ; elle ne part pas avec la copie")
        texte += "." if o["kind"] == "etiquette" else _t("; the copy requires an unfiltered principal.",
                                                         " ; la copie exige un principal non filtré.")
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
        head = [cap(kind_label(o["kind"]))]
        if o["planification"]:
            head.append(_t(f"schedule {o['planification']}", f"planification {tr(o['planification'])}"))
        if o["etat"]:
            head.append(_t(f"state {o['etat']}", f"état {tr(o['etat'])}"))
        if o["kind"] == "flux" and o["executions_n"] is None:
            runs = (_t(f"on {', '.join(o['reference'])}", f"sur {', '.join(o['reference'])}") if o["reference"]
                    else _t("tracked object not read", "objet suivi non lu"))
        elif o["executions_n"] is None:
            runs = _t("runs not read", "exécutions non lues")
        else:
            runs = (pl(o["executions_n"], _t("run", "exécution")) + ", "
                    + (pl(o["echecs_n"], _t("failure", "échec")) if o["echecs_n"] is not None else _t("failures not read", "échecs non lus")))
        if o["derniere_execution"]:
            runs += _t(f", last on {fmt_date(o['derniere_execution'])}", f", dernière le {fmt_date(o['derniere_execution'])}")
        ch = chain(o)
        sep = _t("; ", " ; ")
        texte = (", ".join(head) + f"{sep}{runs}" + (_t(f"; chain: {' → '.join(ch)}", f" ; chaîne : {' → '.join(ch)}") if len(ch) > 1 else "")
                 + ".")
        flags.append(_flag("F11", "objet", i, texte, ctx.kind_ids(o["kind"]),
                           {"chaine": ch, "executions_n": o["executions_n"], "echecs_n": o["echecs_n"], "etat": o["etat"]}))
    return flags


def _label(o: dict) -> str:
    return cap(kind_label(o["kind"]))


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
            io.append(pl(len(o["entrees"]), _t("input", "entrée")))
        if "sorties" in o:
            io.append(pl(len(o["sorties"]), _t("output", "sortie")))
        raison = o["detail"].get("raison_portabilite")
        texte = _t(f"{_label(o)} with no executable equivalent off the platform (type read: {o['kind']}"
                   + (f", engine {o['moteur']}" if o.get("moteur") else "") + ")"
                   + (f"; {raison}" if raison else "")
                   + (f"; {', '.join(io)}" if io else "") + "; to rebuild: counted, not replayed.",
                   f"{_label(o)} sans équivalent exécutable hors de la plateforme (type lu : {o['kind']}"
                   + (f", moteur {o['moteur']}" if o.get("moteur") else "") + ")"
                   + (f" ; {tr(raison)}" if raison else "")
                   + (f" ; {', '.join(io)}" if io else "") + " ; à reconstruire : compté, non rejoué.")
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
            etat.append(_t("code not read", "code non lu"))
        if licence:
            etat.append(_t(f"license {licence}", f"licence {licence}"))
        usage = ((_t(f"used by {pl(len(users), 'object')}: ", f"utilisé par {pl(len(users), 'objet')} : ") + ", ".join(users[:5])
                  + (" …" if len(users) > 5 else "")) if users
                 else _t("no use observed in the objects read", "aucune utilisation observée dans les objets lus")) if usage_lu \
            else _t("use per object not read", "utilisation par objet non lue")
        texte = f"{_label(o)} ({', '.join(etat)}) ; {usage}."
        flags.append(_flag("F14", "objet", i, texte, ctx.kind_ids(o["kind"]),
                           {"version": o["detail"].get("version"), "code_lu": not code_non_lu, "licence": licence, "utilise_par": users[:10]}))
    for o in ctx.objets:
        if o["kind"] == "recette_plugin" and not any(e in plugins for e in o.get("extensions", [])):
            ext = o.get("extensions", [])
            texte = _t(f"{_label(o)}: extension used but not inventoried"
                       + (f" ({', '.join(ext[:5])})" if ext else " (plugin not named by collection)") + "; extension code not read.",
                       f"{_label(o)} : extension utilisée mais non inventoriée"
                       + (f" ({', '.join(ext[:5])})" if ext else " (plugin non nommé par la collecte)") + " ; code de l'extension non lu.")
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
            texte = _t(f"{head}: export formats not read (license or privileges); open export not determined.",
                       f"{head} : formats d'export non lus (licence ou droits) ; export ouvert non déterminé.")
        elif any(f in FORMATS_OUVERTS for f in fx):
            continue
        else:
            texte = _t(f"{head}: no open format declared among MLflow, ONNX, PMML, Python"
                       + (f" (formats read: {', '.join(fx)})" if fx else " (no format read)") + ".",
                       f"{head} : aucun format ouvert déclaré parmi MLflow, ONNX, PMML, Python"
                       + (f" (formats lus : {', '.join(fx)})" if fx else " (aucun format lu)") + ".")
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
            pourquoi = _t("non-trivial according to the connector", "calcul non trivial selon le connecteur")
        elif nt is None and isinstance(lignes, int) and lignes >= F16_LIGNES:
            pourquoi = _t(f"{pl(lignes, 'line')} of calculation", f"{pl(lignes, 'ligne')} de calcul")
        else:
            continue
        texte = _t(f"{_label(o)} in the presentation layer, {pourquoi}" + (f"; language {o['langage']}" if o.get("langage") else "")
                   + "; recalculation outside the tool not compared.",
                   f"{_label(o)} dans la couche de présentation, {pourquoi}" + (f" ; langage {o['langage']}" if o.get("langage") else "")
                   + " ; recalcul hors de l'outil non comparé.")
        flags.append(_flag("F16", "objet", o["id"], texte, ctx.kind_ids(o["kind"]),
                           {"non_triviale": nt, "lignes_code": lignes, "langage": o.get("langage")}))
    return flags


def _f12(ctx: _Ctx, edition_missing: bool, unresolved_owners: list[str], sources: list[dict]) -> list[dict]:
    r, flags = ctx.reads, []
    for lec in r.all:
        key = _impact_key(lec["vue"])
        if lec["statut"] == READ:
            if (lec["raison"] or "").startswith("partial"):
                impact = _impact(key)
                lec["limite"] = impact
                flags.append(_flag("F12", "lecture", lec["id"],
                                   _t(f"{short_vue(lec['vue'])} partly read ({lec['raison']}); limits: {impact}.",
                                      f"{short_vue(lec['vue'])} lu en partie ({tr(lec['raison'])}) ; limite : {impact}."),
                                   [lec["id"]], {"vue": lec["vue"], "raison": lec["raison"]}))
            continue
        if key in FALLBACKS and r.ok(*FALLBACKS[key]):
            lec["limite"] = _t(f"none: {FALLBACKS[key][0]} read instead", f"aucune : {FALLBACKS[key][0]} lu à la place")
            continue
        if key == "ACCESS_HISTORY":
            impact = (_t("reads per object: F05 computed with TABLE_QUERY_PRUNING_HISTORY, medium confidence",
                         "lectures par objet : F05 calculé avec TABLE_QUERY_PRUNING_HISTORY, confiance moyenne")
                      if r.ok("TABLE_QUERY_PRUNING_HISTORY") else _t("reads per object: F05 not computed", "lectures par objet : F05 non calculé"))
            if edition_missing:
                impact += _t("; edition not determined (Standard or missing privilege)", " ; édition non déterminée (Standard ou droit absent)")
        else:
            impact = _impact(key)
        lec["limite"] = impact
        texte = _t(f"{short_vue(lec['vue'])} not read ({lec['raison'] or 'reason not provided'}); limits: {impact}.",
                   f"{short_vue(lec['vue'])} non lu ({tr(lec['raison']) or 'motif non fourni'}) ; limite : {impact}.")
        flags.append(_flag("F12", "lecture", lec["id"], texte, [], {"vue": lec["vue"], "raison": lec["raison"]}))
    f04 = _t("owner roles with no holder (F04)", "rôles propriétaires sans titulaire (F04)")
    if ctx.plat == "snowflake" and not r.find(*GRANT_SOURCES):
        flags.append(_flag("F12", "lecture", "droits",
                           _t(f"Privileges not read (level {ctx.snap.get('niveau_droits')}); limits: {f04}.",
                              f"Droits non lus (niveau {ctx.snap.get('niveau_droits')}) ; limite : {f04}."), []))
    elif r.find(*GRANT_SOURCES) and all(r.status(n) == READ for n in GRANT_SOURCES) and (ctx.snap.get("droits") or {}).get("roles") is None:
        flags.append(_flag("F12", "lecture", "droits", _t(f"Privileges read but not passed to the analysis; limits: {f04}.",
                                                          f"Droits lus mais non transmis à l'analyse ; limite : {f04}."), []))
    if unresolved_owners:
        flags.append(_flag("F12", "lecture", "roles",
                           _t(f"{pl(len(unresolved_owners), 'owner role')} with no grant read; limits: {f04}.",
                              f"{pl(len(unresolved_owners), 'rôle propriétaire', 'rôles propriétaires')} sans attribution lue ; limite : {f04}."),
                           r.ids(*GRANT_SOURCES), {"roles": unresolved_owners[:10]}))
    if sources:
        unknown = [t["id"] for t in ctx.present if _source_for(t, sources)[0] is None and t["type"] not in ("EXTERNE", "TEMPORAIRE")]
        if unknown:
            flags.append(_flag("F12", "lecture", "lectures_par_table",
                               _t(f"Reads not determined for {pl(len(unknown), 'table')}; limits: storage with no observed read (F05).",
                                  f"Lectures non déterminées pour {pl(len(unknown), 'table')} ; limite : stockage sans lecture observée (F05)."),
                               [i for s in sources for i in s["ids"]], {"tables": unknown[:10]}))
    # an external function has no SQL body: its empty sql_dynamique is not unread code
    hidden = [o["id"] for o in ctx.objets if o["kind"] in ("procedure", "fonction") and o["sql_dynamique"] is None
              and not (o["kind"] == "fonction" and o["externe"])]
    if hidden:
        flags.append(_flag("F12", "lecture", "code_des_routines",
                           _t(f"Code not read for {pl(len(hidden), 'routine')} (filtered by privileges or not provided); limits: dynamic SQL (F08).",
                              f"Code non lu pour {pl(len(hidden), 'routine')} (filtré par les droits ou non fourni) ; limite : SQL dynamique (F08)."),
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
        return fig(nature=NOT_READ, unite=unite, raison=reads.why(*names) if not reads.ok(*names) else _t("not provided by collection", "non fourni par la collecte"))
    return fig(value, nature=MEASURED, unite=unite, base=reads.ids(*names),
               confiance=conf or ("medium" if reads.status(*names) == PARTIAL else None))


def _usd_from_credits(credits: dict, credit_price: float | None, base: list[str]) -> dict:
    c = credits["valeur"] if credits["nature"] != ESTIMATED else None
    lo, hi = (credits["bas"], credits["haut"]) if credits["nature"] == ESTIMATED else (c, c)
    if lo is None:
        return fig(nature=NOT_ESTIMATED, unite="USD", raison=_t("credits not read", "crédits non lus"))
    formule = _t("credits × credit price provided", "crédits × prix du crédit fourni")
    if credit_price:
        hyp = [_t(f"credit price provided: ${credit_price}", f"prix du crédit fourni : {i18n.usd(credit_price, 2)}")]
        if credits["nature"] == ESTIMATED:
            return fig(nature=ESTIMATED, unite="USD", bas=round(lo * credit_price, 2), haut=round(hi * credit_price, 2), base=base,
                       formule=formule, hypotheses=hyp)
        return fig(round(c * credit_price, 2), nature=COMPUTED, unite="USD", base=base, formule=formule, hypotheses=hyp)
    pmin, pmax = min(prices.SNOWFLAKE_CREDIT_USD.values()), max(prices.SNOWFLAKE_CREDIT_USD.values())
    return fig(nature=ESTIMATED, unite="USD", bas=round(lo * pmin, 2), haut=round(hi * pmax, 2), base=base, confiance="low",
               formule=_t("credits × list price of the credit, from lowest to highest", "crédits × prix catalogue du crédit, du plus bas au plus haut"),
               hypotheses=[_t(f"Snowflake list price as of {prices.AS_OF}, AWS Paris/Frankfurt: ${fmt_num(pmin, 2)} (Standard edition) to ${fmt_num(pmax, 2)} "
                              f"(highest edition), {prices.SNOWFLAKE_SOURCE}",
                              f"prix catalogue Snowflake au {prices.AS_OF}, AWS Paris/Francfort : {i18n.usd(pmin)} (édition Standard) à {i18n.usd(pmax)} "
                              f"(édition la plus haute), {prices.SNOWFLAKE_SOURCE}"),
                           _t(f"{CONTRAT_NON_LU}; your region may differ", f"{CONTRAT_NON_LU_FR} ; votre région peut différer")])


def _months_touched(du: datetime, au: datetime) -> int:
    return (au.year - du.year) * 12 + au.month - du.month + 1


COUTS_FICHIERS = "project files: no credits and no billed bytes; the scan of the target platform measures them"
COUTS_FICHIERS_FR = "fichiers de projet : ni crédits ni octets facturés ; le scan de la plateforme cible les mesure"


def couts_fichiers() -> str:
    return _t(COUTS_FICHIERS, COUTS_FICHIERS_FR)


def _couts(ctx: _Ctx, credit_price: float | None, ne: _NonEstime, row_policies: bool) -> dict:
    r, m = ctx.reads, ctx.snap.get("metering") or {}
    if ctx.famille == "fichiers":
        ne.add(_t("Platform consumption", "Consommation de la plateforme"), _t(f"not read: {COUTS_FICHIERS}", f"non lue : {COUTS_FICHIERS_FR}"))
        return {"fenetre_jours": None, "credits": fig(nature=NOT_READ, unite="credits", raison=couts_fichiers()),
                "credits_par_service": [], "credits_par_entrepot": [], "transferts": [],
                "monnaie": fig(nature=NOT_ESTIMATED, unite="USD", raison=_t("consumption not read", "consommation non lue")),
                "stockage_octets_moyens": fig(nature=NOT_READ, unite="bytes", raison=couts_fichiers()),
                "stockage_monnaie": fig(nature=NOT_ESTIMATED, unite="USD", raison=contrat_non_lu()),
                "note": _t("No consumption in project files.", "Aucune consommation dans les fichiers de projet.")}
    out: dict = {"fenetre_jours": m.get("fenetre_jours")}
    stock = _measured(m.get("stockage_octets_moyens"), r, ("STORAGE_USAGE", "TABLE_STORAGE", "tables.get"), "bytes")
    out["stockage_octets_moyens"] = ne.track(_t("Average billed storage", "Stockage facturé moyen"), stock)
    out["stockage_monnaie"] = ne.track(_t("Storage cost", "Coût du stockage"), fig(nature=NOT_ESTIMATED, unite="USD", raison=contrat_non_lu()))
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
                    formule=_t(f"sum of credits per service over {m.get('fenetre_jours') or '?'} days",
                               f"somme des crédits par service sur {m.get('fenetre_jours') or '?'} jours"),
                    confiance="medium" if r.status(*names) == PARTIAL or len(known) < len(par) else None)
    else:
        par = []
        total = fig(nature=NOT_READ, unite="credits", raison=r.why(*names) if not r.ok(*names) else _t("not provided by collection",
                                                                                                     "non fourni par la collecte"))
    out["credits"] = ne.track(_t("Credits consumed", "Crédits consommés"), total)
    out["credits_par_service"] = par
    wnames = ("WAREHOUSE_METERING_HISTORY",)
    out["credits_par_entrepot"] = [{"entrepot": scrub(w.get("entrepot")), "credits": _measured(w.get("credits"), r, wnames, "credits")}
                                   for w in sorted(m.get("par_entrepot") or [], key=lambda w: (-(w.get("credits") or 0), str(w.get("entrepot"))))
                                   ] if r.ok(*wnames) else []
    out["monnaie"] = ne.track(_t("Credits in dollars", "Crédits en dollars"), _usd_from_credits(total, credit_price, r.ids(*names)))
    out["note"] = _t("Credits measured; in dollars, only with the price provided or a dated list-price range.",
                     "Crédits mesurés ; en dollars, seulement avec le prix fourni ou une fourchette de prix catalogue datée.")
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
        hyp = [_t(f"on-demand list price as of {prices.AS_OF}: ${fmt_num(price, 2)} per TiB (US rate; region to confirm), {prices.BIGQUERY_SOURCE}",
                  f"prix catalogue à la demande au {prices.AS_OF} : {i18n.usd(price)} par TiB (tarif US ; région à confirmer), {prices.BIGQUERY_SOURCE}"),
               _t("high: no free tier deducted; low: 1 free TiB per month touched by the window; "
                  "the free tier is shared by the billing account, it cannot be attributed per project",
                  "haut : aucune gratuité déduite ; bas : 1 TiB gratuit par mois touché par la fenêtre ; "
                  "la gratuité est partagée par le compte de facturation, elle ne peut pas être attribuée par projet"), contrat_non_lu()]
        if row_policies:
            hyp.append(_t("billed bytes NULL for queries on tables with a row access policy: not counted",
                          "octets facturés NULL pour les requêtes sur des tables sous politique d'accès aux lignes : non comptés"))
        hi = round(tib * price, 2)
        lo = round(max(0.0, tib - months) * price, 2) if months else hi
        usd_od = fig(nature=ESTIMATED, unite="USD", bas=lo, haut=hi, base=base, confiance="low",
                     formule=_t("billed TiB × list price, free tier deducted or not", "TiB facturés × prix catalogue, gratuité déduite ou non"),
                     hypotheses=hyp)
    else:
        usd_od = fig(nature=NOT_ESTIMATED, unite="USD", raison=_t("billed bytes not read", "octets facturés non lus"))
    slots = _measured((rs or {}).get("slot_heures"), r, names, "slot-hours")
    return {
        "a_la_demande": {"octets_factures": ne.track(_t("On-demand billed bytes", "Octets facturés à la demande"), octets),
                         "requetes": _measured((od or {}).get("requetes"), r, names, "queries"),
                         "monnaie": ne.track(_t("On-demand, in dollars", "À la demande, en dollars"), usd_od)},
        "reserve": {"slot_heures": ne.track(_t("Reserved slot-hours", "Heures-slot réservées"), slots),
                    "requetes": _measured((rs or {}).get("requetes"), r, names, "queries"),
                    "monnaie": ne.track(_t("Reserved, in dollars", "Réservé, en dollars"),
                                        fig(nature=NOT_ESTIMATED, unite="USD", raison=contrat_non_lu()))},
        "note": _t("On-demand and reserved: two billing models, never added together.",
                   "À la demande et réservé : deux modèles de facturation, jamais additionnés."),
    }


def _fourchettes(ctx: _Ctx, credit_price: float | None, ne: _NonEstime) -> dict:
    r = ctx.reads
    base = r.ids(*STORAGE_SOURCES)
    movable = [t for t in ctx.present if t["type"] not in ("EXTERNE", "TEMPORAIRE")]
    known = [t["octets_actifs"] for t in movable if t["octets_actifs"] is not None]
    missing = len(movable) - len(known)
    if movable and not known:
        if ctx.famille == "fichiers":
            raison = (_t("volumes missing from catalog.json", "volumes absents de catalog.json") if r.ok("catalog.json")
                      else _t("catalog.json not read", "catalog.json non lu"))
        else:
            raison = r.why(*STORAGE_SOURCES) if not r.ok(*STORAGE_SOURCES) else _t("volumes not provided", "volumes non fournis")
        volume = fig(nature=NOT_READ, unite="bytes", raison=raison)
    else:
        hyp = [_t("active bytes of present tables, excluding external and temporary tables",
                  "octets actifs des tables présentes, hors tables externes et temporaires")]
        if ctx.plat == "snowflake":
            hyp.append(_t("compressed stored volume; the volume read by the copy may be higher",
                          "volume stocké compressé ; le volume lu par la copie peut être plus élevé"))
        if any(t["type"] == "CLONE" for t in movable):
            hyp.append(_t("clones: only their own bytes are counted", "clones : seuls leurs propres octets sont comptés"))
        if missing:
            hyp.append(_t(f"{pl(missing, 'table')} with no volume read, not counted: minimum value",
                          f"{pl(missing, 'table')} sans volume lu, non comptée{'s' if missing > 1 else ''} : valeur minimale"))
        volume = fig(sum(known), nature=COMPUTED, unite="bytes", base=base, formule=_t("sum of active bytes", "somme des octets actifs"),
                     hypotheses=hyp, confiance="medium" if missing else None)
    out = {"volume": ne.track(_t("Volume to copy", "Volume à copier"), volume)}
    v = volume["valeur"]
    debit = _t(f"assumed throughput of {fmt_num(THROUGHPUT_MBPS[0])} to {fmt_num(THROUGHPUT_MBPS[1])} MB/s, replaced by the measurement of a canot drill",
               f"débit supposé de {fmt_num(THROUGHPUT_MBPS[0])} à {fmt_num(THROUGHPUT_MBPS[1])} Mo/s, remplacé par la mesure d'un canot drill")
    if v is None:
        out["duree_lecture"] = ne.track(_t("Copy read time", "Durée de lecture de la copie"),
                                        fig(nature=NOT_ESTIMATED, unite="hours", raison=_t("volume not read", "volume non lu")))
        hours = None
    else:
        hours = (v / 1e6 / THROUGHPUT_MBPS[1] / 3600, v / 1e6 / THROUGHPUT_MBPS[0] / 3600)
        out["duree_lecture"] = fig(nature=ESTIMATED, unite="hours", bas=round(hours[0], 2), haut=round(hours[1], 2), base=base,
                                   formule=_t("volume ÷ assumed throughput", "volume ÷ débit supposé"), hypotheses=[debit], confiance="low")
    if ctx.plat == "snowflake":
        if hours is None:
            cr = fig(nature=NOT_ESTIMATED, unite="credits", raison=_t("volume not read", "volume non lu"))
        else:
            cr = fig(nature=ESTIMATED, unite="credits", bas=round(hours[0] * CREDITS_PAR_HEURE, 2), haut=round(hours[1] * CREDITS_PAR_HEURE, 2),
                     base=base, formule=_t("time × credits per hour", "durée × crédits par heure"),
                     hypotheses=[debit, _t("XS warehouse, 1 credit per hour", "entrepôt XS, 1 crédit par heure")], confiance="low")
        out["credits_lecture"] = ne.track(_t("Copy read credits", "Crédits de lecture de la copie"), cr)
        out["cout_lecture"] = ne.track(_t("Copy read cost", "Coût de lecture de la copie"), _usd_from_credits(cr, credit_price, base))
    elif ctx.plat != "bigquery":
        out["cout_lecture"] = ne.track(_t("Copy read cost", "Coût de lecture de la copie"),
                                       fig(nature=NOT_ESTIMATED, unite="USD",
                                           raison=_t("depends on the platform that hosts the tables, not read here",
                                                     "dépend de la plateforme qui héberge les tables, non lue ici")))
    else:
        out["cout_lecture"] = ne.track(_t("Copy read cost (Storage Read API, network egress)",
                                          "Coût de lecture de la copie (Storage Read API, sortie réseau)"),
                                       fig(nature=NOT_ESTIMATED, unite="USD", raison=contrat_non_lu(),
                                           hypotheses=[_t("read through the Storage Read API, billed per TiB read, and network egress out of Google Cloud",
                                                          "lecture par la Storage Read API, facturée par TiB lu, et sortie réseau hors de Google Cloud")]))
    n_obj = sum(o["kind"] in REBUILD_KINDS or o.get("portabilite") == "a_reconstruire" for o in ctx.objets)
    n_cols = sum(len(t["colonnes_non_portables"]) for t in ctx.present)
    lo, hi = prices.rework_days(n_obj, n_cols)
    rd, rc = prices.REWORK_DAYS_PER_OBJECT, prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN
    hyp = [_t(f"{pl(n_obj, 'object')} to rebuild and {pl(n_cols, 'column')} with a platform-specific type",
              f"{pl(n_obj, 'objet')} à reconstruire et {pl(n_cols, 'colonne')} de type propre à la plateforme"),
           _t(f"q4o assumptions: {fmt_num(rd[0], 2)} to {fmt_num(rd[1], 1)} day per object, "
              f"{fmt_num(rc[0], 1)} to {fmt_num(rc[1], 1)} per column, open to discussion",
              f"hypothèses q4o : {fmt_num(rd[0], 2)} à {fmt_num(rd[1], 1)} jour par objet, "
              f"{fmt_num(rc[0], 1)} à {fmt_num(rc[1], 1)} par colonne, à discuter")]
    unread = [short_vue(lec["vue"]) for lec in r.find(*CODE_SOURCES) if lec["statut"] != READ]
    if unread:
        hyp.append(_t(f"objects from reads not done not counted: {', '.join(sorted(set(unread)))}",
                      f"objets des lectures non faites non comptés : {', '.join(sorted(set(unread)))}"))
    out["reprise"] = fig(nature=ESTIMATED, unite="days", bas=lo, haut=hi, base=r.ids(*CODE_SOURCES),
                         formule=_t("objects × days per object + columns × days per column", "objets × jours par objet + colonnes × jours par colonne"),
                         hypotheses=hyp, confiance="low")
    return out


def _tr_fig(f: dict) -> dict:
    """A figure computed by collection (replay, dbt): its texts in the current language."""
    if not i18n.is_fr():
        return f
    return {**f, "formule": tr(f.get("formule")), "raison": tr(f.get("raison")), "hypotheses": [tr(h) for h in f.get("hypotheses") or []]}


def _rejouabilite(ctx: _Ctx, ne: _NonEstime) -> dict:
    names = ("QUERY_HISTORY", "JOBS")
    rj = ctx.snap.get("rejouabilite")
    if not rj:
        raison = ctx.reads.why(*names) if not ctx.reads.ok(*names) else _t("not provided by collection", "non fourni par la collecte")
        ne.add(_t("Query replayability", "Rejouabilité des requêtes"), raison)
        return {"statut": NOT_READ, "raison": raison, "categories": {}, "base": None, "mention": mention(), "taux": {}}
    cats = {k: {"familles": v.get("familles"), "executions": v.get("executions"), "poids": v.get("poids")}
            for k, v in sorted((rj.get("categories") or {}).items())}
    given = rj.get("taux") or {}
    given = {k: ({**v, "nature": legacy("nature", v.get("nature"))} if isinstance(v, dict) else v) for k, v in given.items()}
    if given and all(isinstance(v, dict) and v.get("nature") in NATURES for v in given.values()):
        taux = {k: _tr_fig(dict(v)) for k, v in sorted(given.items())}  # computed by replay on its own bases: taken as is
    else:
        tested = [k for k in cats if k != "C7"]
        taux = {}
        for key, unite in (("familles", "families"), ("executions", "executions"), ("poids", "credits" if ctx.plat == "snowflake" else "bytes")):
            vals = [cats[k][key] for k in tested]
            den = sum(v for v in vals if v is not None)
            num = (cats.get("C0") or {}).get(key)
            if None in vals or num is None or not den:
                taux[key] = fig(nature=NOT_READ, unite="%", raison=_t(f"{unite} not provided per category", f"{tr(unite)} non fournis par catégorie"))
            else:
                taux[key] = fig(round(100 * num / den, 1), nature=COMPUTED, unite="%", base=ctx.reads.ids(*names),
                                formule=_t(f"C0 ÷ C0 to C6, in {unite}; C7 counted separately", f"C0 ÷ C0 à C6, en {tr(unite)} ; C7 compté à part"))
    return {"statut": READ, "raison": None, "categories": cats, "base": tr(scrub(rj.get("base"))), "mention": mention(),
            "taux": taux, "bases": {k: tr(scrub(v)) for k, v in sorted((rj.get("bases") or {}).items())},
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
                  formule=_t("reads attempted, read or refused by the platform", "lectures tentées, lues ou refusées par la plateforme"),
                  hypotheses=[_t("one read = one query", "une lecture = une requête")])
    duree = (fig(c["duree_s"], nature=MEASURED, unite="seconds") if c.get("duree_s") is not None
             else fig(nature=NOT_READ, unite="seconds", raison=_t("duration not provided by collection", "durée non fournie par la collecte")))
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
            "latence_max_h": f.get("latence_max_h"), "texte": tr(scrub(f.get("texte"))),
            "tables_sans_historique": n_t, "latence_historique_h": max(lat, default=None)}


def _fraicheur_phrase(fr: dict | None) -> str | None:
    """Sentences of the CIO summary: tables seen only in real time, volumes not yet computed by TABLE_STORAGE_METRICS."""
    if not fr:
        return None
    out = []
    n, h, v = fr["tables_sans_historique"], fr["latence_historique_h"], fr.get("volumes_en_retard") or 0
    if i18n.is_fr():
        lat = f", dont la latence peut atteindre {i18n.trim0(fmt_num(h, 1))} h" if h else ""
        if n == 1:
            out.append(f"1 table vue seulement en temps réel : absente d'ACCOUNT_USAGE{lat} ; "
                       "son historique (volumes facturés, lectures) n'est pas encore disponible.")
        elif n > 1:
            out.append(f"{fmt_num(n)} tables vues seulement en temps réel : absentes d'ACCOUNT_USAGE{lat} ; "
                       "leur historique (volumes facturés, lectures) n'est pas encore disponible.")
        if v:
            out.append(f"Volume sous-estimé : métriques de stockage pas encore à jour pour {pl(v, 'table')}.")
        return " ".join(out) or None
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


def _resume_fichiers_fr(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    """French twin of _resume_fichiers (same facts, same order)."""
    kinds = Counter(o["kind"] for o in ctx.objets if o.get("famille_objet") in ("traitement", "donnees"))
    quoi = ", ".join(pl(n, kind_label(k), kind_plural(k)) for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
    port = Counter(o["portabilite"] or "non_determine" for o in ctx.objets if "portabilite" in o)
    mats = Counter(o["detail"].get("materialisation") for o in ctx.objets if o["kind"] == "modele_dbt")
    dsi = [f"{quoi or 'aucun objet'} ; {pl(len(ctx.present), 'relation stockée', 'relations stockées')} ; "
           f"{pl(len(res['liens']), 'lien de dépendance', 'liens de dépendance')}."
           + (f" Matérialisations : {', '.join(f'{fmt_num(n)} {m}' for m, n in sorted(mats.items(), key=lambda kv: str(kv[0])))}." if mats else ""),
           f"Portabilité : {fmt_num(port['traduisible'])} traduisible{'s' if port['traduisible'] > 1 else ''}, "
           f"{fmt_num(port['a_reconstruire'])} à reconstruire (F13), "
           f"{fmt_num(port['non_determine'])} non déterminé{'s' if port['non_determine'] > 1 else ''}, "
           f"{fmt_num(port['portable_tel_quel'])} portable{'s' if port['portable_tel_quel'] > 1 else ''} tel quel."]
    rj = res["rejouabilite"]
    cats = rj.get("categories") or {}
    tested = sum((v["familles"] or 0) for k, v in cats.items() if k not in ("C7",))
    if rj["statut"] == READ and tested:
        dsi.append(f"Rejouabilité : {fmt_num((cats.get('C0') or {}).get('familles') or 0)}/{fmt_num(tested)} modèles acceptés par DuckDB ; "
                   f"{MENTION_REJOUABILITE_FR}.")
    elif rj["statut"] == READ:
        nt = (rj.get("non_testees") or {}).get("familles") or 0
        dsi.append(f"Rejouabilité : {pl(nt, 'modèle traduit', 'modèles traduits')}, non testé{'s' if nt > 1 else ''} par EXPLAIN DuckDB "
                   f"({tr(rj['taux']['familles'].get('raison'))}).")
    daf = [f"Consommation non lue : {COUTS_FICHIERS_FR}."]
    rssi = ["Aucune connexion à une plateforme : fichiers lus localement ; le code est réduit à des empreintes et des comptes.",
            f"{pl(by_code['F01'], 'dépendance externe déclarée', 'dépendances externes déclarées')} (F01) ; écritures hors de la plateforme, "
            "droits et politiques non lus ici."]
    return {"DSI": dsi, "DAF": daf, "RSSI": rssi}


def _resume_fr(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    """French twin of _resume (same facts, same order): the summaries for the DSI, DAF and RSSI boxes of the French pane."""
    if ctx.famille == "fichiers":
        return _resume_fichiers_fr(ctx, res, by_code)
    r, couts, fr = ctx.reads, res["couts"], res["fourchettes"]
    vol = fr["volume"]["valeur"]
    n_obj = sum(o["kind"] in REBUILD_KINDS or o.get("portabilite") == "a_reconstruire" for o in ctx.objets)
    mv = res["mouvements"]
    fraiche = _fraicheur_phrase(res.get("fraicheur"))
    dsi = [f"{pl(len(ctx.present), 'table présente', 'tables présentes')} ({fmt_bytes(vol) if vol is not None else 'volume non lu'}) "
           f"et {pl(n_obj, 'objet')} à reconstruire en plus des tables." + (f" {fraiche}" if fraiche else ""),
           f"Mouvement : {fmt_num(mv['figée'])} figée{'s' if mv['figée'] > 1 else ''}, {fmt_num(mv['vivante'])} vivante{'s' if mv['vivante'] > 1 else ''}, "
           f"{fmt_num(mv['recréée'])} recréée{'s' if mv['recréée'] > 1 else ''}, "
           f"{fmt_num(mv['non_determine'])} non déterminée{'s' if mv['non_determine'] > 1 else ''}."]
    rj = res["rejouabilite"]
    if rj["statut"] == READ and rj["categories"]:
        tested = sum((v["familles"] or 0) for k, v in rj["categories"].items() if k != "C7")
        dsi.append(f"Rejouabilité : {fmt_num((rj['categories'].get('C0') or {}).get('familles') or 0)}/{fmt_num(tested)} familles "
                   f"acceptées par DuckDB ; {MENTION_REJOUABILITE_FR}.")
    else:
        dsi.append(f"Rejouabilité non lue : {rj['raison']}.")

    daf = []
    jours = couts.get("fenetre_jours")
    sur = f"sur {jours} jours" if jours else "sur la fenêtre lue"
    if ctx.plat == "bigquery":
        od, rs = couts["a_la_demande"], couts["reserve"]
        a = (fmt_bytes(od["octets_factures"]["valeur"]) + " facturés à la demande" if od["octets_factures"]["valeur"] is not None
             else "à la demande non lu")
        b = (f"{fmt_num(rs['slot_heures']['valeur'], 1)} heures-slot réservées" if rs["slot_heures"]["valeur"] is not None
             else "réservé non lu")
        daf.append(f"Mesuré {sur} : {a}, {b} ; les deux ne s'additionnent pas.")
        m = od["monnaie"]
        daf.append((f"À la demande, fourchette au prix catalogue : {i18n.usd(m['bas'])} à {i18n.usd(m['haut'])}" if m["nature"] == ESTIMATED
                    else f"À la demande, non estimé : {m['raison']}") + f" ; réservé, non estimé : {CONTRAT_NON_LU_FR}.")
    else:
        c = couts["credits"]
        daf.append(f"Consommation mesurée {sur} : {fmt_num(c['valeur'], 1)} crédits." if c["valeur"] is not None
                   else f"Consommation non lue : {c['raison']}.")
        m = couts["monnaie"]
        if m["nature"] == COMPUTED:
            daf.append(f"Au prix du crédit fourni : {i18n.usd(m['valeur'])}.")
        elif m["nature"] == ESTIMATED:
            daf.append(f"En dollars, fourchette au prix catalogue du {prices.AS_OF} : {i18n.usd(m['bas'])} à {i18n.usd(m['haut'])} ; "
                       f"{CONTRAT_NON_LU_FR}.")
        else:
            daf.append(f"En dollars, non estimé : {m['raison']}.")
    h = res["ne_part_pas"]["historique"]
    if h["octets"]["valeur"] is not None:
        daf.append(f"Historique facturé qui ne part pas avec la copie : {fmt_bytes(h['octets']['valeur'])} sur {pl(h['tables'], 'table')} (F06, F07).")
    else:
        daf.append(f"Historique facturé non lu : {h['octets']['raison']}.")

    rssi = []
    if r.ok("POLICY_REFERENCES", "TAG_REFERENCES", "rowAccessPolicies.list", "COLUMN_FIELD_PATHS") or by_code["F10"]:
        rssi.append(f"{pl(by_code['F10'], 'table ou politique', 'tables ou politiques')} à reconstruire côté sécurité (F10) ; "
                    "la copie exige un principal non filtré.")
    else:
        rssi.append(f"Politiques non lues : {r.why(*POLICY_SOURCES)}.")
    rssi.append(f"{pl(by_code['F01'] + by_code['F02'], 'dépendance externe', 'dépendances externes')} (F01, F02), "
                f"{pl(by_code['F03'], 'écriture')} hors de la plateforme (F03), "
                f"{pl(by_code['F09'], 'partage')} avec des consommateurs externes (F09).")
    if res["f04_lu"]:
        rssi.append(f"{pl(by_code['F04'], 'rôle propriétaire', 'rôles propriétaires')} sans titulaire (F04).")
    elif ctx.plat == "bigquery":
        rssi.append("Rôles propriétaires (F04) : sans objet sur BigQuery, où les droits sont portés par les datasets et les tables.")
    else:
        rssi.append("Rôles propriétaires sans titulaire non déterminés : droits non lus.")
    return {"DSI": dsi, "DAF": daf, "RSSI": rssi}


def _resume(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    if i18n.is_fr():
        return _resume_fr(ctx, res, by_code)
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


def analyze(snapshot: dict, *, credit_price: float | None = None, lang: str = "en") -> dict:
    """/1 or /2 snapshot (model.py) -> scan.json (schema canot.scan/2: /1 + added fields). Pure function.

    lang="en" (default) gives scan.json, unchanged. lang="fr" gives the same result with its human-readable texts in French
    (flags, summaries, formulas, hypotheses, reasons): the French pane of scan.html, never written as a file. Keys, data values
    (statuses, natures, units, movements, kinds) and the order of every list are the same in both languages."""
    with i18n.use(lang):
        return _analyze(snapshot, credit_price)


def _analyze(snapshot: dict, credit_price: float | None) -> dict:
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
        edition = (_t("not determined (Standard or missing privilege)", "non déterminée (Standard ou droit absent)") if edition_missing
                   else _t("not determined", "non déterminée"))

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
                  "regle": _t(f"recreated: DDL less than {RECREEE_JOURS} days old with no later write; live: write less than "
                              f"{VIVANTE_JOURS} days old; frozen: older last write; not determined: writes not read",
                              f"recréée : DDL de moins de {RECREEE_JOURS} jours sans écriture ultérieure ; vivante : écriture de moins de "
                              f"{VIVANTE_JOURS} jours ; figée : dernière écriture plus ancienne ; non déterminée : écritures non lues")}

    hist_tables = [t for t in tables if t["octets_historique"] and t["octets_historique"] > 0
                   or (t["supprimee_le"] and (t["octets_actifs"] or 0) > 0)]
    hist_total = sum((t["octets_historique"] or 0) + ((t["octets_actifs"] or 0) if t["supprimee_le"] else 0) for t in hist_tables)
    if reads.ok(*STORAGE_SOURCES) and any(t["octets_historique"] is not None for t in tables):
        hist = fig(hist_total, nature=COMPUTED, unite="bytes", base=reads.ids(*STORAGE_SOURCES),
                   formule=_t("Time Travel + Fail-safe of present tables, + bytes still billed for dropped tables",
                              "Time Travel + Fail-safe des tables présentes, + octets encore facturés des tables supprimées"))
    elif ctx.famille == "fichiers":
        hist = fig(nature=NOT_READ, unite="bytes", raison=couts_fichiers())
    else:
        hist = fig(nature=NOT_READ, unite="bytes", raison=reads.why(*STORAGE_SOURCES) if not reads.ok(*STORAGE_SOURCES)
                   else _t("history not provided", "historique non fourni"))
    ne.track(_t("Billed history", "Historique facturé"), hist)
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

    ne.add(_t("Your contract rate", "Votre tarif contractuel"),
           contrat_non_lu() if not (credit_price and plat == "snowflake")
           else _t("credit price provided, other items not read", "prix du crédit fourni, autres postes non lus"))
    ne.add(_t("Destination storage, running both platforms in parallel, operations after the exit",
              "Stockage de destination, fonctionnement en parallèle des deux plateformes, exploitation après la sortie"),
           _t("depend on your contract and your architecture", "dépendent de votre contrat et de votre architecture"))
    ne.add(_t("Identical results of translated queries", "Résultats identiques des requêtes traduites"),
           _t(f"{MENTION_REJOUABILITE}: translated to DuckDB, not compared; a canot drill compares the results",
              f"{MENTION_REJOUABILITE_FR} ; traduites vers DuckDB, non comparées ; un canot drill compare les résultats"))
    ne.add(_t("Cost projections provided by the platform", "Projections de coût fournies par la plateforme"),
           _t("not used: they cannot be verified from the scan", "non utilisées, car elles ne peuvent pas être vérifiées depuis le scan"))
    if plat == "bigquery":
        ne.add(_t("BigQuery free tier", "Gratuité BigQuery"),
               _t("shared by the billing account, cannot be attributed per project",
                  "partagée par le compte de facturation, ne peut pas être attribuée par projet"))
        ne.add(_t("Reads through the Storage Read API", "Lectures par la Storage Read API"),
               _t("missing from INFORMATION_SCHEMA.JOBS", "absentes d'INFORMATION_SCHEMA.JOBS"))
        if row_policies:
            ne.add(_t("Billed bytes of queries under a row access policy", "Octets facturés des requêtes sous politique d'accès aux lignes"),
                   _t("NULL value in INFORMATION_SCHEMA.JOBS", "valeur NULL dans INFORMATION_SCHEMA.JOBS"))

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
