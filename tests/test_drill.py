from pathlib import Path

import os
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
    assert s["parity"] == "complete" and s["tables_ok"] == 3 and s["queries_match"] == 4
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
    assert res["summary"]["parity"] == "mismatch"
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


def test_scaleway_named_profile(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("access_key: TOPKEY\nsecret_key: topsecret\nprofiles:\n  canot:\n    access_key: CANOTKEY\n    secret_key: canotsecret\n    default_project_id: p\n")
    for v in ("CANOT_S3_ACCESS_KEY", "CANOT_S3_SECRET_KEY", "SCW_ACCESS_KEY", "SCW_SECRET_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SCW_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("SCW_PROFILE", "canot")
    t = parse_target("scaleway:fr-par", "b", tmp_path)
    assert t.props["s3.access-key-id"] == "CANOTKEY" and t.props["s3.secret-access-key"] == "canotsecret"
    monkeypatch.delenv("SCW_PROFILE")
    t = parse_target("scaleway:fr-par", "b", tmp_path)
    assert t.props["s3.access-key-id"] == "TOPKEY"


def test_demo_command_end_to_end_and_synthesis(tmp_path, capsys, monkeypatch):
    from canot.cli import main
    monkeypatch.setattr("canot.demo.make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))  # fallback dataset, no network
    from canot.evidence import verify_dir

    assert main(["demo", "--out", str(tmp_path / "d"), "--rows", "2000", "--no-open"]) == 0
    html = (tmp_path / "d" / "evidence" / "report.html").read_text()
    assert "Exit feasible on this scope" in html and "canot.io/#contact" in html
    assert "lose precision" in html and "TIMESTAMP_NS" in html  # the nanosecond trap is flagged as a point to address
    assert "/tmp" not in html and "/private" not in html and "None" not in html  # no local path and no "None" in a shared report
    assert "Fingerprint of the public signing key" in html
    assert verify_dir(tmp_path / "d" / "evidence")[0]


def test_synthesis_flags_corruption(demo, tmp_path):
    from canot.report import synthese

    res, _ = drill(LossySource(str(demo[0])), tmp_path, demo[1])
    assert synthese(res)["verdict"].startswith("Exit not proven")


def test_snowflake_setup_generates_key(tmp_path, monkeypatch, capsys):
    from canot.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    assert main(["snowflake-setup", "--generate-key", "--base", "MABASE"]) == 0
    out = capsys.readouterr().out
    assert "MABASE" in out and "RSA_PUBLIC_KEY = '" in out and "<public key" not in out and "<DATABASE>" not in out
    if os.name != "nt":  # POSIX permission bits do not exist on Windows
        assert (tmp_path / ".canot" / "snowflake_rsa_key.p8").stat().st_mode & 0o077 == 0


def test_errors_are_readable():
    from canot.cli import explain

    msg = explain(ModuleNotFoundError("x", name="google.cloud"), ["scan", "--from", "bigquery://p/d"])
    assert "canot[bigquery]" in msg and "uvx" in msg
    assert "gcloud auth application-default login" in explain(type("DefaultCredentialsError", (Exception,), {})("x"), [])
    assert explain(RuntimeError("something else"), []) is None


def test_reread_sql_runs_with_plain_duckdb(tmp_path, monkeypatch):
    """The copy must be readable without Canot: actually run reread.sql with DuckDB."""
    import duckdb
    from canot.cli import main
    monkeypatch.setattr("canot.demo.make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))

    assert main(["demo", "--out", str(tmp_path / "d"), "--rows", "1000", "--no-open"]) == 0
    sql = (tmp_path / "d" / "evidence" / "reread.sql").read_text()
    con = duckdb.connect()
    try:
        con.execute("INSTALL iceberg; LOAD iceberg;")
    except Exception as e:  # offline: the extension cannot be downloaded
        pytest.skip(f"iceberg extension unavailable: {e}")
    counts = {}
    for line in sql.splitlines():
        if line.startswith("SELECT"):
            name, n = con.execute(line.rstrip(";")).fetchone()
            counts[name] = n
    assert counts["distrib.stores"] == 300 and counts["distrib.tickets"] == 1000


def test_table_vide_et_fuseau_non_utc(tmp_path):
    """Regressions found on a real Snowflake account: empty table without an Arrow batch, TIMESTAMP_TZ in a named time zone."""
    import pyarrow as pa
    from pyiceberg.catalog.sql import SqlCatalog

    from canot.convert import target_schema
    from canot.copy import copy_table, empty_table
    from canot.model import ColumnInfo, TableInfo
    from canot.sources.base import Source

    t = TableInfo("RAW", "VIDE", 0, 0, [ColumnInfo("ID", "NUMBER(38,0)"), ColumnInfo("TS", "TIMESTAMP_TZ"), ColumnInfo("V", "VARIANT")])
    e = empty_table(t, "snowflake")
    assert e.num_rows == 0 and e.column_names == ["ID", "TS", "V"]

    sch, convs = target_schema("RAW.T", pa.schema([("ts", pa.timestamp("us", tz="America/Los_Angeles"))]), {})
    assert sch.field("ts").type == pa.timestamp("us", tz="UTC") and convs and convs[0].lossy

    class Vide(Source):
        dialect = "snowflake"
        def tables(self): return [t]
        def read(self, table, batch_rows=100_000): return iter(())
        def query(self, sql): return pa.table({})

    cat = SqlCatalog("c", uri=f"sqlite:///{tmp_path}/c.db", warehouse=f"file://{tmp_path}/w")
    r = copy_table(Vide(), t, cat)
    assert r.error is None and cat.table_exists(("RAW", "VIDE"))


def test_demo_tpch_si_l_extension_est_disponible(tmp_path):
    """The default demo is TPC-H (if the extension can be downloaded): 8 tables, and the nanosecond mismatch is flagged."""
    import duckdb
    import pytest

    try:
        duckdb.connect().execute("install tpch")
    except Exception:
        pytest.skip("tpch extension unavailable (offline)")
    from canot.cli import main

    assert main(["demo", "--out", str(tmp_path / "d"), "--sf", "0.01", "--no-open"]) in (0, 1)
    html = (tmp_path / "d" / "evidence" / "report.html").read_text()
    assert "TPC-H" in html or "lineitem" in html.lower()
    assert "TIMESTAMP_NS" in html


def test_demo_est_un_raccourci_exact(tmp_path, capsys, monkeypatch):
    """The two commands shown by `canot demo` give the same result as the demo: replay them by hand."""
    import re

    from canot.cli import main

    monkeypatch.setattr("canot.demo.make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    out = tmp_path / "d"
    assert main(["demo", "--out", str(out), "--no-open"]) == 0
    shown = capsys.readouterr().out
    assert f"canot demo-data {out}/demo.duckdb --tpch" in shown
    m = re.search(r"canot drill (\S+) (\S+) --queries (\S+) --out (\S+)", shown)
    assert m and m.group(2) == f"{out}/iceberg" and m.group(3) == f"{out}/queries.sql" and m.group(4) == f"{out}/evidence"
    # same source, same queries, same output folder: the long command replays the exercise
    assert main(["drill", m.group(1), str(tmp_path / "autre"), "--queries", m.group(3), "--out", str(tmp_path / "o2")]) in (0, 1)
    assert (tmp_path / "o2" / "report.json").exists()


def test_demo_refuse_tout_de_suite_une_destination_incomplete(tmp_path, capsys):
    from canot.cli import main

    assert main(["demo", "aws:eu-west-3", "--out", str(tmp_path / "d"), "--no-open"]) == 1
    assert "bucket" in capsys.readouterr().err.lower()
