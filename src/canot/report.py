from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

CSS = """body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#14212b}
h1{font-size:1.7rem}h2{margin-top:2rem;border-bottom:1px solid #cdd7de;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e1e8ec;padding:6px 8px;text-align:left;vertical-align:top}
.ok{color:#1b7a3a;font-weight:600}.ko{color:#b3261e;font-weight:600}.warn{color:#9a5b00;font-weight:600}
code{background:#eef3f6;padding:1px 4px;border-radius:3px;word-break:break-all}.box{background:#eef3f6;padding:.8rem 1rem;border-radius:6px}"""


def _e(x) -> str:
    return html.escape(str(x))


def write_report(res: dict, out: Path) -> Path:
    s = res["summary"]
    (out / "report.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
    ok = s["parity"] == "complète"
    rows = "".join(
        f"<tr><td>{_e(c['table'])}</td><td>{c['rows_source']}</td><td>{c['rows_target']}</td>"
        f"<td class='{'ok' if c['ok'] else 'ko'}'>{'identique' if c['ok'] else 'écart'}</td>"
        f"<td>{_e('; '.join(c['stats_diffs'][:3]))}{' buckets ' + _e(c['bad_buckets'][:5]) if c['bad_buckets'] else ''}</td></tr>"
        for c in res["checks"])
    qrows = "".join(
        f"<tr><td><code>{_e(q['sql'][:140])}</code></td><td class='{'ok' if q['status']=='match' else 'ko'}'>{_e(q['status'])}"
        f"{' (ordre différent)' if q['order_differs'] else ''}</td><td>{_e(q['detail'])}</td></tr>" for q in res["queries"])
    crows = "".join(f"<tr><td>{_e(c['table'])}.{_e(c['column'])}</td><td>{_e(c['source_type'])} → {_e(c['target_type'])}</td>"
                    f"<td class='{'warn' if c['lossy'] else ''}'>{_e(c['note'])}{' (perte possible)' if c['lossy'] else ''}</td></tr>" for c in res["conversions"])
    nonp = res["scan"]["non_portable_columns"]
    objs = res["scan"]["objects_that_do_not_move"]
    objrows = "".join(f"<tr><td>{_e(k)}</td><td>{len(v)}</td><td>{_e(', '.join(v[:8]))}{'…' if len(v) > 8 else ''}</td></tr>" for k, v in objs.items() if v)
    rto = s.get("rto")
    page = f"""<!doctype html><html lang="fr"><meta charset="utf-8"><title>Dossier de preuve Canot</title><style>{CSS}</style>
<h1>Dossier de preuve : exercice de sortie</h1>
<p>Source : <code>{_e(res['source'])}</code> · Cible : <code>{_e(res['target'])}</code> · Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')} · Canot {_e(res['env']['canot'])}</p>
<div class="box"><b>Parité :</b> <span class="{'ok' if ok else 'ko'}">{_e(s['parity'])}</span> —
{s['tables_ok']}/{s['tables_total']} tables identiques, {s['queries_match']}/{s['queries_total']} requêtes identiques,
{s['queries_untranslatable']} non rejouables, {s['queries_mismatch']} avec écart, {s['lossy_conversions']} conversion(s) avec perte possible.</div>
<h2>Mesures</h2><ul>
<li>Débit mesuré : {s['throughput_mb_s']} Mo/s</li>
<li>Temps de reprise estimé (fourchette) : {_e(f"{rto['seconds'][0]} à {rto['seconds'][1]} s — {rto['basis']}") if rto else 'non mesuré'}</li>
<li>Crédits d'entrepôt consommés (est.) : {s['warehouse_credits_used_est']} · coût (est.) : {s['cost_usd_est']} $</li>
<li>Durée totale de l'exercice : {s['duration_s']} s</li></ul>
<h2>Tables</h2><table><tr><th>Table</th><th>Lignes source</th><th>Lignes cible</th><th>Résultat</th><th>Détail</th></tr>{rows}</table>
<h2>Requêtes rejouées des deux côtés</h2><table><tr><th>Requête</th><th>Résultat</th><th>Détail</th></tr>{qrows or '<tr><td colspan=3>aucune</td></tr>'}</table>
<h2>Conversions de types</h2><table>{crows or '<tr><td>aucune</td></tr>'}</table>
<h2>Ce qui ne part pas tel quel</h2>
<p>Colonnes à type propre à la plateforme : {len(nonp)}. Objets à reconstruire (vues, procédures, tâches…) :</p>
<table><tr><th>Type</th><th>Nombre</th><th>Exemples</th></tr>{objrows or '<tr><td colspan=3>aucun détecté</td></tr>'}</table>
<h2>Tolérances appliquées</h2><p><code>{_e(json.dumps(res['tolerances'], ensure_ascii=False))}</code></p>
<h2>Intégrité du dossier</h2>
<p>Journal chaîné : {res['seal']['entries']} entrées, dernier empreinte <code>{_e(res['seal']['last_hash'])}</code>.
Toute modification du journal se voit. Vérification : <code>canot verify-evidence {_e(str(out))}</code></p>
<p class="small"><i>Ce rapport est un relevé de mesures pour votre propre évaluation. Ce n'est pas une certification et il ne dit pas si vos contrats ou votre organisation sont conformes à un règlement.</i></p>
</html>"""
    p = out / "report.html"
    p.write_text(page)
    return p
