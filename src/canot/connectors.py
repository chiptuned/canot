"""Connector registry: what each platform supports (inventory, export, evidence), declared, never guessed.

The CLI, the scan collection and `canot connectors` read these declarations. No driver is imported here:
opening goes through `sources.open_source`, collection through `scanner.<platform>`, only when called.
"""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import ClassVar

from . import prices

PREUVES = {"P1": "data parity", "P2": "query replay", "P3": "pipeline replay",
           "P4": "scoring parity", "P5": "metrics parity"}
TESTE, NON_TESTE = "tested", "written, untested"
# platform families (generalist memo §1); keys are data values; "fichiers" = artifacts read offline (dbt, .sas…)
FAMILLES = {"entrepot": "warehouse", "lakehouse": "lakehouse", "science_donnees": "data science", "integration": "integration / ETL",
            "bi_semantique": "semantic BI", "orchestrateur": "orchestrator", "sas": "SAS", "fichiers": "files"}
# display labels of the inventory levels (the values themselves are data, read by the scan: unchanged)
INVENTAIRE_LABELS = {"complet": "full", "léger": "light", "partiel": "partial", "aucun": "none"}


@dataclass(frozen=True)
class Capabilities:
    inventaire: str                      # data value: "complet" (full scan), "léger" (light, scan.py), "aucun" (none)
    niveaux: tuple[int, ...] = ()        # --level values accepted by the full inventory
    export: tuple[str, ...] = ()         # formats written by drill
    preuve: tuple[str, ...] = ()         # PREUVES keys
    # actual date of a test recorded in the repository (docs/), with what was tested; None if the repository keeps no record
    teste_sur: str | None = None


class Connector(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    schemes: ClassVar[tuple[str, ...]]
    url: ClassVar[str]                   # URL form, for the help
    dialect: ClassVar[str]
    extra: ClassVar[str | None] = None   # pip install 'canot[extra]'
    driver: ClassVar[str | None] = None  # driver module prefix ("missing connector" message)
    status: ClassVar[str] = NON_TESTE
    famille: ClassVar[str] = "entrepot"  # FAMILLES key
    capabilities: ClassVar[Capabilities]
    # analysis profile: platform-specific constants, read by scanner/analyze.py and scanner/render.py
    # (grants requested per level, grants never requested…)
    profile: ClassVar[dict] = {}
    # HTTP guard (scanner/http_guard.py): POST allowed only on these read paths (regex patterns of the URL path)
    http_post_lecture: ClassVar[tuple[str, ...]] = ()
    # replayability of the history's query families (scanner/replay.py), computed after collection
    rejeu_familles: ClassVar[bool] = True

    def capacites(self) -> dict:
        """Serializable declaration (scan.json, MCP): what Canot supports here, and whether it is tested."""
        cap = self.capabilities
        return {"plateforme": self.name, "libelle": self.label, "famille": self.famille, "inventaire": cap.inventaire,
                "niveaux": list(cap.niveaux), "export": list(cap.export),
                "preuve": [{"forme": p, "libelle": PREUVES[p]} for p in cap.preuve],
                "statut": self.status, "teste_sur": cap.teste_sur}

    def open(self, url: str):
        from . import sources

        return sources.open_source(url)

    def scan_collect(self, src, *, level: int, salt: bytes, days: int | None = None) -> tuple[dict, list[dict]]:
        """Full inventory: (canot.scan/1 or /2 snapshot, raw families). The raw SQL text lives only in the second return value."""
        raise ValueError(f"full scan not available for {self.dialect}")


class Snowflake(Connector):
    name, label, schemes, dialect = "snowflake", "Snowflake", ("snowflake",), "snowflake"
    url = "snowflake://ACCOUNT/DATABASE"
    extra = driver = "snowflake"
    status = TESTE
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 2026-10-17: Snowflake trial account, TPC-H SF10 (docs/banc-essai.md)")
    profile = {
        "droits": {1: ["SNOWFLAKE.OBJECT_VIEWER", "SNOWFLAKE.USAGE_VIEWER"], 2: ["SNOWFLAKE.GOVERNANCE_VIEWER"], 3: ["SNOWFLAKE.SECURITY_VIEWER"]},
        "droits_jamais": "IMPORTED PRIVILEGES, MANAGE GRANTS, IMPORT SHARE, USAGE on functions, READ on stages",
        "horizon_jours": 365,
        "imports_systeme": ("SNOWFLAKE",),  # system database present in every account: not a client dependency
        # sources of reads per table, most to least reliable: (view, label, confidence [data value], fields of t["lectures"])
        "sources_lectures": (("ACCESS_HISTORY", "ACCESS_HISTORY", "haute", ("n",)),
                             ("TABLE_QUERY_PRUNING_HISTORY", "TABLE_QUERY_PRUNING_HISTORY", "moyenne", ("n_pruning", "n"))),
        "non_vus": {"ACCESS_HISTORY": ["share consumers", "RESULT_SCAN"],
                    "TABLE_QUERY_PRUNING_HISTORY": ["share consumers", "results served from the cache",
                                                    "queries answered from metadata"]},
        "source_prix": prices.SNOWFLAKE_SOURCE,
    }

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import snowflake

        return snowflake.collect(src.run_readonly, base=src.database, schemas=src.schemas or None, level=level, salt=salt,
                                 **({"days": days} if days else {}))


class BigQuery(Connector):
    name, label, schemes, dialect = "bigquery", "BigQuery", ("bigquery",), "bigquery"
    url = "bigquery://PROJECT/DATASET"
    extra, driver = "bigquery", "google.cloud"
    status = TESTE
    capabilities = Capabilities("complet", (1, 2, 3), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 2026-10-13: public dataset thelook_ecommerce (docs/ecarts.md)")
    profile = {
        "droits": {1: ["bigquery.jobs.create", "jobs.listAll", "datasets.get", "tables.list", "tables.get", "routines.list", "routines.get",
                       "models.list", "models.getMetadata"],
                   2: ["rowAccessPolicies.list", "transfers.get", "connections.list", "dataPolicies.list (optional)"],
                   3: ["reservations.list", "reservationAssignments.list", "capacityCommitments.list", "organization-level permissions"]},
        "droits_jamais": "tables.getData, bigquery.user and jobUser roles",
        "horizon_jours": 180,
        "sources_lectures": (("JOBS", "INFORMATION_SCHEMA.JOBS", "moyenne", ("n",)),),
        "non_vus": {"JOBS": ["reads through the Storage Read API", "queries run from other projects", "Analytics Hub consumers"]},
        # a "not seen" item that disappears when another read covered it
        "non_vus_couverts": {"queries run from other projects": "JOBS_BY_ORGANIZATION"},
        "source_prix": prices.BIGQUERY_SOURCE,
    }

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import bigquery

        return bigquery.collect(src, project=src.project, data_project=src.data_project, datasets=src.datasets or None, level=level,
                                salt=salt, **({"days": days} if days else {}))


class Databricks(Connector):
    name, label, schemes, dialect = "databricks", "Databricks", ("databricks",), "databricks"
    url = "databricks://HOST/HTTP_PATH?catalog=…"
    extra = driver = "databricks"
    famille = "lakehouse"
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"))


class DuckDB(Connector):
    name, label, schemes, dialect = "duckdb", "DuckDB", ("duckdb",), "duckdb"
    url = ".duckdb file or duckdb://PATH"
    status = TESTE
    # embedded SQL engine: grouped with warehouses (tables, SQL), for lack of a better family
    capabilities = Capabilities("léger", (), ("iceberg",), ("P1", "P2"),
                                teste_sur="canot drill, 2026-10-17: canot demo database (docs/ecarts.md)")


class Dbt(Connector):
    """dbt project. Scan: read through its artifacts (target/manifest.json, run_results.json, catalog.json), no network, no dbt
    command. Drill (canot drill dbt://PROJECT DESTINATION, dbt_drill.py): the warehouse named by profiles.yml is read, read-only,
    with the profile's credentials; the relations the project uses are copied to Iceberg and compared, then the models are replayed."""
    name, label, schemes, dialect = "dbt", "dbt", ("dbt", "dbt+file"), "dbt"
    url = "dbt://PROJECT_PATH"
    famille = "fichiers"
    status = TESTE
    # export and P1/P2 go through the warehouse the profile names (Snowflake, BigQuery, Databricks, DuckDB); P3 is the model replay
    capabilities = Capabilities("complet", (1,), ("iceberg",), ("P1", "P2", "P3"),
                                teste_sur="2026-10-19: canot scan dbt://lab/dbt (test bench dbt project, dbt 1.12.6 manifest, Snowflake adapter); "
                                          "canot drill dbt://lab/dbt --target snowflake on the Snowflake trial account: 8 relations copied and "
                                          "identical, 8 models replayed (docs/scan.md); BigQuery and Databricks profiles written, untested")
    rejeu_familles = False  # no query history: replayability is computed per model during collection
    profile = {
        "droits": {1: ["read the manifest.json, run_results.json and catalog.json files in the target/ folder"]},
        "droits_jamais": "the scan opens no connection to the warehouse and runs no dbt command (no parse, compile or run)",
        # read by render.py (Capabilities section)
        "commande_preuve": "canot drill dbt://PROJECT DESTINATION (read-only, with the dbt profile's credentials)",
        "limite_preuve": ("P1 covers the sources, seeds, snapshots and table or incremental models found in the warehouse; P3 covers "
                          "the SQL models that DuckDB replays and whose original table is in the copy; views are replayed without "
                          "comparison; Python models, incremental models and models to rebuild (F13) are counted, not proven."),
    }

    def open(self, url: str):
        from .scanner.dbt import ProjetDbt

        return ProjetDbt(url)

    def scan_collect(self, src, *, level, salt, days=None):
        from .scanner import dbt

        return dbt.collect(src, salt=salt)


CONNECTORS: dict[str, Connector] = {c.name: c for c in (Snowflake(), BigQuery(), Databricks(), DuckDB(), Dbt())}


# Platforms people ask for, with no connector yet: they get a helpful message and a way to ask for it (scheme -> label).
ROADMAP = {
    "fabric": "Microsoft Fabric / Azure Synapse", "synapse": "Microsoft Fabric / Azure Synapse", "mssql": "Microsoft Fabric / Azure Synapse",
    "redshift": "Amazon Redshift", "teradata": "Teradata", "oracle": "Oracle", "sap": "SAP Datasphere", "datasphere": "SAP Datasphere",
    "hana": "SAP HANA", "clickhouse": "ClickHouse", "dataiku": "Dataiku", "sas": "SAS",
}
REQUEST_URL = "https://canot.io/en/review.html?connector={scheme}"


class NoConnector(ValueError):
    """The URL names a platform Canot has no connector for (yet)."""

    def __init__(self, scheme: str):
        self.scheme = scheme
        self.label = ROADMAP.get(scheme, scheme)
        super().__init__(self.explain())

    def explain(self) -> str:
        have = ", ".join(c.label for c in CONNECTORS.values())
        return (f"Canot has no connector for {self.label} yet.\n"
                f"It would be written and validated with you, on your own account. Tell us you need it (two minutes, it tells us what to build next):\n"
                f"  {REQUEST_URL.format(scheme=self.scheme)}\n"
                f"Q4O, the team behind Canot, can also build it for a real project and run the exit exercise with you: https://canot.io/en/review.html\n"
                f"Available today: {have} (canot connectors).")


def for_url(url: str) -> Connector | None:
    """Connector named by the URL scheme; without a scheme, a folder that holds a dbt manifest is a dbt project."""
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        return next((c for c in CONNECTORS.values() if scheme in c.schemes), None)
    from pathlib import Path

    p = Path(url).expanduser()
    if p.is_dir() and ((p / "manifest.json").is_file() or (p / "target" / "manifest.json").is_file()):
        return CONNECTORS.get("dbt")
    return None


def open_url(url: str):
    """Open the source through the registry; falls back to sources.open_source (same message for an unknown scheme)."""
    c = for_url(url)
    if c is not None:
        return c.open(url)
    from . import sources

    return sources.open_source(url)


def effective_level(src, level: int) -> int:
    """Requested level, capped at the highest level the connector declares."""
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
    """The table printed by `canot connectors`."""
    def inv(c: Connector) -> str:
        cap = c.capabilities
        label = INVENTAIRE_LABELS.get(cap.inventaire, cap.inventaire)
        if cap.inventaire == "complet":
            if len(cap.niveaux) == 1:
                return f"{label} (level {cap.niveaux[0]})"
            return f"{label} (levels {cap.niveaux[0]}-{cap.niveaux[-1]})" if cap.niveaux else label
        return label

    head = ("Connector", "URL", "Inventory (scan)", "Export (drill)", "Evidence", "Status", "Installation")
    rows = [(c.label, c.url, inv(c), ", ".join(c.capabilities.export) or "none", ", ".join(c.capabilities.preuve) or "none",
             c.status, "included") for c in CONNECTORS.values()]
    w = [max(len(r[i]) for r in (head, *rows)) for i in range(len(head))]
    lines = ["  ".join(v.ljust(w[i]) for i, v in enumerate(r)).rstrip() for r in (head, *rows)]
    used = sorted({p for c in CONNECTORS.values() for p in c.capabilities.preuve})
    lines += ["", "On the roadmap, no connector yet: " + ", ".join(sorted(set(ROADMAP.values()))) + ". Ask for one: " + REQUEST_URL.format(scheme="your-platform"), "",
              "Evidence: " + "; ".join(f"{p} {PREUVES[p]}" for p in used) + ".",
              "Full inventory: canot scan (logged reads, levels 1 to 3 depending on the connector). Light: tables, volumes, objects, no history."]
    return "\n".join(lines)
