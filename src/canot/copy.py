from __future__ import annotations

import time
from dataclasses import dataclass, field

import pyarrow as pa

from .convert import Conversion, conform, fingerprint, merge_fp, target_schema
from .model import TableInfo
from .sources.base import Source


@dataclass
class CopyResult:
    table: TableInfo
    rows: int = 0
    bytes: int = 0
    seconds: float = 0.0
    fingerprint: dict[int, list[int]] = field(default_factory=dict)
    conversions: list[Conversion] = field(default_factory=list)
    schema: pa.Schema | None = None
    error: str | None = None


def copy_table(src: Source, t: TableInfo, catalog, chunk_rows: int = 500_000) -> CopyResult:
    res = CopyResult(t)
    t0 = time.monotonic()
    ns = t.schema
    try:
        catalog.create_namespace_if_not_exists(ns)
        types = {c.name: c.type for c in t.columns}
        ice = None
        buf: list[pa.RecordBatch] = []
        buf_rows = 0

        def flush():
            nonlocal buf, buf_rows
            if not buf:
                return
            tbl = conform(pa.Table.from_batches(buf), res.schema)
            ice.append(tbl)
            res.fingerprint = merge_fp(res.fingerprint, fingerprint(tbl))
            res.rows += tbl.num_rows
            res.bytes += tbl.nbytes
            buf, buf_rows = [], 0

        for batch in src.read(t):
            if ice is None:
                res.schema, res.conversions = target_schema(t.fqn, batch.schema, types)
                ident = (ns, t.name)
                if catalog.table_exists(ident):
                    catalog.drop_table(ident)
                ice = catalog.create_table(ident, schema=res.schema)
            buf.append(batch)
            buf_rows += batch.num_rows
            if buf_rows >= chunk_rows:
                flush()
        if ice is None:  # empty table: schema from source column list is not available as arrow; create from a limit-0 read
            empty = src.query(f"select * from {t.sql} limit 0") if src.dialect == "duckdb" else None
            if empty is not None:
                res.schema, res.conversions = target_schema(t.fqn, empty.schema, types)
                catalog.create_table((ns, t.name), schema=res.schema)
        else:
            flush()
    except Exception as e:  # keep going: one broken table must not hide the others
        res.error = f"{type(e).__name__}: {e}"
    res.seconds = time.monotonic() - t0
    return res
