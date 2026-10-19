"""Garde lecture seule pour HTTP, symétrique de guard.py : toute requête d'un connecteur d'API passe ici avant de partir.

Admis : GET et HEAD ; POST seulement sur la liste blanche des chemins de lecture que le connecteur déclare
(`Connector.http_post_lecture`, motifs regex du chemin d'URL). Tout le reste lève NotReadOnly, et rien n'est envoyé.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from urllib.parse import unquote, urlparse

from .guard import NotReadOnly

LECTURE = ("GET", "HEAD")
# en-têtes qui font passer une requête pour une autre auprès de certains serveurs
SURCHARGES = ("x-http-method-override", "x-http-method", "x-method-override")


class HttpGuard:
    def __init__(self, transport: Callable[..., object], *, post_lecture: Iterable[str] = (), get_chemins: Iterable[str] | None = None):
        """transport(methode, url, **kw) : le client réel (requests, urllib…), appelé seulement après contrôle.
        get_chemins : si fourni, GET et HEAD sont aussi limités à ces motifs."""
        self.transport = transport
        self.post = tuple(re.compile(p) for p in post_lecture)
        self.get = None if get_chemins is None else tuple(re.compile(p) for p in get_chemins)

    @classmethod
    def pour(cls, connecteur, transport: Callable[..., object], **kw) -> HttpGuard:
        return cls(transport, post_lecture=getattr(connecteur, "http_post_lecture", ()), **kw)

    def check(self, methode: str, url: str, headers: dict | None = None) -> str:
        """Renvoie la méthode normalisée si la requête est une lecture admise, sinon lève NotReadOnly."""
        m = str(methode or "").strip().upper()
        for k, v in (headers or {}).items():
            if str(k).lower() in SURCHARGES and str(v).strip().upper() not in LECTURE:
                raise NotReadOnly(f"surcharge de méthode refusée : {k}")
        chemin = unquote(urlparse(url).path or "/")
        if any(seg == ".." for seg in chemin.split("/")):
            raise NotReadOnly("chemin avec « .. » refusé")
        if m in LECTURE:
            if self.get is not None and not any(p.fullmatch(chemin) for p in self.get):
                raise NotReadOnly(f"{m} hors de la liste des chemins de lecture : {chemin}")
            return m
        if m == "POST":
            if any(p.fullmatch(chemin) for p in self.post):
                return m
            raise NotReadOnly(f"POST hors de la liste blanche de lecture : {chemin}")
        raise NotReadOnly(f"méthode refusée : {m or 'vide'}")

    def request(self, methode: str, url: str, **kw):
        m = self.check(methode, url, kw.get("headers"))
        return self.transport(m, url, **kw)
