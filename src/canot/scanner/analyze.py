"""Analyse pure du scan : snapshot (contrat de model.py) -> scan.json (spec §2).

Aucune E/S, aucune horloge : la date de référence est snapshot["as_of"]. Chaque chiffre dérivé passe par `fig` ;
une valeur non lue reste None. Les drapeaux disent ce qui est observé et ce qui ne l'est pas, jamais quoi faire.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .. import prices
from ..scan import NON_PORTABLE, THROUGHPUT_MBPS
from .model import NATURES, bucket_only, fig

SCHEMA = "canot.scan/1"
ETIQUETTE = "À examiner"
LIBELLES = {
    "F01": "Dépendance externe",
    "F02": "Dépendance externe cachée",
    "F03": "Écriture hors de la plateforme",
    "F04": "Rôle propriétaire sans titulaire",
    "F05": "Stockage sans lecture observée",
    "F06": "Historique payé non transférable",
    "F07": "Table supprimée encore facturée",
    "F08": "SQL dynamique",
    "F09": "Consommateurs externes",
    "F10": "Sécurité à reconstruire",
    "F11": "Chaîne planifiée et ses échecs",
    "F12": "Lecture incomplète",
}
KIND_LABELS = {
    "vue": "vue", "vue_materialisee": "vue matérialisée", "fonction": "fonction", "procedure": "procédure", "tache": "tâche",
    "flux": "flux", "pipe": "pipe", "stage": "stage", "base_importee": "base importée", "partage": "partage",
    "table_dynamique": "table dynamique", "modele_ml": "modèle ML", "politique_masquage": "politique de masquage",
    "politique_lignes": "politique de lignes", "requete_planifiee": "requête planifiée", "connexion": "connexion",
    "integration": "intégration", "reservation": "réservation", "etiquette": "étiquette",
}
# objets à reconstruire sur la destination (comptés dans le travail de reprise)
REBUILD_KINDS = ("vue", "vue_materialisee", "fonction", "procedure", "tache", "flux", "pipe", "stage", "table_dynamique",
                 "modele_ml", "politique_masquage", "politique_lignes", "requete_planifiee", "connexion", "integration")
SCHEDULED_KINDS = ("tache", "requete_planifiee", "pipe", "table_dynamique", "flux")
POLICY_KINDS = ("politique_masquage", "politique_lignes", "etiquette")
SYSTEM_IMPORTS = ("SNOWFLAKE",)  # base système présente dans tout compte Snowflake : pas une dépendance du client

VIVANTE_JOURS = 30   # écriture observée dans les 30 derniers jours
RECREEE_JOURS = 7    # DDL dans les 7 derniers jours, sans écriture postérieure
HORIZON_JOURS = {"snowflake": 365, "bigquery": 180}
CREDITS_PAR_HEURE = 1.0  # entrepôt XS
TIB = 2 ** 40
PRICE_SOURCES = (prices.SNOWFLAKE_SOURCE, prices.BIGQUERY_SOURCE)
CONTRAT_NON_LU = "prix de votre contrat non lu"
MENTION_REJOUABILITE = "accepté ≠ résultat identique"

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
}
TABLE_SOURCES = ("TABLES", "TABLE_OPTIONS", "tables.get")
STORAGE_SOURCES = ("TABLE_STORAGE_METRICS", "TABLE_STORAGE", "tables.get")
GRANT_SOURCES = ("GRANTS_TO_USERS", "GRANTS_TO_ROLES")
POLICY_SOURCES = ("POLICY_REFERENCES", "TAG_REFERENCES", "rowAccessPolicies.list", "COLUMN_FIELD_PATHS")
WRITE_SOURCES = ("TABLES", "TABLE_DML_HISTORY", "ACCESS_HISTORY", "tables.get", "JOBS")
CODE_SOURCES = ("COLUMNS", "VIEWS", "FUNCTIONS", "PROCEDURES", "ROUTINES", "TASKS", "PIPES", "transferConfigs.list")
NON_VUS = {
    "ACCESS_HISTORY": ["consommateurs de partages", "RESULT_SCAN"],
    "TABLE_QUERY_PRUNING_HISTORY": ["consommateurs de partages", "résultats servis par le cache", "requêtes résolues par les métadonnées"],
    "JOBS": ["lectures par l'API Storage Read", "requêtes lancées depuis d'autres projets", "consommateurs Analytics Hub"],
}
# ce que limite une lecture non lue (F12)
IMPACTS = {
    "ACCESS_HISTORY": "lectures et écritures par objet (F05)",
    "TABLE_QUERY_PRUNING_HISTORY": "repli des lectures par table (F05)",
    "TABLES": "inventaire des tables", "COLUMNS": "types de colonnes non portables",
    "TABLE_STORAGE_METRICS": "octets actifs et historique (F05, F06, F07, fourchettes)",
    "TABLE_STORAGE": "volumes par table (F05, F06, fourchettes)", "tables.get": "volumes par table (F05, F06, fourchettes)",
    "STORAGE_USAGE": "stockage moyen facturé", "METERING_DAILY_HISTORY": "crédits par service",
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
    "SEARCH_OPTIMIZATION_HISTORY": "crédits de la search optimization",
    "QUERY_ACCELERATION_HISTORY": "crédits de la query acceleration",
    "QUERY_HISTORY": "familles de requêtes, rejouabilité, écritures observées (F03)",
    "POLICY_REFERENCES": "politiques appliquées (F10)", "TAG_REFERENCES": "étiquettes (F10)",
    "GRANTS_TO_ROLES": "rôles propriétaires sans titulaire (F04)", "GRANTS_TO_USERS": "rôles propriétaires sans titulaire (F04)",
    "USERS": "types d'utilisateurs (personne ou service)", "SESSIONS": "outils clients", "SHARES": "consommateurs externes (F09)",
    "JOBS": "lectures par table (F05), familles de requêtes, coûts à la demande et réservé",
    "JOBS_BY_ORGANIZATION": "requêtes lancées depuis d'autres projets", "JOBS_TIMELINE": "profil de charge",
    "TABLE_OPTIONS": "emplacements des tables externes (F01)", "COLUMN_FIELD_PATHS": "policy tags (F10)",
    "SCHEMATA_OPTIONS": "options des datasets", "SCHEMATA_LINKS": "datasets liés, Analytics Hub (F09)",
    "SHARED_DATASET_USAGE": "consommateurs Analytics Hub (F09)", "PARTITIONS": "partitions",
    "MATERIALIZED_VIEWS": "vues matérialisées", "RESERVATIONS": "capacité réservée",
    "CAPACITY_COMMITMENTS": "engagements de capacité", "RESERVATIONS_TIMELINE": "utilisation des réservations",
    "datasets.list": "inventaire des datasets", "datasets.get": "datasets liés et externes, droits des datasets (F01, F09)",
    "transferConfigs.list": "requêtes planifiées (F11)", "rowAccessPolicies.list": "politiques de lignes (F10)",
    "connections.list": "connexions externes (F01)", "models.list": "modèles ML",
    "SHOW WAREHOUSES": "taille et suspension des entrepôts", "SHOW DYNAMIC TABLES": "tables dynamiques (F11)",
    "SHOW ICEBERG TABLES": "tables Iceberg (F01)", "SHOW EXTERNAL TABLES": "emplacements des tables externes (F01)",
    "SHOW STREAMS": "flux (F11)", "QUERY_HISTORY × SESSIONS": "outil par famille de requêtes",
    "transferConfigs.runs.list": "exécutions et échecs des requêtes planifiées (F11)", "tables.list": "inventaire des tables",
}
# une lecture non lue ne limite rien si sa remplaçante a été lue
FALLBACKS = {"TABLE_QUERY_PRUNING_HISTORY": ("ACCESS_HISTORY",), "TABLE_STORAGE": ("tables.get",), "tables.get": ("TABLE_STORAGE",),
             # vues régionales refusées : l'API tables.list/get donne inventaire, types, options, définitions et policy tags
             **{v: ("tables.get",) for v in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS")}}

# --- formats (partagés avec render.py) ------------------------------------------------------------------


def fr_num(x, dec: int = 0) -> str:
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _short_num(v: float) -> str:
    s = fr_num(v, 0 if abs(v) >= 100 else 1)
    return s[:-2] if s.endswith(",0") else s


def fr_bytes(n) -> str:
    if n is None:
        return "non lu"
    for unit, f in (("To", 1e12), ("Go", 1e9), ("Mo", 1e6), ("ko", 1e3)):
        if abs(n) >= f:
            return f"{_short_num(n / f)} {unit}"
    return f"{int(n)} o"


def parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fr_date(v) -> str:
    d = parse_dt(v)
    return d.strftime("%d/%m/%Y") if d else "date inconnue"


def pl(n, singular: str, plural: str | None = None) -> str:
    return f"{fr_num(n)} {singular if abs(n) < 2 else (plural or singular + 's')}"


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# --- hygiène : rien de sensible ne traverse l'analyse ------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>)]+", re.I)
_DENY_KEYS = {"sql", "texte", "text", "definition", "body", "corps", "query", "erreur", "error", "message", "email",
              "login", "ip", "bind", "binds", "password", "secret", "token", "environment"}


def _bucket(uri: str) -> str:
    """Compartiment seul ; pour http(s), l'hôte sans schéma : un emplacement cité n'est pas un lien."""
    b = bucket_only(uri) or ""
    return re.sub(r"(?i)^https?://", "", b)


def scrub(v):
    """URI réduites au compartiment, adresses e-mail masquées."""
    if not isinstance(v, str):
        return v
    v = _URI.sub(lambda m: _bucket(m.group(0)), v)
    return _EMAIL.sub("[masqué]", v)


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


# --- lectures --------------------------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9_]+")


def _tokens(vue: str) -> set[str]:
    vue = re.sub(r"\([^)]*\)", " ", vue)  # « GRANTS_TO_ROLES (hiérarchie des rôles) » : le libellé ne compte pas
    out = set()
    for m in _WORD.finditer(vue):
        w, nxt = m.group(0), vue[m.end():m.end() + 2]
        if not w.isupper() and nxt[:1] == "." and nxt[1:2].islower():
            continue  # « tables.get » : appel d'API, pas la vue TABLES
        out.add(w.upper())
    return out


def _match(vue: str, name: str) -> bool:
    """Un nom avec point ou espace se cherche tel quel ; sinon comme identifiant de vue (ni SHOW, ni jointure « × »)."""
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
        n = sum(lec["statut"] == "lu" for lec in found)
        return "lu" if n == len(found) else ("partiel" if n else "non_lu")

    def ok(self, *names) -> bool:
        return self.status(*names) in ("lu", "partiel")

    def ids(self, *names) -> list[str]:
        return sorted(lec["id"] for lec in self.find(*names) if lec["statut"] == "lu")

    def why(self, *names) -> str:
        failed = [lec for lec in self.find(*names) if lec["statut"] != "lu"]
        if not failed:
            return f"{names[0]} : lecture non tentée"
        lec = failed[0]
        return f"{short_vue(lec['vue'])} non lue : {lec['raison'] or 'raison non transmise'}"

    def partial(self, *names) -> bool:
        """Une lecture lue peut n'être que partielle (fenêtres ou appels en échec) : la collecte le dit dans sa raison."""
        return self.status(*names) == "partiel" or any((lec["raison"] or "").startswith("partiel")
                                                       for lec in self.find(*names) if lec["statut"] == "lu")

    def window(self, *names) -> tuple[datetime | None, datetime | None]:
        du = [parse_dt((lec["fenetre"] or {}).get("du")) for lec in self.find(*names) if lec["statut"] == "lu"]
        au = [parse_dt((lec["fenetre"] or {}).get("au")) for lec in self.find(*names) if lec["statut"] == "lu"]
        du, au = [d for d in du if d], [d for d in au if d]
        return (min(du) if du else None, max(au) if au else None)


def _lecture(lec: dict) -> dict:
    statut = "lu" if lec.get("statut") == "lu" else "non_lu"
    fen = lec.get("fenetre") or None
    return {"id": str(lec.get("id")), "vue": scrub(str(lec.get("vue") or "")), "statut": statut,
            "raison": scrub(lec.get("raison")),
            "lignes": lec.get("lignes"), "latence_max_h": lec.get("latence_max_h"),
            "fenetre": {"du": fen.get("du"), "au": fen.get("au")} if isinstance(fen, dict) else None}


def _impact_key(vue: str) -> str | None:
    return next((k for k in IMPACTS if _match(vue, k)), None)


# --- tables, objets, familles ------------------------------------------------------------------------------


def _non_portables(cols) -> list[dict]:
    return [{"nom": c.get("nom"), "type": c.get("type")} for c in cols or []
            if any(k in str(c.get("type") or "").upper() for k in NON_PORTABLE)]


def _mouvement(t: dict, as_of: datetime) -> tuple[str | None, str]:
    if t.get("supprimee_le"):
        return None, f"supprimée le {fr_date(t['supprimee_le'])}"
    ddl, w, nw = parse_dt(t.get("derniere_ddl")), parse_dt(t.get("derniere_ecriture")), t.get("n_ecritures")
    if ddl and (as_of - ddl).days < RECREEE_JOURS and (w is None or (w - ddl).total_seconds() <= 3600):
        unknown = w is None and nw is None
        return "recréée", f"dernière DDL le {fr_date(ddl)}, " + ("écritures non lues" if unknown else "sans écriture postérieure")
    if w and (as_of - w).days < VIVANTE_JOURS:
        return "vivante", f"dernière écriture le {fr_date(w)}"
    if w:
        return "figée", f"dernière écriture le {fr_date(w)}"
    if nw == 0 and ddl and (as_of - ddl).days >= VIVANTE_JOURS:
        return "figée", f"aucune écriture observée, dernière DDL le {fr_date(ddl)}"
    return None, "écritures non lues"


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
        # n_pruning : repli Snowflake sans ACCESS_HISTORY ; partage : lectures des abonnés (BigQuery SHARED_DATASET_USAGE)
        "lectures": {"n": t.get("n_lectures"), "derniere": t.get("derniere_lecture"),
                     "n_pruning": detail.get("requetes_pruning_n"), "partage": detail.get("lectures_partage_n")},
        "ecritures": {"n": t.get("n_ecritures"), "derniere": t.get("derniere_ecriture")},
        "mouvement": mv, "mouvement_preuve": preuve, "detail": detail, "drapeaux": [],
    }


def _objet(o: dict) -> dict:
    return {
        "kind": o.get("kind"), "id": scrub(str(o.get("id"))), "schema": o.get("schema"), "langage": o.get("langage"),
        "proprietaire": o.get("proprietaire"), "externe": bool(o.get("externe")),
        "dependances_externes": sorted({d for d in (_reduce_uri(x) for x in o.get("dependances_externes") or []) if d}),
        "reference": sorted({scrub(str(r)) for r in o.get("reference") or []}), "etat": scrub(o.get("etat")),
        "planification": scrub(o.get("planification")), "executions_n": o.get("executions_n"), "echecs_n": o.get("echecs_n"),
        "derniere_execution": o.get("derniere_execution"), "sql_dynamique": o.get("sql_dynamique"),
        "detail": _safe_detail(o.get("detail")), "drapeaux": [],
    }


FAMILLE_KEYS = ("empreinte", "hash_plateforme", "n_exec", "temps_ms", "credits", "octets", "app", "outil", "entrepot",
                "categorie", "ecriture_externe")


def _famille(f: dict) -> dict:
    out = {k: scrub(f.get(k)) for k in FAMILLE_KEYS}
    out["ecriture_externe"] = bool(f.get("ecriture_externe"))
    out["tables"] = sorted(scrub(str(x)) for x in f.get("tables") or [])
    out["fonctions"] = sorted(scrub(str(x)) for x in f.get("fonctions") or [])
    out["sql_normalise"] = scrub(f.get("sql_normalise"))
    uris = f.get("compartiments") or f.get("uris_export") or (f.get("detail") or {}).get("export") or []  # replay : detail.export
    out["compartiments"] = sorted({d for d in (_reduce_uri(u) for u in uris) if d})
    return out


# --- drapeaux --------------------------------------------------------------------------------------------


def _flag(code, cible_type, cible, texte, base, preuve=None, confiance=None) -> dict:
    return {"code": code, "libelle": LIBELLES[code], "etiquette": ETIQUETTE, "cible_type": cible_type, "cible": cible,
            "texte": texte, "preuve": preuve or {}, "base": sorted(set(base)), "confiance": confiance}


class _Ctx:
    def __init__(self, snap, as_of, reads, tables, objets, familles):
        self.snap, self.plat, self.as_of, self.reads = snap, snap.get("plateforme"), as_of, reads
        self.tables, self.objets, self.familles = tables, objets, familles
        self.present = [t for t in tables if not t["supprimee_le"]]

    def kind_ids(self, kind) -> list[str]:
        return self.reads.ids(*KIND_SOURCES.get(kind, ()))


def _external(ctx: _Ctx) -> dict[str, tuple[str, str, list[str]]]:
    """id -> (cible_type, description, emplacements) pour tout ce qui dépend directement de l'extérieur (F01)."""
    out = {}
    for t in ctx.tables:
        if t["supprimee_le"]:
            continue
        if t["type"] in ("EXTERNE", "ICEBERG") or t["externe_uri"]:
            loc = [t["externe_uri"]] if t["externe_uri"] else []
            what = "Table Iceberg : données sur un volume externe" if t["type"] == "ICEBERG" else "Table externe : données hors de la plateforme"
            out[t["id"]] = ("table", what, loc)
    for o in ctx.objets:
        k, deps = o["kind"], o["dependances_externes"]
        if k == "base_importee" and o["id"].upper() in SYSTEM_IMPORTS:
            continue
        exports = bool(o["detail"].get("ecriture_externe") or o["detail"].get("exporte"))
        if k == "base_importee":
            out[o["id"]] = ("objet", "Base importée d'un partage entrant", deps)
        elif k in ("connexion", "integration") or o["externe"] or (deps and not exports):  # une destination d'export relève de F03
            what = {"stage": "Stage externe", "fonction": "Fonction externe : appelle un service hors de la plateforme",
                    "connexion": "Connexion vers un service externe", "integration": "Intégration avec un service externe"}
            out[o["id"]] = ("objet", what.get(k, f"{cap(KIND_LABELS.get(k, k))} : dépendance externe déclarée"), deps)
    return out


def _f01_f02(ctx: _Ctx, ext: dict) -> list[dict]:
    flags = []
    for i, (ctype, what, loc) in sorted(ext.items()):
        kind = next((o["kind"] for o in ctx.objets if o["id"] == i), None)
        base = ctx.reads.ids(*TABLE_SOURCES) if ctype == "table" else ctx.kind_ids(kind)
        where = f" ({', '.join(loc)})" if loc else ("" if kind == "base_importee" else " (emplacement non lu)")
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
        texte = (f"Dépend de {path[-1]}, hors de la plateforme, par {pl(len(path) - 1, 'niveau', 'niveaux')} : {chemin}"
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
            texte = (f"{cap(KIND_LABELS[o['kind']])} qui écrit hors de la plateforme (déclaré dans sa définition) ; "
                     f"destination : {', '.join(loc) if loc else 'non lue'}.")
            flags.append(_flag("F03", "objet", o["id"], texte, ctx.kind_ids(o["kind"]), {"mode": "déclaré", "emplacements": loc}))
    stage_locs = {o["id"].upper(): o["dependances_externes"] for o in ctx.objets if o["kind"] == "stage"}
    for f in ctx.familles:
        if not f["ecriture_externe"]:
            continue
        # replay peut nommer le stage plutôt que le compartiment : on remonte à l'emplacement du stage s'il est connu
        loc = sorted({x for c in f["compartiments"] for x in (stage_locs.get(c.lstrip("@").upper()) or [c])})
        qui = f["outil"] or f["app"]
        texte = (f"Écriture hors de la plateforme observée : {pl(f['n_exec'] or 0, 'exécution')}"
                 + (f" ({qui})" if qui else "") + f" ; destination : {', '.join(loc) if loc else 'non lue'}.")
        flags.append(_flag("F03", "famille", (f["empreinte"] or "")[:12], texte, ctx.reads.ids("QUERY_HISTORY", "JOBS"),
                           {"mode": "observé", "emplacements": loc, "n_exec": f["n_exec"], "tables": f["tables"][:5]}))
    for o in ctx.objets:  # déchargements vers un stage externe vus dans ACCESS_HISTORY
        n = o["detail"].get("ecritures_observees_n")
        if o["kind"] == "stage" and o["externe"] and isinstance(n, int) and n > 0:
            loc = o["dependances_externes"]
            texte = f"Écriture hors de la plateforme observée : {pl(n, 'écriture')} vers ce stage externe ({', '.join(loc) if loc else 'emplacement non lu'})."
            flags.append(_flag("F03", "objet", o["id"], texte, ctx.kind_ids("stage") + ctx.reads.ids("ACCESS_HISTORY"),
                               {"mode": "observé", "emplacements": loc, "n_exec": n}))
    return flags


def _has_holder(roles: dict):
    """Titulaire = rôle accordé à un utilisateur, directement ou par un rôle parent. None = non déterminé."""
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
    """(drapeaux, rôles non résolus). Ne devine pas : sans GRANTS_TO_USERS et GRANTS_TO_ROLES lus, rien n'est conclu."""
    roles_list = (ctx.snap.get("droits") or {}).get("roles")
    if not (roles_list is not None and all(ctx.reads.status(n) == "lu" for n in GRANT_SOURCES)):
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
        texte = (f"Le rôle {role} possède {pl(len(ids), 'objet')} et n'est accordé à aucun utilisateur, "
                 f"ni directement ni par un autre rôle.")
        flags.append(_flag("F04", "role", role, texte, ctx.reads.ids(*GRANT_SOURCES), {"objets_n": len(ids), "exemples": ids[:5]}))
    return flags, unresolved


def _read_sources(ctx: _Ctx) -> list[dict]:
    """Sources des lectures par table, de la plus sûre à la moins sûre ; seules celles qui ont été lues."""
    r = ctx.reads
    cands = {
        "snowflake": [("ACCESS_HISTORY", "ACCESS_HISTORY", "haute", lambda t: t["lectures"]["n"]),
                      ("TABLE_QUERY_PRUNING_HISTORY", "TABLE_QUERY_PRUNING_HISTORY", "moyenne",
                       lambda t: t["lectures"]["n_pruning"] if t["lectures"]["n_pruning"] is not None else t["lectures"]["n"])],
        "bigquery": [("JOBS", "INFORMATION_SCHEMA.JOBS", "moyenne", lambda t: t["lectures"]["n"])],
    }.get(ctx.plat, [])
    out = []
    for name, label, conf, get in cands:
        if not r.ok(name):
            continue
        non_vus = list(NON_VUS[name])
        if name == "JOBS" and r.status("JOBS_BY_ORGANIZATION") == "lu":
            non_vus.remove("requêtes lancées depuis d'autres projets")
        du, au = r.window(name)
        partiel = r.partial(name)
        lat = [lec["latence_max_h"] for lec in r.find(name) if lec["statut"] == "lu" and lec["latence_max_h"] is not None]
        out.append({"vue": name, "label": label, "confiance": "basse" if partiel or not du else conf, "non_vus": non_vus,
                    "ids": r.ids(name), "du": du, "au": au, "partiel": partiel, "get": get, "latence_h": max(lat, default=None)})
    return out


def _source_for(t: dict, sources: list[dict]) -> tuple[dict | None, int | None]:
    for s in sources:
        n = s["get"](t)
        if n is not None:
            return s, n
    return None, None


def _too_recent(t: dict, src: dict, as_of: datetime) -> bool:
    """Table vue seulement en temps réel, ou créée depuis moins que la latence de la source des lectures : son absence de
    lecture n'est pas encore observable."""
    if "recente" in t["flags"]:
        return True
    created = parse_dt(t["detail"].get("creee_le"))
    lat = src.get("latence_h")
    return bool(created and lat is not None and created > (src["au"] or as_of) - timedelta(hours=lat))


def _f05(ctx: _Ctx, sources: list[dict], shared: dict[str, int]) -> list[dict]:
    """Aucune source lue, ou valeur inconnue pour la table : F05 se tait (F12 le dit). Table trop récente : F05 se tait aussi."""
    horizon = HORIZON_JOURS.get(ctx.plat, 365)
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
            fen = f"du {fr_date(src['du'])} au {fr_date(src['au'])}"
        else:
            jours, fen = None, "(fenêtre non transmise)"
        propre = f"{fr_bytes(octets)} stockés" if not t.get("clone_de") else f"Clone ou snapshot de {t['clone_de']} (seuls ses octets propres sont facturés)"
        texte = (f"{propre}. Aucune lecture observée dans {src['label']} {fen} ; "
                 f"non vus : {', '.join(src['non_vus'])}.")
        if jours is not None and jours < horizon - 1:
            texte += f" Fenêtre de {jours} jours, plus courte que {horizon} : un traitement trimestriel ou annuel peut ne pas y figurer."
        if src["partiel"]:
            texte += " Certaines fenêtres de lecture n'ont pas été lues."
        ddl = parse_dt(t["derniere_ddl"])
        if ddl and src["du"] and ddl > src["du"]:
            texte += f" Dernière DDL le {fr_date(ddl)}."
        if shared.get(t["id"]):
            texte += f" Partagée avec {pl(shared[t['id']], 'consommateur externe', 'consommateurs externes')} : leurs lectures ne sont pas vues."
        preuve = {"octets_actifs": octets, "source": src["label"], "du": src["du"].strftime("%Y-%m-%d") if src["du"] else None,
                  "au": src["au"].strftime("%Y-%m-%d") if src["au"] else None, "jours": jours, "non_vus": src["non_vus"]}
        flags.append(_flag("F05", "table", t["id"], texte, src["ids"] + ctx.reads.ids(*STORAGE_SOURCES), preuve, src["confiance"]))
    return flags


def _f06_f07(ctx: _Ctx) -> list[dict]:
    base = ctx.reads.ids(*STORAGE_SOURCES)
    flags = []
    for t in ctx.tables:
        h, a = t["octets_historique"], t["octets_actifs"]
        if "recente" in t["flags"]:  # historique facturé pas encore dans ACCOUNT_USAGE : aucune conclusion
            continue
        if t["supprimee_le"]:
            if h is None and a is None:
                continue
            total = (h or 0) + (a or 0)
            if total > 0:
                texte = f"Supprimée le {fr_date(t['supprimee_le'])} ; {fr_bytes(total)} encore facturés (Time Travel et Fail-safe)."
                flags.append(_flag("F07", "table", t["id"], texte, base + ctx.reads.ids("TABLES"),
                                   {"supprimee_le": t["supprimee_le"], "octets": total}))
        elif h and h > 0:
            texte = (f"{fr_bytes(h)} d'historique facturé (Time Travel et Fail-safe)"
                     + (f", rétention {fr_num(t['retention_jours'])} jours" if t["retention_jours"] is not None else "")
                     + (f", pour {fr_bytes(a)} actifs" if a is not None else "") + " ; cet historique ne part pas avec la copie.")
            flags.append(_flag("F06", "table", t["id"], texte, base,
                               {"octets_historique": h, "octets_actifs": a, "retention_jours": t["retention_jours"]}))
    return flags


def _f08(ctx: _Ctx) -> list[dict]:
    return [_flag("F08", "objet", o["id"],
                  f"{cap(KIND_LABELS.get(o['kind'], o['kind']))}" + (f" en {o['langage']}" if o["langage"] else "")
                  + " : construit du SQL à l'exécution ; les objets touchés ne se lisent pas dans le code.",
                  ctx.kind_ids(o["kind"]), {"langage": o["langage"]})
            for o in ctx.objets if o["sql_dynamique"] is True]


CONSUMER_KEYS = (("comptes_cibles", "compte cible", "comptes cibles"), ("comptes_cibles_n", "compte cible", "comptes cibles"),
                 ("projets_consommateurs_n", "projet consommateur", "projets consommateurs"), ("abonnes", "abonné", "abonnés"),
                 ("consommateurs", "consommateur", "consommateurs"))


def _consumers(o: dict) -> tuple[int | None, str]:
    d = o["detail"]
    for k, one, many in CONSUMER_KEYS:
        if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool):
            return int(d[k]), pl(int(d[k]), one, many)
    return None, "consommateurs externes (nombre non lu)"


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
        quoi = (pl(len(o["reference"]), "objet partagé", "objets partagés") if o["reference"]
                else f"base {o['detail']['base']}" if o["detail"].get("base") else "objets partagés non lus")
        texte = f"Partage vers {qui} ; {quoi}. Les lectures des consommateurs ne sont pas vues par ce scan."
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
            parts.append(f"politiques : {', '.join(t['politiques'])}")
        if t["etiquettes_n"]:
            parts.append(pl(t["etiquettes_n"], "étiquette"))
        texte = (f"{cap(' ; '.join(parts))}. Elles ne partent pas avec la copie ; "
                 f"la copie demande un principal non filtré par ces politiques.")
        flags.append(_flag("F10", "table", t["id"], texte, base, {"politiques": t["politiques"], "etiquettes_n": t["etiquettes_n"]}))
    for o in ctx.objets:
        if o["kind"] not in POLICY_KINDS:
            continue
        what = cap(KIND_LABELS[o["kind"]])
        texte = f"{what} appliquée à {pl(len(o['reference']), 'objet')} ; elle ne part pas avec la copie"
        texte += "." if o["kind"] == "etiquette" else " ; la copie demande un principal non filtré."
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
        head = [cap(KIND_LABELS[o["kind"]])]
        if o["planification"]:
            head.append(f"planification {o['planification']}")
        if o["etat"]:
            head.append(f"état {o['etat']}")
        if o["kind"] == "flux" and o["executions_n"] is None:
            runs = f"sur {', '.join(o['reference'])}" if o["reference"] else "objet suivi non lu"
        elif o["executions_n"] is None:
            runs = "exécutions non lues"
        else:
            runs = pl(o["executions_n"], "exécution") + ", " + (pl(o["echecs_n"], "échec") if o["echecs_n"] is not None else "échecs non lus")
        if o["derniere_execution"]:
            runs += f", dernière le {fr_date(o['derniere_execution'])}"
        ch = chain(o)
        texte = ", ".join(head) + f" ; {runs}" + (f" ; chaîne : {' → '.join(ch)}" if len(ch) > 1 else "") + "."
        flags.append(_flag("F11", "objet", i, texte, ctx.kind_ids(o["kind"]),
                           {"chaine": ch, "executions_n": o["executions_n"], "echecs_n": o["echecs_n"], "etat": o["etat"]}))
    return flags


def _f12(ctx: _Ctx, edition_missing: bool, unresolved_owners: list[str], sources: list[dict]) -> list[dict]:
    r, flags = ctx.reads, []
    for lec in r.all:
        key = _impact_key(lec["vue"])
        if lec["statut"] == "lu":
            if (lec["raison"] or "").startswith("partiel"):
                impact = IMPACTS.get(key, "les conclusions qui en dépendent")
                lec["limite"] = impact
                flags.append(_flag("F12", "lecture", lec["id"], f"{short_vue(lec['vue'])} lue en partie ({lec['raison']}) ; limite : {impact}.",
                                   [lec["id"]], {"vue": lec["vue"], "raison": lec["raison"]}))
            continue
        if key in FALLBACKS and r.ok(*FALLBACKS[key]):
            lec["limite"] = f"aucune : {FALLBACKS[key][0]} lue à la place"
            continue
        if key == "ACCESS_HISTORY":
            impact = ("lectures par objet : F05 calculé avec TABLE_QUERY_PRUNING_HISTORY, confiance moyenne"
                      if r.ok("TABLE_QUERY_PRUNING_HISTORY") else "lectures par objet : F05 non calculé")
            if edition_missing:
                impact += " ; édition non déterminée (Standard ou droit absent)"
        else:
            impact = IMPACTS.get(key, "les conclusions qui en dépendent")
        lec["limite"] = impact
        texte = f"{short_vue(lec['vue'])} non lue ({lec['raison'] or 'raison non transmise'}) ; limite : {impact}."
        flags.append(_flag("F12", "lecture", lec["id"], texte, [], {"vue": lec["vue"], "raison": lec["raison"]}))
    if ctx.plat == "snowflake" and not r.find(*GRANT_SOURCES):
        flags.append(_flag("F12", "lecture", "droits",
                           f"Droits non lus (niveau {ctx.snap.get('niveau_droits')}) ; limite : rôles propriétaires sans titulaire (F04).", []))
    elif r.find(*GRANT_SOURCES) and all(r.status(n) == "lu" for n in GRANT_SOURCES) and (ctx.snap.get("droits") or {}).get("roles") is None:
        flags.append(_flag("F12", "lecture", "droits", "Droits lus mais non transmis à l'analyse ; limite : rôles propriétaires sans titulaire (F04).", []))
    if unresolved_owners:
        flags.append(_flag("F12", "lecture", "roles",
                           f"{pl(len(unresolved_owners), 'rôle propriétaire', 'rôles propriétaires')} sans octroi lu ; "
                           f"limite : rôles propriétaires sans titulaire (F04).", r.ids(*GRANT_SOURCES), {"roles": unresolved_owners[:10]}))
    if sources:
        unknown = [t["id"] for t in ctx.present if _source_for(t, sources)[0] is None and t["type"] not in ("EXTERNE", "TEMPORAIRE")]
        if unknown:
            flags.append(_flag("F12", "lecture", "lectures_par_table",
                               f"Lectures non déterminées pour {pl(len(unknown), 'table')} ; limite : stockage sans lecture observée (F05).",
                               [i for s in sources for i in s["ids"]], {"tables": unknown[:10]}))
    # une fonction externe n'a pas de corps SQL : son sql_dynamique vide n'est pas un code non lu
    hidden = [o["id"] for o in ctx.objets if o["kind"] in ("procedure", "fonction") and o["sql_dynamique"] is None
              and not (o["kind"] == "fonction" and o["externe"])]
    if hidden:
        flags.append(_flag("F12", "lecture", "code_des_routines",
                           f"Code non lu pour {pl(len(hidden), 'routine')} (filtré par droits ou non transmis) ; limite : SQL dynamique (F08).",
                           ctx.reads.ids("PROCEDURES", "FUNCTIONS", "ROUTINES"), {"routines": hidden[:10]}))
    return flags


# --- chiffres --------------------------------------------------------------------------------------------


class _NonEstime:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, sujet: str, raison: str) -> None:
        item = {"sujet": sujet, "raison": raison}
        if item not in self.items:
            self.items.append(item)

    def track(self, sujet: str, f: dict) -> dict:
        if f["nature"] in ("non_lu", "non_estimé"):
            self.add(sujet, f["raison"])
        return f


def _measured(value, reads: _Reads, names: tuple, unite: str, conf=None) -> dict:
    if value is None or not reads.ok(*names):
        return fig(nature="non_lu", unite=unite, raison=reads.why(*names) if not reads.ok(*names) else "non transmis par la collecte")
    return fig(value, nature="mesuré", unite=unite, base=reads.ids(*names),
               confiance=conf or ("moyenne" if reads.status(*names) == "partiel" else None))


def _usd_from_credits(credits: dict, credit_price: float | None, base: list[str]) -> dict:
    c = credits["valeur"] if credits["nature"] != "estimé" else None
    lo, hi = (credits["bas"], credits["haut"]) if credits["nature"] == "estimé" else (c, c)
    if lo is None:
        return fig(nature="non_estimé", unite="USD", raison="crédits non lus")
    if credit_price:
        if credits["nature"] == "estimé":
            return fig(nature="estimé", unite="USD", bas=round(lo * credit_price, 2), haut=round(hi * credit_price, 2), base=base,
                       formule="crédits × prix du crédit fourni", hypotheses=[f"prix du crédit fourni : {credit_price} $"])
        return fig(round(c * credit_price, 2), nature="calculé", unite="USD", base=base, formule="crédits × prix du crédit fourni",
                   hypotheses=[f"prix du crédit fourni : {credit_price} $"])
    pmin, pmax = min(prices.SNOWFLAKE_CREDIT_USD.values()), max(prices.SNOWFLAKE_CREDIT_USD.values())
    return fig(nature="estimé", unite="USD", bas=round(lo * pmin, 2), haut=round(hi * pmax, 2), base=base, confiance="basse",
               formule="crédits × prix catalogue du crédit, du plus bas au plus haut",
               hypotheses=[f"prix catalogue Snowflake du {prices.AS_OF}, AWS Paris/Francfort : {fr_num(pmin, 2)} $ (Standard) à {fr_num(pmax, 2)} $ "
                           f"(Business Critical), {prices.SNOWFLAKE_SOURCE}", f"{CONTRAT_NON_LU} ; votre région peut différer"])


def _months_touched(du: datetime, au: datetime) -> int:
    return (au.year - du.year) * 12 + au.month - du.month + 1


def _couts(ctx: _Ctx, credit_price: float | None, ne: _NonEstime, row_policies: bool) -> dict:
    r, m = ctx.reads, ctx.snap.get("metering") or {}
    out: dict = {"fenetre_jours": m.get("fenetre_jours")}
    stock = _measured(m.get("stockage_octets_moyens"), r, ("STORAGE_USAGE", "TABLE_STORAGE", "tables.get"), "octets")
    out["stockage_octets_moyens"] = ne.track("Stockage moyen facturé", stock)
    out["stockage_monnaie"] = ne.track("Coût du stockage", fig(nature="non_estimé", unite="USD", raison=CONTRAT_NON_LU))
    tr_names = ("DATA_TRANSFER_HISTORY",)
    out["transferts"] = [{"cible": scrub(x.get("cible")), "octets": _measured(x.get("octets"), r, tr_names, "octets")}
                         for x in sorted(m.get("transferts") or [], key=lambda x: str(x.get("cible")))] if r.ok(*tr_names) else []
    if ctx.plat == "bigquery":
        out.update(_couts_bigquery(ctx, m, ne, row_policies))
        return out
    names = ("METERING_DAILY_HISTORY",)
    services = m.get("par_service")
    if r.ok(*names) and services is not None:
        par = [{"service": scrub(s.get("service")), "credits": _measured(s.get("credits"), r, names, "crédits")}
               for s in sorted(services, key=lambda s: (-(s.get("credits") or 0), str(s.get("service"))))]
        known = [p["credits"]["valeur"] for p in par if p["credits"]["valeur"] is not None]
        total = fig(round(sum(known), 4), nature="calculé", unite="crédits", base=r.ids(*names),
                    formule=f"somme des crédits par service sur {m.get('fenetre_jours') or '?'} jours",
                    confiance="moyenne" if r.status(*names) == "partiel" or len(known) < len(par) else None)
    else:
        par = []
        total = fig(nature="non_lu", unite="crédits", raison=r.why(*names) if not r.ok(*names) else "non transmis par la collecte")
    out["credits"] = ne.track("Crédits consommés", total)
    out["credits_par_service"] = par
    wnames = ("WAREHOUSE_METERING_HISTORY",)
    out["credits_par_entrepot"] = [{"entrepot": scrub(w.get("entrepot")), "credits": _measured(w.get("credits"), r, wnames, "crédits")}
                                   for w in sorted(m.get("par_entrepot") or [], key=lambda w: (-(w.get("credits") or 0), str(w.get("entrepot"))))
                                   ] if r.ok(*wnames) else []
    out["monnaie"] = ne.track("Crédits en dollars", _usd_from_credits(total, credit_price, r.ids(*names)))
    out["note"] = "Crédits mesurés ; en dollars, seulement avec le prix fourni ou une fourchette catalogue datée."
    return out


def _couts_bigquery(ctx: _Ctx, m: dict, ne: _NonEstime, row_policies: bool) -> dict:
    r, names = ctx.reads, ("JOBS",)
    od, rs = m.get("a_la_demande"), m.get("reserve")
    base = r.ids(*names)
    octets = _measured((od or {}).get("octets_factures"), r, names, "octets")
    if octets["valeur"] is not None:
        du, au = r.window(*names)
        jours = m.get("fenetre_jours")
        if not (du and au) and jours:
            au = ctx.as_of
            du = datetime.fromtimestamp(au.timestamp() - jours * 86400, tz=timezone.utc)
        months = _months_touched(du, au) if du and au else None
        tib, price = octets["valeur"] / TIB, prices.BIGQUERY_ON_DEMAND_USD_PER_TIB
        hyp = [f"prix catalogue à la demande du {prices.AS_OF} : {fr_num(price, 2)} $ par Tio (tarif US ; région à confirmer), {prices.BIGQUERY_SOURCE}",
               "haut : aucun palier gratuit déduit ; bas : 1 Tio gratuit par mois touché par la fenêtre ; "
               "le palier gratuit est partagé par le compte de facturation, il n'est pas imputable par projet", CONTRAT_NON_LU]
        if row_policies:
            hyp.append("octets facturés NULL pour les requêtes sur tables à politique de lignes : non comptés")
        hi = round(tib * price, 2)
        lo = round(max(0.0, tib - months) * price, 2) if months else hi
        usd_od = fig(nature="estimé", unite="USD", bas=lo, haut=hi, base=base, confiance="basse",
                     formule="Tio facturés × prix catalogue, palier gratuit déduit ou non", hypotheses=hyp)
    else:
        usd_od = fig(nature="non_estimé", unite="USD", raison="octets facturés non lus")
    slots = _measured((rs or {}).get("slot_heures"), r, names, "slot-heures")
    return {
        "a_la_demande": {"octets_factures": ne.track("Octets facturés à la demande", octets),
                         "requetes": _measured((od or {}).get("requetes"), r, names, "requêtes"),
                         "monnaie": ne.track("À la demande, en dollars", usd_od)},
        "reserve": {"slot_heures": ne.track("Slot-heures en réservé", slots),
                    "requetes": _measured((rs or {}).get("requetes"), r, names, "requêtes"),
                    "monnaie": ne.track("Réservé, en dollars", fig(nature="non_estimé", unite="USD", raison=CONTRAT_NON_LU))},
        "note": "À la demande et réservé : deux modes de facturation, jamais additionnés.",
    }


def _fourchettes(ctx: _Ctx, credit_price: float | None, ne: _NonEstime) -> dict:
    r = ctx.reads
    base = r.ids(*STORAGE_SOURCES)
    movable = [t for t in ctx.present if t["type"] not in ("EXTERNE", "TEMPORAIRE")]
    known = [t["octets_actifs"] for t in movable if t["octets_actifs"] is not None]
    missing = len(movable) - len(known)
    if movable and not known:
        volume = fig(nature="non_lu", unite="octets", raison=r.why(*STORAGE_SOURCES) if not r.ok(*STORAGE_SOURCES) else "volumes non transmis")
    else:
        hyp = ["octets actifs des tables présentes, hors tables externes et temporaires"]
        if ctx.plat == "snowflake":
            hyp.append("volume compressé stocké ; le volume lu par la copie peut être plus élevé")
        if any(t["type"] == "CLONE" for t in movable):
            hyp.append("clones : seuls leurs octets propres sont comptés")
        if missing:
            hyp.append(f"{pl(missing, 'table')} sans volume lu, non comptée(s) : valeur minimale")
        volume = fig(sum(known), nature="calculé", unite="octets", base=base, formule="somme des octets actifs", hypotheses=hyp,
                     confiance="moyenne" if missing else None)
    out = {"volume": ne.track("Volume à copier", volume)}
    v = volume["valeur"]
    debit = f"débit supposé de {fr_num(THROUGHPUT_MBPS[0])} à {fr_num(THROUGHPUT_MBPS[1])} Mo/s, remplacé par la mesure d'un canot drill"
    if v is None:
        out["duree_lecture"] = ne.track("Durée de lecture de la copie", fig(nature="non_estimé", unite="heures", raison="volume non lu"))
        hours = None
    else:
        hours = (v / 1e6 / THROUGHPUT_MBPS[1] / 3600, v / 1e6 / THROUGHPUT_MBPS[0] / 3600)
        out["duree_lecture"] = fig(nature="estimé", unite="heures", bas=round(hours[0], 2), haut=round(hours[1], 2), base=base,
                                   formule="volume ÷ débit supposé", hypotheses=[debit], confiance="basse")
    if ctx.plat == "snowflake":
        if hours is None:
            cr = fig(nature="non_estimé", unite="crédits", raison="volume non lu")
        else:
            cr = fig(nature="estimé", unite="crédits", bas=round(hours[0] * CREDITS_PAR_HEURE, 2), haut=round(hours[1] * CREDITS_PAR_HEURE, 2),
                     base=base, formule="durée × crédits par heure", hypotheses=[debit, "entrepôt XS, 1 crédit par heure"], confiance="basse")
        out["credits_lecture"] = ne.track("Crédits de lecture de la copie", cr)
        out["cout_lecture"] = ne.track("Coût de lecture de la copie", _usd_from_credits(cr, credit_price, base))
    else:
        out["cout_lecture"] = ne.track("Coût de lecture de la copie (API Storage Read, sortie réseau)",
                                       fig(nature="non_estimé", unite="USD", raison=CONTRAT_NON_LU,
                                           hypotheses=["lecture par l'API Storage Read, facturée par Tio lu, et sortie réseau hors de Google Cloud"]))
    n_obj = sum(o["kind"] in REBUILD_KINDS for o in ctx.objets)
    n_cols = sum(len(t["colonnes_non_portables"]) for t in ctx.present)
    lo, hi = prices.rework_days(n_obj, n_cols)
    hyp = [f"{pl(n_obj, 'objet')} à reconstruire et {pl(n_cols, 'colonne')} à type propre à la plateforme",
           f"hypothèses de q4o : {fr_num(prices.REWORK_DAYS_PER_OBJECT[0], 2)} à {fr_num(prices.REWORK_DAYS_PER_OBJECT[1], 1)} jour par objet, "
           f"{fr_num(prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[0], 1)} à {fr_num(prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[1], 1)} par colonne, à discuter"]
    unread = [short_vue(lec["vue"]) for lec in r.find(*CODE_SOURCES) if lec["statut"] != "lu"]
    if unread:
        hyp.append(f"objets des lectures non lues non comptés : {', '.join(sorted(set(unread)))}")
    out["reprise"] = fig(nature="estimé", unite="jours", bas=lo, haut=hi, base=r.ids(*CODE_SOURCES), formule="objets × jours par objet + colonnes × jours par colonne",
                         hypotheses=hyp, confiance="basse")
    return out


def _rejouabilite(ctx: _Ctx, ne: _NonEstime) -> dict:
    names = ("QUERY_HISTORY", "JOBS")
    rj = ctx.snap.get("rejouabilite")
    if not rj:
        raison = ctx.reads.why(*names) if not ctx.reads.ok(*names) else "non transmise par la collecte"
        ne.add("Rejouabilité des requêtes", raison)
        return {"statut": "non_lu", "raison": raison, "categories": {}, "base": None, "mention": MENTION_REJOUABILITE, "taux": {}}
    cats = {k: {"familles": v.get("familles"), "executions": v.get("executions"), "poids": v.get("poids")}
            for k, v in sorted((rj.get("categories") or {}).items())}
    given = rj.get("taux") or {}
    if given and all(isinstance(v, dict) and v.get("nature") in NATURES for v in given.values()):
        taux = {k: dict(v) for k, v in sorted(given.items())}  # calculés par replay sur ses propres bases : repris tels quels
    else:
        tested = [k for k in cats if k != "C7"]
        taux = {}
        for key, unite in (("familles", "familles"), ("executions", "exécutions"), ("poids", "crédits" if ctx.plat == "snowflake" else "octets")):
            vals = [cats[k][key] for k in tested]
            den = sum(v for v in vals if v is not None)
            num = (cats.get("C0") or {}).get(key)
            if None in vals or num is None or not den:
                taux[key] = fig(nature="non_lu", unite="%", raison=f"{unite} non transmises par catégorie")
            else:
                taux[key] = fig(round(100 * num / den, 1), nature="calculé", unite="%", base=ctx.reads.ids(*names),
                                formule=f"C0 ÷ C0 à C6, en {unite} ; C7 compté à part")
    return {"statut": "lu", "raison": None, "categories": cats, "base": scrub(rj.get("base")), "mention": MENTION_REJOUABILITE,
            "taux": taux, "bases": {k: scrub(v) for k, v in sorted((rj.get("bases") or {}).items())},
            "non_testees": rj.get("non_testees"), "secrets_ecartes": rj.get("secrets_ecartes"),
            "fonctions": {scrub(str(k)): v for k, v in sorted((rj.get("fonctions") or {}).items())}}


def _cout_du_scan(ctx: _Ctx) -> dict:
    c = ctx.snap.get("cout_du_scan") or {}
    lecs = ctx.reads.all
    tried = [lec for lec in lecs if lec["statut"] == "lu" or _attempted(lec["raison"])]
    if c.get("requetes") is not None:
        req = fig(c["requetes"], nature="mesuré", unite="requêtes")
    else:
        req = fig(len(tried), nature="calculé", unite="requêtes", base=sorted(lec["id"] for lec in tried),
                  formule="lectures tentées, lues ou refusées par la plateforme", hypotheses=["une lecture = une requête"])
    duree = (fig(c["duree_s"], nature="mesuré", unite="secondes") if c.get("duree_s") is not None
             else fig(nature="non_lu", unite="secondes", raison="durée non transmise par la collecte"))
    out = {"requetes": req, "duree": duree,
           "lectures": {"total": len(lecs), "lu": sum(lec["statut"] == "lu" for lec in lecs), "non_lu": sum(lec["statut"] != "lu" for lec in lecs)}}
    if c.get("appels_api") is not None:
        out["appels_api"] = fig(c["appels_api"], nature="mesuré", unite="appels")
    if c.get("octets_factures") is not None:
        out["octets_factures"] = fig(c["octets_factures"], nature="mesuré", unite="octets")
    if c.get("credits") is not None:
        out["credits"] = fig(c["credits"], nature="mesuré", unite="crédits")
    return out


_TRIED = ("droit absent", "édition requise", "absent ou non exposé", "service désactivé", "délai dépassé", "échec (", "volume estimé")


def _attempted(raison: str | None) -> bool:
    return bool(raison) and raison.startswith(_TRIED)


# --- synthèse --------------------------------------------------------------------------------------------


def _fraicheur(ctx: _Ctx) -> dict | None:
    """Recoupement temps réel de la collecte (Snowflake) : objets plus récents que la latence d'ACCOUNT_USAGE. None si non transmis."""
    f = ctx.snap.get("fraicheur")
    if not isinstance(f, dict):
        return None
    rec = f.get("recents_seulement_temps_reel") or {}
    n_t = sum("recente" in t["flags"] for t in ctx.present)
    lat = [lec["latence_max_h"] for lec in ctx.reads.find("TABLES", *STORAGE_SOURCES, "ACCESS_HISTORY")
           if lec["statut"] == "lu" and lec["latence_max_h"]]
    return {"recents_seulement_temps_reel": {"tables": rec.get("tables") if isinstance(rec.get("tables"), int) else n_t,
                                             "objets": rec.get("objets") if isinstance(rec.get("objets"), int) else None},
            "volumes_en_retard": f.get("volumes_en_retard") if isinstance(f.get("volumes_en_retard"), int) else None,
            "latence_max_h": f.get("latence_max_h"), "texte": scrub(f.get("texte")),
            "tables_sans_historique": n_t, "latence_historique_h": max(lat, default=None)}


def _fraicheur_phrase(fr: dict | None) -> str | None:
    """Phrases du résumé DSI : tables vues seulement en temps réel, volumes pas encore calculés par TABLE_STORAGE_METRICS."""
    if not fr:
        return None
    out = []
    n, h, v = fr["tables_sans_historique"], fr["latence_historique_h"], fr.get("volumes_en_retard") or 0
    lat = f", dont la latence peut atteindre {fr_num(h, 1).removesuffix(',0')} h" if h else ""
    if n == 1:
        out.append(f"1 table vue seulement en temps réel : absente d'ACCOUNT_USAGE{lat} ; "
                   "son historique (volumes facturés, lectures) n'est pas encore disponible.")
    elif n > 1:
        out.append(f"{fr_num(n)} tables vues seulement en temps réel : absentes d'ACCOUNT_USAGE{lat} ; "
                   "leur historique (volumes facturés, lectures) n'est pas encore disponible.")
    if v:
        out.append(f"Volume sous-estimé : métriques de stockage pas encore à jour pour {pl(v, 'table')}.")
    return " ".join(out) or None


def _resume(ctx: _Ctx, res: dict, by_code: Counter) -> dict:
    r, couts, fr = ctx.reads, res["couts"], res["fourchettes"]
    vol = fr["volume"]["valeur"]
    n_obj = sum(o["kind"] in REBUILD_KINDS for o in ctx.objets)
    mv = res["mouvements"]
    fraiche = _fraicheur_phrase(res.get("fraicheur"))  # dans la première phrase : l'encadré garde trois entrées
    dsi = [f"{pl(len(ctx.present), 'table présente', 'tables présentes')} ({fr_bytes(vol) if vol is not None else 'volume non lu'}) "
           f"et {pl(n_obj, 'objet')} à reconstruire hors tables." + (f" {fraiche}" if fraiche else ""),
           f"Mouvement : {fr_num(mv['figée'])} figée(s), {fr_num(mv['vivante'])} vivante(s), {fr_num(mv['recréée'])} recréée(s), "
           f"{fr_num(mv['non_determine'])} non déterminée(s)."]
    rj = res["rejouabilite"]
    if rj["statut"] == "lu" and rj["categories"]:
        tested = sum((v["familles"] or 0) for k, v in rj["categories"].items() if k != "C7")
        dsi.append(f"Rejouabilité : {fr_num((rj['categories'].get('C0') or {}).get('familles') or 0)}/{fr_num(tested)} familles "
                   f"acceptées par DuckDB ; {MENTION_REJOUABILITE}.")
    else:
        dsi.append(f"Rejouabilité non lue : {rj['raison']}.")

    daf = []
    jours = couts.get("fenetre_jours")
    sur = f"sur {jours} jours" if jours else "sur la fenêtre lue"
    if ctx.plat == "bigquery":
        od, rs = couts["a_la_demande"], couts["reserve"]
        a = fr_bytes(od["octets_factures"]["valeur"]) + " facturés à la demande" if od["octets_factures"]["valeur"] is not None else "à la demande non lu"
        b = f"{fr_num(rs['slot_heures']['valeur'], 1)} slot-heures en réservé" if rs["slot_heures"]["valeur"] is not None else "réservé non lu"
        daf.append(f"Mesuré {sur} : {a}, {b} ; les deux ne s'additionnent pas.")
        m = od["monnaie"]
        daf.append((f"À la demande, fourchette catalogue : {fr_num(m['bas'], 2)} à {fr_num(m['haut'], 2)} $" if m["nature"] == "estimé"
                    else f"À la demande, non estimé : {m['raison']}") + f" ; réservé, non estimé : {CONTRAT_NON_LU}.")
    else:
        c = couts["credits"]
        daf.append(f"Consommation mesurée {sur} : {fr_num(c['valeur'], 1)} crédits." if c["valeur"] is not None
                   else f"Consommation non lue : {c['raison']}.")
        m = couts["monnaie"]
        if m["nature"] == "calculé":
            daf.append(f"Au prix du crédit fourni : {fr_num(m['valeur'], 2)} $.")
        elif m["nature"] == "estimé":
            daf.append(f"En dollars, fourchette catalogue du {prices.AS_OF} : {fr_num(m['bas'], 2)} à {fr_num(m['haut'], 2)} $ ; {CONTRAT_NON_LU}.")
        else:
            daf.append(f"En dollars, non estimé : {m['raison']}.")
    h = res["ne_part_pas"]["historique"]
    if h["octets"]["valeur"] is not None:
        daf.append(f"Historique facturé qui ne part pas avec la copie : {fr_bytes(h['octets']['valeur'])} sur {pl(h['tables'], 'table')} (F06, F07).")
    else:
        daf.append(f"Historique facturé non lu : {h['octets']['raison']}.")

    rssi = []
    if r.ok("POLICY_REFERENCES", "TAG_REFERENCES", "rowAccessPolicies.list", "COLUMN_FIELD_PATHS") or by_code["F10"]:
        rssi.append(f"{pl(by_code['F10'], 'table ou politique', 'tables ou politiques')} à reconstruire côté sécurité (F10) ; "
                    "la copie demande un principal non filtré.")
    else:
        rssi.append(f"Politiques non lues : {r.why(*POLICY_SOURCES)}.")
    rssi.append(f"{pl(by_code['F01'] + by_code['F02'], 'dépendance externe', 'dépendances externes')} (F01, F02), "
                f"{pl(by_code['F03'], 'écriture', 'écritures')} hors de la plateforme (F03), "
                f"{pl(by_code['F09'], 'partage', 'partages')} avec consommateurs externes (F09).")
    if res["f04_lu"]:
        rssi.append(f"{pl(by_code['F04'], 'rôle propriétaire', 'rôles propriétaires')} sans titulaire (F04).")
    elif ctx.plat == "bigquery":
        rssi.append("Rôles propriétaires (F04) : sans objet sur BigQuery, où les droits sont portés par les datasets et les tables.")
    else:
        rssi.append("Rôles propriétaires sans titulaire non déterminés : droits non lus.")
    return {"DSI": dsi, "DAF": daf, "RSSI": rssi}


# --- point d'entrée --------------------------------------------------------------------------------------


def analyze(snapshot: dict, *, credit_price: float | None = None) -> dict:
    """snapshot (model.py) -> scan.json (schema canot.scan/1). Fonction pure."""
    as_of = parse_dt(snapshot.get("as_of"))
    if as_of is None:
        raise ValueError("snapshot sans as_of : l'analyse n'a pas d'horloge")
    reads = _Reads(sorted((_lecture(lec) for lec in snapshot.get("lectures") or []), key=lambda lec: lec["id"]))
    tables = sorted((_table(t, as_of) for t in snapshot.get("tables") or []), key=lambda t: t["id"])
    objets = sorted((_objet(o) for o in snapshot.get("objets") or []), key=lambda o: (str(o["kind"]), o["id"]))
    familles = sorted((_famille(f) for f in snapshot.get("familles") or []), key=lambda f: (-(f["n_exec"] or 0), str(f["empreinte"])))
    ctx = _Ctx(snapshot, as_of, reads, tables, objets, familles)
    plat = ctx.plat

    edition = snapshot.get("edition")
    edition_missing = not edition and plat == "snowflake" and reads.status("ACCESS_HISTORY") == "non_lu"
    if not edition:
        edition = "non déterminée (Standard ou droit absent)" if edition_missing else "non déterminée"

    ext = _external(ctx)
    sources = _read_sources(ctx)
    f09, shared = _f09(ctx)
    f04, unresolved = _f04(ctx)
    drapeaux = (_f01_f02(ctx, ext) + _f03(ctx) + f04 + _f05(ctx, sources, shared) + _f06_f07(ctx) + _f08(ctx) + f09
                + _f10(ctx) + _f11(ctx) + _f12(ctx, edition_missing, unresolved, sources))
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
    mouvements = {"figée": mv["figée"], "vivante": mv["vivante"], "recréée": mv["recréée"], "non_determine": mv[None],
                  "base": reads.ids(*WRITE_SOURCES),
                  "regle": (f"recréée : DDL de moins de {RECREEE_JOURS} jours sans écriture postérieure ; vivante : écriture de moins de "
                            f"{VIVANTE_JOURS} jours ; figée : dernière écriture plus ancienne ; non déterminée : écritures non lues")}

    hist_tables = [t for t in tables if t["octets_historique"] and t["octets_historique"] > 0
                   or (t["supprimee_le"] and (t["octets_actifs"] or 0) > 0)]
    hist_total = sum((t["octets_historique"] or 0) + ((t["octets_actifs"] or 0) if t["supprimee_le"] else 0) for t in hist_tables)
    if reads.ok(*STORAGE_SOURCES) and any(t["octets_historique"] is not None for t in tables):
        hist = fig(hist_total, nature="calculé", unite="octets", base=reads.ids(*STORAGE_SOURCES),
                   formule="Time Travel + Fail-safe des tables présentes, + octets encore facturés des tables supprimées")
    else:
        hist = fig(nature="non_lu", unite="octets", raison=reads.why(*STORAGE_SOURCES) if not reads.ok(*STORAGE_SOURCES) else "historique non transmis")
    ne.track("Historique facturé", hist)
    kinds = Counter(o["kind"] for o in objets)
    ne_part_pas = {
        "objets_par_type": dict(sorted(kinds.items())),
        "exemples": {k: [o["id"] for o in objets if o["kind"] == k][:5] for k in sorted(kinds)},
        "routines_par_langage": dict(sorted(Counter((o["langage"] or "non lu").upper() for o in objets
                                                    if o["kind"] in ("procedure", "fonction")).items())),
        "colonnes_non_portables": sum(len(t["colonnes_non_portables"]) for t in ctx.present),
        "historique": {"octets": hist, "tables": len(hist_tables)},
    }

    outils = defaultdict(lambda: [0, 0])
    for f in familles:
        k = f["outil"] or f["app"] or "non identifié"
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

    ne.add("Tarif de votre contrat", CONTRAT_NON_LU if not (credit_price and plat == "snowflake") else "prix du crédit fourni, autres postes non lus")
    ne.add("Stockage d'arrivée, double exploitation, fonctionnement après la sortie", "dépendent de votre contrat et de votre architecture")
    ne.add("Résultat identique des requêtes traduites", f"{MENTION_REJOUABILITE} : traduit vers DuckDB, non comparé ; un canot drill compare les résultats")
    ne.add("Projections de coût fournies par la plateforme", "non reprises : elles ne se vérifient pas depuis le scan")
    if plat == "bigquery":
        ne.add("Palier gratuit BigQuery", "partagé par le compte de facturation, non imputable par projet")
        ne.add("Lectures par l'API Storage Read", "absentes de INFORMATION_SCHEMA.JOBS")
        if row_policies:
            ne.add("Octets facturés des requêtes sous politique de lignes", "valeur NULL dans INFORMATION_SCHEMA.JOBS")

    lectures = reads.all
    for lec in lectures:
        lec.setdefault("limite", None)
    res = {
        "schema": SCHEMA, "as_of": snapshot.get("as_of"), "versions": {k: str(v) for k, v in sorted((snapshot.get("versions") or {}).items())},
        "plateforme": plat, "perimetre": scrub(snapshot.get("perimetre")), "edition": edition,
        "schemas_caches": sorted({str(t["schema"]) for t in tables if str(t["schema"] or "").startswith("_")}
                                 | {str(d.get("nom")) for d in snapshot.get("datasets") or [] if d.get("cache") and d.get("nom")}),
        "niveau_droits": snapshot.get("niveau_droits"), "mode_sql": snapshot.get("mode_sql") or "lecture seule",
        "lectures": lectures, "tables": tables, "objets": objets, "familles": familles, "drapeaux": drapeaux,
        "couts": couts, "fourchettes": fourchettes, "dependances": dependances, "rejouabilite": rejouabilite,
        "mouvements": mouvements, "ne_part_pas": ne_part_pas, "non_estime": ne.items, "cout_du_scan": _cout_du_scan(ctx),
        "latence_max_h": max((lec["latence_max_h"] for lec in lectures if lec["statut"] == "lu" and lec["latence_max_h"] is not None), default=None),
        "fraicheur": _fraicheur(ctx),
        "f04_lu": bool(f04) or (all(reads.status(n) == "lu" for n in GRANT_SOURCES) and (snapshot.get("droits") or {}).get("roles") is not None),
        "regles": {"vivante_jours": VIVANTE_JOURS, "recreee_jours": RECREEE_JOURS, "horizon_lectures_jours": HORIZON_JOURS.get(plat, 365),
                   "debit_mo_s": list(THROUGHPUT_MBPS)},
        "sources_prix": [prices.SNOWFLAKE_SOURCE] if plat == "snowflake" else [prices.BIGQUERY_SOURCE] if plat == "bigquery" else [],
    }
    res["resume"] = _resume(ctx, res, by_code)
    return res
