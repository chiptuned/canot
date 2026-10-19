"""analyze(): pure function snapshot -> scan.json. One test per rule of the spec (§2, §4, §7), positive and negative cases."""
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
FORBIDDEN = ("savings", "waste", "wasted", "useless", "unused", "critical", "urgent", "easy", "guaranteed", "compliant", "certified",
             "row by row",
             # French equivalents: stay vigilant on leftovers
             "économie", "gaspillage", "inutile", "inutilisé", "critique", "facile", "garanti", "conforme", "certifié", "ligne par ligne")
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


# --- figures ---------------------------------------------------------------------------------------------


def test_fig_estime_impose_une_fourchette():
    with pytest.raises(ValueError):
        fig(3, nature="estimated")
    assert fig(nature="estimated", bas=1, haut=2)["bas"] == 1


def test_fig_non_lu_impose_valeur_nulle_et_raison():
    with pytest.raises(ValueError):
        fig(0, nature="not_read", raison="missing privilege")
    with pytest.raises(ValueError):
        fig(nature="not_estimated")
    assert fig(nature="not_read", raison="missing privilege")["valeur"] is None


def test_natures_anglaises_et_anciennes_refusees():
    assert NATURES == ("measured", "computed", "estimated", "not_read", "not_estimated")
    with pytest.raises(ValueError):
        fig(1, nature="mesuré")


@pytest.mark.parametrize("name", ALL)
def test_tous_les_chiffres_respectent_les_regles(name):
    res = analyze(snap(name))
    found = list(figs(res))
    assert found
    for f in found:
        assert f["nature"] in NATURES
        if f["nature"] == "estimated":
            assert f["bas"] is not None and f["haut"] is not None and f["bas"] <= f["haut"]
        if f["nature"] in ("not_read", "not_estimated"):
            assert f["valeur"] is None and f["raison"]


def test_une_valeur_manquante_ne_vaut_jamais_zero():
    s = snap("degrade")
    assert s["metering"]["par_service"] == []  # collection returned an empty list: the read itself failed
    res = analyze(s)
    assert res["couts"]["credits"]["valeur"] is None and res["couts"]["credits"]["nature"] == "not_read"
    assert "METERING_DAILY_HISTORY" in res["couts"]["credits"]["raison"]
    assert res["couts"]["monnaie"]["valeur"] is None
    assert res["fourchettes"]["volume"]["valeur"] is None and res["fourchettes"]["volume"]["nature"] == "not_read"
    assert res["fourchettes"]["duree_lecture"]["nature"] == "not_estimated"
    assert res["ne_part_pas"]["historique"]["octets"]["valeur"] is None
    assert all(t["lectures"]["n"] is None and t["octets_actifs"] is None for t in res["tables"])


def test_zero_lu_reste_un_zero_mesure():
    s = snap("snowflake")
    s["metering"]["par_service"] = []
    res = analyze(s)
    assert res["couts"]["credits"]["valeur"] == 0 and res["couts"]["credits"]["nature"] == "computed"


def test_prix_fourni_ou_fourchette_catalogue_datee():
    s = snap("snowflake")
    paid = analyze(s, credit_price=3.0)["couts"]["monnaie"]
    assert paid["nature"] == "computed" and paid["valeur"] == round(43.5 * 3.0, 2)
    cat = analyze(s)["couts"]["monnaie"]
    lo, hi = min(prices.SNOWFLAKE_CREDIT_USD.values()), max(prices.SNOWFLAKE_CREDIT_USD.values())
    assert cat["nature"] == "estimated" and cat["valeur"] is None
    assert (cat["bas"], cat["haut"]) == (round(43.5 * lo, 2), round(43.5 * hi, 2))
    assert any(prices.SNOWFLAKE_SOURCE in h and prices.AS_OF in h for h in cat["hypotheses"])
    assert cat["base"] == ["S05"]


def test_a_la_demande_et_reserve_jamais_additionnes():
    res = analyze(snap("bigquery"))
    c = res["couts"]
    assert set(c) >= {"a_la_demande", "reserve"}
    assert not [k for k in c if "total" in k] and "credits" not in c
    od, rs = c["a_la_demande"], c["reserve"]
    assert od["octets_factures"]["nature"] == "measured" and rs["slot_heures"]["valeur"] == 820.5
    assert rs["monnaie"]["nature"] == "not_estimated" and rs["monnaie"]["raison"] == "your contract price not read"
    assert od["monnaie"]["nature"] == "estimated"
    assert any("free tier" in h for h in od["monnaie"]["hypotheses"])
    assert any("row access policy" in h for h in od["monnaie"]["hypotheses"])
    assert "do not add up" in " ".join(res["resume"]["DAF"])


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
    assert "credits_lecture" not in bq and bq["cout_lecture"]["raison"] == "your contract price not read"


def test_cout_du_scan():
    sf = analyze(snap("snowflake"))["cout_du_scan"]
    assert sf["requetes"] == fig(35, nature="measured", unite="queries") and sf["duree"]["valeur"] == 412.0
    dg = analyze(snap("degrade"))["cout_du_scan"]
    # 5 read + 6 refused (missing privilege) + 1 timed out; reads that were not requested are not sent
    assert dg["requetes"]["valeur"] == 12 and dg["requetes"]["nature"] == "computed"
    assert dg["duree"]["nature"] == "not_read" and dg["lectures"] == {"total": 19, "lu": 5, "non_lu": 14}


def test_rejouabilite_taux_et_mention():
    rj = analyze(snap("snowflake"))["rejouabilite"]
    assert rj["taux"]["familles"]["valeur"] == 66.7 and rj["mention"] == "accepted ≠ identical result"
    assert "C7" in rj["taux"]["familles"]["formule"]
    dg = analyze(snap("degrade"))["rejouabilite"]
    assert dg["statut"] == "not_read" and "QUERY_HISTORY" in dg["raison"]


def test_ancien_snapshot_francais_normalise():
    """A snapshot written by an older Canot (French logic values) is read with the English values."""
    s = snap("snowflake")
    for x in s["lectures"]:
        x["statut"] = {"read": "lu", "not_read": "non_lu"}[x["statut"]]
    s["mode_sql"] = "lecture seule"
    res = analyze(s)
    assert res == analyze(snap("snowflake"))


# --- movement, edition, scope ------------------------------------------------------------------------------


def test_mouvement_figee_vivante_recreee():
    res = analyze(snap("snowflake"))
    assert table(res, "CANOT_LAB.RAW.ARCHIVE_1")["mouvement"] == "frozen"
    assert table(res, "CANOT_LAB.RAW.ORDERS")["mouvement"] == "live"
    assert table(res, "CANOT_LAB.MART.FCT_ORDERS")["mouvement"] == "recreated"
    assert table(res, "CANOT_LAB.MART.DIM_LEGACY")["mouvement"] is None
    assert table(res, "CANOT_LAB.RAW.TMP_DROPPED")["mouvement"] is None
    assert res["mouvements"]["recréée"] == 1  # scan.json keys unchanged in this step


def test_mouvement_recreee_sans_ecritures_lues_le_dit():
    res = analyze(snap("degrade"))
    t = table(res, "PROD.MART.KPI")
    assert t["mouvement"] == "recreated" and "writes not read" in t["mouvement_preuve"]
    assert table(res, "PROD.RAW.ARCHIVE_2019")["mouvement"] is None  # old DDL, writes not read: no guessing


def test_edition_non_determinee_si_access_history_refusee():
    assert analyze(snap("degrade"))["edition"] == "not determined (Standard or missing privilege)"
    assert analyze(snap("snowflake"))["edition"] == "enterprise"


def test_datasets_caches_signales():
    assert analyze(snap("bigquery"))["schemas_caches"] == ["_tmp"]


# --- flags -----------------------------------------------------------------------------------------------


def test_drapeaux_attendus_du_banc_snowflake():
    res = analyze(snap("snowflake"))
    assert {d["code"] for d in res["drapeaux"]} == {f"F{i:02d}" for i in range(1, 13)}


@pytest.mark.parametrize("name", ALL)
def test_tous_les_drapeaux_a_examiner_sans_gravite(name):
    for d in analyze(snap(name))["drapeaux"]:
        assert d["etiquette"] == ETIQUETTE == "To review"
        assert not {"severite", "sévérité", "gravite", "niveau", "severity"} & set(d)


def test_f01_dependance_externe():
    s = snap("snowflake")
    f = flags(analyze(s), "F01")
    assert {"CANOT_LAB.EXT.US_STATES", "CANOT_LAB.EXT.S3_PUBLIC", "SNOWFLAKE_SAMPLE_DATA"} <= set(f)
    assert f["CANOT_LAB.EXT.US_STATES"]["preuve"]["emplacements"] == ["s3://canot-lab-public"]
    # negatives: internal stage, SNOWFLAKE system database, export destination (belongs to F03)
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
    assert f["CANOT_LAB.OPS.T_EXPORT_NIGHTLY"]["preuve"]["mode"] == "declared"
    obs = f["d" * 12]
    assert obs["preuve"]["mode"] == "observed" and obs["preuve"]["emplacements"] == ["s3://canot-lab-export"]
    assert "e" * 12 not in f  # COPY INTO @RAW.INT_STAGE: internal stage
    next(o for o in s["objets"] if o["id"] == "CANOT_LAB.OPS.T_EXPORT_NIGHTLY")["detail"]["ecriture_externe"] = False
    assert "CANOT_LAB.OPS.T_EXPORT_NIGHTLY" not in flags(analyze(s), "F03")


def test_f03_dechargements_observes_vers_un_stage_externe():
    s = snap("snowflake")
    stages = {o["id"]: o for o in s["objets"] if o["kind"] == "stage"}
    stages["CANOT_LAB.EXT.S3_PUBLIC"]["detail"]["ecritures_observees_n"] = 12
    stages["CANOT_LAB.RAW.INT_STAGE"]["detail"]["ecritures_observees_n"] = 40  # internal stage: not off the platform
    f = flags(analyze(s), "F03")
    assert f["CANOT_LAB.EXT.S3_PUBLIC"]["preuve"]["mode"] == "observed" and "12 writes" in f["CANOT_LAB.EXT.S3_PUBLIC"]["texte"]
    assert "CANOT_LAB.RAW.INT_STAGE" not in f


def test_f04_role_sans_titulaire():
    res = analyze(snap("snowflake"))
    f = flags(res, "F04")
    assert set(f) == {"LAB_ORPHAN"} and f["LAB_ORPHAN"]["preuve"]["exemples"] == ["CANOT_LAB.SANDBOX.OLD_EXPORT_2023"]
    assert "F04" in table(res, "CANOT_LAB.SANDBOX.OLD_EXPORT_2023")["drapeaux"]
    # LAB_ENGINEER -> SYSADMIN -> ACCOUNTADMIN (1 user): held through inheritance
    assert "F04" not in table(res, "CANOT_LAB.SANDBOX.TEAM_WORK")["drapeaux"]


def test_f04_ne_devine_pas_sans_le_niveau_3():
    s = snap("snowflake")
    set_lecture(s, "GRANTS_TO_USERS", statut="not_read", raison="missing privilege", lignes=None)
    res = analyze(s)
    assert not flags(res, "F04") and not res["f04_lu"]
    assert "F04" in next(d for d in res["drapeaux"] if d["code"] == "F12" and d["preuve"].get("vue", "").endswith("GRANTS_TO_USERS"))["texte"]
    assert "not determined" in res["resume"]["RSSI"][-1]
    s2 = snap("snowflake")
    s2["lectures"] = [x for x in s2["lectures"] if "GRANTS" not in x["vue"]]
    res2 = analyze(s2)
    assert not flags(res2, "F04") and "droits" in flags(res2, "F12")


def test_f04_forme_du_collecteur_snowflake():
    s = snap("snowflake")
    s["droits"]["roles"] = [{"nom": r["nom"], "utilisateurs_n": r["utilisateurs"], "parents": r["accorde_a"]} for r in s["droits"]["roles"]]
    for x in s["lectures"]:  # labels in parentheses, as collection writes them
        if x["vue"].endswith("GRANTS_TO_ROLES"):
            x["vue"] += " (role hierarchy)"
    assert set(flags(analyze(s), "F04")) == {"LAB_ORPHAN"}
    s["droits"]["roles"].append({"nom": "LAB_ORPHAN", "utilisateurs_n": None, "parents": []})
    s["droits"]["roles"] = [r for r in s["droits"]["roles"] if not (r["nom"] == "LAB_ORPHAN" and r["utilisateurs_n"] == 0)]
    res = analyze(s)
    assert not flags(res, "F04") and "LAB_ORPHAN" in flags(res, "F12")["roles"]["preuve"]["roles"]  # unknown: no guessing


def test_libelles_de_vue_et_jointure():
    s = snap("snowflake")
    s["lectures"].append({"id": "S36", "vue": "SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY × SESSIONS (tool per family)",
                          "statut": "not_read", "raison": "missing privilege", "lignes": None, "fenetre": None, "latence_max_h": None})
    res = analyze(s)
    assert flags(res, "F12")["S36"]["texte"].endswith("limits: tool per query family.")
    assert res["rejouabilite"]["taux"]["familles"]["base"] == ["S24"]  # the join does not count as QUERY_HISTORY


def test_f05_stockage_sans_lecture_fenetre_reelle():
    res = analyze(snap("snowflake"))
    f = flags(res, "F05")
    assert set(f) == {f"CANOT_LAB.RAW.ARCHIVE_{i}" for i in (1, 2, 3)}
    assert ("No read observed in ACCESS_HISTORY from 2025-10-18 to 2026-10-17; "
            "blind spots: share consumers, RESULT_SCAN.") in f["CANOT_LAB.RAW.ARCHIVE_2"]["texte"]
    assert f["CANOT_LAB.RAW.ARCHIVE_2"]["confiance"] == "high"
    assert "S25" in f["CANOT_LAB.RAW.ARCHIVE_2"]["base"]
    assert "Shared with 2 external consumers" in f["CANOT_LAB.RAW.ARCHIVE_1"]["texte"]
    # negatives: read, unknown read (None), external, dropped
    assert not {"CANOT_LAB.RAW.ORDERS", "CANOT_LAB.MART.DIM_LEGACY", "CANOT_LAB.EXT.US_STATES", "CANOT_LAB.RAW.TMP_DROPPED"} & set(f)
    assert "lectures_par_table" in flags(res, "F12")


def test_f05_se_tait_quand_aucune_source_de_lecture_n_est_lue():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", statut="not_read", raison="edition required", lignes=None)
    set_lecture(s, "TABLE_QUERY_PRUNING_HISTORY", statut="not_read", raison="missing privilege", lignes=None)
    res = analyze(s)
    assert not flags(res, "F05")
    f12 = next(d for d in res["drapeaux"] if d["code"] == "F12" and d["preuve"].get("vue", "").endswith("ACCESS_HISTORY"))
    assert "F05 not computed" in f12["texte"]
    assert not flags(analyze(snap("degrade")), "F05")


def test_f05_repli_sur_pruning_en_confiance_moyenne():
    s = snap("snowflake")
    s["edition"] = None
    set_lecture(s, "ACCESS_HISTORY", statut="not_read", raison="edition required", lignes=None)
    for t in s["tables"]:  # like the collector: without ACCESS_HISTORY, n_lectures stays None and pruning goes into detail
        t["detail"] = {"requetes_pruning_n": t["n_lectures"]}
        t["n_lectures"] = None
    res = analyze(s)
    f = flags(res, "F05")
    assert set(f) == {f"CANOT_LAB.RAW.ARCHIVE_{i}" for i in (1, 2, 3)}
    assert all(d["confiance"] == "medium" and "No read observed in TABLE_QUERY_PRUNING_HISTORY" in d["texte"]
               and "results served from the cache" in d["texte"] for d in f.values())
    assert res["edition"] == "not determined (Standard or missing privilege)"


def test_f05_access_history_partielle_le_dit_en_confiance_basse():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", raison="partial: 30/52 weeks read, stopped on timed out",
                fenetre={"du": "2026-03-21T00:00:00Z", "au": "2026-10-17T07:00:00Z"})
    res = analyze(s)
    d = flags(res, "F05")["CANOT_LAB.RAW.ARCHIVE_2"]
    assert d["confiance"] == "low" and "from 2026-03-21 to 2026-10-17" in d["texte"] and "were not read" in d["texte"]
    assert "partly read" in flags(res, "F12")["S25"]["texte"]


def test_f05_lectures_d_abonnes_observees_pas_de_drapeau():
    s = snap("bigquery")
    next(t for t in s["tables"] if t["id"] == "lab-a.raw.read_by_api_only")["detail"] = {"lectures_partage_n": 4}
    assert "lab-a.raw.read_by_api_only" not in flags(analyze(s), "F05")


def test_f05_fenetre_courte_le_dit():
    s = snap("snowflake")
    set_lecture(s, "ACCESS_HISTORY", fenetre={"du": "2026-07-17T00:00:00Z", "au": "2026-10-17T07:00:00Z"})
    texte = flags(analyze(s), "F05")["CANOT_LAB.RAW.ARCHIVE_2"]["texte"]
    assert "from 2026-07-17 to 2026-10-17" in texte and "shorter than 365" in texte


def test_f05_bigquery_reserve_api_storage_read():
    f = flags(analyze(snap("bigquery")), "F05")
    assert set(f) == {"lab-a.archive.archive_2023", "lab-a.raw.read_by_api_only"}
    t = f["lab-a.raw.read_by_api_only"]["texte"]
    assert "INFORMATION_SCHEMA.JOBS from 2026-04-20 to 2026-10-17" in t and "Storage Read API" in t and "shorter than" not in t


def test_f06_historique_paye_non_transferable():
    f = flags(analyze(snap("snowflake")), "F06")
    assert set(f) == {"CANOT_LAB.RAW.ORDERS"} and f["CANOT_LAB.RAW.ORDERS"]["preuve"]["octets_historique"] == 6_000_000_000
    assert "retention 30 days" in f["CANOT_LAB.RAW.ORDERS"]["texte"]


def test_f07_table_supprimee_encore_facturee():
    f = flags(analyze(snap("snowflake")), "F07")
    assert set(f) == {"CANOT_LAB.RAW.TMP_DROPPED"}  # OLD_DROPPED: nothing billed anymore
    assert "Dropped on 2026-10-10" in f["CANOT_LAB.RAW.TMP_DROPPED"]["texte"]


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
    # collector shapes: comptes_cibles_n (Snowflake SHARES), projets_consommateurs_n (BigQuery SCHEMATA_LINKS)
    s = snap("snowflake")
    for o in s["objets"]:
        if o["kind"] == "partage":
            n = o["detail"].pop("comptes_cibles")
            o["detail"].update(comptes_cibles_n=n, base="CANOT_LAB")
            o["reference"] = []
    f = flags(analyze(s), "F09")
    assert set(f) == {"S_CLIENTS"} and f["S_CLIENTS"]["texte"].startswith("Share to 2 target accounts; database CANOT_LAB.")
    b = snap("bigquery")
    next(o for o in b["objets"] if o["kind"] == "partage")["detail"] = {"projets_consommateurs_n": 3}
    assert "3 consumer projects" in flags(analyze(b), "F09")["lab-a.hub.listing_sales"]["texte"]


def test_f10_securite_a_reconstruire():
    f = flags(analyze(snap("snowflake")), "F10")
    assert {"CANOT_LAB.RAW.CUSTOMERS", "CANOT_LAB.MART.FCT_ORDERS", "CANOT_LAB.OPS.MP_EMAIL", "CANOT_LAB.OPS.RAP_COUNTRY", "CANOT_LAB.OPS.PII"} == set(f)
    assert "principal not filtered" in f["CANOT_LAB.RAW.CUSTOMERS"]["texte"]
    assert "CANOT_LAB.RAW.ORDERS" not in f
    dg = analyze(snap("degrade"))
    assert not flags(dg, "F10") and "Policies not read" in dg["resume"]["RSSI"][0]


def test_f11_chaine_planifiee_et_echecs():
    f = flags(analyze(snap("snowflake")), "F11")
    assert f["CANOT_LAB.OPS.T_REFRESH"]["preuve"]["chaine"] == ["CANOT_LAB.OPS.T_DAILY", "CANOT_LAB.OPS.T_REFRESH"]
    assert "30 failures" in f["CANOT_LAB.OPS.T_EXPORT_NIGHTLY"]["texte"]
    assert {"CANOT_LAB.OPS.ORDERS_STREAM", "CANOT_LAB.OPS.P_EVENTS", "CANOT_LAB.MART.DT_COUNTRY_REVENUE"} <= set(f)
    assert not {"CANOT_LAB.MART.V_KPI", "CANOT_LAB.MART.SP_DYNAMIC_PY"} & set(f)
    bq = flags(analyze(snap("bigquery")), "F11")
    assert "state disabled" in bq["lab-a.sq_old"]["texte"]
    s = snap("snowflake")  # collector shape: detail.predecesseurs, names possibly quoted
    t = next(o for o in s["objets"] if o["id"] == "CANOT_LAB.OPS.T_REFRESH")
    t["detail"] = {"predecesseurs": '"CANOT_LAB"."OPS"."T_DAILY"'}
    assert flags(analyze(s), "F11")["CANOT_LAB.OPS.T_REFRESH"]["preuve"]["chaine"] == ["CANOT_LAB.OPS.T_DAILY", "CANOT_LAB.OPS.T_REFRESH"]


def test_f12_lecture_incomplete():
    s = snap("snowflake")
    assert "S33" in flags(analyze(s), "F12")
    set_lecture(s, "STREAMS", statut="read", raison=None, lignes=1)
    assert "S33" not in flags(analyze(s), "F12")


def test_f12_se_tait_quand_une_lecture_de_repli_couvre():
    res = analyze(snap("bigquery"))
    assert "B06" not in flags(res, "F12")  # TABLE_STORAGE disabled, tables.get read
    b06 = next(x for x in res["lectures"] if x["id"] == "B06")
    assert b06["statut"] == "not_read" and "tables.get" in b06["limite"]
    assert "B15" in flags(res, "F12")


def test_contrat_avec_les_familles_de_replay():
    """The actual output of replay.process_familles goes through analyze: rates taken as is, export resolved, nothing raw."""
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


# --- purity, privacy, vocabulary ---------------------------------------------------------------------------


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
    assert d == {"commentaire": "contact [masked]", "partitions_n": 12, "sources_chargement": "gs://lab-bucket"}


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
    assert set(r) == {"DSI", "DAF", "RSSI"}  # keys unchanged; rendered as CIO, CFO, CISO
    for phrases in r.values():
        assert 1 <= len(phrases) <= 3 and all(p.endswith(".") for p in phrases)


def test_non_estime_liste_explicite():
    sf = {i["sujet"]: i["raison"] for i in analyze(snap("snowflake"))["non_estime"]}
    assert sf["Your contract rate"] == "your contract price not read"
    assert sf["Storage cost"] == "your contract price not read"
    bq = {i["sujet"] for i in analyze(snap("bigquery"))["non_estime"]}
    assert {"Reads through the Storage Read API", "BigQuery free tier", "Reserved, in dollars"} <= bq
    dg = {i["sujet"] for i in analyze(snap("degrade"))["non_estime"]}
    assert {"Credits consumed", "Volume to copy", "Query replayability"} <= dg


def test_f12_se_tait_quand_l_api_remplace_la_vue_regionale():
    """Regional view refused but tables.get read: the inventory is not limited, no F12 for these views."""
    from canot.scanner.analyze import FALLBACKS

    for vue in ("TABLES", "TABLE_OPTIONS", "COLUMNS", "COLUMN_FIELD_PATHS", "VIEWS", "MATERIALIZED_VIEWS"):
        assert FALLBACKS[vue] == ("tables.get",)


# --- freshness: tables seen only in real time (missing from ACCOUNT_USAGE) ------------------------------------------------


def _with_recent(s: dict, n: int = 2) -> dict:
    """Like the collector: n tables seen only in real time, unknown history, and the snapshot's `fraicheur` block."""
    base = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_2")
    for i in range(n):
        s["tables"].append({**base, "id": f"CANOT_LAB.LAB.NEW_{i}", "schema": "LAB", "nom": f"NEW_{i}", "flags": ["recente"],
                            "octets_historique": None, "n_lectures": None, "n_ecritures": None, "derniere_ecriture": None,
                            "derniere_ddl": "2026-10-17T09:30:00Z",
                            "detail": {"source": "information_schema", "creee_le": "2026-10-17T09:30:00Z",
                                       "historique_non_lu": "table too recent for ACCOUNT_USAGE"}})
    s["fraicheur"] = {"recents_seulement_temps_reel": {"tables": n, "objets": 1}, "latence_max_h": 48,
                      "texte": f"{n} tables and 1 object seen only in real time: missing from ACCOUNT_USAGE."}
    return s


def test_fraicheur_exposee_et_resume_dsi():
    res = analyze(_with_recent(snap("snowflake")))
    f = res["fraicheur"]
    assert f["recents_seulement_temps_reel"] == {"tables": 2, "objets": 1} and f["latence_max_h"] == 48
    assert f["tables_sans_historique"] == 2 and f["latence_historique_h"] == 3 and f["texte"].startswith("2 tables and 1 object")
    dsi = res["resume"]["DSI"]
    assert len(dsi) == 3 and all(p.endswith(".") for p in dsi)
    assert ("2 tables seen only in real time: missing from ACCOUNT_USAGE, whose latency can reach 3 h; "
            "their history (billed volumes, reads) is not available yet.") in dsi[0]
    one = analyze(_with_recent(snap("snowflake"), 1))["resume"]["DSI"][0]
    assert "1 table seen only in real time: missing from ACCOUNT_USAGE" in one and "its history" in one


def test_fraicheur_absente_ou_nulle_rien_dans_le_resume():
    res = analyze(snap("snowflake"))
    assert res["fraicheur"] is None and "real time" not in " ".join(res["resume"]["DSI"])
    s = snap("snowflake")
    s["fraicheur"] = {"recents_seulement_temps_reel": {"tables": 0, "objets": 0}, "latence_max_h": 3, "texte": "no object missing."}
    res = analyze(s)
    assert res["fraicheur"]["tables_sans_historique"] == 0 and "real time" not in " ".join(res["resume"]["DSI"])
    assert analyze(snap("bigquery"))["fraicheur"] is None


def test_tables_recentes_sans_conclusion_f05_f06_f07():
    s = _with_recent(snap("snowflake"))
    for t in s["tables"]:
        if t["id"].startswith("CANOT_LAB.LAB.NEW_"):
            t["n_lectures"], t["octets_historique"] = 0, 10 ** 9  # even with values: no conclusion for a recent table
    res = analyze(s)
    for code in ("F05", "F06", "F07"):
        assert not {k for k in flags(res, code) if str(k).startswith("CANOT_LAB.LAB.NEW_")}, code
    assert "CANOT_LAB.RAW.ARCHIVE_2" in flags(res, "F05")


def test_f05_se_tait_pour_une_table_creee_depuis_moins_que_la_latence_d_access_history():
    s = snap("snowflake")
    t = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_2")
    t["detail"] = {"creee_le": "2026-10-17T06:00:00Z"}  # ACCESS_HISTORY read up to 07:00, 3 h latency: reads not visible yet
    assert "CANOT_LAB.RAW.ARCHIVE_2" not in flags(analyze(s), "F05")
    t["detail"] = {"creee_le": "2026-10-16T00:00:00Z"}  # created well before the latency: the finding stays
    assert "CANOT_LAB.RAW.ARCHIVE_2" in flags(analyze(s), "F05")


def test_volume_sous_estime_dit_dans_le_resume():
    s = snap("snowflake")
    s["fraicheur"] = {"recents_seulement_temps_reel": {"tables": 0, "objets": 0}, "volumes_en_retard": 7, "latence_max_h": 48, "texte": "x."}
    dsi = analyze(s)["resume"]["DSI"]
    assert len(dsi) == 3 and dsi[0].endswith("Volume underestimated: storage metrics not up to date yet for 7 tables.")
