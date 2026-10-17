from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

import duckdb
import pyarrow
import pyiceberg
import sqlglot

from . import __version__
from .copy import copy_table
from .evidence import EvidenceLog
from .scan import scan as run_scan
from .sources.base import Source
from .target import Target
from .tolerances import Tolerances
from .verify import check_table, replay_queries, split_sql


def run_drill(src: Source, target: Target, out: Path, key_path: Path, queries_file: str | None,
              tol: Tolerances, top_n: int = 20, only: list[str] | None = None,
              credit_price: float | None = 3.90, replay_max_gb: float = 2.0, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    ev = EvidenceLog(out)
    t_start = time.monotonic()
    env = {"canot": __version__, "duckdb": duckdb.__version__, "pyarrow": pyarrow.__version__,
           "pyiceberg": pyiceberg.__version__, "sqlglot": sqlglot.__version__}
    ev.add("start", {"source": src.describe(), "target": target.label, "env": env, "tolerances": tol.to_dict()})

    log("→ inventaire")
    scan_res = run_scan(src, credit_price)
    ev.add("scan", {"totals": scan_res["totals"], "objects": scan_res["object_counts"]})
    tables = [t for t in src.tables() if not only or t.fqn.lower() in {o.lower() for o in only}]

    cat = target.catalog()
    log(f"→ copie de {len(tables)} table(s) vers {target.label}")
    copies = {}
    conversions = []
    for t in tables:
        r = copy_table(src, t, cat)
        copies[t.fqn] = r
        conversions += r.conversions
        mb = r.bytes / 1e6
        log(f"   {t.fqn}: {r.rows} lignes, {mb:.1f} Mo, {r.seconds:.1f}s" + (f"  ERREUR {r.error}" if r.error else ""))
        ev.add("copy", {"table": t.fqn, "rows": r.rows, "bytes": r.bytes, "seconds": round(r.seconds, 3), "error": r.error,
                        "fingerprint": r.fingerprint})
    copy_seconds = sum(r.seconds for r in copies.values())
    copy_bytes = sum(r.bytes for r in copies.values())

    log("→ comparaison des tables")
    checks = []
    for t in tables:
        r = copies[t.fqn]
        if r.error:
            continue
        c = check_table(src, t, cat.load_table((t.schema, t.name)), r.fingerprint, tol, {cv.column for cv in r.conversions if cv.lossy})
        checks.append(c)
        log(f"   {t.fqn}: {'OK' if c.ok else 'ÉCART'}")
        ev.add("table_check", asdict(c) | {"ok": c.ok})

    queries = split_sql(Path(queries_file).read_text()) if queries_file else src.history_queries(top_n)
    qres = []
    if queries:
        log(f"→ rejeu de {len(queries)} requête(s) des deux côtés")
        qres = replay_queries(src, queries[:top_n], cat, [t for t in tables if not copies[t.fqn].error], tol,
                              {k: v.bytes for k, v in copies.items()}, int(replay_max_gb * 1e9))
        for q in qres:
            log(f"   [{q.status}] {q.sql[:70].replace(chr(10), ' ')}")
            ev.add("query", asdict(q))
    else:
        log("→ aucune requête fournie (--queries) ni trouvée dans l'historique : rejeu sauté")

    mbps = (copy_bytes / 1e6) / copy_seconds if copy_seconds > 0 else None
    total_bytes = scan_res["totals"]["bytes"] or copy_bytes
    rto = None
    if mbps:
        center = total_bytes / 1e6 / mbps
        rto = {"seconds": [round(center * 0.7, 1), round(center * 1.5, 1)], "basis": "débit mesuré ±, hors validation humaine et reprise des applications"}
    credits = round(copy_seconds / 3600 * src.credits_per_hour, 4) if src.dialect == "snowflake" else None
    summary = {
        "tables_total": len(tables),
        "tables_ok": sum(1 for c in checks if c.ok),
        "tables_error": [t for t, r in copies.items() if r.error],
        "queries_total": len(qres),
        "queries_match": sum(1 for q in qres if q.status == "match"),
        "queries_mismatch": sum(1 for q in qres if q.status == "mismatch"),
        "queries_untranslatable": sum(1 for q in qres if q.status in ("untranslatable", "error")),
        "queries_skipped": sum(1 for q in qres if q.status == "skipped"),
        "tables_precision_only": sum(1 for c in checks if c.precision_only),
        "queries_order_differs": sum(1 for q in qres if q.order_differs),
        "lossy_conversions": sum(1 for c in conversions if c.lossy),
        "throughput_mb_s": round(mbps, 2) if mbps else None,
        "rto": rto,
        "warehouse_credits_used_est": credits,
        "cost_usd_est": round(credits * credit_price, 2) if credits is not None and credit_price else None,
        "duration_s": round(time.monotonic() - t_start, 1),
    }
    summary["parity"] = ("complète" if summary["tables_ok"] == summary["tables_total"] and not summary["tables_error"]
                         and not summary["queries_mismatch"] else "écarts")
    ev.add("summary", summary)
    seal = ev.seal(key_path)
    return {"summary": summary, "scan": scan_res, "checks": [asdict(c) | {"ok": c.ok} for c in checks],
            "queries": [asdict(q) for q in qres], "conversions": [asdict(c) for c in conversions],
            "env": env, "seal": seal, "source": src.describe(), "target": target.label, "tolerances": tol.to_dict()}
