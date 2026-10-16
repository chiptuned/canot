from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _p(*a):
    print(*a, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="canot", description="Prouvez que vous pouvez partir.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="radiographie en lecture seule : inventaire, enfermement, coût de sortie estimé")
    s.add_argument("--from", dest="src", required=True)
    s.add_argument("--out", default="canot-out")
    s.add_argument("--credit-price", type=float, default=3.90, help="prix du crédit Snowflake (est. Enterprise, AWS Paris)")

    d = sub.add_parser("drill", help="exercice de sortie complet : copie, comparaison, dossier de preuve")
    d.add_argument("--from", dest="src", required=True)
    d.add_argument("--to", dest="dst", required=True, help="dossier local, s3://..., ou scaleway:fr-par, ovh:gra, outscale:eu-west-2, ionos:de..., avec --bucket")
    d.add_argument("--bucket")
    d.add_argument("--out", default="canot-out")
    d.add_argument("--queries", help="fichier SQL (requêtes séparées par ;). Sinon, les plus coûteuses de l'historique.")
    d.add_argument("--top", type=int, default=20)
    d.add_argument("--tolerances", help="fichier TOML (voir `canot init-tolerances`)")
    d.add_argument("--tables", nargs="*", help="limiter à ces tables (SCHEMA.TABLE)")
    d.add_argument("--key", default=str(Path.home() / ".canot" / "evidence.key"))
    d.add_argument("--credit-price", type=float, default=3.90)

    t = sub.add_parser("teardown", help="supprime les données copiées")
    t.add_argument("--to", dest="dst", required=True)
    t.add_argument("--bucket")
    t.add_argument("--out", default="canot-out")

    v = sub.add_parser("verify-evidence", help="vérifie la chaîne et la signature d'un dossier")
    v.add_argument("dir")

    sub.add_parser("snowflake-setup", help="affiche le script SQL d'accès en lecture seule à relire")
    it = sub.add_parser("init-tolerances", help="écrit un fichier de tolérances à relire avec le client")
    it.add_argument("path", nargs="?", default="tolerances.toml")
    dd = sub.add_parser("demo-data", help="crée une base DuckDB de démonstration")
    dd.add_argument("path")
    dd.add_argument("--rows", type=int, default=20_000)

    a = ap.parse_args(argv)
    out = Path(getattr(a, "out", "canot-out"))

    if a.cmd == "snowflake-setup":
        from .sources.snowflake_src import SETUP_SQL

        _p(SETUP_SQL)
        return 0
    if a.cmd == "init-tolerances":
        from .tolerances import DEFAULT_TOML

        Path(a.path).write_text(DEFAULT_TOML)
        _p(f"écrit : {a.path}")
        return 0
    if a.cmd == "demo-data":
        from .demo import DEMO_QUERIES, make_demo

        make_demo(a.path, a.rows)
        q = Path(a.path).with_suffix(".queries.sql")
        q.write_text(DEMO_QUERIES)
        _p(f"écrit : {a.path} et {q}")
        return 0
    if a.cmd == "verify-evidence":
        from .evidence import verify_dir

        ok, msg = verify_dir(Path(a.dir))
        _p(("OK : " if ok else "ÉCHEC : ") + msg)
        return 0 if ok else 2

    if a.cmd == "teardown":
        from .target import parse_target

        _p(parse_target(a.dst, a.bucket, out).teardown())
        return 0

    from .sources import open_source

    src = open_source(a.src)
    try:
        if a.cmd == "scan":
            from .scan import scan

            res = scan(src, a.credit_price)
            out.mkdir(parents=True, exist_ok=True)
            (out / "scan.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
            tt = res["totals"]
            _p(f"Source : {res['source']}")
            _p(f"{tt['tables']} tables, {tt['rows']} lignes, {tt['gb']} Go")
            e = res["exit_estimate"]
            _p(f"Sortie estimée : {e['duration_hours'][0]} à {e['duration_hours'][1]} h de copie ({e['assumed_throughput_mb_s'][0]}-{e['assumed_throughput_mb_s'][1]} Mo/s, est.)")
            if "cost_usd" in e:
                _p(f"Coût de lecture estimé : {e['cost_usd'][0]} à {e['cost_usd'][1]} $ ({e['credits'][0]}-{e['credits'][1]} crédits)")
            if res["egress_note"]:
                _p(res["egress_note"])
            if res["usage_90d"]:
                _p(f"Consommation 90 jours : {res['usage_90d']}")
            _p(f"Colonnes à type propre à la plateforme : {len(res['non_portable_columns'])}")
            for k, n in res["object_counts"].items():
                if n:
                    _p(f"  {k} à reconstruire : {n}")
            _p(f"Détail : {out / 'scan.json'}")
            return 0

        if a.cmd == "drill":
            from .drill import run_drill
            from .report import write_report
            from .target import parse_target
            from .tolerances import Tolerances

            target = parse_target(a.dst, a.bucket, out)
            res = run_drill(src, target, out, Path(a.key), a.queries, Tolerances.load(a.tolerances), a.top, a.tables, a.credit_price, _p)
            rep = write_report(res, out)
            s = res["summary"]
            _p(f"\nParité : {s['parity']} — {s['tables_ok']}/{s['tables_total']} tables, {s['queries_match']}/{s['queries_total']} requêtes")
            _p(f"Dossier : {rep}")
            return 0 if s["parity"] == "complète" else 1
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
