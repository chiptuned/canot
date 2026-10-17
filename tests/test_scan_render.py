"""render_html() et write_csv() : page autonome sans script, sections du §4 dans l'ordre, listes de 20 lignes, CSV déterministes."""
import base64
import csv
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from canot.scanner.analyze import PRICE_SOURCES, analyze
from canot.scanner.render import CSS, MAX_ROWS, render_html, write_csv

FIX = Path(__file__).parent / "fixtures"
CANARI = "canari-7f3a@exemple.test"
FORBIDDEN = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti",
             "conforme", "certifié", "ligne par ligne")
SECTIONS = ("entete", "perimetre", "couts", "mouvements", "ne-part-pas", "dependances", "rejouabilite", "fourchettes",
            "constats", "non-estime", "methode", "suite")
ALL = ("snowflake", "bigquery", "degrade")


def result(name: str, sql_brut: bool = False) -> dict:
    r = analyze(json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8")))
    r["avec_sql_brut"] = sql_brut  # posé par run.scan_full après l'analyse
    return r


def text(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


class _Lists(HTMLParser):
    """Compte les lignes de données de chaque <table> et les <li> de chaque <ul>."""

    def __init__(self):
        super().__init__()
        self.tables, self.lists, self.stack = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("table", "ul"):
            self.stack.append([tag, 0])
        elif tag in ("tr", "li") and self.stack:
            self.stack[-1][1] += 1

    def handle_endtag(self, tag):
        if tag in ("table", "ul") and self.stack:
            kind, n = self.stack.pop()
            (self.tables if kind == "table" else self.lists).append(n)


@pytest.mark.parametrize("name", ALL)
def test_sections_dans_l_ordre_du_paragraphe_4(name):
    page = render_html(result(name))
    pos = [page.index(f'id="{s}"') for s in SECTIONS]
    assert pos == sorted(pos)


@pytest.mark.parametrize("name", ALL)
def test_autonome_sans_script_ni_ressource_externe(name):
    page = render_html(result(name, sql_brut=True))
    low = page.lower()
    for bad in ("<script", "<link", " src=", "@import", "url(", "<iframe", "<img", "<form", "javascript:"):
        assert bad not in low, bad
    digest = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    assert f"default-src 'none'; style-src 'sha256-{digest}'" in page
    assert f"<style>{CSS}</style>" in page


@pytest.mark.parametrize("name", ALL)
def test_seules_urls_canot_io_et_sources_de_prix(name):
    page = render_html(result(name))
    urls = {u.rstrip(".,;)") for u in re.findall(r"https?://[^\s\"'<>]+", html.unescape(page))}
    assert "https://canot.io" in urls
    assert urls <= {"https://canot.io", *PRICE_SOURCES}, urls
    assert page.count("<a ") == 1 and '<a href="https://canot.io">' in page


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("sql_brut", (False, True))
def test_aucun_mot_interdit_dans_le_html(name, sql_brut):
    page = text(render_html(result(name, sql_brut))).lower()
    for w in FORBIDDEN:
        assert w not in page, w


@pytest.mark.parametrize("name", ALL)
def test_canari_absent_du_html_et_des_csv(name, tmp_path):
    r = result(name, sql_brut=True)
    assert CANARI not in render_html(r)
    for p in write_csv(r, tmp_path):
        assert CANARI not in p.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ALL)
def test_rendu_deterministe_a_l_octet(name, tmp_path):
    assert render_html(result(name)).encode() == render_html(result(name)).encode()
    a = [p.read_bytes() for p in write_csv(result(name), tmp_path / "a")]
    b = [p.read_bytes() for p in write_csv(result(name), tmp_path / "b")]
    assert a == b


def test_listes_tronquees_a_20_lignes():
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    base = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_1")
    for i in range(30):
        s["tables"].append({**base, "id": f"CANOT_LAB.RAW.BULK_{i:02d}", "nom": f"BULK_{i:02d}",
                            "colonnes": [{"nom": "doc", "type": "VARIANT"}]})
    r = analyze(s)
    r["avec_sql_brut"] = False
    page = render_html(r)
    p = _Lists()
    p.feed(page)
    assert p.tables and max(p.tables) <= MAX_ROWS + 1  # + l'en-tête
    assert max(p.lists) <= MAX_ROWS
    assert "… et 13 autres dans drapeaux.csv." in page  # 33 constats F05
    assert "autres dans tables.csv." in page and "autres dans lectures.csv." in page
    assert sum(1 for d in r["drapeaux"] if d["code"] == "F05") == 33


def test_entete_diffusion_interne_et_encadres():
    page = render_html(result("snowflake"))
    head = page[page.index('id="entete"'):page.index('id="perimetre"')]
    assert "Diffusion interne" in head and "17/10/2026 à 10:00 UTC" in head and "enterprise" in head
    for k in ("DSI", "DAF", "RSSI"):
        assert f"<h3>{k}</h3>" in head
    assert "retard de 6 h au plus" in head


def test_bandeau_sql_brut_seulement_sur_demande():
    assert "Texte SQL brut exporté" in render_html(result("bigquery", sql_brut=True))
    assert "Texte SQL brut exporté" not in render_html(result("bigquery", sql_brut=False))


def test_constats_a_examiner_et_formulations_types():
    t = text(render_html(result("snowflake")))
    assert t.count("À examiner") >= 12
    assert ("Aucune lecture observée dans ACCESS_HISTORY du 18/10/2025 au 17/10/2026 ; "
            "non vus : consommateurs de partages, RESULT_SCAN.") in t
    assert "accepté ≠ résultat identique" in t and "Traduit vers DuckDB, non comparé." in t
    bq = text(render_html(result("bigquery")))
    assert "Non estimé : prix de votre contrat non lu." in bq
    assert "ne s'additionnent pas" in bq


def test_degrade_affiche_non_lu_jamais_zero():
    t = text(render_html(result("degrade")))
    assert "Non lu : METERING_DAILY_HISTORY non lue : droit absent." in t
    assert "Une absence de constat ne vaut que pour les lectures faites" in t
    assert "0 crédits" not in t


def test_methode_lectures_droits_cout_du_scan():
    t = text(render_html(result("degrade")))
    m = t[t.index("Méthode"):t.index("Suite")]
    assert "ACCESS_HISTORY" in m and "niveau de droits non demandé" in m and "SNOWFLAKE.OBJECT_VIEWER" in m
    assert "SNOWFLAKE.SECURITY_VIEWER" not in m.split("Jamais demandés")[0]  # niveau 1
    assert "Requêtes émises" in m and "12 requêtes" in m and "Non lu : durée non transmise par la collecte." in m
    assert "SELECT, SHOW, DESCRIBE et ALTER SESSION SET" in m


def test_suite_et_lien_unique():
    page = render_html(result("snowflake"))
    s = text(page[page.index('id="suite"'):])
    assert "Ce rapport décrit votre plateforme ; il ne recommande ni de partir ni de rester." in s
    assert s.index("canot drill") < s.index("22 000 € HT") < s.index("canot.io")


def test_imprimable_a4_et_theme_clair_sombre():
    assert "@page{size:A4" in CSS and "prefers-color-scheme:dark" in CSS and "@media print" in CSS


def test_csv_fichiers_et_contenu(tmp_path):
    r = result("snowflake")
    paths = write_csv(r, tmp_path)
    assert [p.name for p in paths] == ["tables.csv", "drapeaux.csv", "familles.csv", "lectures.csv"]
    rows = {p.name: list(csv.DictReader(p.open(encoding="utf-8"))) for p in paths}
    assert len(rows["lectures.csv"]) == len(r["lectures"]) and len(rows["tables.csv"]) == len(r["tables"])
    assert len(rows["drapeaux.csv"]) == len(r["drapeaux"]) and {x["etiquette"] for x in rows["drapeaux.csv"]} == {"À examiner"}
    fam_cols = set(rows["familles.csv"][0])
    assert "sql_normalise" in fam_cols and not {"texte", "sql", "utilisateur", "role"} & fam_cols
    legacy = next(x for x in rows["tables.csv"] if x["id"] == "CANOT_LAB.MART.DIM_LEGACY")
    assert legacy["n_lectures"] == "" and legacy["mouvement"] == ""


def test_csv_valeur_non_lue_vide_jamais_zero(tmp_path):
    rows = list(csv.DictReader(write_csv(result("degrade"), tmp_path)[0].open(encoding="utf-8")))
    assert all(x["octets_actifs"] == "" and x["n_lectures"] == "" for x in rows)


def _recent_result(n: int = 2) -> dict:
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    base = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_2")
    for i in range(n):
        s["tables"].append({**base, "id": f"CANOT_LAB.LAB.NEW_{i}", "nom": f"NEW_{i}", "flags": ["recente"], "octets_historique": None,
                            "n_lectures": None, "detail": {"source": "information_schema", "creee_le": "2026-10-17T09:30:00Z"}})
    s["fraicheur"] = {"recents_seulement_temps_reel": {"tables": n, "objets": 0}, "latence_max_h": 48,
                      "texte": f"{n} tables vues seulement en temps réel : absentes d'ACCOUNT_USAGE."}
    r = analyze(s)
    r["avec_sql_brut"] = False
    return r


def test_fraicheur_dans_le_perimetre_les_couts_et_les_mouvements():
    t = text(render_html(_recent_result()))
    per = t[t.index("Périmètre"):t.index("Coûts observés")]
    assert "Fraîcheur : 2 tables vues seulement en temps réel : absentes d'ACCOUNT_USAGE." in per
    couts = t[t.index("Coûts observés"):t.index("Mouvements")]
    assert "2 tables récentes sans historique dans ACCOUNT_USAGE" in couts
    mv = t[t.index("Mouvements"):t.index("Ce qui ne part pas")]
    assert "2 tables vues seulement en temps réel : écritures et lectures pas encore disponibles dans ACCOUNT_USAGE" in mv
    assert render_html(_recent_result()).encode() == render_html(_recent_result()).encode()
    for w in FORBIDDEN:
        assert w not in t.lower(), w
    plain = text(render_html(result("snowflake")))
    assert "Fraîcheur" not in plain and "récentes sans historique" not in plain and "Inventaire vide" not in plain


def test_inventaire_vide_avec_requetes_ou_credits_lus_le_dit():
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    s["tables"] = []
    r = analyze(s)
    r["avec_sql_brut"] = False
    t = text(render_html(r))
    per = t[t.index("Périmètre"):t.index("Coûts observés")]
    assert ("Inventaire vide alors que des requêtes ou des crédits ont été lus : vérifier la base scannée et les droits du rôle "
            "sur ses objets") in per
    assert "Inventaire vide" not in text(render_html(result("degrade")))
