(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Mot qui change dans l'accroche : le sortant monte, l'entrant arrive du bas (fixe si l'utilisateur préfère moins de mouvement)
  var rot = document.getElementById("rot");
  if (rot && !reduce) {
    ["BigQuery", "Databricks"].forEach(function (name, k) {
      var s = document.createElement("span");
      s.className = "w d" + (k + 1); s.textContent = name; s.setAttribute("aria-hidden", "true");
      rot.appendChild(s);
    });
    var ws = rot.querySelectorAll(".w"), cur = 0;
    setInterval(function () {
      cur = (cur + 1) % ws.length;
      ws.forEach(function (w, i) {
        var d = (i - cur + ws.length) % ws.length; // 0 : affiché, 1 : en attente (en bas), 2 : sorti (en haut)
        w.className = "w d" + d;
        w.setAttribute("aria-hidden", d === 0 ? "false" : "true");
      });
    }, 2200);
  }

  // Copier la commande
  var btn = document.getElementById("copy"), label = document.getElementById("copy-label"), cmd = document.getElementById("cmd");
  if (btn && cmd) {
    btn.addEventListener("click", function () {
      var text = cmd.textContent;
      var done = function () { label.textContent = "copié"; setTimeout(function () { label.textContent = "copier"; }, 1800); };
      var fallback = function () {
        var r = document.createRange(); r.selectNodeContents(cmd);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
      else fallback();
    });
  }

  // Apparitions au défilement (le texte reste lisible sans ce script ; rien ne bouge si l'on réduit les animations)
  var targets = document.querySelectorAll(".seal-grid, .card");
  if (reduce || !("IntersectionObserver" in window)) {
    targets.forEach(function (t) { t.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.35 });
    targets.forEach(function (t) { io.observe(t); });
  }

  // Formulaires (contact et demande qualifiée) : même envoi vers /api/contact
  var EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function setup(form) {
    var offre = form.getAttribute("data-lead") === "offre";
    var thanks = document.getElementById(offre ? "thanks-offre" : "thanks"), msg = document.getElementById("thanks-msg"),
        fail = document.getElementById(offre ? "fail-offre" : "fail"), send = form.querySelector("button.send"),
        again = document.getElementById("again"), preview = document.getElementById("preview"), body = document.getElementById("preview-body");
    var checks = {
      name: function (v) { return v.trim() ? "" : "Champ requis."; },
      company: function (v) { return v.trim() ? "" : "Champ requis."; },
      email: function (v) { return v ? (EMAIL.test(v) ? "" : "Adresse invalide.") : "Champ requis."; },
      platform: function (v) { return v ? "" : "Champ requis."; }
    };
    if (offre) checks.message = function (v) { return v.trim() ? "" : "Dites-nous en quelques mots votre projet."; };
    else checks.renewal = function (v) { return v ? "" : "Champ requis."; };
    var partage = null;
    if (offre) form.elements.partage.addEventListener("change", function () {
      var f = this.files[0], out = form.querySelector('[data-for="partage"]');
      partage = null; out.textContent = ""; preview.hidden = true;
      if (!f) return;
      if (f.size > 30000) { out.textContent = "Fichier trop gros : joignez scan-partage.json, pas scan.json."; return; }
      var r = new FileReader();
      r.onload = function () {
        try {
          var o = JSON.parse(r.result);
          if (o.schema !== "canot.partage/1") throw new Error("schema");
          partage = r.result; body.textContent = JSON.stringify(o, null, 2); preview.hidden = false;
        } catch (e) { out.textContent = "Ce fichier n'est pas un scan-partage.json de Canot."; }
      };
      r.readAsText(f);
    });
    function validate() {
      var ok = true;
      Object.keys(checks).forEach(function (k) {
        var el = form.elements[k], e = checks[k](el.value), out = form.querySelector('[data-for="' + k + '"]');
        out.textContent = e; el.setAttribute("aria-invalid", e ? "true" : "false");
        if (e) ok = false;
      });
      if (offre && form.querySelector('[data-for="partage"]').textContent) ok = false;
      return ok;
    }
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      fail.hidden = true;
      if (!validate()) return;
      var data = {};
      ["name", "company", "email", "platform", "renewal", "message"].forEach(function (k) { data[k] = (form.elements[k].value || "").trim(); });
      data.website = form.elements.website.value;
      if (offre) { data.kind = "offre"; if (partage) data.partage = partage; }
      send.disabled = true;
      fetch("/api/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
        .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r; })
        .then(function () {
          if (!offre) {
            var parts = data.renewal.split("-"), when = new Date(+parts[0], +parts[1] - 1, 1).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
            msg.textContent = "Merci " + data.name.split(" ")[0] + ". Nous revenons vers vous avec un créneau, avant votre renouvellement de " + when + ".";
          }
          form.hidden = true; thanks.hidden = false;
        })
        .catch(function () {
          fail.textContent = "L'envoi n'a pas abouti. Rien n'a été enregistré. Écrivez-nous directement à vincent@q4o.com.";
          fail.hidden = false;
        })
        .then(function () { send.disabled = false; });
    });
    if (again) again.addEventListener("click", function () { form.reset(); thanks.hidden = true; form.hidden = false; });
  }
  document.querySelectorAll("form[data-lead], form#form").forEach(setup);
})();
