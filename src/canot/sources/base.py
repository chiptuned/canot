from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

import pyarrow as pa

from ..model import TableInfo


class Source(ABC):
    dialect: str = "duckdb"
    kind: str = "source"
    # credits consumed per warehouse hour (Snowflake XS = 1); used for cost estimates only
    credits_per_hour: float = 1.0

    @abstractmethod
    def tables(self) -> list[TableInfo]: ...

    @abstractmethod
    def read(self, table: TableInfo, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]: ...

    @abstractmethod
    def query(self, sql: str) -> pa.Table: ...

    def table_ref(self, table: TableInfo) -> str:
        """How to name the table in SQL sent to this source."""
        return table.sql_for(self.dialect)

    def history_queries(self, limit: int = 20) -> list[str]:
        return []

    def objects(self) -> dict[str, list[str]]:
        """Non-table objects that do not move automatically: views, procedures, tasks, ..."""
        return {}

    def usage_90d(self) -> dict | None:
        return None

    def describe(self) -> str:
        return self.kind

    def close(self) -> None:  # pragma: no cover
        pass
