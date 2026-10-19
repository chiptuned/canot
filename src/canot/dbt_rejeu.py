"""P3 evidence on a dbt project: the SQL models are replayed in a local DuckDB from the copy of the sources,
then each output is compared with the table the platform had materialized, using the `canot drill` criteria
(row count, per-column statistics, content fingerprint: verify.check_table).

Input: the dbt artifacts (target/manifest.json, compiled SQL) and a copy that holds the source tables and the models'
original tables: a DuckDB database, or the Iceberg copy written by `canot drill`. Nothing is sent to the platform.

Each model gets a status (data values, unchanged): `identique` (replayed, compared, equal), `different` (replayed,
compared, mismatch), `non_compare` (replayed, no original table in the copy: a view for instance; its output feeds the
next models) or `non_rejouable` (with the reason: language, materialization, platform-specific function, translation,
dependency not replayed).
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
DOSSIER = "canot.replay-dbt/1"
REPORT_FILE = "replay-dbt.json"


def _arrow(rel) -> pa.Table:
    return rel.to_arrow_table() if hasattr(rel, "to_arrow_table") else rel.fetch_arrow_table()


class _Copie:
    """The copy seen as the "source" of verify.check_table: the original tables, queried in DuckDB SQL."""

    dialect = "duckdb"

    def __init__(self, con: duckdb.DuckDBPyConnection, index: dict[tuple[str, str], tuple[str, str]]):
        self.con, self.index = con, index

    def table_ref(self, t: TableInfo) -> str:
        return f"{COPIE}.{q(t.schema)}.{q(t.name)}"

    def query(self, sql: str) -> pa.Table:
        return _arrow(self.con.execute(sql))


class _Rejoue:
    """An Arrow table presented as an Iceberg table (schema().as_arrow(), scan().to_arrow_batch_reader())."""

    def __init__(self, tb: pa.Table):
        self.tb = tb

    def schema(self):
        return SimpleNamespace(as_arrow=lambda: self.tb.schema)

    def scan(self):
        return SimpleNamespace(to_arrow_batch_reader=lambda: self.tb.to_batches())


def _connexion() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(config={"autoinstall_known_extensions": False, "autoload_known_extensions": False})


def ouvrir_copie(base: str | None = None, lac: str | None = None, drill_out: str | None = None, catalog: str | None = None):
    """(in-memory DuckDB connection, index (SCHEMA, NAME) -> (schema, name) of the copy's tables, description)."""
    con = _connexion()
    if base:
        chemin = str(Path(base).expanduser()).replace("'", "''")
        con.execute(f"ATTACH '{chemin}' AS {COPIE} (READ_ONLY)")
        rows = con.execute(f"select table_schema, table_name from information_schema.tables where table_catalog = '{COPIE}' "
                           "and table_type = 'BASE TABLE'").fetchall()
        desc = f"DuckDB database {Path(base).name}"
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
        desc = f"Iceberg copy {lac}"
    else:
        raise ValueError("specify the copy: --base FILE.duckdb, or --iceberg DESTINATION (the copy written by canot drill)")
    con.execute(f"create schema {SCHEMA_REJEU}")
    return con, {(s.upper(), n.upper()): (s, n) for s, n in rows}, desc


def _ordre(models: dict[str, dict]) -> list[str]:
    """Topological order of the models (parents first), stable."""
    restants = {u: {p for p in (n.get("depends_on") or {}).get("nodes") or [] if p in models} for u, n in models.items()}
    out = []
    while restants:
        prets = sorted(u for u, ps in restants.items() if not ps)
        if not prets:  # cycle: impossible in dbt, but never loop forever
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
    """Replace each relation with the replayed table (model) or the copy's table (source, seed…). Return the reason for a refusal."""
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
                return f"depends on a model that was not replayed ({uid})"
            uid = None  # snapshot, seed: their original table is an input, read from the copy
        if uid is not None:
            nom, schema, catalogue = rejoues[uid], SCHEMA_REJEU, None
        else:
            hit = copie.get(tuple(p.upper() for p in parts[-2:])) if len(parts) >= 2 else None
            if hit is None:
                nus = [v for k, v in copie.items() if k[1] == parts[-1].upper()]
                hit = nus[0] if len(nus) == 1 else None
            if hit is None:
                return f"table missing from the copy ({'.'.join(parts)})"
            catalogue, schema, nom = COPIE, hit[0], hit[1]
        if not tb.args.get("alias"):
            tb.set("alias", exp.TableAlias(this=exp.to_identifier(tb.name)))
        renommees.add(tuple(p.upper() for p in parts[-2:]))
        tb.set("catalog", exp.to_identifier(catalogue, quoted=True) if catalogue else None)
        tb.set("db", exp.to_identifier(schema, quoted=True))
        tb.set("this", exp.to_identifier(nom, quoted=True))
    for col in tree.find_all(exp.Column):  # schema.table.column -> table.column
        if col.args.get("db") and (col.db.upper(), col.table.upper()) in renommees:
            col.set("db", None)
            col.set("catalog", None)
    return None


def _colonnes(con, ref: str) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in con.execute(f"describe {ref}").fetchall()]


def _aligner(con, rejouee: str, origine: str) -> tuple[str | None, list[str], str | None]:
    """SELECT that gives the replayed output the columns and types of the original table (original order).
    Return (sql, logged conversions, reason for a column mismatch)."""
    mine = {n.lower(): (n, t) for n, t in _colonnes(con, rejouee)}
    ref = _colonnes(con, origine)
    manquantes = [n for n, _ in ref if n.lower() not in mine]
    en_trop = [n for n in mine if n not in {x.lower() for x, _ in ref}]
    if manquantes or en_trop:
        return None, [], ("different columns: " + ", ".join(
            ([f"missing from the replay {', '.join(manquantes[:5])}"] if manquantes else []) + ([f"extra {', '.join(en_trop[:5])}"] if en_trop else [])))
    sel, conv = [], []
    for n, t in ref:
        src_n, src_t = mine[n.lower()]
        if src_t != t:
            conv.append(f"{n}: {src_t} -> {t}")
            sel.append(f"cast({q(src_n)} as {t}) as {q(n)}")
        else:
            sel.append(f"{q(src_n)} as {q(n)}")
    return f"select {', '.join(sel)} from {rejouee}", conv, None


def _raison_duckdb(e: BaseException) -> str:
    """Short, fixed reason: the DuckDB message quotes the SQL, so it is never kept."""
    if isinstance(e, duckdb.ParserException):
        return replay.R_C4
    r = replay._liaison(e, False)
    return "DuckDB execution failed" if r == replay._liaison(duckdb.Error(""), False) else r  # generic binder fallback


def chiffres_flottants(tol: Tolerances) -> int:
    """Significant digits kept for the float fingerprint: finer than the relative tolerance (1e-9 -> 12)."""
    return max(6, min(15, round(-math.log10(tol.float_rel)) + 3)) if tol.float_rel > 0 else 15


def _arrondir(tb: pa.Table, cols: list[str], chiffres: int) -> pa.Table:
    for c in cols:
        i = tb.schema.get_field_index(c)
        vals = [None if v is None else format(v, f".{chiffres}g") for v in tb.column(i).to_pylist()]
        tb = tb.set_column(i, c, pa.array(vals, type=pa.string()))
    return tb


def _empreinte_flottants(orig: pa.Table, rej: pa.Table, tol: Tolerances, res: dict) -> bool:
    """Equal rows and statistics, different fingerprint: with float columns, a float sum computed in another order
    differs in the last bit (IEEE 754 addition is not associative), and the fingerprint hashes the exact text.
    Recompute the fingerprint on both sides with these columns rounded to N significant digits (N finer than the tolerance)
    and measure the exact difference. Equal: identical within tolerance, logged; otherwise the mismatch stands."""
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
        res["note"] = (f"identical within tolerance: fingerprint recomputed with {', '.join(cols)} rounded to {n} significant digits; "
                       "float values differ in the last bit (float sum in another order), see verification.empreinte_flottants")
    return egal


def rejouer(projet: dbt_scan.ProjetDbt, con, copie: dict, tol: Tolerances, log=lambda s: None) -> list[dict]:
    """Replay the project's models in `con` (rejeu schema) and compare each output with its original table in the copy."""
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
            res["raison"] = f"language {a['langage']}: not replayed"
        elif mat == "ephemeral":
            res["raison"] = "ephemeral: inlined as a CTE in the models that use it, replayed through them"
        elif mat == "incremental":
            res["raison"] = "incremental: the compiled SQL depends on the previous state of the table"
        elif port == "a_reconstruire":
            res["raison"] = raison_p
        elif not (compiled and compiled.strip()):
            res["raison"] = "compiled SQL missing from the manifest (dbt compile produces it)"
        elif a["secret"]:
            res["raison"] = "secret detected in the SQL: set aside"
        if res["raison"]:
            log(f"   [not replayable] {uid}: {res['raison']}")
            continue
        with replay._quiet():
            tree = replay._parse(compiled, dialect or "")
        if not isinstance(tree, exp.Query):
            res["raison"] = replay.R_C1 if tree is None else "not a SELECT query"
            continue
        refus = _reecrire(tree, relations, rejoues, copie)
        if refus:
            res["raison"] = refus
            log(f"   [not replayable] {uid}: {refus}")
            continue
        try:
            sql = tree.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
        except Exception:  # noqa: BLE001 : translation refused by sqlglot
            res["raison"] = replay.R_C3
            continue
        cible = f"{(n.get('schema') or 'x')}__{n.get('alias') or n.get('name')}"
        ref = f"{SCHEMA_REJEU}.{q(cible)}"
        try:
            con.execute(f"create table {ref} as {sql}")
        except duckdb.Error as e:
            res["raison"] = _raison_duckdb(e)
            log(f"   [not replayable] {uid}: {res['raison']}")
            continue
        rejoues[uid] = cible
        origine = copie.get(((n.get("schema") or "").upper(), (n.get("alias") or n.get("name") or "").upper()))
        if origine is None:
            res.update(statut="non_compare", raison="no original table in the copy" + (" (view)" if mat == "view" else ""))
            log(f"   [replayed, not compared] {uid}")
            continue
        oref = f"{COPIE}.{q(origine[0])}.{q(origine[1])}"
        sel, conv, ecart = _aligner(con, ref, oref)
        res["conversions"] = conv
        if ecart:
            res.update(statut="different", raison=ecart)
            log(f"   [replayed, different] {uid}: {ecart}")
            continue
        try:  # the output aligned on the original types replaces the raw output: the next models read what the platform stored
            con.execute(f"create table {SCHEMA_REJEU}.{q(cible + '__a')} as {sel}")
            con.execute(f"drop table {ref}")
            con.execute(f"alter table {SCHEMA_REJEU}.{q(cible + '__a')} rename to {q(cible)}")
        except duckdb.Error as e:
            res.update(statut="different", raison="conversion to the original types failed (" + _raison_duckdb(e) + ")")
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
        res["raison"] = None if ok else "; ".join(chk.stats_diffs[:3]) or "different content fingerprint"
        log(f"   [replayed, {'identical' if ok else 'different'}] {uid}")
    return out


def run_rejeu(url: str, out: Path, key_path: Path, *, base: str | None = None, lac: str | None = None, drill_out: str | None = None,
              catalog: str | None = None, tol: Tolerances | None = None, log=print) -> dict:
    tol = tol or Tolerances()
    t0 = time.monotonic()
    projet = dbt_scan.ProjetDbt(url)
    con, copie, desc = ouvrir_copie(base, lac, drill_out, catalog)
    ev = EvidenceLog(out)
    env = {"canot": __version__, "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__}
    ev.add("start", {"projet": projet.describe(), "copie": desc, "forme": "P3 pipeline replay", "env": env, "tolerances": tol.to_dict()})
    try:
        log(f"→ replaying the dbt models of {projet.describe()} on {desc}")
        res = rejouer(projet, con, copie, tol, log)
    finally:
        con.close()
    for r in res:
        ev.add("dbt_model", r)
    compte = {s: sum(1 for r in res if r["statut"] == s) for s in STATUTS}
    summary = {"modeles": len(res), **compte, "duree_s": round(time.monotonic() - t0, 1),
               "preuve": "complete" if res and compte["identique"] == len(res) else ("mismatch" if compte["different"] else "partial")}
    ev.add("summary", summary)
    seal = ev.seal(key_path, log)
    rapport = {"schema": DOSSIER, "projet": projet.describe(), "copie": desc, "forme": "P3", "summary": summary, "modeles": res,
               "env": env, "tolerances": tol.to_dict(), "seal": seal,
               "regles": ["same compiled SQL, copied inputs, two runs: source platform and local DuckDB",
                          "types of the replayed output aligned on the original table before comparison; each conversion is logged",
                          "comparison: row count, per-column statistics, content fingerprint (canot drill criteria)",
                          "a view has no original table: replayed, not compared; its output feeds the next models"]}
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_FILE).write_text(json.dumps(rapport, indent=2, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return rapport
