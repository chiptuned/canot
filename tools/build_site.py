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
(root.parent / "site" / "index.html").write_text(html)  # q4o_caps est appliqué plus bas
import hashlib
import html as _html
import json


def ecarts():
    """Génère le catalogue des écarts : docs/ecarts.md (GitHub) et site/ecarts.html (canot.io) à partir de tools/ecarts.json."""
    data = json.loads((root / "ecarts.json").read_text())
    e = _html.escape
    md = ["# Catalogue des écarts", "",
          "Ce que deux moteurs rendent différemment pour la même requête ou le même type, **mesuré** avec Canot : la requête, le résultat de chaque côté, les versions et la date. Rien n'est ici supposé : ce qui n'est pas encore mesuré est listé à part.", ""]
    for m in data["mesures"]:
        md += [f"## {m['titre']}", "", f"*Mesuré le {m['mesure_le']}. {m['versions']}.*", "", "```sql", m["requete"], "```", "",
               "| | |", "|---|---|", f"| **Source** ({m['source']}) | `{m['resultat_source']}` |", f"| **Copie** ({m['destination']}) | `{m['resultat_copie']}` |", "",
               f"**Pourquoi.** {m['cause']}", "", f"**Que faire.** {m['que_faire']}", ""]
    md += ["## À mesurer", "", "Pas encore mesuré : ne rien en conclure.", ""] + [f"- **{t}** : {d}" for t, d in data["a_mesurer"]]
    md += ["", "## Proposer un écart", "", "Ouvrez une issue avec : la requête ou le type, le résultat de chaque moteur, les versions, la date. Un écart sans résultat reproductible n'entre pas dans le catalogue.", ""]
    (root.parent / "docs" / "ecarts.md").write_text("\n".join(md))
    cards = "".join(
        f'''<article class="ecart"><h2>{e(m["titre"])}</h2><p class="mono-line">Mesuré le {e(m["mesure_le"])} · {e(m["versions"])}</p>
<pre><code>{e(m["requete"])}</code></pre>
<dl class="duo"><div><dt>Source ({e(m["source"])})</dt><dd><code>{e(m["resultat_source"])}</code></dd></div><div><dt>Copie ({e(m["destination"])})</dt><dd><code>{e(m["resultat_copie"])}</code></dd></div></dl>
<p><b>Pourquoi.</b> {e(m["cause"])}</p><p><b>Que faire.</b> {e(m["que_faire"])}</p></article>''' for m in data["mesures"])
    todo = "".join(f"<li><b>{e(t)}</b> : {e(x)}</li>" for t, x in data["a_mesurer"])
    section = f'''<section class="s ecarts"><div class="wrap"><h1 class="h2like">Catalogue des écarts</h1>
<p class="lead-p">Ce que deux moteurs rendent différemment pour la même requête ou le même type, mesuré avec Canot : la requête, le résultat de chaque côté, les versions et la date. Ce qui n'est pas encore mesuré est listé à part.</p>
{cards}
<h2>À mesurer</h2><p class="lead-p">Pas encore mesuré : ne rien en conclure.</p><ul class="todo-list">{todo}</ul>
<p class="sources">Un écart de plus à proposer ? Ouvrez une <a href="https://github.com/chiptuned/canot/issues">issue</a> avec la requête ou le type, le résultat de chaque moteur, les versions et la date.</p>
</div></section>'''
    (root / "sections" / "ecarts.html").write_text(section)


ecarts()

import unicodedata


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def inline(t):
    t = _html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    def link(m):
        href = m.group(2)
        href = {"scan.md": "#scan", "sur-site.md": "#sur-site", "cibles-ue.md": "#cibles", "ecarts.md": "ecarts.html", "commandes.md": "#commandes"}.get(href, href)
        return f'<a href="{href}">{m.group(1)}</a>'
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


def md(text, shift=1):
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + _html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            title = ln.lstrip("# ").strip()
            out.append(f'<h{n + shift} id="{slug(title)}">{inline(title)}</h{n + shift}>')
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            out.append("<table><tr>" + th + "</tr>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</table>")
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:])
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
        elif not ln.strip():
            i += 1
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "```", "|", "- ")):
                para.append(lines[i])
                i += 1
            out.append("<p>" + inline(" ".join(para)) + "</p>")
    return "\n".join(out)


DOC_PAGES = [("commandes", "commandes.md"), ("scan", "scan.md"), ("sur-site", "sur-site.md"), ("cibles", "cibles-ue.md")]


def docs():
    """Page docs.html (depuis docs/*.md) et fichiers pour agents : llms.txt, llms-full.txt."""
    parts, full = [], []
    for key, fname in DOC_PAGES:
        text = (root.parent / "docs" / fname).read_text()
        parts.append(f'<section id="{key}">' + md(text) + "</section>")
        full.append(text.strip())
    toc = '<p class="toc"><a href="#commandes">Les commandes</a><a href="#sortie-en-local-ou-on-prem">Sortie en local</a><a href="#avec-un-agent-mcp">Avec un agent (MCP)</a><a href="#scan">Le scan</a><a href="#sur-site">Arrivée sur site</a><a href="#cibles">Hébergeurs</a></p>'
    section = ('<section class="s docs"><div class="wrap"><h1 class="h2like">Documentation</h1>'
               '<p class="lead-p">Une commande, ses possibilités, et un accès pour votre agent. Le détail se demande à <a href="#avec-un-agent-mcp">l\'agent MCP</a> ou se lit ici.</p>'
               + toc + "\n".join(parts) + "</div></section>")
    (root / "sections" / "docs.html").write_text(section)
    site = root.parent / "site"
    (site / "llms-full.txt").write_text("# Canot : documentation complète\n\n" + "\n\n---\n\n".join(full) + "\n")
    (site / "llms.txt").write_text("""# Canot

> Outil libre (Apache-2.0) qui répète le départ d'une plateforme de données (BigQuery, Snowflake, Databricks) vers Apache Iceberg, dans vos propres comptes : scan en lecture seule, copie, comparaison, rejeu de requêtes, dossier de preuve signé. Sans télémétrie. Commande : `uvx canot drill SOURCE DESTINATION`. Les prestations payantes sont assurées par Q4O.

## Documentation
- [Documentation complète](https://canot.io/llms-full.txt): commandes, sortie locale ou on-prem, scan, hébergeurs, MCP
- [Les commandes](https://canot.io/docs.html#commandes): drill, scan, demo, verify-evidence, teardown, snowflake-setup, bigquery-setup, mcp
- [Sortie en local ou on-prem](https://canot.io/docs.html#sortie-en-local-ou-on-prem)
- [Le scan en lecture seule](https://canot.io/docs.html#scan): ce qui est lu, avec quels droits
- [Avec un agent (MCP)](https://canot.io/docs.html#avec-un-agent-mcp): `claude mcp add canot -- uvx canot mcp`

## Autres
- [Catalogue des écarts](https://canot.io/ecarts.html): ce que deux moteurs rendent différemment, mesuré
- [Code source](https://github.com/chiptuned/canot)
- [Offres](https://canot.io/offres.html)
""")
    # la documentation de référence suit le dépôt : la même source sert le site, GitHub et le serveur MCP


docs()

Q4O_LINES = '<polyline points="0,40.0 40,42.3 80,44.8 120,47.4 160,49.8 200,52.0 240,53.9 280,55.2 320,55.8 360,55.8 400,54.9 440,53.3 480,50.8 520,47.6 560,43.9 600,39.7 640,35.2 680,30.6 720,26.2 760,22.2 800,18.7 840,16.1 880,14.4 920,13.9 960,14.5 1000,16.4 1040,19.4 1080,23.6 1120,28.7 1160,34.6 1200,41.0" fill="none" stroke="#4cb3bf" stroke-opacity="0.100" stroke-width="1"/><polyline points="0,60.8 40,63.3 80,65.7 120,68.0 160,70.0 200,71.5 240,72.4 280,72.7 320,72.1 360,70.8 400,68.7 440,65.8 480,62.2 520,58.1 560,53.7 600,49.1 640,44.6 680,40.3 720,36.6 760,33.6 800,31.5 840,30.5 880,30.7 920,32.2 960,34.9 1000,38.7 1040,43.6 1080,49.4 1120,55.8 1160,62.5 1200,69.4" fill="none" stroke="#4cb3bf" stroke-opacity="0.112" stroke-width="1"/><polyline points="0,81.5 40,83.8 80,85.9 120,87.6 160,88.8 200,89.3 240,89.1 280,88.1 320,86.3 360,83.7 400,80.4 440,76.5 480,72.2 520,67.7 560,63.1 600,58.7 640,54.7 680,51.4 720,48.9 760,47.5 800,47.3 840,48.3 880,50.5 920,54.0 960,58.6 1000,64.2 1040,70.5 1080,77.3 1120,84.3 1160,91.1 1200,97.4" fill="none" stroke="#4cb3bf" stroke-opacity="0.124" stroke-width="1"/><polyline points="0,101.7 40,103.5 80,104.9 120,105.7 160,105.8 200,105.2 240,103.7 280,101.5 320,98.5 360,94.9 400,90.7 440,86.3 480,81.7 520,77.2 560,73.0 600,69.4 640,66.6 680,64.8 720,64.1 760,64.7 800,66.5 840,69.5 880,73.8 920,79.1 960,85.2 1000,92.0 1040,99.0 1080,106.0 1120,112.7 1160,118.7 1200,123.6" fill="none" stroke="#4cb3bf" stroke-opacity="0.136" stroke-width="1"/><polyline points="0,120.8 40,121.8 80,122.2 120,121.9 160,120.9 200,119.0 240,116.4 280,113.0 320,109.1 360,104.8 400,100.3 440,95.8 480,91.5 520,87.7 560,84.6 600,82.4 640,81.3 680,81.3 720,82.7 760,85.3 800,89.2 840,94.1 880,100.0 920,106.7 960,113.7 1000,120.8 1040,127.7 1080,134.1 1120,139.5 1160,143.7 1200,146.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.148" stroke-width="1"/><polyline points="0,138.4 40,138.4 80,137.7 120,136.2 160,134.0 200,131.0 240,127.4 280,123.4 320,119.0 360,114.5 400,110.2 440,106.2 480,102.8 520,100.3 560,98.7 600,98.3 640,99.2 680,101.3 720,104.7 760,109.3 800,114.9 840,121.3 880,128.3 920,135.5 960,142.6 1000,149.2 1040,155.0 1080,159.8 1120,163.1 1160,164.9 1200,164.9" fill="none" stroke="#4cb3bf" stroke-opacity="0.160" stroke-width="1"/><polyline points="0,154.3 40,153.2 80,151.4 120,148.8 160,145.6 200,141.8 240,137.6 280,133.3 320,128.9 360,124.8 400,121.3 440,118.4 480,116.5 520,115.6 560,116.0 600,117.7 640,120.6 680,124.7 720,130.0 760,136.1 800,142.9 840,150.1 880,157.2 920,164.1 960,170.3 1000,175.5 1040,179.4 1080,181.8 1120,182.4 1160,181.2 1200,178.2" fill="none" stroke="#4cb3bf" stroke-opacity="0.172" stroke-width="1"/><polyline points="0,168.5 40,166.3 80,163.5 120,160.1 160,156.2 200,152.0 240,147.7 280,143.6 320,139.9 360,136.8 400,134.5 440,133.2 480,133.1 520,134.3 560,136.7 600,140.4 640,145.2 680,151.0 720,157.6 760,164.6 800,171.8 840,178.8 880,185.3 920,190.9 960,195.3 1000,198.3 1040,199.6 1080,199.0 1120,196.6 1160,192.3 1200,186.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.184" stroke-width="1"/><polyline points="0,181.2 40,178.2 80,174.6 120,170.7 160,166.6 200,162.5 240,158.7 280,155.4 320,152.8 360,151.2 400,150.6 440,151.3 480,153.2 520,156.3 560,160.7 600,166.1 640,172.3 680,179.2 720,186.3 760,193.4 800,200.1 840,206.0 880,210.9 920,214.4 960,216.4 1000,216.5 1040,214.7 1080,211.1 1120,205.7 1160,198.6 1200,190.2" fill="none" stroke="#4cb3bf" stroke-opacity="0.196" stroke-width="1"/><polyline points="0,192.9 40,189.3 80,185.4 120,181.4 160,177.6 200,174.2 240,171.4 280,169.4 320,168.4 360,168.6 400,169.9 440,172.6 480,176.4 520,181.4 560,187.2 600,193.8 640,200.8 680,207.9 720,214.7 760,220.9 800,226.2 840,230.2 880,232.8 920,233.5 960,232.5 1000,229.5 1040,224.7 1080,218.2 1120,210.2 1160,201.0 1200,191.0" fill="none" stroke="#4cb3bf" stroke-opacity="0.208" stroke-width="1"/><polyline points="0,204.1 40,200.3 80,196.6 120,193.1 160,190.2 200,187.9 240,186.5 280,186.2 320,187.1 360,189.1 400,192.4 440,196.9 480,202.3 520,208.5 560,215.3 600,222.3 640,229.2 680,235.6 720,241.2 760,245.7 800,248.8 840,250.2 880,249.9 920,247.6 960,243.4 1000,237.5 1040,230.0 1080,221.2 1120,211.4 1160,201.2 1200,190.8" fill="none" stroke="#4cb3bf" stroke-opacity="0.220" stroke-width="1"/><polyline points="0,215.7 40,212.2 80,209.1 120,206.6 160,204.9 200,204.2 240,204.5 280,206.1 320,208.8 360,212.7 400,217.6 440,223.5 480,229.9 520,236.7 560,243.6 600,250.1 640,256.0 680,260.9 720,264.5 760,266.5 800,266.8 840,265.3 880,261.8 920,256.6 960,249.6 1000,241.3 1040,231.8 1080,221.7 1120,211.3 1160,201.1 1200,191.7" fill="none" stroke="#4cb3bf" stroke-opacity="0.232" stroke-width="1"/><polyline points="0,228.2 40,225.6 80,223.6 120,222.5 160,222.3 200,223.3 240,225.5 280,228.8 320,233.2 360,238.6 400,244.7 440,251.2 480,258.0 520,264.5 560,270.6 600,275.8 640,279.9 680,282.5 720,283.4 760,282.6 800,279.8 840,275.2 880,268.9 920,261.1 960,252.1 1000,242.2 1040,231.8 1080,221.6 1120,211.8 1160,203.1 1200,195.9" fill="none" stroke="#4cb3bf" stroke-opacity="0.244" stroke-width="1"/><polyline points="0,242.5 40,241.1 80,240.5 120,241.0 160,242.6 200,245.3 240,249.1 280,254.0 320,259.6 360,265.8 400,272.3 440,278.9 480,285.0 520,290.5 560,294.9 600,298.0 640,299.6 680,299.4 720,297.4 760,293.6 800,287.9 840,280.7 880,272.1 920,262.5 960,252.4 1000,242.1 1040,232.2 1080,223.1 1120,215.3 1160,209.2 1200,205.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.256" stroke-width="1"/><polyline points="0,259.0 40,259.0 80,260.0 120,262.1 160,265.4 200,269.6 240,274.8 280,280.6 320,286.8 360,293.2 400,299.3 440,305.0 480,309.7 520,313.3 560,315.4 600,315.9 640,314.6 680,311.5 720,306.6 760,300.0 800,292.0 840,282.8 880,272.9 920,262.7 960,252.6 1000,243.3 1040,235.0 1080,228.3 1120,223.6 1160,221.2 1200,221.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.268" stroke-width="1"/><polyline points="0,277.8 40,279.4 80,282.0 120,285.6 160,290.2 200,295.5 240,301.4 280,307.5 320,313.6 360,319.3 400,324.3 440,328.2 480,330.9 520,332.0 560,331.4 600,329.0 640,324.8 680,318.9 720,311.5 760,302.9 800,293.3 840,283.3 880,273.2 920,263.6 960,255.0 1000,247.8 1040,242.3 1080,239.1 1120,238.3 1160,240.1 1200,244.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.280" stroke-width="1"/><polyline points="0,299.0 40,302.0 80,305.9 120,310.7 160,316.1 200,321.9 240,327.8 280,333.5 320,338.6 360,342.9 400,346.0 440,347.7 480,347.8 520,346.1 560,342.7 600,337.5 640,330.8 680,322.7 720,313.6 760,303.8 800,293.9 840,284.2 880,275.2 920,267.5 960,261.5 1000,257.4 1040,255.8 1080,256.6 1120,260.1 1160,266.1 1200,274.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.292" stroke-width="1"/>'
def q4o_caps(h):
    """q4o s'écrit Q4O dans le texte visible (pas dans les adresses, classes, noms de fichiers)."""
    parts = re.split(r"(<[^>]+>)", h)
    for k, part in enumerate(parts):
        if part.startswith("<"):
            parts[k] = part.replace('alt="q4o"', 'alt="Q4O"')
        else:
            parts[k] = re.sub(r"(?<![\w./@-])q4o(?!\.\w|[\w/@-])", "Q4O", part)
    return "".join(parts)


PAGES = {
    "pourquoi": ("Pourquoi", "Pourquoi vérifier la sortie de votre plateforme de données : ce que demandent DORA et le Data Act, et ce que coûte l'inconnu.", ["why"]),
    "methode": ("Méthode", "Comment Canot teste la sortie de votre plateforme de données, dans vos propres comptes, et ce qu'il vous remet.", ["method", "platforms", "onprem", "hosts"]),
    "offres": ("Offres", "Quatre façons de commencer avec Canot : l'outil seul, le diagnostic de réversibilité, le test de sortie DORA, l'abonnement.", ["offers"]),
    "ecarts": ("Catalogue des écarts", "Ce que deux moteurs rendent différemment pour la même requête, mesuré avec Canot : requête, résultats, versions, date.", ["ecarts"]),
    "docs": ("Documentation", "La documentation de Canot : les commandes, la sortie en local ou on-prem, le scan en lecture seule, et l'accès pour un agent (MCP).", ["docs"]),
    "faq": ("Questions fréquentes", "Réponses aux questions fréquentes sur Canot : données, certification, contrats, durée d'un exercice.", ["faq"]),
}
head = (root / "head.partial.html").read_text()
tail = (root / "tail.partial.html").read_text()
strip = '<section class="s contact-strip"><div class="wrap"><p>Une question, ou un test à proposer ? <a class="btn btn-accent" href="/#contact">Nous écrire</a></p></div></section>'
for name, (title, desc, parts) in PAGES.items():
    hd = head.replace("<title>Canot : reprenez la main sur vos données</title>", f"<title>{title} | Canot</title>")
    hd = re.sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{desc}">', hd)
    hd = hd.replace('<link rel="canonical" href="https://canot.io/">', f'<link rel="canonical" href="https://canot.io/{name}.html">')
    hd = re.sub(r'<meta property="og:[^>]*>\n', "", hd)
    hd = hd.replace('<a class="skip" href="#contact">Aller au contact</a>', '<a class="skip" href="#main">Aller au contenu</a>').replace("<main>", '<main id="main">')
    body = "\n\n".join((root / "sections" / f"{p}.html").read_text() for p in parts).replace("@@q4olines@@", Q4O_LINES)
    (root.parent / "site" / f"{name}.html").write_text(q4o_caps(hd + "\n" + body + "\n" + strip + "\n" + tail))
ix = root.parent / "site" / "index.html"
ix.write_text(q4o_caps(ix.read_text()))
print("pages écrites : index," + ", ".join(PAGES))




def busting():
    """Ajoute ?v=<empreinte> aux liens vers style.css et app.js dans toutes les pages."""
    site = root.parent / "site"
    tag = {n: hashlib.sha256((site / n).read_bytes()).hexdigest()[:10] for n in ("style.css", "app.js")}
    for page in site.glob("*.html"):
        x = page.read_text()
        for n, v in tag.items():
            x = re.sub(rf'((?:href|src)="){re.escape(n)}(?:\?v=[0-9a-f]+)?"', rf'\g<1>{n}?v={v}"', x)
        page.write_text(x)
    print("empreintes :", tag)


busting()
