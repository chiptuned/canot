"""Rejouabilité (spec §3) : familles de requêtes sans littéraux, catégories C0–C7, taux avec leur base.

Le texte brut (`_sql`) n'entre que dans `process_familles` et `raw_text_export` ; seul le second le rend, sur demande
(--include-sql-text), jamais pour une requête à secret. Le squelette DuckDB est local, en mémoire, sans accès fichier
ni réseau : rien ne part vers une plateforme, et les messages d'erreur (qui citent le SQL) ne sont jamais conservés.

C0 traduit et accepté par DuckDB ; C1 non analysable ; C2 commande ; C3 traduction impossible ; C4 erreur d'analyse
DuckDB ; C5 erreur de liaison ; C6 variable de session ; C7 DDL, COPY, CLONE, MERGE, DML (compté à part).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from contextlib import contextmanager

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

from .model import bucket_only, fig, pseudo

CATEGORIES = ("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7")
BASE_TAUX = ("C0", "C1", "C2", "C3", "C4", "C5", "C6")  # dénominateur des taux ; C7 est compté à part
MENTION = "accepté ≠ résultat identique"
LIBELLE_C0 = "traduit et accepté par DuckDB"

# constructions propres à la plateforme, comptées par node.key ; une fonction Anonymous est ajoutée en udf:/moteur:
PLATFORM_KEYS = frozenset({"historicaldata", "lateral", "qualify", "matchrecognize", "version", "predict", "connect",
                           "explode", "pivot", "tablesample", "tablefromrows"})

_SECRET = re.compile(
    r"PASSWORD|CREDENTIALS|\bSECRET|IDENTIFIED\s+BY|PRIVATE_KEY|MASTER_KEY|AWS_KEY_ID|AWS_SECRET_KEY|AWS_TOKEN"
    r"|AZURE_SAS_TOKEN|OAUTH_CLIENT_SECRET|OAUTH_REFRESH_TOKEN|API_KEY|ACCESS_TOKEN|\bTOKEN\s*=", re.I)
# filet derrière le WHERE de la collecte : utilisateurs, stages, secrets (valeurs de QUERY_TYPE : est.)
_SECRET_QUERY_TYPES = re.compile(r"USER|SECRET|STAGE|INTEGRATION|CONNECTION", re.I)
_DBT = re.compile(r"/\*\s*(\{[^{}]*\})\s*\*/", re.S)
_LEX = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.|'')*(?:'|$)|\$\$.*?(?:\$\$|$)|\"(?:[^\"]|\"\")*\"|`[^`]*`|\b\d+(?:\.\d+)?\b", re.S)
_STRINGS = (exp.Literal, exp.Boolean, exp.RawString, exp.National, exp.HexString, exp.ByteString, exp.BitString, exp.UnicodeString)
_C7 = (exp.Create, exp.Insert, exp.Copy, exp.Merge, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.TruncateTable,
       exp.Export, exp.MultitableInserts, exp.Comment, exp.Undrop)
_SEMI_STRUCTURED = {"VARIANT", "OBJECT", "ARRAY"}  # Snowflake : sqlglot traduit `v:a` en opérateurs JSON

R_C1 = "non analysable par sqlglot"
R_COMMAND = "commande : verbe seul conservé"
R_BLOCK = "plusieurs instructions"
R_SESSION = "commande de session ou d'administration"
R_C3 = "traduction vers DuckDB impossible"
R_C4 = "refusé par l'analyseur DuckDB"
R_C6 = "variable de session"
R_NON_TESTE = "colonnes non lues : EXPLAIN DuckDB non exécuté"


@contextmanager
def _quiet():
    """sqlglot journalise le SQL qu'il ne sait pas analyser : rien ne doit en sortir."""
    log = logging.getLogger("sqlglot")
    level = log.level
    log.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        log.setLevel(level)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def is_secret_query(sql: str | None, query_type: str | None = None) -> bool:
    """Requête à secret (mot de passe, identifiants, CREATE SECRET...) : comptée puis jetée, jamais normalisée ni stockée."""
    return bool(sql and _SECRET.search(sql)) or bool(query_type and _SECRET_QUERY_TYPES.search(query_type))


def _parse(sql: str, dialect: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read=dialect)
    except (SqlglotError, ValueError, TypeError, RecursionError):
        return None


def _dbt(sql: str) -> dict:
    """{app, node_id, dbt_version} du commentaire dbt ; les autres clés (profil, cible...) sont ignorées."""
    for m in _DBT.finditer(sql):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and ("dbt_version" in d or "node_id" in d or d.get("app") == "dbt"):
            return {k: str(d[k]) for k in ("app", "node_id", "dbt_version") if d.get(k) is not None}
    return {}


def _lexical(sql: str) -> str:
    """Empreinte de repli (C1) : commentaires retirés, chaînes et nombres masqués, y compris une chaîne tronquée."""
    def sub(m: re.Match) -> str:
        t = m.group(0)
        if t.startswith(("--", "/*")):
            return " "
        return t if t[0] in '"`' else "?"
    return " ".join(_LEX.sub(sub, sql).split())


def _ident(part, dialect: str) -> str | None:
    if part is None:
        return None
    name = part.name
    if not name:
        return None
    return name.upper() if dialect == "snowflake" and not part.args.get("quoted") else name


def _table_parts(tb: exp.Table, dialect: str) -> tuple[str, ...]:
    return tuple(p for p in (_ident(tb.args.get("catalog"), dialect), _ident(tb.args.get("db"), dialect),
                             _ident(tb.this, dialect)) if p)


def _ctes(tree: exp.Expression) -> set[str]:
    return {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}


def _tables(tree: exp.Expression, dialect: str) -> list[str]:
    ctes = _ctes(tree)
    out = set()
    for tb in tree.find_all(exp.Table):
        if not isinstance(tb.this, exp.Identifier):
            continue
        parts = _table_parts(tb, dialect)
        if parts and not (len(parts) == 1 and parts[0].upper() in ctes):
            out.add(".".join(parts))
    return sorted(out)


def _stage(text: str, dialect: str) -> str:
    """@BASE.SCHEMA.STAGE/chemin -> BASE.SCHEMA.STAGE : jamais le chemin."""
    name = text.lstrip("@").split("/")[0]
    if name.startswith(("~", "%")):
        return "@" + name[0]  # stage d'utilisateur ou de table : interne
    return name.replace('"', "") if '"' in name else (name.upper() if dialect == "snowflake" else name)


def _locations(tree: exp.Expression, dialect: str) -> tuple[list[str], list[str], list[str]]:
    """(compartiments d'export, stages référencés, stages cibles d'un COPY INTO @stage)."""
    exports, stages, cibles = set(), set(), set()
    for tb in tree.find_all(exp.Table):
        if isinstance(tb.this, exp.Var) and tb.this.name.startswith("@"):
            stages.add(_stage(tb.this.name, dialect))
    for cp in tree.find_all(exp.Copy):
        target = cp.this.this if isinstance(cp.this, exp.Table) else cp.this
        if isinstance(target, exp.Literal) and target.is_string and "://" in target.this:
            exports.add(bucket_only(target.this))
        elif isinstance(target, exp.Var) and target.name.startswith("@"):
            cibles.add(_stage(target.name, dialect))
    for ex in tree.find_all(exp.Export):
        options = ex.args.get("options")
        for lit in options.find_all(exp.Literal) if options else ():
            if lit.is_string and "://" in lit.this:
                exports.add(bucket_only(lit.this))
    exports.discard(None)
    return sorted(exports), sorted(stages), sorted(cibles)


def _keep_literal(node: exp.Expression) -> bool:
    """Ce qui n'est pas une valeur : `$1` (colonne de stage), paramètre de type, position dans GROUP BY / ORDER BY."""
    parent = node.parent
    if isinstance(parent, exp.Parameter) or node.find_ancestor(exp.DataType):
        return True
    return isinstance(node, exp.Literal) and not node.is_string and isinstance(parent, (exp.Group, exp.Ordered))


def _masked(tree: exp.Expression) -> exp.Expression:
    t = tree.copy()
    for cmd in list(t.find_all(exp.Command)):
        cmd.set("expression", None)  # le reste d'une commande peut contenir un mot de passe
    for node in list(t.find_all(exp.In)):
        if node.args.get("expressions"):
            node.set("expressions", [exp.Placeholder()])
    for node in list(t.find_all(*_STRINGS)):
        if not _keep_literal(node):
            node.replace(exp.Placeholder())
    return t


def _normalize(sql: str, tree: exp.Expression | None, dialect: str) -> tuple[str | None, dict]:
    infos: dict = {"empreinte": None, "racine": None, "dbt": _dbt(sql), "tables": [], "exports": [], "stages": [], "stages_cibles": []}
    if tree is None:
        infos["empreinte"] = _sha("C1\0" + _lexical(sql))
        return None, infos
    if isinstance(tree, exp.Command):
        forme = str(tree.this).upper()
    else:
        try:
            forme = _masked(tree).sql(dialect=dialect, comments=False)
        except (SqlglotError, RecursionError, ValueError):  # forme non générable : traitée comme non analysable
            return _normalize(sql, None, dialect)
        infos["tables"] = _tables(tree, dialect)
        infos["exports"], infos["stages"], infos["stages_cibles"] = _locations(tree, dialect)
    infos["racine"] = tree.key
    infos["empreinte"] = _sha(forme)
    return forme, infos


def normalize(sql: str, dialect: str) -> tuple[str | None, dict]:
    """(forme normalisée | None, infos). Littéraux et booléens -> ?, listes IN -> (?), commentaires retirés ;
    infos = {empreinte (sha256 de la forme), racine, dbt {app, node_id, dbt_version}, tables, exports (compartiments),
    stages, stages_cibles}. Une commande ne garde que son verbe ; une requête à secret n'est pas normalisée."""
    if is_secret_query(sql):
        return None, {"secret": True}
    with _quiet():
        return _normalize(sql, _parse(sql, dialect), dialect)


def _fn(name) -> str:
    """BASE.SCHEMA.F(NUMBER) -> F : on compare le nom seul."""
    return str(name).split("(")[0].strip('"`').rsplit(".", 1)[-1].strip('"`').upper()


def _names(functions: Iterable[str] | None) -> frozenset[str]:
    return frozenset(_fn(f) for f in functions or ())


def _fonctions(tree: exp.Expression | None, known: frozenset[str]) -> list[str]:
    if tree is None or isinstance(tree, exp.Command):
        return []
    out = set()
    for node in tree.walk():
        if node.key in PLATFORM_KEYS:
            out.add(node.key)
        elif isinstance(node, exp.Anonymous):
            name = _fn(node.name)
            out.add(("udf:" if name in known else "moteur:") + name)
        elif isinstance(node, exp.Table) and isinstance(node.this, exp.Var) and node.this.name.startswith("@"):
            out.add("stage")
    return sorted(out)


def _table_specs(columns) -> list[tuple[tuple[str, ...], list[dict]]]:
    """Tables du snapshot ([Table]) ou {"BASE.SCHEMA.NOM": [{"nom", "type"}]} -> [(parties, colonnes)], triées."""
    if isinstance(columns, Mapping):
        specs = [(tuple(p for p in str(k).split(".") if p), list(v or [])) for k, v in columns.items()]
    else:
        specs = [(tuple(p for p in (t.get("base"), t.get("schema"), t.get("nom")) if p), list(t.get("colonnes") or []))
                 for t in columns]
    return sorted((s for s in specs if s[0]), key=lambda s: tuple(p.upper() for p in s[0]))


class _Skeleton:
    """Une table vide typée par table de COLUMNS, sous un nom interne ; les références des requêtes y sont résolues."""

    def __init__(self, columns, dialect: str):
        self.dialect = dialect
        self.con = duckdb.connect(config={"enable_external_access": False, "autoinstall_known_extensions": False,
                                          "autoload_known_extensions": False})
        self.index: dict[tuple[str, ...], str] = {}
        self.types: dict[str, str] = {}
        self.ignorees = 0
        for i, (parts, cols) in enumerate(_table_specs(columns)):
            defs, seen = [], set()
            for c in cols:
                name = c.get("nom")
                if not name or name.upper() in seen:
                    continue
                seen.add(name.upper())
                defs.append(f"{exp.to_identifier(name, quoted=True).sql('duckdb')} {self._type(c.get('type'))}")
            internal = f"__canot_t{i}"
            try:
                self.con.execute(f'CREATE TABLE "{internal}" ({", ".join(defs)})')
            except duckdb.Error:
                self.ignorees += 1  # table sans colonne lisible : les requêtes qui la lisent tomberont en C5
                continue
            key = tuple(p.upper() for p in parts)
            for n in range(1, len(key) + 1):
                self.index.setdefault(key[-n:], internal)  # nom ambigu : la première table dans l'ordre trié
        self.con.execute("SET lock_configuration = true")

    def _type(self, platform_type: str | None) -> str:
        """Type plateforme -> type DuckDB, validé par DuckDB ; repli VARCHAR."""
        key = (platform_type or "").strip()
        if key not in self.types:
            out = "VARCHAR"
            if key.upper() in _SEMI_STRUCTURED:
                out = "JSON"
            elif key:
                try:
                    cand = exp.DataType.build(key, dialect=self.dialect).sql("duckdb")
                    self.con.execute(f"SELECT CAST(NULL AS {cand})")
                    out = cand
                except (SqlglotError, ValueError, duckdb.Error):
                    pass  # repli VARCHAR
            self.types[key] = out
        return self.types[key]

    def rewrite(self, query: exp.Expression) -> None:
        ctes = _ctes(query)
        renamed = set()
        for tb in list(query.find_all(exp.Table)):
            if not isinstance(tb.this, exp.Identifier):
                continue
            parts = tuple(p.upper() for p in _table_parts(tb, self.dialect))
            if not parts or (len(parts) == 1 and parts[0] in ctes):
                continue
            internal = self.index.get(parts)
            if internal is None:
                continue
            if not tb.args.get("alias"):
                tb.set("alias", exp.TableAlias(this=exp.to_identifier(tb.name)))
            renamed.add(parts[-2:])
            tb.set("catalog", None)
            tb.set("db", None)
            tb.set("this", exp.to_identifier(internal, quoted=True))
        for col in query.find_all(exp.Column):  # schema.table.colonne -> table.colonne
            if col.args.get("db") and (col.db.upper(), col.table.upper()) in renamed:
                col.set("db", None)
                col.set("catalog", None)

    def check(self, sql: str, params: bool, udf: bool) -> tuple[str, str]:
        """EXPLAIN (PREPARE quand la requête porte des paramètres liés, que EXPLAIN exigerait)."""
        try:
            if params:
                self.con.execute(f"PREPARE canot_q AS {sql}")
                self.con.execute("DEALLOCATE canot_q")
            else:
                self.con.execute(f"EXPLAIN {sql}")
        except duckdb.ParserException:
            return "C4", R_C4
        except duckdb.InvalidInputException as e:
            if not params and "prepared statement parameters" in str(e):
                return self.check(sql, True, udf)
            return "C5", _liaison(e, udf)
        except duckdb.Error as e:
            return "C5", _liaison(e, udf)
        return "C0", LIBELLE_C0

    def close(self) -> None:
        self.con.close()


def _liaison(e: BaseException, udf: bool) -> str:
    """Raison courte et fixe : le message de DuckDB cite le SQL, il n'est jamais conservé."""
    m = str(e).lower()
    if "function" in m:
        return "UDF du client" if udf else "fonction absente de DuckDB"
    if "time travel" in m:
        return "lecture historique (time travel)"
    if udf and "catalog" in m:
        return "UDF du client"
    if "table with name" in m or "catalog" in m or "schema with name" in m:
        return "table absente des métadonnées"
    if "column" in m:
        return "colonne non résolue"
    return "liaison refusée par DuckDB"


def _testable(tree: exp.Expression) -> exp.Expression | None:
    """Select/With/Union, ou le SELECT interne d'un CTAS, d'un INSERT…SELECT, d'un CREATE VIEW (copie)."""
    if isinstance(tree, exp.Query):
        return tree.copy()
    inner = None
    if (isinstance(tree, exp.Create) and not tree.args.get("clone") and str(tree.args.get("kind") or "").upper() in ("TABLE", "VIEW")
            and isinstance(tree.expression, exp.Query)):
        inner = tree.expression
    elif isinstance(tree, exp.Insert) and isinstance(tree.expression, exp.Query):
        inner = tree.expression
    if inner is None:
        return None
    inner = inner.copy()
    while isinstance(inner, exp.Subquery) and not inner.alias:
        inner = inner.this
    if tree.args.get("with_") and not inner.args.get("with_"):
        inner.set("with_", tree.args["with_"].copy())
    return inner


_METADATA = re.compile(r"\b(INFORMATION_SCHEMA|ACCOUNT_USAGE|ORGANIZATION_USAGE|READER_ACCOUNT_USAGE)\b", re.I)


def _c7_raison(tree: exp.Expression) -> str:
    if isinstance(tree, exp.Copy):
        return "COPY"
    if isinstance(tree, exp.Merge):
        return "MERGE"
    if isinstance(tree, exp.Export):
        return "EXPORT"
    if isinstance(tree, exp.Create) and tree.args.get("clone"):
        return "CLONE"
    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.MultitableInserts)):
        return "DML"
    return "DDL"


def _categorize(tree: exp.Expression | None, dialect: str, skel: _Skeleton | None, known: frozenset[str]) -> tuple[str | None, str]:
    if tree is None:
        return "C1", R_C1
    if isinstance(tree, exp.Command):
        return "C2", R_COMMAND
    if isinstance(tree, exp.Block):
        return "C2", R_BLOCK
    if _METADATA.search(tree.sql(dialect=dialect)):
        return "C7", "METADONNEES"  # catalogue de la plateforme (INFORMATION_SCHEMA...) : sans équivalent à rejouer, compté à part
    query = _testable(tree)
    if query is None:
        if isinstance(tree, exp.Alter) and str(tree.args.get("kind") or "").upper() == "SESSION":
            return "C2", R_SESSION
        if isinstance(tree, _C7):
            return "C7", _c7_raison(tree)
        return "C2", R_SESSION
    udf = any(isinstance(n, exp.Anonymous) and _fn(n.name) in known for n in query.walk())
    if skel is not None:
        skel.rewrite(query)
    try:
        sql = query.sql(dialect="duckdb", unsupported_level=ErrorLevel.RAISE)
    except Exception:  # noqa: BLE001 : UnsupportedError, ou générateur en échec : la traduction n'aboutit pas
        return "C3", R_C3
    if skel is None:
        return None, R_NON_TESTE
    cat, raison = skel.check(sql, query.find(exp.Placeholder, exp.Parameter) is not None, udf)
    if cat == "C0" and dialect == "snowflake" and any(isinstance(p.this, exp.Var) for p in query.find_all(exp.Parameter)):
        return "C6", R_C6
    return cat, raison


def classify(sql: str, dialect: str, skeleton_columns=None, known_functions: Iterable[str] = ()) -> str | None:
    """Catégorie C0..C7 d'une requête. La forme brute est plus juste : une forme normalisée peut tomber à tort en
    C3/C4 (`TABLESAMPLE (?)`, clés d'OBJECT_CONSTRUCT masquées) ; process_familles classe donc le texte brut du
    représentant, en mémoire. skeleton_columns : tables du snapshot ou
    {"BASE.SCHEMA.NOM": [{"nom", "type"}]} ; None = colonnes non lues, la catégorie d'une requête traduite reste None.
    known_functions (FUNCTIONS/ROUTINES) distingue une UDF du client d'une fonction moteur. Requête à secret : None."""
    if is_secret_query(sql):
        return None
    with _quiet():
        skel = _Skeleton(skeleton_columns, dialect) if skeleton_columns is not None else None
        try:
            return _categorize(_parse(sql, dialect), dialect, skel, _names(known_functions))[0]
        finally:
            if skel is not None:
                skel.close()


def _row_key(row: dict) -> tuple:
    return (-int(row.get("n_exec") or 0), str(row.get("hash_plateforme") or ""), _sha(str(row.get("_sql") or "")))


def _sum(values: Iterable) -> float | int | None:
    vals = [v if isinstance(v, (int, float)) else float(v) for v in values if v is not None]  # Decimal du connecteur
    if not vals:
        return None
    total = sum(vals)
    return round(total, 6) if isinstance(total, float) else total


def _str(v) -> str | None:
    return None if v is None else str(v)


def _n(x) -> str:
    if isinstance(x, float):
        return f"{x:,.2f}".replace(",", " ").replace(".", ",")
    return f"{x:,}".replace(",", " ")


def _periode(fenetre: dict | None) -> str:
    def d(iso: str | None) -> str | None:
        return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if iso and len(iso) >= 10 else None
    du, au = d((fenetre or {}).get("du")), d((fenetre or {}).get("au"))
    return f"du {du} au {au}" if du and au else "fenêtre non lue"


def _famille(empreinte: str, g: dict, dialect: str, skel: _Skeleton | None, known: frozenset[str], salt: bytes,
             external: frozenset[str]) -> dict:
    rows, tree, infos = g["rows"], g["tree"], g["infos"]
    rep = rows[0]  # la plus exécutée (ordre de _row_key)
    cat, raison = _categorize(tree, dialect, skel, known)
    hashes = sorted({str(r["hash_plateforme"]) for r in rows if r.get("hash_plateforme")})
    exports = sorted(set(g["exports"]) | {s for s in g["cibles"] if s.upper() in external or any(e.endswith("." + s.upper()) for e in external)})
    dbt = infos["dbt"]
    detail = {"raison": raison, "query_type": rep.get("query_type"), "dbt_node_id": dbt.get("node_id"),
              "dbt_version": dbt.get("dbt_version"), "export": exports, "stages": sorted(g["stages"]),
              "stages_cibles": sorted(g["cibles"]), "n_utilisateurs": len({r["utilisateur"] for r in rows if r.get("utilisateur")})}
    if len(hashes) > 1:
        detail["hashes_plateforme"] = hashes
    return {
        "empreinte": empreinte, "hash_plateforme": hashes[0] if hashes else None,
        "n_exec": sum(int(r.get("n_exec") or 0) for r in rows),
        "temps_ms": _sum(r.get("temps_ms") for r in rows), "credits": _sum(r.get("credits") for r in rows),
        "octets": _sum(r.get("octets") for r in rows),
        "app": rep.get("app"), "outil": dbt.get("app") or next((r["outil"] for r in rows if r.get("outil")), None),
        "utilisateur": pseudo(_str(rep.get("utilisateur")), salt, "u"), "role": pseudo(_str(rep.get("role")), salt, "r"),
        "entrepot": rep.get("entrepot"),
        "tables": sorted(set(infos["tables"]) | {t for r in rows for t in (r.get("tables") or [])}), "fonctions": _fonctions(tree, known), "categorie": cat, "sql_normalise": g["forme"],
        "ecriture_externe": bool(exports) or isinstance(tree, exp.Export),
        "detail": detail,
    }


def _taux(axe: str, num, den, unite_base: str, lectures: list[str], suffixe: str, hypotheses: list[str]) -> tuple[dict, str]:
    base = f"{_n(num)}/{_n(den)} {unite_base}, {suffixe}"
    if not den:
        return fig(nature="non_estimé", raison=f"aucune {axe} testable", base=lectures), base
    return fig(round(100 * num / den, 1), nature="calculé", unite="%", base=lectures,
               formule=f"{LIBELLE_C0} (C0) / C0 à C6 ; C7 compté à part", hypotheses=[MENTION, *hypotheses]), base


def _rejouabilite(familles: list[dict], dialect: str, fenetre: dict | None, lectures: list[str], ecartees: dict,
                  testees: bool, ignorees: int) -> dict:
    poids_key = "octets" if dialect == "bigquery" else "credits"
    poids_lu = any(f[poids_key] is not None for f in familles)
    cats: dict = {}
    for c in CATEGORIES:
        members = [f for f in familles if f["categorie"] == c]
        vals = [f[poids_key] for f in members if f[poids_key] is not None]
        poids = _sum(vals) if vals else (0 if poids_lu and not members else None)
        cats[c] = {"familles": len(members), "executions": sum(f["n_exec"] for f in members), "poids": poids}
    non_testees = [f for f in familles if f["categorie"] is None]
    versions = {"sqlglot": sqlglot.__version__, "duckdb": duckdb.__version__}
    suffixe = f"{_periode(fenetre)}, sqlglot {versions['sqlglot']}, duckdb {versions['duckdb']}"
    test = [f for f in familles if f["categorie"] in BASE_TAUX]
    out_taux, bases = {}, {}
    if not testees:
        for axe in ("familles", "executions", "poids"):
            out_taux[axe] = fig(nature="non_estimé", raison=R_NON_TESTE, base=lectures)
        bases = {axe: f"{R_NON_TESTE}, {suffixe}" for axe in ("familles", "executions", "poids")}
    else:
        out_taux["familles"], bases["familles"] = _taux("famille", cats["C0"]["familles"], len(test), "familles", lectures, suffixe, [])
        out_taux["executions"], bases["executions"] = _taux(
            "exécution", cats["C0"]["executions"], sum(f["n_exec"] for f in test), "exécutions", lectures, suffixe, [])
        unite = "octets" if poids_key == "octets" else "crédits"
        if not poids_lu:
            out_taux["poids"] = fig(nature="non_lu", raison=f"{unite} non lus", base=lectures)
            bases["poids"] = f"{unite} non lus, {suffixe}"
        else:
            lus = [f for f in test if f[poids_key] is not None]
            hyp = [] if len(lus) == len(test) else [f"poids lu pour {len(lus)}/{len(test)} familles"]
            out_taux["poids"], bases["poids"] = _taux("famille", cats["C0"]["poids"] or 0, _sum(f[poids_key] for f in lus) or 0,
                                                      unite, lectures, suffixe, hyp)
    fonctions: dict[str, int] = {}
    for f in familles:
        for k in f["fonctions"]:
            fonctions[k] = fonctions.get(k, 0) + 1
    return {
        "categories": cats, "base": bases["familles"], "mention": MENTION, "libelle_c0": LIBELLE_C0,
        "taux": out_taux, "bases": bases, "poids_unite": poids_key,
        "non_testees": {"familles": len(non_testees), "executions": sum(f["n_exec"] for f in non_testees)},
        "secrets_ecartes": ecartees, "fonctions": dict(sorted(fonctions.items())),
        "tables_hors_squelette": ignorees, "fenetre": fenetre, "versions": versions,
    }


def process_familles(raw: list[dict], dialect: str, *, columns, known_functions: Iterable[str] | None, salt: bytes,
                     fenetre: dict | None = None, lectures: list[str] | None = None,
                     external_stages: Iterable[str] = ()) -> tuple[list[dict], dict]:
    """raw = [{_sql, hash_plateforme, n_exec, temps_ms, credits, octets, app, utilisateur, role, entrepot, query_type}].
    Rend (familles, rejouabilite) sans aucune trace de `_sql` : regroupement par empreinte, utilisateur et rôle
    pseudonymisés, requêtes à secret comptées puis jetées. columns : tables du snapshot ou {"BASE.SCHEMA.NOM": [...]},
    None si COLUMNS n'a pas été lue (EXPLAIN non exécuté). fenetre {du, au} et lectures (ids) alimentent la base des taux ;
    external_stages (noms de stages externes) fait d'un COPY INTO @stage une écriture externe (F03)."""
    known = _names(known_functions)
    external = frozenset(str(s).upper() for s in external_stages)
    ecartees = {"familles": 0, "executions": 0}
    groups: dict[str, dict] = {}
    with _quiet():
        for row in sorted(raw, key=_row_key):
            sql = str(row.get("_sql") or "")
            if is_secret_query(sql, row.get("query_type")):
                ecartees["familles"] += 1
                ecartees["executions"] += int(row.get("n_exec") or 0)
                continue
            tree = _parse(sql, dialect)
            forme, infos = _normalize(sql, tree, dialect)
            if infos["racine"] is None:
                tree = None
            g = groups.setdefault(infos["empreinte"], {"forme": forme, "infos": infos, "tree": tree, "rows": [],
                                                       "exports": set(), "stages": set(), "cibles": set()})
            g["rows"].append(row)
            g["exports"].update(infos["exports"])
            g["stages"].update(infos["stages"])
            g["cibles"].update(infos["stages_cibles"])
        skel = _Skeleton(columns, dialect) if columns is not None else None
        try:
            familles = [_famille(e, g, dialect, skel, known, salt, external) for e, g in groups.items()]
        finally:
            if skel is not None:
                skel.close()
    poids_key = "octets" if dialect == "bigquery" else "credits"
    familles.sort(key=lambda f: (-(f[poids_key] or 0), -f["n_exec"], f["empreinte"]))
    rej = _rejouabilite(familles, dialect, fenetre, list(lectures or []), ecartees, columns is not None,
                        skel.ignorees if skel is not None else 0)
    return familles, rej


def raw_text_export(raw: list[dict], dialect: str | None = None) -> list[dict]:
    """Pour --include-sql-text seulement : le texte brut, jamais celui d'une requête à secret. Avec le dialecte,
    chaque entrée porte l'empreinte de sa famille."""
    out = []
    with _quiet():
        for row in sorted(raw, key=_row_key):
            sql = row.get("_sql")
            if not sql or is_secret_query(sql, row.get("query_type")):
                continue
            entry = {"hash_plateforme": row.get("hash_plateforme"), "empreinte": None, "n_exec": row.get("n_exec"), "sql": sql}
            if dialect:
                entry["empreinte"] = _normalize(sql, _parse(sql, dialect), dialect)[1]["empreinte"]
            out.append(entry)
    return out
