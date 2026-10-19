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

from canot import brand
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


def result(name: str, sql_brut: bool = False, lang: str = "en") -> dict:
    r = analyze(json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8")), lang=lang)
    r["avec_sql_brut"] = sql_brut  # set by run.scan_full after the analysis
    return r


def page_of(name: str, sql_brut: bool = False, lang: str = "en") -> str:
    """The page as scan_full writes it: English analysis, French analysis for the French pane."""
    return render_html(result(name, sql_brut), result(name, sql_brut, "fr"), lang=lang)


def pane(page: str, lang: str) -> str:
    """The HTML of one language pane."""
    start = page.index(f'id="lang-{lang}"')
    end = page.index('<div class="pane', start) if page.find('<div class="pane', start) > 0 else page.rindex("</body>")
    return page[start:end]


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
@pytest.mark.parametrize("lang", ("en", "fr"))
def test_sections_dans_l_ordre_du_paragraphe_4(name, lang):
    p = pane(page_of(name), lang)
    pos = [p.index(f'id="{s}-{lang}"') for s in SECTIONS]
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
    assert urls <= {"https://canot.io", brand.REVIEW_URL, brand.REVIEW_URL_FR, *PRICE_SOURCES}, urls
    # three links: canot.io, the shareable-summary download (data: URI, nothing leaves the page), the review page on canot.io
    # per pane, three links: canot.io, the shareable-summary download (data: URI, nothing leaves the page), the review page on canot.io;
    # plus the two links of the language bar (in-page anchors)
    for lang in ("en", "fr"):
        p = pane(page, lang)
        assert p.count("<a ") == 3 and '<a href="https://canot.io">' in p and p.count('download="scan-share.json"') == 1
    assert page.count("<a ") == 8 and '<a href="#lang-en"' in page and '<a href="#lang-fr"' in page and "<script" not in page


@pytest.mark.parametrize("name", ALL)
def test_le_resume_partageable_embarque_ne_contient_que_des_comptes(name):
    import base64
    import json

    page = render_html(result(name, sql_brut=True))
    m = re.search(r'href="data:application/json;base64,([A-Za-z0-9+/=]+)"', page)
    payload = json.loads(base64.b64decode(m.group(1)))
    assert payload["schema"] == "canot.share/1"
    raw = json.dumps(payload)
    assert CANARI not in raw
    for t in result(name)["tables"]:
        assert t["nom"] not in raw or len(t["nom"]) < 4  # no table names (very short names may coincide with a word)
    assert "Canot" in page and "<svg" in page and "Export the shareable summary" in page and "Get an expert review" in page


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
    page = page_of("snowflake")
    head = page[page.index('id="header-en"'):page.index('id="scope-en"')]
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
    page = page_of("snowflake")
    s = text(pane(page, "en")[pane(page, "en").index('id="next-en"'):])
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


# --- bilingual page: English and French panes ------------------------------------------------------------------------------

# English words that never belong in a French sentence of the report (object names, SQL and platform terms are not in this list)
ENGLISH = ("the", "and", "with", "not", "of", "to", "is", "are", "from", "which", "this", "that", "by", "read", "only", "for", "per",
           "none", "no", "missing", "privilege", "level", "requested", "reads", "write", "writes", "table seen", "rows", "days", "window",
           "billed", "query", "queries", "families", "objects", "finding", "findings", "review", "estimated", "measured", "computed")


def _francais(name: str) -> str:
    """Text of the French pane outside code, the regulatory table (regulatory.py) and the share block (brand.py)."""
    p = pane(page_of(name, sql_brut=True), "fr")
    p = re.sub(r"<code>.*?</code>", " ", p, flags=re.S)
    p = re.sub(r'<section id="regulatory[^"]*">.*?</section>', " ", p, flags=re.S)
    p = re.sub(r'<div class="share">.*?</div></div>|<div class="share">.*?</p></div>', " ", p, flags=re.S)
    return text(p)


@pytest.mark.parametrize("name", ALL)
def test_deux_panneaux_complets_meme_structure(name):
    page = page_of(name)
    en, fr = pane(page, "en"), pane(page, "fr")
    struct = [[i for i in re.findall(r'id="([a-z-]+)-' + lang + '"', p) if i != "regulatory"] for lang, p in (("en", en), ("fr", fr))]
    assert struct[0] == struct[1] and set(SECTIONS) <= set(struct[0])
    assert 'id="regulatory"' in en and 'id="regulatory-fr"' in fr  # ids given by regulatory.section, one per language
    for tag in ("<h2>", "<h3>", "<table>", "<li>"):
        assert en.count(tag) == fr.count(tag), tag
    assert 'lang="en"' in en[:60] and 'lang="fr"' in fr[:60]


@pytest.mark.parametrize("name", ALL + ("dataiku_synthetique",))
def test_panneau_francais_sans_phrase_anglaise(name):
    # identifiers (names with _ . / - : @) are one token, never an English word
    words = {w for w in re.findall(r"[\w.@/:'-]+", _francais(name)) if re.fullmatch(r"[A-Za-z]+", w)}
    assert not words & set(ENGLISH), sorted(words & set(ENGLISH))
    t = _francais(name)
    for fr in ("À examiner", "Périmètre", "Méthode", "Suite", "Constats", "Fourchettes", "dès 22\u00a0000\u00a0€ HT"):
        assert fr in t, fr


def test_panneau_francais_termes_et_formats():
    t = _francais("snowflake")
    for k in ("DSI", "DAF", "RSSI"):
        assert f" {k} " in f" {t} "
    assert "accepté ≠ résultat identique" in t and "Traduit vers DuckDB, non comparé." in t
    assert "19,4 Go" in t and "43,50 crédits" in t and "113,10\u00a0$ à 226,20\u00a0$" in t and "66,7\u00a0%" in t
    assert "mesuré" in t and "calculé" in t and "estimé, confiance basse" in t and "non estimé" in t
    assert "angles morts : consommateurs des partages, RESULT_SCAN" in t and "dossier de preuve" in t
    dg = _francais("degrade")
    assert "Non lu : METERING_DAILY_HISTORY non lu : droit absent." in dg and "niveau de droits non demandé" in dg
    assert "lecture seule" in _francais("bigquery")


@pytest.mark.parametrize("name", ALL + ("dataiku_synthetique",))
@pytest.mark.parametrize("sql_brut", (False, True))
def test_aucun_mot_interdit_dans_les_deux_panneaux(name, sql_brut):
    snap = json.loads((FIX / f"scan_{name}.json").read_text(encoding="utf-8"))
    r, f = analyze(snap), analyze(snap, lang="fr")
    r["avec_sql_brut"] = f["avec_sql_brut"] = sql_brut
    page = render_html(r, f)
    for lang in ("en", "fr"):
        t = text(pane(page, lang)).lower()
        for w in FORBIDDEN:
            assert w not in t, (lang, w)


@pytest.mark.parametrize("name", ALL)
@pytest.mark.parametrize("lang", ("en", "fr"))
def test_bilingue_deterministe_sans_script_ids_uniques(name, lang):
    a, b = page_of(name, lang=lang), page_of(name, lang=lang)
    assert a.encode() == b.encode()
    low = a.lower()
    for bad in ("<script", "<link", " src=", "@import", "url(", "<iframe", "<img", "<form", "javascript:", " on"):
        assert bad + ("click" if bad == " on" else "") not in low
    ids = re.findall(r'\bid="([^"]+)"', a)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    assert a.count("<style>") == 1 and a.count('class="brandbar"') == 1 and a.count("<svg") == 2


def test_bascule_de_langue_par_ancre_sans_script():
    en_first, fr_first = page_of("snowflake", lang="en"), page_of("snowflake", lang="fr")
    bar = '<nav class="langbar" aria-label="Language / Langue"><a href="#lang-en" hreflang="en">English</a><a href="#lang-fr" hreflang="fr">Français</a></nav>'
    assert bar in en_first and bar in fr_first
    # the pane shown first is the "main" one, after the alternative one (CSS :target, no :has, no script)
    assert '<div class="pane alt" id="lang-fr" lang="fr">' in en_first and '<div class="pane main" id="lang-en" lang="en">' in en_first
    assert '<div class="pane alt" id="lang-en" lang="en">' in fr_first and '<div class="pane main" id="lang-fr" lang="fr">' in fr_first
    assert '<html lang="en">' in en_first and '<html lang="fr">' in fr_first and "<title>Canot, scan de sortie</title>" in fr_first
    assert ".pane.alt:target ~ .pane.main{display:none}" in CSS


@pytest.mark.parametrize("name", ALL)
def test_canari_absent_des_deux_panneaux(name):
    page = page_of(name, sql_brut=True)
    for lang in ("en", "fr"):
        assert CANARI not in pane(page, lang)
    m = re.findall(r'href="data:application/json;base64,([A-Za-z0-9+/=]+)"', page)
    assert len(m) == 2 and m[0] == m[1]  # the same English shareable summary, counts only, in both panes
    assert "Exporter le résumé partageable" in pane(page, "fr") and "Export the shareable summary" in pane(page, "en")


def test_listes_tronquees_a_20_lignes_dans_les_deux_panneaux():
    s = json.loads((FIX / "scan_snowflake.json").read_text(encoding="utf-8"))
    base = next(t for t in s["tables"] if t["id"] == "CANOT_LAB.RAW.ARCHIVE_1")
    for i in range(30):
        s["tables"].append({**base, "id": f"CANOT_LAB.RAW.BULK_{i:02d}", "nom": f"BULK_{i:02d}",
                            "colonnes": [{"nom": "doc", "type": "VARIANT"}]})
    r, f = analyze(s), analyze(s, lang="fr")
    r["avec_sql_brut"] = f["avec_sql_brut"] = False
    page = render_html(r, f)
    for lang in ("en", "fr"):
        p = _Lists()
        p.feed(pane(page, lang))
        assert max(p.tables) <= MAX_ROWS + 1 and max(p.lists) <= MAX_ROWS
    fr = pane(page, "fr")
    assert "… et 13 de plus dans flags.csv." in fr and "de plus dans tables.csv." in fr and "de plus dans reads.csv." in fr


def test_langue_par_defaut_option_locale_repli(monkeypatch, tmp_path):
    from canot.brand import default_lang

    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    assert default_lang(None) == "en"
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert default_lang(None) == "fr" and default_lang("en") == "en"
    monkeypatch.setenv("LANG", "C")
    assert default_lang("fr") == "fr" and default_lang(None) == "en"
    # through the CLI: --lang wins, otherwise the locale; scan.json stays English
    from canot.cli import parser

    assert parser().parse_args(["scan", "x", "--lang", "fr"]).lang == "fr" and parser().parse_args(["scan", "x"]).lang is None
