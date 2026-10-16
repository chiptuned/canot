"""Assemble site/index.html à partir du gabarit et des macarons. Usage : python3 tools/build_site.py"""
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from make_seals import SEALS, seal, wave

root = pathlib.Path(__file__).resolve().parent
html = (root / "index.template.html").read_text()
for key, label, size in SEALS:
    html = html.replace(f"@@{key}@@", seal(key, label, size))
html = html.replace('@@wave_back@@', wave(90, 22, '#E6ECF2')).replace('@@wave_front@@', wave(52, 10, '#D6E0E9'))
assert not re.search(r"@@\w+@@", html)
(root.parent / "site" / "index.html").write_text(html)
print("site/index.html écrit")
