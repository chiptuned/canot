"""BigQuery source against a fake client: checks the SQL Canot sends (quoting, regions), not Google itself."""
import pyarrow as pa

from canot.model import TableInfo
from canot.sources.bigquery_src import BigQuerySource


class Row(dict):
    def values(self):
        return list(super().values())


class Job:
    def __init__(self, rows): self.rows = rows
    def result(self): return [Row({str(i): v for i, v in enumerate(r)}) for r in self.rows]
    def to_arrow(self): return pa.table({"n": [1]})


class FakeClient:
    def __init__(self): self.sql = []
    def query(self, sql, **kw):
        self.sql.append(sql)
        s = sql.lower()
        if "from" in s and ".columns" in s:
            return Job([("orders", "id", "INT64"), ("orders", "total", "NUMERIC")])
        if "table_storage" in s:
            return Job([("orders", 10, 1000)])
        if ".tables" in s:
            return Job([("orders",)])
        return Job([])
    def list_datasets(self): return []
    def close(self): pass


def test_tables_and_quoting():
    c = FakeClient()
    src = BigQuerySource("bigquery://proj/sales?location=EU", client=c)
    tabs = src.tables()
    assert [(t.fqn, t.rows, t.bytes) for t in tabs] == [("sales.orders", 10, 1000)]
    assert [col.type for col in tabs[0].columns] == ["INT64", "NUMERIC"]
    assert src.table_ref(tabs[0]) == "`proj`.`sales`.`orders`"
    assert any("`region-eu`.INFORMATION_SCHEMA.TABLE_STORAGE" in s for s in c.sql)


def test_dialect_and_defaults():
    src = BigQuerySource("bigquery://proj", client=FakeClient())
    assert src.dialect == "bigquery" and src.describe() == "bigquery:proj"
    assert src.table_ref(TableInfo("d", "t")) == "`proj`.`d`.`t`"
