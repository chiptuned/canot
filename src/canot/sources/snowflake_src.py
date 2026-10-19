from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pyarrow as pa

from ..model import ColumnInfo, TableInfo, q
from .base import Source

PUBLIC_KEY_PLACEHOLDER = "<public key, without the BEGIN/END lines>"

SETUP_SQL = """\
-- Canot: read-only access. Review it, then have an administrator run it (ACCOUNTADMIN or SECURITYADMIN role).
-- Replace the values between <> before running.
CREATE ROLE IF NOT EXISTS CANOT_READER;
CREATE WAREHOUSE IF NOT EXISTS CANOT_WH WITH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON WAREHOUSE CANOT_WH TO ROLE CANOT_READER;
-- Credit cap for the exercise (adjust if needed)
CREATE RESOURCE MONITOR IF NOT EXISTS CANOT_MONITOR WITH CREDIT_QUOTA = 25
  TRIGGERS ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE CANOT_WH SET RESOURCE_MONITOR = CANOT_MONITOR;
-- Read-only access to the database to copy
GRANT USAGE ON DATABASE <DATABASE> TO ROLE CANOT_READER;
GRANT USAGE ON ALL SCHEMAS IN DATABASE <DATABASE> TO ROLE CANOT_READER;
GRANT SELECT ON ALL TABLES IN DATABASE <DATABASE> TO ROLE CANOT_READER;
GRANT SELECT ON FUTURE TABLES IN DATABASE <DATABASE> TO ROLE CANOT_READER;
-- `canot scan`: account metadata (SNOWFLAKE.ACCOUNT_USAGE) through the read-only database roles.
-- Keep the lines for the level you want and those of the lower levels.
GRANT DATABASE ROLE SNOWFLAKE.OBJECT_VIEWER TO ROLE CANOT_READER;     -- level 1: inventory, code, dependencies
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE CANOT_READER;      -- level 1: volumes, consumption
GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER TO ROLE CANOT_READER; -- level 2: queries (normalized forms), reads per table, policies
GRANT DATABASE ROLE SNOWFLAKE.SECURITY_VIEWER TO ROLE CANOT_READER;   -- level 3: grants and tools (pseudonymized)
-- Optional: parameters of every warehouse in SHOW WAREHOUSES
-- GRANT MONITOR ON WAREHOUSE <WAREHOUSE> TO ROLE CANOT_READER;
-- Technical service user, key-pair authentication (no password)
CREATE USER IF NOT EXISTS CANOT_USER TYPE = SERVICE DEFAULT_ROLE = CANOT_READER DEFAULT_WAREHOUSE = CANOT_WH
  RSA_PUBLIC_KEY = '<public key, without the BEGIN/END lines>';
GRANT ROLE CANOT_READER TO USER CANOT_USER;
"""


class SnowflakeSource(Source):
    dialect = "snowflake"
    kind = "snowflake"

    def __init__(self, url: str):
        import snowflake.connector  # optional extra

        u = urlparse(url)
        qs = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        self.account = u.netloc
        self.schemas = [x.upper() for x in qs.get("schemas", "").split(",") if x]
        self.database = u.path.strip("/") or qs.get("database", "")
        if not self.database:
            raise ValueError("Expected Snowflake URL: snowflake://ACCOUNT/DATABASE?user=...&warehouse=...&role=...")
        args: dict = dict(
            account=self.account,
            user=qs.get("user") or os.environ.get("SNOWFLAKE_USER"),
            warehouse=qs.get("warehouse") or os.environ.get("SNOWFLAKE_WAREHOUSE", "CANOT_WH"),
            role=qs.get("role") or os.environ.get("SNOWFLAKE_ROLE", "CANOT_READER"),
            database=self.database,
            session_parameters={"QUERY_TAG": "canot"},
        )
        for k in ("warehouse", "role"):  # "role=" (present, empty): the user's default, as dbt does for a profile without a role
            if qs.get(k) == "":
                del args[k]
        key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
        if key_path:
            from cryptography.hazmat.primitives import serialization

            pw = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
            with open(key_path, "rb") as f:
                key = serialization.load_pem_private_key(f.read(), password=pw.encode() if pw else None)
            args["private_key"] = key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        elif os.environ.get("SNOWFLAKE_PASSWORD"):
            args["password"] = os.environ["SNOWFLAKE_PASSWORD"]
        else:
            raise ValueError("Set SNOWFLAKE_PRIVATE_KEY_PATH (recommended) or SNOWFLAKE_PASSWORD.")
        self.con = snowflake.connector.connect(**args)

    def describe(self) -> str:
        return f"snowflake:{self.account}/{self.database}"

    def table_ref(self, table: TableInfo) -> str:
        return f"{q(self.database, 'snowflake')}.{table.sql_for('snowflake')}"

    def _schema_filter(self) -> str:
        return (" and table_schema in (" + ",".join("'" + x.replace("'", "''") + "'" for x in self.schemas) + ")") if self.schemas else ""

    def run_readonly(self, sql: str) -> list[dict]:
        """Run a scan read (read-only guard first); rows as dicts, column names in UPPERCASE."""
        from ..scanner.guard import check_readonly

        sql = check_readonly(sql, "snowflake")
        cur = self.con.cursor()
        try:
            cur.execute(sql)
            cols = [d[0].upper() for d in cur.description or []]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            cur.close()

    def _rows(self, sql: str) -> list[tuple]:
        cur = self.con.cursor()
        try:
            cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.close()

    def tables(self) -> list[TableInfo]:
        db = q(self.database, 'snowflake')
        trs = self._rows(
            f"select table_schema, table_name, row_count, bytes from {db}.information_schema.tables "
            "where table_type='BASE TABLE' and table_schema <> 'INFORMATION_SCHEMA'" + self._schema_filter() + " order by 1,2"
        )
        cols: dict[tuple[str, str], list[ColumnInfo]] = {}
        for s, t, c, d in self._rows(
            f"select table_schema, table_name, column_name, data_type from {db}.information_schema.columns "
            "where table_schema <> 'INFORMATION_SCHEMA'" + self._schema_filter() + " order by table_schema, table_name, ordinal_position"
        ):
            cols.setdefault((s, t), []).append(ColumnInfo(c, d))
        return [TableInfo(s, t, r, b, cols.get((s, t), [])) for s, t, r, b in trs]

    def read(self, table: TableInfo, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        cur = self.con.cursor()
        try:
            cur.execute(f"select * from {self.table_ref(table)}")
            for chunk in cur.fetch_arrow_batches():
                yield from chunk.to_batches(max_chunksize=batch_rows)
        finally:
            cur.close()

    def query(self, sql: str) -> pa.Table:
        cur = self.con.cursor()
        try:
            cur.execute(sql)
            return cur.fetch_arrow_all() or pa.table({})
        finally:
            cur.close()

    def history_queries(self, limit: int = 20) -> list[str]:
        try:
            rows = self._rows(
                "select query_text, sum(total_elapsed_time) t from table(information_schema.query_history("
                "end_time_range_start=>dateadd('day',-7,current_timestamp()), result_limit=>10000)) "
                "where query_type='SELECT' and execution_status='SUCCESS' "
                "and coalesce(query_tag,'') <> 'canot' "
                "and lower(query_text) not like '%information_schema%' and lower(query_text) not like '%snowflake.account_usage%' "
                f"group by query_text order by t desc limit {int(limit)}"
            )
            return [r[0] for r in rows]
        except Exception:
            return []

    def objects(self) -> dict[str, list[str]]:
        db = q(self.database, 'snowflake')
        out: dict[str, list[str]] = {}

        def grab(key: str, sql: str):
            try:
                out[key] = [".".join(str(x) for x in r) for r in self._rows(sql)]
            except Exception:
                out[key] = []

        grab("views", f"select table_schema, table_name from {db}.information_schema.views where table_schema <> 'INFORMATION_SCHEMA'")
        grab("procedures", f"select procedure_schema, procedure_name from {db}.information_schema.procedures")
        grab("functions", f"select function_schema, function_name from {db}.information_schema.functions where function_schema <> 'INFORMATION_SCHEMA'")
        for key, show in (("tasks", "tasks"), ("streams", "streams"), ("pipes", "pipes"), ("dynamic_tables", "dynamic tables")):
            try:
                rows = self._rows(f"show {show} in database {db}")
                out[key] = [str(r[1]) for r in rows]
            except Exception:
                out[key] = []
        return out

    def usage_90d(self) -> dict | None:
        try:
            r = self._rows(
                "select coalesce(sum(credits_billed),0) from snowflake.account_usage.metering_daily_history "
                "where usage_date >= dateadd(day,-90,current_date())"
            )
            return {"credits_90d": float(r[0][0])}
        except Exception:
            return None

    def close(self) -> None:
        self.con.close()
