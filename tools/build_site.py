"""Build the canot.io static site. Usage: python3 tools/build_site.py

French pages live at the root (the default), English pages under /en/. The documentation (docs.html)
and the discrepancy catalogue (discrepancies.html) are English only and shared by both languages.

Sources:
  FR  tools/index.template.html     + tools/sections/<name>.html
  EN  tools/index.template.en.html  + tools/sections/en/<name>.html   (skipped with a warning if missing)
  docs/commands.md, scan.md, on-prem.md, targets.md (falls back to the former French file names)
  tools/ecarts.json -> discrepancies.html + docs/discrepancies.md
Static pages not generated here: rapport.html, mentions-legales.html, en/report.html, en/legal.html.
"""
import hashlib
import html as _html
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from make_seals import SEALS, seal, wave

root = pathlib.Path(__file__).resolve().parent
repo = root.parent
SITE = repo / "site"
DOCS = repo / "docs"
BASE = "https://canot.io"
e = _html.escape


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def first(d, *keys, default=""):
    """Read a field under its English name, falling back to the former French one."""
    for k in keys:
        if k in d:
            return d[k]
    return default


# ---------------------------------------------------------------- pages and their equivalents
# key -> (French path, English path). Shared English-only pages have no French path.
EQUIV = {
    "home": ("/", "/en/"),
    "why": ("/pourquoi.html", "/en/why.html"),
    "method": ("/methode.html", "/en/method.html"),
    "offers": ("/offres.html", "/en/offers.html"),
    "faq": ("/faq.html", "/en/faq.html"),
    "report": ("/rapport.html", "/en/report.html"),
    "legal": ("/mentions-legales.html", "/en/legal.html"),
}
SHARED = {"docs": "/docs.html", "discrepancies": "/discrepancies.html"}

# Generated secondary pages: key -> (sections, FR title, FR description, EN title, EN description)
PAGES = {
    "why": (["why"], "Pourquoi", "Pourquoi vérifier la sortie de votre plateforme de données : ce que demandent DORA et le Data Act, et ce que coûte l'inconnu.",
            "Why", "Why test the exit from your data platform: what DORA and the Data Act require, and what the unknown costs."),
    "method": (["method", "platforms", "onprem", "hosts"], "Méthode", "Comment Canot teste la sortie de votre plateforme de données, dans vos propres comptes, et ce qu'il vous remet.",
               "Method", "How Canot tests the exit from your data platform, in your own accounts, and what it hands you."),
    "offers": (["offers"], "Offres", "Quatre façons de commencer avec Canot : l'outil seul, le diagnostic de réversibilité, le test de sortie DORA, l'abonnement.",
               "Offers", "Four ways to start with Canot: the tool on its own, the reversibility assessment, the DORA exit test, the subscription."),
    "faq": (["faq"], "Questions fréquentes", "Réponses aux questions fréquentes sur Canot : données, certification, contrats, durée d'un exercice.",
            "FAQ", "Answers to frequent questions about Canot: data, certification, contracts, how long an exercise takes."),
}

HOME = {
    "fr": {"title": "Canot : reprenez la main sur vos données",
           "desc": "Canot teste la sortie de votre plateforme de données (Snowflake, BigQuery, Databricks) dans vos propres comptes et vous remet un dossier de preuve signé.",
           "og_title": "Canot : votre plan de sortie, exécuté et mesuré",
           "og_desc": "Un exercice de sortie dans vos propres comptes, et un dossier signé que votre comité et vos achats peuvent lire."},
    "en": {"title": "Canot: take back control of your data",
           "desc": "Canot tests the exit from your data platform (Snowflake, BigQuery, Databricks) in your own accounts and hands you a signed evidence pack.",
           "og_title": "Canot: your exit plan, executed and measured",
           "og_desc": "An exit exercise in your own accounts, and a signed evidence pack your risk committee and procurement can read."},
}
L10N = {
    "fr": {"skip_contact": "Aller au contact", "skip_main": "Aller au contenu", "locale": "fr_FR", "booking": "Réserver 30 minutes",
           "strip": '<section class="s contact-strip"><div class="wrap"><p>Une question, ou un test à proposer ? <a class="btn btn-accent" href="/#contact">Nous écrire</a></p></div></section>'},
    "en": {"skip_contact": "Skip to contact", "skip_main": "Skip to content", "locale": "en_GB", "booking": "Book 30 minutes",
           "strip": '<section class="s contact-strip"><div class="wrap"><p>A question, or a test to suggest? <a class="btn btn-accent" href="/en/#contact">Write to us</a></p></div></section>'},
}
EN_SECTIONS = root / "sections" / "en"
EN_TEMPLATE = root / "index.template.en.html"


def en_available():
    """Which English equivalents exist (or will be generated) in this build."""
    ok = {"home": EN_TEMPLATE.exists()}
    for key, (parts, *_rest) in PAGES.items():
        missing = [p for p in parts if not (EN_SECTIONS / f"{p}.html").exists()]
        ok[key] = not missing
        if missing:
            warn(f"English page /en/{key}.html skipped: missing tools/sections/en/{', '.join(m + '.html' for m in missing)}")
    if not ok["home"]:
        warn("English home /en/ skipped: missing tools/index.template.en.html")
    for key in ("report", "legal"):
        ok[key] = (SITE / EQUIV[key][1].lstrip("/")).exists()
        if not ok[key]:
            warn(f"static English page site{EQUIV[key][1]} not found yet")
    return ok


AVAIL = en_available()


def blocks(path):
    """A partial holds one block per language, each starting with '<!-- lang: xx -->'."""
    out, cur = {}, None
    for ln in path.read_text().splitlines(keepends=True):
        m = re.match(r"<!-- lang: (\w+) -->\s*$", ln)
        if m:
            cur = m.group(1)
            out[cur] = ""
        elif cur:
            out[cur] += ln
    return out


HEAD, TAIL = blocks(root / "head.partial.html"), blocks(root / "tail.partial.html")


def switch(lang, key):
    """'FR | EN': the current language in bold, the other a link to the equivalent page (or its home)."""
    if lang == "fr":
        href = EQUIV[key][1] if key in EQUIV and AVAIL.get(key) else ("/en/" if AVAIL["home"] else None)
        other = f'<a href="{href}" hreflang="en" lang="en">EN</a>' if href else '<span class="off" lang="en">EN</span>'
        return f'<span class="lang-switch" aria-label="Langue / Language"><b aria-current="true">FR</b> | {other}</span>'
    href = EQUIV[key][0] if key in EQUIV else "/"
    return f'<span class="lang-switch" aria-label="Language / Langue"><a href="{href}" hreflang="fr" lang="fr">FR</a> | <b aria-current="true">EN</b></span>'


def alternates(key):
    if key not in EQUIV or not AVAIL.get(key):
        return ""
    fr, en = EQUIV[key]
    return (f'<link rel="alternate" hreflang="fr" href="{BASE}{fr}">\n<link rel="alternate" hreflang="en" href="{BASE}{en}">\n'
            f'<link rel="alternate" hreflang="x-default" href="{BASE}{fr}">\n')


def path_of(lang, key):
    return SHARED[key] if key in SHARED else EQUIV[key][0 if lang == "fr" else 1]


def head(lang, key, title, desc, og=None, skip="main"):
    path = path_of(lang, key)
    og_tags = ""
    if og:
        og_tags = (f'<meta property="og:title" content="{e(og[0])}">\n<meta property="og:description" content="{e(og[1])}">\n'
                   f'<meta property="og:type" content="website">\n<meta property="og:url" content="{BASE}{path}">\n'
                   f'<meta property="og:locale" content="{L10N[lang]["locale"]}">\n<meta property="og:image" content="{BASE}/assets/canot-symbole.png">\n')
    rep = {"@@title@@": e(title, quote=False), "@@desc@@": e(desc), "@@canonical@@": BASE + path, "@@alternates@@": alternates(key),
           "@@og@@": og_tags, "@@skip_href@@": "#contact" if skip == "contact" else "#main",
           "@@skip_label@@": L10N[lang]["skip_contact" if skip == "contact" else "skip_main"], "@@switch@@": switch(lang, key)}
    h = HEAD[lang]
    for k, v in rep.items():
        h = h.replace(k, v)
    return h


def q4o_caps(h):
    """q4o is written Q4O in visible text (not in addresses, classes, file names)."""
    parts = re.split(r"(<[^>]+>)", h)
    for k, part in enumerate(parts):
        if part.startswith("<"):
            parts[k] = part.replace('alt="q4o"', 'alt="Q4O"')
        else:
            parts[k] = re.sub(r"(?<![\w./@-])q4o(?!\.\w|[\w/@-])", "Q4O", part)
    return "".join(parts)


# ---------------------------------------------------------------- discrepancy catalogue (English only)
def discrepancies():
    """docs/discrepancies.md (GitHub) and the body of site/discrepancies.html, from tools/ecarts.json."""
    data = json.loads((root / "ecarts.json").read_text())
    items = first(data, "measurements", "mesures", default=[])
    todo_items = first(data, "to_measure", "a_mesurer", default=[])
    F = lambda m, *k: first(m, *k)
    intro = ("What two engines return differently for the same query or the same type, **measured** with Canot: the query, "
             "the result on each side, the versions and the date. Nothing here is assumed: what is not measured yet is listed separately.")
    md = ["# Discrepancy catalogue", "", intro, ""]
    for m in items:
        md += [f"## {F(m, 'title', 'titre')}", "", f"*Measured on {F(m, 'measured_on', 'mesure_le')}. {F(m, 'versions')}.*", "",
               "```sql", F(m, "query", "requete"), "```", "", "| | |", "|---|---|",
               f"| **Source** ({F(m, 'source')}) | `{F(m, 'source_result', 'result_source', 'resultat_source')}` |",
               f"| **Copy** ({F(m, 'destination')}) | `{F(m, 'copy_result', 'result_copy', 'resultat_copie')}` |", "",
               f"**Why.** {F(m, 'cause', 'why')}", "", f"**What to do.** {F(m, 'what_to_do', 'fix', 'que_faire')}", ""]
    md += ["## To be measured", "", "Not measured yet: draw no conclusion from it.", ""] + [f"- **{t}**: {d}" for t, d in todo_items]
    md += ["", "## Suggest a discrepancy", "",
           "Open an issue with: the query or the type, the result from each engine, the versions, the date. "
           "A discrepancy without a reproducible result does not enter the catalogue.", ""]
    (DOCS / "discrepancies.md").write_text("\n".join(md))
    old = DOCS / "ecarts.md"
    if old.exists():
        old.unlink()
        print("removed docs/ecarts.md (replaced by docs/discrepancies.md)")
    cards = "".join(
        f'''<article class="ecart" id="{e(F(m, 'id'))}"><h2>{e(F(m, "title", "titre"))}</h2><p class="mono-line">Measured on {e(F(m, "measured_on", "mesure_le"))} · {e(F(m, "versions"))}</p>
<pre><code>{e(F(m, "query", "requete"))}</code></pre>
<dl class="duo"><div><dt>Source ({e(F(m, "source"))})</dt><dd><code>{e(F(m, "source_result", "result_source", "resultat_source"))}</code></dd></div><div><dt>Copy ({e(F(m, "destination"))})</dt><dd><code>{e(F(m, "copy_result", "result_copy", "resultat_copie"))}</code></dd></div></dl>
<p><b>Why.</b> {e(F(m, "cause", "why"))}</p><p><b>What to do.</b> {e(F(m, "what_to_do", "fix", "que_faire"))}</p></article>''' for m in items)
    todo = "".join(f"<li><b>{e(t)}</b>: {e(x)}</li>" for t, x in todo_items)
    return f'''<section class="s ecarts"><div class="wrap"><h1 class="h2like">Discrepancy catalogue</h1>
<p class="lead-p">What two engines return differently for the same query or the same type, measured with Canot: the query, the result on each side, the versions and the date. What is not measured yet is listed separately.</p>
{cards}
<h2>To be measured</h2><p class="lead-p">Not measured yet: draw no conclusion from it.</p><ul class="todo-list">{todo}</ul>
<p class="sources">Another discrepancy to suggest? Open an <a href="https://github.com/chiptuned/canot/issues">issue</a> with the query or the type, the result from each engine, the versions and the date.</p>
</div></section>'''


# ---------------------------------------------------------------- documentation (English only)
# section id, new file name, former file name, table-of-contents label
DOC_PAGES = [("commands", "commands.md", "commandes.md", "Commands"), ("scan", "scan.md", "scan.md", "The scan"),
             ("on-prem", "on-prem.md", "sur-site.md", "On-prem arrival"), ("targets", "targets.md", "cibles-ue.md", "Hosts")]
DOC_LINKS = {"commands.md": "#commands", "commandes.md": "#commands", "scan.md": "#scan", "on-prem.md": "#on-prem", "sur-site.md": "#on-prem",
             "targets.md": "#targets", "cibles-ue.md": "#targets", "discrepancies.md": "discrepancies.html", "ecarts.md": "discrepancies.html"}


def slug(t):
    """GitHub-style heading anchor, so links written for GitHub also work on the site."""
    t = re.sub(r"[^\w\- ]", "", t.strip().lower())
    return t.replace(" ", "-")


def inline(t):
    t = _html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)

    def link(m):
        href = m.group(2)
        page, _, frag = href.partition("#")
        if page in DOC_LINKS:
            target = DOC_LINKS[page]
            href = f"#{frag}" if frag and target.startswith("#") else (f"{target}#{frag}" if frag else target)
        return f'<a href="{href}">{m.group(1)}</a>'
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


def md(text, ids, headings, shift=1):
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + _html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            title = ln.lstrip("# ").strip()
            base = s = slug(title)
            k = 0
            while s in ids:
                k += 1
                s = f"{base}-{k}"
            ids.add(s)
            headings.append((n, title, s))
            out.append(f'<h{n + shift} id="{s}">{inline(title)}</h{n + shift}>')
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
            out.append("<table><tr>" + th + "</tr>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[2:]) + "</table>")
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


DOC_IDS = set()
DOC_ANCHORS = {}  # role -> anchor in docs.html ("local", "mcp"), also used to remap former French anchors


def docs():
    """Body of docs.html (from docs/*.md) and the agent files llms.txt, llms-full.txt."""
    parts, full, ids, headings = [], [], set(k for k, *_ in DOC_PAGES), []
    toc = []
    for key, fname, old, label in DOC_PAGES:
        path = DOCS / fname
        if not path.exists():
            warn(f"docs/{fname} not found, using the former docs/{old}")
            path = DOCS / old
        text = path.read_text()
        start = len(headings)
        parts.append(f'<section id="{key}">' + md(text, ids, headings) + "</section>")
        full.append(text.strip())
        toc.append((f"#{key}", label))
        for n, title, s in headings[start:]:
            low = s.lower()
            if key == "commands" and n == 2 and "mcp" in low and "mcp" not in DOC_ANCHORS:
                DOC_ANCHORS["mcp"] = (s, title)
            if key == "commands" and n == 2 and "local" in low and "prem" in low and "local" not in DOC_ANCHORS:
                DOC_ANCHORS["local"] = (s, title)
    DOC_IDS.update(ids)
    for role, at in (("local", 1), ("mcp", 2)):
        if role in DOC_ANCHORS:
            s, title = DOC_ANCHORS[role]
            toc.insert(at, (f"#{s}", inline(title.replace("`", ""))))
    mcp = "#" + DOC_ANCHORS.get("mcp", ("commands",))[0]
    local = "#" + DOC_ANCHORS.get("local", ("commands",))[0]
    section = ('<section class="s docs"><div class="wrap"><h1 class="h2like">Documentation</h1>'
               f'<p class="lead-p">One command, what it can do, and access for your agent. Ask <a href="{mcp}">the MCP agent</a> for the details, or read them here.</p>'
               '<p class="toc">' + "".join(f'<a href="{h}">{t}</a>' for h, t in toc) + "</p>" + "\n".join(parts) + "</div></section>")
    (SITE / "llms-full.txt").write_text("# Canot: full documentation\n\n" + "\n\n---\n\n".join(full) + "\n")
    (SITE / "llms.txt").write_text(f"""# Canot

> Free, open-source tool (Apache-2.0) that rehearses leaving a data platform (BigQuery, Snowflake, Databricks) for Apache Iceberg, in your own accounts: read-only scan, copy, comparison, query replay, signed evidence pack. No telemetry. Command: `uvx canot drill SOURCE DESTINATION`. Paid services are provided by Q4O.

## Documentation
- [Full documentation](https://canot.io/llms-full.txt): commands, local or on-prem exit, scan, hosts, MCP
- [Commands](https://canot.io/docs.html#commands): drill, scan, demo, verify-evidence, teardown, snowflake-setup, bigquery-setup, mcp
- [Local or on-prem exit](https://canot.io/docs.html{local})
- [Read-only scan](https://canot.io/docs.html#scan): what is read, with which rights
- [With an agent (MCP)](https://canot.io/docs.html{mcp}): `claude mcp add canot -- uvx canot mcp`

## Other
- [Discrepancy catalogue](https://canot.io/discrepancies.html): what two engines return differently, measured
- [Source code](https://github.com/chiptuned/canot)
- [Offers](https://canot.io/en/offers.html)
- [Site in French](https://canot.io/)
""")
    return section


# ---------------------------------------------------------------- links
FR_TO_EN = {"index.html": "", "pourquoi.html": "why.html", "methode.html": "method.html", "offres.html": "offers.html",
            "faq.html": "faq.html", "rapport.html": "report.html", "mentions-legales.html": "legal.html"}
EN_NAMES = {"", "index.html", "why.html", "method.html", "offers.html", "faq.html", "report.html", "legal.html"}
RENAMED = {"ecarts.html": "discrepancies.html", "gaps.html": "discrepancies.html"}
OLD_DOC_ANCHORS = {"commandes": "commands", "sur-site": "on-prem", "cibles": "targets"}


def fix_links(h, lang):
    """Make internal links absolute so pages work from / and /en/; route each link to the page of the right language."""
    def one(m):
        attr, url = m.group(1), m.group(2)
        if re.match(r"(?:[a-z][a-z0-9+.-]*:|//|#|\?)", url, re.I):
            return m.group(0)
        path, sep, rest = re.match(r"([^?#]*)([?#]?)(.*)", url).groups()
        absolute = path.startswith("/")
        name = path.lstrip("/")
        if absolute and name.startswith("en/"):
            return m.group(0)
        if name.startswith("../"):
            name = name[3:]
        name = RENAMED.get(name, name)
        if name == "docs.html" and sep == "#":
            rest = OLD_DOC_ANCHORS.get(rest, rest)
            if rest not in DOC_IDS:
                role = "local" if "local" in rest else "mcp" if "mcp" in rest else None
                if role and role in DOC_ANCHORS:
                    rest = DOC_ANCHORS[role][0]
                else:
                    warn(f"link to docs.html#{rest}: no such anchor")
        if lang == "en" and not absolute:  # absolute paths are already language-specific (partials)
            en = FR_TO_EN.get(name, name)
            if en in EN_NAMES:
                return f'{attr}="/en/{"" if en == "index.html" else en}{sep}{rest}"'
        if name == "index.html":
            name = ""
        return f'{attr}="/{name}{sep}{rest}"'
    return re.sub(r'\b(href|src)="([^"]*)"', one, h)


def selector_ids(h):
    """The hero selector's action id is 'replay' (formerly 'rejeu'); app.js still accepts both."""
    h = re.sub(r'(data-cs-show="[^"]*)\brejeu\b', r"\1replay", h)
    h = re.sub(r'(data-cs-show="[^"]*)\brejeu\b', r"\1replay", h)
    return h.replace('data-cs-pick-act="rejeu"', 'data-cs-pick-act="replay"').replace("?action=rejeu", "?action=replay")


# ---------------------------------------------------------------- assembly
GENERATED = {}  # site-relative output path -> (lang, key)


def write(lang, key, html):
    out = path_of(lang, key)
    rel = (out + "index.html" if out.endswith("/") else out).lstrip("/")
    html = q4o_caps(selector_ids(fix_links(html.replace("@@q4olines@@", Q4O_LINES), lang)))
    assert not re.search(r"@@\w+@@", html), f"placeholder left in {rel}: {re.findall(r'@@\w+@@', html)}"
    (SITE / rel).parent.mkdir(parents=True, exist_ok=True)
    (SITE / rel).write_text(html)
    GENERATED[rel] = (lang, key)


def home(lang, template):
    html = template.read_text()
    for key, label, size in SEALS:
        html = html.replace(f"@@{key}@@", seal(key, label, size, lang))
    html = html.replace("@@wave_back@@", wave(90, 22, "#E6ECF2")).replace("@@wave_front@@", wave(52, 10, "#D6E0E9"))
    booking = os.environ.get("CANOT_BOOKING_URL", "").strip()
    html = html.replace("@@booking@@", f'<a class="btn btn-line" href="{booking}">{L10N[lang]["booking"]}</a>' if booking else "")
    body = re.search(r"<main[^>]*>(.*)</main>", html, re.S)
    if not body:
        raise SystemExit(f"{template.name}: no <main> element")
    meta = dict(HOME[lang])
    d = re.search(r'<meta name="description" content="([^"]*)">', html)
    ogt = re.search(r'<meta property="og:title" content="([^"]*)">', html)
    ogd = re.search(r'<meta property="og:description" content="([^"]*)">', html)
    if lang == "en":  # the English home title is fixed; the rest follows the template when it has it
        meta.update({k: _html.unescape(v.group(1)) for k, v in (("desc", d), ("og_title", ogt), ("og_desc", ogd)) if v})
    write(lang, "home", head(lang, "home", meta["title"], meta["desc"], (meta["og_title"], meta["og_desc"]), skip="contact")
          + body.group(1).strip("\n") + "\n" + TAIL[lang])


def secondary(lang, key, title, desc, body):
    write(lang, key, head(lang, key, f"{title} | Canot", desc) + "\n" + body + "\n" + L10N[lang]["strip"] + "\n" + TAIL[lang])


def main():
    docs_body, disc_body = docs(), discrepancies()  # first: the docs anchors are needed to route links
    # French home and pages
    home("fr", root / "index.template.html")
    for key, (parts, tfr, dfr, ten, den) in PAGES.items():
        secondary("fr", key, tfr, dfr, "\n\n".join((root / "sections" / f"{p}.html").read_text() for p in parts))
    # English home and pages (each skipped while its source is missing)
    if AVAIL["home"]:
        home("en", EN_TEMPLATE)
    for key, (parts, tfr, dfr, ten, den) in PAGES.items():
        if AVAIL[key]:
            secondary("en", key, ten, den, "\n\n".join((EN_SECTIONS / f"{p}.html").read_text() for p in parts))
    # Remove generated English pages whose source disappeared (never the static en/report.html, en/legal.html)
    for key in ["home", *PAGES]:
        rel = (EQUIV[key][1] + ("index.html" if key == "home" else "")).lstrip("/")
        if not AVAIL[key] and (SITE / rel).exists():
            (SITE / rel).unlink()
            print(f"removed stale site/{rel}")
    # Shared English pages
    secondary("en", "docs", "Documentation", "Canot documentation: the commands, local or on-prem exit, the read-only scan, and access for an agent (MCP).", docs_body)
    secondary("en", "discrepancies", "Discrepancy catalogue", "What two engines return differently for the same query, measured with Canot: query, results, versions, date.", disc_body)
    (SITE / "ecarts.html").write_text("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Discrepancy catalogue | Canot</title>
<meta name="robots" content="noindex">
<link rel="canonical" href="https://canot.io/discrepancies.html">
<meta http-equiv="refresh" content="0; url=/discrepancies.html">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<main class="wrap prose"><p>The discrepancy catalogue has moved: <a href="/discrepancies.html">canot.io/discrepancies.html</a>.</p>
<p lang="fr">Le catalogue des écarts a déménagé : <a href="/discrepancies.html">canot.io/discrepancies.html</a>.</p></main>
</body>
</html>
""")
    print("pages written:", ", ".join(sorted(GENERATED)) + ", ecarts.html (redirect)")
    static_en()
    sitemap()
    busting()


def static_en():
    """The static English pages (en/report.html, en/legal.html) get the hreflang links and the language switch.
    Idempotent; their content is left untouched."""
    for lang, idx in (("en", 1), ("fr", 0)):
      for key in ("report", "legal"):
        page = SITE / EQUIV[key][idx].lstrip("/")
        if not page.exists():
            continue
        x = page.read_text()
        if 'hreflang="x-default"' not in x:
            x = x.replace('<link rel="icon"', alternates(key) + '<link rel="icon"', 1)
        if "lang-switch" not in x:
            sw = switch(lang, key)
            if '<div class="bar">' in x:  # report: next to "Get yours"
                x = re.sub(r'(<div class="bar">.*?)(\n\s*</div>)', lambda m: m.group(1) + " " + sw + m.group(2), x, count=1, flags=re.S)
            else:  # legal: in the header, after the brand
                x = re.sub(r'(<header class="top"><div class="wrap">.*?</a>)', lambda m: m.group(1) + "\n  " + sw, x, count=1, flags=re.S)
        page.write_text(x)


def sitemap():
    """sitemap.xml: every indexable page, with its hreflang equivalents."""
    def indexable(rel):
        p = SITE / rel
        return p.exists() and 'name="robots" content="noindex"' not in p.read_text()
    rel_of = lambda path: (path + "index.html" if path.endswith("/") else path).lstrip("/")
    urls = []
    for key, (fr, en) in EQUIV.items():
        has_en = AVAIL.get(key) and indexable(rel_of(en))
        alt = ""
        if has_en:
            alt = (f'\n    <xhtml:link rel="alternate" hreflang="fr" href="{BASE}{fr}"/>\n    <xhtml:link rel="alternate" hreflang="en" href="{BASE}{en}"/>'
                   f'\n    <xhtml:link rel="alternate" hreflang="x-default" href="{BASE}{fr}"/>')
        if indexable(rel_of(fr)):
            urls.append(f"  <url>\n    <loc>{BASE}{fr}</loc>{alt}\n  </url>")
        if has_en:
            urls.append(f"  <url>\n    <loc>{BASE}{en}</loc>{alt}\n  </url>")
    for path in SHARED.values():
        urls.append(f"  <url>\n    <loc>{BASE}{path}</loc>\n  </url>")
    (SITE / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
                                      + "\n".join(urls) + "\n</urlset>\n")
    print(f"sitemap.xml: {len(urls)} urls")


def busting():
    """Add ?v=<fingerprint> to the style.css and app.js links of every page."""
    tag = {n: hashlib.sha256((SITE / n).read_bytes()).hexdigest()[:10] for n in ("style.css", "app.js")}
    for page in [*SITE.glob("*.html"), *SITE.glob("en/*.html")]:
        x = page.read_text()
        for n, v in tag.items():
            x = re.sub(rf'((?:href|src)="(?:/|\.\./)?){re.escape(n)}(?:\?v=[0-9a-f]+)?"', rf'\g<1>{n}?v={v}"', x)
        page.write_text(x)
    print("fingerprints:", tag)


Q4O_LINES = '<polyline points="0,40.0 40,42.3 80,44.8 120,47.4 160,49.8 200,52.0 240,53.9 280,55.2 320,55.8 360,55.8 400,54.9 440,53.3 480,50.8 520,47.6 560,43.9 600,39.7 640,35.2 680,30.6 720,26.2 760,22.2 800,18.7 840,16.1 880,14.4 920,13.9 960,14.5 1000,16.4 1040,19.4 1080,23.6 1120,28.7 1160,34.6 1200,41.0" fill="none" stroke="#4cb3bf" stroke-opacity="0.100" stroke-width="1"/><polyline points="0,60.8 40,63.3 80,65.7 120,68.0 160,70.0 200,71.5 240,72.4 280,72.7 320,72.1 360,70.8 400,68.7 440,65.8 480,62.2 520,58.1 560,53.7 600,49.1 640,44.6 680,40.3 720,36.6 760,33.6 800,31.5 840,30.5 880,30.7 920,32.2 960,34.9 1000,38.7 1040,43.6 1080,49.4 1120,55.8 1160,62.5 1200,69.4" fill="none" stroke="#4cb3bf" stroke-opacity="0.112" stroke-width="1"/><polyline points="0,81.5 40,83.8 80,85.9 120,87.6 160,88.8 200,89.3 240,89.1 280,88.1 320,86.3 360,83.7 400,80.4 440,76.5 480,72.2 520,67.7 560,63.1 600,58.7 640,54.7 680,51.4 720,48.9 760,47.5 800,47.3 840,48.3 880,50.5 920,54.0 960,58.6 1000,64.2 1040,70.5 1080,77.3 1120,84.3 1160,91.1 1200,97.4" fill="none" stroke="#4cb3bf" stroke-opacity="0.124" stroke-width="1"/><polyline points="0,101.7 40,103.5 80,104.9 120,105.7 160,105.8 200,105.2 240,103.7 280,101.5 320,98.5 360,94.9 400,90.7 440,86.3 480,81.7 520,77.2 560,73.0 600,69.4 640,66.6 680,64.8 720,64.1 760,64.7 800,66.5 840,69.5 880,73.8 920,79.1 960,85.2 1000,92.0 1040,99.0 1080,106.0 1120,112.7 1160,118.7 1200,123.6" fill="none" stroke="#4cb3bf" stroke-opacity="0.136" stroke-width="1"/><polyline points="0,120.8 40,121.8 80,122.2 120,121.9 160,120.9 200,119.0 240,116.4 280,113.0 320,109.1 360,104.8 400,100.3 440,95.8 480,91.5 520,87.7 560,84.6 600,82.4 640,81.3 680,81.3 720,82.7 760,85.3 800,89.2 840,94.1 880,100.0 920,106.7 960,113.7 1000,120.8 1040,127.7 1080,134.1 1120,139.5 1160,143.7 1200,146.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.148" stroke-width="1"/><polyline points="0,138.4 40,138.4 80,137.7 120,136.2 160,134.0 200,131.0 240,127.4 280,123.4 320,119.0 360,114.5 400,110.2 440,106.2 480,102.8 520,100.3 560,98.7 600,98.3 640,99.2 680,101.3 720,104.7 760,109.3 800,114.9 840,121.3 880,128.3 920,135.5 960,142.6 1000,149.2 1040,155.0 1080,159.8 1120,163.1 1160,164.9 1200,164.9" fill="none" stroke="#4cb3bf" stroke-opacity="0.160" stroke-width="1"/><polyline points="0,154.3 40,153.2 80,151.4 120,148.8 160,145.6 200,141.8 240,137.6 280,133.3 320,128.9 360,124.8 400,121.3 440,118.4 480,116.5 520,115.6 560,116.0 600,117.7 640,120.6 680,124.7 720,130.0 760,136.1 800,142.9 840,150.1 880,157.2 920,164.1 960,170.3 1000,175.5 1040,179.4 1080,181.8 1120,182.4 1160,181.2 1200,178.2" fill="none" stroke="#4cb3bf" stroke-opacity="0.172" stroke-width="1"/><polyline points="0,168.5 40,166.3 80,163.5 120,160.1 160,156.2 200,152.0 240,147.7 280,143.6 320,139.9 360,136.8 400,134.5 440,133.2 480,133.1 520,134.3 560,136.7 600,140.4 640,145.2 680,151.0 720,157.6 760,164.6 800,171.8 840,178.8 880,185.3 920,190.9 960,195.3 1000,198.3 1040,199.6 1080,199.0 1120,196.6 1160,192.3 1200,186.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.184" stroke-width="1"/><polyline points="0,181.2 40,178.2 80,174.6 120,170.7 160,166.6 200,162.5 240,158.7 280,155.4 320,152.8 360,151.2 400,150.6 440,151.3 480,153.2 520,156.3 560,160.7 600,166.1 640,172.3 680,179.2 720,186.3 760,193.4 800,200.1 840,206.0 880,210.9 920,214.4 960,216.4 1000,216.5 1040,214.7 1080,211.1 1120,205.7 1160,198.6 1200,190.2" fill="none" stroke="#4cb3bf" stroke-opacity="0.196" stroke-width="1"/><polyline points="0,192.9 40,189.3 80,185.4 120,181.4 160,177.6 200,174.2 240,171.4 280,169.4 320,168.4 360,168.6 400,169.9 440,172.6 480,176.4 520,181.4 560,187.2 600,193.8 640,200.8 680,207.9 720,214.7 760,220.9 800,226.2 840,230.2 880,232.8 920,233.5 960,232.5 1000,229.5 1040,224.7 1080,218.2 1120,210.2 1160,201.0 1200,191.0" fill="none" stroke="#4cb3bf" stroke-opacity="0.208" stroke-width="1"/><polyline points="0,204.1 40,200.3 80,196.6 120,193.1 160,190.2 200,187.9 240,186.5 280,186.2 320,187.1 360,189.1 400,192.4 440,196.9 480,202.3 520,208.5 560,215.3 600,222.3 640,229.2 680,235.6 720,241.2 760,245.7 800,248.8 840,250.2 880,249.9 920,247.6 960,243.4 1000,237.5 1040,230.0 1080,221.2 1120,211.4 1160,201.2 1200,190.8" fill="none" stroke="#4cb3bf" stroke-opacity="0.220" stroke-width="1"/><polyline points="0,215.7 40,212.2 80,209.1 120,206.6 160,204.9 200,204.2 240,204.5 280,206.1 320,208.8 360,212.7 400,217.6 440,223.5 480,229.9 520,236.7 560,243.6 600,250.1 640,256.0 680,260.9 720,264.5 760,266.5 800,266.8 840,265.3 880,261.8 920,256.6 960,249.6 1000,241.3 1040,231.8 1080,221.7 1120,211.3 1160,201.1 1200,191.7" fill="none" stroke="#4cb3bf" stroke-opacity="0.232" stroke-width="1"/><polyline points="0,228.2 40,225.6 80,223.6 120,222.5 160,222.3 200,223.3 240,225.5 280,228.8 320,233.2 360,238.6 400,244.7 440,251.2 480,258.0 520,264.5 560,270.6 600,275.8 640,279.9 680,282.5 720,283.4 760,282.6 800,279.8 840,275.2 880,268.9 920,261.1 960,252.1 1000,242.2 1040,231.8 1080,221.6 1120,211.8 1160,203.1 1200,195.9" fill="none" stroke="#4cb3bf" stroke-opacity="0.244" stroke-width="1"/><polyline points="0,242.5 40,241.1 80,240.5 120,241.0 160,242.6 200,245.3 240,249.1 280,254.0 320,259.6 360,265.8 400,272.3 440,278.9 480,285.0 520,290.5 560,294.9 600,298.0 640,299.6 680,299.4 720,297.4 760,293.6 800,287.9 840,280.7 880,272.1 920,262.5 960,252.4 1000,242.1 1040,232.2 1080,223.1 1120,215.3 1160,209.2 1200,205.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.256" stroke-width="1"/><polyline points="0,259.0 40,259.0 80,260.0 120,262.1 160,265.4 200,269.6 240,274.8 280,280.6 320,286.8 360,293.2 400,299.3 440,305.0 480,309.7 520,313.3 560,315.4 600,315.9 640,314.6 680,311.5 720,306.6 760,300.0 800,292.0 840,282.8 880,272.9 920,262.7 960,252.6 1000,243.3 1040,235.0 1080,228.3 1120,223.6 1160,221.2 1200,221.3" fill="none" stroke="#4cb3bf" stroke-opacity="0.268" stroke-width="1"/><polyline points="0,277.8 40,279.4 80,282.0 120,285.6 160,290.2 200,295.5 240,301.4 280,307.5 320,313.6 360,319.3 400,324.3 440,328.2 480,330.9 520,332.0 560,331.4 600,329.0 640,324.8 680,318.9 720,311.5 760,302.9 800,293.3 840,283.3 880,273.2 920,263.6 960,255.0 1000,247.8 1040,242.3 1080,239.1 1120,238.3 1160,240.1 1200,244.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.280" stroke-width="1"/><polyline points="0,299.0 40,302.0 80,305.9 120,310.7 160,316.1 200,321.9 240,327.8 280,333.5 320,338.6 360,342.9 400,346.0 440,347.7 480,347.8 520,346.1 560,342.7 600,337.5 640,330.8 680,322.7 720,313.6 760,303.8 800,293.9 840,284.2 880,275.2 920,267.5 960,261.5 1000,257.4 1040,255.8 1080,256.6 1120,260.1 1160,266.1 1200,274.5" fill="none" stroke="#4cb3bf" stroke-opacity="0.292" stroke-width="1"/>'


if __name__ == "__main__":
    main()
