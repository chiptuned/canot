(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var EN = /^en/i.test(document.documentElement.lang);
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Reveal on scroll (the text stays readable without this script; nothing moves with reduced motion)
  var targets = document.querySelectorAll(".seal-grid, .card");
  if (reduce || !("IntersectionObserver" in window)) {
    targets.forEach(function (t) { t.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.35 });
    targets.forEach(function (t) { io.observe(t); });
  }

  // Language: French is the default, never an automatic redirect. The choice is remembered only to decide
  // whether to offer the English version to English-language browsers on the French home page.
  var KEY = "canot.lang";
  function store(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* storage unavailable */ } }
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  document.querySelectorAll(".lang-switch a[hreflang]").forEach(function (a) {
    a.addEventListener("click", function () { store(a.getAttribute("hreflang")); });
  });
  // Docs and discrepancies exist in English only and are shared by both versions: when the visitor came
  // from the French site, keep the French header so the way back lands on the French home page.
  (function sharedPages() {
    var shared = /^\/(docs|discrepancies)(\.html)?$/.test(location.pathname);
    if (!shared) { store(EN ? "en" : "fr"); return; }
    if (stored() !== "fr") return;
    var FR = { "/en/": ["/", "accueil"], "/en/why.html": ["/pourquoi.html", "Pourquoi"], "/en/method.html": ["/methode.html", "Méthode"],
      "/en/offers.html": ["/offres.html", "Offres"], "/en/report.html": ["/rapport.html", null] };
    document.querySelectorAll("header.top a").forEach(function (a) {
      var m = FR[a.getAttribute("href")];
      if (m) { a.setAttribute("href", m[0]); if (m[1] && !a.classList.contains("brand")) a.textContent = m[1]; }
    });
    document.querySelectorAll("header.top nav a").forEach(function (a) {
      var h = a.getAttribute("href");
      if (h === "/discrepancies.html") a.textContent = "Écarts";
      else if (h === "/docs.html") a.textContent = "Documentation";
      else if (h === "/rapport.html") a.innerHTML = '<span class="long">Exemple de rapport</span><span class="short">Exemple</span>';
      else if (a.classList.contains("btn")) a.innerHTML = '<span class="long">Tester gratuitement</span><span class="short">Test gratuit</span>';
    });
    var sw = document.querySelector(".lang-switch a[hreflang='fr']");
    if (sw) sw.setAttribute("href", "/");
  })();
  (function banner() {
    var en = document.querySelector(".lang-switch a[hreflang='en']");
    var nav = (navigator.languages && navigator.languages[0]) || navigator.language || "";
    var home = location.pathname === "/" || location.pathname === "/index.html";
    if (EN || !en || !home || !/^en/i.test(nav) || stored() === "fr") return;
    var bar = document.createElement("div");
    bar.className = "lang-banner"; bar.setAttribute("lang", "en"); bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Language");
    var link = document.createElement("a");
    link.href = en.getAttribute("href"); link.hreflang = "en"; link.textContent = "Read this site in English?";
    link.addEventListener("click", function () { store("en"); });
    var no = document.createElement("button");
    no.type = "button"; no.textContent = "No thanks";
    no.addEventListener("click", function () { store("fr"); bar.remove(); });
    bar.appendChild(link); bar.appendChild(no);
    var skip = document.querySelector(".skip");
    if (skip && skip.nextSibling) skip.parentNode.insertBefore(bar, skip.nextSibling); else document.body.insertBefore(bar, document.body.firstChild);
  })();

  // Forms (contact and qualified request): same POST to /api/contact
  var T = EN ? {
    required: "Required field.", email: "Invalid address.", project: "Tell us about your project in a few words.",
    big: "File too large: attach scan-share.json, not scan.json.", notShare: "This file is not a Canot scan-share.json.",
    thanks: function (name, when) { return "Thank you " + name + ". We will get back to you with a slot before your renewal in " + when + "."; },
    fail: "Sending failed. Nothing was recorded. Write to us directly at vincent@q4o.com.", locale: "en-GB"
  } : {
    required: "Champ requis.", email: "Adresse invalide.", project: "Dites-nous en quelques mots votre projet.",
    big: "Fichier trop gros : joignez scan-share.json, pas scan.json.", notShare: "Ce fichier n'est pas un scan-share.json de Canot.",
    thanks: function (name, when) { return "Merci " + name + ". Nous revenons vers vous avec un créneau, avant votre renouvellement de " + when + "."; },
    fail: "L'envoi n'a pas abouti. Rien n'a été enregistré. Écrivez-nous directement à vincent@q4o.com.", locale: "fr-FR"
  };
  var SCHEMAS = ["canot.share/1", "canot.partage/1"];
  var EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function setup(form) {
    var lead = form.getAttribute("data-lead"), offre = lead === "offre" || lead === "offer", invited = lead === "invited";
    var thanks = document.getElementById(invited ? "thanks-invited" : offre ? "thanks-offre" : "thanks"), msg = document.getElementById("thanks-msg"),
        fail = document.getElementById(invited ? "fail-invited" : offre ? "fail-offre" : "fail"), send = form.querySelector("button.send"),
        again = document.getElementById("again"), preview = document.getElementById("preview"), body = document.getElementById("preview-body");
    var req = function (v) { return v.trim() ? "" : T.required; };
    // Arriving from the command line (`canot scan redshift://…`): preselect the platform and start the message
    var want = (new URLSearchParams(location.search).get("connector") || "").toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 30);
    if (offre && want && form.elements.platform) {
      var NAMES = { fabric: "Microsoft Fabric / Synapse", synapse: "Microsoft Fabric / Synapse", mssql: "Microsoft Fabric / Synapse", redshift: "Amazon Redshift", teradata: "Teradata",
                    oracle: "Oracle", sap: "SAP Datasphere", datasphere: "SAP Datasphere", hana: "SAP Datasphere", clickhouse: "Autre", dataiku: "Autre", sas: "Autre" };
      var label = NAMES[want] || "Autre", sel = form.elements.platform;
      var opt = Array.prototype.find.call(sel.options, function (o) { return o.value === label || o.text === label; });
      sel.value = opt ? opt.value : "Autre";
      if (form.elements.message && !form.elements.message.value) {
        form.elements.message.value = (EN ? "I would like a Canot connector for: " : "Je voudrais un connecteur Canot pour : ") + (want === "your-platform" ? "" : want) + ". ";
      }
    }
    var checks = {
      name: req, company: req,
      email: function (v) { return v ? (EMAIL.test(v) ? "" : T.email) : T.required; },
      platform: function (v) { return v ? "" : T.required; }
    };
    if (invited) { delete checks.company; delete checks.platform; }
    else if (offre) checks.message = function (v) { return v.trim() ? "" : T.project; };
    else checks.renewal = function (v) { return v ? "" : T.required; };
    var fileInput = offre ? (form.elements.share || form.elements.partage) : null;
    var fileOut = fileInput ? form.querySelector('[data-for="' + fileInput.name + '"]') : null;
    var share = null;
    if (fileInput) fileInput.addEventListener("change", function () {
      var f = this.files[0];
      share = null; fileOut.textContent = ""; if (preview) preview.hidden = true;
      if (!f) return;
      if (f.size > 30000) { fileOut.textContent = T.big; return; }
      var r = new FileReader();
      r.onload = function () {
        try {
          var o = JSON.parse(r.result);
          if (SCHEMAS.indexOf(o.schema) < 0) throw new Error("schema");
          share = r.result;
          if (body && preview) { body.textContent = JSON.stringify(o, null, 2); preview.hidden = false; }
        } catch (e) { fileOut.textContent = T.notShare; }
      };
      r.readAsText(f);
    });
    function validate() {
      var ok = true;
      Object.keys(checks).forEach(function (k) {
        var el = form.elements[k], e = checks[k](el.value), out = form.querySelector('[data-for="' + k + '"]');
        if (out) out.textContent = e;
        el.setAttribute("aria-invalid", e ? "true" : "false");
        if (e) ok = false;
      });
      if (fileOut && fileOut.textContent) ok = false;
      return ok;
    }
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      fail.hidden = true;
      if (!validate()) return;
      var data = { lang: EN ? "en" : "fr" };
      ["name", "company", "email", "platform", "renewal", "message"].forEach(function (k) { data[k] = ((form.elements[k] && form.elements[k].value) || "").trim(); });
      data.website = form.elements.website.value;
      if (invited) data.kind = "invited";
      if (offre) { data.kind = "offer"; if (share) data.share = share; }
      send.disabled = true;
      fetch("/api/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
        .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r; })
        .then(function () {
          if (!offre && !invited && msg) {
            var parts = data.renewal.split("-"), when = new Date(+parts[0], +parts[1] - 1, 1).toLocaleDateString(T.locale, { month: "long", year: "numeric" });
            msg.textContent = T.thanks(data.name.split(" ")[0], when);
          }
          form.hidden = true; thanks.hidden = false;
        })
        .catch(function () { fail.textContent = T.fail; fail.hidden = false; })
        .then(function () { send.disabled = false; });
    });
    if (again) again.addEventListener("click", function () { form.reset(); thanks.hidden = true; form.hidden = false; });
  }
  document.querySelectorAll("form[data-lead], form#form").forEach(setup);
})();

/* Runner, action, source and destination selectors (tools/selecteur/, extended). Bilingual texts; identical commands. */
(function () {
  'use strict';
  var EN = /^en/i.test(document.documentElement.lang);
  function tr(fr, en) { return EN ? en : fr; }
  var ALIAS = { rejeu: 'replay' }; // former action id, still accepted in markup and URLs
  function norm(id) { return ALIAS[id] || id; }

  var ACTIONS = [
    { id: 'demo', word: 'demo', label: tr('démo', 'demo'), src: false, dst: true, dstIds: null, srcIds: [],
      text: tr("Génère TPC-H en local, le copie, compare tout et écrit un dossier de preuve signé. Sans compte ni données.",
               "Generates TPC-H locally, copies it, compares everything and writes a signed evidence pack. No account, no data needed.") },
    { id: 'scan', word: 'scan', label: 'scan', src: true, dst: false, srcIds: ['snowflake', 'bigquery', 'databricks', 'dbt', 'fabric', 'redshift', 'teradata', 'oracle', 'sap', 'clickhouse'],
      text: tr("Radiographie en lecture seule : inventaire, coûts, ce qui ne part pas tel quel. Rien n'est modifié chez vous.",
               "Read-only X-ray: inventory, costs, what will not move as is. Nothing is changed on your side.") },
    { id: 'drill', word: 'drill', label: 'drill', src: true, dst: true, dstIds: null, srcIds: ['snowflake', 'bigquery', 'databricks', 'fabric', 'redshift', 'teradata', 'oracle', 'sap', 'clickhouse'],
      text: tr("Copie, compare tout, rejoue vos requêtes les plus coûteuses et écrit un dossier de preuve signé.",
               "Copies, compares everything, replays your most expensive queries and writes a signed evidence pack.") },
    { id: 'replay', word: 'replay-dbt', label: tr('rejeu dbt', 'dbt replay'), src: true, dst: true, dstIds: ['local', 'onprem'], srcIds: ['dbt'],
      text: tr("Rejoue les modèles dbt dans DuckDB sur la copie et compare le résultat aux tables d'origine.",
               "Replays the dbt models in DuckDB on the copy and compares the result with the original tables.") }
  ];
  var SOURCES = [
    { id: 'snowflake', label: 'Snowflake', rest: '://MY_ACCOUNT/MY_DATABASE?user=MY_USER' },
    { id: 'databricks', label: 'Databricks', rest: '://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG' },
    { id: 'bigquery', label: 'BigQuery', rest: '://MY_PROJECT/MY_DATASET?location=EU' },
    { id: 'redshift', label: 'Amazon Redshift', rest: '://MY_CLUSTER/MY_DATABASE', soon: true },
    { id: 'fabric', label: 'Fabric / Synapse', rest: '://MY_WORKSPACE', soon: true },
    { id: 'teradata', label: 'Teradata', rest: '://MY_HOST/MY_DATABASE', soon: true },
    { id: 'oracle', label: 'Oracle', rest: '://MY_HOST/MY_SERVICE', soon: true },
    { id: 'sap', label: 'SAP Datasphere', rest: '://MY_TENANT', soon: true },
    { id: 'clickhouse', label: 'ClickHouse', rest: '://MY_HOST/MY_DATABASE', soon: true },
    { id: 'dbt', label: 'dbt', rest: '://MY_DBT_PROJECT' }  // scan and replay only: not offered with drill
  ];
  var BUCKET = ' --bucket MY_BUCKET';
  var DESTS = [
    { id: 'local', label: tr('Dossier local (DuckDB)', 'Local folder (DuckDB)'), word: './iceberg', rest: '' },
    { id: 'scaleway', label: 'Scaleway', word: 'scaleway:fr-par', rest: BUCKET },
    { id: 'ovh', label: 'OVHcloud', word: 'ovh:gra', rest: BUCKET },
    { id: 'outscale', label: 'Outscale', word: 'outscale:eu-west-2', rest: BUCKET },
    { id: 'ionos', label: 'IONOS', word: 'ionos:eu-central-1', rest: BUCKET },
    { id: 'stackit', label: 'STACKIT', word: 'stackit:eu01', rest: BUCKET },
    { id: 'aws', label: 'AWS (Paris)', word: 'aws:eu-west-3', rest: BUCKET },
    { id: 'onprem', label: tr('S3 sur site', 'On-prem S3'), word: '"s3://MY_BUCKET/canot?endpoint=https://s3.internal:9000"', rest: '' }
  ];
  // How to run Canot: uv (uvx), pipx or pip. The warehouse drivers (Snowflake, BigQuery, Databricks) are optional extras added to the command when needed.
  var RUNNERS = [
    { id: 'uvx', label: 'uvx', word: 'uvx canot' },
    { id: 'pipx', label: 'pipx', word: 'pipx run canot' },
    { id: 'pip', label: 'pip', word: 'pip install canot && canot' }
  ];
  var LISTS = { run: RUNNERS, act: ACTIONS, src: SOURCES, dst: DESTS };
  var INTRO_DELAY = 1400, INTRO_STEP = 650;

  function init(root) {
    var st = { run: 0, act: 2, src: 0, dst: 0 }; // default action: drill
    var touched = false;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var qs = new URLSearchParams(location.search);
    [['outil', 'run'], ['source', 'src'], ['sortie', 'dst']].forEach(function (pair) {
      var k = LISTS[pair[1]].findIndex(function (p) { return p.id === norm(qs.get(pair[0])); });
      if (k >= 0) { st[pair[1]] = k; touched = true; }
    });

    function allowed(kind) {  // list indices usable with the current action
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
    // Phones: nobody runs a long command there, and the drums are heavy on a small screen: offer the short demo command
    var mobile = !!(window.matchMedia && window.matchMedia('(max-width: 640px)').matches);
    if (mobile) {
      var cmdCode = root.querySelector('.cmd code');
      if (cmdCode) cmdCode.textContent = RUNNERS[0].word + ' demo';
    }
    var restEls = root.querySelectorAll('[data-cs-rest]');
    var destRestEls = root.querySelectorAll('[data-cs-dest-rest]');
    var prefixEls = root.querySelectorAll('[data-cs-dest-prefix]');
    var textEls = root.querySelectorAll('[data-cs-action-text]');
    var picks = [['data-cs-pick', 'src'], ['data-cs-pick-out', 'dst'], ['data-cs-pick-act', 'act']].map(function (x) { return { attr: x[0], kind: x[1], els: root.querySelectorAll('[' + x[0] + ']') }; });
    var copyEls = root.querySelectorAll('[data-cs-copy]');
    var drums = Array.prototype.map.call(root.querySelectorAll('[data-cs-drum]'), buildDrum);

    function command() {
      if (mobile) return RUNNERS[0].word + ' demo';
      var r = RUNNERS[st.run], a = ACTIONS[st.act], s = SOURCES[st.src], d = DESTS[st.dst];
      var ex = a.src && ['snowflake', 'bigquery', 'databricks'].indexOf(s.id) >= 0 ? s.id : '';  // heavy drivers are optional extras
      var rw = !ex ? r.word : r.id === 'uvx' ? 'uvx "canot[' + ex + ']"' : r.id === 'pipx' ? 'pipx run --spec "canot[' + ex + ']" canot' : 'pip install "canot[' + ex + ']" && canot';
      var out = rw + ' ' + a.word;
      if (a.src) out += ' "' + s.id + s.rest + '"';
      if (a.dst) out += (a.id === 'replay' ? ' --iceberg ' : ' ') + d.word + (a.id === 'replay' ? '' : d.rest);
      return out;
    }
    function actionFor(kind, i) {  // the first action that can show this value (picking dbt switches to scan, a host to drill...)
      var id = LISTS[kind][i].id, order = kind === 'src' ? ['drill', 'scan', 'replay'] : ['drill', 'demo', 'replay'];
      for (var n = 0; n < order.length; n++) {
        var k = ACTIONS.findIndex(function (a) { return a.id === order[n]; }), a = ACTIONS[k];
        var ids = kind === 'src' ? a.srcIds : a.dstIds;
        if (a[kind] && (!ids || ids.indexOf(id) >= 0)) return k;
      }
      return -1;
    }
    function set(kind, i, byUser) {
      if (kind === 'src' || kind === 'dst') {
        if (allowed(kind).indexOf(i) < 0 || !ACTIONS[st.act][kind]) {
          var k = actionFor(kind, i);
          if (k < 0) return;
          st.act = k; clamp();
        }
      }
      if (byUser) touched = true;
      if (i === st[kind]) return;
      st[kind] = i;
      if (kind === 'act') clamp();
      render();
    }
    function render() {
      var a = ACTIONS[st.act];
      parts.forEach(function (el) { el.hidden = el.getAttribute('data-cs-show').split(' ').map(norm).indexOf(a.id) < 0; });
      drums.forEach(function (dr) { dr.render(); });
      restEls.forEach(function (el) { el.textContent = SOURCES[st.src].rest; });
      destRestEls.forEach(function (el) { el.textContent = a.id === 'replay' ? '' : DESTS[st.dst].rest; });
      prefixEls.forEach(function (el) { el.textContent = a.id === 'replay' ? ' --iceberg ' : ' '; });
      textEls.forEach(function (el) { el.textContent = a.text; });

      picks.forEach(function (p) {
        p.els.forEach(function (el) {
          var j = LISTS[p.kind].findIndex(function (x) { return x.id === norm(el.getAttribute(p.attr)); });
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
        if (np < 0 || np >= v.length) return; // at the end of the list, the page scrolls
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
          if (!v.length) return; // no possible value for this action: the part is hidden
          items.forEach(function (it, j) { it.hidden = v.indexOf(j) < 0; });
          var first = items[v[0]], h = first.getBoundingClientRect().height; // fractional: offsetHeight rounds, which drifts with the index
          el.style.height = h + 'px';
          el.style.width = items[v[pos]].offsetWidth + 'px';
          col.style.transform = 'translateY(' + (-pos * h) + 'px)';
          v.forEach(function (j, n) {
            var dd = Math.min(Math.abs(n - pos), 2);
            items[j].setAttribute('data-dist', dd);
            items[j].setAttribute('aria-hidden', dd ? 'true' : 'false');
          });
          var names = EN ? { dst: 'Destination: ', run: 'Runner: ', act: 'Action: ', src: 'Platform: ' } : { dst: 'Destination : ', run: 'Outil de lancement : ', act: 'Action : ', src: 'Plateforme : ' };
          el.setAttribute('aria-label', names[kind] + (list[st[kind]].label || list[st[kind]].word) + tr('. Flèches haut et bas pour changer.', '. Up and down arrows to change.'));
          el.hidden = false;
        }
      };
    }

    copyEls.forEach(function (btn) {
      var original = btn.textContent, timer;
      btn.addEventListener('click', function () {
        var text = command();
        var done = function () { btn.textContent = tr('copié', 'copied'); clearTimeout(timer); timer = setTimeout(function () { btn.textContent = original; }, 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
        else { fallbackCopy(text); done(); }
      });
    });
    picks.forEach(function (p) {
      p.els.forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.preventDefault();
          var j = LISTS[p.kind].findIndex(function (x) { return x.id === norm(el.getAttribute(p.attr)); });
          if (j >= 0) set(p.kind, j, true);
        });
      });
    });

    render();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
    window.addEventListener('resize', render);

    // A single spin on load (on the source), cancelled as soon as the user touches a drum, never restarted.
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
    try { document.execCommand('copy'); } catch (e) { /* nothing */ }
    document.body.removeChild(t);
  }

  function boot() { document.querySelectorAll('[data-cs-root]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
