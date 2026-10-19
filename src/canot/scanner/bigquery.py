"""BigQuery collection for the scan, read-only.

One pass per region (`project`.`region-x`.INFORMATION_SCHEMA: one query per view and per region, never per dataset,
because each referenced view is billed at least 10 MB, with no cache) plus REST APIs called with GET only.
All SQL goes through `run_readonly` (guard, dry-run, byte cap, label tool=canot). Never ALTER PROJECT
and never enable an API, even when an error message suggests it: the read becomes "not read" and the tables.get API
takes over when it can.

Raw SQL text leaves collect() only through the second return value (raw families, key `_sql`). View, routine and
scheduled query definitions are used only to extract references, then discarded.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import sqlglot
from sqlglot import exp

from .. import __version__
from ..sources.bigquery_src import DEFAULT_MAX_BYTES, BigQuerySource, BytesCapExceeded
from .guard import NotReadOnly
from .model import Collector, bucket_only, classify_failure, pseudo

FAMILLES_MAX = 500
JOBS_RETENTION_DAYS = 180  # "never read" is measured over the full JOBS retention
TIMELINE_DAYS = 30
API_WORKERS = 8
DTS = "https://bigquerydatatransfer.googleapis.com/v1"
CONNECTIONS = "https://bigqueryconnection.googleapis.com/v1"
NIVEAU = {2: "level 2 not requested", 3: "level 3 not requested"}

_PROJECT = re.compile(r"^[a-z][a-z0-9.:-]{1,62}$")
_NAME = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_REGION = re.compile(r"^[a-z0-9-]{2,40}$")
_ANONYMOUS = re.compile(r"^_([0-9a-f]{40}|script)")
_DYNAMIC = re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.I)
_REF3 = re.compile(r"`?((?:[a-z0-9.-]+:)?[a-z][a-z0-9-]{4,28}[a-z0-9])`?\.`?([A-Za-z_]\w*)`?\.`?([\w*]+)`?")
_LEGACY_REF = re.compile(r"\[(?:([\w.:-]+?)[:.])?(\w+)\.([\w*]+)\]")
# literals and comments stripped before any pattern search: an address or a path never becomes an object name
_NOISE = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|--[^\n]*|#[^\n]*|/\*.*?\*/", re.S)
_LABEL = re.compile(r'STRUCT\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)')
_SQLGLOT_LOG = logging.getLogger("sqlglot")

TABLE_TYPES = {"BASE TABLE": "TABLE", "TABLE": "TABLE", "CLONE": "CLONE", "SNAPSHOT": "SNAPSHOT", "EXTERNAL": "EXTERNE"}
VIEW_KINDS = {"VIEW": "vue", "MATERIALIZED VIEW": "vue_materialisee", "MATERIALIZED_VIEW": "vue_materialisee"}
OUTILS = ("dbt", "looker", "airflow", "composer", "dataform", "tableau", "powerbi", "metabase", "superset", "sigma", "hex", "fivetran",
          "airbyte", "stitch", "dagster", "prefect", "census", "hightouch", "notebook", "colab", "dataflow", "dataproc", "sheets")
APP_KEYS = ("app", "application", "outil")
CONNECTION_TYPES = ("cloudSql", "aws", "azure", "cloudSpanner", "cloudResource", "spark", "salesforceDataCloud")

# --- SQL: named columns, never error_result.message nor the full ddl ------------------------------------------------
NOT_CANOT = "NOT EXISTS (SELECT 1 FROM UNNEST({a}labels) AS l WHERE l.key = 'tool' AND l.value = 'canot')"
NOT_SCRIPT = "({a}statement_type IS NULL OR {a}statement_type <> 'SCRIPT')"
SINCE = "{a}creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)"


def _jobs_filter(days: int, alias: str = "") -> str:
    return " AND ".join(f.format(a=alias, days=days) for f in (SINCE, NOT_SCRIPT, NOT_CANOT))


SQL = {
    "TABLES": "SELECT table_schema, table_name, table_type, creation_time, base_table_catalog, base_table_schema, base_table_name, "
              "IF(table_type = 'MATERIALIZED VIEW', ddl, NULL) AS mv_ddl FROM {I}.TABLES{w}",
    "TABLE_OPTIONS": "SELECT table_schema, table_name, option_name, option_value FROM {I}.TABLE_OPTIONS "
                     "WHERE option_name IN ('uris', 'labels', 'kms_key_name', 'table_format', 'expiration_timestamp'){a}",
    "COLUMNS": "SELECT table_schema, table_name, column_name, ordinal_position, data_type, is_partitioning_column, "
               "clustering_ordinal_position, is_system_defined FROM {I}.COLUMNS{w}",
    "COLUMN_FIELD_PATHS": "SELECT table_schema, table_name, field_path, policy_tags FROM {I}.COLUMN_FIELD_PATHS "
                          "WHERE ARRAY_LENGTH(policy_tags) > 0{a}",
    "TABLE_STORAGE": "SELECT table_schema, table_name, table_type, deleted, total_rows, total_partitions, total_logical_bytes, "
                     "active_logical_bytes, long_term_logical_bytes, total_physical_bytes, active_physical_bytes, long_term_physical_bytes, "
                     "time_travel_physical_bytes, fail_safe_physical_bytes, storage_last_modified_time, table_deletion_time "
                     "FROM {I}.TABLE_STORAGE{w}",
    "VIEWS": "SELECT table_schema, table_name, view_definition, use_standard_sql FROM {I}.VIEWS{w}",
    "MATERIALIZED_VIEWS": "SELECT table_schema, table_name, last_refresh_time FROM {I}.MATERIALIZED_VIEWS{w}",
    "ROUTINES": "SELECT routine_schema, routine_name, routine_type, routine_body, external_language, routine_definition, last_altered "
                "FROM {I}.ROUTINES{wr}",
}
JOBS_CONSO = (
    "SELECT COUNTIF(job_type = 'QUERY' AND reservation_id IS NULL) AS demande_n, "
    "SUM(IF(job_type = 'QUERY' AND reservation_id IS NULL, total_bytes_billed, NULL)) AS demande_octets, "
    "COUNTIF(job_type = 'QUERY' AND reservation_id IS NULL AND total_bytes_billed IS NULL AND error_result IS NULL) AS demande_octets_null_n, "
    "COUNTIF(job_type = 'QUERY' AND reservation_id IS NOT NULL) AS reserve_n, "
    "SUM(IF(job_type = 'QUERY' AND reservation_id IS NOT NULL, total_slot_ms, NULL)) AS reserve_slot_ms, "
    "COUNTIF(job_type = 'LOAD') AS load_n, COUNTIF(job_type = 'EXTRACT') AS extract_n, COUNTIF(job_type = 'COPY') AS copy_n, "
    "COUNTIF(statement_type = 'EXPORT_DATA') AS export_data_n, COUNTIF(error_result IS NOT NULL) AS echecs_n, "
    "MIN(creation_time) AS du, MAX(creation_time) AS au FROM {I}.JOBS WHERE {f}"
)
JOBS_FAMILLES = (
    "SELECT IFNULL(query_info.query_hashes.normalized_literals, TO_HEX(SHA256(query))) AS hash_bq, "
    "LOGICAL_OR(query_info.query_hashes.normalized_literals IS NULL) AS hash_repli, "
    "ANY_VALUE(query) AS sql_text, ANY_VALUE(statement_type) AS statement_type, COUNT(*) AS n_exec, "
    "COUNTIF(error_result IS NOT NULL) AS echecs_n, SUM(TIMESTAMP_DIFF(end_time, start_time, MILLISECOND)) AS temps_ms, "
    "SUM(total_slot_ms) AS slot_ms, SUM(total_bytes_billed) AS octets, SUM(total_bytes_processed) AS octets_traites, "
    "COUNTIF(total_bytes_billed IS NULL AND error_result IS NULL) AS octets_null_n, COUNTIF(reservation_id IS NULL) AS demande_n, "
    "ANY_VALUE(reservation_id) AS reservation, MIN(creation_time) AS premiere, MAX(creation_time) AS derniere, "
    "ARRAY_CONCAT_AGG(referenced_tables LIMIT 20) AS tables_ref{n3} FROM {I}.JOBS WHERE {f} AND job_type = 'QUERY' "
    "AND IFNULL(statement_type, '') NOT IN ('GRANT', 'REVOKE', 'CREATE_ROW_ACCESS_POLICY', 'DROP_ROW_ACCESS_POLICY', "
    "'DROP_ALL_ROW_ACCESS_POLICIES') GROUP BY hash_bq ORDER BY n_exec DESC, hash_bq LIMIT {limit}"
)
# level 3: identities (pseudonymized afterwards) and labels
JOBS_FAMILLES_N3 = (", APPROX_TOP_COUNT(user_email, 1)[SAFE_OFFSET(0)].value AS utilisateur, COUNT(DISTINCT user_email) AS utilisateurs_n, "
                    "ANY_VALUE(labels) AS labels, ANY_VALUE(job_id) AS job_id")
JOBS_TABLES = (
    "SELECT 'lecture' AS sens, rt.project_id AS projet, rt.dataset_id AS dataset, rt.table_id AS nom, COUNT(*) AS n, "
    "MAX(j.creation_time) AS derniere, "
    "COUNTIF(j.job_type = 'QUERY' AND j.total_bytes_billed IS NULL AND j.error_result IS NULL) AS octets_null_n "
    "FROM {I}.JOBS AS j, UNNEST(j.referenced_tables) AS rt WHERE {fj} GROUP BY 2, 3, 4 "
    "UNION ALL SELECT 'ecriture', destination_table.project_id, destination_table.dataset_id, destination_table.table_id, COUNT(*), "
    "MAX(creation_time), NULL FROM {I}.JOBS WHERE {f} AND error_result IS NULL AND destination_table.table_id IS NOT NULL "
    "AND (job_type IN ('LOAD', 'COPY') OR statement_type IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT', "
    "'TRUNCATE_TABLE')) GROUP BY 2, 3, 4"
)
TIMELINE = (
    "SELECT TIMESTAMP_TRUNC(period_start, HOUR) AS heure, SUM(period_slot_ms) AS slot_ms FROM {I}.JOBS_TIMELINE "
    "WHERE job_creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {d1} DAY) "
    "AND period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {d} DAY) "
    "AND (statement_type IS NULL OR statement_type <> 'SCRIPT') GROUP BY heure"
)
PARTITIONS = (
    "SELECT table_name, COUNT(*) AS partitions, MAX(last_modified_time) AS derniere_modif, "
    "COUNTIF(last_modified_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)) AS modifiees, "
    "COUNTIF(storage_tier = 'LONG_TERM') AS long_terme FROM `{p}`.`{ds}`.INFORMATION_SCHEMA.PARTITIONS GROUP BY table_name"
)
LINKS = "SELECT schema_name, linked_schema_catalog_name, linked_schema_org_display_name FROM {I}.SCHEMATA_LINKS"
SHARED = (
    "SELECT dataset_id, table_id, COUNT(*) AS n, COUNT(DISTINCT subscriber_org_number) AS organisations, MAX(job_start_time) AS derniere "
    "FROM {I}.SHARED_DATASET_USAGE WHERE job_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY) GROUP BY 1, 2"
)
RESERVATIONS = "SELECT reservation_name, slot_capacity, edition, ignore_idle_slots, autoscale.max_slots AS autoscale_max FROM {I}.RESERVATIONS"
COMMITMENTS = "SELECT capacity_commitment_id, commitment_plan, state, slot_count, edition FROM {I}.CAPACITY_COMMITMENTS"
RES_TIMELINE = (
    "SELECT reservation_name, COUNT(*) AS minutes, AVG(slots_assigned) AS slots_moyens, MAX(autoscale.current_slots) AS autoscale_pic "
    "FROM {I}.RESERVATIONS_TIMELINE WHERE period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {d} DAY) GROUP BY 1"
)
ORG = (
    "SELECT j.project_id AS projet, COUNT(*) AS n, MAX(j.creation_time) AS derniere FROM {I}.JOBS_BY_ORGANIZATION AS j, "
    "UNNEST(j.referenced_tables) AS rt WHERE {fj} AND rt.project_id = '{p}' AND j.project_id <> '{p}' GROUP BY 1"
)


class ServiceDisabled(Exception):
    """API not enabled on the project: we never enable it."""


# --- small helpers ----------------------------------------------------------------------------------------------------
def _check(rx: re.Pattern, value: str) -> str:
    if not isinstance(value, str) or not rx.match(value):
        raise ValueError(f"invalid BigQuery identifier: {value!r}")
    return value


def _iso(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):  # REST API: milliseconds since the epoch
        return datetime.fromtimestamp(int(v) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def _int(v) -> int | None:
    return None if v is None or v == "" else int(v)


def _sum(*vals) -> int | None:
    known = [v for v in vals if v is not None]
    return sum(known) if known else None


def _days(ms) -> float | None:
    return None if ms in (None, "") else round(int(ms) / 86_400_000, 2)


def _anonymous(ds: str) -> bool:
    return bool(_ANONYMOUS.match(ds))


def _reason(e: BaseException) -> str | None:
    """BigQuery-specific reasons; otherwise model.classify_failure. Never the message itself."""
    if isinstance(e, NotReadOnly):
        return "refused by the read-only guard"
    if isinstance(e, BytesCapExceeded):
        return f"estimated volume above the cap ({e.estimated / 1e6:,.0f} MB > {e.cap / 1e6:,.0f} MB)"
    if isinstance(e, ServiceDisabled):
        return "service disabled"
    m = str(e).lower()
    if any(k in m for k in ("service_disabled", "has not been used", "it is disabled", "accessnotconfigured")):
        return "service disabled"
    return None


def _bq_type(f: dict) -> str:
    """Type of a tables.get API field, written as in INFORMATION_SCHEMA.COLUMNS.data_type."""
    t = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL", "RECORD": "STRUCT"}.get(f.get("type", ""), f.get("type", ""))
    if t == "STRUCT":
        t = "STRUCT<" + ", ".join(f"{x.get('name')} {_bq_type(x)}" for x in f.get("fields", [])) + ">"
    elif t in ("NUMERIC", "BIGNUMERIC") and f.get("precision"):
        t += f"({f['precision']}" + (f", {f['scale']}" if f.get("scale") else "") + ")"
    return f"ARRAY<{t}>" if f.get("mode") == "REPEATED" else t


def _policy_paths(fields: list, prefix: str = "") -> list[str]:
    out = []
    for f in fields or []:
        path = f"{prefix}{f.get('name')}"
        if (f.get("policyTags") or {}).get("names"):
            out.append(path)
        out += _policy_paths(f.get("fields"), path + ".")
    return out


def _uris(value) -> list[str]:
    if isinstance(value, list):
        return [str(u) for u in value]
    try:
        v = json.loads(value)
        return [str(u) for u in (v if isinstance(v, list) else [v])]
    except (TypeError, ValueError):
        return re.findall(r'"([^"]+)"', str(value or ""))


def _buckets(uris: list[str]) -> list[str]:
    return sorted({b for u in uris if (b := bucket_only(u))})


def _labels(pairs, salt: bytes) -> list[str]:
    """Keys in clear, values hashed."""
    return sorted(f"{k}={pseudo(str(v), salt, 'v')}" for k, v in pairs)


def refs(sql: str | None, project: str, *, legacy: bool = False, exclude: str | None = None) -> list[str]:
    """Objects referenced by a SQL text (tables, views, table functions), as project.dataset.name. The text is not kept."""
    if not sql:
        return []
    if legacy:
        found = {f"{(m[0] or project).replace(':', '.')}.{m[1]}.{m[2]}" for m in _LEGACY_REF.findall(_NOISE.sub(" ", sql))}
        return sorted(found - {exclude})
    found, fallback = set(), False
    level = _SQLGLOT_LOG.level
    _SQLGLOT_LOG.setLevel(logging.ERROR)  # sqlglot copies the text it cannot parse into its warnings
    try:
        trees = [t for t in sqlglot.parse(sql, read="bigquery") if t is not None]
    except Exception:  # noqa: BLE001: unparsable text, fall back to three-part names
        trees, fallback = [], True
    finally:
        _SQLGLOT_LOG.setLevel(level)
    for tree in trees:
        if isinstance(tree, exp.Command):  # BEGIN ... END of a procedure: not parsed by sqlglot
            fallback = True
        for t in tree.find_all(exp.Table):
            name = t.name or (t.this.name if isinstance(t.this, exp.Func) else "")
            if name and t.db:
                found.add(f"{t.catalog or project}.{t.db}.{name}")
    if fallback:  # without a known project or a hyphen, "a.b.c" may be a STRUCT field: discarded
        found |= {f"{a}.{b}.{c}" for a, b, c in _REF3.findall(_NOISE.sub(" ", sql)) if a == project or "-" in a or ":" in a}
    return sorted(found - {exclude})


def outil(labels: dict, job_id: str | None) -> str | None:
    """Tool recognized from a fixed vocabulary (labels, job id prefix); nothing else is inferred."""
    tokens = {t for x in list(labels) + list(labels.values()) for t in re.split(r"[^a-z0-9]+", str(x).lower()) if t}
    for o in OUTILS:
        if o in tokens:
            return o
    jid = (job_id or "").lower()
    if labels.get("data_source_id") == "scheduled_query" or jid.startswith("scheduled_query"):
        return "scheduled query"
    if jid.startswith("bquxjob"):
        return "console"
    return None


def app(labels: dict, salt: bytes) -> str | None:
    for k in APP_KEYS:
        if labels.get(k):
            v = str(labels[k])
            return v.lower() if v.lower() in OUTILS else pseudo(v, salt, "app")
    return None


def _blank_table(base: str, ds: str, name: str, typ: str) -> dict:
    return {"id": f"{base}.{ds}.{name}", "base": base, "schema": ds, "nom": name, "type": typ, "flags": [], "lignes": None,
            "octets_actifs": None, "octets_historique": None, "clone_de": None, "colonnes": [], "retention_jours": None,
            "derniere_ddl": None, "derniere_ecriture": None, "derniere_lecture": None, "n_lectures": None, "n_ecritures": None,
            "proprietaire": None, "politiques": [], "etiquettes": [], "supprimee_le": None, "externe_uri": None,
            "partitionnement": None, "clustering": [], "detail": {}, "_src": set(), "_loc": None}


def _objet(kind: str, oid: str, schema: str | None = None, **kw) -> dict:
    o = {"kind": kind, "id": oid, "schema": schema, "langage": None, "proprietaire": None, "externe": False, "dependances_externes": [],
         "reference": [], "etat": None, "planification": None, "executions_n": None, "echecs_n": None, "derniere_execution": None,
         "sql_dynamique": None, "detail": {}}
    o.update(kw)
    return o


# --- collection --------------------------------------------------------------------------------------------------------
class _Scan:
    def __init__(self, runner, *, project: str, data_project: str, datasets: list[str] | None, level: int, salt: bytes,
                 days: int, max_bytes: int):
        self.runner, self.client = runner, runner.client
        self.project, self.p = _check(_PROJECT, project), _check(_PROJECT, data_project)
        self.requested = [_check(_NAME, d) for d in datasets] if datasets else None
        self.level, self.salt, self.max_bytes = int(level), salt, max_bytes
        self.days = min(int(days), JOBS_RETENTION_DAYS)
        self.col = Collector(prefix="B")
        self.ds: dict[str, dict] = {}
        self.tables: dict[str, dict] = {}
        self.views: dict[str, dict] = {}
        self.objets: list[dict] = []
        self.raw: list[dict] = []
        self.loc: dict[str, str] = {}  # region key (lowercase) -> job location
        self.ok: set[tuple[str, str]] = set()
        self.jobs: dict[str, dict | None] = {}
        self.timeline: dict[str, dict] = {}
        self.reads: dict[str, list] = {}
        self.writes: dict[str, list] = {}
        self.par_table_lu: set[str] = set()
        self.rls: dict[str, list[str]] = {}
        self.shared: dict[str, int] = {}
        self.editions: set[str] | None = None
        self.api_calls = 0
        self._lock = threading.Lock()
        self.now = datetime.now(timezone.utc)
        self.t0 = time.monotonic()
        self.cost0 = dict(getattr(runner, "cost", {}) or {})

    # reads ------------------------------------------------------------------------------------------------------------
    def _read(self, vue: str, fn, **kw):
        err: list[BaseException] = []

        def call():
            try:
                return fn()
            except Exception as e:
                err.append(e)
                raise

        res = self.col.read(vue, call, **kw)
        if err and (r := _reason(err[0])):
            self.col.lectures[-1]["raison"] = r
        return res

    def _many(self, vue: str, items: list, fn, *, workers: int = 1, **kw) -> dict | None:
        """One logged read for a series of API calls; partial if some fail, not read if all fail."""
        errs: list[BaseException] = []

        def one(it):
            try:
                return it, fn(it), None
            except Exception as e:  # noqa: BLE001: counted then logged
                return it, None, e

        def go():
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                res = list(pool.map(one, items))
            out = {it: v for it, v, e in res if e is None}
            errs.extend(e for _, _, e in res if e is not None)
            if errs and not out:
                raise errs[0]
            return out

        out = self._read(vue, go, **kw)
        if out is not None and errs:
            self.col.lectures[-1]["raison"] = f"partial: {len(errs)}/{len(items)} failed ({_reason(errs[0]) or classify_failure(errs[0])})"
        return out

    def _q(self, vue: str, region: str, sql: str, **kw):
        return self._read(vue, lambda: self.runner.run_readonly(sql, location=self.loc[region], max_bytes=self.max_bytes), **kw)

    def _fen(self, days: int) -> dict:
        return {"du": _iso(self.now - timedelta(days=days)), "au": _iso(self.now)}

    def _count(self, n: int = 1):
        with self._lock:
            self.api_calls += n

    def _v2(self, path: str, params: dict | None = None) -> dict:
        self._count()
        return self.client._connection.api_request(method="GET", path=path, query_params=params or {})

    def _v2_list(self, path: str, key: str, params: dict | None = None, *, page: str = "maxResults") -> list[dict]:
        out, token = [], None
        while True:
            r = self._v2(path, dict(params or {}, **{page: 1000}, **({"pageToken": token} if token else {})))
            out += r.get(key, [])
            if not (token := r.get("nextPageToken")):
                return out

    def _rest(self, url: str, params: dict | None = None) -> dict:
        """GET on another Google API (transfers, connections). The error keeps only the code and the status."""
        self._count()
        r = self.client._http.get(url, params=params or {})
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {}) or {}
            except ValueError:
                err = {}
            reasons = {d.get("reason") for d in err.get("details", []) if isinstance(d, dict)}
            msg = str(err.get("message", "")).lower()
            if "SERVICE_DISABLED" in reasons or "has not been used" in msg or "it is disabled" in msg:
                raise ServiceDisabled("service disabled")
            raise RuntimeError(f"{r.status_code} {err.get('status', '')}")
        return r.json()

    def _rest_list(self, url: str, key: str) -> list[dict]:
        out, token = [], None
        while True:
            r = self._rest(url, {"pageSize": 1000, **({"pageToken": token} if token else {})})
            out += r.get(key, [])
            if not (token := r.get("nextPageToken")):
                return out

    def _I(self, region: str) -> str:
        return f"`{self.p}`.`region-{region}`.INFORMATION_SCHEMA"

    def _where(self, column: str, region: str, first: bool = True) -> str:
        """Filter on the requested datasets; with none requested, the whole region."""
        if not self.requested:
            return ""
        names = [d["nom"] for d in self.ds.values() if d["_region"] == region and not d["cache"]]
        cond = f"{column} IN ({', '.join(repr(n) for n in names) or 'NULL'})"
        return (" WHERE " if first else " AND ") + cond

    # main pass --------------------------------------------------------------------------------------------------------
    def run(self):
        self._datasets()
        complete = sorted({d["_region"] for d in self.ds.values() if d["_region"] and not d["anonyme"]})
        jobs_only = {d["_region"] for d in self.ds.values() if d["_region"] and d["anonyme"]}
        if getattr(self.runner, "location", None):
            r = self.runner.location.lower()
            self.loc.setdefault(r, self.runner.location)
            jobs_only.add(r)
        jobs_only = sorted(jobs_only - set(complete))
        for r in complete + jobs_only:
            _check(_REGION, r)
            if r in complete:
                self._metadata(r)
            self._jobs(r)
            self._level2(r, complete=r in complete)
            self._level3(r)
        self.complete, self.jobs_only = complete, jobs_only
        self._finish()

    def _datasets(self):
        listed = self._read("API datasets.list", lambda: self._v2_list(f"/projects/{self.p}/datasets", "datasets", {"all": "true"}))
        items = {d["datasetReference"]["datasetId"]: d for d in listed or []}
        if self.requested:
            items = {ds: items.get(ds, {}) for ds in self.requested}
        targets = sorted(ds for ds in items if not _anonymous(ds))
        got = self._many("API datasets.get", targets, lambda ds: self._v2(f"/projects/{self.p}/datasets/{ds}")) if targets else {}
        for ds in sorted(items):
            props = (got or {}).get(ds) or {}
            self.ds[ds] = self._dataset(ds, props, items[ds])

    def _dataset(self, ds: str, props: dict, listed: dict) -> dict:
        region = props.get("location") or listed.get("location")
        if region:
            self.loc.setdefault(region.lower(), region)
        acc = props.get("access")
        autoris = [a[k] for a in acc or [] for k in ("view", "routine", "dataset") if k in a]
        hors_projet = sum(1 for x in autoris if (x.get("projectId") or (x.get("dataset") or {}).get("projectId")) not in (None, self.p))
        typ = props.get("type") or listed.get("type") or ("LINKED" if props.get("linkedDatasetSource") else "DEFAULT")
        d = {"id": f"{self.p}.{ds}", "nom": ds, "region": region, "type": typ, "cache": ds.startswith("_"), "anonyme": _anonymous(ds),
             "lu": bool(props), "facturation_stockage": props.get("storageBillingModel"), "time_travel_h": _int(props.get("maxTimeTravelHours")),
             "expiration_tables_jours": _days(props.get("defaultTableExpirationMs")),
             "expiration_partitions_jours": _days(props.get("defaultPartitionExpirationMs")),
             "cmek": bool(props.get("defaultEncryptionConfiguration")) if props else None,
             "etiquettes": _labels((props.get("labels") or listed.get("labels") or {}).items(), self.salt),
             "acces": None if acc is None else {
                 "entrees_n": len(acc), "par_type": dict(sorted(Counter(k for a in acc for k in a if k != "role").items())),
                 "public": any(a.get("specialGroup") == "allAuthenticatedUsers" or a.get("iamMember") in ("allUsers", "allAuthenticatedUsers")
                               for a in acc),
                 "autorisations_n": len(autoris), "autorisations_hors_projet_n": hors_projet},
             "tables_n": None, "_region": region.lower() if region else None}
        src = props.get("linkedDatasetSource") or {}
        if typ == "LINKED" or src:
            sp = (src.get("sourceDataset") or {}).get("projectId")
            self.objets.append(_objet("base_importee", d["id"], ds, externe=True,
                                      detail={"type": "LINKED", "source_projet": pseudo(sp, self.salt, "projet"), "region": region}))
        ext = props.get("externalDatasetReference") or {}
        if typ == "EXTERNAL" or ext:
            scheme = str(ext.get("externalSource") or "").split(":", 1)[0] or None
            self.objets.append(_objet("base_importee", d["id"], ds, externe=True, dependances_externes=[scheme] if scheme else [],
                                      detail={"type": "EXTERNAL", "connexion": bool(ext.get("connection")), "region": region}))
        return d

    # metadata of a region -------------------------------------------------------------------------------------------
    def _metadata(self, r: str):
        I, vue = self._I(r), f"region-{r}.INFORMATION_SCHEMA."
        rows = self._q(vue + "TABLES", r, SQL["TABLES"].format(I=I, w=self._where("table_schema", r)))
        if rows is not None:
            self.ok.add((r, "TABLES"))
            for x in rows:
                e = self._relation(x["table_schema"], x["table_name"], x.get("table_type"), r)
                if e is None:
                    continue
                e["_src"].add("type")
                if "kind" not in e:
                    e["derniere_ddl"] = _iso(x.get("creation_time"))
                    if x.get("base_table_name"):
                        e["clone_de"] = f"{x.get('base_table_catalog') or self.p}.{x['base_table_schema']}.{x['base_table_name']}"
                elif e.get("kind") == "vue_materialisee" and x.get("mv_ddl"):
                    e["reference"] = refs(x["mv_ddl"], self.p, exclude=e["id"])
                    e["_src"].add("def")
        # hidden datasets (INFORMATION_SCHEMA refuses them) and fallback when TABLES is not readable: listing through the API
        listing = sorted(d["nom"] for d in self.ds.values() if d["_region"] == r and not d["anonyme"] and (rows is None or d["cache"]))
        if listing:
            got = self._many(f"API tables.list (region-{r})", listing,
                             lambda ds: self._v2_list(f"/projects/{self.p}/datasets/{ds}/tables", "tables"))
            for ds, items in (got or {}).items():
                self.ds[ds]["tables_n"] = len(items)
                for it in items:
                    self._relation(ds, it["tableReference"]["tableId"], it.get("type"), r)

        rows = self._q(vue + "TABLE_OPTIONS", r, SQL["TABLE_OPTIONS"].format(I=I, a=self._where("table_schema", r, first=False)))
        if rows is not None:
            self.ok.add((r, "TABLE_OPTIONS"))
            for x in rows:
                if t := self.tables.get(f"{self.p}.{x['table_schema']}.{x['table_name']}"):
                    self._option(t, x["option_name"], x.get("option_value"))

        rows = self._q(vue + "COLUMNS", r, SQL["COLUMNS"].format(I=I, w=self._where("table_schema", r)))
        if rows is not None:
            self.ok.add((r, "COLUMNS"))
            clusters: dict[str, list] = {}
            for x in sorted(rows, key=lambda x: (x["table_schema"], x["table_name"], x.get("ordinal_position") or 0)):
                e = self._entry(x["table_schema"], x["table_name"])
                if e is None:
                    continue
                e["_src"].add("columns")
                if x.get("is_partitioning_column") == "YES" and "kind" not in e:
                    e["partitionnement"] = x["column_name"]
                if x.get("clustering_ordinal_position") is not None and "kind" not in e:
                    clusters.setdefault(e["id"], []).append((int(x["clustering_ordinal_position"]), x["column_name"]))
                if x.get("is_system_defined") != "YES":
                    e["colonnes"].append({"nom": x["column_name"], "type": x["data_type"]})
            for tid, cl in clusters.items():
                self.tables[tid]["clustering"] = [c for _, c in sorted(cl)]

        rows = self._q(vue + "COLUMN_FIELD_PATHS", r, SQL["COLUMN_FIELD_PATHS"].format(I=I, a=self._where("table_schema", r, first=False)))
        if rows is not None:
            self.ok.add((r, "COLUMN_FIELD_PATHS"))
            for x in rows:
                if t := self.tables.get(f"{self.p}.{x['table_schema']}.{x['table_name']}"):
                    self._policy_tag(t, x["field_path"])

        rows = self._q(vue + "TABLE_STORAGE", r, SQL["TABLE_STORAGE"].format(I=I, w=self._where("table_schema", r)))
        if rows is None:
            lec = self.col.lectures[-1]
            if lec["raison"] in ("missing privilege", "absent or not exposed"):
                lec["raison"] = "missing privilege or view not enabled (enable_info_schema_storage, which Canot does not change); fallback to tables.get"
        else:
            self.ok.add((r, "TABLE_STORAGE"))
            for x in rows:
                self._storage(x, r)

        rows = self._q(vue + "VIEWS", r, SQL["VIEWS"].format(I=I, w=self._where("table_schema", r)))
        if rows is not None:
            self.ok.add((r, "VIEWS"))
            for x in rows:
                v = self._relation(x["table_schema"], x["table_name"], "VIEW", r)
                legacy = str(x.get("use_standard_sql") or "").upper() == "NO"
                v["reference"] = refs(x.get("view_definition"), self.p, legacy=legacy, exclude=v["id"])
                v["langage"] = "legacy SQL" if legacy else "SQL"
                v["_src"].add("def")

        rows = self._q(vue + "MATERIALIZED_VIEWS", r, SQL["MATERIALIZED_VIEWS"].format(I=I, w=self._where("table_schema", r)))
        if rows is not None:
            for x in rows:
                v = self._relation(x["table_schema"], x["table_name"], "MATERIALIZED VIEW", r)
                v["derniere_execution"] = _iso(x.get("last_refresh_time"))

        rows = self._q(vue + "ROUTINES", r, SQL["ROUTINES"].format(I=I, wr=self._where("routine_schema", r)))
        for x in rows or []:
            self._routine(x, r)

        dss = sorted(d["nom"] for d in self.ds.values() if d["_region"] == r and not d["anonyme"])
        if dss:
            got = self._many(f"API models.list (region-{r})", dss, lambda ds: self._v2_list(f"/projects/{self.p}/datasets/{ds}/models", "models"))
            for ds, models in (got or {}).items():
                for m in models:
                    mid = (m.get("modelReference") or {}).get("modelId")
                    self.objets.append(_objet("modele_ml", f"{self.p}.{ds}.{mid}", ds, externe=bool(m.get("remoteModelInfo")),
                                              detail={"type": m.get("modelType"), "region": self.loc[r]}))

        need = [e for e in self._entries(r) if self._needs_api(e, r)]
        if need:
            got = self._many(f"API tables.get (region-{r})", [e["id"] for e in need], lambda tid: self._get_table(tid), workers=API_WORKERS)
            for tid, props in (got or {}).items():
                self._apply_api(self.tables.get(tid) or self.views[tid], props)

    def _entries(self, r: str) -> list[dict]:
        return [e for e in list(self.tables.values()) + list(self.views.values()) if e["_loc"] == r and not e.get("supprimee_le")]

    def _entry(self, ds: str, name: str) -> dict | None:
        tid = f"{self.p}.{ds}.{name}"
        return self.tables.get(tid) or self.views.get(tid)

    def _relation(self, ds: str, name: str, typ: str | None, r: str) -> dict | None:
        tid = f"{self.p}.{ds}.{name}"
        if typ in VIEW_KINDS:
            v = self.views.get(tid)
            if v is None:
                v = self.views[tid] = _objet(VIEW_KINDS[typ], tid, ds, langage="SQL", detail={"region": self.loc[r]}, colonnes=[],
                                             _src=set(), _loc=r, _ds=ds, _nom=name)
            return v
        if tid not in self.tables and typ in TABLE_TYPES:
            self.tables[tid] = _blank_table(self.p, ds, name, TABLE_TYPES[typ])
            self.tables[tid]["_loc"] = r
            if ds.startswith("_"):
                self.tables[tid]["flags"].append("dataset_cache")
        return self.tables.get(tid)

    def _option(self, t: dict, name: str, value):
        t["_src"].add("options")
        if name == "uris":
            b = _buckets(_uris(value))
            t["externe_uri"] = b[0] if b else None
            t["detail"]["compartiments"] = ", ".join(b) or None
        elif name == "labels":
            t["detail"]["labels_n"] = len(_LABEL.findall(str(value or "")))  # resource labels: not governance (F10)
        elif name == "kms_key_name" and "cmek" not in t["flags"]:
            t["flags"].append("cmek")
        elif name == "table_format" and "ICEBERG" in str(value).upper():
            t["type"] = "ICEBERG"
        elif name == "expiration_timestamp":
            t["detail"]["expiration"] = str(value).strip('"') if value else None

    def _policy_tag(self, t: dict, path: str):
        if f"policy_tag:{path}" not in t["politiques"]:
            t["politiques"].append(f"policy_tag:{path}")
        t["detail"]["policy_tags_n"] = sum(1 for p in t["politiques"] if p.startswith("policy_tag:"))

    def _storage(self, x: dict, r: str):
        ds, name = x["table_schema"], x["table_name"]
        if x.get("deleted"):  # dropped table still billed (time travel, fail-safe)
            when = _iso(x.get("table_deletion_time"))
            t = _blank_table(self.p, ds, name, TABLE_TYPES.get(str(x.get("table_type") or ""), "TABLE"))
            t["id"] += f"@{when or 'supprimee'}"
            t["supprimee_le"], t["_loc"] = when, r
            t["flags"].append("supprimee")
            self.tables[t["id"]] = t
        else:
            t = self.tables.get(f"{self.p}.{ds}.{name}")
            if t is None:
                return
        t["_src"].add("storage")
        tt, fs = _int(x.get("time_travel_physical_bytes")), _int(x.get("fail_safe_physical_bytes"))
        t["lignes"] = _int(x.get("total_rows"))
        t["octets_actifs"] = _int(x.get("total_logical_bytes"))
        t["octets_historique"] = _sum(tt, fs)
        t["derniere_ecriture"] = _iso(x.get("storage_last_modified_time"))
        t["detail"].update({"source_volumes": "TABLE_STORAGE", "partitions_n": _int(x.get("total_partitions")),
                            "octets_logiques_actifs": _int(x.get("active_logical_bytes")),
                            "octets_logiques_long_terme": _int(x.get("long_term_logical_bytes")),
                            "octets_physiques": _int(x.get("total_physical_bytes")),
                            "octets_physiques_actifs": _int(x.get("active_physical_bytes")),
                            "octets_physiques_long_terme": _int(x.get("long_term_physical_bytes")),
                            "octets_time_travel": tt, "octets_fail_safe": fs})

    def _routine(self, x: dict, r: str):
        lang = x.get("external_language") or ("SQL" if x.get("routine_body") == "SQL" else None)
        body = x.get("routine_definition")
        kind = "procedure" if x.get("routine_type") == "PROCEDURE" else "fonction"
        sql = lang == "SQL"
        self.objets.append(_objet(
            kind, f"{self.p}.{x['routine_schema']}.{x['routine_name']}", x["routine_schema"], langage=lang,
            reference=refs(body, self.p) if sql else [],
            sql_dynamique=bool(_DYNAMIC.search(body or "")) if sql else (False if lang == "JAVASCRIPT" else None),
            externe=x.get("routine_body") == "EXTERNAL" and not x.get("external_language"),
            detail={"type": x.get("routine_type"), "corps": x.get("routine_body"), "modifiee_le": _iso(x.get("last_altered")),
                    "region": self.loc[r]}))

    def _needs_api(self, e: dict, r: str) -> bool:
        if "kind" in e:
            return not {"type", "def", "columns"} <= e["_src"]
        if not {"type", "storage", "columns"} <= e["_src"]:
            return True
        return (r, "TABLE_OPTIONS") not in self.ok or (r, "COLUMN_FIELD_PATHS") not in self.ok

    def _get_table(self, tid: str) -> dict:
        e = self.tables.get(tid) or self.views[tid]
        ds, name = (e["schema"], e["nom"]) if "kind" not in e else (e["_ds"], e["_nom"])
        return self._v2(f"/projects/{self.p}/datasets/{ds}/tables/{quote(name, safe='')}")

    def _apply_api(self, e: dict, p: dict):
        """Fills in, from tables.get, what the region views did not provide; never replaces a value that was read."""
        src = e["_src"]
        fields = (p.get("schema") or {}).get("fields") or []
        if "columns" not in src:
            e["colonnes"] = [{"nom": f.get("name"), "type": _bq_type(f)} for f in fields]
        if "kind" in e:
            if "def" not in src:
                if v := p.get("view"):
                    legacy = bool(v.get("useLegacySql"))
                    e["reference"], e["langage"] = refs(v.get("query"), self.p, legacy=legacy, exclude=e["id"]), "legacy SQL" if legacy else "SQL"
                elif mv := p.get("materializedView"):
                    e["reference"] = refs(mv.get("query"), self.p, exclude=e["id"])
                    e["derniere_execution"] = e["derniere_execution"] or _iso(mv.get("lastRefreshTime"))
            e["_src"] |= {"type", "def", "columns"}
            return
        if "type" not in src:
            e["derniere_ddl"] = _iso(p.get("creationTime"))
            base = (p.get("cloneDefinition") or p.get("snapshotDefinition") or {}).get("baseTableReference")
            if base:
                e["clone_de"] = f"{base.get('projectId')}.{base.get('datasetId')}.{base.get('tableId')}"
                e["type"] = "CLONE" if p.get("cloneDefinition") else "SNAPSHOT"
            if p.get("biglakeConfiguration"):
                e["type"] = "ICEBERG"
        if "columns" not in src:
            tp, rp = p.get("timePartitioning") or {}, p.get("rangePartitioning") or {}
            e["partitionnement"] = tp.get("field") or ("_PARTITIONTIME" if tp else None) or rp.get("field")
            e["clustering"] = list((p.get("clustering") or {}).get("fields") or [])
        if "options" not in src:
            ext = p.get("externalDataConfiguration") or {}
            b = _buckets(ext.get("sourceUris") or [])
            e["externe_uri"] = b[0] if b else None
            if b:
                e["detail"]["compartiments"] = ", ".join(b)
            if ext:
                e["detail"]["format_externe"] = ext.get("sourceFormat")
                e["detail"]["connexion"] = bool(ext.get("connectionId"))
            e["detail"]["labels_n"] = len(p.get("labels") or {})
            if p.get("encryptionConfiguration") and "cmek" not in e["flags"]:
                e["flags"].append("cmek")
            if p.get("expirationTime"):
                e["detail"]["expiration"] = _iso(p["expirationTime"])
        if "cfp" not in src:
            for path in _policy_paths(fields):
                self._policy_tag(e, path)
        if "storage" not in src:
            tt = _int(p.get("numTimeTravelPhysicalBytes"))
            e["lignes"] = _int(p.get("numRows"))
            e["octets_actifs"] = _int(p.get("numTotalLogicalBytes", p.get("numBytes")))
            e["octets_historique"] = tt  # fail-safe not exposed by tables.get
            e["derniere_ecriture"] = _iso(p.get("lastModifiedTime"))
            e["detail"].update({"source_volumes": "tables.get", "partitions_n": _int(p.get("numPartitions")),
                                "octets_logiques_actifs": _int(p.get("numActiveLogicalBytes")),
                                "octets_logiques_long_terme": _int(p.get("numLongTermLogicalBytes")),
                                "octets_physiques": _int(p.get("numTotalPhysicalBytes")),
                                "octets_physiques_actifs": _int(p.get("numActivePhysicalBytes")),
                                "octets_physiques_long_terme": _int(p.get("numLongTermPhysicalBytes")),
                                "octets_time_travel": tt, "octets_fail_safe": None,
                                "derniere_ecriture_source": "lastModifiedTime (includes metadata changes)"})
        e["_src"] |= {"type", "columns", "options", "cfp", "storage"}

    # jobs ---------------------------------------------------------------------------------------------------------------
    def _jobs(self, r: str):
        I = self._I(r)
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.JOBS (consumption)", r, JOBS_CONSO.format(I=I, f=_jobs_filter(self.days)),
                       fenetre=self._fen(self.days))
        self.jobs[r] = rows[0] if rows else None
        if self.level < 2:
            for v in ("JOBS (families)", "JOBS (reads per table)", f"JOBS_TIMELINE ({TIMELINE_DAYS} d)"):
                self.col.skip(f"region-{r}.INFORMATION_SCHEMA.{v}", NIVEAU[2])
            return
        n3 = JOBS_FAMILLES_N3 if self.level >= 3 else ""
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.JOBS (families)", r,
                       JOBS_FAMILLES.format(I=I, f=_jobs_filter(self.days), n3=n3, limit=FAMILLES_MAX), fenetre=self._fen(self.days))
        for x in rows or []:
            self.raw.append(self._famille(x, r))
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.JOBS (reads per table)", r,
                       JOBS_TABLES.format(I=I, f=_jobs_filter(JOBS_RETENTION_DAYS), fj=_jobs_filter(JOBS_RETENTION_DAYS, "j.")),
                       fenetre=self._fen(JOBS_RETENTION_DAYS))
        if rows is not None:
            self.par_table_lu.add(r)
            for x in rows:
                tid = f"{x['projet']}.{x['dataset']}.{x['nom']}"
                acc = (self.reads if x["sens"] == "lecture" else self.writes).setdefault(tid, [0, None, 0])
                acc[0] += int(x["n"] or 0)
                acc[1] = max(filter(None, (acc[1], _iso(x.get("derniere")))), default=None)
                acc[2] += int(x.get("octets_null_n") or 0)
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.JOBS_TIMELINE ({TIMELINE_DAYS} d)", r,
                       TIMELINE.format(I=I, d=TIMELINE_DAYS, d1=TIMELINE_DAYS + 1), fenetre=self._fen(TIMELINE_DAYS))
        if rows is not None:
            slots = sorted(int(x["slot_ms"] or 0) / 3.6e6 for x in rows)  # average slots over each active hour
            self.timeline[r] = {"heures_actives": len(slots), "slot_heures": round(sum(slots), 3),
                                "slots_moyens_pic": round(slots[-1], 3) if slots else None,
                                "slots_moyens_p95": round(slots[int(0.95 * (len(slots) - 1))], 3) if slots else None}

    def _famille(self, x: dict, r: str) -> dict:
        labels = {str(lab.get("key")): str(lab.get("value")) for lab in x.get("labels") or [] if isinstance(lab, dict)}
        tabs = sorted({f"{t.get('project_id')}.{t.get('dataset_id')}.{t.get('table_id')}" for t in x.get("tables_ref") or []
                       if isinstance(t, dict) and t.get("table_id")})
        n = int(x["n_exec"])
        n3 = self.level >= 3
        return {"_sql": x.get("sql_text"), "hash_plateforme": x.get("hash_bq"), "n_exec": n, "temps_ms": _int(x.get("temps_ms")),
                "credits": None, "octets": _int(x.get("octets")),  # SUM over NULLs: None, never 0
                "app": app(labels, self.salt) if n3 else None, "outil": outil(labels, x.get("job_id")) if n3 else None,
                "utilisateur": pseudo(x.get("utilisateur"), self.salt, "u") if n3 else None, "role": None,
                "entrepot": x.get("reservation") if (x.get("demande_n") or 0) < n else None,
                "tables": tabs, "ecriture_externe": x.get("statement_type") == "EXPORT_DATA",
                "query_type": x.get("statement_type"), "echecs_n": _int(x.get("echecs_n")), "region": self.loc[r],
                "premiere": _iso(x.get("premiere")), "derniere": _iso(x.get("derniere")),
                "detail": {"slot_ms": _int(x.get("slot_ms")), "octets_traites": _int(x.get("octets_traites")),
                           "octets_null_n": _int(x.get("octets_null_n")), "a_la_demande_n": _int(x.get("demande_n")),
                           "utilisateurs_n": _int(x.get("utilisateurs_n")) if n3 else None,
                           "regroupement": "exact text" if x.get("hash_repli") else "query_hashes.normalized_literals"}}

    # level 2: sharing, partitions, policies, transfers, connections -------------------------------------------------
    def _level2(self, r: str, *, complete: bool):
        if not complete:
            return
        if self.level < 2:
            for v in (f"region-{r}.INFORMATION_SCHEMA.SCHEMATA_LINKS", f"region-{r}.INFORMATION_SCHEMA.SHARED_DATASET_USAGE",
                      "DATASET.INFORMATION_SCHEMA.PARTITIONS", f"API rowAccessPolicies.list (region-{r})",
                      f"API transferConfigs.list ({r})", f"API connections.list ({r})"):
                self.col.skip(v, NIVEAU[2])
            return
        I = self._I(r)
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.SCHEMATA_LINKS", r, LINKS.format(I=I))
        links: dict[str, list] = {}
        for x in rows or []:
            links.setdefault(x["schema_name"], []).append(x)
        for ds, xs in sorted(links.items()):
            self.objets.append(_objet("partage", f"{self.p}.{ds}", ds, externe=True, detail={
                "source": "Analytics Hub (SCHEMATA_LINKS)", "liens_n": len(xs),
                "projets_consommateurs_n": len({x.get("linked_schema_catalog_name") for x in xs}),
                "organisations_n": len({x.get("linked_schema_org_display_name") for x in xs if x.get("linked_schema_org_display_name")})}))
        rows = self._q(f"region-{r}.INFORMATION_SCHEMA.SHARED_DATASET_USAGE", r, SHARED.format(I=I, days=self.days), fenetre=self._fen(self.days))
        for x in rows or []:
            tid = f"{self.p}.{x['dataset_id']}.{x['table_id']}"
            self.shared[tid] = self.shared.get(tid, 0) + int(x["n"] or 0)

        for ds in sorted({t["schema"] for t in self.tables.values() if t["_loc"] == r and t["partitionnement"] and not t["schema"].startswith("_")}):
            rows = self._q(f"{ds}.INFORMATION_SCHEMA.PARTITIONS", r, PARTITIONS.format(p=self.p, ds=_check(_NAME, ds), days=self.days))
            for x in rows or []:
                if t := self.tables.get(f"{self.p}.{ds}.{x['table_name']}"):
                    t["detail"].update({"partitions_n": _int(x.get("partitions")), "partitions_modifiees": _int(x.get("modifiees")),
                                        "partitions_long_terme": _int(x.get("long_terme")),
                                        "partition_derniere_modif": _iso(x.get("derniere_modif"))})

        bases = [t for t in self.tables.values() if t["_loc"] == r and t["type"] in ("TABLE", "CLONE") and not t["supprimee_le"]]
        if bases:
            got = self._many(f"API rowAccessPolicies.list (region-{r})", [t["id"] for t in bases], self._row_policies, workers=API_WORKERS)
            for tid, pols in (got or {}).items():
                self.rls[tid] = sorted(p["rowAccessPolicyReference"]["policyId"] for p in pols)
                for p in pols:
                    t = self.tables[tid]
                    self.objets.append(_objet("politique_lignes", f"{tid}.{p['rowAccessPolicyReference']['policyId']}", t["schema"],
                                              reference=[tid], detail={"table": tid, "creee_le": _iso(p.get("creationTime"))}))
        self._transfers(r)
        conns = self._read(f"API connections.list ({r})",
                           lambda: self._rest_list(f"{CONNECTIONS}/projects/{self.p}/locations/{r}/connections", "connections"))
        for c in conns or []:
            typ = next((k for k in CONNECTION_TYPES if k in c), None)
            self.objets.append(_objet("connexion", f"{r}/{str(c.get('name', '')).rsplit('/', 1)[-1]}", None, externe=True,
                                      detail={"type": typ, "region": self.loc[r]}))

    def _row_policies(self, tid: str) -> list[dict]:
        t = self.tables[tid]
        return self._v2_list(f"/projects/{self.p}/datasets/{t['schema']}/tables/{quote(t['nom'], safe='')}/rowAccessPolicies",
                             "rowAccessPolicies", page="pageSize")

    def _transfers(self, r: str):
        locs = [r] + (["europe"] if r == "eu" else [])  # the transfers API may name the EU multi-region "europe"

        def configs():
            out, errs = {}, []
            for loc in locs:
                try:
                    for c in self._rest_list(f"{DTS}/projects/{self.p}/locations/{loc}/transferConfigs", "transferConfigs"):
                        out[c.get("name")] = c
                except Exception as e:  # noqa: BLE001: raised below if no location answers
                    errs.append(e)
            if len(errs) == len(locs):
                raise errs[0]
            return [out[k] for k in sorted(out)]

        cfgs = self._read(f"API transferConfigs.list ({r})", configs)
        if not cfgs:
            return
        since = _iso(self.now - timedelta(days=self.days))
        runs = self._many(f"API transferConfigs.runs.list ({r})", [c["name"] for c in cfgs],
                          lambda name: self._rest(f"{DTS}/{name}/runs", {"pageSize": 200}).get("transferRuns", [])) or {}
        for c in cfgs:
            params = c.get("params") or {}
            src = c.get("dataSourceId")
            query = params.get("query") if src == "scheduled_query" else None
            buckets = _buckets([params[k] for k in ("data_path_template", "data_path") if isinstance(params.get(k), str)])
            o = _objet("requete_planifiee", f"{r}/{str(c.get('name', '')).rsplit('/', 1)[-1]}", c.get("destinationDatasetId"),
                       langage="SQL" if query else None, reference=refs(query, self.p),
                       sql_dynamique=bool(_DYNAMIC.search(query)) if query else None,
                       externe=src != "scheduled_query", dependances_externes=buckets,
                       etat="disabled" if c.get("disabled") else c.get("state"), planification=c.get("schedule"),
                       detail={"source": src, "nom": c.get("displayName"), "prochaine_execution": _iso(c.get("nextRunTime")),
                               "region": self.loc[r]})
            if c.get("name") in runs:
                rr = [x for x in runs[c["name"]] if (_iso(x.get("runTime") or x.get("startTime")) or "") >= since]
                o["executions_n"] = len(rr)
                o["echecs_n"] = sum(1 for x in rr if x.get("state") == "FAILED")
                o["derniere_execution"] = max((_iso(x.get("runTime") or x.get("startTime")) for x in rr), default=None)
            self.objets.append(o)

    # level 3: capacity and consumers across the organization --------------------------------------------------------
    def _level3(self, r: str):
        vues = ["RESERVATIONS", "CAPACITY_COMMITMENTS", f"RESERVATIONS_TIMELINE ({TIMELINE_DAYS} d)", "JOBS_BY_ORGANIZATION"]
        if self.level < 3:
            for v in vues:
                self.col.skip(f"region-{r}.INFORMATION_SCHEMA.{v}", NIVEAU[3])
            return
        I, base = self._I(r), f"region-{r}.INFORMATION_SCHEMA."
        res = self._q(base + "RESERVATIONS", r, RESERVATIONS.format(I=I))
        com = self._q(base + "CAPACITY_COMMITMENTS", r, COMMITMENTS.format(I=I))
        tl = self._q(base + vues[2], r, RES_TIMELINE.format(I=I, d=TIMELINE_DAYS), fenetre=self._fen(TIMELINE_DAYS))
        if res is not None:
            self.editions = (self.editions or set()) | {x["edition"] for x in res if x.get("edition")}
        usage = {x["reservation_name"]: x for x in tl or []}
        for x in res or []:
            u = usage.get(x["reservation_name"], {})
            self.objets.append(_objet("reservation", f"{r}/{x['reservation_name']}", None, detail={
                "type": "reservation", "slots": _int(x.get("slot_capacity")), "edition": x.get("edition"),
                "autoscale_max": _int(x.get("autoscale_max")), "ignore_idle_slots": x.get("ignore_idle_slots"),
                "slots_moyens_30j": round(float(u["slots_moyens"]), 2) if u.get("slots_moyens") is not None else None,
                "minutes_30j": _int(u.get("minutes")), "region": self.loc[r]}))
        for x in com or []:
            self.objets.append(_objet("reservation", f"{r}/engagement/{x['capacity_commitment_id']}", None, etat=x.get("state"), detail={
                "type": "commitment", "plan": x.get("commitment_plan"), "slots": _int(x.get("slot_count")), "edition": x.get("edition"),
                "region": self.loc[r]}))
        rows = self._q(base + "JOBS_BY_ORGANIZATION", r, ORG.format(I=I, fj=_jobs_filter(self.days, "j."), p=self.p), fenetre=self._fen(self.days))
        if rows:
            self.objets.append(_objet("partage", f"{r}/organisation", None, externe=True, detail={
                "source": "JOBS_BY_ORGANIZATION", "projets_consommateurs_n": len(rows), "requetes_n": sum(int(x["n"] or 0) for x in rows),
                "derniere": max((_iso(x.get("derniere")) or "" for x in rows), default=None) or None}))

    # assembly ---------------------------------------------------------------------------------------------------------
    def _finish(self):
        rls_objets: dict[str, list[dict]] = {}
        for o in self.objets:
            if o["kind"] == "politique_lignes":
                rls_objets.setdefault(o["detail"]["table"], []).append(o)
        for tid, t in self.tables.items():
            d = self.ds.get(t["schema"]) or {}
            if d.get("time_travel_h") is not None:
                t["retention_jours"] = round(d["time_travel_h"] / 24, 2)
            t["detail"]["region"] = self.loc.get(t["_loc"]) if t["_loc"] else None
            if t["_loc"] in self.par_table_lu and not t["supprimee_le"]:
                n, last, nulls = self.reads.get(tid, [0, None, 0])
                t["n_lectures"], t["derniere_lecture"] = n, last
                w = self.writes.get(tid, [0, None, 0])
                t["n_ecritures"] = w[0]
                t["derniere_ecriture"] = max(filter(None, (t["derniere_ecriture"], w[1])), default=None)
                t["detail"]["lectures_octets_null_n"] = nulls
            if tid in self.shared:
                t["detail"]["lectures_partage_n"] = self.shared[tid]
            self._rls_signals(t, rls_objets.get(tid, []))
        for d in self.ds.values():
            if d["tables_n"] is None and not d["anonyme"]:
                n = sum(1 for t in self.tables.values() if t["schema"] == d["nom"] and not t["supprimee_le"])
                d["tables_n"] = n if (d["_region"], "TABLES") in self.ok or n else None

    def _rls_signals(self, t: dict, objets: list[dict]):
        """F10: the copy needs an unfiltered principal. Signals: policy read through the API, billed bytes NULL,
        visible rows = 0 while the table occupies physical storage."""
        tid = t["id"]
        pols = self.rls.get(tid)
        nulls = (self.reads.get(tid) or [0, None, 0])[2] if t["_loc"] in self.par_table_lu else None
        zero = t["lignes"] == 0 and (t["detail"].get("octets_physiques_actifs") or t["detail"].get("octets_physiques") or 0) > 0
        indices = (["billed bytes NULL"] if nulls else []) + (["visible rows = 0"] if zero and (pols or nulls) else [])
        for p in pols or []:
            if f"lignes:{p}" not in t["politiques"]:
                t["politiques"].append(f"lignes:{p}")
        if not pols and nulls and tid not in self.rls:
            t["politiques"].append("lignes:probable")
        if pols is not None or indices:
            t["detail"]["politique_lignes_n"] = len(pols) if pols is not None else None
            t["detail"]["politique_lignes_indices"] = ", ".join(indices) or None
        for o in objets:
            o["detail"].update({"lignes_visibles_zero": zero if t["lignes"] is not None else None, "octets_factures_null_n": nulls})

    def metering(self) -> dict:
        lus = {r: j for r, j in self.jobs.items() if j is not None}
        dem = res = None
        if lus:
            dn = sum(int(j.get("demande_n") or 0) for j in lus.values())
            do = [int(j["demande_octets"]) for j in lus.values() if j.get("demande_octets") is not None]
            rn = sum(int(j.get("reserve_n") or 0) for j in lus.values())
            rs = [int(j["reserve_slot_ms"]) for j in lus.values() if j.get("reserve_slot_ms") is not None]
            dem = {"octets_factures": sum(do) if do else (0 if dn == 0 else None), "requetes": dn}
            res = {"slot_heures": round(sum(rs) / 3.6e6, 3) if rs else (0 if rn == 0 else None), "requetes": rn}
        tot = lambda k: sum(int(j.get(k) or 0) for j in lus.values()) if lus else None  # noqa: E731
        live = [t for t in self.tables.values() if not t["supprimee_le"] and t["type"] != "EXTERNE"]
        stock = {k: _sum(*(t["detail"].get(k) for t in live)) for k in (
            "octets_logiques_actifs", "octets_logiques_long_terme", "octets_physiques_actifs", "octets_physiques_long_terme",
            "octets_time_travel", "octets_fail_safe")}
        stock["tables_sans_volume_n"] = sum(1 for t in live if t["octets_actifs"] is None)
        stock["supprimees_facturees_octets"] = _sum(*(t["octets_historique"] for t in self.tables.values() if t["supprimee_le"]))
        return {"par_service": [], "par_entrepot": [], "stockage_octets_moyens": None, "transferts": [],
                "a_la_demande": dem, "reserve": res, "fenetre_jours": self.days,
                "detail": {"regions_lues": sorted(self.loc[r] for r in lus), "regions_non_lues": sorted(self.loc[r] for r in self.jobs if r not in lus),
                           "octets_factures_null_n": tot("demande_octets_null_n"), "jobs_load_n": tot("load_n"),
                           "jobs_extract_n": tot("extract_n"), "jobs_copy_n": tot("copy_n"), "export_data_n": tot("export_data_n"),
                           "jobs_echecs_n": tot("echecs_n"),
                           "jobs_du": min((_iso(j.get("du")) for j in lus.values() if j.get("du")), default=None),
                           "jobs_au": max((_iso(j.get("au")) for j in lus.values() if j.get("au")), default=None),
                           "profil_charge_30j": {self.loc[r]: v for r, v in sorted(self.timeline.items())},
                           "stockage_actuel": stock}}

    def snapshot(self) -> dict:
        def clean(e: dict) -> dict:
            return {k: v for k, v in e.items() if not k.startswith("_")}

        tables = [clean(t) for _, t in sorted(self.tables.items())]
        objets = sorted([clean(o) for o in self.objets] + [clean(v) for v in self.views.values()], key=lambda o: (o["kind"], o["id"]))
        visibles = [d for d in self.ds.values() if not d["anonyme"]]
        caches = sorted(d["nom"] for d in visibles if d["cache"])
        anon = sum(1 for d in self.ds.values() if d["anonyme"])
        perimetre = (f"Project {self.p}: {len(visibles)} dataset(s)"
                     + (f" ({', '.join(self.requested)})" if self.requested else "")
                     + (f", region(s) {', '.join(self.loc[r] for r in self.complete)}" if self.complete else "")
                     + (f"; hidden datasets flagged: {', '.join(caches)}" if caches else "")
                     + (f"; {anon} anonymous dataset(s) (cached query results)" if anon else "")
                     + (f"; jobs only in {', '.join(self.loc[r] for r in self.jobs_only)}" if self.jobs_only else "")
                     + (f"; billing on {self.project}" if self.project != self.p else ""))
        if self.editions is None:
            edition = None
        else:
            edition = ", ".join(sorted(self.editions)) or "on-demand (no reservation)"
        cost = getattr(self.runner, "cost", None)
        delta = None
        if cost:
            f0, f1 = self.cost0.get("octets_factures"), cost.get("octets_factures")
            delta = {"requetes": cost["requetes"] - self.cost0.get("requetes", 0), "dry_runs": cost["dry_runs"] - self.cost0.get("dry_runs", 0),
                     "octets_estimes": cost["octets_estimes"] - self.cost0.get("octets_estimes", 0),
                     "octets_factures": None if f1 is None else f1 - (f0 or 0)}
        return {
            "schema": "canot.scan/1", "plateforme": "bigquery", "as_of": _iso(self.now), "perimetre": perimetre, "edition": edition,
            "niveau_droits": self.level, "mode_sql": "read-only", "versions": {"canot": __version__},
            "lectures": self.col.lectures, "tables": tables, "objets": objets, "familles": [], "metering": self.metering(),
            "rejouabilite": None,
            "regions": [self.loc[r] for r in self.complete + self.jobs_only],
            "datasets": [clean(d) for _, d in sorted(self.ds.items())],
            "reserves": ["Reads through the BigQuery Storage Read API, by Analytics Hub subscribers and from other projects "
                         "do not appear in the project's JOBS: \"no observed read\" covers only the project's queries."],
            "cout_du_scan": dict(delta or {}, appels_api=self.api_calls, plafond_par_requete=self.max_bytes,
                                 duree_s=round(time.monotonic() - self.t0, 1)),
        }


def collect(client_or_runner, *, project: str, data_project: str | None, datasets: list[str] | None, level: int, salt: bytes,
            days: int = 90, max_bytes_per_query: int = DEFAULT_MAX_BYTES) -> tuple[dict, list[dict]]:
    """Read-only BigQuery collection. Returns (snapshot matching model.py, raw families with `_sql`).

    `client_or_runner`: a BigQuerySource (or any object offering `run_readonly` and `client`), or a google-cloud-bigquery client."""
    runner = client_or_runner if hasattr(client_or_runner, "run_readonly") else BigQuerySource(f"bigquery://{project}", client=client_or_runner)
    scan = _Scan(runner, project=project, data_project=data_project or project, datasets=datasets, level=level, salt=salt,
                 days=days, max_bytes=max_bytes_per_query)
    scan.run()
    return scan.snapshot(), scan.raw
