// Rend des SVG/HTML en PNG avec Playwright (Chromium). Usage : node tools/render_png.mjs <source> <sortie.png> <largeur> <hauteur> [fond] [attente_ms] [clip_hauteur]
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_PATH || "playwright"); // ex. PLAYWRIGHT_PATH=/chemin/vers/node_modules/playwright
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
const [src, out, w, h, bg = "transparent", wait = "300", clipH] = process.argv.slice(2);
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 2 });
await p.goto(pathToFileURL(resolve(src)).href);
if (bg !== "transparent") await p.evaluate((c) => { document.documentElement.style.background = c; if (document.body) { document.body.style.background = c; document.body.style.margin = "0"; } }, bg);
await p.waitForTimeout(+wait);
await p.screenshot({ path: out, omitBackground: bg === "transparent", clip: { x: 0, y: 0, width: +w, height: clipH ? +clipH : +h } });
await b.close();
