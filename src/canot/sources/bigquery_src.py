from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pyarrow as pa

from ..model import ColumnInfo, TableInfo, q
from ..scanner.guard import check_readonly
from .base import Source

TIB = 1024**4

SCAN_PERMISSIONS = (
    "bigquery.jobs.create", "bigquery.jobs.listAll", "bigquery.datasets.get", "bigquery.tables.list", "bigquery.tables.get",
    "bigquery.tables.getIamPolicy", "bigquery.routines.list", "bigquery.routines.get", "bigquery.models.list", "bigquery.models.getMetadata",
    "bigquery.reservations.list", "bigquery.reservationAssignments.list", "bigquery.capacityCommitments.list", "bigquery.bireservations.get",
    "bigquery.config.get",
)
# optionnelles : sans elles, ces lectures sont consignées « non lu » dans le rapport
SCAN_OPTIONAL_PERMISSIONS = ("bigquery.rowAccessPolicies.list", "bigquery.transfers.get", "bigquery.connections.list", "bigquery.dataPolicies.list")

SETUP_SH = """\
# Canot : accès en lecture seule à BigQuery. À relire puis à lancer par un administrateur du projet.
# Le rôle ne contient aucune permission de lecture du contenu des tables (ni tables.getData, ni les rôles bigquery.user / jobUser).
PROJECT=<PROJET>
MEMBER=<user:prenom.nom@exemple.fr  ou  serviceAccount:canot@<PROJET>.iam.gserviceaccount.com>

gcloud iam roles create canotScanner --project="$PROJECT" \\
  --title="Canot : lecture des métadonnées" \\
  --description="Inventaire, volumes, historique des jobs, objets et politiques. Aucune lecture du contenu des tables." \\
  --permissions="{perms}"

gcloud projects add-iam-policy-binding "$PROJECT" --member="$MEMBER" --role="projects/$PROJECT/roles/canotScanner"

# Optionnel : politiques de lignes, requêtes planifiées, connexions, politiques de données (sinon : « non lu » dans le rapport).
# gcloud iam roles update canotScanner --project="$PROJECT" --add-permissions="{optional}"

# Pour un exercice de sortie complet (canot drill), qui lit les données : sur les seuls datasets à copier,
# roles/bigquery.dataViewer, et sur le projet roles/bigquery.readSessionUser. Pas pour le scan.
"""


def setup_script(project: str | None = None, member: str | None = None) -> str:
    sh = SETUP_SH.format(perms=",".join(SCAN_PERMISSIONS), optional=",".join(SCAN_OPTIONAL_PERMISSIONS))
    if project:
        sh = sh.replace("<PROJET>", project)
    if member:
        sh = sh.replace("<user:prenom.nom@exemple.fr  ou  serviceAccount:canot@" + (project or "<PROJET>") + ".iam.gserviceaccount.com>", member)
    return sh

MIN_BILLED = 10 * 1024**2  # BigQuery facture au moins 10 Mo par vue INFORMATION_SCHEMA citée, sans cache
DEFAULT_MAX_BYTES = 10 * 1024**3
LABELS = {"tool": "canot"}


class BytesCapExceeded(Exception):
    def __init__(self, estimated: int, cap: int):
        super().__init__(f"estimated {estimated} bytes, cap {cap}")
        self.estimated, self.cap = estimated, cap


def _job_config(**kw):
    try:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(**kw)
    except ImportError:  # client de test
        from types import SimpleNamespace

        return SimpleNamespace(**kw)


class BigQuerySource(Source):
    """bigquery://PROJET/DATASET1,DATASET2[?location=EU]  (sans dataset : tous ceux du projet).

    Sans `location`, la région de chaque dataset est lue sur le dataset lui-même : les régions ne sont pas
    hiérarchiques (EU ne couvre pas europe-west1).

    Authentification : identifiants applicatifs Google (GOOGLE_APPLICATION_CREDENTIALS ou
    `gcloud auth application-default login`). Droits minimaux : BigQuery Data Viewer sur les datasets,
    BigQuery Job User sur le projet, et BigQuery Resource Viewer pour lire l'historique des jobs."""

    dialect = "bigquery"
    kind = "bigquery"

    def __init__(self, url: str, client=None):
        u = urlparse(url)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.project = u.netloc
        raw = [d for d in u.path.strip("/").split(",") if d]
        # "bigquery://FACTURATION/projet-donnees.dataset" : lire un dataset d'un autre projet (jeux publics inclus)
        self.data_project = next((d.rsplit(".", 1)[0] for d in raw if "." in d), self.project)
        self.datasets = [d.rsplit(".", 1)[-1] for d in raw]
        self.location = qs.get("location")
        self.max_bytes_billed = DEFAULT_MAX_BYTES
        self.cost = {"requetes": 0, "dry_runs": 0, "octets_estimes": 0, "octets_factures": None}
        self._locations: dict[str, str | None] = {}
        if client is None:
            from google.cloud import bigquery  # extra optionnel

            client = bigquery.Client(project=self.project, location=self.location)
        self.client = client

    def run_readonly(self, sql: str, location: str | None = None, *, max_bytes: int | None = None) -> list[dict]:
        """Exécuteur du scan : garde lecture seule, dry-run d'abord, refus si l'estimation dépasse le plafond,
        puis exécution étiquetée tool=canot avec maximum_bytes_billed. Lève sans rien exécuter en cas de refus."""
        check_readonly(sql, "bigquery")
        loc = location or self.location
        cap = max(max_bytes or self.max_bytes_billed, MIN_BILLED)
        dry = self.client.query(sql, location=loc, job_config=_job_config(dry_run=True, use_query_cache=False, labels=dict(LABELS)))
        est = int(getattr(dry, "total_bytes_processed", None) or 0)
        self.cost["dry_runs"] += 1
        self.cost["octets_estimes"] += est
        if est > cap:
            raise BytesCapExceeded(est, cap)
        job = self.client.query(sql, location=loc, job_config=_job_config(labels=dict(LABELS), maximum_bytes_billed=cap))
        rows = [r if isinstance(r, dict) else dict(r.items()) for r in job.result()]
        self.cost["requetes"] += 1
        billed = getattr(job, "total_bytes_billed", None)
        if billed is not None:
            self.cost["octets_factures"] = (self.cost["octets_factures"] or 0) + int(billed)
        return rows

    def _location(self, ds: str) -> str | None:
        if self.location:
            return self.location
        if ds not in self._locations:
            self._locations[ds] = self.client.get_dataset(f"{self.data_project}.{ds}").location
        return self._locations[ds]

    def _regions(self) -> list[str]:
        if self.location:
            return [self.location]
        return sorted({loc for ds in self._datasets() if (loc := self._location(ds))})

    def describe(self) -> str:
        return f"bigquery:{self.project}" + (f"/{','.join(self.datasets)}" if self.datasets else "")

    def table_ref(self, table: TableInfo) -> str:
        return f"{q(self.data_project, 'bigquery')}.{table.sql_for('bigquery')}"

    def _job(self, sql: str):
        try:  # étiquette : ces requêtes sont les nôtres, on les exclut de l'historique rejoué
            from google.cloud import bigquery

            return self.client.query(sql, job_config=bigquery.QueryJobConfig(labels={"tool": "canot"}))
        except ImportError:
            return self.client.query(sql)

    def _rows(self, sql: str) -> list[tuple]:
        return [tuple(r.values()) for r in self._job(sql).result()]

    def _datasets(self) -> list[str]:
        return self.datasets or [d.dataset_id for d in self.client.list_datasets()]

    def tables(self) -> list[TableInfo]:
        out = []
        for ds in self._datasets():
            info = f"{q(self.data_project, 'bigquery')}.{q(ds, 'bigquery')}.INFORMATION_SCHEMA"
            cols: dict[str, list[ColumnInfo]] = {}
            for t, c, d in self._rows(
                f"select table_name, column_name, data_type from {info}.COLUMNS order by table_name, ordinal_position"
            ):
                cols.setdefault(t, []).append(ColumnInfo(c, d))
            sizes = {}
            try:
                for t, n, b in self._rows(
                    f"select table_name, total_rows, total_logical_bytes from `region-{self._location(ds).lower()}`."
                    f"INFORMATION_SCHEMA.TABLE_STORAGE where table_schema='{ds}'"
                ):
                    sizes[t] = (n, b)
            except Exception:
                pass
            for (t,) in self._rows(f"select table_name from {info}.TABLES where table_type='BASE TABLE' order by 1"):
                n, b = sizes.get(t, (None, None))
                if n is None:  # projet de données différent ou droits limités : métadonnées de la table
                    try:
                        meta = self.client.get_table(f"{self.data_project}.{ds}.{t}")
                        n, b = meta.num_rows, meta.num_bytes
                    except Exception:
                        pass
                out.append(TableInfo(ds, t, n, b, cols.get(t, [])))
        return out

    def read(self, table: TableInfo, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        ref = f"{self.data_project}.{table.schema}.{table.name}"
        bqs = None
        try:  # API Storage Read : bien plus rapide que les pages REST
            from google.cloud import bigquery_storage

            bqs = bigquery_storage.BigQueryReadClient()
        except Exception:
            pass
        yield from self.client.list_rows(ref, page_size=batch_rows).to_arrow_iterable(bqstorage_client=bqs)

    def query(self, sql: str) -> pa.Table:
        return self._job(sql).to_arrow()

    def history_queries(self, limit: int = 20) -> list[str]:
        try:
            rows = []
            for loc in self._regions():
                rows += self._rows(
                    "select query, sum(total_bytes_billed) b from "
                    f"`region-{loc.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT "
                    "where job_type='QUERY' and state='DONE' and error_result is null "
                    "and creation_time > timestamp_sub(current_timestamp(), interval 30 day) "
                    "and statement_type='SELECT' and cache_hit is not true "
                    "and lower(query) not like '%information_schema%' "
                    "and not exists (select 1 from unnest(labels) l where l.key='tool' and l.value='canot') "
                    f"group by query order by b desc limit {int(limit)}"
                )
            return [r[0] for r in sorted(rows, key=lambda r: r[1] or 0, reverse=True)[:limit]]
        except Exception:
            return []

    def objects(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for key, view, extra in (
            ("views", "TABLES", "where table_type='VIEW'"),
            ("materialized_views", "TABLES", "where table_type='MATERIALIZED VIEW'"),
            ("external_tables", "TABLES", "where table_type='EXTERNAL'"),
            ("routines", "ROUTINES", ""),
            ("row_access_policies", "ROW_ACCESS_POLICIES", ""),
        ):
            found: list[str] = []
            for ds in self._datasets():
                try:
                    cols = {"TABLES": "table_name", "ROUTINES": "routine_name", "ROW_ACCESS_POLICIES": "row_access_policy_id"}[view]
                    found += [f"{ds}.{r[0]}" for r in self._rows(
                        f"select {cols} from {q(self.data_project, 'bigquery')}.{q(ds, 'bigquery')}.INFORMATION_SCHEMA.{view} {extra}")]
                except Exception:
                    pass
            out[key] = found
        try:  # BigQuery ML models
            out["ml_models"] = [f"{m.dataset_id}.{m.model_id}" for ds in self._datasets()
                                for m in self.client.list_models(f"{self.data_project}.{ds}")]
        except Exception:
            out["ml_models"] = []
        return out

    def usage_90d(self) -> dict | None:
        """Consommation sur 90 jours. La facturation à la demande (octets facturés) et la capacité réservée (slots) sont séparées :
        additionner les deux n'aurait pas de sens."""
        try:
            tot = [0, 0, 0, 0]
            for loc in self._regions():
                r = self._rows(
                    "select coalesce(sum(if(reservation_id is null, total_bytes_billed, 0)),0), "
                    "count(*), "
                    "coalesce(sum(if(reservation_id is not null, total_slot_ms, 0)),0), "
                    "countif(reservation_id is not null) from "
                    f"`region-{loc.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT "
                    "where job_type='QUERY' and creation_time > timestamp_sub(current_timestamp(), interval 90 day)"
                )[0]
                tot = [a + (b or 0) for a, b in zip(tot, r)]
            return {"on_demand_tib_billed_90d": round(tot[0] / TIB, 3), "query_jobs_90d": int(tot[1]),
                    "reserved_slot_hours_90d": round(tot[2] / 3.6e6, 1), "reserved_jobs_90d": int(tot[3])}
        except Exception:
            return None

    def close(self) -> None:
        self.client.close()
