"""Génère les visuels du README : bannières claire/sombre animées et démo de terminal animée.
Usage : python3 tools/make_readme_assets.py"""
import pathlib, re, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets"
LOGO = (ROOT / "site" / "assets" / "canot-logo.svg").read_text()
NAVY, ORANGE = "#062E60", "#FF6B16"


def wave_path(h, amp, period=720, periods=4, extra=140):
    y = h - amp * 2 - 4
    d = f"M0 {y}"
    for i in range(periods * 2):
        x = i * period // 2
        d += f" Q{x + period // 4} {y + (amp if i % 2 else -amp)} {x + period // 2} {y}"
    return d + f" V{h + extra} H0 Z"


def logo_part(viewbox, navy):
    body = re.sub(r'<svg [^>]*>', '', LOGO, count=1).replace('</svg>', '').replace('<title>Canot — Fredoka SemiBold 600</title>', '')
    return f'<svg viewBox="{viewbox}" preserveAspectRatio="xMidYMid meet" overflow="hidden" xmlns="http://www.w3.org/2000/svg">{body.replace(NAVY, navy)}</svg>'


def banner(dark: bool) -> str:
    ink, sub, w1, w2, blocks = ("#EAF1F8", "#9FB3C8", "#14304B", "#1B4468", "#7FB0FF") if dark else ("#062E60", "#4B6070", "#E6ECF2", "#D3DEE8", NAVY)
    boat = logo_part("239 361 776 362", blocks).replace('<svg ', '<svg x="0" y="0" width="360" height="167" ', 1)
    word = logo_part("255 722 745 205", ink).replace('<svg ', '<svg x="0" y="0" width="372" height="102" ', 1)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 300" width="1280" height="300" role="img" aria-label="Canot : prouvez que vous pouvez partir">
<style>
@keyframes houle{{0%,100%{{transform:translateY(0) rotate(-.6deg)}}50%{{transform:translateY(-4px) rotate(.4deg)}}}}
@keyframes vague{{from{{transform:translateX(0)}}to{{transform:translateX(-1440px)}}}}
@keyframes entre{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
.boat{{transform-box:fill-box;transform-origin:50% 85%;animation:houle 17s ease-in-out infinite}}
.w1{{animation:vague 40s linear infinite}}.w2{{animation:vague 26s linear infinite reverse}}
.t1{{animation:entre .8s .2s both}}
text{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}}
@media (prefers-reduced-motion:reduce){{.boat,.w1,.w2,.t1{{animation:none}}}}
</style>
<g transform="translate(0 110)"><g class="w1"><path d="{wave_path(150, 22)}" fill="{w1}"/></g></g>
<g transform="translate(249 80)"><g class="boat">{boat}</g></g>
<g transform="translate(0 146)"><g class="w2"><path d="{wave_path(110, 12)}" fill="{w2}"/></g></g>
<g transform="translate(659 88)"><g class="t1">{word}</g></g>
</svg>
'''


TERM = [
    ("cmd", '$ canot drill "snowflake://MY_ACCOUNT/SNOWFLAKE_SAMPLE_DATA?schemas=TPCH_SF1&user=MY_USER" ./iceberg'),
    ("dim", "→ inventaire"),
    ("dim", "→ copie de 8 table(s) vers local:./iceberg"),
    ("txt", "   lineitem   6 001 215 lignes"),
    ("txt", "   orders     1 500 000 lignes"),
    ("txt", "   partsupp     800 000 lignes"),
    ("dim", "→ comparaison des tables"),
    ("ok", "   lineitem   OK    orders     OK    partsupp   OK    part     OK"),
    ("ok", "   customer   OK    supplier   OK    nation     OK    region   OK"),
    ("dim", "→ rejeu de 4 requêtes des deux côtés"),
    ("warn", "   [mismatch]  select l_returnflag, l_linestatus, sum(l_quantity) …"),
    ("ok", "   [match]     select n_name, sum(l_extendedprice * (1 - l_discount)) …"),
    ("ok", "   [match]     select sum(l_extendedprice * l_discount) as revenue …"),
    ("ok", "   [match]     select o_orderpriority, count(*) as order_count …"),
    ("bold", "Parité : écarts — 8/8 tables, 3/4 requêtes"),
    ("dim", "Dossier : canot-out/report.html   (journal signé, vérifiable)"),
]


def terminal() -> str:
    W, H, LH, X0, Y0 = 940, 56 + len(TERM) * 25 + 30, 25, 28, 86
    colors = {"cmd": "#E6EDF3", "dim": "#7D8FA3", "txt": "#B9C6D3", "ok": "#56D364", "warn": "#FF9A55", "bold": "#FFFFFF"}
    cycle = 22.0
    css, rows = [], []
    for i, (k, t) in enumerate(TERM):
        start = 0.6 + i * 0.95 if i else 0.4
        s, e = start / cycle * 100, 93.0
        css.append(f"@keyframes l{i}{{0%,{s:.2f}%{{opacity:0}}{s + .6:.2f}%,{e}%{{opacity:1}}100%{{opacity:0}}}}.l{i}{{animation:l{i} {cycle}s infinite}}")
        weight = ' font-weight="700"' if k in ("bold", "cmd") else ""
        txt = html.escape(t).replace("[match]", '<tspan fill="#56D364">[match]</tspan>').replace("[mismatch]", '<tspan fill="#FF9A55">[mismatch]</tspan>')
        rows.append(f'<text class="l{i}" x="{X0}" y="{Y0 + i * LH}" fill="{colors[k]}"{weight} xml:space="preserve">{txt}</text>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Démonstration : canot drill copie 8 tables Snowflake vers Iceberg, compare tout et signale une requête qui diverge">
<style>
text{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;font-size:15px}}
{''.join(css)}
@media (prefers-reduced-motion:reduce){{{''.join(f'.l{i}' + ('' if i == len(TERM) - 1 else ',') for i in range(len(TERM)))}{{animation:none;opacity:1}}}}
</style>
<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="12" fill="#0D1B2A" stroke="#223A4B" stroke-width="2"/>
<path d="M1 13a12 12 0 0 1 12-12h{W - 26}a12 12 0 0 1 12 12v32H1z" fill="#13273B"/>
<circle cx="26" cy="26" r="6.5" fill="#FF5F57"/><circle cx="48" cy="26" r="6.5" fill="#FEBC2E"/><circle cx="70" cy="26" r="6.5" fill="#28C840"/>
<text x="{W / 2}" y="31" text-anchor="middle" fill="#7D8FA3" style="font-size:13px">canot : exercice de sortie</text>
{''.join(rows)}
</svg>
'''


if __name__ == "__main__":
    (OUT / "banner-light.svg").write_text(banner(False))
    (OUT / "banner-dark.svg").write_text(banner(True))
    (OUT / "demo.svg").write_text(terminal())
    print("écrit :", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))
