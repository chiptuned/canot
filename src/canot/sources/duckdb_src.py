from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
from urllib.parse import urlparse

import duckdb
import pyarrow as pa

from ..model import ColumnInfo, TableInfo, q
from .base import Source


class DuckDBSource(Source):
    """A local DuckDB file. Used for demos, tests and as a stand-in for a warehouse."""

    dialect = "duckdb"
    kind = "duckdb"

    def __init__(self, url: str):
        u = urlparse(url)
        windows_path = re.match(r"^[A-Za-z]:[\\/]", url) is not None  # C:\... parses as scheme "c"
        path = url if u.scheme == "" or windows_path else (u.netloc + u.path)
        if u.scheme == "duckdb" and url.startswith("duckdb:///"):
            path = u.path
        self.path = str(Path(path).expanduser())
        self.con = duckdb.connect(self.path, read_only=True)

    def describe(self) -> str:
        return f"duckdb:{self.path}"

    def tables(self) -> list[TableInfo]:
        rows = self.con.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_type='BASE TABLE' and table_schema not in ('information_schema','pg_catalog') "
            "order by 1,2"
        ).fetchall()
        out = []
        for schema, name in rows:
            cols = self.con.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema=? and table_name=? order by ordinal_position",
                [schema, name],
            ).fetchall()
            n = self.con.execute(f"select count(*) from {q(schema)}.{q(name)}").fetchone()[0]
            sample = self.con.execute(f"select * from {q(schema)}.{q(name)} limit 1000").fetch_arrow_table()
            est = int(sample.nbytes * (n / max(sample.num_rows, 1))) if n else 0
            out.append(TableInfo(schema, name, n, est, [ColumnInfo(c, t) for c, t in cols]))
        return out

    def read(self, table: TableInfo, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        cur = self.con.cursor()
        reader = cur.execute(f"select * from {table.sql}").fetch_record_batch(batch_rows)
        yield from reader

    def query(self, sql: str) -> pa.Table:
        return self.con.cursor().execute(sql).fetch_arrow_table()

    def objects(self) -> dict[str, list[str]]:
        views = self.con.execute(
            "select table_schema||'.'||table_name from information_schema.tables where table_type='VIEW' "
            "and table_schema not in ('information_schema','pg_catalog')"
        ).fetchall()
        macros = self.con.execute(
            "select schema_name||'.'||function_name from duckdb_functions() where internal=false and function_type='macro'"
        ).fetchall()
        return {"views": [v[0] for v in views], "macros": sorted({m[0] for m in macros})}

    def close(self) -> None:
        self.con.close()
