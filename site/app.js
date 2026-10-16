(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Mot qui change dans l'accroche (fixe si l'utilisateur préfère moins de mouvement)
  var rot = document.getElementById("rot");
  if (rot && !reduce) {
    var words = ["Snowflake", "BigQuery", "Databricks"], i = 0;
    setInterval(function () { i = (i + 1) % words.length; rot.textContent = words[i]; }, 2200);
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

  // Formulaire
  var form = document.getElementById("form");
  if (!form) return;
  var thanks = document.getElementById("thanks"), msg = document.getElementById("thanks-msg"),
      fail = document.getElementById("fail"), send = document.getElementById("send"), again = document.getElementById("again");
  var checks = {
    name: function (v) { return v.trim() ? "" : "Champ requis."; },
    company: function (v) { return v.trim() ? "" : "Champ requis."; },
    email: function (v) { return v ? (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) ? "" : "Adresse invalide.") : "Champ requis."; },
    platform: function (v) { return v ? "" : "Champ requis."; },
    renewal: function (v) { return v ? "" : "Champ requis."; }
  };
  function validate() {
    var ok = true;
    Object.keys(checks).forEach(function (k) {
      var el = form.elements[k], e = checks[k](el.value), out = form.querySelector('[data-for="' + k + '"]');
      out.textContent = e; el.setAttribute("aria-invalid", e ? "true" : "false");
      if (e) ok = false;
    });
    return ok;
  }
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    fail.hidden = true;
    if (!validate()) return;
    var data = {}; Object.keys(checks).forEach(function (k) { data[k] = form.elements[k].value.trim(); });
    data.website = form.elements.website.value;
    send.disabled = true;
    fetch("/api/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r; })
      .then(function () {
        var parts = data.renewal.split("-"), when = new Date(+parts[0], +parts[1] - 1, 1).toLocaleDateString("fr-FR", { month: "long", year: "numeric" });
        msg.textContent = "Merci " + data.name.split(" ")[0] + ". Nous revenons vers vous avec un créneau, avant votre renouvellement de " + when + ".";
        form.hidden = true; thanks.hidden = false;
      })
      .catch(function () {
        fail.textContent = "L'envoi n'a pas abouti. Rien n'a été enregistré. Écrivez-nous directement à vincent@q4o.com.";
        fail.hidden = false;
      })
      .then(function () { send.disabled = false; });
  });
  again.addEventListener("click", function () { form.reset(); thanks.hidden = true; form.hidden = false; });
})();
