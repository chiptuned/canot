"""dbt connector: inventory from the artifacts (manifest, run_results, catalog), with no network and no dbt command."""
import copy
import json
import socket
from pathlib import Path

import pytest

from canot import connectors
from canot.scanner import dbt as dbt_scan
from canot.scanner.analyze import analyze
from canot.scanner.model import valider_snapshot
from canot.scanner.render import render_html, write_csv
from canot.scanner.run import partage

FIX = Path(__file__).parent / "fixtures" / "dbt" / "banc"
LAB = Path(__file__).parents[1] / "lab" / "dbt"
SENTINELLE = "CANOT_SENTINELLE_7f3a"
EMAIL = "jean.dupont@exemple.test"
FORBIDDEN = ("savings", "waste", "wasted", "useless", "unused", "critical", "urgent", "easy", "guaranteed",
             "compliant", "certified", "row by row",
             "économie", "gaspillage", "inutile", "inutilisé", "critique", "facile", "garanti",
             "conforme", "certifié", "ligne par ligne")


def _collect(path) -> dict:
    snap, raw = dbt_scan.collect(dbt_scan.ProjetDbt(f"dbt://{path}"), salt=b"sel-essai-dbt-00")
    assert raw == []
    return snap


def _kinds(snap) -> dict:
    out = {}
    for o in snap["objets"]:
        out[o["kind"]] = out.get(o["kind"], 0) + 1
    return out


def _sorties(r: dict, tmp_path, fr: dict | None = None) -> str:
    r = {**r, "avec_sql_brut": False}
    write_csv(r, tmp_path)
    return (json.dumps(r, ensure_ascii=False) + render_html(r, fr) + json.dumps(partage(r), ensure_ascii=False)
            + "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.csv")))


# --- test bench: copy of the lab/dbt artifacts ---------------------------------------------------------------------


def test_banc_comptes_graphe_et_lectures():
    s = _collect(FIX)
    assert valider_snapshot(s) == []
    assert s["plateforme"] == "dbt" and s["famille"] == "fichiers" and s["schema"] == "canot.scan/2"
    assert "adapter snowflake, dbt 1.12.6" in s["perimetre"]
    assert _kinds(s) == {"source_dbt": 4, "modele_dbt": 8, "test_dbt": 8, "paquet_dbt": 2, "dag": 1}
    mods = {o["id"]: o for o in s["objets"] if o["kind"] == "modele_dbt"}
    mats = {i: o["detail"]["materialisation"] for i, o in mods.items()}
    assert sorted(i.split(".")[-1] for i, m in mats.items() if m == "view") == ["stg_customers", "stg_order_lines", "stg_orders", "stg_products"]
    assert sorted(i.split(".")[-1] for i, m in mats.items() if m == "table") == ["dbt_customer_ltv", "dbt_daily_revenue", "dbt_fct_orders",
                                                                                 "int_order_totals"]
    # stored relations: 4 sources + 4 mart tables (views store nothing)
    assert sorted(t["id"] for t in s["tables"]) == sorted(
        [f"CANOT_LAB.RAW.{n}" for n in ("customers", "order_lines", "orders", "products")]
        + [f"CANOT_LAB.DBT_dbt_mart.{n}" for n in ("dbt_customer_ltv", "dbt_daily_revenue", "dbt_fct_orders", "int_order_totals")])
    assert all(t["stockage"] == {"connexion": "dbt:snowflake", "type": "snowflake"} for t in s["tables"])
    # every ref() and source() becomes a link; tests depend on their model
    man = json.loads((FIX / "target" / "manifest.json").read_text(encoding="utf-8"))
    attendus = {(c, p) for c, ps in man["parent_map"].items() for p in ps}
    assert {(x["de"], x["vers"]) for x in s["liens"]} == attendus and len(s["liens"]) == 19
    assert {x["nature"] for x in s["liens"] if x["de"].startswith("model.")} == {"lit"}
    assert {x["nature"] for x in s["liens"] if x["de"].startswith("test.")} == {"depend"}
    fct = mods["model.canot_lab.dbt_fct_orders"]
    assert fct["entrees"] == ["model.canot_lab.int_order_totals", "model.canot_lab.stg_customers", "model.canot_lab.stg_orders"]
    assert fct["executions_n"] == 1 and fct["echecs_n"] == 0 and fct["etat"] == "success" and fct["portabilite"] == "traduisible"
    assert len(fct["empreinte_code"]) == 64 and fct["lignes_code"] == 4 and fct["sql_dynamique"] is False
    lec = {x["vue"]: x for x in s["lectures"]}
    assert lec["target/manifest.json"]["statut"] == "read" and lec["target/run_results.json"]["lignes"] == 16
    assert lec["target/catalog.json"] == {**lec["target/catalog.json"], "statut": "not_read", "raison": "absent or not exposed"}
    dag = next(o for o in s["objets"] if o["kind"] == "dag")
    assert dag["executions_n"] == 1 and dag["echecs_n"] == 0 and dag["detail"]["noeuds_executes_n"] == 16
    assert s["cout_du_scan"]["requetes"] == 0


def test_banc_analyse_drapeaux_et_partage(tmp_path):
    r = analyze(_collect(FIX))
    codes = sorted((f["code"], f["cible"]) for f in r["drapeaux"])
    assert codes == [("F11", "dbt:canot_lab"), ("F12", "B03")]
    assert "schedule not read" in r["drapeaux"][0]["texte"] and "1 run, 0 failures" in r["drapeaux"][0]["texte"]
    assert r["capacites"]["statut"] == "tested" and r["capacites"]["export"] == ["iceberg"]
    assert [x["forme"] for x in r["capacites"]["preuve"]] == ["P1", "P2", "P3"]
    assert r["carte_sortie"] == {"portable_tel_quel": {"donnees": 4}, "traduisible": {"planification": 1, "traitement": 16}}
    assert r["mouvements"]["vivante"] + r["mouvements"]["figée"] == 4  # the 4 mart tables: write read from run_results
    p = partage(r)
    assert p["schema"] == "canot.share/1" and p["famille"] == "fichiers" and p["tables"] == 8
    brut = json.dumps(p, ensure_ascii=False)
    for nom in ("canot_lab", "stg_", "dbt_fct", "CANOT_LAB"):
        assert nom not in brut
    tout = _sorties(r, tmp_path).lower()
    for w in FORBIDDEN:
        assert w not in tout, w
    assert "lower(" not in tout and "coalesce(" not in tout  # no model SQL


@pytest.mark.skipif(not (LAB / "target" / "manifest.json").exists(), reason="lab/dbt/target/manifest.json absent")
def test_projet_reel_du_banc():
    """The real lab/dbt artifacts (dbt build on the bench Snowflake account): the validation behind the "tested" status."""
    s = _collect(LAB)
    assert valider_snapshot(s) == []
    k = _kinds(s)
    assert k["modele_dbt"] == 8 and k["test_dbt"] == 8 and k["source_dbt"] == 4
    vues = [o for o in s["objets"] if o["kind"] == "modele_dbt" and o["detail"]["materialisation"] == "view"]
    tables = [o for o in s["objets"] if o["kind"] == "modele_dbt" and o["detail"]["materialisation"] == "table"]
    assert len(vues) == 4 and len(tables) == 4 and len(s["liens"]) == 19


# --- synthetic: platform-specific constructs, template, canaries -----------------------------------------------------


def _node(man, base_uid, uid, name, **kw):
    n = copy.deepcopy(man["nodes"][base_uid])
    n.update({"unique_id": uid, "name": name, "alias": name, "relation_name": f"CANOT_LAB.DBT_dbt_mart.{name}",
              "original_file_path": f"models/marts/{name}.sql", "fqn": ["canot_lab", "marts", name]})
    for k, v in kw.items():
        if k == "materialized":
            n["config"]["materialized"] = v
        else:
            n[k] = v
    man["nodes"][uid] = n
    man["parent_map"][uid] = n["depends_on"]["nodes"]
    return n


def projet_synthetique(tmp_path, avec_catalogue=True) -> Path:
    man = json.loads((FIX / "target" / "manifest.json").read_text(encoding="utf-8"))
    rr = json.loads((FIX / "target" / "run_results.json").read_text(encoding="utf-8"))
    base = "model.canot_lab.dbt_daily_revenue"
    fct = "CANOT_LAB.DBT_dbt_mart.dbt_fct_orders"
    _node(man, base, "model.canot_lab.m_propre", "m_propre",
          raw_code="{% if var('pays') %}select conditional_true_event(total > 0) over (order by order_date) as e, "
                   f"'{SENTINELLE}' as s from {{{{ ref('dbt_fct_orders') }}}}{{% endif %}}",
          compiled_code=f"select conditional_true_event(total > 0) over (order by order_date) as e, '{SENTINELLE}' as s from {fct}")
    _node(man, base, "model.canot_lab.m_dyn", "m_dyn", materialized="dynamic_table")
    _node(man, base, "model.canot_lab.m_py", "m_py", language="python", raw_code=f"def model(dbt, session):\n    return '{SENTINELLE}'",
          compiled_code=None)
    _node(man, base, "model.canot_lab.m_eph", "m_eph", materialized="ephemeral", relation_name=None)
    _node(man, base, "model.canot_lab.m_brut", "m_brut", compiled_code=None,
          raw_code="select order_id, total from {{ ref('dbt_fct_orders') }} where total > 0")
    _node(man, base, "model.canot_lab.m_secret", "m_secret", compiled_code=f"select 'x' as password, '{SENTINELLE}' as s from {fct}")
    man["macros"]["macro.canot_lab.ma_macro"] = {
        "name": "ma_macro", "resource_type": "macro", "package_name": "canot_lab", "unique_id": "macro.canot_lab.ma_macro",
        "original_file_path": "macros/ma_macro.sql", "macro_sql": f"{{% macro ma_macro() %}} '{SENTINELLE}' {{% endmacro %}}",
        "depends_on": {"macros": []}}
    man["exposures"]["exposure.canot_lab.tableau_de_bord"] = {
        "unique_id": "exposure.canot_lab.tableau_de_bord", "name": "tableau_de_bord", "type": "dashboard", "maturity": "high",
        "owner": {"email": EMAIL, "name": "Jean"}, "url": f"https://bi.exemple.test/{SENTINELLE}", "description": SENTINELLE,
        "depends_on": {"nodes": ["model.canot_lab.dbt_customer_ltv"]}}
    src = copy.deepcopy(man["sources"]["source.canot_lab.raw.orders"])
    src.update({"unique_id": "source.canot_lab.raw.ext", "name": "ext", "identifier": "ext", "relation_name": "CANOT_LAB.RAW.ext",
                "external": {"location": f"s3://compartiment-x/{SENTINELLE}/data/", "file_format": "parquet"}})
    man["sources"][src["unique_id"]] = src
    for r in rr["results"]:
        if r["unique_id"].startswith("test.canot_lab.unique_dbt_fct_orders"):
            r.update({"status": "fail", "failures": 3, "message": f"Got 3 results {SENTINELLE}"})
    d = tmp_path / "projet" / "target"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (d / "run_results.json").write_text(json.dumps(rr), encoding="utf-8")
    if avec_catalogue:
        cols = {"customers": [("customer_id", "NUMBER"), ("email", "TEXT"), ("country", "TEXT"), ("segment", "TEXT"),
                              ("signed_up_at", "TIMESTAMP_NTZ")],
                "orders": [("order_id", "NUMBER"), ("customer_id", "NUMBER"), ("order_ts", "TIMESTAMP_NTZ"), ("total", "NUMBER(12,2)"),
                           ("status", "TEXT"), ("payment_method", "TEXT")]}
        cat = {"metadata": {}, "nodes": {}, "sources": {
            f"source.canot_lab.raw.{t}": {"metadata": {"type": "BASE TABLE", "database": "CANOT_LAB", "schema": "RAW", "name": t,
                                                       "owner": "CANOT_LAB_OWNER"},
                                          "columns": {c: {"name": c, "type": ty, "index": i + 1} for i, (c, ty) in enumerate(cs)},
                                          "stats": {"row_count": {"value": 1000, "include": True}, "bytes": {"value": 4096, "include": True}}}
            for t, cs in cols.items()}}
        (d / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
    return d.parent


def test_synthetique_portabilite_et_drapeaux(tmp_path):
    s = _collect(projet_synthetique(tmp_path))
    assert valider_snapshot(s) == []
    o = {x["id"]: x for x in s["objets"]}
    m = o["model.canot_lab.m_propre"]
    assert m["portabilite"] == "a_reconstruire" and m["detail"]["fonctions_propres"] == ["CONDITIONAL_TRUE_EVENT"]
    assert m["sql_dynamique"] is True and m["detail"]["jinja_blocs_n"] == 1
    assert o["model.canot_lab.m_dyn"]["portabilite"] == "a_reconstruire"
    assert "dynamic_table specific to the snowflake adapter" in o["model.canot_lab.m_dyn"]["detail"]["raison_portabilite"]
    assert o["model.canot_lab.m_py"]["portabilite"] == "non_determine" and o["model.canot_lab.m_py"]["langage"] == "python"
    assert o["model.canot_lab.m_eph"]["portabilite"] == "traduisible" and o["model.canot_lab.m_eph"]["sorties"] == []
    assert o["model.canot_lab.m_brut"]["detail"]["analyse"] == "raw" and o["model.canot_lab.m_brut"]["portabilite"] == "traduisible"
    sec = o["model.canot_lab.m_secret"]
    assert sec["empreinte_code"] is None and sec["portabilite"] == "non_determine" and s["rejouabilite"]["secrets_ecartes"]["familles"] == 1
    # catalog read: DuckDB EXPLAIN on the skeleton; staging views read typed sources
    assert o["model.canot_lab.stg_orders"]["detail"]["categorie"] == "C0"
    assert o["model.canot_lab.stg_customers"]["detail"]["categorie"] == "C0"
    assert s["rejouabilite"]["categories"]["C0"]["familles"] >= 2 and s["rejouabilite"]["taux"]["familles"]["nature"] == "computed"
    t = {x["id"]: x for x in s["tables"]}
    assert t["CANOT_LAB.RAW.orders"]["lignes"] == 1000 and t["CANOT_LAB.RAW.orders"]["octets_actifs"] == 4096
    assert t["CANOT_LAB.RAW.orders"]["proprietaire"].startswith("r-") and t["CANOT_LAB.RAW.ext"]["type"] == "EXTERNE"
    assert o["model.canot_lab.ma_macro".replace("model", "macro")]["kind"] == "macro_dbt"
    assert o["exposure.canot_lab.tableau_de_bord"]["proprietaire"].startswith("u-")
    dag = o["dbt:canot_lab"]
    assert dag["echecs_n"] == 1 and dag["detail"]["noeuds_en_echec_n"] == 1
    test = next(x for x in s["objets"] if x["id"].startswith("test.canot_lab.unique_dbt_fct_orders"))
    assert test["echecs_n"] == 1 and test["detail"]["lignes_en_echec"] == 3

    r = analyze(s)
    by = {}
    for f in r["drapeaux"]:
        by.setdefault(f["code"], set()).add(f["cible"])
    assert by["F13"] == {"model.canot_lab.m_propre", "model.canot_lab.m_dyn"}
    assert by["F08"] == {"model.canot_lab.m_propre"}  # generic tests (dbt-core macros) do not count
    assert by["F01"] == {"CANOT_LAB.RAW.ext"} and by["F11"] == {"dbt:canot_lab"}
    f13 = next(f for f in r["drapeaux"] if f["cible"] == "model.canot_lab.m_propre" and f["code"] == "F13")
    assert "function absent from DuckDB: CONDITIONAL_TRUE_EVENT" in f13["texte"]
    assert "1 failure" in next(f for f in r["drapeaux"] if f["code"] == "F11")["texte"]
    fr = analyze(s, lang="fr")
    f13_fr = next(f for f in fr["drapeaux"] if f["cible"] == "model.canot_lab.m_propre" and f["code"] == "F13")
    assert "fonction absente de DuckDB : CONDITIONAL_TRUE_EVENT" in f13_fr["texte"] and "à reconstruire : compté, non rejoué" in f13_fr["texte"]
    assert "1 échec" in next(f for f in fr["drapeaux"] if f["code"] == "F11")["texte"]
    assert not {"F14", "F15", "F16"} & set(by)


def test_aucun_code_ni_canari_dans_les_sorties(tmp_path):
    s = _collect(projet_synthetique(tmp_path))
    r, fr = analyze(s), analyze(s, lang="fr")
    out = tmp_path / "sorties"
    tout = _sorties(r, out, fr)  # both panes of scan.html
    for x in (SENTINELLE, EMAIL, "password", "conditional_true_event(", "def model", "compartiment-x/"):
        assert x not in tout, x
    assert "s3://compartiment-x" in tout  # location reduced to the bucket


def test_sans_catalogue_ni_run_results(tmp_path):
    d = projet_synthetique(tmp_path, avec_catalogue=False)
    (d / "target" / "run_results.json").unlink()
    s = _collect(d)
    lec = {x["vue"].split("/")[-1]: x for x in s["lectures"]}
    assert lec["run_results.json"]["statut"] == "not_read" and lec["catalog.json"]["statut"] == "not_read"
    mod = next(o for o in s["objets"] if o["id"] == "model.canot_lab.stg_orders")
    assert mod["executions_n"] is None and mod["echecs_n"] is None  # not read: never 0
    assert mod["detail"]["categorie"] is None and mod["portabilite"] == "traduisible"
    assert s["rejouabilite"]["taux"]["familles"]["nature"] == "not_estimated"
    r = analyze(s)
    assert {f["cible"] for f in r["drapeaux"] if f["code"] == "F12"} == {lec["run_results.json"]["id"], lec["catalog.json"]["id"]}


def test_manifest_illisible_lecture_non_lue(tmp_path):
    d = tmp_path / "p" / "target"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    s = _collect(d.parent)
    assert s["lectures"][0]["statut"] == "not_read" and s["tables"] == [] and valider_snapshot(s) == []


def test_aucun_reseau_pendant_la_collecte(monkeypatch, tmp_path):
    d = projet_synthetique(tmp_path)

    def interdit(*a, **k):
        raise AssertionError("network connection attempted")

    monkeypatch.setattr(socket, "socket", interdit)
    monkeypatch.setattr(socket, "create_connection", interdit)
    assert _collect(d)["objets"]


# --- URL, registry, CLI ---------------------------------------------------------------------------------------


def test_formes_d_url(tmp_path):
    cible = (FIX / "target").resolve()
    for u in (f"dbt://{FIX}", f"dbt://{FIX}/target", f"dbt://{FIX}/target/manifest.json", f"dbt+file://{FIX}"):
        assert dbt_scan.ProjetDbt(u).cible == cible
    assert connectors.for_url(str(FIX)).name == "dbt"  # project folder without a scheme
    assert connectors.for_url(str(tmp_path)) is None
    with pytest.raises(ValueError, match="dbt URL: no manifest.json"):
        connectors.open_url(f"dbt://{tmp_path}")


def test_registre_et_niveau():
    c = connectors.get("dbt")
    assert c.famille == "fichiers" and c.status == "tested" and c.capabilities.inventaire == "complet"
    assert c.capabilities.niveaux == (1,) and c.capabilities.export == ("iceberg",) and c.capabilities.preuve == ("P1", "P2", "P3")
    assert connectors.effective_level(dbt_scan.ProjetDbt(f"dbt://{FIX}"), 3) == 1
    assert "full (level 1)" in connectors.table()


def test_cli_scan_bout_en_bout(tmp_path, capsys):
    from canot.cli import run as cli_run

    out = tmp_path / "out"
    assert cli_run(["scan", f"dbt://{FIX}", "--out", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "level 1" in stdout and "To review: F11 ×1, F12 ×1" in stdout
    for f in ("scan.json", "scan.html", "tables.csv", "flags.csv", "families.csv", "reads.csv", "scan-share.json"):
        assert (out / f).exists(), f
    r = json.loads((out / "scan.json").read_text(encoding="utf-8"))
    assert r["niveau_droits"] == 1 and r["familles"] == [] and r["plateforme"] == "dbt"
    assert json.loads((out / "scan-share.json").read_text(encoding="utf-8"))["schema"] == "canot.share/1"


def test_cli_drill_refuse_un_projet_dbt(tmp_path, capsys):
    from canot.cli import main

    assert main(["drill", f"dbt://{FIX}", str(tmp_path / "lac")]) == 1  # artifacts only: no dbt_project.yml, so no profile
    assert "no dbt_project.yml next to target/" in capsys.readouterr().err


def test_include_sql_text_sans_objet(tmp_path, capsys):
    from canot.cli import run as cli_run

    out = tmp_path / "out"
    assert cli_run(["scan", f"dbt://{FIX}", "--out", str(out), "--include-sql-text"]) == 0
    assert "does not apply here" in capsys.readouterr().out and not (out / "scan-sql-brut.json").exists()
