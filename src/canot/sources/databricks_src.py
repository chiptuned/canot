from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pyarrow as pa

from ..model import ColumnInfo, TableInfo, q
from .base import Source


class DatabricksSource(Source):
    """Reads through a SQL warehouse. Databricks tables can also be read straight from the
    customer's bucket with delta-rs (planned: no Databricks compute at all)."""

    dialect = "databricks"
    kind = "databricks"

    def __init__(self, url: str):
        from databricks import sql  # optional extra

        u = urlparse(url)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.host = u.netloc
        self.catalog = qs.get("catalog") or os.environ.get("DATABRICKS_CATALOG", "main")
        token = os.environ.get("DATABRICKS_TOKEN")
        if not token:
            raise ValueError("Définissez DATABRICKS_TOKEN (jeton à droits de lecture).")
        self.con = sql.connect(server_hostname=self.host, http_path=u.path, access_token=token, catalog=self.catalog)

    def describe(self) -> str:
        return f"databricks:{self.host}/{self.catalog}"

    def table_ref(self, table: TableInfo) -> str:
        return f"{q(self.catalog, 'databricks')}.{table.sql_for('databricks')}"

    def _rows(self, sql: str) -> list[tuple]:
        with self.con.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def tables(self) -> list[TableInfo]:
        cat = q(self.catalog, "databricks")
        trs = self._rows(
            f"select table_schema, table_name from {cat}.information_schema.tables "
            "where table_type in ('MANAGED','EXTERNAL','BASE TABLE') and table_schema <> 'information_schema' order by 1,2"
        )
        cols: dict[tuple[str, str], list[ColumnInfo]] = {}
        for s, t, c, d in self._rows(
            f"select table_schema, table_name, column_name, data_type from {cat}.information_schema.columns "
            "where table_schema <> 'information_schema' order by table_schema, table_name, ordinal_position"
        ):
            cols.setdefault((s, t), []).append(ColumnInfo(c, d))
        out = []
        for s, t in trs:
            n = self._rows(f"select count(*) from {cat}.{q(s, "databricks")}.{q(t, "databricks")}")[0][0]
            out.append(TableInfo(s, t, int(n), None, cols.get((s, t), [])))
        return out

    def read(self, table: TableInfo, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        with self.con.cursor() as cur:
            cur.execute(f"select * from {self.table_ref(table)}")
            while True:
                chunk = cur.fetchmany_arrow(batch_rows)
                if chunk.num_rows == 0:
                    break
                yield from chunk.to_batches(max_chunksize=batch_rows)

    def query(self, sql: str) -> pa.Table:
        with self.con.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall_arrow()

    def objects(self) -> dict[str, list[str]]:
        try:
            rows = self._rows(
                f"select table_schema, table_name from {q(self.catalog, 'databricks')}.information_schema.views where table_schema <> 'information_schema'"
            )
            return {"views": [f"{a}.{b}" for a, b in rows]}
        except Exception:
            return {}

    def close(self) -> None:
        self.con.close()
