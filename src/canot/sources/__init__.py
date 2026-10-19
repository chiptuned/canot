from __future__ import annotations

from .base import Source


def open_source(url: str) -> Source:
    scheme = url.split("://", 1)[0] if "://" in url else ""
    if scheme == "snowflake":
        from .snowflake_src import SnowflakeSource

        return SnowflakeSource(url)
    if scheme == "bigquery":
        from .bigquery_src import BigQuerySource

        return BigQuerySource(url)
    if scheme == "databricks":
        from .databricks_src import DatabricksSource

        return DatabricksSource(url)
    if scheme in ("duckdb", "") or url.endswith((".duckdb", ".db")):
        from .duckdb_src import DuckDBSource

        return DuckDBSource(url)
    raise ValueError(f"Unknown source: {url!r} (snowflake://, bigquery://, databricks://, duckdb://)")
