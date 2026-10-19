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
    """Une phrase claire pour les erreurs que tout premier utilisateur rencontre (connecteur absent, identifiants absents)."""
    from .connectors import driver_hints

    name = type(exc).__name__
    if isinstance(exc, ModuleNotFoundError) and exc.name:
        for prefix, (label, extra) in {"mcp": ("MCP", "mcp"), **driver_hints()}.items():
            if exc.name.startswith(prefix):
                return (f"Il manque le connecteur {label}. Lancez : uvx --from \"canot[{extra}]\" canot {' '.join(argv)}\n"
                        f"ou installez-le : pip install 'canot[{extra}]'  (ou 'canot[all]' pour les trois).")
    if name == "DefaultCredentialsError":
        return "Identifiants Google introuvables. Lancez : gcloud auth application-default login"
    if name in ("Forbidden", "PermissionDenied") or "403" in str(exc)[:40]:
        return "Accès refusé. Vérifiez les rôles en lecture seule demandés (voir la documentation de la source)."
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
        print("Interrompu.", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 : une phrase claire plutôt qu'une trace
        if debug:
            raise
        msg = explain(e, argv_list) or f"{type(e).__name__} : {str(e).splitlines()[0][:300] if str(e) else ''}"
        print(f"Erreur : {msg}\n(ajoutez --debug pour la trace complète)", file=sys.stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="canot", description="Prouvez que vous pouvez partir.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="radiographie en lecture seule : inventaire, enfermement, coût de sortie estimé")
    s.add_argument("source", nargs="?", help="la source : bigquery://PROJET/dataset, snowflake://COMPTE/BASE, un fichier .duckdb, dbt://CHEMIN_DU_PROJET")
    s.add_argument("--from", dest="src", help="équivalent de l'argument source")
    s.add_argument("--out", default="canot-out")
    s.add_argument("--credit-price", type=float, default=None, help="prix du crédit Snowflake de votre contrat, en USD. Sans lui : fourchette du prix catalogue (voir canot/prices.py)")
    s.add_argument("--level", type=int, choices=(1, 2, 3), default=3, help="jusqu'où lire : 1 inventaire et consommation ; 2 + requêtes (formes normalisées) et lectures par table ; 3 + droits et outils (pseudonymisés). Ce que vos droits ne permettent pas est consigné « non lu »")
    s.add_argument("--include-sql-text", action="store_true", help="écrit aussi le texte SQL brut dans scan-sql-brut.json (droits 0600). Par défaut il n'est jamais écrit : seules les formes normalisées le sont")

    d = sub.add_parser("drill", help="exercice de sortie complet : copie, comparaison, dossier de preuve")
    d.add_argument("source", nargs="?", help="la source : bigquery://PROJET/dataset, snowflake://COMPTE/BASE, un fichier .duckdb")
    d.add_argument("dest", nargs="?", help="la destination : un dossier local (./iceberg), s3://..., ou scaleway:fr-par, ovh:gra, outscale:eu-west-2, ionos:de..., avec --bucket")
    d.add_argument("--from", dest="src", help="équivalent de l'argument source")
    d.add_argument("--to", dest="dst", help="équivalent de l'argument dest")
    d.add_argument("--bucket")
    d.add_argument("--catalog", help="catalogue Iceberg : URI SQLAlchemy (postgresql://… avec canot[postgres]) ou env:NOM_DE_VARIABLE. Défaut : un fichier SQLite dans le dossier de sortie")
    d.add_argument("--out", default="canot-out")
    d.add_argument("--queries", help="optionnel : fichier SQL à rejouer. Par défaut, les requêtes les plus coûteuses de l'historique de la plateforme.")
    d.add_argument("--top", type=int, default=20)
    d.add_argument("--tolerances", help="fichier TOML (voir `canot init-tolerances`)")
    d.add_argument("--tables", nargs="*", help="limiter à ces tables (SCHEMA.TABLE)")
    d.add_argument("--key", default=os.environ.get("CANOT_EVIDENCE_KEY_PATH", str(Path.home() / ".canot" / "evidence.key")), help="clé de signature (défaut : variable CANOT_EVIDENCE_KEY_PATH, sinon ~/.canot/evidence.key)")
    d.add_argument("--credit-price", type=float, default=None, help="prix du crédit Snowflake de votre contrat, en USD (défaut : fourchette du prix catalogue)")
    d.add_argument("--replay-max-gb", type=float, default=2.0, help="taille maximale d'une table chargée en mémoire pour rejouer les requêtes")

    t = sub.add_parser("teardown", help="supprime les données copiées")
    t.add_argument("--to", dest="dst", required=True)
    t.add_argument("--bucket")
    t.add_argument("--catalog", help="le même catalogue que pour drill")
    t.add_argument("--out", default="canot-out")

    v = sub.add_parser("verify-evidence", help="vérifie la chaîne et la signature d'un dossier")
    v.add_argument("dir")
    v.add_argument("--expect-pub", help="fichier .pub de la clé attendue : refuse un dossier signé par une autre clé")

    ss = sub.add_parser("snowflake-setup", help="affiche le script SQL d'accès en lecture seule à relire")
    ss.add_argument("--generate-key", action="store_true", help="crée la paire de clés ici et met la clé publique dans le script")
    ss.add_argument("--base", default="<BASE>", help="base Snowflake à copier")
    sub.add_parser("mcp", help="serveur MCP (stdio) : la documentation et l'explication de vos rapports, pour un agent")
    bs = sub.add_parser("bigquery-setup", help="affiche le script gcloud d'accès en lecture seule (rôle personnalisé) à relire")
    bs.add_argument("--project", help="projet à scanner")
    bs.add_argument("--member", help="user:... ou serviceAccount:... qui recevra le rôle")
    dm = sub.add_parser("demo", help="démonstration complète en 30 secondes, sans compte : jeu TPC-H, exercice de sortie, dossier de preuve")
    dm.add_argument("dest", nargs="?", help="où écrire la copie (comme pour drill : ./iceberg, s3://…, scaleway:fr-par, aws:eu-west-3…). Défaut : OUT/iceberg")
    dm.add_argument("--bucket", help="obligatoire avec un raccourci d'hébergeur (voir canot drill)")
    dm.add_argument("--catalog", help="catalogue Iceberg (voir canot drill)")
    dm.add_argument("--out", default="canot-demo")
    dm.add_argument("--sf", type=float, default=0.1, help="taille de TPC-H (0.1 : 600 000 lignes de lignes de commande)")
    dm.add_argument("--rows", type=int, default=50_000, help=argparse.SUPPRESS)  # repli hors ligne : jeu fictif
    dm.add_argument("--no-open", action="store_true", help="n'ouvre pas le rapport dans le navigateur")
    it = sub.add_parser("init-tolerances", help="écrit un fichier de tolérances à relire avec le client")
    it.add_argument("path", nargs="?", default="tolerances.toml")
    dd = sub.add_parser("demo-data", help="crée une base DuckDB de démonstration")
    dd.add_argument("path")
    dd.add_argument("--rows", type=int, default=20_000)
    dd.add_argument("--retail", action="store_true", help="jeu fictif de distributeur (tickets avec lignes imbriquées)")
    dd.add_argument("--tpch", action="store_true", help="jeu TPC-H, le benchmark de référence (généré en local)")
    dd.add_argument("--sf", type=float, default=0.1, help="avec --tpch : taille (1 = environ 1 Go)")
    sub.add_parser("connecteurs", help="les plateformes connues : inventaire, export, preuve, statut de validation")
    rd = sub.add_parser("rejeu-dbt", help="preuve P3 : rejoue les modèles d'un projet dbt dans DuckDB sur la copie et compare aux tables d'origine")
    rd.add_argument("projet", help="dbt://CHEMIN_DU_PROJET (dossier qui contient target/manifest.json, SQL compilé compris)")
    rd.add_argument("--base", help="copie en base DuckDB : tables sources et tables d'origine des modèles")
    rd.add_argument("--iceberg", "--lac", dest="lac", help="ou la copie Iceberg écrite par canot drill (même DESTINATION que pour drill)")
    rd.add_argument("--drill-out", help="avec --iceberg : le dossier --out du drill, qui contient son catalogue (défaut canot-out)")
    rd.add_argument("--catalog", help="avec --iceberg : le même catalogue que pour drill")
    rd.add_argument("--out", default="canot-rejeu-dbt")
    rd.add_argument("--tolerances", help="fichier TOML (voir `canot init-tolerances`)")
    rd.add_argument("--key", default=os.environ.get("CANOT_EVIDENCE_KEY_PATH", str(Path.home() / ".canot" / "evidence.key")),
                    help="clé de signature (défaut : variable CANOT_EVIDENCE_KEY_PATH, sinon ~/.canot/evidence.key)")
    return ap


def run(argv: list[str] | None = None) -> int:
    ap = parser()
    a = ap.parse_args(argv)
    if a.cmd in ("scan", "drill"):
        a.src = a.src or a.source
        if not a.src:
            ap.error("indiquez la source : canot " + a.cmd + (" SOURCE DESTINATION" if a.cmd == "drill" else " SOURCE"))
        if a.cmd == "drill":
            a.dst = a.dst or a.dest
            if not a.dst:
                ap.error("indiquez la destination : canot drill SOURCE DESTINATION (un dossier local suffit : ./iceberg)")
    out = Path(getattr(a, "out", "canot-out"))

    if a.cmd == "snowflake-setup":
        from .sources.snowflake_src import SETUP_SQL

        sql = SETUP_SQL.replace("<BASE>", a.base)
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
            sql = sql.replace("<clé publique, sans les lignes BEGIN/END>", pub)
            print(f"-- Clé privée : {kp} (reste sur cette machine). Puis : export SNOWFLAKE_PRIVATE_KEY_PATH={kp}", file=sys.stderr)
        _p(sql)
        return 0
    if a.cmd == "connecteurs":
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
        db, qf, dossier = work / "demo.duckdb", work / "requetes.sql", work / "dossier"
        dest = a.dest or str(work / "iceberg")
        bucket = f" --bucket {a.bucket}" if a.bucket else ""
        _p("canot demo est un raccourci de deux commandes, que vous pouvez lancer et modifier vous-même :")
        _p(f"  canot demo-data {db} --tpch --sf {a.sf:g}")
        _p(f"  canot drill {db} {dest}{bucket} --queries {qf} --out {dossier}")
        _p("Changez la destination pour jouer la même démo ailleurs : canot demo aws:eu-west-3 --bucket MON-COFFRE\n")
        if db.exists():
            db.unlink()
        target = parse_target(dest, a.bucket, dossier, a.catalog)  # refuse tout de suite une destination incomplète
        try:
            _p("TPC-H : le jeu de référence des bancs d'essai de bases de données, généré ici (aucune donnée réelle)\n")
            make_tpch(str(db), a.sf)
            queries = TPCH_QUERIES
        except Exception as e:  # noqa: BLE001 : l'extension tpch se télécharge au premier usage ; hors ligne, jeu de secours
            _p(f"TPC-H indisponible ({type(e).__name__}) : jeu fictif de secours, générable hors ligne\n")
            if db.exists():
                db.unlink()
            make_retail(str(db), a.rows)
            queries = RETAIL_QUERIES
        qf.write_text(queries)
        src = open_demo(str(db))
        try:
            res = run_drill(src, target, dossier, Path.home() / ".canot" / "evidence.key", str(qf), Tolerances(), log=_p)
        finally:
            src.close()
        rep = write_report(res, dossier)
        _p(f"\n{res['summary']['parity'] == 'complète' and 'Parité complète' or 'Des écarts ont été trouvés'} : {res['summary']['tables_ok']}/{res['summary']['tables_total']} tables, {res['summary']['queries_match']}/{res['summary']['queries_total']} requêtes.")
        _p(f"Dossier de preuve : {rep}")
        _p("Vérifier qu'il n'a pas été modifié : canot verify-evidence " + str(dossier))
        _p("\nEt maintenant, sur vos données (lecture seule) :  canot scan \"snowflake://MY_ACCOUNT/MY_DATABASE?user=MY_USER\"")
        _p("Besoin d'aide pour l'exécuter sur votre périmètre ? https://canot.io/#contact")
        if not a.no_open:
            webbrowser.open(rep.resolve().as_uri())
        return 0
    if a.cmd == "init-tolerances":
        from .tolerances import DEFAULT_TOML

        Path(a.path).write_text(DEFAULT_TOML)
        _p(f"écrit : {a.path}")
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
        _p(f"écrit : {a.path} et {q}")
        return 0
    if a.cmd == "verify-evidence":
        from .evidence import verify_dir

        ok, msg = verify_dir(Path(a.dir), Path(a.expect_pub) if a.expect_pub else None)
        _p(("OK : " if ok else "ÉCHEC : ") + msg)
        return 0 if ok else 2

    if a.cmd == "rejeu-dbt":
        from .dbt_rejeu import run_rejeu
        from .tolerances import Tolerances

        res = run_rejeu(a.projet, out, Path(a.key), base=a.base, lac=a.lac, drill_out=a.drill_out, catalog=a.catalog,
                        tol=Tolerances.load(a.tolerances), log=_p)
        s = res["summary"]
        _p(f"\nRejeu dbt (P3) : {s['identique']} identique(s), {s['different']} différent(s), {s['non_compare']} rejoué(s) non comparé(s), "
           f"{s['non_rejouable']} non rejouable(s), sur {s['modeles']} modèle(s)")
        _p(f"Dossier : {out / 'rejeu-dbt.json'} ; vérifier : canot verify-evidence {out}")
        return 0 if s["different"] == 0 else 1
    if a.cmd == "teardown":
        from .target import parse_target

        _p(parse_target(a.dst, a.bucket, out, a.catalog).teardown())
        return 0

    from .connectors import effective_level, for_url, open_url

    conn = for_url(a.src)
    if a.cmd == "drill" and conn is not None and conn.famille == "fichiers":
        raise ValueError(f"URL {conn.label} : drill indisponible pour des fichiers de projet ; les tables vivent sur la plateforme "
                         "de l'adaptateur : canot drill sur cette plateforme les copie et les compare (voir canot scan pour la commande).")
    src = open_url(a.src)
    try:
        from .connectors import supports_full

        if a.cmd == "scan" and supports_full(src):
            from collections import Counter

            from .scanner.run import LEVELS, scan_full

            level = effective_level(src, a.level)
            if a.include_sql_text and conn is not None and conn.famille == "fichiers":
                _p("--include-sql-text sans objet ici : le code est déjà dans les fichiers du projet ; rien de plus n'est écrit.")
                a.include_sql_text = False
            _p(f"Canot scan en lecture seule (niveau {level} : {LEVELS[level]})")
            res = scan_full(src, out, level=level, credit_price=a.credit_price, include_sql_text=a.include_sql_text, log=_p)
            _p(f"Source : {res['perimetre']}")
            for role, lines in res["resume"].items():
                for line in lines:
                    _p(f"  {role} : {line}")
            codes = Counter(f["code"] for f in res["drapeaux"])
            if codes:
                _p("À examiner : " + ", ".join(f"{c} ×{n}" for c, n in sorted(codes.items())))
            nl = [l for l in res["lectures"] if l["statut"] == "non_lu"]
            _p(f"Lectures : {len(res['lectures']) - len(nl)} faites, {len(nl)} non lues (détail et raisons dans le rapport)")
            _p(f"Rapport à transmettre : {out / 'scan.html'}")
            _p(f"Données : {out / 'scan.json'} et les CSV du même dossier")
            _p(f"Pour nous le transmettre : {out / 'scan-partage.json'} (des comptes seulement : ni nom de table, ni SQL, ni identité) sur https://canot.io/offres.html#demarrer")
            if a.include_sql_text:
                _p(f"ATTENTION : {out / 'scan-sql-brut.json'} contient le texte SQL brut de votre historique. Ne le diffusez pas.")
            _p("Besoin d'aide pour lire ce rapport ou préparer la suite ? https://canot.io/#contact")
            return 0
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
                _p(f"  base : {e['cost_basis']}")
            ec = res["exit_cost"]
            _p(f"Travail de reprise estimé : {ec['rework_days'][0]} à {ec['rework_days'][1]} jours ({ec['rework_basis']})")
            _p(f"Non inclus : {ec['not_included']}")
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

            target = parse_target(a.dst, a.bucket, out, a.catalog)
            res = run_drill(src, target, out, Path(a.key), a.queries, Tolerances.load(a.tolerances), a.top, a.tables, a.credit_price, a.replay_max_gb, _p)
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
