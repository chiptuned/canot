/* Canot · sélecteur de plateforme source (variante 3b)
   Sans dépendance. Marquage attendu : voir README.md. */
(function () {
  'use strict';

  // Ordre = ordre du tambour. Laisser la commande la plus longue en dernier :
  // les voisines affichées sous le mot ne recouvrent alors jamais la 2e ligne.
  var PLATFORMS = [
    { id: 'snowflake', label: 'Snowflake', rest: '://MY_ACCOUNT/MY_DATABASE?user=MY_USER' },
    { id: 'bigquery', label: 'BigQuery', rest: '://MY_PROJECT/MY_DATASET?location=EU' },
    { id: 'databricks', label: 'Databricks', rest: '://MY_WORKSPACE.cloud.databricks.com/sql/1.0/warehouses/MY_WAREHOUSE_ID?catalog=MY_CATALOG' }
  ];
  var INTRO_DELAY = 1400;
  var INTRO_STEP = 650;

  function command(p) { return 'uvx canot drill "' + p.id + p.rest + '" ./iceberg'; }

  function init(root) {
    var idx = 0;
    var touched = false;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var fromUrl = new URLSearchParams(location.search).get('source');
    var urlIdx = PLATFORMS.findIndex(function (p) { return p.id === fromUrl; });
    if (urlIdx >= 0) { idx = urlIdx; touched = true; }

    var restEls = root.querySelectorAll('[data-cs-rest]');
    var pickEls = root.querySelectorAll('[data-cs-pick]');
    var copyEls = root.querySelectorAll('[data-cs-copy]');
    var drums = Array.prototype.map.call(root.querySelectorAll('[data-cs-drum]'), buildDrum);

    function touch() { touched = true; }

    function set(i, byUser) {
      i = Math.max(0, Math.min(PLATFORMS.length - 1, i));
      if (byUser) touch();
      if (i === idx) return;
      idx = i;
      render();
    }

    function render() {
      var p = PLATFORMS[idx];
      drums.forEach(function (d) { d.render(idx); });
      restEls.forEach(function (el) { el.textContent = p.rest; });
      pickEls.forEach(function (el) {
        el.setAttribute('aria-current', el.getAttribute('data-cs-pick') === p.id ? 'true' : 'false');
      });
    }

    function buildDrum(el) {
      var field = el.getAttribute('data-cs-drum'); // "label" ou "scheme"
      el.textContent = '';
      el.classList.add('cs-drum');
      el.tabIndex = 0;
      el.setAttribute('role', 'button');

      var col = document.createElement('span');
      col.className = 'cs-drum__col';
      var items = PLATFORMS.map(function (p, j) {
        var s = document.createElement('span');
        s.className = 'cs-drum__item';
        s.textContent = field === 'label' ? p.label : p.id;
        s.addEventListener('click', function () { set(j, true); });
        col.appendChild(s);
        return s;
      });
      el.appendChild(col);

      var acc = 0;
      el.addEventListener('wheel', function (e) {
        var dir = e.deltaY > 0 ? 1 : -1;
        var next = Math.max(0, Math.min(PLATFORMS.length - 1, idx + dir));
        if (next === idx) return; // en bout de liste, on laisse la page défiler
        e.preventDefault();
        touch();
        acc += e.deltaY;
        if (Math.abs(acc) < 40) return;
        acc = 0;
        set(next, true);
      }, { passive: false });

      el.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          set(idx + (e.key === 'ArrowDown' ? 1 : -1), true);
        }
      });
      ['pointerenter', 'focus'].forEach(function (ev) {
        el.addEventListener(ev, function () { touch(); el.classList.add('is-open'); });
      });
      ['pointerleave', 'blur'].forEach(function (ev) {
        el.addEventListener(ev, function () { el.classList.remove('is-open'); });
      });

      return {
        el: el,
        render: function (i) {
          var h = items[0].offsetHeight;
          el.style.height = h + 'px';
          el.style.width = items[i].offsetWidth + 'px';
          col.style.transform = 'translateY(' + (-i * h) + 'px)';
          items.forEach(function (it, j) {
            var d = Math.min(Math.abs(j - i), 2);
            it.setAttribute('data-dist', d);
            it.setAttribute('aria-hidden', d ? 'true' : 'false');
          });
          el.setAttribute('aria-label', 'Plateforme : ' + PLATFORMS[i].label + '. Flèches haut et bas pour changer.');
        }
      };
    }

    copyEls.forEach(function (btn) {
      var original = btn.textContent;
      var timer;
      btn.addEventListener('click', function () {
        var text = command(PLATFORMS[idx]);
        var done = function () {
          btn.textContent = 'copié';
          clearTimeout(timer);
          timer = setTimeout(function () { btn.textContent = original; }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
        } else { fallbackCopy(text); done(); }
      });
    });

    pickEls.forEach(function (el) {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        var j = PLATFORMS.findIndex(function (p) { return p.id === el.getAttribute('data-cs-pick'); });
        if (j >= 0) set(j, true);
      });
    });

    render();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(render);
    window.addEventListener('resize', render);

    // Un seul tour au chargement, annulé dès que l'utilisateur touche au tambour, jamais relancé.
    if (!touched && !reduce && drums.length) {
      setTimeout(function () {
        if (touched) return;
        drums.forEach(function (d) { d.el.classList.add('is-open'); });
        [1, 2, 0].forEach(function (v, n) {
          setTimeout(function () { if (!touched) set(v, false); }, INTRO_STEP * (n + 1));
        });
        setTimeout(function () {
          if (!touched) drums.forEach(function (d) { d.el.classList.remove('is-open'); });
        }, INTRO_STEP * 4);
      }, INTRO_DELAY);
    }
  }

  function fallbackCopy(text) {
    var t = document.createElement('textarea');
    t.value = text;
    t.setAttribute('readonly', '');
    t.style.position = 'fixed';
    t.style.opacity = '0';
    document.body.appendChild(t);
    t.select();
    try { document.execCommand('copy'); } catch (e) { /* rien */ }
    document.body.removeChild(t);
  }

  function boot() {
    document.querySelectorAll('[data-cs-root]').forEach(init);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
