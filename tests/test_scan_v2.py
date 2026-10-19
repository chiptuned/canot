"""canot.scan/2 contract (additive): validator, F13-F16 flags on a synthetic snapshot, report and share file driven by capabilities."""
import copy
import hashlib
import html
import json
import re
from pathlib import Path

import pytest

from canot import agent, connectors
from canot.scanner.analyze import CODES_HORS_ENTREPOT, LIBELLES, analyze
from canot.scanner.model import valider_snapshot
from canot.scanner.render import render_html, write_csv
from canot.scanner.run import partage

FIX = Path(__file__).parent / "fixtures"
V1 = ("snowflake", "bigquery", "degrade")
SENTINELLES = ("CANOT_SENTINELLE_7f3a", "password")
FORBIDDEN = ("savings", "waste", "wasted", "useless", "unused", "critical", "urgent", "easy", "guaranteed", "compliant", "certified",
             "row by row",
             # French equivalents: stay vigilant on leftovers
             "économie", "gaspillage", "inutile", "inutilisé", "critique", "facile", "garanti", "conforme", "certifié", "ligne par ligne")


def snap(name: str) -> dict:
    return json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8"))


def synth() -> dict:
    return snap("dataiku_synthetique")


def flags(r: dict, code: str) -> dict[str, dict]:
    return {f["cible"]: f for f in r["drapeaux"] if f["code"] == code}


def obj(kind: str, i: str, **kw) -> dict:
    return {"kind": kind, "id": i, "reference": [], "detail": {}, **kw}


def mini(*objets) -> dict:
    return {"schema": "canot.scan/2", "plateforme": "essai", "as_of": "2026-10-19T10:00:00Z", "lectures": [], "tables": [],
            "objets": list(objets), "metering": None}


# --- contract -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", V1 + ("dataiku_synthetique",))
def test_les_fixtures_v1_et_v2_passent_le_validateur(name):
    assert valider_snapshot(snap(name)) == []


def test_le_validateur_refuse_code_brut_et_valeurs_hors_contrat():
    s = synth()
    s["objets"][0]["sql"] = "select 1"
    s["objets"][1]["portabilite"] = "portable"
    s["objets"][2]["empreinte_code"] = "abc"
    s["liens"].append({"de": "a", "nature": "lit"})
    s["schema"] = "canot.scan/9"
    err = " | ".join(valider_snapshot(s))
    for attendu in ("raw field refused (sql)", "portability 'portable'", "is not a sha256", "incomplete", "unexpected schema"):
        assert attendu in err


@pytest.mark.parametrize("name", V1)
def test_scan_json_v2_est_v1_plus_des_champs(name):
    r = analyze(snap(name))
    assert r["schema"] == "canot.scan/2" and r["famille"] == "entrepot" and r["liens"] == [] and r["carte_sortie"] == {}
    c = r["capacites"]
    assert c == connectors.get(r["plateforme"]).capacites()
    assert c["statut"] == "tested" and [p["forme"] for p in c["preuve"]] == ["P1", "P2"] and c["teste_sur"].startswith("canot drill, 2026-10-1")
    for o in r["objets"]:  # /1 objects unchanged: no /2 field added
        assert not {"portabilite", "famille_objet", "formats_export", "empreinte_code", "extensions"} & set(o)
    assert all("stockage" not in t for t in r["tables"])


# --- flags F13 to F16 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", V1)
def test_f13_a_f16_jamais_sur_les_fixtures_entrepot(name):
    assert not [f for f in analyze(snap(name))["drapeaux"] if f["code"] in CODES_HORS_ENTREPOT]


def test_fixture_synthetique_drapeaux_attendus():
    r = analyze(synth())
    assert set(flags(r, "F13")) == {"BANC.prepare_ventes", "BANC.groupe_ventes", "BANC.geo_enrichit"}
    assert set(flags(r, "F14")) == {"PLUGIN.geo"}
    assert set(flags(r, "F15")) == {"BANC.modele_churn", "BANC.modele_zip"}
    assert set(flags(r, "F16")) == {"MODELE.marge_cumulee", "MODELE.ratio_annuel"}
    assert set(flags(r, "F11")) == {"BANC.nuit"} and flags(r, "F11")["BANC.nuit"]["preuve"]["echecs_n"] == 1
    # negatives: translatable, code read with no license, open format, measure declared trivial or too short
    tous = {f["cible"] for f in r["drapeaux"]}
    for i in ("BANC.jointure_sql", "BANC.score_py", "PLUGIN.ouvert", "BANC.modele_mlflow", "MODELE.ventes_total", "MODELE.compte", "BANC.exploration"):
        assert i not in tous, i
    for f in r["drapeaux"]:
        assert f["etiquette"] == "To review" and f["libelle"] == LIBELLES[f["code"]]
        for w in FORBIDDEN:
            assert w not in (f["texte"] + f["libelle"]).lower(), (w, f["texte"])


def test_f13_seulement_sur_type_lu_ou_traitement_a_reconstruire():
    r = analyze(mini(obj("recette_visuelle", "a"), obj("recette_visuelle", "b", portabilite="traduisible"),
                     obj("flux_etl", "c", famille_objet="traitement", portabilite="a_reconstruire"),
                     obj("flux_etl", "d", famille_objet="traitement"), obj("vue", "e", portabilite="a_reconstruire")))
    assert set(flags(r, "F13")) == {"a", "c"}


def test_f14_licence_usage_et_recette_de_plugin_sans_plugin_inventorie():
    h = hashlib.sha256(b"x").hexdigest()
    r = analyze(mini(obj("plugin", "p_lic", empreinte_code=h, detail={"licence": "commerciale"}), obj("plugin", "p_ouvert", empreinte_code=h),
                     obj("recette_plugin", "r1", extensions=["p_absent"])))
    f = flags(r, "F14")
    assert set(f) == {"p_lic", "r1"}
    assert "license commerciale" in f["p_lic"]["texte"] and "no use observed" in f["p_lic"]["texte"]
    assert "not inventoried (p_absent)" in f["r1"]["texte"]
    seul = analyze(mini(obj("plugin", "p")))
    assert "use per object not read" in flags(seul, "F14")["p"]["texte"]


def test_f15_non_lu_dit_non_determine_jamais_non_exportable():
    r = analyze(mini(obj("modele_entraine", "m_absent"), obj("modele_entraine", "m_none", formats_export=None),
                     obj("modele_entraine", "m_vide", formats_export=[]), obj("modele_entraine", "m_onnx", formats_export=["ONNX"]),
                     obj("modele_ml", "bq_v1")))  # BigQuery /1 kind: no /2 data, no F15
    f = flags(r, "F15")
    assert set(f) == {"m_absent", "m_none", "m_vide"}
    for i in ("m_absent", "m_none"):
        assert "not determined" in f[i]["texte"] and "not exportable" not in f[i]["texte"] and f[i]["preuve"]["determine"] is False
    assert "no format read" in f["m_vide"]["texte"] and f["m_vide"]["preuve"]["determine"] is True


def test_f16_critere_declare_puis_nombre_de_lignes():
    r = analyze(mini(obj("mesure", "a", non_triviale=True), obj("mesure", "b", non_triviale=False, lignes_code=50),
                     obj("calcul_rapport", "c", lignes_code=5), obj("mesure", "d", lignes_code=4), obj("mesure", "e")))
    assert set(flags(r, "F16")) == {"a", "c"}


# --- outputs: no raw code, share file, report ---------------------------------------------------------------


def _sorties(r: dict, tmp_path) -> str:
    r = {**r, "avec_sql_brut": False}
    write_csv(r, tmp_path)
    return (json.dumps(r, ensure_ascii=False) + render_html(r) + json.dumps(partage(r), ensure_ascii=False)
            + "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.csv")))


def test_aucune_sentinelle_dans_les_sorties(tmp_path):
    s = synth()
    assert any("CANOT_SENTINELLE_7f3a" in json.dumps(o) for o in s["objets"])  # the fixture does contain it
    tout = _sorties(analyze(s), tmp_path)
    for x in SENTINELLES:
        assert x not in tout, x


def test_partage_reste_partage_1_avec_champs_ajoutes():
    r = analyze(synth())
    p = partage(r)
    assert p["schema"] == "canot.share/1" and p["famille"] == "science_donnees"
    assert p["capacites"] == {"inventaire": "partiel", "export": [], "preuve": [], "statut": "written, untested"}
    assert p["portabilite"] == {"a_reconstruire": 7, "exporte_proprietaire": 1, "non_determine": 1, "portable_tel_quel": 1, "traduisible": 6}
    brut = json.dumps(p, ensure_ascii=False)
    for nom in ("BANC.", "PLUGIN.", "MODELE.", "postgres-banc"):
        assert nom not in brut
    sf = partage(analyze(snap("snowflake")))
    assert sf["capacites"] == {"inventaire": "complet", "export": ["iceberg"], "preuve": ["P1", "P2"], "statut": "tested"}


def test_partage_d_un_scan_v1_inchange():
    r = analyze(snap("bigquery"))
    v1 = {k: v for k, v in r.items() if k not in ("famille", "capacites", "liens", "carte_sortie")} | {"schema": "canot.scan/1"}
    assert set(partage(v1)) == {"schema", "as_of", "plateforme", "niveau_droits", "resume", "tables", "objets", "constats",
                                "rejouabilite_familles_pct", "lectures", "versions"}


def _text(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def test_rapport_affiche_les_capacites_honnetement():
    t = _text(render_html({**analyze(snap("snowflake")), "avec_sql_brut": False}))
    cap = t[t.index("Capabilities"):t.index("Observed costs")]
    assert "inventoried, neither exported nor replayed" in cap and "not done by this scan" in cap and "iceberg, with canot drill" in cap
    assert "full, levels 1 to 3" in cap
    assert "P1 data parity; P2 query replay" in cap and "Snowflake connector: tested; recorded test: canot drill, 2026-10-17" in cap
    assert "Exit map" not in t
    for name in ("bigquery", "degrade"):  # "No finding for": never F13-F16 on a warehouse
        b = _text(render_html({**analyze(snap(name)), "avec_sql_brut": False}))
        aucun = b[b.index("No finding for"):].split(".")[0]
        assert "F12" in aucun or "F01" in aucun or "F04" in aucun
        assert not any(c in aucun for c in CODES_HORS_ENTREPOT)
    d = _text(render_html({**analyze(synth()), "avec_sql_brut": False}))
    assert "Exit scan: Dataiku" in d and "No form of evidence is declared for Dataiku" in d
    assert "no test date recorded in the repository" in d and "Exit map" in d and "to rebuild" in d
    for w in FORBIDDEN:
        assert w not in d.lower(), w


def test_rapport_v1_sans_capacites_ne_montre_pas_la_section():
    r = {k: v for k, v in analyze(snap("bigquery")).items() if k not in ("famille", "capacites", "liens", "carte_sortie")}
    t = _text(render_html({**r, "schema": "canot.scan/1", "avec_sql_brut": False}))
    assert "Capabilities" not in t and "Exit scan: BigQuery" in t and "tables.getData" in t


def test_droits_lus_dans_la_fiche_du_connecteur(monkeypatch):
    import canot.scanner.render as render

    assert not hasattr(render, "PLATEFORMES") and not hasattr(render, "DROITS") and not hasattr(render, "DROITS_JAMAIS")
    fiche = copy.deepcopy(connectors.get("snowflake").profile)
    fiche["droits_jamais"] = "JAMAIS_ESSAI"
    monkeypatch.setattr(type(connectors.get("snowflake")), "profile", fiche)
    assert "JAMAIS_ESSAI" in render_html({**analyze(snap("snowflake")), "avec_sql_brut": False})


# --- MCP ------------------------------------------------------------------------------------------------------


def test_expliquer_scan_lit_v1_et_v2(tmp_path):
    r = analyze(synth())
    p = tmp_path / "scan.json"
    p.write_text(json.dumps(r), encoding="utf-8")
    e = agent.explain_scan(str(p))
    assert e["family"] == "science_donnees" and e["not_provable"]["findings"] == {"F13": 3, "F14": 1, "F15": 2, "F16": 2}
    assert e["not_provable"]["objects_to_rebuild"] == 7 and e["not_provable"]["declared_evidence_forms"] == []
    p.write_text(json.dumps({**r, "schema": "canot.scan/1"}), encoding="utf-8")
    assert agent.explain_scan(str(p))["not_provable"] is None


def test_outil_capacites_lit_le_registre(monkeypatch):
    toutes = agent.capabilities()
    assert [c["plateforme"] for c in toutes["connectors"]] == list(connectors.CONNECTORS)
    db = agent.capabilities("Databricks")["connectors"][0]
    assert db["statut"] == "written, untested" and db["teste_sur"] is None and db["famille"] == "lakehouse"
    assert "error" in agent.capabilities("oracle")

    class Faux(connectors.Connector):
        name, label, schemes, dialect, url = "faux", "Faux", ("faux",), "faux", "faux://"
        capabilities = connectors.Capabilities("aucun")

    monkeypatch.setitem(connectors.CONNECTORS, "faux", Faux())
    assert agent.capabilities("faux")["connectors"][0]["teste_sur"] is None


def test_chaque_outil_mcp_est_documente():
    doc = agent.read_doc("commands")
    for fn in agent.TOOLS:
        assert f"| `{fn.__name__}" in doc, fn.__name__
