"""Collecte BigQuery du scan contre un faux client : SQL envoyé, API appelées, contrat du snapshot. Aucun réseau."""
import json
import re
from types import SimpleNamespace

import pytest

from canot.scanner import bigquery as bq
from canot.scanner.guard import NotReadOnly, check_readonly
from canot.sources.bigquery_src import MIN_BILLED, BigQuerySource

CANARI = "canari-7f3a@exemple.test"
SALT = b"sel-de-test-0123"
P = "proj-test"
ANON = "_" + "a" * 40
INTERDITS = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti")


class Denied(Exception):
    pass


def _dt(day):
    return f"2026-10-{day:02d}T08:00:00Z"


class Job:
    def __init__(self, rows, est):
        self.rows, self.total_bytes_processed, self.total_bytes_billed = rows, est, MIN_BILLED

    def result(self):
        return [dict(r) for r in self.rows]


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


DATASETS = {"raw": "EU", "mart": "EU", "ext": "EU", "_tmp": "EU", "west": "europe-west1", ANON: "US"}

TABLES = {  # (dataset, nom) -> type INFORMATION_SCHEMA
    ("raw", "customers"): "BASE TABLE", ("raw", "orders"): "BASE TABLE", ("raw", "archive_2023"): "BASE TABLE",
    ("mart", "fct_orders"): "BASE TABLE", ("mart", "fct_orders_dev"): "CLONE", ("mart", "fct_orders_snap"): "SNAPSHOT",
    ("mart", "mv_daily"): "MATERIALIZED VIEW", ("mart", "v_kpi"): "VIEW", ("ext", "us_states"): "EXTERNAL",
    ("west", "events"): "BASE TABLE",
}
API_TYPES = {"BASE TABLE": "TABLE", "CLONE": "TABLE", "SNAPSHOT": "SNAPSHOT", "MATERIALIZED VIEW": "MATERIALIZED_VIEW",
             "VIEW": "VIEW", "EXTERNAL": "EXTERNAL"}


class FakeBQ:
    """Imite google.cloud.bigquery.Client : query() (dry-run puis exécution), _connection.api_request (GET), _http.get."""

    def __init__(self, *, storage=True, region_views=True, dts="disabled", rls_api=True, est=MIN_BILLED, families_null=True):
        self.storage, self.region_views, self.dts, self.rls_api, self.est = storage, region_views, dts, rls_api, est
        self.families_null = families_null
        self.jobs, self.api, self.rest = [], [], []
        self._connection = SimpleNamespace(api_request=self._api)
        self._http = SimpleNamespace(get=self._get)

    # SQL ----------------------------------------------------------------------------------------------------------------
    def query(self, sql, location=None, job_config=None):
        self.jobs.append((sql, location, job_config))
        return Job(self._rows(sql), self.est)

    def real_sql(self):
        return [s for s, _, cfg in self.jobs if not getattr(cfg, "dry_run", False)]

    def _rows(self, sql):
        view = re.search(r"INFORMATION_SCHEMA\.(\w+)", sql).group(1)
        region = (re.search(r"`region-([a-z0-9-]+)`", sql) or [None, None])[1]
        eu = region == "eu"
        mine = [(ds, n, t) for (ds, n), t in TABLES.items() if (DATASETS[ds].lower() == region)]
        if view in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS") and not self.region_views:
            raise Denied("403 Access Denied: User does not have the required permissions ('bigquery.tables.list')")
        if view == "TABLES":
            return [{"table_schema": ds, "table_name": n, "table_type": t, "creation_time": _dt(1),
                     "base_table_catalog": P if t in ("CLONE", "SNAPSHOT") else None,
                     "base_table_schema": "mart" if t in ("CLONE", "SNAPSHOT") else None,
                     "base_table_name": "fct_orders" if t in ("CLONE", "SNAPSHOT") else None,
                     "mv_ddl": f"CREATE MATERIALIZED VIEW `{P}.mart.mv_daily` AS SELECT order_date FROM `{P}`.mart.fct_orders" if n == "mv_daily" else None}
                    for ds, n, t in mine]
        if view == "TABLE_OPTIONS":
            return [{"table_schema": "ext", "table_name": "us_states", "option_name": "uris",
                     "option_value": f'["gs://bucket-canot/secret/{CANARI}.csv"]'},
                    {"table_schema": "raw", "table_name": "customers", "option_name": "labels",
                     "option_value": f'[STRUCT("pii", "true"), STRUCT("owner", "{CANARI}")]'}] if eu else []
        if view == "COLUMNS":
            out = []
            for ds, n, _ in mine:
                cols = [("id", "INT64"), ("geo", "GEOGRAPHY"), ("payload", "JSON")] if n == "customers" else [("order_date", "DATE"), ("customer_id", "INT64"), ("status", "STRING")]
                for i, (c, t) in enumerate(cols, 1):
                    out.append({"table_schema": ds, "table_name": n, "column_name": c, "ordinal_position": i, "data_type": t,
                                "is_partitioning_column": "YES" if (n == "fct_orders" and c == "order_date") else "NO",
                                "clustering_ordinal_position": {"customer_id": 1, "status": 2}.get(c) if n == "fct_orders" else None,
                                "is_system_defined": "NO"})
            return out
        if view == "COLUMN_FIELD_PATHS":
            return [{"table_schema": "raw", "table_name": "customers", "field_path": "payload", "policy_tags": ["projects/x/policyTags/1"]}] if eu else []
        if view == "TABLE_STORAGE":
            if not self.storage:
                raise Denied("403 Access Denied: User does not have permission to query table region-eu.INFORMATION_SCHEMA.TABLE_STORAGE, "
                             "or perhaps it does not exist. Run ALTER PROJECT SET OPTIONS (`region-eu.enable_info_schema_storage` = TRUE)")
            rows = [{"table_schema": ds, "table_name": n, "table_type": t, "deleted": False, "total_rows": 0 if n == "fct_orders" else 100,
                     "total_partitions": 1, "total_logical_bytes": 1000, "active_logical_bytes": 1000, "long_term_logical_bytes": 0,
                     "total_physical_bytes": 400, "active_physical_bytes": 300, "long_term_physical_bytes": 0, "time_travel_physical_bytes": 100,
                     "fail_safe_physical_bytes": 50, "storage_last_modified_time": _dt(2), "table_deletion_time": None}
                    for ds, n, t in mine if t in ("BASE TABLE", "CLONE", "SNAPSHOT")]
            if eu:
                rows.append({"table_schema": "raw", "table_name": "tmp_dropped", "table_type": "BASE TABLE", "deleted": True, "total_rows": 5,
                             "total_logical_bytes": 0, "time_travel_physical_bytes": 700, "fail_safe_physical_bytes": 300,
                             "storage_last_modified_time": _dt(3), "table_deletion_time": _dt(4)})
            return rows
        if view == "VIEWS":
            return [{"table_schema": "mart", "table_name": "v_kpi", "use_standard_sql": "YES",
                     "view_definition": f"SELECT name FROM `{P}`.ext.us_states WHERE note = '{CANARI}'"}] if eu else []
        if view == "MATERIALIZED_VIEWS":
            return [{"table_schema": "mart", "table_name": "mv_daily", "last_refresh_time": _dt(5)}] if eu else []
        if view == "ROUTINES":
            return [{"routine_schema": "mart", "routine_name": "sp_dyn", "routine_type": "PROCEDURE", "routine_body": "SQL", "external_language": None,
                     "routine_definition": f"BEGIN DECLARE q STRING DEFAULT '{CANARI}'; EXECUTE IMMEDIATE q; "
                                           f"CREATE OR REPLACE TABLE `{P}`.mart.dim AS SELECT * FROM `{P}`.raw.customers; END", "last_altered": _dt(6)},
                    {"routine_schema": "mart", "routine_name": "clean_name", "routine_type": "FUNCTION", "routine_body": "EXTERNAL",
                     "external_language": "JAVASCRIPT", "routine_definition": "return s;", "last_altered": _dt(6)},
                    {"routine_schema": "mart", "routine_name": "fx", "routine_type": "FUNCTION", "routine_body": "SQL", "external_language": None,
                     "routine_definition": "CAST(x * 100 AS INT64)", "last_altered": _dt(6)}] if eu else []
        if view == "JOBS":
            if "demande_octets_null_n" in sql:
                if not eu:  # agrégat sans job : une ligne de zéros et de NULL
                    return [{"demande_n": 0, "demande_octets": None, "demande_octets_null_n": 0, "reserve_n": 0, "reserve_slot_ms": None,
                             "load_n": 0, "extract_n": 0, "copy_n": 0, "export_data_n": 0, "echecs_n": 0, "du": None, "au": None}]
                return [{"demande_n": 12, "demande_octets": None, "demande_octets_null_n": 12, "reserve_n": 0, "reserve_slot_ms": None,
                         "load_n": 1, "extract_n": 0, "copy_n": 0, "export_data_n": 0, "echecs_n": 1, "du": _dt(1), "au": _dt(16)}]
            if "hash_bq" in sql:
                if not eu:
                    return []
                labels = [{"key": "app", "value": "looker"}, {"key": "owner", "value": CANARI}]
                return [{"hash_bq": "h1", "hash_repli": False, "sql_text": f"SELECT * FROM `{P}`.mart.fct_orders WHERE email = '{CANARI}'",
                         "statement_type": "SELECT", "n_exec": 9, "echecs_n": 0, "temps_ms": 900, "slot_ms": 50,
                         "octets": None if self.families_null else 10, "octets_traites": None, "octets_null_n": 9, "demande_n": 9,
                         "reservation": None, "premiere": _dt(1), "derniere": _dt(15),
                         "tables_ref": [{"project_id": P, "dataset_id": "mart", "table_id": "fct_orders"}],
                         "utilisateur": CANARI, "utilisateurs_n": 1, "labels": labels, "job_id": "job_x"},
                        {"hash_bq": "h2", "hash_repli": True, "sql_text": "SELECT 1", "statement_type": "SELECT", "n_exec": 3, "echecs_n": 0,
                         "temps_ms": 30, "slot_ms": 3, "octets": 30, "octets_traites": 30, "octets_null_n": 0, "demande_n": 3, "reservation": None,
                         "premiere": _dt(2), "derniere": _dt(3), "tables_ref": [], "utilisateur": "svc@proj.iam.test", "utilisateurs_n": 1,
                         "labels": [{"key": "app", "value": CANARI}], "job_id": "bquxjob_1"}]
            if "'lecture'" in sql:
                return [{"sens": "lecture", "projet": P, "dataset": "mart", "nom": "fct_orders", "n": 9, "derniere": _dt(15), "octets_null_n": 9},
                        {"sens": "lecture", "projet": P, "dataset": "raw", "nom": "customers", "n": 4, "derniere": _dt(14), "octets_null_n": 0},
                        {"sens": "ecriture", "projet": P, "dataset": "mart", "nom": "fct_orders", "n": 2, "derniere": _dt(16), "octets_null_n": None}] if eu else []
        if view == "JOBS_TIMELINE":
            return [{"heure": _dt(10), "slot_ms": 3_600_000}, {"heure": _dt(11), "slot_ms": 7_200_000}]
        if view == "PARTITIONS":
            return [{"table_name": "fct_orders", "partitions": 600, "derniere_modif": _dt(9), "modifiees": 2, "long_terme": 500}]
        if view in ("SCHEMATA_LINKS", "SHARED_DATASET_USAGE", "RESERVATIONS", "CAPACITY_COMMITMENTS", "RESERVATIONS_TIMELINE"):
            return []
        if view == "JOBS_BY_ORGANIZATION":
            raise Denied("403 Access Denied: User does not have the required permissions ('bigquery.jobs.listAll' at the organization level)")
        raise AssertionError(f"vue inattendue : {view}")

    # API BigQuery v2 ----------------------------------------------------------------------------------------------------
    def _api(self, method, path, query_params=None):
        self.api.append((method, path, dict(query_params or {})))
        parts = path.strip("/").split("/")  # projects/P/datasets/D/tables/T/rowAccessPolicies
        if parts[2:] == ["datasets"]:
            return {"datasets": [{"datasetReference": {"datasetId": d}, "location": loc, "type": "DEFAULT"} for d, loc in DATASETS.items()]}
        ds = parts[3]
        if len(parts) == 4:
            return {"location": DATASETS[ds], "type": "DEFAULT", "maxTimeTravelHours": "168", "labels": {"owner": CANARI},
                    "access": [{"role": "OWNER", "userByEmail": CANARI}, {"role": "READER", "specialGroup": "projectReaders"},
                               {"view": {"projectId": "autre-projet", "datasetId": "x", "tableId": "v"}}]}
        if parts[4] == "models":
            return {"models": [{"modelReference": {"modelId": "churn"}, "modelType": "LOGISTIC_REGRESSION"}]} if ds == "mart" else {}
        if len(parts) == 5:  # tables.list
            if ds == "_tmp":
                return {"tables": [{"tableReference": {"tableId": "scratch"}, "type": "TABLE"}]}
            return {"tables": [{"tableReference": {"tableId": n}, "type": API_TYPES[t]} for (d, n), t in TABLES.items() if d == ds]}
        name = parts[5]
        if len(parts) == 7:  # rowAccessPolicies : paginé par pageSize (maxResults y est refusé, 400)
            if "maxResults" in (query_params or {}):
                raise Denied("400 Bad Request: unknown parameter maxResults")
            if not self.rls_api:
                raise Denied("403 Forbidden: does not have bigquery.rowAccessPolicies.list")
            if (ds, name) == ("mart", "fct_orders"):
                return {"rowAccessPolicies": [{"rowAccessPolicyReference": {"policyId": "only_owner"}, "creationTime": "1792216515432",
                                               "filterPredicate": f"email = '{CANARI}'"}]}
            return {}
        typ = TABLES.get((ds, name), "BASE TABLE")
        props = {"type": API_TYPES[typ], "creationTime": "1792216515432", "lastModifiedTime": "1792216515432",
                 "numRows": "0" if name == "fct_orders" else "100", "numTotalLogicalBytes": "2000", "numTotalPhysicalBytes": "800",
                 "numActivePhysicalBytes": "600", "numTimeTravelPhysicalBytes": "200",
                 "labels": {"owner": CANARI},
                 "schema": {"fields": [{"name": "id", "type": "INTEGER"},
                                       {"name": "actor", "type": "RECORD", "fields": [{"name": "device", "type": "STRING"}]},
                                       {"name": "tags", "type": "STRING", "mode": "REPEATED", "policyTags": {"names": ["projects/x/t/1"]}}]}}
        if typ == "CLONE":
            props["cloneDefinition"] = {"baseTableReference": {"projectId": P, "datasetId": "mart", "tableId": "fct_orders"}}
        if typ == "SNAPSHOT":
            props["snapshotDefinition"] = {"baseTableReference": {"projectId": P, "datasetId": "mart", "tableId": "fct_orders"}}
        if typ == "EXTERNAL":
            props["externalDataConfiguration"] = {"sourceUris": [f"gs://bucket-canot/secret/{CANARI}.csv"], "sourceFormat": "CSV"}
        if typ == "VIEW":
            props["view"] = {"query": f"SELECT * FROM [{P}:ext.us_states] WHERE n = '{CANARI}'", "useLegacySql": True}
        if typ == "MATERIALIZED VIEW":
            props["materializedView"] = {"query": f"SELECT order_date FROM `{P}.mart.fct_orders`"}
        if name == "fct_orders":
            props["timePartitioning"] = {"type": "DAY", "field": "order_date"}
            props["clustering"] = {"fields": ["customer_id", "status"]}
        return props

    # autres API (transferts, connexions) ---------------------------------------------------------------------------------
    def _get(self, url, params=None):
        self.rest.append(url)
        if "bigquerydatatransfer" in url:
            if self.dts == "disabled":
                return Resp(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": f"API has not been used in project 1 ({CANARI})",
                                            "details": [{"reason": "SERVICE_DISABLED"}]}})
            if url.endswith("/runs"):
                return Resp(200, {"transferRuns": [{"state": "FAILED", "runTime": _dt(15)}, {"state": "SUCCEEDED", "runTime": _dt(14)}]})
            if "/locations/eu/" in url:
                return Resp(200, {"transferConfigs": [
                    {"name": "projects/1/locations/eu/transferConfigs/abc", "displayName": "nightly", "dataSourceId": "scheduled_query",
                     "destinationDatasetId": "mart", "schedule": "every 24 hours", "disabled": True, "state": "FAILED",
                     "ownerInfo": {"email": CANARI}, "params": {"query": f"SELECT * FROM `{P}`.raw.orders WHERE x = '{CANARI}'"}},
                    {"name": "projects/1/locations/eu/transferConfigs/gcs", "displayName": "ingest", "dataSourceId": "google_cloud_storage",
                     "destinationDatasetId": "raw", "params": {"data_path_template": f"gs://ingest-bucket/{CANARI}/*.csv"}}]})
            return Resp(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
        if "bigqueryconnection" in url:
            if "/locations/eu/" in url:
                return Resp(200, {"connections": [{"name": "projects/1/locations/eu/connections/pg", "cloudSql": {"credential": {"username": CANARI}}}]})
            return Resp(200, {})
        raise AssertionError(url)

    def get_dataset(self, ref):
        return SimpleNamespace(location=DATASETS[ref.split(".")[-1]])

    def list_datasets(self):
        return [SimpleNamespace(dataset_id=d) for d in DATASETS if not d.startswith("_")]


def run(level=3, **kw):
    fake = FakeBQ(**kw)
    src = BigQuerySource(f"bigquery://{P}", client=fake)
    snap, raw = bq.collect(src, project=P, data_project=P, datasets=None, level=level, salt=SALT, max_bytes_per_query=MIN_BILLED)
    return fake, snap, raw


def lecture(snap, vue):
    found = [x for x in snap["lectures"] if x["vue"] == vue]
    assert found, f"lecture absente : {vue} ; vues : {[x['vue'] for x in snap['lectures']]}"
    return found[0]


def table(snap, tid):
    return next(t for t in snap["tables"] if t["id"] == tid)


def objet(snap, oid):
    return next(o for o in snap["objets"] if o["id"] == oid)


TABLE_KEYS = {"id", "base", "schema", "nom", "type", "flags", "lignes", "octets_actifs", "octets_historique", "clone_de", "colonnes",
              "retention_jours", "derniere_ddl", "derniere_ecriture", "derniere_lecture", "n_lectures", "n_ecritures", "proprietaire",
              "politiques", "etiquettes", "supprimee_le", "externe_uri", "partitionnement", "clustering"}
OBJET_KEYS = {"kind", "id", "schema", "langage", "proprietaire", "externe", "dependances_externes", "reference", "etat", "planification",
              "executions_n", "echecs_n", "derniere_execution", "sql_dynamique", "detail"}


def test_snapshot_conforme():
    _, snap, raw = run()
    json.dumps(snap)  # sérialisable
    for k in ("schema", "plateforme", "as_of", "perimetre", "edition", "niveau_droits", "mode_sql", "versions", "lectures", "tables",
              "objets", "familles", "metering", "rejouabilite"):
        assert k in snap
    assert snap["schema"] == "canot.scan/1" and snap["plateforme"] == "bigquery" and snap["niveau_droits"] == 3
    for lec in snap["lectures"]:
        assert set(lec) == {"id", "vue", "statut", "raison", "lignes", "fenetre", "latence_max_h"}
        assert lec["id"].startswith("B") and lec["statut"] in ("lu", "non_lu")
        assert lec["statut"] == "lu" or (lec["raison"] and lec["lignes"] is None)
    assert len({x["id"] for x in snap["lectures"]}) == len(snap["lectures"])
    for t in snap["tables"]:
        assert TABLE_KEYS <= set(t) and t["type"] in ("TABLE", "EXTERNE", "ICEBERG", "TRANSITOIRE", "CLONE", "SNAPSHOT", "TEMPORAIRE")
    for o in snap["objets"]:
        assert OBJET_KEYS <= set(o) and not any(k.startswith("_") for k in o)
    m = snap["metering"]
    assert {"par_service", "par_entrepot", "stockage_octets_moyens", "transferts", "a_la_demande", "reserve", "fenetre_jours"} <= set(m)
    assert snap["regions"] == ["EU", "europe-west1", "US"]
    assert table(snap, f"{P}.mart.fct_orders_dev")["type"] == "CLONE"
    assert table(snap, f"{P}.mart.fct_orders_dev")["clone_de"] == f"{P}.mart.fct_orders"
    fct = table(snap, f"{P}.mart.fct_orders")
    assert fct["partitionnement"] == "order_date" and fct["clustering"] == ["customer_id", "status"]
    assert fct["detail"]["partitions_modifiees"] == 2
    assert objet(snap, f"{P}.mart.v_kpi")["reference"] == [f"{P}.ext.us_states"]
    assert objet(snap, f"{P}.mart.mv_daily")["reference"] == [f"{P}.mart.fct_orders"]
    assert objet(snap, f"{P}.mart.churn")["kind"] == "modele_ml"
    assert table(snap, f"{P}.raw.customers")["politiques"] == ["policy_tag:payload"]
    dropped = next(t for t in snap["tables"] if t["nom"] == "tmp_dropped")
    assert dropped["supprimee_le"] == _dt(4) and dropped["octets_historique"] == 1000
    raw_text = json.dumps(snap, ensure_ascii=False).lower()
    assert not any(w in raw_text for w in INTERDITS)


def test_sql_lecture_seule_etiquete_dry_run_et_plafond():
    fake, _, _ = run()
    assert fake.jobs
    for i, (sql, loc, cfg) in enumerate(fake.jobs):
        check_readonly(sql, "bigquery")
        assert cfg.labels == {"tool": "canot"}
        assert not re.search(r"\balter\b", sql, re.I) and "enable_info_schema_storage" not in sql.lower()
        assert "error_result" not in sql.lower().replace("error_result is", "")
        assert "ddl" not in sql.lower().replace("if(table_type = 'materialized view', ddl, null) as mv_ddl", "")
        if not cfg.dry_run:
            prev_sql, prev_loc, prev_cfg = fake.jobs[i - 1]
            assert prev_cfg.dry_run and prev_sql == sql and prev_loc == loc
            assert cfg.maximum_bytes_billed >= MIN_BILLED
    assert {m for m, _, _ in fake.api} == {"GET"}
    assert {s for s, _, cfg in fake.jobs if not cfg.dry_run}  # au moins une requête exécutée


def test_une_requete_par_region_pas_par_dataset():
    fake, _, _ = run()
    real = fake.real_sql()
    for view in ("TABLES", "COLUMNS", "TABLE_STORAGE", "VIEWS", "ROUTINES", "TABLE_OPTIONS"):
        sent = [s for s in real if re.search(rf"INFORMATION_SCHEMA\.{view}\b", s)]
        assert sorted(re.search(r"`region-([a-z0-9-]+)`", s)[1] for s in sent) == ["eu", "europe-west1"], view
    # trois datasets visibles en EU, une seule passe ; aucune requête par dataset hors PARTITIONS (qualificatif de dataset imposé)
    assert not [s for s in real if re.search(r"`(raw|mart|ext)`\.INFORMATION_SCHEMA\.(?!PARTITIONS)", s)]
    # région US : seulement un jeu anonyme, donc jobs seuls
    us = [re.search(r"INFORMATION_SCHEMA\.(\w+)", s)[1] for s in real if "`region-us`" in s]
    assert set(us) <= {"JOBS", "JOBS_TIMELINE", "RESERVATIONS", "CAPACITY_COMMITMENTS", "RESERVATIONS_TIMELINE", "JOBS_BY_ORGANIZATION"}


def test_repli_tables_get_sans_alter_quand_table_storage_echoue():
    fake, snap, _ = run(storage=False)
    lec = lecture(snap, "region-eu.INFORMATION_SCHEMA.TABLE_STORAGE")
    assert lec["statut"] == "non_lu" and "enable_info_schema_storage" in lec["raison"] and "repli sur tables.get" in lec["raison"]
    assert lecture(snap, "API tables.get (region-eu)")["statut"] == "lu"
    t = table(snap, f"{P}.raw.orders")
    assert (t["lignes"], t["octets_actifs"], t["octets_historique"]) == (100, 2000, 200)
    assert t["detail"]["source_volumes"] == "tables.get" and t["detail"]["octets_fail_safe"] is None
    assert not any(re.search(r"\balter\b", s, re.I) for s, _, _ in fake.jobs)
    assert any(p.endswith("/tables/orders") for _, p, _ in fake.api)


def test_api_desactivee_non_lu_service_desactive():
    _, snap, _ = run(dts="disabled")
    lec = lecture(snap, "API transferConfigs.list (eu)")
    assert (lec["statut"], lec["raison"]) == ("non_lu", "service désactivé")
    assert lecture(snap, "API connections.list (eu)")["statut"] == "lu"


def test_octets_factures_null_restent_none():
    _, snap, raw = run()
    m = snap["metering"]
    assert m["a_la_demande"] == {"octets_factures": None, "requetes": 12}
    assert m["detail"]["octets_factures_null_n"] == 12
    assert m["reserve"] == {"slot_heures": 0, "requetes": 0}  # zéro mesuré : aucune requête sur réservation
    assert raw[0]["octets"] is None and raw[0]["detail"]["octets_null_n"] == 9
    assert raw[1]["octets"] == 30


def test_canari_absent_des_sorties(caplog):
    caplog.set_level("DEBUG")
    _, snap, raw = run(dts="ok")
    assert CANARI not in json.dumps(snap, ensure_ascii=False)
    assert CANARI not in caplog.text  # sqlglot journalise le texte qu'il ne sait pas analyser
    for f in raw:
        assert CANARI not in json.dumps({k: v for k, v in f.items() if k != "_sql"}, ensure_ascii=False)
    assert CANARI in raw[0]["_sql"]  # le texte brut ne sort que par le second retour
    assert raw[0]["utilisateur"].startswith("u-") and raw[1]["app"].startswith("app-")
    assert table(snap, f"{P}.ext.us_states")["externe_uri"] == "gs://bucket-canot"
    assert all(e.startswith(("pii=v-", "owner=v-")) for e in table(snap, f"{P}.raw.customers")["etiquettes"])


def test_niveau_1_ne_lit_ni_niveau_2_ni_niveau_3():
    fake, snap, raw = run(level=1)
    sent = " ".join(fake.real_sql())
    for marker in ("hash_bq", "'lecture'", "JOBS_TIMELINE", "PARTITIONS", "SCHEMATA_LINKS", "SHARED_DATASET_USAGE", "RESERVATIONS",
                   "CAPACITY_COMMITMENTS", "JOBS_BY_ORGANIZATION", "user_email", "sql_text"):
        assert marker not in sent, marker
    assert not fake.rest and not any("rowAccessPolicies" in p for _, p, _ in fake.api)
    assert raw == []
    skipped = {x["raison"] for x in snap["lectures"] if x["statut"] == "non_lu" and x["raison"].startswith("niveau")}
    assert skipped == {"niveau 2 non demandé", "niveau 3 non demandé"}
    assert all(t["n_lectures"] is None for t in snap["tables"])  # non lu, jamais 0
    assert snap["metering"]["a_la_demande"]["requetes"] == 12


def test_niveau_2_sans_identites():
    fake, _, raw = run(level=2)
    fam = [s for s in fake.real_sql() if "hash_bq" in s]
    assert fam and not any("user_email" in s or "labels) AS labels" in s for s in fam)
    assert raw[0]["utilisateur"] is None and raw[0]["outil"] is None


def test_politique_de_lignes_detectee():
    _, snap, _ = run()
    fct = table(snap, f"{P}.mart.fct_orders")
    assert "lignes:only_owner" in fct["politiques"]
    assert fct["detail"]["politique_lignes_n"] == 1
    assert fct["detail"]["politique_lignes_indices"] == "octets facturés NULL, lignes visibles = 0"
    pol = objet(snap, f"{P}.mart.fct_orders.only_owner")
    assert pol["kind"] == "politique_lignes" and pol["reference"] == [fct["id"]]
    assert pol["detail"]["lignes_visibles_zero"] is True and pol["detail"]["octets_factures_null_n"] == 9


def test_politique_de_lignes_probable_sans_droit_api():
    _, snap, _ = run(rls_api=False)
    assert lecture(snap, "API rowAccessPolicies.list (region-eu)")["raison"] == "droit absent"
    fct = table(snap, f"{P}.mart.fct_orders")
    assert fct["politiques"] == ["lignes:probable"] and fct["detail"]["politique_lignes_n"] is None
    assert "lignes:probable" not in table(snap, f"{P}.raw.customers")["politiques"]


def test_lectures_par_table_et_zero_mesure():
    _, snap, _ = run()
    assert table(snap, f"{P}.mart.fct_orders")["n_lectures"] == 9
    assert table(snap, f"{P}.mart.fct_orders")["n_ecritures"] == 2
    arch = table(snap, f"{P}.raw.archive_2023")
    assert arch["n_lectures"] == 0 and arch["derniere_lecture"] is None  # lu, aucune lecture sur 180 j
    assert lecture(snap, "region-eu.INFORMATION_SCHEMA.JOBS (lectures par table)")["fenetre"] is not None


def test_datasets_caches_signales_pas_ignores():
    _, snap, _ = run()
    ds = {d["nom"]: d for d in snap["datasets"]}
    assert ds["_tmp"]["cache"] and not ds["_tmp"]["anonyme"]
    assert ds[ANON]["anonyme"]
    scratch = table(snap, f"{P}._tmp.scratch")
    assert "dataset_cache" in scratch["flags"]
    assert "_tmp" in snap["perimetre"]
    assert ds["raw"]["acces"]["autorisations_hors_projet_n"] == 1 and ds["raw"]["acces"]["par_type"]["userByEmail"] == 1


def test_vues_de_region_refusees_repli_api():
    fake, snap, _ = run(region_views=False)
    assert lecture(snap, "region-eu.INFORMATION_SCHEMA.TABLES")["statut"] == "non_lu"
    assert lecture(snap, "API tables.list (region-eu)")["statut"] == "lu"
    dev = table(snap, f"{P}.mart.fct_orders_dev")
    assert dev["type"] == "CLONE" and dev["clone_de"] == f"{P}.mart.fct_orders"
    assert table(snap, f"{P}.ext.us_states")["externe_uri"] == "gs://bucket-canot"
    v = objet(snap, f"{P}.mart.v_kpi")
    assert v["langage"] == "SQL hérité" and v["reference"] == [f"{P}.ext.us_states"]
    orders = table(snap, f"{P}.raw.orders")
    assert orders["colonnes"][1] == {"nom": "actor", "type": "STRUCT<device STRING>"}
    assert orders["colonnes"][2]["type"] == "ARRAY<STRING>" and "policy_tag:tags" in orders["politiques"]
    assert table(snap, f"{P}.mart.fct_orders")["partitionnement"] == "order_date"


def test_plafond_d_octets_refuse_avant_execution():
    fake, snap, _ = run(est=MIN_BILLED * 50)
    assert not fake.real_sql()
    lec = lecture(snap, "region-eu.INFORMATION_SCHEMA.TABLES")
    assert lec["statut"] == "non_lu" and lec["raison"].startswith("volume estimé au-delà du plafond")


def test_garde_refuse_avant_envoi():
    fake = FakeBQ()
    src = BigQuerySource(f"bigquery://{P}", client=fake)
    with pytest.raises(NotReadOnly):
        src.run_readonly("ALTER PROJECT `proj-test` SET OPTIONS (`region-eu.enable_info_schema_storage` = TRUE)", location="EU")
    assert fake.jobs == []


def test_routines_langage_et_sql_dynamique():
    _, snap, _ = run()
    sp = objet(snap, f"{P}.mart.sp_dyn")
    assert sp["kind"] == "procedure" and sp["sql_dynamique"] is True and f"{P}.raw.customers" in sp["reference"]
    js = objet(snap, f"{P}.mart.clean_name")
    assert (js["langage"], js["sql_dynamique"], js["externe"]) == ("JAVASCRIPT", False, False)
    assert objet(snap, f"{P}.mart.fx")["sql_dynamique"] is False


def test_requetes_planifiees_lues():
    _, snap, _ = run(dts="ok")
    q = objet(snap, "eu/abc")
    assert (q["kind"], q["etat"], q["echecs_n"], q["executions_n"]) == ("requete_planifiee", "désactivée", 1, 2)
    assert q["reference"] == [f"{P}.raw.orders"] and q["detail"]["nom"] == "nightly"
    g = objet(snap, "eu/gcs")
    assert g["externe"] and g["dependances_externes"] == ["gs://ingest-bucket"]
    c = objet(snap, "eu/pg")
    assert c["kind"] == "connexion" and c["detail"]["type"] == "cloudSql"


def test_outil_et_app_vocabulaire_ferme():
    assert bq.outil({"dbt_invocation_id": "x"}, None) == "dbt"
    assert bq.outil({}, "scheduled_query_123") == "requête planifiée"
    assert bq.outil({"team": "finance"}, "job_1") is None
    assert bq.app({"app": "Looker"}, SALT) == "looker"
    assert bq.app({"app": "tableau-de-bord-rh"}, SALT).startswith("app-")


def test_references_sql():
    assert bq.refs(f"SELECT * FROM `{P}.a.t` JOIN b.u USING (id)", P) == [f"{P}.a.t", f"{P}.b.u"]
    assert bq.refs("SELECT * FROM [other:ds.t]", P, legacy=True) == ["other.ds.t"]
    assert bq.refs(f"BEGIN INSERT INTO `{P}`.a.t SELECT 1; END", P) == [f"{P}.a.t"]
    # procédure non analysée : ni une adresse dans un littéral, ni un champ de STRUCT ne deviennent des objets
    body = f"BEGIN SELECT orders.actor.device FROM `{P}`.a.t WHERE m = 'jean.dupont@corp.example.com'; END"
    assert bq.refs(body, P) == [f"{P}.a.t"]
    assert bq.refs("SELECT * FROM [ds.t] WHERE x = '[a.b.c]'", P, legacy=True) == [f"{P}.ds.t"]
    assert bq.refs(None, P) == []


def test_identifiants_valides_avant_tout_sql():
    with pytest.raises(ValueError):
        bq.collect(FakeBQ(), project="p`; DROP", data_project=None, datasets=None, level=1, salt=SALT)
    with pytest.raises(ValueError):
        bq.collect(FakeBQ(), project=P, data_project=P, datasets=["x' OR 1=1"], level=1, salt=SALT)


def test_location_vient_des_datasets():
    fake = FakeBQ()
    src = BigQuerySource(f"bigquery://{P}", client=fake)
    assert src.location is None
    assert src._regions() == ["EU", "europe-west1"]
    assert BigQuerySource(f"bigquery://{P}?location=US", client=fake)._regions() == ["US"]
