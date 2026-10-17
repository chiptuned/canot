from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path

CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#14212b}
h1{font-size:1.7rem}h2{margin-top:2rem;border-bottom:1px solid #cdd7de;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e1e8ec;padding:6px 8px;text-align:left;vertical-align:top}
.ok{color:#1b7a3a;font-weight:600}.ko{color:#b3261e;font-weight:600}.warn{color:#9a5b00;font-weight:600}
code{background:#eef3f6;padding:1px 4px;border-radius:3px;word-break:break-all}.box{background:#eef3f6;padding:.8rem 1rem;border-radius:6px}
.verdict{font-size:1.6rem;line-height:1.2;margin:.4rem 0 1rem}.find{display:grid;grid-template-columns:110px 1fr;gap:16px;padding:14px 0;border-bottom:1px solid #e1e8ec}
.tag{align-self:start;font-size:13px;font-weight:600;padding:4px 10px;border-radius:999px;text-align:center}.tag.ok{background:#e7f2ea;color:#2f7a4b}.tag.warn{background:#fbf1ea;color:#c8652f}.tag.info{background:#e6ecf2;color:#1f3a5f}
.find b{display:block;font-size:1.05rem}.find span{color:#41566b}.cta{background:#1f3a5f;color:#fff;padding:1rem 1.2rem;border-radius:8px;margin-top:2rem}.cta a{color:#ffb88f;font-weight:600}
@media(max-width:560px){.find{grid-template-columns:1fr}}"""


def _e(x) -> str:
    return html.escape(str(x))


def synthese(res: dict) -> dict:
    """Le constat en langage simple : ce qui est identique, ce qui diffère, ce qui est à traiter."""
    s = res["summary"]
    tabs = res["checks"]
    rows = sum(c["rows_target"] for c in tabs)
    prec = [c for c in tabs if not c["ok"] and c.get("precision_cols") and c["rows_source"] == c["rows_target"] and c["fingerprint_ok"] and c["schema_ok"]
            and all(d.split(".")[0] in c["precision_cols"] for d in c["stats_diffs"])]
    ko = [c for c in tabs if not c["ok"] and c not in prec]
    qm = [q for q in res["queries"] if q["status"] == "mismatch"]
    qu = [q for q in res["queries"] if q["status"] in ("untranslatable", "error")]
    qs = [q for q in res["queries"] if q["status"] == "skipped"]
    objs = sum(len(v) for v in res["scan"]["objects_that_do_not_move"].values())
    conv = {(c["table"], c["column"]): c for c in res["conversions"]}
    prec_cols = {(c["table"], col) for c in prec for col in c["precision_cols"]}
    lossy_no_diff = [c for c in res["conversions"] if c["lossy"] and (c["table"], c["column"]) not in prec_cols]
    todo = len(qm) + len(qu) + (1 if prec_cols else 0) + (1 if lossy_no_diff else 0) + (1 if objs else 0) + len(s["tables_error"])
    f = []
    if not ko and not s["tables_error"]:
        f.append(("ok", "Identique", "Le contenu arrive à l'identique." if not prec else "Le contenu arrive, à une précision près.",
                  f"Les {len(tabs)} tables, soit {rows:,} lignes, ont été copiées puis comparées : nombre de lignes, statistiques par colonne et empreinte du contenu.".replace(",", " ")
                  + (" Seules les colonnes signalées plus bas diffèrent." if prec else " Aucune différence.")))
    else:
        f.append(("warn", "Écart", "Des données diffèrent.", f"{len(ko) + len(s['tables_error'])} table(s) sur {s['tables_total']} ne sont pas identiques : à corriger avant de conclure."))
    if res["queries"]:
        done = [q for q in res["queries"] if q["status"] != "skipped"]
        tag = "ok" if not qm and not qu else "info"
        f.append((tag, f"{s['queries_match']} sur {len(done)}", "Vos rapports donnent les mêmes chiffres." if tag == "ok" else "Vos rapports donnent presque les mêmes chiffres.",
                  f"{len(done)} requêtes de reporting ont été rejouées des deux côtés. {s['queries_match']} rendent exactement le même résultat."))
    for q in qm:
        f.append(("warn", "À traiter", "Un rapport rend un résultat différent.", f"{q['sql'][:110].strip()}… : {q['detail'] or 'résultat différent'}. Les chiffres sont peut-être justes et la présentation diffère : mieux vaut le savoir avant le départ."))
    if qu:
        f.append(("warn", "À traiter", f"{len(qu)} requête(s) n'ont pas pu être rejouées.", "Fonction ou syntaxe propre à la plateforme d'origine : à réécrire pour la destination."))
    if qs:
        f.append(("info", "Non rejouées", f"{len(qs)} requête(s) portent sur des tables trop volumineuses.", "Le rejeu charge les tables en mémoire. Augmentez --replay-max-gb ou rejouez sur un échantillon."))
    if prec_cols:
        lines = []
        for t, col in sorted(prec_cols):
            c = conv.get((t, col))
            lines.append(f"{t}.{col} : {c['source_type']} devient {c['target_type']}" if c else f"{t}.{col}")
        f.append(("warn", "À traiter", f"{len(prec_cols)} colonne(s) perdent de la précision à l'arrivée.", " · ".join(lines) + ". Les valeurs diffèrent au-delà de la précision du type d'arrivée. Ce n'est pas une corruption : c'est un choix de type à décider avant de partir."))
    if lossy_no_diff:
        f.append(("warn", "À traiter", f"{len(lossy_no_diff)} conversion(s) de type avec perte possible.", "Aucune différence observée sur ces données, mais d'autres valeurs pourraient en montrer : la liste est plus bas."))
    if objs:
        f.append(("warn", "À traiter", f"{objs} objet(s) ne partent pas tels quels.", "Vues, routines, tâches planifiées : à reconstruire sur la destination."))
    f.append(("info", "Signé", "Toute modification du journal se voit.", "Chaque étape est enregistrée dans un journal signé avec une clé créée chez vous. La signature garantit l'intégrité du journal, pas l'identité de son auteur ni la date : l'empreinte de la clé est indiquée en bas du dossier."))
    if ko or s["tables_error"]:
        verdict = "Sortie non prouvée sur ce périmètre : des données diffèrent."
    elif todo == 0:
        verdict = "Sortie faisable sur ce périmètre. Rien à traiter avant de partir."
    else:
        verdict = f"Sortie faisable sur ce périmètre. {'Un point' if todo == 1 else str(todo) + ' points'} à traiter avant de partir."
    return {"verdict": verdict, "findings": f, "todo": todo}


def _key_fingerprint(out: Path) -> str:
    try:
        from cryptography.hazmat.primitives import serialization

        pub = serialization.load_pem_public_key((out / "evidence.pub").read_bytes())
        raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        h = hashlib.sha256(raw).hexdigest()
        return " ".join(h[i:i + 8] for i in range(0, 32, 8))
    except Exception:
        return "non disponible"


def _clean(label: str) -> str:
    """Pas de chemins locaux dans un dossier qu'on transmet."""
    return re.sub(r"(local|duckdb):[^ ]*/([^/ ]+)", r"\1:\2", str(label))


def write_report(res: dict, out: Path) -> Path:
    s = res["summary"]
    (out / "report.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
    ok = s["parity"] == "complète"
    rows = "".join(
        f"<tr><td>{_e(c['table'])}</td><td>{c['rows_source']}</td><td>{c['rows_target']}</td>"
        f"<td class='{'ok' if c['ok'] else ('warn' if c.get('precision_cols') and c['rows_source'] == c['rows_target'] and c['fingerprint_ok'] else 'ko')}'>{'identique' if c['ok'] else ('précision' if c.get('precision_cols') and c['rows_source'] == c['rows_target'] and c['fingerprint_ok'] else 'écart')}</td>"
        f"<td>{_e('; '.join(c['stats_diffs'][:3]))}{' buckets ' + _e(c['bad_buckets'][:5]) if c['bad_buckets'] else ''}</td></tr>"
        for c in res["checks"])
    qrows = "".join(
        f"<tr><td><code>{_e(q['sql'][:140])}</code></td><td class='{'ok' if q['status']=='match' else ('warn' if q['status']=='skipped' else 'ko')}'>{_e({'skipped': 'non rejouée'}.get(q['status'], q['status']))}"
        f"{' (ordre différent)' if q['order_differs'] else ''}</td><td>{_e(q['detail'])}</td></tr>" for q in res["queries"])
    crows = "".join(f"<tr><td>{_e(c['table'])}.{_e(c['column'])}</td><td>{_e(c['source_type'])} → {_e(c['target_type'])}</td>"
                    f"<td class='{'warn' if c['lossy'] else ''}'>{_e(c['note'])}{' (perte possible)' if c['lossy'] else ''}</td></tr>" for c in res["conversions"])
    nonp = res["scan"]["non_portable_columns"]
    objs = res["scan"]["objects_that_do_not_move"]
    objrows = "".join(f"<tr><td>{_e(k)}</td><td>{len(v)}</td><td>{_e(', '.join(v[:8]))}{'…' if len(v) > 8 else ''}</td></tr>" for k, v in objs.items() if v)
    rto = s.get("rto")
    sy = synthese(res)
    finds = "".join(f'<div class="find"><span class="tag {t}">{_e(tag)}</span><div><b>{_e(h)}</b><span>{_e(d)}</span></div></div>' for t, tag, h, d in sy["findings"])
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8"><title>Dossier de preuve Canot</title><style>{CSS}</style>
<h1>Dossier de preuve : exercice de sortie</h1>
<p>Source : <code>{_e(_clean(res['source']))}</code> · Cible : <code>{_e(_clean(res['target']))}</code> · Constat daté du {datetime.now().strftime('%d/%m/%Y à %H:%M')}, sur le périmètre exécuté · Canot {_e(res['env']['canot'])}</p>
<h2 style="border:0;margin-bottom:0">Synthèse</h2><p class="verdict"><b>@@VERDICT@@</b></p>@@FINDINGS@@
<div class="box"><b>Constat :</b> <span class="{'ok' if ok else 'warn'}">{_e('contenu identique' if ok else 'écarts à examiner')}</span> —
{s['tables_ok']}/{s['tables_total']} tables identiques, {s['queries_match']}/{s['queries_total']} requêtes identiques,
{s['queries_untranslatable']} non rejouables, {s['queries_mismatch']} avec écart, {s['lossy_conversions']} conversion(s) avec perte possible.</div>
<h2>Mesures de cet exercice</h2><ul>
{('<li>Débit de copie mesuré : ' + str(s['throughput_mb_s']) + ' Mo/s</li>') if s['throughput_mb_s'] else ''}
{('<li>Temps de reprise estimé (fourchette) : ' + _e(f"{rto['seconds'][0]} à {rto['seconds'][1]} s — {rto['basis']}") + '</li>') if rto else ''}
{('<li>Crédits d\'entrepôt consommés (est.) : ' + str(s['warehouse_credits_used_est']) + (' · coût (est.) : ' + str(s['cost_usd_est']) + ' $' if s['cost_usd_est'] is not None else '') + '</li>') if s['warehouse_credits_used_est'] is not None else ''}
<li>Durée totale de l'exercice : {s['duration_s']} s</li></ul>
<p class="small">Ces mesures décrivent cet exercice, sur ce périmètre. Elles ne comparent pas deux plateformes.</p>
<h2>Tables</h2><table><tr><th>Table</th><th>Lignes source</th><th>Lignes cible</th><th>Résultat</th><th>Détail</th></tr>{rows}</table>
<h2>Requêtes rejouées des deux côtés</h2><table><tr><th>Requête</th><th>Résultat</th><th>Détail</th></tr>{qrows or '<tr><td colspan=3>aucune</td></tr>'}</table>
<h2>Conversions de types</h2><table>{crows or '<tr><td>aucune</td></tr>'}</table>
<h2>Ce qui ne part pas tel quel</h2>
<p>Colonnes à type propre à la plateforme : {len(nonp)}. Objets à reconstruire (vues, procédures, tâches…) :</p>
<table><tr><th>Type</th><th>Nombre</th><th>Exemples</th></tr>{objrows or '<tr><td colspan=3>aucun détecté</td></tr>'}</table>
<h2>Tolérances appliquées</h2><p><code>{_e(json.dumps(res['tolerances'], ensure_ascii=False))}</code></p>
<h2>Intégrité du dossier</h2>
<p>Journal chaîné : {res['seal']['entries']} entrées, dernière empreinte <code>{_e(res['seal']['last_hash'])}</code>.
Toute modification du journal se voit. Vérification : <code>canot verify-evidence {_e(out.name)}</code></p>
<p>Empreinte de la clé publique de signature : <code>{_e(_key_fingerprint(out))}</code>.
La signature garantit l'intégrité du journal. Elle n'établit ni l'identité de l'auteur ni la date : pour cela, conservez le dossier dans votre propre archivage horodaté.</p>
<p class="small"><i>Ce rapport est un relevé de mesures pour votre propre évaluation. Ce n'est pas une certification et il ne dit pas si vos contrats ou votre organisation sont conformes à un règlement.</i></p>
</html>"""
    page = page.replace("@@VERDICT@@", _e(sy["verdict"])).replace("@@FINDINGS@@", finds)
    page = page.replace("</html>", '''<div class="cta">Ce dossier couvre le périmètre que vous avez choisi. Pour l'obtenir sur votre environnement, avec trois scénarios de coût de sortie et la marche à suivre : <a href="https://canot.io/#contact">canot.io</a>.</div></html>''')
    p = out / "report.html"
    p.write_text(page)
    return p
