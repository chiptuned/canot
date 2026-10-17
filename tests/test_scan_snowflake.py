"""Collecte Snowflake du scan, contre un faux compte : vérifie le SQL envoyé (lecture seule, colonnes interdites, niveaux) et le snapshot
(contrat, honnêteté des chiffres, pseudonymisation, canari). Aucun réseau."""
import json
import re
from datetime import datetime, timezone

import pytest

from canot.scanner import snowflake as sf
from canot.scanner.guard import NotReadOnly, check_readonly

CANARI = "canari-7f3a@exemple.test"
NOW = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
D = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
SALT = b"0123456789abcdef"
FORBIDDEN_SQL = ("BIND_VALUES", "ERROR_MESSAGE", "EMAIL", "LOGIN_NAME", "CLIENT_IP", "CLIENT_ENVIRONMENT", "LAST_DDL_BY")
FORBIDDEN_WORDS = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti", "conforme", "certifié",
                   "ligne par ligne")


def T(tid, schema, name, typ="BASE TABLE", **kw):
    r = {"TABLE_ID": tid, "TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": schema, "TABLE_NAME": name, "TABLE_OWNER": "LAB_ENGINEER",
         "TABLE_TYPE": typ, "IS_TRANSIENT": "NO", "CLUSTERING_KEY": None, "AUTO_CLUSTERING_ON": "NO", "ROW_COUNT": 100, "BYTES": 2048,
         "RETENTION_TIME": 1, "CREATED": D, "LAST_DDL": D, "DELETED": None, "IS_ICEBERG": "NO", "IS_DYNAMIC": "NO", "IS_HYBRID": "NO"}
    r.update(kw)
    return r


def M(tid, schema, name, **kw):
    r = {"ID": tid, "TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": schema, "TABLE_NAME": name, "CLONE_GROUP_ID": tid, "IS_TRANSIENT": "NO",
         "ACTIVE_BYTES": 1000, "TIME_TRAVEL_BYTES": 10, "FAILSAFE_BYTES": 20, "RETAINED_FOR_CLONE_BYTES": 0, "DELETED": False, "TABLE_DROPPED": None}
    r.update(kw)
    return r


AH_ROWS = [
    {"SENS": "L", "OBJET_ID": 1, "OBJET": "CANOT_LAB.RAW.CUSTOMERS", "DOMAINE": "Table", "N": 5, "DERNIERE": D},
    {"SENS": "L", "OBJET_ID": 2, "OBJET": "CANOT_LAB.RAW.ORDERS", "DOMAINE": "Table", "N": 3, "DERNIERE": D},
    {"SENS": "E", "OBJET_ID": 2, "OBJET": "CANOT_LAB.RAW.ORDERS", "DOMAINE": "Table", "N": 7, "DERNIERE": NOW},
    {"SENS": "E", "OBJET_ID": 501, "OBJET": "CANOT_LAB.OPS.STG_EXTERNE", "DOMAINE": "Stage", "N": 2, "DERNIERE": D},
]

DATA = {
    "METERING_DAILY_HISTORY": [{"SERVICE_TYPE": "WAREHOUSE_METERING", "UTILISES": 10.5, "FACTURES": 10.0, "DU": D.date(), "AU": NOW.date()},
                               {"SERVICE_TYPE": "AUTO_CLUSTERING", "UTILISES": 0.3, "FACTURES": 0.3, "DU": D.date(), "AU": NOW.date()}],
    "TABLES": [
        T(1, "RAW", "CUSTOMERS"),
        T(2, "RAW", "ORDERS", RETENTION_TIME=30, CLUSTERING_KEY="LINEAR(TO_DATE(ORDER_TS))", AUTO_CLUSTERING_ON="YES"),
        T(3, "RAW", "ORDERS_CLONE"),
        T(4, "RAW", "ARCHIVE_1"),
        T(5, "RAW", "TMP_DROPPED", DELETED=D),
        T(6, "SANDBOX", "OLD_EXPORT_2023", TABLE_OWNER="LAB_ORPHAN"),
        T(7, "EXT", "US_STATES", "EXTERNAL TABLE", ROW_COUNT=None),
        T(8, "RAW", "EVENTS"),
        T(80, "RAW", "EVENTS", DELETED=D),
        T(9, "MART", "MV_DAILY", "MATERIALIZED VIEW"),
        T(10, "MART", "DT_COUNTRY", IS_DYNAMIC="YES"),
        T(11, "STG", "V_STATES", "VIEW"),
    ],
    "COLUMNS": [{"TABLE_ID": 1, "COLUMN_NAME": "CUSTOMER_ID", "DATA_TYPE": "NUMBER", "NUMERIC_PRECISION": 38, "NUMERIC_SCALE": 0, "ORDINAL_POSITION": 1},
                {"TABLE_ID": 1, "COLUMN_NAME": "EMAIL_ADDR", "DATA_TYPE": "TEXT", "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "ORDINAL_POSITION": 2},
                {"TABLE_ID": 1, "COLUMN_NAME": "PROFILE", "DATA_TYPE": "VARIANT", "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "ORDINAL_POSITION": 3},
                {"TABLE_ID": 1, "COLUMN_NAME": "GEO", "DATA_TYPE": "GEOGRAPHY", "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "ORDINAL_POSITION": 4}],
    "TABLE_STORAGE_METRICS": [M(1, "RAW", "CUSTOMERS"), M(2, "RAW", "ORDERS", TIME_TRAVEL_BYTES=5000), M(3, "RAW", "ORDERS_CLONE", CLONE_GROUP_ID=2),
                              M(4, "RAW", "ARCHIVE_1"), M(5, "RAW", "TMP_DROPPED", ACTIVE_BYTES=0, FAILSAFE_BYTES=1000, TIME_TRAVEL_BYTES=0,
                                                         DELETED=True, TABLE_DROPPED=D),
                              M(99, "RAW", "OLD_GONE", ACTIVE_BYTES=0, TIME_TRAVEL_BYTES=0, FAILSAFE_BYTES=0, RETAINED_FOR_CLONE_BYTES=500,
                                DELETED=True, TABLE_DROPPED=datetime(2024, 1, 1, tzinfo=timezone.utc))],
    "STORAGE_USAGE": [{"JOURS": 365, "MOYEN": 1e9, "TABLES": 8e8, "STAGES": 1e8, "FAILSAFE": 1e8}],
    "WAREHOUSE_METERING_HISTORY": [{"WAREHOUSE_NAME": "CANOT_WH", "CREDITS": 9.0, "CALCUL": 8.5, "SERVICES": 0.5, "ATTRIBUES": 6.0}],
    "QUERY_ATTRIBUTION_HISTORY": [{"H": "h1", "N": 40, "CREDITS": 1.2, "QAS": 0, "FAMILLES_TOTAL": 3, "CREDITS_TOTAL": 1.5}],
    "TABLE_DML_HISTORY": [{"TABLE_ID": 2, "DERNIERE": D, "INTERVALLES": 12, "AJOUTEES": 100, "MODIFIEES": 50, "RETIREES": 0}],
    "TABLE_QUERY_PRUNING_HISTORY": [{"TABLE_ID": 1, "N": 50, "DERNIERE": D}, {"TABLE_ID": 2, "N": 20, "DERNIERE": D}],
    "AUTOMATIC_CLUSTERING_HISTORY": [{"TABLE_ID": 2, "CREDITS": 0.3}],
    "OBJECT_DEPENDENCIES": [
        {"REFERENCING_DATABASE": "CANOT_LAB", "REFERENCING_SCHEMA": "MART", "REFERENCING_OBJECT_NAME": "V_KPI", "REFERENCING_OBJECT_DOMAIN": "VIEW",
         "REFERENCED_DATABASE": "CANOT_LAB", "REFERENCED_SCHEMA": "STG", "REFERENCED_OBJECT_NAME": "V_STATES", "REFERENCED_OBJECT_DOMAIN": "VIEW"},
        {"REFERENCING_DATABASE": "CANOT_LAB", "REFERENCING_SCHEMA": "STG", "REFERENCING_OBJECT_NAME": "V_STATES", "REFERENCING_OBJECT_DOMAIN": "VIEW",
         "REFERENCED_DATABASE": "CANOT_LAB", "REFERENCED_SCHEMA": "EXT", "REFERENCED_OBJECT_NAME": "US_STATES", "REFERENCED_OBJECT_DOMAIN": "EXTERNAL TABLE"},
    ],
    "VIEWS": [
        {"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "STG", "TABLE_NAME": "V_STATES", "TABLE_OWNER": "LAB_ENGINEER", "IS_SECURE": "NO",
         "LAST_ALTERED": D, "VIEW_DEFINITION": f"create view v_states as select * from EXT.US_STATES union all select $1 from @EXT.S3_PUBLIC/x/ "
                                               f"where c <> '{CANARI}' and d <> ' @exemple.test'"},
        {"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "MART", "TABLE_NAME": "V_KPI", "TABLE_OWNER": "LAB_ENGINEER", "IS_SECURE": "YES",
         "LAST_ALTERED": D, "VIEW_DEFINITION": None},
    ],
    "FUNCTIONS": [
        {"FUNCTION_CATALOG": "CANOT_LAB", "FUNCTION_SCHEMA": "MART", "FUNCTION_NAME": "CLEAN_NAME", "FUNCTION_OWNER": "LAB_ENGINEER",
         "FUNCTION_LANGUAGE": "JAVASCRIPT", "IS_EXTERNAL": "NO", "API_INTEGRATION": None, "EXTERNAL_ACCESS_INTEGRATIONS": None,
         "FUNCTION_DEFINITION": "return S.toLowerCase();", "LAST_ALTERED": D},
        {"FUNCTION_CATALOG": "CANOT_LAB", "FUNCTION_SCHEMA": "MART", "FUNCTION_NAME": "EXT_FN", "FUNCTION_OWNER": "LAB_ENGINEER",
         "FUNCTION_LANGUAGE": "EXTERNAL", "IS_EXTERNAL": "YES", "API_INTEGRATION": "API_INT", "EXTERNAL_ACCESS_INTEGRATIONS": None,
         "FUNCTION_DEFINITION": "https://api.exemple.io/v1/canari-7f3a?key=s3cr3t", "LAST_ALTERED": D},
        {"FUNCTION_CATALOG": "CANOT_LAB", "FUNCTION_SCHEMA": "MART", "FUNCTION_NAME": "SLUG", "FUNCTION_OWNER": "LAB_ENGINEER",
         "FUNCTION_LANGUAGE": "PYTHON", "IS_EXTERNAL": "NO", "API_INTEGRATION": None, "EXTERNAL_ACCESS_INTEGRATIONS": None,
         "FUNCTION_DEFINITION": "def f(s): return s", "LAST_ALTERED": D},
        {"FUNCTION_CATALOG": "CANOT_LAB", "FUNCTION_SCHEMA": "MART", "FUNCTION_NAME": "SLUG", "FUNCTION_OWNER": "LAB_ENGINEER",
         "FUNCTION_LANGUAGE": "PYTHON", "IS_EXTERNAL": "NO", "API_INTEGRATION": None, "EXTERNAL_ACCESS_INTEGRATIONS": None,
         "FUNCTION_DEFINITION": "def f(s, n): return s", "LAST_ALTERED": D},
    ],
    "PROCEDURES": [
        {"PROCEDURE_CATALOG": "CANOT_LAB", "PROCEDURE_SCHEMA": "MART", "PROCEDURE_NAME": "SP_DYNAMIC_JS", "PROCEDURE_OWNER": "LAB_ENGINEER",
         "PROCEDURE_LANGUAGE": "JAVASCRIPT", "EXTERNAL_ACCESS_INTEGRATIONS": None, "LAST_ALTERED": D,
         "PROCEDURE_DEFINITION": "var r = snowflake.execute({sqlText: \"select * from t where d < \" + DAYS}); return 'ok';"},
        {"PROCEDURE_CATALOG": "CANOT_LAB", "PROCEDURE_SCHEMA": "MART", "PROCEDURE_NAME": "SP_DYNAMIC_PY", "PROCEDURE_OWNER": "LAB_ENGINEER",
         "PROCEDURE_LANGUAGE": "PYTHON", "EXTERNAL_ACCESS_INTEGRATIONS": None, "LAST_ALTERED": D,
         "PROCEDURE_DEFINITION": f"def run(session, t):\n    x = '{CANARI}'\n    return session.sql(f\"select * from {{t}}\").collect()"},
        {"PROCEDURE_CATALOG": "CANOT_LAB", "PROCEDURE_SCHEMA": "MART", "PROCEDURE_NAME": "SP_STATIC", "PROCEDURE_OWNER": "LAB_ENGINEER",
         "PROCEDURE_LANGUAGE": "SQL", "EXTERNAL_ACCESS_INTEGRATIONS": None, "LAST_ALTERED": D,
         "PROCEDURE_DEFINITION": "begin insert into OPS.EVENTS_LOG select current_timestamp(), 'ok'; return 'ok'; end;"},
        {"PROCEDURE_CATALOG": "CANOT_LAB", "PROCEDURE_SCHEMA": "MART", "PROCEDURE_NAME": "SP_HIDDEN", "PROCEDURE_OWNER": "LAB_ENGINEER",
         "PROCEDURE_LANGUAGE": "SQL", "EXTERNAL_ACCESS_INTEGRATIONS": None, "LAST_ALTERED": D, "PROCEDURE_DEFINITION": None},
    ],
    "TASKS": [
        {"TASK_DATABASE": "CANOT_LAB", "TASK_SCHEMA": "OPS", "TASK_NAME": "T_DAILY", "TASK_OWNER": "LAB_ENGINEER", "WAREHOUSE": "CANOT_WH",
         "SCHEDULE": "USING CRON 0 3 * * * UTC", "PREDECESSORS": "[]", "STATE": "started", "DEFINITION": "insert into RAW.ORDERS select 1",
         "CONDITION": None, "LAST_SUSPENDED_REASON": None},
        {"TASK_DATABASE": "CANOT_LAB", "TASK_SCHEMA": "OPS", "TASK_NAME": "T_REFRESH", "TASK_OWNER": "LAB_ENGINEER", "WAREHOUSE": "CANOT_WH",
         "SCHEDULE": None, "PREDECESSORS": '["CANOT_LAB.OPS.T_DAILY"]', "STATE": "started",
         "DEFINITION": "insert into OPS.EVENTS_LOG select current_timestamp(), 'refresh'", "CONDITION": None, "LAST_SUSPENDED_REASON": None},
        {"TASK_DATABASE": "CANOT_LAB", "TASK_SCHEMA": "OPS", "TASK_NAME": "T_SERVERLESS", "TASK_OWNER": "LAB_ENGINEER", "WAREHOUSE": None,
         "SCHEDULE": "60 MINUTE", "PREDECESSORS": "[]", "STATE": "suspended", "DEFINITION": "call MART.SP_DYNAMIC_JS()",
         "CONDITION": "SYSTEM$STREAM_HAS_DATA('OPS.ORDERS_STREAM')", "LAST_SUSPENDED_REASON": "USER_SUSPENDED"},
    ],
    "TASK_HISTORY": [{"DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "OPS", "NAME": "T_DAILY", "N": 10, "ECHECS": 2, "DERNIERE": D, "DERNIER_ECHEC": D}],
    "SERVERLESS_TASK_HISTORY": [{"DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "OPS", "TASK_NAME": "T_SERVERLESS", "CREDITS": 0.5}],
    "STREAMS": [{"STREAM_CATALOG": "CANOT_LAB", "STREAM_SCHEMA": "OPS", "STREAM_NAME": "ORDERS_STREAM", "OWNER": "LAB_ENGINEER",
                 "TABLE_NAME": "CANOT_LAB.RAW.ORDERS", "SOURCE_TYPE": "Table", "MODE": "DEFAULT", "STALE_AFTER": NOW}],
    "PIPES": [{"PIPE_CATALOG": "CANOT_LAB", "PIPE_SCHEMA": "OPS", "PIPE_NAME": "P_LOAD", "PIPE_OWNER": "LAB_ENGINEER",
               "IS_AUTOINGEST_ENABLED": "YES", "DEFINITION": "COPY INTO RAW.ORDERS FROM @OPS.STG_EXTERNE/in/"}],
    "COPY_HISTORY": [{"TABLE_ID": 2, "TABLE_CATALOG_NAME": "CANOT_LAB", "TABLE_SCHEMA_NAME": "RAW", "TABLE_NAME": "ORDERS",
                      "PIPE_CATALOG_NAME": "CANOT_LAB", "PIPE_SCHEMA_NAME": "OPS", "PIPE_NAME": "P_LOAD", "LOC1": "s3:", "LOC3": "canot-lab-bucket",
                      "FICHIERS": 30, "LIGNES": 3000, "ECHECS": 1, "DERNIER": D}],
    "STAGES": [
        {"STAGE_CATALOG": "CANOT_LAB", "STAGE_SCHEMA": "EXT", "STAGE_NAME": "S3_PUBLIC", "STAGE_OWNER": "LAB_ENGINEER", "STAGE_TYPE": "External Named",
         "STAGE_REGION": "us-east-1", "STORAGE_INTEGRATION": None, "URL1": "s3:", "URL3": "user:s3cr3t@pub-bucket"},
        {"STAGE_CATALOG": "CANOT_LAB", "STAGE_SCHEMA": "OPS", "STAGE_NAME": "STG_EXTERNE", "STAGE_OWNER": "LAB_ENGINEER", "STAGE_TYPE": "External Named",
         "STAGE_REGION": "eu-west-1", "STORAGE_INTEGRATION": "LAB_S3_INT", "URL1": "s3:", "URL3": "canot-lab-inexistant"},
        {"STAGE_CATALOG": "CANOT_LAB", "STAGE_SCHEMA": "OPS", "STAGE_NAME": "STG_INTERNE", "STAGE_OWNER": "LAB_ENGINEER", "STAGE_TYPE": "Internal Named",
         "STAGE_REGION": None, "STORAGE_INTEGRATION": None, "URL1": "", "URL3": ""},
    ],
    "DATABASES": [{"DATABASE_NAME": "CANOT_LAB", "DATABASE_OWNER": "LAB", "TYPE": "STANDARD"},
                  {"DATABASE_NAME": "SNOWFLAKE_SAMPLE_DATA", "DATABASE_OWNER": "ACCOUNTADMIN", "TYPE": "IMPORTED DATABASE"},
                  {"DATABASE_NAME": "SNOWFLAKE", "DATABASE_OWNER": None, "TYPE": "IMPORTED DATABASE"}],
    "DATA_TRANSFER_HISTORY": [{"TRANSFER_TYPE": "COPY", "TARGET_CLOUD": "AWS", "TARGET_REGION": "us-east-1", "OCTETS": 12345}],
    "DYNAMIC_TABLE_REFRESH_HISTORY": [{"DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "MART", "NAME": "DT_COUNTRY", "N": 5, "ECHECS": 1, "AMONT": 0,
                                       "DERNIERE": D, "LAG": 86400}],
    "QUERY_HISTORY": [
        {"H": "h1", "TXT": f"select * from RAW.CUSTOMERS where email = '{CANARI}'", "QUERY_TYPE": "SELECT", "N": 40, "MS": 9000, "OCTETS": 1 << 20,
         "DECHARGEES": 0, "ECHECS": 1, "UTILISATEURS": 2, "USER_NAME": CANARI, "ROLE_NAME": "ANALYST", "WAREHOUSE_NAME": "CANOT_WH",
         "QUERY_TAG": json.dumps({"app": "looker", "user": CANARI}), "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "RAW", "PREMIERE": D,
         "DERNIERE": NOW, "FAMILLES_TOTAL": 3, "EXEC_TOTAL": 50, "MS_TOTAL": 10000, "DU_TOTAL": D, "AU_TOTAL": NOW},
        {"H": "h2", "TXT": "copy into @OPS.STG_EXTERNE/out/ from RAW.ORDERS", "QUERY_TYPE": "UNLOAD", "N": 7, "MS": 800, "OCTETS": 10,
         "DECHARGEES": 70, "ECHECS": 7, "UTILISATEURS": 1, "USER_NAME": "SVC_DBT", "ROLE_NAME": "LAB_ENGINEER", "WAREHOUSE_NAME": "CANOT_WH",
         "QUERY_TAG": CANARI, "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "OPS", "PREMIERE": D, "DERNIERE": D,
         "FAMILLES_TOTAL": 3, "EXEC_TOTAL": 50, "MS_TOTAL": 10000, "DU_TOTAL": D, "AU_TOTAL": NOW},
    ],
    "ACCESS_HISTORY": AH_ROWS,
    "POLICY_REFERENCES": [
        {"POLICY_DB": "CANOT_LAB", "POLICY_SCHEMA": "OPS", "POLICY_NAME": "MP_EMAIL", "POLICY_KIND": "MASKING_POLICY", "REF_DATABASE_NAME": "CANOT_LAB",
         "REF_SCHEMA_NAME": "RAW", "REF_ENTITY_NAME": "CUSTOMERS", "REF_ENTITY_DOMAIN": "TABLE", "REF_COLUMN_NAME": "EMAIL_ADDR", "POLICY_STATUS": "ACTIVE"},
        {"POLICY_DB": "CANOT_LAB", "POLICY_SCHEMA": "OPS", "POLICY_NAME": "RAP_COUNTRY", "POLICY_KIND": "ROW_ACCESS_POLICY", "REF_DATABASE_NAME": "CANOT_LAB",
         "REF_SCHEMA_NAME": "RAW", "REF_ENTITY_NAME": "ORDERS", "REF_ENTITY_DOMAIN": "TABLE", "REF_COLUMN_NAME": None, "POLICY_STATUS": "ACTIVE"},
    ],
    "TAG_REFERENCES": [{"TAG_DATABASE": "CANOT_LAB", "TAG_SCHEMA": "OPS", "TAG_NAME": "PII", "TAG_VALUE": CANARI, "OBJECT_DATABASE": "CANOT_LAB",
                        "OBJECT_SCHEMA": "RAW", "OBJECT_NAME": "CUSTOMERS", "DOMAIN": "COLUMN", "COLUMN_NAME": "EMAIL_ADDR"}],
    "SHOW WAREHOUSES": [{"NAME": "CANOT_WH", "SIZE": "X-Small", "TYPE": "STANDARD", "AUTO_SUSPEND": 60, "MAX_CLUSTER_COUNT": 1, "STATE": "SUSPENDED"},
                        {"NAME": "OTHER_WH", "SIZE": "Large", "TYPE": "STANDARD", "AUTO_SUSPEND": 600, "MAX_CLUSTER_COUNT": 3, "STATE": "SUSPENDED"}],
    "SHOW DYNAMIC TABLES": [{"NAME": "DT_COUNTRY", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "MART", "OWNER": "LAB_ENGINEER", "TARGET_LAG": "1 day",
                             "REFRESH_MODE": "INCREMENTAL", "WAREHOUSE": "CANOT_WH", "SCHEDULING_STATE": "RUNNING",
                             "TEXT": f"create dynamic table x as select '{CANARI}'"}],
    "SHOW ICEBERG TABLES": [],
    "SHOW EXTERNAL TABLES": [{"NAME": "US_STATES", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "EXT",
                              "LOCATION": "s3://ext-bucket/states/canari-7f3a/", "STAGE": "@CANOT_LAB.EXT.S3_PUBLIC"}],
    "SHOW STREAMS": [{"NAME": "ORDERS_STREAM", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "OPS", "OWNER": "LAB_ENGINEER",
                      "TABLE_NAME": "CANOT_LAB.RAW.ORDERS", "SOURCE_TYPE": "Table", "MODE": "DEFAULT", "STALE_AFTER": NOW}],
    "GRANTS_TO_ROLES_TREE": [{"ROLE": "LAB_ENGINEER", "PARENT": "SYSADMIN"}, {"ROLE": "SYSADMIN", "PARENT": "ACCOUNTADMIN"}],
    "GRANTS_TO_ROLES": [{"GRANTED_TO": "ROLE", "GRANTED_ON": "TABLE", "PRIVILEGE": "SELECT", "N": 12, "BENEFICIAIRES": 3},
                        {"GRANTED_TO": "SHARE", "GRANTED_ON": "DATABASE", "PRIVILEGE": "USAGE", "N": 1, "BENEFICIAIRES": 1}],
    "GRANTS_TO_USERS": [{"ROLE": "SYSADMIN", "N": 1}, {"ROLE": "ACCOUNTADMIN", "N": 1}],
    "USERS": [{"NAME": CANARI, "TYPE": "PERSON"}, {"NAME": "SVC_DBT", "TYPE": "SERVICE"}, {"NAME": "OLD", "TYPE": None}],
    "SESSIONS": [{"OUTIL": "JDBC 3.8.7", "SESSIONS": 10, "UTILISATEURS": 2, "DERNIERE": D}],
    "QH_SESSIONS": [{"H": "h1", "OUTIL": "PythonConnector 3.12", "N": 5}, {"H": "h1", "OUTIL": "JDBC 3.8.7", "N": 2}],
    "SHARES": [{"NAME": "S_PARTNER", "OWNER": "ACCOUNTADMIN", "DATABASE_NAME": "CANOT_LAB", "TARGET_ACCOUNTS": "", "LISTING_GLOBAL_NAME": None},
               {"NAME": "S_OTHER_DB", "OWNER": "ACCOUNTADMIN", "DATABASE_NAME": "OTHER", "TARGET_ACCOUNTS": "ORG.ACC1", "LISTING_GLOBAL_NAME": None}],
}


def _rt_mirror() -> dict:
    """Vues temps réel du banc : les mêmes objets vivants qu'ACCOUNT_USAGE (cas « présent des deux côtés »)."""
    live = [r for r in DATA["TABLES"] if not r["DELETED"]]
    names = {r["TABLE_ID"]: (r["TABLE_SCHEMA"], r["TABLE_NAME"]) for r in live}
    return {
        "IS.TABLES": [{k: v for k, v in r.items() if k not in ("TABLE_ID", "DELETED", "IS_HYBRID")} | {"LAST_ALTERED": D} for r in live],
        "IS.COLUMNS": [{"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": names[c["TABLE_ID"]][0], "TABLE_NAME": names[c["TABLE_ID"]][1],
                        **{k: v for k, v in c.items() if k != "TABLE_ID"}} for c in DATA["COLUMNS"]],
        "IS.VIEWS": [dict(r) for r in DATA["VIEWS"]], "IS.FUNCTIONS": [dict(r) for r in DATA["FUNCTIONS"]],
        "IS.PROCEDURES": [dict(r) for r in DATA["PROCEDURES"]], "IS.STAGES": [dict(r) for r in DATA["STAGES"]],
        "IS.PIPES": [dict(r) for r in DATA["PIPES"]],
        "SHOW TASKS": [{"NAME": r["TASK_NAME"], "DATABASE_NAME": r["TASK_DATABASE"], "SCHEMA_NAME": r["TASK_SCHEMA"], "OWNER": r["TASK_OWNER"],
                        "WAREHOUSE": r["WAREHOUSE"] or "", "SCHEDULE": r["SCHEDULE"] or "", "PREDECESSORS": r["PREDECESSORS"], "STATE": r["STATE"],
                        "DEFINITION": r["DEFINITION"], "CONDITION": r["CONDITION"] or "", "LAST_SUSPENDED_REASON": r["LAST_SUSPENDED_REASON"] or ""}
                       for r in DATA["TASKS"]],
    }


DATA.update(_rt_mirror())
RT_KEYS = ["IS.TABLES", "IS.COLUMNS", "IS.VIEWS", "IS.FUNCTIONS", "IS.PROCEDURES", "IS.STAGES", "IS.PIPES", "SHOW TASKS"]
NEW = datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc)  # 30 min avant le scan : invisible dans ACCOUNT_USAGE

DENIED = "SQL access control error: Insufficient privileges to operate on schema 'ACCOUNT_USAGE'"


class FakeSnowflake:
    """Répond selon la vue lue (premier FROM) ou la commande SHOW ; enregistre chaque SQL reçu."""

    def __init__(self, fail=None, ah_fail_at=None, data=None):
        self.sql, self.fail, self.ah_fail_at, self.ah_calls = [], fail or {}, ah_fail_at, 0
        self.data = {**DATA, **(data or {})}

    @staticmethod
    def key(sql: str) -> str:
        if sql.upper().startswith("ALTER SESSION"):
            return "ALTER"
        if sql.startswith("SHOW"):
            return re.match(r"SHOW ([A-Z ]+?)(?: IN |$)", sql).group(0).replace(" IN ", "").strip()
        if "SESSIONS s ON" in sql:
            return "QH_SESSIONS"
        if "GRANTED_ON IN ('ROLE'" in sql:
            return "GRANTS_TO_ROLES_TREE"
        m = re.search(r'FROM "[^"]+"\.INFORMATION_SCHEMA\.(\w+)', sql)
        if m:
            return "IS." + m.group(1)
        return re.search(r"FROM SNOWFLAKE\.ACCOUNT_USAGE\.(\w+)", sql).group(1)

    def __call__(self, sql: str):
        self.sql.append(sql)
        k = self.key(sql)
        if k in self.fail:
            raise RuntimeError(self.fail[k])
        if k == "ACCESS_HISTORY":
            self.ah_calls += 1
            if self.ah_fail_at == self.ah_calls:
                raise RuntimeError("Statement reached its statement or warehouse timeout of 600 second(s) and was canceled.")
            return [dict(r) for r in self.data[k]] if self.ah_calls == 1 else []
        return [dict(r) for r in self.data.get(k, [])]


def scan(level=3, days=14, base="CANOT_LAB", schemas=None, **kw):
    fake = FakeSnowflake(**kw)
    snap, raw = sf.collect(fake, base=base, schemas=schemas, level=level, salt=SALT, days=days, now=NOW)
    return fake, snap, raw


def lecture(snap, vue):
    hits = [x for x in snap["lectures"] if x["vue"] == vue]
    assert len(hits) == 1, (vue, [x["vue"] for x in snap["lectures"]])
    return hits[0]


def by_id(items, oid):
    return next(x for x in items if x["id"] == oid)


AU = "SNOWFLAKE.ACCOUNT_USAGE."
TABLE_KEYS = {"id", "base", "schema", "nom", "type", "flags", "lignes", "octets_actifs", "octets_historique", "clone_de", "colonnes",
              "retention_jours", "derniere_ddl", "derniere_ecriture", "derniere_lecture", "n_lectures", "n_ecritures", "proprietaire",
              "politiques", "etiquettes", "supprimee_le", "externe_uri", "partitionnement", "clustering"}
OBJET_KEYS = {"kind", "id", "schema", "langage", "proprietaire", "externe", "dependances_externes", "reference", "etat", "planification",
              "executions_n", "echecs_n", "derniere_execution", "sql_dynamique", "detail"}
KINDS = {"vue", "vue_materialisee", "fonction", "procedure", "tache", "flux", "pipe", "stage", "base_importee", "partage", "table_dynamique",
         "modele_ml", "politique_masquage", "politique_lignes", "requete_planifiee", "connexion", "integration", "reservation", "etiquette"}


# --- contrat et lecture seule -------------------------------------------------------------------------------

def test_snapshot_follows_contract():
    _, snap, _ = scan()
    json.dumps(snap)
    for k in ("schema", "plateforme", "as_of", "perimetre", "edition", "niveau_droits", "mode_sql", "versions", "lectures", "tables",
              "objets", "familles", "metering", "rejouabilite"):
        assert k in snap
    assert snap["schema"] == "canot.scan/1" and snap["plateforme"] == "snowflake" and snap["mode_sql"] == "lecture seule"
    assert snap["as_of"] == "2026-10-17T10:00:00Z" and snap["niveau_droits"] == 3 and snap["familles"] == []
    ids = [x["id"] for x in snap["lectures"]]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"S\d{2}", i) for i in ids)
    for x in snap["lectures"]:
        assert set(x) == {"id", "vue", "statut", "raison", "lignes", "fenetre", "latence_max_h"}
        assert x["statut"] in ("lu", "non_lu") and (x["statut"] == "lu" or x["raison"])
    for t in snap["tables"]:
        assert TABLE_KEYS <= set(t) and t["type"] in ("TABLE", "EXTERNE", "ICEBERG", "TRANSITOIRE", "CLONE", "SNAPSHOT", "TEMPORAIRE")
    for o in snap["objets"]:
        assert set(o) == OBJET_KEYS and o["kind"] in KINDS
        assert all(v is None or isinstance(v, (str, int, float, bool)) for v in o["detail"].values())
    assert len({t["id"] for t in snap["tables"]}) == len(snap["tables"])
    m = snap["metering"]
    assert {"par_service", "par_entrepot", "stockage_octets_moyens", "transferts", "a_la_demande", "reserve", "fenetre_jours"} <= set(m)


def test_every_sql_is_readonly_and_never_selects_forbidden_columns():
    fake, _, _ = scan()
    assert len(fake.sql) > 30
    for sql in fake.sql:
        assert check_readonly(sql, "snowflake") == sql
        up = sql.upper()
        for word in FORBIDDEN_SQL:
            assert word not in up, (word, sql[:80])
        assert CANARI not in sql
    texts = [s for s in fake.sql if "QUERY_TEXT" in s]
    assert len(texts) == 1 and "ANY_VALUE(QUERY_TEXT)" in texts[0]
    assert all("STAGE_URL" not in s or "SPLIT_PART(STAGE_URL" in s for s in fake.sql)
    assert all("STAGE_LOCATION" not in s or "SPLIT_PART(STAGE_LOCATION" in s for s in fake.sql)


def test_session_switched_to_utc_before_metering():
    fake, _, _ = scan()
    alter = fake.sql.index("ALTER SESSION SET TIMEZONE = 'UTC'")
    metering = next(i for i, s in enumerate(fake.sql) if "METERING_DAILY_HISTORY" in s)
    assert alter < metering == alter + 1


def test_tables_reads_last_ddl_not_last_altered():
    fake, snap, _ = scan()
    sql = next(s for s in fake.sql if "FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES " in s)
    assert "LAST_DDL" in sql and "LAST_ALTERED" not in sql
    assert by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")["derniere_ddl"] == "2026-10-10T08:00:00Z"


def test_level_1_sends_no_level_2_or_3_read():
    fake, snap, raw = scan(level=1)
    for frag in (AU + "QUERY_HISTORY", "ACCESS_HISTORY", "POLICY_REFERENCES", "TAG_REFERENCES", "GRANTS_TO_", AU + "USERS",
                 AU + "SESSIONS", AU + "SHARES", "SHOW "):
        assert not any(frag in s for s in fake.sql), frag
    for vue in (AU + "QUERY_HISTORY", AU + "ACCESS_HISTORY", AU + "POLICY_REFERENCES", AU + "TAG_REFERENCES", "SHOW WAREHOUSES",
                "SHOW DYNAMIC TABLES", "SHOW ICEBERG TABLES", AU + "GRANTS_TO_USERS", AU + "USERS", AU + "SESSIONS", AU + "SHARES"):
        x = lecture(snap, vue)
        assert x["statut"] == "non_lu" and "non demandé" in x["raison"]
    assert raw == [] and snap["requetes"] is None and snap["droits"] is None
    assert all(t["n_lectures"] is None for t in snap["tables"])
    assert snap["edition"].startswith("non déterminée")
    assert lecture(snap, AU + "TABLE_QUERY_PRUNING_HISTORY")["statut"] == "lu"


def test_level_2_skips_level_3():
    fake, snap, raw = scan(level=2)
    assert not any("GRANTS_TO_" in s or AU + "SESSIONS" in s for s in fake.sql)
    assert lecture(snap, AU + "USERS")["raison"] == "non demandé (niveau 2)"
    assert snap["droits"] is None and raw and all(f["outil"] is None and f["utilisateur_type"] is None for f in raw)


# --- échecs : non lu, jamais 0, le reste continue ---------------------------------------------------------

def test_denied_read_becomes_non_lu_and_values_stay_none():
    _, snap, _ = scan(fail={"TABLE_STORAGE_METRICS": DENIED})
    x = lecture(snap, AU + "TABLE_STORAGE_METRICS")
    assert x["statut"] == "non_lu" and x["raison"] == "droit absent" and x["lignes"] is None
    assert all(t["octets_actifs"] is None and t["octets_historique"] is None for t in snap["tables"])
    assert by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")["lignes"] == 100
    assert sum(1 for x in snap["lectures"] if x["statut"] == "non_lu") == 1


def test_metering_refused_gives_none_not_empty():
    _, snap, _ = scan(fail={"METERING_DAILY_HISTORY": DENIED, "DATA_TRANSFER_HISTORY": DENIED, "WAREHOUSE_METERING_HISTORY": DENIED},
                      data={"STORAGE_USAGE": [{"JOURS": 0, "MOYEN": None, "TABLES": None, "STAGES": None, "FAILSAFE": None}]})
    m = snap["metering"]
    assert m["par_service"] is None and m["transferts"] is None and m["stockage_octets_moyens"] is None
    assert {e["entrepot"]: e["credits"] for e in m["par_entrepot"]} == {"CANOT_WH": None, "OTHER_WH": None}


def test_everything_denied_still_returns_a_snapshot():
    keys = ["METERING_DAILY_HISTORY", "TABLES", "COLUMNS", "TABLE_STORAGE_METRICS", "STORAGE_USAGE", "WAREHOUSE_METERING_HISTORY",
            "QUERY_ATTRIBUTION_HISTORY", "TABLE_DML_HISTORY", "TABLE_QUERY_PRUNING_HISTORY", "AUTOMATIC_CLUSTERING_HISTORY",
            "OBJECT_DEPENDENCIES", "VIEWS", "FUNCTIONS", "PROCEDURES", "TASKS", "TASK_HISTORY", "SERVERLESS_TASK_HISTORY", "STREAMS",
            "PIPES", "COPY_HISTORY", "STAGES", "DATABASES", "DATA_TRANSFER_HISTORY", "DYNAMIC_TABLE_REFRESH_HISTORY", "QUERY_HISTORY",
            "ACCESS_HISTORY", "POLICY_REFERENCES", "TAG_REFERENCES", "SHOW WAREHOUSES", "SHOW DYNAMIC TABLES", "SHOW ICEBERG TABLES",
            "SHOW EXTERNAL TABLES", "SHOW STREAMS", "GRANTS_TO_ROLES_TREE", "GRANTS_TO_ROLES", "GRANTS_TO_USERS", "USERS", "SESSIONS", "SHARES",
            *RT_KEYS]
    _, snap, raw = scan(fail={k: "Object does not exist or not authorized." for k in keys})
    json.dumps(snap)
    assert all(x["statut"] == "non_lu" and x["raison"] for x in snap["lectures"])
    assert snap["tables"] == [] and snap["objets"] == [] and raw == []
    assert snap["requetes"] is None and snap["metering"]["par_service"] is None
    assert snap["droits"] == {"roles": None, "privileges_perimetre": None, "utilisateurs": None, "outils": None}


def test_streams_view_refused_falls_back_to_show_streams():
    fake, snap, _ = scan(fail={"STREAMS": "Object 'SNOWFLAKE.ACCOUNT_USAGE.STREAMS' does not exist or not authorized."})
    assert lecture(snap, AU + "STREAMS")["raison"] == "droit absent"
    assert lecture(snap, "SHOW STREAMS")["statut"] == "lu"
    o = by_id(snap["objets"], "CANOT_LAB.OPS.ORDERS_STREAM")
    assert o["reference"] == ["CANOT_LAB.RAW.ORDERS"] and o["detail"]["inventaire"] == "show" and "recent" not in o["detail"]  # repli
    fake, snap, _ = scan()  # vue lue : SHOW STREAMS sert de recoupement temps réel, sans doublon
    assert sum(s.startswith("SHOW STREAMS") for s in fake.sql) == 1
    assert [x["id"] for x in snap["objets"] if x["kind"] == "flux"] == ["CANOT_LAB.OPS.ORDERS_STREAM"]
    assert "inventaire" not in by_id(snap["objets"], "CANOT_LAB.OPS.ORDERS_STREAM")["detail"]


def test_code_views_marked_as_filtered_by_rights():
    _, snap, _ = scan()
    for v in ("VIEWS", "FUNCTIONS", "PROCEDURES"):
        x = lecture(snap, AU + v)
        assert x["statut"] == "lu" and "filtrées par les droits" in x["raison"]


# --- ACCESS_HISTORY : édition, fenêtres d'une semaine, « jamais lu » honnête ----------------------------------

def test_access_history_read_gives_measured_reads_and_never_read():
    fake, snap, _ = scan()
    assert snap["edition"] == "Enterprise ou supérieure"
    ah = [s for s in fake.sql if "ACCESS_HISTORY" in s]
    assert len(ah) == 2 and all("STARTSWITH" in s and "QUERY_TAG = 'canot'" in s for s in ah)
    cust, arch, orders = (by_id(snap["tables"], f"CANOT_LAB.RAW.{n}") for n in ("CUSTOMERS", "ARCHIVE_1", "ORDERS"))
    assert (cust["n_lectures"], cust["derniere_lecture"], cust["detail"]["jamais_lu"]) == (5, "2026-10-10T08:00:00Z", False)
    assert (arch["n_lectures"], arch["derniere_lecture"], arch["detail"]["jamais_lu"]) == (0, None, True)
    assert (orders["n_ecritures"], orders["derniere_ecriture"]) == (7, "2026-10-17T10:00:00Z")
    assert by_id(snap["objets"], "CANOT_LAB.OPS.STG_EXTERNE")["detail"]["ecritures_observees_n"] == 2
    x = lecture(snap, AU + "ACCESS_HISTORY")
    assert x["fenetre"] == {"du": "2026-10-03T10:00:00Z", "au": "2026-10-17T10:00:00Z"} and x["latence_max_h"] == 3


def test_access_history_uses_one_week_windows_over_365_days():
    fake, snap, _ = scan(days=365)
    assert sum("ACCESS_HISTORY" in s for s in fake.sql) == 53
    assert lecture(snap, AU + "ACCESS_HISTORY")["fenetre"]["du"] == "2025-10-17T10:00:00Z"


def test_access_history_refused_edition_undetermined_and_pruning_fallback():
    _, snap, _ = scan(fail={"ACCESS_HISTORY": DENIED})
    assert snap["edition"] == "non déterminée (Standard ou droit absent)"
    for t in snap["tables"]:
        assert t["n_lectures"] is None and t["n_ecritures"] is None and t["detail"]["jamais_lu"] is None
    assert by_id(snap["tables"], "CANOT_LAB.RAW.ARCHIVE_1")["detail"]["jamais_lu_pruning"] is True
    assert by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")["detail"]["requetes_pruning_n"] == 50


def test_access_history_partial_read_reports_real_window():
    _, snap, _ = scan(ah_fail_at=2)
    x = lecture(snap, AU + "ACCESS_HISTORY")
    assert x["statut"] == "lu" and x["raison"].startswith("partielle : 1/2 semaines") and "délai dépassé" in x["raison"]
    assert x["fenetre"]["du"] == "2026-10-10T10:00:00Z"


def test_empty_access_history_contradicted_by_pruning_is_not_trusted():
    _, snap, _ = scan(data={"ACCESS_HISTORY": []})
    assert snap["edition"].startswith("non déterminée (ACCESS_HISTORY vide")
    assert all(t["n_lectures"] is None for t in snap["tables"])
    _, snap, _ = scan(data={"ACCESS_HISTORY": [], "TABLE_QUERY_PRUNING_HISTORY": []})
    assert snap["edition"] == "non déterminée (ACCESS_HISTORY lisible, sans ligne)"
    assert by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")["n_lectures"] == 0


def test_pruning_excludes_canot_own_reads():
    fake, _, _ = scan(level=1)
    sql = next(s for s in fake.sql if "FROM SNOWFLAKE.ACCOUNT_USAGE.TABLE_QUERY_PRUNING_HISTORY" in s)
    assert "NUM_QUERIES" in sql and "INTERVAL_START_TIME" in sql and "QUERY_TAG = 'canot'" in sql


# --- tables : volumes, historique, clones, suppressions, propriétaires ---------------------------------------

def test_tables_storage_clone_dropped_recreated():
    _, snap, _ = scan()
    t = {x["id"]: x for x in snap["tables"]}
    assert "CANOT_LAB.MART.MV_DAILY" not in t and "CANOT_LAB.STG.V_STATES" not in t
    orders = t["CANOT_LAB.RAW.ORDERS"]
    assert (orders["octets_actifs"], orders["octets_historique"], orders["retention_jours"]) == (1000, 5020, 30)
    assert orders["clustering"] == ["LINEAR(TO_DATE(ORDER_TS))"] and "clustering_auto" in orders["flags"]
    assert orders["detail"]["credits_clustering"] == 0.3 and orders["detail"]["lignes_ajoutees"] == 100
    clone = t["CANOT_LAB.RAW.ORDERS_CLONE"]
    assert clone["type"] == "CLONE" and clone["clone_de"] == "CANOT_LAB.RAW.ORDERS"
    dropped = t["CANOT_LAB.RAW.TMP_DROPPED"]
    assert dropped["supprimee_le"] == "2026-10-10T08:00:00Z" and dropped["octets_historique"] == 1000 and "supprimee" in dropped["flags"]
    gone = t["CANOT_LAB.RAW.OLD_GONE"]
    assert gone["supprimee_le"] == "2024-01-01T00:00:00Z" and gone["detail"]["octets_retenus_clone"] == 500 and gone["detail"]["type_non_lu"]
    assert "recreee" in t["CANOT_LAB.RAW.EVENTS"]["flags"] and "CANOT_LAB.RAW.EVENTS#80" in t
    assert t["CANOT_LAB.EXT.US_STATES"]["type"] == "EXTERNE" and t["CANOT_LAB.EXT.US_STATES"]["lignes"] is None
    assert t["CANOT_LAB.SANDBOX.OLD_EXPORT_2023"]["proprietaire"] == "LAB_ORPHAN"
    assert t["CANOT_LAB.EXT.US_STATES"]["octets_actifs"] is None  # absente des métriques : non lu, pas 0


def test_columns_types_policies_tags():
    _, snap, _ = scan()
    cust = by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")
    assert [c["type"] for c in cust["colonnes"]] == ["NUMBER(38,0)", "TEXT", "VARIANT", "GEOGRAPHY"]
    assert cust["politiques"] == ["MASKING_POLICY:CANOT_LAB.OPS.MP_EMAIL"]
    assert len(cust["etiquettes"]) == 1 and re.fullmatch(r"CANOT_LAB\.OPS\.PII=v-[0-9a-f]{8} \(EMAIL_ADDR\)", cust["etiquettes"][0])
    assert by_id(snap["objets"], "CANOT_LAB.OPS.RAP_COUNTRY")["kind"] == "politique_lignes"
    tag = by_id(snap["objets"], "CANOT_LAB.OPS.PII")
    assert tag["detail"] == {"references_n": 1, "valeurs_distinctes_n": 1}


def test_owner_roles_and_holders_for_f04():
    _, snap, _ = scan()
    roles = {r["nom"]: r for r in snap["droits"]["roles"]}
    assert roles["LAB_ORPHAN"]["titulaire"] is False and roles["LAB_ORPHAN"]["proprietaire_d_objets"]
    assert roles["LAB_ENGINEER"]["titulaire"] is True and roles["LAB_ENGINEER"]["parents"] == ["SYSADMIN"]
    assert snap["droits"]["utilisateurs"] == {"n": 3, "par_type": {"PERSON": 1, "SERVICE": 1, "non renseigné": 1}}
    _, snap, _ = scan(fail={"GRANTS_TO_ROLES_TREE": DENIED})
    assert all(r["titulaire"] is None for r in snap["droits"]["roles"])


# --- objets ------------------------------------------------------------------------------------------------

def test_views_functions_procedures():
    _, snap, _ = scan()
    o = {x["id"]: x for x in snap["objets"]}
    v = o["CANOT_LAB.STG.V_STATES"]
    assert v["reference"] == ["CANOT_LAB.EXT.US_STATES"] and v["dependances_externes"] == ["s3://pub-bucket"] and v["externe"]
    assert v["detail"]["stages"] == "CANOT_LAB.EXT.S3_PUBLIC"
    assert o["CANOT_LAB.MART.V_KPI"]["reference"] == ["CANOT_LAB.STG.V_STATES"] and o["CANOT_LAB.MART.V_KPI"]["detail"]["securisee"]
    assert o["CANOT_LAB.MART.MV_DAILY"]["kind"] == "vue_materialisee"
    ext = o["CANOT_LAB.MART.EXT_FN"]
    assert ext["externe"] and ext["dependances_externes"] == ["https://api.exemple.io"] and ext["detail"]["integration_api"] == "API_INT"
    assert o["CANOT_LAB.MART.SLUG"]["detail"]["surcharges_n"] == 2
    assert [o[f"CANOT_LAB.MART.{n}"]["sql_dynamique"] for n in ("SP_DYNAMIC_JS", "SP_DYNAMIC_PY", "SP_STATIC", "SP_HIDDEN")] == [True, True, False, None]
    assert o["CANOT_LAB.MART.SP_HIDDEN"]["detail"]["definition_visible"] is False
    assert all(x["proprietaire"] == "LAB_ENGINEER" for x in o.values() if x["kind"] in ("vue", "fonction", "procedure", "tache", "stage"))


def test_tasks_pipes_streams_dynamic_tables():
    _, snap, _ = scan()
    o = {x["id"]: x for x in snap["objets"]}
    daily, refresh, sless = o["CANOT_LAB.OPS.T_DAILY"], o["CANOT_LAB.OPS.T_REFRESH"], o["CANOT_LAB.OPS.T_SERVERLESS"]
    assert (daily["executions_n"], daily["echecs_n"], daily["reference"]) == (10, 2, ["CANOT_LAB.RAW.ORDERS"])
    assert (refresh["executions_n"], refresh["echecs_n"]) == (0, 0)
    assert refresh["planification"] == "après CANOT_LAB.OPS.T_DAILY" and refresh["reference"] == ["CANOT_LAB.OPS.EVENTS_LOG"]
    assert sless["detail"]["serverless"] and sless["detail"]["credits_serverless"] == 0.5
    assert sless["detail"]["appelle"] == "CANOT_LAB.MART.SP_DYNAMIC_JS" and sless["detail"]["condition"] is True
    pipe = o["CANOT_LAB.OPS.P_LOAD"]
    assert pipe["reference"] == ["CANOT_LAB.RAW.ORDERS"] and (pipe["executions_n"], pipe["echecs_n"]) == (30, 1)
    assert pipe["dependances_externes"] == ["s3://canot-lab-bucket", "s3://canot-lab-inexistant"]
    dt = o["CANOT_LAB.MART.DT_COUNTRY"]
    assert dt["kind"] == "table_dynamique" and dt["planification"] == "1 day" and (dt["executions_n"], dt["echecs_n"]) == (5, 1)
    assert o["CANOT_LAB.OPS.ORDERS_STREAM"]["kind"] == "flux"


def test_stages_imported_databases_shares_external_tables():
    _, snap, _ = scan()
    o = {x["id"]: x for x in snap["objets"]}
    assert o["CANOT_LAB.EXT.S3_PUBLIC"]["dependances_externes"] == ["s3://pub-bucket"] and o["CANOT_LAB.EXT.S3_PUBLIC"]["externe"]
    assert o["CANOT_LAB.OPS.STG_INTERNE"]["externe"] is False and o["CANOT_LAB.OPS.STG_INTERNE"]["dependances_externes"] == []
    assert [x["id"] for x in snap["objets"] if x["kind"] == "base_importee"] == ["SNOWFLAKE_SAMPLE_DATA"]
    assert [x["id"] for x in snap["objets"] if x["kind"] == "partage"] == ["S_PARTNER"]
    assert o["S_PARTNER"]["externe"] is False and o["S_PARTNER"]["detail"]["comptes_cibles_n"] == 0
    us = by_id(snap["tables"], "CANOT_LAB.EXT.US_STATES")
    assert us["externe_uri"] == "s3://ext-bucket" and us["detail"]["stage"] == "CANOT_LAB.EXT.S3_PUBLIC"


def test_metering_and_warehouses():
    _, snap, _ = scan()
    m = snap["metering"]
    assert m["par_service"][0] == {"service": "WAREHOUSE_METERING", "credits": 10.0, "credits_utilises": 10.5}
    wh = {e["entrepot"]: e for e in m["par_entrepot"]}
    assert wh["CANOT_WH"]["credits"] == 9.0 and wh["CANOT_WH"]["taille"] == "X-Small"
    assert wh["OTHER_WH"]["credits"] == 0.0 and wh["OTHER_WH"]["clusters_max"] == 3
    assert m["stockage_octets_moyens"] == 1e9 and m["fenetre_jours"] == 14
    assert m["transferts"] == [{"cible": "AWS.us-east-1", "type": "COPY", "octets": 12345}]
    assert m["par_famille"] == [{"hash_plateforme": "h1", "credits": 1.2, "n_exec": 40}]


# --- familles brutes : texte brut seulement dans le second retour, identités pseudonymisées ---------------------

def test_raw_families_and_server_side_aggregation():
    fake, snap, raw = scan()
    sql = next(s for s in fake.sql if "ANY_VALUE(QUERY_TEXT)" in s)
    for frag in ("GROUP BY QUERY_PARAMETERIZED_HASH", "LIMIT 500", "COALESCE(QUERY_TAG, '') <> 'canot'", "QUERY_TYPE NOT LIKE '%USER%'",
                 "QUERY_TYPE NOT LIKE '%STAGE%'", "QUERY_TYPE NOT LIKE '%SECRET%'", "COUNT(*)", "SUM(TOTAL_ELAPSED_TIME)"):
        assert frag in sql
    assert [f["hash_plateforme"] for f in raw] == ["h1", "h2"]
    h1, h2 = raw
    assert CANARI in h1["_sql"] and h1["n_exec"] == 40 and h1["temps_ms"] == 9000
    assert h1["credits"] == 1.2 and h2["credits"] is None
    assert re.fullmatch(r"u-[0-9a-f]{8}", h1["utilisateur"]) and re.fullmatch(r"r-[0-9a-f]{8}", h1["role"])
    assert (h1["app"], h2["app"]) == ("looker", None)
    assert h1["outil"] == "PythonConnector 3.12" and h1["utilisateur_type"] == "PERSON" and h2["utilisateur_type"] == "SERVICE"
    assert h2["lignes_dechargees"] == 70 and h2["query_type"] == "UNLOAD"
    assert snap["requetes"]["familles_total"] == 3 and snap["requetes"]["executions_total"] == 50 and snap["requetes"]["familles_lues"] == 2
    tools_sql = next(s for s in fake.sql if "SESSIONS s ON" in s)
    assert "IN ('h1', 'h2')" in tools_sql


def test_canary_absent_from_every_output():
    _, snap, raw = scan()
    out = json.dumps(snap, ensure_ascii=False, default=str)
    for leak in (CANARI, "canari-7f3a", "exemple.test", "s3cr3t", "user:", "states/", "/in/", "/out/", "select ", "SELECT ", "copy into"):
        assert leak not in out, leak
    assert "\"_sql\"" not in out
    for f in raw:
        rest = json.dumps({k: v for k, v in f.items() if k != "_sql"}, default=str)
        assert "canari-7f3a" not in rest and "exemple.test" not in rest


def test_no_forbidden_words_in_snapshot():
    for kw in ({}, {"level": 1}, {"fail": {"ACCESS_HISTORY": DENIED}}, {"ah_fail_at": 2}):
        _, snap, _ = scan(**kw)
        out = json.dumps(snap, ensure_ascii=False).lower()
        for w in FORBIDDEN_WORDS:
            assert w not in out, w


# --- périmètre ---------------------------------------------------------------------------------------------

def test_scope_filters_and_escaping():
    fake, snap, _ = scan(base="canot_lab", schemas=["raw", "mart"])
    tables_sql = next(s for s in fake.sql if "FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES " in s)
    assert "TABLE_CATALOG = 'CANOT_LAB'" in tables_sql and "TABLE_SCHEMA IN ('RAW', 'MART')" in tables_sql
    assert 'SHOW DYNAMIC TABLES IN DATABASE "CANOT_LAB"' in fake.sql
    assert "schémas RAW, MART" in snap["perimetre"]
    fake, _, _ = scan(base="x' OR 1=1 --")
    for s in fake.sql:
        check_readonly(s, "snowflake")
    assert any("= 'x'' OR 1=1 --'" in s for s in fake.sql)


def test_whole_account_scope():
    fake, snap, _ = scan(base="*")
    assert not any("TABLE_CATALOG =" in s for s in fake.sql)
    assert "SHOW DYNAMIC TABLES IN ACCOUNT" in fake.sql and snap["perimetre"].startswith("tout le compte")


# --- analyses locales ------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang, body, expected", [
    ("SQL", "begin execute immediate :q; end;", True),
    ("SQL", "begin let r resultset := (select * from identifier(:t)); end;", True),
    ("SQL", "begin insert into t select 1; end;", False),
    ("JAVASCRIPT", "snowflake.execute({sqlText: 'select ' + col + ' from t'})", True),
    ("JAVASCRIPT", "snowflake.execute({sqlText: `select ${col} from t`})", True),
    ("JAVASCRIPT", "snowflake.execute({sqlText: 'select 1'})", False),
    ("PYTHON", "session.sql(f'select * from {t}')", True),
    ("PYTHON", "session.sql('select * from {}'.format(t))", True),
    ("PYTHON", "session.sql('select 1').collect()", False),
    ("PYTHON", "return x + 'a'", False),
    ("SQL", None, None),
    ("COBOL", "anything", None),
])
def test_dynamic_sql_detection(lang, body, expected):
    assert sf._dynamic(lang, body) is expected


def test_query_tag_app_extraction():
    assert sf._app('{"app":"looker"}') == "looker"
    assert sf._app("airflow") == "airflow"
    assert sf._app(CANARI) is None
    assert sf._app('{"user":"x@y.z"}') is None
    assert sf._app("a" * 41) is None and sf._app(None) is None


def test_location_reduced_to_bucket():
    assert sf._location("s3:", "user:pw@bucket") == ("s3://bucket", None)
    assert sf._location('["s3:', "bucket") == ("s3://bucket", None)
    assert sf._location("@OPS.STG", None, "DB", "S") == (None, "DB.OPS.STG")
    assert sf._location("@~", None, "DB", "S") == (None, None)
    assert sf._location("stages", "abc") == (None, None)
    assert sf._code_refs("SQL", f"select '{CANARI}', $1 from @stg/p", "DB", "S") == (["DB.S.STG"], [])


# --- source : run_readonly et script d'accès --------------------------------------------------------------

class _Cursor:
    def __init__(self, log): self.log, self.description = log, None
    def execute(self, sql):
        self.log.append(sql)
        self.description = [("name",), ("Size",)]
    def fetchall(self): return [("CANOT_WH", "X-Small")]
    def close(self): pass


class _Con:
    def __init__(self): self.log = []
    def cursor(self): return _Cursor(self.log)


def test_run_readonly_uppercases_keys_and_refuses_writes():
    from canot.sources.snowflake_src import SnowflakeSource

    src = object.__new__(SnowflakeSource)
    src.con = _Con()
    assert src.run_readonly("SHOW WAREHOUSES") == [{"NAME": "CANOT_WH", "SIZE": "X-Small"}]
    for bad in ("DROP TABLE t", "ALTER WAREHOUSE w SET WAREHOUSE_SIZE = 'LARGE'", "SELECT 1; DELETE FROM t", "GRANT ROLE r TO USER u"):
        with pytest.raises(NotReadOnly):
            src.run_readonly(bad)
    assert src.con.log == ["SHOW WAREHOUSES"]


def test_setup_sql_uses_database_roles_never_imported_privileges():
    from canot.sources.snowflake_src import SETUP_SQL

    grants = [line for line in SETUP_SQL.splitlines() if line.startswith("GRANT")]
    assert not any("IMPORTED PRIVILEGES" in g or "MANAGE GRANTS" in g or "IMPORT SHARE" in g for g in grants)
    for role in ("OBJECT_VIEWER", "USAGE_VIEWER", "GOVERNANCE_VIEWER", "SECURITY_VIEWER"):
        assert f"GRANT DATABASE ROLE SNOWFLAKE.{role} TO ROLE CANOT_READER;" in SETUP_SQL
    assert "TYPE = SERVICE" in SETUP_SQL and "<clé publique, sans les lignes BEGIN/END>" in SETUP_SQL


# --- recoupement en temps réel (INFORMATION_SCHEMA, SHOW) : objets plus récents que la latence d'ACCOUNT_USAGE ------------------

def _recent_data(**extra):
    """Banc + objets créés 30 min avant le scan : visibles en temps réel, absents d'ACCOUNT_USAGE."""
    new_t = {"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "LAB", "TABLE_NAME": "NEW_T", "TABLE_OWNER": "LAB_ENGINEER", "TABLE_TYPE": "BASE TABLE",
             "IS_TRANSIENT": "NO", "CLUSTERING_KEY": "LINEAR(D)", "AUTO_CLUSTERING_ON": "YES", "ROW_COUNT": 1500, "BYTES": 4096,
             "RETENTION_TIME": 7, "CREATED": NEW, "LAST_ALTERED": NEW, "LAST_DDL": NEW, "IS_ICEBERG": "NO", "IS_DYNAMIC": "NO"}
    new_dt = {**new_t, "TABLE_NAME": "NEW_DT", "IS_DYNAMIC": "YES", "CLUSTERING_KEY": None, "AUTO_CLUSTERING_ON": "NO", "BYTES": 0}
    new_mv = {**new_t, "TABLE_NAME": "NEW_MV", "TABLE_TYPE": "MATERIALIZED VIEW", "CLUSTERING_KEY": None}
    cols = [{"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "LAB", "TABLE_NAME": "NEW_T", "COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER",
             "NUMERIC_PRECISION": 38, "NUMERIC_SCALE": 0, "ORDINAL_POSITION": 1},
            {"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "LAB", "TABLE_NAME": "NEW_T", "COLUMN_NAME": "DOC", "DATA_TYPE": "VARIANT",
             "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "ORDINAL_POSITION": 2}]
    view = {"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "LAB", "TABLE_NAME": "V_NEW", "TABLE_OWNER": "LAB_ENGINEER", "IS_SECURE": "NO",
            "LAST_ALTERED": NEW, "VIEW_DEFINITION": f"create view V_NEW as select * from LAB.NEW_T where x <> '{CANARI}'"}
    mv_view = {**view, "TABLE_NAME": "NEW_MV"}  # une vue matérialisée peut aussi figurer dans VIEWS : pas de doublon
    fn = {**DATA["FUNCTIONS"][0], "FUNCTION_SCHEMA": "LAB", "FUNCTION_NAME": "FN_NEW", "LAST_ALTERED": NEW}
    proc = {**DATA["PROCEDURES"][2], "PROCEDURE_SCHEMA": "LAB", "PROCEDURE_NAME": "SP_NEW", "LAST_ALTERED": NEW}
    stage = {**DATA["STAGES"][1], "STAGE_SCHEMA": "LAB", "STAGE_NAME": "STG_NEW", "URL3": "new-bucket"}
    pipe = {**DATA["PIPES"][0], "PIPE_SCHEMA": "LAB", "PIPE_NAME": "P_NEW", "DEFINITION": "COPY INTO LAB.NEW_T FROM @LAB.STG_NEW/x/"}
    task = {"NAME": "T_NEW", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "LAB", "OWNER": "LAB_ENGINEER", "WAREHOUSE": "", "SCHEDULE": "5 MINUTE",
            "PREDECESSORS": "[]", "STATE": "started", "DEFINITION": f"insert into LAB.NEW_T select 1, '{CANARI}'", "CONDITION": "",
            "LAST_SUSPENDED_REASON": ""}
    stream = {"NAME": "S_NEW", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "LAB", "OWNER": "LAB_ENGINEER", "TABLE_NAME": "CANOT_LAB.LAB.NEW_T",
              "SOURCE_TYPE": "Table", "MODE": "DEFAULT", "STALE_AFTER": NOW}
    show_dt = {"NAME": "NEW_DT", "DATABASE_NAME": "CANOT_LAB", "SCHEMA_NAME": "LAB", "OWNER": "LAB_ENGINEER", "TARGET_LAG": "1 minute",
               "REFRESH_MODE": "FULL", "WAREHOUSE": "CANOT_WH", "SCHEDULING_STATE": "RUNNING", "TEXT": f"select '{CANARI}'"}
    data = {"IS.TABLES": DATA["IS.TABLES"] + [new_t, new_dt, new_mv], "IS.COLUMNS": DATA["IS.COLUMNS"] + cols,
            "IS.VIEWS": DATA["IS.VIEWS"] + [view, mv_view], "IS.FUNCTIONS": DATA["IS.FUNCTIONS"] + [fn],
            "IS.PROCEDURES": DATA["IS.PROCEDURES"] + [proc], "IS.STAGES": DATA["IS.STAGES"] + [stage], "IS.PIPES": DATA["IS.PIPES"] + [pipe],
            "SHOW TASKS": DATA["SHOW TASKS"] + [task], "SHOW STREAMS": DATA["SHOW STREAMS"] + [stream],
            "SHOW DYNAMIC TABLES": DATA["SHOW DYNAMIC TABLES"] + [show_dt]}
    data.update(extra)
    return data


def test_realtime_reads_are_logged_with_zero_latency():
    fake, snap, _ = scan()
    for leaf in ("TABLES", "COLUMNS", "VIEWS", "FUNCTIONS", "PROCEDURES", "STAGES", "PIPES"):
        x = lecture(snap, f"CANOT_LAB.INFORMATION_SCHEMA.{leaf}")
        assert x["statut"] == "lu" and x["latence_max_h"] == 0 and "temps réel" in x["raison"] and x["fenetre"] is None
    for vue in ("SHOW TASKS", "SHOW STREAMS"):
        assert lecture(snap, vue)["latence_max_h"] == 0
    assert any(s.startswith('SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, TABLE_OWNER') and '"CANOT_LAB".INFORMATION_SCHEMA.TABLES' in s
               for s in fake.sql)


def test_table_only_in_realtime_is_added_as_recent_with_unknown_history():
    _, snap, _ = scan(data=_recent_data())
    t = by_id(snap["tables"], "CANOT_LAB.LAB.NEW_T")
    assert "recente" in t["flags"] and t["detail"]["source"] == "information_schema"
    assert (t["lignes"], t["octets_actifs"], t["retention_jours"], t["proprietaire"], t["type"]) == (1500, 4096, 7, "LAB_ENGINEER", "TABLE")
    assert t["clustering"] == ["LINEAR(D)"] and "clustering_auto" in t["flags"]
    assert t["detail"]["creee_le"] == "2026-10-17T09:30:00Z" and t["detail"]["modifiee_le"] == "2026-10-17T09:30:00Z"
    assert t["derniere_ddl"] == "2026-10-17T09:30:00Z"
    assert [c["type"] for c in t["colonnes"]] == ["NUMBER(38,0)", "VARIANT"]
    # ce qui dépend d'ACCOUNT_USAGE reste inconnu : None, jamais 0
    for k in ("octets_historique", "n_lectures", "n_ecritures", "derniere_lecture", "derniere_ecriture", "clone_de"):
        assert t[k] is None, k
    d = t["detail"]
    assert d["historique_non_lu"] == "table trop récente pour ACCOUNT_USAGE"
    assert d["requetes_pruning_n"] is None and d["jamais_lu_pruning"] is None and d["jamais_lu"] is None
    assert d.get("fichiers_charges_n") is None and d.get("octets_retenus_clone") is None and d["table_id"] is None
    dt = by_id(snap["tables"], "CANOT_LAB.LAB.NEW_DT")
    assert {"recente", "dynamique"} <= set(dt["flags"]) and dt["octets_actifs"] == 0  # 0 lu en temps réel reste un 0 mesuré
    assert "CANOT_LAB.LAB.NEW_MV" not in {x["id"] for x in snap["tables"]}


def test_objects_only_in_realtime_are_added_and_marked():
    _, snap, _ = scan(data=_recent_data())
    o = {(x["kind"], x["id"]): x for x in snap["objets"]}
    assert len(o) == len(snap["objets"])  # aucun doublon
    expected = {("vue", "CANOT_LAB.LAB.V_NEW"): "information_schema", ("vue_materialisee", "CANOT_LAB.LAB.NEW_MV"): "information_schema",
                ("fonction", "CANOT_LAB.LAB.FN_NEW"): "information_schema", ("procedure", "CANOT_LAB.LAB.SP_NEW"): "information_schema",
                ("stage", "CANOT_LAB.LAB.STG_NEW"): "information_schema", ("pipe", "CANOT_LAB.LAB.P_NEW"): "information_schema",
                ("tache", "CANOT_LAB.LAB.T_NEW"): "show", ("flux", "CANOT_LAB.LAB.S_NEW"): "show",
                ("table_dynamique", "CANOT_LAB.LAB.NEW_DT"): "information_schema"}
    for k, src in expected.items():
        assert o[k]["detail"]["inventaire"] == src and o[k]["detail"]["recent"] is True, k
    assert ("vue", "CANOT_LAB.LAB.NEW_MV") not in o
    assert o[("vue", "CANOT_LAB.LAB.V_NEW")]["reference"] == ["CANOT_LAB.LAB.NEW_T"]  # OBJECT_DEPENDENCIES ne la connaît pas encore
    assert o[("stage", "CANOT_LAB.LAB.STG_NEW")]["dependances_externes"] == ["s3://new-bucket"]
    task, pipe, dt = o[("tache", "CANOT_LAB.LAB.T_NEW")], o[("pipe", "CANOT_LAB.LAB.P_NEW")], o[("table_dynamique", "CANOT_LAB.LAB.NEW_DT")]
    assert task["reference"] == ["CANOT_LAB.LAB.NEW_T"] and task["detail"]["serverless"] is True
    for x in (task, pipe, dt):  # historique pas encore disponible : inconnu, pas 0
        assert x["executions_n"] is None and x["echecs_n"] is None
    assert task["detail"]["credits_serverless"] is None
    assert dt["planification"] == "1 minute"
    assert sum(1 for x in snap["objets"] if x["detail"].get("recent")) == 9


def test_present_in_both_no_recent_flag_no_duplicate():
    fake, snap, _ = scan()
    assert not any("recente" in t["flags"] or t["detail"].get("source") for t in snap["tables"])
    assert not any("inventaire" in o["detail"] or "recent" in o["detail"] for o in snap["objets"])
    ids = [(o["kind"], o["id"]) for o in snap["objets"]]
    assert len(ids) == len(set(ids))
    cust = by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")
    assert cust["detail"]["vue_temps_reel"] is True and "ecart_colonnes" not in cust["detail"] and "ecart_type" not in cust["detail"]
    assert cust["n_lectures"] == 5 and by_id(snap["tables"], "CANOT_LAB.RAW.ARCHIVE_1")["detail"]["jamais_lu"] is True
    f = snap["fraicheur"]
    assert f["recents_seulement_temps_reel"] == {"tables": 0, "objets": 0} and f["latence_max_h"] == 48
    assert f["texte"] == "Recoupement en temps réel (INFORMATION_SCHEMA, SHOW) : aucun objet absent d'ACCOUNT_USAGE."


def test_columns_and_type_differences_are_reported_and_present_state_kept():
    cols = [dict(c) for c in DATA["IS.COLUMNS"]]
    cols[1]["DATA_TYPE"] = "VARIANT"                       # EMAIL_ADDR : type changé
    cols = cols[:3] + [{**cols[0], "COLUMN_NAME": "NOUVELLE", "ORDINAL_POSITION": 5}]  # GEO retirée, NOUVELLE ajoutée
    tabs = [dict(r, TABLE_TYPE="EVENT TABLE") if r["TABLE_NAME"] == "ARCHIVE_1" else r for r in DATA["IS.TABLES"]]
    _, snap, _ = scan(data={"IS.COLUMNS": cols, "IS.TABLES": tabs})
    cust = by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")
    assert cust["detail"]["ecart_colonnes"] == "colonnes en temps réel : 1 ajoutée(s), 1 retirée(s), 1 de type changé"
    assert [c["nom"] for c in cust["colonnes"]] == ["CUSTOMER_ID", "EMAIL_ADDR", "PROFILE", "NOUVELLE"]
    assert by_id(snap["tables"], "CANOT_LAB.RAW.ARCHIVE_1")["detail"]["ecart_type"] == "BASE TABLE dans ACCOUNT_USAGE, EVENT TABLE en temps réel"


def test_table_dropped_and_recreated_within_latency():
    tabs = [r for r in DATA["IS.TABLES"]] + [{**DATA["IS.TABLES"][0], "TABLE_NAME": "TMP_DROPPED", "CREATED": NEW, "LAST_DDL": NEW}]
    _, snap, _ = scan(data={"IS.TABLES": tabs})
    live, old = by_id(snap["tables"], "CANOT_LAB.RAW.TMP_DROPPED"), by_id(snap["tables"], "CANOT_LAB.RAW.TMP_DROPPED#5")
    assert {"recente", "recreee"} <= set(live["flags"]) and live["supprimee_le"] is None
    assert old["supprimee_le"] == "2026-10-10T08:00:00Z" and old["octets_historique"] == 1000


def test_realtime_denied_is_non_lu_with_reason_and_inventory_kept():
    fail = {k: "SQL access control error: Insufficient privileges to operate on schema 'INFORMATION_SCHEMA'" for k in RT_KEYS}
    _, snap, _ = scan(fail=fail, data=_recent_data())
    for leaf in ("TABLES", "COLUMNS", "VIEWS", "FUNCTIONS", "PROCEDURES", "STAGES", "PIPES"):
        x = lecture(snap, f"CANOT_LAB.INFORMATION_SCHEMA.{leaf}")
        assert x["statut"] == "non_lu" and x["raison"] == "droit absent" and x["lignes"] is None
    assert lecture(snap, "SHOW TASKS")["raison"] == "droit absent"
    assert "CANOT_LAB.LAB.NEW_T" not in {t["id"] for t in snap["tables"]} and by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")
    assert snap["fraicheur"]["texte"].startswith("Recoupement en temps réel non lu (droit absent)")
    # SHOW STREAMS restait lisible : le flux récent est vu
    assert by_id(snap["objets"], "CANOT_LAB.LAB.S_NEW")["detail"]["recent"] is True


def test_account_usage_tables_refused_realtime_is_a_fallback_not_recent():
    _, snap, _ = scan(fail={"TABLES": DENIED}, data=_recent_data())
    t = by_id(snap["tables"], "CANOT_LAB.LAB.NEW_T")
    assert "recente" not in t["flags"] and t["detail"]["source"] == "information_schema"
    assert t["detail"]["historique_non_lu"] == "ACCOUNT_USAGE.TABLES non lue"
    cust = by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")  # connue des métriques de stockage : complétée, pas « récente »
    assert cust["detail"]["inventaire"] == "information_schema" and "type_non_lu" not in cust["detail"] and "recente" not in cust["flags"]
    assert cust["proprietaire"] == "LAB_ENGINEER" and cust["octets_actifs"] == 1000 and cust["n_lectures"] == 5
    assert len({t["id"] for t in snap["tables"]}) == len(snap["tables"])


def test_fraicheur_counts_and_text():
    _, snap, _ = scan(data=_recent_data())
    f = snap["fraicheur"]
    assert f["recents_seulement_temps_reel"] == {"tables": 2, "objets": 9} and f["latence_max_h"] == 48
    assert f["texte"] == ("2 tables et 9 objets vus seulement en temps réel : absents d'ACCOUNT_USAGE, dont la latence va jusqu'à 48 h "
                          "selon les vues ; leur historique (volumes facturés, lectures) n'est pas encore disponible.")
    _, snap, _ = scan(base="*")
    assert snap["fraicheur"]["texte"].startswith("Recoupement en temps réel non fait")
    assert not any("INFORMATION_SCHEMA" in x["vue"] for x in snap["lectures"])
    _, snap, _ = scan(level=1)
    assert snap["fraicheur"]["latence_max_h"] == 48 and lecture(snap, "CANOT_LAB.INFORMATION_SCHEMA.TABLES")["statut"] == "lu"


def test_realtime_never_reads_table_data_and_stays_readonly():
    import sqlglot
    from sqlglot import exp

    fake, snap, _ = scan(data=_recent_data(), schemas=["raw", "lab"])
    rt = [s for s in fake.sql if ".INFORMATION_SCHEMA." in s]
    assert len(rt) == 7
    for s in fake.sql:
        assert check_readonly(s, "snowflake") == s
        if s.startswith(("SHOW", "ALTER")):
            continue
        for t in sqlglot.parse_one(s, read="snowflake").find_all(exp.Table):  # métadonnées seulement : jamais une table du client
            assert (t.catalog, t.db) == ("SNOWFLAKE", "ACCOUNT_USAGE") or t.db == "INFORMATION_SCHEMA", s[:100]
    assert all("TABLE_SCHEMA IN ('RAW', 'LAB')" in s or "_SCHEMA IN ('RAW', 'LAB')" in s for s in rt)
    assert all("STAGE_URL" not in s or "SPLIT_PART(STAGE_URL" in s for s in rt)


def test_realtime_canary_and_forbidden_words_absent():
    _, snap, _ = scan(data=_recent_data())
    out = json.dumps(snap, ensure_ascii=False, default=str)
    for leak in (CANARI, "canari-7f3a", "exemple.test", "/x/", "select ", "insert into"):
        assert leak not in out, leak
    low = out.lower()
    for w in FORBIDDEN_WORDS:
        assert w not in low, w


def test_materialized_view_listed_in_views_is_described_once():
    views = DATA["VIEWS"] + [{"TABLE_CATALOG": "CANOT_LAB", "TABLE_SCHEMA": "MART", "TABLE_NAME": "MV_DAILY", "TABLE_OWNER": "LAB_ENGINEER",
                              "IS_SECURE": "NO", "LAST_ALTERED": D, "VIEW_DEFINITION": None}]
    _, snap, _ = scan(data={"VIEWS": views, "IS.VIEWS": views})
    assert [o["kind"] for o in snap["objets"] if o["id"] == "CANOT_LAB.MART.MV_DAILY"] == ["vue_materialisee"]


def test_storage_metrics_not_yet_computed_is_said():
    tsm = [dict(m, ACTIVE_BYTES=0) if m["ID"] == 1 else m for m in DATA["TABLE_STORAGE_METRICS"]]
    _, snap, _ = scan(data={"TABLE_STORAGE_METRICS": tsm})
    cust = by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")
    assert cust["octets_actifs"] == 0 and cust["detail"]["octets_temps_reel"] == 2048 and cust["detail"]["volume_en_retard"] is True
    f = snap["fraicheur"]
    assert f["volumes_en_retard"] == 1 and f["texte"].endswith(
        "1 table à 0 octet dans TABLE_STORAGE_METRICS mais non vide en temps réel : métriques de stockage pas encore à jour, volume sous-estimé.")
    _, snap, _ = scan()
    assert snap["fraicheur"]["volumes_en_retard"] == 0 and "volume_en_retard" not in by_id(snap["tables"], "CANOT_LAB.RAW.CUSTOMERS")["detail"]
