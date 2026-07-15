/**
 * ui_motion.js — Zentrale Motion-Schicht (v5.1)
 * Vanilla, keine Dependencies. Wird von base_v5.html mit `defer` geladen.
 *  - Scroll-Reveal via IntersectionObserver ([data-reveal], stagger via --rev-i)
 *  - Spotlight-Cards (Pointer -> --mx/--my auf .card)
 *  - Magnetic Primary-CTA (.magnetic -> --tx/--ty, transform-only, rAF)
 *  - Count-Up ([data-countup] animiert Zahl beim Sichtbarwerden)
 * Respektiert prefers-reduced-motion (dann No-Op ausser sofort-sichtbar).
 */
(function () {
  'use strict';
  document.documentElement.classList.add('js');

  var REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------
  // 1. Scroll-Reveal + Count-Up
  //    Robust: sofort-sichtbare Elemente werden DIREKT gezeigt (ohne
  //    IntersectionObserver-Abhaengigkeit — manche Headless-Renderer
  //    feuern IO nie), IO nur fuer below-fold, plus Failsafe-Timeout +
  //    Scroll-Fallback. Content bleibt NIE stuck-hidden.
  // ------------------------------------------------------------
  function reveal(el) {
    if (el.__revealed) return;
    el.__revealed = true;
    el.classList.add('is-in');
    if (el.hasAttribute('data-countup')) countUp(el);
    if (el.querySelectorAll) el.querySelectorAll('[data-countup]').forEach(countUp);
  }
  function _inView(el) {
    var b = el.getBoundingClientRect();
    return b.top < (window.innerHeight * 0.92) && b.bottom > 0;
  }
  function initReveal() {
    var els = document.querySelectorAll('[data-reveal]');
    // Stagger-Index je Gruppe (Geschwister mit data-reveal)
    els.forEach(function (el) {
      if (el.style.getPropertyValue('--rev-i') || !el.parentNode) return;
      var sibs = el.parentNode.querySelectorAll(':scope > [data-reveal]');
      el.style.setProperty('--rev-i', String(Array.prototype.indexOf.call(sibs, el) % 8));
    });
    if (REDUCED) {
      els.forEach(function (el) { el.classList.add('is-in'); el.__revealed = true; });
      return;
    }
    // (a) sofort sichtbare direkt zeigen
    els.forEach(function (el) { if (_inView(el)) reveal(el); });
    document.querySelectorAll('[data-countup]').forEach(function (el) {
      if (!el.closest('[data-reveal]') && _inView(el)) countUp(el);
    });
    // (b) IO fuer below-fold
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { reveal(e.target); io.unobserve(e.target); } });
      }, { threshold: 0.08, rootMargin: '0px 0px -6% 0px' });
      els.forEach(function (el) { if (!el.__revealed) io.observe(el); });
      document.querySelectorAll('[data-countup]').forEach(function (el) {
        if (!el.closest('[data-reveal]') && !el.__counted) io.observe(el);
      });
    }
    // (c) Scroll-Fallback (falls IO im Renderer stumm bleibt)
    var onScroll = function () { els.forEach(function (el) { if (!el.__revealed && _inView(el)) reveal(el); }); };
    window.addEventListener('scroll', onScroll, { passive: true });
    // (d) Failsafe: nach 1.6s alles zeigen -> nie stuck-hidden
    setTimeout(function () { els.forEach(reveal); }, 1600);
  }

  // ------------------------------------------------------------
  // 2. Count-Up — animiert Ganzzahl-Anteil, behaelt Suffix/Prefix
  // ------------------------------------------------------------
  function countUp(el) {
    if (REDUCED || el.__counted) return;
    el.__counted = true;
    var raw = (el.getAttribute('data-countup') || el.textContent || '').trim();
    var m = raw.match(/-?\d[\d.,]*/);
    if (!m) return;
    // Dezimal-Fix (2026-07-14): nur DE-Format (mit Komma) hat '.' als Tausender-Trenner;
    // sonst ist '.' der Dezimalpunkt (31.8 darf NICHT zu 318 werden).
    var target = m[0].indexOf(',') >= 0
      ? parseFloat(m[0].replace(/\./g, '').replace(',', '.'))
      : parseFloat(m[0]);
    if (!isFinite(target)) return;
    var suffix = raw.slice(m.index + m[0].length);
    var prefix = raw.slice(0, m.index);
    var dur = 900, t0 = null;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var p = Math.min((ts - t0) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      var val = Math.round(target * eased);
      el.textContent = prefix + val + suffix;
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = prefix + (Number.isInteger(target) ? target : m[0]) + suffix;
    }
    requestAnimationFrame(step);
  }

  // ------------------------------------------------------------
  // 3. Spotlight-Cards (delegiert, ein Listener)
  // ------------------------------------------------------------
  function initSpotlight() {
    if (REDUCED) return;
    document.addEventListener('pointermove', function (e) {
      var card = e.target.closest && e.target.closest('.appcont .card');
      if (!card) return;
      var r = card.getBoundingClientRect();
      card.style.setProperty('--mx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
      card.style.setProperty('--my', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
    }, { passive: true });
  }

  // ------------------------------------------------------------
  // 4. Magnetic-Buttons (.magnetic) — transform-only via rAF
  // ------------------------------------------------------------
  function initMagnetic() {
    if (REDUCED) return;
    var STRENGTH = 0.28, MAX = 7;
    document.querySelectorAll('.magnetic').forEach(function (btn) {
      var raf = null;
      btn.addEventListener('pointermove', function (e) {
        var r = btn.getBoundingClientRect();
        var dx = (e.clientX - (r.left + r.width / 2)) * STRENGTH;
        var dy = (e.clientY - (r.top + r.height / 2)) * STRENGTH;
        dx = Math.max(-MAX, Math.min(MAX, dx));
        dy = Math.max(-MAX, Math.min(MAX, dy));
        if (raf) cancelAnimationFrame(raf);
        raf = requestAnimationFrame(function () {
          btn.style.setProperty('--tx', dx.toFixed(1) + 'px');
          btn.style.setProperty('--ty', dy.toFixed(1) + 'px');
        });
      });
      btn.addEventListener('pointerleave', function () {
        btn.style.setProperty('--tx', '0px');
        btn.style.setProperty('--ty', '0px');
      });
    });
  }

  function boot() { initReveal(); initSpotlight(); initMagnetic(); }  /* Spotlight wieder aktiv, aber dezent (Marco 2026-07-14) */
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  // Nach HTMX-Swaps: neue [data-reveal]/[data-countup]/.magnetic aktivieren
  document.addEventListener('htmx:afterSwap', function () { initReveal(); initMagnetic(); });
})();
