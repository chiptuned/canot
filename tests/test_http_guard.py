"""Read-only HTTP guard: GET/HEAD allowed, POST only on the connector allowlist, nothing is sent on refusal."""
import pytest

from canot import connectors
from canot.scanner.guard import NotReadOnly
from canot.scanner.http_guard import HttpGuard

BASE = "https://dss.exemple.test:11200"


class Transport:
    def __init__(self):
        self.appels = []

    def __call__(self, methode, url, **kw):
        self.appels.append((methode, url))
        return {"ok": True}


def garde(**kw):
    t = Transport()
    return HttpGuard(t, post_lecture=(r"/public/api/projects/[^/]+/datasets/[^/]+/schema/lire",), **kw), t


@pytest.mark.parametrize("methode", ["PUT", "PATCH", "DELETE", "put", "OPTIONS", "TRACE", "CONNECT", ""])
def test_ecritures_et_methodes_inconnues_refusees_sans_envoi(methode):
    g, t = garde()
    with pytest.raises(NotReadOnly):
        g.request(methode, f"{BASE}/public/api/projects/")
    assert t.appels == []


def test_post_hors_liste_blanche_refuse_sans_envoi():
    g, t = garde()
    for url in (f"{BASE}/public/api/projects/A/export", f"{BASE}/public/api/projects/A/datasets/D/schema/lire/plus",
                f"{BASE}/public/api/projects/A/datasets/D/schema/lire/../../../export"):
        with pytest.raises(NotReadOnly):
            g.request("POST", url)
    assert t.appels == []


def test_lectures_admises():
    g, t = garde()
    g.request("get", f"{BASE}/public/api/projects/?x=1")
    g.request("HEAD", f"{BASE}/public/api/projects/A/recipes/")
    g.request("POST", f"{BASE}/public/api/projects/A/datasets/D/schema/lire")
    assert [m for m, _ in t.appels] == ["GET", "HEAD", "POST"]


def test_surcharge_de_methode_et_chemins_get_limites():
    g, t = garde(get_chemins=(r"/public/api/projects/", r"/public/api/projects/[^/]+/recipes/"))
    with pytest.raises(NotReadOnly, match=r"(?i)override|surcharge"):  # message owned by scanner/http_guard.py
        g.request("GET", f"{BASE}/public/api/projects/", headers={"X-HTTP-Method-Override": "DELETE"})
    with pytest.raises(NotReadOnly, match=r"(?i)allow ?list|not (in|on) the list|hors de la liste"):
        g.request("GET", f"{BASE}/public/api/projects/A/datasets/D/data")
    with pytest.raises(NotReadOnly, match=r"\.\."):
        g.request("GET", f"{BASE}/public/api/projects/A/recipes/%2e%2e/export")
    assert t.appels == []
    g.request("GET", f"{BASE}/public/api/projects/A/recipes/")
    assert len(t.appels) == 1


def test_liste_blanche_declaree_par_le_connecteur(monkeypatch):
    class Api(connectors.Connector):
        name, label, schemes, dialect, url = "api", "Api", ("api",), "api", "api://"
        capabilities = connectors.Capabilities("partiel")
        http_post_lecture = (r"/v1/scan/getInfo",)

    t = Transport()
    g = HttpGuard.pour(Api(), t)
    g.request("POST", "https://x.test/v1/scan/getInfo")
    with pytest.raises(NotReadOnly):
        g.request("POST", "https://x.test/v1/scan/delete")
    assert t.appels == [("POST", "https://x.test/v1/scan/getInfo")]
    assert all(c.http_post_lecture == () for c in connectors.CONNECTORS.values())  # no current connector declares a POST
