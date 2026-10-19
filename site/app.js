(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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

/* Sélecteurs de plateforme source et de destination (tools/selecteur/, étendu à la destination) */
(function () {
  'use strict';

  // Ordre = ordre du tambour. Commande la plus longue en dernier : les voisines affichées dessous ne recouvrent pas la 2e ligne.
  var SOURCES = [
    { id: 'snowflake', label: 'Snowflake', rest: '://MY_ACCOUNT/MY_DATABASE?user=MY_USER' },
    { id: 'bigquery', label: 'BigQuery', rest: '://MY_PROJECT/MY_DATASET?location=EU' },
    { id: 'databricks', label: 'Databricks', rest: '://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG' }
  ];
  var BUCKET = ' --bucket MY_BUCKET';
  var DESTS = [
    { id: 'local', label: 'Dossier local (DuckDB)', word: './iceberg', rest: '' },
    { id: 'scaleway', label: 'Scaleway', word: 'scaleway:fr-par', rest: BUCKET },
    { id: 'ovh', label: 'OVHcloud', word: 'ovh:gra', rest: BUCKET },
    { id: 'outscale', label: 'Outscale', word: 'outscale:eu-west-2', rest: BUCKET },
    { id: 'ionos', label: 'IONOS', word: 'ionos:eu-central-1', rest: BUCKET },
    { id: 'stackit', label: 'STACKIT', word: 'stackit:eu01', rest: BUCKET },
    { id: 'onprem', label: 'S3 sur site', word: '"s3://MY_BUCKET/canot?endpoint=https://s3.internal:9000"', rest: '' }
  ];
  // Comment lancer Canot : uv (uvx), pipx ou pip. Le connecteur de la plateforme choisie est demandé tel quel (canot[snowflake]).
  var RUNNERS = [
    { id: 'uvx', label: 'uvx', word: 'uvx', rest: function (x) { return ' --from "canot[' + x + ']" canot'; } },
    { id: 'pipx', label: 'pipx', word: 'pipx run', rest: function (x) { return ' --spec "canot[' + x + ']" canot'; } },
    { id: 'pip', label: 'pip', word: 'pip install', rest: function (x) { return ' "canot[' + x + ']" && canot'; } }
  ];
  var INTRO_DELAY = 1400, INTRO_STEP = 650;

  function init(root) {
    var st = { src: 0, dst: 0, run: 0 };
    var touched = false;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var qs = new URLSearchParams(location.search);
    var u = SOURCES.findIndex(function (p) { return p.id === qs.get('source'); });
    var d = DESTS.findIndex(function (p) { return p.id === qs.get('sortie'); });
    if (u >= 0) { st.src = u; touched = true; }
    if (d >= 0) { st.dst = d; touched = true; }

    var restEls = root.querySelectorAll('[data-cs-rest]');
    var destRestEls = root.querySelectorAll('[data-cs-dest-rest]');
    var runRestEls = root.querySelectorAll('[data-cs-run-rest]');
    var qr = RUNNERS.findIndex(function (p) { return p.id === qs.get('outil'); });
    if (qr >= 0) { st.run = qr; touched = true; }
    var pickEls = root.querySelectorAll('[data-cs-pick]');
    var pickOutEls = root.querySelectorAll('[data-cs-pick-out]');
    var copyEls = root.querySelectorAll('[data-cs-copy]');
    var drums = Array.prototype.map.call(root.querySelectorAll('[data-cs-drum]'), buildDrum);

    function command() {
      var a = SOURCES[st.src], b = DESTS[st.dst], r = RUNNERS[st.run];
      return r.word + r.rest(a.id) + ' drill "' + a.id + a.rest + '" ' + b.word + b.rest;
    }
    function set(kind, i, byUser) {
      var list = kind === 'dst' ? DESTS : kind === 'run' ? RUNNERS : SOURCES;
      i = Math.max(0, Math.min(list.length - 1, i));
      if (byUser) touched = true;
      if (i === st[kind]) return;
      st[kind] = i;
      render();
    }
    function render() {
      drums.forEach(function (dr) { dr.render(); });
      restEls.forEach(function (el) { el.textContent = SOURCES[st.src].rest; });
      destRestEls.forEach(function (el) { el.textContent = DESTS[st.dst].rest; });
      runRestEls.forEach(function (el) { el.textContent = RUNNERS[st.run].rest(SOURCES[st.src].id); });
      pickEls.forEach(function (el) { el.setAttribute('aria-current', el.getAttribute('data-cs-pick') === SOURCES[st.src].id ? 'true' : 'false'); });
      pickOutEls.forEach(function (el) { el.setAttribute('aria-current', el.getAttribute('data-cs-pick-out') === DESTS[st.dst].id ? 'true' : 'false'); });
    }

    function buildDrum(el) {
      var field = el.getAttribute('data-cs-drum'); // label | scheme (source), dest (destination)
      var kind = field === 'dest' ? 'dst' : field === 'runner' ? 'run' : 'src';
      var list = kind === 'dst' ? DESTS : kind === 'run' ? RUNNERS : SOURCES;
      el.textContent = '';
      el.classList.add('cs-drum');
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      var col = document.createElement('span');
      col.className = 'cs-drum__col';
      var items = list.map(function (p, j) {
        var s = document.createElement('span');
        s.className = 'cs-drum__item';
        s.textContent = field === 'label' ? p.label : (field === 'dest' || field === 'runner') ? p.word : p.id;
        s.addEventListener('click', function () { set(kind, j, true); });
        col.appendChild(s);
        return s;
      });
      el.appendChild(col);
      var acc = 0;
      el.addEventListener('wheel', function (e) {
        var dir = e.deltaY > 0 ? 1 : -1;
        var next = Math.max(0, Math.min(list.length - 1, st[kind] + dir));
        if (next === st[kind]) return; // en bout de liste, la page défile
        e.preventDefault();
        touched = true;
        acc += e.deltaY;
        if (Math.abs(acc) < 40) return;
        acc = 0;
        set(kind, next, true);
      }, { passive: false });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); set(kind, st[kind] + (e.key === 'ArrowDown' ? 1 : -1), true); }
      });
      ['pointerenter', 'focus'].forEach(function (ev) { el.addEventListener(ev, function () { touched = true; el.classList.add('is-open'); }); });
      ['pointerleave', 'blur'].forEach(function (ev) { el.addEventListener(ev, function () { el.classList.remove('is-open'); }); });
      return {
        el: el,
        render: function () {
          var i = st[kind], h = items[0].offsetHeight;
          el.style.height = h + 'px';
          el.style.width = items[i].offsetWidth + 'px';
          col.style.transform = 'translateY(' + (-i * h) + 'px)';
          items.forEach(function (it, j) {
            var dd = Math.min(Math.abs(j - i), 2);
            it.setAttribute('data-dist', dd);
            it.setAttribute('aria-hidden', dd ? 'true' : 'false');
          });
          el.setAttribute('aria-label', (kind === 'dst' ? 'Destination : ' : kind === 'run' ? 'Outil de lancement : ' : 'Plateforme : ') + list[i].label + '. Flèches haut et bas pour changer.');
        }
      };
    }

    copyEls.forEach(function (btn) {
      var original = btn.textContent, timer;
      btn.addEventListener('click', function () {
        var text = command();
        var done = function () { btn.textContent = 'copié'; clearTimeout(timer); timer = setTimeout(function () { btn.textContent = original; }, 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
        else { fallbackCopy(text); done(); }
      });
    });
    function pick(els, attr, list, kind) {
      els.forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.preventDefault();
          var j = list.findIndex(function (p) { return p.id === el.getAttribute(attr); });
          if (j >= 0) set(kind, j, true);
        });
      });
    }
    pick(pickEls, 'data-cs-pick', SOURCES, 'src');
    pick(pickOutEls, 'data-cs-pick-out', DESTS, 'dst');

    render();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
    window.addEventListener('resize', render);

    // Un seul tour au chargement (sur la source), annulé dès que l'utilisateur touche à un tambour, jamais relancé.
    if (!touched && !reduce && drums.length) {
      setTimeout(function () {
        if (touched) return;
        drums.forEach(function (dr) { if (['label', 'scheme'].indexOf(dr.el.getAttribute('data-cs-drum')) >= 0) dr.el.classList.add('is-open'); });
        [1, 2, 0].forEach(function (v, n) { setTimeout(function () { if (!touched) set('src', v, false); }, INTRO_STEP * (n + 1)); });
        setTimeout(function () {
          if (!touched) drums.forEach(function (dr) { dr.el.classList.remove('is-open'); });
        }, INTRO_STEP * 4);
      }, INTRO_DELAY);
    }
  }

  function fallbackCopy(text) {
    var t = document.createElement('textarea');
    t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0';
    document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); } catch (e) { /* rien */ }
    document.body.removeChild(t);
  }

  function boot() { document.querySelectorAll('[data-cs-root]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
