from __future__ import annotations

from . import prices
from .convert import target_schema
from .sources.base import Source

EGRESS_NOTES = {
    "snowflake": "Lecture par le connecteur : pas de frais de sortie Snowflake, seulement les crédits de l'entrepôt qui lit.",
    "bigquery": "BigQuery : la lecture facture l'API Storage Read (par Tio lu) et la sortie réseau vers l'extérieur de Google Cloud ; "
                "le programme de sortie de Google peut les créditer sous conditions (à confirmer sur cloud.google.com/exit-cloud). Tarifs à renseigner.",
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
        "note": "estimation de planification ; remplacée par la mesure après un `canot drill`",
    }
    if src.dialect == "snowflake":
        lo, basis = prices.snowflake_cost(est["credits"][0], credit_price)
        hi, _ = prices.snowflake_cost(est["credits"][1], credit_price)
        est["cost_usd"] = [lo[0], hi[1]]
        est["cost_basis"] = basis
    n_objects = sum(len(v) for v in objs.values())
    exit_cost = {
        "reading_usd": est.get("cost_usd"),
        "reading_basis": est.get("cost_basis") or ("BigQuery : la lecture facture l'API Storage Read (par Tio lu) : tarif à renseigner, non inclus ici." if src.dialect == "bigquery" else None),
        "rework_days": prices.rework_days(n_objects, len(risky)),
        "rework_basis": f"{n_objects} objet(s) à reconstruire et {len(risky)} colonne(s) à type propre à la plateforme ; hypothèses de q4o ({prices.REWORK_DAYS_PER_OBJECT[0]} à {prices.REWORK_DAYS_PER_OBJECT[1]} jour par objet, {prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[0]} à {prices.REWORK_DAYS_PER_NONPORTABLE_COLUMN[1]} par colonne), à discuter.",
        "not_included": "Tarif de votre contrat, stockage d'arrivée, double exploitation, coût de fonctionnement après la sortie : ils dépendent de votre contrat et de votre architecture, et ne sont pas estimés ici.",
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
