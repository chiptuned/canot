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
