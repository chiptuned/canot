"""Contrat de données du scan. Tout ce qui sort de la collecte est un dict JSON-sérialisable décrit ici.

Principes (non négociables) :
- Chaque lecture est consignée avec son statut (`lu` | `non_lu`) et sa raison ; jamais de `except: pass`.
- Une valeur non lue vaut `None`, jamais 0.
- Chaque chiffre porte sa nature : mesuré | calculé | estimé | non_lu | non_estimé (voir `fig`).
- Le texte SQL brut ne quitte pas la collecte : seules les formes normalisées (littéraux masqués) circulent.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

NATURES = ("mesuré", "calculé", "estimé", "non_lu", "non_estimé")

# --- Snapshot ---------------------------------------------------------------------------------------------
# snapshot = {
#   "schema": "canot.scan/1"|"canot.scan/2", "plateforme": "snowflake"|"bigquery"|..., "as_of": "2026-10-17T10:00:00Z",
#   "perimetre": "texte lisible", "edition": str|None, "niveau_droits": 1|2|3, "mode_sql": "lecture seule",
#   "versions": {"canot": "...", "sqlglot": "...", "duckdb": "..."},
#   "lectures": [Lecture, ...],
#   "tables": [Table, ...], "objets": [Objet, ...], "familles": [Famille, ...],
#   "metering": Metering,
#   "rejouabilite": Rejouabilite | None,
# }
#
# Lecture  = {"id": "S07", "vue": "SNOWFLAKE.ACCOUNT_USAGE.TABLE_STORAGE_METRICS", "statut": "lu"|"non_lu",
#             "raison": str|None, "lignes": int|None, "fenetre": {"du": iso, "au": iso}|None, "latence_max_h": number|None}
#
# Table    = {"id": "BASE.SCHEMA.NOM", "base": str, "schema": str, "nom": str,
#             "type": "TABLE"|"EXTERNE"|"ICEBERG"|"TRANSITOIRE"|"CLONE"|"SNAPSHOT"|"TEMPORAIRE",
#             "flags": [str], "lignes": int|None, "octets_actifs": int|None, "octets_historique": int|None,  # time travel + fail-safe
#             "clone_de": str|None, "colonnes": [{"nom": str, "type": str}], "retention_jours": number|None,
#             "derniere_ddl": iso|None, "derniere_ecriture": iso|None, "derniere_lecture": iso|None,
#             "n_lectures": int|None, "n_ecritures": int|None,   # None = non lu (jamais 0 par défaut)
#             "proprietaire": str|None, "politiques": [str], "etiquettes": [str],  # valeurs d'étiquettes hachées
#             "supprimee_le": iso|None, "externe_uri": str|None,  # réduit au compartiment
#             "partitionnement": str|None, "clustering": [str]}
#
# Objet    = {"kind": "vue"|"vue_materialisee"|"fonction"|"procedure"|"tache"|"flux"|"pipe"|"stage"|"base_importee"|
#             "partage"|"table_dynamique"|"modele_ml"|"politique_masquage"|"politique_lignes"|"requete_planifiee"|
#             "connexion"|"integration"|"reservation"|"etiquette",
#             "id": str, "schema": str|None, "langage": str|None, "proprietaire": str|None,
#             "externe": bool, "dependances_externes": [str],   # compartiments / URI réduits
#             "reference": [table ids], "etat": str|None, "planification": str|None,
#             "executions_n": int|None, "echecs_n": int|None, "derniere_execution": iso|None,
#             "sql_dynamique": bool|None, "detail": {str: scalar}}
#
# Famille  = {"empreinte": sha256 hex de la forme normalisée, "hash_plateforme": str|None, "n_exec": int, "temps_ms": number|None,
#             "credits": number|None, "octets": int|None, "app": str|None, "outil": str|None,
#             "utilisateur": str|None (pseudonyme), "role": str|None (pseudonyme), "entrepot": str|None,
#             "tables": [str], "fonctions": [str], "categorie": "C0".."C7", "sql_normalise": str|None, "ecriture_externe": bool}
#
# Metering = {"par_service": [{"service": str, "credits": number}], "par_entrepot": [{"entrepot": str, "credits": number}],
#             "stockage_octets_moyens": number|None, "transferts": [{"cible": str, "octets": int}],
#             "a_la_demande": {"octets_factures": int, "requetes": int}|None,   # BigQuery
#             "reserve": {"slot_heures": number, "requetes": int}|None,         # BigQuery
#             "fenetre_jours": int}
#
# Rejouabilite = {"categories": {"C0": {"familles": n, "executions": n, "poids": number|None}, ...},
#                 "base": "X/Y familles, du … au …, sqlglot …, duckdb …", "mention": "accepté ≠ résultat identique"}
#   C0 = traduit et accepté par DuckDB ; C1 = non analysable ; C2 = commande (non analysée) ; C3 = traduction impossible ;
#   C4 = erreur d'analyse DuckDB ; C5 = erreur de liaison (objet/fonction propre) ; C6 = variable ; C7 = DDL/COPY/MERGE (compté à part)
#
# --- canot.scan/2 = canot.scan/1 + champs optionnels (un snapshot /1 reste valide en /2) ----------------------
# Racine   : + "famille" (clé de connectors.FAMILLES), + "capacites" (Connector.capacites()), + "liens" [Lien] ;
#            "plateforme" devient une chaîne libre. "metering" peut valoir None (consommation non lisible).
# Table    : + "stockage" = {"connexion": str, "type": "snowflake"|"postgres"|"s3"|"fichiers_plateforme"|...} (optionnel)
# Objet    : "kind" ouvert (KINDS_V2 pour les plateformes hors entrepôt) ; champs optionnels, présents seulement si la collecte les lit :
#            "famille_objet" (FAMILLES_OBJET), "entrees"/"sorties" [ids], "portabilite" (PORTABILITES), "formats_export" [str]|None
#            (None = non lu), "empreinte_code" (sha256 de la forme normalisée, jamais le code), "lignes_code" int, "moteur" str,
#            "extensions" [ids d'objets plugin utilisés], "non_triviale" bool (mesure ou calcul de présentation, critère du connecteur).
# Lien     = {"de": id, "vers": id, "nature": "lit"|"ecrit"|"declenche"|"depend"}
# Lecture  : "vue" accepte un chemin d'API (« GET /public/api/projects/{p}/recipes/ »).

SCHEMAS = ("canot.scan/1", "canot.scan/2")
PORTABILITES = ("portable_tel_quel", "traduisible", "exporte_proprietaire", "a_reconstruire", "non_determine")
FAMILLES_OBJET = ("donnees", "traitement", "modele", "rapport", "planification", "connexion", "securite", "code", "extension")
NATURES_LIEN = ("lit", "ecrit", "declenche", "depend")
KINDS_V2 = ("recette_sql", "recette_python", "recette_visuelle", "recette_plugin", "scenario", "modele_entraine", "notebook", "dossier_gere",
            "service_api", "env_code", "plugin", "mesure", "calcul_rapport", "rapport", "flux_etl", "dag", "programme_sas",
            # projet dbt (scanner/dbt.py) : artefacts lus hors ligne
            "modele_dbt", "test_dbt", "source_dbt", "seed_dbt", "snapshot_dbt", "macro_dbt", "paquet_dbt", "exposition_dbt", "metrique_dbt",
            "modele_semantique_dbt", "requete_sauvegardee_dbt", "test_unitaire_dbt", "fonction_dbt")
CHAMPS_OBJET_V2 = ("famille_objet", "entrees", "sorties", "portabilite", "formats_export", "empreinte_code", "lignes_code", "moteur",
                   "extensions", "non_triviale")
# clés qui porteraient du code ou du texte brut : refusées au premier niveau des tables / objets / liens
CHAMPS_BRUTS = ("code", "sql", "texte", "text", "source", "corps", "body", "definition", "script")


def valider_snapshot(snap: dict) -> list[str]:
    """Erreurs de contrat d'un snapshot /1 ou /2 (liste vide = valide). Ne corrige rien, ne lève pas."""
    err = []
    if snap.get("schema") not in SCHEMAS:
        err.append(f"schema inattendu : {snap.get('schema')!r}")
    if not isinstance(snap.get("plateforme"), str) or not snap.get("plateforme"):
        err.append("plateforme absente")
    for lec in snap.get("lectures") or []:
        if lec.get("statut") not in ("lu", "non_lu"):
            err.append(f"lecture {lec.get('id')} : statut {lec.get('statut')!r}")
        elif lec["statut"] == "non_lu" and not lec.get("raison"):
            err.append(f"lecture {lec.get('id')} non lue sans raison")
    for where in ("tables", "objets", "liens"):
        for x in snap.get(where) or []:
            # detail peut porter une définition pour l'analyse (analyze._safe_detail la retire) ; les champs de premier niveau, jamais
            bruts = [k for k in x if str(k).lower() in CHAMPS_BRUTS]
            if bruts:
                err.append(f"{where} {x.get('id', x.get('de'))} : champ brut refusé ({', '.join(bruts)})")
    for o in snap.get("objets") or []:
        if "portabilite" in o and o["portabilite"] not in PORTABILITES:
            err.append(f"objet {o.get('id')} : portabilité {o['portabilite']!r}")
        if "famille_objet" in o and o["famille_objet"] not in FAMILLES_OBJET:
            err.append(f"objet {o.get('id')} : famille d'objet {o['famille_objet']!r}")
        e = o.get("empreinte_code")
        if e is not None and not (isinstance(e, str) and len(e) == 64 and all(c in "0123456789abcdef" for c in e)):
            err.append(f"objet {o.get('id')} : empreinte_code n'est pas un sha256")
    for lien in snap.get("liens") or []:
        if lien.get("nature") not in NATURES_LIEN or not lien.get("de") or not lien.get("vers"):
            err.append(f"lien {lien!r} incomplet")
    return err


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_salt() -> bytes:
    """Sel tiré à chaque scan, jamais écrit : les pseudonymes ne sont comparables qu'à l'intérieur d'un rapport."""
    return secrets.token_bytes(16)


def pseudo(value: str | None, salt: bytes, prefix: str = "id") -> str | None:
    if value is None:
        return None
    return f"{prefix}-{hmac.new(salt, value.encode(), hashlib.sha256).hexdigest()[:8]}"


def bucket_only(uri: str | None) -> str | None:
    """s3://bucket/chemin/secret?x=y -> s3://bucket ; jamais de chemin, de requête ni d'identifiants."""
    if not uri:
        return None
    from urllib.parse import urlparse

    u = urlparse(uri)
    if not u.scheme:
        return None
    return f"{u.scheme}://{u.hostname or ''}" + (f":{u.port}" if u.port else "")


def fig(valeur=None, *, nature: str, unite: str | None = None, bas=None, haut=None, base: list[str] | None = None,
        formule: str | None = None, hypotheses: list[str] | None = None, confiance: str | None = None, raison: str | None = None) -> dict:
    """Un chiffre du rapport. Règles : `estimé` impose une fourchette bas–haut ; `non_lu`/`non_estimé` imposent valeur=None et une raison."""
    if nature not in NATURES:
        raise ValueError(f"nature inconnue : {nature}")
    if nature == "estimé" and (bas is None or haut is None):
        raise ValueError("un chiffre estimé exige une fourchette bas–haut")
    if nature in ("non_lu", "non_estimé") and (valeur is not None or not raison):
        raise ValueError("un chiffre non lu / non estimé n'a pas de valeur et exige une raison")
    return {"valeur": valeur, "bas": bas, "haut": haut, "unite": unite, "nature": nature, "base": base or [],
            "formule": formule, "hypotheses": hypotheses or [], "confiance": confiance, "raison": raison}


@dataclass
class Collector:
    """Enregistre chaque lecture : statut, raison, volume. `read` ne lève jamais : une lecture refusée devient `non_lu`."""

    prefix: str = "L"
    lectures: list[dict] = field(default_factory=list)

    def read(self, vue: str, fn, *, fenetre: dict | None = None, latence_max_h: float | None = None):
        """Exécute fn() ; renvoie son résultat (liste de lignes) ou None si la lecture a échoué."""
        lid = f"{self.prefix}{len(self.lectures) + 1:02d}"
        entry = {"id": lid, "vue": vue, "statut": "lu", "raison": None, "lignes": None, "fenetre": fenetre, "latence_max_h": latence_max_h}
        self.lectures.append(entry)
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 : la raison est consignée, jamais avalée
            entry["statut"] = "non_lu"
            entry["raison"] = classify_failure(e)
            return None
        try:
            entry["lignes"] = len(res)
        except TypeError:
            pass
        return res

    def skip(self, vue: str, raison: str) -> None:
        """Lecture non tentée (niveau de droits non demandé, édition insuffisante...)."""
        self.lectures.append({"id": f"{self.prefix}{len(self.lectures) + 1:02d}", "vue": vue, "statut": "non_lu", "raison": raison,
                              "lignes": None, "fenetre": None, "latence_max_h": None})


def classify_failure(e: BaseException) -> str:
    """Raison courte et sans donnée sensible : jamais le message complet (il peut contenir du SQL ou un identifiant)."""
    name = type(e).__name__
    msg = str(e).lower()
    if any(k in msg for k in ("insufficient privileges", "not authorized", "permission", "access denied", "403", "forbidden", "does not have", "accessdenied")):
        return "droit absent"
    if any(k in msg for k in ("enterprise", "edition", "not supported in standard")):
        return "édition requise"
    if any(k in msg for k in ("does not exist", "not found", "404", "unknown", "invalid identifier", "no such")):
        return "absent ou non exposé"
    if "has not been used" in msg or "is disabled" in msg or "api has not" in msg or "disabled" in msg:
        return "service désactivé"
    if "timeout" in msg or "timed out" in msg:
        return "délai dépassé"
    return f"échec ({name})"
