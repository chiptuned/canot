"""Iceberg targets: a local folder, or any S3-compatible object storage (French and EU hosts included)."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# provider -> (endpoint template, label). Check each endpoint against your provider's console before use.
PROVIDERS: dict[str, tuple[str, str]] = {
    "scaleway": ("https://s3.{region}.scw.cloud", "Scaleway (FR, NL, PL)"),
    "ovh": ("https://s3.{region}.io.cloud.ovh.net", "OVHcloud (FR, DE, ...)"),
    "outscale": ("https://oos.{region}.outscale.com", "Outscale (FR)"),
    "ionos": ("https://s3-{region}.ionoscloud.com", "IONOS (DE, ES, FR)"),
    "stackit": ("https://object.storage.{region}.onstackit.cloud", "STACKIT (DE)"),
    "aws": ("https://s3.{region}.amazonaws.com", "AWS"),
}


@dataclass
class Target:
    kind: str  # local | s3
    label: str
    warehouse: str
    props: dict[str, str] = field(default_factory=dict)
    workdir: Path = Path(".")

    def catalog(self):
        from pyiceberg.catalog.sql import SqlCatalog

        self.workdir.mkdir(parents=True, exist_ok=True)
        props = {"uri": f"sqlite:///{self.workdir / 'catalog.db'}", "warehouse": self.warehouse, **self.props}
        return SqlCatalog("canot", **props)

    def teardown(self) -> str:
        if self.kind == "local":
            p = Path(self.warehouse.replace("file://", ""))
            if p.exists():
                shutil.rmtree(p)
            return f"supprimé : {p}"
        from pyarrow import fs

        u = urlparse(self.warehouse)
        s3 = fs.S3FileSystem(
            endpoint_override=self.props.get("s3.endpoint"),
            region=self.props.get("s3.region"),
            access_key=self.props.get("s3.access-key-id"),
            secret_key=self.props.get("s3.secret-access-key"),
        )
        path = f"{u.netloc}{u.path}".rstrip("/")
        s3.delete_dir_contents(path, missing_dir_ok=True)
        return f"vidé : s3://{path}"


def _scw_config() -> tuple[str | None, str | None]:
    """Clés de la CLI Scaleway (`scw init`) : ~/.config/scw/config.yaml, profil par défaut."""
    path = Path(os.environ.get("SCW_CONFIG_PATH", Path.home() / ".config" / "scw" / "config.yaml"))
    ak = sk = None
    try:
        for line in path.read_text().splitlines():
            if line.startswith((" ", "\t")) or ":" not in line:
                if line.strip().startswith("profiles:"):
                    break
                continue
            k, v = (x.strip().strip("'\"") for x in line.split(":", 1))
            if k == "access_key":
                ak = v
            elif k == "secret_key":
                sk = v
    except OSError:
        pass
    return ak, sk


def _creds() -> dict[str, str]:
    cak, csk = _scw_config()
    ak = os.environ.get("CANOT_S3_ACCESS_KEY") or os.environ.get("SCW_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or cak
    sk = os.environ.get("CANOT_S3_SECRET_KEY") or os.environ.get("SCW_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or csk
    out = {}
    if ak and sk:
        out["s3.access-key-id"], out["s3.secret-access-key"] = ak, sk
    return out


def parse_target(spec: str, bucket: str | None, workdir: Path) -> Target:
    if "://" not in spec and ":" in spec and spec.split(":", 1)[0] in PROVIDERS:
        provider, region = spec.split(":", 1)
        if not bucket:
            raise ValueError(f"--bucket est obligatoire pour la cible {spec!r}")
        endpoint = PROVIDERS[provider][0].format(region=region)
        props = {"s3.endpoint": endpoint, "s3.region": region, **_creds()}
        if not props.get("s3.access-key-id"):
            raise ValueError("Clés d'accès absentes : lancez `scw init`, ou définissez CANOT_S3_ACCESS_KEY et CANOT_S3_SECRET_KEY.")
        return Target("s3", f"{provider}:{region}/{bucket}", f"s3://{bucket}/canot", props, workdir)
    if spec.startswith("s3://"):
        u = urlparse(spec)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        props = {**_creds()}
        if "endpoint" in qs:
            props["s3.endpoint"] = qs["endpoint"]
        if "region" in qs:
            props["s3.region"] = qs["region"]
        return Target("s3", f"s3://{u.netloc}{u.path}", f"s3://{u.netloc}{u.path}".rstrip("/"), props, workdir)
    path = Path(spec.replace("file://", "")).expanduser().resolve()
    return Target("local", f"local:{path}", f"file://{path / 'warehouse'}", {}, workdir)
