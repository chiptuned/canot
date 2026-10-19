"""Rendu du scan : scan.json -> scan.html (autonome, imprimable en A4, sans script ni ressource externe) et CSV.

render_html est pure. Chaque liste affiche au plus 20 lignes ; le reste est dans les CSV (ou dans scan.json).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
from pathlib import Path

from .. import connectors
from .analyze import (CODES_HORS_ENTREPOT, ETIQUETTE, KIND_LABELS, LIBELLES, MENTION_REJOUABILITE, cap, fr_bytes, fr_date, fr_num, parse_dt,
                      pl, short_vue)

MAX_ROWS = 20
CANOT_URL = "https://canot.io"
NATURES = {"mesuré": "mesuré", "calculé": "calculé", "estimé": "estimé", "non_lu": "non lu", "non_estimé": "non estimé"}
CATEGORIES = {"C0": "traduit et accepté par DuckDB", "C1": "non analysable", "C2": "commande, non analysée", "C3": "traduction impossible",
              "C4": "erreur d'analyse DuckDB", "C5": "erreur de liaison (objet ou fonction propre)", "C6": "variable",
              "C7": "DDL, COPY, MERGE, requêtes de métadonnées : comptés à part"}
NIVEAUX = {1: "inventaire, volumes, consommation", 2: "+ requêtes (formes normalisées), lectures par table, politiques",
           3: "+ droits et outils (pseudonymisés)"}
# libellé, droits demandés et droits jamais demandés : fiche du connecteur (connectors.py), plus de table ici
PORTABILITES = {"portable_tel_quel": "portable tel quel", "traduisible": "traduisible (texte ouvert, rejeu possible)",
                "exporte_proprietaire": "exporté au format de l'éditeur", "a_reconstruire": "à reconstruire",
                "non_determine": "non déterminé (non lu)"}

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


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _val(v, unite: str | None) -> str:
    if unite == "octets":
        return fr_bytes(v)
    if unite == "USD":
        return f"{fr_num(v, 2)} $"
    if unite == "crédits":
        return f"{fr_num(v, 2)} crédits"
    if unite == "heures":
        if abs(v) < 1 / 60:
            return "moins d'une minute"
        if abs(v) < 1:
            return f"{fr_num(round(v * 60))} min"
        return f"{fr_num(v, 2 if abs(v) < 10 else 1)} h"
    if unite == "jours":
        return f"{fr_num(v, 1)} j"
    if unite == "secondes":
        return f"{fr_num(v)} s"
    if unite == "%":
        return f"{fr_num(v, 1)} %"
    if unite == "slot-heures":
        return f"{fr_num(v, 1)} slot-heures"
    if unite == "requêtes":
        return pl(v, "requête")
    return fr_num(v) + (f" {unite}" if unite else "")


def fig_text(f: dict) -> str:
    if f["nature"] == "non_lu":
        return f"Non lu : {f['raison']}."
    if f["nature"] == "non_estimé":
        return f"Non estimé : {f['raison']}."
    if f["nature"] == "estimé":
        lo, hi = _val(f["bas"], f["unite"]), _val(f["haut"], f["unite"])
        return lo if lo == hi else f"{lo} à {hi}"
    return _val(f["valeur"], f["unite"])


def _base(ids: list[str]) -> str:
    return ", ".join(ids[:6]) + (" …" if len(ids) > 6 else "")


def _fig_cells(f: dict) -> str:
    hyp = "".join(f"<li>{_e(h)}</li>" for h in f.get("hypotheses") or [])
    nat = NATURES[f["nature"]] + (f", confiance {f['confiance']}" if f.get("confiance") else "") + (f" · {_base(f['base'])}" if f.get("base") else "")
    return (f"<td>{_e(fig_text(f))}" + (f"<ul class=hyp>{hyp}</ul>" if hyp else "") + "</td>"
            f"<td class=nat>{_e(nat)}" + (f"<br>{_e(f['formule'])}" if f.get("formule") else "") + "</td>")


def _fig_table(rows: list[tuple[str, dict]]) -> str:
    body = "".join(f"<tr><th scope=row>{_e(label)}</th>{_fig_cells(f)}</tr>" for label, f in rows)
    return f"<table><tr><th>Poste</th><th>Valeur</th><th>Nature et base</th></tr>{body}</table>"


def _list_table(header: list[str], rows: list[list[str]], more_in: str, numeric: tuple[int, ...] = ()) -> str:
    """rows : cellules déjà échappées. Au plus MAX_ROWS lignes ; le reste est renvoyé vers `more_in`."""
    if not rows:
        return "<p class=mut>Aucun.</p>"
    head = "".join(f"<th>{_e(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td class=n>{c}</td>" if i in numeric else f"<td>{c}</td>" for i, c in enumerate(r)) + "</tr>"
                   for r in rows[:MAX_ROWS])
    more = f"<p class=more>… et {fr_num(len(rows) - MAX_ROWS)} autres dans {_e(more_in)}.</p>" if len(rows) > MAX_ROWS else ""
    return f"<table><tr>{head}</tr>{body}</table>{more}"


def _num(v) -> str:
    return "non lu" if v is None else fr_num(v)


def _trim(s: str) -> str:
    return s[:-2] if s.endswith(",0") else s


def _poids(v, plateforme: str) -> str:
    if v is None:
        return "non lu"
    return fr_bytes(v) if plateforme == "bigquery" else f"{fr_num(v, 2)} crédits"


def _plateforme(r: dict) -> str:
    c = connectors.get(r["plateforme"])
    if c:
        return c.label
    return (r.get("capacites") or {}).get("libelle") or r["plateforme"]


def _section(sid: str, title: str, body: str) -> str:
    return f'<section id="{sid}"><h2>{_e(title)}</h2>{body}</section>'


def _recentes(r: dict) -> int:
    """Tables vues seulement en temps réel (absentes d'ACCOUNT_USAGE) : leur historique n'est pas encore disponible."""
    return sum(1 for t in r["tables"] if not t["supprimee_le"] and "recente" in t["flags"])


# --- sections ----------------------------------------------------------------------------------------------


def _entete(r: dict) -> str:
    as_of = parse_dt(r["as_of"])
    meta = [f"Constat du {as_of.strftime('%d/%m/%Y à %H:%M')} UTC"]
    if r.get("latence_max_h") is not None:
        meta.append(f"données en retard de {_trim(fr_num(r['latence_max_h'], 1))} h au plus selon les vues")
    meta += [f"édition : {r['edition']}", f"Canot {(r.get('versions') or {}).get('canot', 'version inconnue')}"]
    alerte = ("<p class=alerte><b>Texte SQL brut exporté.</b> Ce scan a été lancé avec --include-sql-text : le fichier "
              "scan-sql-brut.json, à côté de ce rapport, contient le texte brut de vos requêtes, littéraux compris (droits 0600). "
              "Il n'est pas destiné à la diffusion.</p>") if r.get("avec_sql_brut") else ""
    boxes = "".join(f"<div class=box><h3>{k}</h3><p>{_e(' '.join(r['resume'][k]))}</p></div>" for k in ("DSI", "DAF", "RSSI"))
    return (f'<header id="entete"><p class=diffusion>Diffusion interne</p>'
            f"<h1>Scan de sortie : {_e(_plateforme(r))}</h1>"
            f"<p class=meta>{_e(' · '.join(meta))}</p>{alerte}<div class=trio>{boxes}</div></header>")


def _perimetre(r: dict) -> str:
    lec = r["cout_du_scan"]["lectures"]
    kinds = r["ne_part_pas"]["objets_par_type"]
    items = [
        f"Plateforme : {_plateforme(r)}",
        f"Périmètre : {r['perimetre'] or 'non précisé'}",
        f"Niveau de droits : {r['niveau_droits']} ({NIVEAUX.get(r['niveau_droits'], 'non précisé')})",
        f"Mode SQL : {r['mode_sql']}",
        f"Lectures : {fr_num(lec['lu'])} lues sur {fr_num(lec['total'])}",
        f"Contenu lu : {pl(len([t for t in r['tables'] if not t['supprimee_le']]), 'table')}, "
        f"{pl(sum(kinds.values()), 'objet')}, {pl(len(r['familles']), 'famille')} de requêtes",
    ]
    if r.get("schemas_caches"):
        items.append(f"Schémas ou datasets cachés lus : {', '.join(r['schemas_caches'])}")
    if (r.get("fraicheur") or {}).get("texte"):
        items.append(f"Fraîcheur : {r['fraicheur']['texte']}")
    credits = (r["couts"].get("credits") or {}).get("valeur") or 0
    if not any(not t["supprimee_le"] for t in r["tables"]) and (r["familles"] or credits > 0):
        items.append("Inventaire vide alors que des requêtes ou des crédits ont été lus : vérifier la base scannée et les droits du rôle "
                     "sur ses objets")
    v = r.get("versions") or {}
    items.append("Versions : " + ", ".join(f"{k} {v[k]}" for k in sorted(v)))
    return _section("perimetre", "Périmètre", "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")


def _capacites(r: dict) -> str:
    """Ce que Canot sait faire sur cette plateforme (déclaré par le connecteur), et ce que ce rapport a fait : un inventaire."""
    c = r.get("capacites")
    if not c:
        return ""
    lab = _plateforme(r)
    niv = c.get("niveaux") or []
    inv = c.get("inventaire") or "non déclaré"
    if niv:
        inv += f", niveaux {niv[0]} à {niv[-1]}" if len(niv) > 1 else f", niveau {niv[0]}"
    export, preuve = c.get("export") or [], [p for p in c.get("preuve") or [] if isinstance(p, dict)]
    rows = [[_e("Inventaire"), _e(f"fait par ce scan (niveau {r['niveau_droits']})" if r.get("niveau_droits") else "fait par ce scan"), _e(inv)],
            [_e("Export"), _e("non fait par ce scan"), _e(f"{', '.join(export)}, avec canot drill" if export else "aucun format déclaré")],
            [_e("Preuve"), _e("non faite par ce scan"),
             _e(" ; ".join(f"{p.get('forme')} {p.get('libelle')}" for p in preuve) + ", avec canot drill" if preuve else "aucune forme déclarée")]]
    statut = f"Connecteur {lab} : {c.get('statut') or 'statut non déclaré'}"
    statut += f" ; test consigné : {c['teste_sur']}." if c.get("teste_sur") else " ; aucune date de test consignée dans le dépôt."
    if preuve:
        limite = (f"Ce que Canot ne peut pas prouver ici : les formes déclarées ({', '.join(str(p.get('forme')) for p in preuve)}) portent sur "
                  "les tables et les requêtes ; les objets à reconstruire hors tables sont comptés, pas prouvés.")
    else:
        limite = f"Aucune forme de preuve n'est déclarée pour {lab} : les objets sont comptés, pas prouvés."
    body = (f"<p>Ce rapport est un inventaire : inventorié, ni exporté ni rejoué.</p>"
            + _list_table(["Niveau", "Ce rapport", f"Canot sur {lab}"], rows, "scan.json")
            + f"<p class=mut>{_e(statut)}</p><p>{_e(limite)}</p>")
    return _section("capacites", "Capacités", body)


def _carte_sortie(r: dict) -> str:
    """Objets par portabilité et famille d'objet : seulement si la collecte a lu la portabilité (canot.scan/2)."""
    carte = r.get("carte_sortie") or {}
    if not carte:
        return ""
    rows = [[_e(PORTABILITES.get(p, p)), _e(f.replace("_", " ")), _e(fr_num(n))] for p, fams in carte.items() for f, n in fams.items()]
    body = ("<p class=mut>Portabilité déclarée par la collecte pour chaque objet lu. « Exporté » n'est pas « portable » : "
            "une archive au format de l'éditeur ne s'exploite qu'avec lui.</p>"
            + _list_table(["Portabilité", "Famille d'objet", "Objets"], rows, "scan.json", (2,)))
    return _section("carte-sortie", "Carte de sortie", body)


def _couts(r: dict) -> str:
    c = r["couts"]
    jours = f"Fenêtre : {c['fenetre_jours']} jours. " if c.get("fenetre_jours") else ""
    if r["plateforme"] == "bigquery":
        od, rs = c["a_la_demande"], c["reserve"]
        body = (f"<h3>À la demande</h3>{_fig_table([('Octets facturés', od['octets_factures']), ('Requêtes', od['requetes']), ('En dollars', od['monnaie'])])}"
                f"<h3>Réservé</h3>{_fig_table([('Slot-heures', rs['slot_heures']), ('Requêtes', rs['requetes']), ('En dollars', rs['monnaie'])])}"
                f"<p><b>À la demande et réservé ne s'additionnent pas</b> : deux modes de facturation, deux bases.</p>")
    else:
        body = _fig_table([("Crédits consommés", c["credits"]), ("En dollars", c["monnaie"])])
        if c.get("credits_par_service"):
            body += "<h3>Par service</h3>" + _list_table(["Service", "Crédits"], [[_e(s["service"]), _e(fig_text(s["credits"]))]
                                                                              for s in c["credits_par_service"]], "scan.json", (1,))
        if c.get("credits_par_entrepot"):
            body += "<h3>Par entrepôt</h3>" + _list_table(["Entrepôt", "Crédits"], [[_e(w["entrepot"]), _e(fig_text(w["credits"]))]
                                                                                for w in c["credits_par_entrepot"]], "scan.json", (1,))
    body += "<h3>Stockage</h3>" + _fig_table([("Stockage moyen facturé", c["stockage_octets_moyens"]), ("Stockage en dollars", c["stockage_monnaie"])])
    if c.get("transferts"):
        body += "<h3>Transferts sortants</h3>" + _list_table(["Cible", "Volume"], [[_e(t["cible"]), _e(fig_text(t["octets"]))] for t in c["transferts"]],
                                                             "scan.json", (1,))
    n = _recentes(r)
    if n:
        body += (f"<p class=mut>{_e(pl(n, 'table récente', 'tables récentes'))} sans historique dans ACCOUNT_USAGE : "
                 "leur stockage et leur consommation ne figurent pas encore dans ces chiffres.</p>")
    return _section("couts", "Coûts observés", f"<p class=mut>{_e(jours + c.get('note', ''))}</p>{body}")


def _mouvements(r: dict) -> str:
    m = r["mouvements"]
    present = sorted((t for t in r["tables"] if not t["supprimee_le"]), key=lambda t: (-(t["octets_actifs"] or -1), t["id"]))
    rows = [[_e(t["id"]), _e(t["type"]), _e(fr_bytes(t["octets_actifs"])), _e(t["mouvement"] or "non déterminé"), _e(t["mouvement_preuve"])]
            for t in present]
    body = (f"<p>{fr_num(m['figée'])} figée(s), {fr_num(m['vivante'])} vivante(s), {fr_num(m['recréée'])} recréée(s), "
            f"{fr_num(m['non_determine'])} non déterminée(s).</p><p class=mut>{_e(m['regle'])}. Base : {_e(_base(m['base']) or 'aucune lecture')}.</p>"
            + _list_table(["Table", "Type", "Volume", "Mouvement", "Preuve"], rows, "tables.csv", (2,)))
    n = _recentes(r)
    if n:
        body += (f"<p class=mut>{_e(pl(n, 'table vue', 'tables vues'))} seulement en temps réel : écritures et lectures pas encore "
                 "disponibles dans ACCOUNT_USAGE ; volume lu dans INFORMATION_SCHEMA.</p>")
    return _section("mouvements", "Mouvements", body)


def _ne_part_pas(r: dict) -> str:
    n = r["ne_part_pas"]
    rows = [[_e(KIND_LABELS.get(k, k)), _e(fr_num(v)), _e(", ".join(n["exemples"].get(k, [])) + (" …" if v > 5 else ""))]
            for k, v in n["objets_par_type"].items()]
    body = "<h3>Objets à reconstruire ou à décrire</h3>" + _list_table(["Type", "Nombre", "Exemples"], rows, "scan.json", (1,))
    if n["routines_par_langage"]:
        body += "<p>Routines par langage : " + _e(", ".join(f"{k} {fr_num(v)}" for k, v in n["routines_par_langage"].items())) + ".</p>"
    cols = [[_e(t["id"]), _e(c["nom"]), _e(c["type"])] for t in r["tables"] if not t["supprimee_le"] for c in t["colonnes_non_portables"]]
    body += f"<h3>Colonnes à type propre à la plateforme ({fr_num(n['colonnes_non_portables'])})</h3>"
    body += _list_table(["Table", "Colonne", "Type"], cols, "tables.csv")
    body += "<h3>Historique facturé</h3>" + _fig_table([(f"Time Travel et Fail-safe ({pl(n['historique']['tables'], 'table')})", n["historique"]["octets"])])
    return _section("ne-part-pas", "Ce qui ne part pas avec la copie", body)


def _dependances(r: dict) -> str:
    d = r["dependances"]
    body = ""
    if d["externes"]:
        body += "<h3>Dépendances externes (F01)</h3>" + _list_table(
            ["Objet", "Type", "Emplacement"], [[_e(x["id"]), _e(KIND_LABELS.get(x["type"], x["type"])), _e(", ".join(x["emplacements"]) or "non lu")]
                                               for x in d["externes"]], "drapeaux.csv")
    if d["cachees"]:
        body += "<h3>Dépendances externes cachées (F02)</h3>" + _list_table(
            ["Objet", "Chemin", "Emplacement"], [[_e(x["id"]), _e(" → ".join(x["chemin"])), _e(", ".join(x["emplacements"]) or "non lu")]
                                                 for x in d["cachees"]], "drapeaux.csv")
    if d["ecritures_externes"]:
        body += "<h3>Écritures hors de la plateforme (F03)</h3>" + _list_table(
            ["Source", "Mode", "Destination"], [[_e(f"{x['cible_type']} {x['cible']}"), _e(x["mode"]), _e(", ".join(x["emplacements"]) or "non lue")]
                                                for x in d["ecritures_externes"]], "drapeaux.csv")
    if d["consommateurs"]:
        body += "<h3>Consommateurs externes (F09)</h3>" + _list_table(
            ["Partage", "Comptes"], [[_e(x["id"]), _e(_num(x["comptes"]))] for x in d["consommateurs"]], "drapeaux.csv", (1,))
    if d["outils"]:
        body += "<h3>Outils vus dans les requêtes</h3>" + _list_table(
            ["Outil", "Familles", "Exécutions"], [[_e(x["outil"]), _e(fr_num(x["familles"])), _e(fr_num(x["executions"]))] for x in d["outils"]],
            "familles.csv", (1, 2))
    if d.get("clients"):
        body += "<h3>Clients connectés (sessions)</h3>" + _list_table(
            ["Client", "Sessions"], [[_e(x["outil"]), _e(_num(x["sessions"]))] for x in d["clients"]], "scan.json", (1,))
    if not body:
        body = "<p class=mut>Aucune dépendance externe observée dans les lectures faites.</p>"
    return _section("dependances", "Dépendances", body)


def _rejouabilite(r: dict) -> str:
    rj = r["rejouabilite"]
    if rj["statut"] != "lu":
        return _section("rejouabilite", "Rejouabilité des requêtes", f"<p>Non lu : {_e(rj['raison'])}.</p>")
    rows = [[_e(k), _e(CATEGORIES.get(k, "")), _e(_num(v["familles"])), _e(_num(v["executions"])),
             _e(_poids(v["poids"], r["plateforme"]))] for k, v in rj["categories"].items()]
    body = _list_table(["Catégorie", "Signification", "Familles", "Exécutions", "Poids"], rows, "scan.json", (2, 3, 4))
    t = {k: {**f, "hypotheses": [h for h in f.get("hypotheses") or [] if h != MENTION_REJOUABILITE]} for k, f in rj["taux"].items()}
    labels = {"familles": "Taux C0, par familles", "executions": "Taux C0, par exécutions", "poids": "Taux C0, pondéré"}
    body += _fig_table([(labels[k], t[k]) for k in ("familles", "executions", "poids") if k in t])
    body += f"<p class=mut>Base : {_e(rj['base'] or 'non transmise')}.</p>"
    body += f"<p><b>{_e(MENTION_REJOUABILITE)}.</b> Traduit vers DuckDB, non comparé.</p>"
    extra = []
    if (rj.get("non_testees") or {}).get("familles"):
        extra.append(f"{pl(rj['non_testees']['familles'], 'famille')} non testée(s)")
    se = rj.get("secrets_ecartes")
    se = se.get("familles") if isinstance(se, dict) else se
    if isinstance(se, int) and se > 0:
        extra.append(f"{pl(se, 'famille')} contenant un secret, comptée(s) puis écartée(s)")
    if extra:
        body += f"<p class=mut>{_e(cap(' ; '.join(extra)))}.</p>"
    if rj.get("fonctions"):
        fn = sorted(rj["fonctions"].items(), key=lambda kv: (-kv[1], kv[0]))
        body += "<h3>Fonctions propres à la plateforme</h3>" + _list_table(["Fonction", "Familles"], [[_e(k), _e(fr_num(v))] for k, v in fn],
                                                                            "scan.json", (1,))
    fams = [[_e(f["categorie"]), _e(_num(f["n_exec"])), _e(f["outil"] or f["app"] or ""),
             f"<code>{_e((f['sql_normalise'] or '')[:160])}{'…' if len(f['sql_normalise'] or '') > 160 else ''}</code>"]
            for f in r["familles"]]
    body += "<h3>Familles les plus exécutées (formes normalisées)</h3>" + _list_table(["Catégorie", "Exécutions", "Outil", "Forme normalisée"],
                                                                                    fams, "familles.csv", (1,))
    return _section("rejouabilite", "Rejouabilité des requêtes", body)


def _fourchettes(r: dict) -> str:
    f = r["fourchettes"]
    rows = [("Volume à copier", f["volume"]), ("Durée de lecture de la copie", f["duree_lecture"])]
    if "credits_lecture" in f:
        rows.append(("Crédits de lecture de la copie", f["credits_lecture"]))
    rows += [("Coût de lecture de la copie", f["cout_lecture"]), ("Travail de reprise", f["reprise"])]
    return _section("fourchettes", "Fourchettes", "<p class=mut>Estimations de planification : chaque fourchette porte ses hypothèses. "
                    "Un canot drill remplace le débit supposé par la mesure.</p>" + _fig_table(rows))


def _constats(r: dict) -> str:
    by = {}
    for d in r["drapeaux"]:
        by.setdefault(d["code"], []).append(d)
    body = (f"<p class=mut>Chaque constat est étiqueté « {ETIQUETTE} », sans niveau de gravité. "
            "Il dit ce qui est observé et ce qui ne l'est pas.</p>")
    for code, label in LIBELLES.items():
        items = by.get(code)
        if not items:
            continue
        rows = [[_e(f"{d['cible_type']} {d['cible']}"), _e(d["texte"])] for d in items]
        body += (f"<h3>{code} · {_e(label)}<span class=tag>{_e(ETIQUETTE)}</span></h3><p class=mut>{pl(len(items), 'constat')}</p>"
                 + _list_table(["Cible", "Observé"], rows, "drapeaux.csv"))
    hors_entrepot = r.get("famille") not in (None, "entrepot")  # F13-F16 ne concernent pas les entrepôts
    empty = [c for c in LIBELLES if c not in by and (hors_entrepot or c not in CODES_HORS_ENTREPOT)]
    if empty:
        body += (f"<p class=mut>Aucun constat pour : {', '.join(empty)}. "
                 "Une absence de constat ne vaut que pour les lectures faites (voir F12 et la méthode).</p>")
    return _section("constats", "Constats", body)


def _non_estime(r: dict) -> str:
    items = r["non_estime"]
    lis = "".join(f"<li>{_e(i['sujet'])} : {_e(i['raison'])}.</li>" for i in items[:MAX_ROWS])
    more = f"<p class=more>… et {fr_num(len(items) - MAX_ROWS)} autres dans scan.json.</p>" if len(items) > MAX_ROWS else ""
    return _section("non-estime", "Ce que ce rapport ne chiffre pas", f"<ul>{lis}</ul>{more}")


def _methode(r: dict) -> str:
    lecs = sorted(r["lectures"], key=lambda x: (x["statut"] == "lu", x["id"]))
    rows = []
    for x in lecs:
        fen = x["fenetre"] or {}
        fenetre = f"du {fr_date(fen.get('du'))} au {fr_date(fen.get('au'))}" if fen.get("du") or fen.get("au") else ""
        raison = x["raison"] or ""
        if x.get("limite"):
            raison += f" ; limite : {x['limite']}"
        rows.append([_e(x["id"]), _e(short_vue(x["vue"])), _e("lu" if x["statut"] == "lu" else "non lu"), _e(raison), _e(_num(x["lignes"])),
                     _e(fenetre), _e("" if x["latence_max_h"] is None else f"{_trim(fr_num(x['latence_max_h'], 1))} h")])
    plat, niv = r["plateforme"], r["niveau_droits"] or 0
    fiche = connectors.profile(plat)
    droits = [d for lvl, ds in sorted((fiche.get("droits") or {}).items()) if lvl <= niv for d in ds]
    cs = r["cout_du_scan"]
    cost = [("Requêtes émises", cs["requetes"]), ("Durée de lecture", cs["duree"])]
    if "octets_factures" in cs:
        cost.append(("Octets facturés par le scan", cs["octets_factures"]))
    if "credits" in cs:
        cost.append(("Crédits consommés par le scan", cs["credits"]))
    rg = r["regles"]
    body = (f"<p>{fr_num(cs['lectures']['lu'])} lectures lues sur {fr_num(cs['lectures']['total'])}. Chaque lecture est consignée avec son statut "
            "et sa raison ; une valeur non lue reste vide, jamais 0.</p>"
            + _list_table(["Id", "Vue", "Statut", "Raison", "Lignes", "Fenêtre", "Latence"], rows, "lectures.csv", (4,))
            + f"<h3>Droits utilisés</h3><p>Niveau {_e(niv)} : {_e(', '.join(droits) or 'non précisés')}. "
            f"Jamais demandés : {_e(fiche.get('droits_jamais', 'droits d’écriture'))}.</p>"
            + "<h3>Coût du scan</h3>" + _fig_table(cost)
            + "<h3>Garde-fous</h3><ul>"
            "<li>Tout SQL envoyé passe par une garde lecture seule : seules les instructions SELECT, SHOW, DESCRIBE et ALTER SESSION SET partent. "
            "Aucune écriture, aucune activation d'API, aucune modification de paramètre.</li>"
            "<li>Utilisateurs et rôles des requêtes pseudonymisés par HMAC, avec un sel tiré pour ce scan et jamais écrit ; "
            "valeurs d'étiquettes hachées ; emplacements réduits au compartiment.</li>"
            "<li>Le texte SQL brut n'est jamais écrit : seules les formes normalisées, littéraux masqués, sortent de la collecte"
            + (" (sauf scan-sql-brut.json, demandé par --include-sql-text)" if r.get("avec_sql_brut") else "") + ".</li></ul>"
            + f"<h3>Règles</h3><ul><li>Mouvement : {_e(r['mouvements']['regle'])}.</li>"
            f"<li>Stockage sans lecture : fenêtre réelle de la vue lue, comparée à {rg['horizon_lectures_jours']} jours ; "
            "les angles morts de chaque vue sont cités dans le constat.</li>"
            f"<li>Durée de copie : débit supposé de {fr_num(rg['debit_mo_s'][0])} à {fr_num(rg['debit_mo_s'][1])} Mo/s.</li>"
            "<li>Les prix catalogue sont datés et sourcés ; ils ne remplacent pas le prix de votre contrat.</li></ul>")
    return _section("methode", "Méthode", body)


def _suite() -> str:
    return ('<section id="suite" class=suite><h2>Suite</h2>'
            "<p><b>Ce rapport décrit votre plateforme ; il ne recommande ni de partir ni de rester.</b></p>"
            "<p>Pour mesurer une sortie sur un périmètre choisi : <code>canot drill</code> copie vers Apache Iceberg, "
            "compare source et copie, et signe un dossier de preuve.</p>"
            "<p>Pour aller plus loin, le diagnostic q4o, dès 22 000 € HT : tarif de votre contrat, exécution réelle, scénarios, marche à suivre. "
            f'<a href="{CANOT_URL}">canot.io</a></p></section>')


def render_html(result: dict) -> str:
    """scan.json -> page HTML autonome. Aucun script, aucune ressource externe (CSP stricte : seule la feuille de style inline, par empreinte)."""
    css_hash = base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
    csp = f"default-src 'none'; style-src 'sha256-{css_hash}'; img-src 'none'; base-uri 'none'; form-action 'none'"
    body = "".join([_entete(result), "<main>", _perimetre(result), _capacites(result), _couts(result), _mouvements(result),
                    _ne_part_pas(result), _carte_sortie(result), _dependances(result), _rejouabilite(result), _fourchettes(result), _constats(result), _non_estime(result),
                    _methode(result), _suite(), "</main>"])
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            f"<title>Scan de sortie Canot</title><style>{CSS}</style></head><body>{body}</body></html>\n")


# --- CSV -------------------------------------------------------------------------------------------------


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "oui" if v else "non"
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


def write_csv(result: dict, outdir: Path) -> list[Path]:
    """tables.csv, drapeaux.csv, familles.csv (formes normalisées seulement), lectures.csv. UTF-8, ordre stable."""
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
        _csv(outdir / "tables.csv", ["id", "base", "schema", "nom", "type", "lignes", "octets_actifs", "octets_historique", "retention_jours",
                                     "clone_de", "supprimee_le", "emplacement", "proprietaire", "mouvement", "mouvement_preuve", "n_lectures",
                                     "derniere_lecture", "n_ecritures", "derniere_ecriture", "derniere_ddl", "colonnes_non_portables",
                                     "politiques", "etiquettes_n", "drapeaux"], tables),
        _csv(outdir / "drapeaux.csv", ["code", "libelle", "etiquette", "cible_type", "cible", "texte", "confiance", "base"], drapeaux),
        _csv(outdir / "familles.csv", ["empreinte", "hash_plateforme", "categorie", "n_exec", "temps_ms", "credits", "octets", "app", "outil",
                                       "entrepot", "tables", "fonctions", "ecriture_externe", "sql_normalise"], familles),
        _csv(outdir / "lectures.csv", ["id", "vue", "statut", "raison", "limite", "lignes", "fenetre_du", "fenetre_au", "latence_max_h"], lectures),
    ]
