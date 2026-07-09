/* Live command-center v2: real-time counters, AI-verdict event tabs with video
   clips, fullscreen gallery, charts, crowd verification. Talks to /cv/*. */
(function () {
  var L = (document.documentElement.lang === "en") ? "en" : "pl";
  var T = {
    pl: {
      live: "NA ŻYWO", off: "OFFLINE", perHour: "/godz.",
      ped: "pieszych", veh: "pojazdów", inFrame: "w kadrze",
      q: "Czy to realne naruszenie?", yes: "✓ Naruszenie", no: "✗ Fałszywy alarm",
      noev: "Brak zdarzeń w tej zakładce — system nagrywa epizody, gdy pojazd W RUCHU spotyka pieszego w strefie przejścia.",
      conf: "głosów „naruszenie”", ref: "głosów „fałszywy alarm”",
      aiViol: "AI: NARUSZENIE", aiNo: "AI: brak naruszenia", aiUnc: "AI: niepewne",
      aiWait: "czeka na analizę AI", more: "więcej", less: "zwiń",
      tabs: { violation: "Naruszenia wg AI", rejected: "Odrzucone przez AI — sprawdź!", pending: "Czekają na AI", all: "Wszystkie" },
      tl: "sygnalizacja", vmax: "max prędkość", epi: "● nagrywam epizod…",
      fsNote: "Klip celowo w niskiej rozdzielczości i klatkażu — oszczędzamy zasoby serwera demo. Produkcyjnie: pełne FPS/rozdzielczość.",
      aiToday: "analiz AI dziś",
    },
    en: {
      live: "LIVE", off: "OFFLINE", perHour: "/h",
      ped: "pedestrians", veh: "vehicles", inFrame: "in frame",
      q: "Is this a real violation?", yes: "✓ Violation", no: "✗ False alarm",
      noev: "No events in this tab — the system records an episode when a MOVING vehicle meets a pedestrian in the crossing zone.",
      conf: "votes “violation”", ref: "votes “false alarm”",
      aiViol: "AI: VIOLATION", aiNo: "AI: no violation", aiUnc: "AI: uncertain",
      aiWait: "awaiting AI analysis", more: "more", less: "less",
      tabs: { violation: "AI violations", rejected: "AI-rejected — double-check!", pending: "Awaiting AI", all: "All" },
      tl: "signals", vmax: "max speed", epi: "● recording episode…",
      fsNote: "Clips are intentionally low-res/low-fps to save demo server resources. Production: full FPS/resolution.",
      aiToday: "AI analyses today",
    },
  }[L];

  var voted = {};
  try { voted = JSON.parse(localStorage.getItem("bp_voted2") || "{}"); } catch (e) {}
  var curTab = "all", events = [], lastSig = "";

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
      var bits = [d.in_frame.ped + " " + T.ped + " · " + d.in_frame.veh + " " + T.veh + " " + T.inFrame];
      if (d.speeds && d.speeds.veh_kmh) bits.push(T.vmax + " ~" + d.speeds.veh_kmh + " km/h");
      var tls = Object.keys(d.tl || {}).map(function (k) { return d.tl[k]; }).filter(function (v) { return v !== "unknown"; });
      if (tls.length) bits.push(T.tl + ": " + tls.join("/"));
      bits.push(T.aiToday + ": " + d.ai_calls_today);
      inf.textContent = bits.join("  ·  ");
    }
    var src = el("live-source"); if (src && d.source) src.textContent = d.source;
    var epi = el("epi-badge"); if (epi) epi.style.display = d.episode_active ? "inline-block" : "none";
    var tk = el("ticker");
    if (tk && d.ticker) tk.innerHTML = d.ticker.slice(0, 5).map(function (t) { return '<span class="tick">' + esc(t) + "</span>"; }).join("");
  }

  /* ---------- events ---------- */
  function loadEvents() {
    fetch("/cv/events.json?tab=" + curTab, { cache: "no-store" })
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
    var sig = curTab + "|" + events.map(function (e) {
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
        " · " + (e.dur != null ? e.dur : "?") + "s · ~" + (e.kmh != null ? e.kmh : "?") + " km/h " + aiBadge(e) + "</div>" +
        (expl ? '<div class="ev-ai">' + esc(expl) + "</div>" : '<div class="ev-ai muted">' + esc(e.desc || "") + "</div>") +
        '<div class="ev-q">' + T.q + "</div>" +
        '<div class="ev-actions">' + vbtn(e.id, "violation", T.yes, v) + vbtn(e.id, "false_alarm", T.no, v) + "</div>" +
        '<div class="ev-tally">' + e.confirm + " " + T.conf + " · " + e.refute + " " + T.ref + "</div>" +
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
      fetch("/cv/api/verify", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: Number(id), verdict: verdict }) })
        .then(function (r) {
          if (r.ok) { voted[id] = verdict; try { localStorage.setItem("bp_voted2", JSON.stringify(voted)); } catch (e) {} lastSig = ""; loadEvents(); }
          else b.disabled = false;
        }).catch(function () { b.disabled = false; });
      return;
    }
    var tb = ev.target.closest && ev.target.closest(".tab");
    if (tb) {
      curTab = tb.getAttribute("data-tab");
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("cur", x === tb); });
      lastSig = ""; loadEvents();
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
    }).catch(function () {});
  }
  function svgEl(w, h) { return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" style="width:100%;height:100%">'; }
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
    e.innerHTML = svgEl(W, H) +
      '<polyline fill="none" stroke="#37b6ff" stroke-width="2" points="' + pts("veh", mv) + '"/>' +
      '<polyline fill="none" stroke="#2ee6a6" stroke-width="2" points="' + pts("ped", mp) + '"/>' +
      "</svg>" +
      '<div class="legend"><i style="background:#37b6ff"></i>' + T.veh + " (max " + mv + "/h)" +
      '<i style="background:#2ee6a6"></i>' + T.ped + " (max " + mp + "/h)</div>";
  }
  function histChart(id, bins, total) {
    var e = el(id); if (!e) return;
    var W = 600, H = 150, m = Math.max.apply(null, bins.concat([1]));
    var bw = W / bins.length;
    var bars = bins.map(function (v, i) {
      var bh = (H - 26) * v / m;
      return '<rect x="' + (i * bw + 2).toFixed(1) + '" y="' + (H - 14 - bh).toFixed(1) +
        '" width="' + (bw - 4).toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="2" fill="#ffcf5c"/>' +
        (i % 2 === 0 ? '<text x="' + (i * bw + bw / 2).toFixed(1) + '" y="' + (H - 2) + '" font-size="9" fill="#8b97a7" text-anchor="middle">' + i * 5 + "</text>" : "");
    }).join("");
    e.innerHTML = svgEl(W, H) + bars + "</svg>" +
      '<div class="legend"><i style="background:#ffcf5c"></i>km/h (' + total + (L === "pl" ? " pomiarów, orientacyjnie" : " samples, indicative") + ")</div>";
  }
  function eventChart(id, hourly) {
    var e = el(id); if (!e) return;
    var W = 600, H = 150, n = hourly.length || 1;
    var m = Math.max.apply(null, hourly.map(function (x) { return x.ev; }).concat([1]));
    var bw = W / n;
    var bars = hourly.map(function (x, i) {
      var bh = (H - 26) * x.ev / m, vh = (H - 26) * (x.viol || 0) / m;
      return '<rect x="' + (i * bw + 1).toFixed(1) + '" y="' + (H - 14 - bh).toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="#22435e"/>' +
             '<rect x="' + (i * bw + 1).toFixed(1) + '" y="' + (H - 14 - vh).toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + vh.toFixed(1) + '" fill="#ff5d6c"/>';
    }).join("");
    e.innerHTML = svgEl(W, H) + bars + "</svg>" +
      '<div class="legend"><i style="background:#22435e"></i>' + (L === "pl" ? "epizody" : "episodes") +
      '<i style="background:#ff5d6c"></i>' + (L === "pl" ? "naruszenia wg AI" : "AI violations") + "</div>";
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
  tick(); loadEvents(); drawCharts();
  setInterval(tick, 3000);
  setInterval(loadEvents, 7000);
  setInterval(drawCharts, 60000);
})();
