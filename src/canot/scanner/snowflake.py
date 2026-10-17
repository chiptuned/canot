"""Collecte Snowflake de `canot scan` : SNOWFLAKE.ACCOUNT_USAGE d'abord (tout le compte, fenêtre de `days` jours), SHOW pour combler les trous.

ACCOUNT_USAGE a jusqu'à 3 h de retard (2 jours pour COPY_HISTORY) : la base scannée est recoupée en temps réel par son
INFORMATION_SCHEMA (tables, colonnes, vues, routines, stages, pipes) et par SHOW (tâches, flux, tables dynamiques). Un objet vu
seulement en temps réel est ajouté et marqué (table : flags « recente », detail.source ; objet : detail.inventaire, detail.recent) ;
son historique ACCOUNT_USAGE reste None. Le bilan est dans snapshot["fraicheur"].

Lecture seule : chaque SQL passe par guard.check_readonly avant d'être envoyé. Agrégation côté serveur. Le texte SQL brut ne sort de
collect() que par le second retour (familles brutes, clé `_sql`) ; les définitions de code sont analysées ici puis jetées.
Jamais lus : BIND_VALUES, ERROR_MESSAGE, USERS.EMAIL/LOGIN_NAME, CLIENT_ENVIRONMENT, CLIENT_IP, LAST_DDL_BY, chemins des stages."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import sqlglot
from sqlglot import exp

from .. import __version__
from .guard import check_readonly
from .model import Collector, bucket_only, classify_failure, pseudo

AU = "SNOWFLAKE.ACCOUNT_USAGE"
FAMILLES_MAX = 500
ATTRIBUTION_MAX = 2000
# latence maximale annoncée par Snowflake, en heures (docs.snowflake.com/en/sql-reference/account-usage) ; SHOW : temps réel
LATENCE_H = {
    "TABLES": 1.5, "COLUMNS": 1.5, "VIEWS": 1.5, "TABLE_STORAGE_METRICS": 1.5, "STORAGE_USAGE": 2, "METERING_DAILY_HISTORY": 3,
    "WAREHOUSE_METERING_HISTORY": 3, "QUERY_ATTRIBUTION_HISTORY": 8, "TABLE_DML_HISTORY": 6, "TABLE_QUERY_PRUNING_HISTORY": 4,
    "AUTOMATIC_CLUSTERING_HISTORY": 3, "OBJECT_DEPENDENCIES": 3, "FUNCTIONS": 2, "PROCEDURES": 2, "TASKS": 3, "TASK_HISTORY": 0.75,
    "SERVERLESS_TASK_HISTORY": 3, "STREAMS": 2, "PIPES": 2, "COPY_HISTORY": 48, "STAGES": 2, "DATABASES": 3,
    "DATA_TRANSFER_HISTORY": 2, "DYNAMIC_TABLE_REFRESH_HISTORY": 3, "QUERY_HISTORY": 0.75, "ACCESS_HISTORY": 3,
    "POLICY_REFERENCES": 2, "TAG_REFERENCES": 2, "GRANTS_TO_ROLES": 2, "GRANTS_TO_USERS": 2, "USERS": 2, "SESSIONS": 3, "SHARES": 3,
}
FILTREES_PAR_DROITS = "lignes filtrées par les droits du rôle : les objets non visibles sont absents"
# recoupement en temps réel (INFORMATION_SCHEMA de la base, SHOW) : objets créés depuis moins que la latence d'ACCOUNT_USAGE
TEMPS_REEL = "temps réel, filtré par les droits du rôle : recoupe ACCOUNT_USAGE"
TROP_RECENTE = "table trop récente pour ACCOUNT_USAGE"
# requêtes jamais remontées : utilisateurs, stages, secrets, fichiers (PUT/GET), intégrations
EXCLU_QUERY_TYPE = ("%USER%", "%STAGE%", "%SECRET%", "%PASSWORD%", "%FILES%", "%INTEGRATION%")


# --- petites conversions ------------------------------------------------------------------------------------

def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        v = v if v.tzinfo else v.replace(tzinfo=timezone.utc)  # session en UTC
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _int(v) -> int | None:
    return None if v is None else int(v)


def _num(v) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, (Decimal, float)) else v


def _yes(v) -> bool:
    return v is True or str(v).upper() in ("YES", "Y", "TRUE")


def _lit(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


def _ts(dt: datetime) -> str:
    return f"TO_TIMESTAMP_TZ('{dt:%Y-%m-%d %H:%M:%S} +0000', 'YYYY-MM-DD HH24:MI:SS TZHTZM')"


def _ident(name: str) -> str:
    """Identifiant non entre guillemets -> majuscules (stockage Snowflake) ; sinon tel quel."""
    name = name.strip()
    return name.upper() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) else name.strip('"')


def _fqn(*parts) -> str:
    return ".".join(str(p) for p in parts if p)


def _qid(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _coltype(r: dict) -> str:
    typ = r["DATA_TYPE"]
    if typ in ("NUMBER", "DECIMAL", "NUMERIC") and r.get("NUMERIC_PRECISION") is not None:
        typ = f"NUMBER({_int(r['NUMERIC_PRECISION'])},{_int(r.get('NUMERIC_SCALE')) or 0})"
    return typ


def _hours(h) -> str:
    s = f"{h:.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def _rt_merge(au: list[dict] | None, rt: list[dict] | None, key) -> list[dict] | None:
    """Lignes d'ACCOUNT_USAGE + lignes temps réel absentes d'ACCOUNT_USAGE (marquées `_rt`). None si aucune des deux n'est lue."""
    if au is None and rt is None:
        return None
    seen = {key(r) for r in au or []}
    return list(au or []) + [{**r, "_rt": True} for r in rt or [] if key(r) not in seen]


def _scope(db: str | None, schemas: list[str], dbcol: str, schcol: str | None) -> str:
    out = f" AND {dbcol} = {_lit(db)}" if db else ""
    if schemas and schcol:
        out += f" AND {schcol} IN (" + ", ".join(_lit(s) for s in schemas) + ")"
    return out


# --- analyse locale des définitions (le texte ne quitte pas cette fonction) -----------------------------------

_DYN_SQL = re.compile(r"\bEXECUTE\s+IMMEDIATE\b|\bIDENTIFIER\s*\(\s*[:$]", re.I)
_DYN_CALL = {"JAVASCRIPT": re.compile(r"snowflake\.(execute|createStatement)\s*\(", re.I),
             "PYTHON": re.compile(r"\.sql\s*\("), "JAVA": re.compile(r"\.sql\s*\("), "SCALA": re.compile(r"\.sql\s*\(")}
_CONCAT = re.compile(r"[\"'`]\s*\+|\+\s*[\"'`]|\$\{|(?<![\w])f[\"']|\.format\s*\(|%\s*\(|String\.format|\bconcat\s*\(", re.I)
_STAGE_REF = re.compile(r"(?<![\w.$@-])@(~|%[\w$]+|\"?[A-Za-z_][\w$]*\"?(?:\.\"?[A-Za-z_][\w$]*\"?){0,2})")
_URL = re.compile(r"\b(?:s3|s3gov|gcs|azure|wasbs?|abfss?|https?)://[^\s'\"`)\]]+", re.I)
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _dynamic(lang: str | None, body: str | None) -> bool | None:
    """SQL dynamique : EXECUTE IMMEDIATE / IDENTIFIER(:var) en SQL ; appel d'exécution avec chaîne construite ailleurs. None si non visible."""
    if body is None:
        return None
    lang = (lang or "SQL").upper()
    if lang == "SQL":
        return bool(_DYN_SQL.search(body))
    call = _DYN_CALL.get(lang)
    if call is None:
        return None
    return bool(call.search(body) and _CONCAT.search(body))


def _qualify(ref: str, db: str | None, schema: str | None) -> str | None:
    """'stg/chemin' ou 'S.OBJ' -> 'DB.S.OBJ' (non entre guillemets : majuscules) ; @~ et @%t (stages internes) -> None."""
    if not ref or ref.startswith(("~", "%")):
        return None
    parts = [p.strip('"') if p.startswith('"') else p.upper() for p in ref.split("/")[0].split(".")][-3:]
    parts = [db, schema][: 3 - len(parts)] + parts
    return _fqn(*parts) if all(parts) else None


def _code_refs(lang: str | None, body: str | None, db: str | None, schema: str | None) -> tuple[list[str], list[str]]:
    """(stages référencés, compartiments/hôtes externes) d'une définition ; jamais de chemin."""
    if not body:
        return [], []
    text = _SQL_LITERAL.sub("''", body) if (lang or "SQL").upper() == "SQL" else body
    stages = sorted({s for s in (_qualify(m, db, schema) for m in _STAGE_REF.findall(text)) if s})
    hosts = sorted({b for b in (bucket_only(u) for u in _URL.findall(body)) if b})
    return stages, hosts


def _tables_in(sql: str | None, db: str | None, schema: str | None) -> list[str]:
    if not sql:
        return []
    try:
        tree = sqlglot.parse_one(sql, read="snowflake")
    except Exception:  # noqa: BLE001 : définition non analysable, on ne déduit rien
        return []
    out = set()
    for t in tree.find_all(exp.Table):
        if not t.name:
            continue
        def part(key, default):
            i = t.args.get(key)
            return default if i is None or not i.name else (i.name if getattr(i, "quoted", False) else i.name.upper())
        out.add(_fqn(part("catalog", db), part("db", schema), part("this", None)))
    return sorted(out)


def _location(first: str | None, third: str | None, db: str | None = None, schema: str | None = None) -> tuple[str | None, str | None]:
    """(compartiment externe, stage nommé) depuis SPLIT_PART(loc,'/',1) et SPLIT_PART(loc,'/',3) : le chemin n'est jamais lu."""
    first = (first or "").lstrip('["').strip()
    if first.endswith(":") and third:
        return bucket_only(f"{first}//{third}"), None
    if first.startswith("@"):
        return None, _qualify(first[1:], db, schema)
    return None, None


def _split_location(loc) -> tuple[str | None, str | None]:
    parts = str(loc or "").split("/")
    return parts[0], parts[2] if len(parts) > 2 else None


def _app(tag: str | None) -> str | None:
    """Nom d'application tiré de QUERY_TAG (JSON {"app": ...} ou jeton court) ; tout le reste est ignoré (peut contenir une identité)."""
    if not tag:
        return None
    token = re.compile(r"[A-Za-z0-9_.:-]{1,40}")
    try:
        d = json.loads(tag)
    except ValueError:
        d = None
    if isinstance(d, dict):
        for k in ("app", "application", "tool", "source"):
            v = d.get(k)
            if isinstance(v, str) and token.fullmatch(v):
                return v
        return None
    return tag if token.fullmatch(tag) else None


def _new_table(tid: str, db: str, schema: str, name: str) -> dict:
    return {"id": tid, "base": db, "schema": schema, "nom": name, "type": "TABLE", "flags": [], "lignes": None,
            "octets_actifs": None, "octets_historique": None, "clone_de": None, "colonnes": [], "retention_jours": None,
            "derniere_ddl": None, "derniere_ecriture": None, "derniere_lecture": None, "n_lectures": None, "n_ecritures": None,
            "proprietaire": None, "politiques": [], "etiquettes": [], "supprimee_le": None, "externe_uri": None,
            "partitionnement": None, "clustering": [], "detail": {}}


def _new_objet(kind: str, oid: str, schema: str | None = None, **kw) -> dict:
    o = {"kind": kind, "id": oid, "schema": schema, "langage": None, "proprietaire": None, "externe": False,
         "dependances_externes": [], "reference": [], "etat": None, "planification": None, "executions_n": None,
         "echecs_n": None, "derniere_execution": None, "sql_dynamique": None, "detail": {}}
    o.update(kw)
    return o


# --- collecte ---------------------------------------------------------------------------------------------

def collect(run, *, base: str, schemas: list[str] | None, level: int, salt: bytes, days: int = 365,
            now: datetime | None = None) -> tuple[dict, list[dict]]:
    """Lit le compte en lecture seule. `run(sql) -> list[dict]` (clés en MAJUSCULES). Renvoie (snapshot, familles brutes avec `_sql`).
    `base` vide ou "*" : tout le compte. `now` sert aux tests (sinon l'heure UTC courante)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=days)
    fen = {"du": _iso(start), "au": _iso(now)}
    db = _ident(base) if base and base.strip() != "*" else None
    schs = [_ident(s) for s in schemas or []]
    since, since_d = _ts(start), f"TO_DATE('{start.date().isoformat()}')"
    c = Collector(prefix="S")

    def q(sql: str) -> list[dict]:
        return run(check_readonly(sql, "snowflake"))

    def sc(dbcol: str, schcol: str | None = None) -> str:
        return _scope(db, schs, dbcol, schcol)

    def in_scope(d, s) -> bool:
        return (not db or d == db) and (not schs or s in schs)

    def read(view: str, sql, *, lvl: int = 1, windowed: bool = False):
        vue = view if view.startswith("SHOW") else f"{AU}.{view}"
        if level < lvl:
            c.skip(vue, f"non demandé (niveau {level})")
            return None
        fn = sql if callable(sql) else (lambda: q(sql))
        lat = 0 if view.startswith("SHOW") else LATENCE_H.get(view.split(" ")[0])
        return c.read(vue, fn, fenetre=dict(fen) if windowed else None, latence_max_h=lat)

    def last() -> dict:
        return c.lectures[-1]

    R: dict[str, list[dict] | None] = {}

    # ---- niveau 1 : inventaire, volumes, consommation (OBJECT_VIEWER, USAGE_VIEWER)
    def metering():
        q("ALTER SESSION SET TIMEZONE = 'UTC'")  # USAGE_DATE en UTC, comme ORGANIZATION_USAGE
        return q(f"SELECT SERVICE_TYPE, SUM(CREDITS_USED) AS UTILISES, SUM(CREDITS_BILLED) AS FACTURES, MIN(USAGE_DATE) AS DU, "
                 f"MAX(USAGE_DATE) AS AU FROM {AU}.METERING_DAILY_HISTORY WHERE USAGE_DATE >= {since_d} GROUP BY SERVICE_TYPE ORDER BY SERVICE_TYPE")
    R["metering"] = read("METERING_DAILY_HISTORY", metering, windowed=True)
    R["tables"] = read("TABLES", f"SELECT TABLE_ID, TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_OWNER, TABLE_TYPE, IS_TRANSIENT, "
                       f"CLUSTERING_KEY, AUTO_CLUSTERING_ON, ROW_COUNT, BYTES, RETENTION_TIME, CREATED, LAST_DDL, DELETED, IS_ICEBERG, "
                       f"IS_DYNAMIC, IS_HYBRID FROM {AU}.TABLES WHERE (DELETED IS NULL OR DELETED >= {since}) "
                       f"AND TABLE_SCHEMA <> 'INFORMATION_SCHEMA'{sc('TABLE_CATALOG', 'TABLE_SCHEMA')}")
    R["columns"] = read("COLUMNS", f"SELECT TABLE_ID, COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, ORDINAL_POSITION "
                        f"FROM {AU}.COLUMNS WHERE DELETED IS NULL AND TABLE_SCHEMA <> 'INFORMATION_SCHEMA'"
                        f"{sc('TABLE_CATALOG', 'TABLE_SCHEMA')} ORDER BY TABLE_ID, ORDINAL_POSITION")
    R["tsm"] = read("TABLE_STORAGE_METRICS", f"SELECT ID, TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, CLONE_GROUP_ID, IS_TRANSIENT, "
                    f"ACTIVE_BYTES, TIME_TRAVEL_BYTES, FAILSAFE_BYTES, RETAINED_FOR_CLONE_BYTES, DELETED, TABLE_DROPPED "
                    f"FROM {AU}.TABLE_STORAGE_METRICS WHERE (NOT DELETED OR ACTIVE_BYTES + TIME_TRAVEL_BYTES + FAILSAFE_BYTES "
                    f"+ RETAINED_FOR_CLONE_BYTES > 0){sc('TABLE_CATALOG', 'TABLE_SCHEMA')}")
    R["storage"] = read("STORAGE_USAGE", f"SELECT COUNT(*) AS JOURS, AVG(STORAGE_BYTES + STAGE_BYTES + FAILSAFE_BYTES) AS MOYEN, "
                        f"AVG(STORAGE_BYTES) AS TABLES, AVG(STAGE_BYTES) AS STAGES, AVG(FAILSAFE_BYTES) AS FAILSAFE "
                        f"FROM {AU}.STORAGE_USAGE WHERE USAGE_DATE >= {since_d}", windowed=True)
    R["wmh"] = read("WAREHOUSE_METERING_HISTORY", f"SELECT WAREHOUSE_NAME, SUM(CREDITS_USED) AS CREDITS, SUM(CREDITS_USED_COMPUTE) AS CALCUL, "
                    f"SUM(CREDITS_USED_CLOUD_SERVICES) AS SERVICES, SUM(CREDITS_ATTRIBUTED_COMPUTE_QUERIES) AS ATTRIBUES "
                    f"FROM {AU}.WAREHOUSE_METERING_HISTORY WHERE START_TIME >= {since} GROUP BY WAREHOUSE_NAME ORDER BY CREDITS DESC",
                    windowed=True)
    not_canot = "COALESCE(QUERY_TAG, '') <> 'canot'"
    R["qah"] = read("QUERY_ATTRIBUTION_HISTORY", f"SELECT QUERY_PARAMETERIZED_HASH AS H, COUNT(*) AS N, SUM(CREDITS_ATTRIBUTED_COMPUTE) AS CREDITS, "
                    f"SUM(CREDITS_USED_QUERY_ACCELERATION) AS QAS, COUNT(*) OVER () AS FAMILLES_TOTAL, "
                    f"SUM(SUM(CREDITS_ATTRIBUTED_COMPUTE)) OVER () AS CREDITS_TOTAL FROM {AU}.QUERY_ATTRIBUTION_HISTORY "
                    f"WHERE START_TIME >= {since} AND {not_canot} GROUP BY QUERY_PARAMETERIZED_HASH "
                    f"ORDER BY CREDITS DESC NULLS LAST LIMIT {ATTRIBUTION_MAX}", windowed=True)
    R["dml"] = read("TABLE_DML_HISTORY", f"SELECT TABLE_ID, MAX(END_TIME) AS DERNIERE, COUNT(*) AS INTERVALLES, SUM(ROWS_ADDED) AS AJOUTEES, "
                    f"SUM(ROWS_UPDATED) AS MODIFIEES, SUM(ROWS_REMOVED) AS RETIREES FROM {AU}.TABLE_DML_HISTORY "
                    f"WHERE START_TIME >= {since}{sc('DATABASE_NAME', 'SCHEMA_NAME')} GROUP BY TABLE_ID", windowed=True)
    # les lectures de Canot (drill) sont retirées par leur empreinte, connue de QUERY_ATTRIBUTION_HISTORY (même rôle de base)
    R["pruning"] = read("TABLE_QUERY_PRUNING_HISTORY", f"SELECT TABLE_ID, SUM(NUM_QUERIES) AS N, MAX(INTERVAL_END_TIME) AS DERNIERE "
                        f"FROM {AU}.TABLE_QUERY_PRUNING_HISTORY WHERE INTERVAL_START_TIME >= {since}{sc('DATABASE_NAME', 'SCHEMA_NAME')} "
                        f"AND COALESCE(QUERY_PARAMETERIZED_HASH, '') NOT IN (SELECT QUERY_PARAMETERIZED_HASH FROM {AU}.QUERY_ATTRIBUTION_HISTORY "
                        f"WHERE QUERY_TAG = 'canot' AND QUERY_PARAMETERIZED_HASH IS NOT NULL AND START_TIME >= {since}) GROUP BY TABLE_ID",
                        windowed=True)
    R["clustering"] = read("AUTOMATIC_CLUSTERING_HISTORY", f"SELECT TABLE_ID, SUM(CREDITS_USED) AS CREDITS FROM {AU}.AUTOMATIC_CLUSTERING_HISTORY "
                           f"WHERE START_TIME >= {since}{sc('DATABASE_NAME', 'SCHEMA_NAME')} GROUP BY TABLE_ID", windowed=True)
    R["deps"] = read("OBJECT_DEPENDENCIES", f"SELECT REFERENCING_DATABASE, REFERENCING_SCHEMA, REFERENCING_OBJECT_NAME, REFERENCING_OBJECT_DOMAIN, "
                     f"REFERENCED_DATABASE, REFERENCED_SCHEMA, REFERENCED_OBJECT_NAME, REFERENCED_OBJECT_DOMAIN FROM {AU}.OBJECT_DEPENDENCIES "
                     f"WHERE REFERENCING_OBJECT_NAME IS NOT NULL{sc('REFERENCING_DATABASE', 'REFERENCING_SCHEMA')}")
    R["views"] = read("VIEWS", f"SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_OWNER, IS_SECURE, LAST_ALTERED, VIEW_DEFINITION "
                      f"FROM {AU}.VIEWS WHERE DELETED IS NULL{sc('TABLE_CATALOG', 'TABLE_SCHEMA')}")
    if R["views"] is not None:
        last()["raison"] = FILTREES_PAR_DROITS
    R["functions"] = read("FUNCTIONS", f"SELECT FUNCTION_CATALOG, FUNCTION_SCHEMA, FUNCTION_NAME, FUNCTION_OWNER, FUNCTION_LANGUAGE, IS_EXTERNAL, "
                          f"API_INTEGRATION, EXTERNAL_ACCESS_INTEGRATIONS, FUNCTION_DEFINITION, LAST_ALTERED FROM {AU}.FUNCTIONS "
                          f"WHERE DELETED IS NULL{sc('FUNCTION_CATALOG', 'FUNCTION_SCHEMA')}")
    if R["functions"] is not None:
        last()["raison"] = FILTREES_PAR_DROITS
    R["procedures"] = read("PROCEDURES", f"SELECT PROCEDURE_CATALOG, PROCEDURE_SCHEMA, PROCEDURE_NAME, PROCEDURE_OWNER, PROCEDURE_LANGUAGE, "
                           f"EXTERNAL_ACCESS_INTEGRATIONS, PROCEDURE_DEFINITION, LAST_ALTERED FROM {AU}.PROCEDURES "
                           f"WHERE DELETED IS NULL{sc('PROCEDURE_CATALOG', 'PROCEDURE_SCHEMA')}")
    if R["procedures"] is not None:
        last()["raison"] = FILTREES_PAR_DROITS
    R["tasks"] = read("TASKS", f"SELECT TASK_DATABASE, TASK_SCHEMA, TASK_NAME, TASK_OWNER, WAREHOUSE, SCHEDULE, PREDECESSORS, STATE, "
                      f"DEFINITION, CONDITION, LAST_SUSPENDED_REASON FROM {AU}.TASKS WHERE DELETED IS NULL{sc('TASK_DATABASE', 'TASK_SCHEMA')}")
    R["task_history"] = read("TASK_HISTORY", f"SELECT DATABASE_NAME, SCHEMA_NAME, NAME, COUNT_IF(STATE IN ('SUCCEEDED', 'FAILED')) AS N, "
                             f"COUNT_IF(STATE = 'FAILED') AS ECHECS, MAX(COMPLETED_TIME) AS DERNIERE, "
                             f"MAX(IFF(STATE = 'FAILED', COMPLETED_TIME, NULL)) AS DERNIER_ECHEC FROM {AU}.TASK_HISTORY "
                             f"WHERE SCHEDULED_TIME >= {since}{sc('DATABASE_NAME', 'SCHEMA_NAME')} GROUP BY 1, 2, 3", windowed=True)
    R["serverless"] = read("SERVERLESS_TASK_HISTORY", f"SELECT DATABASE_NAME, SCHEMA_NAME, TASK_NAME, SUM(CREDITS_USED) AS CREDITS "
                           f"FROM {AU}.SERVERLESS_TASK_HISTORY WHERE START_TIME >= {since}{sc('DATABASE_NAME', 'SCHEMA_NAME')} GROUP BY 1, 2, 3",
                           windowed=True)
    # STREAMS est absent de la table des rôles de base : lecture tentée, SHOW STREAMS en repli au niveau 2
    R["streams"] = read("STREAMS", f"SELECT STREAM_CATALOG, STREAM_SCHEMA, STREAM_NAME, OWNER, TABLE_NAME, SOURCE_TYPE, MODE, STALE_AFTER "
                        f"FROM {AU}.STREAMS WHERE DELETED IS NULL{sc('STREAM_CATALOG', 'STREAM_SCHEMA')}")
    R["pipes"] = read("PIPES", f"SELECT PIPE_CATALOG, PIPE_SCHEMA, PIPE_NAME, PIPE_OWNER, IS_AUTOINGEST_ENABLED, DEFINITION "
                      f"FROM {AU}.PIPES WHERE DELETED IS NULL{sc('PIPE_CATALOG', 'PIPE_SCHEMA')}")
    R["copy"] = read("COPY_HISTORY", f"SELECT TABLE_ID, TABLE_CATALOG_NAME, TABLE_SCHEMA_NAME, TABLE_NAME, PIPE_CATALOG_NAME, PIPE_SCHEMA_NAME, "
                     f"PIPE_NAME, SPLIT_PART(STAGE_LOCATION, '/', 1) AS LOC1, SPLIT_PART(STAGE_LOCATION, '/', 3) AS LOC3, COUNT(*) AS FICHIERS, "
                     f"SUM(ROW_COUNT) AS LIGNES, COUNT_IF(UPPER(STATUS) IN ('LOAD FAILED', 'PARTIALLY LOADED')) AS ECHECS, "
                     f"MAX(LAST_LOAD_TIME) AS DERNIER FROM {AU}.COPY_HISTORY WHERE LAST_LOAD_TIME >= {since}"
                     f"{sc('TABLE_CATALOG_NAME', 'TABLE_SCHEMA_NAME')} GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9", windowed=True)
    R["stages"] = read("STAGES", f"SELECT STAGE_CATALOG, STAGE_SCHEMA, STAGE_NAME, STAGE_OWNER, STAGE_TYPE, STAGE_REGION, STORAGE_INTEGRATION, "
                       f"SPLIT_PART(STAGE_URL, '/', 1) AS URL1, SPLIT_PART(STAGE_URL, '/', 3) AS URL3 FROM {AU}.STAGES "
                       f"WHERE DELETED IS NULL{sc('STAGE_CATALOG', 'STAGE_SCHEMA')}")
    R["databases"] = read("DATABASES", f"SELECT DATABASE_NAME, DATABASE_OWNER, TYPE FROM {AU}.DATABASES WHERE DELETED IS NULL")
    R["transfers"] = read("DATA_TRANSFER_HISTORY", f"SELECT TRANSFER_TYPE, TARGET_CLOUD, TARGET_REGION, SUM(BYTES_TRANSFERRED) AS OCTETS "
                          f"FROM {AU}.DATA_TRANSFER_HISTORY WHERE START_TIME >= {since} GROUP BY 1, 2, 3 ORDER BY 4 DESC", windowed=True)
    R["dt_refresh"] = read("DYNAMIC_TABLE_REFRESH_HISTORY", f"SELECT DATABASE_NAME, SCHEMA_NAME, NAME, COUNT_IF(STATE IN ('SUCCEEDED', 'FAILED')) AS N, "
                           f"COUNT_IF(STATE = 'FAILED') AS ECHECS, COUNT_IF(STATE = 'UPSTREAM_FAILED') AS AMONT, MAX(REFRESH_END_TIME) AS DERNIERE, "
                           f"MODE(TARGET_LAG_SEC) AS LAG FROM {AU}.DYNAMIC_TABLE_REFRESH_HISTORY WHERE REFRESH_START_TIME >= {since}"
                           f"{sc('DATABASE_NAME', 'SCHEMA_NAME')} GROUP BY 1, 2, 3", windowed=True)

    # ---- niveau 1, temps réel : INFORMATION_SCHEMA de la base scannée (métadonnées seulement, mêmes droits que la lecture)
    # ACCOUNT_USAGE a jusqu'à 3 h de retard : un objet créé entre-temps n'y figure pas encore. Base par base : rien pour « tout le compte ».
    def rt(view: str, sql: str):
        res = c.read(f"{db}.INFORMATION_SCHEMA.{view}", lambda: q(sql), latence_max_h=0)
        if res is not None:
            last()["raison"] = TEMPS_REEL
        return res

    if db:
        isch = f"{_qid(db)}.INFORMATION_SCHEMA"

        def rsc(schcol: str) -> str:
            return f" WHERE {schcol} <> 'INFORMATION_SCHEMA'" + (f" AND {schcol} IN (" + ", ".join(_lit(s) for s in schs) + ")" if schs else "")
        R["rt_tables"] = rt("TABLES", f"SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_OWNER, TABLE_TYPE, IS_TRANSIENT, CLUSTERING_KEY, "
                                      f"AUTO_CLUSTERING_ON, ROW_COUNT, BYTES, RETENTION_TIME, CREATED, LAST_ALTERED, LAST_DDL, IS_ICEBERG, IS_DYNAMIC "
                                      f"FROM {isch}.TABLES{rsc('TABLE_SCHEMA')}")
        R["rt_columns"] = rt("COLUMNS", f"SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, "
                                        f"ORDINAL_POSITION FROM {isch}.COLUMNS{rsc('TABLE_SCHEMA')} ORDER BY 1, 2, 3, ORDINAL_POSITION")
        R["rt_views"] = rt("VIEWS", f"SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_OWNER, IS_SECURE, LAST_ALTERED, VIEW_DEFINITION "
                                    f"FROM {isch}.VIEWS{rsc('TABLE_SCHEMA')}")
        R["rt_functions"] = rt("FUNCTIONS", f"SELECT FUNCTION_CATALOG, FUNCTION_SCHEMA, FUNCTION_NAME, FUNCTION_OWNER, FUNCTION_LANGUAGE, "
                                            f"IS_EXTERNAL, API_INTEGRATION, EXTERNAL_ACCESS_INTEGRATIONS, FUNCTION_DEFINITION, LAST_ALTERED "
                                            f"FROM {isch}.FUNCTIONS{rsc('FUNCTION_SCHEMA')}")
        R["rt_procedures"] = rt("PROCEDURES", f"SELECT PROCEDURE_CATALOG, PROCEDURE_SCHEMA, PROCEDURE_NAME, PROCEDURE_OWNER, PROCEDURE_LANGUAGE, "
                                              f"EXTERNAL_ACCESS_INTEGRATIONS, PROCEDURE_DEFINITION, LAST_ALTERED "
                                              f"FROM {isch}.PROCEDURES{rsc('PROCEDURE_SCHEMA')}")
        # stages et pipes : INFORMATION_SCHEMA plutôt que SHOW, pour ne jamais lire le chemin des stages (SPLIT_PART côté serveur)
        R["rt_stages"] = rt("STAGES", f"SELECT STAGE_CATALOG, STAGE_SCHEMA, STAGE_NAME, STAGE_OWNER, STAGE_TYPE, STAGE_REGION, "
                                      f"SPLIT_PART(STAGE_URL, '/', 1) AS URL1, SPLIT_PART(STAGE_URL, '/', 3) AS URL3 "
                                      f"FROM {isch}.STAGES{rsc('STAGE_SCHEMA')}")
        R["rt_pipes"] = rt("PIPES", f"SELECT PIPE_CATALOG, PIPE_SCHEMA, PIPE_NAME, PIPE_OWNER, IS_AUTOINGEST_ENABLED, DEFINITION "
                                    f"FROM {isch}.PIPES{rsc('PIPE_SCHEMA')}")

    # ---- niveau 2 : requêtes, lectures par objet, politiques (GOVERNANCE_VIEWER), compléments SHOW
    excl = " AND ".join(f"QUERY_TYPE NOT LIKE {_lit(p)}" for p in EXCLU_QUERY_TYPE)
    qh_where = f"START_TIME >= {since} AND {not_canot} AND QUERY_PARAMETERIZED_HASH IS NOT NULL AND {excl}"
    R["qh"] = read("QUERY_HISTORY", f"SELECT QUERY_PARAMETERIZED_HASH AS H, ANY_VALUE(QUERY_TEXT) AS TXT, MODE(QUERY_TYPE) AS QUERY_TYPE, "
                   f"COUNT(*) AS N, SUM(TOTAL_ELAPSED_TIME) AS MS, SUM(BYTES_SCANNED) AS OCTETS, SUM(ROWS_UNLOADED) AS DECHARGEES, "
                   f"COUNT_IF(UPPER(EXECUTION_STATUS) <> 'SUCCESS') AS ECHECS, COUNT(DISTINCT USER_NAME) AS UTILISATEURS, "
                   f"MODE(USER_NAME) AS USER_NAME, MODE(ROLE_NAME) AS ROLE_NAME, MODE(WAREHOUSE_NAME) AS WAREHOUSE_NAME, "
                   f"MODE(QUERY_TAG) AS QUERY_TAG, MODE(DATABASE_NAME) AS DATABASE_NAME, MODE(SCHEMA_NAME) AS SCHEMA_NAME, "
                   f"MIN(START_TIME) AS PREMIERE, MAX(START_TIME) AS DERNIERE, COUNT(*) OVER () AS FAMILLES_TOTAL, "
                   f"SUM(COUNT(*)) OVER () AS EXEC_TOTAL, SUM(SUM(TOTAL_ELAPSED_TIME)) OVER () AS MS_TOTAL, "
                   f"MIN(MIN(START_TIME)) OVER () AS DU_TOTAL, MAX(MAX(START_TIME)) OVER () AS AU_TOTAL "
                   f"FROM {AU}.QUERY_HISTORY WHERE {qh_where} GROUP BY QUERY_PARAMETERIZED_HASH "
                   f"ORDER BY MS DESC NULLS LAST LIMIT {FAMILLES_MAX}", lvl=2, windowed=True)

    ah_state = {"semaines": 0, "du": None, "echec": None}

    def access_history_week(a: datetime, b: datetime) -> str:
        """Une semaine d'ACCESS_HISTORY, agrégée par objet : lectures (objets de base, vues résolues) et écritures. Canot exclu."""
        pref = f" AND STARTSWITH(f.value:\"objectName\"::STRING, {_lit(db + '.')})" if db else ""
        mine = (f"ah.QUERY_ID NOT IN (SELECT QUERY_ID FROM {AU}.QUERY_HISTORY WHERE QUERY_TAG = 'canot' "
                f"AND START_TIME >= {_ts(a - timedelta(days=1))} AND START_TIME < {_ts(b)})")

        def part(sens: str, col: str) -> str:
            return (f"SELECT '{sens}' AS SENS, f.value:\"objectId\"::NUMBER AS OBJET_ID, f.value:\"objectName\"::STRING AS OBJET, "
                    f"f.value:\"objectDomain\"::STRING AS DOMAINE, COUNT(DISTINCT ah.QUERY_ID) AS N, MAX(ah.QUERY_START_TIME) AS DERNIERE "
                    f"FROM {AU}.ACCESS_HISTORY ah, LATERAL FLATTEN(input => ah.{col}) f WHERE ah.QUERY_START_TIME >= {_ts(a)} "
                    f"AND ah.QUERY_START_TIME < {_ts(b)}{pref} AND {mine} GROUP BY 2, 3, 4")
        return part("L", "BASE_OBJECTS_ACCESSED") + " UNION ALL " + part("E", "OBJECTS_MODIFIED")

    def access_history():
        out = []
        for k in range(math.ceil(days / 7)):  # du plus récent au plus ancien : un échec en route laisse la période récente
            b, a = now - timedelta(days=7 * k), max(start, now - timedelta(days=7 * (k + 1)))
            try:
                out += q(access_history_week(a, b))
            except Exception as e:  # noqa: BLE001
                if k == 0:
                    raise
                ah_state["echec"] = e
                break
            ah_state["semaines"], ah_state["du"] = k + 1, a
        return out
    R["access"] = read("ACCESS_HISTORY", access_history, lvl=2, windowed=True)
    if R["access"] is not None and ah_state["echec"] is not None:
        last()["fenetre"]["du"] = _iso(ah_state["du"])
        last()["raison"] = (f"partielle : {ah_state['semaines']}/{math.ceil(days / 7)} semaines lues, "
                            f"arrêt sur {classify_failure(ah_state['echec'])}")

    R["policies"] = read("POLICY_REFERENCES", f"SELECT POLICY_DB, POLICY_SCHEMA, POLICY_NAME, POLICY_KIND, REF_DATABASE_NAME, REF_SCHEMA_NAME, "
                         f"REF_ENTITY_NAME, REF_ENTITY_DOMAIN, REF_COLUMN_NAME, POLICY_STATUS FROM {AU}.POLICY_REFERENCES "
                         f"WHERE REF_DATABASE_NAME IS NOT NULL AND REF_ENTITY_DOMAIN NOT IN ('USER', 'ACCOUNT')"
                         f"{sc('REF_DATABASE_NAME', 'REF_SCHEMA_NAME')}", lvl=2)
    R["tags"] = read("TAG_REFERENCES", f"SELECT TAG_DATABASE, TAG_SCHEMA, TAG_NAME, TAG_VALUE, OBJECT_DATABASE, OBJECT_SCHEMA, OBJECT_NAME, "
                     f"DOMAIN, COLUMN_NAME FROM {AU}.TAG_REFERENCES WHERE OBJECT_DELETED IS NULL AND OBJECT_DATABASE IS NOT NULL "
                     f"AND DOMAIN NOT IN ('USER', 'ROLE', 'ACCOUNT'){sc('OBJECT_DATABASE', 'OBJECT_SCHEMA')}", lvl=2)
    where = f"IN DATABASE \"{db.replace(chr(34), chr(34) * 2)}\"" if db else "IN ACCOUNT"
    R["show_wh"] = read("SHOW WAREHOUSES", "SHOW WAREHOUSES", lvl=2)
    R["show_dt"] = read("SHOW DYNAMIC TABLES", f"SHOW DYNAMIC TABLES {where}", lvl=2)
    R["show_ice"] = read("SHOW ICEBERG TABLES", f"SHOW ICEBERG TABLES {where}", lvl=2)
    R["show_ext"] = read("SHOW EXTERNAL TABLES", f"SHOW EXTERNAL TABLES {where}", lvl=2)
    # SHOW STREAMS / SHOW TASKS : repli si la vue ACCOUNT_USAGE est refusée, et recoupement en temps réel sinon
    R["show_streams"] = read("SHOW STREAMS", f"SHOW STREAMS {where}", lvl=2)
    R["show_tasks"] = read("SHOW TASKS", f"SHOW TASKS {where}", lvl=2)

    # ---- niveau 3 : droits, utilisateurs (pseudonymisés), outils, partages (SECURITY_VIEWER)
    R["role_tree"] = read("GRANTS_TO_ROLES (hiérarchie des rôles)", f"SELECT NAME AS ROLE, GRANTEE_NAME AS PARENT FROM {AU}.GRANTS_TO_ROLES "
                          f"WHERE GRANTED_ON IN ('ROLE', 'DATABASE_ROLE') AND PRIVILEGE = 'USAGE' AND DELETED_ON IS NULL", lvl=3)
    R["grants"] = read("GRANTS_TO_ROLES (droits sur le périmètre)", f"SELECT GRANTED_TO, GRANTED_ON, PRIVILEGE, COUNT(*) AS N, "
                       f"COUNT(DISTINCT GRANTEE_NAME) AS BENEFICIAIRES FROM {AU}.GRANTS_TO_ROLES WHERE DELETED_ON IS NULL"
                       f"{sc('TABLE_CATALOG')} GROUP BY 1, 2, 3 ORDER BY 4 DESC", lvl=3)
    R["role_users"] = read("GRANTS_TO_USERS", f"SELECT g.ROLE AS ROLE, COUNT(DISTINCT g.GRANTEE_NAME) AS N FROM {AU}.GRANTS_TO_USERS g "
                           f"JOIN {AU}.USERS u ON u.NAME = g.GRANTEE_NAME AND u.DELETED_ON IS NULL WHERE g.DELETED_ON IS NULL GROUP BY g.ROLE", lvl=3)
    R["users"] = read("USERS", f"SELECT NAME, TYPE FROM {AU}.USERS WHERE DELETED_ON IS NULL", lvl=3)
    R["sessions"] = read("SESSIONS", f"SELECT CLIENT_APPLICATION_ID AS OUTIL, COUNT(*) AS SESSIONS, COUNT(DISTINCT USER_NAME) AS UTILISATEURS, "
                         f"MAX(CREATED_ON) AS DERNIERE FROM {AU}.SESSIONS WHERE CREATED_ON >= {since} GROUP BY 1 ORDER BY 2 DESC", lvl=3, windowed=True)
    hashes = [r["H"] for r in R["qh"] or [] if r.get("H")]
    if hashes and level >= 3:
        R["family_tools"] = read("QUERY_HISTORY × SESSIONS (outil par famille)",
                                 f"SELECT q.QUERY_PARAMETERIZED_HASH AS H, s.CLIENT_APPLICATION_ID AS OUTIL, COUNT(*) AS N "
                                 f"FROM {AU}.QUERY_HISTORY q JOIN {AU}.SESSIONS s ON s.SESSION_ID = q.SESSION_ID WHERE q.START_TIME >= {since} "
                                 f"AND COALESCE(q.QUERY_TAG, '') <> 'canot' AND q.QUERY_PARAMETERIZED_HASH IN ("
                                 + ", ".join(_lit(h) for h in hashes) + ") GROUP BY 1, 2", lvl=3, windowed=True)
    R["shares"] = read("SHARES", f"SELECT NAME, OWNER, DATABASE_NAME, TARGET_ACCOUNTS, LISTING_GLOBAL_NAME FROM {AU}.SHARES "
                       f"WHERE DELETED_ON IS NULL", lvl=3)

    return _assemble(R, c, db=db, schs=schs, level=level, salt=salt, days=days, now=now, fen=fen, in_scope=in_scope)


# --- assemblage (pur) -------------------------------------------------------------------------------------

def _assemble(R: dict, c: Collector, *, db, schs, level, salt, days, now, fen, in_scope) -> tuple[dict, list[dict]]:
    lu = {k: v is not None for k, v in R.items()}

    def rt_mark(o: dict, r: dict, au_key: str, source: str = "information_schema") -> dict:
        """Objet vu seulement en temps réel : `recent` si la vue ACCOUNT_USAGE correspondante a bien été lue (sinon simple repli)."""
        if r.get("_rt"):
            o["detail"]["inventaire"] = source  # « source » est déjà pris par les flux (type de la source)
            if lu.get(au_key):
                o["detail"]["recent"] = True
        return o

    # recoupement temps réel des objets : par identifiant, les lignes absentes d'ACCOUNT_USAGE sont ajoutées et marquées
    def key3(*cols):
        return lambda r: _fqn(*(r.get(x) for x in cols))
    stage_rows = _rt_merge(R["stages"], R.get("rt_stages"), key3("STAGE_CATALOG", "STAGE_SCHEMA", "STAGE_NAME"))
    pipe_rows = _rt_merge(R["pipes"], R.get("rt_pipes"), key3("PIPE_CATALOG", "PIPE_SCHEMA", "PIPE_NAME"))
    fn_rows = _rt_merge(R["functions"], R.get("rt_functions"), key3("FUNCTION_CATALOG", "FUNCTION_SCHEMA", "FUNCTION_NAME"))
    proc_rows = _rt_merge(R["procedures"], R.get("rt_procedures"), key3("PROCEDURE_CATALOG", "PROCEDURE_SCHEMA", "PROCEDURE_NAME"))
    mv_names = {_fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"]) for r in (R["tables"] or []) + (R.get("rt_tables") or [])
                if r.get("TABLE_TYPE") == "MATERIALIZED VIEW"}
    # VIEWS liste aussi les vues matérialisées (vérifié sur un compte réel) : elles sont décrites une fois, comme vue_materialisee
    not_mv = key3("TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME")
    view_rows = _rt_merge([r for r in R["views"] if not_mv(r) not in mv_names] if lu["views"] else None,
                          [r for r in R.get("rt_views") or [] if not_mv(r) not in mv_names] if lu.get("rt_views") else None, not_mv)

    # dépendances déclarées : objet référent -> objets référencés
    deps: dict[str, list[str]] = defaultdict(list)
    for r in R["deps"] or []:
        src = _fqn(r["REFERENCING_DATABASE"], r["REFERENCING_SCHEMA"], r["REFERENCING_OBJECT_NAME"])
        dst = _fqn(r["REFERENCED_DATABASE"], r["REFERENCED_SCHEMA"], r["REFERENCED_OBJECT_NAME"])
        if dst not in deps[src]:
            deps[src].append(dst)

    # ---- stages (servent à résoudre les références des autres objets)
    objets: list[dict] = []
    stage_bucket: dict[str, str | None] = {}
    for r in stage_rows or []:
        sid = _fqn(r["STAGE_CATALOG"], r["STAGE_SCHEMA"], r["STAGE_NAME"])
        bucket, _ = _location(r.get("URL1"), r.get("URL3"))
        ext = "EXTERNAL" in str(r.get("STAGE_TYPE") or "").upper()
        stage_bucket[sid] = bucket if ext else None
        objets.append(rt_mark(_new_objet("stage", sid, r["STAGE_SCHEMA"], proprietaire=r.get("STAGE_OWNER"), externe=ext,
                                         dependances_externes=[bucket] if bucket else [],
                                         detail={"type": r.get("STAGE_TYPE"), "region": r.get("STAGE_REGION"),
                                                 "integration": r.get("STORAGE_INTEGRATION")}), r, "stages"))

    def externals(stages: list[str], hosts: list[str]) -> list[str]:
        return sorted(set(hosts) | {stage_bucket[s] for s in stages if stage_bucket.get(s)})

    def loc_bucket(l1, l3, dbn, sch) -> str | None:
        bucket, stage = _location(l1, l3, dbn, sch)
        return bucket or stage_bucket.get(stage or "")

    # ---- tables
    trows = [r for r in R["tables"] or [] if r.get("TABLE_TYPE") not in ("VIEW", "MATERIALIZED VIEW")]
    tsm = {r["ID"]: r for r in R["tsm"] or []}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for r in trows:
        by_name[_fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])].append(r)
    known_ids = {x["TABLE_ID"] for x in trows}
    for r in tsm.values():  # supprimées hors fenêtre mais encore facturées (clones, fail-safe) ; tout, si TABLES est refusée
        if r["ID"] not in known_ids and (r.get("DELETED") or R["tables"] is None):
            by_name[_fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])].append(
                {"TABLE_ID": r["ID"], "TABLE_CATALOG": r["TABLE_CATALOG"], "TABLE_SCHEMA": r["TABLE_SCHEMA"], "TABLE_NAME": r["TABLE_NAME"],
                 "TABLE_TYPE": "BASE TABLE", "IS_TRANSIENT": r.get("IS_TRANSIENT"), "DELETED": r.get("TABLE_DROPPED") if r.get("DELETED") else None,
                 "_tsm_seul": True})
    # temps réel : une table vivante dans INFORMATION_SCHEMA sans version vivante dans ACCOUNT_USAGE est ajoutée (créée ou recréée récemment)
    rt_by_name = {_fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"]): r for r in R.get("rt_tables") or []
                  if r.get("TABLE_TYPE") not in ("VIEW", "MATERIALIZED VIEW")}
    for name, r in rt_by_name.items():
        live = [x for x in by_name.get(name, []) if not x.get("DELETED")]
        if not live:
            by_name[name].append({**r, "TABLE_ID": None, "DELETED": None, "_rt": True})
        elif all(x.get("_tsm_seul") for x in live):  # TABLES refusée : le type, le propriétaire, etc. viennent du temps réel
            for x in live:
                by_name[name][by_name[name].index(x)] = {**r, "TABLE_ID": x["TABLE_ID"], "DELETED": None, "_rt_complete": True}
    tables: list[dict] = []
    by_tid: dict = {}
    for name, rows in sorted(by_name.items()):
        live = [r for r in rows if not r.get("DELETED")]
        for r in rows:
            tid = name if (len(rows) == 1 or not r.get("DELETED")) else f"{name}#{r['TABLE_ID']}"
            t = _new_table(tid, r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])
            flags = t["flags"]
            if _yes(r.get("IS_TRANSIENT")):
                flags.append("transitoire")
            for col, flag in (("IS_DYNAMIC", "dynamique"), ("IS_HYBRID", "hybride"), ("IS_ICEBERG", "iceberg"), ("AUTO_CLUSTERING_ON", "clustering_auto")):
                if _yes(r.get(col)):
                    flags.append(flag)
            if r.get("TABLE_TYPE") == "EVENT TABLE":
                flags.append("evenements")
            if live and not r.get("DELETED") and len(rows) > 1:
                flags.append("recreee")
            if r.get("DELETED"):
                flags.append("supprimee")
            t.update(lignes=_int(r.get("ROW_COUNT")), retention_jours=_num(r.get("RETENTION_TIME")), derniere_ddl=_iso(r.get("LAST_DDL")),
                     proprietaire=r.get("TABLE_OWNER"), supprimee_le=_iso(r.get("DELETED")),
                     clustering=[r["CLUSTERING_KEY"]] if r.get("CLUSTERING_KEY") else [])
            t["detail"] = {"table_id": r["TABLE_ID"], "creee_le": _iso(r.get("CREATED")), "octets_scan": _int(r.get("BYTES"))}
            if r.get("_tsm_seul"):
                t["detail"]["type_non_lu"] = True
            if r.get("_rt_complete"):
                t["detail"].update(inventaire="information_schema", modifiee_le=_iso(r.get("LAST_ALTERED")))
            if r.get("_rt"):  # vue seulement en temps réel : volumes de la vue temps réel, historique ACCOUNT_USAGE inconnu (None, jamais 0)
                t["octets_actifs"] = _int(r.get("BYTES"))
                t["detail"].update(source="information_schema", modifiee_le=_iso(r.get("LAST_ALTERED")),
                                   octets_source="INFORMATION_SCHEMA.TABLES.BYTES",
                                   historique_non_lu=TROP_RECENTE if lu["tables"] else "ACCOUNT_USAGE.TABLES non lue")
                if lu["tables"]:
                    flags.append("recente")
            m = tsm.get(r["TABLE_ID"]) if r["TABLE_ID"] is not None else None
            if m:
                tt, fs = _int(m.get("TIME_TRAVEL_BYTES")), _int(m.get("FAILSAFE_BYTES"))
                t["octets_actifs"] = _int(m["ACTIVE_BYTES"])
                t["octets_historique"] = None if tt is None and fs is None else (tt or 0) + (fs or 0)
                t["detail"]["octets_retenus_clone"] = _int(m.get("RETAINED_FOR_CLONE_BYTES"))
                if m.get("CLONE_GROUP_ID") is not None and m["CLONE_GROUP_ID"] != m["ID"]:
                    flags.append("clone")
                    t["detail"]["clone_groupe_id"] = m["CLONE_GROUP_ID"]
            ttype = r.get("TABLE_TYPE")
            t["type"] = ("EXTERNE" if ttype == "EXTERNAL TABLE" else "TEMPORAIRE" if ttype == "TEMPORARY TABLE" else
                         "ICEBERG" if "iceberg" in flags else "CLONE" if "clone" in flags else "TRANSITOIRE" if "transitoire" in flags else "TABLE")
            tables.append(t)
            if r["TABLE_ID"] is not None:
                by_tid[r["TABLE_ID"]] = t
    for t in tables:
        g = t["detail"].get("clone_groupe_id")
        if g is not None and g in by_tid:
            t["clone_de"] = by_tid[g]["id"]

    for r in R["columns"] or []:
        t = by_tid.get(r["TABLE_ID"])
        if t is not None:
            t["colonnes"].append({"nom": r["COLUMN_NAME"], "type": _coltype(r)})

    # recoupement des colonnes et des types avec la vue temps réel (version vivante seulement) : l'état présent l'emporte, l'écart est dit
    rt_cols: dict[str, list[dict]] = defaultdict(list)
    for r in R.get("rt_columns") or []:
        rt_cols[_fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])].append({"nom": r["COLUMN_NAME"], "type": _coltype(r)})
    for t in tables:
        name = _fqn(t["base"], t["schema"], t["nom"])
        if t["id"] != name or t["supprimee_le"]:
            continue
        d = t["detail"]
        live = rt_by_name.get(name)
        if live is not None and d.get("source") != "information_schema":
            d["vue_temps_reel"] = True
            au_type = next((x.get("TABLE_TYPE") for x in by_name[name] if x.get("TABLE_ID") == d["table_id"]), None)
            if au_type and live.get("TABLE_TYPE") and au_type != live["TABLE_TYPE"]:
                d["ecart_type"] = f"{au_type} dans ACCOUNT_USAGE, {live['TABLE_TYPE']} en temps réel"
            rt_bytes = _int(live.get("BYTES"))
            d["octets_temps_reel"] = rt_bytes
            if t["octets_actifs"] == 0 and rt_bytes:  # métriques de stockage pas encore calculées pour une table récente
                d["volume_en_retard"] = True
        cols = rt_cols.get(name)
        if cols is None:
            continue
        if t["colonnes"] and t["colonnes"] != cols:
            before, after = {x["nom"]: x["type"] for x in t["colonnes"]}, {x["nom"]: x["type"] for x in cols}
            parts = [f"{n} {what}" for n, what in ((len(after.keys() - before.keys()), "ajoutée(s)"),
                                                     (len(before.keys() - after.keys()), "retirée(s)"),
                                                     (sum(before[k] != after[k] for k in before.keys() & after.keys()), "de type changé"))
                     if n]
            d["ecart_colonnes"] = "colonnes en temps réel : " + (", ".join(parts) if parts else "ordre changé")
        t["colonnes"] = cols

    dml = {r["TABLE_ID"]: r for r in R["dml"] or []}
    pruning = {r["TABLE_ID"]: r for r in R["pruning"] or []}
    clus = {r["TABLE_ID"]: r for r in R["clustering"] or []}
    copy_by_table: dict = defaultdict(list)
    for r in R["copy"] or []:
        copy_by_table[r["TABLE_ID"]].append(r)

    # ACCESS_HISTORY : lectures (objets de base) et écritures par objet ; inutilisable s'il est vide alors que l'élagage montre des lectures
    reads, writes = defaultdict(lambda: [0, None]), defaultdict(lambda: [0, None])
    for r in R["access"] or []:
        acc = (reads if r["SENS"] == "L" else writes)
        for key in (r.get("OBJET_ID"), r.get("OBJET")):
            if key is not None:
                acc[key][0] += _int(r["N"]) or 0
                acc[key][1] = max(filter(None, (acc[key][1], _iso(r.get("DERNIERE")))), default=None)
    pruning_reads = sum(_int(r["N"]) or 0 for r in R["pruning"] or [])
    ah_rows = len(R["access"] or [])
    ah_ok = lu["access"] and (ah_rows > 0 or (lu["pruning"] and pruning_reads == 0))
    if level < 2:
        edition = "non déterminée (ACCESS_HISTORY non demandé)"
    elif not lu["access"]:
        edition = "non déterminée (Standard ou droit absent)"
    elif not ah_ok:
        edition = "non déterminée (ACCESS_HISTORY vide alors que des lectures sont observées ailleurs)"
    elif ah_rows == 0:  # vide, corroboré par l'élagage : lectures à 0 admises, édition non prouvée
        edition = "non déterminée (ACCESS_HISTORY lisible, sans ligne)"
    else:
        edition = "Enterprise ou supérieure"

    pol_by_table, tag_by_table = defaultdict(list), defaultdict(list)
    policies: dict[str, dict] = {}
    for r in R["policies"] or []:
        pid = _fqn(r["POLICY_DB"], r["POLICY_SCHEMA"], r["POLICY_NAME"])
        target = _fqn(r["REF_DATABASE_NAME"], r["REF_SCHEMA_NAME"], r["REF_ENTITY_NAME"])
        label = f"{r['POLICY_KIND']}:{pid}"
        if label not in pol_by_table[target]:
            pol_by_table[target].append(label)
        kind = "politique_lignes" if str(r["POLICY_KIND"]).upper() in ("ROW_ACCESS_POLICY", "AGGREGATION_POLICY", "JOIN_POLICY") else "politique_masquage"
        p = policies.setdefault(pid, _new_objet(kind, pid, r["POLICY_SCHEMA"], detail={"type": r["POLICY_KIND"], "colonnes_n": 0}))
        if target not in p["reference"]:
            p["reference"].append(target)
        if r.get("REF_COLUMN_NAME"):
            p["detail"]["colonnes_n"] += 1
    tags: dict[str, dict] = {}
    tag_values: dict[str, set] = defaultdict(set)
    for r in R["tags"] or []:
        tag = _fqn(r["TAG_DATABASE"], r["TAG_SCHEMA"], r["TAG_NAME"])
        target = _fqn(r["OBJECT_DATABASE"], r["OBJECT_SCHEMA"], r["OBJECT_NAME"])
        hv = pseudo(r.get("TAG_VALUE"), salt, "v")
        label = tag + (f"={hv}" if hv else "") + (f" ({r['COLUMN_NAME']})" if r.get("COLUMN_NAME") else "")
        tag_by_table[target].append(label)
        o = tags.setdefault(tag, _new_objet("etiquette", tag, r["TAG_SCHEMA"], detail={"references_n": 0}))
        o["detail"]["references_n"] += 1
        if target not in o["reference"]:
            o["reference"].append(target)
        if hv:
            tag_values[tag].add(hv)
    for tag, o in tags.items():
        o["detail"]["valeurs_distinctes_n"] = len(tag_values[tag])

    ice = {_fqn(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"), r.get("NAME")): r for r in R.get("show_ice") or []}
    ext = {_fqn(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"), r.get("NAME")): r for r in R.get("show_ext") or []}

    for t in tables:
        tid, d = t["detail"]["table_id"], t["detail"]
        name = _fqn(t["base"], t["schema"], t["nom"])
        if d.get("source") == "information_schema":
            # historique ACCOUNT_USAGE pas encore disponible : rien n'est déduit (ni 0 lecture, ni 0 chargement) ; une lecture
            # ou une écriture déjà visible dans ACCESS_HISTORY, par son nom, reste une observation
            d.update(requetes_pruning_n=None, dernier_scan_pruning=None, jamais_lu_pruning=None, jamais_lu=None)
            if ah_ok and name in reads:
                t["n_lectures"], t["derniere_lecture"] = reads[name][0], reads[name][1]
                d["jamais_lu"] = False if reads[name][0] else None
            if ah_ok and name in writes:
                t["n_ecritures"], t["derniere_ecriture"] = writes[name][0], writes[name][1]
            t["politiques"], t["etiquettes"] = pol_by_table.get(name, []), sorted(tag_by_table.get(name, []))
            continue
        w = dml.get(tid)
        if w:
            t["derniere_ecriture"] = _iso(w["DERNIERE"])
            d.update(ecritures_intervalles_n=_int(w["INTERVALLES"]), lignes_ajoutees=_int(w["AJOUTEES"]),
                     lignes_modifiees=_int(w["MODIFIEES"]), lignes_retirees=_int(w["RETIREES"]))
        p = pruning.get(tid)
        d["requetes_pruning_n"] = (_int(p["N"]) if p else 0) if lu["pruning"] else None
        d["dernier_scan_pruning"] = _iso(p["DERNIERE"]) if p else None
        d["jamais_lu_pruning"] = (d["requetes_pruning_n"] == 0) if lu["pruning"] else None
        if ah_ok:
            alive = t["id"] == name  # une version supprimée ne se retrouve que par son identifiant interne
            rd = reads.get(tid) or (reads.get(name) if alive else None) or [0, None]
            wr = writes.get(tid) or (writes.get(name) if alive else None) or [0, None]
            t["n_lectures"], t["derniere_lecture"] = rd[0], rd[1]
            t["n_ecritures"] = wr[0]
            t["derniere_ecriture"] = max(filter(None, (t["derniere_ecriture"], wr[1])), default=None)
            d["jamais_lu"] = rd[0] == 0
        else:
            d["jamais_lu"] = None
        if clus.get(tid):
            d["credits_clustering"] = _num(clus[tid]["CREDITS"])
        loads = copy_by_table.get(tid, [])
        if lu["copy"]:
            d["fichiers_charges_n"] = sum(_int(r["FICHIERS"]) or 0 for r in loads)
            srcs = sorted({b for b in (loc_bucket(r.get("LOC1"), r.get("LOC3"), t["base"], t["schema"]) for r in loads) if b})
            if srcs:
                d["sources_chargement"] = ", ".join(srcs)
        if t["id"] == name:  # politiques, étiquettes et SHOW ne visent que la version vivante
            t["politiques"], t["etiquettes"] = pol_by_table.get(name, []), sorted(tag_by_table.get(name, []))
            if name in ice:
                d.update(volume_externe=ice[name].get("EXTERNAL_VOLUME_NAME"), catalogue=ice[name].get("CATALOG_NAME"))
            if name in ext:
                bucket, stage = _location(*_split_location(ext[name].get("LOCATION")), t["base"], t["schema"])
                stage = stage or _qualify(str(ext[name].get("STAGE") or "").lstrip("@"), t["base"], t["schema"])
                t["externe_uri"] = bucket or stage_bucket.get(stage or "")
                if stage:
                    d["stage"] = stage

    # ---- objets
    mv_rows = _rt_merge([r for r in R["tables"] or [] if r.get("TABLE_TYPE") == "MATERIALIZED VIEW" and not r.get("DELETED")] if lu["tables"] else None,
                        [r for r in R.get("rt_tables") or [] if r.get("TABLE_TYPE") == "MATERIALIZED VIEW"] if lu.get("rt_tables") else None,
                        key3("TABLE_CATALOG", "TABLE_SCHEMA", "TABLE_NAME"))
    for r in mv_rows or []:
        oid = _fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])
        m = tsm.get(r.get("TABLE_ID")) if r.get("TABLE_ID") is not None else None
        objets.append(rt_mark(_new_objet("vue_materialisee", oid, r["TABLE_SCHEMA"], langage="sql", proprietaire=r.get("TABLE_OWNER"),
                                         reference=deps.get(oid, []),
                                         detail={"lignes": _int(r.get("ROW_COUNT")), "octets_actifs": _int(m["ACTIVE_BYTES"]) if m else None}),
                              r, "tables"))
    for r in view_rows or []:
        oid = _fqn(r["TABLE_CATALOG"], r["TABLE_SCHEMA"], r["TABLE_NAME"])
        stages, hosts = _code_refs("SQL", r.get("VIEW_DEFINITION"), r["TABLE_CATALOG"], r["TABLE_SCHEMA"])
        dx = externals(stages, hosts)
        # vue récente : OBJECT_DEPENDENCIES ne la connaît pas encore ; ses références viennent de sa définition (analysée ici, puis jetée)
        ref = deps.get(oid, []) or ([x for x in _tables_in(r.get("VIEW_DEFINITION"), r["TABLE_CATALOG"], r["TABLE_SCHEMA"]) if x != oid]
                                    if r.get("_rt") else [])
        objets.append(rt_mark(_new_objet("vue", oid, r["TABLE_SCHEMA"], langage="sql", proprietaire=r.get("TABLE_OWNER"), externe=bool(dx),
                                         dependances_externes=dx, reference=ref,
                                         detail={"securisee": _yes(r.get("IS_SECURE")), "modifiee_le": _iso(r.get("LAST_ALTERED")),
                                                 "definition_visible": r.get("VIEW_DEFINITION") is not None,
                                                 **({"stages": ", ".join(stages)} if stages else {})}), r, "views"))
    for kind, rows, pre, defcol in (("fonction", fn_rows, "FUNCTION", "FUNCTION_DEFINITION"),
                                    ("procedure", proc_rows, "PROCEDURE", "PROCEDURE_DEFINITION")):
        seen: dict[str, dict] = {}
        for r in rows or []:
            oid = _fqn(r[f"{pre}_CATALOG"], r[f"{pre}_SCHEMA"], r[f"{pre}_NAME"])
            if oid in seen:  # surcharges : un objet par nom
                seen[oid]["detail"]["surcharges_n"] += 1
                continue
            lang, body = r.get(f"{pre}_LANGUAGE"), r.get(defcol)
            is_ext = _yes(r.get("IS_EXTERNAL"))  # fonction externe : la définition est l'URL du service, réduite à l'hôte
            stages, hosts = _code_refs(lang, body, r[f"{pre}_CATALOG"], r[f"{pre}_SCHEMA"])
            eai = r.get("EXTERNAL_ACCESS_INTEGRATIONS")
            dx = externals(stages, hosts)
            o = _new_objet(kind, oid, r[f"{pre}_SCHEMA"], langage=lang, proprietaire=r.get(f"{pre}_OWNER"),
                           externe=is_ext or bool(eai and str(eai).strip("[] ")), dependances_externes=dx,
                           reference=deps.get(oid, []), sql_dynamique=None if is_ext else _dynamic(lang, body),
                           detail={"surcharges_n": 1, "definition_visible": body is not None, "modifiee_le": _iso(r.get("LAST_ALTERED")),
                                   **({"integration_api": r["API_INTEGRATION"]} if r.get("API_INTEGRATION") else {}),
                                   **({"integrations_acces_externe": str(eai)} if eai and str(eai).strip("[] ") else {}),
                                   **({"stages": ", ".join(stages)} if stages else {})})
            rt_mark(o, r, "functions" if kind == "fonction" else "procedures")
            seen[oid] = o
            objets.append(o)

    # tâches : TASKS + historique + crédits serverless
    th = {_fqn(r["DATABASE_NAME"], r["SCHEMA_NAME"], r["NAME"]): r for r in R["task_history"] or []}
    sv = {_fqn(r["DATABASE_NAME"], r["SCHEMA_NAME"], r["TASK_NAME"]): r for r in R["serverless"] or []}
    task_rows = {_fqn(r["TASK_DATABASE"], r["TASK_SCHEMA"], r["TASK_NAME"]): r for r in R["tasks"] or []}
    for r in R.get("show_tasks") or []:  # SHOW TASKS : tâches absentes de TASKS (récentes, ou TASKS refusée)
        oid = _fqn(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"), r.get("NAME"))
        if oid not in task_rows and in_scope(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME")):
            task_rows[oid] = {"TASK_DATABASE": r.get("DATABASE_NAME"), "TASK_SCHEMA": r.get("SCHEMA_NAME"), "TASK_NAME": r.get("NAME"),
                              "TASK_OWNER": r.get("OWNER"), "WAREHOUSE": r.get("WAREHOUSE") or None, "SCHEDULE": r.get("SCHEDULE") or None,
                              "PREDECESSORS": r.get("PREDECESSORS"), "STATE": r.get("STATE"), "DEFINITION": r.get("DEFINITION"),
                              "CONDITION": r.get("CONDITION") or None, "LAST_SUSPENDED_REASON": r.get("LAST_SUSPENDED_REASON") or None,
                              "_rt": True}
    for oid in sorted(set(task_rows) | set(th)):
        r, h = task_rows.get(oid, {}), th.get(oid)
        hist = lu["task_history"] and not (r.get("_rt") and h is None)  # tâche récente sans historique : exécutions inconnues, pas 0
        dbn, sch = (oid.split(".") + [None])[:2]
        defn = r.get("DEFINITION")
        try:
            preds = json.loads(r["PREDECESSORS"]) if isinstance(r.get("PREDECESSORS"), str) else (r.get("PREDECESSORS") or [])
        except ValueError:
            preds = []
        call = re.match(r"\s*CALL\s+([\w$.\"]+)", defn or "", re.I)
        d = {"serverless": (not r.get("WAREHOUSE")) if r else None, "entrepot": r.get("WAREHOUSE"),
             "predecesseurs": ", ".join(str(p) for p in preds) or None, "condition": bool(r.get("CONDITION")) if r else None,
             "suspendue_pour": r.get("LAST_SUSPENDED_REASON"), "definition_visible": defn is not None,
             "dernier_echec": _iso(h["DERNIER_ECHEC"]) if h else None,
             "credits_serverless": _num(sv[oid]["CREDITS"]) if oid in sv else (0.0 if lu["serverless"] and r and not r.get("WAREHOUSE") else None)}
        if call:
            d["appelle"] = _qualify(call.group(1), dbn, sch)
        if r.get("_rt") and d["credits_serverless"] == 0.0:
            d["credits_serverless"] = None
        objets.append(rt_mark(_new_objet("tache", oid, sch, langage="sql", proprietaire=r.get("TASK_OWNER"), etat=r.get("STATE"),
                                         planification=r.get("SCHEDULE") or (("après " + d["predecesseurs"]) if d["predecesseurs"] else None),
                                         reference=_tables_in(defn, dbn, sch) if not call else [],
                                         executions_n=(_int(h["N"]) if h else 0) if hist else None,
                                         echecs_n=(_int(h["ECHECS"]) if h else 0) if hist else None,
                                         derniere_execution=_iso(h["DERNIERE"]) if h else None,
                                         sql_dynamique=_dynamic("SQL", defn), detail=d), r, "tasks", "show"))

    stream_rows = _rt_merge(R["streams"], [r for r in R.get("show_streams") or [] if in_scope(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"))]
                            if R.get("show_streams") is not None else None,
                            lambda r: _fqn(r.get("STREAM_CATALOG") or r.get("DATABASE_NAME"), r.get("STREAM_SCHEMA") or r.get("SCHEMA_NAME"),
                                           r.get("STREAM_NAME") or r.get("NAME")))
    for r in stream_rows or []:
        dbn, sch = r.get("STREAM_CATALOG") or r.get("DATABASE_NAME"), r.get("STREAM_SCHEMA") or r.get("SCHEMA_NAME")
        if not in_scope(dbn, sch):
            continue
        src = r.get("TABLE_NAME")
        objets.append(rt_mark(_new_objet("flux", _fqn(dbn, sch, r.get("STREAM_NAME") or r.get("NAME")), sch, proprietaire=r.get("OWNER"),
                                         reference=[src] if src and "privilege" not in str(src).lower() else [],
                                         detail={"source": r.get("SOURCE_TYPE"), "mode": r.get("MODE"), "perime_apres": _iso(r.get("STALE_AFTER"))}),
                              r, "streams", "show"))

    copy_by_pipe: dict = defaultdict(list)
    for r in R["copy"] or []:
        if r.get("PIPE_NAME"):
            copy_by_pipe[_fqn(r["PIPE_CATALOG_NAME"], r["PIPE_SCHEMA_NAME"], r["PIPE_NAME"])].append(r)
    for r in pipe_rows or []:
        oid = _fqn(r["PIPE_CATALOG"], r["PIPE_SCHEMA"], r["PIPE_NAME"])
        defn, loads = r.get("DEFINITION"), copy_by_pipe.get(oid, [])
        hist = lu["copy"] and not (r.get("_rt") and not loads)  # pipe récent sans chargement visible : inconnu, pas 0
        stages, hosts = _code_refs("SQL", defn, r["PIPE_CATALOG"], r["PIPE_SCHEMA"])
        for x in loads:
            bucket, stage = _location(x.get("LOC1"), x.get("LOC3"), r["PIPE_CATALOG"], r["PIPE_SCHEMA"])
            hosts += [bucket] if bucket else []
            stages += [stage] if stage else []
        target = re.search(r"\bCOPY\s+INTO\s+([\w$.\"]+)", defn or "", re.I)
        ref = sorted({_fqn(x["TABLE_CATALOG_NAME"], x["TABLE_SCHEMA_NAME"], x["TABLE_NAME"]) for x in loads}) or \
            ([_qualify(target.group(1), r["PIPE_CATALOG"], r["PIPE_SCHEMA"])] if target else [])
        dx = externals(sorted(set(stages)), sorted(set(hosts)))
        objets.append(rt_mark(_new_objet("pipe", oid, r["PIPE_SCHEMA"], proprietaire=r.get("PIPE_OWNER"), externe=bool(dx), dependances_externes=dx,
                                         reference=ref, executions_n=sum(_int(x["FICHIERS"]) or 0 for x in loads) if hist else None,
                                         echecs_n=sum(_int(x["ECHECS"]) or 0 for x in loads) if hist else None,
                                         derniere_execution=max(filter(None, (_iso(x["DERNIER"]) for x in loads)), default=None),
                                         detail={"auto_ingest": _yes(r.get("IS_AUTOINGEST_ENABLED")), "definition_visible": defn is not None,
                                                 **({"stages": ", ".join(sorted(set(stages)))} if stages else {})}), r, "pipes"))

    # tables dynamiques : TABLES (IS_DYNAMIC) + historique de rafraîchissement + SHOW DYNAMIC TABLES
    dtr = {_fqn(r["DATABASE_NAME"], r["SCHEMA_NAME"], r["NAME"]): r for r in R["dt_refresh"] or []}
    dts = {_fqn(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"), r.get("NAME")): r for r in R.get("show_dt") or []}
    dyn = {t["id"]: t for t in tables if "dynamique" in t["flags"] and not t["supprimee_le"]}
    for oid in sorted(set(dyn) | set(dtr) | {k for k, r in dts.items() if in_scope(r.get("DATABASE_NAME"), r.get("SCHEMA_NAME"))}):
        h, s, t = dtr.get(oid), dts.get(oid, {}), dyn.get(oid)
        lag = s.get("TARGET_LAG") or (f"{_int(h['LAG'])} s" if h and h.get("LAG") is not None else None)
        # vue seulement en temps réel : table récente (INFORMATION_SCHEMA), ou seulement SHOW DYNAMIC TABLES
        rt_src = ("information_schema" if t and t["detail"].get("source") == "information_schema"
                  else "show" if t is None and h is None and s else None)
        hist = lu["dt_refresh"] and not (rt_src and h is None)
        o = _new_objet("table_dynamique", oid, (oid.split(".") + [None])[1], langage="sql",
                       proprietaire=(t or {}).get("proprietaire") or s.get("OWNER"), etat=s.get("SCHEDULING_STATE"),
                       planification=lag, reference=deps.get(oid, []),
                       executions_n=(_int(h["N"]) if h else 0) if hist else None,
                       echecs_n=(_int(h["ECHECS"]) if h else 0) if hist else None,
                       derniere_execution=_iso(h["DERNIERE"]) if h else None,
                       detail={"rafraichissement": s.get("REFRESH_MODE"), "entrepot": s.get("WAREHOUSE"),
                               "echecs_amont_n": _int(h["AMONT"]) if h else None})
        objets.append(rt_mark(o, {"_rt": bool(rt_src)}, "tables", rt_src or ""))

    for r in R["databases"] or []:
        if str(r.get("TYPE") or "").upper() == "IMPORTED DATABASE" and r["DATABASE_NAME"] != "SNOWFLAKE":
            objets.append(_new_objet("base_importee", r["DATABASE_NAME"], proprietaire=r.get("DATABASE_OWNER"), externe=True,
                                     detail={"type": r.get("TYPE")}))
    for r in R["shares"] or []:
        if db and r.get("DATABASE_NAME") not in (db, None, ""):
            continue
        targets = [x for x in str(r.get("TARGET_ACCOUNTS") or "").split(",") if x.strip()]
        objets.append(_new_objet("partage", r["NAME"], proprietaire=r.get("OWNER"), externe=bool(targets),
                                 detail={"base": r.get("DATABASE_NAME") or None, "comptes_cibles_n": len(targets),
                                         "annonce": bool(r.get("LISTING_GLOBAL_NAME"))}))
    if ah_ok:  # écritures observées vers les stages (déchargements)
        for o in objets:
            if o["kind"] == "stage":
                o["detail"]["ecritures_observees_n"] = writes.get(o["id"], [0])[0]
    objets += list(policies.values()) + list(tags.values())

    # ---- consommation
    wh = {r.get("NAME"): r for r in R.get("show_wh") or []}
    par_entrepot = None
    if lu["wmh"] or wh:
        par_entrepot = []
        for r in R["wmh"] or []:
            par_entrepot.append({"entrepot": r["WAREHOUSE_NAME"], "credits": _num(r["CREDITS"]), "credits_attribues_requetes": _num(r["ATTRIBUES"])})
        known = {e["entrepot"] for e in par_entrepot}
        for name in sorted(n for n in wh if n not in known):
            par_entrepot.append({"entrepot": name, "credits": 0.0 if lu["wmh"] else None, "credits_attribues_requetes": None})
        for e in par_entrepot:
            w = wh.get(e["entrepot"])
            if w:
                e.update(taille=w.get("SIZE"), type=w.get("TYPE"), auto_suspend_s=_int(w.get("AUTO_SUSPEND")),
                         clusters_max=_int(w.get("MAX_CLUSTER_COUNT")), etat=w.get("STATE"))
    st = R["storage"][0] if R["storage"] and _int(R["storage"][0].get("JOURS")) else None  # aucun jour lu : non lu, pas 0
    qah = R["qah"] or []
    metering = {
        "par_service": [{"service": r["SERVICE_TYPE"], "credits": _num(r["FACTURES"]), "credits_utilises": _num(r["UTILISES"])}
                        for r in R["metering"]] if lu["metering"] else None,
        "par_entrepot": par_entrepot,
        "stockage_octets_moyens": _num(st["MOYEN"]) if st else None,
        "transferts": [{"cible": _fqn(r.get("TARGET_CLOUD"), r.get("TARGET_REGION")) or None, "type": r.get("TRANSFER_TYPE"),
                        "octets": _int(r["OCTETS"])} for r in R["transfers"]] if lu["transfers"] else None,
        "a_la_demande": None, "reserve": None, "fenetre_jours": days,
        "par_famille": [{"hash_plateforme": r["H"], "credits": _num(r["CREDITS"]), "n_exec": _int(r["N"])} for r in qah[:FAMILLES_MAX]]
        if lu["qah"] else None,
        "detail": {"stockage_tables_octets_moyens": _num(st["TABLES"]) if st else None,
                   "stockage_stages_octets_moyens": _num(st["STAGES"]) if st else None,
                   "failsafe_octets_moyens": _num(st["FAILSAFE"]) if st else None,
                   "stockage_jours": _int(st["JOURS"]) if st else None,
                   "credits_attribues_total": _num(qah[0]["CREDITS_TOTAL"]) if qah else (0.0 if lu["qah"] else None),
                   "familles_attribuees_total": _int(qah[0]["FAMILLES_TOTAL"]) if qah else (0 if lu["qah"] else None)},
    }

    # ---- familles brutes (texte SQL brut : uniquement ici) et totaux
    credits_by_hash = {r["H"]: _num(r["CREDITS"]) for r in qah}
    tools: dict[str, list] = defaultdict(list)
    for r in R.get("family_tools") or []:
        tools[r["H"]].append((_int(r["N"]) or 0, r.get("OUTIL")))
    user_type = {pseudo(r["NAME"], salt, "u"): r.get("TYPE") for r in R["users"] or []}
    raw: list[dict] = []
    for r in R["qh"] or []:
        u = pseudo(r.get("USER_NAME"), salt, "u")
        best = max(tools.get(r["H"], []), key=lambda x: (x[0], str(x[1])), default=(0, None))[1]
        raw.append({"_sql": r.get("TXT"), "hash_plateforme": r["H"], "n_exec": _int(r["N"]), "temps_ms": _num(r.get("MS")),
                    "credits": credits_by_hash.get(r["H"]), "octets": _int(r.get("OCTETS")), "app": _app(r.get("QUERY_TAG")),
                    "outil": best, "utilisateur": u, "role": pseudo(r.get("ROLE_NAME"), salt, "r"), "entrepot": r.get("WAREHOUSE_NAME"),
                    "utilisateurs_n": _int(r.get("UTILISATEURS")), "utilisateur_type": user_type.get(u) if lu["users"] else None,
                    "query_type": r.get("QUERY_TYPE"), "echecs_n": _int(r.get("ECHECS")), "lignes_dechargees": _int(r.get("DECHARGEES")),
                    "base": r.get("DATABASE_NAME"), "schema": r.get("SCHEMA_NAME"),
                    "premiere": _iso(r.get("PREMIERE")), "derniere": _iso(r.get("DERNIERE"))})
    qh = R["qh"] or []
    requetes = None if not lu["qh"] else {
        "familles_total": _int(qh[0]["FAMILLES_TOTAL"]) if qh else 0, "executions_total": _int(qh[0]["EXEC_TOTAL"]) if qh else 0,
        "temps_ms_total": _num(qh[0]["MS_TOTAL"]) if qh else 0, "familles_lues": len(qh), "familles_max": FAMILLES_MAX,
        "du": _iso(qh[0].get("DU_TOTAL")) if qh else None, "au": _iso(qh[0].get("AU_TOTAL")) if qh else None,
        "exclusions": "requêtes de Canot (QUERY_TAG='canot'), types " + ", ".join(EXCLU_QUERY_TYPE) + ", empreinte absente",
    }

    # ---- droits (niveau 3) : rôles en clair, utilisateurs seulement comptés
    droits = None
    if level >= 3:
        parents: dict[str, set] = defaultdict(set)
        for r in R["role_tree"] or []:
            parents[r["ROLE"]].add(r["PARENT"])
        direct = {r["ROLE"]: _int(r["N"]) or 0 for r in R["role_users"] or []}
        owners = {o["proprietaire"] for o in tables + objets if o.get("proprietaire")}
        names = sorted(owners | set(parents) | {p for ps in parents.values() for p in ps} | set(direct))

        def holder(role: str, seen: frozenset = frozenset()) -> bool:
            return direct.get(role, 0) > 0 or any(holder(p, seen | {role}) for p in parents.get(role, ()) if p not in seen)
        both = lu["role_tree"] and lu["role_users"]
        types = defaultdict(int)
        for r in R["users"] or []:
            types[r.get("TYPE") or "non renseigné"] += 1
        droits = {
            "roles": [{"nom": n, "utilisateurs_n": direct.get(n, 0) if lu["role_users"] else None, "parents": sorted(parents.get(n, ())),
                       "proprietaire_d_objets": n in owners, "titulaire": holder(n) if both else None} for n in names]
            if (lu["role_tree"] or lu["role_users"]) else None,
            "privileges_perimetre": [{"beneficiaire_type": r["GRANTED_TO"], "objet_type": r["GRANTED_ON"], "privilege": r["PRIVILEGE"],
                                      "n": _int(r["N"]), "beneficiaires_n": _int(r["BENEFICIAIRES"])} for r in R["grants"]] if lu["grants"] else None,
            "utilisateurs": {"n": len(R["users"]), "par_type": dict(sorted(types.items()))} if lu["users"] else None,
            "outils": [{"outil": r.get("OUTIL"), "sessions": _int(r["SESSIONS"]), "utilisateurs_n": _int(r["UTILISATEURS"]),
                        "derniere": _iso(r.get("DERNIERE"))} for r in R["sessions"]] if lu["sessions"] else None,
        }

    fraicheur = _fraicheur(c.lectures, tables, objets, db)
    perim = (f"base {db}" if db else "tout le compte") + (f" (schémas {', '.join(schs)})" if schs else "")
    perim += " ; consommation, stockage, requêtes et droits : tout le compte"
    snapshot = {
        "schema": "canot.scan/1", "plateforme": "snowflake", "as_of": _iso(now), "perimetre": perim, "edition": edition,
        "niveau_droits": level, "mode_sql": "lecture seule", "versions": {"canot": __version__, "sqlglot": sqlglot.__version__},
        "fenetre": fen, "lectures": c.lectures, "tables": tables, "objets": objets, "familles": [], "metering": metering,
        "rejouabilite": None, "requetes": requetes, "droits": droits, "fraicheur": fraicheur,
    }
    return snapshot, raw


def _fraicheur(lectures: list[dict], tables: list[dict], objets: list[dict], db: str | None) -> dict:
    """Ce que le recoupement en temps réel a vu et qu'ACCOUNT_USAGE ne montre pas encore ; latence des vues ACCOUNT_USAGE lues."""
    n_t = sum("recente" in t["flags"] for t in tables)
    n_o = sum(bool(o["detail"].get("recent")) for o in objets)
    lat = max((x["latence_max_h"] for x in lectures if x["statut"] == "lu" and x["vue"].startswith(AU + ".")
               and x["latence_max_h"] is not None), default=None)
    jusqu = f", dont la latence va jusqu'à {_hours(lat)} h selon les vues" if lat is not None else ""
    rt_tables = next((x for x in lectures if x["vue"] == f"{db}.INFORMATION_SCHEMA.TABLES"), None) if db else None
    if not db:
        texte = ("Recoupement en temps réel non fait : il se fait base par base, et le périmètre est tout le compte. "
                 "Les objets créés depuis moins que la latence d'ACCOUNT_USAGE" + (f" (jusqu'à {_hours(lat)} h selon les vues)" if lat is not None else "")
                 + " peuvent manquer.")
    elif rt_tables is None or rt_tables["statut"] != "lu":
        raison = (rt_tables or {}).get("raison") or "non tenté"
        texte = (f"Recoupement en temps réel non lu ({raison}) : les objets créés depuis moins que la latence d'ACCOUNT_USAGE peuvent manquer.")
    elif n_t or n_o:
        quoi = " et ".join(s for s in (f"{n_t} table{'s' if n_t > 1 else ''}" if n_t else "",
                                       f"{n_o} objet{'s' if n_o > 1 else ''}" if n_o else "") if s)
        texte = (f"{quoi} vu{'s' if n_t + n_o > 1 else ''} seulement en temps réel : absent{'s' if n_t + n_o > 1 else ''} "
                 f"d'ACCOUNT_USAGE{jusqu} ; leur historique (volumes facturés, lectures) n'est pas encore disponible.")
    else:
        texte = "Recoupement en temps réel (INFORMATION_SCHEMA, SHOW) : aucun objet absent d'ACCOUNT_USAGE."
    n_v = sum(bool(t["detail"].get("volume_en_retard")) for t in tables)
    if n_v:
        texte += (f" {n_v} table{'s' if n_v > 1 else ''} à 0 octet dans TABLE_STORAGE_METRICS mais non vide{'s' if n_v > 1 else ''} "
                  f"en temps réel : métriques de stockage pas encore à jour, volume sous-estimé.")
    return {"recents_seulement_temps_reel": {"tables": n_t, "objets": n_o}, "volumes_en_retard": n_v, "latence_max_h": lat, "texte": texte}
