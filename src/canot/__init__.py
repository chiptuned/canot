from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("canot")  # one source of truth: pyproject.toml
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0+unknown"
