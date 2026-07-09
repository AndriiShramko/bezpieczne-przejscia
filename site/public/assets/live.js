/* Live command-center: real-time counters, event feed, crowd verification.
   Talks to the CV service via /cv/* (proxied by nginx to patrol-cv). */
(function () {
  var L = (document.documentElement.lang === "en") ? "en" : "pl";
  var T = {
    pl: {
      offline: "Kamera chwilowo niedostępna — ponawiam połączenie…",
      ped: "pieszych", veh: "pojazdów", events: "zdarzeń", acc: "trafność wg ludzi",
      perHour: "/godz.", inFrame: "w kadrze", live: "NA ŻYWO", off: "OFFLINE",
      q: "Czy kierowca ustąpił pierwszeństwa?", yes: "✓ Naruszenie", no: "✗ Fałszywy alarm",
      thanks: "Dzięki!", noev: "Czekam na pierwsze wykryte zdarzenie…",
      conf: "potwierdzeń", ref: "odrzuceń", verifiedBy: "zweryfikowane przez ludzi",
      help: "Pomóż nam sprawdzać AI",
    },
    en: {
      offline: "Camera temporarily unavailable — reconnecting…",
      ped: "pedestrians", veh: "vehicles", events: "events", acc: "human-rated accuracy",
      perHour: "/h", inFrame: "in frame", live: "LIVE", off: "OFFLINE",
      q: "Did the driver yield?", yes: "✓ Violation", no: "✗ False alarm",
      thanks: "Thanks!", noev: "Waiting for the first detected event…",
      conf: "confirms", ref: "refutes", verifiedBy: "human-verified",
      help: "Help us check the AI",
    },
  }[L];

  var voted = {};
  try { voted = JSON.parse(localStorage.getItem("bp_voted") || "{}"); } catch (e) {}

  function el(id) { return document.getElementById(id); }
  function num(n) { return (n == null) ? "—" : Number(n).toLocaleString(L === "pl" ? "pl-PL" : "en-US"); }

  function tick() {
    fetch("/cv/state.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { setLive(false); });
  }

  function setLive(on) {
    var b = el("live-badge");
    if (!b) return;
    b.textContent = on ? ("● " + T.live) : ("○ " + T.off);
    b.className = "live-badge " + (on ? "on" : "off");
    var o = el("live-offline");
    if (o) o.style.display = on ? "none" : "flex";
  }

  function render(d) {
    setLive(d.live);
    setStat("st-ped", num(d.ped_total), d.ped_per_hour != null ? (num(d.ped_per_hour) + " " + T.perHour) : "");
    setStat("st-veh", num(d.veh_total), d.veh_per_hour != null ? (num(d.veh_per_hour) + " " + T.perHour) : "");
    setStat("st-ev", num(d.event_total), "");
    var acc = d.stats && d.stats.human_precision_pct;
    setStat("st-acc", acc == null ? "—" : (acc + "%"),
      d.stats ? (num(d.stats.events_judged) + " / " + num(d.stats.votes_total) + " " + T.verifiedBy) : "");
    var inf = el("st-inframe");
    if (inf && d.in_frame) inf.textContent = d.in_frame.ped + " " + T.ped + " · " + d.in_frame.veh + " " + T.veh + " " + T.inFrame;
    var src = el("live-source"); if (src && d.source) src.textContent = d.source;

    var tk = el("ticker");
    if (tk && d.ticker) {
      tk.innerHTML = d.ticker.slice(0, 5).map(function (t) {
        return '<span class="tick">' + escapeHtml(t) + "</span>";
      }).join("");
    }
    renderEvents(d.events || []);
  }

  function setStat(id, big, sub) {
    var e = el(id); if (!e) return;
    e.querySelector(".big").textContent = big;
    var s = e.querySelector(".sub"); if (s) s.textContent = sub;
  }

  function renderEvents(events) {
    var box = el("events");
    if (!box) return;
    if (!events.length) { box.innerHTML = '<p class="muted pad">' + T.noev + "</p>"; return; }
    box.innerHTML = events.map(function (e) {
      var v = voted[e.id];
      var total = e.confirm + e.refute;
      var pct = total ? Math.round(100 * e.confirm / total) : null;
      return '' +
        '<figure class="ev">' +
        '<img loading="lazy" src="/cv/snap/' + escapeAttr(e.snap) + '" alt="event ' + e.id + '">' +
        '<figcaption>' +
        '<div class="ev-desc">#' + e.id + ' · ' + escapeHtml(e.desc) + '</div>' +
        '<div class="ev-q">' + T.q + '</div>' +
        '<div class="ev-actions">' +
        btn(e.id, "confirm", T.yes, v) + btn(e.id, "refute", T.no, v) +
        '</div>' +
        '<div class="ev-tally">' + e.confirm + ' ' + T.conf + ' · ' + e.refute + ' ' + T.ref +
        (pct == null ? "" : ' · <b>' + pct + '%</b>') + '</div>' +
        '</figcaption></figure>';
    }).join("");
  }

  function btn(id, verdict, label, votedVerdict) {
    var done = votedVerdict != null;
    var mine = votedVerdict === verdict;
    return '<button class="vbtn ' + verdict + (mine ? " mine" : "") + '"' +
      (done ? " disabled" : "") +
      ' data-id="' + id + '" data-v="' + verdict + '">' + label + "</button>";
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".vbtn");
    if (!b || b.disabled) return;
    var id = b.getAttribute("data-id"), verdict = b.getAttribute("data-v");
    b.disabled = true;
    fetch("/cv/api/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: Number(id), verdict: verdict }),
    }).then(function (r) {
      if (r.ok) {
        voted[id] = verdict;
        try { localStorage.setItem("bp_voted", JSON.stringify(voted)); } catch (e) {}
        tick();
      } else { b.disabled = false; }
    }).catch(function () { b.disabled = false; });
  });

  function escapeHtml(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function escapeAttr(s) { return String(s).replace(/[^a-zA-Z0-9_.\-]/g, ""); }

  // kick the MJPEG image + retry on error
  var img = el("live-img");
  if (img) {
    img.src = "/cv/live.mjpg";
    img.addEventListener("error", function () {
      setLive(false);
      setTimeout(function () { img.src = "/cv/live.mjpg?" + Date.now(); }, 4000);
    });
  }
  tick();
  setInterval(tick, 2500);
})();
