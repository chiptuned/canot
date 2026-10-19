"""report.html of `canot drill` in English and French: both panes complete, unique ids, the toggle, the default pane, the wording rules."""
import html
import json
import re
import shutil
import string
from pathlib import Path

import pytest

from canot import report
from canot.report import T, write_report

EN_FORBIDDEN = ("savings", "waste", "unused", "critical", "urgent", "easy", "guaranteed", "compliant", "certified", "row by row")
FR_FORBIDDEN = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti", "conforme", "certifié",
                "ligne par ligne")
# common English words that must not survive in the French pane (code, identifiers and framework names are removed first)
EN_WORDS = ("the", "and", "with", "this", "that", "rows", "query", "queries", "report", "copy", "identical", "mismatch", "none",
            "finding", "summary", "measured", "not", "you", "your", "what", "which", "to", "of", "is", "are", "loss", "only", "from")
ACCENTS = re.compile(r"[àâäçéèêëîïôöûùüÿœæÀÂÄÇÉÈÊËÎÏÔÖÛÙÜŸŒÆ]")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """One offline demo run (fallback dataset), report written with --lang fr."""
    import canot.demo
    from canot.cli import main

    out = tmp_path_factory.mktemp("i18n") / "d"
    keep = canot.demo.make_tpch
    canot.demo.make_tpch = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    try:
        assert main(["demo", "--out", str(out), "--rows", "2000", "--no-open", "--lang", "fr"]) == 0
    finally:
        canot.demo.make_tpch = keep
    ev = out / "evidence"
    return json.loads((ev / "report.json").read_text(encoding="utf-8")), ev


def panes(page: str) -> dict:
    """{lang: inner html of its pane} and the order in which the panes appear."""
    m = re.search(r'<div class="pane alt" id="lang-(\w+)" lang="\1">(.*)</div><div class="pane main" id="lang-(\w+)" lang="\3">(.*)</div>\s*</html>$',
                  page, re.S)
    assert m, "two panes, alternate first then main"
    return {m.group(1): m.group(2), m.group(3): m.group(4), "_order": (m.group(1), m.group(3))}


def text(fragment: str, drop_code: bool = False) -> str:
    fragment = re.sub(r'href="data:[^"]*"', "", fragment)
    if drop_code:
        fragment = re.sub(r"<code>.*?</code>", " ", fragment, flags=re.S)
    return re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def rewrite(res, ev, tmp_path, lang=None) -> str:
    out = tmp_path / "copy"
    shutil.copytree(ev, out)
    return write_report(res, out, lang).read_text(encoding="utf-8")


def test_both_panes_present_with_the_same_structure(demo):
    page = (demo[1] / "report.html").read_text(encoding="utf-8")
    p = panes(page)
    en, fr = p["en"], p["fr"]
    for tag in ("<h1", "<h2", "<h3", "<table", "<tr", "<li", 'class="find"', 'class="share"', 'class="cta"', "<code>"):
        assert en.count(tag) == fr.count(tag), tag
    assert "Evidence report: exit exercise" in en and "Rapport de preuve : exercice de sortie" in fr
    assert "What these figures do not say" in en and "Ce que ces chiffres ne disent pas" in fr
    assert "Sortie réalisable sur ce périmètre" in text(fr) and "Exit feasible on this scope" in text(en)
    assert "Toute modification du journal se voit" in text(fr) and "empreinte de contenu" in text(fr)
    assert "TIMESTAMP_NS devient" in text(fr) and "ne conserve pas les nanosecondes" in text(fr)
    assert "RGPD (règlement (UE) 2016/679)" in text(fr) and "GDPR (Regulation (EU) 2016/679)" in text(en)


def test_no_english_left_in_the_french_pane_and_no_accent_in_the_english_one(demo):
    p = panes((demo[1] / "report.html").read_text(encoding="utf-8"))
    fr = text(p["fr"], drop_code=True)
    for name in ("Data Act", "Apache Iceberg", "SecNumCloud", "reporting", "drill-share.json", "Business Critical"):
        fr = fr.replace(name, " ")
    left = sorted({w for w in EN_WORDS if re.search(rf"(?<![\w.'’-]){w}(?![\w.'’-])", fr)})
    assert not left, (left, fr[:3000])
    en = text(p["en"])
    assert not ACCENTS.search(en), ACCENTS.findall(en)


def test_french_numbers_and_date(demo):
    fr = text(panes((demo[1] / "report.html").read_text(encoding="utf-8"))["fr"])
    assert re.search(r"\d \d{3} lignes au total", fr)  # 12 300, narrow no-break space
    assert re.search(r"Débit de copie mesuré : \d+(,\d+)? Mo/s", fr)
    assert re.search(r"Constat daté du (1er|\d{1,2}) (janv\.|févr\.|mars|avr\.|mai|juin|juil\.|août|sept\.|oct\.|nov\.|déc\.) 20\d\d", fr)


def test_ids_are_unique_and_only_the_pane_wrappers_are_shared_names(demo):
    page = (demo[1] / "report.html").read_text(encoding="utf-8")
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), ids
    assert {"lang-en", "lang-fr", "regulatory-en", "regulatory-fr"} <= set(ids)
    p = panes(page)
    assert all(i.endswith("-en") for i in re.findall(r'\sid="([^"]+)"', p["en"]))
    assert all(i.endswith("-fr") for i in re.findall(r'\sid="([^"]+)"', p["fr"]))


def test_toggle_one_brand_bar_and_default_from_lang_option(demo):
    page = (demo[1] / "report.html").read_text(encoding="utf-8")
    assert page.count('href="#lang-en"') == 1 and page.count('href="#lang-fr"') == 1
    assert page.count("<svg") == 1 and page.count('class="brandbar"') == 1 and page.count("<style>") == 1
    assert page.index('class="langbar"') < page.index('id="lang-')  # the language bar comes before both panes
    assert panes(page)["_order"] == ("en", "fr")  # --lang fr: English is the alternate pane, French the main one
    assert '<html lang="fr">' in page and "<title>Rapport de preuve Canot</title>" in page


def test_default_from_locale_and_fallback(demo, tmp_path, monkeypatch):
    res, ev = demo
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert panes(rewrite(res, ev, tmp_path / "a"))["_order"] == ("en", "fr")
    monkeypatch.delenv("LANG")
    page = rewrite(res, ev, tmp_path / "b")
    assert panes(page)["_order"] == ("fr", "en") and '<html lang="en">' in page
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert panes(rewrite(res, ev, tmp_path / "c", "en"))["_order"] == ("fr", "en")  # the option wins over the locale


def test_no_script_no_external_resource(demo):
    page = (demo[1] / "report.html").read_text(encoding="utf-8")
    assert "<script" not in page.lower() and " src=" not in page and "url(" not in page and "@import" not in page
    urls = re.findall(r"https?://[^\s\"'<>)]+", page)
    assert urls and all(u.startswith("https://canot.io/") for u in urls), urls
    p = panes(page)
    assert "https://canot.io/en/review.html" in p["en"] and "https://canot.io/revue.html" in p["fr"]
    assert "revue.html" not in p["en"] and "en/review.html" not in p["fr"]
    assert "https://canot.io/en/#contact" in p["en"] and "https://canot.io/#contact" in p["fr"]  # each pane links to the site in its own language


def test_forbidden_words_absent_in_both_languages(demo):
    p = panes((demo[1] / "report.html").read_text(encoding="utf-8"))
    en, fr = text(p["en"]).lower(), text(p["fr"]).lower()
    for w in EN_FORBIDDEN:
        assert w not in en, w
    for w in FR_FORBIDDEN:
        assert w not in fr, w


def test_text_table_is_complete_in_both_languages():
    def fields(v):
        if isinstance(v, str):
            return {f for _, f, _, _ in string.Formatter().parse(v) if f}
        return None
    assert T["en"].keys() == T["fr"].keys()
    for k, v in T["en"].items():
        w = T["fr"][k]
        assert type(v) is type(w), k
        if isinstance(v, str):
            assert fields(v) == fields(w), k
        elif isinstance(v, tuple):
            assert len(v) == len(w), k
    assert T["en"]["q_status"].keys() == T["fr"]["q_status"].keys()
    for w in FR_FORBIDDEN:
        assert w not in json.dumps(T["fr"], ensure_ascii=False).lower(), w
    for w in EN_FORBIDDEN:
        assert w not in json.dumps(T["en"]).lower(), w


def test_synthesis_in_french(demo):
    res = demo[0]
    en, fr = report.synthese(res), report.synthese(res, "fr")
    assert en["verdict"].startswith("Exit feasible") and fr["verdict"].startswith("Sortie réalisable")
    assert len(en["findings"]) == len(fr["findings"]) and en["todo"] == fr["todo"]
    assert [f[0] for f in en["findings"]] == [f[0] for f in fr["findings"]]


def test_run_strings_worded_in_french():
    tr = lambda s: report._tr(s, "fr")  # noqa: E731
    assert tr("3 rows / 4") == "3 lignes / 4"
    assert tr("sorted row 2, column 1: 5 / 6") == "ligne triée 2, colonne 1 : 5 / 6"
    assert tr("row count: 150000 / 25325; TOTAL.sum: 1 / 2") == "nombre de lignes : 150000 / 25325 ; TOTAL.sum: 1 / 2"
    assert tr("time zone Europe/Paris -> UTC (Iceberg keeps UTC only): the instant is kept, the zone name is not").startswith("fuseau Europe/Paris -> UTC")
    assert tr("read-only, with role R") == "lecture seule, avec role R"  # the reader is worded separately in the dbt section
    assert tr("ValueError: boom") == "ValueError: boom"  # a technical message stays as written
    assert report._tr("3 rows / 4", "en") == "3 rows / 4"


def test_dbt_causes_in_french():
    from canot import dbt_drill

    man = {"nodes": {"model.p.fct": {"depends_on": {"nodes": ["source.p.raw.orders", "source.p.raw.customers"]}}}}
    copiees = [{"uid": "source.p.raw.orders", "fqn": "RAW.orders"}, {"uid": "source.p.raw.customers", "fqn": "RAW.customers"}]
    pol = {"lu": True, "tables": {"RAW.CUSTOMERS": [{"kind": "ROW_ACCESS_POLICY", "policy": "RAP_COUNTRY", "column": None}]}}
    conv = [{"table": "RAW.ORDERS", "column": "ORDER_TS", "lossy": True, "tz": "America/Los_Angeles",
             "note": "time zone America/Los_Angeles -> UTC (Iceberg keeps UTC only): the instant is kept, the zone name is not"}]

    def rapport(tz, rows=(10, 10), d="ORDER_DATE.max: datetime.date(2025, 8, 22) / datetime.date(2025, 8, 23)"):
        return {"timezone": tz, "modeles": [{"modele": "model.p.fct", "statut": "different", "raison": d,
                                             "verification": {"rows_source": rows[0], "rows_target": rows[1], "stats_diffs": [d]}}]}
    cases = [
        (rapport({"applied": "UTC", "finding": "several source time zones: conversions to a date can differ, result not determined for "
                  "the models concerned (replayed in UTC): America/Los_Angeles (RAW.ORDERS.ORDER_TS)"}), "fuseau", "le rejeu s'est fait en UTC"),
        (rapport({"applied": "America/Los_Angeles"}), "decalages", "les décalages stockés avec les valeurs"),
        (rapport({"applied": "America/Los_Angeles"}, (150000, 25325), "row count: 150000 / 25325"), "politique", "25 325 lignes"),
        (rapport({"applied": "America/Los_Angeles"}, d="TOTAL.sum: 1 / 2"), "inconnue", "causes possibles"),
    ]
    for rap, nature, marker in cases:
        (e,) = dbt_drill.explications(man, rap, copiees, pol, conv, "role R")
        (c,) = e["causes"]
        assert c["nature"] == nature
        fr = report._cause_fr(c)
        assert marker in fr, (nature, fr)
        bare = re.sub(r"\S*[._/]\S*|TIMESTAMP_TZ|date_trunc|extract|cast", " ", fr)
        left = [w for w in ("the", "and", "with", "replay", "copied", "rows", "possible causes") if re.search(rf"\b{w}\b", bare)]
        assert not left, (nature, left, fr)
    assert report._cause_fr({"nature": "inconnue", "texte": "old english text"}) == "old english text"  # report.json from before


def test_dbt_section_in_both_panes(tmp_path):
    from test_drill_dbt import _drill, build_project

    proj = build_project(tmp_path, ecart=True)
    r, out = _drill(tmp_path, proj, lang="fr")
    page = (out / "report.html").read_text(encoding="utf-8")
    p = panes(page)
    assert p["_order"] == ("en", "fr")
    en, fr = text(p["en"]), text(p["fr"])
    assert "dbt project" in en and "Not copied" in en and "possible causes" in en
    assert "Projet dbt" in fr and "Non copiées" in fr and "causes possibles" in fr and "possible causes" not in fr
    assert "vue : ne contient pas de données" in fr and "introuvable comme table dans l'entrepôt" in fr
    assert "Lecture seule, avec le fichier DuckDB local, en lecture seule" in fr
    assert "Fuseau horaire du rejeu" in fr and "aucun fuseau source parmi les entrées" in fr
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    rep = (out / "report.json").read_text(encoding="utf-8")
    assert "Projet dbt" not in rep and "causes possibles" not in rep  # report.json stays English
