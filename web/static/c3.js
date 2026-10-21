/* Design C3 „Lobby-HUD“ — Verhalten des App-Rahmens.
   Bereichsleiste/Schublade, Kopfzeilen-Suche („/“), Tab-Deep-Links (?tab=),
   Prisma-Splitter. Ohne Abhängigkeiten; läuft auch ohne HTMX. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  var drawerMQ = window.matchMedia('(max-width: 1024px)');

  function flash(el) {
    el.classList.remove('is-flash');
    void el.offsetWidth;
    el.classList.add('is-flash');
    setTimeout(function () { el.classList.remove('is-flash'); }, 1700);
  }
  function focusCard(el) {
    el.scrollIntoView({ behavior: reduce.matches ? 'auto' : 'smooth', block: 'start' });
    flash(el);
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true });
  }
  window.c3FocusCard = focusCard;

  /* ---------- Befehl kopieren (Modulkarten: /backup, /warn, …) ---------- */
  document.addEventListener('click', function (e) {
    var btn = e.target instanceof Element ? e.target.closest('.c3-cmd[data-cmd]') : null;
    if (!btn) return;
    var text = btn.getAttribute('data-cmd');
    var done = function () {
      btn.classList.add('is-copied');
      setTimeout(function () { btn.classList.remove('is-copied'); }, 1600);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { window.prompt('Befehl kopieren:', text); });
    } else {
      window.prompt('Befehl kopieren:', text);
    }
  });

  function init() {
    // Beim Laden mit #mod-…: Karte aufleuchten lassen, Seitenspalte passend markieren
    if (location.hash.indexOf('#mod-') === 0) {
      var karte = document.getElementById(location.hash.slice(1));
      if (karte) window.setTimeout(function () { focusCard(karte); }, 60);
    }
    var nav = $('#c3-areanav');
    if (!nav) return;
    if (location.hash) {
      var passend = $('.c3-nav-item[href="' + location.pathname + location.hash + '"]', nav);
      if (passend) {
        $$('.c3-nav-item[aria-current]', nav).forEach(function (x) { x.removeAttribute('aria-current'); });
        passend.setAttribute('aria-current', 'page');
      }
    }
    var backdrop = $('#c3-backdrop');
    var drawerBtn = $('#c3-drawer-btn');
    var current = nav.getAttribute('data-current') || '';
    var lastOpener = null;

    /* ---------- Bereiche + Seitenspalte ---------- */
    var app = nav.closest('.c3-app');
    var ohneSpalte = app && app.getAttribute('data-ohne-spalte') === 'ja';
    function setArea(area) {
      $$('[data-panel]', nav).forEach(function (p) { p.hidden = p.getAttribute('data-panel') !== area; });
      // Bereich mit nur einer Seite: Spalte nur zeigen, solange ein anderer Bereich geöffnet ist
      if (ohneSpalte) app.classList.toggle('c3-app--ohne-spalte', area === current);
      $$('.c3-rail-item').forEach(function (r) {
        var a = r.getAttribute('data-area');
        var browsing = a === area && a !== current;
        r.classList.toggle('is-browsing', browsing);
        if (r.tagName === 'BUTTON') r.setAttribute('aria-pressed', String(browsing));
      });
    }
    function openDrawer(opener) {
      lastOpener = opener || drawerBtn;
      nav.classList.add('is-open');
      backdrop.hidden = false;
      if (drawerBtn) drawerBtn.setAttribute('aria-expanded', 'true');
      window.requestAnimationFrame(function () { var c = $('.c3-an-close', nav); if (c) c.focus(); });
    }
    function closeDrawer(restore) {
      if (!nav.classList.contains('is-open')) return;
      nav.classList.remove('is-open');
      backdrop.hidden = true;
      if (drawerBtn) drawerBtn.setAttribute('aria-expanded', 'false');
      if (restore && lastOpener && document.contains(lastOpener)) lastOpener.focus();
    }
    $$('button.c3-rail-item').forEach(function (b) {
      b.addEventListener('click', function () {
        var a = b.getAttribute('data-area');
        var browsingAlready = b.classList.contains('is-browsing');
        if (drawerMQ.matches) { setArea(a); openDrawer(b); return; }
        setArea(browsingAlready ? current : a);
      });
    });
    if (drawerBtn) drawerBtn.addEventListener('click', function () {
      if (nav.classList.contains('is-open')) closeDrawer(true); else { setArea(current); openDrawer(drawerBtn); }
    });
    var closeBtn = $('.c3-an-close', nav);
    if (closeBtn) closeBtn.addEventListener('click', function () { closeDrawer(true); setArea(current); });
    if (backdrop) backdrop.addEventListener('click', function () { closeDrawer(true); setArea(current); });
    if (drawerMQ.addEventListener) drawerMQ.addEventListener('change', function () { closeDrawer(false); setArea(current); });

    /* ---------- Tab-Deep-Links: ?tab=<name> ---------- */
    function markNav(url) {
      // aktiven Eintrag der Seitenspalte nach der URL neu setzen (Tab-Wechsel ohne Reload)
      var best = null, bestScore = 0;
      var here = new URL(url, location.href);
      $$('.c3-nav-item[href]', nav).forEach(function (a) {
        var u = new URL(a.getAttribute('href'), location.href);
        if (u.pathname !== here.pathname) return;
        var t = u.searchParams.get('tab');
        var score = t ? (t === here.searchParams.get('tab') ? 2 : 0) : 1;
        if (score > bestScore) { best = a; bestScore = score; }
      });
      if (!best) return;
      $$('.c3-nav-item[aria-current]', nav).forEach(function (a) { a.removeAttribute('aria-current'); });
      best.setAttribute('aria-current', 'page');
      var crumb = $('#c3-crumb');
      var panel = best.closest('[data-panel]');
      if (crumb && panel) crumb.textContent = panel.getAttribute('data-title') + ' / ' + best.textContent.trim();
    }
    var tabParam = new URLSearchParams(location.search).get('tab');
    if (tabParam) {
      // erst nach HTMX-Init klicken (Lazy-Tabs laden per hx-get); defer-Skripte laufen vor DOMContentLoaded
      var openTab = function () {
        var btn = $('.c3-content [data-tab="' + (window.CSS && CSS.escape ? CSS.escape(tabParam) : tabParam) + '"]');
        if (btn && !btn.classList.contains('active')) btn.click();
      };
      if (document.readyState === 'complete') openTab();
      else window.addEventListener('load', openTab);
    }
    document.addEventListener('click', function (e) {
      var t = e.target instanceof Element ? e.target.closest('.c3-content [data-tab]') : null;
      if (!t || !t.closest('.tabs, .tab-nav, .tab-list')) return;
      var u = new URL(location.href);
      var first = t.parentElement && t.parentElement.querySelector('[data-tab]');
      if (first === t) u.searchParams.delete('tab'); else u.searchParams.set('tab', t.getAttribute('data-tab'));
      if (u.href !== location.href) { history.replaceState(history.state, '', u.href); markNav(u.href); }
    });
    // Links in der Seitenspalte auf DIESELBE Seite (nur anderer Tab): ohne Reload umschalten
    nav.addEventListener('click', function (e) {
      var a = e.target instanceof Element ? e.target.closest('a.c3-nav-item') : null;
      if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
      var u = new URL(a.getAttribute('href'), location.href);
      if (u.pathname !== location.pathname) return;
      if (u.hash) {
        // Sprung zu einer Modulkarte: ist sie sichtbar, scrollen + aufleuchten; sonst Seite neu laden
        var ziel = document.getElementById(u.hash.slice(1));
        e.preventDefault();
        if (!ziel || !ziel.offsetParent) { location.href = u.pathname + u.hash; return; }
        history.replaceState(history.state, '', u.pathname + u.hash);
        closeDrawer(false);
        setArea(current);
        focusCard(ziel);
        $$('.c3-nav-item[aria-current]', nav).forEach(function (x) { x.removeAttribute('aria-current'); });
        a.setAttribute('aria-current', 'page');
        return;
      }
      var t = u.searchParams.get('tab');
      var tb = t ? $('.c3-content [data-tab="' + t + '"]') : $('.c3-content .tabs [data-tab], .c3-content .tab-nav [data-tab]');
      if (!tb) return;
      e.preventDefault();
      tb.click();
      history.replaceState(history.state, '', u.href);
      markNav(u.href);
      closeDrawer(false);
      setArea(current);
      window.scrollTo({ top: 0, behavior: reduce.matches ? 'auto' : 'smooth' });
    });

    /* ---------- Suche (Tastenkürzel /) ---------- */
    var q = $('#c3-q'), resBox = $('#c3-q-results'), form = $('#c3-q-form');
    var IDX = [];
    try { IDX = JSON.parse(($('#c3-suchindex') || {}).textContent || '[]'); } catch (err) { IDX = []; }
    var norm = function (s) { return String(s).toLowerCase().replace(/ß/g, 'ss').normalize('NFD').replace(/[̀-ͯ]/g, ''); };
    var sel = -1;
    function clearSearch() { if (!q) return; resBox.hidden = true; resBox.textContent = ''; sel = -1; }
    function render() {
      var v = norm(q.value.trim());
      if (!v) { clearSearch(); return; }
      var words = v.split(/\s+/);
      var hits = IDX.filter(function (x) {
        var hay = norm(x.t + ' ' + x.a + ' ' + (x.k || ''));
        return words.every(function (w) { return hay.indexOf(w) !== -1; });
      }).slice(0, 8);
      resBox.textContent = '';
      sel = -1;
      if (!hits.length) {
        var p = document.createElement('p');
        p.className = 'c3-q-empty';
        p.append('Nichts im Menü gefunden. ');
        var full = document.createElement('a');
        full.href = '/search?q=' + encodeURIComponent(q.value.trim());
        full.textContent = 'In Logs und Daten suchen';
        p.append(full);
        resBox.append(p);
      } else {
        var ul = document.createElement('ul');
        hits.forEach(function (x) {
          var a = document.createElement('a');
          a.className = 'c3-q-hit';
          a.href = x.href;
          var tt = document.createElement('span'); tt.className = 'c3-q-t'; tt.textContent = x.t;
          var aa = document.createElement('span'); aa.className = 'c3-q-a'; aa.textContent = x.a;
          a.append(tt, aa);
          var li = document.createElement('li'); li.append(a); ul.append(li);
        });
        resBox.append(ul);
      }
      resBox.hidden = false;
    }
    if (q) {
      q.addEventListener('input', render);
      q.addEventListener('keydown', function (e) {
        var hits = $$('.c3-q-hit', resBox);
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          if (!hits.length) return;
          e.preventDefault();
          sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
          hits.forEach(function (h, i) { h.classList.toggle('is-sel', i === sel); });
        }
      });
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var hits = $$('.c3-q-hit', resBox);
        var pick = hits[sel] || hits[0];
        if (pick) location.href = pick.href;
        else if (q.value.trim()) location.href = '/search?q=' + encodeURIComponent(q.value.trim());
      });
      resBox.addEventListener('mousedown', function (e) { e.preventDefault(); });
      form.addEventListener('focusout', function (e) {
        if (!form.contains(e.relatedTarget)) setTimeout(function () { if (!form.contains(document.activeElement)) resBox.hidden = true; }, 150);
      });
      q.addEventListener('focus', function () { if (q.value.trim() && resBox.childElementCount) resBox.hidden = false; });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (resBox && !resBox.hidden) { clearSearch(); q.focus(); return; }
        if (nav.classList.contains('is-open')) { closeDrawer(true); setArea(current); }
        return;
      }
      if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && q) {
        var el = e.target;
        if (el instanceof Element && el.closest('input, textarea, select, [contenteditable]')) return;
        e.preventDefault();
        q.focus();
        q.select();
      }
    });
  }

  /* ---------- Prisma-Splitter: Füllung + pixelgenaue Neon-Kante ---------- */
  function parse(s) { return s.split(',').map(function (p) { return p.trim().split(/\s+/).map(Number); }); }
  function normals(P, closed) {
    var n = P.length, N = [], m = closed ? n : n - 1;
    for (var i = 0; i < m; i++) {
      var a = P[i], b = P[(i + 1) % n];
      var dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
      N.push([-dy / L, dx / L]);
    }
    return N;
  }
  function miter(p, a, b, d) {
    var mx = a[0] + b[0], my = a[1] + b[1];
    var ml = Math.hypot(mx, my) || 1; mx /= ml; my /= ml;
    var c = Math.max(mx * a[0] + my * a[1], 0.3);
    return [p[0] + mx * d / c, p[1] + my * d / c];
  }
  function insetClosed(P, d) {
    var n = P.length, N = normals(P, true);
    var cx = P.reduce(function (s, p) { return s + p[0]; }, 0) / n, cy = P.reduce(function (s, p) { return s + p[1]; }, 0) / n;
    var mx = (P[0][0] + P[1][0]) / 2, my = (P[0][1] + P[1][1]) / 2;
    var sgn = ((cx - mx) * N[0][0] + (cy - my) * N[0][1]) > 0 ? 1 : -1;
    return P.map(function (p, i) { return miter(p, N[(i - 1 + n) % n], N[i], d * sgn); });
  }
  function offsetOpen(P, d) {
    var n = P.length, N = normals(P, false);
    return P.map(function (p, i) {
      if (i === 0) return [p[0] + N[0][0] * d, p[1] + N[0][1] * d];
      if (i === n - 1) return [p[0] + N[n - 2][0] * d, p[1] + N[n - 2][1] * d];
      return miter(p, N[i - 1], N[i], d);
    });
  }
  function px(p) { return p[0].toFixed(1) + 'px ' + p[1].toFixed(1) + 'px'; }
  function buildShard(el) {
    if (el.dataset.built) return;
    el.dataset.built = '1';
    var closed = !!el.dataset.pts;
    var P = parse(el.dataset.pts || el.dataset.line);
    var edge = document.createElement('i');
    edge.className = 'c3-shard-edge';
    if (closed) {
      var fill = document.createElement('i');
      fill.className = 'c3-shard-fill';
      fill.style.clipPath = 'polygon(' + P.map(function (p) { return p[0] + '% ' + p[1] + '%'; }).join(', ') + ')';
      el.append(fill);
    }
    el.append(edge);
    var t = parseFloat(el.dataset.t || '1.5');
    var draw = function () {
      var w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      var X = P.map(function (p) { return [p[0] / 100 * w, p[1] / 100 * h]; });
      if (closed) {
        var Q = insetClosed(X, t);
        var ring = X.concat([X[0]], Q, [Q[0]]);
        edge.style.clipPath = 'polygon(evenodd, ' + ring.map(px).join(', ') + ')';
      } else {
        var A = offsetOpen(X, t / 2), B = offsetOpen(X, -t / 2).reverse();
        edge.style.clipPath = 'polygon(' + A.concat(B).map(px).join(', ') + ')';
      }
    };
    draw();
    if ('ResizeObserver' in window) new ResizeObserver(draw).observe(el);
  }
  function shards(root) { $$('.c3-shard', root).forEach(buildShard); }
  window.c3Shards = shards;

  /* ---------- Rollen: Namen statt IDs (Snapshot von recon, /api/guild/roles) ---------- */
  var rollenCache = null;
  function ladeRollen() {
    if (!rollenCache || Date.now() - rollenCache.ts > 15000) {
      rollenCache = { ts: Date.now(), p: fetch('/api/guild/roles?alle=1', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { roles: [] }; })
        .then(function (d) { return (d && d.roles) || []; })
        .catch(function () { rollenCache.ts = 0; return []; }) };
    }
    return rollenCache.p;
  }
  function rollen(root) {
    var sel = $$('select[data-role-select]', root).filter(function (s) { return s.dataset.roleReady !== '1'; });
    var spans = $$('[data-role-name]', root).filter(function (s) { return s.dataset.roleReady !== '1'; });
    if (!sel.length && !spans.length) return;
    ladeRollen().then(function (liste) {
      var nachId = {};
      liste.forEach(function (r) { nachId[r.id] = r; });
      sel.forEach(function (s) {
        s.dataset.roleReady = '1';
        var aktuell = s.dataset.value || s.value || '';
        var leer = s.querySelector('option[value=""]');
        s.textContent = '';
        if (leer) s.append(leer);
        liste.forEach(function (r) {
          if (r.managed && r.id !== aktuell) return;
          var o = document.createElement('option');
          o.value = r.id; o.textContent = r.name;
          if (r.id === aktuell) o.selected = true;
          s.append(o);
        });
        if (aktuell && !nachId[aktuell]) {
          var o2 = document.createElement('option');
          o2.value = aktuell; o2.textContent = 'Gelöschte oder unbekannte Rolle'; o2.selected = true;
          s.append(o2);
        }
        if (!liste.length && !aktuell) {
          var o3 = document.createElement('option');
          o3.value = ''; o3.disabled = true; o3.textContent = '(noch keine Rollenliste vom Bot)';
          s.append(o3);
        }
        s.dispatchEvent(new Event('c3:rollen', { bubbles: true }));
      });
      spans.forEach(function (sp) {
        sp.dataset.roleReady = '1';
        var r = nachId[sp.getAttribute('data-role-name')];
        sp.textContent = r ? r.name : 'Gelöschte oder unbekannte Rolle';
        if (r && r.color) sp.style.setProperty('--rc', r.color);
        sp.classList.toggle('is-gone', !r);
      });
    });
  }
  window.c3Rollen = rollen;

  /* ---------- Ungespeicherte Änderungen (Maki-Vorschlag 7) ----------
     Jede Einstellungs-Form in .c3-content: nach der ersten Eingabe erscheint unten eine Leiste
     mit „Zurücksetzen“ und „Speichern“; Verlassen der Seite fragt nach. Ausgenommen sind
     Befehlszeilen, Such- und Aktionsformulare (data-c3-nodirty oder ohne echte Eingabefelder). */
  var dirtyForm = null, dirtyBar = null;
  var NODIRTY = '[data-c3-nodirty], .rcon-form, #bl-add-form, .c3-search';
  function zaehlt(form) {
    if (!form || form.closest(NODIRTY) || !form.closest('.c3-content')) return false;
    return !!form.querySelector('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=search]), select, textarea');
  }
  function bar() {
    if (dirtyBar) return dirtyBar;
    dirtyBar = document.createElement('div');
    dirtyBar.className = 'c3-dirty';
    dirtyBar.setAttribute('role', 'status');
    dirtyBar.hidden = true;
    var t = document.createElement('span'); t.textContent = 'Ungespeicherte Änderungen';
    var r = document.createElement('button'); r.type = 'button'; r.className = 'btn'; r.textContent = 'Zurücksetzen';
    var sv = document.createElement('button'); sv.type = 'button'; sv.className = 'btn btn-primary'; sv.textContent = 'Speichern';
    r.addEventListener('click', function () {
      if (!dirtyForm) return;
      // Listen, die per Skript gepflegt werden (z. B. Zugang), stellt nur ein Neuladen sicher her
      if (dirtyForm.querySelector('input[type=hidden][id]')) { dirtyForm = null; location.reload(); return; }
      dirtyForm.reset(); sauber();
    });
    sv.addEventListener('click', function () {
      if (!dirtyForm) return;
      var f = dirtyForm;
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    });
    dirtyBar.append(t, r, sv);
    document.body.appendChild(dirtyBar);
    return dirtyBar;
  }
  function sauber() { dirtyForm = null; if (dirtyBar) dirtyBar.hidden = true; }
  function markiere(e) {
    var f = e.target instanceof Element ? e.target.closest('form') : null;
    if (!zaehlt(f)) return;
    dirtyForm = f; bar().hidden = false;
  }
  document.addEventListener('input', markiere);
  document.addEventListener('change', markiere);
  document.addEventListener('submit', function (e) { if (e.target === dirtyForm) sauber(); }, true);
  document.addEventListener('htmx:afterRequest', function (e) {
    var f = e.detail && e.detail.elt && e.detail.elt.closest ? e.detail.elt.closest('form') : null;
    if (f && f === dirtyForm && e.detail.successful) sauber();
  });
  document.addEventListener('htmx:afterSwap', function () {
    if (dirtyForm && !document.body.contains(dirtyForm)) sauber();
  });
  window.addEventListener('beforeunload', function (e) {
    if (dirtyForm && document.body.contains(dirtyForm)) { e.preventDefault(); e.returnValue = ''; }
  });

  function boot() { init(); shards(); rollen(); }
  document.addEventListener('htmx:afterSwap', function (e) { rollen(e.target); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
  document.addEventListener('htmx:afterSwap', function (e) { shards(e.target); });
})();
