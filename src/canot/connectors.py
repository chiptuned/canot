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


@dataclass(frozen=True)
class Capabilities:
    inventaire: str                      # "complet" (scan complet), "léger" (scan.py), "aucun"
    niveaux: tuple[int, ...] = ()        # --level acceptés par l'inventaire complet
    export: tuple[str, ...] = ()         # formats écrits par drill
    preuve: tuple[str, ...] = ()         # clés de PREUVES


class Connector(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    schemes: ClassVar[tuple[str, ...]]
    url: ClassVar[str]                   # forme d'URL, pour l'aide
    dialect: ClassVar[str]
    extra: ClassVar[str | None] = None   # pip install 'canot[extra]'
    driver: ClassVar[str | None] = None  # préfixe du module du pilote (message « connecteur absent »)
    status: ClassVar[str] = NON_TESTE
    capabilities: ClassVar[Capabilities]
    # fiche d'analyse : constantes propres à la plateforme, lues par scanner/analyze.py
    profile: ClassVar[dict] = {}

    def open(self, url: str):
        from . import sources

        return sources.open_source(url)

    def scan_collect(self, src, *, level: int, salt: bytes, days: int | None = None) -> tuple[dict, list[dict]]:
        """Inventaire complet : (snapshot canot.scan/1, familles brutes). Le texte SQL brut ne vit que dans le second retour."""
        raise ValueError(f"scan complet non disponible pour {self.dialect}")


class Snowflake(Connector):
    name, label, schemes, dialect = "snowflake", "Snowflake", ("snowflake",), "snowflake"
    url = "snowflake://COMPTE/BASE"
    extra = driver = "snowflake"
    status = TESTE
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"))
    profile = {
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
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"))
    profile = {
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
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"))


class DuckDB(Connector):
    name, label, schemes, dialect = "duckdb", "DuckDB", ("duckdb",), "duckdb"
    url = "fichier .duckdb ou duckdb://CHEMIN"
    status = TESTE
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"))


CONNECTORS: dict[str, Connector] = {c.name: c for c in (Snowflake(), BigQuery(), Databricks(), DuckDB())}


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


def driver_hints() -> dict[str, tuple[str, str]]:
    return {c.driver: (c.label, c.extra) for c in CONNECTORS.values() if c.driver and c.extra}


def table() -> str:
    """Le tableau imprimé par `canot connecteurs`."""
    def inv(c: Connector) -> str:
        cap = c.capabilities
        if cap.inventaire == "complet":
            return f"complet (niveaux {cap.niveaux[0]}-{cap.niveaux[-1]})" if cap.niveaux else "complet"
        return cap.inventaire

    head = ("Connecteur", "URL", "Inventaire (scan)", "Export (drill)", "Preuve", "Statut", "Extra pip")
    rows = [(c.label, c.url, inv(c), ", ".join(c.capabilities.export) or "aucun", ", ".join(c.capabilities.preuve) or "aucune",
             c.status, f"canot[{c.extra}]" if c.extra else "aucun") for c in CONNECTORS.values()]
    w = [max(len(r[i]) for r in (head, *rows)) for i in range(len(head))]
    lines = ["  ".join(v.ljust(w[i]) for i, v in enumerate(r)).rstrip() for r in (head, *rows)]
    used = sorted({p for c in CONNECTORS.values() for p in c.capabilities.preuve})
    lines += ["", "Preuve : " + " ; ".join(f"{p} {PREUVES[p]}" for p in used) + ".",
              "Inventaire complet : canot scan (lectures consignées, niveaux 1 à 3). Léger : tables, volumes, objets, sans historique."]
    return "\n".join(lines)
