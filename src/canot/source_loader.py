from __future__ import annotations

from .sources.duckdb_src import DuckDBSource


def open_demo(path: str) -> DuckDBSource:
    return DuckDBSource(path)
