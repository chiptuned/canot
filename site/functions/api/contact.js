// Cloudflare Pages Function: POST /api/contact
// Stores the request in D1 (binding LEADS, EU jurisdiction) and, if CONTACT_WEBHOOK_URL is set
// (for example an ntfy topic), sends a notification. Answers 503 if nothing could be recorded.
// Body field `lang` ("fr" by default, or "en") picks the language of the responses and of the notification.
const clean = (v, n = 200) => String(v ?? "").replace(/[\r\n]+/g, " ").trim().slice(0, n);
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SHARE_SCHEMAS = ["canot.share/1", "canot.partage/1"]; // scan-share.json (current) and scan-partage.json (former)
const T = {
  fr: {
    bad: "requête illisible", invalid: "demande invalide", renewal: "renouvellement", qualified: "Demande qualifiée",
    withReport: " avec rapport Canot", number: "Demande n° ", titleQualified: "Canot : demande qualifiée", titleNew: "Canot : nouvelle demande",
  },
  en: {
    bad: "bad request", invalid: "invalid request", renewal: "renewal", qualified: "Qualified request",
    withReport: " with Canot report", number: "Request #", titleQualified: "Canot: qualified request", titleNew: "Canot: new request",
  },
};
const text = (body, status) => new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch { return text(T.fr.bad, 400); }
  if (!body || typeof body !== "object") return text(T.fr.bad, 400);
  const t = body.lang === "en" ? T.en : T.fr;
  if (body.website) return new Response(null, { status: 204 }); // bot trap
  const f = {
    name: clean(body.name), company: clean(body.company), email: clean(body.email), platform: clean(body.platform),
    renewal: clean(body.renewal), message: clean(body.message, 500),
  };
  // Qualified request (offers page): a project description and the summary of a Canot scan
  // (scan-share.json: counts only, no table name, no SQL). `share` is the current field, `partage` the former one.
  const offre = body.kind === "offer" || body.kind === "offre";
  let share = null;
  if (offre) {
    f.message = String(body.message ?? "").replace(/\r/g, "").trim().slice(0, 2000);
    const given = body.share ?? body.partage;
    if (given) {
      const raw = typeof given === "string" ? given : JSON.stringify(given);
      try {
        const p = JSON.parse(raw);
        if (raw.length > 30000 || !p || !SHARE_SCHEMAS.includes(p.schema)) throw new Error("schema");
        share = raw;
      } catch { return text(t.invalid, 422); }
    }
    if (!f.message) return text(t.invalid, 422);
  }
  const renewalOk = offre ? (f.renewal === "" || /^\d{4}-\d{2}$/.test(f.renewal)) : /^\d{4}-\d{2}$/.test(f.renewal);
  if (!f.name || !f.company || !EMAIL.test(f.email) || !f.platform || !renewalOk) {
    return text(t.invalid, 422);
  }
  let stored = false, notified = false, leadId = null;
  if (env.LEADS) {
    try {
      // The `partage` column keeps its name (existing database); it holds the scan-share.json content.
      const res = await env.LEADS.prepare(
        "INSERT INTO leads (created_at, name, company, email, platform, renewal, message, source, partage) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)"
      ).bind(new Date().toISOString(), f.name, f.company, f.email, f.platform, f.renewal, f.message, offre ? "offre" : "contact", share).run();
      stored = true;
      leadId = res && res.meta ? res.meta.last_row_id : null;
    } catch (e) { console.error("d1", e && e.message); }
  }
  if (env.CONTACT_WEBHOOK_URL) {
    try {
      // The notification holds neither the e-mail address nor the message: the details stay in D1 (see tools/leads.sh).
      const first = f.name.split(" ")[0];
      const note = `${first}, ${f.company}\n${f.platform}${f.renewal ? `, ${t.renewal} ${f.renewal}` : ""}${offre ? `\n${t.qualified}${share ? t.withReport : ""}` : ""}${leadId ? `\n${t.number}${leadId}` : ""}`;
      const r = await fetch(env.CONTACT_WEBHOOK_URL, { method: "POST", headers: { "Content-Type": "text/plain; charset=utf-8", Title: offre ? t.titleQualified : t.titleNew, Priority: "high", Tags: "ship" }, body: note });
      notified = r.ok;
    } catch (e) { console.error("webhook", e && e.message); }
  }
  return new Response(null, { status: stored || notified ? 204 : 503 });
}
