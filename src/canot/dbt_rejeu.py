"""Preuve P3 sur un projet dbt : les modèles SQL sont rejoués dans un DuckDB local à partir de la copie des sources,
puis chaque sortie est comparée à la table que la plateforme avait matérialisée, avec les critères de `canot drill`
(nombre de lignes, statistiques par colonne, empreinte de contenu : verify.check_table).

Entrée : les artefacts dbt (target/manifest.json, SQL compilé) et une copie qui contient les tables sources et les tables
d'origine des modèles : une base DuckDB, ou la copie Iceberg écrite par `canot drill`. Rien n'est envoyé à la plateforme.

Chaque modèle est classé : `identique` (rejoué, comparé, égal), `different` (rejoué, comparé, écart), `non_compare`
(rejoué, aucune table d'origine dans la copie : une vue par exemple ; sa sortie sert aux modèles suivants) ou
`non_rejouable` (avec la raison : langage, matérialisation, fonction propre, traduction, dépendance non rejouée).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel

from . import __version__
from .convert import fingerprint
from .evidence import EvidenceLog
from .model import ColumnInfo, TableInfo, q
from .scanner import dbt as dbt_scan
from .scanner import replay
from .tolerances import Tolerances
from .verify import check_table

STATUTS = ("identique", "different", "non_compare", "non_rejouable")
SCHEMA_REJEU = "rejeu"
COPIE = "copie"
DOSSIER = "canot.rejeu-dbt/1"


def _arrow(rel) -> pa.Table:
    return rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.fetch_arrow_table()


class _Copie:
    """La copie vue comme la « source » de verify.check_table : les tables d'origine, interrogées en SQL DuckDB."""

    dialect = "duckdb"

    def __init__(self, con: duckdb.DuckDBPyConnection, index: dict[tuple[str, str], tuple[str, str]]):
        self.con, self.index = con, index

    def table_ref(self, t: TableInfo) -> str:
        return f"{COPIE}.{q(t.schema)}.{q(t.name)}"

    def query(self, sql: str) -> pa.Table:
        return _arrow(self.con.execute(sql))


class _Rejoue:
    """Une table Arrow présentée comme une table Iceberg (schema().as_arrow(), scan().to_arrow_batch_reader())."""

    def __init__(self, tb: pa.Table):
        self.tb = tb

    def schema(self):
        return SimpleNamespace(as_arrow=lambda: self.tb.schema)

    def scan(self):
        return SimpleNamespace(to_arrow_batch_reader=lambda: self.tb.to_batches())


def _connexion() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})


def ouvrir_copie(base: str | None = None, lac: str | None = None, drill_out: str | None = None, catalog: str | None = None):
    """(connexion DuckDB en mémoire, index (SCHEMA, NOM) -> (schéma, nom) des tables de la copie, description)."""
    con = _connexion()
    if base:
        chemin = str(Path(base).expanduser()).replace("'", "''")
        con.execute(f"ATTACH '{chemin}' AS {COPIE} (READ_ONLY)")
        rows = con.execute(f"select table_schema, table_name from information_schema.tables where table_catalog = '{COPIE}' "
                           "and table_type = 'BASE TABLE'").fetchall()
        desc = f"base DuckDB {Path(base).name}"
    elif lac:
        from .target import parse_target

        cat = parse_target(lac, None, Path(drill_out or "canot-out"), catalog).catalog()
        con.execute(f"ATTACH ':memory:' AS {COPIE}")
        rows = []
        for ns in cat.list_namespaces():
            for ident in cat.list_tables(ns):
                schema, name = ".".join(ident[:-1]), ident[-1]
                tb = cat.load_table(ident).scan().to_arrow()
                con.execute(f"create schema if not exists {COPIE}.{q(schema)}")
                con.register("_canot_tmp", tb)
                con.execute(f"create table {COPIE}.{q(schema)}.{q(name)} as select * from _canot_tmp")
                con.unregister("_canot_tmp")
                rows.append((schema, name))
        desc = f"copie Iceberg {lac}"
    else:
        raise ValueError("indiquez la copie : --base FICHIER.duckdb, ou --iceberg DESTINATION (la copie écrite par canot drill)")
    con.execute(f"create schema {SCHEMA_REJEU}")
    return con, {(s.upper(), n.upper()): (s, n) for s, n in rows}, desc


def _ordre(models: dict[str, dict]) -> list[str]:
    """Ordre topologique des modèles (parents d'abord), stable."""
    restants = {u: {p for p in (n.get("depends_on") or {}).get("nodes") or [] if p in models} for u, n in models.items()}
    out = []
    while restants:
        prets = sorted(u for u, ps in restants.items() if not ps)
        if not prets:  # cycle : impossible dans dbt, mais on ne boucle pas
            prets = sorted(restants)
        for u in prets:
            out.append(u)
            del restants[u]
        for ps in restants.values():
            ps.difference_update(prets)
    return out


def _parties(tb: exp.Table) -> tuple[str, ...]:
    return tuple(p.name for p in (tb.args.get("catalog"), tb.args.get("db"), tb.this) if p is not None and p.name)


def _reecrire(tree: exp.Expression, relations: dict[str, str], rejoues: dict[str, str], copie: dict) -> str | None:
    """Remplace chaque relation par la table rejouée (modèle) ou la table de la copie (source, seed…). Rend la raison d'un refus."""
    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    renommees = set()
    for tb in list(tree.find_all(exp.Table)):
        if not isinstance(tb.this, exp.Identifier):
            continue
        parts = _parties(tb)
        if not parts or (len(parts) == 1 and parts[0].upper() in ctes):
            continue
        cle = ".".join(parts).upper()
        uid = relations.get(cle)
        if uid is not None and uid not in rejoues:
            if uid.startswith("model."):
                return f"dépend d'un modèle non rejoué ({uid})"
            uid = None  # snapshot, seed : leur table d'origine est une entrée, lue dans la copie
        if uid is not None:
            nom, schema, catalogue = rejoues[uid], SCHEMA_REJEU, None
        else:
            hit = copie.get(tuple(p.upper() for p in parts[-2:])) if len(parts) >= 2 else None
            if hit is None:
                nus = [v for k, v in copie.items() if k[1] == parts[-1].upper()]
                hit = nus[0] if len(nus) == 1 else None
            if hit is None:
                return f"table absente de la copie ({'.'.join(parts)})"
            catalogue, schema, nom = COPIE, hit[0], hit[1]
        if not tb.args.get("alias"):
            tb.set("alias", exp.TableAlias(this=exp.to_identifier(tb.name)))
        renommees.add(tuple(p.upper() for p in parts[-2:]))
        tb.set("catalog", exp.to_identifier(catalogue, quoted=True) if catalogue else None)
        tb.set("db", exp.to_identifier(schema, quoted=True))
        tb.set("this", exp.to_identifier(nom, quoted=True))
    for col in tree.find_all(exp.Column):  # schema.table.colonne -> table.colonne
        if col.args.get("db") and (col.db.upper(), col.table.upper()) in renommees:
            col.set("db", None)
            col.set("catalog", None)
    return None


def _colonnes(con, ref: str) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in con.execute(f"describe {ref}").fetchall()]


def _aligner(con, rejouee: str, origine: str) -> tuple[str | None, list[str], str | None]:
    """SELECT qui donne à la sortie rejouée les colonnes et les types de la table d'origine (ordre d'origine).
    Rend (sql, conversions consignées, raison d'un écart de colonnes)."""
    mine = {n.lower(): (n, t) for n, t in _colonnes(con, rejouee)}
    ref = _colonnes(con, origine)
    manquantes = [n for n, _ in ref if n.lower() not in mine]
    en_trop = [n for n in mine if n not in {x.lower() for x, _ in ref}]
    if manquantes or en_trop:
        return None, [], ("colonnes différentes : " + ", ".join(
            ([f"absentes du rejeu {', '.join(manquantes[:5])}"] if manquantes else []) + ([f"en trop {', '.join(en_trop[:5])}"] if en_trop else [])))
    sel, conv = [], []
    for n, t in ref:
        src_n, src_t = mine[n.lower()]
        if src_t != t:
            conv.append(f"{n} : {src_t} -> {t}")
            sel.append(f"cast({q(src_n)} as {t}) as {q(n)}")
        else:
            sel.append(f"{q(src_n)} as {q(n)}")
    return f"select {', '.join(sel)} from {rejouee}", conv, None


def _raison_duckdb(e: BaseException) -> str:
    """Raison courte et fixe : le message de DuckDB cite le SQL, il n'est jamais conservé."""
    if isinstance(e, duckdb.ParserException):
        return replay.R_C4
    r = replay._liaison(e, False)
    return "échec d'exécution DuckDB" if r == "liaison refusée par DuckDB" else r


def chiffres_flottants(tol: Tolerances) -> int:
    """Chiffres significatifs gardés pour l'empreinte des flottants : plus fins que la tolérance relative (1e-9 -> 12)."""
    return max(6, min(15, round(-math.log10(tol.float_rel)) + 3)) if tol.float_rel > 0 else 15


def _arrondir(tb: pa.Table, cols: list[str], chiffres: int) -> pa.Table:
    for c in cols:
        i = tb.schema.get_field_index(c)
        vals = [None if v is None else format(v, f".{chiffres}g") for v in tb.column(i).to_pylist()]
        tb = tb.set_column(i, c, pa.array(vals, type=pa.string()))
    return tb


def _empreinte_flottants(orig: pa.Table, rej: pa.Table, tol: Tolerances, res: dict) -> bool:
    """Lignes et statistiques égales, empreinte différente : si des colonnes sont flottantes, une somme flottante calculée dans un
    autre ordre diffère au dernier bit (non-associativité de l'addition IEEE 754), et l'empreinte hache le texte exact.
    On recalcule l'empreinte des deux côtés avec ces colonnes arrondies à N chiffres significatifs (N plus fin que la tolérance)
    et on mesure l'écart exact. Égale : identique dans la tolérance, consigné ; sinon l'écart reste."""
    cols = [f.name for f in orig.schema if pa.types.is_floating(f.type)]
    if not cols:
        return False
    n = chiffres_flottants(tol)
    egal = fingerprint(_arrondir(orig, cols, n)) == fingerprint(_arrondir(rej, cols, n))
    exacts = {}
    for c in cols:
        a = sorted(x for x in orig.column(c).to_pylist() if x is not None)
        b = sorted(x for x in rej.column(c).to_pylist() if x is not None)
        rel = max((abs(x - y) / max(abs(x), abs(y)) for x, y in zip(a, b) if x != y), default=0.0)
        exacts[c] = {"valeurs_differentes_au_bit": sum(1 for x, y in zip(a, b) if x != y), "ecart_relatif_max": rel}
    res["verification"]["empreinte_flottants"] = {"colonnes": cols, "chiffres_significatifs": n, "egale": egal, "ecarts_exacts": exacts}
    if egal:
        res["note"] = (f"identique dans la tolérance : empreinte recalculée avec {', '.join(cols)} arrondi(s) à {n} chiffres significatifs ; "
                       "valeurs flottantes différentes au dernier bit (somme flottante dans un autre ordre), voir verification.empreinte_flottants")
    return egal


def rejouer(projet: dbt_scan.ProjetDbt, con, copie: dict, tol: Tolerances, log=lambda s: None) -> list[dict]:
    """Rejoue les modèles du projet dans `con` (schéma rejeu) et compare chaque sortie à sa table d'origine dans la copie."""
    man = json.loads((projet.cible / "manifest.json").read_text(encoding="utf-8"))
    adaptateur = str((man.get("metadata") or {}).get("adapter_type") or "")
    dialect = dbt_scan.DIALECTES.get(adaptateur)
    nodes = man.get("nodes") or {}
    models = {u: n for u, n in nodes.items() if n.get("resource_type") == "model"}
    relations = {}
    for u, n in {**nodes, **(man.get("sources") or {})}.items():
        r = dbt_scan._relation(n)
        if r:
            relations[r.upper()] = u
    core, autres = dbt_scan._materialisations(man)
    rejoues: dict[str, str] = {}
    out = []
    for uid in _ordre(models):
        n = models[uid]
        mat = (n.get("config") or {}).get("materialized") or "view"
        res = {"modele": uid, "materialisation": mat, "relation": dbt_scan._relation(n), "statut": "non_rejouable", "raison": None,
               "conversions": [], "verification": None}
        out.append(res)
        a = dbt_scan.analyse_sql(n, dialect, None, frozenset(), {}, {})
        port, raison_p = dbt_scan.portabilite(a, mat, mat not in core and mat not in autres, adaptateur)
        compiled = n.get("compiled_code") if n.get("compiled_code") is not None else n.get("compiled_sql")
        if a["langage"] != "sql":
            res["raison"] = f"langage {a['langage']} : non rejoué"
        elif mat == "ephemeral":
            res["raison"] = "éphémère : inclus en CTE dans les modèles qui l'utilisent, rejoué à travers eux"
        elif mat == "incremental":
            res["raison"] = "incrémental : le SQL compilé dépend de l'état antérieur de la table"
        elif port == "a_reconstruire":
            res["raison"] = raison_p
        elif not (compiled and compiled.strip()):
            res["raison"] = "SQL compilé absent du manifest (dbt compile le produit)"
        elif a["secret"]:
            res["raison"] = "secret détecté dans le SQL : écarté"
        if res["raison"]:
            log(f"   [non rejouable] {uid} : {res['raison']}")
            continue
        with replay._quiet():
            tree = replay._parse(compiled, dialect or "")
        if not isinstance(tree, exp.Query):
            res["raison"] = replay.R_C1 if tree is None else "pas une requête SELECT"
            continue
        refus = _reecrire(tree, relations, rejoues, copie)
        if refus:
            res["raison"] = refus
            log(f"   [non rejouable] {uid} : {refus}")
            continue
        try:
            sql = tree.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
        except Exception:  # noqa: BLE001 : traduction refusée par sqlglot
            res["raison"] = replay.R_C3
            continue
        cible = f"{(n.get('schema') or 'x')}__{n.get('alias') or n.get('name')}"
        ref = f"{SCHEMA_REJEU}.{q(cible)}"
        try:
            con.execute(f"create table {ref} as {sql}")
        except duckdb.Error as e:
            res["raison"] = _raison_duckdb(e)
            log(f"   [non rejouable] {uid} : {res['raison']}")
            continue
        rejoues[uid] = cible
        origine = copie.get(((n.get("schema") or "").upper(), (n.get("alias") or n.get("name") or "").upper()))
        if origine is None:
            res.update(statut="non_compare", raison="aucune table d'origine dans la copie" + (" (vue)" if mat == "view" else ""))
            log(f"   [rejoué, non comparé] {uid}")
            continue
        oref = f"{COPIE}.{q(origine[0])}.{q(origine[1])}"
        sel, conv, ecart = _aligner(con, ref, oref)
        res["conversions"] = conv
        if ecart:
            res.update(statut="different", raison=ecart)
            log(f"   [rejoué, différent] {uid} : {ecart}")
            continue
        try:  # la sortie alignée sur les types d'origine remplace la sortie brute : les modèles suivants lisent ce que la plateforme stockait
            con.execute(f"create table {SCHEMA_REJEU}.{q(cible + '__a')} as {sel}")
            con.execute(f"drop table {ref}")
            con.execute(f"alter table {SCHEMA_REJEU}.{q(cible + '__a')} rename to {q(cible)}")
        except duckdb.Error as e:
            res.update(statut="different", raison="conversion vers les types d'origine impossible (" + _raison_duckdb(e) + ")")
            continue
        orig_tb = _arrow(con.execute(f"select * from {oref}"))
        t = TableInfo(origine[0], origine[1], orig_tb.num_rows, None, [ColumnInfo(f.name, str(f.type)) for f in orig_tb.schema])
        rej_tb = _arrow(con.execute(f"select * from {ref}"))
        chk = check_table(_Copie(con, copie), t, _Rejoue(rej_tb), fingerprint(orig_tb), tol)
        res["verification"] = asdict(chk) | {"ok": chk.ok}
        ok = chk.ok
        if not ok and chk.schema_ok and chk.stats_ok and chk.rows_source == chk.rows_target and not chk.fingerprint_ok:
            ok = _empreinte_flottants(orig_tb, rej_tb, tol, res)
        res["statut"] = "identique" if ok else "different"
        res["raison"] = None if ok else "; ".join(chk.stats_diffs[:3]) or "empreinte de contenu différente"
        log(f"   [rejoué, {'identique' if ok else 'différent'}] {uid}")
    return out


def run_rejeu(url: str, out: Path, key_path: Path, *, base: str | None = None, lac: str | None = None, drill_out: str | None = None,
              catalog: str | None = None, tol: Tolerances | None = None, log=print) -> dict:
    tol = tol or Tolerances()
    t0 = time.monotonic()
    projet = dbt_scan.ProjetDbt(url)
    con, copie, desc = ouvrir_copie(base, lac, drill_out, catalog)
    ev = EvidenceLog(out)
    env = {"canot": __version__, "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__}
    ev.add("start", {"projet": projet.describe(), "copie": desc, "forme": "P3 rejeu de traitements", "env": env, "tolerances": tol.to_dict()})
    try:
        log(f"→ rejeu des modèles dbt de {projet.describe()} sur {desc}")
        res = rejouer(projet, con, copie, tol, log)
    finally:
        con.close()
    for r in res:
        ev.add("dbt_model", r)
    compte = {s: sum(1 for r in res if r["statut"] == s) for s in STATUTS}
    summary = {"modeles": len(res), **compte, "duree_s": round(time.monotonic() - t0, 1),
               "preuve": "complète" if res and compte["identique"] == len(res) else ("écarts" if compte["different"] else "partielle")}
    ev.add("summary", summary)
    seal = ev.seal(key_path, log)
    rapport = {"schema": DOSSIER, "projet": projet.describe(), "copie": desc, "forme": "P3", "summary": summary, "modeles": res,
               "env": env, "tolerances": tol.to_dict(), "seal": seal,
               "regles": ["même SQL compilé, entrées copiées, deux exécutions : plateforme d'origine et DuckDB local",
                          "types de la sortie rejouée alignés sur la table d'origine avant comparaison ; chaque conversion est consignée",
                          "comparaison : nombre de lignes, statistiques par colonne, empreinte de contenu (critères de canot drill)",
                          "une vue n'a pas de table d'origine : rejouée, non comparée ; sa sortie alimente les modèles suivants"]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "rejeu-dbt.json").write_text(json.dumps(rapport, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return rapport
