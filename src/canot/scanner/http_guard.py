"""Read-only guard for HTTP, the counterpart of guard.py: every request of an API connector goes through here before it leaves.

Allowed: GET and HEAD; POST only on the allowlist of read paths that the connector declares
(`Connector.http_post_lecture`, regex patterns of the URL path). Everything else raises NotReadOnly, and nothing is sent.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from urllib.parse import unquote, urlparse

from .guard import NotReadOnly

LECTURE = ("GET", "HEAD")
# headers that make some servers treat a request as another one
SURCHARGES = ("x-http-method-override", "x-http-method", "x-method-override")


class HttpGuard:
    def __init__(self, transport: Callable[..., object], *, post_lecture: Iterable[str] = (), get_chemins: Iterable[str] | None = None):
        """transport(methode, url, **kw): the actual client (requests, urllib…), called only after the check.
        get_chemins: if provided, GET and HEAD are also limited to these patterns."""
        self.transport = transport
        self.post = tuple(re.compile(p) for p in post_lecture)
        self.get = None if get_chemins is None else tuple(re.compile(p) for p in get_chemins)

    @classmethod
    def pour(cls, connecteur, transport: Callable[..., object], **kw) -> HttpGuard:
        return cls(transport, post_lecture=getattr(connecteur, "http_post_lecture", ()), **kw)

    def check(self, methode: str, url: str, headers: dict | None = None) -> str:
        """Returns the normalized method if the request is an allowed read, otherwise raises NotReadOnly."""
        m = str(methode or "").strip().upper()
        for k, v in (headers or {}).items():
            if str(k).lower() in SURCHARGES and str(v).strip().upper() not in LECTURE:
                raise NotReadOnly(f"method override refused: {k}")
        chemin = unquote(urlparse(url).path or "/")
        if any(seg == ".." for seg in chemin.split("/")):
            raise NotReadOnly("path with \"..\" refused")
        if m in LECTURE:
            if self.get is not None and not any(p.fullmatch(chemin) for p in self.get):
                raise NotReadOnly(f"{m} not in the list of read paths: {chemin}")
            return m
        if m == "POST":
            if any(p.fullmatch(chemin) for p in self.post):
                return m
            raise NotReadOnly(f"POST outside the read allowlist: {chemin}")
        raise NotReadOnly(f"method refused: {m or 'empty'}")

    def request(self, methode: str, url: str, **kw):
        m = self.check(methode, url, kw.get("headers"))
        return self.transport(m, url, **kw)
