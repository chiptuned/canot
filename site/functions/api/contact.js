// Cloudflare Pages Function : POST /api/contact
// Enregistre la demande dans D1 (binding LEADS, juridiction UE) et, si CONTACT_WEBHOOK_URL est défini
// (par exemple un sujet ntfy), envoie une notification. Répond 503 si rien n'a pu être enregistré.
const clean = (v, n = 200) => String(v ?? "").replace(/[\r\n]+/g, " ").trim().slice(0, n);
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
  if (body.website) return new Response(null, { status: 204 }); // piège à robots
  const f = {
    name: clean(body.name), company: clean(body.company), email: clean(body.email), platform: clean(body.platform),
    renewal: clean(body.renewal), message: clean(body.message, 500),
  };
  if (!f.name || !f.company || !EMAIL.test(f.email) || !f.platform || !/^\d{4}-\d{2}$/.test(f.renewal)) {
    return new Response("invalid", { status: 422 });
  }
  let stored = false, notified = false;
  if (env.LEADS) {
    try {
      await env.LEADS.prepare(
        "INSERT INTO leads (created_at, name, company, email, platform, renewal, message) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)"
      ).bind(new Date().toISOString(), f.name, f.company, f.email, f.platform, f.renewal, f.message).run();
      stored = true;
    } catch (e) { console.error("d1", e && e.message); }
  }
  if (env.CONTACT_WEBHOOK_URL) {
    try {
      const text = `${f.name} (${f.company})\n${f.email}\nPlateforme : ${f.platform}\nRenouvellement : ${f.renewal}${f.message ? "\n" + f.message : ""}`;
      const r = await fetch(env.CONTACT_WEBHOOK_URL, { method: "POST", headers: { "Content-Type": "text/plain; charset=utf-8", Title: "Canot : nouvelle demande" }, body: text });
      notified = r.ok;
    } catch (e) { console.error("webhook", e && e.message); }
  }
  return new Response(null, { status: stored || notified ? 204 : 503 });
}
