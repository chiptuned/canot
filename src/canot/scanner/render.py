"""Scan rendering: scan.json -> scan.html (self-contained, printable on A4, no script and no external resource) and CSV.

render_html is pure. Each list shows at most 20 rows; the rest is in the CSV files (or in scan.json).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
from pathlib import Path

from .. import brand as _brand
from .. import regulatory as _regulatory
from .. import connectors
from .analyze import (CODES_HORS_ENTREPOT, ETIQUETTE, KIND_LABELS, LIBELLES, MENTION_REJOUABILITE, cap, fmt_bytes, fmt_date, fmt_num,
                      parse_dt, pl, short_vue)
from .model import COMPUTED, ESTIMATED, MEASURED, NOT_ESTIMATED, NOT_READ, READ

MAX_ROWS = 20
CANOT_URL = "https://canot.io"
NATURES = {MEASURED: "measured", COMPUTED: "computed", ESTIMATED: "estimated", NOT_READ: "not read", NOT_ESTIMATED: "not estimated"}
CATEGORIES = {"C0": "translated and accepted by DuckDB", "C1": "not parseable", "C2": "command, not analyzed", "C3": "translation impossible",
              "C4": "DuckDB parse error", "C5": "binding error (platform-specific object or function)", "C6": "variable",
              "C7": "DDL, COPY, MERGE, metadata queries: counted separately"}
NIVEAUX = {1: "inventory, volumes, consumption", 2: "+ queries (normalized forms), reads per table, policies",
           3: "+ privileges and tools (pseudonymized)"}
# label, privileges requested and never requested: connector profile (connectors.py), no table here anymore
PORTABILITES = {"portable_tel_quel": "portable as is", "traduisible": "translatable (open text, replay possible)",
                "exporte_proprietaire": "exported in the vendor's format", "a_reconstruire": "to rebuild",
                "non_determine": "not determined (not read)"}
FAMILLES_OBJET = {"donnees": "data", "traitement": "processing", "modele": "model", "rapport": "report", "planification": "scheduling",
                  "connexion": "connection", "securite": "security", "code": "code", "extension": "extension", "non_precisee": "unspecified"}
# inventory depth declared by a connector (connectors.Capabilities.inventaire, French data values)
INVENTAIRES = {"complet": "full", "léger": "light", "partiel": "partial", "aucun": "none"}
# flag target types and table types (French data values in scan.json) -> displayed label
CIBLES = {"objet": "object", "famille": "family", "lecture": "read", "role": "role", "table": "table"}
TABLE_TYPES = {"EXTERNE": "EXTERNAL", "TRANSITOIRE": "TRANSIENT", "TEMPORAIRE": "TEMPORARY"}
# summary boxes: scan.json keys (unchanged) -> reader
AUDIENCES = {"DSI": "CIO", "DAF": "CFO", "RSSI": "CISO"}
# in-page anchors, in order
SECTION_IDS = ("header", "scope", "capabilities", "costs", "movements", "stays-behind", "exit-map", "dependencies", "replayability", "ranges",
               "findings", "not-estimated", "method", "next")

CSS = """:root{--bg:#ffffff;--fg:#14212b;--mut:#4d6173;--line:#dfe6eb;--soft:#eef3f6;--acc:#1f3a5f;--tag:#8a4b0f;--tagbg:#fbf1e6}
@media (prefers-color-scheme:dark){:root{--bg:#10171d;--fg:#e3eaf0;--mut:#9aaebe;--line:#2a3742;--soft:#18232c;--acc:#9cc3e6;--tag:#f0b77a;--tagbg:#2e2318}}
@page{size:A4;margin:16mm 14mm}
html{background:var(--bg)}
body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:980px;margin:2rem auto;padding:0 16px;color:var(--fg);background:var(--bg)}
h1{font-size:1.6rem;margin:.3rem 0}h2{font-size:1.2rem;margin-top:2.2rem;border-bottom:1px solid var(--line);padding-bottom:.3rem}
h3{font-size:1rem;margin:1.2rem 0 .4rem}
table{border-collapse:collapse;width:100%;font-size:13px;margin:.4rem 0}
th,td{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top;overflow-wrap:break-word}
th{color:var(--mut);font-weight:600}th[scope=row]{min-width:9em}td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
td{overflow-wrap:anywhere}th[scope=row],td.n,.nat{overflow-wrap:normal}
code{background:var(--soft);padding:1px 4px;border-radius:3px;font-size:12px;overflow-wrap:anywhere}
.diffusion{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:2px 10px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--mut)}
.meta,.mut{color:var(--mut)}.more{color:var(--mut);font-size:13px;margin:.2rem 0 .8rem}
.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:1rem}
.box{background:var(--soft);border-radius:6px;padding:.7rem 1rem}.box h3{margin:0 0 .3rem;font-size:.9rem;color:var(--acc)}.box p{margin:0}
.alerte{border:2px solid var(--tag);border-radius:6px;padding:.6rem 1rem;margin:1rem 0}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:1px 9px;border-radius:999px;background:var(--tagbg);color:var(--tag);margin-left:.4rem;vertical-align:1px}
.nat{color:var(--mut);font-size:12px;min-width:10em}.hyp{color:var(--mut);font-size:12px;margin:.2rem 0 0;padding-left:1.1rem}
a{color:var(--acc)}
.suite{border-top:2px solid var(--acc);margin-top:2.4rem;padding-top:.6rem}
@media (max-width:640px){.trio{grid-template-columns:1fr}table{font-size:12px}th,td{padding:4px 3px}th[scope=row]{min-width:6em}.nat{min-width:7em}}
@media print{:root{--bg:#fff;--fg:#000;--mut:#444;--line:#ccc;--soft:#f2f2f2;--acc:#1f3a5f;--tag:#6b3a0b;--tagbg:#fff}
body{max-width:none;margin:0;padding:0;font-size:10.5px}h2{break-after:avoid}tr,.box,.alerte{break-inside:avoid}}"""
CSS += _brand.CSS


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _val(v, unite: str | None) -> str:
    if unite == "bytes":
        return fmt_bytes(v)
    if unite == "USD":
        return f"${fmt_num(v, 2)}"
    if unite == "credits":
        return f"{fmt_num(v, 2)} credits"
    if unite == "hours":
        if abs(v) < 1 / 60:
            return "less than a minute"
        if abs(v) < 1:
            return f"{fmt_num(round(v * 60))} min"
        return f"{fmt_num(v, 2 if abs(v) < 10 else 1)} h"
    if unite == "days":
        return f"{fmt_num(v, 1)} d"
    if unite == "seconds":
        return f"{fmt_num(v)} s"
    if unite == "%":
        return f"{fmt_num(v, 1)}%"
    if unite == "slot-hours":
        return f"{fmt_num(v, 1)} slot-hours"
    if unite == "queries":
        return pl(v, "query", "queries")
    return fmt_num(v) + (f" {unite}" if unite else "")


def fig_text(f: dict) -> str:
    if f["nature"] == NOT_READ:
        return f"Not read: {f['raison']}."
    if f["nature"] == NOT_ESTIMATED:
        return f"Not estimated: {f['raison']}."
    if f["nature"] == ESTIMATED:
        lo, hi = _val(f["bas"], f["unite"]), _val(f["haut"], f["unite"])
        return lo if lo == hi else f"{lo} to {hi}"
    return _val(f["valeur"], f["unite"])


def _base(ids: list[str]) -> str:
    return ", ".join(ids[:6]) + (" …" if len(ids) > 6 else "")


def _fig_cells(f: dict) -> str:
    hyp = "".join(f"<li>{_e(h)}</li>" for h in f.get("hypotheses") or [])
    nat = NATURES[f["nature"]] + (f", {f['confiance']} confidence" if f.get("confiance") else "") + (f" · {_base(f['base'])}" if f.get("base") else "")
    return (f"<td>{_e(fig_text(f))}" + (f"<ul class=hyp>{hyp}</ul>" if hyp else "") + "</td>"
            f"<td class=nat>{_e(nat)}" + (f"<br>{_e(f['formule'])}" if f.get("formule") else "") + "</td>")


def _fig_table(rows: list[tuple[str, dict]]) -> str:
    body = "".join(f"<tr><th scope=row>{_e(label)}</th>{_fig_cells(f)}</tr>" for label, f in rows)
    return f"<table><tr><th>Item</th><th>Value</th><th>Nature and basis</th></tr>{body}</table>"


def _list_table(header: list[str], rows: list[list[str]], more_in: str, numeric: tuple[int, ...] = ()) -> str:
    """rows: cells already escaped. At most MAX_ROWS rows; the rest is referred to `more_in`."""
    if not rows:
        return "<p class=mut>None.</p>"
    head = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td class=n>{c}</td>" if i in numeric else f"<td>{c}</td>" for i, c in enumerate(r)) + "</tr>"
                   for r in rows[:MAX_ROWS])
    more = f"<p class=more>… and {fmt_num(len(rows) - MAX_ROWS)} more in {_e(more_in)}.</p>" if len(rows) > MAX_ROWS else ""
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def _num(v) -> str:
    return "not read" if v is None else fmt_num(v)


def _trim(s: str) -> str:
    return s[:-2] if s.endswith(".0") else s


def _poids(v, plateforme: str) -> str:
    if v is None:
        return "not read"
    return fmt_bytes(v) if plateforme == "bigquery" else f"{fmt_num(v, 2)} credits"


def _plateforme(r: dict) -> str:
    c = connectors.get(r["plateforme"])
    if c:
        return c.label
    return (r.get("capacites") or {}).get("libelle") or r["plateforme"]


def _section(sid: str, title: str, body: str) -> str:
    return f'<section id="{sid}"><h2>{_e(title)}</h2>{body}</section>'


def _recentes(r: dict) -> int:
    """Tables seen only in real time (missing from ACCOUNT_USAGE): their history is not available yet."""
    return sum(1 for t in r["tables"] if not t["supprimee_le"] and "recente" in t["flags"])


# --- sections ----------------------------------------------------------------------------------------------


def _entete(r: dict) -> str:
    as_of = parse_dt(r["as_of"])
    meta = [f"Findings as of {as_of.strftime('%Y-%m-%d %H:%M')} UTC"]
    if r.get("latence_max_h") is not None:
        meta.append(f"data up to {_trim(fmt_num(r['latence_max_h'], 1))} h behind depending on the view")
    meta += [f"edition: {r['edition']}", f"Canot {(r.get('versions') or {}).get('canot', 'unknown version')}"]
    alerte = ("<p class=alerte><b>Raw SQL text exported.</b> This scan was run with --include-sql-text: the file "
              "scan-sql-raw.json, next to this report, contains the raw text of your queries, literals included (permissions 0600). "
              "It is not meant for distribution.</p>") if r.get("avec_sql_brut") else ""
    boxes = "".join(f"<div class=box><h3>{AUDIENCES[k]}</h3><p>{_e(' '.join(r['resume'][k]))}</p></div>" for k in ("DSI", "DAF", "RSSI"))
    from .run import partage  # the shareable summary: counts only

    return (f'<header id="header">{_brand.brand_bar("Exit scan")}<p class=diffusion>Internal distribution</p>'
            f"<h1>Exit scan: {_e(_plateforme(r))}</h1>"
            f"<p class=meta>{_e(' · '.join(meta))}</p>{alerte}<div class=trio>{boxes}</div>"
            f'{_brand.share_block(partage(r), "scan-share.json")}</header>')


def _perimetre(r: dict) -> str:
    lec = r["cout_du_scan"]["lectures"]
    kinds = r["ne_part_pas"]["objets_par_type"]
    items = [
        f"Platform: {_plateforme(r)}",
        f"Scope: {r['perimetre'] or 'not specified'}",
        f"Privilege level: {r['niveau_droits']} ({NIVEAUX.get(r['niveau_droits'], 'not specified')})",
        f"SQL mode: {r['mode_sql']}",
        f"Reads: {fmt_num(lec['lu'])} done out of {fmt_num(lec['total'])}",
        f"Content read: {pl(len([t for t in r['tables'] if not t['supprimee_le']]), 'table')}, "
        f"{pl(sum(kinds.values()), 'object')}, {pl(len(r['familles']), 'query family', 'query families')}",
    ]
    if r.get("schemas_caches"):
        items.append(f"Hidden schemas or datasets read: {', '.join(r['schemas_caches'])}")
    if (r.get("fraicheur") or {}).get("texte"):
        items.append(f"Freshness: {r['fraicheur']['texte']}")
    credits = (r["couts"].get("credits") or {}).get("valeur") or 0
    if not any(not t["supprimee_le"] for t in r["tables"]) and (r["familles"] or credits > 0):
        items.append("Empty inventory although queries or credits were read: check the scanned database and the role's privileges "
                     "on its objects")
    v = r.get("versions") or {}
    items.append("Versions: " + ", ".join(f"{k} {v[k]}" for k in sorted(v)))
    return _section("scope", "Scope", "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")


def _capacites(r: dict) -> str:
    """What Canot can do on this platform (declared by the connector), and what this report did: an inventory."""
    c = r.get("capacites")
    if not c:
        return ""
    lab = _plateforme(r)
    niv = c.get("niveaux") or []
    inv = INVENTAIRES.get(c.get("inventaire"), c.get("inventaire")) or "not declared"
    if niv:
        inv += f", levels {niv[0]} to {niv[-1]}" if len(niv) > 1 else f", level {niv[0]}"
    export, preuve = c.get("export") or [], [p for p in c.get("preuve") or [] if isinstance(p, dict)]
    fiche = connectors.profile(r["plateforme"])
    commande = fiche.get("commande_preuve", "canot drill")
    rows = [[_e("Inventory"), _e(f"done by this scan (level {r['niveau_droits']})" if r.get("niveau_droits") else "done by this scan"), _e(inv)],
            [_e("Export"), _e("not done by this scan"), _e(f"{', '.join(export)}, with canot drill" if export else "no format declared")],
            [_e("Evidence"), _e("not done by this scan"),
             _e("; ".join(f"{p.get('forme')} {p.get('libelle')}" for p in preuve) + f", with {commande}" if preuve else "no form declared")]]
    statut = f"{lab} connector: {c.get('statut') or 'status not declared'}"
    statut += f"; recorded test: {c['teste_sur']}." if c.get("teste_sur") else "; no test date recorded in the repository."
    if preuve and fiche.get("limite_preuve"):
        limite = f"What Canot cannot prove here: {fiche['limite_preuve']}"
    elif preuve:
        limite = (f"What Canot cannot prove here: the declared forms ({', '.join(str(p.get('forme')) for p in preuve)}) cover "
                  "tables and queries; objects to rebuild besides tables are counted, not proven.")
    else:
        limite = f"No form of evidence is declared for {lab}: objects are counted, not proven."
    body = (f"<p>This report is an inventory: inventoried, neither exported nor replayed.</p>"
            + _list_table(["Level", "This report", f"Canot on {lab}"], rows, "scan.json")
            + f"<p class=mut>{_e(statut)}</p><p>{_e(limite)}</p>")
    return _section("capabilities", "Capabilities", body)


def _carte_sortie(r: dict) -> str:
    """Objects per portability and object family: only when collection read the portability (canot.scan/2)."""
    carte = r.get("carte_sortie") or {}
    if not carte:
        return ""
    rows = [[_e(PORTABILITES.get(p, p)), _e(FAMILLES_OBJET.get(f, f.replace("_", " "))), _e(fmt_num(n))]
            for p, fams in carte.items() for f, n in fams.items()]
    body = ("<p class=mut>Portability declared by collection for each object read. \"Exported\" is not \"portable\": "
            "an archive in the vendor's format can only be used with that vendor.</p>"
            + _list_table(["Portability", "Object family", "Objects"], rows, "scan.json", (2,)))
    return _section("exit-map", "Exit map", body)


def _couts(r: dict) -> str:
    c = r["couts"]
    if r.get("famille") == "fichiers":
        return _section("costs", "Observed costs", f"<p>Not read: {_e(c['credits']['raison'])}.</p>")
    jours = f"Window: {c['fenetre_jours']} days. " if c.get("fenetre_jours") else ""
    if r["plateforme"] == "bigquery":
        od, rs = c["a_la_demande"], c["reserve"]
        body = (f"<h3>On demand</h3>{_fig_table([('Billed bytes', od['octets_factures']), ('Queries', od['requetes']), ('In dollars', od['monnaie'])])}"
                f"<h3>Reserved</h3>{_fig_table([('Slot-hours', rs['slot_heures']), ('Queries', rs['requetes']), ('In dollars', rs['monnaie'])])}"
                f"<p><b>On demand and reserved do not add up</b>: two billing models, two bases.</p>")
    else:
        body = _fig_table([("Credits consumed", c["credits"]), ("In dollars", c["monnaie"])])
        if c.get("credits_par_service"):
            body += "<h3>Per service</h3>" + _list_table(["Service", "Credits"], [[_e(s["service"]), _e(fig_text(s["credits"]))]
                                                                              for s in c["credits_par_service"]], "scan.json", (1,))
        if c.get("credits_par_entrepot"):
            body += "<h3>Per warehouse</h3>" + _list_table(["Warehouse", "Credits"], [[_e(w["entrepot"]), _e(fig_text(w["credits"]))]
                                                                                   for w in c["credits_par_entrepot"]], "scan.json", (1,))
    body += "<h3>Storage</h3>" + _fig_table([("Average billed storage", c["stockage_octets_moyens"]), ("Storage in dollars", c["stockage_monnaie"])])
    if c.get("transferts"):
        body += "<h3>Outbound transfers</h3>" + _list_table(["Target", "Volume"], [[_e(t["cible"]), _e(fig_text(t["octets"]))] for t in c["transferts"]],
                                                            "scan.json", (1,))
    n = _recentes(r)
    if n:
        body += (f"<p class=mut>{_e(pl(n, 'recent table'))} with no history in ACCOUNT_USAGE: "
                 "their storage and consumption are not in these figures yet.</p>")
    return _section("costs", "Observed costs", f"<p class=mut>{_e(jours + c.get('note', ''))}</p>{body}")


def _mouvements(r: dict) -> str:
    m = r["mouvements"]
    present = sorted((t for t in r["tables"] if not t["supprimee_le"]), key=lambda t: (-(t["octets_actifs"] or -1), t["id"]))
    rows = [[_e(t["id"]), _e(TABLE_TYPES.get(t["type"], t["type"])), _e(fmt_bytes(t["octets_actifs"])), _e(t["mouvement"] or "not determined"), _e(t["mouvement_preuve"])]
            for t in present]
    body = (f"<p>{fmt_num(m['figée'])} frozen, {fmt_num(m['vivante'])} live, {fmt_num(m['recréée'])} recreated, "
            f"{fmt_num(m['non_determine'])} not determined.</p><p class=mut>{_e(m['regle'])}. Based on: {_e(_base(m['base']) or 'no read')}.</p>"
            + _list_table(["Table", "Type", "Volume", "Movement", "Evidence"], rows, "tables.csv", (2,)))
    n = _recentes(r)
    if n:
        body += (f"<p class=mut>{_e(pl(n, 'table'))} seen only in real time: writes and reads not yet "
                 "available in ACCOUNT_USAGE; volume read from INFORMATION_SCHEMA.</p>")
    return _section("movements", "Movements", body)


def _ne_part_pas(r: dict) -> str:
    n = r["ne_part_pas"]
    rows = [[_e(KIND_LABELS.get(k, k)), _e(fmt_num(v)), _e(", ".join(n["exemples"].get(k, [])) + (" …" if v > 5 else ""))]
            for k, v in n["objets_par_type"].items()]
    body = "<h3>Objects to rebuild or describe</h3>" + _list_table(["Type", "Count", "Examples"], rows, "scan.json", (1,))
    if n["routines_par_langage"]:
        body += "<p>Routines per language: " + _e(", ".join(f"{k} {fmt_num(v)}" for k, v in n["routines_par_langage"].items())) + ".</p>"
    cols = [[_e(t["id"]), _e(c["nom"]), _e(c["type"])] for t in r["tables"] if not t["supprimee_le"] for c in t["colonnes_non_portables"]]
    body += f"<h3>Columns with a platform-specific type ({fmt_num(n['colonnes_non_portables'])})</h3>"
    body += _list_table(["Table", "Column", "Type"], cols, "tables.csv")
    if r.get("famille") != "fichiers":
        body += "<h3>Billed history</h3>" + _fig_table([(f"Time Travel and Fail-safe ({pl(n['historique']['tables'], 'table')})", n["historique"]["octets"])])
    return _section("stays-behind", "What does not leave with the copy", body)


def _dependances(r: dict) -> str:
    d = r["dependances"]
    body = ""
    if d["externes"]:
        body += "<h3>External dependencies (F01)</h3>" + _list_table(
            ["Object", "Type", "Location"], [[_e(x["id"]), _e(KIND_LABELS.get(x["type"], x["type"])), _e(", ".join(x["emplacements"]) or "not read")]
                                             for x in d["externes"]], "flags.csv")
    if d["cachees"]:
        body += "<h3>Hidden external dependencies (F02)</h3>" + _list_table(
            ["Object", "Path", "Location"], [[_e(x["id"]), _e(" → ".join(x["chemin"])), _e(", ".join(x["emplacements"]) or "not read")]
                                             for x in d["cachees"]], "flags.csv")
    if d["ecritures_externes"]:
        body += "<h3>Writes outside the platform (F03)</h3>" + _list_table(
            ["Source", "Mode", "Destination"], [[_e(f"{CIBLES.get(x['cible_type'], x['cible_type'])} {x['cible']}"), _e(x["mode"]), _e(", ".join(x["emplacements"]) or "not read")]
                                                for x in d["ecritures_externes"]], "flags.csv")
    if d["consommateurs"]:
        body += "<h3>External consumers (F09)</h3>" + _list_table(
            ["Share", "Accounts"], [[_e(x["id"]), _e(_num(x["comptes"]))] for x in d["consommateurs"]], "flags.csv", (1,))
    if d["outils"]:
        body += "<h3>Tools seen in queries</h3>" + _list_table(
            ["Tool", "Families", "Executions"], [[_e(x["outil"]), _e(fmt_num(x["familles"])), _e(fmt_num(x["executions"]))] for x in d["outils"]],
            "families.csv", (1, 2))
    if d.get("clients"):
        body += "<h3>Connected clients (sessions)</h3>" + _list_table(
            ["Client", "Sessions"], [[_e(x["outil"]), _e(_num(x["sessions"]))] for x in d["clients"]], "scan.json", (1,))
    if not body:
        body = "<p class=mut>No external dependency observed in the reads done.</p>"
    return _section("dependencies", "Dependencies", body)


def _rejouabilite(r: dict) -> str:
    rj = r["rejouabilite"]
    if rj["statut"] != READ:
        return _section("replayability", "Query replayability", f"<p>Not read: {_e(rj['raison'])}.</p>")
    rows = [[_e(k), _e(CATEGORIES.get(k, "")), _e(_num(v["familles"])), _e(_num(v["executions"])),
             _e(_poids(v["poids"], r["plateforme"]))] for k, v in rj["categories"].items()]
    body = _list_table(["Category", "Meaning", "Families", "Executions", "Weight"], rows, "scan.json", (2, 3, 4))
    t = {k: {**f, "hypotheses": [h for h in f.get("hypotheses") or [] if h != MENTION_REJOUABILITE]} for k, f in rj["taux"].items()}
    labels = {"familles": "C0 rate, by families", "executions": "C0 rate, by executions", "poids": "C0 rate, weighted"}
    body += _fig_table([(labels[k], t[k]) for k in ("familles", "executions", "poids") if k in t])
    body += f"<p class=mut>Based on: {_e(rj['base'] or 'not provided')}.</p>"
    body += f"<p><b>{_e(MENTION_REJOUABILITE)}.</b> Translated to DuckDB, not compared.</p>"
    extra = []
    if (rj.get("non_testees") or {}).get("familles"):
        extra.append(f"{pl(rj['non_testees']['familles'], 'family', 'families')} not tested")
    se = rj.get("secrets_ecartes")
    se = se.get("familles") if isinstance(se, dict) else se
    if isinstance(se, int) and se > 0:
        extra.append(f"{pl(se, 'family', 'families')} containing a secret, counted then set aside")
    if extra:
        body += f"<p class=mut>{_e(cap('; '.join(extra)))}.</p>"
    if rj.get("fonctions"):
        fn = sorted(rj["fonctions"].items(), key=lambda kv: (-kv[1], kv[0]))
        body += "<h3>Platform-specific functions</h3>" + _list_table(["Function", "Families"], [[_e(k), _e(fmt_num(v))] for k, v in fn],
                                                                     "scan.json", (1,))
    fams = [[_e(f["categorie"]), _e(_num(f["n_exec"])), _e(f["outil"] or f["app"] or ""),
             f"<code>{_e((f['sql_normalise'] or '')[:160])}{'…' if len(f['sql_normalise'] or '') > 160 else ''}</code>"]
            for f in r["familles"]]
    body += "<h3>Most executed families (normalized forms)</h3>" + _list_table(["Category", "Executions", "Tool", "Normalized form"],
                                                                               fams, "families.csv", (1,))
    return _section("replayability", "Query replayability", body)


def _fourchettes(r: dict) -> str:
    f = r["fourchettes"]
    rows = [("Volume to copy", f["volume"]), ("Copy read time", f["duree_lecture"])]
    if "credits_lecture" in f:
        rows.append(("Copy read credits", f["credits_lecture"]))
    rows += [("Copy read cost", f["cout_lecture"]), ("Rework", f["reprise"])]
    return _section("ranges", "Ranges", "<p class=mut>Planning estimates: each range carries its assumptions. "
                    "A canot drill replaces the assumed throughput with a measurement.</p>" + _fig_table(rows))


def _constats(r: dict) -> str:
    by = {}
    for d in r["drapeaux"]:
        by.setdefault(d["code"], []).append(d)
    body = (f"<p class=mut>Each finding is labeled \"{ETIQUETTE}\", with no severity level. "
            "It states what is observed and what is not.</p>")
    for code, label in LIBELLES.items():
        items = by.get(code)
        if not items:
            continue
        rows = [[_e(f"{CIBLES.get(d['cible_type'], d['cible_type'])} {d['cible']}"), _e(d["texte"])] for d in items]
        body += (f"<h3>{code} · {_e(label)}<span class=tag>{_e(ETIQUETTE)}</span></h3><p class=mut>{pl(len(items), 'finding')}</p>"
                 + _list_table(["Target", "Observed"], rows, "flags.csv"))
    hors_entrepot = r.get("famille") not in (None, "entrepot")  # F13-F16 do not apply to warehouses
    empty = [c for c in LIBELLES if c not in by and (hors_entrepot or c not in CODES_HORS_ENTREPOT)]
    if empty:
        body += (f"<p class=mut>No finding for: {', '.join(empty)}. "
                 "An absence of findings only holds for the reads done (see F12 and the method).</p>")
    return _section("findings", "Findings", body)


def _non_estime(r: dict) -> str:
    items = r["non_estime"]
    lis = "".join(f"<li>{_e(i['sujet'])}: {_e(i['raison'])}.</li>" for i in items[:MAX_ROWS])
    more = f"<p class=more>… and {fmt_num(len(items) - MAX_ROWS)} more in scan.json.</p>" if len(items) > MAX_ROWS else ""
    return _section("not-estimated", "What this report does not quantify", f"<ul>{lis}</ul>{more}")


def _methode(r: dict) -> str:
    lecs = sorted(r["lectures"], key=lambda x: (x["statut"] == READ, x["id"]))
    rows = []
    for x in lecs:
        fen = x["fenetre"] or {}
        fenetre = f"from {fmt_date(fen.get('du'))} to {fmt_date(fen.get('au'))}" if fen.get("du") or fen.get("au") else ""
        raison = x["raison"] or ""
        if x.get("limite"):
            raison += f"; limits: {x['limite']}"
        rows.append([_e(x["id"]), _e(short_vue(x["vue"])), _e("read" if x["statut"] == READ else "not read"), _e(raison), _e(_num(x["lignes"])),
                     _e(fenetre), _e("" if x["latence_max_h"] is None else f"{_trim(fmt_num(x['latence_max_h'], 1))} h")])
    plat, niv = r["plateforme"], r["niveau_droits"] or 0
    fiche = connectors.profile(plat)
    droits = [d for lvl, ds in sorted((fiche.get("droits") or {}).items()) if lvl <= niv for d in ds]
    cs = r["cout_du_scan"]
    cost = [("Queries sent", cs["requetes"]), ("Read time", cs["duree"])]
    if "octets_factures" in cs:
        cost.append(("Bytes billed for the scan", cs["octets_factures"]))
    if "credits" in cs:
        cost.append(("Credits consumed by the scan", cs["credits"]))
    rg = r["regles"]
    body = (f"<p>{fmt_num(cs['lectures']['lu'])} reads done out of {fmt_num(cs['lectures']['total'])}. Each read is recorded with its status "
            "and its reason; a value that was not read stays empty, never 0.</p>"
            + _list_table(["Id", "View", "Status", "Reason", "Rows", "Window", "Latency"], rows, "reads.csv", (4,))
            + f"<h3>Privileges used</h3><p>Level {_e(niv)}: {_e(', '.join(droits) or 'not specified')}. "
            f"Never requested: {_e(fiche.get('droits_jamais', 'write privileges'))}.</p>"
            + "<h3>Cost of the scan</h3>" + _fig_table(cost)
            + "<h3>Safeguards</h3><ul>"
            + ("<li>No connection: Canot reads the project's JSON files, with no network and no command; no query is sent.</li>"
               if r.get("famille") == "fichiers" else
               "<li>Every SQL statement sent goes through a read-only guard: only SELECT, SHOW, DESCRIBE and ALTER SESSION SET statements go out. "
               "No write, no API activation, no parameter change.</li>")
            + "<li>Query users and roles pseudonymized with HMAC, using a salt drawn for this scan and never written; "
            "tag values hashed; locations reduced to the bucket.</li>"
            "<li>Raw SQL text is never written: only normalized forms, with literals masked, leave collection"
            + (" (except scan-sql-raw.json, requested with --include-sql-text)" if r.get("avec_sql_brut") else "") + ".</li></ul>"
            + f"<h3>Rules</h3><ul><li>Movement: {_e(r['mouvements']['regle'])}.</li>"
            f"<li>Storage with no read: actual window of the view read, compared with {rg['horizon_lectures_jours']} days; "
            "the blind spots of each view are listed in the finding.</li>"
            f"<li>Copy time: assumed throughput of {fmt_num(rg['debit_mo_s'][0])} to {fmt_num(rg['debit_mo_s'][1])} MB/s.</li>"
            "<li>List prices are dated and sourced; they do not replace your contract price.</li></ul>")
    return _section("method", "Method", body)


def _suite() -> str:
    return ('<section id="next" class=suite><h2>Next steps</h2>'
            "<p><b>This report describes your platform; it recommends neither leaving nor staying.</b></p>"
            "<p>To measure an exit on a chosen scope: <code>canot drill</code> copies to Apache Iceberg, "
            "compares source and copy, and signs an evidence report.</p>"
            "<p>To go further, the q4o assessment, from €22,000 excl. VAT: your contract rate, actual execution, scenarios, the steps to follow. "
            f'<a href="{CANOT_URL}">canot.io</a></p></section>')


def render_html(result: dict) -> str:
    """scan.json -> self-contained HTML page. No script, no external resource (strict CSP: only the inline stylesheet, by hash)."""
    css_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    csp = f"default-src 'none'; style-src 'sha256-{css_hash}'; img-src 'none'; base-uri 'none'; form-action 'none'"
    body = "".join([_entete(result), "<main>", _perimetre(result), _capacites(result), _couts(result), _mouvements(result),
                    _ne_part_pas(result), _carte_sortie(result), _dependances(result), _rejouabilite(result), _fourchettes(result), _constats(result),
                    _non_estime(result), _regulatory.section("scan"), _methode(result), _suite(), "</main>"])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f"<title>Canot exit scan</title><style>{CSS}</style></head><body>{body}</body></html>\n")


# --- CSV -------------------------------------------------------------------------------------------------


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return " ; ".join(_cell(x) for x in v)
    return str(v)


def _csv(path: Path, header: list[str], rows: list[list]) -> Path:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows([[_cell(c) for c in r] for r in rows])
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


CSV_FILES = ("tables.csv", "flags.csv", "families.csv", "reads.csv")


def write_csv(result: dict, outdir: Path) -> list[Path]:
    """tables.csv, flags.csv, families.csv (normalized forms only), reads.csv. UTF-8, stable order."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tables = [[t["id"], t["base"], t["schema"], t["nom"], t["type"], t["lignes"], t["octets_actifs"], t["octets_historique"], t["retention_jours"],
               t["clone_de"], t["supprimee_le"], t["externe_uri"], t["proprietaire"], t["mouvement"], t["mouvement_preuve"], t["lectures"]["n"],
               t["lectures"]["derniere"], t["ecritures"]["n"], t["ecritures"]["derniere"], t["derniere_ddl"],
               [f"{c['nom']}:{c['type']}" for c in t["colonnes_non_portables"]], t["politiques"], t["etiquettes_n"], t["drapeaux"]]
              for t in result["tables"]]
    drapeaux = [[d["code"], d["libelle"], d["etiquette"], d["cible_type"], d["cible"], d["texte"], d["confiance"], d["base"]]
                for d in result["drapeaux"]]
    familles = [[f["empreinte"], f["hash_plateforme"], f["categorie"], f["n_exec"], f["temps_ms"], f["credits"], f["octets"], f["app"], f["outil"],
                 f["entrepot"], f["tables"], f["fonctions"], f["ecriture_externe"], f["sql_normalise"]] for f in result["familles"]]
    lectures = [[x["id"], x["vue"], x["statut"], x["raison"], x.get("limite"), x["lignes"], (x["fenetre"] or {}).get("du"),
                 (x["fenetre"] or {}).get("au"), x["latence_max_h"]] for x in result["lectures"]]
    return [
        _csv(outdir / "tables.csv", ["id", "database", "schema", "name", "type", "rows", "active_bytes", "history_bytes", "retention_days",
                                     "clone_of", "dropped_on", "location", "owner", "movement", "movement_evidence", "reads_n",
                                     "last_read", "writes_n", "last_write", "last_ddl", "non_portable_columns",
                                     "policies", "tags_n", "flags"], tables),
        _csv(outdir / "flags.csv", ["code", "title", "label", "target_type", "target", "observed", "confidence", "based_on"], drapeaux),
        _csv(outdir / "families.csv", ["fingerprint", "platform_hash", "category", "executions", "time_ms", "credits", "bytes", "app", "tool",
                                       "warehouse", "tables", "functions", "external_write", "normalized_sql"], familles),
        _csv(outdir / "reads.csv", ["id", "view", "status", "reason", "limits", "rows", "window_from", "window_to", "max_latency_h"], lectures),
    ]
