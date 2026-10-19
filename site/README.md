# canot.io site

Static site (HTML, CSS, a single script). No tracker, no external font, no inline style or script (see `_headers`).
Local preview: `python3 -m http.server -d site 8000`.

## Languages

French is the default, at the root; English lives under `/en/`. There is no automatic redirect: a "FR | EN" switch
in the header links each page to its equivalent, and English-language browsers get a discreet banner on the French home page.

| French (root) | English |
|---|---|
| `/` | `/en/` |
| `/pourquoi.html` | `/en/why.html` |
| `/methode.html` | `/en/method.html` |
| `/offres.html` | `/en/offers.html` |
| `/faq.html` | `/en/faq.html` |
| `/rapport.html` (static) | `/en/report.html` (static) |
| `/mentions-legales.html` (static) | `/en/legal.html` (static) |

English only, shared by both languages: `/docs.html` (from `docs/*.md`) and `/discrepancies.html` (from `tools/ecarts.json`).
`/ecarts.html` redirects to `/discrepancies.html`.

## Build

`python3 tools/build_site.py` generates every page except the four static ones:

- French: `tools/index.template.html` + `tools/sections/<name>.html`
- English: `tools/index.template.en.html` + `tools/sections/en/<name>.html` (a page whose source is missing is skipped with a warning)
- header and footer: `tools/head.partial.html`, `tools/tail.partial.html` (one block per language)
- also: `docs/discrepancies.md`, `llms.txt`, `llms-full.txt`, `sitemap.xml`, and the `?v=` fingerprints of `style.css` and `app.js`

Internal links are rewritten to absolute paths, so pages work from `/` and from `/en/`.

## Deploy (Cloudflare Pages)

`wrangler pages deploy site --project-name canot-site`, then attach the canot.io domain to the project.
Form: set the project secret `CONTACT_WEBHOOK_URL` (e.g. an ntfy topic). Requests are stored in the D1 database
`canot-leads` (`schema.sql`; read them with `tools/leads.sh`). Without a database or webhook, sending fails cleanly
and the page shows the e-mail address.

Logo: `assets/` (Canot kit, chosen version). Font of the word "canot": Fredoka, SIL OFL licence (`assets/fonts/OFL.txt`).
