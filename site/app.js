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

/* Sélecteurs d'outil, d'action, de source et de destination (tools/selecteur/, étendu) */
(function () {
  'use strict';

  var ACTIONS = [
    { id: 'demo', word: 'demo', label: 'démo', src: false, dst: true, dstIds: null, srcIds: [],
      text: "Génère TPC-H en local, le copie, compare tout et écrit un dossier de preuve signé. Sans compte ni données." },
    { id: 'scan', word: 'scan', label: 'scan', src: true, dst: false, srcIds: ['snowflake', 'bigquery', 'databricks', 'dbt'],
      text: "Radiographie en lecture seule : inventaire, coûts, ce qui ne part pas tel quel. Rien n'est modifié chez vous." },
    { id: 'drill', word: 'drill', label: 'drill', src: true, dst: true, dstIds: null, srcIds: ['snowflake', 'bigquery', 'databricks'],
      text: "Copie, compare tout, rejoue vos requêtes les plus coûteuses et écrit un dossier de preuve signé." },
    { id: 'rejeu', word: 'rejeu-dbt', label: 'rejeu dbt', src: true, dst: true, dstIds: ['local', 'onprem'], srcIds: ['dbt'],
      text: "Rejoue les modèles dbt dans DuckDB sur la copie et compare le résultat aux tables d'origine." }
  ];
  var SOURCES = [
    { id: 'snowflake', label: 'Snowflake', rest: '://MY_ACCOUNT/MY_DATABASE?user=MY_USER' },
    { id: 'bigquery', label: 'BigQuery', rest: '://MY_PROJECT/MY_DATASET?location=EU' },
    { id: 'dbt', label: 'dbt', rest: '://MY_DBT_PROJECT' },
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
    { id: 'aws', label: 'AWS (Paris)', word: 'aws:eu-west-3', rest: BUCKET },
    { id: 'onprem', label: 'S3 sur site', word: '"s3://MY_BUCKET/canot?endpoint=https://s3.internal:9000"', rest: '' }
  ];
  // Comment lancer Canot : uv (uvx), pipx ou pip. Les connecteurs sont installés d'office avec Canot.
  var RUNNERS = [
    { id: 'uvx', label: 'uvx', word: 'uvx canot' },
    { id: 'pipx', label: 'pipx', word: 'pipx run canot' },
    { id: 'pip', label: 'pip', word: 'pip install canot && canot' }
  ];
  var LISTS = { run: RUNNERS, act: ACTIONS, src: SOURCES, dst: DESTS };
  var INTRO_DELAY = 1400, INTRO_STEP = 650;

  function init(root) {
    var st = { run: 0, act: 2, src: 0, dst: 0 }; // action par défaut : drill
    var touched = false;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var qs = new URLSearchParams(location.search);
    [['outil', 'run'], ['action', 'act'], ['source', 'src'], ['sortie', 'dst']].forEach(function (pair) {
      var k = LISTS[pair[1]].findIndex(function (p) { return p.id === qs.get(pair[0]); });
      if (k >= 0) { st[pair[1]] = k; touched = true; }
    });

    function allowed(kind) {  // indices de la liste utilisables avec l'action courante
      var a = ACTIONS[st.act], ids = kind === 'src' ? a.srcIds : kind === 'dst' ? a.dstIds : null;
      return LISTS[kind].map(function (p, j) { return !ids || ids.indexOf(p.id) >= 0 ? j : -1; }).filter(function (j) { return j >= 0; });
    }
    function clamp() {
      ['src', 'dst'].forEach(function (k) {
        var ok = allowed(k);
        if (ok.length && ok.indexOf(st[k]) < 0) st[k] = ok[0];
      });
    }
    clamp();

    var parts = root.querySelectorAll('[data-cs-show]');
    var restEls = root.querySelectorAll('[data-cs-rest]');
    var destRestEls = root.querySelectorAll('[data-cs-dest-rest]');
    var prefixEls = root.querySelectorAll('[data-cs-dest-prefix]');
    var textEls = root.querySelectorAll('[data-cs-action-text]');
    var picks = [['data-cs-pick', 'src'], ['data-cs-pick-out', 'dst'], ['data-cs-pick-act', 'act']].map(function (x) { return { attr: x[0], kind: x[1], els: root.querySelectorAll('[' + x[0] + ']') }; });
    var copyEls = root.querySelectorAll('[data-cs-copy]');
    var drums = Array.prototype.map.call(root.querySelectorAll('[data-cs-drum]'), buildDrum);

    function command() {
      var r = RUNNERS[st.run], a = ACTIONS[st.act], s = SOURCES[st.src], d = DESTS[st.dst];
      var out = r.word + ' ' + a.word;
      if (a.src) out += ' "' + s.id + s.rest + '"';
      if (a.dst) out += (a.id === 'rejeu' ? ' --iceberg ' : ' ') + d.word + (a.id === 'rejeu' ? '' : d.rest);
      return out;
    }
    function set(kind, i, byUser) {
      if (kind === 'src' || kind === 'dst') { if (allowed(kind).indexOf(i) < 0) return; }
      if (byUser) touched = true;
      if (i === st[kind]) return;
      st[kind] = i;
      if (kind === 'act') clamp();
      render();
    }
    function render() {
      var a = ACTIONS[st.act];
      parts.forEach(function (el) { el.hidden = el.getAttribute('data-cs-show').split(' ').indexOf(a.id) < 0; });
      drums.forEach(function (dr) { dr.render(); });
      restEls.forEach(function (el) { el.textContent = SOURCES[st.src].rest; });
      destRestEls.forEach(function (el) { el.textContent = a.id === 'rejeu' ? '' : DESTS[st.dst].rest; });
      prefixEls.forEach(function (el) { el.textContent = a.id === 'rejeu' ? ' --iceberg ' : ' '; });
      textEls.forEach(function (el) { el.textContent = a.text; });
      picks.forEach(function (p) {
        p.els.forEach(function (el) {
          var j = LISTS[p.kind].findIndex(function (x) { return x.id === el.getAttribute(p.attr); });
          el.setAttribute('aria-current', j === st[p.kind] ? 'true' : 'false');
        });
      });
    }

    function buildDrum(el) {
      var field = el.getAttribute('data-cs-drum'); // runner | action | label | scheme (source) | dest
      var kind = field === 'dest' ? 'dst' : field === 'runner' ? 'run' : field === 'action' ? 'act' : 'src';
      var list = LISTS[kind];
      el.textContent = '';
      el.classList.add('cs-drum');
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      var col = document.createElement('span');
      col.className = 'cs-drum__col';
      var items = list.map(function (p, j) {
        var s = document.createElement('span');
        s.className = 'cs-drum__item';
        s.textContent = field === 'label' ? p.label : (field === 'dest' || field === 'runner' || field === 'action') ? p.word : p.id;
        s.addEventListener('click', function () { set(kind, j, true); });
        col.appendChild(s);
        return s;
      });
      el.appendChild(col);
      function vis() { return kind === 'src' || kind === 'dst' ? allowed(kind) : list.map(function (_, j) { return j; }); }
      var acc = 0;
      el.addEventListener('wheel', function (e) {
        var v = vis(), pos = v.indexOf(st[kind]), dir = e.deltaY > 0 ? 1 : -1, np = pos + dir;
        if (np < 0 || np >= v.length) return; // en bout de liste, la page défile
        e.preventDefault();
        touched = true;
        acc += e.deltaY;
        if (Math.abs(acc) < 40) return;
        acc = 0;
        set(kind, v[np], true);
      }, { passive: false });
      el.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          var v = vis(), np = v.indexOf(st[kind]) + (e.key === 'ArrowDown' ? 1 : -1);
          if (np >= 0 && np < v.length) set(kind, v[np], true);
        }
      });
      ['pointerenter', 'focus'].forEach(function (ev) { el.addEventListener(ev, function () { touched = true; el.classList.add('is-open'); }); });
      ['pointerleave', 'blur'].forEach(function (ev) { el.addEventListener(ev, function () { el.classList.remove('is-open'); }); });
      return {
        el: el,
        render: function () {
          var v = vis(), pos = Math.max(0, v.indexOf(st[kind]));
          if (!v.length) return; // aucune valeur possible pour cette action : la partie est masquée
          items.forEach(function (it, j) { it.hidden = v.indexOf(j) < 0; });
          var first = items[v[0]], h = first.offsetHeight;
          el.style.height = h + 'px';
          el.style.width = items[v[pos]].offsetWidth + 'px';
          col.style.transform = 'translateY(' + (-pos * h) + 'px)';
          v.forEach(function (j, n) {
            var dd = Math.min(Math.abs(n - pos), 2);
            items[j].setAttribute('data-dist', dd);
            items[j].setAttribute('aria-hidden', dd ? 'true' : 'false');
          });
          var names = { dst: 'Destination : ', run: 'Outil de lancement : ', act: 'Action : ', src: 'Plateforme : ' };
          el.setAttribute('aria-label', names[kind] + (list[st[kind]].label || list[st[kind]].word) + '. Flèches haut et bas pour changer.');
          el.hidden = false;
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
    picks.forEach(function (p) {
      p.els.forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.preventDefault();
          var j = LISTS[p.kind].findIndex(function (x) { return x.id === el.getAttribute(p.attr); });
          if (j >= 0) set(p.kind, j, true);
        });
      });
    });

    render();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
    window.addEventListener('resize', render);

    // Un seul tour au chargement (sur la source), annulé dès que l'utilisateur touche à un tambour, jamais relancé.
    if (!touched && !reduce && drums.length) {
      setTimeout(function () {
        if (touched) return;
        var order = allowed('src');
        drums.forEach(function (dr) { if (['label', 'scheme'].indexOf(dr.el.getAttribute('data-cs-drum')) >= 0) dr.el.classList.add('is-open'); });
        [order[1], order[2], order[0]].forEach(function (v, n) { setTimeout(function () { if (!touched && v !== undefined) set('src', v, false); }, INTRO_STEP * (n + 1)); });
        setTimeout(function () { if (!touched) drums.forEach(function (dr) { dr.el.classList.remove('is-open'); }); }, INTRO_STEP * 4);
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
