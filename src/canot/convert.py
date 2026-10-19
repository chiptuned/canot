"""Arrow -> Iceberg-compatible schema, row fingerprints."""
from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pyarrow as pa

from .model import q

BUCKETS = 64


@dataclass
class Conversion:
    table: str
    column: str
    source_type: str
    target_type: str
    lossy: bool
    note: str


def _convert_type(t: pa.DataType) -> tuple[pa.DataType, str | None, bool]:
    if pa.types.is_timestamp(t) and t.tz and t.tz.upper() not in ("UTC", "+00:00", "Z"):
        unit = "us" if t.unit == "ns" else t.unit
        return pa.timestamp(unit, tz="UTC"), f"time zone {t.tz} -> UTC (Iceberg keeps UTC only): the instant is kept, the zone name is not", True
    if pa.types.is_timestamp(t) and t.unit == "ns":
        return pa.timestamp("us", tz=t.tz), "ns -> us (Iceberg v2 does not keep nanoseconds)", True
    if pa.types.is_large_string(t):
        return pa.string(), None, False
    if pa.types.is_null(t):
        return pa.string(), "all-null column, typed as text", False
    if pa.types.is_unsigned_integer(t):
        wider = {8: pa.int16(), 16: pa.int32(), 32: pa.int64(), 64: pa.int64()}[t.bit_width]
        return wider, "unsigned integer -> signed", t.bit_width == 64
    if pa.types.is_decimal(t) and t.precision > 38:
        return pa.string(), "precision > 38 stored as text", True
    if pa.types.is_struct(t) and {f.name.lower() for f in t} >= {"epoch", "timezone"}:
        return pa.string(), "native Snowflake TIMESTAMP_TZ: original time zone kept as text", True
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        inner, _, lossy = _convert_type(t.value_type)
        return pa.list_(inner), None, lossy
    if pa.types.is_struct(t):
        fields, lossy = [], False
        for f in t:
            ft, _, l = _convert_type(f.type)
            fields.append(pa.field(f.name, ft))
            lossy = lossy or l
        return pa.struct(fields), None, lossy
    if pa.types.is_dictionary(t):
        return t.value_type, None, False
    return t, None, False


def target_schema(table: str, schema: pa.Schema, source_types: dict[str, str]) -> tuple[pa.Schema, list[Conversion]]:
    fields, convs = [], []
    for f in schema:
        nt, note, lossy = _convert_type(f.type)
        if note or nt != f.type:
            convs.append(Conversion(table, f.name, source_types.get(f.name, str(f.type)), str(nt), lossy, note or "normalized type"))
        fields.append(pa.field(f.name, nt, nullable=True))
    return pa.schema(fields), convs


def conform(batch: pa.RecordBatch | pa.Table, schema: pa.Schema) -> pa.Table:
    t = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
    cols = []
    for f in schema:
        col = t.column(f.name)
        if col.type != f.type:
            if pa.types.is_string(f.type) and pa.types.is_struct(col.type):
                col = pa.array([None if v is None else str(v) for v in col.to_pylist()], type=pa.string())
            else:
                col = col.cast(f.type, safe=False)
        cols.append(col)
    return pa.Table.from_arrays(cols, schema=schema)


def fingerprint(table: pa.Table) -> dict[int, list[int]]:
    """Per-bucket [row_count, sum_of_row_hashes]. Additive, so batches can be merged."""
    if table.num_rows == 0 or table.num_columns == 0:
        return {}
    con = duckdb.connect()
    con.register("t", table)
    parts = ", ".join(f"({q(n)} is null), coalesce(cast({q(n)} as varchar), '')" for n in table.column_names)
    rows = con.execute(
        f"select h % {BUCKETS} b, count(*), sum(h % 1000000007) from (select hash({parts}) h from t) group by 1"
    ).fetchall()
    con.close()
    return {int(b): [int(c), int(s)] for b, c, s in rows}


def merge_fp(a: dict[int, list[int]], b: dict[int, list[int]]) -> dict[int, list[int]]:
    out = {k: list(v) for k, v in a.items()}
    for k, (c, s) in b.items():
        cur = out.setdefault(k, [0, 0])
        cur[0] += c
        cur[1] += s
    return out
