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


FR_FORBIDDEN = ("économie", "gaspillage", "inutile", "inutilisé", "critique", "urgent", "facile", "garanti", "conforme", "certifié")


def test_section_en_francais_complete_et_sans_promesse():
    for kind in ("drill", "scan"):
        en, fr = regulatory.section(kind), regulatory.section(kind, "fr")
        assert en.count("<tr>") == fr.count("<tr>") == len(regulatory.rows_for(kind)) + 1
        low = fr.lower()
        for name in ("DORA (règlement (UE) 2022/2554)", "RGPD (règlement (UE) 2016/679)", "NIS2 (directive (UE) 2022/2555)",
                     "Data Act (règlement (UE) 2023/2854)", "Orientations de l&#x27;ABE sur l&#x27;externalisation (EBA/GL/2019/02)"):
            assert name in fr, (kind, name)
        assert ("SecNumCloud (ANSSI)" in fr) == (kind == "drill")
        assert "pas une certification" in low and "pas un conseil juridique" in low and regulatory.AS_OF in fr
        for w in FR_FORBIDDEN:
            assert w not in low, (kind, w)
        assert "<a " not in fr and "<script" not in fr and not re.search(r"https?://", fr)


def test_ids_par_langue_et_signature_conservee():
    assert 'id="regulatory"' in regulatory.section("scan") and 'id="regulatory-fr"' in regulatory.section("scan", "fr")
    assert regulatory.section("drill", "en", tag="div", anchor="regulatory-en").startswith('<div id="regulatory-en">')
    assert len(regulatory.ROWS_FR) == len(regulatory.ROWS)


def test_bloc_de_partage_en_francais():
    from canot.brand import share_block

    en, fr = share_block({"a": 1}, "x.json"), share_block({"a": 1}, "x.json", "fr")
    assert "https://canot.io/en/review.html" in en and "Get an expert review" in en
    assert "https://canot.io/revue.html" in fr and "Faire relire ce rapport par un expert" in fr and "Rien n" in fr
    assert "review.html" not in fr
