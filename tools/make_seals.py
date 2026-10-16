"""Génère les macarons du site : un timbre dentelé, double filet, texte courbe.
Usage : python3 tools/make_seals.py  (affiche le HTML des quatre macarons)"""
import math

CX = CY = 100
SEALS = [("rgpd", "RGPD", 27), ("dora", "DORA", 27), ("dataact", "Data Act", 21), ("snc", "SecNumCloud", 15)]


def scalloped(n=44, outer=96, hole=3.7):
    pts = [(CX + outer * math.cos(2 * math.pi * k / n), CY + outer * math.sin(2 * math.pi * k / n)) for k in range(n)]
    d = f"M{pts[0][0]:.2f},{pts[0][1]:.2f}"
    for k in range(1, n + 1):
        x, y = pts[k % n]
        d += f" A{hole},{hole} 0 0 0 {x:.2f},{y:.2f}"  # morsure vers l'intérieur : la dentelure d'un timbre
    return d + "Z"


def seal(key, label, size):
    top = f"M{CX-66},{CY} A66,66 0 0 1 {CX+66},{CY}"
    bot = f"M{CX-76},{CY} A76,76 0 0 0 {CX+76},{CY}"
    return f'''<svg class="stamp reveal-stamp" viewBox="0 0 200 200" role="img" aria-label="{label} : élément de preuve, pas une certification" width="150" height="150">
  <defs><path id="t-{key}" d="{top}"/><path id="b-{key}" d="{bot}"/></defs>
  <g transform="rotate(-4 100 100)">
    <path d="{scalloped()}" fill="#fff" stroke="#1F3A5F" stroke-width="2.2" stroke-linejoin="round"/>
    <circle cx="100" cy="100" r="82" fill="none" stroke="#1F3A5F" stroke-width="1.6"/>
    <circle cx="100" cy="100" r="60" fill="none" stroke="#C9D4DE" stroke-width="1.2"/>
    <text font-size="11.5" font-weight="700" letter-spacing="2.4" fill="#1F3A5F"><textPath href="#t-{key}" startOffset="50%" text-anchor="middle">ÉLÉMENT DE PREUVE</textPath></text>
    <text font-size="11.5" font-weight="700" letter-spacing="2.4" fill="#1F3A5F"><textPath href="#b-{key}" startOffset="50%" text-anchor="middle">CANOT · DOSSIER SIGNÉ</textPath></text>
    <circle cx="19" cy="100" r="3.4" fill="#E8834F"/><circle cx="181" cy="100" r="3.4" fill="#E8834F"/>
    <text x="100" y="{100 + size * 0.35:.1f}" text-anchor="middle" font-size="{size}" font-weight="800" letter-spacing="-.5" fill="#1F3A5F">{label}</text>
  </g>
</svg>'''


def wave(height, amp, fill):
    """Une vague qui se répète : le SVG fait 4 périodes et défile de la moitié, donc sans couture."""
    P = 720
    y = height - amp * 2 - 4
    d = f"M0 {y}"
    for i in range(8):
        x = i * P // 2
        d += f" Q{x + P // 4} {y + (amp if i % 2 else -amp)} {x + P // 2} {y}"
    d += f" V{height} H0 Z"
    return f'<svg viewBox="0 0 {P * 4} {height}" preserveAspectRatio="none" focusable="false"><path d="{d}" fill="{fill}"/></svg>'


if __name__ == "__main__":
    for k, l, s in SEALS:
        print(seal(k, l, s))
