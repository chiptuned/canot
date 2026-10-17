"""Arrivée sur site : un dossier monté (comme un NAS) et un stockage compatible S3 local (moto), sans cloud."""
import os
import socket
from pathlib import Path

import pytest

from canot.demo import DEMO_QUERIES, make_demo
from canot.drill import run_drill
from canot.evidence import verify_dir
from canot.sources.duckdb_src import DuckDBSource
from canot.target import parse_target
from canot.tolerances import Tolerances


@pytest.fixture()
def demo(tmp_path: Path):
    db = tmp_path / "demo.duckdb"
    make_demo(str(db), 1_000)
    q = tmp_path / "q.sql"
    q.write_text(DEMO_QUERIES)
    return db, q


def _run(src, target, tmp_path, q, name):
    out = tmp_path / name
    return run_drill(src, target, out, tmp_path / "k.key", str(q), Tolerances(), log=lambda *_: None), out


def test_mounted_folder_with_spaces_like_a_nas(demo, tmp_path):
    db, q = demo
    nas = tmp_path / "Mon NAS" / "sauvegarde canot"
    res, out = _run(DuckDBSource(str(db)), parse_target(str(nas), None, tmp_path / "w1"), tmp_path, q, "o1")
    assert res["summary"]["tables_ok"] == 3 and verify_dir(out)[0]
    assert (nas / "warehouse").exists()
    parse_target(str(nas), None, tmp_path / "w1").teardown()
    assert not (nas / "warehouse").exists()


def test_custom_catalog_uri(demo, tmp_path):
    db, q = demo
    cat = tmp_path / "ailleurs" / "catalogue.db"
    cat.parent.mkdir()
    res, _ = _run(DuckDBSource(str(db)), parse_target(str(tmp_path / "lac"), None, tmp_path / "w2", f"sqlite:///{cat}"), tmp_path, q, "o2")
    assert res["summary"]["tables_ok"] == 3 and cat.exists()


def test_catalog_from_env_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("CANOT_CATALOG_URI", "sqlite:///x.db")
    assert parse_target(str(tmp_path / "l"), None, tmp_path, "env:CANOT_CATALOG_URI").catalog_uri == "sqlite:///x.db"
    monkeypatch.delenv("CANOT_CATALOG_URI")
    with pytest.raises(ValueError):
        parse_target(str(tmp_path / "l"), None, tmp_path, "env:CANOT_CATALOG_URI")


def test_s3_compatible_on_prem_object_store(demo, tmp_path, monkeypatch):
    moto_server = pytest.importorskip("moto.server")
    boto3 = pytest.importorskip("boto3")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    try:
        monkeypatch.setenv("CANOT_S3_ACCESS_KEY", "test")
        monkeypatch.setenv("CANOT_S3_SECRET_KEY", "test")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
        ep = f"http://127.0.0.1:{port}"
        boto3.client("s3", endpoint_url=ep, region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test").create_bucket(Bucket="coffre")
        db, q = demo
        spec = f"s3://coffre/canot?endpoint={ep}&region=us-east-1"
        res, out = _run(DuckDBSource(str(db)), parse_target(spec, None, tmp_path / "w3"), tmp_path, q, "o3")
        assert res["summary"]["tables_ok"] == 3, res["summary"]
        objs = boto3.client("s3", endpoint_url=ep, region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test").list_objects_v2(Bucket="coffre", Prefix="canot")
        assert objs["KeyCount"] > 5
        assert verify_dir(out)[0]
        msg = parse_target(spec, None, tmp_path / "w3").teardown()
        assert "vidé" in msg
    finally:
        server.stop()
