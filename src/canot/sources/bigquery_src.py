from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pyarrow as pa

from ..model import ColumnInfo, TableInfo, q
from .base import Source

TIB = 1024**4


class BigQuerySource(Source):
    """bigquery://PROJET/DATASET1,DATASET2?location=EU  (sans dataset : tous ceux du projet).

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
        self.location = qs.get("location", "EU")
        if client is None:
            from google.cloud import bigquery  # extra optionnel

            client = bigquery.Client(project=self.project, location=self.location)
        self.client = client

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
                    f"select table_name, total_rows, total_logical_bytes from `region-{self.location.lower()}`."
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
            rows = self._rows(
                "select query, sum(total_bytes_billed) b from "
                f"`region-{self.location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT "
                "where job_type='QUERY' and state='DONE' and error_result is null "
                "and creation_time > timestamp_sub(current_timestamp(), interval 30 day) "
                "and statement_type='SELECT' and cache_hit is not true "
                "and lower(query) not like '%information_schema%' "
                "and not exists (select 1 from unnest(labels) l where l.key='tool' and l.value='canot') "
                f"group by query order by b desc limit {int(limit)}"
            )
            return [r[0] for r in rows]
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
            r = self._rows(
                "select coalesce(sum(if(reservation_id is null, total_bytes_billed, 0)),0), "
                "count(*), "
                "coalesce(sum(if(reservation_id is not null, total_slot_ms, 0)),0), "
                "countif(reservation_id is not null) from "
                f"`region-{self.location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT "
                "where job_type='QUERY' and creation_time > timestamp_sub(current_timestamp(), interval 90 day)"
            )[0]
            return {"on_demand_tib_billed_90d": round(r[0] / TIB, 3), "query_jobs_90d": int(r[1]),
                    "reserved_slot_hours_90d": round(r[2] / 3.6e6, 1), "reserved_jobs_90d": int(r[3])}
        except Exception:
            return None

    def close(self) -> None:
        self.client.close()
