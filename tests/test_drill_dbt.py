"""canot drill dbt://PROJECT DESTINATION: profile resolution, relations from the manifest, copy + replay in one evidence folder.

A tiny dbt-style project is built in tmp_path: dbt_project.yml, profiles.yml (a DuckDB target pointing to a DuckDB file built
here, and a Snowflake target served by the same file), and a hand-written target/manifest.json consistent with the models.
Offline, deterministic: no account, no dbt command.
"""
import json
from pathlib import Path

import duckdb
import pytest

from canot import dbt_drill
from canot.connectors import NoConnector
from canot.dbt_drill import DbtProfileError, load_profile, relations, resolve, run_dbt_drill
from canot.evidence import verify_dir

SECRET = "s3cr3t-Pa55word-never-written"
WH = "warehouse"  # DuckDB file warehouse.duckdb: its catalog (the dbt "database") is "warehouse"

MODELS = {  # name -> (materialization, compiled SQL, parents); the same SQL builds the original tables
    "stg_orders": ("view", f'select order_id, customer_id, amount from "{WH}"."raw"."orders" where status <> \'cancelled\'',
                   ["source.shop.raw.orders"]),
    "fct_orders": ("table", f'select o.order_id, o.customer_id, o.amount, c.country, cc.region\n'
                            f'from "{WH}"."analytics"."stg_orders" o join "{WH}"."raw"."customers" c on c.customer_id = o.customer_id\n'
                            f'join "{WH}"."main"."country_codes" cc on cc.country = c.country',
                   ["model.shop.stg_orders", "source.shop.raw.customers", "seed.shop.country_codes"]),
    "customer_totals": ("table", f'select customer_id, sum(amount) as total, count(*) as n from "{WH}"."analytics"."fct_orders" group by customer_id',
                        ["model.shop.fct_orders"]),
    "orders_inc": ("incremental", f'select * from "{WH}"."raw"."orders"', ["source.shop.raw.orders"]),
    "eph_big": ("ephemeral", f'select * from "{WH}"."raw"."orders" where amount > 100', ["source.shop.raw.orders"]),
    "mv_daily": ("materialized_view", f'select 1 as x from "{WH}"."raw"."orders"', ["source.shop.raw.orders"]),
}


def build_warehouse(path: Path, ecart: bool = False) -> Path:
    con = duckdb.connect(str(path))
    con.execute("create schema raw; create schema analytics")
    con.execute("""create table raw.customers as select i as customer_id, ['FR','DE','ES'][1 + i % 3] as country from range(1, 31) t(i)""")
    con.execute("""create table raw.orders as select i as order_id, 1 + i % 30 as customer_id, cast(5 + (i * 13) % 190 as decimal(12,2)) as amount,
                   ['paid','cancelled','paid','shipped'][1 + i % 4] as status from range(1, 301) t(i)""")
    con.execute("create table main.country_codes as select * from (values ('FR','EU-W'),('DE','EU-C'),('ES','EU-S')) v(country, region)")
    for name in ("stg_orders", "fct_orders", "customer_totals", "orders_inc"):
        mat, sql, _ = MODELS[name]
        con.execute(f"create {'view' if mat == 'view' else 'table'} analytics.{name} as {sql}")
    con.execute("create table analytics.py_scores as select customer_id, 0.5 as score from raw.customers")
    if ecart:  # the platform holds something other than what the SQL gives on these inputs
        con.execute("update analytics.customer_totals set total = total + 1 where customer_id = 1")
    con.close()
    return path


def _node(name, mat, sql, parents, rt="model", language="sql"):
    return {"unique_id": f"{rt}.shop.{name}", "resource_type": rt, "name": name, "alias": name, "database": WH,
            "schema": "main" if rt == "seed" else "analytics", "package_name": "shop", "language": language,
            "relation_name": None if mat == "ephemeral" else f'"{WH}"."{"main" if rt == "seed" else "analytics"}"."{name}"',
            "config": {"materialized": mat}, "raw_code": sql, "compiled_code": sql,
            "depends_on": {"macros": [], "nodes": parents}}


def build_project(tmp_path: Path, profiles: str | None = None, ecart: bool = False) -> Path:
    proj = tmp_path / "shop"
    (proj / "target").mkdir(parents=True)
    build_warehouse(proj / f"{WH}.duckdb", ecart)
    (proj / "dbt_project.yml").write_text("name: shop\nversion: '1.0'\nprofile: shop_profile\n")
    (proj / "profiles.yml").write_text(profiles or (
        "shop_profile:\n  target: dev\n  outputs:\n"
        "    dev:\n      type: duckdb\n      path: \"{{ env_var('SHOP_DB', 'warehouse.duckdb') }}\"\n      threads: 1\n"
        "    sf:\n      type: snowflake\n      account: \"{{ env_var('SHOP_ACCOUNT') }}\"\n      user: SHOP_USER\n      role: SHOP_READER\n"
        "      warehouse: SHOP_WH\n      database: warehouse\n      schema: analytics\n"
        "      password: \"{{ env_var('SHOP_PW') }}\"\n      private_key_path: \"{{ env_var('SHOP_KEY', '/keys/shop_rsa.p8') }}\"\n"
        "    pg:\n      type: postgres\n      host: localhost\n"))
    nodes = {}
    for name, (mat, sql, parents) in MODELS.items():
        nodes[f"model.shop.{name}"] = _node(name, mat, sql, parents)
    nodes["model.shop.py_scores"] = _node("py_scores", "table", None, ["source.shop.raw.customers"], language="python")
    nodes["model.shop.py_scores"]["raw_code"] = "def model(dbt, session):\n    return None"
    nodes["seed.shop.country_codes"] = _node("country_codes", "seed", None, [], rt="seed")
    nodes["test.shop.not_null"] = {"unique_id": "test.shop.not_null", "resource_type": "test", "name": "not_null", "config": {}}
    srcs = {f"source.shop.raw.{t}": {"unique_id": f"source.shop.raw.{t}", "resource_type": "source", "name": t, "identifier": t,
                                     "source_name": "raw", "database": WH, "schema": "raw", "relation_name": f'"{WH}"."raw"."{t}"'}
            for t in ("customers", "orders", "ghost")}  # ghost: declared, never loaded
    macros = {f"macro.dbt.materialization_{m}_default": {"name": f"materialization_{m}_default", "package_name": "dbt"}
              for m in ("table", "view", "incremental", "seed", "materialized_view")}
    man = {"metadata": {"adapter_type": "duckdb", "project_name": "shop", "dbt_version": "1.9.0"}, "nodes": nodes, "sources": srcs,
           "macros": macros}
    (proj / "target" / "manifest.json").write_text(json.dumps(man))
    return proj


def _par(res) -> dict:
    return {m["modele"].split(".")[-1]: m for m in res["replay"]["modeles"]}


# ---------------------------------------------------------------- profile


def test_resolution_env_var_sans_jinja():
    env = {"A": "x"}
    assert resolve("{{ env_var('A') }}", "f", env) == "x"
    assert resolve("pre-{{env_var(\"B\", 'dflt')}}", "f", env) == "pre-dflt"
    assert resolve("{{ env_var('A') | as_number }}", "f", env) == "x"
    assert resolve(4, "f", env) == 4
    with pytest.raises(DbtProfileError, match="B, which is not set"):
        resolve("{{ env_var('B') }}", "f", env)
    for bad in ("{{ 7 * 7 }}", "{% if x %}a{% endif %}", "{{ env_var('A') ~ var('x') }}", "{{ ''.__class__ }}"):
        with pytest.raises(DbtProfileError, match="does not evaluate"):
            resolve(bad, "f", env)


def test_choix_du_profil_et_de_la_cible(tmp_path, monkeypatch):
    proj = build_project(tmp_path)
    p = load_profile(proj, env={})
    assert (p.projet, p.profil, p.cible, p.adaptateur, p.origine) == ("shop", "shop_profile", "dev", "duckdb", "project folder")
    assert load_profile(proj, target="sf", env={}).adaptateur == "snowflake"
    with pytest.raises(DbtProfileError, match="no target 'nope'.*dev, sf, pg"):
        load_profile(proj, target="nope", env={})
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "profiles.yml").write_text("shop_profile:\n  target: x\n  outputs:\n    x:\n      type: bigquery\n      project: p\n")
    assert load_profile(proj, profiles_dir=str(other), env={}).adaptateur == "bigquery"
    assert load_profile(proj, env={"DBT_PROFILES_DIR": str(other)}).origine == "DBT_PROFILES_DIR"
    (proj / "profiles.yml").unlink()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "nohome")
    with pytest.raises(DbtProfileError, match="no profiles.yml found"):
        load_profile(proj, env={})


def test_secret_en_clair_refuse(tmp_path):
    proj = build_project(tmp_path, profiles="shop_profile:\n  target: sf\n  outputs:\n    sf:\n      type: snowflake\n      account: a\n"
                                            "      user: u\n      database: warehouse\n      password: hunter2\n")
    p = load_profile(proj, env={})
    with pytest.raises(DbtProfileError, match="written in clear"):
        dbt_drill.connexion(p, ["raw"], proj, env={})


def test_correspondance_snowflake_sans_secret_dans_la_description(tmp_path):
    proj = build_project(tmp_path)
    env = {"SHOP_ACCOUNT": "xy12345", "SHOP_PW": SECRET}
    p = load_profile(proj, target="sf", env=env)
    cx = dbt_drill.connexion(p, ["raw", "analytics"], proj, env=env)
    assert cx.url.startswith("snowflake://xy12345/warehouse?") and "schemas=ANALYTICS%2CRAW" in cx.url and "role=SHOP_READER" in cx.url
    assert cx.env == {"SNOWFLAKE_PASSWORD": SECRET, "SNOWFLAKE_PRIVATE_KEY_PATH": "/keys/shop_rsa.p8"}
    assert SECRET not in json.dumps(cx.description) and "shop_rsa" not in json.dumps(cx.description) and "SHOP_USER" not in json.dumps(cx.description)
    # a variable the user already set wins
    assert "SNOWFLAKE_PASSWORD" not in dbt_drill.connexion(p, ["raw"], proj, env={**env, "SNOWFLAKE_PASSWORD": "mine"}).env


def test_adaptateur_sans_connecteur(tmp_path):
    proj = build_project(tmp_path)
    with pytest.raises(NoConnector) as e:
        run_dbt_drill(f"dbt://{proj}?target=pg", str(tmp_path / "lake"), tmp_path / "out", tmp_path / "k.key", log=lambda *_: None)
    msg = str(e.value)
    assert "postgres adapter (profile shop_profile, target pg)" in msg and "canot.io/en/review.html?connector=postgres" in msg


# ---------------------------------------------------------------- relations


def test_relations_du_manifest(tmp_path):
    man = json.loads((build_project(tmp_path) / "target" / "manifest.json").read_text())
    copier, ecartees = relations(man)
    assert sorted(r["fqn"] for r in copier) == ["analytics.customer_totals", "analytics.fct_orders", "analytics.orders_inc",
                                                 "analytics.py_scores", "main.country_codes", "raw.customers", "raw.ghost", "raw.orders"]
    assert next(r for r in copier if r["name"] == "py_scores")["note"].startswith("python model")
    why = {r["uid"].split(".")[-1]: r["raison"] for r in ecartees}
    assert set(why) == {"stg_orders", "eph_big", "mv_daily"}
    assert why["stg_orders"].startswith("view") and why["eph_big"].startswith("ephemeral") and "materialized_view" in why["mv_daily"]


# ---------------------------------------------------------------- end to end


def _drill(tmp_path, proj, *, ecart_out="out", **kw):
    out = tmp_path / ecart_out
    r = run_dbt_drill(f"dbt://{proj}", str(tmp_path / "lake"), out, tmp_path / "k.key", log=lambda *_: None, **kw)
    return r, out


def test_drill_dbt_duckdb_de_bout_en_bout(tmp_path):
    proj = build_project(tmp_path)
    r, out = _drill(tmp_path, proj)
    s, d = r["drill"]["summary"], r["dbt"]
    assert s["parity"] == "complete" and s["tables_total"] == s["tables_ok"] == 7  # ghost is missing from the warehouse
    par = _par(r)
    assert {m: par[m]["statut"] for m in ("fct_orders", "customer_totals")} == {"fct_orders": "identique", "customer_totals": "identique"}
    assert par["stg_orders"]["statut"] == par["mv_daily"]["statut"] == "non_compare"  # replayed, nothing to compare with
    assert par["orders_inc"]["statut"] == par["py_scores"]["statut"] == par["eph_big"]["statut"] == "non_rejouable"
    assert d["verdict"] == "partial" and d["explications"] == []
    nc = {x["uid"].split(".")[-1]: x["raison"] for x in d["non_copiees"]}
    assert nc["ghost"].startswith("not found as a table") and nc["stg_orders"].startswith("view")
    # one evidence folder: drill report, replay report, one signed log that verify-evidence accepts
    for f in ("report.html", "report.json", "replay-dbt.json", "evidence.jsonl", "evidence.sig", "evidence.pub", "reread.sql"):
        assert (out / f).exists(), f
    assert verify_dir(out)[0]
    events = [json.loads(l)["event"] for l in (out / "evidence.jsonl").read_text().splitlines()]
    assert events[0] == "dbt_project" and "copy" in events and "replay_start" in events and "dbt_model" in events and events[-1] == "dbt_verdict"
    rep = json.loads((out / "report.json").read_text())
    rj = json.loads((out / "replay-dbt.json").read_text())
    assert rep["seal"] == rj["seal"] and rep["seal"]["entries"] == len(events) and rep["dbt"]["projet"] == "shop"
    assert "dbt project" in (out / "report.html").read_text()


def test_drill_dbt_detecte_un_ecart_injecte(tmp_path):
    proj = build_project(tmp_path, ecart=True)
    r, out = _drill(tmp_path, proj)
    assert r["drill"]["summary"]["parity"] == "complete"  # the copy is faithful: the difference is in the pipeline
    par = _par(r)
    assert par["customer_totals"]["statut"] == "different" and par["fct_orders"]["statut"] == "identique"
    assert r["dbt"]["verdict"] == "mismatch"
    (e,) = r["dbt"]["explications"]
    assert e["modele"] == "model.shop.customer_totals" and "raw.orders" in e["entrees_copiees"] and "possible causes" in e["explication"]
    assert verify_dir(out)[0]


def test_ecart_explique_par_une_politique(tmp_path, monkeypatch):
    proj = build_project(tmp_path, ecart=True)
    monkeypatch.setattr(dbt_drill, "politiques", lambda src, tables: {"lu": True, "raison": None, "tables": {
        "raw.customers": [{"kind": "ROW_ACCESS_POLICY", "policy": "RAP_COUNTRY", "column": None}]}})
    r, _ = _drill(tmp_path, proj)
    (e,) = r["dbt"]["explications"]
    assert "raw.customers (row access policy RAP_COUNTRY)" in e["explication"] and "F10" in e["explication"]


def test_profil_snowflake_aucun_secret_ecrit(tmp_path, monkeypatch, capsys):
    """Snowflake target, served by the DuckDB file: the secret and the key path reach the source's environment while it
    opens, and appear in no output file and no printed line."""
    from canot.sources.duckdb_src import DuckDBSource

    proj = build_project(tmp_path)
    monkeypatch.setenv("SHOP_ACCOUNT", "xy12345")
    monkeypatch.setenv("SHOP_PW", SECRET)
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PRIVATE_KEY_PATH", raising=False)
    vu = {}

    def ouvrir(url):
        import os

        vu.update(url=url, pw=os.environ.get("SNOWFLAKE_PASSWORD"), key=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"))
        return DuckDBSource(str(proj / f"{WH}.duckdb"))

    out = tmp_path / "out"
    lines = []
    r = run_dbt_drill(f"dbt://{proj}?target=sf", str(tmp_path / "lake"), out, tmp_path / "k.key", log=lines.append, open_source=ouvrir)
    assert vu["pw"] == SECRET and vu["key"] == "/keys/shop_rsa.p8" and "user=SHOP_USER" in vu["url"]
    import os

    assert "SNOWFLAKE_PASSWORD" not in os.environ  # restored after opening
    assert r["dbt"]["connexion"]["role"] == "SHOP_READER" and r["dbt"]["lecture"] == "read-only, with role SHOP_READER"
    blob = "\n".join(lines + dbt_drill.resume(r, out))
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".jsonl", ".html", ".sql", ".sig", ".pub"):
            blob += f.read_text(errors="ignore")
    assert SECRET not in blob and "shop_rsa" not in blob and "SHOP_USER" not in blob
    assert "Read-only" in lines[1] or any("Read-only" in x for x in lines)


def test_cli_drill_dbt(tmp_path, capsys):
    from canot.cli import run as cli_run

    proj = build_project(tmp_path, ecart=True)
    out = tmp_path / "out"
    code = cli_run(["drill", f"dbt://{proj}", str(tmp_path / "lake"), "--out", str(out), "--key", str(tmp_path / "k.key")])
    txt = capsys.readouterr().out
    assert code == 1
    assert "Read-only: Canot reads the warehouse with your credentials" in txt
    assert "dbt exit exercise (shop, target dev): mismatch" in txt
    assert "Models (P3): 1 identical, 1 different, 2 replayed but not compared (no table to compare: views), 3 not replayable, out of 7" in txt
    assert "Why customer_totals differs" in txt and f"canot verify-evidence {out}" in txt
    assert cli_run(["verify-evidence", str(out)]) == 0
    with pytest.raises(SystemExit):
        cli_run(["drill", str(proj / f"{WH}.duckdb"), str(tmp_path / "l2"), "--target", "x"])


def test_explication_fuseau_horaire_mesuree_avant_la_politique():
    """Case of the real run on the test bench: ORDER_TS TIMESTAMP_TZ copied as UTC, a date derived from it shifts by a day;
    RAW.CUSTOMERS carries policies. The measured cause comes first, the policy is stated with what this run shows."""
    man = {"nodes": {"model.p.fct": {"depends_on": {"nodes": ["source.p.raw.orders", "source.p.raw.customers"]}}}}
    copiees = [{"uid": "source.p.raw.orders", "fqn": "RAW.orders"}, {"uid": "source.p.raw.customers", "fqn": "RAW.customers"}]
    rapport = {"modeles": [{"modele": "model.p.fct", "statut": "different", "raison": "ORDER_DATE.max: datetime.date(2025, 8, 22) / datetime.date(2025, 8, 23)",
                            "verification": {"rows_source": 150000, "rows_target": 150000, "stats_diffs": ["ORDER_DATE.max: datetime.date(2025, 8, 22) / datetime.date(2025, 8, 23)"]}}]}
    pol = {"lu": True, "tables": {"RAW.CUSTOMERS": [{"kind": "ROW_ACCESS_POLICY", "policy": "RAP_COUNTRY", "column": None}]}}
    conv = [{"table": "RAW.ORDERS", "column": "ORDER_TS", "lossy": True,
             "note": "time zone America/Los_Angeles -> UTC (Iceberg keeps UTC only): the instant is kept, the zone name is not"}]
    (e,) = dbt_drill.explications(man, rapport, copiees, pol, conv, "role R", "Europe/Paris")
    assert [c["nature"] for c in e["causes"]] == ["fuseau", "politique"] and e["causes"][0]["mesure"]
    assert "RAW.ORDERS.ORDER_TS is copied as an instant in UTC" in e["explication"] and "Europe/Paris" in e["explication"]
    assert "the differences on date values here are this shift" in e["explication"]
    assert "as many rows as the platform table (150000)" in e["explication"]


def test_mcp_explique_le_dossier_dbt(tmp_path):
    from canot import agent

    _, out = _drill(tmp_path, build_project(tmp_path, ecart=True))
    e = agent.explain_report(str(out / "report.json"))
    assert e["dbt"]["verdict"] == "mismatch" and [x["model"] for x in e["dbt"]["why_models_differ"]] == ["model.shop.customer_totals"]
    assert any(x["relation"].endswith("stg_orders") for x in e["dbt"]["not_copied"])
    assert agent.explain_report(str(out / "replay-dbt.json"))["form"] == "P3 pipeline replay"
