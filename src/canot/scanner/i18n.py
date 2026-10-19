"""Language of the scan texts: English (scan.json, CSV, command line) or French (the French pane of scan.html only).

analyze() and render_html() write their texts through `t(en, fr)`; the language is set for the duration of one call with `use(lang)`
(a context variable: no global state survives the call, analyze stays pure). English is the default, so every existing caller gets
exactly the texts it had. `tr()` translates the English phrases that come from collection (read reasons, replay bases, freshness):
an unknown phrase is returned as is, never guessed.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANGS = ("en", "fr")
NBSP = " "
_LANG: ContextVar[str] = ContextVar("canot_scan_lang", default="en")


def lang() -> str:
    return _LANG.get()


def is_fr() -> bool:
    return _LANG.get() == "fr"


@contextmanager
def use(code: str | None) -> Iterator[None]:
    tok = _LANG.set(code if code in LANGS else "en")
    try:
        yield
    finally:
        _LANG.reset(tok)


def t(en: str, fr: str) -> str:
    return fr if _LANG.get() == "fr" else en


# --- numbers ------------------------------------------------------------------------------------------------


def fmt_num(x, dec: int = 0) -> str:
    s = f"{x:,.{dec}f}"
    if _LANG.get() == "fr":
        s = s.replace(",", "\0").replace(".", ",").replace("\0", NBSP)
    return s


def trim0(s: str) -> str:
    """1.0 -> 1 (1,0 -> 1 in French)."""
    return s[:-2] if s.endswith((".0", ",0")) else s


def plural(n, singular: str, plural_form: str | None = None) -> str:
    """English: singular only for 1. French: singular for 0 and 1."""
    one = abs(n) < 2 if _LANG.get() == "fr" else n == 1
    return f"{fmt_num(n)} {singular if one else (plural_form or singular + 's')}"


def usd(x, dec: int = 2) -> str:
    return f"{fmt_num(x, dec)}{NBSP}$" if _LANG.get() == "fr" else f"${fmt_num(x, dec)}"


def pct(x, dec: int = 1) -> str:
    return f"{fmt_num(x, dec)}{NBSP}%" if _LANG.get() == "fr" else f"{fmt_num(x, dec)}%"


BYTE_UNITS = {"en": (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3), "B"),
              "fr": (("To", 1e12), ("Go", 1e9), ("Mo", 1e6), ("ko", 1e3), "o")}


# --- phrases that come from collection (English in the snapshot) ---------------------------------------------

EXACT = {
    # model.classify_failure and the collectors' reasons
    "missing privilege": "droit absent",
    "edition required": "édition requise",
    "absent or not exposed": "absent ou non exposé",
    "service disabled": "service désactivé",
    "timed out": "délai dépassé",
    "refused by the read-only guard": "refusée par la garde lecture seule",
    "privilege level not requested": "niveau de droits non demandé",
    "not attempted": "non tentée",
    "reason not provided": "motif non fourni",
    "level 2 not requested": "niveau 2 non demandé",
    "level 3 not requested": "niveau 3 non demandé",
    "rows filtered by the role's privileges: objects it cannot see are missing":
        "lignes filtrées par les droits du rôle : les objets qu'il ne voit pas manquent",
    "real time, filtered by the role's privileges: cross-checks ACCOUNT_USAGE":
        "temps réel, filtré par les droits du rôle : recoupe ACCOUNT_USAGE",
    "table too recent for ACCOUNT_USAGE": "table trop récente pour ACCOUNT_USAGE",
    "missing privilege or view not enabled (enable_info_schema_storage, which Canot does not change); fallback to tables.get":
        "droit absent ou vue non activée (enable_info_schema_storage, que Canot ne modifie pas) ; repli sur tables.get",
    # replay and dbt (replay.R_*, dbt.R_*)
    "not parsable by sqlglot": "non analysable par sqlglot",
    "command: verb only kept": "commande : seul le verbe est conservé",
    "multiple statements": "plusieurs instructions",
    "session or administration command": "commande de session ou d'administration",
    "translation to DuckDB impossible": "traduction vers DuckDB impossible",
    "rejected by the DuckDB parser": "rejetée par l'analyseur DuckDB",
    "session variable": "variable de session",
    "columns not read: DuckDB EXPLAIN not run": "colonnes non lues : EXPLAIN DuckDB non exécuté",
    "table outside the read scope: not tested": "table hors du périmètre lu : non testée",
    "text not provided by the platform: not tested": "texte non fourni par la plateforme : non testé",
    "customer UDF": "UDF client",
    "function absent from DuckDB": "fonction absente de DuckDB",
    "table absent from the metadata": "table absente des métadonnées",
    "binding rejected by DuckDB": "liaison rejetée par DuckDB",
    "compiled SQL absent and Jinja not resolved (dbt compile produces it)": "SQL compilé absent et Jinja non résolu (dbt compile le produit)",
    "Python model: not analyzed (depends on the adapter API)": "modèle Python : non analysé (dépend de l'API de l'adaptateur)",
    "code absent from the manifest": "code absent du manifeste",
    "secret detected: counted then dropped": "secret détecté : compté puis écarté",
    "not analyzed": "non analysé",
    "catalog.json not read: DuckDB EXPLAIN not run": "catalog.json non lu : EXPLAIN DuckDB non exécuté",
    "no testable model": "aucun modèle testable",
    "no testable family": "aucune famille testable",
    "no testable execution": "aucune exécution testable",
    "not applicable: dbt files carry no per-query history": "sans objet : les fichiers dbt ne portent aucun historique par requête",
    "credits and bytes not read (dbt files)": "crédits et octets non lus (fichiers dbt)",
    "bytes not read": "octets non lus",
    "credits not read": "crédits non lus",
    "window not read": "fenêtre non lue",
    "families": "familles", "executions": "exécutions", "bytes": "octets", "credits": "crédits",
    "accepted ≠ identical result": "accepté ≠ résultat identique",
    "translated and accepted by DuckDB (C0) / C0 to C6; C7 counted separately":
        "traduite et acceptée par DuckDB (C0) / C0 à C6 ; C7 compté à part",
    "translated and accepted by DuckDB (C0) / C0 to C6; one family = one model or snapshot":
        "traduite et acceptée par DuckDB (C0) / C0 à C6 ; une famille = un modèle ou un snapshot",
    # editions, SQL modes, dbt schedule (collection values shown as text)
    "on-demand (no reservation)": "à la demande (aucune réservation)",
    "no SQL issued (files read)": "aucun SQL émis (fichiers lus)",
    "read-only": "lecture seule",
    "not read (orchestrator outside the dbt files)": "non lue (orchestrateur hors des fichiers dbt)",
    "Real-time cross-check (INFORMATION_SCHEMA, SHOW): no object missing from ACCOUNT_USAGE.":
        "Recoupement en temps réel (INFORMATION_SCHEMA, SHOW) : aucun objet absent d'ACCOUNT_USAGE.",
}

_N = r"[\d,.]+"


def _rep(fr: str):
    def f(m: re.Match) -> str:
        return fr.format(*(tr(g) if g is not None else "" for g in m.groups()))
    return f


def _keep(fr: str):
    def f(m: re.Match) -> str:
        return fr.format(*(g or "" for g in m.groups()))
    return f


def _fr_count(n: str, sing: str, plur: str) -> str:
    return f"{n} {sing if n in ('0', '1') else plur}"


def _rt_quoi(m: re.Match) -> str:
    parts = []
    for n, what in re.findall(r"(\d+) (tables?|objects?)", m.group(1)):
        parts.append(_fr_count(n, "table", "tables") if what.startswith("table") else _fr_count(n, "objet", "objets"))
    lat = f", dont la latence atteint {m.group(2)} h selon la vue" if m.group(2) else ""
    plural = "s" if sum(int(x) for x in re.findall(r"(\d+)", m.group(1))) > 1 else ""
    return (f"{' et '.join(parts)} vu{plural} seulement en temps réel : "
            f"absent{plural} d'ACCOUNT_USAGE{lat} ; leur historique "
            "(volumes facturés, lectures) n'est pas encore disponible.")


# (pattern, replacement) tried in order on the whole phrase; groups are translated recursively (_rep) or kept (_keep)
PATTERNS = [
    (re.compile(r"^failed \((.*)\)$"), _keep("échec ({0})")),
    (re.compile(r"^not requested \(level (\d+)\)$"), _keep("non demandé (niveau {0})")),
    (re.compile(r"^partial: (\d+)/(\d+) weeks read, stopped on (.*)$"), lambda m: f"partielle : {m.group(1)}/{m.group(2)} semaines lues, arrêt sur {tr(m.group(3))}"),
    (re.compile(r"^partial: (\d+)/(\d+) failed \((.*)\)$"), lambda m: f"partielle : {m.group(1)}/{m.group(2)} en échec ({tr(m.group(3))})"),
    (re.compile(r"^partial: (.*)$"), _rep("partielle : {0}")),
    (re.compile(rf"^estimated volume above the cap \(({_N}) MB > ({_N}) MB\)$"), _keep("volume estimé au-dessus du plafond ({0} Mo > {1} Mo)")),
    (re.compile(r"^(.+) not read: (.+)$"), lambda m: f"{m.group(1)} non lu : {tr(m.group(2))}"),
    (re.compile(r"^(.+): read not attempted$"), _keep("{0} : lecture non tentée")),
    (re.compile(r"^weight read for (\d+)/(\d+) families$"), _keep("poids lu pour {0}/{1} familles")),
    (re.compile(r"^(\d+)/(\d+) (families|executions|bytes|credits), (.+)$"),
     lambda m: f"{m.group(1)}/{m.group(2)} {tr(m.group(3))}, {tr(m.group(4))}"),
    (re.compile(r"^(\d+)/(\d+) models tested, (\d+) not tested, (.+)$"),
     lambda m: f"{m.group(1)}/{m.group(2)} modèles testés, {m.group(3)} non testé{'s' if int(m.group(3)) > 1 else ''}, {tr(m.group(4))}"),
    (re.compile(r"^(.+), (sqlglot \S+, duckdb \S+)$"), lambda m: f"{tr(m.group(1))}, {m.group(2)}"),
    (re.compile(r"^from (\d{4}-\d\d-\d\d) to (\d{4}-\d\d-\d\d)$"), _keep("du {0} au {1}")),
    (re.compile(r"^last invocation: (.*)$"), _keep("dernière invocation : {0}")),
    (re.compile(r"^(function absent from DuckDB|customer UDF|binding rejected by DuckDB|table absent from the metadata|"
                r"not parsable by sqlglot|translation to DuckDB impossible|rejected by the DuckDB parser): (.+)$"),
     lambda m: f"{EXACT[m.group(1)]} : {m.group(2)}"),
    (re.compile(r"^SQL of the manifest models and snapshots$"), lambda m: "SQL des modèles et snapshots du manifeste"),
    (re.compile(r"^(columns not read: DuckDB EXPLAIN not run|bytes not read|credits not read|window not read)$"), lambda m: EXACT[m.group(1)]),
    # Snowflake freshness (scanner/snowflake._fraicheur)
    (re.compile(r"^Real-time cross-check not done: it runs database by database, and the scope is the whole account\. "
                r"Objects created more recently than the ACCOUNT_USAGE latency(?: \(up to ([\d.,]+) h depending on the view\))? may be missing\.(.*)$"),
     lambda m: ("Recoupement en temps réel non fait : il s'exécute base par base, et le périmètre est le compte entier. "
                "Les objets créés plus récemment que la latence d'ACCOUNT_USAGE" + (f" (jusqu'à {m.group(1)} h selon la vue)" if m.group(1) else "")
                + " peuvent manquer." + _tail(m.group(2)))),
    (re.compile(r"^Real-time cross-check not read \((.*?)\): objects created more recently than the ACCOUNT_USAGE latency may be missing\.(.*)$"),
     lambda m: (f"Recoupement en temps réel non lu ({tr(m.group(1))}) : les objets créés plus récemment que la latence d'ACCOUNT_USAGE "
                "peuvent manquer." + _tail(m.group(2)))),
    (re.compile(r"^Real-time cross-check \(INFORMATION_SCHEMA, SHOW\): no object missing from ACCOUNT_USAGE\.(.*)$"),
     lambda m: "Recoupement en temps réel (INFORMATION_SCHEMA, SHOW) : aucun objet absent d'ACCOUNT_USAGE." + _tail(m.group(1))),
    (re.compile(r"^((?:\d+ tables?)?(?: and )?(?:\d+ objects?)?) seen only in real time: missing from ACCOUNT_USAGE"
                r"(?:, whose latency reaches ([\d.,]+) h depending on the view)?(?:; (?:their|its) history \(billed volumes, reads\) is not available yet)?\.(.*)$"),
     lambda m: _rt_quoi(m) + _tail(m.group(3))),
]


def _tail(rest: str | None) -> str:
    rest = (rest or "").strip()
    if not rest:
        return ""
    m = re.match(r"^(\d+) tables? at 0 bytes in TABLE_STORAGE_METRICS but not empty in real time: storage metrics not up to date yet, "
                 r"volume underestimated\.$", rest)
    if m:
        n = m.group(1)
        return (f" {_fr_count(n, 'table', 'tables')} à 0 octet dans TABLE_STORAGE_METRICS mais non vide{'s' if n not in ('0', '1') else ''} "
                "en temps réel : métriques de stockage pas encore à jour, volume sous-estimé.")
    return " " + tr(rest)


_STOP = ("de", "d'", "du", "des", "aux", "à")
_KEEP = ("dbt", "SQL", "ML", "SAS", "ETL", "DAG", "API", "Python")


def plural_fr(label: str) -> str:
    """French plural of a label: each word up to the first preposition or parenthesis (vue matérialisée -> vues matérialisées)."""
    out, stop = [], False
    for w in label.split(" "):
        if stop or w.startswith("(") or w in _KEEP:
            stop = stop or w.startswith("(") or w in _STOP
            out.append(w)
            continue
        if w in _STOP or w.startswith(("d'", "l'")):
            stop = True
            out.append(w)
            continue
        out.append(w if w.endswith(("s", "x", "z")) else w + "s")
    return " ".join(out)


def tr(s):
    """French for a known English phrase from collection, when the current language is French; otherwise s unchanged."""
    if not isinstance(s, str) or _LANG.get() != "fr" or not s:
        return s
    if s in EXACT:
        return EXACT[s]
    for pat, rep in PATTERNS:
        m = pat.match(s)
        if m:
            return rep(m)
    return s


# scope line written by collection (snowflake, bigquery, dbt, fixtures): known fragments, replaced in place; names stay as they are
_PERIMETRE = [
    (re.compile(r"; consumption, storage, queries and privileges: whole account"), " ; consommation, stockage, requêtes et droits : compte entier"),
    (re.compile(r"\bwhole account\b"), "compte entier"),
    (re.compile(r"^dbt project (\S+): adapter (\S+), dbt "), r"projet dbt \1 : adaptateur \2, dbt "),
    (re.compile(r"\bversion not read\b"), "version non lue"),
    (re.compile(r", manifest of (\d{4}-\d\d-\d\d)"), r", manifeste du \1"),
    (re.compile(r"^Project (\S+): (\d+) dataset\(s\)"), r"Projet \1 : \2 dataset(s)"),
    (re.compile(r", region\(s\) "), ", région(s) "),
    (re.compile(r"; hidden datasets flagged: "), " ; datasets cachés signalés : "),
    (re.compile(r"; (\d+) anonymous dataset\(s\) \(cached query results\)"), r" ; \1 dataset(s) anonyme(s) (résultats de requêtes en cache)"),
    (re.compile(r"; jobs only in "), " ; jobs seulement dans "),
    (re.compile(r"; billing on "), " ; facturation sur "),
    (re.compile(r"\bprojects? (\S+) \(data\)"), r"projet \1 (données)"),
    (re.compile(r"\bproject (\S+) \(synthetic\)"), r"projet \1 (synthétique)"),
    (re.compile(r"; regions "), " ; régions "),
    (re.compile(r"\baccount (\S+)"), r"compte \1"),
    (re.compile(r"\bdatabase (\S+)"), r"base \1"),
    (re.compile(r"\(schemas "), "(schémas "),
]


def tr_perimetre(s):
    """The scope line in French (French pane only); object names are kept."""
    if not isinstance(s, str) or _LANG.get() != "fr":
        return s
    for pat, rep in _PERIMETRE:
        s = pat.sub(rep, s)
    return s
