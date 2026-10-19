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
    monkeypatch.setattr("canot.demo.make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("hors ligne")))  # jeu de secours, sans réseau
    from canot.evidence import verify_dir

    assert main(["demo", "--out", str(tmp_path / "d"), "--rows", "2000", "--no-open"]) == 0
    html = (tmp_path / "d" / "dossier" / "report.html").read_text()
    assert "Sortie faisable sur ce périmètre" in html and "canot.io/#contact" in html
    assert "perdent de la précision" in html and "TIMESTAMP_NS" in html  # le piège des nanosecondes est signalé comme point à traiter
    assert "/tmp" not in html and "/private" not in html and "None" not in html  # pas de chemin local ni de « None » dans un dossier transmis
    assert "Empreinte de la clé publique" in html
    assert verify_dir(tmp_path / "d" / "dossier")[0]


def test_synthesis_flags_corruption(demo, tmp_path):
    from canot.report import synthese

    res, _ = drill(LossySource(str(demo[0])), tmp_path, demo[1])
    assert synthese(res)["verdict"].startswith("Sortie non prouvée")


def test_snowflake_setup_generates_key(tmp_path, monkeypatch, capsys):
    from canot.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["snowflake-setup", "--generate-key", "--base", "MABASE"]) == 0
    out = capsys.readouterr().out
    assert "MABASE" in out and "RSA_PUBLIC_KEY = '" in out and "<clé publique" not in out
    assert (tmp_path / ".canot" / "snowflake_rsa_key.p8").stat().st_mode & 0o077 == 0


def test_errors_are_readable():
    from canot.cli import explain

    msg = explain(ModuleNotFoundError("x", name="google.cloud"), ["scan", "--from", "bigquery://p/d"])
    assert "canot[bigquery]" in msg and "uvx --from" in msg
    assert "gcloud auth application-default login" in explain(type("DefaultCredentialsError", (Exception,), {})("x"), [])
    assert explain(RuntimeError("autre chose"), []) is None


def test_relire_sql_runs_with_plain_duckdb(tmp_path, monkeypatch):
    """La copie doit se relire sans Canot : on exécute vraiment relire.sql avec DuckDB."""
    import duckdb
    from canot.cli import main
    monkeypatch.setattr("canot.demo.make_tpch", lambda *a, **k: (_ for _ in ()).throw(OSError("hors ligne")))

    assert main(["demo", "--out", str(tmp_path / "d"), "--rows", "1000", "--no-open"]) == 0
    sql = (tmp_path / "d" / "dossier" / "relire.sql").read_text()
    con = duckdb.connect()
    try:
        con.execute("INSTALL iceberg; LOAD iceberg;")
    except Exception as e:  # hors ligne : l'extension ne peut pas être téléchargée
        pytest.skip(f"extension iceberg indisponible : {e}")
    counts = {}
    for line in sql.splitlines():
        if line.startswith("SELECT"):
            name, n = con.execute(line.rstrip(";")).fetchone()
            counts[name] = n
    assert counts["distrib.stores"] == 300 and counts["distrib.tickets"] == 1000


def test_table_vide_et_fuseau_non_utc(tmp_path):
    """Régressions trouvées sur un vrai compte Snowflake : table vide sans lot Arrow, TIMESTAMP_TZ en fuseau nommé."""
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
    """La démo par défaut est TPC-H (si l'extension peut se télécharger) : 8 tables, et l'écart de nanosecondes est signalé."""
    import duckdb
    import pytest

    try:
        duckdb.connect().execute("install tpch")
    except Exception:
        pytest.skip("extension tpch indisponible (hors ligne)")
    from canot.cli import main

    assert main(["demo", "--out", str(tmp_path / "d"), "--sf", "0.01", "--no-open"]) in (0, 1)
    html = (tmp_path / "d" / "dossier" / "report.html").read_text()
    assert "TPC-H" in html or "lineitem" in html.lower()
    assert "TIMESTAMP_NS" in html
