"""render_html() and write_csv(): self-contained page with no script, §4 sections in order, 20-row lists, deterministic CSV."""
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
FORBIDDEN = ("savings", "waste", "wasted", "useless", "unused", "critical", "urgent", "easy", "guaranteed", "compliant", "certified",
             "row by row",
             # French equivalents: stay vigilant on leftovers
             "économie", "gaspillage", "inutile", "inutilisé", "critique", "facile", "garanti", "conforme", "certifié", "ligne par ligne")
SECTIONS = ("header", "scope", "costs", "movements", "stays-behind", "dependencies", "replayability", "ranges",
            "findings", "not-estimated", "method", "next")
ALL = ("snowflake", "bigquery", "degrade")


def result(name: str, sql_brut: bool = False) -> dict:
    r = analyze(json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8")))
    r["avec_sql_brut"] = sql_brut  # set by run.scan_full after the analysis
    return r


def text(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


class _Lists(HTMLParser):
    """Counts the data rows of each <table> and the <li> of each <ul>."""

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
    assert p.tables and max(p.tables) <= MAX_ROWS + 1  # + the header row
    assert max(p.lists) <= MAX_ROWS
    assert "… and 13 more in flags.csv." in page  # 33 F05 findings
    assert "more in tables.csv." in page and "more in reads.csv." in page
    assert sum(1 for d in r["drapeaux"] if d["code"] == "F05") == 33


def test_entete_diffusion_interne_et_encadres():
    page = render_html(result("snowflake"))
    head = page[page.index('id="header"'):page.index('id="scope"')]
    assert "Internal distribution" in head and "2026-10-17 10:00 UTC" in head and "enterprise" in head
    for k in ("CIO", "CFO", "CISO"):
        assert f"<h3>{k}</h3>" in head
    assert "up to 6 h behind" in head
    assert '<html lang="en">' in page


def test_bandeau_sql_brut_seulement_sur_demande():
    assert "Raw SQL text exported" in render_html(result("bigquery", sql_brut=True))
    assert "scan-sql-raw.json" in render_html(result("bigquery", sql_brut=True))
    assert "Raw SQL text exported" not in render_html(result("bigquery", sql_brut=False))


def test_constats_a_examiner_et_formulations_types():
    t = text(render_html(result("snowflake")))
    assert t.count("To review") >= 12
    assert ("No read observed in ACCESS_HISTORY from 2025-10-18 to 2026-10-17; "
            "blind spots: share consumers, RESULT_SCAN.") in t
    assert "accepted ≠ identical result" in t and "Translated to DuckDB, not compared." in t
    bq = text(render_html(result("bigquery")))
    assert "Not estimated: your contract price not read." in bq
    assert "do not add up" in bq


def test_degrade_affiche_non_lu_jamais_zero():
    t = text(render_html(result("degrade")))
    assert "Not read: METERING_DAILY_HISTORY not read: missing privilege." in t
    assert "An absence of findings only holds for the reads done" in t
    assert "0 credits" not in t and "0.00 credits" not in t


def test_methode_lectures_droits_cout_du_scan():
    t = text(render_html(result("degrade")))
    m = t[t.index("Method"):t.index("Next steps")]
    assert "ACCESS_HISTORY" in m and "privilege level not requested" in m and "SNOWFLAKE.OBJECT_VIEWER" in m
    assert "SNOWFLAKE.SECURITY_VIEWER" not in m.split("Never requested")[0]  # level 1
    assert "Queries sent" in m and "12 queries" in m and "Not read: duration not provided by collection." in m
    assert "SELECT, SHOW, DESCRIBE and ALTER SESSION SET" in m


def test_suite_et_lien_unique():
    page = render_html(result("snowflake"))
    s = text(page[page.index('id="next"'):])
    assert "This report describes your platform; it recommends neither leaving nor staying." in s
    assert s.index("canot drill") < s.index("€22,000 excl. VAT") < s.index("canot.io")


def test_imprimable_a4_et_theme_clair_sombre():
    assert "@page{size:A4" in CSS and "prefers-color-scheme:dark" in CSS and "@media print" in CSS


def test_csv_fichiers_et_contenu(tmp_path):
    r = result("snowflake")
    paths = write_csv(r, tmp_path)
    assert [p.name for p in paths] == ["tables.csv", "flags.csv", "families.csv", "reads.csv"]
    rows = {p.name: list(csv.DictReader(p.open(encoding="utf-8"))) for p in paths}
    assert len(rows["reads.csv"]) == len(r["lectures"]) and len(rows["tables.csv"]) == len(r["tables"])
    assert {x["status"] for x in rows["reads.csv"]} <= {"read", "not_read"}
    assert len(rows["flags.csv"]) == len(r["drapeaux"]) and {x["label"] for x in rows["flags.csv"]} == {"To review"}
    fam_cols = set(rows["families.csv"][0])
    assert "normalized_sql" in fam_cols and not {"text", "texte", "sql", "user", "utilisateur", "role"} & fam_cols
    legacy = next(x for x in rows["tables.csv"] if x["id"] == "CANOT_LAB.MART.DIM_LEGACY")
    assert legacy["reads_n"] == "" and legacy["movement"] == ""
    orders = next(x for x in rows["tables.csv"] if x["id"] == "CANOT_LAB.RAW.ORDERS")
    assert orders["movement"] == "live"


def test_csv_valeur_non_lue_vide_jamais_zero(tmp_path):
    rows = list(csv.DictReader(write_csv(result("degrade"), tmp_path)[0].open(encoding="utf-8")))
    assert all(x["active_bytes"] == "" and x["reads_n"] == "" for x in rows)


def _recent_result(n: int = 2) -> dict:
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    base = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_2")
    for i in range(n):
        s["tables"].append({**base, "id": f"CANOT_LAB.LAB.NEW_{i}", "nom": f"NEW_{i}", "flags": ["recente"], "octets_historique": None,
                            "n_lectures": None, "detail": {"source": "information_schema", "creee_le": "2026-10-17T09:30:00Z"}})
    s["fraicheur"] = {"recents_seulement_temps_reel": {"tables": n, "objets": 0}, "latence_max_h": 48,
                      "texte": f"{n} tables seen only in real time: missing from ACCOUNT_USAGE."}
    r = analyze(s)
    r["avec_sql_brut"] = False
    return r


def test_fraicheur_dans_le_perimetre_les_couts_et_les_mouvements():
    t = text(render_html(_recent_result()))
    per = t[t.index("Scope"):t.index("Observed costs")]
    assert "Freshness: 2 tables seen only in real time: missing from ACCOUNT_USAGE." in per
    couts = t[t.index("Observed costs"):t.index("Movements")]
    assert "2 recent tables with no history in ACCOUNT_USAGE" in couts
    mv = t[t.index("Movements"):t.index("What does not leave")]
    assert "2 tables seen only in real time: writes and reads not yet available in ACCOUNT_USAGE" in mv
    assert render_html(_recent_result()).encode() == render_html(_recent_result()).encode()
    for w in FORBIDDEN:
        assert w not in t.lower(), w
    plain = text(render_html(result("snowflake")))
    assert "Freshness" not in plain and "recent tables with no history" not in plain and "Empty inventory" not in plain


def test_inventaire_vide_avec_requetes_ou_credits_lus_le_dit():
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    s["tables"] = []
    r = analyze(s)
    r["avec_sql_brut"] = False
    t = text(render_html(r))
    per = t[t.index("Scope"):t.index("Observed costs")]
    assert ("Empty inventory although queries or credits were read: check the scanned database and the role's privileges "
            "on its objects") in per
    assert "Empty inventory" not in text(render_html(result("degrade")))
