from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path

from .regulatory import section as regulatory_section
from .brand import CSS as BRAND_CSS, brand_bar, drill_share, share_block

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
    """The finding in plain language: what is identical, what differs, what needs action."""
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
        f.append(("ok", "Identical", "The content arrives identical." if not prec else "The content arrives, except for precision.",
                  f"All {len(tabs)} tables, {rows:,} rows in total, were copied then compared: row count, per-column statistics and content fingerprint."
                  + (" Only the columns flagged below differ." if prec else " No difference.")))
    else:
        f.append(("warn", "Mismatch", "Some data differs.", f"{len(ko) + len(s['tables_error'])} table(s) out of {s['tables_total']} are not identical: fix them before drawing conclusions."))
    if res["queries"]:
        done = [q for q in res["queries"] if q["status"] != "skipped"]
        tag = "ok" if not qm and not qu else "info"
        f.append((tag, f"{s['queries_match']} of {len(done)}", "Your reports give the same figures." if tag == "ok" else "Your reports give almost the same figures.",
                  f"{len(done)} reporting queries were replayed on both sides. {s['queries_match']} return exactly the same result."))
    for q in qm:
        f.append(("warn", "To address", "A report returns a different result.", f"{q['sql'][:110].strip()}…: {q['detail'] or 'different result'}. The figures may be right and only the presentation differs: better to know before leaving."))
    if qu:
        f.append(("warn", "To address", f"{len(qu)} query(ies) could not be replayed.", "Function or syntax specific to the source platform: rewrite it for the destination."))
    if qs:
        f.append(("info", "Not replayed", f"{len(qs)} query(ies) read tables that are too large.", "The replay loads tables in memory. Raise --replay-max-gb or replay on a sample."))
    if prec_cols:
        lines = []
        for t, col in sorted(prec_cols):
            c = conv.get((t, col))
            lines.append(f"{t}.{col}: {c['source_type']} becomes {c['target_type']}" if c else f"{t}.{col}")
        f.append(("warn", "To address", f"{len(prec_cols)} column(s) lose precision on arrival.", " · ".join(lines) + ". The values differ beyond the precision of the target type. This is not corruption: it is a type choice to make before leaving."))
    if lossy_no_diff:
        f.append(("warn", "To address", f"{len(lossy_no_diff)} type conversion(s) with possible loss.", "No difference observed on this data, but other values could show one: the list is below."))
    if objs:
        f.append(("warn", "To address", f"{objs} object(s) do not move as is.", "Views, routines, scheduled tasks: rebuild them on the destination."))
    f.append(("info", "Signed", "Any change to the log shows.", "Each step is recorded in a log signed with a key created on your side. The signature attests the integrity of the log, not the identity of its author nor the date: the key fingerprint is shown at the bottom of the report."))
    if ko or s["tables_error"]:
        verdict = "Exit not proven on this scope: some data differs."
    elif todo == 0:
        verdict = "Exit feasible on this scope. Nothing to address before leaving."
    else:
        verdict = f"Exit feasible on this scope. {'One point' if todo == 1 else str(todo) + ' points'} to address before leaving."
    return {"verdict": verdict, "findings": f, "todo": todo}


def _key_fingerprint(out: Path) -> str:
    try:
        from cryptography.hazmat.primitives import serialization

        pub = serialization.load_pem_public_key((out / "evidence.pub").read_bytes())
        raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        h = hashlib.sha256(raw).hexdigest()
        return " ".join(h[i:i + 8] for i in range(0, 32, 8))
    except Exception:
        return "not available"


REREAD_FILE = "reread.sql"


def write_relire_sql(res: dict, out: Path) -> Path:
    """A SQL file to reread the copy with DuckDB, without Canot: evidence that it does not depend on the tool."""
    loc = res.get("copy_location") or {}
    lines = ["-- Reread the copy without Canot, with DuckDB (https://duckdb.org).", f"-- Run: duckdb < {REREAD_FILE}", "INSTALL iceberg; LOAD iceberg;"]
    if loc.get("kind") == "s3":
        lines += ["-- S3-compatible storage: first create a secret with YOUR keys (never paste them into a shared file).",
                  "-- CREATE SECRET (TYPE S3, KEY_ID '...', SECRET '...', ENDPOINT 's3-host', URL_STYLE 'path');"]
    for fqn, meta in sorted((loc.get("metadata") or {}).items()):
        path = meta[len("file://"):] if meta.startswith("file://") else meta
        lines.append(f"SELECT '{fqn}' AS table_name, count(*) AS row_count FROM iceberg_scan('{path}');")
    p = out / REREAD_FILE
    p.write_text("\n".join(lines) + "\n")
    return p


def _fmt_cost(c) -> str:
    if isinstance(c, (list, tuple)):
        return f"{c[0]}" if c[0] == c[1] else f"{c[0]} to {c[1]}"
    return str(c)


def _clean_path(p: str) -> str:
    """A local path becomes "…/folder": no user name in a report that gets shared."""
    return re.sub(r"^file://(?:/[^/ ]+)*/([^/ ]+/[^/ ]+)$", r"…/\1", str(p))


def _clean(label: str) -> str:
    """No local paths in a report that gets shared."""
    return re.sub(r"(local|duckdb):[^ ]*/([^/ ]+)", r"\1:\2", str(label))


def write_report(res: dict, out: Path) -> Path:
    s = res["summary"]
    (out / "report.json").write_text(json.dumps(res, indent=2, default=str, ensure_ascii=False))
    ok = s["parity"] == "complete"
    rows = "".join(
        f"<tr><td>{_e(c['table'])}</td><td>{c['rows_source']}</td><td>{c['rows_target']}</td>"
        f"<td class='{'ok' if c['ok'] else ('warn' if c.get('precision_cols') and c['rows_source'] == c['rows_target'] and c['fingerprint_ok'] else 'ko')}'>{'identical' if c['ok'] else ('precision' if c.get('precision_cols') and c['rows_source'] == c['rows_target'] and c['fingerprint_ok'] else 'mismatch')}</td>"
        f"<td>{_e('; '.join(c['stats_diffs'][:3]))}{' buckets ' + _e(c['bad_buckets'][:5]) if c['bad_buckets'] else ''}</td></tr>"
        for c in res["checks"])
    qrows = "".join(
        f"<tr><td><code>{_e(q['sql'][:140])}</code></td><td class='{'ok' if q['status']=='match' else ('warn' if q['status']=='skipped' else 'ko')}'>{_e({'skipped': 'not replayed'}.get(q['status'], q['status']))}"
        f"{' (different order)' if q['order_differs'] else ''}</td><td>{_e(q['detail'])}</td></tr>" for q in res["queries"])
    crows = "".join(f"<tr><td>{_e(c['table'])}.{_e(c['column'])}</td><td>{_e(c['source_type'])} → {_e(c['target_type'])}</td>"
                    f"<td class='{'warn' if c['lossy'] else ''}'>{_e(c['note'])}{' (possible loss)' if c['lossy'] else ''}</td></tr>" for c in res["conversions"])
    nonp = res["scan"]["non_portable_columns"]
    objs = res["scan"]["objects_that_do_not_move"]
    objrows = "".join(f"<tr><td>{_e(k)}</td><td>{len(v)}</td><td>{_e(', '.join(v[:8]))}{'…' if len(v) > 8 else ''}</td></tr>" for k, v in objs.items() if v)
    rto = s.get("rto")
    sy = synthese(res)
    finds = "".join(f'<div class="find"><span class="tag {t}">{_e(tag)}</span><div><b>{_e(h)}</b><span>{_e(d)}</span></div></div>' for t, tag, h, d in sy["findings"])
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Canot evidence report</title><style>{CSS}{BRAND_CSS}</style>
{brand_bar("Evidence report")}<h1>Evidence report: exit exercise</h1>
<p>Source: <code>{_e(_clean(res['source']))}</code> · Target: <code>{_e(_clean(res['target']))}</code> · Finding dated {datetime.now().strftime('%Y-%m-%d %H:%M')}, on the scope run · Canot {_e(res['env']['canot'])}</p>
<h2 style="border:0;margin-bottom:0">Summary</h2><p class="verdict"><b>@@VERDICT@@</b></p>@@FINDINGS@@
{share_block(drill_share(res), "drill-share.json")}
<div class="box"><b>Finding:</b> <span class="{'ok' if ok else 'warn'}">{_e('identical content' if ok else 'mismatches to review')}</span> —
{s['tables_ok']}/{s['tables_total']} identical tables, {s['queries_match']}/{s['queries_total']} identical queries,
{s['queries_untranslatable']} not replayable, {s['queries_mismatch']} with a mismatch, {s['lossy_conversions']} conversion(s) with possible loss.</div>
<h2>Measurements from this exercise</h2><ul>
{('<li>Measured copy throughput: ' + str(s['throughput_mb_s']) + ' MB/s</li>') if s['throughput_mb_s'] else ''}
{('<li>Estimated recovery time (range): ' + _e(f"{rto['seconds'][0]} to {rto['seconds'][1]} s — {rto['basis']}") + '</li>') if rto else ''}
{('<li>Warehouse credits used (est.): ' + str(s['warehouse_credits_used_est']) + (' · cost: ' + _fmt_cost(s['cost_usd_est']) + ' USD (' + _e(s.get('cost_basis') or '') + ')' if s['cost_usd_est'] else '') + '</li>') if s['warehouse_credits_used_est'] is not None else ''}
<li>Total exercise duration: {s['duration_s']} s</li></ul>
<p class="small">These measurements describe this exercise, on this scope. They do not compare two platforms.</p>
<h2>Where the copy is</h2>
<p>Destination: <code>{_e(_clean(res['copy_location']['target']))}</code> · location: <code>{_e(_clean_path(res['copy_location']['warehouse']))}</code> · catalog: {_e(res['copy_location']['catalog'])}.<br>
The copy is in Apache Iceberg format: you can reread it without Canot. The <code>{REREAD_FILE}</code> file next to this report holds the DuckDB commands to do so.</p>
<h2>Tables</h2><table><tr><th>Table</th><th>Source rows</th><th>Target rows</th><th>Result</th><th>Details</th></tr>{rows}</table>
<h2>Queries replayed on both sides</h2><table><tr><th>Query</th><th>Result</th><th>Details</th></tr>{qrows or '<tr><td colspan=3>none</td></tr>'}</table>
<h2>Type conversions</h2><table>{crows or '<tr><td>none</td></tr>'}</table>
<h2>What does not move as is</h2>
<p>Columns with a platform-specific type: {len(nonp)}. Objects to rebuild (views, procedures, tasks…):</p>
<table><tr><th>Type</th><th>Count</th><th>Examples</th></tr>{objrows or '<tr><td colspan=3>none detected</td></tr>'}</table>
{regulatory_section("drill", tag="div")}
<h2>What these figures do not say</h2>
<ul><li>They cover the scope that was run, on the date of the finding. They say nothing about other tables or about tomorrow.</li>
<li>They do not compare two platforms: no durations, costs or performance.</li>
<li>They do not cover access rights, views, scheduled tasks or the applications connected to the platform.</li>
<li>This is not a certification and not a legal opinion on your contracts.</li></ul>
<h2>Tolerances applied</h2><p><code>{_e(json.dumps(res['tolerances'], ensure_ascii=False))}</code></p>
<h2>Report integrity</h2>
<p>Chained log: {res['seal']['entries']} entries, last hash <code>{_e(res['seal']['last_hash'])}</code>.
Any change to the log shows. Verification: <code>canot verify-evidence {_e(out.name)}</code></p>
<p>Fingerprint of the public signing key: <code>{_e(_key_fingerprint(out))}</code>.
The signature attests the integrity of the log. It establishes neither the identity of the author nor the date: for that, keep the report in your own timestamped archive.</p>
<p class="small"><i>This report is a record of measurements for your own assessment. It is not a certification and it does not say whether your contracts or your organization meet any regulation.</i></p>
</html>"""
    page = page.replace("@@VERDICT@@", _e(sy["verdict"])).replace("@@FINDINGS@@", finds)
    page = page.replace("</html>", '''<div class="cta">This report covers the scope you chose. To get it on your environment, with three exit-cost scenarios and the steps to follow: <a href="https://canot.io/#contact">canot.io</a>.</div></html>''')
    write_relire_sql(res, out)
    p = out / "report.html"
    p.write_text(page)
    return p
