from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import exp


def q(ident: str, dialect: str = "duckdb") -> str:
    """Quote an SQL identifier for the given dialect (backticks for BigQuery/Databricks, double quotes otherwise)."""
    return exp.to_identifier(ident, quoted=True).sql(dialect=dialect)


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

    def sql_for(self, dialect: str = "duckdb") -> str:
        return f"{q(self.schema, dialect)}.{q(self.name, dialect)}"

    @property
    def sql(self) -> str:
        return self.sql_for("duckdb")
