/* Shramko Analytics v1.1-patrol (2026-10-21) — GA4 per WEB-TECH-BASELINE §T6.
 * Based on the canonical module in Andrii Shramko's vault
 *   (03 - Resources/Deployment/analytics/shramko-analytics.js), adapted for patrol.flyreelstudio.eu.
 *
 * Contract (docs/analytics.md):
 *  - Nothing is requested from Google until the visitor clicks Accept (Consent Mode BASIC, never advanced).
 *    gtag.js is injected only then; `config` runs after consent, so the page_view is GRANTED (gcs=G1x1).
 *  - Exactly one page_view per page load (the automatic one from `config`); SPA/history page views are
 *    left to GA4 Enhanced Measurement "page changes" — no manual hook here.
 *  - Reject / withdraw: ga-disable-<ID>=true, consent update denied, _ga* cookies removed for the host and
 *    its parent domains, choice stored (localStorage `sa_consent`), no further hits.
 *  - PII-free, low-cardinality parameters only. Never strings in `value`; no anonymize_ip / page_path.
 *
 * Markup used by this module:
 *   <script defer src="/shramko-analytics.js" data-ga="G-..." data-skip-if="bp_admin"></script>
 *   #sa-banner (hidden) with buttons data-sa-consent="accept" | "reject"; footer link data-sa-consent="settings".
 *   data-cta="<id>" on CTAs, section[id] / [data-sa-section] for section_view, data-sa-form="<id>" on forms,
 *   data-sa-video="<id>" on user-started videos, data-lang on language links, data-copy on copy-link buttons.
 * Site code calls ShramkoAnalytics.event(name, params) and ShramkoAnalytics.lead(formId, params).
 */
(function () {
  'use strict';
  var KEY = 'sa_consent';                                // 'granted' | 'denied'  (localStorage, not a cookie)
  var script = document.currentScript;
  var GA = (script && script.getAttribute('data-ga')) || '';
  var SKIP_KEY = (script && script.getAttribute('data-skip-if')) || '';
  var COOKIE_LIFE = 400 * 24 * 3600;                     // 400 days (Chrome's cap); GA data retention: 14 months
  var loaded = false, seenSections = {}, steps = {}, errors = 0, io = null;

  function store(v) { try { localStorage.setItem(KEY, v); } catch (_) {} }
  function stored() { try { return localStorage.getItem(KEY); } catch (_) { return null; } }
  function lang() { return (document.documentElement.lang || 'und').slice(0, 5).toLowerCase(); }
  function hostOf(u) { try { return new URL(u).hostname; } catch (_) { return ''; } }
  // never track: admin sessions (token in URL or stored), or a page that is redirecting away right now
  function skipped() {
    if (window.__saRedirect) return true;
    if (/[?&#]admin=/.test(location.href)) return true;
    try { if (SKIP_KEY && localStorage.getItem(SKIP_KEY)) return true; } catch (_) {}
    return false;
  }
  function active() { return loaded && stored() === 'granted' && !skipped(); }

  var banner = null;
  function showBanner(on, focus) {
    banner = banner || document.getElementById('sa-banner');
    if (!banner) return;
    banner.hidden = !on;
    if (on && focus) { var b = banner.querySelector('[data-sa-consent]'); if (b && b.focus) try { b.focus({ preventScroll: true }); } catch (_) {} }
  }

  function wipeCookies() {
    var labels = location.hostname.split('.');
    document.cookie.split(';').forEach(function (c) {
      var n = c.split('=')[0].trim();
      if (!/^_ga/.test(n)) return;
      var exp = n + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
      document.cookie = exp;                                                   // host-only cookie
      for (var i = 0; i <= labels.length - 2; i++) document.cookie = exp + '; domain=.' + labels.slice(i).join('.');
    });
  }

  function config() {
    var c = { site_lang: lang(), cookie_domain: location.hostname, cookie_flags: 'SameSite=Lax;Secure', cookie_expires: COOKIE_LIFE };
    // the root page may auto-redirect to /pl/ etc. before this runs: it saves the external referrer so the
    // session is still attributed to Google / LinkedIn instead of "self-referral"
    try {
      var ref = sessionStorage.getItem('sa_ref');
      if (ref !== null) { sessionStorage.removeItem('sa_ref'); if (ref && hostOf(ref) !== location.hostname) c.page_referrer = ref; }
    } catch (_) {}
    return c;
  }

  function load() {
    if (!GA || skipped()) return;
    window['ga-disable-' + GA] = false;
    if (loaded) {                                        // re-accept on the same page after a withdrawal
      gtag('consent', 'update', { analytics_storage: 'granted' });
      gtag('config', GA, config());
      reobserve();
      return;
    }
    loaded = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
    gtag('consent', 'default', { ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'granted' });
    gtag('js', new Date());
    gtag('set', { allow_google_signals: false, allow_ad_personalization_signals: false });
    gtag('config', GA, config());                        // sends the GRANTED page_view of this page (only one)
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(GA);
    document.head.appendChild(s);
    reobserve();                                         // sections already on screen at Accept get their section_view
  }

  function event(name, params) {
    if (!active()) return;                               // nothing before consent / after withdrawal
    var p = {};
    for (var k in (params || {})) if (Object.prototype.hasOwnProperty.call(params, k) && params[k] != null) p[k] = params[k];
    if (!p.site_lang) p.site_lang = lang();
    gtag('event', name, p);
  }

  function label(el) { return (el.getAttribute('data-cta') || el.id || (el.textContent || '').trim().slice(0, 40) || el.tagName).toLowerCase(); }
  function where(el) {
    if (el.closest('#lightbox')) return 'lightbox';
    var s = el.closest('section[id],[data-sa-section],header,footer,nav,#sa-banner');
    return s ? (s.id || s.getAttribute('data-sa-section') || s.tagName.toLowerCase()) : 'page';
  }
  function network(href) {
    return /linkedin\.com\/shar/i.test(href) ? 'linkedin' : /facebook\.com\/shar/i.test(href) ? 'facebook' :
      /(twitter|x)\.com\/intent/i.test(href) ? 'x' : /wa\.me\/\?|api\.whatsapp\.com\/send\?text/i.test(href) ? 'whatsapp' : '';
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-sa-consent],[data-copy],a[href],[data-cta]');
    if (!t) return;
    var c = t.getAttribute('data-sa-consent');
    if (c) {
      e.preventDefault();
      if (c === 'accept') api.accept(); else if (c === 'reject' || c === 'withdraw') api.reject(); else api.settings();
      return;
    }
    if (t.hasAttribute('data-copy')) return event('share', { network: 'copy', cta_location: where(t) });
    var href = t.getAttribute('href') || '';
    if (t.hasAttribute('data-lang')) {
      var to = t.getAttribute('data-lang').slice(0, 5).toLowerCase();
      if (to !== lang()) event('lang_switch', { lang_to: to, transport_type: 'beacon' });   // navigation follows at once
      return;
    }
    if (/^(tel:|mailto:)|wa\.me\/\d|api\.whatsapp\.com\/send\?phone|t\.me\/|m\.me\/|messenger\.com/i.test(href)) {
      return event('contact_click', { method: /^tel:/i.test(href) ? 'phone' : /^mailto:/i.test(href) ? 'email' : /t\.me/i.test(href) ? 'telegram' : /m\.me|messenger/i.test(href) ? 'messenger' : 'whatsapp', cta_location: where(t) });
    }
    var net = network(href);
    if (net) return event('share', { network: net, cta_location: where(t) });
    if (/\.(pdf|zip|csv|xlsx?|json|mp4|apk|exe|stl|3mf|ply|sog)(\?|#|$)/i.test(href)) {
      return event('file_download_click', { file_ext: href.split(/[?#]/)[0].split('.').pop().toLowerCase(), cta_location: where(t) });
    }
    if (t.hasAttribute('data-cta') || /(^|\s)btn(\s|$)/.test(t.className || '')) {
      event('cta_click', { cta_id: label(t), cta_location: where(t) });
    }
  }, true);

  // form_start comes from GA4 Enhanced Measurement (form interactions ON in the stream; form_id = the form's
  // id) — sending it here too would double-count. EM does NOT send form_submit for forms whose submit handler
  // calls preventDefault() (fetch-based forms like the lead form; verified 2026-10-21), so it is sent here.
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f && f.hasAttribute && f.hasAttribute('data-sa-form')) event('form_submit', { form_id: f.getAttribute('data-sa-form') });
  }, true);
  // Field progress (field NAME only, never the value):
  document.addEventListener('change', function (e) {
    var el = e.target, f = el && el.form;
    if (!f || !el.name || !f.hasAttribute('data-sa-form') || el.type === 'hidden' || /^(website|hp)$/.test(el.name)) return;
    var id = f.getAttribute('data-sa-form'), k = id + ':' + el.name;
    if (steps[k] || !active()) return;
    steps[k] = 1;
    event('form_step', { form_id: id, step: el.name.slice(0, 30) });
  }, true);

  function onSection(entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting || !active()) return;
      // tall sections never reach a 0.5 ratio: also count "covers half of the viewport"
      if (en.intersectionRatio < 0.5 && en.intersectionRect.height < 0.5 * (window.innerHeight || 800)) return;
      var id = en.target.id || en.target.getAttribute('data-sa-section');
      if (!id || seenSections[id]) return;
      seenSections[id] = 1;
      event('section_view', { section_id: id.slice(0, 40) });
      if (io) io.unobserve(en.target);
    });
  }
  function reobserve() {
    if (!('IntersectionObserver' in window)) return;
    if (io) io.disconnect();
    io = new IntersectionObserver(onSection, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    document.querySelectorAll('section[id],[data-sa-section]').forEach(function (s) { io.observe(s); });
  }

  // videos (also ones created later, e.g. the event clip in the lightbox). Muted autoplay loops count only
  // when the page marks them as user-started with data-sa-video.
  function videoId(v) { return (v.getAttribute('data-sa-video') || (v.currentSrc || v.src || 'video').split('/').pop().split('?')[0]).slice(0, 40); }
  function counts(v) { return v.hasAttribute('data-sa-video') || !(v.autoplay && v.muted); }
  document.addEventListener('play', function (e) {
    var v = e.target;
    if (!v || v.tagName !== 'VIDEO' || v.__saPlay || !counts(v)) return;
    v.__saPlay = 1;
    event('video_play', { video_id: videoId(v) });
  }, true);
  function done(v) { if (v.__saDone || !v.__saPlay) return; v.__saDone = 1; event('video_complete', { video_id: videoId(v) }); }
  document.addEventListener('ended', function (e) { if (e.target && e.target.tagName === 'VIDEO') done(e.target); }, true);
  document.addEventListener('timeupdate', function (e) {                 // looping clips never fire "ended"
    var v = e.target;
    if (!v || v.tagName !== 'VIDEO' || !v.loop || !v.__saPlay) return;
    if (v.__saLast > 0 && v.duration && v.currentTime + 0.5 < v.__saLast && v.__saLast > v.duration * 0.8) done(v);
    v.__saLast = v.currentTime;
  }, true);

  // the site's own JS errors only (same-origin scripts), short and URL-free, max 5 per page
  function exception(msg) {
    if (errors >= 5) return;
    errors++;
    event('exception', { description: String(msg || 'error').replace(/https?:\/\/\S+/g, '').replace(/["'`]/g, '').slice(0, 100), fatal: false });
  }
  window.addEventListener('error', function (e) {
    if (!e || !e.message) return;
    if (e.filename && hostOf(e.filename) !== location.hostname) return;
    exception(e.message);
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    exception('unhandled: ' + (r && r.message ? r.message : String(r)));
  });

  var api = {
    accept: function () { store('granted'); showBanner(false); load(); event('consent_accept', {}); },
    reject: function () {                                // = withdraw when consent was given before
      store('denied');
      if (GA) window['ga-disable-' + GA] = true;          // first: blocks every further hit, incl. queued ones
      if (loaded) gtag('consent', 'update', { analytics_storage: 'denied' });
      wipeCookies();
      showBanner(false);
    },
    settings: function () { showBanner(true, true); },
    status: function () { return stored(); },
    event: event,
    lead: function (formId, extra) { var p = extra || {}; p.form_id = formId || 'form'; event('generate_lead', p); },
  };
  window.ShramkoAnalytics = api;

  function ready() {
    if (skipped()) { showBanner(false); return; }
    var s = stored();
    if (s === 'granted') load();
    else {
      if (GA) window['ga-disable-' + GA] = true;
      if (s !== 'denied') showBanner(true);
    }
    reobserve();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
})();
