import re

from canot import regulatory

FORBIDDEN = ("compliant", "certified", "guaranteed", "savings", "waste", "unused", "critical", "urgent", "easy")


def test_chaque_cadre_cite_est_present_et_aucune_promesse_de_conformite():
    for kind in ("drill", "scan"):
        sec = regulatory.section(kind).lower()
        for fw in ("dora", "data act", "gdpr", "secnumcloud", "nis2", "eba") if kind == "drill" else ("dora", "data act", "gdpr", "nis2", "eba"):
            assert fw in sec, (kind, fw)
        for w in FORBIDDEN:
            assert w not in sec, (kind, w)
        assert "not a certification" in sec and "not legal advice" in sec
        assert "<a " not in sec and "<script" not in sec and not re.search(r"https?://", sec)


def test_les_lignes_sont_choisies_selon_le_rapport():
    assert all("scan" in r[4] for r in regulatory.rows_for("scan")) and all("drill" in r[4] for r in regulatory.rows_for("drill"))
    assert any("minimisation" in r[1] for r in regulatory.rows_for("scan")) and not any("minimisation" in r[1] for r in regulatory.rows_for("drill"))


def test_le_rapport_du_drill_et_celui_du_scan_contiennent_la_section(tmp_path):
    from pathlib import Path

    from canot.scanner.render import render_html
    import json

    snap = json.loads((Path(__file__).parent / "fixtures" / "scan_bigquery.json").read_text())
    from canot.scanner.analyze import analyze

    page = render_html(analyze(snap))
    assert 'id="regulatory"' in page and "GDPR, DORA, NIS2, Data Act, SecNumCloud, EBA" in page
