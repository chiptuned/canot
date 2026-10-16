// Cloudflare Pages Function : POST /api/contact
// Ne stocke rien lui-même. Transmet la demande au webhook défini dans CONTACT_WEBHOOK_URL
// (par exemple un sujet ntfy). Sans webhook, répond 503 : le site affiche alors l'adresse e-mail.
const MAX = 200;
const clean = (v) => String(v ?? "").replace(/[\r\n]+/g, " ").trim().slice(0, MAX);

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
  if (body.website) return new Response(null, { status: 204 }); // piège à robots
  const f = { name: clean(body.name), company: clean(body.company), email: clean(body.email), platform: clean(body.platform), renewal: clean(body.renewal) };
  if (!f.name || !f.company || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email) || !f.platform || !/^\d{4}-\d{2}$/.test(f.renewal)) {
    return new Response("invalid", { status: 422 });
  }
  if (!env.CONTACT_WEBHOOK_URL) return new Response("not configured", { status: 503 });
  const text = `Demande Canot\n${f.name} (${f.company})\n${f.email}\nPlateforme : ${f.platform}\nRenouvellement : ${f.renewal}`;
  const r = await fetch(env.CONTACT_WEBHOOK_URL, { method: "POST", headers: { "Content-Type": "text/plain; charset=utf-8", Title: "Canot : nouvelle demande" }, body: text });
  return new Response(null, { status: r.ok ? 204 : 502 });
}
