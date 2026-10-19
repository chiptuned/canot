import shutil
import subprocess
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent.parent / "site"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_app_js_a_une_syntaxe_valide():
    """Une traduction de commentaires a déjà cassé app.js sans qu'aucun test Python ne le voie."""
    r = subprocess.run(["node", "--check", str(SITE / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("page", ["index.html", "en/index.html"])
def test_le_hero_garde_les_quatre_tambours_et_les_parties_conditionnelles(page):
    """La traduction du site avait retiré les tambours d'action et de destination sans qu'aucun test ne le voie."""
    html = (SITE / page).read_text(encoding="utf-8")
    for drum in ("runner", "scheme", "dest", "label"):  # the hero command is always `drill`: no action drum
        assert f'data-cs-drum="{drum}"' in html, drum
    for marker in ("data-cs-show=", "data-cs-rest", "data-cs-dest-prefix", "data-cs-copy", "data-cs-action-text"):
        assert marker in html, marker
