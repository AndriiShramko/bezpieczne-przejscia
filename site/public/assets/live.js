/* Live command-center v2: real-time counters, AI-verdict event tabs with video
   clips, fullscreen gallery, charts, crowd verification. Talks to /cv/*. */
(function () {
  var L = (["en", "pl", "es", "ru"].indexOf(document.documentElement.lang) >= 0)
    ? document.documentElement.lang : "en";
  var T = {
    pl: {
      live: "NA ŻYWO", off: "OFFLINE", perHour: "/godz.",
      ped: "pieszych", veh: "pojazdów", bike: "rowerów", inFrame: "w kadrze",
      q: "Czy to realne naruszenie?", yes: "✓ Naruszenie", no: "✗ Fałszywy alarm",
      noev: "Brak zdarzeń w tej zakładce — system nagrywa epizody, gdy pojazd W RUCHU spotyka pieszego w strefie przejścia.",
      conf: "głosów „naruszenie”", ref: "głosów „fałszywy alarm”",
      aiViol: "AI: NARUSZENIE", aiNo: "AI: brak naruszenia", aiUnc: "AI: niepewne",
      aiWait: "czeka na analizę AI", more: "więcej", less: "zwiń",
      tabs: { violation: "Naruszenia wg AI", rejected: "Odrzucone przez AI — sprawdź!", pending: "Czekają na AI", all: "Wszystkie" },
      tl: "sygnalizacja", vmax: "max prędkość", epi: "● nagrywam epizod…",
      fsNote: "Klip celowo w niskiej rozdzielczości i klatkażu — oszczędzamy zasoby serwera demo. Produkcyjnie: pełne FPS/rozdzielczość.",
      aiToday: "analiz AI dziś",
      share: "Udostępnij:", copy: "Kopiuj link", trash: "do kosza",
    },
    en: {
      live: "LIVE", off: "OFFLINE", perHour: "/h",
      ped: "pedestrians", veh: "vehicles", bike: "bikes", inFrame: "in frame",
      q: "Is this a real violation?", yes: "✓ Violation", no: "✗ False alarm",
      noev: "No events in this tab — the system records an episode when a MOVING vehicle meets a pedestrian in the crossing zone.",
      conf: "votes “violation”", ref: "votes “false alarm”",
      aiViol: "AI: VIOLATION", aiNo: "AI: no violation", aiUnc: "AI: uncertain",
      aiWait: "awaiting AI analysis", more: "more", less: "less",
      tabs: { violation: "AI violations", rejected: "AI-rejected — double-check!", pending: "Awaiting AI", all: "All" },
      tl: "signals", vmax: "max speed", epi: "● recording episode…",
      fsNote: "Clips are intentionally low-res/low-fps to save demo server resources. Production: full FPS/resolution.",
      aiToday: "AI analyses today",
      share: "Share:", copy: "Copy link", trash: "trash",
    },
  }[L];

  var voted = {};
  try { voted = JSON.parse(localStorage.getItem("bp_voted2") || "{}"); } catch (e) {}
  var curTab = "all", curHour = null, events = [], lastSig = "";
  var SHARE = "https://patrol.flyreelstudio.eu/cv/share/";
  // admin mode: sign in ONCE in the /admin panel — the token is stored in this
  // browser (localStorage) and the main page picks it up automatically, so the
  // "move to trash" buttons appear here without any URL tricks. A URL token
  // (?admin= / #admin=) still works as a fallback and is scrubbed from the URL.
  var ADMIN = "";
  try {
    var qa = ((location.search + "&" + location.hash).match(/[?&#]admin=([^&#]+)/) || [])[1];
    if (qa) {
      ADMIN = decodeURIComponent(qa);
      localStorage.setItem("bp_admin", ADMIN);
      history.replaceState({}, "", location.pathname + (location.hash.replace(/[?&#]?admin=[^&#]+/, "") || "#live"));
    } else {
      ADMIN = localStorage.getItem("bp_admin") || "";
    }
  } catch (e) {}

  function el(i) { return document.getElementById(i); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function num(n) { return (n == null) ? "—" : Number(n).toLocaleString(L === "pl" ? "pl-PL" : "en-US"); }

  /* ---------- live state ---------- */
  function tick() {
    fetch("/cv/state.json", { cache: "no-store" }).then(function (r) { return r.json(); })
      .then(render).catch(function () { setLive(false); });
  }
  function setLive(on) {
    var b = el("live-badge");
    if (b) { b.textContent = on ? "● " + T.live : "○ " + T.off; b.className = "live-badge " + (on ? "on" : "off"); }
    var o = el("live-offline"); if (o) o.style.display = on ? "none" : "flex";
  }
  function setStat(id, big, sub) {
    var e = el(id); if (!e) return;
    e.querySelector(".big").textContent = big;
    var s = e.querySelector(".sub"); if (s) s.textContent = sub || "";
  }
  // stat cards are clickable filters: events card -> full event list
  (function () {
    var map = { "st-ev": "all", "st-acc": "all" };
    Object.keys(map).forEach(function (id) {
      var e = el(id); if (!e) return;
      e.style.cursor = "pointer";
      e.title = L === "pl" ? "Kliknij, aby zobaczyć wszystkie nagrania zdarzeń" : "Click to see all event recordings";
      e.addEventListener("click", function () {
        curTab = map[id]; curHour = null;
        document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("cur", x.getAttribute("data-tab") === curTab); });
        lastSig = ""; loadEvents();
        var evs = el("events"); if (evs) evs.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      e.addEventListener("mouseenter", function () { e.style.outline = "1px solid #37b6ff"; });
      e.addEventListener("mouseleave", function () { e.style.outline = "none"; });
    });
  })();
  function render(d) {
    setLive(d.live);
    setStat("st-ped", num(d.ped_total), num(d.ped_per_hour) + " " + T.perHour);
    setStat("st-veh", num(d.veh_total), num(d.veh_per_hour) + " " + T.perHour);
    setStat("st-ev", num(d.stats.events_total),
      num(d.stats.ai_violations) + " " + (L === "pl" ? "naruszeń wg AI" : "AI violations"));
    var ag = d.stats.ai_human_agreement_pct;
    setStat("st-acc", ag == null ? "—" : ag + "%",
      num(d.stats.human_judged) + (L === "pl" ? " ocen ludzi" : " human ratings"));
    var inf = el("st-inframe");
    if (inf) {
      var bits = [d.in_frame.ped + " " + T.ped + " · " + (d.in_frame.bike || 0) + " " + T.bike + " · " + d.in_frame.veh + " " + T.veh + " " + T.inFrame];
      if (d.bike_total != null) bits.push(num(d.bike_total) + " " + T.bike + " (" + num(d.bike_per_hour) + T.perHour + ")");
      if (d.speeds && d.speeds.veh_kmh) bits.push(T.vmax + " ~" + d.speeds.veh_kmh + " km/h");
      var tls = Object.keys(d.tl || {}).map(function (k) { return d.tl[k]; }).filter(function (v) { return v !== "unknown"; });
      if (tls.length) bits.push(T.tl + ": " + tls.join("/"));
      bits.push(T.aiToday + ": " + d.ai_calls_today);
      if (d.stats) {
        bits.push((L === "pl" ? "zdarzenia — piesi: " : "events — pedestrians: ") + (d.stats.cat_ped || 0) +
          (L === "pl" ? " · rowerzyści: " : " · cyclists: ") + (d.stats.cat_bike || 0) +
          (L === "pl" ? " · dzieci/wózki: " : " · children/prams: ") + (d.stats.cat_child || 0) +
          (L === "pl" ? " · prędkość: " : " · speeding: ") + (d.stats.cat_speeding || 0));
      }
      inf.textContent = bits.join("  ·  ");
    }
    var src = el("live-source");
    if (src && d.source) {
      // camera name links to the ORIGINAL public stream page (source credit)
      if (d.source_url) src.innerHTML = '<a href="' + esc(d.source_url) + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline dotted">' + esc(d.source) + " ↗</a>";
      else src.textContent = d.source;
    }
    // playlist countdown: when does the camera change and to what
    var pc = el("pl-countdown");
    if (!pc && d.playlist) {
      pc = document.createElement("div");
      pc.id = "pl-countdown";
      pc.className = "pl-timer";
      if (src && src.parentNode) src.parentNode.insertBefore(pc, src.nextSibling);
    }
    if (pc) {
      if (d.playlist) {
        window._plNext = { t: Date.now() + d.playlist.next_in_s * 1000, label: d.playlist.next_label };
      } else { window._plNext = null; pc.textContent = ""; }
    }
    var epi = el("epi-badge"); if (epi) epi.style.display = d.episode_active ? "inline-block" : "none";
    var tk = el("ticker");
    if (tk && d.ticker) tk.innerHTML = d.ticker.slice(0, 5).map(function (t) { return '<span class="tick">' + esc(t) + "</span>"; }).join("");
  }

  /* ---------- events ---------- */
  function loadEvents() {
    fetch("/cv/events.json?tab=" + curTab + (curHour ? "&hour=" + encodeURIComponent(curHour) : ""), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { events = d.events || []; renderEvents(); })
      .catch(function () {});
  }
  function aiBadge(e) {
    if (e.ai_verdict === "violation") return '<span class="badge viol">' + T.aiViol + " · " + Math.round((e.ai_conf || 0) * 100) + "%</span>";
    if (e.ai_verdict === "no_violation") return '<span class="badge ok">' + T.aiNo + "</span>";
    if (e.ai_verdict === "uncertain") return '<span class="badge unc">' + T.aiUnc + "</span>";
    return '<span class="badge wait">' + T.aiWait + "</span>";
  }
  function renderEvents() {
    var box = el("events"); if (!box) return;
    var sig = curTab + "|" + (curHour || "") + "|" + events.map(function (e) {
      return e.id + ":" + e.status + ":" + (e.ai_verdict || "") + ":" + e.confirm + ":" + e.refute + ":" + (voted[e.id] || "");
    }).join(",");
    if (sig === lastSig) return;
    lastSig = sig;
    if (!events.length) { box.innerHTML = '<p class="muted pad">' + T.noev + "</p>"; return; }
    box.innerHTML = events.map(function (e, i) {
      var expl = L === "pl" ? e.ai_pl : e.ai_en;
      var v = voted[e.id];
      return '<figure class="ev" data-i="' + i + '">' +
        '<div class="ev-media" data-open="' + i + '">' +
        (e.clip ? '<video preload="none" muted playsinline poster="/cv/snap/' + esc(e.snap) + '" src="/cv/clip/' + esc(e.clip) + '"></video><span class="play">▶</span>'
                : '<img loading="lazy" src="/cv/snap/' + esc(e.snap) + '" alt="">') +
        '</div><figcaption>' +
        '<div class="ev-top">#' + e.id + " · " + esc((e.ts || "").replace("T", " ").slice(0, 16)) +
        " · " + (e.dur != null ? e.dur : "?") + "s · ~" + (e.kmh != null ? e.kmh : "?") + " km/h " +
        (e.kind === "speeding" ? '<span class="ai-badge" style="background:#4d3800;color:#ffcf5c">⚡ ' + (L === "pl" ? "prędkość (szac.)" : "speed (est.)") + "</span> " : "") +
        aiBadge(e) + "</div>" +
        (expl ? '<div class="ev-ai">' + esc(expl) + "</div>" : '<div class="ev-ai muted">' + esc(e.desc || "") + "</div>") +
        '<div class="ev-q">' + T.q + "</div>" +
        '<div class="ev-actions">' + vbtn(e.id, "violation", T.yes, v) + vbtn(e.id, "false_alarm", T.no, v) + "</div>" +
        '<div class="ev-tally">' + e.confirm + " " + T.conf + " · " + e.refute + " " + T.ref + "</div>" +
        '<div class="ev-share">' + T.share + " " +
          '<a target="_blank" rel="noopener" href="https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(SHARE + e.id) + '">LinkedIn</a> ' +
          '<a target="_blank" rel="noopener" href="https://twitter.com/intent/tweet?url=' + encodeURIComponent(SHARE + e.id) + '">X</a> ' +
          '<a href="#" data-copy="' + SHARE + e.id + '">' + T.copy + "</a>" +
          (ADMIN ? ' · <a href="#" class="ev-trash" data-trash="' + e.id + '" style="color:#ff6b6b">🗑 ' + (curTab === "trash" ? (L === "pl" ? "przywróć" : "restore") : T.trash) + "</a>" : "") +
        "</div>" +
        "</figcaption></figure>";
    }).join("");
  }
  function vbtn(id, verdict, label, votedV) {
    var done = votedV != null, mine = votedV === verdict;
    return '<button class="vbtn ' + (verdict === "violation" ? "confirm" : "refute") + (mine ? " mine" : "") + '"' +
      (done ? " disabled" : "") + ' data-id="' + id + '" data-v="' + verdict + '">' + label + "</button>";
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest(".vbtn");
    if (b && !b.disabled) {
      var id = b.getAttribute("data-id"), verdict = b.getAttribute("data-v");
      b.disabled = true;
      // lock BOTH buttons of this event everywhere (cards + lightbox) at once
      document.querySelectorAll('.vbtn[data-id="' + id + '"]').forEach(function (x) { x.disabled = true; });
      fetch("/cv/api/verify", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(id), verdict: verdict }) })
        .then(function (r) {
          if (r.ok) { voted[id] = verdict; try { localStorage.setItem("bp_voted2", JSON.stringify(voted)); } catch (e) {} lastSig = ""; loadEvents(); if (typeof boxIdx !== "undefined" && boxIdx >= 0 && typeof renderBox === "function") renderBox(); }
          else document.querySelectorAll('.vbtn[data-id="' + id + '"]').forEach(function (x) { x.disabled = false; });
        }).catch(function () { document.querySelectorAll('.vbtn[data-id="' + id + '"]').forEach(function (x) { x.disabled = false; }); });
      return;
    }
    var tb = ev.target.closest && ev.target.closest(".tab");
    if (tb) {
      curTab = tb.getAttribute("data-tab");
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("cur", x === tb); });
      lastSig = ""; loadEvents();
      return;
    }
    var cp = ev.target.closest && ev.target.closest("[data-copy]");
    if (cp) {
      ev.preventDefault();
      navigator.clipboard.writeText(cp.getAttribute("data-copy")).then(function () {
        var o = cp.textContent; cp.textContent = "✓"; setTimeout(function () { cp.textContent = o; }, 1500);
      });
      return;
    }
    var tr = ev.target.closest && ev.target.closest("[data-trash]");
    if (tr && ADMIN) {
      ev.preventDefault();
      var restore = curTab === "trash";
      fetch("/cv/admin/event", { method: "POST", headers: { "Content-Type": "application/json", "X-Admin-Token": ADMIN },
        body: JSON.stringify({ id: Number(tr.getAttribute("data-trash")), restore: restore }) })
        .then(function (r) { if (r.ok) { lastSig = ""; loadEvents(); } else if (r.status === 403) { alert("Admin token invalid"); } });
      return;
    }
    var m = ev.target.closest && ev.target.closest("[data-open]");
    if (m) { openBox(Number(m.getAttribute("data-open"))); return; }
  });

  /* ---------- fullscreen gallery ---------- */
  var boxIdx = -1;
  function openBox(i) {
    boxIdx = i;
    var bx = el("lightbox");
    if (!bx) return;
    bx.style.display = "flex";
    renderBox();
    if (bx.requestFullscreen) bx.requestFullscreen().catch(function () {});
  }
  function closeBox() {
    var bx = el("lightbox");
    bx.style.display = "none";
    var v = bx.querySelector("video"); if (v) v.pause();
    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
    boxIdx = -1;
  }
  function renderBox() {
    var e = events[boxIdx]; if (!e) return;
    var bx = el("lightbox");
    var expl = (L === "pl" ? e.ai_pl : e.ai_en) || e.desc || "";
    bx.querySelector(".lb-stage").innerHTML = e.clip
      ? '<video controls autoplay loop muted playsinline src="/cv/clip/' + esc(e.clip) + '" poster="/cv/snap/' + esc(e.snap) + '"></video>'
      : '<img src="/cv/snap/' + esc(e.snap) + '" alt="">';
    bx.querySelector(".lb-info").innerHTML =
      "<b>#" + e.id + "</b> · " + esc((e.ts || "").replace("T", " ").slice(0, 19)) + " UTC · " +
      (e.dur != null ? e.dur : "?") + "s · ~" + (e.kmh != null ? e.kmh : "?") + " km/h · " + T.tl + ": " + esc(e.tl || "?") + "<br>" +
      aiBadge(e) + " " + esc(expl) +
      '<div class="ev-actions" style="max-width:420px;margin-top:.5rem">' +
      vbtn(e.id, "violation", T.yes, voted[e.id]) + vbtn(e.id, "false_alarm", T.no, voted[e.id]) + "</div>" +
      '<div class="lb-share">' + T.share +
        ' <a class="li" target="_blank" rel="noopener" href="https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(SHARE + e.id) + '">in LinkedIn</a>' +
        ' <a class="fb" target="_blank" rel="noopener" href="https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(SHARE + e.id) + '">Facebook</a>' +
        ' <a class="tw" target="_blank" rel="noopener" href="https://twitter.com/intent/tweet?url=' + encodeURIComponent(SHARE + e.id) + '">X</a>' +
        ' <a class="cp" href="#" data-copy="' + SHARE + e.id + '">📋 ' + T.copy + "</a>" +
        (ADMIN ? ' <a class="tr" href="#" data-trash="' + e.id + '">🗑 ' + (curTab === "trash" ? (L === "pl" ? "przywróć" : "restore") : T.trash) + "</a>" : "") +
      "</div>" +
      '<div class="muted small" style="margin-top:.4rem">' + T.fsNote + " (" + (boxIdx + 1) + "/" + events.length + ")</div>";
  }
  function nav(d) { if (boxIdx < 0) return; boxIdx = (boxIdx + d + events.length) % events.length; renderBox(); }
  document.addEventListener("keydown", function (e) {
    if (boxIdx < 0) return;
    if (e.key === "ArrowRight") nav(1);
    else if (e.key === "ArrowLeft") nav(-1);
    else if (e.key === "Escape") closeBox();
  });
  var lb = el("lightbox");
  if (lb) {
    lb.querySelector(".lb-prev").addEventListener("click", function () { nav(-1); });
    lb.querySelector(".lb-next").addEventListener("click", function () { nav(1); });
    lb.querySelector(".lb-close").addEventListener("click", closeBox);
  }

  /* ---------- charts (inline SVG, no libs) ---------- */
  function drawCharts() {
    fetch("/cv/charts.json", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      lineChart("chart-traffic", d.hourly || []);
      histChart("chart-speed", d.speed_hist_bins_kmh5 || [], d.speed_n || 0);
      eventChart("chart-events", d.hourly || []);
      todChart("chart-tod", d.speeding_by_hour || []);
      // animate bars/lines only on the FIRST paint of each chart (no jitter
      // on the periodic refresh)
      ["chart-traffic", "chart-speed", "chart-events", "chart-tod"].forEach(function (id) {
        var e = el(id); if (!e || e.dataset.animated) return;
        var svg = e.querySelector("svg"); if (svg) { svg.classList.add("anim"); e.dataset.animated = "1"; }
      });
    }).catch(function () {});
  }
  function svgEl(w, h) { return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" style="width:100%;height:150px;display:block">'; }
  function lineChart(id, hourly) {
    var e = el(id); if (!e) return;
    var W = 600, H = 150, n = hourly.length;
    if (!n) { e.innerHTML = '<p class="muted small pad">…</p>'; return; }
    var mv = Math.max.apply(null, hourly.map(function (x) { return x.veh; }).concat([1]));
    var mp = Math.max.apply(null, hourly.map(function (x) { return x.ped; }).concat([1]));
    function pts(key, m) {
      return hourly.map(function (x, i) {
        return (i * W / Math.max(1, n - 1)).toFixed(1) + "," + (H - 12 - (H - 24) * x[key] / m).toFixed(1);
      }).join(" ");
    }
    // a fresh database has 1-2 hourly points — a polyline is invisible then,
    // so draw dots as well
    function dots(key, m, col) {
      if (n > 3) return "";
      return hourly.map(function (x, i) {
        return '<circle cx="' + (i * W / Math.max(1, n - 1)).toFixed(1) + '" cy="' +
          (H - 12 - (H - 24) * x[key] / m).toFixed(1) + '" r="4" fill="' + col + '"/>';
      }).join("");
    }
    var mb = Math.max.apply(null, hourly.map(function (x) { return x.bike || 0; }).concat([1]));
    // invisible hover strips with native tooltips (hour + values)
    var bw = W / Math.max(1, n);
    var hov = hourly.map(function (x, i) {
      var tip = (x.h || "").slice(11, 16) + "  " + x.veh + " " + T.veh + ", " + x.ped + " " + T.ped + ", " + (x.bike || 0) + " " + T.bike;
      return '<rect x="' + (i * bw).toFixed(1) + '" y="0" width="' + bw.toFixed(1) + '" height="' + H + '" fill="transparent"><title>' + tip + "</title></rect>";
    }).join("");
    e.innerHTML = svgEl(W, H) +
      '<polyline fill="none" stroke="#37b6ff" stroke-width="2" points="' + pts("veh", mv) + '"/>' +
      '<polyline fill="none" stroke="#2ee6a6" stroke-width="2" points="' + pts("ped", mp) + '"/>' +
      '<polyline fill="none" stroke="#ffcf5c" stroke-width="1.5" points="' + hourly.map(function (x, i) {
        return (i * W / Math.max(1, n - 1)).toFixed(1) + "," + (H - 12 - (H - 24) * (x.bike || 0) / mb).toFixed(1);
      }).join(" ") + '"/>' + dots("veh", mv, "#37b6ff") + dots("ped", mp, "#2ee6a6") + hov +
      "</svg>" +
      '<div class="legend"><i style="background:#37b6ff"></i>' + T.veh + " (max " + mv + "/h)" +
      '<i style="background:#2ee6a6"></i>' + T.ped + " (max " + mp + "/h)" +
      '<i style="background:#ffcf5c"></i>' + T.bike + "</div>";
  }
  function histChart(id, bins, total) {
    var e = el(id); if (!e) return;
    var W = 600, H = 150, m = Math.max.apply(null, bins.concat([1]));
    var bw = W / bins.length;
    var bars = bins.map(function (v, i) {
      var bh = (H - 26) * v / m;
      return '<rect x="' + (i * bw + 2).toFixed(1) + '" y="' + (H - 14 - bh).toFixed(1) +
        '" width="' + (bw - 4).toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="2" fill="#ffcf5c"><title>' +
        (i * 5) + "-" + (i * 5 + 5) + " km/h: " + v + "</title></rect>" +
        (i % 2 === 0 ? '<text x="' + (i * bw + bw / 2).toFixed(1) + '" y="' + (H - 2) + '" font-size="9" fill="#8b97a7" text-anchor="middle">' + i * 5 + "</text>" : "");
    }).join("");
    e.innerHTML = svgEl(W, H) + bars + "</svg>" +
      '<div class="legend"><i style="background:#ffcf5c"></i>km/h (' + total + (L === "pl" ? " pomiarów, orientacyjnie — kliknij, by zobaczyć przekroczenia" : " samples, indicative — click to see speeding events") + ")</div>";
    e.style.cursor = "pointer";
    e.title = L === "pl" ? "Kliknij: nagrania możliwych przekroczeń prędkości" : "Click: recordings of possible speeding";
    e.onclick = function () {
      curTab = "speeding"; curHour = null;
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("cur", x.getAttribute("data-tab") === "speeding"); });
      lastSig = ""; loadEvents();
      var evs = el("events"); if (evs) evs.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }
  function eventChart(id, hourly) {
    var e = el(id); if (!e) return;
    var W = 600, H = 150, n = hourly.length || 1;
    var m = Math.max.apply(null, hourly.map(function (x) { return x.ev; }).concat([1]));
    var bw = W / n;
    bw = Math.min(bw, 56);   // fresh DB with 1-2 hours -> avoid one giant bar
    var bars = hourly.map(function (x, i) {
      var bh = (H - 26) * x.ev / m, vh = (H - 26) * (x.viol || 0) / m;
      var hr = (x.h || "").slice(0, 13);
      var sel = curHour === hr;
      var tip = (x.h || "").slice(11, 16) + "  " + x.ev + (L === "pl" ? " epizodów, " : " episodes, ") + (x.viol || 0) + (L === "pl" ? " naruszeń — kliknij, by filtrować" : " violations — click to filter");
      return '<g class="evbar" data-hour="' + hr + '" style="cursor:pointer">' +
             '<rect x="' + (i * bw + 1).toFixed(1) + '" y="0" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + H + '" fill="transparent"/>' +
             '<rect x="' + (i * bw + 1).toFixed(1) + '" y="' + (H - 14 - bh).toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="' + (sel ? "#3f6f9e" : "#22435e") + '"/>' +
             '<rect x="' + (i * bw + 1).toFixed(1) + '" y="' + (H - 14 - vh).toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + vh.toFixed(1) + '" fill="#ff5d6c"/>' +
             "<title>" + tip + "</title></g>";
    }).join("");
    e.innerHTML = svgEl(W, H) + bars + "</svg>" +
      '<div class="legend"><i style="background:#22435e"></i>' + (L === "pl" ? "epizody" : "episodes") +
      '<i style="background:#ff5d6c"></i>' + (L === "pl" ? "naruszenia wg AI" : "AI violations") +
      (curHour ? ' · <a href="#" id="hour-reset">' + (L === "pl" ? "pokaż wszystkie godziny ✕" : "show all hours ✕") + "</a>" : "") + "</div>";
    e.querySelectorAll(".evbar").forEach(function (g) {
      g.addEventListener("click", function () {
        var hr = g.getAttribute("data-hour");
        curHour = (curHour === hr) ? null : hr;
        lastSig = ""; loadEvents(); drawCharts();
        var evs = el("events"); if (evs && curHour) evs.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    var rst = el("hour-reset");
    if (rst) rst.addEventListener("click", function (ev2) { ev2.preventDefault(); curHour = null; lastSig = ""; loadEvents(); drawCharts(); });
  }

  function todChart(id, tod) {
    var e = el(id); if (!e) return;
    var totSp = tod.reduce(function (a, x) { return a + (x.speeding || 0); }, 0);
    if (!tod.length || totSp === 0) {
      e.innerHTML = '<p class="muted pad">' + (L === "pl"
        ? "Brak zarejestrowanych przekroczeń prędkości — wykres wypełni się, gdy pojawią się szybkie pojazdy."
        : "No speeding recorded yet — this chart fills in as fast vehicles appear.") + "</p>";
      return;
    }
    var W = 600, H = 150, bw = W / 24;
    var m = Math.max.apply(null, tod.map(function (x) { return x.per1000 || 0; }).concat([0.1]));
    var byH = {}; tod.forEach(function (x) { byH[x.h] = x; });
    var bars = [];
    for (var hh = 0; hh < 24; hh++) {
      var x = byH[hh];
      var v = x && x.per1000 != null ? x.per1000 : 0;
      var bh = (H - 26) * v / m;
      var tip = ("0" + hh).slice(-2) + ":00 UTC — " + (x ? (x.speeding + (L === "pl" ? " przekroczeń / " : " speeding / ") + x.veh + (L === "pl" ? " pojazdów" : " vehicles")) : (L === "pl" ? "brak danych" : "no data"));
      bars.push('<rect x="' + (hh * bw + 2).toFixed(1) + '" y="' + (H - 14 - bh).toFixed(1) +
        '" width="' + (bw - 4).toFixed(1) + '" height="' + Math.max(bh, v > 0 ? 2 : 0).toFixed(1) +
        '" rx="2" fill="#ff9d5c"><title>' + tip + "</title></rect>" +
        (hh % 3 === 0 ? '<text x="' + (hh * bw + bw / 2).toFixed(1) + '" y="' + (H - 2) + '" font-size="9" fill="#8b97a7" text-anchor="middle">' + hh + "</text>" : ""));
    }
    e.innerHTML = svgEl(W, H) + bars.join("") + "</svg>" +
      '<div class="legend"><i style="background:#ff9d5c"></i>' +
      (L === "pl" ? "przekroczenia prędkości na 1000 pojazdów wg godziny (UTC)" : "speeding per 1000 vehicles by hour (UTC)") + "</div>";
  }

  /* ---------- boot ---------- */
  var img = el("live-img");
  if (img) {
    img.src = "/cv/live.mjpg";
    img.addEventListener("error", function () {
      setLive(false);
      setTimeout(function () { img.src = "/cv/live.mjpg?" + Date.now(); }, 4000);
    });
  }
  // admin banner — visible only when signed in (via /admin panel or URL token)
  if (ADMIN) {
    var ab = document.createElement("div");
    ab.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:200;background:#141c28;border:1px solid #2ee6a6;color:#e6edf3;padding:.5rem .8rem;border-radius:10px;font-size:.85rem;box-shadow:0 4px 16px rgba(0,0,0,.5)";
    ab.innerHTML = "🛡️ " + (L === "pl" ? "Tryb admina" : L === "es" ? "Modo admin" : L === "ru" ? "Режим админа" : "Admin mode") +
      ' — <a href="#" id="admin-out" style="color:#ff8a97">' + (L === "pl" ? "Wyloguj" : L === "es" ? "Salir" : L === "ru" ? "Выйти" : "Sign out") + "</a>";
    document.body.appendChild(ab);
    ab.querySelector("#admin-out").addEventListener("click", function (e) {
      e.preventDefault();
      try { localStorage.removeItem("bp_admin"); } catch (x) {}
      location.reload();
    });
  }
  // local 1 s ticking of the camera-rotation countdown
  setInterval(function () {
    var pc = el("pl-countdown");
    if (!pc || !window._plNext) return;
    var s = Math.max(0, Math.round((window._plNext.t - Date.now()) / 1000));
    var mm = Math.floor(s / 60), ss = ("0" + (s % 60)).slice(-2);
    pc.innerHTML = "⏱ " + (L === "pl" ? "Zmiana kamery za " : "Camera changes in ") +
      "<b>" + mm + ":" + ss + "</b> " + (L === "pl" ? "→ następna: " : "→ next: ") +
      esc(window._plNext.label);
  }, 1000);
  tick(); loadEvents(); drawCharts();
  setInterval(tick, 3000);
  setInterval(loadEvents, 7000);
  setInterval(drawCharts, 60000);
})();
