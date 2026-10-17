"""analyze() : fonction pure snapshot -> scan.json. Un test par règle de la spec (§2, §4, §7), cas positifs et négatifs."""
import copy
import json
import random
from pathlib import Path

import pytest

from canot import prices
from canot.scan import THROUGHPUT_MBPS
from canot.scanner.analyze import ETIQUETTE, FAMILLE_KEYS, REBUILD_KINDS, analyze
from canot.scanner.model import NATURES, fig

FIX = Path(__file__).parent / "fixtures"
CANARI = "canari-7f3a@exemple.test"
FORBIDDEN = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti",
             "conforme", "certifié", "ligne par ligne")
ALL = ("snowflake", "bigquery", "degrade")


def snap(name: str) -> dict:
    return json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8"))


def flags(res: dict, code: str) -> dict:
    return {d["cible"]: d for d in res["drapeaux"] if d["code"] == code}


def set_lecture(s: dict, leaf: str, **kw) -> None:
    hits = [x for x in s["lectures"] if x["vue"].split(".")[-1] == leaf]
    assert hits, leaf
    for x in hits:
        x.update(kw)


def figs(o):
    if isinstance(o, dict):
        if "nature" in o and "valeur" in o and "base" in o:
            yield o
        for v in o.values():
            yield from figs(v)
    elif isinstance(o, list):
        for v in o:
            yield from figs(v)


def table(res: dict, tid: str) -> dict:
    return next(t for t in res["tables"] if t["id"] == tid)


# --- chiffres --------------------------------------------------------------------------------------------


def test_fig_estime_impose_une_fourchette():
    with pytest.raises(ValueError):
        fig(3, nature="estimé")
    assert fig(nature="estimé", bas=1, haut=2)["bas"] == 1


def test_fig_non_lu_impose_valeur_nulle_et_raison():
    with pytest.raises(ValueError):
        fig(0, nature="non_lu", raison="droit absent")
    with pytest.raises(ValueError):
        fig(nature="non_estimé")
    assert fig(nature="non_lu", raison="droit absent")["valeur"] is None


@pytest.mark.parametrize("name", ALL)
def test_tous_les_chiffres_respectent_les_regles(name):
    res = analyze(snap(name))
    found = list(figs(res))
    assert found
    for f in found:
        assert f["nature"] in NATURES
        if f["nature"] == "estimé":
            assert f["bas"] is not None and f["haut"] is not None and f["bas"] <= f["haut"]
        if f["nature"] in ("non_lu", "non_estimé"):
            assert f["valeur"] is None and f["raison"]


def test_une_valeur_manquante_ne_vaut_jamais_zero():
    s = snap("degrade")
    assert s["metering"]["par_service"] == []  # la collecte a rendu une liste vide : la lecture, elle, a échoué
    res = analyze(s)
    assert res["couts"]["credits"]["valeur"] is None and res["couts"]["credits"]["nature"] == "non_lu"
    assert "METERING_DAILY_HISTORY" in res["couts"]["credits"]["raison"]
    assert res["couts"]["monnaie"]["valeur"] is None
    assert res["fourchettes"]["volume"]["valeur"] is None and res["fourchettes"]["volume"]["nature"] == "non_lu"
    assert res["fourchettes"]["duree_lecture"]["nature"] == "non_estimé"
    assert res["ne_part_pas"]["historique"]["octets"]["valeur"] is None
    assert all(t["lectures"]["n"] is None and t["octets_actifs"] is None for t in res["tables"])


def test_zero_lu_reste_un_zero_mesure():
    s = snap("snowflake")
    s["metering"]["par_service"] = []
    res = analyze(s)
    assert res["couts"]["credits"]["valeur"] == 0 and res["couts"]["credits"]["nature"] == "calculé"


def test_prix_fourni_ou_fourchette_catalogue_datee():
    s = snap("snowflake")
    paid = analyze(s, credit_price=3.0)["couts"]["monnaie"]
    assert paid["nature"] == "calculé" and paid["valeur"] == round(43.5 * 3.0, 2)
    cat = analyze(s)["couts"]["monnaie"]
    lo, hi = min(prices.SNOWFLAKE_CREDIT_USD.values()), max(prices.SNOWFLAKE_CREDIT_USD.values())
    assert cat["nature"] == "estimé" and cat["valeur"] is None
    assert (cat["bas"], cat["haut"]) == (round(43.5 * lo, 2), round(43.5 * hi, 2))
    assert any(prices.SNOWFLAKE_SOURCE in h and prices.AS_OF in h for h in cat["hypotheses"])
    assert cat["base"] == ["S05"]


def test_a_la_demande_et_reserve_jamais_additionnes():
    res = analyze(snap("bigquery"))
    c = res["couts"]
    assert set(c) >= {"a_la_demande", "reserve"}
    assert not [k for k in c if "total" in k] and "credits" not in c
    od, rs = c["a_la_demande"], c["reserve"]
    assert od["octets_factures"]["nature"] == "mesuré" and rs["slot_heures"]["valeur"] == 820.5
    assert rs["monnaie"]["nature"] == "non_estimé" and rs["monnaie"]["raison"] == "prix de votre contrat non lu"
    assert od["monnaie"]["nature"] == "estimé"
    assert any("palier gratuit" in h for h in od["monnaie"]["hypotheses"])
    assert any("politique de lignes" in h for h in od["monnaie"]["hypotheses"])
    assert "ne s'additionnent pas" in " ".join(res["resume"]["DAF"])


def test_fourchettes_debit_et_reprise():
    res = analyze(snap("snowflake"))
    fo = res["fourchettes"]
    v = fo["volume"]["valeur"]
    assert v == sum(t["octets_actifs"] for t in res["tables"] if not t["supprimee_le"] and t["type"] != "EXTERNE")
    assert fo["duree_lecture"]["bas"] == round(v / 1e6 / THROUGHPUT_MBPS[1] / 3600, 2)
    assert fo["duree_lecture"]["haut"] == round(v / 1e6 / THROUGHPUT_MBPS[0] / 3600, 2)
    n_obj = sum(o["kind"] in REBUILD_KINDS for o in res["objets"])
    n_cols = sum(len(t["colonnes_non_portables"]) for t in res["tables"] if not t["supprimee_le"])
    assert n_cols == 2  # VARIANT, GEOGRAPHY
    assert [fo["reprise"]["bas"], fo["reprise"]["haut"]] == prices.rework_days(n_obj, n_cols)
    bq = analyze(snap("bigquery"))["fourchettes"]
    assert "credits_lecture" not in bq and bq["cout_lecture"]["raison"] == "prix de votre contrat non lu"


def test_cout_du_scan():
    sf = analyze(snap("snowflake"))["cout_du_scan"]
    assert sf["requetes"] == fig(35, nature="mesuré", unite="requêtes") and sf["duree"]["valeur"] == 412.0
    dg = analyze(snap("degrade"))["cout_du_scan"]
    # 5 lues + 6 refusées (droit absent) + 1 délai dépassé ; les lectures non demandées ne partent pas
    assert dg["requetes"]["valeur"] == 12 and dg["requetes"]["nature"] == "calculé"
    assert dg["duree"]["nature"] == "non_lu" and dg["lectures"] == {"total": 19, "lu": 5, "non_lu": 14}


def test_rejouabilite_taux_et_mention():
    rj = analyze(snap("snowflake"))["rejouabilite"]
    assert rj["taux"]["familles"]["valeur"] == 66.7 and rj["mention"] == "accepté ≠ résultat identique"
    assert "C7" in rj["taux"]["familles"]["formule"]
    dg = analyze(snap("degrade"))["rejouabilite"]
    assert dg["statut"] == "non_lu" and "QUERY_HISTORY" in dg["raison"]


# --- mouvement, édition, périmètre -------------------------------------------------------------------------


def test_mouvement_figee_vivante_recreee():
    res = analyze(snap("snowflake"))
    assert table(res, "CANOT_LAB.RAW.ARCHIVE_1")["mouvement"] == "figée"
    assert table(res, "CANOT_LAB.RAW.ORDERS")["mouvement"] == "vivante"
    assert table(res, "CANOT_LAB.MART.FCT_ORDERS")["mouvement"] == "recréée"
    assert table(res, "CANOT_LAB.MART.DIM_LEGACY")["mouvement"] is None
    assert table(res, "CANOT_LAB.RAW.TMP_DROPPED")["mouvement"] is None
    assert res["mouvements"]["recréée"] == 1


def test_mouvement_recreee_sans_ecritures_lues_le_dit():
    res = analyze(snap("degrade"))
    t = table(res, "PROD.MART.KPI")
    assert t["mouvement"] == "recréée" and "écritures non lues" in t["mouvement_preuve"]
    assert table(res, "PROD.RAW.ARCHIVE_2019")["mouvement"] is None  # DDL ancienne, écritures non lues : on ne devine pas


def test_edition_non_determinee_si_access_history_refusee():
    assert analyze(snap("degrade"))["edition"] == "non déterminée (Standard ou droit absent)"
    assert analyze(snap("snowflake"))["edition"] == "enterprise"


def test_datasets_caches_signales():
    assert analyze(snap("bigquery"))["schemas_caches"] == ["_tmp"]


# --- drapeaux --------------------------------------------------------------------------------------------


def test_drapeaux_attendus_du_banc_snowflake():
    res = analyze(snap("snowflake"))
    assert {d["code"] for d in res["drapeaux"]} == {f"F{i:02d}" for i in range(1, 13)}


@pytest.mark.parametrize("name", ALL)
def test_tous_les_drapeaux_a_examiner_sans_gravite(name):
    for d in analyze(snap(name))["drapeaux"]:
        assert d["etiquette"] == ETIQUETTE == "À examiner"
        assert not {"severite", "sévérité", "gravite", "niveau"} & set(d)


def test_f01_dependance_externe():
    s = snap("snowflake")
    f = flags(analyze(s), "F01")
    assert {"CANOT_LAB.EXT.US_STATES", "CANOT_LAB.EXT.S3_PUBLIC", "SNOWFLAKE_SAMPLE_DATA"} <= set(f)
    assert f["CANOT_LAB.EXT.US_STATES"]["preuve"]["emplacements"] == ["s3://canot-lab-public"]
    # négatifs : stage interne, base système SNOWFLAKE, destination d'export (relève de F03)
    assert not {"CANOT_LAB.RAW.INT_STAGE", "SNOWFLAKE", "CANOT_LAB.OPS.T_EXPORT_NIGHTLY"} & set(f)
    next(o for o in s["objets"] if o["id"] == "CANOT_LAB.EXT.S3_PUBLIC").update(externe=False, dependances_externes=[])
    assert "CANOT_LAB.EXT.S3_PUBLIC" not in flags(analyze(s), "F01")


def test_f01_bigquery_uris():
    f = flags(analyze(snap("bigquery")), "F01")
    assert f["lab-a.ext.gcs_events"]["preuve"]["emplacements"] == ["gs://lab-bucket"]
    assert f["lab-a.ext.sheet_targets"]["preuve"]["emplacements"] == ["docs.google.com"]
    assert "lab-a.raw.orders" not in f


def test_f02_dependance_cachee_avec_chemin():
    f = flags(analyze(snap("snowflake")), "F02")
    assert f["CANOT_LAB.MART.V_KPI"]["preuve"]["chemin"] == ["CANOT_LAB.MART.V_KPI", "CANOT_LAB.STG.V_STATES", "CANOT_LAB.EXT.US_STATES"]
    assert "CANOT_LAB.MART.V_KPI → CANOT_LAB.STG.V_STATES → CANOT_LAB.EXT.US_STATES (s3://canot-lab-public)" in f["CANOT_LAB.MART.V_KPI"]["texte"]
    assert "CANOT_LAB.STG.V_STATES" in f
    assert "CANOT_LAB.STAGING.STG_ORDERS" not in f


def test_f03_ecriture_hors_plateforme_declaree_et_observee():
    s = snap("snowflake")
    f = flags(analyze(s), "F03")
    assert f["CANOT_LAB.OPS.T_EXPORT_NIGHTLY"]["preuve"]["mode"] == "déclaré"
    obs = f["d" * 12]
    assert obs["preuve"]["mode"] == "observé" and obs["preuve"]["emplacements"] == ["s3://canot-lab-export"]
    assert "e" * 12 not in f  # COPY INTO @RAW.INT_STAGE : stage interne
    next(o for o in s["objets"] if o["id"] == "CANOT_LAB.OPS.T_EXPORT_NIGHTLY")["detail"]["ecriture_externe"] = False
    assert "CANOT_LAB.OPS.T_EXPORT_NIGHTLY" not in flags(analyze(s), "F03")


def test_f03_dechargements_observes_vers_un_stage_externe():
    s = snap("snowflake")
    stages = {o["id"]: o for o in s["objets"] if o["kind"] == "stage"}
    stages["CANOT_LAB.EXT.S3_PUBLIC"]["detail"]["ecritures_observees_n"] = 12
    stages["CANOT_LAB.RAW.INT_STAGE"]["detail"]["ecritures_observees_n"] = 40  # stage interne : pas hors plateforme
    f = flags(analyze(s), "F03")
    assert f["CANOT_LAB.EXT.S3_PUBLIC"]["preuve"]["mode"] == "observé" and "12 écritures" in f["CANOT_LAB.EXT.S3_PUBLIC"]["texte"]
    assert "CANOT_LAB.RAW.INT_STAGE" not in f


def test_f04_role_sans_titulaire():
    res = analyze(snap("snowflake"))
    f = flags(res, "F04")
    assert set(f) == {"LAB_ORPHAN"} and f["LAB_ORPHAN"]["preuve"]["exemples"] == ["CANOT_LAB.SANDBOX.OLD_EXPORT_2023"]
    assert "F04" in table(res, "CANOT_LAB.SANDBOX.OLD_EXPORT_2023")["drapeaux"]
    # LAB_ENGINEER -> SYSADMIN -> ACCOUNTADMIN (1 utilisateur) : tenu par héritage
    assert "F04" not in table(res, "CANOT_LAB.SANDBOX.TEAM_WORK")["drapeaux"]


def test_f04_ne_devine_pas_sans_le_niveau_3():
    s = snap("snowflake")
    set_lecture(s, "GRANTS_TO_USERS", statut="non_lu", raison="droit absent", lignes=None)
    res = analyze(s)
    assert not flags(res, "F04") and not res["f04_lu"]
    assert "F04" in next(d for d in res["drapeaux"] if d["code"] == "F12" and d["preuve"].get("vue", "").endswith("GRANTS_TO_USERS"))["texte"]
    assert "non déterminés" in res["resume"]["RSSI"][-1]
    s2 = snap("snowflake")
    s2["lectures"] = [x for x in s2["lectures"] if "GRANTS" not in x["vue"]]
    res2 = analyze(s2)
    assert not flags(res2, "F04") and "droits" in flags(res2, "F12")


def test_f04_forme_du_collecteur_snowflake():
    s = snap("snowflake")
    s["droits"]["roles"] = [{"nom": r["nom"], "utilisateurs_n": r["utilisateurs"], "parents": r["accorde_a"]} for r in s["droits"]["roles"]]
    for x in s["lectures"]:  # libellés entre parenthèses, comme la collecte les écrit
        if x["vue"].endswith("GRANTS_TO_ROLES"):
            x["vue"] += " (hiérarchie des rôles)"
    assert set(flags(analyze(s), "F04")) == {"LAB_ORPHAN"}
    s["droits"]["roles"].append({"nom": "LAB_ORPHAN", "utilisateurs_n": None, "parents": []})
    s["droits"]["roles"] = [r for r in s["droits"]["roles"] if not (r["nom"] == "LAB_ORPHAN" and r["utilisateurs_n"] == 0)]
    res = analyze(s)
    assert not flags(res, "F04") and "LAB_ORPHAN" in flags(res, "F12")["roles"]["preuve"]["roles"]  # inconnu : on ne devine pas


def test_libelles_de_vue_et_jointure():
    s = snap("snowflake")
    s["lectures"].append({"id": "S36", "vue": "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY × SESSIONS (outil par famille)",
                          "statut": "non_lu", "raison": "droit absent", "lignes": None, "fenetre": None, "latence_max_h": None})
    res = analyze(s)
    assert flags(res, "F12")["S36"]["texte"].endswith("limite : outil par famille de requêtes.")
    assert res["rejouabilite"]["taux"]["familles"]["base"] == ["S24"]  # la jointure ne compte pas comme QUERY_HISTORY


def test_f05_stockage_sans_lecture_fenetre_reelle():
    res = analyze(snap("snowflake"))
    f = flags(res, "F05")
    assert set(f) == {f"CANOT_LAB.RAW.ARCHIVE_{i}" for i in (1, 2, 3)}
    assert ("Aucune lecture observée dans ACCESS_HISTORY du 18/10/2025 au 17/10/2026 ; "
            "non vus : consommateurs de partages, RESULT_SCAN.") in f["CANOT_LAB.RAW.ARCHIVE_2"]["texte"]
    assert f["CANOT_LAB.RAW.ARCHIVE_2"]["confiance"] == "haute"
    assert "S25" in f["CANOT_LAB.RAW.ARCHIVE_2"]["base"]
    assert "Partagée avec 2 consommateurs externes" in f["CANOT_LAB.RAW.ARCHIVE_1"]["texte"]
    # négatifs : lue, lecture inconnue (None), externe, supprimée
    assert not {"CANOT_LAB.RAW.ORDERS", "CANOT_LAB.MART.DIM_LEGACY", "CANOT_LAB.EXT.US_STATES", "CANOT_LAB.RAW.TMP_DROPPED"} & set(f)
    assert "lectures_par_table" in flags(res, "F12")


def test_f05_se_tait_quand_aucune_source_de_lecture_n_est_lue():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", statut="non_lu", raison="édition requise", lignes=None)
    set_lecture(s, "TABLE_QUERY_PRUNING_HISTORY", statut="non_lu", raison="droit absent", lignes=None)
    res = analyze(s)
    assert not flags(res, "F05")
    f12 = next(d for d in res["drapeaux"] if d["code"] == "F12" and d["preuve"].get("vue", "").endswith("ACCESS_HISTORY"))
    assert "F05 non calculé" in f12["texte"]
    assert not flags(analyze(snap("degrade")), "F05")


def test_f05_repli_sur_pruning_en_confiance_moyenne():
    s = snap("snowflake")
    s["edition"] = None
    set_lecture(s, "ACCESS_HISTORY", statut="non_lu", raison="édition requise", lignes=None)
    for t in s["tables"]:  # comme le collecteur : sans ACCESS_HISTORY, n_lectures reste None et l'élagage va dans detail
        t["detail"] = {"requetes_pruning_n": t["n_lectures"]}
        t["n_lectures"] = None
    res = analyze(s)
    f = flags(res, "F05")
    assert set(f) == {f"CANOT_LAB.RAW.ARCHIVE_{i}" for i in (1, 2, 3)}
    assert all(d["confiance"] == "moyenne" and "Aucune lecture observée dans TABLE_QUERY_PRUNING_HISTORY" in d["texte"]
               and "résultats servis par le cache" in d["texte"] for d in f.values())
    assert res["edition"] == "non déterminée (Standard ou droit absent)"


def test_f05_access_history_partielle_le_dit_en_confiance_basse():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", raison="partielle : 30/52 semaines lues, arrêt sur délai dépassé",
                fenetre={"du": "2026-03-21T00:00:00Z", "au": "2026-10-17T07:00:00Z"})
    res = analyze(s)
    d = flags(res, "F05")["CANOT_LAB.RAW.ARCHIVE_2"]
    assert d["confiance"] == "basse" and "du 21/03/2026 au 17/10/2026" in d["texte"] and "n'ont pas été lues" in d["texte"]
    assert "lue en partie" in flags(res, "F12")["S25"]["texte"]


def test_f05_lectures_d_abonnes_observees_pas_de_drapeau():
    s = snap("bigquery")
    next(t for t in s["tables"] if t["id"] == "lab-a.raw.read_by_api_only")["detail"] = {"lectures_partage_n": 4}
    assert "lab-a.raw.read_by_api_only" not in flags(analyze(s), "F05")


def test_f05_fenetre_courte_le_dit():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", fenetre={"du": "2026-07-17T00:00:00Z", "au": "2026-10-17T07:00:00Z"})
    texte = flags(analyze(s), "F05")["CANOT_LAB.RAW.ARCHIVE_2"]["texte"]
    assert "du 17/07/2026 au 17/10/2026" in texte and "plus courte que 365" in texte


def test_f05_bigquery_reserve_api_storage_read():
    f = flags(analyze(snap("bigquery")), "F05")
    assert set(f) == {"lab-a.archive.archive_2023", "lab-a.raw.read_by_api_only"}
    t = f["lab-a.raw.read_by_api_only"]["texte"]
    assert "INFORMATION_SCHEMA.JOBS du 20/04/2026 au 17/10/2026" in t and "API Storage Read" in t and "plus courte" not in t


def test_f06_historique_paye_non_transferable():
    f = flags(analyze(snap("snowflake")), "F06")
    assert set(f) == {"CANOT_LAB.RAW.ORDERS"} and f["CANOT_LAB.RAW.ORDERS"]["preuve"]["octets_historique"] == 6_000_000_000
    assert "rétention 30 jours" in f["CANOT_LAB.RAW.ORDERS"]["texte"]


def test_f07_table_supprimee_encore_facturee():
    f = flags(analyze(snap("snowflake")), "F07")
    assert set(f) == {"CANOT_LAB.RAW.TMP_DROPPED"}  # OLD_DROPPED : plus rien de facturé
    assert "Supprimée le 10/10/2026" in f["CANOT_LAB.RAW.TMP_DROPPED"]["texte"]


def test_f08_sql_dynamique_et_comptes_par_langage():
    res = analyze(snap("snowflake"))
    assert set(flags(res, "F08")) == {"CANOT_LAB.MART.SP_DYNAMIC_PY", "CANOT_LAB.MART.SP_DYNAMIC_JS"}
    assert res["ne_part_pas"]["routines_par_langage"] == {"JAVASCRIPT": 2, "PYTHON": 2, "SQL": 2}
    dg = analyze(snap("degrade"))
    assert not flags(dg, "F08") and "code_des_routines" in flags(dg, "F12")


def test_f09_consommateurs_externes():
    f = flags(analyze(snap("snowflake")), "F09")
    assert set(f) == {"S_CLIENTS"} and f["S_CLIENTS"]["preuve"]["consommateurs"] == 2
    assert "lab-a.hub.listing_sales" in flags(analyze(snap("bigquery")), "F09")
    # forme des collecteurs : comptes_cibles_n (Snowflake SHARES), projets_consommateurs_n (BigQuery SCHEMATA_LINKS)
    s = snap("snowflake")
    for o in s["objets"]:
        if o["kind"] == "partage":
            n = o["detail"].pop("comptes_cibles")
            o["detail"].update(comptes_cibles_n=n, base="CANOT_LAB")
            o["reference"] = []
    f = flags(analyze(s), "F09")
    assert set(f) == {"S_CLIENTS"} and f["S_CLIENTS"]["texte"].startswith("Partage vers 2 comptes cibles ; base CANOT_LAB.")
    b = snap("bigquery")
    next(o for o in b["objets"] if o["kind"] == "partage")["detail"] = {"projets_consommateurs_n": 3}
    assert "3 projets consommateurs" in flags(analyze(b), "F09")["lab-a.hub.listing_sales"]["texte"]


def test_f10_securite_a_reconstruire():
    f = flags(analyze(snap("snowflake")), "F10")
    assert {"CANOT_LAB.RAW.CUSTOMERS", "CANOT_LAB.MART.FCT_ORDERS", "CANOT_LAB.OPS.MP_EMAIL", "CANOT_LAB.OPS.RAP_COUNTRY", "CANOT_LAB.OPS.PII"} == set(f)
    assert "principal non filtré" in f["CANOT_LAB.RAW.CUSTOMERS"]["texte"]
    assert "CANOT_LAB.RAW.ORDERS" not in f
    dg = analyze(snap("degrade"))
    assert not flags(dg, "F10") and "Politiques non lues" in dg["resume"]["RSSI"][0]


def test_f11_chaine_planifiee_et_echecs():
    f = flags(analyze(snap("snowflake")), "F11")
    assert f["CANOT_LAB.OPS.T_REFRESH"]["preuve"]["chaine"] == ["CANOT_LAB.OPS.T_DAILY", "CANOT_LAB.OPS.T_REFRESH"]
    assert "30 échecs" in f["CANOT_LAB.OPS.T_EXPORT_NIGHTLY"]["texte"]
    assert {"CANOT_LAB.OPS.ORDERS_STREAM", "CANOT_LAB.OPS.P_EVENTS", "CANOT_LAB.MART.DT_COUNTRY_REVENUE"} <= set(f)
    assert not {"CANOT_LAB.MART.V_KPI", "CANOT_LAB.MART.SP_DYNAMIC_PY"} & set(f)
    bq = flags(analyze(snap("bigquery")), "F11")
    assert "état désactivée" in bq["lab-a.sq_old"]["texte"]
    s = snap("snowflake")  # forme du collecteur : detail.predecesseurs, noms éventuellement entre guillemets
    t = next(o for o in s["objets"] if o["id"] == "CANOT_LAB.OPS.T_REFRESH")
    t["detail"] = {"predecesseurs": '"CANOT_LAB"."OPS"."T_DAILY"'}
    assert flags(analyze(s), "F11")["CANOT_LAB.OPS.T_REFRESH"]["preuve"]["chaine"] == ["CANOT_LAB.OPS.T_DAILY", "CANOT_LAB.OPS.T_REFRESH"]


def test_f12_lecture_incomplete():
    s = snap("snowflake")
    assert "S33" in flags(analyze(s), "F12")
    set_lecture(s, "STREAMS", statut="lu", raison=None, lignes=1)
    assert "S33" not in flags(analyze(s), "F12")


def test_f12_se_tait_quand_une_lecture_de_repli_couvre():
    res = analyze(snap("bigquery"))
    assert "B06" not in flags(res, "F12")  # TABLE_STORAGE désactivée, tables.get lue
    b06 = next(x for x in res["lectures"] if x["id"] == "B06")
    assert b06["statut"] == "non_lu" and "tables.get" in b06["limite"]
    assert "B15" in flags(res, "F12")


def test_contrat_avec_les_familles_de_replay():
    """La sortie réelle de replay.process_familles traverse analyze : taux repris tels quels, export résolu, rien de brut."""
    from canot.scanner import replay
    from canot.scanner.model import new_salt
    from canot.scanner.render import render_html

    s = snap("snowflake")
    row = {"temps_ms": 10.0, "credits": 0.1, "octets": None, "app": None, "role": "R", "entrepot": "WH"}
    raw = [{**row, "_sql": f"select * from CANOT_LAB.RAW.CUSTOMERS where email = '{CANARI}'", "hash_plateforme": "h1", "n_exec": 5,
            "utilisateur": "alice.martin", "query_type": "SELECT"},
           {**row, "_sql": "copy into @CANOT_LAB.EXT.S3_PUBLIC/out/ from (select * from CANOT_LAB.OPS.EVENTS_LOG)", "hash_plateforme": "h2",
            "n_exec": 3, "utilisateur": "bob.durand", "query_type": "UNLOAD"}]
    s["familles"], s["rejouabilite"] = replay.process_familles(raw, "snowflake", columns=s["tables"], known_functions=set(),
                                                               salt=new_salt(), external_stages=["CANOT_LAB.EXT.S3_PUBLIC"])
    res = analyze(s)
    res["avec_sql_brut"] = False
    out = json.dumps(res, ensure_ascii=False) + render_html(res)
    assert CANARI not in out and "alice.martin" not in out and "bob.durand" not in out
    assert res["rejouabilite"]["taux"] == s["rejouabilite"]["taux"]
    exports = [d for d in res["drapeaux"] if d["code"] == "F03" and d["cible_type"] == "famille"]
    assert [d["preuve"]["emplacements"] for d in exports] == [["s3://canot-lab-public"]]
    assert all(f["nature"] in NATURES for f in figs(res))


# --- pureté, vie privée, vocabulaire -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_fonction_pure_et_deterministe(name):
    s = snap(name)
    before = copy.deepcopy(s)
    a = json.dumps(analyze(s, credit_price=None), sort_keys=True, ensure_ascii=False)
    assert s == before
    assert a == json.dumps(analyze(copy.deepcopy(before)), sort_keys=True, ensure_ascii=False)
    shuffled = copy.deepcopy(before)
    rnd = random.Random(7)
    for k in ("lectures", "tables", "objets", "familles"):
        rnd.shuffle(shuffled[k])
    assert a == json.dumps(analyze(shuffled), sort_keys=True, ensure_ascii=False)


def test_analyse_sans_as_of_refusee():
    s = snap("snowflake")
    del s["as_of"]
    with pytest.raises(ValueError):
        analyze(s)


@pytest.mark.parametrize("name", ALL)
def test_canari_absent_et_uri_reduites(name):
    s = snap(name)
    out = json.dumps(analyze(s), ensure_ascii=False)
    if name == "snowflake":
        assert CANARI in json.dumps(s, ensure_ascii=False)
    assert CANARI not in out and "exemple.test" not in out
    for path in ("canot-lab-public/", "lab-bucket/", "docs.google.com/", "?sig=", "SQL access control"):
        assert path not in out


def test_detail_filtre_cles_sensibles_et_masque_les_valeurs():
    s = snap("bigquery")
    s["tables"][0]["detail"] = {"commentaire": f"contact {CANARI}", "client_ip": "10.9.8.7", "query_text": "select 1",
                                "sources_chargement": "gs://lab-bucket/secret/path.csv", "partitions_n": 12}
    s["objets"][0]["detail"]["error_message"] = "boom"
    out = json.dumps(analyze(s), ensure_ascii=False)
    assert CANARI not in out and "10.9.8.7" not in out and "select 1" not in out and "boom" not in out and "secret/path" not in out
    d = next(t for t in analyze(s)["tables"] if t["id"] == s["tables"][0]["id"])["detail"]
    assert d == {"commentaire": "contact [masqué]", "partitions_n": 12, "sources_chargement": "gs://lab-bucket"}


@pytest.mark.parametrize("name", ALL)
def test_familles_sans_texte_brut(name):
    for f in analyze(snap(name))["familles"]:
        assert set(f) <= set(FAMILLE_KEYS) | {"tables", "fonctions", "sql_normalise", "compartiments"}
        assert not {"texte", "bind_values", "utilisateur", "role"} & set(f)


@pytest.mark.parametrize("name", ALL)
def test_aucun_mot_interdit_dans_le_json(name):
    out = json.dumps(analyze(snap(name)), ensure_ascii=False).lower()
    for w in FORBIDDEN:
        assert w not in out, w


@pytest.mark.parametrize("name", ALL)
def test_resume_trois_phrases_par_destinataire(name):
    r = analyze(snap(name))["resume"]
    assert set(r) == {"DSI", "DAF", "RSSI"}
    for phrases in r.values():
        assert 1 <= len(phrases) <= 3 and all(p.endswith(".") for p in phrases)


def test_non_estime_liste_explicite():
    sf = {i["sujet"]: i["raison"] for i in analyze(snap("snowflake"))["non_estime"]}
    assert sf["Tarif de votre contrat"] == "prix de votre contrat non lu"
    assert sf["Coût du stockage"] == "prix de votre contrat non lu"
    bq = {i["sujet"] for i in analyze(snap("bigquery"))["non_estime"]}
    assert {"Lectures par l'API Storage Read", "Palier gratuit BigQuery", "Réservé, en dollars"} <= bq
    dg = {i["sujet"] for i in analyze(snap("degrade"))["non_estime"]}
    assert {"Crédits consommés", "Volume à copier", "Rejouabilité des requêtes"} <= dg


def test_f12_se_tait_quand_l_api_remplace_la_vue_regionale():
    """Vue régionale refusée mais tables.get lue : l'inventaire n'est pas limité, pas de F12 pour ces vues."""
    from canot.scanner.analyze import FALLBACKS

    for vue in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS"):
        assert FALLBACKS[vue] == ("tables.get",)


def test_f12_se_tait_quand_l_api_remplace_la_vue_regionale():
    """Vue régionale refusée mais tables.get lue : l'inventaire n'est pas limité, pas de F12 pour ces vues."""
    from canot.scanner.analyze import FALLBACKS

    for vue in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS"):
        assert FALLBACKS[vue] == ("tables.get",)
