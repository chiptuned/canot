from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp

from .convert import fingerprint, merge_fp
from .model import TableInfo, q
from .sources.base import Source
from .tolerances import Tolerances, equal


@dataclass
class TableCheck:
    table: str
    rows_source: int | None
    rows_target: int
    schema_ok: bool
    stats_ok: bool
    stats_diffs: list[str] = field(default_factory=list)
    fingerprint_ok: bool = True
    bad_buckets: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.schema_ok and self.stats_ok and self.fingerprint_ok and self.rows_source == self.rows_target


def _kind(t: pa.DataType) -> str | None:
    if pa.types.is_boolean(t):
        return None
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "num"
    if pa.types.is_string(t) or pa.types.is_date(t) or pa.types.is_timestamp(t) or pa.types.is_time(t):
        return "ord"
    return None


def _stats_sql(table_sql: str, schema: pa.Schema) -> tuple[str, list[tuple[str, str]]]:
    exprs, keys = ["count(*) as n"], []
    for f in schema:
        k = _kind(f.type)
        c = q(f.name)
        exprs.append(f"count({c}) as {q('nn__' + f.name)}")
        keys.append((f.name, "nn"))
        if k:
            exprs += [f"min({c}) as {q('min__' + f.name)}", f"max({c}) as {q('max__' + f.name)}"]
            keys += [(f.name, "min"), (f.name, "max")]
        if k == "num":
            exprs.append(f"sum({c}) as {q('sum__' + f.name)}")
            keys.append((f.name, "sum"))
    return f"select {', '.join(exprs)} from {table_sql}", keys


def _row(tbl: pa.Table) -> dict:
    return {k.lower(): v[0] for k, v in tbl.to_pydict().items()}


def check_table(src: Source, t: TableInfo, ice_table, source_fp: dict[int, list[int]], tol: Tolerances) -> TableCheck:
    schema = ice_table.schema().as_arrow()
    src_names = [c.name.lower() for c in t.columns]
    schema_ok = [n.lower() for n in schema.names] == src_names if src_names else True

    # one pass over the Iceberg data: fingerprint + stats via DuckDB
    con = duckdb.connect()
    reader = ice_table.scan().to_arrow_batch_reader()
    arrow_t = pa.Table.from_batches(reader, schema=schema)
    fp = merge_fp({}, fingerprint(arrow_t))
    con.register("tgt", arrow_t)
    sql_t, keys = _stats_sql("tgt", schema)
    tgt = _row(con.execute(sql_t).fetch_arrow_table())
    con.close()

    sql_s, _ = _stats_sql(f"{q(src_db(src))}.{t.sql}" if src.dialect == "snowflake" else t.sql, schema)
    srow = _row(src.query(sql_s))

    diffs = []
    rows_src = int(srow.get("n", -1))
    if rows_src != int(tgt["n"]):
        diffs.append(f"nombre de lignes : {rows_src} / {tgt['n']}")
    for col, what in keys:
        key = f"{what}__{col}".lower()
        a, b = srow.get(key), tgt.get(key)
        ov = tol.abs_for(t.fqn, col)
        if not equal(a, b, tol, ov):
            diffs.append(f"{col}.{what} : {a!r} / {b!r}")
    bad = sorted(k for k in set(fp) | set(source_fp) if fp.get(k) != source_fp.get(k))
    return TableCheck(t.fqn, t.rows, int(tgt["n"]), schema_ok, not diffs, diffs[:20], not bad, bad)


def src_db(src: Source) -> str:
    return getattr(src, "database", "")


# ---------------------------------------------------------------- query replay

@dataclass
class QueryCheck:
    sql: str
    translated: str | None
    status: str  # match | mismatch | untranslatable | error
    detail: str = ""
    rows_source: int | None = None
    rows_target: int | None = None
    order_differs: bool = False
    seconds_source: float = 0.0
    seconds_target: float = 0.0


def split_sql(text: str) -> list[str]:
    return [s.strip() for s in text.split(";") if s.strip() and not s.strip().startswith("--")]


def referenced_tables(sql: str, dialect: str) -> list[tuple[str | None, str]]:
    tree = sqlglot.parse_one(sql, read=dialect)
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    out = []
    for t in tree.find_all(exp.Table):
        if t.name.lower() in ctes and not t.db:
            continue
        out.append((t.db or None, t.name))
    return out


def _rows(t: pa.Table) -> list[tuple]:
    cols = [t.column(i).to_pylist() for i in range(t.num_columns)]
    return list(zip(*cols)) if cols else []


def _key(r: tuple) -> str:
    out = []
    for v in r:
        if isinstance(v, float):
            out.append(f"{v:.9g}")
        elif v is None:
            out.append("\u0000")
        else:
            out.append(str(v))
    return "\u0001".join(out)


def compare_results(a: pa.Table, b: pa.Table, tol: Tolerances, ordered: bool) -> tuple[bool, str, bool]:
    if a.num_columns != b.num_columns:
        return False, f"{a.num_columns} colonnes / {b.num_columns}", False
    ra, rb = _rows(a), _rows(b)
    if len(ra) != len(rb):
        return False, f"{len(ra)} lignes / {len(rb)}", False
    sa, sb = sorted(ra, key=_key), sorted(rb, key=_key)
    for i, (x, y) in enumerate(zip(sa, sb)):
        for j, (u, v) in enumerate(zip(x, y)):
            if not equal(u, v, tol):
                return False, f"ligne triée {i}, colonne {j} : {u!r} / {v!r}", False
    order_differs = ordered and any(not all(equal(u, v, tol) for u, v in zip(x, y)) for x, y in zip(ra, rb))
    return True, "", order_differs


def replay_queries(src: Source, queries: list[str], catalog, tables: list[TableInfo], tol: Tolerances) -> list[QueryCheck]:
    by_name = {(t.schema.lower(), t.name.lower()): t for t in tables}
    by_bare: dict[str, list[TableInfo]] = {}
    for t in tables:
        by_bare.setdefault(t.name.lower(), []).append(t)
    con = duckdb.connect()
    loaded: set[tuple[str, str]] = set()
    out: list[QueryCheck] = []

    def load(t: TableInfo):
        key = (t.schema, t.name)
        if key in loaded:
            return
        con.execute(f"create schema if not exists {q(t.schema)}")
        arrow = catalog.load_table((t.schema, t.name)).scan().to_arrow()
        con.register("_tmp_" + str(len(loaded)), arrow)
        con.execute(f"create or replace view {q(t.schema)}.{q(t.name)} as select * from _tmp_{len(loaded)}")
        loaded.add(key)

    for sql in queries:
        qc = QueryCheck(sql, None, "error")
        try:
            tr = sqlglot.transpile(sql, read=src.dialect, write="duckdb")[0]
            qc.translated = tr
            for db, name in referenced_tables(sql, src.dialect):
                t = by_name.get(((db or "").lower(), name.lower())) if db else None
                if t is None:
                    c = by_bare.get(name.lower(), [])
                    t = c[0] if len(c) >= 1 else None
                if t is None:
                    raise LookupError(f"table inconnue de la copie : {name}")
                load(t)
        except LookupError as e:
            qc.status, qc.detail = "untranslatable", str(e)
            out.append(qc)
            continue
        except Exception as e:
            qc.status, qc.detail = "untranslatable", f"{type(e).__name__}: {e}"[:300]
            out.append(qc)
            continue
        try:
            t0 = time.monotonic()
            a = src.query(sql)
            qc.seconds_source = time.monotonic() - t0
            t0 = time.monotonic()
            b = con.execute(qc.translated).fetch_arrow_table()
            qc.seconds_target = time.monotonic() - t0
            qc.rows_source, qc.rows_target = a.num_rows, b.num_rows
            ordered = bool(sqlglot.parse_one(sql, read=src.dialect).find(exp.Order))
            ok, detail, od = compare_results(a, b, tol, ordered)
            qc.status, qc.detail, qc.order_differs = ("match" if ok else "mismatch"), detail, od
        except Exception as e:
            qc.status, qc.detail = "error", f"{type(e).__name__}: {e}"[:300]
        out.append(qc)
    con.close()
    return out
