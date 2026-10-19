from __future__ import annotations

from . import prices
from .convert import target_schema
from .sources.base import Source

EGRESS_NOTES = {
    "snowflake": "Read through the connector: no Snowflake egress fees, only the credits of the warehouse that reads.",
    "bigquery": "BigQuery: the read is billed through the Storage Read API (per TiB read) plus network egress out of Google Cloud; "
                "Google's exit program may credit them under conditions (to confirm at cloud.google.com/exit-cloud). Rates to fill in.",
}
NON_PORTABLE = ("JSON", "RANGE", "INTERVAL", "VARIANT", "OBJECT", "ARRAY", "GEOGRAPHY", "GEOMETRY", "VECTOR", "MAP", "STRUCT")
# assumed copy throughput range in MB/s: a planning assumption (est.), replaced by the measured value after a drill
THROUGHPUT_MBPS = (20.0, 80.0)


def scan(src: Source, credit_price: float | None = 3.90) -> dict:
    tables = src.tables()
    total_rows = sum(t.rows or 0 for t in tables)
    total_bytes = sum(t.bytes or 0 for t in tables)
    risky = []
    for t in tables:
        for c in t.columns:
            if any(k in c.type.upper() for k in NON_PORTABLE):
                risky.append({"table": t.fqn, "column": c.name, "type": c.type})
    objs = src.objects()
    movable = {k: len(v) for k, v in objs.items()}
    gb = total_bytes / 1e9
    hours = (total_bytes / 1e6 / THROUGHPUT_MBPS[1] / 3600, total_bytes / 1e6 / THROUGHPUT_MBPS[0] / 3600)
    est = {
        "duration_hours": [round(hours[0], 2), round(hours[1], 2)],
        "credits": [round(hours[0] * src.credits_per_hour, 2), round(hours[1] * src.credits_per_hour, 2)],
        "assumed_throughput_mb_s": list(THROUGHPUT_MBPS),
        "note": "planning estimate; replaced by the measurement after a `canot drill`",
    }
    if src.dialect == "snowflake":
        lo, basis = prices.snowflake_cost(est["credits"][0], credit_price)
        hi, _ = prices.snowflake_cost(est["credits"][1], credit_price)
        est["cost_usd"] = [lo[0], hi[1]]
        est["cost_basis"] = basis
    n_objects = sum(len(v) for v in objs.values())
    exit_cost = {
        "reading_usd": est.get("cost_usd"),
        "reading_basis": est.get("cost_basis") or ("BigQuery: the read is billed through the Storage Read API (per TiB read): rate to fill in, not included here." if src.dialect == "bigquery" else None),
        "rework_days": prices.rework_days(n_objects, len(risky)),
        "rework_basis": f"{n_objects} object(s) to rebuild and {len(risky)} column(s) with a platform-specific type; q4o assumptions ({prices.REWORK_DAYS_PER_OBJECT[0]} to {prices.REWORK_DAYS_PER_OBJECT[1]} day per object, {prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[0]} to {prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[1]} per column), open to discussion.",
        "not_included": "Your contract rates, target storage, running both platforms in parallel, operating cost after the exit: they depend on your contract and your architecture, and are not estimated here.",
    }
    return {
        "source": src.describe(),
        "tables": [{"name": t.fqn, "rows": t.rows, "bytes": t.bytes, "columns": len(t.columns)} for t in tables],
        "totals": {"tables": len(tables), "rows": total_rows, "bytes": total_bytes, "gb": round(gb, 3)},
        "non_portable_columns": risky,
        "objects_that_do_not_move": objs,
        "object_counts": movable,
        "usage_90d": src.usage_90d(),
        "exit_estimate": est,
        "exit_cost": exit_cost,
        "egress_note": EGRESS_NOTES.get(src.dialect, ""),
    }
