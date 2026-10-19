"""Preuve P3 sur le projet dbt du banc : modèles rejoués dans DuckDB sur une copie locale, comparés aux tables d'origine.

Les « tables d'origine » sont construites ici par un SQL écrit à la main (équivalent des modèles, pas le SQL de dbt) sur un
petit jeu de données : sans réseau, sans compte. Les artefacts sont la copie de lab/dbt (dbt build réel sur Snowflake).
"""
import copy
import json
from pathlib import Path

import duckdb
import pytest

from canot.dbt_rejeu import run_rejeu
from canot.evidence import verify_dir

FIX = Path(__file__).parent / "fixtures" / "dbt" / "banc"
MARTS = ("dbt_customer_ltv", "dbt_daily_revenue", "dbt_fct_orders", "int_order_totals")
STG = ("stg_customers", "stg_order_lines", "stg_orders", "stg_products")

ORIGINES = {  # ce que Snowflake aurait matérialisé, avec des types à la Snowflake (NUMBER)
    "int_order_totals": """select l.order_id, cast(sum(l.qty * p.unit_price) as decimal(38,2)) as basket_value,
                                  cast(sum(l.qty) as decimal(38,0)) as items
                           from RAW.order_lines l join RAW.products p on p.product_id = l.product_id group by l.order_id""",
    "dbt_fct_orders": """select o.order_id, o.customer_id, cast(o.order_ts as date) as order_date, o.total, o.status, c.country,
                                coalesce(c.segment, 'inconnu') as segment, t.basket_value, t.items
                         from RAW.orders o join RAW.customers c on c.customer_id = o.customer_id
                         left join DBT_dbt_mart.int_order_totals t on t.order_id = o.order_id""",
    "dbt_customer_ltv": """select customer_id, cast(count(*) as decimal(38,0)) as orders, cast(sum(total) as decimal(38,2)) as lifetime_value,
                                  min(order_date) as first_order, max(order_date) as last_order
                           from DBT_dbt_mart.dbt_fct_orders where status <> 'returned' group by customer_id""",
    "dbt_daily_revenue": """select order_date, country, status, cast(count(*) as decimal(38,0)) as orders, cast(sum(total) as decimal(38,2)) as revenue
                            from DBT_dbt_mart.dbt_fct_orders group by order_date, country, status""",
}


def copie_duckdb(path: Path, ecart: bool = False) -> Path:
    con = duckdb.connect(str(path))
    con.execute("create schema RAW; create schema DBT_dbt_mart")
    con.execute("""create table RAW.customers as select i as customer_id, 'Client' || i || '@exemple.test' as email,
                   ['FR','DE','ES'][1 + i % 3] as country, case when i % 4 = 0 then null else ['pme','ge'][1 + i % 2] end as segment,
                   timestamp '2025-01-01' + i * interval 1 day as signed_up_at from range(1, 61) t(i)""")
    con.execute("""create table RAW.products as select i as product_id, 'Produit ' || i as label, ['a','b'][1 + i % 2] as category,
                   cast(1.5 + i as decimal(10,2)) as unit_price from range(1, 11) t(i)""")
    con.execute("""create table RAW.orders as select i as order_id, 1 + i % 60 as customer_id,
                   timestamp '2026-01-01 08:00:00' + (i % 45) * interval 1 day + (i % 7) * interval 1 hour as order_ts,
                   cast(10 + (i * 37) % 200 + 0.25 as decimal(12,2)) as total, ['paid','returned','paid','shipped'][1 + i % 4] as status,
                   ['card','sepa'][1 + i % 2] as payment_method from range(1, 401) t(i)""")
    con.execute("""create table RAW.order_lines as select i as line_id, 1 + i % 380 as order_id, 1 + i % 10 as product_id,
                   1 + i % 3 as qty from range(1, 1201) t(i)""")
    for name in ("int_order_totals", "dbt_fct_orders", "dbt_customer_ltv", "dbt_daily_revenue"):
        con.execute(f"create table DBT_dbt_mart.{name} as {ORIGINES[name]}")
    if ecart:  # la plateforme a produit autre chose que ce que le SQL donne sur ces entrées
        con.execute("update DBT_dbt_mart.dbt_daily_revenue set revenue = revenue + 1 where rowid = (select min(rowid) from DBT_dbt_mart.dbt_daily_revenue)")
    con.close()
    return path


def projet_etendu(tmp_path: Path) -> Path:
    """Le banc + des modèles que DuckDB ne peut pas rejouer : fonction propre, incrémental, Python, et un descendant."""
    man = json.loads((FIX / "target" / "manifest.json").read_text(encoding="utf-8"))
    base = man["nodes"]["model.canot_lab.dbt_daily_revenue"]
    fct = "CANOT_LAB.DBT_dbt_mart.dbt_fct_orders"

    def add(name, **kw):
        n = copy.deepcopy(base)
        n.update({"unique_id": f"model.canot_lab.{name}", "name": name, "alias": name, "relation_name": f"CANOT_LAB.DBT_dbt_mart.{name}"})
        if "materialized" in kw:
            n["config"]["materialized"] = kw.pop("materialized")
        n.update(kw)
        man["nodes"][n["unique_id"]] = n

    add("m_propre", compiled_code=f"select conditional_true_event(total > 0) over (order by order_date) as e from {fct}")
    add("m_inc", materialized="incremental")
    add("m_py", language="python", compiled_code=None, raw_code="def model(dbt, session):\n    return None")
    add("m_apres", compiled_code="select * from CANOT_LAB.DBT_dbt_mart.m_propre",
        depends_on={"macros": [], "nodes": ["model.canot_lab.m_propre"]})
    d = tmp_path / "projet" / "target"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d.parent


def _par(res) -> dict[str, dict]:
    return {r["modele"].split(".")[-1]: r for r in res["modeles"]}


def test_rejeu_banc_identique_et_dossier_signe(tmp_path):
    db = copie_duckdb(tmp_path / "copie.duckdb")
    out = tmp_path / "out"
    res = run_rejeu(f"dbt://{FIX}", out, tmp_path / "k.key", base=str(db), log=lambda *_: None)
    par = _par(res)
    assert {m: par[m]["statut"] for m in MARTS} == dict.fromkeys(MARTS, "identique")
    assert {m: par[m]["statut"] for m in STG} == dict.fromkeys(STG, "non_compare")  # vues : pas de table d'origine
    assert all(par[m]["verification"]["ok"] and par[m]["verification"]["fingerprint_ok"] for m in MARTS)
    assert par["dbt_customer_ltv"]["verification"]["rows_target"] == par["dbt_customer_ltv"]["verification"]["rows_source"] > 0
    assert any("orders" in c and "-> DECIMAL(38,0)" in c for c in par["dbt_customer_ltv"]["conversions"])  # types alignés, consignés
    s = res["summary"]
    assert s == {**s, "modeles": 8, "identique": 4, "non_compare": 4, "different": 0, "non_rejouable": 0}
    assert verify_dir(out)[0]
    rapport = json.loads((out / "rejeu-dbt.json").read_text(encoding="utf-8"))
    assert rapport["schema"] == "canot.rejeu-dbt/1" and rapport["forme"] == "P3"
    brut = (out / "rejeu-dbt.json").read_text(encoding="utf-8") + (out / "evidence.jsonl").read_text(encoding="utf-8")
    assert "coalesce(" not in brut.lower() and "select " not in brut.lower()  # jamais le SQL des modèles


def test_rejeu_detecte_un_ecart(tmp_path):
    db = copie_duckdb(tmp_path / "copie.duckdb", ecart=True)
    par = _par(run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None))
    assert par["dbt_daily_revenue"]["statut"] == "different" and "revenue.sum" in par["dbt_daily_revenue"]["raison"]
    assert par["dbt_customer_ltv"]["statut"] == "identique"


def test_rejeu_classe_le_non_rejouable_avec_sa_raison(tmp_path):
    db = copie_duckdb(tmp_path / "copie.duckdb")
    res = run_rejeu(f"dbt://{projet_etendu(tmp_path)}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None)
    par = _par(res)
    assert par["m_propre"]["statut"] == "non_rejouable" and "CONDITIONAL_TRUE_EVENT" in par["m_propre"]["raison"]
    assert par["m_inc"]["statut"] == "non_rejouable" and par["m_inc"]["raison"].startswith("incrémental")
    assert par["m_py"]["statut"] == "non_rejouable" and "python" in par["m_py"]["raison"]
    assert par["m_apres"]["statut"] == "non_rejouable" and "m_propre" in par["m_apres"]["raison"]
    assert res["summary"]["non_rejouable"] == 4 and res["summary"]["identique"] == 4 and res["summary"]["preuve"] == "partielle"


def test_table_source_absente(tmp_path):
    db = copie_duckdb(tmp_path / "copie.duckdb")
    con = duckdb.connect(str(db))
    con.execute("drop table RAW.products")
    con.close()
    par = _par(run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None))
    assert par["stg_products"]["statut"] == "non_rejouable" and "table absente de la copie" in par["stg_products"]["raison"]
    assert "stg_products" in par["int_order_totals"]["raison"] and par["dbt_fct_orders"]["statut"] == "non_rejouable"
    assert par["stg_orders"]["statut"] == "non_compare"


def test_rejeu_sur_le_lac_ecrit_par_canot_drill(tmp_path):
    """Bout en bout : canot drill copie la base vers Iceberg, le rejeu lit le lac (sources et tables d'origine copiées)."""
    from canot.drill import run_drill
    from canot.sources.duckdb_src import DuckDBSource
    from canot.target import parse_target
    from canot.tolerances import Tolerances

    db = copie_duckdb(tmp_path / "copie.duckdb")
    drill_out = tmp_path / "drill"
    src = DuckDBSource(str(db))
    try:
        r = run_drill(src, parse_target(str(tmp_path / "lac"), None, drill_out), drill_out, tmp_path / "k.key", None, Tolerances(),
                      log=lambda *_: None)
    finally:
        src.close()
    assert r["summary"]["parity"] == "complète"
    res = run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", lac=str(tmp_path / "lac"), drill_out=str(drill_out),
                    log=lambda *_: None)
    assert {m: _par(res)[m]["statut"] for m in MARTS} == dict.fromkeys(MARTS, "identique")


def test_cli_rejeu_dbt(tmp_path, capsys):
    from canot.cli import run as cli_run

    db = copie_duckdb(tmp_path / "copie.duckdb")
    out = tmp_path / "out"
    assert cli_run(["rejeu-dbt", f"dbt://{FIX}", "--base", str(db), "--out", str(out), "--key", str(tmp_path / "k.key")]) == 0
    assert "4 identique(s), 0 différent(s), 4 rejoué(s) non comparé(s), 0 non rejouable(s), sur 8 modèle(s)" in capsys.readouterr().out
    db2 = copie_duckdb(tmp_path / "copie2.duckdb", ecart=True)
    assert cli_run(["rejeu-dbt", f"dbt://{FIX}", "--base", str(db2), "--out", str(tmp_path / "o2"), "--key", str(tmp_path / "k.key")]) == 1


def test_copie_absente_message_clair(tmp_path):
    with pytest.raises(ValueError, match="indiquez la copie"):
        run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", log=lambda *_: None)


def test_mcp_explique_un_rejeu(tmp_path):
    from canot import agent

    db = copie_duckdb(tmp_path / "copie.duckdb", ecart=True)
    run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None)
    e = agent.expliquer_rapport(str(tmp_path / "out" / "rejeu-dbt.json"))
    assert e["forme"] == "P3 rejeu de traitements" and [d["modele"] for d in e["differents"]] == ["model.canot_lab.dbt_daily_revenue"]
    assert len(e["non_compares"]) == 4 and e["non_rejouables"] == []


def _copie_flottante(path: Path, decalage: str) -> Path:
    """Cas du premier rejeu réel : UNIT_PRICE en DOUBLE, BASKET_VALUE somme flottante, ITEMS en INTEGER (rejeu : HUGEINT)."""
    copie_duckdb(path)
    con = duckdb.connect(str(path))
    con.execute("alter table RAW.products alter unit_price type double")
    con.execute("drop table DBT_dbt_mart.int_order_totals")
    con.execute("""create table DBT_dbt_mart.int_order_totals as select l.order_id, sum(l.qty * p.unit_price) as basket_value,
                   cast(sum(l.qty) as integer) as items
                   from RAW.order_lines l join RAW.products p on p.product_id = l.product_id group by l.order_id""")
    # somme flottante dans un autre ordre côté plateforme : la valeur diffère de la nôtre (ici au dernier bit)
    con.execute(f"update DBT_dbt_mart.int_order_totals set basket_value = {decalage} where order_id % 3 = 0")
    con.close()
    return path


def test_regression_somme_flottante_au_dernier_bit(tmp_path):
    db = _copie_flottante(tmp_path / "c.duckdb", "nextafter(basket_value, 1e300)")
    par = _par(run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None))
    r = par["int_order_totals"]
    assert r["verification"]["fingerprint_ok"] is False and r["verification"]["stats_ok"] is True  # l'empreinte exacte diffère
    assert any(c.startswith("items : HUGEINT -> INTEGER") for c in r["conversions"])
    ef = r["verification"]["empreinte_flottants"]
    assert ef["egale"] and ef["chiffres_significatifs"] == 12 and ef["colonnes"] == ["basket_value"]
    assert ef["ecarts_exacts"]["basket_value"]["valeurs_differentes_au_bit"] > 0 and ef["ecarts_exacts"]["basket_value"]["ecart_relatif_max"] < 1e-12
    assert r["statut"] == "identique" and "dernier bit" in r["note"]


def test_regression_ecart_flottant_reel_reste_different(tmp_path):
    db = _copie_flottante(tmp_path / "c.duckdb", "basket_value * (1 + 1e-7)")
    r = _par(run_rejeu(f"dbt://{FIX}", tmp_path / "out", tmp_path / "k.key", base=str(db), log=lambda *_: None))["int_order_totals"]
    assert r["statut"] == "different" and "note" not in r
