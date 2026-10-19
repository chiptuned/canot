"""Canot's visual identity for the HTML reports (inline, no external resource) and the share/export actions.

The share summary holds counts only: no table names, no SQL, no identities. The report offers it as a download and points to the
form on canot.io where it can be sent to Q4O; nothing is sent from the report itself."""
from __future__ import annotations

import base64
import html
import json

REVIEW_URL = "https://canot.io/en/review.html"

LOGO_SVG = '<svg viewBox="280 362 694 357" height="26" aria-hidden="true" focusable="false"><path d="M0 0 C5.95 3.28 10.89 7.94 15.88 12.5 C59.5 52.37 125.33 53.74 181.14 57.08 C181.66 57.11 181.66 57.11 184.28 57.27 C216.93 59.22 249.51 59.66 282.2 59.64 C282.71 59.64 282.71 59.64 285.26 59.64 C317.13 59.64 348.94 59.37 380.76 57.62 C383.68 57.46 386.59 57.3 389.5 57.15 C481.48 52.22 481.48 52.22 523.64 37.46 C524.31 37.23 524.98 36.99 525.67 36.76 C544.99 29.92 561.05 18.53 576.64 5.46 C584.31 -0.82 589.44 -3.29 599.58 -2.92 C604.74 -2.29 607.58 -0.83 611.13 3.12 C615.5 9.12 616.06 15.9 615.39 23.16 C614.94 25.95 614.38 28.7 613.77 31.46 C613.54 32.48 613.32 33.51 613.08 34.57 C605.54 67.38 591.85 98.89 569.64 124.46 C569.23 124.94 569.23 124.94 567.13 127.37 C542.14 155.8 507.2 171.67 469.64 174.46 C464.16 174.71 458.67 174.85 453.18 174.98 C452.37 175 451.56 175.03 450.72 175.05 C428.63 175.6 406.54 175.8 384.45 175.99 C380.95 176.02 377.46 176.05 373.96 176.08 C314.93 176.61 255.93 176.57 196.9 176.05 C193.38 176.02 189.87 175.99 186.35 175.96 C144.21 175.6 144.21 175.6 124.68 175 C124.08 174.98 124.08 174.98 121.07 174.89 C90.4 173.82 60.76 164.99 36.64 145.46 C36.05 144.99 35.46 144.51 34.86 144.03 C-1.35 114.74 -24.93 67.53 -30.36 21.46 C-30.67 14.34 -29.41 8.88 -25.11 3.14 C-17.62 -4.18 -9.04 -3.93 0 0 Z " fill="#FF6B16" transform="translate(334.359375,518.54296875)" />\n<path d="M0 0 C0.86 -0.01 1.72 -0.01 2.6 -0.02 C3.53 -0.02 4.47 -0.03 5.43 -0.03 C6.42 -0.04 7.4 -0.04 8.42 -0.05 C11.7 -0.07 14.97 -0.08 18.25 -0.09 C19.37 -0.1 20.49 -0.1 21.64 -0.1 C27.57 -0.12 33.5 -0.14 39.43 -0.15 C45.55 -0.16 51.66 -0.19 57.77 -0.23 C62.48 -0.26 67.2 -0.27 71.91 -0.27 C74.16 -0.28 76.41 -0.29 78.67 -0.31 C81.83 -0.33 84.99 -0.33 88.15 -0.32 C89.07 -0.34 89.99 -0.35 90.94 -0.36 C100.17 -0.29 108.11 2.58 114.68 9.15 C121.5 17.53 122.12 27.26 122.08 37.66 C122.09 38.87 122.09 40.08 122.1 41.33 C122.12 44.62 122.12 47.91 122.11 51.2 C122.11 53.96 122.11 56.72 122.12 59.47 C122.13 65.97 122.13 72.48 122.12 78.98 C122.11 85.67 122.12 92.37 122.15 99.06 C122.17 104.83 122.18 110.59 122.17 116.36 C122.17 119.8 122.17 123.23 122.19 126.67 C122.21 130.5 122.19 134.32 122.18 138.15 C122.18 138.72 122.18 138.72 122.21 141.58 C122.15 147.72 121.89 151.54 117.68 156.15 C84.98 174.69 35.39 174.56 -1.32 177.15 C-1.97 177.2 -1.97 177.2 -5.22 177.44 C-6.49 177.53 -7.75 177.63 -9.06 177.72 C-10.17 177.8 -11.28 177.89 -12.43 177.97 C-15.82 178.12 -19.17 178.16 -22.57 178.16 C-23.11 178.16 -23.11 178.16 -25.84 178.15 C-26.66 178.15 -27.48 178.15 -28.32 178.15 C-28.42 158.39 -28.49 138.63 -28.53 118.87 C-28.55 109.7 -28.58 100.52 -28.63 91.35 C-28.67 83.35 -28.69 75.34 -28.7 67.34 C-28.71 63.11 -28.72 58.88 -28.75 54.65 C-28.77 50.65 -28.78 46.66 -28.78 42.67 C-28.78 41.21 -28.79 39.75 -28.8 38.3 C-28.92 26.66 -27.62 16.58 -19.32 7.87 C-13.66 2.99 -7.5 0.04 0 0 Z " fill="#062E60" transform="translate(740.3240051269531,385.8528289794922)" />\n<path d="M0 0 C0.87 -0.01 1.74 -0.01 2.64 -0.02 C5.48 -0.03 8.33 -0.03 11.18 -0.03 C13.18 -0.03 15.18 -0.03 17.17 -0.04 C21.35 -0.05 25.53 -0.05 29.71 -0.04 C35.04 -0.04 40.37 -0.05 45.7 -0.08 C49.83 -0.09 53.95 -0.09 58.08 -0.09 C60.04 -0.09 62.01 -0.09 63.97 -0.1 C92.09 -0.23 92.09 -0.23 101.62 9.03 C108.67 16.5 110.27 23.55 110.16 33.55 C110.16 34.54 110.16 35.54 110.16 36.56 C110.16 39.83 110.13 43.1 110.11 46.37 C110.1 48.64 110.1 50.92 110.1 53.19 C110.08 59.17 110.05 65.14 110.02 71.11 C109.99 77.21 109.98 83.31 109.96 89.41 C109.93 101.36 109.88 113.32 109.82 125.27 C101.98 125.87 94.16 126.42 86.31 126.59 C85.47 126.61 84.64 126.63 83.77 126.65 C81.08 126.72 78.38 126.78 75.69 126.84 C74.75 126.86 73.81 126.88 72.84 126.9 C46.3 127.48 19.8 127.41 -6.75 126.84 C-7.69 126.82 -8.64 126.8 -9.61 126.78 C-12.33 126.72 -15.04 126.66 -17.75 126.59 C-18.54 126.57 -19.33 126.56 -20.15 126.54 C-26.19 126.38 -32.17 125.88 -38.18 125.27 C-38.28 112.56 -38.35 99.84 -38.39 87.12 C-38.41 81.21 -38.44 75.31 -38.49 69.4 C-38.53 63.7 -38.55 57.99 -38.56 52.28 C-38.57 50.11 -38.59 47.94 -38.61 45.77 C-38.86 19.06 -38.86 19.06 -28.32 8.15 C-20.38 1.18 -10.22 -0.03 0 0 Z " fill="#062E60" transform="translate(586.184814453125,439.725341796875)" />\n<path d="M0 0 C1.74 0 3.47 -0 5.21 -0 C8.83 -0 12.45 0.01 16.07 0.03 C20.69 0.05 25.31 0.05 29.94 0.04 C33.52 0.04 37.09 0.05 40.67 0.06 C42.38 0.06 44.08 0.06 45.79 0.06 C48.17 0.06 50.56 0.07 52.94 0.09 C53.29 0.09 53.29 0.09 55.04 0.08 C63.94 0.18 72.63 2.61 79.42 8.62 C86.65 16.96 87.77 26.04 87.51 36.66 C87.5 37.97 87.49 39.29 87.49 40.64 C87.46 44.1 87.4 47.55 87.34 51 C87.28 54.54 87.25 58.08 87.22 61.62 C87.15 68.54 87.05 75.45 86.93 82.36 C75.53 82.54 64.29 82.06 52.93 81.11 C52.11 81.05 51.29 80.98 50.45 80.91 C-32.26 74.13 -32.26 74.13 -43.7 62.75 C-45.57 59.5 -45.5 56.54 -45.47 52.89 C-45.48 52.16 -45.48 51.44 -45.48 50.69 C-45.48 49.15 -45.48 47.62 -45.47 46.08 C-45.45 43.77 -45.47 41.45 -45.48 39.13 C-45.5 27.3 -44.51 17.3 -36.07 8.36 C-26.37 -0.98 -12.55 -0.07 0 0 Z " fill="#062E60" transform="translate(445.07421875,481.63671875)" />\n</svg>'

CSS = """.brandbar{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:0 0 12px;margin:0 0 18px;border-bottom:1px solid rgba(128,128,128,.35)}
.brandbar .brand{display:flex;align-items:center;gap:10px;font:700 20px system-ui,sans-serif;letter-spacing:-.01em}
.brandbar .brand svg{background:#fff;border-radius:6px;padding:3px 5px;height:26px;width:auto;box-sizing:content-box}
.brandbar .kind{font:600 13px system-ui,sans-serif;opacity:.7}
.share{margin:14px 0 18px;padding:12px 14px;border:1px solid rgba(128,128,128,.35);border-radius:8px}
.share .acts{display:flex;flex-wrap:wrap;gap:8px}
.share a{display:inline-block;padding:8px 14px;border:1px solid currentColor;border-radius:6px;text-decoration:none;font:600 14px system-ui,sans-serif;color:inherit}
.share a.pri{background:#e8622a;border-color:#e8622a;color:#fff}
.share p{margin:8px 0 0;font:13px/1.45 system-ui,sans-serif;opacity:.8}
@media print{.share{display:none}}
"""


def brand_bar(kind: str) -> str:
    return f'<div class="brandbar"><span class="brand">{LOGO_SVG}<span>canot</span></span><span class="kind">{html.escape(kind)}</span></div>'


def _data_uri(payload: dict) -> str:
    raw = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "data:application/json;base64," + base64.b64encode(raw).decode()


def share_block(payload: dict, filename: str) -> str:
    """Primary action: get an expert review (a page on canot.io that explains who reads it and what happens). Secondary: download the
    shareable summary, embedded in the page. No script, nothing is sent from this page."""
    return (f'<div class="share"><div class="acts"><a class="pri" href="{REVIEW_URL}">Get an expert review of this report</a>'
            f'<a href="{_data_uri(payload)}" download="{html.escape(filename)}">Export the shareable summary</a></div>'
            f"<p>The review is optional and free of obligation: you send a summary ({html.escape(filename)}: counts only, no table names, no SQL, "
            "no identities) and the team behind Canot tells you what the findings mean. Nothing is sent from this page.</p></div>")


def drill_share(res: dict) -> dict:
    """Shareable summary of an exit exercise (drill): counts and versions only."""
    s = res["summary"]
    keep = ("tables_total", "tables_ok", "tables_precision_only", "queries_total", "queries_match", "queries_mismatch",
            "queries_untranslatable", "queries_skipped", "queries_order_differs", "lossy_conversions", "parity")
    return {"schema": "canot.share/1", "kind": "drill", "summary": {k: s.get(k) for k in keep}, "tables_in_error": len(s.get("tables_error", [])),
            "versions": res.get("env")}
