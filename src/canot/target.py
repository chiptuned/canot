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


def file_uri(p: Path) -> str:
    """file:// URI of a local folder. On Windows: forward slashes (file://C:/...), the form PyIceberg turns back into C:/...
    (file:///C:/... would become /C:/..., a path Windows rejects) and that the Iceberg metadata can store portably."""
    return "file://" + p.as_posix() if os.name == "nt" else f"file://{p}"


def local_path(uri: str) -> Path:
    """Inverse of file_uri (also accepts a plain path)."""
    rest = uri[len("file://"):] if uri.startswith("file://") else uri
    if len(rest) > 2 and rest[0] == "/" and rest[2] == ":":  # /C:/Users/...
        rest = rest[1:]
    return Path(rest)


@dataclass
class Target:
    kind: str  # local | s3
    label: str
    warehouse: str
    props: dict[str, str] = field(default_factory=dict)
    workdir: Path = Path(".")
    catalog_uri: str | None = None  # default: a SQLite file in the working folder

    def catalog(self):
        from pyiceberg.catalog.sql import SqlCatalog

        self.workdir.mkdir(parents=True, exist_ok=True)
        props = {"uri": self.catalog_uri or f"sqlite:///{self.workdir / 'catalog.db'}", "warehouse": self.warehouse, **self.props}
        return SqlCatalog("canot", **props)

    def teardown(self) -> str:
        if self.kind == "local":
            p = local_path(self.warehouse)
            if p.exists():
                shutil.rmtree(p)
            return f"deleted: {p}"
        from pyarrow import fs

        u = urlparse(self.warehouse)
        s3 = fs.S3FileSystem(
            endpoint_override=self.props.get("s3.endpoint"),
            proxy_options=self.props.get("s3.proxy-uri"),
            region=self.props.get("s3.region"),
            access_key=self.props.get("s3.access-key-id"),
            secret_key=self.props.get("s3.secret-access-key"),
        )
        path = f"{u.netloc}{u.path}".rstrip("/")
        s3.delete_dir_contents(path, missing_dir_ok=True)
        return f"emptied: s3://{path}"


def _scw_config() -> tuple[str | None, str | None]:
    """Keys from the Scaleway CLI (`scw init`): ~/.config/scw/config.yaml.
    Default profile, or the profile named by SCW_PROFILE (see `scw init profile=NAME`)."""
    path = Path(os.environ.get("SCW_CONFIG_PATH", Path.home() / ".config" / "scw" / "config.yaml"))
    wanted = os.environ.get("SCW_PROFILE")
    ak = sk = None
    section = None  # None = top of the file; otherwise the current profile name
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip())
            line = raw.strip()
            if indent == 0:
                section = "__profiles__" if line.startswith("profiles:") else None
                if section is None and not wanted and ":" in line:
                    k, v = (x.strip().strip("'\"") for x in line.split(":", 1))
                    ak, sk = (v if k == "access_key" else ak), (v if k == "secret_key" else sk)
                continue
            if section == "__profiles__" and indent <= 2 and line.endswith(":"):
                section = line[:-1]
                continue
            if wanted and section == wanted and ":" in line:
                k, v = (x.strip().strip("'\"") for x in line.split(":", 1))
                ak, sk = (v if k == "access_key" else ak), (v if k == "secret_key" else sk)
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


def _catalog_uri(value: str | None) -> str | None:
    """--catalog postgresql://... or env:NAME: the second form keeps a password off the command line."""
    if not value:
        return None
    if value.startswith("env:"):
        got = os.environ.get(value[4:])
        if not got:
            raise ValueError(f"Environment variable empty or missing: {value[4:]}")
        return got
    return value


def parse_target(spec: str, bucket: str | None, workdir: Path, catalog: str | None = None) -> Target:
    uri = _catalog_uri(catalog)
    if "://" not in spec and ":" in spec and spec.split(":", 1)[0] in PROVIDERS:
        provider, region = spec.split(":", 1)
        if not bucket:
            raise ValueError(f"--bucket is required for the target {spec!r}")
        endpoint = PROVIDERS[provider][0].format(region=region)
        props = {"s3.endpoint": endpoint, "s3.region": region, **_creds()}
        if not props.get("s3.access-key-id"):
            raise ValueError("Access keys missing: run `scw init`, or set CANOT_S3_ACCESS_KEY and CANOT_S3_SECRET_KEY.")
        return Target("s3", f"{provider}:{region}/{bucket}", f"s3://{bucket}/canot", props, workdir, uri)
    if spec.startswith("s3://"):
        u = urlparse(spec)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        props = {**_creds()}
        if "endpoint" in qs:
            props["s3.endpoint"] = qs["endpoint"]
        if "region" in qs:
            props["s3.region"] = qs["region"]
        if "proxy" in qs:  # corporate proxy
            props["s3.proxy-uri"] = qs["proxy"]
        if "ca" in qs:  # internal certificate authority: passed to the AWS SDK through its usual environment variables
            os.environ.setdefault("AWS_CA_BUNDLE", qs["ca"])
            os.environ.setdefault("SSL_CERT_FILE", qs["ca"])
        return Target("s3", f"s3://{u.netloc}{u.path}", f"s3://{u.netloc}{u.path}".rstrip("/"), props, workdir, uri)
    path = Path(spec.replace("file://", "")).expanduser().resolve()
    return Target("local", f"local:{path}", file_uri(path / "warehouse"), {}, workdir, uri)
