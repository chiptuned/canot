from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__


def _p(*a):
    print(*a, flush=True)


def explain(exc: BaseException, argv: list[str]) -> str | None:
    """One clear sentence for the errors every first-time user hits (missing connector, missing credentials)."""
    from .connectors import driver_hints

    name = type(exc).__name__
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        for prefix, (label, extra) in {"mcp": ("MCP", "mcp"), **driver_hints()}.items():
            if exc.name.startswith(prefix):
                return (f"The {label} connector is missing. Run: uvx --from \"canot[{extra}]\" canot {' '.join(argv)}\n"
                        f"or install it: pip install 'canot[{extra}]'  (or 'canot[all]' for all three).")
    if name == "DefaultCredentialsError":
        return "Google credentials not found. Run: gcloud auth application-default login"
    if name in ("Forbidden", "PermissionDenied") or "403" in str(exc)[:40]:
        return "Access denied. Check the read-only roles the source requires (see the source documentation)."
    from .connectors import NoConnector

    if isinstance(exc, NoConnector):
        return str(exc)
    from .dbt_drill import DbtProfileError

    if isinstance(exc, DbtProfileError):
        return str(exc)
    if isinstance(exc, ValueError) and ("SNOWFLAKE_" in str(exc) or "DATABRICKS_" in str(exc) or "URL " in str(exc)):
        return str(exc)
    return None


def main(argv: list[str] | None = None) -> int:
    import warnings

    warnings.filterwarnings("ignore", message=".*end user credentials.*")
    argv_list = list(sys.argv[1:] if argv is None else argv)
    debug = "--debug" in argv_list
    argv_list = [a for a in argv_list if a != "--debug"]
    try:
        return run(argv_list)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 : one clear sentence rather than a traceback
        if debug:
            raise
        msg = explain(e, argv_list) or f"{type(e).__name__}: {str(e).splitlines()[0][:300] if str(e) else ''}"
        print(f"Error: {msg}\n(add --debug for the full traceback)", file=sys.stderr)
        return 1


LANG_HELP = ("language shown first in report.html: en or fr. Both are always in the file (switch at the top of the page). "
             "Default: your locale (fr* gives French), else English")
KEY_HELP = "signing key (default: the CANOT_EVIDENCE_KEY_PATH variable, otherwise ~/.canot/evidence.key)"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="canot", description="Prove you can leave.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="read-only X-ray: inventory, lock-in, estimated exit cost")
    s.add_argument("source", nargs="?", help="the source: bigquery://PROJECT/dataset, snowflake://ACCOUNT/DATABASE, a .duckdb file, dbt://PROJECT_PATH")
    s.add_argument("--from", dest="src", help="same as the source argument")
    s.add_argument("--out", default="canot-out")
    s.add_argument("--credit-price", type=float, default=None, help="Snowflake credit price in your contract, in USD. Without it: list-price range (see canot/prices.py)")
    s.add_argument("--level", type=int, choices=(1, 2, 3), default=3, help="how far to read: 1 inventory and consumption; 2 + queries (normalized forms) and reads per table; 3 + grants and tools (pseudonymized). What your grants do not allow is logged as \"not read\"")
    s.add_argument("--include-sql-text", action="store_true", help="also write the raw SQL text to scan-sql-raw.json (mode 0600). By default it is never written: only normalized forms are")
    s.add_argument("--lang", choices=("en", "fr"), default=None, help="language shown first in scan.html, which always holds English and French (default: your locale, else English). scan.json and the CSV files stay English")

    d = sub.add_parser("drill", help="full exit exercise: copy, comparison, evidence report")
    d.add_argument("source", nargs="?", help="the source: bigquery://PROJECT/dataset, snowflake://ACCOUNT/DATABASE, a .duckdb file, "
                   "or dbt://PROJECT_PATH (copies the tables the dbt project uses, then replays its models)")
    d.add_argument("dest", nargs="?", help="the destination: a local folder (./iceberg), s3://..., or scaleway:fr-par, ovh:gra, outscale:eu-west-2, ionos:de..., with --bucket")
    d.add_argument("--from", dest="src", help="same as the source argument")
    d.add_argument("--to", dest="dst", help="same as the dest argument")
    d.add_argument("--bucket")
    d.add_argument("--catalog", help="Iceberg catalog: SQLAlchemy URI (postgresql://… with canot[postgres]) or env:VARIABLE_NAME. Default: a SQLite file in the output folder")
    d.add_argument("--out", default="canot-out")
    d.add_argument("--queries", help="optional: SQL file to replay. By default, the most expensive queries in the platform history.")
    d.add_argument("--top", type=int, default=20)
    d.add_argument("--tolerances", help="TOML file (see `canot init-tolerances`)")
    d.add_argument("--tables", nargs="*", help="limit to these tables (SCHEMA.TABLE)")
    d.add_argument("--key", default=os.environ.get("CANOT_EVIDENCE_KEY_PATH", str(Path.home() / ".canot" / "evidence.key")), help=KEY_HELP)
    d.add_argument("--credit-price", type=float, default=None, help="Snowflake credit price in your contract, in USD (default: list-price range)")
    d.add_argument("--replay-max-gb", type=float, default=2.0, help="maximum size of a table loaded in memory to replay queries")
    d.add_argument("--profiles-dir", help="with dbt://: the folder that holds profiles.yml (default: DBT_PROFILES_DIR, else the project folder, else ~/.dbt)")
    d.add_argument("--target", help="with dbt://: the profile target to read (default: ?target=NAME in the URL, else the profile's default target)")
    d.add_argument("--lang", choices=("en", "fr"), default=None, help=LANG_HELP)

    t = sub.add_parser("teardown", help="delete the copied data")
    t.add_argument("--to", dest="dst", required=True)
    t.add_argument("--bucket")
    t.add_argument("--catalog", help="the same catalog as for drill")
    t.add_argument("--out", default="canot-out")

    v = sub.add_parser("verify-evidence", help="verify the chain and signature of an evidence folder")
    v.add_argument("dir")
    v.add_argument("--expect-pub", help=".pub file of the expected key: rejects a folder signed by another key")

    ss = sub.add_parser("snowflake-setup", help="print the read-only access SQL script, to review")
    ss.add_argument("--generate-key", action="store_true", help="create the key pair here and put the public key in the script")
    ss.add_argument("--base", default="<DATABASE>", help="Snowflake database to copy")
    sub.add_parser("mcp", help="MCP server (stdio): the documentation and explanations of your reports, for an agent")
    bs = sub.add_parser("bigquery-setup", help="print the read-only access gcloud script (custom role), to review")
    bs.add_argument("--project", help="project to scan")
    bs.add_argument("--member", help="user:... or serviceAccount:... that receives the role")
    dm = sub.add_parser("demo", help="full 30-second demo, no account needed: TPC-H dataset, exit exercise, evidence report")
    dm.add_argument("dest", nargs="?", help="where to write the copy (as for drill: ./iceberg, s3://…, scaleway:fr-par, aws:eu-west-3…). Default: OUT/iceberg")
    dm.add_argument("--bucket", help="required with a provider shortcut (see canot drill)")
    dm.add_argument("--catalog", help="Iceberg catalog (see canot drill)")
    dm.add_argument("--out", default="canot-demo")
    dm.add_argument("--sf", type=float, default=0.1, help="TPC-H size: 0.1 = 100 MB (default), 1 = about 1 GB (6 million order lines)")
    dm.add_argument("--rows", type=int, default=50_000, help=argparse.SUPPRESS)  # offline fallback: fictional dataset
    dm.add_argument("--no-open", action="store_true", help="do not open the report in the browser")
    dm.add_argument("--lang", choices=("en", "fr"), default=None, help=LANG_HELP)
    it = sub.add_parser("init-tolerances", help="write a tolerances file, to review with the client")
    it.add_argument("path", nargs="?", default="tolerances.toml")
    dd = sub.add_parser("demo-data", help="create a demo DuckDB database")
    dd.add_argument("path")
    dd.add_argument("--rows", type=int, default=20_000)
    dd.add_argument("--retail", action="store_true", help="fictional retailer dataset (receipts with nested lines)")
    dd.add_argument("--tpch", action="store_true", help="TPC-H dataset, the reference benchmark (generated locally)")
    dd.add_argument("--sf", type=float, default=0.1, help="with --tpch: size (0.1 = 100 MB, 1 = about 1 GB)")
    sub.add_parser("connectors", help="known platforms: inventory, export, evidence, validation status")
    rd = sub.add_parser("replay-dbt", help="P3 evidence: replay the models of a dbt project in DuckDB on the copy and compare them with the original tables")
    rd.add_argument("project", help="dbt://PROJECT_PATH (folder that holds target/manifest.json, compiled SQL included)")
    rd.add_argument("--base", help="DuckDB database copy: source tables and the models' original tables")
    rd.add_argument("--iceberg", dest="lac", help="or the Iceberg copy written by canot drill (same DESTINATION as for drill)")
    rd.add_argument("--drill-out", help="with --iceberg: the drill --out folder, which holds its catalog (default canot-out)")
    rd.add_argument("--catalog", help="with --iceberg: the same catalog as for drill")
    rd.add_argument("--out", default="canot-replay-dbt")
    rd.add_argument("--tolerances", help="TOML file (see `canot init-tolerances`)")
    rd.add_argument("--key", default=os.environ.get("CANOT_EVIDENCE_KEY_PATH", str(Path.home() / ".canot" / "evidence.key")), help=KEY_HELP)
    return ap


def run(argv: list[str] | None = None) -> int:
    ap = parser()
    a = ap.parse_args(argv)
    if a.cmd in ("scan", "drill"):
        a.src = a.src or a.source
        if not a.src:
            ap.error("specify the source: canot " + a.cmd + (" SOURCE DESTINATION" if a.cmd == "drill" else " SOURCE"))
        if a.cmd == "drill":
            a.dst = a.dst or a.dest
            if not a.dst:
                ap.error("specify the destination: canot drill SOURCE DESTINATION (a local folder is enough: ./iceberg)")
    out = Path(getattr(a, "out", "canot-out"))

    if a.cmd == "snowflake-setup":
        from .sources.snowflake_src import PUBLIC_KEY_PLACEHOLDER, SETUP_SQL

        sql = SETUP_SQL.replace("<DATABASE>", a.base)
        if a.generate_key:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            kp = Path.home() / ".canot" / "snowflake_rsa_key.p8"
            if kp.exists():
                key = serialization.load_pem_private_key(kp.read_bytes(), password=None)
            else:
                kp.parent.mkdir(parents=True, exist_ok=True)
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                os.chmod(kp, 0o600)
            pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            pub = "".join(l for l in pub.splitlines() if not l.startswith("-----"))
            sql = sql.replace(PUBLIC_KEY_PLACEHOLDER, pub)
            print(f"-- Private key: {kp} (stays on this machine). Then: export SNOWFLAKE_PRIVATE_KEY_PATH={kp}", file=sys.stderr)
        _p(sql)
        return 0
    if a.cmd == "connectors":
        from .connectors import table

        _p(table())
        return 0
    if a.cmd == "mcp":
        from .agent import serve

        serve()
        return 0
    if a.cmd == "bigquery-setup":
        from .sources.bigquery_src import setup_script

        _p(setup_script(a.project, a.member))
        return 0
    if a.cmd == "demo":
        import webbrowser

        from .demo import RETAIL_QUERIES, TPCH_QUERIES, make_retail, make_tpch
        from .drill import run_drill
        from .report import write_report
        from .source_loader import open_demo
        from .target import parse_target
        from .tolerances import Tolerances

        work = Path(a.out)
        work.mkdir(parents=True, exist_ok=True)
        db, qf, evidence = work / "demo.duckdb", work / "queries.sql", work / "evidence"
        dest = a.dest or str(work / "iceberg")
        bucket = f" --bucket {a.bucket}" if a.bucket else ""
        _p("canot demo is a shortcut for two commands, which you can run and change yourself:")
        _p(f"  canot demo-data {db} --tpch --sf {a.sf:g}")
        _p(f"  canot drill {db} {dest}{bucket} --queries {qf} --out {evidence}")
        _p("Change the destination to run the same demo elsewhere: canot demo aws:eu-west-3 --bucket MY-BUCKET\n")
        if db.exists():
            db.unlink()
        target = parse_target(dest, a.bucket, evidence, a.catalog)  # reject an incomplete destination right away
        try:
            size = f"{a.sf:g} GB" if a.sf >= 1 else f"{round(a.sf * 1000)} MB"
            _p(f"TPC-H: the reference dataset of database benchmarks (8 tables, 22 queries, 4 of them replayed here), generated locally: about {size} of data, no real data.")
            _p("Scale factor 1 (about 1 GB): canot demo --sf 1\n" if a.sf < 1 else "")
            make_tpch(str(db), a.sf)
            queries = TPCH_QUERIES
        except Exception as e:  # noqa: BLE001 : the tpch extension downloads on first use; offline, fallback dataset
            _p(f"TPC-H unavailable ({type(e).__name__}): fallback fictional dataset, generated offline\n")
            if db.exists():
                db.unlink()
            make_retail(str(db), a.rows)
            queries = RETAIL_QUERIES
        qf.write_text(queries)
        src = open_demo(str(db))
        try:
            res = run_drill(src, target, evidence, Path.home() / ".canot" / "evidence.key", str(qf), Tolerances(), log=_p)
        finally:
            src.close()
        rep = write_report(res, evidence, a.lang)
        s = res["summary"]
        _p(f"\n{'Full parity' if s['parity'] == 'complete' else 'Mismatches found'}: {s['tables_ok']}/{s['tables_total']} tables, {s['queries_match']}/{s['queries_total']} queries.")
        _p(f"Evidence report: {rep}")
        _p("Check that it has not been modified: canot verify-evidence " + str(evidence))
        _p("\nNext, on your data (read-only):  canot scan \"snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER\"")
        _p("Need help running it on your scope? https://canot.io/#contact")
        if not a.no_open:
            webbrowser.open(rep.resolve().as_uri())
        return 0
    if a.cmd == "init-tolerances":
        from .tolerances import DEFAULT_TOML

        Path(a.path).write_text(DEFAULT_TOML)
        _p(f"written: {a.path}")
        return 0
    if a.cmd == "demo-data":
        from .demo import DEMO_QUERIES, RETAIL_QUERIES, TPCH_QUERIES, make_demo, make_retail, make_tpch

        if a.tpch:
            make_tpch(a.path, a.sf)
        elif a.retail:
            make_retail(a.path, a.rows)
        else:
            make_demo(a.path, a.rows)
        q = Path(a.path).with_suffix(".queries.sql")
        q.write_text(TPCH_QUERIES if a.tpch else RETAIL_QUERIES if a.retail else DEMO_QUERIES)
        _p(f"written: {a.path} and {q}")
        return 0
    if a.cmd == "verify-evidence":
        from .evidence import verify_dir

        ok, msg = verify_dir(Path(a.dir), Path(a.expect_pub) if a.expect_pub else None)
        _p(("OK: " if ok else "FAILED: ") + msg)
        return 0 if ok else 2

    if a.cmd == "replay-dbt":
        from .dbt_rejeu import REPORT_FILE, run_rejeu
        from .tolerances import Tolerances

        res = run_rejeu(a.project, out, Path(a.key), base=a.base, lac=a.lac, drill_out=a.drill_out, catalog=a.catalog,
                        tol=Tolerances.load(a.tolerances), log=_p)
        s = res["summary"]
        _p(f"\ndbt replay (P3): {s['identique']} identical, {s['different']} different, {s['non_compare']} replayed but not compared, "
           f"{s['non_rejouable']} not replayable, out of {s['modeles']} model(s)")
        _p(f"Evidence: {out / REPORT_FILE}; verify: canot verify-evidence {out}")
        return 0 if s["different"] == 0 else 1
    if a.cmd == "teardown":
        from .target import parse_target

        _p(parse_target(a.dst, a.bucket, out, a.catalog).teardown())
        return 0

    from .connectors import effective_level, for_url, open_url

    conn = for_url(a.src.split("?", 1)[0])
    if a.cmd == "drill" and conn is not None and conn.name == "dbt":
        from .dbt_drill import resume, run_dbt_drill
        from .tolerances import Tolerances

        url = a.src if "://" in a.src else "dbt://" + a.src
        r = run_dbt_drill(url, a.dst, out, Path(a.key), profiles_dir=a.profiles_dir, target=a.target, bucket=a.bucket, catalog=a.catalog,
                          queries=a.queries, top=a.top, only=a.tables, tol=Tolerances.load(a.tolerances), credit_price=a.credit_price,
                          replay_max_gb=a.replay_max_gb, log=_p, lang=a.lang)
        for line in resume(r, out):
            _p(line)
        return 1 if r["dbt"]["verdict"] == "mismatch" else 0
    if a.cmd == "drill" and conn is not None and conn.famille == "fichiers":
        raise ValueError(f"URL {conn.label}: drill is not available for these project files; the tables live on the platform "
                         "that runs them: canot drill on that platform copies and compares them.")
    if a.cmd == "drill" and (a.profiles_dir or a.target):
        ap.error("--profiles-dir and --target apply to a dbt project only (canot drill dbt://PROJECT DESTINATION)")
    src = open_url(a.src)
    try:
        from .connectors import supports_full

        if a.cmd == "scan" and supports_full(src):
            from collections import Counter

            from .scanner.run import LEVELS, scan_full

            level = effective_level(src, a.level)
            if a.include_sql_text and conn is not None and conn.famille == "fichiers":
                _p("--include-sql-text does not apply here: the code is already in the project files; nothing more is written.")
                a.include_sql_text = False
            _p(f"Canot read-only scan (level {level}: {LEVELS[level]})")
            res = scan_full(src, out, level=level, credit_price=a.credit_price, include_sql_text=a.include_sql_text, lang=a.lang, log=_p)
            _p(f"Source: {res['perimetre']}")
            for role, lines in res["resume"].items():
                for line in lines:
                    _p(f"  {role}: {line}")
            codes = Counter(f["code"] for f in res["drapeaux"])
            if codes:
                _p("To review: " + ", ".join(f"{c} ×{n}" for c, n in sorted(codes.items())))
            nl = [l for l in res["lectures"] if l["statut"] == "not_read"]
            _p(f"Reads: {len(res['lectures']) - len(nl)} done, {len(nl)} not read (details and reasons in the report)")
            _p(f"Report to share: {out / 'scan.html'}")
            _p(f"Data: {out / 'scan.json'} and the CSV files in the same folder")
            _p(f"To send it to us: {out / 'scan-share.json'} (counts only: no table name, no SQL, no identity) at https://canot.io/offres.html#demarrer")
            if conn is not None and conn.name == "dbt":
                _p("Next step: canot drill dbt://PROJECT_PATH DESTINATION copies the tables this project uses to Apache Iceberg, "
                   "compares them and replays the models on the copy (read-only, with the credentials of your dbt profile).")
            if a.include_sql_text:
                _p(f"WARNING: {out / 'scan-sql-raw.json'} holds the raw SQL text of your history. Do not share it.")
            _p("Need help reading this report or planning the next step? https://canot.io/#contact")
            return 0
        if a.cmd == "scan":
            from .scan import scan

            res = scan(src, a.credit_price)
            out.mkdir(parents=True, exist_ok=True)
            (out / "scan.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
            tt = res["totals"]
            _p(f"Source: {res['source']}")
            _p(f"{tt['tables']} tables, {tt['rows']} rows, {tt['gb']} GB")
            e = res["exit_estimate"]
            _p(f"Estimated exit: {e['duration_hours'][0]} to {e['duration_hours'][1]} h of copy ({e['assumed_throughput_mb_s'][0]}-{e['assumed_throughput_mb_s'][1]} MB/s, est.)")
            if "cost_usd" in e:
                _p(f"Estimated read cost: {e['cost_usd'][0]} to {e['cost_usd'][1]} USD ({e['credits'][0]}-{e['credits'][1]} credits)")
                _p(f"  basis: {e['cost_basis']}")
            ec = res["exit_cost"]
            _p(f"Estimated rework: {ec['rework_days'][0]} to {ec['rework_days'][1]} days ({ec['rework_basis']})")
            _p(f"Not included: {ec['not_included']}")
            if res["egress_note"]:
                _p(res["egress_note"])
            if res["usage_90d"]:
                _p(f"90-day consumption: {res['usage_90d']}")
            _p(f"Columns with a platform-specific type: {len(res['non_portable_columns'])}")
            for k, n in res["object_counts"].items():
                if n:
                    _p(f"  {k} to rebuild: {n}")
            _p(f"Details: {out / 'scan.json'}")
            return 0

        if a.cmd == "drill":
            from .drill import run_drill
            from .report import write_report
            from .target import parse_target
            from .tolerances import Tolerances

            target = parse_target(a.dst, a.bucket, out, a.catalog)
            res = run_drill(src, target, out, Path(a.key), a.queries, Tolerances.load(a.tolerances), a.top, a.tables, a.credit_price, a.replay_max_gb, _p)
            rep = write_report(res, out, a.lang)
            s = res["summary"]
            _p(f"\nParity: {s['parity']} — {s['tables_ok']}/{s['tables_total']} tables, {s['queries_match']}/{s['queries_total']} queries")
            _p(f"Evidence report: {rep}")
            return 0 if s["parity"] == "complete" else 1
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
