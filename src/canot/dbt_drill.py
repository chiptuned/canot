"""`canot drill dbt://PROJECT DESTINATION`: the exit exercise of a dbt project, in one command.

1. Read the project: dbt_project.yml (profile name), profiles.yml (target, adapter, connection), target/manifest.json.
2. Work out the warehouse and the relations the project uses: every source, seed and snapshot, every model materialized as
   `table` or `incremental`, under the names dbt itself uses (database, schema, alias). Views are not copied: they hold no
   data and are proven through the tables that read them (replayed, compared through their descendants).
3. Copy exactly those relations to Apache Iceberg and compare them: the normal drill (drill.run_drill).
4. Replay the dbt models on the copy and compare each output with the original table (dbt_rejeu).
5. One evidence folder: report.html / report.json (the drill, with a `dbt` section), replay-dbt.json, one signed evidence log.

Read-only against the warehouse, with the credentials and the role the profile names: Canot never widens them.
profiles.yml is read as data (yaml.safe_load). Only `{{ env_var('NAME') }}` and `{{ env_var('NAME', 'default') }}` are
resolved: no Jinja is executed, no code is evaluated. Secrets (password, token, key passphrase) are read from the environment
only, never printed, never written: the evidence log records the adapter, account or project, database, role and schemas,
never a user, a secret or a key path.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from .connectors import NoConnector
from .dbt_rejeu import ancetres
from .evidence import EvidenceLog
from .scanner import dbt as dbt_scan
from .tolerances import Tolerances

ADAPTATEURS = ("snowflake", "bigquery", "databricks", "duckdb")
COPIEES = ("table", "incremental")  # model materializations copied (seeds and snapshots are tables too: copied)
SCHEMA = "canot.drill-dbt/1"
RAISONS_NON_COPIE = {
    "view": "view: holds no data; not copied, proven through the tables that read it (replayed, compared through its descendants)",
    "ephemeral": "ephemeral: no relation in the warehouse (inlined in the models that use it)",
}
R_ABSENTE = "not found as a table in the warehouse (not built yet, an external table, or not visible to the profile's role)"
R_GENERIQUE = ("possible causes, to check in replay-dbt.json: an input read through a row access or masking policy that filters what "
               "the profile's role sees (the copy is then partial for this reader); a table on the platform built from older inputs "
               "(last dbt run before the latest load); a real difference between the two engines.")

_ENV_VAR = re.compile(r"""\{\{-?\s*env_var\(\s*(['"])([A-Za-z_][A-Za-z0-9_]*)\1\s*(?:,\s*(['"])(.*?)\3\s*)?\)\s*"""
                      r"""(?:\|\s*(?:as_text|as_number|as_bool|string|int|trim)\s*)?-?\}\}""", re.S)


class DbtProfileError(ValueError):
    """profiles.yml or dbt_project.yml cannot be used as is: the message says what to change."""


class AdaptateurSansConnecteur(NoConnector):
    """The dbt project runs on a platform Canot has no connector for."""

    def __init__(self, adaptateur: str, profil: str, cible: str):
        self.adaptateur, self.profil, self.cible = adaptateur, profil, cible
        super().__init__(adaptateur.lower())

    def explain(self) -> str:
        return (f"The dbt project runs on the {self.adaptateur} adapter (profile {self.profil}, target {self.cible}): "
                "canot drill dbt:// reads the tables on that platform.\n" + super().explain())


# ---------------------------------------------------------------- profiles.yml


def resolve(value, champ: str, env=None):
    """Value of a profile field: env_var('NAME') and env_var('NAME', 'default') resolved; any other Jinja is refused."""
    env = os.environ if env is None else env
    if not isinstance(value, str):
        return value
    reste = _ENV_VAR.sub("", value)
    if "{{" in reste or "{%" in reste:
        raise DbtProfileError(f"profiles.yml: {champ} holds Jinja that Canot does not evaluate (only env_var('NAME') and "
                              "env_var('NAME', 'default') are resolved). Write the value, or an env_var(), in the profile.")

    def sub(m: re.Match) -> str:
        v = env.get(m.group(2))
        if v is None:
            if m.group(4) is None:
                raise DbtProfileError(f"profiles.yml: {champ} reads the environment variable {m.group(2)}, which is not set and has no default.")
            v = m.group(4)
        return v

    return _ENV_VAR.sub(sub, value)


def _secret(cfg: dict, cle: str, champ: str, env) -> str | None:
    """A secret field: accepted only as an env_var() (read from the environment), never written in clear in the profile."""
    raw = cfg.get(cle)
    if raw is None or raw == "":
        return None
    if not (isinstance(raw, str) and _ENV_VAR.fullmatch(raw.strip())):
        raise DbtProfileError(f"profiles.yml: {champ} is written in clear in the profile. Canot reads secrets only from the environment: "
                              "write it as {{ env_var('VARIABLE') }} and set the variable.")
    return resolve(raw.strip(), champ, env)


def _yaml(p: Path) -> dict:
    import yaml

    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise DbtProfileError(f"{p.name}: unreadable YAML ({type(e).__name__})") from None
    if not isinstance(d, dict):
        raise DbtProfileError(f"{p.name}: unexpected content (a mapping is expected)")
    return d


def find_profiles(project_dir: Path, profiles_dir: str | None = None, env=None) -> tuple[Path, str]:
    """(profiles.yml, where it was found): --profiles-dir, else DBT_PROFILES_DIR, else the project folder, else ~/.dbt."""
    env = os.environ if env is None else env
    if profiles_dir:
        cands = [(Path(profiles_dir).expanduser(), "--profiles-dir")]
    elif env.get("DBT_PROFILES_DIR"):
        cands = [(Path(env["DBT_PROFILES_DIR"]).expanduser(), "DBT_PROFILES_DIR")]
    else:
        cands = [(project_dir, "project folder"), (Path.home() / ".dbt", "~/.dbt")]
    for d, origine in cands:
        p = d if d.is_file() else d / "profiles.yml"
        if p.is_file():
            return p, origine
    raise DbtProfileError("no profiles.yml found (looked in: " + ", ".join(o for _, o in cands) + "). "
                          "Pass --profiles-dir FOLDER, or set DBT_PROFILES_DIR.")


@dataclass
class Profil:
    projet: str
    profil: str
    cible: str
    adaptateur: str
    config: dict
    origine: str  # where profiles.yml was found (no path: never written)


def project_dir(base: str, projet: dbt_scan.ProjetDbt) -> Path:
    rest = base.split("://", 1)[1] if "://" in base else base
    p = Path(rest or ".").expanduser()
    for d in (p, projet.cible.parent, projet.cible):
        if d.is_dir() and (d / "dbt_project.yml").is_file():
            return d.resolve()
    raise DbtProfileError(f"no dbt_project.yml next to {projet.cible.name}/: canot drill dbt:// needs the project folder "
                          "(dbt_project.yml names the profile, profiles.yml the warehouse).")


def load_profile(pdir: Path, profiles_dir: str | None = None, target: str | None = None, env=None) -> Profil:
    env = os.environ if env is None else env
    proj = _yaml(pdir / "dbt_project.yml")
    nom_profil = resolve(proj.get("profile"), "dbt_project.yml profile", env)
    if not nom_profil:
        raise DbtProfileError("dbt_project.yml names no profile (the `profile:` key).")
    chemin, origine = find_profiles(pdir, profiles_dir, env)
    profils = _yaml(chemin)
    prof = profils.get(nom_profil)
    if not isinstance(prof, dict):
        raise DbtProfileError(f"profiles.yml ({origine}) has no profile {nom_profil!r}, the one dbt_project.yml names. "
                              f"Profiles found: {', '.join(k for k in profils if k != 'config') or 'none'}.")
    outputs = prof.get("outputs") or {}
    cible = target or resolve(prof.get("target"), f"profile {nom_profil} target", env)
    if not cible:
        raise DbtProfileError(f"profile {nom_profil}: no default target; pass --target NAME ({', '.join(outputs) or 'no target defined'}).")
    cfg = outputs.get(cible)
    if not isinstance(cfg, dict):
        raise DbtProfileError(f"profile {nom_profil}: no target {cible!r} (targets: {', '.join(outputs) or 'none'}).")
    adaptateur = str(resolve(cfg.get("type"), f"target {cible} type", env) or "").lower()
    if not adaptateur:
        raise DbtProfileError(f"profile {nom_profil}, target {cible}: no `type` (the dbt adapter).")
    return Profil(str(proj.get("name") or pdir.name), str(nom_profil), str(cible), adaptateur, cfg, origine)


# ---------------------------------------------------------------- relations


def relations(man: dict) -> tuple[list[dict], list[dict]]:
    """(relations to copy, relations not copied with the reason), from the manifest, under dbt's own names."""
    copier: list[dict] = []
    ecartees: list[dict] = []
    vues: set[tuple] = set()

    def ajouter(uid: str, nature: str, n: dict, nom: str | None, mat: str | None, note: str | None = None):
        if not nom or not n.get("schema"):
            ecartees.append({"uid": uid, "nature": nature, "materialisation": mat, "relation": dbt_scan._relation(n),
                             "raison": "no relation name in the manifest"})
            return
        r = {"uid": uid, "nature": nature, "materialisation": mat, "database": n.get("database") or None, "schema": n["schema"],
             "name": nom, "fqn": f"{n['schema']}.{nom}", "relation": ".".join(x for x in (n.get("database"), n["schema"], nom) if x)}
        if note:
            r["note"] = note
        cle = tuple(str(x or "").upper() for x in (r["database"], r["schema"], nom))
        if cle not in vues:
            vues.add(cle)
            copier.append(r)

    for uid, n in sorted((man.get("sources") or {}).items()):
        ajouter(uid, "source", n, n.get("identifier") or n.get("name"), None)
    for uid, n in sorted((man.get("nodes") or {}).items()):
        rt = n.get("resource_type")
        mat = (n.get("config") or {}).get("materialized")
        if rt in ("seed", "snapshot"):
            ajouter(uid, rt, n, n.get("alias") or n.get("name"), mat or rt)
        elif rt == "model":
            mat = mat or "view"
            langage = (n.get("language") or "sql").lower()
            if mat in COPIEES:
                ajouter(uid, "model", n, n.get("alias") or n.get("name"), mat,
                        f"{langage} model: its table is copied and compared, the model itself is not replayed" if langage != "sql" else None)
            else:
                ecartees.append({"uid": uid, "nature": "model", "materialisation": mat, "relation": dbt_scan._relation(n),
                                 "raison": RAISONS_NON_COPIE.get(mat) or (f"materialization {mat}: maintained by the platform, not a plain "
                                                                          "table; not copied (to rebuild on the destination), the tables it reads are")})
    return copier, ecartees


# ---------------------------------------------------------------- connection


@dataclass
class Connexion:
    adaptateur: str
    url: str  # may name the user: never written, never printed
    database: str | None  # what the source reads: relations in another database are not copied
    env: dict[str, str] = field(default_factory=dict)  # set only while the source opens; secret values included: never written
    description: dict = field(default_factory=dict)  # what can be written: no user, no secret, no key path
    lecteur: str = ""  # who reads, for the read-only message
    fuseau_session: str | None = None  # DuckDB: settings.TimeZone of the profile, the zone dbt computes in (applied to the source)


def database_du_profil(p: Profil, pdir: Path, env=None) -> str | None:
    c = p.config
    r = lambda k: resolve(c.get(k), f"target {p.cible} {k}", env)  # noqa: E731
    if p.adaptateur == "snowflake":
        return r("database")
    if p.adaptateur == "bigquery":
        return r("project") or r("database")
    if p.adaptateur == "databricks":
        return r("catalog") or r("database")
    if p.adaptateur == "duckdb":
        return r("database") or Path(str(r("path") or "")).stem or None
    return None


def connexion(p: Profil, schemas: list[str], pdir: Path, env=None) -> Connexion:
    """How to open the warehouse the profile names, with the profile's own credentials. `schemas`: those of the relations."""
    env = os.environ if env is None else env
    c = p.config
    r = lambda k: resolve(c.get(k), f"target {p.cible} {k}", env)  # noqa: E731
    a = p.adaptateur
    var: dict[str, str] = {}

    def poser(nom: str, valeur: str | None):  # an environment variable for the source, only if the user has not set it already
        if valeur and not env.get(nom):
            var[nom] = valeur

    db = database_du_profil(p, pdir, env)
    if a == "snowflake":
        auth = str(r("authenticator") or "snowflake").lower()
        if auth not in ("snowflake", "snowflake_jwt"):
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: authenticator {auth} is not supported by the Snowflake "
                                  "connector yet: use a key pair (private_key_path) or a password read from the environment.")
        if c.get("private_key"):
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: an inline private_key is not supported: "
                                  "use private_key_path (or the SNOWFLAKE_PRIVATE_KEY_PATH variable).")
        account, user = r("account"), r("user")
        if not account or not user or not db:
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: account, user and database are required.")
        kp = r("private_key_path")
        poser("SNOWFLAKE_PRIVATE_KEY_PATH", str(Path(kp).expanduser()) if kp else None)
        poser("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", _secret(c, "private_key_passphrase", f"target {p.cible} private_key_passphrase", env))
        poser("SNOWFLAKE_PASSWORD", _secret(c, "password", f"target {p.cible} password", env))
        role, wh = r("role"), r("warehouse")
        qs = {"user": user, "role": role or "", "warehouse": wh or "", "schemas": ",".join(sorted({s.upper() for s in schemas}))}
        url = f"snowflake://{account}/{db}?" + urlencode(qs)
        desc = {"adapter": a, "account": account, "database": db, "role": role or "user's default role",
                "warehouse": wh or "user's default warehouse", "schemas": sorted({s.upper() for s in schemas})}
        return Connexion(a, url, db, var, desc, f"role {role}" if role else "the user's default role")
    if a == "bigquery":
        methode = str(r("method") or "oauth").lower()
        if methode == "service-account":
            kf = r("keyfile")
            poser("GOOGLE_APPLICATION_CREDENTIALS", str(Path(kf).expanduser()) if kf else None)
        elif methode != "oauth":
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: method {methode} is not supported yet: use oauth "
                                  "(gcloud auth application-default login) or service-account with a keyfile path.")
        if not db:
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: no project.")
        facturation = r("execution_project") or db
        loc = r("location")
        ds = sorted(set(schemas))
        chemin = ",".join(ds) if facturation == db else ",".join(f"{db}.{d}" for d in ds)
        url = f"bigquery://{facturation}/{chemin}" + (f"?{urlencode({'location': loc})}" if loc else "")
        desc = {"adapter": a, "project": db, "billing_project": facturation, "location": loc, "method": methode, "datasets": ds}
        return Connexion(a, url, db, var, desc, "the Google credentials of the profile" if methode == "service-account"
                         else "your Google application default credentials")
    if a == "databricks":
        host, http_path = r("host"), r("http_path")
        if not host or not http_path:
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: host and http_path are required.")
        poser("DATABRICKS_TOKEN", _secret(c, "token", f"target {p.cible} token", env))
        host = str(host).replace("https://", "").rstrip("/")
        url = f"databricks://{host}/{str(http_path).lstrip('/')}" + (f"?{urlencode({'catalog': db})}" if db else "")
        return Connexion(a, url, db, var, {"adapter": a, "host": host, "catalog": db, "schemas": sorted(set(schemas))},
                         "the profile's token (DATABRICKS_TOKEN)")
    if a == "duckdb":
        path = r("path")
        if not path or str(path).startswith(":memory:") or str(path).startswith("md:"):
            raise DbtProfileError(f"profile {p.profil}, target {p.cible}: the DuckDB path is {path or 'empty'}: nothing to read "
                                  "(an in-memory or MotherDuck database is not supported).")
        f = Path(str(path)).expanduser()
        f = f if f.is_absolute() else (pdir / f)
        reglages = c.get("settings") if isinstance(c.get("settings"), dict) else {}
        tzs = resolve(reglages.get("TimeZone") or reglages.get("timezone"), f"target {p.cible} settings.TimeZone", env)
        return Connexion(a, str(f.resolve()), db, var, {"adapter": a, "file": f.name, "database": db, "timezone": tzs},
                         "the local DuckDB file, read-only", tzs)
    raise AdaptateurSansConnecteur(a, p.profil, p.cible)


@contextmanager
def _environnement(var: dict[str, str]):
    avant = {k: os.environ.get(k) for k in var}
    os.environ.update(var)
    try:
        yield
    finally:
        for k, v in avant.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def politiques(src, tables: list) -> dict:
    """Row access and masking policies on the copied tables (Snowflake), read with the profile's role. Not readable: said so."""
    if getattr(src, "dialect", None) != "snowflake" or not hasattr(src, "run_readonly"):
        return {"lu": False, "raison": "not read: policies are read on Snowflake only", "tables": {}}
    from .model import q

    db = src.database
    out: dict[str, list[dict]] = {}
    for t in tables:
        lit = ".".join(q(x, "snowflake") for x in (db, t.schema, t.name)).replace("'", "''")
        try:
            rows = src.run_readonly(f"select policy_kind, policy_name, ref_column_name from table({q(db, 'snowflake')}.information_schema."
                                    f"policy_references(ref_entity_name => '{lit}', ref_entity_domain => 'table'))")
        except Exception as e:  # noqa: BLE001 : a role without the grant: the report says the policies were not read
            return {"lu": False, "raison": f"not readable with the profile's role ({type(e).__name__})", "tables": out}
        if rows:
            out[t.fqn] = [{"kind": str(x.get("POLICY_KIND") or ""), "policy": str(x.get("POLICY_NAME") or ""),
                           "column": x.get("REF_COLUMN_NAME")} for x in rows]
    return {"lu": True, "raison": None, "tables": out}


def _libelle_politique(items: list[dict]) -> str:
    def un(x):
        k = x["kind"].lower().replace("_", " ")
        return f"{k} {x['policy']}" + (f" on {x['column']}" if x.get("column") else "")
    return "; ".join(un(x) for x in items)


def explications(man: dict, rapport: dict, copiees: list[dict], pol: dict, conversions: list[dict] | None = None,
                 lecteur: str = "the profile's role") -> list[dict]:
    """For each model reported different: the copied inputs it reads, then the causes this run measured, measured ones first.
    A source time zone the replay applied is not a cause (rapport["timezone"]); several source zones are (not determined). A row
    access policy on an input is a cause only when the row counts differ; otherwise it is reported in the policies, not here.
    Lossy conversions other than a time zone come next; else the possible causes. Nothing is asserted that this run did not read.
    Each cause carries "p", the values its text is built from, so the HTML report can word it in French as well (report.py)."""
    par_uid = {r["uid"]: r["fqn"] for r in copiees}
    tables_pol = {k.upper(): v for k, v in (pol.get("tables") or {}).items()}
    tz = rapport.get("timezone") or {}
    out = []
    for m in rapport.get("modeles") or []:
        if m.get("statut") != "different":
            continue
        entrees = sorted({par_uid[u] for u in ancetres(man, m["modele"]) if u in par_uid})
        hautes = {e.upper() for e in entrees}
        conv = [c for c in conversions or [] if str(c.get("table", "")).upper() in hautes and c.get("lossy")]
        sous_pol = {e: tables_pol[e.upper()] for e in entrees if e.upper() in tables_pol}
        v = m.get("verification") or {}
        diffs = " ".join(v.get("stats_diffs") or []) + " " + str(m.get("raison") or "")
        dates = "datetime." in diffs
        causes = []
        autres = []  # lossy conversions other than a time zone (precision): stated only when nothing else explains the difference
        for c in conv:
            ref = f"{c['table']}.{c['column']}"
            zone = c.get("tz") or (re.search(r"time zone (\S+) -> UTC", str(c.get("note"))) or [None, None])[1]
            if zone and zone == tz.get("applied"):
                if dates:  # zone applied, dates still differ: the stored offsets do not follow the zone's rules
                    causes.append({"nature": "decalages", "mesure": False, "p": {"ref": ref, "zone": zone},
                                   "court": f"{ref}: zone {zone} applied, dates still differ: the stored offsets do not follow that zone "
                                            "(a TIMESTAMP_TZ keeps each value's offset), not covered by the rule",
                                   "texte": f"{ref} was copied as UTC and the replay ran in its source zone {zone}, yet the date values still "
                                            "differ: the offsets stored with the values do not follow that zone's rules (a TIMESTAMP_TZ keeps the "
                                            "offset of each value: a fixed offset across daylight saving time, or offsets that vary by row). "
                                            "The copy keeps the instant only; the replay rule cannot recover per-value offsets. If they matter, "
                                            "copy them as a separate column before leaving (docs/scan.md)."})
                continue  # applied to the replay: dates are derived in the same zone on both sides
            if zone:
                causes.append({"nature": "fuseau", "mesure": True,
                               "p": {"ref": ref, "zone": zone, "applied": tz.get("applied"), "dates": dates, "finding": tz.get("finding")},
                               "court": f"{ref} copied as UTC (zone {zone}), replayed in {tz.get('applied') or 'another zone'}: dates derived "
                                        "from it can shift" + (" (measured on the date values here)" if dates else ""),
                               "texte": (f"{tz['finding']}. " if tz.get("finding") else "")
                               + f"{ref} is copied as an instant in UTC (source zone {zone}); the replay ran in {tz.get('applied') or 'another zone'}: "
                               "a date or an hour derived from it (cast to date, date_trunc, extract) can differ from the platform's"
                               + (": the differences on date values here are this shift" if dates else "") + " (docs/discrepancies.md)."})
            else:
                autres.append({"nature": "conversion", "mesure": True, "p": {"ref": ref, "note": c["note"]}, "court": f"{ref} copied with a possible loss ({c['note']})",
                               "texte": f"{ref} is copied with a possible loss ({c['note']}): a model computed from it can differ."})
        rap = {e: [x for x in items if "ROW_ACCESS" in str(x.get("kind", "")).upper()] for e, items in sous_pol.items()}
        rap = {e: x for e, x in rap.items() if x}
        lignes_diff = v.get("rows_source") is not None and v.get("rows_target") is not None and v["rows_source"] != v["rows_target"]
        decale = any(c["nature"] in ("fuseau", "decalages") for c in causes)  # a date shift also changes the rows of a grouping
        if rap and lignes_diff and not decale:
            causes.append({"nature": "politique", "mesure": True,
                           "p": {"policies": rap, "rows_source": v["rows_source"], "rows_target": v["rows_target"], "reader": lecteur},
                           "court": "reads " + ", ".join(f"{e} under {_libelle_politique(x)}" for e, x in rap.items())
                                    + f": the copy is what {lecteur} can read; row counts differ ({v['rows_source']} / {v['rows_target']})",
                           "texte":
                           "reads " + ", ".join(f"{e} ({_libelle_politique(x)})" for e, x in rap.items())
                           + f", and the replayed output has {v['rows_target']} rows where the platform table has {v['rows_source']}: the copy "
                           f"holds what {lecteur} can read, while dbt built the table with a role that saw more rows; the replay on the copy "
                           "cannot reproduce it, and that is the expected result for this reader, not a translation error (finding F10 of "
                           "canot scan: a complete copy needs a principal these policies do not filter)."})
        if not causes:
            causes = autres
        if not causes:
            non_lues = not pol.get("lu") and str(pol.get("raison") or "").startswith("not readable")
            causes.append({"nature": "inconnue", "mesure": False, "p": {"policies_not_read": pol.get("raison") if non_lues else None}, "court": "cause not identified by this run: see replay-dbt.json",
                           "texte": (f"row access and masking policies {pol['raison']}; " if non_lues else "") + R_GENERIQUE})
        out.append({"modele": m["modele"], "raison": m.get("raison"), "entrees_copiees": entrees, "politiques": sous_pol,
                    "causes": causes, "explication": " Also: ".join(c["texte"] for c in causes)})
    return out


# ---------------------------------------------------------------- the exercise


def run_dbt_drill(url: str, dest: str, out: Path, key_path: Path, *, profiles_dir: str | None = None, target: str | None = None,
                  bucket: str | None = None, catalog: str | None = None, queries: str | None = None, top: int = 20,
                  only: list[str] | None = None, tol: Tolerances | None = None, credit_price: float | None = None,
                  replay_max_gb: float = 2.0, log=print, env=None, open_source=None, lang: str | None = None) -> dict:
    from . import sources
    from .dbt_rejeu import STATUTS, run_rejeu, write_rapport
    from .drill import run_drill
    from .report import write_report
    from .target import parse_target

    env = os.environ if env is None else env
    tol = tol or Tolerances()
    base, _, query = url.partition("?")
    qs = {k: v[0] for k, v in parse_qs(query).items()}
    projet = dbt_scan.ProjetDbt(base)
    pdir = project_dir(base, projet)
    prof = load_profile(pdir, profiles_dir, target or qs.get("target"), env)
    if prof.adaptateur not in ADAPTATEURS:
        raise AdaptateurSansConnecteur(prof.adaptateur, prof.profil, prof.cible)
    man = json.loads((projet.cible / "manifest.json").read_text(encoding="utf-8"))
    adapt_man = str((man.get("metadata") or {}).get("adapter_type") or "")
    copier, ecartees = relations(man)
    db = database_du_profil(prof, pdir, env)
    dans_db = [r for r in copier if not (db and r["database"] and r["database"].lower() != db.lower())]
    for r in copier:
        if r not in dans_db:
            ecartees.append({**r, "raison": f"in database {r['database']}; the profile connects to {db}: run canot drill on that database"})
    if not dans_db:
        raise ValueError(f"URL dbt: none of the {len(copier)} relation(s) of the manifest is in the database the profile connects to ({db}).")
    cx = connexion(prof, [r["schema"] for r in dans_db], pdir, env)
    cible = parse_target(dest, bucket, out, catalog)  # reject an incomplete destination before connecting

    log(f"dbt project {prof.projet}: profile {prof.profil}, target {prof.cible}, adapter {prof.adaptateur} (profiles.yml: {prof.origine})")
    if adapt_man and adapt_man != prof.adaptateur:
        log(f"   note: the manifest was compiled for the {adapt_man} adapter, the target is {prof.adaptateur}: "
            "run dbt parse or dbt compile with this target so the compiled SQL matches it")
    log(f"Read-only: Canot reads the warehouse with your credentials and {cx.lecteur}; nothing is written to it, no right is added.")
    with _environnement(cx.env):
        src = (open_source or sources.open_source)(cx.url)
    if cx.fuseau_session and hasattr(src, "con"):  # the session zone dbt-duckdb sets: timestamps with time zone are read in it
        src.con.execute(f"SET GLOBAL TimeZone = '{str(cx.fuseau_session).replace(chr(39), '')}'")
    ev = EvidenceLog(out)
    try:
        tables = src.tables()
        presentes = {t.fqn.lower() for t in tables}
        retenues = [r for r in dans_db if r["fqn"].lower() in presentes]
        if only:
            garder = {o.lower() for o in only}
            for r in retenues:
                if r["fqn"].lower() not in garder:
                    ecartees.append({**r, "raison": "left out by --tables"})
            retenues = [r for r in retenues if r["fqn"].lower() in garder]
        absentes = [r for r in dans_db if r["fqn"].lower() not in presentes]
        ecartees += [{**r, "raison": R_ABSENTE} for r in absentes]
        if not retenues:
            raise ValueError(f"URL dbt: none of the {len(dans_db)} relation(s) to copy was found as a table in the warehouse "
                             f"with {cx.lecteur} (the dbt models may not be built yet: dbt run creates them).")
        log(f"Relations to copy: {len(retenues)} (sources, seeds, snapshots, table and incremental models); not copied: {len(ecartees)}")
        for r in ecartees:
            log(f"   not copied: {r.get('relation') or r['uid']}: {r['raison']}")
        ev.add("dbt_project", {"projet": prof.projet, "profil": prof.profil, "cible": prof.cible, "adaptateur": prof.adaptateur,
                               "adaptateur_manifest": adapt_man or None, "profiles_yml": prof.origine, "connexion": cx.description,
                               "lecture": f"read-only, with {cx.lecteur}",
                               "a_copier": [{k: r[k] for k in ("uid", "nature", "materialisation", "relation")} for r in retenues],
                               "non_copiees": [{k: r.get(k) for k in ("uid", "nature", "materialisation", "relation", "raison")} for r in ecartees]})
        res = run_drill(src, cible, out, key_path, queries, tol, top, [r["fqn"] for r in retenues], credit_price, replay_max_gb, log,
                        tables=tables, ev=ev)
        pol = politiques(src, [t for t in tables if t.fqn.lower() in {r["fqn"].lower() for r in retenues}])
    finally:
        src.close()
    ev.add("dbt_policies", pol)
    if pol["tables"]:
        for t, items in pol["tables"].items():
            log(f"   policy on {t}: {_libelle_politique(items)}: the copy holds what {cx.lecteur} can read")
    elif not pol["lu"] and prof.adaptateur == "snowflake":
        log(f"   row access and masking policies: {pol['raison']}")

    log("→ replaying the dbt models on the copy (P3)")
    rapport = run_rejeu(base, out, key_path, lac=cible.label, cat=cible.catalog(), tol=tol, log=log, ev=ev, conversions=res["conversions"])
    expl = explications(man, rapport, retenues, pol, res["conversions"], cx.lecteur)
    s, rs = res["summary"], rapport["summary"]
    verdict = ("mismatch" if s["parity"] != "complete" or rs["different"] else
               "partial" if rs["non_rejouable"] or absentes or rs.get("fuseau_non_determine") else "complete")
    dbt = {"schema": SCHEMA, "projet": prof.projet, "profil": prof.profil, "cible": prof.cible, "adaptateur": prof.adaptateur,
           "connexion": cx.description, "lecture": f"read-only, with {cx.lecteur}",
           "copiees": [{k: r.get(k) for k in ("uid", "nature", "materialisation", "relation", "note")} for r in retenues],
           "non_copiees": [{k: r.get(k) for k in ("uid", "nature", "materialisation", "relation", "raison")} for r in ecartees],
           "politiques": pol, "rejeu": {k: rs[k] for k in ("modeles", *STATUTS, "proof_level", "models_by_proof_level")}, "fuseau": rapport.get("timezone"), "preuve_p3": rs["preuve"],
           "parite_copie": s["parity"], "explications": expl, "verdict": verdict}
    ev.add("dbt_verdict", {k: v for k, v in dbt.items() if k not in ("copiees", "non_copiees", "politiques", "connexion")})
    seal = ev.seal(key_path, log)
    res["seal"] = rapport["seal"] = seal
    res["dbt"] = dbt
    rep = write_report(res, out, lang)
    write_rapport(rapport, out)
    return {"drill": res, "replay": rapport, "dbt": dbt, "report": rep}


def resume(r: dict, out: Path) -> list[str]:
    """The short combined summary printed by the CLI."""
    s, d = r["drill"]["summary"], r["dbt"]
    rj, tz = d["rejeu"], d.get("fuseau") or {}
    lines = [f"\ndbt exit exercise ({d['projet']}, target {d['cible']}): {d['verdict']}",
             f"  Copy (P1): parity {s['parity']}, {s['tables_ok']}/{s['tables_total']} tables match",
             f"  Queries (P2): {s['queries_match']}/{s['queries_total']} same result" if s["queries_total"] else
             "  Queries (P2): none replayed (no --queries and none found in the history)",
             f"  Models (P3): {rj['identique']} match, {rj['different']} different, {rj['non_compare']} replayed but not compared (no table to compare: views), "
             f"{rj['non_rejouable']} not replayable, out of {rj['modeles']}",
             f"  Proof level: {s.get('proof_level')} for the copy, {rj.get('proof_level')} for the models (the lowest over each)",
             f"  Replay time zone: {tz.get('applied')} ({tz.get('rule')}" + (f": {', '.join(tz['origin'][:3])}" if tz.get("origin") else "") + ")"]
    if tz.get("finding"):
        lines.append(f"  Finding: {tz['finding']}")
    nc = {}
    for x in d["non_copiees"]:
        nc[x.get("materialisation") or x["nature"]] = nc.get(x.get("materialisation") or x["nature"], 0) + 1
    if nc:
        lines.append("  Not copied: " + ", ".join(f"{k} ×{n}" for k, n in sorted(nc.items())) + " (reasons in report.json, section dbt)")
    for e in d["explications"]:
        lines.append(f"  Why {e['modele'].split('.')[-1]} differs: " + "; ".join(c["court"] for c in e["causes"]))
    natures = {c["nature"] for e in d["explications"] for c in e["causes"]}
    if natures & {"fuseau", "decalages"}:
        lines.append("  Time zone: Iceberg keeps instants in UTC; the platform derived dates in the zone stored with each value, the replay "
                     "in another zone. After the exit, the pipeline must set that zone explicitly (docs/discrepancies.md).")
    if "politique" in natures:
        lines.append(f"  Policies: the copy holds what {d['lecture'].split('with ', 1)[-1]} can read; a table built on the platform by a role "
                     "that saw more cannot be reproduced from it, and that is expected for this reader (finding F10 of canot scan).")
    lines += [f"Evidence: {out / 'report.html'}, {out / 'replay-dbt.json'}",
              f"Verify: canot verify-evidence {out}"]
    return lines
