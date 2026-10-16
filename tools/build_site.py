"""Assemble site/index.html à partir du gabarit et des macarons. Usage : python3 tools/build_site.py"""
import os, pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from make_seals import SEALS, seal, wave

root = pathlib.Path(__file__).resolve().parent
html = (root / "index.template.html").read_text()
for key, label, size in SEALS:
    pass
for key, label, size in SEALS:
    html = html.replace(f"@@{key}@@", seal(key, label, size))
html = html.replace('@@wave_back@@', wave(90, 22, '#E6ECF2')).replace('@@wave_front@@', wave(52, 10, '#D6E0E9'))
booking = os.environ.get('CANOT_BOOKING_URL', '').strip()
html = html.replace('@@booking@@', f'<a class="btn btn-line" href="{booking}">Réserver 30 minutes</a>' if booking else '')
assert not re.search(r"@@\w+@@", html)
(root.parent / "site" / "index.html").write_text(html)
PAGES = {
    "pourquoi": ("Pourquoi", "Pourquoi vérifier la sortie de votre plateforme de données : ce que demandent DORA et le Data Act, et ce que coûte l'inconnu.", ["why"]),
    "methode": ("Méthode", "Comment Canot teste la sortie de votre plateforme de données, dans vos propres comptes, et ce qu'il vous remet.", ["method", "hosts"]),
    "offres": ("Offres", "Quatre façons de commencer avec Canot : l'outil seul, le diagnostic de réversibilité, le test de sortie DORA, l'abonnement.", ["offers"]),
    "faq": ("Questions fréquentes", "Réponses aux questions fréquentes sur Canot : données, certification, contrats, durée d'un exercice.", ["faq"]),
}
head = (root / "head.partial.html").read_text()
tail = (root / "tail.partial.html").read_text()
strip = '<section class="s contact-strip"><div class="wrap"><p>Une question, ou un test à proposer ? <a class="btn btn-accent" href="/#contact">Nous écrire</a></p></div></section>'
for name, (title, desc, parts) in PAGES.items():
    hd = head.replace("<title>Canot : prouvez que vous pouvez partir</title>", f"<title>{title} | Canot</title>")
    hd = re.sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{desc}">', hd)
    hd = hd.replace('<link rel="canonical" href="https://canot.io/">', f'<link rel="canonical" href="https://canot.io/{name}.html">')
    hd = re.sub(r'<meta property="og:[^>]*>\n', "", hd)
    hd = hd.replace('<a class="skip" href="#contact">Aller au contact</a>', '<a class="skip" href="#main">Aller au contenu</a>').replace("<main>", '<main id="main">')
    body = "\n\n".join((root / "sections" / f"{p}.html").read_text() for p in parts)
    (root.parent / "site" / f"{name}.html").write_text(hd + "\n" + body + "\n" + strip + "\n" + tail)
print("pages écrites : index," + ", ".join(PAGES))
