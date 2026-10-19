"""Scan data contract. Everything that comes out of collection is a JSON-serializable dict described here.

Principles (non-negotiable):
- Every read is recorded with its status (`read` | `not_read`) and its reason; never `except: pass`.
- A value that was not read is `None`, never 0.
- Every figure carries its nature: measured | computed | estimated | not_read | not_estimated (see `fig`).
- Raw SQL text never leaves collection: only normalized forms (literals masked) travel.

Dict KEYS stay as they are (data contract); the logic VALUES below are English. VALUE_MAP is the single
old -> new table for the values that changed (French values from earlier versions of Canot), and `legacy` normalizes them.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- logic values (old French value -> English value) -----------------------------------------------------
VALUE_MAP = {
    # read status (lecture["statut"], rejouabilite["statut"]); "partiel" is the internal status of a partly read view
    "statut": {"lu": "read", "non_lu": "not_read", "partiel": "partial"},
    # nature of a figure (fig)
    "nature": {"mesuré": "measured", "calculé": "computed", "estimé": "estimated", "non_lu": "not_read", "non_estimé": "not_estimated"},
    # unit of a figure (fig)
    "unite": {"octets": "bytes", "crédits": "credits", "heures": "hours", "jours": "days", "secondes": "seconds", "requêtes": "queries",
              "appels": "calls", "familles": "families", "exécutions": "executions", "slot-heures": "slot-hours"},
    # confidence of a figure or a flag
    "confiance": {"haute": "high", "moyenne": "medium", "basse": "low"},
    # table movement
    "mouvement": {"figée": "frozen", "vivante": "live", "recréée": "recreated"},
    # flag label
    "etiquette": {"À examiner": "To review"},
    # SQL mode of the scan
    "mode_sql": {"lecture seule": "read-only"},
    # F03 mode
    "mode": {"déclaré": "declared", "observé": "observed"},
    # reasons of a failed read (classify_failure) and prefixes that analyze.py reads
    "raison": {"droit absent": "missing privilege", "édition requise": "edition required", "absent ou non exposé": "absent or not exposed",
               "service désactivé": "service disabled", "délai dépassé": "timed out", "échec (": "failed (", "partielle : ": "partial: ",
               "volume estimé": "estimated volume", "refusée par la garde lecture seule": "refused by the read-only guard"},
}

READ, NOT_READ, PARTIAL = "read", "not_read", "partial"
MEASURED, COMPUTED, ESTIMATED, NOT_ESTIMATED = "measured", "computed", "estimated", "not_estimated"
NATURES = (MEASURED, COMPUTED, ESTIMATED, NOT_READ, NOT_ESTIMATED)
LABEL_TO_REVIEW = "To review"
READ_ONLY = "read-only"


def legacy(kind: str, value):
    """English value for a French value from an older snapshot (VALUE_MAP[kind]); any other value is returned as is."""
    if isinstance(value, str):
        return VALUE_MAP.get(kind, {}).get(value, value)
    return value


# --- Snapshot ---------------------------------------------------------------------------------------------
# snapshot = {
#   "schema": "canot.scan/1"|"canot.scan/2", "plateforme": "snowflake"|"bigquery"|..., "as_of": "2026-10-17T10:00:00Z",
#   "perimetre": "readable text", "edition": str|None, "niveau_droits": 1|2|3, "mode_sql": "read-only",
#   "versions": {"canot": "...", "sqlglot": "...", "duckdb": "..."},
#   "lectures": [Read, ...],
#   "tables": [Table, ...], "objets": [Object, ...], "familles": [Family, ...],
#   "metering": Metering,
#   "rejouabilite": Replayability | None,
# }
#
# Read     = {"id": "S07", "vue": "SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS", "statut": "read"|"not_read",
#             "raison": str|None, "lignes": int|None, "fenetre": {"du": iso, "au": iso}|None, "latence_max_h": number|None}
#
# Table    = {"id": "DB.SCHEMA.NAME", "base": str, "schema": str, "nom": str,
#             "type": "TABLE"|"EXTERNE"|"ICEBERG"|"TRANSITOIRE"|"CLONE"|"SNAPSHOT"|"TEMPORAIRE",
#             "flags": [str], "lignes": int|None, "octets_actifs": int|None, "octets_historique": int|None,  # time travel + fail-safe
#             "clone_de": str|None, "colonnes": [{"nom": str, "type": str}], "retention_jours": number|None,
#             "derniere_ddl": iso|None, "derniere_ecriture": iso|None, "derniere_lecture": iso|None,
#             "n_lectures": int|None, "n_ecritures": int|None,   # None = not read (never 0 by default)
#             "proprietaire": str|None, "politiques": [str], "etiquettes": [str],  # tag values hashed
#             "supprimee_le": iso|None, "externe_uri": str|None,  # reduced to the bucket
#             "partitionnement": str|None, "clustering": [str]}
#
# Object   = {"kind": "vue"|"vue_materialisee"|"fonction"|"procedure"|"tache"|"flux"|"pipe"|"stage"|"base_importee"|
#             "partage"|"table_dynamique"|"modele_ml"|"politique_masquage"|"politique_lignes"|"requete_planifiee"|
#             "connexion"|"integration"|"reservation"|"etiquette",
#             "id": str, "schema": str|None, "langage": str|None, "proprietaire": str|None,
#             "externe": bool, "dependances_externes": [str],   # buckets / reduced URIs
#             "reference": [table ids], "etat": str|None, "planification": str|None,
#             "executions_n": int|None, "echecs_n": int|None, "derniere_execution": iso|None,
#             "sql_dynamique": bool|None, "detail": {str: scalar}}
#
# Family   = {"empreinte": sha256 hex of the normalized form, "hash_plateforme": str|None, "n_exec": int, "temps_ms": number|None,
#             "credits": number|None, "octets": int|None, "app": str|None, "outil": str|None,
#             "utilisateur": str|None (pseudonym), "role": str|None (pseudonym), "entrepot": str|None,
#             "tables": [str], "fonctions": [str], "categorie": "C0".."C7", "sql_normalise": str|None, "ecriture_externe": bool}
#
# Metering = {"par_service": [{"service": str, "credits": number}], "par_entrepot": [{"entrepot": str, "credits": number}],
#             "stockage_octets_moyens": number|None, "transferts": [{"cible": str, "octets": int}],
#             "a_la_demande": {"octets_factures": int, "requetes": int}|None,   # BigQuery
#             "reserve": {"slot_heures": number, "requetes": int}|None,         # BigQuery
#             "fenetre_jours": int}
#
# Replayability = {"categories": {"C0": {"familles": n, "executions": n, "poids": number|None}, ...},
#                  "base": "X/Y families, from … to …, sqlglot …, duckdb …", "mention": "accepted ≠ identical result"}
#   C0 = translated and accepted by DuckDB; C1 = not parseable; C2 = command (not analyzed); C3 = translation impossible;
#   C4 = DuckDB parse error; C5 = binding error (platform-specific object or function); C6 = variable; C7 = DDL/COPY/MERGE (counted separately)
#
# --- canot.scan/2 = canot.scan/1 + optional fields (a /1 snapshot stays valid as /2) ---------------------------
# Root     : + "famille" (key of connectors.FAMILLES), + "capacites" (Connector.capacites()), + "liens" [Link];
#            "plateforme" becomes a free string. "metering" may be None (consumption not readable).
# Table    : + "stockage" = {"connexion": str, "type": "snowflake"|"postgres"|"s3"|"fichiers_plateforme"|...} (optional)
# Object   : open "kind" (KINDS_V2 for non-warehouse platforms); optional fields, present only when collection reads them:
#            "famille_objet" (FAMILLES_OBJET), "entrees"/"sorties" [ids], "portabilite" (PORTABILITES), "formats_export" [str]|None
#            (None = not read), "empreinte_code" (sha256 of the normalized form, never the code), "lignes_code" int, "moteur" str,
#            "extensions" [ids of plugin objects used], "non_triviale" bool (non-trivial presentation measure or calculation, connector criterion).
# Link     = {"de": id, "vers": id, "nature": "lit"|"ecrit"|"declenche"|"depend"}
# Read     : "vue" accepts an API path ("GET /public/api/projects/{p}/recipes/").

SCHEMAS = ("canot.scan/1", "canot.scan/2")
PORTABILITES = ("portable_tel_quel", "traduisible", "exporte_proprietaire", "a_reconstruire", "non_determine")
FAMILLES_OBJET = ("donnees", "traitement", "modele", "rapport", "planification", "connexion", "securite", "code", "extension")
NATURES_LIEN = ("lit", "ecrit", "declenche", "depend")
KINDS_V2 = ("recette_sql", "recette_python", "recette_visuelle", "recette_plugin", "scenario", "modele_entraine", "notebook", "dossier_gere",
            "service_api", "env_code", "plugin", "mesure", "calcul_rapport", "rapport", "flux_etl", "dag", "programme_sas",
            # dbt project (scanner/dbt.py): artifacts read offline
            "modele_dbt", "test_dbt", "source_dbt", "seed_dbt", "snapshot_dbt", "macro_dbt", "paquet_dbt", "exposition_dbt", "metrique_dbt",
            "modele_semantique_dbt", "requete_sauvegardee_dbt", "test_unitaire_dbt", "fonction_dbt")
CHAMPS_OBJET_V2 = ("famille_objet", "entrees", "sorties", "portabilite", "formats_export", "empreinte_code", "lignes_code", "moteur",
                   "extensions", "non_triviale")
# keys that would carry code or raw text: refused at the top level of tables / objects / links
CHAMPS_BRUTS = ("code", "sql", "texte", "text", "source", "corps", "body", "definition", "script")


def valider_snapshot(snap: dict) -> list[str]:
    """Contract errors of a /1 or /2 snapshot (empty list = valid). Fixes nothing, never raises."""
    err = []
    if snap.get("schema") not in SCHEMAS:
        err.append(f"unexpected schema: {snap.get('schema')!r}")
    if not isinstance(snap.get("plateforme"), str) or not snap.get("plateforme"):
        err.append("platform missing")
    for lec in snap.get("lectures") or []:
        if lec.get("statut") not in (READ, NOT_READ):
            err.append(f"read {lec.get('id')}: status {lec.get('statut')!r}")
        elif lec["statut"] == NOT_READ and not lec.get("raison"):
            err.append(f"read {lec.get('id')} not read without a reason")
    for where in ("tables", "objets", "liens"):
        for x in snap.get(where) or []:
            # detail may carry a definition for the analysis (analyze._safe_detail drops it); top-level fields, never
            bruts = [k for k in x if str(k).lower() in CHAMPS_BRUTS]
            if bruts:
                err.append(f"{where} {x.get('id', x.get('de'))}: raw field refused ({', '.join(bruts)})")
    for o in snap.get("objets") or []:
        if "portabilite" in o and o["portabilite"] not in PORTABILITES:
            err.append(f"object {o.get('id')}: portability {o['portabilite']!r}")
        if "famille_objet" in o and o["famille_objet"] not in FAMILLES_OBJET:
            err.append(f"object {o.get('id')}: object family {o['famille_objet']!r}")
        e = o.get("empreinte_code")
        if e is not None and not (isinstance(e, str) and len(e) == 64 and all(c in "0123456789abcdef" for c in e)):
            err.append(f"object {o.get('id')}: empreinte_code is not a sha256")
    for lien in snap.get("liens") or []:
        if lien.get("nature") not in NATURES_LIEN or not lien.get("de") or not lien.get("vers"):
            err.append(f"link {lien!r} incomplete")
    return err


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_salt() -> bytes:
    """Salt drawn for each scan, never written: pseudonyms are only comparable within one report."""
    return secrets.token_bytes(16)


def pseudo(value: str | None, salt: bytes, prefix: str = "id") -> str | None:
    if value is None:
        return None
    return f"{prefix}-{hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:8]}"


def bucket_only(uri: str | None) -> str | None:
    """s3://bucket/path/secret?x=y -> s3://bucket; never a path, a query string or credentials."""
    if not uri:
        return None
    from urllib.parse import urlparse

    u = urlparse(uri)
    if not u.scheme:
        return None
    return f"{u.scheme}://{u.hostname or ''}" + (f":{u.port}" if u.port else "")


def fig(valeur=None, *, nature: str, unite: str | None = None, bas=None, haut=None, base: list[str] | None = None,
        formule: str | None = None, hypotheses: list[str] | None = None, confiance: str | None = None, raison: str | None = None) -> dict:
    """One figure of the report. Rules: `estimated` requires a low–high range; `not_read`/`not_estimated` require valeur=None and a reason."""
    if nature not in NATURES:
        raise ValueError(f"unknown nature: {nature}")
    if nature == ESTIMATED and (bas is None or haut is None):
        raise ValueError("an estimated figure requires a low–high range")
    if nature in (NOT_READ, NOT_ESTIMATED) and (valeur is not None or not raison):
        raise ValueError("a not-read / not-estimated figure has no value and requires a reason")
    return {"valeur": valeur, "bas": bas, "haut": haut, "unite": unite, "nature": nature, "base": base or [],
            "formule": formule, "hypotheses": hypotheses or [], "confiance": confiance, "raison": raison}


@dataclass
class Collector:
    """Records every read: status, reason, volume. `read` never raises: a refused read becomes `not_read`."""

    prefix: str = "L"
    lectures: list[dict] = field(default_factory=list)

    def read(self, vue: str, fn, *, fenetre: dict | None = None, latence_max_h: float | None = None):
        """Runs fn(); returns its result (list of rows) or None if the read failed."""
        lid = f"{self.prefix}{len(self.lectures) + 1:02d}"
        entry = {"id": lid, "vue": vue, "statut": READ, "raison": None, "lignes": None, "fenetre": fenetre, "latence_max_h": latence_max_h}
        self.lectures.append(entry)
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 : the reason is recorded, never swallowed
            entry["statut"] = NOT_READ
            entry["raison"] = classify_failure(e)
            return None
        try:
            entry["lignes"] = len(res)
        except TypeError:
            pass
        return res

    def skip(self, vue: str, raison: str) -> None:
        """Read not attempted (privilege level not requested, insufficient edition...)."""
        self.lectures.append({"id": f"{self.prefix}{len(self.lectures) + 1:02d}", "vue": vue, "statut": NOT_READ, "raison": raison,
                              "lignes": None, "fenetre": None, "latence_max_h": None})


def classify_failure(e: BaseException) -> str:
    """Short reason with no sensitive data: never the full message (it may contain SQL or an identifier)."""
    name = type(e).__name__
    msg = str(e).lower()
    if any(k in msg for k in ("insufficient privileges", "not authorized", "permission", "access denied", "403", "forbidden", "does not have", "accessdenied")):
        return "missing privilege"
    if any(k in msg for k in ("enterprise", "edition", "not supported in standard")):
        return "edition required"
    if any(k in msg for k in ("does not exist", "not found", "404", "unknown", "invalid identifier", "no such")):
        return "absent or not exposed"
    if "has not been used" in msg or "is disabled" in msg or "api has not" in msg or "disabled" in msg:
        return "service disabled"
    if "timeout" in msg or "timed out" in msg:
        return "timed out"
    return f"failed ({name})"
