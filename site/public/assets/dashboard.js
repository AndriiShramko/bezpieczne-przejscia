/* Dashboard renderer — aggregate charts only; gaps stay visible as no-data. */
(function () {
  var L = window.DASH_LANG || "pl";
  var TXT = {
    pl: { ped: "piesi/godz.", head: "% głowa w dół", lo: "CI dół", hi: "CI góra",
          yield_: "nieustąpienia/dzień", conf: "konflikty/dzień", nodata: "brak danych",
          fresh: "Dane wygenerowano (UTC): ", cover: "pokrycie", up: "AKTYWNA",
          standby: "REZERWA", days: ["Pn","Wt","Śr","Cz","Pt","So","Nd"] },
    en: { ped: "pedestrians/h", head: "% head-down", lo: "CI low", hi: "CI high",
          yield_: "failures to yield/day", conf: "conflicts/day", nodata: "no data",
          fresh: "Data generated (UTC): ", cover: "coverage", up: "ACTIVE",
          standby: "STANDBY", days: ["Mo","Tu","We","Th","Fr","Sa","Su"] }
  }[L];

  fetch("/data/demo.json").then(function (r) { return r.json(); }).then(render);

  function render(D) {
    var hours = D.hourly;

    /* ---- KPIs ---- */
    var covered = hours.filter(function (h) { return !h.no_data; });
    var byDay = {};
    covered.forEach(function (h) {
      var d = h.t.slice(0, 10);
      (byDay[d] = byDay[d] || { ped: 0, yv: 0, hk: 0, hn: 0 }).ped += h.ped;
      byDay[d].yv += h.yield_violations; byDay[d].hk += h.head_down_n;
      byDay[d].hn += h.head_sample_n;
    });
    var days = Object.keys(byDay).sort();
    var avg = function (f) {
      return Math.round(days.reduce(function (s, d) { return s + f(byDay[d]); }, 0) / days.length);
    };
    var last7 = days.slice(-7);
    var hk = 0, hn = 0;
    last7.forEach(function (d) { hk += byDay[d].hk; hn += byDay[d].hn; });
    set("kpi0", D.coverage_pct + "%");
    set("kpi1", avg(function (x) { return x.ped; }).toLocaleString());
    set("kpi2", avg(function (x) { return x.yv; }));
    set("kpi3", hn ? (100 * hk / hn).toFixed(1) + "%" : "—");

    /* ---- pedestrians/hour, nulls preserved as gaps ---- */
    mkChart("c_ped", {
      type: "line",
      data: { labels: hours.map(function (h) { return h.t.slice(5, 13).replace("T", " "); }),
        datasets: [{ label: TXT.ped, spanGaps: false, pointRadius: 0, borderWidth: 1.4,
          borderColor: "#1668b0", tension: .25,
          data: hours.map(function (h) { return h.no_data ? null : h.ped; }) }] },
      options: base({ xticks: 14 })
    });

    /* ---- head-down % daily with CI band ---- */
    var hd = days.map(function (d) {
      var x = byDay[d];
      return x.hn ? { p: 100 * x.hk / x.hn, n: x.hn } : null;
    });
    var ci = hd.map(function (v) {
      if (!v) return { lo: null, hi: null };
      var z = 1.96, p = v.p / 100, n = v.n, den = 1 + z * z / n;
      var c = (p + z * z / (2 * n)) / den,
        h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den;
      return { lo: 100 * Math.max(0, c - h), hi: 100 * Math.min(1, c + h) };
    });
    mkChart("c_head", {
      type: "line",
      data: { labels: days.map(function (d) { return d.slice(5); }), datasets: [
        { label: TXT.hi, data: ci.map(function (c) { return c.hi; }), pointRadius: 0,
          borderWidth: 0, backgroundColor: "rgba(22,104,176,.15)", fill: "+2" },
        { label: TXT.head, data: hd.map(function (v) { return v && v.p; }),
          borderColor: "#12314f", borderWidth: 1.8, pointRadius: 2, spanGaps: false },
        { label: TXT.lo, data: ci.map(function (c) { return c.lo; }), pointRadius: 0,
          borderWidth: 0 }] },
      options: base({ pct: true })
    });

    /* ---- yield violations + conflicts per day ---- */
    mkChart("c_yield", {
      type: "bar",
      data: { labels: days.map(function (d) { return d.slice(5); }), datasets: [
        { label: TXT.yield_, backgroundColor: "#b3452c",
          data: days.map(function (d) { return byDay[d].yv; }) },
        { label: TXT.conf, backgroundColor: "#e8b409",
          data: days.map(function (d) {
            return covered.filter(function (h) { return h.t.slice(0, 10) === d; })
              .reduce(function (s, h) { return s + h.conflicts; }, 0);
          }) }] },
      options: base({})
    });

    /* ---- heatmap hour x weekday ---- */
    var grid = {}, cnt = {};
    covered.forEach(function (h) {
      var dt = new Date(h.t.replace("Z", ":00Z"));
      var k = ((dt.getUTCDay() + 6) % 7) + "-" + dt.getUTCHours();
      grid[k] = (grid[k] || 0) + h.ped; cnt[k] = (cnt[k] || 0) + 1;
    });
    var hm = document.getElementById("heatmap"), max = 1;
    Object.keys(grid).forEach(function (k) { max = Math.max(max, grid[k] / cnt[k]); });
    hm.appendChild(cell(""));
    for (var hh = 0; hh < 24; hh++) hm.appendChild(cell(hh, "#fff"));
    for (var d = 0; d < 7; d++) {
      hm.appendChild(cell(TXT.days[d], "#fff"));
      for (var h2 = 0; h2 < 24; h2++) {
        var k2 = d + "-" + h2, v = grid[k2] ? grid[k2] / cnt[k2] : null;
        var c = v === null ? "#eceff2" :
          "rgba(22,104,176," + (0.08 + 0.85 * v / max).toFixed(2) + ")";
        var el = cell("", c);
        el.title = TXT.days[d] + " " + h2 + ":00 — " +
          (v === null ? TXT.nodata : Math.round(v) + " " + TXT.ped);
        hm.appendChild(el);
      }
    }

    /* ---- pool + health ---- */
    var pool = document.getElementById("pool");
    D.crossing.pool.forEach(function (s) {
      var b = document.createElement("span");
      b.style.cssText = "display:inline-block;margin:0 .5rem .5rem 0;padding:.25rem .6rem;" +
        "border-radius:12px;font-size:.8rem;font-weight:700;color:#fff;background:" +
        (s.state === "up" ? "#176b2c" : "#5a6672");
      b.textContent = s.source + " · " + (s.state === "up" ? TXT.up : TXT.standby);
      pool.appendChild(b);
    });
    var ht = document.getElementById("health");
    D.health.slice(-12).reverse().forEach(function (e) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td>" + e.t + "</td><td>" + e.source + "</td><td>" +
        e.event + "</td><td>" + e.detail + "</td>";
      ht.appendChild(tr);
    });
    set("freshness", TXT.fresh + D.generated_utc + " · " + TXT.cover + " " +
      D.coverage_pct + "% · synthetic demo");
  }

  function mkChart(id, cfg) { return new Chart(document.getElementById(id), cfg); }
  function set(id, v) { var e = document.getElementById(id); if (e) e.textContent = v; }
  function cell(txt, bg) {
    var d = document.createElement("div");
    d.textContent = txt; if (bg) d.style.background = bg;
    return d;
  }
  function base(o) {
    return {
      animation: false, responsive: true,
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 10 },
        filter: function (i) { return i.text.indexOf("CI") !== 0; } } } },
      scales: {
        x: { ticks: { maxTicksLimit: o.xticks || 20, font: { size: 9 } } },
        y: { beginAtZero: true,
          ticks: o.pct ? { callback: function (v) { return v + "%"; } } : {} }
      }
    };
  }
})();
