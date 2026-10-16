from __future__ import annotations

from dataclasses import dataclass, field


def q(ident: str) -> str:
    """Quote an SQL identifier (valid for Snowflake, Databricks ANSI mode and DuckDB)."""
    return '"' + ident.replace('"', '""') + '"'


@dataclass
class ColumnInfo:
    name: str
    type: str


@dataclass
class TableInfo:
    schema: str
    name: str
    rows: int | None = None
    bytes: int | None = None
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def sql(self) -> str:
        return f"{q(self.schema)}.{q(self.name)}"
