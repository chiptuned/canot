from pathlib import Path

import pyarrow as pa
import pytest

from canot.convert import conform, fingerprint, target_schema
from canot.demo import DEMO_QUERIES, make_demo
from canot.drill import run_drill
from canot.evidence import verify_dir
from canot.scan import scan
from canot.sources.duckdb_src import DuckDBSource
from canot.target import parse_target
from canot.tolerances import Tolerances


@pytest.fixture()
def demo(tmp_path: Path):
    db = tmp_path / "demo.duckdb"
    make_demo(str(db), 2_000)
    q = tmp_path / "q.sql"
    q.write_text(DEMO_QUERIES)
    return db, q


def drill(src, tmp_path, q, name="out"):
    out = tmp_path / name
    return run_drill(src, parse_target(str(tmp_path / (name + "-lake")), None, out), out,
                     tmp_path / "k.key", str(q), Tolerances(), log=lambda *_: None), out


def test_scan(demo):
    db, _ = demo
    res = scan(DuckDBSource(str(db)))
    assert res["totals"]["tables"] == 3 and res["totals"]["rows"] == 2_000 + 200


def test_happy_path_and_evidence(demo, tmp_path):
    db, q = demo
    res, out = drill(DuckDBSource(str(db)), tmp_path, q)
    s = res["summary"]
    assert s["parity"] == "complète" and s["tables_ok"] == 3 and s["queries_match"] == 4
    assert verify_dir(out)[0]


def test_tamper_is_detected(demo, tmp_path):
    db, q = demo
    _, out = drill(DuckDBSource(str(db)), tmp_path, q)
    p = out / "evidence.jsonl"
    p.write_text(p.read_text().replace('"rows": 2000', '"rows": 2001', 1))
    assert not verify_dir(out)[0]


class LossySource(DuckDBSource):
    """Copy path silently corrupts one value: Canot must flag it."""

    def read(self, table, batch_rows=100_000):
        for b in super().read(table, batch_rows):
            if table.name == "orders":
                t = pa.Table.from_batches([b])
                i = t.schema.get_field_index("total")
                vals = t.column(i).to_pylist()
                vals[3] = vals[3] + 1
                t = t.set_column(i, t.schema.field(i), pa.array(vals, type=t.schema.field(i).type))
                yield from t.to_batches()
            else:
                yield b


def test_silent_corruption_is_detected(demo, tmp_path):
    db, q = demo
    res, _ = drill(LossySource(str(db)), tmp_path, q)
    assert res["summary"]["parity"] == "écarts"
    bad = [c for c in res["checks"] if not c["ok"]]
    assert bad and bad[0]["table"] == "shop.orders"


def test_nanosecond_timestamps_are_logged_as_lossy():
    t = pa.table({"ts": pa.array([1, 2], pa.timestamp("ns")), "n": pa.array([1, 2], pa.uint64())})
    sch, convs = target_schema("s.t", t.schema, {})
    assert sch.field("ts").type == pa.timestamp("us")
    assert {c.column for c in convs if c.lossy} == {"ts", "n"}
    assert conform(t, sch).schema == sch


def test_fingerprint_is_order_independent():
    a = pa.table({"x": [1, 2, 3], "y": ["a", None, "c"]})
    b = pa.table({"x": [3, 1, 2], "y": ["c", "a", None]})
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(pa.table({"x": [1, 2, 4], "y": ["a", None, "c"]}))


def test_scaleway_keys_from_scw_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("access_key: SCWTEST\nsecret_key: 'sekret'\ndefault_project_id: p\nprofiles:\n  other:\n    access_key: NOPE\n")
    for v in ("CANOT_S3_ACCESS_KEY", "CANOT_S3_SECRET_KEY", "SCW_ACCESS_KEY", "SCW_SECRET_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SCW_CONFIG_PATH", str(cfg))
    t = parse_target("scaleway:fr-par", "b", tmp_path)
    assert t.props["s3.access-key-id"] == "SCWTEST" and t.props["s3.secret-access-key"] == "sekret"
    assert t.props["s3.endpoint"] == "https://s3.fr-par.scw.cloud"
