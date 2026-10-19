"""Registre des connecteurs : ce que chaque plateforme sait faire (inventaire, export, preuve), déclaré et non deviné.

Le CLI, la collecte du scan et `canot connecteurs` lisent ces déclarations. Aucun pilote n'est importé ici :
l'ouverture passe par `sources.open_source`, la collecte par `scanner.<plateforme>`, à l'appel seulement.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import ClassVar

from . import prices

PREUVES = {"P1": "parité des données", "P2": "rejeu de requêtes", "P3": "rejeu de traitements",
           "P4": "parité de scoring", "P5": "parité de mesures"}
TESTE, NON_TESTE = "testé", "écrit, non testé"
# familles de plateformes (mémo généraliste §1) ; « fichiers » = artefacts lus hors ligne (dbt, .sas…)
FAMILLES = {"entrepot": "entrepôt", "lakehouse": "lakehouse", "science_donnees": "science des données", "integration": "intégration / ETL",
            "bi_semantique": "BI sémantique", "orchestrateur": "orchestrateur", "sas": "SAS", "fichiers": "fichiers"}


@dataclass(frozen=True)
class Capabilities:
    inventaire: str                      # "complet" (scan complet), "léger" (scan.py), "aucun"
    niveaux: tuple[int, ...] = ()        # --level acceptés par l'inventaire complet
    export: tuple[str, ...] = ()         # formats écrits par drill
    preuve: tuple[str, ...] = ()         # clés de PREUVES
    # date réelle d'un test consigné dans le dépôt (docs/), avec ce qui a été testé ; None si le dépôt n'en garde aucune trace
    teste_sur: str | None = None


class Connector(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    schemes: ClassVar[tuple[str, ...]]
    url: ClassVar[str]                   # forme d'URL, pour l'aide
    dialect: ClassVar[str]
    extra: ClassVar[str | None] = None   # pip install 'canot[extra]'
    driver: ClassVar[str | None] = None  # préfixe du module du pilote (message « connecteur absent »)
    status: ClassVar[str] = NON_TESTE
    famille: ClassVar[str] = "entrepot"  # clé de FAMILLES
    capabilities: ClassVar[Capabilities]
    # fiche d'analyse : constantes propres à la plateforme, lues par scanner/analyze.py et scanner/render.py
    # (droits demandés par niveau, droits jamais demandés…)
    profile: ClassVar[dict] = {}
    # garde HTTP (scanner/http_guard.py) : POST admis seulement sur ces chemins de lecture (motifs regex du chemin d'URL)
    http_post_lecture: ClassVar[tuple[str, ...]] = ()
    # rejouabilité des familles de l'historique (scanner/replay.py), calculée après la collecte
    rejeu_familles: ClassVar[bool] = True

    def capacites(self) -> dict:
        """Déclaration sérialisable (scan.json, MCP) : ce que Canot sait faire ici, et si c'est testé."""
        cap = self.capabilities
        return {"plateforme": self.name, "libelle": self.label, "famille": self.famille, "inventaire": cap.inventaire,
                "niveaux": list(cap.niveaux), "export": list(cap.export),
                "preuve": [{"forme": p, "libelle": PREUVES[p]} for p in cap.preuve],
                "statut": self.status, "teste_sur": cap.teste_sur}

    def open(self, url: str):
        from . import sources

        return sources.open_source(url)

    def scan_collect(self, src, *, level: int, salt: bytes, days: int | None = None) -> tuple[dict, list[dict]]:
        """Inventaire complet : (snapshot canot.scan/1 ou /2, familles brutes). Le texte SQL brut ne vit que dans le second retour."""
        raise ValueError(f"scan complet non disponible pour {self.dialect}")


class Snowflake(Connector):
    name, label, schemes, dialect = "snowflake", "Snowflake", ("snowflake",), "snowflake"
    url = "snowflake://COMPTE/BASE"
    extra = driver = "snowflake"
    status = TESTE
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 17/10/2026 : compte d'essai Snowflake, TPC-H SF10 (docs/banc-essai.md)")
    profile = {
        "droits": {1: ["SNOWFLAKE.OBJECT_VIEWER", "SNOWFLAKE.USAGE_VIEWER"], 2: ["SNOWFLAKE.GOVERNANCE_VIEWER"], 3: ["SNOWFLAKE.SECURITY_VIEWER"]},
        "droits_jamais": "IMPORTED PRIVILEGES, MANAGE GRANTS, IMPORT SHARE, USAGE sur les fonctions, READ sur les stages",
        "horizon_jours": 365,
        "imports_systeme": ("SNOWFLAKE",),  # base système présente dans tout compte : pas une dépendance du client
        # sources des lectures par table, de la plus sûre à la moins sûre : (vue, libellé, confiance, champs de t["lectures"])
        "sources_lectures": (("ACCESS_HISTORY", "ACCESS_HISTORY", "haute", ("n",)),
                             ("TABLE_QUERY_PRUNING_HISTORY", "TABLE_QUERY_PRUNING_HISTORY", "moyenne", ("n_pruning", "n"))),
        "non_vus": {"ACCESS_HISTORY": ["consommateurs de partages", "RESULT_SCAN"],
                    "TABLE_QUERY_PRUNING_HISTORY": ["consommateurs de partages", "résultats servis par le cache",
                                                    "requêtes résolues par les métadonnées"]},
        "source_prix": prices.SNOWFLAKE_SOURCE,
    }

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import snowflake

        return snowflake.collect(src.run_readonly, base=src.database, schemas=src.schemas or None, level=level, salt=salt,
                                 **({"days": days} if days else {}))


class BigQuery(Connector):
    name, label, schemes, dialect = "bigquery", "BigQuery", ("bigquery",), "bigquery"
    url = "bigquery://PROJET/DATASET"
    extra, driver = "bigquery", "google.cloud"
    status = TESTE
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 13/10/2026 : jeu public thelook_ecommerce (docs/ecarts.md)")
    profile = {
        "droits": {1: ["bigquery.jobs.create", "jobs.listAll", "datasets.get", "tables.list", "tables.get", "routines.list", "routines.get",
                       "models.list", "models.getMetadata"],
                   2: ["rowAccessPolicies.list", "transfers.get", "connections.list", "dataPolicies.list (optionnelles)"],
                   3: ["reservations.list", "reservationAssignments.list", "capacityCommitments.list", "droits d'organisation"]},
        "droits_jamais": "tables.getData, rôles bigquery.user et jobUser",
        "horizon_jours": 180,
        "sources_lectures": (("JOBS", "INFORMATION_SCHEMA.JOBS", "moyenne", ("n",)),),
        "non_vus": {"JOBS": ["lectures par l'API Storage Read", "requêtes lancées depuis d'autres projets", "consommateurs Analytics Hub"]},
        # un « non vu » qui disparaît quand une autre lecture l'a couvert
        "non_vus_couverts": {"requêtes lancées depuis d'autres projets": "JOBS_BY_ORGANIZATION"},
        "source_prix": prices.BIGQUERY_SOURCE,
    }

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import bigquery

        return bigquery.collect(src, project=src.project, data_project=src.data_project, datasets=src.datasets or None, level=level,
                                salt=salt, **({"days": days} if days else {}))


class Databricks(Connector):
    name, label, schemes, dialect = "databricks", "Databricks", ("databricks",), "databricks"
    url = "databricks://HÔTE/CHEMIN_HTTP?catalog=…"
    extra = driver = "databricks"
    famille = "lakehouse"
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"))


class DuckDB(Connector):
    name, label, schemes, dialect = "duckdb", "DuckDB", ("duckdb",), "duckdb"
    url = "fichier .duckdb ou duckdb://CHEMIN"
    status = TESTE
    # moteur SQL embarqué : rangé avec les entrepôts (tables, SQL), faute de famille plus juste
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 17/10/2026 : base de démonstration canot demo (docs/ecarts.md)")


class Dbt(Connector):
    """Projet dbt lu par ses artefacts (target/manifest.json, run_results.json, catalog.json) : ni réseau, ni commande dbt."""
    name, label, schemes, dialect = "dbt", "dbt", ("dbt", "dbt+file"), "dbt"
    url = "dbt://CHEMIN_DU_PROJET"
    famille = "fichiers"
    status = TESTE
    capabilities = Capabilities("complet", (1,), (), ("P3",),
                                teste_sur="19/10/2026 : canot scan dbt://lab/dbt (projet dbt du banc, manifest dbt 1.12.6, adaptateur Snowflake) ; "
                                          "canot rejeu-dbt sur ces artefacts et une copie DuckDB locale construite par les tests (docs/scan.md)")
    rejeu_familles = False  # pas d'historique de requêtes : la rejouabilité est calculée par modèle à la collecte
    profile = {
        "droits": {1: ["lecture des fichiers manifest.json, run_results.json et catalog.json du dossier target/"]},
        "droits_jamais": "aucune connexion à l'entrepôt, aucune commande dbt (ni parse, ni compile, ni run)",
        # lus par render.py (section Capacités)
        "commande_preuve": "canot rejeu-dbt",
        "limite_preuve": ("P3 porte sur les modèles SQL que DuckDB rejoue et dont la table d'origine est dans la copie ; les vues sont "
                          "rejouées sans comparaison, les modèles Python, incrémentaux, les snapshots et les modèles à reconstruire (F13) "
                          "sont comptés, pas prouvés."),
    }

    def open(self, url: str):
        from .scanner.dbt import ProjetDbt

        return ProjetDbt(url)

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import dbt

        return dbt.collect(src, salt=salt)


CONNECTORS: dict[str, Connector] = {c.name: c for c in (Snowflake(), BigQuery(), Databricks(), DuckDB(), Dbt())}


def for_url(url: str) -> Connector | None:
    """Connecteur désigné par le schéma de l'URL ; sans schéma, un dossier qui contient un manifest dbt est un projet dbt."""
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        return next((c for c in CONNECTORS.values() if scheme in c.schemes), None)
    from pathlib import Path

    p = Path(url).expanduser()
    if p.is_dir() and ((p / "manifest.json").is_file() or (p / "target" / "manifest.json").is_file()):
        return CONNECTORS.get("dbt")
    return None


def open_url(url: str):
    """Ouvre la source par le registre ; repli sur sources.open_source (même message pour un schéma inconnu)."""
    c = for_url(url)
    if c is not None:
        return c.open(url)
    from . import sources

    return sources.open_source(url)


def effective_level(src, level: int) -> int:
    """Niveau demandé, borné au plus haut niveau déclaré par le connecteur."""
    c = for_source(src)
    if c and c.capabilities.niveaux:
        return min(level, max(c.capabilities.niveaux))
    return level


def register(connector: Connector) -> Connector:
    CONNECTORS[connector.name] = connector
    return connector


def get(name: str | None) -> Connector | None:
    return CONNECTORS.get(name or "")


def for_source(src) -> Connector | None:
    d = getattr(src, "dialect", None)
    return next((c for c in CONNECTORS.values() if c.dialect == d), None)


def supports_full(src) -> bool:
    c = for_source(src)
    return bool(c and c.capabilities.inventaire == "complet")


def profile(plateforme: str | None) -> dict:
    c = get(plateforme)
    return c.profile if c else {}


def capacites(plateforme: str | None) -> dict | None:
    c = get(plateforme)
    return c.capacites() if c else None


def label(plateforme: str | None) -> str:
    c = get(plateforme)
    return c.label if c else str(plateforme)


def driver_hints() -> dict[str, tuple[str, str]]:
    return {c.driver: (c.label, c.extra) for c in CONNECTORS.values() if c.driver and c.extra}


def table() -> str:
    """Le tableau imprimé par `canot connecteurs`."""
    def inv(c: Connector) -> str:
        cap = c.capabilities
        if cap.inventaire == "complet":
            if len(cap.niveaux) == 1:
                return f"complet (niveau {cap.niveaux[0]})"
            return f"complet (niveaux {cap.niveaux[0]}-{cap.niveaux[-1]})" if cap.niveaux else "complet"
        return cap.inventaire

    head = ("Connecteur", "URL", "Inventaire (scan)", "Export (drill)", "Preuve", "Statut", "Extra pip")
    rows = [(c.label, c.url, inv(c), ", ".join(c.capabilities.export) or "aucun", ", ".join(c.capabilities.preuve) or "aucune",
             c.status, f"canot[{c.extra}]" if c.extra else "aucun") for c in CONNECTORS.values()]
    w = [max(len(r[i]) for r in (head, *rows)) for i in range(len(head))]
    lines = ["  ".join(v.ljust(w[i]) for i, v in enumerate(r)).rstrip() for r in (head, *rows)]
    used = sorted({p for c in CONNECTORS.values() for p in c.capabilities.preuve})
    lines += ["", "Preuve : " + " ; ".join(f"{p} {PREUVES[p]}" for p in used) + ".",
              "Inventaire complet : canot scan (lectures consignées, niveaux 1 à 3 selon le connecteur). Léger : tables, volumes, objets, sans historique."]
    return "\n".join(lines)
