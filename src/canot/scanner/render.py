"""Scan rendering: scan.json -> scan.html (self-contained, printable on A4, no script and no external resource) and CSV.

render_html is pure. The page holds the report twice, in English and in French, switched by brand.bilingual (CSS :target, no
script): the English pane renders scan.json, the French pane renders the French analysis of the same snapshot
(analyze(..., lang="fr")). Ids are suffixed by language (#scope-en, #scope-fr). Each list shows at most 20 rows; the rest is in
the CSV files (or in scan.json). CSV files stay English.
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
from . import i18n
from .analyze import (CODES_HORS_ENTREPOT, LIBELLES, cap, etiquette, flag_label, fmt_bytes, fmt_date, fmt_num, kind_label, mention, parse_dt,
                      pl, short_vue)
from .i18n import t as _t, tr
from .model import COMPUTED, ESTIMATED, MEASURED, NOT_ESTIMATED, NOT_READ, READ

MAX_ROWS = 20
CANOT_URL = "https://canot.io"
NATURES = {MEASURED: "measured", COMPUTED: "computed", ESTIMATED: "estimated", NOT_READ: "not read", NOT_ESTIMATED: "not estimated"}
NATURES_FR = {MEASURED: "mesuré", COMPUTED: "calculé", ESTIMATED: "estimé", NOT_READ: "non lu", NOT_ESTIMATED: "non estimé"}
CONFIANCES_FR = {"high": "haute", "medium": "moyenne", "low": "basse"}
CATEGORIES = {"C0": "translated and accepted by DuckDB", "C1": "not parseable", "C2": "command, not analyzed", "C3": "translation impossible",
              "C4": "DuckDB parse error", "C5": "binding error (platform-specific object or function)", "C6": "variable",
              "C7": "DDL, COPY, MERGE, metadata queries: counted separately"}
CATEGORIES_FR = {"C0": "traduite et acceptée par DuckDB", "C1": "non analysable", "C2": "commande, non analysée", "C3": "traduction impossible",
                 "C4": "erreur d'analyse DuckDB", "C5": "erreur de liaison (objet ou fonction propre à la plateforme)", "C6": "variable",
                 "C7": "DDL, COPY, MERGE, requêtes de métadonnées : comptées à part"}
NIVEAUX = {1: "inventory, volumes, consumption", 2: "+ queries (normalized forms), reads per table, policies",
           3: "+ privileges and tools (pseudonymized)"}
NIVEAUX_FR = {1: "inventaire, volumes, consommation", 2: "+ requêtes (formes normalisées), lectures par table, politiques",
              3: "+ droits et outils (pseudonymisés)"}
# label, privileges requested and never requested: connector profile (connectors.py), no table here anymore
PORTABILITES = {"portable_tel_quel": "portable as is", "traduisible": "translatable (open text, replay possible)",
                "exporte_proprietaire": "exported in the vendor's format", "a_reconstruire": "to rebuild",
                "non_determine": "not determined (not read)"}
PORTABILITES_FR = {"portable_tel_quel": "portable tel quel", "traduisible": "traduisible (texte ouvert, rejeu possible)",
                   "exporte_proprietaire": "exporté au format de l'éditeur", "a_reconstruire": "à reconstruire",
                   "non_determine": "non déterminé (non lu)"}
FAMILLES_OBJET = {"donnees": "data", "traitement": "processing", "modele": "model", "rapport": "report", "planification": "scheduling",
                  "connexion": "connection", "securite": "security", "code": "code", "extension": "extension", "non_precisee": "unspecified"}
FAMILLES_OBJET_FR = {"donnees": "données", "traitement": "traitement", "modele": "modèle", "rapport": "rapport", "planification": "planification",
                     "connexion": "connexion", "securite": "sécurité", "code": "code", "extension": "extension", "non_precisee": "non précisée"}
# inventory depth declared by a connector (connectors.Capabilities.inventaire, French data values)
INVENTAIRES = {"complet": "full", "léger": "light", "partiel": "partial", "aucun": "none"}
INVENTAIRES_FR = {"complet": "complet", "léger": "léger", "partiel": "partiel", "aucun": "aucun"}
# flag target types and table types (French data values in scan.json) -> displayed label
CIBLES = {"objet": "object", "famille": "family", "lecture": "read", "role": "role", "table": "table"}
CIBLES_FR = {"objet": "objet", "famille": "famille", "lecture": "lecture", "role": "rôle", "table": "table"}
TABLE_TYPES = {"EXTERNE": "EXTERNAL", "TRANSITOIRE": "TRANSIENT", "TEMPORAIRE": "TEMPORARY"}
MOUVEMENTS_FR = {"frozen": "figée", "live": "vivante", "recreated": "recréée"}
MODES_FR = {"declared": "déclaré", "observed": "observé", "read-only": "lecture seule"}
# summary boxes: scan.json keys (unchanged) -> reader
AUDIENCES = {"DSI": "CIO", "DAF": "CFO", "RSSI": "CISO"}
AUDIENCES_FR = {"DSI": "DSI", "DAF": "DAF", "RSSI": "RSSI"}
# in-page anchors, in order (suffixed by language in the page: scope-en, scope-fr)
SECTION_IDS = ("header", "scope", "capabilities", "costs", "movements", "stays-behind", "exit-map", "dependencies", "replayability", "ranges",
               "findings", "not-estimated", "method", "next")
TITLES = {"en": "Canot exit scan", "fr": "Canot, scan de sortie"}
KINDS = {"en": "Exit scan", "fr": "Scan de sortie"}

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


def _fr() -> bool:
    return i18n.is_fr()


def _d(en: dict, fr: dict, k, default=None):
    """Displayed label of a data value, in the current language."""
    return (fr if _fr() else en).get(k, k if default is None else default)


def _sid(base: str) -> str:
    """Section id, unique in the bilingual page: scope-en, scope-fr."""
    return f"{base}-{i18n.lang()}"


def _trim(s: str) -> str:
    return i18n.trim0(s)


def _val(v, unite: str | None) -> str:
    if unite == "bytes":
        return fmt_bytes(v)
    if unite == "USD":
        return i18n.usd(v)
    if unite == "credits":
        return f"{fmt_num(v, 2)} {_t('credits', 'crédit' if abs(v) < 2 else 'crédits')}"
    if unite == "hours":
        if abs(v) < 1 / 60:
            return _t("less than a minute", "moins d'une minute")
        if abs(v) < 1:
            return f"{fmt_num(round(v * 60))} min"
        return f"{fmt_num(v, 2 if abs(v) < 10 else 1)} h"
    if unite == "days":
        return f"{fmt_num(v, 1)} {_t('d', 'j')}"
    if unite == "seconds":
        return f"{fmt_num(v)} s"
    if unite == "%":
        return i18n.pct(v)
    if unite == "slot-hours":
        return f"{fmt_num(v, 1)} {_t('slot-hours', 'heures-slot')}"
    if unite == "queries":
        return pl(v, _t("query", "requête"), _t("queries", "requêtes"))
    if unite and _fr():
        unite = {"calls": "appels", "families": "familles", "executions": "exécutions"}.get(unite, unite)
    return fmt_num(v) + (f" {unite}" if unite else "")


def fig_text(f: dict) -> str:
    if f["nature"] == NOT_READ:
        return _t(f"Not read: {f['raison']}.", f"Non lu : {tr(f['raison'])}.")
    if f["nature"] == NOT_ESTIMATED:
        return _t(f"Not estimated: {f['raison']}.", f"Non estimé : {tr(f['raison'])}.")
    if f["nature"] == ESTIMATED:
        lo, hi = _val(f["bas"], f["unite"]), _val(f["haut"], f["unite"])
        return lo if lo == hi else _t(f"{lo} to {hi}", f"{lo} à {hi}")
    return _val(f["valeur"], f["unite"])


def _base(ids: list[str]) -> str:
    return ", ".join(ids[:6]) + (" …" if len(ids) > 6 else "")


def _fig_cells(f: dict) -> str:
    hyp = "".join(f"<li>{_e(tr(h))}</li>" for h in f.get("hypotheses") or [])
    if _fr():
        nat = (NATURES_FR[f["nature"]] + (f", confiance {CONFIANCES_FR.get(f['confiance'], f['confiance'])}" if f.get("confiance") else "")
               + (f" · {_base(f['base'])}" if f.get("base") else ""))
    else:
        nat = NATURES[f["nature"]] + (f", {f['confiance']} confidence" if f.get("confiance") else "") + (f" · {_base(f['base'])}" if f.get("base") else "")
    return (f"<td>{_e(fig_text(f))}" + (f"<ul class=hyp>{hyp}</ul>" if hyp else "") + "</td>"
            f"<td class=nat>{_e(nat)}" + (f"<br>{_e(tr(f['formule']))}" if f.get("formule") else "") + "</td>")


def _fig_table(rows: list[tuple[str, dict]]) -> str:
    body = "".join(f"<tr><th scope=row>{_e(label)}</th>{_fig_cells(f)}</tr>" for label, f in rows)
    return f"<table><tr><th>{_t('Item', 'Élément')}</th><th>{_t('Value', 'Valeur')}</th><th>{_t('Nature and basis', 'Nature et base')}</th></tr>{body}</table>"


def _list_table(header: list[str], rows: list[list[str]], more_in: str, numeric: tuple[int, ...] = ()) -> str:
    """rows: cells already escaped. At most MAX_ROWS rows; the rest is referred to `more_in`."""
    if not rows:
        return f"<p class=mut>{_t('None.', 'Aucun.')}</p>"
    head = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td class=n>{c}</td>" if i in numeric else f"<td>{c}</td>" for i, c in enumerate(r)) + "</tr>"
                   for r in rows[:MAX_ROWS])
    n = fmt_num(len(rows) - MAX_ROWS)
    more = (f"<p class=more>{_t(f'… and {n} more in {_e(more_in)}.', f'… et {n} de plus dans {_e(more_in)}.')}</p>"
            if len(rows) > MAX_ROWS else "")
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def _num(v) -> str:
    return _t("not read", "non lu") if v is None else fmt_num(v)


def _poids(v, plateforme: str) -> str:
    if v is None:
        return _t("not read", "non lu")
    return fmt_bytes(v) if plateforme == "bigquery" else _val(v, "credits")


def _plateforme(r: dict) -> str:
    c = connectors.get(r["plateforme"])
    if c:
        return c.label
    return (r.get("capacites") or {}).get("libelle") or r["plateforme"]


def _fiche(plat: str | None, key: str, default=None):
    """A text of the connector profile, its French variant (key + "_fr") in the French pane when there is one."""
    fiche = connectors.profile(plat)
    if _fr() and f"{key}_fr" in fiche:
        return fiche[f"{key}_fr"]
    return fiche.get(key, default)


def _section(sid: str, title: str, body: str) -> str:
    return f'<section id="{_sid(sid)}"><h2>{_e(title)}</h2>{body}</section>'


def _recentes(r: dict) -> int:
    """Tables seen only in real time (missing from ACCOUNT_USAGE): their history is not available yet."""
    return sum(1 for t in r["tables"] if not t["supprimee_le"] and "recente" in t["flags"])


def _fpl(n: int, sing: str, plur: str) -> str:
    """French count with agreement (0 and 1 singular)."""
    return f"{fmt_num(n)} {sing if abs(n) < 2 else plur}"


# --- sections ----------------------------------------------------------------------------------------------


def _entete(r: dict, share: dict) -> str:
    as_of = parse_dt(r["as_of"])
    version = (r.get("versions") or {}).get("canot", _t("unknown version", "version inconnue"))
    if _fr():
        meta = [f"Constats au {as_of.strftime('%Y-%m-%d %H:%M')} UTC"]
        if r.get("latence_max_h") is not None:
            meta.append(f"données en retard de {_trim(fmt_num(r['latence_max_h'], 1))} h au plus selon la vue")
        meta += [f"édition : {tr(r['edition'])}", f"Canot {version}"]
        alerte = ("<p class=alerte><b>Texte SQL brut exporté.</b> Ce scan a été lancé avec --include-sql-text : le fichier "
                  "scan-sql-raw.json, à côté de ce rapport, contient le texte brut de vos requêtes, littéraux compris (permissions 0600). "
                  "Il n'est pas destiné à la diffusion.</p>") if r.get("avec_sql_brut") else ""
    else:
        meta = [f"Findings as of {as_of.strftime('%Y-%m-%d %H:%M')} UTC"]
        if r.get("latence_max_h") is not None:
            meta.append(f"data up to {_trim(fmt_num(r['latence_max_h'], 1))} h behind depending on the view")
        meta += [f"edition: {r['edition']}", f"Canot {version}"]
        alerte = ("<p class=alerte><b>Raw SQL text exported.</b> This scan was run with --include-sql-text: the file "
                  "scan-sql-raw.json, next to this report, contains the raw text of your queries, literals included (permissions 0600). "
                  "It is not meant for distribution.</p>") if r.get("avec_sql_brut") else ""
    boxes = "".join(f"<div class=box><h3>{_d(AUDIENCES, AUDIENCES_FR, k)}</h3><p>{_e(' '.join(r['resume'][k]))}</p></div>"
                    for k in ("DSI", "DAF", "RSSI"))
    return (f'<header id="{_sid("header")}"><p class=diffusion>{_t("Internal distribution", "Diffusion interne")}</p>'
            f"<h1>{_e(_t('Exit scan: ', 'Scan de sortie : ') + _plateforme(r))}</h1>"
            f"<p class=meta>{_e(' · '.join(meta))}</p>{alerte}<div class=trio>{boxes}</div>"
            f'{_brand.share_block(share, "scan-share.json", i18n.lang())}</header>')


def _perimetre(r: dict) -> str:
    lec = r["cout_du_scan"]["lectures"]
    kinds = r["ne_part_pas"]["objets_par_type"]
    n_tables = len([t for t in r["tables"] if not t["supprimee_le"]])
    v = r.get("versions") or {}
    credits = (r["couts"].get("credits") or {}).get("valeur") or 0
    vide = not any(not t["supprimee_le"] for t in r["tables"]) and (r["familles"] or credits > 0)
    fresh = (r.get("fraicheur") or {}).get("texte")
    if _fr():
        items = [
            f"Plateforme : {_plateforme(r)}",
            f"Périmètre : {i18n.tr_perimetre(r['perimetre']) or 'non précisé'}",
            f"Niveau de droits : {r['niveau_droits']} ({NIVEAUX_FR.get(r['niveau_droits'], 'non précisé')})",
            f"Mode SQL : {tr(r['mode_sql'])}",
            f"Lectures : {fmt_num(lec['lu'])} faites sur {fmt_num(lec['total'])}",
            f"Contenu lu : {pl(n_tables, 'table')}, {pl(sum(kinds.values()), 'objet')}, "
            f"{pl(len(r['familles']), 'famille de requêtes', 'familles de requêtes')}",
        ]
        if r.get("schemas_caches"):
            items.append(f"Schémas ou datasets cachés lus : {', '.join(r['schemas_caches'])}")
        if fresh:
            items.append(f"Fraîcheur : {tr(fresh)}")
        if vide:
            items.append("Inventaire vide alors que des requêtes ou des crédits ont été lus : vérifiez la base scannée et les droits du rôle "
                         "sur ses objets")
        items.append("Versions : " + ", ".join(f"{k} {v[k]}" for k in sorted(v)))
        return _section("scope", "Périmètre", "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")
    items = [
        f"Platform: {_plateforme(r)}",
        f"Scope: {r['perimetre'] or 'not specified'}",
        f"Privilege level: {r['niveau_droits']} ({NIVEAUX.get(r['niveau_droits'], 'not specified')})",
        f"SQL mode: {r['mode_sql']}",
        f"Reads: {fmt_num(lec['lu'])} done out of {fmt_num(lec['total'])}",
        f"Content read: {pl(n_tables, 'table')}, "
        f"{pl(sum(kinds.values()), 'object')}, {pl(len(r['familles']), 'query family', 'query families')}",
    ]
    if r.get("schemas_caches"):
        items.append(f"Hidden schemas or datasets read: {', '.join(r['schemas_caches'])}")
    if fresh:
        items.append(f"Freshness: {fresh}")
    if vide:
        items.append("Empty inventory although queries or credits were read: check the scanned database and the role's privileges "
                     "on its objects")
    items.append("Versions: " + ", ".join(f"{k} {v[k]}" for k in sorted(v)))
    return _section("scope", "Scope", "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")


def _capacites(r: dict) -> str:
    """What Canot can do on this platform (declared by the connector), and what this report did: an inventory."""
    c = r.get("capacites")
    if not c:
        return ""
    lab = _plateforme(r)
    niv = c.get("niveaux") or []
    inv = _d(INVENTAIRES, INVENTAIRES_FR, c.get("inventaire")) or _t("not declared", "non déclaré")
    if niv:
        inv += (_t(f", levels {niv[0]} to {niv[-1]}", f", niveaux {niv[0]} à {niv[-1]}") if len(niv) > 1
                else _t(f", level {niv[0]}", f", niveau {niv[0]}"))
    export, preuve = c.get("export") or [], [p for p in c.get("preuve") or [] if isinstance(p, dict)]
    commande = _fiche(r["plateforme"], "commande_preuve", "canot drill")
    limite_fiche = _fiche(r["plateforme"], "limite_preuve")
    formes = _t("; ", " ; ").join(f"{p.get('forme')} {connectors.PREUVES_FR.get(p.get('forme'), p.get('libelle')) if _fr() else p.get('libelle')}"
                       for p in preuve)
    conn = connectors.get(r["plateforme"])
    teste_sur = c.get("teste_sur")
    if _fr() and teste_sur and conn is not None and conn.capabilities.teste_sur == teste_sur and conn.capabilities.teste_sur_fr:
        teste_sur = conn.capabilities.teste_sur_fr
    if _fr():
        rows = [[_e("Inventaire"), _e(f"fait par ce scan (niveau {r['niveau_droits']})" if r.get("niveau_droits") else "fait par ce scan"), _e(inv)],
                [_e("Export"), _e("non fait par ce scan"), _e(f"{', '.join(export)}, avec canot drill" if export else "aucun format déclaré")],
                [_e("Preuve"), _e("non faite par ce scan"), _e(f"{formes}, avec {commande}" if preuve else "aucune forme déclarée")]]
        statut = f"Connecteur {lab} : {connectors.STATUTS_FR.get(c.get('statut'), c.get('statut')) or 'statut non déclaré'}"
        statut += f" ; test enregistré : {teste_sur}." if teste_sur else " ; aucune date de test enregistrée dans le dépôt."
        if preuve and limite_fiche:
            limite = f"Ce que Canot ne peut pas prouver ici : {limite_fiche}"
        elif preuve:
            limite = (f"Ce que Canot ne peut pas prouver ici : les formes déclarées ({', '.join(str(p.get('forme')) for p in preuve)}) couvrent "
                      "les tables et les requêtes ; les objets à reconstruire hors tables sont comptés, non prouvés.")
        else:
            limite = f"Aucune forme de preuve n'est déclarée pour {lab} : les objets sont comptés, non prouvés."
        body = ("<p>Ce rapport est un inventaire : inventorié, ni exporté ni rejoué.</p>"
                + _list_table(["Niveau", "Ce rapport", f"Canot sur {lab}"], rows, "scan.json")
                + f"<p class=mut>{_e(statut)}</p><p>{_e(limite)}</p>")
        return _section("capabilities", "Capacités", body)
    rows = [[_e("Inventory"), _e(f"done by this scan (level {r['niveau_droits']})" if r.get("niveau_droits") else "done by this scan"), _e(inv)],
            [_e("Export"), _e("not done by this scan"), _e(f"{', '.join(export)}, with canot drill" if export else "no format declared")],
            [_e("Evidence"), _e("not done by this scan"), _e(f"{formes}, with {commande}" if preuve else "no form declared")]]
    statut = f"{lab} connector: {c.get('statut') or 'status not declared'}"
    statut += f"; recorded test: {teste_sur}." if teste_sur else "; no test date recorded in the repository."
    if preuve and limite_fiche:
        limite = f"What Canot cannot prove here: {limite_fiche}"
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
    rows = [[_e(_d(PORTABILITES, PORTABILITES_FR, p)), _e(_d(FAMILLES_OBJET, FAMILLES_OBJET_FR, f, f.replace("_", " "))), _e(fmt_num(n))]
            for p, fams in carte.items() for f, n in fams.items()]
    intro = _t("<p class=mut>Portability declared by collection for each object read. \"Exported\" is not \"portable\": "
               "an archive in the vendor's format can only be used with that vendor.</p>",
               "<p class=mut>Portabilité déclarée par la collecte pour chaque objet lu. « Exporté » n'est pas « portable » : "
               "une archive au format de l'éditeur ne s'utilise qu'avec cet éditeur.</p>")
    body = intro + _list_table([_t("Portability", "Portabilité"), _t("Object family", "Famille d'objet"), _t("Objects", "Objets")],
                               rows, "scan.json", (2,))
    return _section("exit-map", _t("Exit map", "Carte de sortie"), body)


def _couts(r: dict) -> str:
    c = r["couts"]
    titre = _t("Observed costs", "Coûts observés")
    if r.get("famille") == "fichiers":
        return _section("costs", titre, f"<p>{_e(_t('Not read: ', 'Non lu : '))}{_e(tr(c['credits']['raison']))}.</p>")
    jours = _t(f"Window: {c['fenetre_jours']} days. ", f"Fenêtre : {c['fenetre_jours']} jours. ") if c.get("fenetre_jours") else ""
    if r["plateforme"] == "bigquery":
        od, rs = c["a_la_demande"], c["reserve"]
        body = (f"<h3>{_t('On demand', 'À la demande')}</h3>"
                f"{_fig_table([(_t('Billed bytes', 'Octets facturés'), od['octets_factures']), (_t('Queries', 'Requêtes'), od['requetes']), (_t('In dollars', 'En dollars'), od['monnaie'])])}"
                f"<h3>{_t('Reserved', 'Réservé')}</h3>"
                f"{_fig_table([(_t('Slot-hours', 'Heures-slot'), rs['slot_heures']), (_t('Queries', 'Requêtes'), rs['requetes']), (_t('In dollars', 'En dollars'), rs['monnaie'])])}"
                + _t("<p><b>On demand and reserved do not add up</b>: two billing models, two bases.</p>",
                     "<p><b>À la demande et réservé ne s'additionnent pas</b> : deux modèles de facturation, deux bases.</p>"))
    else:
        body = _fig_table([(_t("Credits consumed", "Crédits consommés"), c["credits"]), (_t("In dollars", "En dollars"), c["monnaie"])])
        if c.get("credits_par_service"):
            body += f"<h3>{_t('Per service', 'Par service')}</h3>" + _list_table(
                ["Service", _t("Credits", "Crédits")], [[_e(s["service"]), _e(fig_text(s["credits"]))] for s in c["credits_par_service"]],
                "scan.json", (1,))
        if c.get("credits_par_entrepot"):
            body += f"<h3>{_t('Per warehouse', 'Par entrepôt')}</h3>" + _list_table(
                [_t("Warehouse", "Entrepôt"), _t("Credits", "Crédits")],
                [[_e(w["entrepot"]), _e(fig_text(w["credits"]))] for w in c["credits_par_entrepot"]], "scan.json", (1,))
    body += f"<h3>{_t('Storage', 'Stockage')}</h3>" + _fig_table([(_t("Average billed storage", "Stockage facturé moyen"), c["stockage_octets_moyens"]),
                                                                  (_t("Storage in dollars", "Stockage en dollars"), c["stockage_monnaie"])])
    if c.get("transferts"):
        body += f"<h3>{_t('Outbound transfers', 'Transferts sortants')}</h3>" + _list_table(
            [_t("Target", "Cible"), "Volume"], [[_e(x["cible"]), _e(fig_text(x["octets"]))] for x in c["transferts"]], "scan.json", (1,))
    n = _recentes(r)
    if n:
        body += _t(f"<p class=mut>{_e(pl(n, 'recent table'))} with no history in ACCOUNT_USAGE: "
                   "their storage and consumption are not in these figures yet.</p>",
                   f"<p class=mut>{_e(_fpl(n, 'table récente', 'tables récentes'))} sans historique dans ACCOUNT_USAGE : "
                   "leur stockage et leur consommation ne figurent pas encore dans ces chiffres.</p>")
    return _section("costs", titre, f"<p class=mut>{_e(jours + c.get('note', ''))}</p>{body}")


def _mouvements(r: dict) -> str:
    m = r["mouvements"]
    present = sorted((t for t in r["tables"] if not t["supprimee_le"]), key=lambda t: (-(t["octets_actifs"] or -1), t["id"]))
    if _fr():
        rows = [[_e(t["id"]), _e(t["type"]), _e(fmt_bytes(t["octets_actifs"])), _e(MOUVEMENTS_FR.get(t["mouvement"], t["mouvement"]) or "non déterminé"),
                 _e(t["mouvement_preuve"])] for t in present]
        body = (f"<p>{_fpl(m['figée'], 'figée', 'figées')}, {_fpl(m['vivante'], 'vivante', 'vivantes')}, "
                f"{_fpl(m['recréée'], 'recréée', 'recréées')}, {_fpl(m['non_determine'], 'non déterminée', 'non déterminées')}.</p>"
                f"<p class=mut>{_e(m['regle'])}. Base : {_e(_base(m['base']) or 'aucune lecture')}.</p>"
                + _list_table(["Table", "Type", "Volume", "Mouvement", "Preuve"], rows, "tables.csv", (2,)))
        n = _recentes(r)
        if n:
            body += (f"<p class=mut>{_e(_fpl(n, 'table vue', 'tables vues'))} seulement en temps réel : écritures et lectures pas encore "
                     "disponibles dans ACCOUNT_USAGE ; volume lu dans INFORMATION_SCHEMA.</p>")
        return _section("movements", "Mouvements", body)
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
    rows = [[_e(kind_label(k)), _e(fmt_num(v)), _e(", ".join(n["exemples"].get(k, [])) + (" …" if v > 5 else ""))]
            for k, v in n["objets_par_type"].items()]
    body = (f"<h3>{_t('Objects to rebuild or describe', 'Objets à reconstruire ou à décrire')}</h3>"
            + _list_table(["Type", _t("Count", "Nombre"), _t("Examples", "Exemples")], rows, "scan.json", (1,)))
    if n["routines_par_langage"]:
        langs = ", ".join(f"{_t(k, 'NON LU') if k == 'NOT READ' else k} {fmt_num(v)}" for k, v in n["routines_par_langage"].items())
        body += f"<p>{_t('Routines per language: ', 'Routines par langage : ')}" + _e(langs) + ".</p>"
    cols = [[_e(t["id"]), _e(c["nom"]), _e(c["type"])] for t in r["tables"] if not t["supprimee_le"] for c in t["colonnes_non_portables"]]
    nc = fmt_num(n["colonnes_non_portables"])
    body += f"<h3>{_t(f'Columns with a platform-specific type ({nc})', f'Colonnes de type propre à la plateforme ({nc})')}</h3>"
    body += _list_table(["Table", _t("Column", "Colonne"), "Type"], cols, "tables.csv")
    if r.get("famille") != "fichiers":
        h = n["historique"]
        body += (f"<h3>{_t('Billed history', 'Historique facturé')}</h3>"
                 + _fig_table([(_t(f"Time Travel and Fail-safe ({pl(h['tables'], 'table')})", f"Time Travel et Fail-safe ({pl(h['tables'], 'table')})"),
                                h["octets"])]))
    return _section("stays-behind", _t("What does not leave with the copy", "Ce qui ne part pas avec la copie"), body)


def _dependances(r: dict) -> str:
    d = r["dependances"]
    body = ""
    nr = _t("not read", "non lu")
    obj, typ, loc = _t("Object", "Objet"), "Type", _t("Location", "Emplacement")
    if d["externes"]:
        body += f"<h3>{_t('External dependencies (F01)', 'Dépendances externes (F01)')}</h3>" + _list_table(
            [obj, typ, loc], [[_e(x["id"]), _e(kind_label(x["type"])), _e(", ".join(x["emplacements"]) or nr)]
                              for x in d["externes"]], "flags.csv")
    if d["cachees"]:
        body += f"<h3>{_t('Hidden external dependencies (F02)', 'Dépendances externes cachées (F02)')}</h3>" + _list_table(
            [obj, _t("Path", "Chemin"), loc], [[_e(x["id"]), _e(" → ".join(x["chemin"])), _e(", ".join(x["emplacements"]) or nr)]
                                               for x in d["cachees"]], "flags.csv")
    if d["ecritures_externes"]:
        body += f"<h3>{_t('Writes outside the platform (F03)', 'Écritures hors de la plateforme (F03)')}</h3>" + _list_table(
            ["Source", "Mode", "Destination"],
            [[_e(f"{_d(CIBLES, CIBLES_FR, x['cible_type'])} {x['cible']}"), _e(MODES_FR.get(x["mode"], x["mode"]) if _fr() else x["mode"]),
              _e(", ".join(x["emplacements"]) or nr)] for x in d["ecritures_externes"]], "flags.csv")
    if d["consommateurs"]:
        body += f"<h3>{_t('External consumers (F09)', 'Consommateurs externes (F09)')}</h3>" + _list_table(
            [_t("Share", "Partage"), _t("Accounts", "Comptes")], [[_e(x["id"]), _e(_num(x["comptes"]))] for x in d["consommateurs"]], "flags.csv", (1,))
    if d["outils"]:
        body += f"<h3>{_t('Tools seen in queries', 'Outils vus dans les requêtes')}</h3>" + _list_table(
            [_t("Tool", "Outil"), _t("Families", "Familles"), _t("Executions", "Exécutions")],
            [[_e(_t(x["outil"], "non identifié") if x["outil"] == "unidentified" else x["outil"]), _e(fmt_num(x["familles"])), _e(fmt_num(x["executions"]))]
             for x in d["outils"]], "families.csv", (1, 2))
    if d.get("clients"):
        body += f"<h3>{_t('Connected clients (sessions)', 'Clients connectés (sessions)')}</h3>" + _list_table(
            ["Client", "Sessions"], [[_e(x["outil"]), _e(_num(x["sessions"]))] for x in d["clients"]], "scan.json", (1,))
    if not body:
        body = _t("<p class=mut>No external dependency observed in the reads done.</p>",
                  "<p class=mut>Aucune dépendance externe observée dans les lectures faites.</p>")
    return _section("dependencies", _t("Dependencies", "Dépendances"), body)


def _rejouabilite(r: dict) -> str:
    rj = r["rejouabilite"]
    titre = _t("Query replayability", "Rejouabilité des requêtes")
    if rj["statut"] != READ:
        return _section("replayability", titre, f"<p>{_e(_t('Not read: ', 'Non lu : '))}{_e(tr(rj['raison']))}.</p>")
    rows = [[_e(k), _e(_d(CATEGORIES, CATEGORIES_FR, k, "")), _e(_num(v["familles"])), _e(_num(v["executions"])),
             _e(_poids(v["poids"], r["plateforme"]))] for k, v in rj["categories"].items()]
    body = _list_table([_t("Category", "Catégorie"), _t("Meaning", "Signification"), _t("Families", "Familles"), _t("Executions", "Exécutions"),
                        _t("Weight", "Poids")], rows, "scan.json", (2, 3, 4))
    mn = mention()
    t = {k: {**f, "hypotheses": [h for h in f.get("hypotheses") or [] if tr(h) != mn]} for k, f in rj["taux"].items()}
    labels = {"familles": _t("C0 rate, by families", "Taux C0, par familles"), "executions": _t("C0 rate, by executions", "Taux C0, par exécutions"),
              "poids": _t("C0 rate, weighted", "Taux C0, pondéré")}
    body += _fig_table([(labels[k], t[k]) for k in ("familles", "executions", "poids") if k in t])
    body += f"<p class=mut>{_t('Based on: ', 'Base : ')}{_e(tr(rj['base']) or _t('not provided', 'non fournie'))}.</p>"
    body += f"<p><b>{_e(mn)}.</b> {_t('Translated to DuckDB, not compared.', 'Traduit vers DuckDB, non comparé.')}</p>"
    extra = []
    nt = (rj.get("non_testees") or {}).get("familles")
    if nt:
        extra.append(_t(f"{pl(nt, 'family', 'families')} not tested", _fpl(nt, "famille non testée", "familles non testées")))
    se = rj.get("secrets_ecartes")
    se = se.get("familles") if isinstance(se, dict) else se
    if isinstance(se, int) and se > 0:
        extra.append(_t(f"{pl(se, 'family', 'families')} containing a secret, counted then set aside",
                        _fpl(se, "famille contenant un secret, comptée puis écartée", "familles contenant un secret, comptées puis écartées")))
    if extra:
        body += f"<p class=mut>{_e(cap(_t('; ', ' ; ').join(extra)))}.</p>"
    if rj.get("fonctions"):
        fn = sorted(rj["fonctions"].items(), key=lambda kv: (-kv[1], kv[0]))
        body += f"<h3>{_t('Platform-specific functions', 'Fonctions propres à la plateforme')}</h3>" + _list_table(
            [_t("Function", "Fonction"), _t("Families", "Familles")], [[_e(k), _e(fmt_num(v))] for k, v in fn], "scan.json", (1,))
    fams = [[_e(f["categorie"]), _e(_num(f["n_exec"])), _e(f["outil"] or f["app"] or ""),
             f"<code>{_e((f['sql_normalise'] or '')[:160])}{'…' if len(f['sql_normalise'] or '') > 160 else ''}</code>"]
            for f in r["familles"]]
    body += (f"<h3>{_t('Most executed families (normalized forms)', 'Familles les plus exécutées (formes normalisées)')}</h3>"
             + _list_table([_t("Category", "Catégorie"), _t("Executions", "Exécutions"), _t("Tool", "Outil"), _t("Normalized form", "Forme normalisée")],
                           fams, "families.csv", (1,)))
    return _section("replayability", titre, body)


def _fourchettes(r: dict) -> str:
    f = r["fourchettes"]
    rows = [(_t("Volume to copy", "Volume à copier"), f["volume"]), (_t("Copy read time", "Durée de lecture de la copie"), f["duree_lecture"])]
    if "credits_lecture" in f:
        rows.append((_t("Copy read credits", "Crédits de lecture de la copie"), f["credits_lecture"]))
    rows += [(_t("Copy read cost", "Coût de lecture de la copie"), f["cout_lecture"]), (_t("Rework", "Reprise"), f["reprise"])]
    intro = _t("<p class=mut>Planning estimates: each range carries its assumptions. "
               "A canot drill replaces the assumed throughput with a measurement.</p>",
               "<p class=mut>Estimations de cadrage : chaque fourchette porte ses hypothèses. "
               "Un canot drill remplace le débit supposé par une mesure.</p>")
    return _section("ranges", _t("Ranges", "Fourchettes"), intro + _fig_table(rows))


def _constats(r: dict) -> str:
    by = {}
    for d in r["drapeaux"]:
        by.setdefault(d["code"], []).append(d)
    et = etiquette()
    body = _t(f"<p class=mut>Each finding is labeled \"{et}\", with no severity level. It states what is observed and what is not.</p>",
              f"<p class=mut>Chaque constat est étiqueté « {et} », sans niveau de gravité. Il dit ce qui est observé et ce qui ne l'est pas.</p>")
    for code in LIBELLES:
        items = by.get(code)
        if not items:
            continue
        rows = [[_e(f"{_d(CIBLES, CIBLES_FR, d['cible_type'])} {d['cible']}"), _e(d["texte"])] for d in items]
        n = len(items)
        body += (f"<h3>{code} · {_e(flag_label(code))}<span class=tag>{_e(et)}</span></h3>"
                 f"<p class=mut>{_t(pl(n, 'finding'), _fpl(n, 'constat', 'constats'))}</p>"
                 + _list_table([_t("Target", "Cible"), _t("Observed", "Observé")], rows, "flags.csv"))
    hors_entrepot = r.get("famille") not in (None, "entrepot")  # F13-F16 do not apply to warehouses
    empty = [c for c in LIBELLES if c not in by and (hors_entrepot or c not in CODES_HORS_ENTREPOT)]
    if empty:
        body += _t(f"<p class=mut>No finding for: {', '.join(empty)}. "
                   "An absence of findings only holds for the reads done (see F12 and the method).</p>",
                   f"<p class=mut>Aucun constat pour : {', '.join(empty)}. "
                   "Une absence de constat ne vaut que pour les lectures faites (voir F12 et la méthode).</p>")
    return _section("findings", _t("Findings", "Constats"), body)


def _non_estime(r: dict) -> str:
    items = r["non_estime"]
    sep = _t(": ", " : ")
    lis = "".join(f"<li>{_e(i['sujet'])}{sep}{_e(tr(i['raison']))}.</li>" for i in items[:MAX_ROWS])
    n = fmt_num(len(items) - MAX_ROWS)
    more = f"<p class=more>{_t(f'… and {n} more in scan.json.', f'… et {n} de plus dans scan.json.')}</p>" if len(items) > MAX_ROWS else ""
    return _section("not-estimated", _t("What this report does not quantify", "Ce que ce rapport ne chiffre pas"), f"<ul>{lis}</ul>{more}")


def _reglementaire() -> str:
    """Regulatory section (regulatory.py), in the pane's language. regulatory.section gives each language its own id
    (regulatory, regulatory-fr): unique in the bilingual page."""
    return _regulatory.section("scan", i18n.lang())


def _methode(r: dict) -> str:
    lecs = sorted(r["lectures"], key=lambda x: (x["statut"] == READ, x["id"]))
    rows = []
    for x in lecs:
        fen = x["fenetre"] or {}
        fenetre = (_t(f"from {fmt_date(fen.get('du'))} to {fmt_date(fen.get('au'))}", f"du {fmt_date(fen.get('du'))} au {fmt_date(fen.get('au'))}")
                   if fen.get("du") or fen.get("au") else "")
        raison = tr(x["raison"]) or ""
        if x.get("limite"):
            raison += _t(f"; limits: {x['limite']}", f" ; limite : {x['limite']}")
        rows.append([_e(x["id"]), _e(short_vue(x["vue"])), _e(_t("read", "lu") if x["statut"] == READ else _t("not read", "non lu")), _e(raison),
                     _e(_num(x["lignes"])), _e(fenetre), _e("" if x["latence_max_h"] is None else f"{_trim(fmt_num(x['latence_max_h'], 1))} h")])
    plat, niv = r["plateforme"], r["niveau_droits"] or 0
    par_niveau = {**(connectors.profile(plat).get("droits") or {}), **(_fiche(plat, "droits") or {})}
    droits = [d for lvl, ds in sorted(par_niveau.items()) if lvl <= niv for d in ds]
    jamais = _fiche(plat, "droits_jamais", _t("write privileges", "droits d'écriture"))
    cs = r["cout_du_scan"]
    cost = [(_t("Queries sent", "Requêtes envoyées"), cs["requetes"]), (_t("Read time", "Durée de lecture"), cs["duree"])]
    if "octets_factures" in cs:
        cost.append((_t("Bytes billed for the scan", "Octets facturés pour le scan"), cs["octets_factures"]))
    if "credits" in cs:
        cost.append((_t("Credits consumed by the scan", "Crédits consommés par le scan"), cs["credits"]))
    rg = r["regles"]
    lu, total = fmt_num(cs["lectures"]["lu"]), fmt_num(cs["lectures"]["total"])
    head = [_t("Id", "Id"), _t("View", "Vue"), _t("Status", "Statut"), _t("Reason", "Motif"), _t("Rows", "Lignes"), _t("Window", "Fenêtre"),
            _t("Latency", "Latence")]
    if _fr():
        garde = ("<li>Aucune connexion : Canot lit les fichiers JSON du projet, sans réseau ni commande ; aucune requête n'est envoyée.</li>"
                 if r.get("famille") == "fichiers" else
                 "<li>Chaque instruction SQL envoyée passe par une garde lecture seule : seules les instructions SELECT, SHOW, DESCRIBE et "
                 "ALTER SESSION SET sortent. Aucune écriture, aucune activation d'API, aucun changement de paramètre.</li>")
        body = (f"<p>{lu} lectures faites sur {total}. Chaque lecture est enregistrée avec son statut et son motif ; une valeur non lue "
                "reste vide, jamais 0.</p>"
                + _list_table(head, rows, "reads.csv", (4,))
                + f"<h3>Droits utilisés</h3><p>Niveau {_e(niv)} : {_e(', '.join(droits) or 'non précisé')}. "
                f"Jamais demandés : {_e(jamais)}.</p>"
                + "<h3>Coût du scan</h3>" + _fig_table(cost)
                + "<h3>Garde-fous</h3><ul>" + garde
                + "<li>Utilisateurs et rôles des requêtes pseudonymisés par HMAC, avec un sel tiré pour ce scan et jamais écrit ; "
                "valeurs d'étiquettes hachées ; emplacements réduits au bucket.</li>"
                "<li>Le texte SQL brut n'est jamais écrit : seules les formes normalisées, littéraux masqués, sortent de la collecte"
                + (" (sauf scan-sql-raw.json, demandé avec --include-sql-text)" if r.get("avec_sql_brut") else "") + ".</li></ul>"
                + f"<h3>Règles</h3><ul><li>Mouvement : {_e(r['mouvements']['regle'])}.</li>"
                f"<li>Stockage sans lecture : fenêtre réelle de la vue lue, comparée à {rg['horizon_lectures_jours']} jours ; "
                "les angles morts de chaque vue sont listés dans le constat.</li>"
                f"<li>Durée de copie : débit supposé de {fmt_num(rg['debit_mo_s'][0])} à {fmt_num(rg['debit_mo_s'][1])} Mo/s.</li>"
                "<li>Les prix catalogue sont datés et sourcés ; ils ne remplacent pas le prix de votre contrat.</li></ul>")
        return _section("method", "Méthode", body)
    body = (f"<p>{lu} reads done out of {total}. Each read is recorded with its status "
            "and its reason; a value that was not read stays empty, never 0.</p>"
            + _list_table(head, rows, "reads.csv", (4,))
            + f"<h3>Privileges used</h3><p>Level {_e(niv)}: {_e(', '.join(droits) or 'not specified')}. "
            f"Never requested: {_e(jamais)}.</p>"
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
    if _fr():
        return (f'<section id="{_sid("next")}" class=suite><h2>Suite</h2>'
                "<p><b>Ce rapport décrit votre plateforme ; il ne recommande ni de partir ni de rester.</b></p>"
                "<p>Pour mesurer une sortie sur un périmètre choisi : <code>canot drill</code> copie vers Apache Iceberg, "
                "compare la source et la copie, et signe un dossier de preuve.</p>"
                f"<p>Pour aller plus loin, le diagnostic q4o, dès 22{i18n.NBSP}000{i18n.NBSP}€ HT : votre tarif contractuel, l'exécution réelle, "
                f'des scénarios, les étapes à suivre. <a href="{CANOT_URL}">canot.io</a></p></section>')
    return (f'<section id="{_sid("next")}" class=suite><h2>Next steps</h2>'
            "<p><b>This report describes your platform; it recommends neither leaving nor staying.</b></p>"
            "<p>To measure an exit on a chosen scope: <code>canot drill</code> copies to Apache Iceberg, "
            "compares source and copy, and signs an evidence report.</p>"
            "<p>To go further, the q4o assessment, from €22,000 excl. VAT: your contract rate, actual execution, scenarios, the steps to follow. "
            f'<a href="{CANOT_URL}">canot.io</a></p></section>')


def _pane(r: dict, share: dict) -> str:
    """One complete report in the current language (i18n.use)."""
    return "".join([_entete(r, share), "<main>", _perimetre(r), _capacites(r), _couts(r), _mouvements(r),
                    _ne_part_pas(r), _carte_sortie(r), _dependances(r), _rejouabilite(r), _fourchettes(r), _constats(r),
                    _non_estime(r), _reglementaire(), _methode(r), _suite(), "</main>"])


def render_html(result: dict, fr: dict | None = None, *, lang: str = "en") -> str:
    """scan.json -> self-contained HTML page, in English and in French. No script, no external resource (strict CSP: only the
    inline stylesheet, by hash).

    fr: the French analysis of the same snapshot (analyze(snapshot, lang="fr")), for the French pane; without it, the French
    pane shows the French labels around the English texts of `result`. lang: the pane shown first ("en" or "fr")."""
    from .run import partage  # the shareable summary: counts only, the same English file in both panes

    lang = lang if lang in i18n.LANGS else "en"
    share = partage(result)
    fr_res = {**(fr if fr is not None else result), "avec_sql_brut": result.get("avec_sql_brut")}
    with i18n.use("en"):
        en_html = _pane(result, share)
    with i18n.use("fr"):
        fr_html = _pane(fr_res, share)
    css_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    csp = f"default-src 'none'; style-src 'sha256-{css_hash}'; img-src 'none'; base-uri 'none'; form-action 'none'"
    body = _brand.brand_bar(f"{KINDS['en']} · {KINDS['fr']}") + _brand.bilingual(en_html, fr_html, lang)
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f"<title>{TITLES[lang]}</title><style>{CSS}</style></head><body>{body}</body></html>\n")


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
    """tables.csv, flags.csv, families.csv (normalized forms only), reads.csv. UTF-8, stable order, English (scan.json values)."""
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
