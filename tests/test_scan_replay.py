"""Rejouabilité (spec §3) : catégories consignées d'avance, normalisation, secrets, vie privée, taux, déterminisme."""
import importlib.util
import json
import logging
import random
from pathlib import Path

import pytest
import sqlglot

from canot.scanner import replay

_spec = importlib.util.spec_from_file_location("scan_corpus", Path(__file__).parent / "fixtures" / "scan_corpus.py")
corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corpus)

CANARI = corpus.CANARI
SALT = b"sel-de-test-fixe"
FENETRE = {"du": "2025-10-18T00:00:00Z", "au": "2026-10-17T00:00:00Z"}
INTERDITS = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti",
             "conforme", "certifié", "ligne par ligne", "compatible")
CTX = {"snowflake": (corpus.SNOWFLAKE_TABLES, corpus.SNOWFLAKE_FUNCTIONS),
       "bigquery": (corpus.BIGQUERY_TABLES, corpus.BIGQUERY_FUNCTIONS)}


def raw_rows(dialect: str, entries=None, **extra) -> list[dict]:
    entries = entries if entries is not None else [e for e in corpus.CORPUS if e[0] == dialect]
    return [{"_sql": sql, "hash_plateforme": f"h-{i}", "n_exec": 10 + n, "temps_ms": 1000.0 + n, "credits": 0.5 if dialect == "snowflake" else None,
             "octets": 10_000_000 if dialect == "bigquery" else None, "app": "Tableau", "utilisateur": f"jdupont-{CANARI}",
             "role": "ANALYST", "entrepot": "BI_WH", "query_type": None, **extra}
            for n, (_, i, sql, _) in enumerate(entries)]


def run(dialect: str, rows=None, **kw):
    cols, fns = CTX[dialect]
    kw = {"columns": cols, "known_functions": fns, "salt": SALT, "fenetre": FENETRE, "lectures": ["S12"], **kw}
    return replay.process_familles(rows if rows is not None else raw_rows(dialect), dialect, **kw)


def dump(*objs) -> str:
    return json.dumps(objs, ensure_ascii=False, sort_keys=True)


# --- corpus consigné d'avance ------------------------------------------------------------------------------

def test_corpus_size_and_coverage():
    assert len(corpus.CORPUS) >= 60
    assert {c for *_, c in corpus.CORPUS} - {None} == set(replay.CATEGORIES)  # None : non testée


@pytest.mark.parametrize("dialect,qid,sql,attendu", corpus.CORPUS, ids=[e[1] for e in corpus.CORPUS])
def test_corpus_categorie(dialect, qid, sql, attendu):
    cols, fns = CTX[dialect]
    assert replay.classify(sql, dialect, cols, fns) == attendu


@pytest.mark.parametrize("dialect", ["snowflake", "bigquery"])
def test_corpus_familles_memes_categories(dialect):
    familles, rej = run(dialect)
    by_fp = {f["empreinte"]: f for f in familles}
    entries = [e for e in corpus.CORPUS if e[0] == dialect]
    for _, qid, sql, attendu in entries:
        fp = replay.normalize(sql, dialect)[1]["empreinte"]
        assert by_fp[fp]["categorie"] == attendu, qid
    assert sum(c["familles"] for c in rej["categories"].values()) + rej["non_testees"]["familles"] == len(familles)
    assert rej["secrets_ecartes"] == {"familles": 0, "executions": 0}


# --- secrets ------------------------------------------------------------------------------------------------

def test_secret_regex():
    for sql in corpus.SECRETS:
        assert replay.is_secret_query(sql)
    assert not replay.is_secret_query("select email from raw.customers")
    assert replay.is_secret_query("create user svc type = service", query_type="CREATE_USER")
    assert not replay.is_secret_query("select 1", query_type="SELECT")


def test_secret_compte_jamais_retenu():
    rows = raw_rows("snowflake", [("snowflake", "x", sql, None) for sql in corpus.SECRETS])
    rows.append({**rows[0], "_sql": "create user svc_loader type = service", "query_type": "CREATE_USER", "n_exec": 3})
    familles, rej = run("snowflake", rows)
    assert familles == []
    assert rej["secrets_ecartes"] == {"familles": len(rows), "executions": sum(r["n_exec"] for r in rows)}
    assert replay.raw_text_export(rows, "snowflake") == []
    out = dump(familles, rej)
    assert CANARI not in out and "PASSWORD" not in out.upper() and "svc_loader" not in out
    assert replay.normalize(corpus.SECRETS[0], "snowflake") == (None, {"secret": True})
    assert replay.classify(corpus.SECRETS[0], "snowflake", corpus.SNOWFLAKE_TABLES) is None


def test_commande_verbe_seul():
    # revérification de la spec : sqlglot garde le reste d'une Command, mot de passe compris
    tree = sqlglot.parse_one(f"create user bob password = '{CANARI}'", read="snowflake")
    assert isinstance(tree, sqlglot.exp.Command) and CANARI in tree.sql("snowflake")
    forme, infos = replay.normalize(f"call ops.sp_refresh('{CANARI}')", "snowflake")
    assert forme == "CALL" and CANARI not in dump(infos)
    assert infos["empreinte"] == replay.normalize("call ops.other_proc(42)", "snowflake")[1]["empreinte"]


# --- vie privée -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", ["snowflake", "bigquery"])
def test_canari_absent_de_toute_sortie(dialect, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    familles, rej = run(dialect)
    normalises = [replay.normalize(sql, dialect) for d, _, sql, _ in corpus.CORPUS if d == dialect]
    assert any(CANARI in sql for d, _, sql, _ in corpus.CORPUS if d == dialect)
    out = dump(familles, rej, normalises)
    assert CANARI not in out
    assert "jdupont" not in out and "ANALYST" not in out
    assert "_sql" not in out
    captured = capsys.readouterr()
    assert CANARI not in caplog.text + captured.out + captured.err


def test_sans_trace_du_texte_brut():
    rows = raw_rows("snowflake")
    familles, rej = run("snowflake", rows)
    out = dump(familles, rej)
    for r in rows:
        assert r["_sql"] not in out
    assert all("_sql" not in f for f in familles)


def test_pseudonymes():
    familles, _ = run("snowflake")
    f = familles[0]
    assert f["utilisateur"].startswith("u-") and f["role"].startswith("r-")
    autre, _ = run("snowflake", salt=b"un-autre-sel")
    assert autre[0]["utilisateur"] != f["utilisateur"]


def test_c1_empreinte_sans_litteral():
    a = replay.normalize(f"select a from raw.orders where status in ('{CANARI}", "snowflake")
    b = replay.normalize("select a from raw.orders where status in ('shipped", "snowflake")
    assert a[0] is None and a[1]["empreinte"] == b[1]["empreinte"]
    assert CANARI not in dump(a)


# --- normalisation et empreinte -------------------------------------------------------------------------------

def test_meme_empreinte_si_seuls_les_litteraux_different():
    q1 = "/* tableau */ select * from raw.orders where status = 'paid' and amount > 10 and order_id in (1, 2, 3) and is_test = false"
    q2 = "select * from raw.orders where status = 'shipped' and amount > 99.5 and order_id in (7) and is_test = true -- fin"
    (f1, i1), (f2, i2) = replay.normalize(q1, "snowflake"), replay.normalize(q2, "snowflake")
    assert f1 == f2 and i1["empreinte"] == i2["empreinte"]
    rows = raw_rows("snowflake", [("snowflake", "a", q1, None), ("snowflake", "b", q2, None)])
    familles, _ = run("snowflake", rows)
    assert len(familles) == 1
    assert familles[0]["n_exec"] == rows[0]["n_exec"] + rows[1]["n_exec"]
    assert familles[0]["detail"]["hashes_plateforme"] == ["h-a", "h-b"]
    assert familles[0]["credits"] == 1.0 and familles[0]["temps_ms"] == 2001.0


def test_empreinte_differente_si_la_structure_differe():
    a = replay.normalize("select status from raw.orders where amount > 10", "snowflake")[1]["empreinte"]
    b = replay.normalize("select amount from raw.orders where amount > 10", "snowflake")[1]["empreinte"]
    c = replay.normalize("select status from raw.orders group by 1", "snowflake")[1]["empreinte"]
    d = replay.normalize("select status from raw.orders group by 2", "snowflake")[1]["empreinte"]
    assert len({a, b, c, d}) == 4


def test_normalisation():
    forme, _ = replay.normalize(
        "select $1, amount::number(12,2) from raw.orders where status = 'x' and flag = true and id in (1,2,3) -- note\n group by 1",
        "snowflake")
    assert forme == "SELECT $1, CAST(amount AS DECIMAL(12, 2)) FROM raw.orders WHERE status = ? AND flag = ? AND id IN (?) GROUP BY 1"


def test_dbt_commentaire():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "S13")
    forme, infos = replay.normalize(sql, "snowflake")
    assert infos["dbt"] == {"app": "dbt", "node_id": "model.acme.fct_orders", "dbt_version": "1.8.7"}
    assert "dbt" not in forme and "prod" not in forme
    familles, _ = run("snowflake", raw_rows("snowflake", [("snowflake", "S13", sql, None)]))
    f = familles[0]
    assert f["outil"] == "dbt" and f["detail"]["dbt_node_id"] == "model.acme.fct_orders" and f["detail"]["dbt_version"] == "1.8.7"
    assert "target_name" not in dump(familles) and "acme\"" not in dump(familles)


def test_tables_referencees():
    _, infos = replay.normalize(
        "with recent as (select * from analytics.raw.orders) select * from recent join raw.customers c using (customer_id)", "snowflake")
    assert infos["tables"] == ["ANALYTICS.RAW.ORDERS", "RAW.CUSTOMERS"]
    _, infos = replay.normalize("select * from `acme-data`.sales.orders", "bigquery")
    assert infos["tables"] == ["acme-data.sales.orders"]


# --- écritures hors plateforme (F03) ---------------------------------------------------------------------------

def test_export_reduit_au_compartiment():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "S49")
    forme, infos = replay.normalize(sql, "snowflake")
    assert infos["exports"] == ["s3://acme-exports"]
    familles, _ = run("snowflake", raw_rows("snowflake", [("snowflake", "S49", sql, None)]))
    assert familles[0]["ecriture_externe"] is True and familles[0]["detail"]["export"] == ["s3://acme-exports"]
    assert "nightly" not in dump(familles) and CANARI not in dump(familles)


def test_copy_vers_stage_interne_puis_externe():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "S50")
    rows = raw_rows("snowflake", [("snowflake", "S50", sql, None)])
    familles, _ = run("snowflake", rows)
    assert familles[0]["ecriture_externe"] is False and familles[0]["detail"]["stages_cibles"] == ["RAW.INT_STAGE"]
    familles, _ = run("snowflake", rows, external_stages=["ANALYTICS.RAW.INT_STAGE"])
    assert familles[0]["ecriture_externe"] is True


def test_export_data_bigquery():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "B27")
    familles, _ = run("bigquery", raw_rows("bigquery", [("bigquery", "B27", sql, None)]))
    assert familles[0]["ecriture_externe"] is True and familles[0]["detail"]["export"] == ["gs://acme-exports"]
    assert CANARI not in dump(familles)


def test_lecture_de_fichier_sans_ecriture_externe():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "S48")
    familles, _ = run("snowflake", raw_rows("snowflake", [("snowflake", "S48", sql, None)]))
    assert familles[0]["ecriture_externe"] is False and familles[0]["detail"]["stages"] == ["RAW.LANDING"]


# --- fonctions propres -----------------------------------------------------------------------------------------

def test_fonctions_et_udf():
    rows = raw_rows("snowflake", [e for e in corpus.CORPUS if e[1] in ("S02", "S29", "S36", "S43", "S44")])
    familles, rej = run("snowflake", rows)
    fonctions = {k for f in familles for k in f["fonctions"]}
    assert {"qualify", "lateral", "explode", "historicaldata", "udf:CLEAN_EMAIL", "moteur:SYSTEM$CLUSTERING_INFORMATION"} <= fonctions
    assert rej["fonctions"]["udf:CLEAN_EMAIL"] == 1
    udf = next(f for f in familles if "udf:CLEAN_EMAIL" in f["fonctions"])
    assert udf["detail"]["raison"] == "UDF du client"
    sans, _ = run("snowflake", rows, known_functions=[])
    assert "moteur:CLEAN_EMAIL" in {k for f in sans for k in f["fonctions"]}


def test_udf_bigquery_nom_qualifie():
    sql = next(s for d, i, s, _ in corpus.CORPUS if i == "B24")
    familles, _ = run("bigquery", raw_rows("bigquery", [("bigquery", "B24", sql, None)]))
    assert "udf:NORMALIZE_SKU" in familles[0]["fonctions"] and familles[0]["detail"]["raison"] == "UDF du client"


def test_liaison_client_n_est_pas_une_variable():
    cols = corpus.SNOWFLAKE_TABLES
    assert replay.classify("select * from raw.orders where status = ?", "snowflake", cols) == "C0"
    assert replay.classify("select * from raw.orders where status = :1", "snowflake", cols) == "C0"
    assert replay.classify("select * from raw.orders where status = $s", "snowflake", cols) == "C6"


def test_duckdb_isole_du_disque():
    cols = corpus.SNOWFLAKE_TABLES
    assert replay.classify("select * from read_csv('/etc/hosts')", "snowflake", cols) == "C5"


def test_squelette_types():
    sk = replay._Skeleton(corpus.SNOWFLAKE_TABLES, "snowflake")
    try:
        assert sk._type("VARIANT") == "JSON" and sk._type("ARRAY") == "JSON"
        assert sk._type("NUMBER(12,2)") == "DECIMAL(12, 2)"
        assert sk._type("GEOGRAPHY") == "VARCHAR" and sk._type("TYPE_INCONNU(3") == "VARCHAR"
    finally:
        sk.close()


# --- taux ------------------------------------------------------------------------------------------------------

def test_taux_et_base():
    familles, rej = run("snowflake")
    cats = rej["categories"]
    testables = sum(cats[c]["familles"] for c in replay.BASE_TAUX)
    assert testables == len(familles) - cats["C7"]["familles"] - rej["non_testees"]["familles"]
    assert rej["base"] == (f"{cats['C0']['familles']}/{testables} familles, du 18/10/2025 au 17/10/2026, "
                           f"sqlglot {sqlglot.__version__}, duckdb {rej['versions']['duckdb']}")
    assert rej["mention"] == "accepté ≠ résultat identique" and rej["libelle_c0"] == "traduit et accepté par DuckDB"
    t = rej["taux"]["familles"]
    assert t["nature"] == "calculé" and t["unite"] == "%" and t["valeur"] == round(100 * cats["C0"]["familles"] / testables, 1)
    assert "accepté ≠ résultat identique" in t["hypotheses"] and t["base"] == ["S12"]
    assert rej["bases"]["executions"].endswith(f"exécutions, du 18/10/2025 au 17/10/2026, sqlglot {sqlglot.__version__}, "
                                               f"duckdb {rej['versions']['duckdb']}")
    assert rej["taux"]["poids"]["nature"] == "calculé" and "crédits" in rej["bases"]["poids"]
    assert cats["C0"]["poids"] == 0.5 * cats["C0"]["familles"]


def test_poids_non_lu_jamais_zero():
    rows = raw_rows("snowflake", credits=None)
    _, rej = run("snowflake", rows)
    assert all(c["poids"] is None for c in rej["categories"].values())
    p = rej["taux"]["poids"]
    assert p["nature"] == "non_lu" and p["valeur"] is None and p["raison"] == "crédits non lus"


def test_poids_en_octets_pour_bigquery():
    _, rej = run("bigquery")
    assert rej["poids_unite"] == "octets" and "octets" in rej["bases"]["poids"]
    assert rej["categories"]["C0"]["poids"] == 10_000_000 * rej["categories"]["C0"]["familles"]


def test_colonnes_non_lues():
    familles, rej = run("snowflake", columns=None)
    cats = {f["detail"]["raison"]: f["categorie"] for f in familles}
    assert cats[replay.R_NON_TESTE] is None
    assert {f["categorie"] for f in familles} == {None, "C1", "C2", "C3", "C7"}
    assert rej["non_testees"]["familles"] > 0 and rej["categories"]["C0"]["familles"] == 0
    assert all(t["nature"] == "non_estimé" and t["valeur"] is None for t in rej["taux"].values())


def test_decimal_du_connecteur():
    from decimal import Decimal
    rows = raw_rows("snowflake", [e for e in corpus.CORPUS if e[1] == "S01"], credits=Decimal("0.125000000"), n_exec=Decimal(4))
    familles, rej = run("snowflake", rows)
    assert familles[0]["credits"] == 0.125 and familles[0]["n_exec"] == 4
    json.dumps([familles, rej])


def test_forme_non_generable_traitee_en_c1(monkeypatch):
    def boom(tree):
        raise RecursionError
    monkeypatch.setattr(replay, "_masked", boom)
    forme, infos = replay.normalize("select 1 from raw.orders", "snowflake")
    assert forme is None and infos["racine"] is None and infos["empreinte"]
    familles, _ = run("snowflake", raw_rows("snowflake", [e for e in corpus.CORPUS if e[1] == "S01"]))
    assert familles[0]["categorie"] == "C1" and familles[0]["sql_normalise"] is None


def test_fenetre_non_lue():
    _, rej = run("snowflake", fenetre=None)
    assert "fenêtre non lue" in rej["base"]


def test_vocabulaire():
    out = dump(run("snowflake"), run("bigquery"), run("snowflake", columns=None)).lower()
    for mot in INTERDITS:
        assert mot not in out, mot


# --- déterminisme ----------------------------------------------------------------------------------------------

def test_determinisme_a_l_octet():
    a = dump(run("snowflake"), run("bigquery"))
    b = dump(run("snowflake"), run("bigquery"))
    assert a == b
    rows = raw_rows("snowflake")
    random.Random(7).shuffle(rows)
    assert dump(run("snowflake", rows)) == dump(run("snowflake"))


# --- --include-sql-text ----------------------------------------------------------------------------------------

def test_raw_text_export():
    rows = raw_rows("snowflake", [e for e in corpus.CORPUS if e[1] in ("S05", "S14")])
    rows.append({**rows[0], "_sql": corpus.SECRETS[0], "hash_plateforme": "h-secret"})
    out = replay.raw_text_export(rows, "snowflake")
    assert [e["hash_plateforme"] for e in out] == ["h-S14", "h-S05"]
    assert any(CANARI in e["sql"] for e in out)  # demandé explicitement : le texte brut, hors secrets
    assert "h-secret" not in dump(out) and "PASSWORD" not in dump(out).upper()
    familles, _ = run("snowflake", rows)
    assert {e["empreinte"] for e in out} == {f["empreinte"] for f in familles}
    assert set(out[0]) == {"hash_plateforme", "empreinte", "n_exec", "sql"}


def test_requetes_de_metadonnees_comptees_a_part():
    """INFORMATION_SCHEMA / ACCOUNT_USAGE : le catalogue de la plateforme n'a pas d'équivalent à rejouer, donc C7 et hors taux."""
    for dialect, sql in (("snowflake", "select table_name from snowflake.account_usage.tables where deleted is null"),
                         ("bigquery", "select table_name from `p.d.INFORMATION_SCHEMA.TABLES`")):
        assert replay.classify(sql, dialect, {}, ()) == "C7"


def test_table_hors_perimetre_n_abaisse_pas_le_taux():
    """Une requête sur une table absente de l'inventaire n'est pas « non portable » : elle est non testée, hors taux."""
    cols = {"DB.S.T": [{"nom": "A", "type": "NUMBER"}]}
    assert replay.classify("select a from db.s.t", "snowflake", cols, ()) == "C0"
    assert replay.classify("select a from autre_base.s.ailleurs", "snowflake", cols, ()) is None
    # une construction propre à la plateforme reste C5 même si la table manque
    assert replay.classify("select metadata$action from db.s.flux", "snowflake", cols, ()) == "C5"


def test_texte_absent_n_est_pas_non_analysable():
    """Une requête interne dont la plateforme ne donne pas le texte est non testée, pas « non analysable » (C1)."""
    familles, rej = replay.process_familles(
        [{"_sql": "", "hash_plateforme": "h", "n_exec": 6000, "temps_ms": 1, "credits": None, "octets": None, "query_type": "SELECT"}],
        "snowflake", columns={}, known_functions=(), salt=b"s")
    assert familles[0]["categorie"] is None and rej["categories"]["C1"]["familles"] == 0
    assert rej["non_testees"] == {"familles": 1, "executions": 6000}
