"""The same demo must give the same report on every operating system (and with or without pandas installed).

Timings, throughput and paths legitimately differ, so they are left out: what is compared is every measured fact
(row counts, statistic differences, fingerprint results, proof levels, query statuses and details, conversions).
Regenerate with CANOT_REGEN_GOLDEN=1 after a deliberate change. CI runs this on Linux, macOS and Windows.
"""
import json
import os
from pathlib import Path

from canot.cli import main

GOLDEN = Path(__file__).parent / "golden" / "demo_report.json"
REGEN = os.environ.get("CANOT_REGEN_GOLDEN") == "1"
VOLATILE = {"throughput_mb_s", "rto", "duration_s", "warehouse_credits_used_est", "cost_usd_est", "cost_basis"}


def canonical(rep: dict) -> dict:
    s = {k: v for k, v in rep["summary"].items() if k not in VOLATILE}
    checks = [{k: c[k] for k in ("table", "rows_source", "rows_target", "schema_ok", "stats_ok", "stats_diffs", "fingerprint_ok",
                                 "bad_buckets", "precision_cols", "within_precision_cols", "proof_level", "ok")} for c in rep["checks"]]
    queries = [{k: q.get(k) for k in ("sql", "translated", "status", "detail", "rows_source", "rows_target", "order_differs")} for q in rep["queries"]]
    return {"summary": s, "checks": checks, "not_compared": rep["not_compared"], "queries": queries, "conversions": rep["conversions"]}


def test_demo_report_is_the_same_on_every_platform(tmp_path):
    out = tmp_path / "demo"
    main(["demo", "--sf", "0.01", "--no-open", "--out", str(out), "--lang", "en"])
    got = canonical(json.loads((out / "evidence" / "report.json").read_text(encoding="utf-8")))
    if REGEN:
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8"))
